"""Pruebas de main.py de la placa con periféricos MicroPython simulados."""

import io
import runpy
import sys
import time
import types
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch


FIRMWARE = Path(__file__).resolve().parents[1] / "esp32_gestos_luces" / "main.py"


class FakePin:
    def __init__(self, number):
        self.number = number


class FakePWM:
    def __init__(self, pin, freq):
        self.pin = pin.number
        self.freq = freq
        self.level = 0

    def duty_u16(self, value):
        self.level = value


class FakePWMLegacy:
    def __init__(self, pin, freq):
        self.pin = pin.number
        self.level = 0

    def duty(self, value):
        self.level = value


class MicroPythonFirmwareTests(unittest.TestCase):
    def load_firmware(self, pwm_type=FakePWM):
        clock = {"ms": 0}
        fake_machine = types.ModuleType("machine")
        fake_machine.Pin = FakePin
        fake_machine.PWM = pwm_type
        period = 1 << 16
        ticks_diff = lambda a, b: ((a - b + period // 2) % period) - period // 2
        with patch.dict(sys.modules, {"machine": fake_machine}):
            with patch.object(time, "ticks_ms", lambda: clock["ms"], create=True), \
                 patch.object(time, "ticks_diff", ticks_diff, create=True), \
                 patch.object(time, "sleep_ms", lambda _: None, create=True):
                namespace = runpy.run_path(str(FIRMWARE), run_name="esp32_under_test")
        controller = namespace["ControladorLuces"]()
        return controller, clock

    @staticmethod
    def levels(controller):
        return (controller.amarillo.level, controller.azul.level, controller.rojo.level)

    def test_static_commands_and_stop(self):
        controller, _ = self.load_firmware()
        output = io.StringIO()
        with redirect_stdout(output):
            controller.procesar_orden("SET 30")
            self.assertEqual(self.levels(controller), (19661, 0, 0))
            controller.procesar_orden("SET 70")
            self.assertEqual(self.levels(controller), (0, 45875, 0))
            controller.procesar_orden("SET 100")
            self.assertEqual(self.levels(controller), (0, 0, 65535))
            controller.procesar_orden("STOP")
            self.assertEqual(self.levels(controller), (0, 0, 0))
        self.assertIn("STATE OFF", output.getvalue())

    def test_two_sequences_can_be_interrupted(self):
        controller, clock = self.load_firmware()
        with redirect_stdout(io.StringIO()):
            controller.procesar_orden("MODE 1")
            self.assertEqual(self.levels(controller), (19661, 0, 0))
            for moment, expected in (
                (600, (0, 45875, 0)), (1200, (0, 0, 65535)),
                (1800, (0, 0, 0)), (2100, (19661, 0, 0))
            ):
                clock["ms"] = moment
                controller.actualizar()
                self.assertEqual(self.levels(controller), expected)
            controller.procesar_orden("MODE 2")
            self.assertEqual(self.levels(controller), (0, 0, 65535))
            for moment, expected in (
                (2450, (0, 45875, 0)), (2800, (19661, 0, 0)),
                (3150, (0, 45875, 0))
            ):
                clock["ms"] = moment
                controller.actualizar()
                self.assertEqual(self.levels(controller), expected)
            controller.procesar_orden("SET 100")
            self.assertEqual(self.levels(controller), (0, 0, 65535))

    def test_rollover_and_serial_responses(self):
        controller, clock = self.load_firmware()
        output = io.StringIO()
        with redirect_stdout(output):
            clock["ms"] = 65536 - 100
            controller.procesar_orden("MODE 2")
            clock["ms"] = 250
            controller.actualizar()
            self.assertEqual(self.levels(controller), (0, 45875, 0))
            controller.procesar_orden("PING")
            controller.procesar_orden("STATUS")
            controller.procesar_orden("INEXISTENTE")
        self.assertIn("PONG", output.getvalue())
        self.assertIn("STATE MODE 2", output.getvalue())
        self.assertIn("ERR Orden desconocida", output.getvalue())

    def test_older_micropython_pwm(self):
        controller, _ = self.load_firmware(FakePWMLegacy)
        with redirect_stdout(io.StringIO()):
            controller.procesar_orden("SET 30")
            self.assertEqual(self.levels(controller), (307, 0, 0))
            controller.procesar_orden("SET 70")
            self.assertEqual(self.levels(controller), (0, 716, 0))
            controller.procesar_orden("SET 100")
            self.assertEqual(self.levels(controller), (0, 0, 1023))


if __name__ == "__main__":
    unittest.main()
