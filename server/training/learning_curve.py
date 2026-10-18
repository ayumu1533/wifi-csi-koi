"""学習曲線: 学習に使う周回数を1, 2, 3, ...と増やしたとき、精度がどう伸びるかを調べる。

使い方:
    python -m training.learning_curve --data data/calib.jsonl --baseline data/baseline.jsonl
    python -m training.learning_curve --data data/calib.jsonl --baseline data/baseline.jsonl \\
        --windows 0,20,40 --algo knn

処理:
  周回（collection.planの"round"。無ければゾーンごとのセッション順）を1つずつ評価用に取り分け、
  残りの周回から先頭k周だけで学習して、取り分けた周回を予測する（k = 1, 2, ...）。
  全フォールドの予測をまとめて、kごとの正解率・macro-F1・emptyの再現率を表にする。
  --windows に複数の窓の長さを渡すと、特徴量の違い（0 = 1フレーム特徴量）も同じ表で比べられる。

読み方:
  k を1増やしたときの伸びが小さく（既定 0.02 未満）なったら、それ以上周回を増やしても効果は薄い。
  まだ伸びているなら、周回を追加で収集する価値がある。
"""
from __future__ import annotations

import argparse
import logging

import numpy as np
from sklearn.metrics import accuracy_score, f1_score, recall_score

from config.zones import load_zones
from models.registry import INPUT_KIND, create_model, is_deep
from .dataset import add_dataset_args, add_deep_args, build_dataset, load_training_data, train_config_from_args

logger = logging.getLogger("learning_curve")


def learning_curve(X: np.ndarray, y: np.ndarray, rounds: np.ndarray, algo: str, seed: int,
                   train_config=None) -> list[dict]:
    """k周で学習したときの指標を k = 1..(周回数-1) について返す。"""
    unique_rounds = sorted(set(rounds.tolist()))
    if len(unique_rounds) < 2:
        raise SystemExit(f"周回が{len(unique_rounds)}つしかありません。学習曲線には2周以上のデータが必要です")

    results = []
    for k in range(1, len(unique_rounds)):
        all_true, all_pred = [], []
        for held_out in unique_rounds:
            train_rounds = [r for r in unique_rounds if r != held_out][:k]
            train_mask = np.isin(rounds, train_rounds)
            test_mask = rounds == held_out
            if len(set(y[train_mask].tolist())) < 2:
                continue  # 1クラスしかないと分類器を学習できない
            model = create_model(algo, seed, X.shape[1:], train_config)
            model.fit(X[train_mask], y[train_mask], rounds[train_mask])
            all_true.extend(y[test_mask].tolist())
            all_pred.extend(model.predict(X[test_mask]).tolist())
        y_true, y_pred = np.asarray(all_true), np.asarray(all_pred)
        empty_recall = (recall_score(y_true, y_pred, labels=[0], average="macro", zero_division=0)
                        if 0 in y_true else float("nan"))
        results.append({
            "k": k,
            "accuracy": accuracy_score(y_true, y_pred),
            "macro_f1": f1_score(y_true, y_pred, average="macro"),
            "empty_recall": empty_recall,
        })
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="learning curve over the number of collection rounds")
    add_dataset_args(parser)
    parser.add_argument("--windows", default="0,20",
                        help="比べる窓の長さ（フレーム数、カンマ区切り）。0は1フレーム特徴量（旧方式）")
    parser.add_argument("--stride", type=int, default=4,
                        help="特徴量をNフレームおきに間引く（速くするため。窓特徴量は隣どうしがほぼ同じ）")
    parser.add_argument("--plateau", type=float, default=0.02,
                        help="1周増やしたときのmacro-F1の伸びがこれ未満なら「頭打ち」と判定する")
    add_deep_args(parser)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    logging.getLogger("dataset").setLevel(logging.WARNING)  # セッションごとのログは省く
    logging.getLogger("deep").setLevel(logging.WARNING)
    input_kind = INPUT_KIND[args.algo]
    train_config = train_config_from_args(args) if is_deep(args.algo) else None

    zone_config = load_zones(args.zones)
    data = load_training_data(args.data, args.baseline, zone_config, args.sensors)
    windows = [int(w) for w in args.windows.split(",") if w.strip()]

    table = []
    for window in windows:
        ds = build_dataset(data.records, data.sensor_ids, data.expected_len, data.baseline,
                           args.interval, args.max_age, zone_config,
                           window_frames=window, stride=max(args.stride, 1), input_kind=input_kind)
        n_rounds = len(set(ds.rounds.tolist()))
        logger.info("window=%d: %d frames x %s features, %d rounds",
                    window, ds.X.shape[0], "x".join(str(d) for d in ds.X.shape[1:]), n_rounds)
        for row in learning_curve(ds.X, ds.y, ds.rounds, args.algo, args.seed, train_config):
            table.append({"window": window, **row})

    print()
    print(f"学習曲線（algo={args.algo}、評価は周回単位で取り分け）")
    print(f"{'窓':>4} {'学習周回':>6} {'正解率':>7} {'macro-F1':>9} {'empty再現率':>11}")
    for row in table:
        print(f"{row['window']:>5} {row['k']:>8} {row['accuracy']:>9.3f} {row['macro_f1']:>9.3f} "
              f"{row['empty_recall']:>13.3f}")

    print()
    for window in windows:
        rows = [r for r in table if r["window"] == window]
        if len(rows) < 2:
            print(f"窓={window}: 比較できる周回数が足りません（3周以上で判定できます）")
            continue
        gain = rows[-1]["macro_f1"] - rows[-2]["macro_f1"]
        verdict = "頭打ち（これ以上周回を増やしても効果は薄い）" if gain < args.plateau \
            else "まだ伸びている（周回を追加する価値あり）"
        print(f"窓={window}: 最後の1周でmacro-F1 {gain:+.3f} → {verdict}")
    if len(windows) > 1:
        best = max((r for r in table if r["k"] == max(t["k"] for t in table)), key=lambda r: r["macro_f1"])
        print(f"全周回で学習したときに最も良い窓: {best['window']}（macro-F1 {best['macro_f1']:.3f}）")


if __name__ == "__main__":
    main()
