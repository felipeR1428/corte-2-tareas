"""Tests exclusivos de teclado y dibujo del punto 1."""

import importlib.util
import math
import sys
import types
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))

from digits import strokes
from drawing_geometry import (CLEARANCE, GRIPPER_LIMIT, PAGE_HEIGHT, PAGE_WIDTH,
                              PARK, PIVOT_Z, SURFACE_X, TIP_FROM_ELBOW,
                              joint_targets, plan_strokes, tip_target)
from teclado_serial import KeyStream, decode_key, encode_key


class KeyboardTests(unittest.TestCase):
    def test_eight_direct_gpio_pins_scan_without_i2c_keypad(self):
        """MicroPython simulado: filas activas en bajo, columnas con pull-up."""
        class FakePin:
            OUT, IN, PULL_UP = 1, 2, 3
            pins = {}
            pressed = set()
            row_numbers = (14, 27, 26, 25)
            col_numbers = (33, 32, 18, 19)

            def __init__(self, number, mode=None, pull=None, value=None):
                self.number, self.mode, self.pull = number, mode, pull
                self.state = value if value is not None else 1
                self.pins[number] = self

            def value(self, new_state=None):
                if new_state is not None:
                    self.state = new_state
                if self.mode == self.IN:
                    col = self.col_numbers.index(self.number)
                    return 0 if any((row, col) in self.pressed and
                                    self.pins[self.row_numbers[row]].state == 0
                                    for row in range(4)) else 1
                return self.state

        fake_machine = types.ModuleType("machine")
        fake_machine.Pin = FakePin
        fake_machine.I2C = object
        firmware_path = BASE / "esp32_teclado_directo" / "main.py"
        spec = importlib.util.spec_from_file_location("firmware_punto1_mock", firmware_path)
        firmware = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {"machine": fake_machine}):
            spec.loader.exec_module(firmware)
        firmware.time = types.SimpleNamespace(sleep_us=lambda delay: None)
        keyboard = firmware.Keyboard()
        self.assertEqual(firmware.ROW_PINS, FakePin.row_numbers)
        self.assertEqual(firmware.COL_PINS, FakePin.col_numbers)
        self.assertTrue(all(FakePin.pins[x].mode == FakePin.OUT
                            for x in firmware.ROW_PINS))
        self.assertTrue(all(FakePin.pins[x].pull == FakePin.PULL_UP
                            for x in firmware.COL_PINS))
        self.assertIsNone(keyboard.read())
        FakePin.pressed = {(1, 2)}  # Segunda fila, tercera columna = '6'.
        self.assertEqual(keyboard.read(), "6")
        self.assertTrue(all(FakePin.pins[x].state == 1 for x in firmware.ROW_PINS))
        FakePin.pressed = {(1, 2), (0, 0)}
        self.assertIsNone(keyboard.read())

    def test_key_from_fragmented_usb_stream(self):
        receiver = KeyStream()
        valid = encode_key("7") + encode_key("A")
        self.assertEqual(receiver.feed(b"MPY: soft reboot\n" + valid[:7]), [])
        self.assertEqual(receiver.feed(valid[7:] + b"@KEY,9*00\n"), ["7", "A"])

    def test_invalid_or_corrupt_key_rejected(self):
        with self.assertRaises(ValueError):
            decode_key(b"@KEY,1*00\n")
        with self.assertRaises(ValueError):
            encode_key("11")

    def test_all_digits_have_strokes_with_coordinates_inside_page(self):
        for digit in range(10):
            lines = strokes(digit)
            self.assertGreaterEqual(len(lines), 1)
            for line in lines:
                self.assertGreater(len(line), 12)
                for x, y in line:
                    self.assertTrue(0 <= x <= 1 and 0 <= y <= 1)

    def test_zero_is_rounded_and_has_reasonable_proportions(self):
        points = strokes(0)[0]
        xs, ys = zip(*points)
        self.assertGreater(len(set(round(x, 3) for x in xs)), 30)
        self.assertLess(math.dist(points[0], points[-1]), .00001)
        real_width = (max(xs) - min(xs)) * PAGE_WIDTH
        real_height = (max(ys) - min(ys)) * PAGE_HEIGHT
        self.assertGreater(real_width / real_height, .70)
        self.assertLess(real_width / real_height, .95)


