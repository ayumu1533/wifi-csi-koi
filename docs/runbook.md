# 実施手順書（書き込み → 収集 → 学習 → 推定）

2026-10-03 に実機で実施した手順とコマンドの記録。これを見れば最初から再現できる。
設計は [design_tx_rx.md](design_tx_rx.md)、収集の考え方は [calibration.md](calibration.md)、通信仕様は [protocol.md](protocol.md)。
2026-10-11以降の収集・学習の予定（2日目の4つ角、3日目以降の扇形）は [collection_plan.html](collection_plan.html)（ブラウザで開く）。

## 0. 現在の構成（2026-10-04時点）

| 項目 | 値 |
| ---- | -- |
| 構成 | csi_rx ×1 + csi_tx ×3（4台運用。2026-10-03までは csi_tx ×1 の2台運用） |
| csi_tx | TX ID **1**=(3,0)、**2**=(0,3)、**3**=(3,3)。電源のみ（モバイルバッテリー等） |
| csi_rx | 座標 **(0,0)**、PCにUSB接続（UARTポート、CP210x）。**COM3**（ボードを差し替えると番号が変わることがある） |
| 領域 | 3m × 3m、1m角の3×3 = 9ゾーン + empty（ID 0）。[server/config/zones.json](../server/config/zones.json) |
| 有効なTX | `zones.json` の `tx` で `enabled: true` のもの（現在は ID 1, 2, 3） |
| 通信 | ESP-NOW、チャンネル1。WiFiネットワーク・PCのWiFi接続は不要 |
| シリアル | 921600bps（`CSI_RX_BAUD`） |

```text
       x=0       1         2         3  (m)
  y=3  ┌─────┬─────┬─────┐
       │  7  │  8  │  9  │ [TX ID3 (3,3)]
  y=2  ├─────┼─────┼─────┤
       │  4  │  5  │  6  │
  y=1  ├─────┼─────┼─────┤
       │  1  │  2  │  3  │
  y=0  └─────┴─────┴─────┘
      [RX (0,0)]
```

ゾーンID: 1=front_left, 2=front_center, 3=front_right, 4=middle_left, 5=middle_center, 6=middle_right,
7=back_left, 8=back_center, 9=back_right（手前→奥、左→右）。

## 1. ファームウェアの書き込み

**Git Bashで**行う（PowerShellでは `idfpy.sh` と `sed` が動かない）。COM番号はデバイスマネージャーで確認する。
書き込みに失敗する場合は、BOOTボタンを押しながらRSTを押して離し、再実行する。

### 1.1 csi_rx（受信機、1台）

```bash
cd firmware/csi_rx
../idfpy.sh -p COM3 flash
```

- RXは起動後にUART0をバイナリ専用（921600bps）にするため、`monitor` は使わない（読めない）。動作確認は §2。
- 設定（`menuconfig`）: `CSI RX Configuration` の WiFi Channel（TXと同じ値）、ネイティブUSB出力、ボーレート。既定のままでよい。

### 1.2 csi_tx（送信機、台数分。TX IDは台ごとに変える）

```bash
cd firmware/csi_tx
sed -i 's/^CONFIG_CSI_TX_ID=.*/CONFIG_CSI_TX_ID=3/' sdkconfig     # 台ごとに 1, 2, 3
grep CONFIG_CSI_TX_ID sdkconfig                                   # 確認
../idfpy.sh -p COM4 flash monitor
```

- 起動ログに `mac=02:c5:15:00:00:03 channel=1` と `tx_id=3 sending every 10ms` が出れば成功（末尾がTX ID）。モニター終了は `Ctrl+]`。
- 初回起動の `failed to load RF calibration data` は無害（初回のみ）。
- `sdkconfig` は `menuconfig`（`../idfpy.sh menuconfig` → `CSI TX Configuration` → `TX ID`）でも変更できる。`menuconfig` は `csi_tx` のフォルダで開くこと（`csi_rx` で開くとTXの項目は出ない）。
- 4台運用（TX ×3）: ID 1=(3,0)、ID 2=(0,3)、ID 3=(3,3)。書いたボードにはテープ等でIDを貼っておくと取り違えない。

## 2. 疎通確認

RXのシリアルポートを他のプログラム（monitor等）が開いていないことを確認してから行う。

```bash
cd server
.venv/Scripts/python.exe -m csi_receiver.receiver --serial COM3
```

