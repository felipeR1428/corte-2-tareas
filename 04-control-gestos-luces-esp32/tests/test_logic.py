"""Pruebas de estabilidad y de las transiciones de la vista previa."""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from logic import (GestureGate, HandGesture, preview_levels,
                   read_hand_gestures, select_control_gesture)


class TwoHandTests(unittest.TestCase):
    def test_reads_both_hands_without_mixing_gestures_and_sides(self):
        category = lambda name, score=1.0: SimpleNamespace(category_name=name, score=score)
        result = SimpleNamespace(
            hand_landmarks=[object(), object()],
            gestures=[[category("Thumb_Up", .93)], [category("Victory", .88)]],
            handedness=[[category("Right")], [category("Left")]],
        )
        self.assertEqual(read_hand_gestures(result), (
            HandGesture("Right", "Thumb_Up", .93),
            HandGesture("Left", "Victory", .88),
        ))

    def test_either_hand_controls_and_conflicting_hands_wait(self):
        victory = HandGesture("Right", "Victory", .95)
        fist = HandGesture("Left", "Closed_Fist", .90)
        neutral = HandGesture("Left", "None", .99)
        self.assertEqual(select_control_gesture((neutral, victory)), ("Victory", .95, False))
        self.assertEqual(select_control_gesture((fist, victory)), (None, 0.0, True))
        self.assertEqual(select_control_gesture((victory, HandGesture("Left", "Victory", .81))),
                         ("Victory", .95, False))
        self.assertEqual(select_control_gesture((fist, HandGesture("Right", "Victory", .69))),
                         ("Closed_Fist", .90, False))

    def test_conflict_does_not_repeat_held_gesture_after_resolution(self):
        gate = GestureGate()
        for n in range(5):
            action = gate.observe("Victory", .95, .05 * n)
        self.assertEqual(action, "SET 70")
        gate.pause_for_conflict()
        for n in range(8):
            self.assertIsNone(gate.observe("Victory", .95, 2 + .05 * n))



class GestureGateTests(unittest.TestCase):
    def test_waits_for_five_confident_frames_and_does_not_repeat_hold(self):
        gate = GestureGate()
        self.assertEqual([gate.observe("Closed_Fist", .9, n * .05) for n in range(4)],
                         [None] * 4)
        self.assertEqual(gate.observe("Closed_Fist", .9, .2), "SET 30")
        self.assertEqual([gate.observe("Closed_Fist", .9, .3 + n * .05)
                          for n in range(30)], [None] * 30)

    def test_release_then_repeat_and_switch_gesture(self):
        gate = GestureGate()
        for n in range(5):
            first = gate.observe("Thumb_Down", .9, n * .05)
        self.assertEqual(first, "MODE 1")
        for n in range(3):
            gate.observe(None, 0.0, 1 + n * .05)
        for n in range(5):
            repeated = gate.observe("Thumb_Down", .9, 2 + n * .05)
        self.assertEqual(repeated, "MODE 1")
        for n in range(5):
            changed = gate.observe("Thumb_Up", .9, 3 + n * .05)
        self.assertEqual(changed, "MODE 2")

    def test_weak_classification_and_cooldown(self):
        gate = GestureGate()
        for n in range(10):
            self.assertIsNone(gate.observe("Open_Palm", .69, n * .05))
        for n in range(5):
            first = gate.observe("Open_Palm", .8, 1 + n * .05)
        self.assertEqual(first, "SET 100")
        for n in range(5):
            self.assertIsNone(gate.observe("Victory", .9, 1.3 + n * .05))
        self.assertEqual(gate.observe("Victory", .9, 2.1), "SET 70")


class PreviewTests(unittest.TestCase):
    def test_static_levels(self):
        self.assertEqual(preview_levels("SET 30"), (30, 0, 0))
        self.assertEqual(preview_levels("SET 70"), (0, 70, 0))
        self.assertEqual(preview_levels("SET 100"), (0, 0, 100))
        self.assertEqual(preview_levels("STOP"), (0, 0, 0))

    def test_mode_boundaries_and_repetition(self):
        self.assertEqual(preview_levels("MODE 1", 0.59), (30, 0, 0))
        self.assertEqual(preview_levels("MODE 1", 0.60), (0, 70, 0))
        self.assertEqual(preview_levels("MODE 1", 1.20), (0, 0, 100))
        self.assertEqual(preview_levels("MODE 1", 1.80), (0, 0, 0))
        self.assertEqual(preview_levels("MODE 1", 2.10), (30, 0, 0))
        self.assertEqual(preview_levels("MODE 2", 0.35), (0, 70, 0))
        self.assertEqual(preview_levels("MODE 2", 0.70), (30, 0, 0))
        self.assertEqual(preview_levels("MODE 2", 1.40), (0, 0, 100))


if __name__ == "__main__":
    unittest.main()
