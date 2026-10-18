"""MLP（全結合ネット）。入力は RandomForest と同じ統計特徴量（387次元）。

「特徴量は同じで、モデルだけを深層学習に変えたら精度が上がるか」を確かめるためのモデル。
  387 → 256 → 128 → クラス数（ReLU、BatchNorm、Dropout）
"""
from __future__ import annotations

import torch
from torch import nn


class MLP(nn.Module):
    def __init__(self, input_shape: tuple[int, ...], n_classes: int, dropout: float = 0.3) -> None:
        super().__init__()
        (n_features,) = input_shape
        self.net = nn.Sequential(
            nn.Linear(n_features, 256), nn.BatchNorm1d(256), nn.ReLU(), nn.Dropout(dropout),
            nn.Linear(256, 128), nn.BatchNorm1d(128), nn.ReLU(), nn.Dropout(dropout),
            nn.Linear(128, n_classes),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:  # x: (バッチ, 特徴量次元)
        return self.net(x)
