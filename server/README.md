# server

CSI受信・ゾーン分類サーバー一式。浴槽内の入浴者が「どのゾーンにいるか（または不在か）」を推定する。

## セットアップ

```bash
cd server
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt
```

- `requirements.txt` は直接依存のバージョンを固定したもの。
- 推移的依存まで完全に再現したい場合は `requirements.lock.txt`（`pip freeze` の結果）を使う:
  `pip install -r requirements.lock.txt`
- 深層学習（`--algo mlp / cnn1d / cnn2d`）を使う場合だけ、PyTorch（CPU版）を追加で入れる:
  `.venv/Scripts/python.exe -m pip install -r requirements-deep.txt`。RandomForest だけなら不要。
  このPCにはNVIDIAのGPUが無いので、時間のかかる学習は Google Colab のGPUで回せる（[docs/colab.md](../docs/colab.md)）。

以降のコマンドはすべて `server/` ディレクトリで `.venv` のPythonを使って実行する。

## ゾーン定義（config/zones.json）

浴槽サイズ・センサー位置・ゾーン（ID、名前、中心座標、矩形範囲）を `config/zones.json` で定義する（各コマンドの `--zones` の既定値）。
ゾーン数や配置を変えたい場合はこのファイルだけを編集する（コードは変更不要）。
**ID 0 は「empty（人がいない）」で、中心・矩形は `null`**。在/不在を演出に使うため必須。

### TX台数の切替

`tx` の各要素に `"enabled": true/false` を持たせてあり、`true` のセンサーだけが
収集（`collection.collect`）・学習（`training.train`）・仮想センサー（`tools.simulate`）で使われる
（省略時は `true`）。zones.json を編集せずに一時的に変えるには各コマンドに `--sensors 1,3` を付ける。
学習済みモデルは学習時のセンサーIDを記憶し、推定時はそのセンサーだけを使う。
**台数を変えたら再収集・再学習が必要。** 学習時に使うセンサーのデータが無いとエラーになる。

学習済みモデルには学習時の `zones.json` の内容が埋め込まれるため、推定時に定義ファイルを差し替えても
モデルと食い違わない（ゾーン構成を変えたら再収集・再学習が必要）。

## モジュール構成

処理の流れ（受信 → 収集 → 特徴量 → 学習 → 推定）の順に並べている。CLIは `python -m <モジュール>` で実行する。

```text
server/
├── config/        zones.json（ゾーン・機器配置）とその読み込み
├── csi_receiver/  受信（シリアル/UDP）・パケット解析・フレーム同期
├── collection/    ラベル付きデータの収集
├── features/      特徴量（学習・推定で共用）
├── models/        ゾーン分類モデル（classic/ = scikit-learn、deep/ = PyTorch）
├── training/      データセット作成・評価・学習・学習曲線
├── localization/  リアルタイム推定
├── web/           スマホのブラウザで見る画面
├── common/        共通の小物（JSONL、スリープ抑止など）
├── tools/         動作確認用ツール・Colab用のzip作成
├── notebooks/     Google Colab 用ノートブック
└── tests/         単体テスト
```

