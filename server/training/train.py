"""収集済みのラベル付きCSIデータからゾーン分類モデルを学習して保存する。

使い方:
    python -m training.train --data data/calib.jsonl --model-out model.pkl
    python -m training.train --data data/*.jsonl --algo rf --baseline data/empty.jsonl
    python -m training.train --data data/*.jsonl --split date --model-out model.pkl
    python -m training.train --data data/calib.jsonl --baseline data/baseline.jsonl \\
        --split round --model-out data/model.pkl
    # 深層学習（PyTorchが必要）
    python -m training.train --data data/calib.jsonl --baseline data/baseline.jsonl \\
        --algo cnn2d --window 40 --split round --stride 2 --model-out data/model_cnn2d.pkl

--algo:
  rf / et / knn       従来の機械学習（models/classic/）。入力は統計特徴量。et（ExtraTrees）が標準
  mlp                 深層学習（models/deep/）。入力は統計特徴量（rf と同じ387次元）
  cnn1d / cnn2d       深層学習（models/deep/）。入力は生の窓（features/tensor.py）

処理:
  1. JSONLをセッションごとに分け、FrameAssemblerで学習用フレームを再構成（training/dataset.py）
  2. フレーム → 特徴量（--algo に合った種類。推論時と同一の処理）。
     --window N（既定20 = 約1秒）で直近Nフレームを1サンプルにする。統計特徴量なら0で1フレーム特徴量
  3. セッション単位（--split date なら収集日単位、round なら周回単位）のleave-one-group-outで評価
     （正解率・macro-F1・混同行列・ゾーンごとの再現率。training/evaluate.py）。
     いずれかのゾーンがグループ2つ未満の場合は、そのゾーンを正しく評価できないため
     警告を出して評価そのものをスキップする（保存されるモデルは全データで学習される）
  4. 全データで再学習してモデル一式（zones.json・クラス一覧・学習条件込み）をpickle保存
"""
from __future__ import annotations

import argparse
import logging
import os
import pickle
import platform
from datetime import datetime

import numpy as np
import sklearn

from config.zones import load_zones
from models.registry import INPUT_KIND, create_model, is_deep
from .dataset import (
    DEFAULT_WINDOW_FRAMES, add_dataset_args, add_deep_args, build_dataset, load_training_data,
    train_config_from_args,
)
from .evaluate import evaluate_leave_one_group_out, save_confusion_matrix_csv, zone_group_counts

logger = logging.getLogger("train")


def library_versions(deep: bool) -> dict:
    versions = {
        "python": platform.python_version(),
        "numpy": np.__version__,
        "scikit_learn": sklearn.__version__,
    }
    if deep:
        import torch
        versions["torch"] = torch.__version__
    return versions


def build_run_info(args: argparse.Namespace, paths: list[str], sensor_ids: list[int],
                   n_records: int, group_counts: dict[int, int]) -> dict:
    """学習・評価の再現性のため、使ったデータ・パラメータ・環境をbundleとログの両方に残す(B2)。"""
    info = {
        "data_files": paths,
        "baseline_file": args.baseline,
        "n_records": n_records,
        "sensor_ids": sensor_ids,
        "split_by": args.split,
        "zone_group_counts": group_counts,
        "cli_args": vars(args),
        "seed": args.seed,
        "library_versions": library_versions(is_deep(args.algo)),
        "trained_at": datetime.now().isoformat(timespec="seconds"),
    }
    logger.info("run info: data_files=%s baseline_file=%s n_records=%d split_by=%s seed=%d",
                paths, args.baseline, n_records, args.split, args.seed)
    logger.info("run info: library_versions=%s", info["library_versions"])
    return info


