"""すべてのゾーン分類モデルが持つ共通の形。

学習（training）と推定（localization）はこの形だけを使うので、モデルの種類を知らなくてよい。
  - fit(X, y, groups)   学習する。groups は周回などのグループ（深層学習の早期終了の検証用に使う）
  - predict_proba(X)    各ゾーンの確率（列の順は classes）
  - predict(X)          確率が最大のゾーンID
  - prepare_realtime()  1件ずつ推論するリアルタイム用の設定（スレッド数など）
"""
from __future__ import annotations

import numpy as np


class ZoneModel:
    algo: str = ""          # "rf" / "et" / "knn" / "mlp" / "cnn1d" / "cnn2d"
    family: str = ""        # "classic"（従来の機械学習）/ "deep"（深層学習）
    input_kind: str = ""    # "stats"（統計特徴量）/ "window"（生の窓）。features/__init__.py
    classes: list[int]      # predict_proba の列に対応するゾーンID

    def fit(self, X: np.ndarray, y: np.ndarray, groups: np.ndarray | None = None) -> None:
        raise NotImplementedError

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        raise NotImplementedError

    def predict(self, X: np.ndarray) -> np.ndarray:
        proba = self.predict_proba(X)
        return np.asarray(self.classes)[np.argmax(proba, axis=1)]

    def prepare_realtime(self) -> None:
        pass

    def describe(self) -> dict:
        """モデルbundleとログに残す設定（ハイパーパラメータなど）。"""
        return {}
