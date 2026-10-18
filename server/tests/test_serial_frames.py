"""csi_receiver.serial_frames のテスト。実行: python -m unittest discover -s tests -t ."""
import struct
import unittest

from csi_receiver.packet import HEADER_FMT, MAGIC, parse_packet
from csi_receiver.serial_frames import FrameParser, crc16, encode_frame


def make_payload(sensor_id: int = 3, seq: int = 1, csi_len: int = 128) -> bytes:
    csi = bytes((i % 100) - 50 & 0xFF for i in range(csi_len))
    header = struct.pack(HEADER_FMT, MAGIC, sensor_id, seq, 123456, -55, 11, 0, 0, csi_len)
    return header + csi


class FrameParserTest(unittest.TestCase):
    def test_roundtrip_and_parse_packet(self):
        payload = make_payload(sensor_id=2, seq=7)
        out = FrameParser().feed(encode_frame(payload))
        self.assertEqual(out, [payload])
        pkt = parse_packet(out[0])
        self.assertEqual((pkt.sensor_id, pkt.seq, len(pkt.amplitude)), (2, 7, 64))

    def test_crc_known_value(self):
        # CRC-16/CCITT-FALSE の標準チェック値
        self.assertEqual(crc16(b"123456789"), 0x29B1)

    def test_split_across_reads(self):
        data = encode_frame(make_payload(seq=1)) + encode_frame(make_payload(seq=2))
        parser = FrameParser()
        out = []
        for i in range(0, len(data), 5):  # 5バイトずつ細切れに到着
            out += parser.feed(data[i:i + 5])
        self.assertEqual([parse_packet(p).seq for p in out], [1, 2])

    def test_garbage_and_boot_log_before_and_between(self):
        data = (b"ESP-ROM:esp32s3-20210327\r\nrst:0x1\r\n" + encode_frame(make_payload(seq=1))
                + b"\xaa\x00garbage\r\n" + encode_frame(make_payload(seq=2)))
        parser = FrameParser()
        out = parser.feed(data)
        self.assertEqual([parse_packet(p).seq for p in out], [1, 2])
        self.assertGreater(parser.skipped_bytes, 0)

    def test_crc_error_dropped_and_resync(self):
        bad = bytearray(encode_frame(make_payload(seq=1)))
        bad[10] ^= 0xFF  # payload破損
        parser = FrameParser()
        out = parser.feed(bytes(bad) + encode_frame(make_payload(seq=2)))
        self.assertEqual([parse_packet(p).seq for p in out], [2])
        self.assertEqual(parser.crc_errors, 1)

    def test_sync_bytes_inside_payload(self):
        # payload中に 0xAA 0x55 が含まれても、長さ+CRCで正しく切り出せる
        payload = bytearray(make_payload(seq=5))
        payload[20:22] = b"\xaa\x55"
        out = FrameParser().feed(encode_frame(bytes(payload)))
        self.assertEqual(out, [bytes(payload)])

    def test_bogus_length_does_not_stall(self):
        data = b"\xaa\x55\xff\xff" + encode_frame(make_payload(seq=9))
        out = FrameParser().feed(data)
        self.assertEqual([parse_packet(p).seq for p in out], [9])


try:
    import serial  # noqa: F401  （pyserial。学習だけの環境（Google Colab など）には入っていない）
    HAS_PYSERIAL = True
except ImportError:
    HAS_PYSERIAL = False


@unittest.skipUnless(HAS_PYSERIAL, "pyserialが入っていない（シリアル受信を使わない環境）")
class SerialSourceTest(unittest.IsolatedAsyncioTestCase):
    async def test_loopback_to_protocol(self):
        import asyncio
        from csi_receiver.source import SerialSource

        got = []

        class Proto:
            def datagram_received(self, data, addr):
                got.append(parse_packet(data).seq)

        source = SerialSource("loop://", 921600, Proto(), asyncio.get_running_loop())
        try:
            source._serial.write(b"boot log" + encode_frame(make_payload(seq=1)) + encode_frame(make_payload(seq=2)))
            for _ in range(50):
                if len(got) == 2:
                    break
                await asyncio.sleep(0.02)
        finally:
            source.close()
        self.assertEqual(got, [1, 2])


if __name__ == "__main__":
    unittest.main()
