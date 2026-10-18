"""Google Colab で深層学習を回すために、server/ のコードと収集データを1つのzipにまとめる。

使い方（server/ で実行）:
    python -m tools.pack_for_colab                          # data/colab_upload.zip を作る
    python -m tools.pack_for_colab --data data/calib.jsonl --baseline data/baseline.jsonl --out data/colab_upload.zip

作ったzipを Google Drive の「マイドライブ/csi-location/」にアップロードし、
notebooks/colab_train.ipynb を Colab で開いて上から実行する（手順は docs/colab.md）。

zipに入れるもの: server/ のPythonコード・config・tests・notebooks・requirements、--data と --baseline のJSONL。
入れないもの: .venv、data/ の他のファイル（モデル・キャッシュ）、__pycache__。
"""
from __future__ import annotations

import argparse
import os
import zipfile

SERVER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CODE_DIRS = ["collection", "common", "config", "csi_receiver", "features", "localization", "models",
             "notebooks", "tests", "tools", "training", "web"]
CODE_FILES = ["README.md", "requirements.txt", "requirements-deep.txt"]


def main() -> None:
    parser = argparse.ArgumentParser(description="pack server code and data for Google Colab")
    parser.add_argument("--data", default="data/calib.jsonl", help="学習データ（collection で作ったJSONL）")
    parser.add_argument("--baseline", default="data/baseline.jsonl", help="ベースライン（空の浴槽）のJSONL")
    parser.add_argument("--out", default="data/colab_upload.zip", help="作るzipのパス")
    args = parser.parse_args()

    for path in (args.data, args.baseline):
        if not os.path.exists(path):
            raise SystemExit(f"{path} がありません")

    n_files = 0
    with zipfile.ZipFile(args.out, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for d in CODE_DIRS:
            for root, dirs, files in os.walk(os.path.join(SERVER_DIR, d)):
                dirs[:] = [x for x in dirs if x != "__pycache__"]
                for name in files:
                    if name.endswith((".pyc", ".pkl")):
                        continue
                    full = os.path.join(root, name)
                    zf.write(full, os.path.join("server", os.path.relpath(full, SERVER_DIR)))
                    n_files += 1
        for name in CODE_FILES:
            zf.write(os.path.join(SERVER_DIR, name), os.path.join("server", name))
            n_files += 1
        # データはzipの中では server/data/ の決まった名前にする（ノートブックがこの名前で読む）
        zf.write(args.data, "server/data/calib.jsonl")
        zf.write(args.baseline, "server/data/baseline.jsonl")

    size_mb = os.path.getsize(args.out) / 1e6
    print(f"{args.out}: コード {n_files} ファイル + データ2つ、{size_mb:.0f}MB")
    print("これを Google Drive の「マイドライブ/csi-location/」にアップロードし、"
          "notebooks/colab_train.ipynb を Colab で開く（docs/colab.md）")


if __name__ == "__main__":
    main()
