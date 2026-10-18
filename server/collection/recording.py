"""全自動収集（collection.plan）の記録: 受信パケットをメモリにためる Recorder と、JSONLからのセッション削除。"""
from __future__ import annotations

import asyncio
import os
from collections import Counter

from csi_receiver.packet import InvalidPacketError, parse_packet
from .collect import make_record


class Recorder(asyncio.DatagramProtocol):
    """記録中のパケットだけをメモリにためる（成功した回だけファイルに書く）。"""

    def __init__(self, sensor_ids: list[int]) -> None:
        self._sensor_ids = set(sensor_ids)
        self._meta: dict | None = None
        self.records: list[dict] = []
        self.counts: Counter[int] = Counter()

    def start(self, meta: dict) -> None:
        self.records = []
        self.counts = Counter()
        self._meta = meta

    def stop(self) -> list[dict]:
        self._meta = None
        return self.records

    def datagram_received(self, data: bytes, addr) -> None:
        if self._meta is None:
            return
        try:
            packet = parse_packet(data)
        except InvalidPacketError:
            return
        if packet.sensor_id not in self._sensor_ids:
            return
        m = self._meta
        self.records.append(make_record(packet, m["session"], m["date"], m["zone"], m["zone_name"],
                                        {"round": m["round"], "posture": m["posture"]}))
        self.counts[packet.sensor_id] += 1


def append_records(path: str, records: list[dict]) -> None:
    with open(path, "a", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec) + "\n")


def remove_session(path: str, session: str) -> int:
    """JSONLから指定セッションの行を取り除く（直前をやり直すとき）。取り除いた行数を返す。"""
    marker = f'"session": "{session}"'
    tmp = path + ".tmp"
    removed = 0
    with open(path, "r", encoding="utf-8") as src, open(tmp, "w", encoding="utf-8") as dst:
        for line in src:
            if marker in line:
                removed += 1
            else:
                dst.write(line)
    os.replace(tmp, path)
    return removed
