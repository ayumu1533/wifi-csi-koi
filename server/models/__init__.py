"""ゾーン分類モデル。どのモデルも models/base.py の共通の形（ZoneModel）を持つ。

  models/base.py     共通の形（fit / predict_proba / predict / prepare_realtime）
  models/registry.py --algo の名前からモデルを作る・保存済みモデルを読み込む
  models/classic/    従来の機械学習（scikit-learn: rf = RandomForest, et = ExtraTrees, knn = k近傍法）
  models/deep/       深層学習（PyTorch: mlp, cnn1d, cnn2d）
"""
