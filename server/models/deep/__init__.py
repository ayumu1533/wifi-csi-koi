"""深層学習（PyTorch）によるゾーン分類器。PyTorchを使うのはこのフォルダだけ。

  mlp.py     MLP（入力: 統計特徴量。RandomForestと同じ387次元）
  cnn.py     CNN1D（時間方向） / CNN2D（時間 × サブキャリア）（入力: 生の窓）
  trainer.py 学習ループ（早期終了・クラスの重み・データ拡張）
  model.py   共通の形（ZoneModel）に包むクラス、標準化、保存と読み込み
"""
from .model import DEEP_ALGOS, INPUT_KINDS, DeepZoneModel
from .trainer import TrainConfig

__all__ = ["DEEP_ALGOS", "INPUT_KINDS", "DeepZoneModel", "TrainConfig"]
