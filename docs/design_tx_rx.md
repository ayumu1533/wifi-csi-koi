# 設計: TX複数台 → RX1台（USB接続）構成

状態: **実装済み（Phase 1〜3）。実機での検証（Phase 0 の確認項目）は未実施。**

- ファーム: `firmware/csi_tx`, `firmware/csi_rx`（ビルド確認済み）
- サーバー: `csi_receiver/serial_frames.py`, `csi_receiver/source.py`、`--serial` 対応、`tests/`（8テスト合格）
- `zones.json`: `rx` / `tx` キー（旧 `ap` / `sensors` も読める）
- 旧 `csi_ap`, `csi_sta` は `legacy/` へ移動済み

## 1. 目的と方針

- 送信機（TX）を1〜3台、受信機（RX）を1台にする。
  - 2台運用: TX 1台 + RX 1台
  - 4台運用: TX 3台 + RX 1台
- **RXをPCにUSB接続し、CSIをシリアルで直接受け取る**。TX側でCSIは取らず、WiFiネットワーク・UDP転送・PCのWiFi接続は不要にする。
- 台数の切替は今と同じ（`zones.json` の `enabled` と `--sensors`）。
- サーバー側の学習・推定ロジック（特徴量・フレーム同期・分類）は**変更しない**。入力の取り込み口だけ追加する。

## 2. 現行との違い

| 項目 | 現行 | 新構成 |
| ---- | ---- | ------ |
| 送信側 | csi_ap（Soft-AP、1台） | **csi_tx**（複数台。ESP-NOWで定期送信） |
| 受信側 | csi_sta（複数台。CSI取得→UDPでPCへ） | **csi_rx**（1台。CSI取得→USBシリアルでPCへ） |
| 電波経路 | AP → 各STA | 各TX → RX（経路の本数・位置は同じ考え方） |
| PCのWiFi | AP（csi-tub-ap）へ接続が必要 | **不要**（RXにUSBケーブルのみ） |
| IP設定 | サーバーIP固定・DHCP | **不要** |
| 時計 | STAごとに別々（サーバー受信時刻で同期） | CSIを受ける機器が1台のため同一時計 |

## 3. 全体構成

```text
 [csi_tx ID1] ─┐
 [csi_tx ID2] ─┼─ ESP-NOW (2.4GHz, 固定チャンネル, 約100Hz/台) ─▶ [csi_rx] ══USB══ [PC]
 [csi_tx ID3] ─┘                                                    │
                                                                    └ CSI取得 → 送信元MACでTXを識別 → シリアルフレーム化

 PC: csi_receiver(serial入力) → フレーム同期 → 特徴量 → 分類 → OSC → TouchDesigner
```

## 4. 方式の選択: ESP-NOW（推奨）

TX→RXの送信方式の候補を比較した。

| 方式 | 内容 | 評価 |
| ---- | ---- | ---- |
| **A. ESP-NOW（推奨）** | WiFi接続せず、TXが固定チャンネルでESP-NOWフレームを送る。RXは同チャンネルで受信し、フレームごとにCSIを得る | 接続・IP・DHCPが不要で構成が最小。Espressif公式のesp-csiも同方式 |
| B. RXをSoft-APにしてTXをSTA接続 | TXがUDPをRXへ送る | 接続処理・IPスタックが必要で、起動順や再接続の不確定要素が増える |

ESP-NOWを採用する。TXはペイロードを使わず「電波を出すこと」だけが役割で、CSIはRXのPHYが受信フレームから取る。

## 5. TXの識別

RXのCSIコールバックは**送信元MAC**を持つが、ペイロードは見えない。そこで、

- 各TXは起動時に **STAのMACアドレスを `02:C5:15:00:00:<tx_id>`** に設定する（ローカル管理MAC。`tx_id` は書き込み時のKconfig値）。
- RXは送信元MACの先頭5バイトが一致するフレームだけを採用し、末尾1バイトを `tx_id`（= 現行の `sensor_id`）とする。
- 近隣の他のWiFi機器のフレームは先頭5バイトで自動的に除外される。

