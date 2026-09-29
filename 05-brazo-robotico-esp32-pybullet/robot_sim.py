"""Brazo URDF original: articulaciones y cámara 3D con PyBullet."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pybullet as bullet

from protocol import JOINT_NAMES


class RobotScene:
    def __init__(self, urdf_path: Path, width: int = 540, height: int = 368) -> None:
        self.width = width
        self.height = height
        gradient = np.linspace((16, 27, 43), (35, 53, 76), height, dtype=np.uint8)
        self.background = np.broadcast_to(
            gradient[:, np.newaxis, :], (height, width, 3)
        ).copy()
        self.client = bullet.connect(bullet.DIRECT)
        if self.client < 0:
            raise RuntimeError("PyBullet no pudo iniciar el motor 3D")

        try:
            self.robot = bullet.loadURDF(
                str(urdf_path.resolve()), useFixedBase=True, physicsClientId=self.client
            )
            self.joints: dict[str, tuple[int, float, float]] = {}
            for index in range(bullet.getNumJoints(self.robot, physicsClientId=self.client)):
                info = bullet.getJointInfo(self.robot, index, physicsClientId=self.client)
                name = info[1].decode("utf-8")
                if name in JOINT_NAMES:
                    self.joints[name] = (index, float(info[8]), float(info[9]))
            if set(self.joints) != set(JOINT_NAMES):
                missing = set(JOINT_NAMES) - set(self.joints)
                raise ValueError(f"Faltan articulaciones en el URDF: {sorted(missing)}")

            self.current = {
                name: bullet.getJointState(self.robot, index, physicsClientId=self.client)[0]
                for name, (index, _, _) in self.joints.items()
            }
            self.targets = dict(self.current)
            self.rates = {"joint_1": 1.5, "joint_2": 1.2,
                          "joint_gripper": 0.2, "joint_dedo_izq": 0.08,
                          "joint_dedo_der": 0.08}
            self.azimuth = 120.0
            self.elevation = -28.0
            self.distance = 1.4

            # Suelo de la escena; el archivo URDF no se altera.
            floor = bullet.createVisualShape(
                bullet.GEOM_BOX, halfExtents=[2.5, 2.5, 0.015],
                rgbaColor=[0.12, 0.18, 0.24, 1], physicsClientId=self.client
            )
            bullet.createMultiBody(
                baseMass=0, baseVisualShapeIndex=floor,
                basePosition=[0, 0, -0.017], physicsClientId=self.client
            )
        except Exception:
            bullet.disconnect(physicsClientId=self.client)
            raise

    def set_targets(self, positions: dict[str, float]) -> None:
        for name, position in positions.items():
            if name not in self.joints:
                raise ValueError(f"Articulación desconocida: {name}")
            _, lower, upper = self.joints[name]
            self.targets[name] = max(lower, min(upper, float(position)))

    def advance(self, elapsed: float) -> None:
        dt = max(0.0, min(elapsed, 0.1))
        for name, (index, _, _) in self.joints.items():
            difference = self.targets[name] - self.current[name]
            maximum = self.rates[name] * dt
            self.current[name] += max(-maximum, min(maximum, difference))
            bullet.resetJointState(
                self.robot, index, self.current[name], physicsClientId=self.client
            )

    def render(self) -> np.ndarray:
        view = bullet.computeViewMatrixFromYawPitchRoll(
            cameraTargetPosition=[0, 0, 0.45], distance=self.distance,
            yaw=self.azimuth, pitch=self.elevation, roll=0, upAxisIndex=2
        )
        projection = bullet.computeProjectionMatrixFOV(
            fov=53, aspect=self.width / self.height, nearVal=0.05, farVal=10.0
        )
        _, _, rgba, _, mask = bullet.getCameraImage(
            self.width, self.height, viewMatrix=view, projectionMatrix=projection,
            renderer=bullet.ER_TINY_RENDERER, shadow=1, physicsClientId=self.client
        )
        frame = np.asarray(rgba, dtype=np.uint8).reshape(self.height, self.width, 4)[:, :, :3].copy()
        empty = np.asarray(mask).reshape(self.height, self.width) < 0
        frame[empty] = self.background[empty]
        return frame

    def camera_move(self, azimuth: float = 0, elevation: float = 0,
                    zoom: float = 0) -> None:
        self.azimuth += azimuth
        self.elevation = max(-80.0, min(80.0, self.elevation + elevation))
        self.distance = max(0.7, min(3.0, self.distance + zoom))

    def close(self) -> None:
        if bullet.isConnected(self.client):
            bullet.disconnect(physicsClientId=self.client)
