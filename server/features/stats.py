"""フレーム → 統計特徴量ベクトルへの変換。学習(training)と推論(localization)で共用する。

RandomForest / kNN が使う特徴量。正規化とベースラインは features/amplitude.py。

1フレームの処理（frame_to_features、window_frames < 2 のとき）:
  1. 各センサーの振幅ベクトルを平均値で割って正規化
  2. ベースライン（浴槽が空の状態の平均振幅パターン）があれば差分を取り、
     固定的なマルチパス成分を除去して人体による変化だけを強調する
  3. 全センサー分を連結して1本のベクトルにする

窓特徴量（WindowFeaturizer、window_frames >= 2 のとき）:
  直近 window_frames フレームの正規化振幅をためて、サブキャリアごとに
    - 平均（からベースラインを引いたもの）: 人がいる「位置」による定常的な形の変化
    - 標準偏差: 人の呼吸・体の揺れによる「揺らぎ」（無人ならほぼノイズだけ）
  と、センサーごとの揺らぎの大きさ（標準偏差のサブキャリア平均）を連結する。
  1フレームだけでは区別しにくい「無人」と「人がいる」を、揺らぎで見分けやすくする。
"""
from __future__ import annotations

from collections import deque

import numpy as np

from csi_receiver.frames import Frame
from .amplitude import normalize_amplitude


def frame_to_features(
    frame: Frame,
    sensor_ids: list[int],
    baseline: dict[int, np.ndarray] | None = None,
) -> np.ndarray:
    parts = []
    for sid in sensor_ids:
        amp = normalize_amplitude(frame.amplitudes[sid])
        if baseline is not None and sid in baseline:
            amp = amp - baseline[sid]
        parts.append(amp)
    return np.concatenate(parts)


def feature_length(n_sensors: int, n_subcarriers: int, window_frames: int) -> int:
    """特徴量ベクトルの長さ（推論前のウォームアップ用）。"""
    if window_frames < 2:
        return n_sensors * n_subcarriers
    return 2 * n_sensors * n_subcarriers + n_sensors


class WindowFeaturizer:
    """フレームを順に受け取り、特徴量ベクトルを返す（学習・推論で共用）。

    window_frames < 2 なら1フレーム特徴量（frame_to_features）をそのまま返す。
    それ以外は窓がたまるまで（最初の window_frames-1 フレーム）Noneを返す。
    セッションの切れ目では reset() して、別セッションのフレームが窓に混ざらないようにする。
    """

    def __init__(
        self,
        sensor_ids: list[int],
        baseline: dict[int, np.ndarray] | None,
        window_frames: int,
    ) -> None:
        self._sensor_ids = list(sensor_ids)
        self._baseline = baseline
        self._window_frames = window_frames
        self._buffer: deque[np.ndarray] = deque(maxlen=max(window_frames, 1))

    def reset(self) -> None:
        self._buffer.clear()

    def push(self, frame: Frame) -> np.ndarray | None:
        if self._window_frames < 2:
            return frame_to_features(frame, self._sensor_ids, self._baseline)

        # (センサー数, サブキャリア数) の正規化振幅
        self._buffer.append(
            np.stack([normalize_amplitude(frame.amplitudes[sid]) for sid in self._sensor_ids])
        )
        if len(self._buffer) < self._window_frames:
            return None

        window = np.stack(self._buffer)  # (窓フレーム数, センサー数, サブキャリア数)
        mean = window.mean(axis=0)
        std = window.std(axis=0)
        if self._baseline is not None:
            mean = mean - np.stack([
                self._baseline.get(sid, np.zeros(mean.shape[1])) for sid in self._sensor_ids
            ])
        motion = std.mean(axis=1)  # センサーごとの揺らぎの大きさ
        return np.concatenate([mean.ravel(), std.ravel(), motion])
