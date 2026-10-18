"""k近傍法（近い5点の距離重み付き多数決）によるゾーン分類器。RandomForestとの比較用。"""
from __future__ import annotations

from sklearn.neighbors import KNeighborsClassifier
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler


def make_knn_pipeline() -> Pipeline:
    model = KNeighborsClassifier(n_neighbors=5, weights="distance")
    return Pipeline([("scaler", StandardScaler()), ("model", model)])
