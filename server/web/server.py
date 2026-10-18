"""スマホのブラウザから見る小さなWebサーバーの共通部分（収集リモコン・推定モニターで共用）。

GET /        → html を返す
GET /status  → get_status() の結果をJSONで返す（ページ側が定期的に取りに来る）
POST /cmd    → on_command(本文) が True なら200、False なら400（リモコンのボタン用。省略可）
PCとスマホは同じWiFiにつなぐ（ファイアウォールの設定は docs/runbook.md §4.0。ポートは既定8765）。
"""
from __future__ import annotations

import json
import threading
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def start_server(port: int, html: str, get_status: Callable[[], dict],
                 on_command: Callable[[str], bool] | None = None,
                 thread_name: str = "web") -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def _send(self, code: int, body: bytes, content_type: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            if self.path == "/status":
                self._send(200, json.dumps(get_status()).encode("utf-8"), "application/json")
            elif self.path in ("/", "/index.html"):
                self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")
            else:
                self._send(404, b"not found", "text/plain")

        def do_POST(self) -> None:
            if on_command is None:
                self._send(404, b"not found", "text/plain")
                return
            length = int(self.headers.get("Content-Length") or 0)
            cmd = self.rfile.read(length).decode("utf-8").strip()
            ok = self.path == "/cmd" and on_command(cmd)
            self._send(200 if ok else 400, b"ok" if ok else b"bad command", "text/plain")

        def log_message(self, *args) -> None:  # アクセスログで画面を埋めない
            pass

    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    threading.Thread(target=server.serve_forever, name=thread_name, daemon=True).start()
    return server
