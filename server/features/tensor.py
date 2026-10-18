"""フレーム → 生の窓テンソルへの変換。CNN（1D / 2D）の入力。学習(training)と推論(localization)で共用する。

統計特徴量（features/stats.py）は窓を平均・標準偏差にまとめるため時間の順番が消えるが、
こちらは直近 window_frames フレームをそのまま並べ、時間方向の変化もネットワークに渡す。

各フレームの処理は統計特徴量と同じ:
  1. 各センサーの振幅ベクトルを平均値で割って正規化
  2. ベースライン（浴槽が空の状態の平均振幅パターン）を引く
出力の形は (窓フレーム数, センサー数, サブキャリア数)。1D/2Dの並べ替えはネットワーク側で行う。
"""
from __future__ import annotations

from collections import deque

import numpy as np

from csi_receiver.frames import Frame
from .amplitude import normalize_amplitude


class WindowTensorizer:
    """フレームを順に受け取り、窓がたまったら (窓フレーム数, センサー数, サブキャリア数) を返す。

    最初の window_frames-1 フレームはNoneを返す。WindowFeaturizer と同じ使い方。
    """

    def __init__(
        self,
        sensor_ids: list[int],
        baseline: dict[int, np.ndarray] | None,
        window_frames: int,
    ) -> None:
        if window_frames < 2:
            raise ValueError("生の窓は2フレーム以上必要です（--window を2以上にする）")
        self._sensor_ids = list(sensor_ids)
        self._baseline = baseline
        self._window_frames = window_frames
        self._buffer: deque[np.ndarray] = deque(maxlen=window_frames)

    def reset(self) -> None:
        self._buffer.clear()

    def push(self, frame: Frame) -> np.ndarray | None:
        rows = []
        for sid in self._sensor_ids:
            amp = normalize_amplitude(frame.amplitudes[sid])
            if self._baseline is not None and sid in self._baseline:
                amp = amp - self._baseline[sid]
            rows.append(amp)
        self._buffer.append(np.stack(rows).astype(np.float32))
        if len(self._buffer) < self._window_frames:
            return None
        return np.stack(self._buffer)