これでMAC表の管理や学習処理が不要になり、デバイスの個体MACを調べる手間もない。

## 6. ファームウェア

### 6.1 csi_tx（新規、`firmware/csi_tx/`）

- `esp_wifi` をSTAモードで起動（接続はしない）、チャンネル固定、`esp_wifi_set_mac` で上記MACを設定。
- ESP-NOWを初期化し、ブロードキャストピアへ `CONFIG_CSI_TX_INTERVAL_MS`（既定10ms）ごとに小さなフレーム（連番程度）を送信。
- 送信レートは固定（MCS0 など。esp-csiの例に倣う）。
- Kconfig: `CSI_TX_ID`（0〜254）、`CSI_TX_CHANNEL`（既定1）、`CSI_TX_INTERVAL_MS`。
- 3台とも同一ファームで、IDだけ書き込み時に変える（現行のSensor IDと同じ運用）。

### 6.2 csi_rx（新規、`firmware/csi_rx/`）

- `esp_wifi` をSTAモードで起動（接続しない）、チャンネルをTXと同じに固定。
- **CSI有効化**: `sdkconfig.defaults` に `CONFIG_ESP_WIFI_CSI_ENABLED=y`（下記「現行ファームの問題」参照）。
- CSI設定は **LLTFのみ**（`lltf_en=true`、他は無効）。1フレームのCSIが128バイト（64サブキャリア）に収まり、サーバー側の想定長（64）とも一致する。
- CSIコールバックで送信元MACを検査 → `tx_id` を決定 → 下記のシリアルフレームへ詰めて、キュー経由の送信タスクがUARTへ書き出す（コールバック内ではブロックしない）。
- 満杯時は古いデータを捨てる（現行STAと同じ方針）。
- Kconfig: `CSI_RX_CHANNEL`（TXと一致させる）、`CSI_RX_BAUD`（既定921600）。

### 6.3 シリアル転送（RX → PC）

帯域の見積もり: 1フレーム ≈ 同期2 + 長さ2 + ヘッダ14 + CSI128 + CRC2 = 148バイト、3台×100Hz = 300フレーム/秒 → 約44KB/s（≈440kbps相当）。現在お使いのボード（COM3 = CP210x USB-UARTブリッジ）の921600bpsに収まる。

**フレーム形式**（リトルエンディアン）:

| offset | size | field | 説明 |
| ------ | ---- | ----- | ---- |
| 0 | 2 | sync | `0xAA 0x55` |
| 2 | 2 | len | 以降のペイロード長（= 14 + csi_len） |
| 4 | len | payload | **現行のUDPパケットと同一**（14バイトヘッダ + CSI。`sensor_id` = `tx_id`、`timestamp_us` = RX側のCSI受信時刻） |
| 4+len | 2 | crc16 | payloadのCRC-16 |

- ペイロードを現行と同一にするため、PC側の `csi_receiver.packet.parse_packet` を**そのまま再利用**できる。
- 起動時のブートログやESP_LOGの文字列がUART上に混ざっても、PC側は `sync` で再同期し、CRCが合わないものを捨てる。RXは起動後にログレベルをERRORに絞る。
- 高帯域が必要になった場合（HT-LTFも使う等）は、ネイティブUSB（USB-Serial-JTAG）またはボーレート引き上げに切り替える（Kconfigで対応）。

## 7. サーバー側の変更

- `csi_receiver/source.py`（新規）: パケット入力の抽象化。
  - `UdpSource`（既存の動作。`tools.simulate` や旧構成で使用）
  - `SerialSource`（新規。pyserialで読み、`sync`/`len`/`crc16` を検証してpayloadを返す）