- `sensor3: n=... rssi=... seq=...` が毎秒出て、`n` が約100/秒ずつ増えればOK（今回: 約93〜97Hz、RSSI -26〜-45、`seq` 欠落なし）。
- 出ない場合: RXのUSBを抜き差し／RSTボタン、TXの給電、TXとRXのチャンネル一致、COMポートの競合を確認。
- 終了は `Ctrl+C`。

## 3. サーバー環境（新しいターミナルを開いたとき）

```bash
cd server
PY=.venv/Scripts/python.exe       # 毎回必要（変数はターミナルごと）
mkdir -p data                     # 初回のみ。data/ は .gitignore 対象
```

## 4. データ収集

`--serial COM3` はRXのポート。収集に使うTXは `zones.json` の `enabled`（または `--sensors 3`）に従う。
記録前の `--delay`（秒）の間に所定の位置に入る。**ほかの人は領域の外へ。RX/TXは動かさない。**

### 4.0 全自動収集（推奨、2026-10-04〜）

1回起動すれば、音声の案内に従って動くだけで収集が終わる（PCに戻らなくてよい）。学習は終わったあとに手動で行う（§5）。

```bash
$PY -m collection.plan --list            # 計画の確認（全52回・約32分）
$PY -m collection.plan --serial COM3     # 開始
```

- 順番: 基準(empty) → [empty → ゾーン9か所] × 5周 → empty。周ごとに姿勢を変える（真ん中に立つ → 真ん中に座る → ずらして立つ → ずらして座る → 向きを変えて立つ）。ゾーンはジグザグ順に回り、周ごとに逆順にする。
- 1回の流れ: 読み上げ → 移動の待ち時間（ゾーン8秒、empty 12秒。最後の3秒は「ピッ」）→ 高い長音で記録開始 → 記録（ゾーン20秒、empty 30秒）→「ピピッ」で終了。
- 記録後にTXごとの受信レートを調べ、50Hz未満のTXがあれば読み上げて取り直す。2回続けて失敗したら一時停止する。
- **スマホのリモコン**: 起動時に表示される `http://<PCのIP>:8765/` をスマホで開く（PCと同じWiFi）。進み具合の確認と、一時停止／再開／今のをやり直す／直前をやり直す／スキップ／終了ができる。**リモコンが有効なときは、起動後スマホの「開始」ボタンを押すまで収集は始まらない**（`--no-wait` で起動直後に開始。`--no-remote` のときは常に起動直後に開始）。**このPCでは事前設定が必要**（2026-10-04確認: Wi-Fiが「パブリック」で、python.exe の受信がパブリックでブロックされている）。Wi-Fiのネットワークプロファイルを「プライベート」に変え、管理者のPowerShellで `New-NetFirewallRule -DisplayName "CSI収集リモコン" -Direction Inbound -Protocol TCP -LocalPort 8765 -Action Allow -Profile Private` を1回実行する。リモコンが使えなくても、収集は音声だけで最後まで進む。
- 収集中はPCがスリープしないように、スクリプトが自動で抑止する（画面は消えてよい。終了すると元に戻る）。
- 保存先: 基準の1回は `data/baseline.jsonl`、それ以外（emptyを含む）は `data/calib.jsonl`。各レコードに周回番号 `round` と姿勢 `posture` が付く。
- 全部終わると「収集が終わりました」と読み上げ、学習（§5）と学習曲線（§5.1）のコマンドを画面に表示する。
- `--train` を付けると、学習・学習曲線まで自動で実行して正解率を読み上げる（scikit-learnが動く環境のみ。ログは `data/train_et.log`、`data/train_rf.log`、`data/learning_curve.txt`、モデルは `data/model.pkl`（et）と `data/model_rf.pkl`）。
- 途中で止めた場合: 表示される番号から `--start-from N --append` で再開できる（記録が終わった回はファイルに残っている）。
- `data/` に前回のデータがあると起動しない（混ざらないように）。退避してから実行する。
- 主なオプション: `--rounds`（周回数）、`--zone-duration` / `--empty-duration`（記録秒数）、`--zone-delay` / `--empty-delay`（移動の待ち秒数）、`--postures "立つ|座る|..."`、`--min-hz`、`--no-voice` / `--no-beep` / `--no-remote` / `--train`。

以下の §4.1〜4.2 は、1回ずつ手動で収集する場合の手順。

### 4.1 empty（ゾーン0、誰もいない状態）— 3セッション以上

```bash
$PY -m collection.collect --serial COM3 --zone 0 --delay 15 --duration 30 --out data/empty.jsonl
```

