"""csi_txの代わりに合成CSIパケットをUDP送信する仮想TX。

実機なしで collect / train / estimate の全パイプラインを動作確認するためのツール。
zones.jsonで有効(enabled)なセンサー（--sensorsで上書き可）が浴槽に配置されていると仮定し、人の位置に応じて各センサーの振幅パターンが
決定的に変化する（＝学習可能な）モデルでパケットを生成する。
浴槽サイズ・センサー位置・ゾーン配置は zones.json から読む。

使い方:
    # 静止位置 (0.3, 0.5) の人を模擬（座標回帰時代の互換モード）
    python -m tools.simulate --pos 0.3,0.5

    # 空の浴槽（ベースライン/ゾーン0収集用）
    python -m tools.simulate --empty

    # 指定ゾーン内をランダムに少し動く人を模擬（collection.collect --zoneでの収集確認用）
    python -m tools.simulate --zone 1

    # 全ゾーンを順番に巡回する人を模擬（境界をまたぐ動き。localization.estimateの確認用）
    python -m tools.simulate --traj tour --period 10

    # 円軌道で動く人を模擬
    python -m tools.simulate --traj circle --period 15
"""
from __future__ import annotations

import argparse
import math
import socket
import struct
import time

import numpy as np

from csi_receiver.packet import HEADER_FMT, MAGIC
from config.zones import DEFAULT_ZONES_PATH, ZoneConfig, load_zones

N_SUBCARRIERS = 64


def base_pattern(sensor_id: int) -> np.ndarray:
    """センサー個体ごとの固定的なマルチパスパターン（毎回同じ）。"""
    rng = np.random.default_rng(1000 + sensor_id)
    pattern = 30.0 + 10.0 * np.sin(np.linspace(0, 3 * np.pi, N_SUBCARRIERS) + sensor_id)
    pattern += rng.normal(0, 2.0, N_SUBCARRIERS)
    return np.clip(pattern, 5.0, 90.0)


def person_gain(
    sensor_pos: tuple[float, float],
    person_pos: tuple[float, float] | None,
    tub_size: tuple[float, float],
) -> np.ndarray:
    """人の位置に応じたサブキャリアごとのゲイン変化。人がいなければ1。"""
    if person_pos is None:
        return np.ones(N_SUBCARRIERS)
    d = math.dist(sensor_pos, person_pos)
    d_max = math.dist((0, 0), tub_size)
    # 人体で生じる減衰の谷。中心サブキャリアと深さが距離に依存して変わる
    center = (d / d_max) * (N_SUBCARRIERS - 1)
    depth = 0.6 / (1.0 + 3.0 * d)
    k = np.arange(N_SUBCARRIERS)
    dip = depth * np.exp(-((k - center) ** 2) / (2 * 6.0**2))
    return 1.0 - dip


def make_packet(sensor_id: int, seq: int, amplitude: np.ndarray, rng: np.random.Generator) -> bytes:
    """振幅ベクトルを (imaginary, real) のint8ペア列にエンコードしてパケット化する。"""
    phases = rng.uniform(0, 2 * math.pi, len(amplitude))
    reals = np.clip(np.round(amplitude * np.cos(phases)), -127, 127).astype(np.int8)
    imags = np.clip(np.round(amplitude * np.sin(phases)), -127, 127).astype(np.int8)
    csi = np.empty(len(amplitude) * 2, dtype=np.int8)
    csi[0::2] = imags
    csi[1::2] = reals

    timestamp_us = int(time.monotonic() * 1e6) & 0xFFFFFFFF
    header = struct.pack(
        HEADER_FMT, MAGIC, sensor_id, seq & 0xFFFF, timestamp_us,
        -55, 11, 0, 0, len(csi),
    )
    return header + csi.tobytes()


