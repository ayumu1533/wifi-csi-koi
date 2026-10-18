"""複数センサーのCSIサンプルを「フレーム」（≒同一瞬間のスナップショット）にまとめる。

各csi_txは独立したタイミングで送信し（csi_rxが受信したフレームごとにCSIを得る）、
TX間の送信タイミングは同期していないため、同期はサーバー側の受信時刻(recv_time)で行う。
一定周期(interval_s)ごとに、全センサーの「直近max_age_s以内のサンプル」が
揃っていれば1フレームとして出力する。

学習時（記録済みJSONLの再生）とリアルタイム推定時の両方で同じクラスを使うことで、
特徴量の作られ方が学習と推論でズレないようにしている。
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class Frame:
    t: float                              # フレーム時刻（recv_time基準）
    amplitudes: dict[int, np.ndarray]     # sensor_id -> サブキャリア振幅ベクトル


class FrameAssembler:
    def __init__(
        self,
        sensor_ids: list[int],
        interval_s: float = 0.05,
        max_age_s: float = 0.2,
        expected_len: int | None = None,
    ) -> None:
        if not sensor_ids:
            raise ValueError("sensor_ids must not be empty")
        self.sensor_ids = sorted(sensor_ids)
        self.interval_s = interval_s
        self.max_age_s = max_age_s
        self.expected_len = expected_len  # Noneなら最初のサンプルの長さで確定
        self.skipped_len_mismatch = 0
        self.skipped_unknown_sensor = 0
        self._latest: dict[int, tuple[float, np.ndarray]] = {}
        self._next_tick: float | None = None

    def add(self, sensor_id: int, recv_time: float, amplitude: np.ndarray) -> Frame | None:
        """1サンプル追加。フレームが完成したタイミングでFrameを返す（それ以外はNone）。"""
        if sensor_id not in self._known_ids():
            self.skipped_unknown_sensor += 1
            return None

        amplitude = np.asarray(amplitude, dtype=np.float64)
        if self.expected_len is None:
            self.expected_len = len(amplitude)
        if len(amplitude) != self.expected_len:
            self.skipped_len_mismatch += 1
            return None

        self._latest[sensor_id] = (recv_time, amplitude)

        if self._next_tick is None:
            self._next_tick = recv_time + self.interval_s
            return None
        if recv_time < self._next_tick:
            return None

        # tick到来: 全センサーが新鮮なら1フレーム出力
        self._next_tick = recv_time + self.interval_s
        amplitudes: dict[int, np.ndarray] = {}
        for sid in self.sensor_ids:
            entry = self._latest.get(sid)
            if entry is None or recv_time - entry[0] > self.max_age_s:
                return None  # 欠けているセンサーがある間はフレームを出さない
            amplitudes[sid] = entry[1]
        return Frame(t=recv_time, amplitudes=amplitudes)

    def freshness(self, now: float) -> dict[int, float | None]:
        """デバッグ用: センサーごとの最終受信からの経過秒（未受信はNone）。"""
        out: dict[int, float | None] = {}
        for sid in self.sensor_ids:
            entry = self._latest.get(sid)
            out[sid] = None if entry is None else now - entry[0]
        return out

    def _known_ids(self) -> set[int]:
        return set(self.sensor_ids)
