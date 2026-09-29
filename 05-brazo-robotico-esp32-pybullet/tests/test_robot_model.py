"""Carga del URDF original y movimiento real de articulaciones en PyBullet."""

import sys
import unittest
from pathlib import Path

import pybullet as bullet

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
from protocol import JOINT_NAMES, positions_from_adc
from robot_sim import RobotScene


class ModelTests(unittest.TestCase):
    def test_original_urdf_moves_and_renders_with_pybullet(self):
        scene = RobotScene(BASE / "brazo.urdf", width=320, height=240)
        try:
            self.assertEqual(set(scene.joints), set(JOINT_NAMES))
            targets = positions_from_adc((65535, 0, 65535, 65535))
            scene.set_targets(targets)
            for _ in range(40):
                scene.advance(0.1)
            for name, expected in targets.items():
                joint_index = scene.joints[name][0]
                actual = bullet.getJointState(
                    scene.robot, joint_index, physicsClientId=scene.client
                )[0]
                self.assertAlmostEqual(actual, expected, places=5)
            image = scene.render()
            self.assertEqual(image.shape, (240, 320, 3))
            self.assertGreater(image.max(), image.min())
            scene.camera_move(azimuth=25, zoom=0.1)
            self.assertFalse((scene.render() == image).all())
        finally:
            scene.close()


if __name__ == "__main__":
    unittest.main()
