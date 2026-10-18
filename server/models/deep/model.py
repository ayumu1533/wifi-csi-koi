"""深層学習モデル（MLP / CNN1D / CNN2D）を共通の形（models/base.py の ZoneModel）に包む。

学習（fit）の手順:
  1. 入力を標準化する（統計特徴量は次元ごと、生の窓はTX×サブキャリアごとに平均0・分散1）
  2. 学習用グループ（周回）のうち最後の1つを丸ごと検証用に取り分け、残りで学習して
     検証macro-F1が最も良いエポック数を決める（早期終了）。
     テスト用の周や、同じ周の中のデータを検証に使うとデータリークになるため、必ず周を丸ごと分ける
  3. 学習用の全グループで、決めたエポック数だけ最初から学習し直す
     （検証に1周取られたままだと、4周で学習するRandomForestより不利になるため）

学習はGPUがあればGPU（TrainConfig.device）。保存したモデルを読み込むと常にCPUに載るので、
Google Colab（GPU）で学習したモデルを、GPUの無いPCのリアルタイム推定でそのまま使える。
"""
from __future__ import annotations

import logging

import numpy as np
import torch

from models.base import ZoneModel
from .cnn import CNN1D, CNN2D
from .mlp import MLP
from .trainer import TrainConfig, balanced_class_weight, predict_logits, resolve_device, train_network

logger = logging.getLogger("deep")

NETWORKS = {"mlp": MLP, "cnn1d": CNN1D, "cnn2d": CNN2D}
INPUT_KINDS = {"mlp": "stats", "cnn1d": "window", "cnn2d": "window"}
DEEP_ALGOS = list(NETWORKS)


class DeepZoneModel(ZoneModel):
    family = "deep"

    def __init__(self, algo: str, seed: int, input_shape: tuple[int, ...],
                 config: TrainConfig | None = None) -> None:
        self.algo = algo
        self.seed = seed
        self.input_kind = INPUT_KINDS[algo]
        self.input_shape = tuple(input_shape)
        self.config = config or TrainConfig()
        self.classes: list[int] = []
        self.mean: np.ndarray | None = None
        self.std: np.ndarray | None = None
        self.best_epoch: int | None = None
        self.history: list[dict] = []
        self.net: torch.nn.Module | None = None

    def _build_net(self) -> torch.nn.Module:
        torch.manual_seed(self.seed)  # 同じseedなら同じ初期値から学習する
        net = NETWORKS[self.algo](self.input_shape, len(self.classes), dropout=self.config.dropout)
        return net.to(resolve_device(self.config.device))

    def _standardize(self, X: np.ndarray) -> np.ndarray:
        return ((X - self.mean) / self.std).astype(np.float32)

    def fit(self, X: np.ndarray, y: np.ndarray, groups: np.ndarray | None = None) -> None:
        self.classes = sorted(set(np.asarray(y).tolist()))
        index = {c: i for i, c in enumerate(self.classes)}
        y_idx = np.array([index[c] for c in np.asarray(y).tolist()], dtype=np.int64)

        # 標準化の基準（統計特徴量: 次元ごと / 生の窓: 時間方向もまとめてTX×サブキャリアごと）
        axes = (0,) if self.input_kind == "stats" else (0, 1)
        self.mean = X.mean(axis=axes).astype(np.float32)
        std = X.std(axis=axes)
        self.std = np.where(std < 1e-6, 1.0, std).astype(np.float32)  # 常に0のサブキャリアは割らない
        Xs = self._standardize(X)
        weight = balanced_class_weight(y_idx, len(self.classes))

        epochs = self.config.fallback_epochs
        unique_groups = sorted(set(groups.tolist())) if groups is not None else []
        if len(unique_groups) >= 2:
            val_group = unique_groups[-1]
            val_mask = groups == val_group
            net = self._build_net()
            epochs, history = train_network(
                net, Xs[~val_mask], y_idx[~val_mask], weight, self.config, self.seed,
                self.config.max_epochs, Xs[val_mask], y_idx[val_mask],
            )
            self.history = history
            logger.info("    %s: early stopping on group %s -> best epoch %d (val macro-F1 %.3f)",
                        self.algo, val_group, epochs,
                        max(h.get("val_macro_f1", 0.0) for h in history))

        self.net = self._build_net()
        train_network(self.net, Xs, y_idx, weight, self.config, self.seed, epochs)
        self.best_epoch = epochs
        self.net.eval()

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        device = next(self.net.parameters()).device
        logits = predict_logits(self.net, torch.from_numpy(self._standardize(np.asarray(X))).to(device))
        return torch.softmax(logits, dim=1).cpu().numpy()

    def prepare_realtime(self) -> None:
        # 1件ずつの小さな推論では、GPUへの転送やスレッドを増やすより、CPUの1スレッドの方が速く安定する
        torch.set_num_threads(1)
        self.net.to("cpu")
        self.net.eval()

    def n_parameters(self) -> int:
        return sum(p.numel() for p in self.net.parameters()) if self.net is not None else 0

    def describe(self) -> dict:
        device = str(next(self.net.parameters()).device) if self.net is not None else None
        return {"seed": self.seed, "input_shape": list(self.input_shape), "train_config": self.config.to_dict(),
                "best_epoch": self.best_epoch, "n_parameters": self.n_parameters(),
                "torch": torch.__version__, "trained_on": device}

    # pickle保存: ネットワーク本体ではなく重み（state_dict、numpy配列）を保存し、読み込み時にCPU上で作り直す
    def __getstate__(self) -> dict:
        state = self.__dict__.copy()
        net = state.pop("net")
        state["state_dict"] = ({k: v.cpu().numpy() for k, v in net.state_dict().items()}
                               if net is not None else None)
        return state

    def __setstate__(self, state: dict) -> None:
        state_dict = state.pop("state_dict")
        self.__dict__.update(state)
        self.net = None
        if state_dict is not None:
            self.net = NETWORKS[self.algo](self.input_shape, len(self.classes), dropout=self.config.dropout)
            self.net.load_state_dict({k: torch.from_numpy(v) for k, v in state_dict.items()})
            self.net.eval()
