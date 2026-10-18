"""推定結果をTouchDesignerへOSCで送る（仕様は docs/protocol.md）。

    /koi/zone       [zone_id: int]        確定ゾーン（変化時＋定期送信）
    /koi/zone_probs [p0, p1, ...]         平滑化後のゾーン確率（毎フレーム、zones.jsonのID昇順）
    /koi/position   [x: float, y: float]  確定ゾーンの中心座標（emptyのときは送らない）
    /koi/mode       [mode: str]           応答設計の切り替え（スマホのモニターでボタンを押したときだけ）
"""
from __future__ import annotations

from config.zones import ZoneConfig


class OscOutput:
    def __init__(self, address: str, zone_config: ZoneConfig) -> None:
        from pythonosc.udp_client import SimpleUDPClient  # 未使用時にpython-oscを要求しないよう遅延import

        host, port_str = address.rsplit(":", 1)
        self._client = SimpleUDPClient(host, int(port_str))
        self._zone_config = zone_config

    def send_probs(self, full_proba) -> None:
        self._client.send_message("/koi/zone_probs", [float(p) for p in full_proba])

    def send_zone(self, zone_id: int) -> None:
        self._client.send_message("/koi/zone", int(zone_id))
        center = self._zone_config.by_id(zone_id).center
        if center is not None:
            self._client.send_message("/koi/position", [float(center[0]), float(center[1])])

    def send_mode(self, mode: str) -> None:
        self._client.send_message("/koi/mode", mode)
