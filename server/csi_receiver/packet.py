"""csi_rxが出力する（またはUDPで届く）CSIパケットのパース。フォーマットはdocs/protocol.mdを参照。"""
from __future__ import annotations

import struct
from dataclasses import dataclass

MAGIC = 0xC5
HEADER_FMT = "<BBHIbBBBH"
HEADER_SIZE = struct.calcsize(HEADER_FMT)


class InvalidPacketError(ValueError):
    pass


@dataclass
class CsiPacket:
    sensor_id: int
    seq: int
    timestamp_us: int
    rssi: int
    rate: int
    csi_raw: bytes

    @property
    def amplitude(self) -> list[float]:
        """csi_rawは(imaginary, real)のint8ペア列。サブキャリアごとの振幅を返す。"""
        n_pairs = len(self.csi_raw) // 2
        out = [0.0] * n_pairs
        for i in range(n_pairs):
            imaginary = struct.unpack_from("b", self.csi_raw, i * 2)[0]
            real = struct.unpack_from("b", self.csi_raw, i * 2 + 1)[0]
            out[i] = (real * real + imaginary * imaginary) ** 0.5
        return out


def parse_packet(data: bytes) -> CsiPacket:
    if len(data) < HEADER_SIZE:
        raise InvalidPacketError(f"packet too short: {len(data)} bytes")

    magic, sensor_id, seq, timestamp_us, rssi, rate, _r0, _r1, csi_len = struct.unpack_from(
        HEADER_FMT, data, 0
    )
    if magic != MAGIC:
        raise InvalidPacketError(f"bad magic: 0x{magic:02x}")

    csi_raw = data[HEADER_SIZE:HEADER_SIZE + csi_len]
    if len(csi_raw) != csi_len:
        raise InvalidPacketError(f"csi_len={csi_len} but only {len(csi_raw)} bytes present")

    return CsiPacket(
        sensor_id=sensor_id,
        seq=seq,
        timestamp_us=timestamp_us,
        rssi=rssi,
        rate=rate,
        csi_raw=csi_raw,
    )