- 領域の完全に外へ出る（記録開始直後に動くとデータが乱れる）。同じコマンドを3回以上実行する。
- **先頭（ファイル内で最初に記録した）セッションはベースライン算出専用**。学習には2つ目以降のセッションが使われる。
- 湯量・水位・設置を変えたら取り直す。

### 4.2 各ゾーン（1〜9）— 各ゾーン2セッション以上

```bash
$PY -m collection.collect --serial COM3 --zone 1 --duration 10 --out data/calib.jsonl
$PY -m collection.collect --serial COM3 --zone 1 --duration 10 --out data/calib.jsonl
$PY -m collection.collect --serial COM3 --zone 5 --duration 10 --out data/calib.jsonl
# ... 取りたいゾーンだけ。全ゾーンなら 1〜9
```

- 3秒のカウントダウン（`--delay`、既定3）の間にゾーンへ入る。
- **ゆっくり小さく動く**（体重移動・向きを変える）。完全静止は避け、大きく歩き回らない。足はゾーンの内側（境界から10cm以上）。
- 2回目は立ち位置・向き・姿勢（立つ／座る）を変える。評価はセッション単位なので、条件が違うほど現実に近い評価になる。
- 完了の目印: `done: N packets recorded` と `sensor 3: N packets (9x Hz)`。途中の `packets so far: {3: N}` は累計パケット数。
- ファイルは追記される（同じ `--out` に溜まる）。やり直したい場合は該当ファイルを削除して取り直す。

## 5. 学習

```bash
# 全自動収集（§4.0）のデータ
$PY -m training.train --data data/calib.jsonl --baseline data/baseline.jsonl \
    --algo rf --split round --stride 2 --model-out data/model.pkl

# 手動収集（§4.1〜4.2）のデータ
$PY -m training.train --data data/calib.jsonl \
    --baseline data/empty.jsonl --algo rf --model-out data/model.pkl
```

- `--window`（既定20 = 約1秒）: 直近20フレームの平均・標準偏差を特徴量にする（窓特徴量。下の「学習が使うデータ」）。`--window 0` で以前の1フレーム特徴量。窓の長さはモデルに保存され、推定でも同じ長さが使われる。
- `--split round`: 周回単位で評価する（5周なら5回の学習で済み、セッション単位より速い。条件の違う周を丸ごと当てるので現実に近い）。
- `--stride 2`: 特徴量を2フレームおきに間引く（窓特徴量は隣どうしがほぼ同じなので精度はほぼ変わらず、速くなる）。
- **`--data` には `calib.jsonl` だけを渡す**（`empty.jsonl` は `--baseline` で渡す）。`--data` にも `empty.jsonl` を入れると、ベースライン専用のセッションが学習・評価に混ざり、精度が実際より高く出る（今回、0.751が正しくは0.628だった）。
- `--algo knn` と比較して良い方を採用する（今回は rf 0.628 / knn 0.642 で大差なし）。
- 評価はセッション単位のleave-one-out。各ゾーン2セッション未満だと評価がスキップされる。
- 出力: ログの正解率・macro-F1・ゾーンごとの `recall`・混同行列。`data/model.pkl`（モデル）と `data/model.pkl.confusion.csv`（混同行列）が保存される。
- 環境変化への頑健性を見るには `--split date`（2日以上のデータが必要）。

### 5.1 学習曲線（周回をあと何周取るべきか）

```bash
$PY -m training.learning_curve --data data/calib.jsonl --baseline data/baseline.jsonl
```

- 1周を評価用に取り分け、残りの先頭k周だけで学習して当てる（k = 1, 2, ...）。kごとの正解率・macro-F1・emptyの再現率を表にする。
- 最後の1周でmacro-F1の伸びが0.02未満なら「頭打ち」と表示する。まだ伸びていれば周回を追加する価値がある。
- `--windows 0,20`（既定）で、1フレーム特徴量と窓特徴量の比較も同じ表に出る。
- `--algo mlp` などでも同じように使える（深層学習は時間がかかる）。

### 5.2 深層学習（MLP / 1D-CNN / 2D-CNN）

初回だけ PyTorch（CPU版）を入れる: `$PY -m pip install -r requirements-deep.txt`

```bash
# 1つのモデルを学習・評価して保存（使い方は rf と同じ。--algo と --window を変える）
$PY -m training.train --data data/calib.jsonl --baseline data/baseline.jsonl \
    --algo cnn2d --window 40 --split round --stride 2 --model-out data/model_cnn2d_w40.pkl

# 全モデル × 窓の長さ × シードを同じ評価で比べる（数時間かかる。途中で止めてもCSVに残る）
$PY -m training.compare --data data/calib.jsonl --baseline data/baseline.jsonl \
    --models rf,knn,mlp,cnn1d,cnn2d --windows 20,40,60 --seeds 42,43,44 --out data/compare
```