class FlatDrawingTests(unittest.TestCase):
    def test_inverse_kinematics_matches_original_urdf_dimensions(self):
        root = ET.parse(BASE / "brazo.urdf").getroot()
        joints = {item.attrib["name"]: item for item in root.findall("joint")}
        base_z = float(joints["joint_1"].find("origin").attrib["xyz"].split()[2])
        elbow_z = float(joints["joint_2"].find("origin").attrib["xyz"].split()[2])
        wrist_z = float(joints["joint_gripper"].find("origin").attrib["xyz"].split()[2])
        limit = float(joints["joint_gripper"].find("limit").attrib["upper"])
        self.assertAlmostEqual(PIVOT_Z, base_z + elbow_z)
        self.assertAlmostEqual(TIP_FROM_ELBOW, wrist_z + .165)
        self.assertAlmostEqual(GRIPPER_LIMIT, limit)

    def test_every_digit_uses_one_plane_and_reachable_original_urdf_joints(self):
        for digit in range(10):
            for u, v, down, clearance in plan_strokes(strokes(digit)):
                x, y, z = tip_target(u, v, clearance)
                q1, q2, extension = joint_targets((x, y, z))
                self.assertTrue(0 <= extension <= GRIPPER_LIMIT)
                radius = TIP_FROM_ELBOW + extension
                self.assertAlmostEqual(radius * math.sin(q2) * math.cos(q1), x)
                self.assertAlmostEqual(radius * math.sin(q2) * math.sin(q1), y)
                self.assertAlmostEqual(PIVOT_Z + radius * math.cos(q2), z)
                if down:
                    self.assertAlmostEqual(x, SURFACE_X)
                    self.assertEqual(clearance, 0)
                else:
                    self.assertLessEqual(x, SURFACE_X)

    def test_marker_lifts_before_travel_and_parks_outside_number(self):
        for digit in range(10):
            program = plan_strokes(strokes(digit))
            self.assertEqual(program[-1], (*PARK, False, CLEARANCE))
            self.assertGreater(tip_target(*PARK, CLEARANCE)[1], PAGE_WIDTH / 2)
            for previous, current in zip(program, program[1:]):
                if not current[2] and current[3] < CLEARANCE:
                    self.assertLess(math.dist(current[:2], previous[:2]), 1e-9)
                if (not previous[2] and not current[2] and
                        math.dist(current[:2], previous[:2]) > 1e-9):
                    self.assertEqual(current[3], CLEARANCE)


@unittest.skipUnless(importlib.util.find_spec("pybullet") and importlib.util.find_spec("numpy"),
                     "PyBullet y NumPy no estan instalados")
class RobotTests(unittest.TestCase):
    def test_original_urdf_draws_actual_3d_ink(self):
        import pybullet as p
        from draw_robot import DrawingRobot
        robot = DrawingRobot(BASE / "brazo.urdf", 120, 90)
        try:
            robot.draw(8)
            while robot.pending:
                robot.step(12)
            self.assertGreater(len(robot.ink), 60)
            ink_positions = [p.getBasePositionAndOrientation(body,
                             physicsClientId=robot.client)[0] for body in robot.ink]
            self.assertTrue(all(abs(position[0] - SURFACE_X) < .0001
                                for position in ink_positions))
            self.assertAlmostEqual(robot.tip_position[0], SURFACE_X - CLEARANCE, places=4)
            self.assertGreater(robot.tip_position[1],
                               max(position[1] for position in ink_positions) + .10)
            self.assertEqual(robot.render().shape, (90, 120, 3))
            robot.clear()
            self.assertEqual(len(robot.ink), 0)
        finally:
            robot.close()


if __name__ == "__main__":
    unittest.main()
