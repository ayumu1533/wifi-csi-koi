"""従来の機械学習（scikit-learn）によるゾーン分類器。

入力は features/stats.py の統計特徴量。rf.py / knn.py のパイプラインを ClassicModel で共通の形に包む。
"""
from __future__ import annotations

import numpy as np
from sklearn.pipeline import Pipeline

from models.base import ZoneModel
from .et import make_et_pipeline
from .knn import make_knn_pipeline
from .rf import make_rf_pipeline

CLASSIC_ALGOS = ["rf", "et", "knn"]


def make_pipeline(algo: str, seed: int) -> Pipeline:
    if algo == "knn":
        return make_knn_pipeline()
    if algo == "rf":
        return make_rf_pipeline(seed)
    if algo == "et":
        return make_et_pipeline(seed)
    raise ValueError(f"unknown algo: {algo}")


class ClassicModel(ZoneModel):
    family = "classic"
    input_kind = "stats"

    def __init__(self, algo: str, seed: int, pipeline: Pipeline | None = None) -> None:
        self.algo = algo
        self.seed = seed
        self.pipeline = pipeline if pipeline is not None else make_pipeline(algo, seed)
        self.classes = (self.pipeline.named_steps["model"].classes_.tolist()
                        if hasattr(self.pipeline.named_steps["model"], "classes_") else [])

    def fit(self, X: np.ndarray, y: np.ndarray, groups: np.ndarray | None = None) -> None:
        self.pipeline.fit(X, y)  # グループは使わない
        self.classes = self.pipeline.named_steps["model"].classes_.tolist()

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        return self.pipeline.predict_proba(X)

    def predict(self, X: np.ndarray) -> np.ndarray:
        return self.pipeline.predict(X)

    def prepare_realtime(self) -> None:
        # 1件ずつの推論では並列化（RandomForestのn_jobs=-1）の起動コストの方が大きい
        # （実測: n_jobs=-1で約77ms/回、n_jobs=1で約17ms/回）。フレーム周期50msに間に合うよう1スレッドにする
        for _, step in self.pipeline.steps:
            if "n_jobs" in step.get_params():
                step.set_params(n_jobs=1)

    def describe(self) -> dict:
        return {"seed": self.seed, "params": self.pipeline.named_steps["model"].get_params()}
