# Google Colab（GPU）で深層学習を回す手順

このPCにはNVIDIAのGPUが無い（AMD Radeon 890M 内蔵。PyTorchのGPU計算＝CUDAには使えない）ため、
深層学習（MLP / 1D-CNN / 2D-CNN）はCPUで計算していて時間がかかる（2D-CNNは1回30分〜2時間）。
Google Colab の無料GPU（T4）で学習し、できたモデルをPCにダウンロードしてリアルタイム推定に使う。

RandomForest はPCで回す（数分で終わる。Colabとはライブラリのバージョンが違い、値がわずかにずれることがあるため、PCの値を基準にする）。

## 全体の流れ

```text
PC:    tools.pack_for_colab でzipを作る → Google Drive にアップロード
Colab: notebooks/colab_train.ipynb を上から実行（比較 → 本番用モデルの学習）
       結果は Drive の csi-location/results/ に保存される
PC:    results/ からモデル(.pkl)をダウンロード → localization.estimate で使う
```

## 1. PC: アップロードするzipを作る

```bash
cd server
.venv/Scripts/python.exe -m tools.pack_for_colab
```

- `data/colab_upload.zip`（約33MB）ができる。中身は `server/` のコードと `data/calib.jsonl`・`data/baseline.jsonl`。
- 別のデータで学習するときは `--data` と `--baseline` で指定する。
- コードやデータを変えたら、作り直してアップロードし直す。

## 2. Google Drive にアップロードする

1. ブラウザで Google Drive を開き、**マイドライブ** に `csi-location` というフォルダを作る。
2. `colab_upload.zip` をそのフォルダに入れる（展開しない）。
3. `server/notebooks/colab_train.ipynb` も同じフォルダに入れる。

## 3. Colab でノートブックを開く

1. Drive の `colab_train.ipynb` をダブルクリック →「アプリで開く → Google Colaboratory」。
   （出てこない場合は、https://colab.research.google.com/ の「ファイル → ノートブックをアップロード」でも開ける）
2. メニュー **「ランタイム → ランタイムのタイプを変更」→ ハードウェア アクセラレータ「T4 GPU」→ 保存**。

## 4. 上のセルから順に実行する

| 手順 | 内容 | 目安の時間 |
| ---- | ---- | ---------- |
| 1. GPUの確認 | `Tesla T4` と出ればOK | すぐ |
| 2. Drive をつなぐ | 許可の画面で自分のアカウントを選ぶ。zipを `/content/work` に展開 | 1分 |
| 3. 環境の確認とテスト | `OK` で終わればOK（GPUのテストもここで動く） | 1分 |
| 4. 比較 | MLP / 1D-CNN / 2D-CNN × 窓 20,40,60 × シード 42,43,44（27回）。結果は `results/compare_deep.csv` に1回ごとに追記 | GPUで数十分の見込み（初回に1回目の時間を見て判断） |
| 5. 比較表 | `results/compare_deep.md` を表示 | すぐ |
| 6. 本番用モデル | `FINAL` を手順5の結果に合わせて書き換えてから実行。`results/model_<モデル>_w<窓>.pkl` | 1モデル数分 |
| 7. 読み込み確認 | 保存したモデルがCPUで動くか確認（`cpu` と出ればOK） | すぐ |

- 比較の組み合わせは手順4のセルの `MODELS` / `WINDOWS` / `SEEDS` で変えられる。
- 評価方法はPCの RandomForest と同じ「1周抜き」（`--split round --stride 2`）。比較の基準: RandomForest（窓1秒）正解率 0.601。

### 途中で切断されたとき

無料版は、長時間の実行や操作しない時間が続くと切断されることがある。

1. 「ランタイムに再接続」（GPUのまま）。
2. **手順1から順にもう一度実行する**。手順4は `--resume` 付きなので、`results/compare_deep.csv` に記録済みの回をとばして続きから回る。手順6も、できているモデルはとばす。
3. 最初からやり直したいときは、Drive の `results/compare_deep.csv` を削除してから手順4を実行する。

## 5. PC: モデルをダウンロードして使う

1. Drive の `csi-location/results/` から、使うモデル（例: `model_cnn1d_w60.pkl`）と `compare_deep.md` / `compare_deep.csv` をダウンロードし、PCの `server/data/` に置く。
2. リアルタイム推定（モデルの種類によらず同じコマンド）:

```bash
cd server
.venv/Scripts/python.exe -m localization.estimate --serial COM3 --model data/model_cnn1d_w60.pkl --remote
```

- 保存したモデルは重みをnumpy形式で持っていて、読み込むと常にCPUに載る。PCにGPUが無くてもそのまま動く。
- 起動時の `inference: ○ ms/frame` が50ms未満なら間に合っている（PCでの実測: MLP 0.2ms、2D-CNN 1.1ms）。
- PCの PyTorch が必要（`pip install -r requirements-deep.txt`。入っていれば不要）。

## 注意

- **CPUとGPUでは同じシードでも結果がわずかに変わる**（計算の細部が違うため）。比較表は深層学習を全部Colabで回したものを使う。PCで回した途中結果（`data/compare.csv`、2026-10-06）は参考値。
- 測定データ（CSI）を自分のGoogle Driveに置くことになる（共有しなければ他人は見られない）。
- コードを直したら、`tools.pack_for_colab` でzipを作り直してアップロードし直す（Colab側は手順2で毎回展開し直す）。
