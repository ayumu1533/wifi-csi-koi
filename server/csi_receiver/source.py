"""CSIパケットの入力元（UDP / USBシリアル）を切り替える。

どちらの入力でも、受信した1パケット（docs/protocol.mdのバイト列）を
`protocol.datagram_received(data, addr)` に渡す。これにより receiver / collect / estimate の
各Protocolクラスは入力元を意識せずに動く。
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import threading

from .serial_frames import FrameParser

logger = logging.getLogger("csi_source")

DEFAULT_BAUD = 921600


def add_source_args(parser: argparse.ArgumentParser, default_port: int = 3334) -> None:
    parser.add_argument("--serial", default=None, metavar="COMx",
                        help="csi_rxのシリアルポート（例: COM3）。指定するとUDPではなくシリアルから受信する")
    parser.add_argument("--baud", type=int, default=DEFAULT_BAUD,
                        help="--serial のボーレート（csi_rxの CSI_RX_BAUD と一致させる）")
    parser.add_argument("--host", default="0.0.0.0", help="UDP待受アドレス（--serial未指定時）")
    parser.add_argument("--port", type=int, default=default_port, help="UDP待受ポート（--serial未指定時）")


class SerialSource:
    """pyserialを別スレッドで読み、フレームを取り出してイベントループへ渡す。"""

    def __init__(self, port: str, baud: int, protocol, loop: asyncio.AbstractEventLoop) -> None:
        import serial  # 未使用時にpyserialを要求しないよう遅延import

        self._serial = serial.serial_for_url(port, baudrate=baud, timeout=0.1)  # "loop://" 等のURLも可（テスト用）
        self._protocol = protocol
        self._loop = loop
        self._parser = FrameParser()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="serial-source", daemon=True)
        self._port = port
        self._thread.start()

    def _run(self) -> None:
        last_report = 0.0
        while not self._stop.is_set():
            try:
                data = self._serial.read(self._serial.in_waiting or 1)
            except Exception as exc:  # ポートが抜かれた等
                logger.error("serial read failed: %s", exc)
                return
            if not data:
                continue
            for payload in self._parser.feed(data):
                self._loop.call_soon_threadsafe(self._protocol.datagram_received, payload, self._port)
            p = self._parser
            if p.crc_errors and p.crc_errors != last_report:
                logger.warning("serial: %d frames ok, %d crc errors, %d bytes skipped",
                               p.frames_ok, p.crc_errors, p.skipped_bytes)
                last_report = p.crc_errors

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=1.0)
        self._serial.close()


async def open_source(args: argparse.Namespace, protocol_factory):
    """--serial があればシリアル、無ければUDPで受信を始める。戻り値は .close() を持つ。"""
    loop = asyncio.get_running_loop()
    protocol = protocol_factory()
    if args.serial:
        try:
            source = SerialSource(args.serial, args.baud, protocol, loop)
        except Exception as exc:  # serial.SerialException（ポートが無い・他のアプリが使用中など）
            raise SystemExit(f"シリアルポート {args.serial} を開けません: {exc}") from exc
        logger.info("reading csi frames from %s at %d baud", args.serial, args.baud)
        return source
    transport, _ = await loop.create_datagram_endpoint(lambda: protocol, local_addr=(args.host, args.port))
    logger.info("listening on UDP %s:%d", args.host, args.port)
    return transport
