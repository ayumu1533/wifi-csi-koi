"""フィンガープリンティング用のラベル付きCSI収集CLI。

浴槽内の指定ゾーンに人（または模擬物体）を配置した状態で実行すると、
指定秒数のCSIパケットを記録し、ゾーンラベル付きでJSONLに追記する。

使い方（ゾーンIDは zones.json で定義したもの。0は「empty」＝人がいない状態）:
    python -m collection.collect --zone 1 --duration 5 --out data/calib.jsonl
    python -m collection.collect --zone 0 --delay 0 --duration 30 --out data/empty.jsonl

これを全ゾーンで繰り返してデータセットを作る。
1回の実行 = 1セッション（session IDで区別）として保存され、
学習時はセッション単位でフレーム同期を再生する。

収集のコツ: ゾーン内で1点に静止し続けるのではなく、ゾーンの範囲内で
少しずつ位置や姿勢を変えながら記録すること。1点で静止した収集だけだと、
ゾーン内で起こりうる振幅のばらつきをモデルが学習できない。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import time
import uuid
from collections import Counter
from datetime import date

from csi_receiver.packet import InvalidPacketError, parse_packet
from csi_receiver.source import add_source_args, open_source
from config.zones import DEFAULT_ZONES_PATH, load_zones

logger = logging.getLogger("collect")


def make_record(packet, session: str, collected_date: str, zone_id: int, zone_name: str,
                extra: dict | None = None) -> dict:
    """1パケット分のJSONLレコード（collect と collection.plan で共通の形式）。"""
    record = {
        "session": session,
        "date": collected_date,  # 収集日（B1の日付単位分割評価に使う）
        "zone": zone_id,
        "zone_name": zone_name,
        "sensor_id": packet.sensor_id,
        "seq": packet.seq,
        "timestamp_us": packet.timestamp_us,
        "recv_time": time.monotonic(),
        "rssi": packet.rssi,
        "rate": packet.rate,
        "amplitude": packet.amplitude,
    }
    if extra:
        record.update(extra)
    return record


class CollectProtocol(asyncio.DatagramProtocol):
    def __init__(self, out_file, zone_id: int, zone_name: str, session: str, collected_date: str,
                 sensor_ids: list[int]) -> None:
        self._out_file = out_file
        self._sensor_ids = set(sensor_ids)  # これ以外のセンサーのパケットは記録しない
        self._zone_id = zone_id
        self._zone_name = zone_name
        self._session = session
        self._date = collected_date
        self.counts: Counter[int] = Counter()
        self.recording = False

    def datagram_received(self, data: bytes, addr) -> None:
        if not self.recording:
            return
        try:
            packet = parse_packet(data)
        except InvalidPacketError as exc:
            logger.warning("invalid packet from %s: %s", addr, exc)
            return
        if packet.sensor_id not in self._sensor_ids:
            return

        record = make_record(packet, self._session, self._date, self._zone_id, self._zone_name)
        self._out_file.write(json.dumps(record) + "\n")
        self.counts[packet.sensor_id] += 1


async def run(args, zone_name: str, sensor_ids: list[int]) -> None:
    session = uuid.uuid4().hex[:12]
    collected_date = date.today().isoformat()

    with open(args.out, "a", encoding="utf-8") as out_file:
        protocol = CollectProtocol(out_file, args.zone, zone_name, session, collected_date, sensor_ids)
        transport = await open_source(args, lambda: protocol)
        try:
            if args.delay > 0:
                logger.info(
                    "zone %d (%s): starting in %d seconds, get in position...",
                    args.zone, zone_name, args.delay,
                )
                for i in range(args.delay, 0, -1):
                    logger.info("  %d...", i)
                    await asyncio.sleep(1)

            logger.info("recording %d seconds in zone %d (%s), session=%s",
                        args.duration, args.zone, zone_name, session)
            protocol.recording = True
            for _ in range(args.duration):
                await asyncio.sleep(1)
                logger.info("  packets so far: %s",
                            dict(sorted(protocol.counts.items())) or "none")
            protocol.recording = False
        finally:
            transport.close()

    total = sum(protocol.counts.values())
    logger.info("done: %d packets recorded to %s", total, args.out)
    if not protocol.counts:
        logger.error("no packets received from sensors %s! check csi_tx/csi_rx and the serial port", sensor_ids)
    else:
        for sid in sorted(protocol.counts):
            logger.info("  sensor %d: %d packets (%.0f Hz)",
                        sid, protocol.counts[sid], protocol.counts[sid] / args.duration)
        missing = [sid for sid in sensor_ids if sid not in protocol.counts]
        if missing:
            logger.error("no packets from sensors %s", missing)


def main() -> None:
    parser = argparse.ArgumentParser(description="labeled CSI collection for zone-classification fingerprinting")
    parser.add_argument("--zone", type=int, required=True, help="収集するゾーンID（zones.json参照。0=empty）")
    parser.add_argument("--zones", default=DEFAULT_ZONES_PATH, help="ゾーン定義ファイル")
    parser.add_argument("--duration", type=int, default=5, help="記録秒数")
    parser.add_argument("--delay", type=int, default=3, help="記録開始までの待ち秒数（位置につく時間）")
    parser.add_argument("--sensors", default=None,
                        help="使うセンサーID（例: 1,3）。省略時はzones.jsonのenabledに従う")
    add_source_args(parser)
    parser.add_argument("--out", required=True, help="追記先JSONLファイル")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    zone_config = load_zones(args.zones)
    try:
        zone = zone_config.by_id(args.zone)
    except KeyError:
        valid = zone_config.zone_ids()
        raise SystemExit(f"zone {args.zone} is not defined in {args.zones} (valid ids: {valid})")

    sensor_ids = zone_config.resolve_sensor_ids(args.sensors)
    logger.info("using sensors: %s", sensor_ids)

    try:
        asyncio.run(run(args, zone.name, sensor_ids))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
