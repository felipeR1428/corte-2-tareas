"""El mismo URDF escribe cifras curvas sobre una hoja plana y retira la pinza."""

from __future__ import annotations

import math
from collections import deque
from pathlib import Path

import numpy as np
import pybullet as p

from digits import strokes
from drawing_geometry import CLEARANCE, PARK, joint_targets, plan_strokes, tip_target


def orientation_to(direction):
    """Cuaternion que alinea el eje z de un cilindro con un vector unitario."""
    z = direction[2]
    if z < -0.999999:
        return (1.0, 0.0, 0.0, 0.0)
    length = math.sqrt(direction[0] ** 2 + direction[1] ** 2 + (1 + z) ** 2)
    return (-direction[1] / length, direction[0] / length, 0.0, (1 + z) / length)


class DrawingRobot:
    def __init__(self, urdf: Path, width: int = 600, height: int = 460):
        self.client = p.connect(p.DIRECT)
        if self.client < 0:
            raise RuntimeError("No se pudo iniciar PyBullet")
        self.width, self.height = width, height
        self.ink = []
        self.pending = deque()
        self.last_tip = None
        self.tip_position = None
        self.cursor = (.5, .5)
        self.clearance = CLEARANCE
        self.last_digit = None
        self.yaw = 0.0
        self.zoom = 1.0
        try:
            self.robot = p.loadURDF(str(urdf.resolve()), useFixedBase=True, physicsClientId=self.client)
            self.joints = {
                p.getJointInfo(self.robot, i, physicsClientId=self.client)[1].decode(): i
                for i in range(p.getNumJoints(self.robot, physicsClientId=self.client))
            }
            required = {"joint_1", "joint_2", "joint_gripper", "joint_dedo_izq", "joint_dedo_der"}
            if not required.issubset(self.joints):
                raise ValueError("El URDF no contiene las articulaciones previstas")
            pen_shape = p.createVisualShape(
                p.GEOM_CYLINDER, radius=0.008, length=0.09,
                rgbaColor=(0.15, 0.9, 0.2, 1), physicsClientId=self.client,
            )
            self.pen_body = p.createMultiBody(
                baseMass=0, baseVisualShapeIndex=pen_shape,
                physicsClientId=self.client,
            )
            self.move(*PARK, False, CLEARANCE)
        except Exception:
            p.disconnect(physicsClientId=self.client)
            raise

    def _joint(self, name: str, value: float):
        p.resetJointState(self.robot, self.joints[name], value, physicsClientId=self.client)

    def move(self, x: float, y: float, pen_down: bool, clearance: float = 0.0):
        # La punta sigue un plano de X fija. Se resuelven las tres articulaciones
        # del URDF para alcanzar (X, Y, Z), sin dibujar arcos de cilindro.
        target = tip_target(x, y, clearance)
        q1, q2, extension = joint_targets(target)
        self._joint("joint_1", q1)
        self._joint("joint_2", q2)
        self._joint("joint_gripper", extension)
        self._joint("joint_dedo_izq", 0.025)
        self._joint("joint_dedo_der", 0.025)
        info = p.getLinkState(
            self.robot, self.joints["joint_gripper"],
            computeForwardKinematics=1, physicsClientId=self.client,
        )
        pen_center, pen_orientation = p.multiplyTransforms(
            info[4], info[5], (0, 0, 0.12), (0, 0, 0, 1)
        )
        p.resetBasePositionAndOrientation(
            self.pen_body, pen_center, pen_orientation, physicsClientId=self.client
        )
        tip, _ = p.multiplyTransforms(info[4], info[5], (0, 0, 0.165), (0, 0, 0, 1))
        self.tip_position = tip
        self.cursor = (x, y)
        self.clearance = clearance
        if pen_down and self.last_tip is not None:
            self._ink_line(self.last_tip, tip)
        self.last_tip = tip if pen_down else None

    def _ink_line(self, start, end):
        vector = tuple(b - a for a, b in zip(start, end))
        length = math.sqrt(sum(v * v for v in vector))
        if length < 0.0001:
            return
        shape = p.createVisualShape(
            p.GEOM_CYLINDER, radius=0.0035, length=length,
            rgbaColor=(1, 0.30, 0.07, 1), physicsClientId=self.client,
        )
        body = p.createMultiBody(
            baseMass=0, baseVisualShapeIndex=shape,
            basePosition=tuple((a + b) / 2 for a, b in zip(start, end)),
            baseOrientation=orientation_to(tuple(v / length for v in vector)),
            physicsClientId=self.client,
        )
        self.ink.append(body)

    def clear(self):
        self.pending.clear()
        self.last_tip = None
        self.last_digit = None
        if self.tip_position is not None:
            self.move(*self.cursor, False, CLEARANCE)
        for body in self.ink:
            p.removeBody(body, physicsClientId=self.client)
        self.ink.clear()

    def draw(self, digit: int):
        self.clear()
        self.last_digit = digit
        self.pending.extend(plan_strokes(strokes(digit), self.cursor))

    def step(self, count: int = 3) -> bool:
        changed = False
        for _ in range(min(count, len(self.pending))):
            self.move(*self.pending.popleft())
            changed = True
        return changed

    def camera_move(self, delta: float = 0.0, zoom_delta: float = 0.0):
        self.yaw += delta
        self.zoom = max(0.65, min(1.7, self.zoom + zoom_delta))

    def render(self) -> np.ndarray:
        # Vista casi perpendicular al plano; permite girar y hacer zoom.
        theta = math.radians(self.yaw)
        target = (0.13, 0, 0.81)
        eye = (1.08 * self.zoom * math.cos(theta),
               1.08 * self.zoom * math.sin(theta), 0.93)
        view = p.computeViewMatrix(eye, target, (0, 0, 1))
        projection = p.computeProjectionMatrixFOV(52, self.width / self.height, 0.05, 8)
        _, _, rgba, _, _ = p.getCameraImage(
            self.width, self.height, viewMatrix=view, projectionMatrix=projection,
            renderer=p.ER_TINY_RENDERER, physicsClientId=self.client,
        )
        return np.asarray(rgba, dtype=np.uint8).reshape(self.height, self.width, 4)[:, :, :3].copy()

    def close(self):
        if p.isConnected(self.client):
            p.disconnect(physicsClientId=self.client)
