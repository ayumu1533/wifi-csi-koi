"""学習済みモデルでリアルタイムのゾーン分類を行う。

使い方:
    python -m localization.estimate --model model.pkl
    python -m localization.estimate --model model.pkl --osc 127.0.0.1:9000 --out track.csv
    python -m localization.estimate --serial COM5 --model data/model.pkl --remote   # スマホで見る

処理:
  UDP受信 → フレーム同期(FrameAssembler) → 特徴量抽出(モデルに保存された種類・窓の長さ。features/)
  → モデルでゾーンの確率(predict_proba)を推論 → 確率ベクトルにEMA平滑化
  → 直近Nフレームの多数決（ヒステリシス）で確定ゾーンを決定（平滑化・多数決は smoothing.py）
  → コンソール表示（任意でOSC送信・CSV保存。OSCは osc_out.py、スマホ表示は web/monitor.py）

OSC送信時:
    /koi/zone       [zone_id: int]     確定ゾーン（変化時＋定期送信）
    /koi/zone_probs [p0, p1, ...]      平滑化後のゾーン確率（毎フレーム、zones.jsonのID昇順）
    /koi/position   [x: float, y: float]  確定ゾーンの中心座標（emptyのときは送らない）
    /koi/mode       [mode: str]        鯉の反応の切り替え（--remote のスマホ画面でボタンを押したときだけ）
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import time

import numpy as np

from common.system import lan_ip, prevent_sleep
from config.zones import ZoneConfig, zone_config_from_dict
from csi_receiver.frames import FrameAssembler
from csi_receiver.packet import InvalidPacketError, parse_packet
from csi_receiver.source import add_source_args, open_source
from features import input_shape, make_featurizer
from models.registry import load_bundle
from web.monitor import MonitorState, start_monitor
from .osc_out import OscOutput
from .smoothing import ProbaSmoother, ProbaWindowAverage, ZoneHysteresis

logger = logging.getLogger("estimate")

# 鯉の反応（応答設計）。スマホのモニターから /koi/mode で切り替える（docs/protocol.md）
MODES = {"A": "寄る", "B": "分散", "C": "周回", "N": "反応なし"}


class EstimateProtocol(asyncio.DatagramProtocol):
    def __init__(
        self,
        bundle: dict,
        zone_config: ZoneConfig,
        proba_smoother: ProbaSmoother | ProbaWindowAverage,
        hysteresis: ZoneHysteresis,
        osc: OscOutput | None,
        out_file,
        zone_report_interval: float,
        monitor: MonitorState | None = None,
    ) -> None:
        self._model = bundle["model"]  # models/base.py の共通の形（RandomForest でも CNN でも同じ使い方）
        self._classes: list[int] = self._model.classes
        self._sensor_ids = bundle["sensor_ids"]
        baseline = bundle.get("baseline")
        baseline = {sid: np.asarray(b) for sid, b in baseline.items()} if baseline else None
        self._featurizer = make_featurizer(bundle["input_kind"], self._sensor_ids, baseline,
                                           bundle["window_frames"])
        self._assembler = FrameAssembler(
            self._sensor_ids,
            interval_s=bundle["interval_s"],
            max_age_s=bundle["max_age_s"],
            expected_len=bundle["expected_len"],
        )
        self._zone_config = zone_config
        self._zone_ids_all = zone_config.zone_ids()
        # predict_probaの列順(classes)から、出力用の全ゾーン配列内の位置への対応表
        self._class_to_slot = [self._zone_ids_all.index(c) for c in self._classes]
        self._proba_smoother = proba_smoother
        self._hysteresis = hysteresis
        self._osc = osc
        self._out_file = out_file
        self._zone_report_interval = zone_report_interval
        self._last_sent_zone: int | None = None
        self._last_zone_send_time = 0.0
        self._last_frame_time = time.monotonic()
        self._start = time.monotonic()
        self._monitor = monitor
        self._monitor_zone: int | None = None
        self._packet_counts = {sid: 0 for sid in self._sensor_ids}  # モニター表示用の受信レート
        self._rate_window_start = time.monotonic()

    def _count_packet(self, sensor_id: int) -> None:
        if self._monitor is None or sensor_id not in self._packet_counts:
            return
        self._packet_counts[sensor_id] += 1
        now = time.monotonic()
        if now - self._rate_window_start >= 1.0:
            span = now - self._rate_window_start
            self._monitor.update(rates={str(sid): round(n / span) for sid, n in self._packet_counts.items()},
                                 stale=[f"TX{sid}" for sid, n in self._packet_counts.items() if n == 0])
            self._packet_counts = {sid: 0 for sid in self._sensor_ids}
            self._rate_window_start = now

    def _update_monitor(self, confirmed_zone: int, raw_zone: int, full_proba: np.ndarray) -> None:
        if self._monitor is None:
            return
        if confirmed_zone != self._monitor_zone:
            if self._monitor_zone is not None:
                self._monitor.zone_changed(
                    f"{self._monitor_zone} → {confirmed_zone} ({self._zone_name(confirmed_zone)})")
            self._monitor_zone = confirmed_zone
        self._monitor.update(
            state="running", updated=time.time(), zone=confirmed_zone, raw_zone=raw_zone,
            probs={str(zid): round(float(p), 3) for zid, p in zip(self._zone_ids_all, full_proba)},
        )

    def _expand_proba(self, proba: np.ndarray) -> np.ndarray:
        """訓練時に出現したクラスだけのproba配列を、zones.json全体のID順に展開する
        （未学習のゾーンは確率0として扱う）。"""
        full = np.zeros(len(self._zone_ids_all))
        for i, slot in enumerate(self._class_to_slot):
            full[slot] = proba[i]
        return full

    def _zone_name(self, zone_id: int) -> str:
        try:
            return self._zone_config.by_id(zone_id).name
        except KeyError:
            return "?"

    def datagram_received(self, data: bytes, addr) -> None:
        try:
            packet = parse_packet(data)
        except InvalidPacketError:
            return
        self._count_packet(packet.sensor_id)

        frame = self._assembler.add(packet.sensor_id, time.monotonic(), np.asarray(packet.amplitude))
        if frame is None:
            self._maybe_report_stall()
            return

        features = self._featurizer.push(frame)
        if features is None:  # 窓がたまるまで（起動直後の約1秒）
            self._last_frame_time = time.monotonic()
            return
        raw_proba = self._model.predict_proba(features[np.newaxis])[0]
        smoothed = self._proba_smoother.update(raw_proba)
        confirmed_zone, raw_zone = self._hysteresis.update(smoothed, self._classes)
        self._last_frame_time = time.monotonic()

        full_proba = self._expand_proba(smoothed)
        elapsed = time.monotonic() - self._start
        logger.info(
            "t=%7.2f  zone=%d (%s)  raw=%d (%s)  p=%s",
            elapsed, confirmed_zone, self._zone_name(confirmed_zone),
            raw_zone, self._zone_name(raw_zone),
            np.array2string(full_proba, precision=2, suppress_small=True),
        )
        self._update_monitor(confirmed_zone, raw_zone, full_proba)

        now = time.monotonic()
        zone_changed = confirmed_zone != self._last_sent_zone
        heartbeat_due = now - self._last_zone_send_time >= self._zone_report_interval

        if self._osc is not None:
            self._osc.send_probs(full_proba)
            if zone_changed or heartbeat_due:
                self._osc.send_zone(confirmed_zone)

        if zone_changed or heartbeat_due:
            self._last_sent_zone = confirmed_zone
            self._last_zone_send_time = now

        if self._out_file is not None:
            row = [f"{time.time():.3f}", str(confirmed_zone), str(raw_zone)]
            row += [f"{p:.4f}" for p in full_proba]
            self._out_file.write(",".join(row) + "\n")
            self._out_file.flush()  # プロセスがkillされても記録が残るように都度書き出す

    def _maybe_report_stall(self) -> None:
        now = time.monotonic()
        if now - self._last_frame_time > 2.0:
            freshness = self._assembler.freshness(now)
            stale = {sid: (f"{age:.1f}s" if age is not None else "never")
                     for sid, age in freshness.items()
                     if age is None or age > self._assembler.max_age_s}
            logger.warning("no frames for %.1fs, stale sensors: %s",
                           now - self._last_frame_time, stale)
            self._last_frame_time = now  # 警告の連投を防ぐ


async def run(args) -> None:
    bundle = load_bundle(args.model)
    model = bundle["model"]
    zone_config = zone_config_from_dict(bundle["zones"])
    metrics = bundle["metrics"]
    if metrics.get("evaluated", True):  # 旧bundle（evaluatedキー無し）は評価済み扱いにする
        eval_summary = f"eval accuracy={metrics['accuracy']:.3f} macro-F1={metrics['macro_f1']:.3f}"
    else:
        eval_summary = f"eval skipped ({metrics.get('reason', 'unknown')})"
    window_frames = bundle["window_frames"]
    logger.info(
        "model loaded: algo=%s (%s, input=%s) zones=%s sensors=%s subcarriers=%d/sensor window=%d frames, %s",
        bundle["algo"], model.family, bundle["input_kind"], zone_config.zone_ids(), bundle["sensor_ids"],
        bundle["expected_len"], window_frames, eval_summary,
    )

    # 1件ずつ推論するための設定（RandomForestは並列化を切る、PyTorchは1スレッドにする）
    model.prepare_realtime()

    # 初回のpredict_probaはライブラリ初期化で1-2秒かかることがあるため、先に空打ちして温めておく。
    # あわせて1回の推論時間を測り、フレーム周期（50ms）に間に合うかを表示する
    shape = input_shape(bundle["input_kind"], len(bundle["sensor_ids"]), bundle["expected_len"], window_frames)
    dummy = np.zeros((1, *shape), dtype=np.float32 if bundle["input_kind"] == "window" else np.float64)
    model.predict_proba(dummy)
    started = time.perf_counter()
    for _ in range(10):
        model.predict_proba(dummy)
    infer_ms = (time.perf_counter() - started) / 10 * 1000
    logger.info("inference: %.1f ms/frame (frame interval %.0f ms)", infer_ms, bundle["interval_s"] * 1000)
    if infer_ms > bundle["interval_s"] * 1000:
        logger.warning("推論がフレーム周期より遅いため、受信が詰まる可能性があります")

    osc = None
    if args.osc:
        osc = OscOutput(args.osc, zone_config)
        logger.info("sending OSC to %s (/koi/zone, /koi/zone_probs, /koi/position)", args.osc)

    out_file = None
    if args.out:
        write_header = not os.path.exists(args.out)
        out_file = open(args.out, "a", encoding="utf-8")
        if write_header:
            zone_cols = ",".join(f"p{zid}" for zid in zone_config.zone_ids())
            out_file.write(f"time,zone,raw_zone,{zone_cols}\n")

    if args.proba_window > 0:
        window_n = max(1, round(args.proba_window / bundle["interval_s"]))
        proba_smoother = ProbaWindowAverage(window_n)
        logger.info("zone probabilities: moving average over %.1fs (%d frames)", args.proba_window, window_n)
    else:
        proba_smoother = ProbaSmoother(args.proba_ema)
        logger.info("zone probabilities: EMA alpha=%.2f", args.proba_ema)
    hysteresis = ZoneHysteresis(args.zone_threshold, args.hysteresis_frames, args.hysteresis_ratio)

    monitor = None
    monitor_server = None
    if args.remote:
        monitor = MonitorState(zone_config, bundle["sensor_ids"],
                               f"モデル: {os.path.basename(args.model)} ({bundle['algo']}, {eval_summary})")
        monitor.update(mode_enabled=osc is not None, mode=None)

        def on_monitor_command(cmd: str) -> bool:
            # スマホのモードボタン（本文 "mode A" など）→ TouchDesignerへ /koi/mode を送る
            parts = cmd.split()
            if osc is None or len(parts) != 2 or parts[0] != "mode" or parts[1] not in MODES:
                return False
            osc.send_mode(parts[1])
            monitor.update(mode=parts[1])
            logger.info("koi mode -> %s (%s)", parts[1], MODES[parts[1]])
            return True

        monitor_server = start_monitor(monitor, args.remote_port, on_monitor_command)
        prevent_sleep()
        print(f"\n  スマホで見る: http://{lan_ip()}:{args.remote_port}/  （PCと同じWiFiで開く）\n", flush=True)

    transport = await open_source(
        args,
        lambda: EstimateProtocol(
            bundle, zone_config, proba_smoother, hysteresis,
            osc, out_file, args.zone_report_interval, monitor,
        ),
    )
    try:
        await asyncio.Future()
    finally:
        transport.close()
        if out_file is not None:
            out_file.close()
        if monitor_server is not None:
            monitor_server.shutdown()


def main() -> None:
    parser = argparse.ArgumentParser(description="real-time zone classification from CSI")
    parser.add_argument("--model", required=True, help="training.train で保存したモデル(.pkl)。rf / et / knn / mlp / cnn1d / cnn2d のどれでも同じ使い方")
    add_source_args(parser)
    parser.add_argument("--proba-window", type=float, default=3.0,
                        help="ゾーン確率を直近この秒数で単純平均する（既定3秒。0でEMA(--proba-ema)に戻す）")
    parser.add_argument("--proba-ema", type=float, default=0.3,
                        help="--proba-window 0 のときのゾーン確率へのEMA係数(0-1, 大=追従重視 小=滑らか重視)")
    parser.add_argument("--zone-threshold", type=float, default=0.0,
                        help="この確率未満のフレームは確定ゾーンの票に数えない(0-1。0=全フレームを数える)")
    parser.add_argument("--hysteresis-frames", type=int, default=20,
                        help="確定ゾーンの多数決に使う直近のフレーム数。"
                             "既定値は学習時デフォルト(interval=0.05s, 約20Hz)で約1秒に相当。"
                             "--intervalを変えて学習した場合は合わせて調整すること")
    parser.add_argument("--hysteresis-ratio", type=float, default=0.5,
                        help="直近 --hysteresis-frames のうち、同じゾーンがこの割合以上を占めたら確定ゾーンを切り替える(0-1)")
    parser.add_argument("--zone-report-interval", type=float, default=1.0,
                        help="/koi/zoneと/koi/positionの定期送信間隔（秒）。変化時は即送信する")
    parser.add_argument("--osc", default=None, help="OSC送信先 host:port（例: 127.0.0.1:9000）")
    parser.add_argument("--out", default=None,
                        help="推定結果を追記するCSV（time,zone,raw_zone,p0..pN）")
    parser.add_argument("--remote", action="store_true",
                        help="推定の様子をスマホのブラウザで見られるようにする（表示されるURLを開く）")
    parser.add_argument("--remote-port", type=int, default=8765,
                        help="--remote のポート（収集リモコンと同じ。ファイアウォールの許可も同じ）")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
