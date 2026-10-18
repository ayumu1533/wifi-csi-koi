"""ExtraTrees（ランダム性を強めた決定木400本の多数決）によるゾーン分類器。

2026-10-11の比較（10/4データ、周回単位の交差検証）で RandomForest より正解率が4〜6ポイント高く、
学習で見ていない姿勢への汎化が良かったため、現在の標準モデルにする。
分割位置もランダムに選ぶため、特定のサブキャリアの細かい値に過剰に合わせにくい。
"""
from __future__ import annotations

from sklearn.ensemble import ExtraTreesClassifier
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler


def make_et_pipeline(seed: int) -> Pipeline:
    model = ExtraTreesClassifier(
        n_estimators=400, min_samples_leaf=2, max_features="sqrt",
        n_jobs=-1, random_state=seed, class_weight="balanced",
    )
    return Pipeline([("scaler", StandardScaler()), ("model", model)])