class PersonSimulator:
    """人の位置を時刻から決める。ゾーン内乱歩・ゾーン巡回・円軌道・固定位置・不在に対応する。"""

    def __init__(self, args: argparse.Namespace, zone_config: ZoneConfig, rng: np.random.Generator) -> None:
        self._args = args
        self._zone_config = zone_config
        self._rng = rng
        self._walk_pos: np.ndarray | None = None
        self._tour_zones = [z for z in zone_config.zones if z.rect is not None]
        if args.traj == "tour" and len(self._tour_zones) < 2:
            raise SystemExit("--traj tour には rect を持つゾーンが2つ以上zones.jsonに必要です")

    def _walk_within(self, center: tuple[float, float], rect: tuple[float, float, float, float],
                      step_std: float = 0.03) -> tuple[float, float]:
        """矩形内にクリップした乱歩。ゾーン内で位置・姿勢が少しずつ変わる様子を模擬する。"""
        if self._walk_pos is None:
            self._walk_pos = np.array(center, dtype=float)
        else:
            self._walk_pos = self._walk_pos + self._rng.normal(0, step_std, 2)
        x0, y0, x1, y1 = rect
        self._walk_pos[0] = np.clip(self._walk_pos[0], x0, x1)
        self._walk_pos[1] = np.clip(self._walk_pos[1], y0, y1)
        return float(self._walk_pos[0]), float(self._walk_pos[1])

    def _tour_position(self, elapsed: float) -> tuple[float, float]:
        """ゾーンをID順に巡回し、境界をまたいで移動するシナリオ。

        各ゾーンの滞在時間(period)のうち最初の30%を前のゾーン中心からの
        直線移動（境界を横切る区間）、残りをゾーン内乱歩にあてる。
        """
        n = len(self._tour_zones)
        period = self._args.period
        t = elapsed % (period * n)
        idx = int(t // period)
        t_in = t % period
        cur = self._tour_zones[idx]
        prev = self._tour_zones[idx - 1]  # idx=0のときは巡回の最後のゾーンに戻る

        transit_s = period * 0.3
        if t_in < transit_s:
            alpha = t_in / transit_s
            x = prev.center[0] + alpha * (cur.center[0] - prev.center[0])
            y = prev.center[1] + alpha * (cur.center[1] - prev.center[1])
            self._walk_pos = np.array([x, y], dtype=float)  # 乱歩の基準点も移動先へ追従させる
            return (x, y)
        return self._walk_within(cur.center, cur.rect)

    def position_at(self, elapsed: float) -> tuple[float, float] | None:
        args = self._args
        if args.empty:
            return None
        if args.pos is not None:
            return args.pos
        if args.zone is not None:
            zone = self._zone_config.by_id(args.zone)
            if zone.rect is None:  # emptyなど範囲を持たないゾーン
                return None
            return self._walk_within(zone.center, zone.rect)
        if args.traj == "circle":
            w, h = self._zone_config.tub_size
            cx, cy = w / 2, h / 2
            r = 0.3 * min(w, h)
            angle = 2 * math.pi * elapsed / args.period
            return (cx + r * math.cos(angle), cy + r * math.sin(angle))
        return self._tour_position(elapsed)


def main() -> None:
    parser = argparse.ArgumentParser(description="virtual CSI sensors for pipeline testing")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--pos", type=str, default=None, help="静止位置 x,y（例: 0.3,0.5）")
    mode.add_argument("--empty", action="store_true", help="人がいない状態（ベースライン/ゾーン0用）")
    mode.add_argument("--zone", type=int, default=None, help="指定ゾーン内をランダムに少し動く人を模擬（zones.json参照）")
    mode.add_argument("--traj", choices=["circle", "tour"], default=None,
                       help="移動軌道を模擬（tour=ゾーンを順番に巡回、境界をまたぐ動き）")
    parser.add_argument("--zones", default=DEFAULT_ZONES_PATH, help="ゾーン定義ファイル")
    parser.add_argument("--sensors", default=None,
                        help="仮想センサーのID（例: 1,3）。省略時はzones.jsonのenabledに従う")
    parser.add_argument("--period", type=float, default=15.0,
                        help="circle: 1周の秒数 / tour: ゾーンごとの滞在秒数")
    parser.add_argument("--rate", type=float, default=100.0, help="センサーごとの送信レート(Hz)")
    parser.add_argument("--duration", type=float, default=0, help="送信秒数（0=無限）")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=3334)
    parser.add_argument("--noise", type=float, default=0.03, help="乗算ノイズの標準偏差")
    args = parser.parse_args()

    if args.pos is not None:
        x_str, y_str = args.pos.split(",")
        args.pos = (float(x_str), float(y_str))

    zone_config = load_zones(args.zones)
    if args.zone is not None:
        try:
            zone_config.by_id(args.zone)
        except KeyError:
            raise SystemExit(f"zone {args.zone} is not defined in {args.zones} (valid ids: {zone_config.zone_ids()})")

    all_positions = zone_config.sensor_positions()
    sensor_positions = {sid: all_positions[sid] for sid in zone_config.resolve_sensor_ids(args.sensors)}

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    rng = np.random.default_rng()
    patterns = {sid: base_pattern(sid) for sid in sensor_positions}
    seqs = dict.fromkeys(sensor_positions, 0)
    person_sim = PersonSimulator(args, zone_config, rng)

    interval = 1.0 / args.rate
    start = time.monotonic()
    next_tick = start
    last_print = start

    print(f"simulating {len(sensor_positions)} sensors -> {args.host}:{args.port} at {args.rate:.0f}Hz "
          f"(tub={zone_config.tub_size[0]:.2f}x{zone_config.tub_size[1]:.2f}m)")
    try:
        while True:
            now = time.monotonic()
            elapsed = now - start
            if args.duration and elapsed >= args.duration:
                break

            person = person_sim.position_at(elapsed)
            for sid, spos in sensor_positions.items():
                amp = patterns[sid] * person_gain(spos, person, zone_config.tub_size)
                amp = amp * rng.normal(1.0, args.noise, N_SUBCARRIERS)
                amp = np.clip(amp, 1.0, 100.0)
                sock.sendto(make_packet(sid, seqs[sid], amp, rng), (args.host, args.port))
                seqs[sid] += 1

            if now - last_print >= 2.0:
                pos_str = "empty" if person is None else f"({person[0]:.3f}, {person[1]:.3f})"
                print(f"t={elapsed:6.1f}s person at {pos_str}")
                last_print = now

            next_tick += interval
            sleep_for = next_tick - time.monotonic()
            if sleep_for > 0:
                time.sleep(sleep_for)
    except KeyboardInterrupt:
        pass
    print("done")


if __name__ == "__main__":
    main()