| `--algo` | 入力 | 中身 |
| -------- | ---- | ---- |
| `mlp` | 統計特徴量（387次元、rfと同じ） | 全結合 387→256→128→10 |
| `cnn1d` | 生の窓（窓 × TX3 × 64） | 192本の波形を時間方向に畳み込む |
| `cnn2d` | 生の窓（窓 × TX3 × 64） | TXを3チャンネルの画像（時間 × サブキャリア）として畳み込む |

- 評価は rf と全く同じ（`--split round` の1周抜き）。比較の条件をそろえるため。
- **早期終了**: 学習用の周のうち最後の1周を丸ごと検証に使い、検証macro-F1が最も良いエポック数を決めてから、学習用の全周で学習し直す。テストの周は見ない。
- 過学習対策: Dropout、weight decay、クラスの重み付け、入力への小さなノイズ（`--dropout` `--weight-decay` `--noise-std`）。
- 乱数で結果が変わるので、比較は `training.compare` で複数シードの平均±標準偏差を見る。
- 保存したモデルは `localization.estimate --model data/model_cnn2d_w40.pkl` でそのまま推定に使える（起動時に1回の推論時間を表示する）。
- このPCはCPUで学習するため時間がかかる（2D-CNNは1回30分〜2時間）。**Google Colab のGPUで回す手順は [colab.md](colab.md)**。
- `training.compare` は途中で止まっても `--resume` で続きから回せる。学習の装置は `--device auto|cuda|cpu`（既定 auto = GPUがあればGPU）。

### 学習が使うデータ

- 50msごとに、各TXの直近サンプルを1フレームにまとめる（`csi_receiver/frames.py`）。
- 各TXの64サブキャリアを平均で正規化する（`features/amplitude.py`）。
- **窓特徴量**（既定）: 直近20フレーム（約1秒）について、サブキャリアごとに
  - 平均 − ベースライン（人の位置による定常的な形の変化）
  - 標準偏差（人の呼吸・体の揺れによる揺らぎ。無人ならほぼノイズだけ）
  - TXごとの揺らぎの大きさ（標準偏差のサブキャリア平均）

  を並べる。TX3台なら 64×3（平均）+ 64×3（標準偏差）+ 3 = 387次元。
- 1フレーム特徴量（`--window 0`）: 各フレームの正規化振幅 − ベースライン。TX3台なら192次元。
- **生の窓**（CNN、`features/tensor.py`）: 直近の窓のフレームを平均・標準偏差にまとめず、正規化振幅 − ベースラインをそのまま並べる（窓 × TX × 64）。時間方向の変化もモデルに渡す。
- RandomForest（既定）、または `--algo` で選んだモデルがゾーンを分類する。
- 学習・評価に使うTXは、`zones.json` の `enabled` か `--sensors` で決まり、モデルに記録される。**TXの台数を変えたら再収集・再学習が必要。**

## 6. リアルタイム推定

```bash
$PY -m localization.estimate --serial COM3 --model data/model.pkl
# TouchDesignerへ送る場合
$PY -m localization.estimate --serial COM3 --model data/model.pkl --osc 127.0.0.1:9000
# スマホで見る場合（--osc と併用できる）
$PY -m localization.estimate --serial COM3 --model data/model.pkl --remote
```

**スマホで見る（`--remote`）**: 起動時に表示される `http://<PCのIP>:8765/` をスマホで開く。収集のリモコンと同じポートなので、§4.0 のファイアウォール設定がそのまま使える（収集と同時には起動しない）。表示されるのは次のとおり。

- 確定ゾーン（大きく表示）と生の推定
- 浴槽の見取り図。ゾーンごとの確率を色の濃さで示し、確定ゾーンは太枠、生の推定は点線
- 誰もいない確率、TXごとの受信レート（受信が止まったTXは赤で表示）
- ゾーンの切り替わりの履歴
- 鯉の反応（モード）の切り替えボタン A=寄る / B=分散 / C=周回 / N=反応なし（`--osc` も付けたときだけ表示。押すと `/koi/mode` を送る）

推定中はPCがスリープしない。

出力例: `t=44.02  zone=0 (empty)  raw=0 (empty)  p=[0.39 0.02 0. 0. 0. 0.22 0. 0. 0. 0.36]`

