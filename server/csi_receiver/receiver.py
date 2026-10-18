"""CSIパケットを受信し、ログ出力・任意でJSONL保存する（csi_rxのシリアル、またはUDP）。

使い方:
    python -m csi_receiver.receiver --serial COM3 --out capture.jsonl   # csi_rxをUSB接続
    python -m csi_receiver.receiver --port 3334 --out capture.jsonl     # UDP（tools.simulate等）

複数TX（sensor_id）のパケットが同じ入力に流れてくる前提。
sensor_idごとに受信数・直近RSSI・平均振幅を一定間隔でコンソールに表示する
（Phase 0の実現可能性検証で、位置によって振幅が変化しているか確認するため）。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import time
from dataclasses import dataclass

from .packet import InvalidPacketError, parse_packet
from .source import add_source_args, open_source

logger = logging.getLogger("csi_receiver")


@dataclass
class SensorStats:
    packet_count: int = 0
    last_rssi: int = 0
    last_seq: int = 0
    amplitude_sum: float = 0.0
    amplitude_count: int = 0

    def mean_amplitude(self) -> float:
        if self.amplitude_count == 0:
            return 0.0
        return self.amplitude_sum / self.amplitude_count


class CsiReceiverProtocol(asyncio.DatagramProtocol):
    def __init__(self, out_file, stats_interval: float) -> None:
        self._out_file = out_file
        self._stats_interval = stats_interval
        self._stats: dict[int, SensorStats] = {}
        self._last_report = time.monotonic()

    def datagram_received(self, data: bytes, addr) -> None:
        try:
            packet = parse_packet(data)
        except InvalidPacketError as exc:
            logger.warning("invalid packet from %s: %s", addr, exc)
            return

        stats = self._stats.setdefault(packet.sensor_id, SensorStats())
        stats.packet_count += 1
        stats.last_rssi = packet.rssi
        stats.last_seq = packet.seq
        amplitude = packet.amplitude
        if amplitude:
            stats.amplitude_sum += sum(amplitude) / len(amplitude)
            stats.amplitude_count += 1

        if self._out_file is not None:
            record = {
                "sensor_id": packet.sensor_id,
                "seq": packet.seq,
                "timestamp_us": packet.timestamp_us,
                # センサー間のフレーム同期はESP32側の時計ではなく
                # サーバー受信時刻（このプロセスのmonotonic時計）で行う
                "recv_time": time.monotonic(),
                "rssi": packet.rssi,
                "rate": packet.rate,
                "amplitude": amplitude,
            }
            self._out_file.write(json.dumps(record) + "\n")

        now = time.monotonic()
        if now - self._last_report >= self._stats_interval:
            self._report()
            self._last_report = now

    def _report(self) -> None:
        parts = []
        for sensor_id in sorted(self._stats):
            s = self._stats[sensor_id]
            parts.append(
                f"sensor{sensor_id}: n={s.packet_count} rssi={s.last_rssi} "
                f"seq={s.last_seq} mean_amp={s.mean_amplitude():.1f}"
            )
        logger.info(" | ".join(parts) if parts else "no packets yet")

    def error_received(self, exc: Exception) -> None:
        logger.error("socket error: %s", exc)


async def run(args: argparse.Namespace) -> None:
    out_path = args.out
    out_file = open(out_path, "a", encoding="utf-8") if out_path else None
    try:
        transport = await open_source(args, lambda: CsiReceiverProtocol(out_file, args.stats_interval))
        if out_path:
            logger.info("writing to %s", out_path)
        try:
            await asyncio.Future()  # run forever
        finally:
            transport.close()
    finally:
        if out_file is not None:
            out_file.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="CSI UDP receiver")
    add_source_args(parser)
    parser.add_argument("--out", default=None, help="受信パケットを保存するJSONLファイル（省略時は保存しない）")
    parser.add_argument("--stats-interval", type=float, default=1.0, help="コンソールへの統計表示間隔（秒）")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
