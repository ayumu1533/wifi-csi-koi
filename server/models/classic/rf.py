"""RandomForest（決定木200本の多数決）によるゾーン分類器。現在の標準モデル。"""
from __future__ import annotations

from sklearn.ensemble import RandomForestClassifier
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler


def make_rf_pipeline(seed: int) -> Pipeline:
    # ゾーンごとの収集時間が揃わずクラス数の偏りが出やすいので、
    # class_weight="balanced" で少数クラス（滞在時間が短いゾーン）の重みを上げる
    model = RandomForestClassifier(
        n_estimators=200, n_jobs=-1, random_state=seed, class_weight="balanced"
    )
    return Pipeline([("scaler", StandardScaler()), ("model", model)])
