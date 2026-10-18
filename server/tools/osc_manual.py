"""手動でゾーンを指定してOSCを送るテスト用ツール（TouchDesignerの鯉の動作確認用）。

localization.estimate と同じ形式・同じ送り先でOSCを送るので、TouchDesigner側は何も変えずに
「手動テスト」と「本番のリアルタイム推定」を切り替えられる（どちらか一方だけを起動する）。

送るOSC（docs/protocol.md と同じ）:
    /koi/zone_probs [p0, p1, ...]  約20Hz（--rate）。zones.json のID昇順
    /koi/zone       [zone_id]      ゾーンが変わったとき＋1秒ごと
    /koi/position   [x, y]         /koi/zone と同じタイミング（emptyのときは送らない）
    /koi/mode       ['A'|'B'|'C'|'N']  「mode X」と入力したときだけ（応答設計の切り替え）
確率は estimate と同じく EMA（--ema）でなめらかに目標へ近づける。

使い方（server/ ディレクトリで）:
    .venv/Scripts/python.exe -m tools.osc_manual                 # 127.0.0.1:9000 へ送る
    .venv/Scripts/python.exe -m tools.osc_manual --osc 127.0.0.1:9000

起動後に入力するコマンド:
    5          ゾーン5にいる（確率1）
    0          不在（empty）
    5 6        ゾーン5と6の境界（半々）。3つ以上も可
    5:0.7 6:0.3  確率を直接指定（合計は自動で1にそろえる）
    tour [秒]  ゾーン1→9を順に巡回（既定10秒ごと）。何か入力すると止まる
    mode A     応答設計を切り替える（A=寄る B=分散 C=周回 N=反応なし）。/koi/mode を1回送る
    list       ゾーン一覧
    q          終了
"""
from __future__ import annotations

import argparse
import threading
import time

from pythonosc.udp_client import SimpleUDPClient

from config.zones import DEFAULT_ZONES_PATH, load_zones


def parse_command(text: str, ids: list[int]) -> list[float] | None:
    """入力文字列 → 目標の確率ベクトル（IDの昇順）。解釈できなければNone。"""
    weights: dict[int, float] = {}
    for tok in text.split():
        if ":" in tok:
            zid_s, w_s = tok.split(":", 1)
            zid, w = int(zid_s), float(w_s)
        else:
            zid, w = int(tok), 1.0
        if zid not in ids:
            return None
        weights[zid] = weights.get(zid, 0.0) + w
    total = sum(weights.values())
    if total <= 0:
        return None
    return [weights.get(i, 0.0) / total for i in ids]


def main() -> None:
    parser = argparse.ArgumentParser(description="手動でゾーンを指定してOSCを送る（TD動作確認用）")
    parser.add_argument("--osc", default="127.0.0.1:9000", help="送信先 host:port（estimateの --osc と同じにする）")
    parser.add_argument("--zones", default=DEFAULT_ZONES_PATH, help="ゾーン定義ファイル")
    parser.add_argument("--rate", type=float, default=20.0, help="/koi/zone_probs の送信頻度 [Hz]")
    parser.add_argument("--ema", type=float, default=0.3, help="確率の平滑化係数（estimateの --proba-ema と同じ意味）")
    parser.add_argument("--zone-report-interval", type=float, default=1.0, help="/koi/zone の定期送信間隔 [秒]")
    args = parser.parse_args()

    cfg = load_zones(args.zones)
    ids = cfg.zone_ids()
    host, port = args.osc.rsplit(":", 1)
    client = SimpleUDPClient(host, int(port))

    # 送信ループと入力で共有する状態
    state = {
        "target": [1.0 if i == 0 else 0.0 for i in ids],  # 目標の確率（最初は不在）
        "probs": [1.0 if i == 0 else 0.0 for i in ids],   # 実際に送る確率（EMAで目標へ近づく）
        "tour": None,                                       # 巡回中なら (開始時刻, 間隔[秒])
        "running": True,
    }
    lock = threading.Lock()

    def sender() -> None:
        # 一定間隔でOSCを送り続ける（estimate の送り方に合わせる）
        last_zone, last_zone_time = None, 0.0
        while state["running"]:
            with lock:
                if state["tour"] is not None:
                    start, period = state["tour"]
                    k = int((time.monotonic() - start) // period) % (len(ids) - 1)
                    state["target"] = [1.0 if i == ids[k + 1] else 0.0 for i in ids]
                # 確率を目標へなめらかに近づける
                a = args.ema
                state["probs"] = [(1 - a) * p + a * t for p, t in zip(state["probs"], state["target"])]
                probs = list(state["probs"])
            client.send_message("/koi/zone_probs", [float(p) for p in probs])

            zone = ids[max(range(len(ids)), key=lambda i: probs[i])]
            now = time.monotonic()
            if zone != last_zone or now - last_zone_time >= args.zone_report_interval:
                client.send_message("/koi/zone", int(zone))
                center = cfg.by_id(zone).center
                if center is not None:
                    client.send_message("/koi/position", [float(center[0]), float(center[1])])
                last_zone, last_zone_time = zone, now
            time.sleep(1.0 / args.rate)

    thread = threading.Thread(target=sender, daemon=True)
    thread.start()

    print(f"OSC送信中 → {args.osc}（Ctrl+C か q で終了）")
    print("ゾーン:", ", ".join(f"{z.id}={z.name}" for z in sorted(cfg.zones, key=lambda z: z.id)))
    print("例: 5（ゾーン5）/ 0（不在）/ 5 6（境界）/ 5:0.7 6:0.3 / tour 10 / mode B / list / q")
    try:
        while True:
            text = input("> ").strip()
            if not text:
                continue
            if text == "q":
                break
            if text == "list":
                for z in sorted(cfg.zones, key=lambda z: z.id):
                    print(f"  {z.id}: {z.name}  center={z.center}")
                continue
            if text.lower().startswith("mode"):
                # 応答設計の切り替え: TD側の osc_in_callbacks.py が受け取り、鯉の反応のしかたが変わる
                parts = text.split()
                m = parts[1].upper() if len(parts) > 1 else ""
                if m not in ("A", "B", "C", "N"):
                    print("mode A / mode B / mode C / mode N のどれか（A=寄る B=分散 C=周回 N=反応なし）")
                    continue
                client.send_message("/koi/mode", m)
                print(f"応答設計 → {m}")
                continue
            if text.startswith("tour"):
                parts = text.split()
                period = float(parts[1]) if len(parts) > 1 else 10.0
                with lock:
                    state["tour"] = (time.monotonic(), period)
                print(f"巡回開始（{period}秒ごと）。何か入力すると止まります")
                continue
            try:
                target = parse_command(text, ids)
            except ValueError:
                target = None
            if target is None:
                print("解釈できません。例: 5 / 0 / 5 6 / 5:0.7 6:0.3 / tour 10")
                continue
            with lock:
                state["tour"] = None
                state["target"] = target
            shown = ", ".join(f"{i}:{p:.2f}" for i, p in zip(ids, target) if p > 0)
            print(f"目標 → {shown}")
    except (KeyboardInterrupt, EOFError):
        pass
    finally:
        state["running"] = False
        thread.join(timeout=1.0)
        print("終了")


if __name__ == "__main__":
    main()
