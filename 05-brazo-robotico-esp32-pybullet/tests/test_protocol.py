"""Integración entre la trama real de MicroPython y el parser del computador."""

import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))

from protocol import PacketStream, SensorPacket, parse_packet, positions_from_adc


def load_esp32_firmware():
    module = types.ModuleType("machine")
    module.ADC = object
    module.Pin = object
    path = BASE / "esp32" / "main.py"
    spec = importlib.util.spec_from_file_location("firmware_esp32", path)
    firmware = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {"machine": module}):
        spec.loader.exec_module(firmware)
    return firmware


class ProtocolTests(unittest.TestCase):
    def test_firmware_packet_reaches_pc_even_when_split(self):
        firmware = load_esp32_firmware()
        line = (firmware.build_frame(65535, 123456, (0, 65535, 32768, 12000)) + "\n").encode()
        stream = PacketStream()
        self.assertEqual(stream.feed(b"MicroPython ready\r\n" + line[:11]), [])
        self.assertEqual(stream.feed(line[11:]),
                         [SensorPacket(65535, 123456, (0, 65535, 32768, 12000))])
        self.assertEqual(stream.bad, 0)

    def test_corrupt_checksum_and_adc_range_are_rejected_then_recover(self):
        firmware = load_esp32_firmware()
        valid = (firmware.build_frame(1, 9, (100, 200, 300, 400)) + "\n").encode()
        corrupt = valid.replace(b",400*", b",401*")
        too_large = (firmware.build_frame(2, 10, (65536, 200, 300, 400)) + "\n").encode()
        stream = PacketStream()
        self.assertEqual(stream.feed(corrupt + too_large + valid),
                         [SensorPacket(1, 9, (100, 200, 300, 400))])
        self.assertEqual(stream.bad, 2)
        with self.assertRaises(ValueError):
            parse_packet(corrupt)

    def test_potentiometers_follow_original_urdf_limits(self):
        low = positions_from_adc((0, 0, 0, 0))
        high = positions_from_adc((65535, 65535, 65535, 65535))
        self.assertEqual((low["joint_1"], high["joint_1"]), (-2.5, 2.5))
        self.assertEqual((low["joint_2"], high["joint_2"]), (-2.0, 2.0))
        self.assertEqual((low["joint_gripper"], high["joint_gripper"]), (0.0, 0.15))
        self.assertEqual(high["joint_dedo_izq"], high["joint_dedo_der"])
        self.assertEqual(high["joint_dedo_der"], 0.05)


if __name__ == "__main__":
    unittest.main()