def main() -> None:
    parser = argparse.ArgumentParser(description="train zone classification model from labeled CSI")
    add_dataset_args(parser)
    parser.add_argument("--window", type=int, default=DEFAULT_WINDOW_FRAMES,
                        help="1サンプルにする直近のフレーム数（既定20 = 約1秒）。統計特徴量なら0で1フレーム特徴量（旧方式）")
    parser.add_argument("--stride", type=int, default=1,
                        help="特徴量をNフレームおきに間引いて学習・評価する（速くなる。保存モデルの推論は毎フレーム）")
    parser.add_argument("--split", choices=["session", "date", "round"], default="session",
                        help="評価時にleave-one-group-outでまとめる単位。"
                             "roundは周回単位（collection.planのデータ。それ以外はゾーンごとのセッション順）で、"
                             "フォールド数が少なく速い。dateは環境変化への頑健性評価用")
    parser.add_argument("--eval-smooth", type=float, default=3.0,
                        help="評価時に、推定（--proba-window）と同じく確率を直近この秒数で平均した結果も出す（0で出さない）")
    parser.add_argument("--model-out", required=True, help="学習済みモデルの保存先(.pkl)")
    parser.add_argument("--report-out", default=None,
                        help="混同行列CSVの保存先（省略時は<model-out>.confusion.csv）")
    add_deep_args(parser)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    input_kind = INPUT_KIND[args.algo]
    train_config = train_config_from_args(args) if is_deep(args.algo) else None
    if input_kind == "window" and args.window < 2:
        raise SystemExit(f"--algo {args.algo} は生の窓を使うため --window を2以上にしてください")

    os.makedirs(os.path.dirname(args.model_out) or ".", exist_ok=True)
    zone_config = load_zones(args.zones)
    data = load_training_data(args.data, args.baseline, zone_config, args.sensors)

    ds = build_dataset(data.records, data.sensor_ids, data.expected_len, data.baseline,
                       args.interval, args.max_age, zone_config,
                       window_frames=args.window, stride=max(args.stride, 1), input_kind=input_kind)
    X, y = ds.X, ds.y
    n_zones = len(set(y.tolist()))
    logger.info("dataset: %d frames x %s features (%s), %d zones (window=%d frames, stride=%d)",
                X.shape[0], "x".join(str(d) for d in X.shape[1:]), input_kind, n_zones,
                args.window, args.stride)

    if args.split == "date" and any(d is None for d in ds.dates.tolist()):
        raise SystemExit(
            "--split date には全レコードに'date'フィールドが必要です（旧いcollect.pyで収集した"
            "データが混ざっています）。--split session を使うか、新しいcollection.collectで再収集してください"
        )
    groups = ds.groups(args.split)

    def make_model():
        return create_model(args.algo, args.seed, X.shape[1:], train_config)

    group_counts = zone_group_counts(y, groups)
    insufficient = {zid: n for zid, n in group_counts.items() if n < 2}
    if insufficient:
        logger.warning(
            "skipping %s-based evaluation: zones with fewer than 2 %ss (cannot leave one out and still "
            "train on that zone): %s. collect more sessions for these zones to enable evaluation.",
            args.split, args.split, insufficient,
        )
        metrics = {"evaluated": False, "reason": "insufficient_groups", "zone_group_counts": group_counts}
    else:
        logger.info("evaluating %s with leave-one-%s-out (%d groups)",
                    args.algo, args.split, len(set(groups.tolist())))
        smooth_samples = round(args.eval_smooth / (args.interval * max(args.stride, 1))) if args.eval_smooth > 0 else 0
        metrics = evaluate_leave_one_group_out(X, y, groups, make_model, zone_config,
                                               sessions=ds.sessions, smooth_samples=smooth_samples)
        report_out = args.report_out or (args.model_out + ".confusion.csv")
        save_confusion_matrix_csv(report_out, metrics["confusion_matrix"], metrics["zone_ids"], zone_config)

    metrics["split_by"] = args.split
    metrics["zone_group_counts"] = group_counts

    run_info = build_run_info(args, data.paths, data.sensor_ids, len(data.records), group_counts)

    # 評価が終わったら（またはスキップした場合もそのまま）全データで学習し直して保存する
    model = make_model()
    model.fit(X, y, groups)
    run_info["model"] = model.describe()

    bundle = {
        "model": model,
        "sensor_ids": data.sensor_ids,
        "expected_len": data.expected_len,
        "baseline": {sid: b.tolist() for sid, b in data.baseline.items()} if data.baseline else None,
        "interval_s": args.interval,
        "max_age_s": args.max_age,
        "window_frames": args.window,
        "input_kind": input_kind,
        "algo": args.algo,
        "zones": zone_config.to_dict(),
        "classes": model.classes,
        "metrics": metrics,
        "run_info": run_info,
    }
    with open(args.model_out, "wb") as f:
        pickle.dump(bundle, f)
    logger.info("model saved to %s", args.model_out)


if __name__ == "__main__":
    main()
