"""リアルタイム推定の様子をスマホのブラウザで見るための小さなWebサーバー。

localization.estimate --remote で起動する。表示するもの:
  - 確定ゾーン（大きく）と生のargmaxゾーン
  - 浴槽の見取り図（ゾーンごとの確率を色の濃さで表示、RX/TXの位置つき）
  - emptyの確率、TXごとの受信レート、直近のゾーン切替の履歴
  - 鯉の反応（モード A/B/C/N）の切り替えボタン（--osc を付けたときだけ。押すと /koi/mode を送る）
PCとスマホは同じWiFiにつなぐ（ファイアウォールの設定は docs/runbook.md §4.0 と同じ。ポートも同じ8765）。
"""
from __future__ import annotations

import threading
import time
from collections.abc import Callable
from http.server import ThreadingHTTPServer

from config.zones import ZoneConfig
from .server import start_server


class MonitorState:
    """推定スレッドが書き込み、HTTPスレッドが読み出す表示用の状態。"""

    def __init__(self, zone_config: ZoneConfig, sensor_ids: list[int], model_summary: str) -> None:
        self._lock = threading.Lock()
        self._history: list[str] = []
        self._layout = {
            "tub_size": list(zone_config.tub_size),
            "rx": list(zone_config.ap_pos or (0.0, 0.0)),
            "tx": {str(sid): list(pos) for sid, pos in zone_config.sensor_positions().items()
                   if sid in sensor_ids},
            "zones": [{"id": z.id, "name": z.name, "rect": list(z.rect) if z.rect else None}
                      for z in zone_config.zones],
        }
        self._status: dict = {"state": "waiting", "model": model_summary}

    def update(self, **fields) -> None:
        with self._lock:
            self._status.update(fields)

    def zone_changed(self, text: str) -> None:
        with self._lock:
            self._history = ([f"{time.strftime('%H:%M:%S')} {text}"] + self._history)[:10]

    def snapshot(self) -> dict:
        with self._lock:
            return {**self._status, "history": list(self._history), "layout": self._layout,
                    "server_time": time.time()}