| モジュール | 役割 |
| ---------- | ---- |
| `config.zones` | `config/zones.json` の読み込み、座標→ゾーン変換 |
| `csi_receiver.packet` | CSIパケットのパース（docs/protocol.md準拠） |
| `csi_receiver.serial_frames` | csi_rxのシリアルフレーム（sync/len/CRC）のパーサー |
| `csi_receiver.source` | 入力元の切替（`--serial COMx` でUSBシリアル、省略でUDP） |
| `csi_receiver.frames` | 複数センサーのサンプルをフレーム（同一瞬間のスナップショット）に同期 |
| `csi_receiver.receiver` | 生データの受信確認・JSONL記録CLI |
| `collection.collect` | ゾーンラベル付きキャリブレーション収集CLI（1回ずつ） |
| `collection.plan` | 音声ガイド・スマホのリモコン付きの全自動収集 → 学習（手順は docs/runbook.md §4.0） |
| `collection.voice` / `collection.recording` | 全自動収集の読み上げ・ビープ／受信パケットの記録・セッション削除 |
| `features.amplitude` | 振幅の正規化・ベースライン（空の浴槽の平均パターン）の算出 |
| `features.stats` | フレーム→統計特徴量（窓の平均・標準偏差）。RandomForest・kNN・MLPの入力 |
| `features.tensor` | フレーム→生の窓（窓フレーム数 × TX × サブキャリア）。CNNの入力 |
| `models.base` | 全モデル共通の形（`fit` / `predict_proba` / `predict` / `prepare_realtime`） |
| `models.registry` | `--algo` の名前からモデルを作る・保存済みモデルを読み込む（旧形式の `model.pkl` も読める） |
| `models.classic` | 従来の機械学習（`rf.py` = RandomForest、`knn.py` = kNN） |
| `models.deep` | 深層学習（`mlp.py` = MLP、`cnn.py` = 1D-CNN / 2D-CNN、`trainer.py` = 学習ループ・早期終了、`model.py` = 共通の形への包み）。PyTorchを使うのはここだけ |
| `training.dataset` | JSONL → 特徴量X・ラベルy・グループ（セッション/日付/周回）。ベースラインの読み込み |
| `training.evaluate` | グループ単位のleave-one-group-out評価、正解率・macro-F1・混同行列 |
| `training.train` | 分類器の学習・評価・モデル保存CLI（`--window` で窓特徴量） |
| `training.learning_curve` | 学習に使う周回数を増やしたときの精度の伸び（学習曲線）と、特徴量の比較 |
| `training.compare` | 複数のモデル × 窓の長さ × 乱数シードを同じ評価で比べ、比較表（Markdown/CSV）を出す |
| `localization.estimate` | リアルタイムゾーン推定CLI（`--osc` でTouchDesignerへ送信、`--remote` でスマホ表示） |
| `localization.smoothing` | 確率のEMA平滑化・多数決による確定ゾーンの切り替え（ヒステリシス） |
| `localization.osc_out` | OSC送信（`/koi/zone`, `/koi/zone_probs`, `/koi/position`） |
| `web.server` | スマホ向け小さなWebサーバーの共通部分 |
| `web.monitor` / `web.remote` | 推定モニター（`estimate --remote`）／収集リモコン（`collection.plan`）の画面 |
| `common.jsonl` / `common.system` | JSONLの読み書き／スリープ抑止・PCのIPアドレス |
| `tools.simulate` | 実機なしで全体を試せる仮想TX（UDP送信。受信側は `--serial` を付けない） |
| `tools.osc_manual` | 手動でゾーンを指定してOSCを送る（TouchDesignerの動作確認用。estimateと同じ形式・送り先） |
| `tools.pack_for_colab` | Google Colab（GPU）で深層学習を回すため、コードとデータを1つのzipにまとめる（手順は docs/colab.md） |
| `notebooks/colab_train.ipynb` | Colab で比較・本番用モデルの学習を行うノートブック |

## ワークフロー

以下のコマンドは csi_rx をUSB接続した実機（`--serial COM3`）の例。実機なしで試すときは、
`--serial COM3` を外して（UDP受信）、別ターミナルで `tools.simulate` を動かす（次節）。

```bash
PY=.venv/Scripts/python.exe

# 0. 受信確認（実機を書き込んだ直後の疎通チェック）
$PY -m csi_receiver.receiver --serial COM3

# 1. empty（ゾーン0）収集。浴槽が空の状態で30秒ほど。セッションを分けて3回以上
#    （先頭1セッションはベースライン算出専用、残りがゾーン0の学習データになる）
$PY -m collection.collect --serial COM3 --zone 0 --delay 0 --duration 30 --out data/empty.jsonl
$PY -m collection.collect --serial COM3 --zone 0 --delay 0 --duration 30 --out data/empty.jsonl
$PY -m collection.collect --serial COM3 --zone 0 --delay 0 --duration 30 --out data/empty.jsonl

# 2. 各ゾーンの収集（ゾーン内で位置・姿勢を少しずつ変えながら。各ゾーン2セッション以上）
$PY -m collection.collect --serial COM3 --zone 1 --duration 10 --out data/calib.jsonl
$PY -m collection.collect --serial COM3 --zone 1 --duration 10 --out data/calib.jsonl
$PY -m collection.collect --serial COM3 --zone 2 --duration 10 --out data/calib.jsonl
# ... 全ゾーン分

# 3. 学習・評価（既定はRandomForest。--algo knn / mlp / cnn1d / cnn2d で比較。深層学習は requirements-deep.txt が必要）
# empty.jsonl は --data に含めない（--baseline が先頭セッションをベースライン専用、残りをゾーン0の学習データにする）
$PY -m training.train --data data/calib.jsonl \
    --baseline data/empty.jsonl --model-out data/model.pkl

# 4. リアルタイム推定（--oscを付けるとTouchDesignerへ送信）
$PY -m localization.estimate --serial COM3 --model data/model.pkl --osc 127.0.0.1:9000
```

