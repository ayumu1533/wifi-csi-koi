"""OSまわりの小さな共通処理（収集・推定の両方で使う）。"""
from __future__ import annotations

import logging
import os
import socket

logger = logging.getLogger("system")


def prevent_sleep() -> None:
    """収集・推定中にPCがスリープしないようにする（画面は消えてよい）。プロセスが終われば自動で元に戻る。"""
    if os.name != "nt":
        return
    import ctypes
    ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
    if not ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED):
        logger.warning("スリープの抑止に失敗しました。電源設定でスリープを「なし」にしてください")


def lan_ip() -> str:
    """スマホから接続するときのPCのIPアドレス（UDPのconnectは実際には何も送らない）。"""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
        except OSError:
            return "127.0.0.1"
