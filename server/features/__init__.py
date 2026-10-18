"""特徴量（学習・推定で共用）。モデルの入力の種類（input_kind）ごとに作り方が決まる。

  "stats"  … features/stats.py  窓の平均・標準偏差など（RandomForest / kNN / MLP）
  "window" … features/tensor.py 生の窓（CNN）
"""
from __future__ import annotations

import numpy as np

from .stats import WindowFeaturizer, feature_length
from .tensor import WindowTensorizer

INPUT_KINDS = ["stats", "window"]


def make_featurizer(input_kind: str, sensor_ids: list[int], baseline: dict[int, np.ndarray] | None,
                    window_frames: int):
    """push(frame) で特徴量（窓がたまるまではNone）を返すオブジェクトを作る。"""
    if input_kind == "stats":
        return WindowFeaturizer(sensor_ids, baseline, window_frames)
    if input_kind == "window":
        return WindowTensorizer(sensor_ids, baseline, window_frames)
    raise ValueError(f"unknown input kind: {input_kind}")


def input_shape(input_kind: str, n_sensors: int, n_subcarriers: int, window_frames: int) -> tuple[int, ...]:
    """1サンプルの形（推論前のウォームアップ用）。"""
    if input_kind == "stats":
        return (feature_length(n_sensors, n_subcarriers, window_frames),)
    if input_kind == "window":
        return (window_frames, n_sensors, n_subcarriers)
    raise ValueError(f"unknown input kind: {input_kind}")
