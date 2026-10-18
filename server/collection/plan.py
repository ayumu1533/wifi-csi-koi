"""音声ガイド付きの全自動収集。1回起動すれば、PCの前に戻らずに収集が終わる。

使い方（server/ で実行）:
    python -m collection.plan --serial COM3
    python -m collection.plan --serial COM3 --rounds 3 --zone-duration 20
    python -m collection.plan --serial COM3 --start-from 23 --append   # 途中から再開

収集の順番（--rounds 5 の場合、全52回・約45分）:
    基準(empty) → [empty → ゾーン9か所] × 5周 → empty
  - 基準の1回は data/baseline.jsonl（特徴量の基準専用）、それ以外は data/calib.jsonl に追記する。
  - 周ごとに姿勢を変える（--postures）。ゾーンはジグザグ順に回り、周ごとに逆順にする。
  - 各レコードに "round"（周回番号）を付ける。学習の --split round と学習曲線で使う。

各回の流れ（A1）:
    読み上げ「ゾーン5、真ん中の中央。座ってください」→ 移動の待ち時間（最後の3秒はピッ）
    → 高い長音で記録開始 → 記録 → ピピッで記録終了
受信の確認（A2）:
    記録後にTXごとの受信レートを調べ、--min-hz 未満のTXがあれば読み上げて取り直す。
    2回続けて失敗したら一時停止する（リモコンが無効なら30秒待って再試行）。
スマホのリモコン（A3）:
    起動時に表示されるURL（http://<PCのIP>:8765/）をスマホで開く。PCとスマホは同じWiFiにつなぐ。
    進み具合の表示と、一時停止・再開・今のをやり直す・直前をやり直す・スキップ・終了ができる。
    初回はWindowsのファイアウォールの確認が出るので「プライベートネットワーク」を許可する。
学習:
    既定では学習しない（収集が終わったら、手動で学習するコマンドを表示する）。
    --train を付けると、全部終わったあとに training.train（et と rf）と training.learning_curve を
    実行し、結果を読み上げる（A4）。ログは data/train_et.log, data/train_rf.log, data/learning_curve.txt。
    モデルは data/model.pkl（et）と data/model_rf.pkl。

Ctrl+C で止めても、それまでに記録が終わった回はファイルに残る（記録中の回は捨てる）。

ファイル分け: 読み上げ・ビープは collection/voice.py、受信の記録は collection/recording.py、
リモコンの画面は web/remote.py。
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import math
import os
import pickle
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import date

from common.jsonl import append_jsonl
from common.system import lan_ip, prevent_sleep
from config.zones import DEFAULT_ZONES_PATH, ZoneConfig, load_zones
from csi_receiver.source import add_source_args, open_source
from web.remote import Control, start_remote
from .recording import Recorder, remove_session
from .voice import Beeper, Speaker

logger = logging.getLogger("collect_plan")

DEFAULT_POSTURES = [
    "ゾーンの真ん中に立ってください",
    "ゾーンの真ん中に座ってください",
    "ゾーンの中で位置をずらして立ってください",
    "ゾーンの中で位置をずらして座ってください",
    "向きを変えて立ってください",
]
ROW_NAMES = {"front": "手前", "middle": "真ん中", "back": "奥"}
COL_NAMES = {"left": "左", "center": "中央", "right": "右"}
ANNOUNCE_SECONDS = 5  # 読み上げにかかるおおよその秒数（残り時間の見積もり用）


# ---------------------------------------------------------------- 収集計画

@dataclass
class Step:
    kind: str           # "baseline" | "empty" | "zone"
    zone_id: int
    zone_name: str
    round: int
    duration: int
    delay: int
    posture: str = ""

    def label(self) -> str:
        if self.kind == "baseline":
            return "基準（誰もいない）"
        if self.kind == "empty":
            return f"{self.round}周目 誰もいない"
        return f"{self.round}周目 ゾーン{self.zone_id} {spoken_zone_name(self.zone_name)}・{self.posture}"


def spoken_zone_name(name: str) -> str:
    """"middle_center" → "真ん中の中央"。形式が違う名前はそのまま返す。"""
    row, _, col = name.partition("_")
    if row in ROW_NAMES and col in COL_NAMES:
        return f"{ROW_NAMES[row]}の{COL_NAMES[col]}"
    return name


def snake_order(zone_config: ZoneConfig) -> list[int]:
    """ゾーンを手前の列から順に、列ごとに左右を折り返す順（移動距離が短い）。"""
    zones = [z for z in zone_config.zones if z.center is not None]
    rows: dict[float, list] = {}
    for z in zones:
        rows.setdefault(round(z.center[1], 3), []).append(z)
    order = []
    for i, y in enumerate(sorted(rows)):
        row = sorted(rows[y], key=lambda z: z.center[0], reverse=(i % 2 == 1))
        order.extend(z.id for z in row)
    return order


def build_plan(zone_config: ZoneConfig, rounds: int, postures: list[str],
               zone_duration: int, empty_duration: int, zone_delay: int, empty_delay: int) -> list[Step]:
    empty_name = zone_config.by_id(0).name
    steps = [Step("baseline", 0, empty_name, 0, empty_duration, empty_delay)]
    order = snake_order(zone_config)
    for r in range(1, rounds + 1):
        steps.append(Step("empty", 0, empty_name, r, empty_duration, empty_delay))
        posture = postures[(r - 1) % len(postures)]
        for zid in (order if r % 2 == 1 else list(reversed(order))):
            steps.append(Step("zone", zid, zone_config.by_id(zid).name, r, zone_duration, zone_delay, posture))
    steps.append(Step("empty", 0, empty_name, rounds, empty_duration, empty_delay))
    return steps


def step_seconds(step: Step) -> float:
    return ANNOUNCE_SECONDS + step.delay + step.duration + 1


# ---------------------------------------------------------------- 実行

class Runner:
    def __init__(self, args, steps: list[Step], sensor_ids: list[int], recorder: Recorder,
                 control: Control, speaker: Speaker, beeper: Beeper, remote_enabled: bool) -> None:
        self.args = args
        self.steps = steps
        self.sensor_ids = sensor_ids
        self.recorder = recorder
        self.control = control
        self.speaker = speaker
        self.beeper = beeper
        self.remote_enabled = remote_enabled
        self.i = args.start_from
        self.paused = False
        self.fail_streak = 0
        self.committed: list[tuple[int, str, str]] = []  # (ステップ番号, セッションID, ファイル)
        self.baseline_path = os.path.join(args.out_dir, "baseline.jsonl")
        self.calib_path = os.path.join(args.out_dir, "calib.jsonl")

    async def say(self, text: str) -> None:
        await asyncio.to_thread(self.speaker.say, text)

    async def beep(self, kind: str) -> None:
        await asyncio.to_thread(getattr(self.beeper, kind))

    def update_status(self, phase: str, remaining: int | None = None) -> None:
        nxt = self.steps[self.i + 1].label() if self.i + 1 < len(self.steps) else ""
        eta = sum(step_seconds(s) for s in self.steps[self.i:])
        self.control.update(
            phase=phase, index=min(self.i, len(self.steps) - 1), total=len(self.steps),
            label=self.steps[self.i].label() if self.i < len(self.steps) else "",
            next=nxt, remaining=remaining, eta_min=math.ceil(eta / 60),
        )

    async def wait(self, seconds: float, phase: str, tick_last: int = 0) -> str | None:
        """secondsだけ待つ。途中でリモコンのコマンドが来たらそれを返す。最後のtick_last秒はピッと鳴らす。"""
        end = time.monotonic() + seconds
        shown = None
        while True:
            cmd = self.control.poll()
            if cmd is not None and cmd != "resume":
                return cmd
            remaining = end - time.monotonic()
            if remaining <= 0:
                return None
            sec = math.ceil(remaining)
            if sec != shown:
                shown = sec
                self.update_status(phase, sec)
                if sec <= tick_last:
                    await self.beep("tick")
            await asyncio.sleep(min(0.1, remaining))

    def announcement(self, step: Step) -> str:
        left = len(self.steps) - self.i
        if step.kind == "baseline":
            body = "基準の記録です。領域の外に出て、動かないでください。"
        elif step.kind == "empty":
            body = "誰もいない状態を記録します。領域の外に出て、動かないでください。"
        else:
            body = f"ゾーン{step.zone_id}、{spoken_zone_name(step.zone_name)}。{step.posture}。"
        return f"あと{left}回。{body}"

    async def run_step(self, step: Step) -> tuple[str, object]:
        """1回分を実行する。("ok", (session, records)) / ("failed", 理由) / ("interrupted", コマンド)"""
        self.update_status("announce")
        await self.say(self.announcement(step))
        cmd = self.control.poll()
        if cmd is not None and cmd != "resume":
            return "interrupted", cmd

        cmd = await self.wait(step.delay, "countdown", tick_last=3)
        if cmd is not None:
            return "interrupted", cmd

        session = uuid.uuid4().hex[:12]
        await self.beep("start")
        self.recorder.start({
            "session": session, "date": date.today().isoformat(), "zone": step.zone_id,
            "zone_name": step.zone_name, "round": step.round, "posture": step.posture,
        })
        started = time.monotonic()
        cmd = await self.wait(step.duration, "recording")
        records = self.recorder.stop()
        elapsed = time.monotonic() - started
        if cmd is not None:
            await self.beep("error")
            return "interrupted", cmd
        await self.beep("end")

        self.update_status("check")
        rates = {sid: self.recorder.counts.get(sid, 0) / elapsed for sid in self.sensor_ids}
        self.control.update(rates={str(k): round(v) for k, v in rates.items()})
        low = [sid for sid, hz in rates.items() if hz < self.args.min_hz]
        rate_text = " ".join(f"TX{sid}:{hz:.0f}Hz" for sid, hz in rates.items())
        if low:
            return "failed", (low, rate_text)
        return "ok", (session, records, rate_text)

    def commit(self, step: Step, session: str, records: list[dict]) -> None:
        path = self.baseline_path if step.kind == "baseline" else self.calib_path
        append_jsonl(path, records)
        self.committed.append((self.i, session, path))

    async def undo_last(self) -> None:
        if not self.committed:
            await self.say("取り消せる記録がありません。")
            return
        idx, session, path = self.committed.pop()
        removed = await asyncio.to_thread(remove_session, path, session)
        logger.info("removed session %s (%d records) from %s", session, removed, path)
        self.control.add_history(f"取り消し: {self.steps[idx].label()}")
        self.i = idx
        await self.say("直前の記録を消しました。もう一度取ります。")

    async def handle(self, cmd: str) -> bool:
        """リモコンのコマンドを処理する。終了ならTrue。"""
        if cmd == "stop":
            return True
        if cmd == "pause":
            self.paused = True
        elif cmd == "skip":
            self.control.add_history(f"スキップ: {self.steps[self.i].label()}")
            self.i += 1
        elif cmd == "redo":
            await self.say("もう一度取ります。")
        elif cmd == "redo_prev":
            await self.undo_last()
        return False

    async def wait_while_paused(self) -> bool:
        """一時停止中。再開でFalse、終了でTrueを返す。"""
        self.update_status("paused")
        await self.say("一時停止しました。再開ボタンを押すと、続きから始めます。")
        while True:
            cmd = self.control.poll()
            if cmd == "resume":
                self.paused = False
                await self.say("再開します。")
                return False
            if cmd == "stop":
                return True
            if cmd in ("skip", "redo_prev"):
                await self.handle(cmd)
                if self.i >= len(self.steps):
                    return False
                self.update_status("paused")
            await asyncio.sleep(0.2)

    async def wait_for_start(self) -> bool:
        """リモコンの「開始」を待つ。開始でFalse、終了でTrueを返す。"""
        self.update_status("ready")
        await self.say("準備ができました。スマホの開始ボタンを押してください。")
        while True:
            cmd = self.control.poll()
            if cmd == "resume":
                return False
            if cmd == "stop":
                return True
            await asyncio.sleep(0.2)

    async def run(self) -> bool:
        """全ステップを実行する。最後まで終わればTrue、途中で終了したらFalse。"""
        if self.remote_enabled and not self.args.no_wait and await self.wait_for_start():
            return False
        total_min = math.ceil(sum(step_seconds(s) for s in self.steps[self.i:]) / 60)
        await self.say(f"収集を始めます。全部で{len(self.steps) - self.i}回、約{total_min}分です。")
        while self.i < len(self.steps):
            if self.paused:
                if await self.wait_while_paused():
                    return False
                continue
            step = self.steps[self.i]
            logger.info("step %d/%d: %s", self.i, len(self.steps), step.label())
            outcome, payload = await self.run_step(step)

            if outcome == "interrupted":
                if await self.handle(payload):
                    return False
                continue

            if outcome == "ok":
                session, records, rate_text = payload
                self.commit(step, session, records)
                self.control.add_history(f"OK: {step.label()} ({rate_text})")
                logger.info("  ok: %d records (%s)", len(records), rate_text)
                self.i += 1
                self.fail_streak = 0
                continue

            low, rate_text = payload
            self.fail_streak += 1
            self.control.add_history(f"失敗: {step.label()} ({rate_text})")
            logger.warning("  failed: low rate from TX %s (%s)", low, rate_text)
            await self.beep("error")
            tx_text = "と".join(f"TX{sid}" for sid in low)
            if self.fail_streak < 2:
                await self.say(f"{tx_text}の受信が少ないため、もう一度取ります。")
            elif self.remote_enabled:
                await self.say(f"{tx_text}の受信が続けて少ないです。電源と位置を確認してください。")
                self.paused = True
            else:
                await self.say(f"{tx_text}の受信が続けて少ないです。30秒後にもう一度試します。")
                await asyncio.sleep(30)
        return True


def run_training(args, control: Control, speaker: Speaker) -> None:
    """A4: 収集が終わったら学習・評価・学習曲線を実行し、結果を読み上げる。"""
    control.update(phase="training", label="学習中", remaining=None, next="")
    speaker.say("収集が終わりました。学習を始めます。数分かかります。")
    calib = os.path.join(args.out_dir, "calib.jsonl")
    baseline = os.path.join(args.out_dir, "baseline.jsonl")
    common = ["--data", calib, "--baseline", baseline, "--zones", args.zones]
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}  # ログをUTF-8で書かせる（Windowsの既定はcp932）
    summaries = []
    results = {}
    for algo, name in (("et", "ExtraTrees"), ("rf", "ランダムフォレスト")):
        model = os.path.join(args.out_dir, "model.pkl" if algo == "et" else f"model_{algo}.pkl")
        log_path = os.path.join(args.out_dir, f"train_{algo}.log")
        cmd = [sys.executable, "-m", "training.train", *common, "--algo", algo,
               "--split", "round", "--window", str(args.window), "--stride", "2", "--model-out", model]
        logger.info("training: %s", " ".join(cmd))
        with open(log_path, "w", encoding="utf-8") as log:
            proc = subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT, env=env)
        if proc.returncode != 0:
            summaries.append(f"{name}: 失敗（{log_path}）")
            continue
        with open(model, "rb") as f:
            metrics = pickle.load(f)["metrics"]
        if not metrics.get("evaluated"):
            summaries.append(f"{name}: 評価できませんでした（周回が足りません）")
            continue
        results[name] = metrics
        empty = metrics["per_zone_recall"].get(0, float("nan"))
        summaries.append(f"{name}: 正解率 {metrics['accuracy']:.3f} / macro-F1 {metrics['macro_f1']:.3f} "
                         f"/ empty再現率 {empty:.3f}（{model}）")

    lc_path = os.path.join(args.out_dir, "learning_curve.txt")
    cmd = [sys.executable, "-m", "training.learning_curve", *common, "--windows", f"0,{args.window}"]
    logger.info("learning curve: %s", " ".join(cmd))
    with open(lc_path, "w", encoding="utf-8") as out:
        subprocess.run(cmd, stdout=out, stderr=subprocess.DEVNULL, env=env)
    summaries.append(f"学習曲線: {lc_path}")

    text = "\n".join(summaries)
    print("\n" + text)
    with open(lc_path, encoding="utf-8") as f:
        print(f.read())
    control.update(phase="done", label="完了", result=text)

    spoken = ["学習が終わりました。"]
    for name, m in results.items():
        spoken.append(f"{name}の正解率は{round(m['accuracy'] * 100)}パーセント。")
    if results:
        best = max(results, key=lambda n: results[n]["macro_f1"])
        spoken.append(f"{best}の方が良い結果です。")
    spoken.append("詳しい結果は画面で確認してください。")
    speaker.say("".join(spoken))


async def amain(args) -> None:
    zone_config = load_zones(args.zones)
    sensor_ids = zone_config.resolve_sensor_ids(args.sensors)
    postures = [p for p in args.postures.split("|") if p.strip()] if args.postures else DEFAULT_POSTURES
    steps = build_plan(zone_config, args.rounds, postures, args.zone_duration, args.empty_duration,
                       args.zone_delay, args.empty_delay)

    if args.list:
        for i, s in enumerate(steps):
            print(f"{i:3d}  {s.label()}  ({s.duration}秒)")
        print(f"合計 {len(steps)} 回、約{math.ceil(sum(step_seconds(s) for s in steps) / 60)}分")
        return

    os.makedirs(args.out_dir, exist_ok=True)
    existing = [p for p in (os.path.join(args.out_dir, "baseline.jsonl"), os.path.join(args.out_dir, "calib.jsonl"))
                if os.path.exists(p) and os.path.getsize(p) > 0]
    if existing and not args.append:
        raise SystemExit(f"{existing} にすでにデータがあります。別のデータと混ざらないよう、"
                         "退避してから実行してください（続きから追記する場合は --append）")
    if args.start_from > 0 and not args.append:
        raise SystemExit("--start-from を使うときは --append も付けてください")

    control = Control()
    remote = None
    if not args.no_remote:
        remote = start_remote(control, args.remote_port)
        print(f"\n  スマホのリモコン: http://{lan_ip()}:{args.remote_port}/  （PCと同じWiFiで開く）\n", flush=True)

    prevent_sleep()
    speaker = Speaker(not args.no_voice)
    beeper = Beeper(not args.no_beep)
    recorder = Recorder(sensor_ids)
    transport = await open_source(args, lambda: recorder)
    runner = Runner(args, steps, sensor_ids, recorder, control, speaker, beeper, remote is not None)
    finished = False
    try:
        finished = await runner.run()
    finally:
        transport.close()

    if finished and args.train:
        await asyncio.to_thread(run_training, args, control, speaker)
    elif finished:
        control.update(phase="done", label="完了")
        await runner.say("収集が終わりました。お疲れさまでした。")
        out = args.out_dir.rstrip("/\\")  # Git Bashに貼れるよう区切りは "/" にする
        common = f"--data {out}/calib.jsonl --baseline {out}/baseline.jsonl"
        print("\n  収集が終わりました。学習は次のコマンドで行えます:\n"
              f"    $PY -m training.train {common} --algo rf --split round --stride 2 "
              f"--window {args.window} --model-out {out}/model.pkl\n"
              f"    $PY -m training.learning_curve {common} --windows 0,{args.window}\n", flush=True)
    else:
        control.update(phase="stopped", label="終了")
        await runner.say(f"収集を終了しました。次は {runner.i} 番から再開できます。")
        print(f"\n  続きから再開: --start-from {runner.i} --append\n", flush=True)
    speaker.close()
    if remote is not None:
        remote.shutdown()


def main() -> None:
    parser = argparse.ArgumentParser(description="voice-guided automatic collection plan")
    add_source_args(parser)
    parser.add_argument("--zones", default=DEFAULT_ZONES_PATH, help="ゾーン定義ファイル")
    parser.add_argument("--sensors", default=None, help="使うTX ID（例: 1,2,3）。省略時はzones.jsonのenabled")
    parser.add_argument("--out-dir", default="data", help="baseline.jsonl / calib.jsonl / モデルの保存先")
    parser.add_argument("--rounds", type=int, default=5, help="全ゾーンを回る周回数")
    parser.add_argument("--postures", default=None,
                        help="周ごとの姿勢の案内を | 区切りで（周回数より少なければ繰り返す）")
    parser.add_argument("--zone-duration", type=int, default=20, help="ゾーン1回の記録秒数")
    parser.add_argument("--empty-duration", type=int, default=30, help="empty・基準1回の記録秒数")
    parser.add_argument("--zone-delay", type=int, default=8, help="ゾーンへ移動する待ち秒数")
    parser.add_argument("--empty-delay", type=int, default=12, help="領域の外へ出る待ち秒数")
    parser.add_argument("--min-hz", type=float, default=50.0, help="TXごとの受信レートの下限（未満なら取り直し）")
    parser.add_argument("--window", type=int, default=20, help="学習の窓特徴量のフレーム数")
    parser.add_argument("--start-from", type=int, default=0, help="このステップ番号から始める（再開用、--listで確認）")
    parser.add_argument("--append", action="store_true", help="既存の baseline/calib に追記する")
    parser.add_argument("--list", action="store_true", help="収集計画を表示するだけで終わる")
    parser.add_argument("--no-voice", action="store_true", help="読み上げをしない")
    parser.add_argument("--no-beep", action="store_true", help="ビープ音を鳴らさない")
    parser.add_argument("--no-remote", action="store_true", help="スマホのリモコンを使わない")
    parser.add_argument("--no-wait", action="store_true",
                        help="スマホの「開始」を待たずにすぐ始める（--no-remote のときは常にすぐ始まる）")
    parser.add_argument("--remote-port", type=int, default=8765, help="リモコンのポート")
    parser.add_argument("--train", action="store_true",
                        help="収集後に学習・学習曲線まで自動で実行する（既定は収集だけで、学習は手動）")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    try:
        asyncio.run(amain(args))
    except KeyboardInterrupt:
        print("\n中断しました。記録が終わった回はファイルに残っています（--list で番号を確認し、"
              "--start-from N --append で再開）")


if __name__ == "__main__":
    main()
