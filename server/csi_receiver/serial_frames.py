"""csi_rxがUSBシリアルへ出力するフレームのパーサー。形式は docs/design_tx_rx.md を参照。

    [sync 0xAA 0x55][len u16][payload (= docs/protocol.md のパケット)][crc16(payload) u16]  リトルエンディアン

ブートログなど無関係なバイトが混ざっても、syncで再同期しCRCが合わないフレームは捨てる。
取り出したpayloadは csi_receiver.packet.parse_packet にそのまま渡せる。
"""
from __future__ import annotations

import binascii
import struct

SYNC = b"\xaa\x55"
MIN_PAYLOAD = 14       # パケットヘッダのみ（CSI 0バイト）
MAX_PAYLOAD = 14 + 512  # protocol.mdのCSI最大長


def crc16(data: bytes) -> int:
    """CRC-16/CCITT-FALSE（poly 0x1021, init 0xFFFF）。ファーム側のcrc16_ccittと同じ。"""
    return binascii.crc_hqx(data, 0xFFFF)


def encode_frame(payload: bytes) -> bytes:
    """テスト・模擬用: payloadをシリアルフレームに包む。"""
    return SYNC + struct.pack("<H", len(payload)) + payload + struct.pack("<H", crc16(payload))


class FrameParser:
    """バイト列を流し込むと、CRCの合ったpayloadを返す逐次パーサー。"""

    def __init__(self) -> None:
        self._buf = bytearray()
        self.frames_ok = 0
        self.crc_errors = 0
        self.skipped_bytes = 0  # sync前に読み捨てたバイト数（ログ混入など）

    def feed(self, data: bytes) -> list[bytes]:
        self._buf.extend(data)
        out: list[bytes] = []
        buf = self._buf
        while True:
            idx = buf.find(SYNC)
            if idx < 0:
                # 末尾がsyncの1バイト目かもしれないので1バイトだけ残す
                keep = 1 if buf.endswith(SYNC[:1]) else 0
                self.skipped_bytes += len(buf) - keep
                del buf[: len(buf) - keep]
                break
            if idx > 0:
                self.skipped_bytes += idx
                del buf[:idx]
            if len(buf) < 4:
                break
            (plen,) = struct.unpack_from("<H", buf, 2)
            if plen < MIN_PAYLOAD or plen > MAX_PAYLOAD:
                del buf[:1]  # 偽のsync。次のsync候補から探し直す
                self.skipped_bytes += 1
                continue
            total = 4 + plen + 2
            if len(buf) < total:
                break
            payload = bytes(buf[4 : 4 + plen])
            (rx_crc,) = struct.unpack_from("<H", buf, 4 + plen)
            if rx_crc == crc16(payload):
                out.append(payload)
                self.frames_ok += 1
                del buf[:total]
            else:
                self.crc_errors += 1
                del buf[:1]
        return out