MONITOR_HTML = """<!doctype html>
<html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>CSI推定モニター</title>
<style>
:root { --bg:#f6f7f9; --card:#fff; --fg:#1d2330; --muted:#667085; --accent:#2563eb; --warn:#d97706;
        --bad:#dc2626; --border:#d0d5dd; --cell:37,99,235; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#111418; --card:#1b2027; --fg:#e8eaed; --muted:#98a2b3; --accent:#60a5fa; --warn:#fbbf24;
          --bad:#f87171; --border:#344054; --cell:96,165,250; }
}
* { box-sizing:border-box; }
body { margin:0; padding:16px; background:var(--bg); color:var(--fg);
       font:16px/1.5 system-ui, -apple-system, "Hiragino Sans", "Yu Gothic UI", sans-serif; }
.card { background:var(--card); border:1px solid var(--border); border-radius:12px; padding:16px; margin-bottom:12px; }
.muted { color:var(--muted); font-size:14px; }
.zone { font-size:40px; font-weight:800; line-height:1.2; }
.zone.empty { color:var(--muted); }
.state.warn { color:var(--warn); font-weight:700; } .state.bad { color:var(--bad); font-weight:700; }
svg { width:100%; height:auto; display:block; }
svg text { font:bold 13px system-ui, sans-serif; fill:var(--fg); }
svg .pct { font-weight:normal; font-size:11px; fill:var(--muted); }
.row { display:flex; justify-content:space-between; gap:8px; }
.modes { display:grid; grid-template-columns:repeat(4, 1fr); gap:8px; margin-top:10px; }
.modes button { font:inherit; font-size:15px; padding:12px 4px; border-radius:10px; cursor:pointer;
                background:var(--bg); color:var(--fg); border:1px solid var(--border); }
.modes button b { display:block; font-size:20px; }
.modes button.on { background:var(--accent); color:#fff; border-color:var(--accent); }
.modes button:disabled { opacity:.5; }
ul { margin:0; padding-left:18px; font-size:14px; }
</style></head>
<body>
<div class="card">
  <div id="state" class="state muted">接続中…</div>
  <div id="zone" class="zone">-</div>
  <div id="raw" class="muted"></div>
</div>
<div class="card" id="modecard" hidden>
  <div class="row"><span>鯉の反応（モード）</span><span id="modenow" class="muted">-</span></div>
  <div class="modes">
    <button type="button" data-mode="A"><b>A</b>寄る</button>
    <button type="button" data-mode="B"><b>B</b>分散</button>
    <button type="button" data-mode="C"><b>C</b>周回</button>
    <button type="button" data-mode="N"><b>N</b>反応なし</button>
  </div>
  <div id="modemsg" class="muted"></div>
</div>
<div class="card">
  <svg id="map" viewBox="0 0 320 340"></svg>
  <div class="muted">色が濃いほど確率が高い。太枠が確定ゾーン、点線が生の推定。</div>
</div>
<div class="card">
  <div class="row"><span>誰もいない確率</span><b id="empty">-</b></div>
  <div class="row"><span>受信レート</span><span id="rates" class="muted">-</span></div>
</div>
<div class="card">
  <div class="muted">ゾーンの切り替わり</div>
  <ul id="history"></ul>
</div>
<div id="model" class="muted"></div>
<script>
const JA_ROW = { front:'手前', middle:'真ん中', back:'奥' };
const JA_COL = { left:'左', center:'中央', right:'右' };
function ja(name) {
  if (name === 'empty') return '誰もいない';
  const [r, c] = name.split('_');
  return (JA_ROW[r] && JA_COL[c]) ? JA_ROW[r] + 'の' + JA_COL[c] : name;
}
function zoneText(id, layout) {
  const z = layout.zones.find(z => z.id === id);
  return z ? (id === 0 ? ja(z.name) : `ゾーン${id} ${ja(z.name)}`) : '-';
}
function drawMap(s) {
  const L = s.layout, [W, H] = L.tub_size, pad = 20, size = 280;
  const sx = x => pad + x / W * size;
  const sy = y => pad + size - y / H * size;   // y=0（手前、RX側）を下にする
  let out = '';
  for (const z of L.zones) {
    if (!z.rect) continue;
    const [x0, y0, x1, y1] = z.rect;
    const p = s.probs ? s.probs[String(z.id)] || 0 : 0;
    const strong = z.id === s.zone, raw = z.id === s.raw_zone && !strong;
    out += `<rect x="${sx(x0)}" y="${sy(y1)}" width="${sx(x1) - sx(x0)}" height="${sy(y0) - sy(y1)}"
      style="fill:rgba(var(--cell),${(0.08 + 0.85 * p).toFixed(2)});stroke:var(--fg);
      stroke-width:${strong ? 4 : raw ? 2 : 1};${raw ? 'stroke-dasharray:6 4' : ''}"/>`;
    const cx = (sx(x0) + sx(x1)) / 2, cy = (sy(y0) + sy(y1)) / 2;
    out += `<text x="${cx}" y="${cy}" text-anchor="middle">${z.id}</text>`;
    out += `<text class="pct" x="${cx}" y="${cy + 16}" text-anchor="middle">${Math.round(p * 100)}%</text>`;
  }
  const mark = (x, y, label) => `<circle cx="${sx(x)}" cy="${sy(y)}" r="6" style="fill:var(--accent)"/>
    <text x="${sx(x) + (x > W / 2 ? -10 : 10)}" y="${sy(y) + (y > H / 2 ? -8 : 18)}"
      text-anchor="${x > W / 2 ? 'end' : 'start'}">${label}</text>`;
  out += mark(L.rx[0], L.rx[1], 'RX');
  for (const [id, pos] of Object.entries(L.tx)) out += mark(pos[0], pos[1], 'TX' + id);
  out += `<text class="pct" x="160" y="336" text-anchor="middle">↓ 手前（RX側）</text>`;
  document.getElementById('map').innerHTML = out;
}
function refresh() {
  fetch('/status').then(r => r.json()).then(s => {
    const st = document.getElementById('state');
    const age = s.updated ? s.server_time - s.updated : null;
    if (s.state === 'waiting') { st.textContent = '受信待ち（推定開始まで数秒）'; st.className = 'state warn'; }
    else if (age != null && age > 2) { st.textContent = `推定が止まっています（${Math.round(age)}秒）`; st.className = 'state bad'; }
    else { st.textContent = '推定中'; st.className = 'state muted'; }
    if (s.stale && s.stale.length) { st.textContent += ` / 受信なし: ${s.stale.join(', ')}`; st.className = 'state bad'; }
    const zEl = document.getElementById('zone');
    zEl.textContent = s.zone != null ? zoneText(s.zone, s.layout) : '-';
    zEl.className = 'zone' + (s.zone === 0 ? ' empty' : '');
    document.getElementById('raw').textContent = s.raw_zone != null
      ? `生の推定: ${zoneText(s.raw_zone, s.layout)}（確定は数秒遅れて切り替わる）` : '';
    drawMap(s);
    document.getElementById('empty').textContent = s.probs ? Math.round((s.probs['0'] || 0) * 100) + '%' : '-';
    document.getElementById('rates').textContent = s.rates
      ? Object.entries(s.rates).map(([k, v]) => `TX${k}: ${v}Hz`).join('  ') : '-';
    document.getElementById('history').innerHTML = (s.history || []).map(h => `<li>${h}</li>`).join('');
    document.getElementById('model').textContent = s.model || '';
    document.getElementById('modecard').hidden = !s.mode_enabled;
    document.getElementById('modenow').textContent = s.mode ? `最後に送った: ${s.mode}` : 'まだ送っていない';
    for (const b of document.querySelectorAll('.modes button')) b.classList.toggle('on', b.dataset.mode === s.mode);
  }).catch(() => {
    const st = document.getElementById('state');
    st.textContent = 'PCに接続できません'; st.className = 'state bad';
  });
}
for (const b of document.querySelectorAll('.modes button')) {
  b.addEventListener('click', () => {
    const msg = document.getElementById('modemsg');
    b.disabled = true;
    fetch('/cmd', { method: 'POST', body: 'mode ' + b.dataset.mode })
      .then(r => { msg.textContent = r.ok ? `モード ${b.dataset.mode} を送りました（exp_*.toe を開いているときは切り替わりません）`
                                         : '送れませんでした（推定を --osc 付きで起動しているか確認してください）'; })
      .catch(() => { msg.textContent = 'PCに接続できません'; })
      .finally(() => { b.disabled = false; refresh(); });
  });
}
setInterval(refresh, 500); refresh();
</script>
</body></html>
"""


def start_monitor(state: MonitorState, port: int,
                  on_command: Callable[[str], bool] | None = None) -> ThreadingHTTPServer:
    """on_command を渡すと、ページのボタン（POST /cmd、本文は "mode A" など）を受け付ける。"""
    return start_server(port, MONITOR_HTML, state.snapshot, on_command=on_command, thread_name="monitor")
