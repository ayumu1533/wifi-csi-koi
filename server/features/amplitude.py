"""振幅ベクトルの正規化と、ベースライン（浴槽が空の状態の平均振幅パターン）の算出。

どの特徴量（統計特徴量・今後の生の窓など）でも最初に通す共通の前処理。
  - normalize_amplitude: 振幅ベクトルを平均値で割って正規化する
    （ESP32のAGCや送信電力の揺らぎによるスケール変動を吸収する）
  - compute_baseline: 空の浴槽で記録したデータから、センサーごとの平均正規化振幅を求める
    （特徴量から差し引いて、固定的なマルチパス成分を除去するのに使う）
"""
from __future__ import annotations

import numpy as np

_EPS = 1e-9


def normalize_amplitude(amplitude: np.ndarray) -> np.ndarray:
    amplitude = np.asarray(amplitude, dtype=np.float64)
    return amplitude / (amplitude.mean() + _EPS)


def compute_baseline(records: list[dict], sensor_ids: list[int]) -> dict[int, np.ndarray]:
    """空の浴槽で記録したJSONLレコード群から、センサーごとの平均正規化振幅を求める。

    receiver.py / collect.py どちらの出力形式でも読める
    （sensor_id と amplitude フィールドだけを使う）。
    """
    sums: dict[int, np.ndarray] = {}
    counts: dict[int, int] = {}
    for rec in records:
        sid = rec["sensor_id"]
        if sid not in sensor_ids:
            continue
        amp = normalize_amplitude(np.asarray(rec["amplitude"], dtype=np.float64))
        if sid in sums:
            if len(amp) != len(sums[sid]):
                continue
            sums[sid] += amp
            counts[sid] += 1
        else:
            sums[sid] = amp.copy()
            counts[sid] = 1

    missing = [sid for sid in sensor_ids if sid not in sums]
    if missing:
        raise ValueError(f"baseline data has no records for sensors: {missing}")
    return {sid: sums[sid] / counts[sid] for sid in sums}
