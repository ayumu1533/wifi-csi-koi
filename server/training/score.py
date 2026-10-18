"""学習済みモデルを、あとから収集した別のデータ（テスト用）で採点する。

リアルタイム推定の記録（estimate.csv）には「その時どこにいたか」が無いため、当たった割合を出せない。
そこで、学習後に collection.plan でテスト用のデータを別フォルダに取り（ゾーンの正解付き）、
保存済みのモデルをそのまま当てて採点する。学習には一切使わないので、時間がたったときの当たり方がわかる。

使い方（server/ で実行）:
    python -m training.score --model data/models/d3/et.pkl --data data/d3_fan_20261012/test1/calib.jsonl

  - 基準（baseline）はモデルに保存されたもの（学習時の基準）を使う。リアルタイム推定と同じ条件。
    --baseline を付けると、その基準で特徴量を作り直す（基準の取り直しで直るかを試す用）。
  - 窓・フレーム同期の設定もモデルに保存されたものを使う。
"""
from __future__ import annotations

import argparse
import logging
import pickle
from collections import Counter

import numpy as np

from config.zones import zone_config_from_dict
from training.dataset import build_dataset, load_baseline, load_training_data
from training.evaluate import causal_moving_average, log_evaluation

logger = logging.getLogger("score")


def main() -> None:
    parser = argparse.ArgumentParser(description="score a trained model on separately collected test data")
    parser.add_argument("--model", required=True, help="training.train で保存したモデル(.pkl)")
    parser.add_argument("--data", nargs="+", required=True, help="テスト用の calib.jsonl（collection.plan で収集）")
    parser.add_argument("--baseline", default=None, help="基準を差し替える場合の baseline.jsonl（省略時はモデルの基準）")
    parser.add_argument("--stride", type=int, default=2, help="採点に使うフレームの間引き（学習と同じ2が既定）")
    parser.add_argument("--proba-window", type=float, default=3.0, help="確率の移動平均の秒数（推定の --proba-window と同じ）")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    with open(args.model, "rb") as f:
        bundle = pickle.load(f)
    model = bundle["model"]
    zone_config = zone_config_from_dict(bundle["zones"])
    sensors = ",".join(str(s) for s in bundle["sensor_ids"])
    data = load_training_data(args.data, None, zone_config, sensors)
    if args.baseline:
        baseline, _ = load_baseline(args.baseline, bundle["sensor_ids"])
    elif bundle["baseline"] is not None:
        baseline = {int(sid): np.asarray(b) for sid, b in bundle["baseline"].items()}
    else:
        baseline = None

    ds = build_dataset(data.records, bundle["sensor_ids"], bundle["expected_len"], baseline,
                       bundle["interval_s"], bundle["max_age_s"], zone_config,
                       window_frames=bundle["window_frames"], stride=max(args.stride, 1),
                       input_kind=bundle["input_kind"])
    X, y = ds.X, ds.y
    logger.info("test: %d frames, zones %s", len(y), sorted(set(y.tolist())))

    proba = model.predict_proba(X)
    classes = np.asarray(model.classes)
    pred = classes[proba.argmax(axis=1)]
    logger.info("---- 1フレームずつ ----")
    log_evaluation(y, pred, zone_config)

    n = max(1, round(args.proba_window / (bundle["interval_s"] * max(args.stride, 1))))
    smoothed = classes[causal_moving_average(proba, ds.sessions, n).argmax(axis=1)]
    logger.info("---- 確率の移動平均 %d サンプル（約%.0f秒）----", n, args.proba_window)
    log_evaluation(y, smoothed, zone_config)

    pmax = proba.max(axis=1)
    logger.info("一番高い確率: 中央値 %.2f、0.5超の割合 %.2f", float(np.median(pmax)), float(np.mean(pmax > 0.5)))

    logger.info("---- 記録ごと（収集順）----")
    for s in dict.fromkeys(ds.sessions.tolist()):
        m = ds.sessions == s
        z = int(y[m][0])
        top, cnt = Counter(pred[m].tolist()).most_common(1)[0]
        logger.info("  round %s zone %d: 正解率 %.2f  最多の判定 %d (%.0f%%)",
                    ds.rounds[m][0], z, float(np.mean(pred[m] == z)), top, 100 * cnt / m.sum())


if __name__ == "__main__":
    main()
