"""--algo の名前からモデルを作る・保存済みモデルを読み込む窓口。

学習（training）と推定（localization）はここだけを通してモデルを扱う。
深層学習（models/deep/）は使うときだけ読み込むので、RandomForestだけならPyTorchが無くても動く。
"""
from __future__ import annotations

import pickle

from models.base import ZoneModel
from models.classic import CLASSIC_ALGOS, ClassicModel

DEEP_ALGO_NAMES = ["mlp", "cnn1d", "cnn2d"]
ALGOS = CLASSIC_ALGOS + DEEP_ALGO_NAMES

# algo → 入力の種類（features/__init__.py の make_featurizer に渡す）
INPUT_KIND = {"rf": "stats", "et": "stats", "knn": "stats", "mlp": "stats", "cnn1d": "window", "cnn2d": "window"}


def is_deep(algo: str) -> bool:
    return algo in DEEP_ALGO_NAMES


def create_model(algo: str, seed: int, input_shape: tuple[int, ...], train_config=None) -> ZoneModel:
    """未学習のモデルを作る。train_config は深層学習のみ（models.deep.TrainConfig）。"""
    if algo in CLASSIC_ALGOS:
        return ClassicModel(algo, seed)
    if is_deep(algo):
        from models.deep import DeepZoneModel
        return DeepZoneModel(algo, seed, input_shape, train_config)
    raise ValueError(f"unknown algo: {algo}")


def load_bundle(path: str) -> dict:
    """training.train が保存したモデル一式を読み込む。

    "model" キーに共通の形（ZoneModel）が入る。旧形式（"pipeline" だけのRandomForest/kNN）も読める。
    """
    with open(path, "rb") as f:
        bundle = pickle.load(f)
    if "model" not in bundle:
        bundle["model"] = ClassicModel(bundle["algo"], seed=0, pipeline=bundle["pipeline"])
    bundle.setdefault("input_kind", bundle["model"].input_kind)
    bundle.setdefault("window_frames", 0)  # 旧bundle（window_framesキー無し）は1フレーム特徴量
    return bundle
