"""グループ単位（セッション / 収集日 / 周回）の leave-one-group-out 評価と、分類指標の集計。

10ms間隔のほぼ同一フレームが学習・評価の両方に混ざるフレーム単位ランダム分割の
データリークを避けるため、必ずグループ単位で学習側と評価側を分ける。
"""
from __future__ import annotations

import csv
import logging
import time
from collections import defaultdict
from collections.abc import Callable

import numpy as np
from sklearn.metrics import accuracy_score, confusion_matrix, f1_score

from config.zones import ZoneConfig
from models.base import ZoneModel
from .dataset import zone_label

logger = logging.getLogger("evaluate")


def zone_group_counts(y: np.ndarray, groups: np.ndarray) -> dict[int, int]:
    """ゾーンごとに、そのゾーンが出現するグループ（セッション or 日付）の数を数える。"""
    by_zone: dict[int, set] = defaultdict(set)
    for zid, g in zip(y.tolist(), groups.tolist()):
        by_zone[int(zid)].add(g)
    return {zid: len(gs) for zid, gs in by_zone.items()}


def evaluate_leave_one_group_out(
    X: np.ndarray, y: np.ndarray, groups: np.ndarray, make_model: Callable[[], ZoneModel],
    zone_config: ZoneConfig, sessions: np.ndarray | None = None, smooth_samples: int = 0,
) -> dict:
    """グループ（セッション/日付）を1つずつ除いて学習→評価し、全フォールドの予測をまとめて集計する。

    同じグループのフレームは学習と評価に分かれて混ざらないため、
    フレーム単位のランダム分割にあったデータリークが起きない。
    make_model はフォールドごとに未学習のモデルを作る関数（models.registry.create_model）。
    smooth_samples > 1 なら、推定時（localization の --proba-window）と同じく、セッション内で
    直近 smooth_samples サンプルの確率を平均してから判定した結果も "smoothed" として集計する。
    学習側のグループも渡すので、深層学習は学習側の周だけで早期終了の検証を行う（テストの周は見ない）。
    """
    unique_groups = sorted(set(groups.tolist()))
    all_true, all_pred, all_pred_smooth = [], [], []
    fold_accuracy = {}
    started = time.monotonic()
    for held_out in unique_groups:
        test_mask = groups == held_out
        train_mask = ~test_mask
        model = make_model()
        model.fit(X[train_mask], y[train_mask], groups[train_mask])
        proba = model.predict_proba(X[test_mask])
        classes = np.asarray(model.classes)
        pred = classes[np.argmax(proba, axis=1)]
        if smooth_samples > 1 and sessions is not None:
            smoothed = causal_moving_average(proba, sessions[test_mask], smooth_samples)
            all_pred_smooth.extend(classes[np.argmax(smoothed, axis=1)].tolist())
        fold_acc = accuracy_score(y[test_mask], pred)
        fold_accuracy[str(held_out)] = float(fold_acc)
        logger.info("  fold held_out=%s: n_test=%d accuracy=%.3f", held_out, test_mask.sum(), fold_acc)
        all_true.extend(y[test_mask].tolist())
        all_pred.extend(pred.tolist())

    metrics = log_evaluation(np.asarray(all_true), np.asarray(all_pred), zone_config)
    if all_pred_smooth:
        logger.info("---- with probability moving average over %d samples (same as --proba-window) ----",
                    smooth_samples)
        metrics["smoothed"] = log_evaluation(np.asarray(all_true), np.asarray(all_pred_smooth), zone_config)
        metrics["smoothed"]["smooth_samples"] = smooth_samples
    metrics["evaluated"] = True
    metrics["n_folds"] = len(unique_groups)
    metrics["fold_accuracy"] = fold_accuracy
    metrics["eval_seconds"] = round(time.monotonic() - started, 1)
    return metrics


def causal_moving_average(proba: np.ndarray, sessions: np.ndarray, n: int) -> np.ndarray:
    """セッションごとに、各サンプルの確率を「自分を含む直近n個」の平均に置き換える（未来は見ない）。

    サンプルはセッション内で時刻順に並んでいる前提（training.dataset.build_dataset の出力順）。
    """
    out = np.empty_like(proba)
    for sid in dict.fromkeys(sessions.tolist()):
        idx = np.where(sessions == sid)[0]
        csum = np.vstack([np.zeros((1, proba.shape[1])), np.cumsum(proba[idx], axis=0)])
        ends = np.arange(1, len(idx) + 1)
        starts = np.maximum(ends - n, 0)
        out[idx] = (csum[ends] - csum[starts]) / (ends - starts)[:, None]
    return out


def log_evaluation(y_test: np.ndarray, pred: np.ndarray, zone_config: ZoneConfig) -> dict:
    """正解率・macro-F1・混同行列・ゾーンごとの再現率をログ表示し、metrics dictを返す。"""
    accuracy = accuracy_score(y_test, pred)
    macro_f1 = f1_score(y_test, pred, average="macro")
    zone_ids = sorted(set(y_test.tolist()) | set(pred.tolist()))
    cm = confusion_matrix(y_test, pred, labels=zone_ids)

    logger.info("evaluation: accuracy=%.3f macro-F1=%.3f (n=%d)",
                accuracy, macro_f1, len(y_test))

    header = "true\\pred".ljust(14) + "".join(f"{zid:>6d}" for zid in zone_ids)
    logger.info(header)
    for i, zid in enumerate(zone_ids):
        row = "".join(f"{v:>6d}" for v in cm[i])
        logger.info("%s%s", f"{zid} ({zone_label(zone_config, zid)})".ljust(14), row)

    recalls = {}
    for i, zid in enumerate(zone_ids):
        row_sum = cm[i].sum()
        recall = cm[i, i] / row_sum if row_sum > 0 else float("nan")
        recalls[zid] = float(recall)
        logger.info("  zone %d (%s): recall=%.3f (n=%d)",
                    zid, zone_label(zone_config, zid), recall, row_sum)

    return {
        "accuracy": float(accuracy),
        "macro_f1": float(macro_f1),
        "zone_ids": zone_ids,
        "confusion_matrix": cm.tolist(),
        "per_zone_recall": recalls,
        "n_test": len(y_test),
    }


def save_confusion_matrix_csv(path: str, cm: list[list[int]], zone_ids: list[int], zone_config: ZoneConfig) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["true\\pred"] + [f"{zid}:{zone_label(zone_config, zid)}" for zid in zone_ids])
        for zid, row in zip(zone_ids, cm):
            writer.writerow([f"{zid}:{zone_label(zone_config, zid)}"] + list(row))
    logger.info("confusion matrix saved to %s", path)