- `receiver`、`collection.collect`、`localization.estimate` に `--serial COM5 [--baud 921600]` を追加。省略時はUDP（互換維持）。
- `requirements.txt` に `pyserial` を追加（バージョン固定）。
- `recv_time` はPCがフレームを受け取った時刻を使う（USBの数msのまとまり受信は、フレーム周期50msに対して十分小さい）。
- `zones.json`: 意味が変わるキーを整理する。
  - `ap` → **`rx`**（RXの位置）、`sensors` → **`tx`**（TXの位置と `enabled`）
  - 旧キー（`ap` / `sensors`）も読めるようにして互換を保つ
  - 既定は RX=(0,0)、TX=ID1 (3,0) / ID2 (0,3) / ID3 (3,3)。2台運用は ID3 のみ `enabled`

## 8. 運用（2台／4台）

| 運用 | 書き込む機器 | PCへの接続 | 設置 |
| ---- | ------------ | ---------- | ---- |
| 2台 | csi_rx ×1、csi_tx ×1（ID3） | **RXのみUSB** | RX=(0,0)、TX=(3,3) |
| 4台 | csi_rx ×1、csi_tx ×3（ID1,2,3） | **RXのみUSB** | RX=(0,0)、TX=(3,0),(0,3),(3,3) |

- TXは電源だけ（モバイルバッテリー等）。RXはUSBでPCと給電を兼ねる。
- 台数の切替は `zones.json` の `enabled`（または `--sensors`）で行い、切替後は再収集・再学習が必要（現状と同じ）。

## 9. リスクと事前検証（Phase 0）

実機で未確認の前提があるため、実装の最初に最小の検証を行う。

1. **ESP-NOWフレームでRXのCSIコールバックが発火し、送信元MACが取れるか**（TX1台＋RX1台、数十行の試験コード）
2. **TX複数台からの受信レートが目標（各約100Hz）に届くか**（衝突でどれだけ落ちるか）
3. **UART 921600bpsでフレーム欠落なく転送できるか**

2と3は、実測の結果で `CSI_TX_INTERVAL_MS`・ボーレート・LLTFのみの設定を調整する。

### 現行ファームの問題（調査中に判明）

現行の `firmware/csi_sta/sdkconfig` と `csi_ap/sdkconfig` では **`CONFIG_ESP_WIFI_CSI_ENABLED` が無効**（`# CONFIG_ESP_WIFI_CSI_ENABLED is not set`）。この設定でCSI APIを呼ぶと、実機では `ESP_ERROR_CHECK` で止まる可能性が高い。新ファームでは `sdkconfig.defaults` で有効化する。

## 10. 実装計画

1. Phase 0: 上記の最小検証（csi_tx / csi_rx の骨組み）
2. Phase 1: csi_tx / csi_rx のファームウェア本実装（Kconfig、`sdkconfig.defaults`、READMEにビルド・書き込み手順）
3. Phase 2: サーバー側 `SerialSource` と `--serial` 対応、`pyserial` 追加、フレーム解析の単体テスト（疑似シリアルでCRC不一致・途中切れ・ログ混入を検証）
4. Phase 3: `zones.json` のキー整理（互換読み込み）、仮想センサーはUDP経由のままでパイプライン全体を再確認
5. Phase 4: ドキュメント更新（README、calibration、protocol。旧構成の記述を差し替え）、旧 `csi_ap` / `csi_sta` の扱いを決める

## 11. 未決事項

なし（以下は決定済み）。

- **RXのシリアル**: ボードにはUARTポート（CP210x）とネイティブUSBポートの両方がある。まずUARTポートを921600bpsで使う（CP210xは921600に対応しており、必要帯域約44KB/sに足りる）。欠落が出た場合のみ、Kconfigでネイティブ USB（USB-Serial-JTAG）へ切り替える。
- **旧ファーム**: `git mv` で `legacy/` へ移動済み（削除はしない。履歴も残る）。