詳細な収集手順・評価の見方は [docs/calibration.md](../docs/calibration.md) を参照。

## 実機なしでの動作確認

```bash
# 別ターミナルで仮想センサーを起動（4センサー×100Hz）
$PY -m tools.simulate --empty                    # 空の浴槽（ゾーン0）
$PY -m tools.simulate --zone 1                   # ゾーン1内をランダムに少し動く人
$PY -m tools.simulate --traj tour --period 10    # 全ゾーンを10秒ずつ巡回（境界をまたぐ動き）
$PY -m tools.simulate --traj circle              # 円軌道で動く人
$PY -m tools.simulate --pos 0.3,0.5              # 静止位置（座標指定）

# その状態で上記ワークフローの0〜4が（`--serial COM3` を外して）そのまま試せる
```

浴槽サイズ・センサー位置は `zones.json` から読まれる。

## 調整すべきパラメータ

| パラメータ | 場所 | 既定値 | 意味 |
| ---------- | ---- | ------ | ---- |
| `--sensors` | `collect` / `train` / `simulate` | zones.jsonのenabled | 使うTX ID（例: `1,3`） |
| `--serial` / `--baud` | `receiver` / `collect` / `estimate` | なし(UDP) / 921600 | csi_rxのシリアルポートとボーレート |
| `--proba-ema` | `localization.estimate` | 0.3 | 確率EMA係数。大=追従重視、小=滑らか重視 |
| `--zone-threshold` | `localization.estimate` | 0.0 | この確率未満のフレームは確定ゾーンの票に数えない（0=全フレーム） |
| `--hysteresis-frames` | `localization.estimate` | 20 | 確定ゾーンの多数決に使う直近のフレーム数（約20Hzで約1秒） |
| `--hysteresis-ratio` | `localization.estimate` | 0.5 | 直近フレームのうち同じゾーンがこの割合以上なら確定ゾーンを切り替える |
| `--zone-report-interval` | `localization.estimate` | 1.0 | `/koi/zone` 等の定期送信間隔（秒） |
| `--split` | `training.train` | session | 評価の分割単位。`date` で収集日単位（環境変化への頑健性評価） |
| `--seed` | `training.train` | 42 | RandomForestの乱数シード |
| `--interval` / `--max-age` | `training.train` | 0.05 / 0.2 | フレーム周期・サンプル鮮度（変えたら `--hysteresis-frames` も合わせる） |

## TouchDesignerの手動テスト

推定の代わりにキーボードでゾーンを指定し、`localization.estimate` と同じ形式でOSCを送る。
TD側は変更不要で、本番は `localization.estimate --osc 127.0.0.1:9000` に替えるだけ（同時に起動しない）。

```bash
$PY -m tools.osc_manual                 # 127.0.0.1:9000 へ送る（--osc で変更）
# 入力: 5（ゾーン5）/ 0（不在）/ 5 6（境界で半々）/ 5:0.7 6:0.3 / tour 10（巡回）/ list / q
```

## OSC出力仕様

| アドレス | 引数 | 送信タイミング |
| -------- | ---- | -------------- |
| `/koi/zone` | `[zone_id: int]` | 確定ゾーンの変化時＋定期送信 |
| `/koi/zone_probs` | `[p0, p1, ...: float]` | 毎フレーム（平滑化後の確率、`zones.json` のID昇順） |
| `/koi/position` | `[x, y: float]` | 確定ゾーンの中心座標。emptyのときは送らない |

詳細は [docs/protocol.md](../docs/protocol.md) を参照。
