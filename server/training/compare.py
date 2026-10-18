"""複数のモデル × 窓の長さ × 乱数シードを、同じデータ・同じ評価（周回単位の交差検証）で比べる。

使い方:
    python -m training.compare --data data/calib.jsonl --baseline data/baseline.jsonl
    python -m training.compare --data data/calib.jsonl --baseline data/baseline.jsonl \\
        --models rf,mlp,cnn2d --windows 20,40 --seeds 42,43 --out data/compare_quick --jobs 2

処理:
  1. 窓の長さごとにデータセットを1回だけ作り、<out>_cache/ に保存する（統計特徴量 / 生の窓）。
     元のJSONLはここで捨てるので、並列に動かしてもメモリを食いにくい
  2. 各モデル・各シードで、training.train と同じ leave-one-group-out 評価を行う
     （深層学習の早期終了は学習側の周だけで行い、テストの周は見ない）。
     --jobs N で N 個のプロセスに分けて並列に動かす（PyTorchのスレッド数は CPUコア数 / N）
  3. シード間の平均±標準偏差を表にする。kNN は乱数を使わないので1回だけ
出力:
  <out>.md   比較表（Markdown。results.md にそのまま貼れる）
  <out>.csv  1回ごとの結果（モデル・窓・シード・指標・フォールドごとの正解率・時間）。終わった順に追記する
途中で止まったとき（Google Colabの切断など）は、同じコマンドに --resume を付けると
<out>.csv に記録済みの (モデル, 窓, シード) をとばして続きから回す。
GPU（Google Colab）では --jobs 1 にする（GPUは1つなので、1プロセスでGPUを使い切る方が速い）。

注意: 窓が長いほど各セッションの先頭（窓が埋まるまで）を多く捨てるため、サンプル数が少し減る。
"""
from __future__ import annotations

import argparse
import csv
import logging
import os
import statistics
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime

import numpy as np

from config.zones import load_zones
from models.registry import ALGOS, INPUT_KIND, create_model, is_deep
from .dataset import add_dataset_args, add_deep_args, build_dataset, load_training_data, train_config_from_args
from .evaluate import evaluate_leave_one_group_out

logger = logging.getLogger("compare")

MODEL_LABELS = {
    "rf": "RandomForest", "et": "ExtraTrees", "knn": "kNN", "mlp": "MLP", "cnn1d": "1D-CNN（時間方向）", "cnn2d": "2D-CNN（時間×サブキャリア）",
}
# 並べる順（重いものから先に始めると、並列のときに早く終わる）
COST = {"cnn2d": 4, "cnn1d": 3, "mlp": 2, "rf": 1, "et": 1, "knn": 0}
FIELDNAMES = ["model", "window", "seed", "n_samples", "accuracy", "macro_f1", "empty_recall",
              "fold_accuracy", "eval_seconds"]


def mean_sd(values: list[float]) -> str:
    if len(values) == 1:
        return f"{values[0]:.3f}"
    return f"{statistics.mean(values):.3f} ± {statistics.stdev(values):.3f}"


def save_dataset(cache_dir: str, ds) -> None:
    os.makedirs(cache_dir, exist_ok=True)
    np.save(os.path.join(cache_dir, "X.npy"), ds.X)
    np.save(os.path.join(cache_dir, "y.npy"), ds.y)


def worker_init(threads: int) -> None:
    """並列実行の各プロセスで1回だけ呼ばれる。PyTorchのスレッド数を分け合う。"""
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(message)s")
    try:
        import torch
        torch.set_num_threads(threads)
    except ImportError:
        pass


def run_one(task: dict) -> dict:
    """1つの (モデル, 窓, シード) を評価する。並列実行でも直接実行でも使う。"""
    X = np.load(os.path.join(task["cache_dir"], "X.npy"), mmap_mode="r")  # 読むだけなのでメモリに全部載せない
    y = np.load(os.path.join(task["cache_dir"], "y.npy"))
    groups = np.load(os.path.join(task["cache_dir"], "groups.npy"))
    zone_config = load_zones(task["zones"])
    algo, seed = task["algo"], task["seed"]
    metrics = evaluate_leave_one_group_out(
        X, y, groups, lambda: create_model(algo, seed, X.shape[1:], task["train_config"]), zone_config)
    return {
        "model": algo, "window": task["window"], "seed": seed, "n_samples": int(X.shape[0]),
        # 丸めない（表示のときだけ丸める。2回丸めると 0.60149 → 0.6015 → 0.602 のようにずれる）
        "accuracy": metrics["accuracy"], "macro_f1": metrics["macro_f1"],
        "empty_recall": metrics["per_zone_recall"].get(0, float("nan")),
        "fold_accuracy": " ".join(f"{v:.3f}" for v in metrics["fold_accuracy"].values()),
        "eval_seconds": metrics["eval_seconds"],
    }


