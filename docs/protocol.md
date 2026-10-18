# 通信プロトコル仕様

## トポロジー概要

- `csi_tx`（1〜3台）: WiFiに接続せず、固定チャンネルでESP-NOWのブロードキャストフレームを
  `CSI_TX_INTERVAL_MS`（既定10ms≒100Hz）ごとに送信する。STAのMACを `02:C5:15:00:00:<TX ID>` に設定する。
- `csi_rx`（1台）: 同じチャンネルで受信し、フレームごとのCSI（L-LTFのみ、64サブキャリア）を取得する。
  送信元MACの先頭5バイトが `02:C5:15:00:00` のフレームだけを採用し、末尾1バイトをTX ID（= `sensor_id`）とする。
  取得したCSIを下記のシリアルフレームでPCへ出力する。
- ESP-NOWのペイロードは使わない（CSIは受信フレームからPHYが取得するため）。
- 詳細な設計は [design_tx_rx.md](design_tx_rx.md)。

## csi_rx → サーバー（USBシリアル）

- トランスポート: UART0（既定。921600bps, 8N1）または USB-Serial-JTAG（`CSI_RX_USE_USB_SERIAL_JTAG`）
- バイトオーダー: リトルエンディアン

### シリアルフレーム

| offset | size    | field   | 説明 |
| ------ | ------- | ------- | ---- |
| 0      | 2       | sync    | `0xAA 0x55` |
| 2      | 2       | len     | payload長（= 14 + csi_len） |
| 4      | len     | payload | 下記「CSIパケット」 |
| 4+len  | 2       | crc16   | payloadのCRC-16/CCITT-FALSE（poly 0x1021, init 0xFFFF。Pythonでは `binascii.crc_hqx(payload, 0xFFFF)`） |

- 起動直後のブートログなど無関係なバイトが混ざってもよい。受信側は `sync` で再同期し、CRCの合わないフレームは捨てる
  （実装: `server/csi_receiver/serial_frames.py`）。
- 帯域: 1フレーム約148バイト × 3台 × 100Hz ≒ 44KB/s。

## CSIパケット（payload。UDPで送る場合も同じ形式）

`--serial` を付けない場合のUDP入力（`tools.simulate` など）は、このパケットをそのままUDPで送る
（既定ポート3334）。

ヘッダ14バイト + CSI生データ（可変長、最大512バイト）。

| offset | size    | field        | type    | 説明 |
| ------ | ------- | ------------ | ------- | ---- |
| 0      | 1       | magic        | uint8   | 固定値 `0xC5` |
| 1      | 1       | sensor_id    | uint8   | TX ID（`CONFIG_CSI_TX_ID`。送信元MACの末尾） |
| 2      | 2       | seq          | uint16  | TXごとにcsi_rxが付ける受信連番（wrap around） |
| 4      | 4       | timestamp_us | uint32  | csi_rxの`esp_timer_get_time()`の下位32bit（CSI受信時刻、約71分でwrap） |
| 8      | 1       | rssi         | int8    | 受信RSSI（dBm） |
| 9      | 1       | rate         | uint8   | PHYレート符号（`wifi_pkt_rx_ctrl_t.rate`） |
| 10     | 2       | reserved     | uint8x2 | 予約（0固定） |
| 12     | 2       | csi_len      | uint16  | 後続CSI生データのバイト数 |
| 14     | csi_len | csi_data     | int8[]  | CSI生データ（サブキャリアごとに `imaginary, real` の順で並ぶint8ペア） |

Python側の読み取り例:

```python
import struct

HEADER_FMT = "<BBHIbBBBH"  # 14 bytes
HEADER_SIZE = struct.calcsize(HEADER_FMT)

def parse_packet(data: bytes):
    magic, sensor_id, seq, timestamp_us, rssi, rate, r0, r1, csi_len = struct.unpack(
        HEADER_FMT, data[:HEADER_SIZE]
    )
    csi_data = data[HEADER_SIZE:HEADER_SIZE + csi_len]
    return sensor_id, seq, timestamp_us, rssi, csi_data
```

## サーバー → TouchDesigner

- トランスポート: OSC（UDP）
- 送信元: `python -m localization.estimate --osc <host>:<port>`
- TouchDesigner側: OSC In CHOPで同じポートを待ち受ける
- ゾーンID・名前・中心座標は `server/config/zones.json` で定義する（ID 0 = empty＝人がいない）

| アドレス | 引数 | 送信タイミング |
| -------- | ---- | -------------- |
| `/koi/zone` | `[zone_id: int]` | 確定ゾーン。変化時に即送信＋`--zone-report-interval`（既定1秒）ごとに定期送信 |
| `/koi/zone_probs` | `[p0, p1, ...: float32]` | 平滑化後のゾーン確率。推定フレームごと（約20Hz）、`zones.json` のID昇順 |
| `/koi/position` | `[x, y: float32]` | 確定ゾーンの中心座標（メートル）。`/koi/zone` と同じタイミング。emptyのときは送らない。後方互換用 |
| `/koi/mode` | `[mode: str]` | 応答設計の切り替え（`A`=寄る `B`=分散 `C`=周回 `N`=反応なし）。定期的には送らない。`tools.osc_manual` の `mode X`、または `localization.estimate --remote --osc ...` のスマホ画面のボタンで送る |

### 確定ゾーンの決め方

1. 推定フレームごとに `predict_proba` の確率ベクトルへEMA平滑化（`--proba-ema`、既定0.3）をかける
2. 平滑化確率が最大のゾーン（生の判定）を、直近 `--hysteresis-frames`（既定20フレーム≒約1秒）分だけ記録する
   （確率が `--zone-threshold`（既定0＝無効）未満のフレームは数えない）
3. 確定ゾーン以外の同じゾーンが、その `--hysteresis-ratio`（既定0.5）以上を占めたら確定ゾーンを切り替える
   （多数決。連続である必要はなく、外れのフレームが混じっても切り替わる）
4. これによりゾーン境界でのチャタリングを抑える。境界での「迷い」は `/koi/zone_probs` で
   TouchDesigner側の演出に使える（例: 2ゾーンの確率が拮抗しているときは中間に寄る）

### `--out` のCSV

`time, zone, raw_zone, p0..pN`（`zone`=確定ゾーン、`raw_zone`=平滑化確率のargmax）。
