"""推定結果の時間方向の安定化: 確率のEMA平滑化（ProbaSmoother）と、多数決による確定ゾーンの切り替え（ZoneHysteresis）。

どのモデル（RandomForest など）の出力確率にも同じようにかける。
"""
from __future__ import annotations

from collections import Counter, deque

import numpy as np


class ProbaSmoother:
    """ゾーン確率ベクトルへの指数移動平均。alphaが大きいほど追従が速く、小さいほど滑らか。"""

    def __init__(self, alpha: float) -> None:
        self._alpha = alpha
        self._value: np.ndarray | None = None

    def update(self, proba: np.ndarray) -> np.ndarray:
        if self._value is None:
            self._value = proba.copy()
        else:
            self._value = self._alpha * proba + (1.0 - self._alpha) * self._value
        return self._value


class ProbaWindowAverage:
    """直近Nフレームのゾーン確率の単純平均（移動平均）。

    EMAより「何秒分を見ているか」がはっきりし、学習時の評価（training.evaluate の --eval-smooth）と
    同じ計算になる。N = 秒数 / フレーム周期（20Hzなら3秒 = 60フレーム）。
    """

    def __init__(self, frames: int) -> None:
        self._buf: deque[np.ndarray] = deque(maxlen=max(1, frames))

    def update(self, proba: np.ndarray) -> np.ndarray:
        self._buf.append(proba)
        return np.mean(self._buf, axis=0)


class ZoneHysteresis:
    """直近Nフレームの生の判定（argmax）の多数決で確定ゾーンを切り替える。

    直近Nフレームのうち、確定ゾーン以外の同じゾーンが ratio 以上を占めたら切り替える。
    連続である必要はないので、途中に外れのフレームが混じっても切り替わる。
    確率が threshold 未満のフレームは票に数えない（0なら全フレームを数える）。
    ゾーン境界付近で判定がわずかな差で入れ替わり続けるチャタリング（揺れ）を防ぐ。
    """

    def __init__(self, threshold: float, frames: int, ratio: float) -> None:
        self._threshold = threshold
        self._ratio = ratio
        self._votes: deque[int | None] = deque(maxlen=max(1, frames))
        self._confirmed: int | None = None

    def update(self, proba: np.ndarray, classes: list[int]) -> tuple[int, int]:
        """(確定ゾーン, 生のargmaxゾーン) を返す。"""
        raw_idx = int(np.argmax(proba))
        raw_zone = classes[raw_idx]
        raw_p = float(proba[raw_idx])

        if self._confirmed is None:
            self._confirmed = raw_zone  # 初回のフレームは即座に確定させる
        self._votes.append(raw_zone if raw_p >= self._threshold else None)

        counts = Counter(v for v in self._votes if v is not None)
        if counts:
            top_zone, top_count = counts.most_common(1)[0]
            if top_zone != self._confirmed and top_count >= self._ratio * self._votes.maxlen:
                self._confirmed = top_zone
                self._votes.clear()  # 切り替え直後に古い票で戻らないようにする
        return self._confirmed, raw_zone