def load_rows(csv_path: str) -> list[dict]:
    """--resume 用: 記録済みのCSVを読み、型を戻す。"""
    rows = []
    with open(csv_path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            rows.append({
                "model": r["model"], "window": int(r["window"]), "seed": int(r["seed"]),
                "n_samples": int(r["n_samples"]), "accuracy": float(r["accuracy"]),
                "macro_f1": float(r["macro_f1"]), "empty_recall": float(r["empty_recall"]),
                "fold_accuracy": r["fold_accuracy"], "eval_seconds": float(r["eval_seconds"]),
            })
    return rows


def write_table(path: str, rows: list[dict], algos: list[str], windows: list[int], args) -> str:
    lines = [
        f"評価: 周回単位の交差検証（--split {args.split}）、stride {args.stride}、"
        f"シード {args.seeds}（kNNは1回）。値はシード間の平均 ± 標準偏差。",
        f"作成: {datetime.now().isoformat(timespec='minutes')}",
        "",
        "| モデル | 入力 | 窓 | サンプル数 | 正解率 | macro-F1 | empty再現率 | 周ごとの正解率（平均） | 評価時間/回 |",
        "| ------ | ---- | -- | ---------- | ------ | -------- | ----------- | ---------------------- | ----------- |",
    ]
    for algo in algos:
        for window in windows:
            runs = [r for r in rows if r["model"] == algo and r["window"] == window]
            if not runs:
                continue
            folds = [[float(v) for v in r["fold_accuracy"].split()] for r in runs]
            fold_mean = " / ".join(f"{statistics.mean(col):.2f}" for col in zip(*folds))
            kind = "統計特徴量" if INPUT_KIND[algo] == "stats" else "生の窓"
            lines.append(
                f"| {MODEL_LABELS[algo]} | {kind} | {window}（{window * args.interval:.0f}秒） | {runs[0]['n_samples']} "
                f"| {mean_sd([r['accuracy'] for r in runs])} | {mean_sd([r['macro_f1'] for r in runs])} "
                f"| {mean_sd([r['empty_recall'] for r in runs])} | {fold_mean} "
                f"| {statistics.mean(r['eval_seconds'] for r in runs):.0f}秒 |"
            )
    table = "\n".join(lines)
    with open(path, "w", encoding="utf-8") as f:
        f.write(table + "\n")
    return table


def main() -> None:
    parser = argparse.ArgumentParser(description="compare models x window lengths with the same evaluation")
    add_dataset_args(parser)
    parser.add_argument("--models", default="rf,et,knn,mlp,cnn1d,cnn2d", help=f"比べるモデル（{','.join(ALGOS)}）")
    parser.add_argument("--windows", default="20,40,60", help="窓の長さ（フレーム数、50ms単位）。20 = 1秒")
    parser.add_argument("--seeds", default="42,43,44", help="乱数シード（シード間の平均±標準偏差を出す）")
    parser.add_argument("--stride", type=int, default=2, help="サンプルをNフレームおきに間引く")
    parser.add_argument("--split", choices=["session", "date", "round"], default="round")
    parser.add_argument("--out", default="data/compare", help="出力の拡張子なしのパス（.md と .csv を作る）")
    parser.add_argument("--jobs", type=int, default=1,
                        help="並列に動かすプロセス数（CPUのときだけ。メモリに余裕があるときだけ増やす。"
                             "生の窓60フレームで1つ約1.7GB）。GPUでは1にする")
    parser.add_argument("--resume", action="store_true",
                        help="<out>.csv に記録済みの (モデル, 窓, シード) をとばして続きから回す")
    parser.add_argument("--cache-dir", default=None,
                        help="データセットの保存先（省略時は <out>_cache）。Colabでは速いローカルディスク（/content/...）にする")
    add_deep_args(parser)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    logging.getLogger("dataset").setLevel(logging.WARNING)  # セッションごとのログは省く
    logging.getLogger("evaluate").setLevel(logging.WARNING)
    logging.getLogger("deep").setLevel(logging.WARNING)

    algos = [a.strip() for a in args.models.split(",") if a.strip()]
    unknown = [a for a in algos if a not in ALGOS]
    if unknown:
        raise SystemExit(f"不明なモデル: {unknown}（使えるもの: {ALGOS}）")
    windows = [int(w) for w in args.windows.split(",") if w.strip()]
    seeds = [int(s) for s in args.seeds.split(",") if s.strip()]
    train_config = train_config_from_args(args) if any(is_deep(a) for a in algos) else None

    # 1. データセットを作って保存する（窓 × 入力の種類ごとに1回）
    zone_config = load_zones(args.zones)
    data = load_training_data(args.data, args.baseline, zone_config, args.sensors)
    cache_root = args.cache_dir or (args.out + "_cache")
    cache_dirs: dict[tuple[str, int], str] = {}
    for window in windows:
        for kind in sorted({INPUT_KIND[a] for a in algos}):
            ds = build_dataset(data.records, data.sensor_ids, data.expected_len, data.baseline,
                               args.interval, args.max_age, zone_config, window_frames=window,
                               stride=max(args.stride, 1), input_kind=kind)
            cache_dir = os.path.join(cache_root, f"{kind}_w{window}")
            save_dataset(cache_dir, ds)
            np.save(os.path.join(cache_dir, "groups.npy"), np.asarray(ds.groups(args.split).tolist()))
            cache_dirs[(kind, window)] = cache_dir
            logger.info("window=%d (%s): %d samples x %s -> %s", window, kind, ds.X.shape[0],
                        "x".join(str(d) for d in ds.X.shape[1:]), cache_dir)
            del ds
    del data  # 元のレコードはもう使わない（メモリを空ける）

    # 2. 評価する組み合わせ（重いものから）
    tasks = [
        {"algo": algo, "window": window, "seed": seed, "cache_dir": cache_dirs[(INPUT_KIND[algo], window)],
         "zones": args.zones, "train_config": train_config}
        for algo in algos for window in windows for seed in (seeds if algo != "knn" else seeds[:1])
    ]
    tasks.sort(key=lambda t: (-COST[t["algo"]], -t["window"]))
    logger.info("%d runs, %d jobs in parallel", len(tasks), args.jobs)

    csv_path = args.out + ".csv"
    os.makedirs(os.path.dirname(csv_path) or ".", exist_ok=True)
    rows: list[dict] = []
    if args.resume and os.path.exists(csv_path):
        rows = load_rows(csv_path)
        done = {(r["model"], r["window"], r["seed"]) for r in rows}
        tasks = [t for t in tasks if (t["algo"], t["window"], t["seed"]) not in done]
        logger.info("resume: %d runs already in %s, %d runs left", len(rows), csv_path, len(tasks))
    else:
        if os.path.exists(csv_path) and os.path.getsize(csv_path) > 0:
            raise SystemExit(f"{csv_path} がすでにあります。続きから回すなら --resume、"
                             "最初からなら --out を変えるかファイルを退避してください")
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=FIELDNAMES).writeheader()
    n_total = len(rows) + len(tasks)

    def record(row: dict) -> None:
        rows.append(row)
        with open(csv_path, "a", newline="", encoding="utf-8") as f:  # 途中で止めても結果が残る
            csv.DictWriter(f, fieldnames=FIELDNAMES).writerow(row)
        logger.info("[%d/%d] %s window=%d seed=%d: accuracy=%.3f macro-F1=%.3f empty=%.3f folds=[%s] (%.0fs)",
                    len(rows), n_total, row["model"], row["window"], row["seed"], row["accuracy"],
                    row["macro_f1"], row["empty_recall"], row["fold_accuracy"], row["eval_seconds"])

    if args.jobs <= 1:
        for task in tasks:
            record(run_one(task))
    else:
        threads = max(1, (os.cpu_count() or 2) // 2 // args.jobs)  # 物理コア数 ≒ 論理コア数 / 2
        with ProcessPoolExecutor(max_workers=args.jobs, initializer=worker_init, initargs=(threads,)) as pool:
            futures = [pool.submit(run_one, task) for task in tasks]
            for future in as_completed(futures):
                record(future.result())

    # 3. 比較表
    table = write_table(args.out + ".md", rows, algos, windows, args)
    print("\n" + table)
    logger.info("saved %s.md and %s", args.out, csv_path)


if __name__ == "__main__":
    main()
