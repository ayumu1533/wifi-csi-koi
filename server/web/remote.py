"""全自動収集（collection.plan）をスマホから操作するリモコン。

進み具合の表示と、一時停止・再開・今のをやり直す・直前をやり直す・スキップ・終了ができる。
収集側は Control.poll() でコマンドを受け取り、Control.update() で表示内容を渡す。
"""
from __future__ import annotations

import json
import queue
import threading
import time
from http.server import ThreadingHTTPServer

from .server import start_server


class Control:
    """リモコンからのコマンドと、リモコンに見せる状態をスレッド間で受け渡す。"""

    COMMANDS = {"pause", "resume", "redo", "redo_prev", "skip", "stop"}

    def __init__(self) -> None:
        self._commands: queue.Queue[str] = queue.Queue()
        self._lock = threading.Lock()
        self._status: dict = {"phase": "starting", "history": []}

    def push(self, cmd: str) -> bool:
        if cmd not in self.COMMANDS:
            return False
        self._commands.put(cmd)
        return True

    def poll(self) -> str | None:
        try:
            return self._commands.get_nowait()
        except queue.Empty:
            return None

    def update(self, **fields) -> None:
        with self._lock:
            self._status.update(fields)

    def add_history(self, text: str) -> None:
        with self._lock:
            self._status["history"] = ([f"{time.strftime('%H:%M:%S')} {text}"] + self._status["history"])[:12]

    def snapshot(self) -> dict:
        with self._lock:
            return json.loads(json.dumps(self._status))


REMOTE_HTML = """<!doctype html>
<html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>CSI収集リモコン</title>
<style>
:root { --bg:#f6f7f9; --card:#fff; --fg:#1d2330; --muted:#667085; --accent:#2563eb; --rec:#dc2626;
        --warn:#d97706; --ok:#16a34a; --border:#d0d5dd; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#111418; --card:#1b2027; --fg:#e8eaed; --muted:#98a2b3; --accent:#60a5fa; --rec:#f87171;
          --warn:#fbbf24; --ok:#4ade80; --border:#344054; }
}
* { box-sizing:border-box; }
body { margin:0; padding:16px; background:var(--bg); color:var(--fg);
       font:16px/1.5 system-ui, -apple-system, "Hiragino Sans", "Yu Gothic UI", sans-serif; }
.card { background:var(--card); border:1px solid var(--border); border-radius:12px; padding:16px; margin-bottom:12px; }
.phase { font-size:15px; font-weight:700; color:var(--muted); }
.phase.recording { color:var(--rec); } .phase.paused { color:var(--warn); } .phase.done { color:var(--ok); }
.label { font-size:22px; font-weight:700; margin:4px 0; }
.count { font-size:56px; font-weight:800; font-variant-numeric:tabular-nums; line-height:1.1; }
.bar { height:10px; background:var(--border); border-radius:5px; overflow:hidden; margin:8px 0 4px; }
.bar div { height:100%; background:var(--accent); width:0; transition:width .5s; }
.muted { color:var(--muted); font-size:14px; }
.grid { display:grid; grid-template-columns:1fr 1fr; gap:10px; }
button { font:inherit; font-weight:700; padding:16px 8px; border-radius:10px; border:1px solid var(--border);
         background:var(--card); color:var(--fg); width:100%; }
button.primary { background:var(--accent); border-color:var(--accent); color:#fff; }
button.danger { color:var(--rec); }
ul { margin:0; padding-left:18px; font-size:14px; }
.err { color:var(--rec); }
</style></head>
<body>
<div class="card">
  <div id="phase" class="phase">接続中…</div>
  <div id="label" class="label">-</div>
  <div id="count" class="count"></div>
  <div class="bar"><div id="bar"></div></div>
  <div id="progress" class="muted"></div>
  <div id="next" class="muted"></div>
</div>
<div class="grid">
  <button id="pause" class="primary" onclick="togglePause()">一時停止</button>
  <button onclick="send('redo')">今のをやり直す</button>
  <button onclick="if (confirm('直前に記録した回を消して、もう一度取りますか？')) send('redo_prev')">直前をやり直す</button>
  <button onclick="send('skip')">スキップ</button>
</div>
<div class="card" style="margin-top:12px">
  <div class="muted">最近の結果</div>
  <ul id="history"></ul>
  <div id="result" style="white-space:pre-wrap;margin-top:8px"></div>
</div>
<button class="danger" onclick="if (confirm('収集を終了しますか？（記録済みの回は残ります）')) send('stop')">終了</button>
<script>
const PHASES = { starting:'準備中', announce:'案内中', countdown:'移動してください', recording:'● 記録中',
  check:'確認中', paused:'一時停止中', training:'学習中', done:'完了', stopped:'終了しました',
  ready:'待機中（開始ボタンで始まります）' };
let paused = false, ready = false;
function send(cmd) { fetch('/cmd', { method:'POST', body:cmd }).then(refresh); }
function togglePause() { send(paused || ready ? 'resume' : 'pause'); }
function refresh() {
  fetch('/status').then(r => r.json()).then(s => {
    paused = s.phase === 'paused';
    ready = s.phase === 'ready';
    const ph = document.getElementById('phase');
    ph.textContent = PHASES[s.phase] || s.phase;
    ph.className = 'phase ' + s.phase;
    document.getElementById('label').textContent = s.label || '';
    document.getElementById('count').textContent = s.remaining != null ? s.remaining + '秒' : '';
    const pct = s.total ? Math.round(100 * s.index / s.total) : 0;
    document.getElementById('bar').style.width = pct + '%';
    document.getElementById('progress').textContent = s.total
      ? `${s.index + 1} / ${s.total} 回目（残り約${s.eta_min}分）` : '';
    document.getElementById('next').textContent = s.next ? '次: ' + s.next : '';
    document.getElementById('pause').textContent = ready ? '開始' : paused ? '再開' : '一時停止';
    document.getElementById('history').innerHTML =
      (s.history || []).map(h => `<li class="${h.includes('失敗') ? 'err' : ''}">${h}</li>`).join('');
    document.getElementById('result').textContent = s.result || '';
  }).catch(() => { document.getElementById('phase').textContent = 'PCに接続できません'; });
}
setInterval(refresh, 1000); refresh();
</script>
</body></html>
"""


def start_remote(control: Control, port: int) -> ThreadingHTTPServer:
    return start_server(port, REMOTE_HTML, control.snapshot, control.push, thread_name="remote")
