"""深層学習モデルの学習ループ（ミニバッチ学習・早期終了・データ拡張）。

過学習を抑える工夫:
  - Dropout と weight decay（重みを小さく保つ正則化）
  - クラスの重み付け（RandomForest の class_weight="balanced" と同じ考え方）
  - データ拡張: 標準化した入力に小さなガウスノイズを足す（学習時のみ）
  - 早期終了: 検証データの macro-F1 が patience エポック伸びなければ止める

GPU（CUDA）があればGPUで学習する（TrainConfig.device = "auto"）。Google Colab で速く回すため。
このPC（GPUなし）では今までどおりCPUで学習し、結果も変わらない。
"""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass

import numpy as np
import torch
from sklearn.metrics import f1_score
from torch import nn

logger = logging.getLogger("deep")


@dataclass
class TrainConfig:
    max_epochs: int = 60       # 早期終了で使う上限
    patience: int = 10         # 検証macro-F1がこのエポック数伸びなければ止める
    batch_size: int = 128
    lr: float = 1e-3
    weight_decay: float = 1e-3
    dropout: float = 0.3
    noise_std: float = 0.1     # データ拡張のノイズ（標準化後の単位）
    fallback_epochs: int = 30  # 検証用のグループが無いときのエポック数
    device: str = "auto"       # "auto"（GPUがあればGPU）/ "cuda" / "cpu"

    def to_dict(self) -> dict:
        return asdict(self)


def resolve_device(name: str) -> torch.device:
    """"auto" ならGPU（CUDA）が使えるときだけGPU、それ以外はCPU。"""
    if name == "auto":
        return torch.device("cuda" if torch.cuda.is_available() else "cpu")
    if name == "cuda" and not torch.cuda.is_available():
        raise SystemExit("--device cuda が指定されましたが、GPU（CUDA）が使えません")
    return torch.device(name)


def balanced_class_weight(y_idx: np.ndarray, n_classes: int) -> np.ndarray:
    """少ないクラスほど重くする（n / (クラス数 × そのクラスの数)）。"""
    counts = np.bincount(y_idx, minlength=n_classes).astype(np.float64)
    counts[counts == 0] = 1.0
    return len(y_idx) / (n_classes * counts)


def predict_logits(net: nn.Module, X: torch.Tensor, batch_size: int = 1024) -> torch.Tensor:
    net.eval()
    with torch.no_grad():
        return torch.cat([net(X[i:i + batch_size]) for i in range(0, len(X), batch_size)])


def train_network(net: nn.Module, X: np.ndarray, y_idx: np.ndarray, class_weight: np.ndarray,
                  cfg: TrainConfig, seed: int, epochs: int,
                  X_val: np.ndarray | None = None, y_val: np.ndarray | None = None) -> tuple[int, list[dict]]:
    """net を学習する。検証データがあれば早期終了し、最も良かったエポックの重みに戻す。

    戻り値: (最良のエポック数, エポックごとの記録)。検証データが無ければ epochs まで学習する。
    net はあらかじめ学習する装置（CPU/GPU）に載せておく。データはここでまとめて同じ装置に載せる。
    """
    device = next(net.parameters()).device
    on_gpu = device.type != "cpu"
    generator = torch.Generator().manual_seed(seed)  # 並び順（CPUで作る）
    # データ拡張のノイズの乱数。GPUのときはGPU上で作る（CPUで作って転送すると遅い）
    noise_generator = torch.Generator(device=device).manual_seed(seed) if on_gpu else generator
    Xt = torch.from_numpy(X).to(device)
    yt = torch.from_numpy(y_idx.astype(np.int64)).to(device)
    Xv = torch.from_numpy(X_val).to(device) if X_val is not None else None
    loss_fn = nn.CrossEntropyLoss(weight=torch.tensor(class_weight, dtype=torch.float32, device=device))
    optimizer = torch.optim.AdamW(net.parameters(), lr=cfg.lr, weight_decay=cfg.weight_decay)

    history: list[dict] = []
    best_f1, best_epoch, best_state, stale = -1.0, epochs, None, 0
    for epoch in range(1, epochs + 1):
        net.train()
        order = torch.randperm(len(Xt), generator=generator).to(device)
        total = torch.zeros((), device=device)  # GPUでは毎バッチCPUに値を戻すと遅いので、装置上で合計する
        for i in range(0, len(order), cfg.batch_size):
            idx = order[i:i + cfg.batch_size]
            if len(idx) < 2:
                continue  # BatchNormは1件のバッチを扱えない
            xb = Xt[idx]
            if cfg.noise_std > 0:
                xb = xb + cfg.noise_std * torch.randn(xb.shape, generator=noise_generator, device=device)
            loss = loss_fn(net(xb), yt[idx])
            optimizer.zero_grad()
            loss.backward()
            optimizer.step()
            total += loss.detach() * len(idx)
        record = {"epoch": epoch, "train_loss": float(total) / len(Xt)}

        if Xv is not None:
            pred = predict_logits(net, Xv).argmax(dim=1).cpu().numpy()
            val_f1 = f1_score(y_val, pred, average="macro")
            record["val_macro_f1"] = float(val_f1)
            if val_f1 > best_f1:
                best_f1, best_epoch, stale = val_f1, epoch, 0
                best_state = {k: v.detach().clone() for k, v in net.state_dict().items()}
            else:
                stale += 1
        history.append(record)
        if Xv is not None and stale >= cfg.patience:
            break

    if best_state is not None:
        net.load_state_dict(best_state)
    return best_epoch, history