| 項目 | 意味 |
| ---- | ---- |
| `t` | 推定開始からの経過秒 |
| `zone` | **確定ゾーン**（OSCで送られる値）。直近 `--hysteresis-frames` の `raw` のうち、別ゾーンが `--hysteresis-ratio` 以上を占めたときに切り替わる（多数決） |
| `raw` | 平滑化後の確率が最大のゾーン（そのままの判定） |
| `p=[...]` | ゾーン0〜9の確率（IDの昇順、EMA平滑化後）。学習していないゾーンは常に0 |

調整パラメータ: `--proba-ema`（既定0.3、大=追従重視）、`--hysteresis-frames`（既定20≒1秒）、`--hysteresis-ratio`（既定0.5）、`--zone-threshold`（既定0＝全フレームを票に数える）。
境界でばたつく場合は `--hysteresis-ratio` かフレーム数を上げる。切り替わりが遅い・変わらない場合は `--hysteresis-ratio` かフレーム数を下げる（2026-10-05に、連続N回方式から多数決方式に変更。連続方式では確率が散らばるモデルだと確定ゾーンがほぼ変わらなかった）。

## 7. 実施結果（2026-10-03、TX ID3のみ・1リンク）

> 2026-10-04以降の結果は [results.md](results.md) に記録する。

データ: empty 3セッション（約30秒×3）、ゾーン1・5・9 各2セッション（約10秒）。

| 評価（leave-one-session-out、4クラス） | 正解率 | macro-F1 | emptyの再現率 |
| ---- | ------ | -------- | ------------- |
| rf（正しい条件） | 0.628 | 0.627 | 0.548 |
| knn（正しい条件） | 0.642 | 0.637 | 0.577 |

- ゾーン別の再現率（rf）: empty 0.55、1: 0.68、5: 0.81、9: 0.64。
- **emptyが弱い**: 誰もいないのに、ゾーン9（約29%）や5（約16%）と判定される。
- 原因の見立て: ①TXが1台で経路が1本、②emptyのセッションが3つしかなく評価時の学習用が1つ、③時間による環境の揺れ（emptyの平均パターンがセッション間で0.05〜0.11ずれる）。
- empty収録データの確認: 受信約97Hz、`seq` 欠落なし、64サブキャリア中12本は常に0（L-LTFの両端・中央で正常）。

### 次にやること（優先順）

1. TXを1台追加する（TX ID 1、座標 (3,0)。`zones.json` で `enabled: true`）→ 再収集・再学習
2. データを増やす: emptyを+2〜3セッション、各ゾーンを3〜4セッション（位置・姿勢を変えて）
3. 特徴量の改善: 直近Nフレームの平均・標準偏差を使う（人がいると振幅が揺れるため）。コードの変更が必要
4. 本番の推定は確率平滑化とヒステリシスが入るので、フレームごとの正解率より体感精度は高い。`estimate` で実際に歩いて確認する

## 8. データの保管と再学習

- `server/data/` は **Gitで管理されない**（`.gitignore` の `server/data/`、`*.pkl`、`*.jsonl`）。`empty.jsonl`・`calib.jsonl`・`model.pkl` は、必要ならUSBメモリやクラウドなどに別途バックアップする。
- **最短の再学習**（データが残っている場合）: §3 → §5 のコマンドのみ。
- 設置（RX/TXの位置・向き・高さ）、`zones.json`、TX台数、湯量などを変えたら、empty・各ゾーンとも再収集して再学習する。

## 9. つまずいたこと

| 症状 | 原因と対処 |
| ---- | ---------- |
| `sed` が効かない | PowerShellで実行していた。Git Bashで実行する |
| `menuconfig` に `CSI TX Configuration` が出ない | `csi_rx` で開いていた。`csi_tx` のフォルダで開く |
| `packets so far` を失敗と誤解 | 累計受信数の表示。`done: ... packets recorded` が出れば成功 |
| 学習精度が実際より高い | `--data` に `empty.jsonl` を入れていた（§5） |
| `could not open port` | 他のプログラム（monitor等）がCOMポートを使用中、またはCOM番号の違い |
| `DLL load failed ... アプリケーション制御ポリシーによってこのファイルがブロックされました` | Windowsの「スマート アプリ コントロール」が、評判の確立していないファイルとして scikit-learn 1.9.1 の `sklearn.ensemble` のネイティブモジュールを止めていた（2026-10-04に発生）。1.9.0 は止められないため `requirements.txt` を 1.9.0 に固定した（`pip install -r requirements.txt` で入れ直す）。ライブラリを更新して再発したら、止められないバージョンに戻す |
