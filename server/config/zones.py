"""ゾーン定義ファイル(zones.json)の読み込みと、座標⇔ゾーンの共通処理。

ゾーンの数・配置はコードに直接書かず、すべて zones.json 側で管理する。
collection（収集）、training（学習）、localization（推定）、tools（模擬センサー）の
どのモジュールからも同じ定義を参照できるよう、ここに集約する。
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass

# 各CLIの --zones の既定値（server/ で実行する前提の相対パス）
DEFAULT_ZONES_PATH = os.path.join("config", "zones.json")


@dataclass
class Zone:
    id: int
    name: str
    center: tuple[float, float] | None  # "empty"などcenterを持たないゾーンはNone
    rect: tuple[float, float, float, float] | None  # (xmin, ymin, xmax, ymax)


@dataclass
class Sensor:  # TX（csi_tx）1台。idはTX ID = パケットのsensor_id
    id: int
    pos: tuple[float, float]
    enabled: bool = True  # Falseのセンサーは収集・学習・仮想センサーで無視される（台数の切替用）


@dataclass
class ZoneConfig:
    units: str
    tub_size: tuple[float, float]
    sensors: list[Sensor]
    zones: list[Zone]
    ap_pos: tuple[float, float] | None = None  # RX(csi_rx)の位置。zones.jsonの"rx"。表示・設置の目安用で、推定には使わない

    def active_sensor_ids(self) -> list[int]:
        """enabled=trueのセンサーIDをID昇順で返す。"""
        return sorted(s.id for s in self.sensors if s.enabled)

    def active_sensor_positions(self) -> dict[int, tuple[float, float]]:
        return {s.id: s.pos for s in self.sensors if s.enabled}

    def resolve_sensor_ids(self, override: str | None) -> list[int]:
        """使うセンサーIDを決める。overrideは "1,3" のようなCLI指定（--sensors）。

        overrideが無ければzones.jsonのenabledに従う。overrideは zones.json に
        定義済みのIDでなければならない（位置情報が必要なため）。
        """
        if override is None:
            ids = self.active_sensor_ids()
        else:
            try:
                ids = sorted({int(tok) for tok in override.split(",") if tok.strip()})
            except ValueError:
                raise SystemExit(f"--sensors の形式が不正です: {override!r}（例: 1,3）")
            unknown = [i for i in ids if i not in {s.id for s in self.sensors}]
            if unknown:
                raise SystemExit(f"--sensors に zones.json 未定義のIDがあります: {unknown}")
        if not ids:
            raise SystemExit("使用するセンサーがありません（zones.json の enabled か --sensors を確認）")
        return ids

    def zone_ids(self) -> list[int]:
        """定義ファイルの記載順によらず、ID昇順で返す（OSC/CSVの列順に使う）。"""
        return sorted(z.id for z in self.zones)

    def by_id(self, zone_id: int) -> Zone:
        for z in self.zones:
            if z.id == zone_id:
                return z
        raise KeyError(f"unknown zone id: {zone_id}")

    def sensor_positions(self) -> dict[int, tuple[float, float]]:
        return {s.id: s.pos for s in self.sensors}

    def point_to_zone(self, x: float, y: float) -> int | None:
        """座標(x, y)を含む矩形を持つゾーンのIDを返す。

        既存の --x/--y 付きデータ（旧・座標回帰用）をゾーンラベルへ変換するために使う。
        どの矩形にも入らない場合はNone（境界外・emptyゾーンなど）。
        """
        for z in self.zones:
            if z.rect is None:
                continue
            x0, y0, x1, y1 = z.rect
            if x0 <= x < x1 and y0 <= y < y1:
                return z.id
        return None

    def to_dict(self) -> dict:
        """モデルbundleへの保存用に、読み込み時と同じ形のdictへ戻す。"""
        return {
            "units": self.units,
            "rx": {"pos": list(self.ap_pos)} if self.ap_pos is not None else None,
            "tub_size": list(self.tub_size),
            "tx": [{"id": s.id, "pos": list(s.pos), "enabled": s.enabled} for s in self.sensors],
            "zones": [
                {
                    "id": z.id,
                    "name": z.name,
                    "center": list(z.center) if z.center is not None else None,
                    "rect": list(z.rect) if z.rect is not None else None,
                }
                for z in self.zones
            ],
        }


def _build_config(data: dict) -> ZoneConfig:
    sensors = [
        Sensor(id=s["id"], pos=tuple(s["pos"]), enabled=bool(s.get("enabled", True)))
        for s in data.get("tx", data.get("sensors", []))  # 旧キー "sensors" も読む
    ]
    zones = [
        Zone(
            id=z["id"],
            name=z["name"],
            center=tuple(z["center"]) if z.get("center") is not None else None,
            rect=tuple(z["rect"]) if z.get("rect") is not None else None,
        )
        for z in data["zones"]
    ]
    return ZoneConfig(
        units=data.get("units", "m"),
        tub_size=tuple(data.get("tub_size", (0.0, 0.0))),
        sensors=sensors,
        zones=zones,
        ap_pos=tuple((data.get("rx") or data.get("ap"))["pos"]) if (data.get("rx") or data.get("ap")) else None,
    )


def load_zones(path: str) -> ZoneConfig:
    """zones.jsonファイルを読み込む。"""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return _build_config(data)


def zone_config_from_dict(data: dict) -> ZoneConfig:
    """モデルbundleに保存済みのzones dict（to_dict()の逆）からZoneConfigを復元する。"""
    return _build_config(data)
