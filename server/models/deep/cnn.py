"""CNN（畳み込みニューラルネット）。入力は生の窓 (窓フレーム数, TX数, サブキャリア数)（features/tensor.py）。

「平均・標準偏差に手でまとめず、波形から特徴をネットワークに見つけさせたら精度が上がるか」を確かめるモデル。
2種類あり、フィルタ（小さな窓）を滑らせる方向が違う。

CNN1D（時間方向だけ）:
  TX×サブキャリア = 192本の波形をチャンネルとして並べ、フィルタを時間方向にだけ滑らせる。
  最初の層から全サブキャリア・全TXの組み合わせを見る（例: TX1とTX3が同時に変わる）。
  入力 (192, 窓フレーム数)

CNN2D（時間 × サブキャリア）:
  TXを3チャンネルの画像（縦 = 時間、横 = サブキャリア）として扱い、フィルタを縦横に滑らせる。
  隣り合うサブキャリアの形（周波数方向の山・谷）をとらえ、同じフィルタを全サブキャリアで使い回す。
  入力 (3, 窓フレーム数, 64)

どちらも最後に全体平均プーリングでまとめるので、窓の長さ（20 / 40 / 60フレーム）を変えても同じ構造で使える。
"""
from __future__ import annotations

import torch
from torch import nn


class CNN1D(nn.Module):
    def __init__(self, input_shape: tuple[int, ...], n_classes: int, dropout: float = 0.3) -> None:
        super().__init__()
        _, n_sensors, n_subcarriers = input_shape
        channels = n_sensors * n_subcarriers
        self.features = nn.Sequential(
            nn.Conv1d(channels, 64, kernel_size=5, padding=2), nn.BatchNorm1d(64), nn.ReLU(),
            nn.Conv1d(64, 64, kernel_size=5, padding=2), nn.BatchNorm1d(64), nn.ReLU(),
            nn.MaxPool1d(2),
            nn.Conv1d(64, 64, kernel_size=3, padding=1), nn.BatchNorm1d(64), nn.ReLU(),
            nn.AdaptiveAvgPool1d(1),
        )
        self.head = nn.Sequential(nn.Flatten(), nn.Dropout(dropout), nn.Linear(64, n_classes))

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        # x: (バッチ, 時間, TX, サブキャリア) → (バッチ, TX×サブキャリア, 時間)
        b, t, s, k = x.shape
        x = x.reshape(b, t, s * k).transpose(1, 2)
        return self.head(self.features(x))


class CNN2D(nn.Module):
    def __init__(self, input_shape: tuple[int, ...], n_classes: int, dropout: float = 0.3) -> None:
        super().__init__()
        _, n_sensors, _ = input_shape
        self.features = nn.Sequential(
            nn.Conv2d(n_sensors, 16, kernel_size=(3, 5), padding=(1, 2)), nn.BatchNorm2d(16), nn.ReLU(),
            nn.MaxPool2d(2),
            nn.Conv2d(16, 32, kernel_size=(3, 5), padding=(1, 2)), nn.BatchNorm2d(32), nn.ReLU(),
            nn.MaxPool2d(2),
            nn.Conv2d(32, 64, kernel_size=3, padding=1), nn.BatchNorm2d(64), nn.ReLU(),
            nn.AdaptiveAvgPool2d(1),
        )
        self.head = nn.Sequential(nn.Flatten(), nn.Dropout(dropout), nn.Linear(64, n_classes))

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        # x: (バッチ, 時間, TX, サブキャリア) → (バッチ, TX, 時間, サブキャリア)
        x = x.permute(0, 2, 1, 3)
        return self.head(self.features(x))
