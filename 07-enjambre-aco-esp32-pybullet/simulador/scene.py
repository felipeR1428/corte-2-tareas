"""Escena PyBullet DIRECT: render CPU y poses recibidas de los nodos."""
import io
import math
import threading
import numpy as np
from PIL import Image
import pybullet as p
from .bridge import GRID, CELL_METERS, pose_on_path

COLORS = ((1.0, 0.48, 0.22, 1), (0.10, 0.76, 0.78, 1), (0.65, 0.52, 1.0, 1))


class Scene:
    def __init__(self, width=960, height=600):
        self.client = p.connect(p.DIRECT)
        self.width, self.height = width, height
        self.camera_lock = threading.Lock()
        self.default_distance = math.hypot(len(GRID[0]), len(GRID)) * CELL_METERS * 1.05
        self.camera = self.default_camera()
        self.paths = {}
        self.path_bodies = {}
        self.cars = {}
        self.display = {}
        self.walls = []
        p.setGravity(0, 0, -9.81, physicsClientId=self.client)
        p.setTimeStep(1 / 60, physicsClientId=self.client)
        self._build()

    def default_camera(self):
        return {"yaw": 38.0, "pitch": -62.0, "distance": self.default_distance,
                "pan_x": 0.0, "pan_y": 0.0}

    def _box(self, half, position, color, collision=True, yaw=0.0):
        visual = p.createVisualShape(p.GEOM_BOX, halfExtents=half, rgbaColor=color, physicsClientId=self.client)
        shape = p.createCollisionShape(p.GEOM_BOX, halfExtents=half, physicsClientId=self.client) if collision else -1
        return p.createMultiBody(baseMass=0, baseVisualShapeIndex=visual, baseCollisionShapeIndex=shape,
                                 basePosition=position, baseOrientation=p.getQuaternionFromEuler([0, 0, yaw]),
                                 physicsClientId=self.client)

    def _build(self):
        scale = CELL_METERS
        width, height = len(GRID[0]), len(GRID)
        self._box([width * scale / 2 + 0.08, height * scale / 2 + 0.08, 0.06],
                  [(width - 1) * scale / 2, -(height - 1) * scale / 2, -0.07], [0.08, 0.12, 0.17, 1])
        for y, row in enumerate(GRID):
            for x, value in enumerate(row):
                pos = [x * scale, -y * scale, 0.0]
                if value == "#":
                    wall = self._box([scale / 2 - 0.005, scale / 2 - 0.005, 0.145],
                                     [pos[0], pos[1], 0.145], [0.24, 0.32, 0.39, 1])
                    self.walls.append(wall)
                else:
                    color = [0.18, 0.23, 0.29, 1]
                    if value == "A":
                        color = [0.13, 0.47, 0.42, 1]
                    elif value == "G":
                        color = [0.90, 0.67, 0.25, 1]
                    self._box([scale / 2 - 0.01, scale / 2 - 0.01, 0.006],
                              [pos[0], pos[1], -0.002], color, collision=False)
        for node in (1, 2, 3):
            self.cars[node] = self._car(COLORS[node - 1])
            p.resetBasePositionAndOrientation(self.cars[node], [scale, -scale + (node - 2) * 0.065, 0.095],
                                               [0, 0, 0, 1], physicsClientId=self.client)

    def _car(self, color):
        base = p.createVisualShape(p.GEOM_BOX, halfExtents=[0.125, 0.065, 0.035], rgbaColor=color, physicsClientId=self.client)
        collision = p.createCollisionShape(p.GEOM_BOX, halfExtents=[0.125, 0.065, 0.04], physicsClientId=self.client)
        wheel = p.createVisualShape(p.GEOM_CYLINDER, radius=0.035, length=0.027,
                                    rgbaColor=[0.035, 0.045, 0.06, 1], physicsClientId=self.client)
        cabin = p.createVisualShape(p.GEOM_BOX, halfExtents=[0.065, 0.052, 0.022],
                                    rgbaColor=[0.08, 0.15, 0.23, 1], physicsClientId=self.client)
        marker = p.createVisualShape(p.GEOM_SPHERE, radius=0.017, rgbaColor=[1, 1, 0.92, 1], physicsClientId=self.client)
        positions = [[x, y, -0.025] for x in (-0.075, 0.075) for y in (-0.073, 0.073)]
        positions += [[-0.014, 0, 0.053], [0.108, 0, 0.04]]
        rotate = p.getQuaternionFromEuler([math.pi / 2, 0, 0])
        return p.createMultiBody(
            baseMass=0, baseCollisionShapeIndex=collision, baseVisualShapeIndex=base,
            linkMasses=[0] * 6, linkCollisionShapeIndices=[-1] * 6,
            linkVisualShapeIndices=[wheel] * 4 + [cabin, marker], linkPositions=positions,
            linkOrientations=[rotate] * 4 + [[0, 0, 0, 1]] * 2,
            linkInertialFramePositions=[[0, 0, 0]] * 6,
            linkInertialFrameOrientations=[[0, 0, 0, 1]] * 6,
            linkParentIndices=[0] * 6, linkJointTypes=[p.JOINT_FIXED] * 6,
            linkJointAxis=[[0, 0, 0]] * 6, physicsClientId=self.client,
        )

    def _route(self, node, path):
        if self.paths.get(node) == path:
            return
        for body in self.path_bodies.get(node, []):
            p.removeBody(body, physicsClientId=self.client)
        self.paths[node] = list(path)
        bodies = []
        offset = (node - 2) * 0.018
        for a, b in zip(path, path[1:]):
            x1, y1 = a % len(GRID[0]), a // len(GRID[0])
            x2, y2 = b % len(GRID[0]), b // len(GRID[0])
            yaw = math.atan2(-(y2 - y1), x2 - x1)
            bodies.append(self._box([CELL_METERS / 2, 0.007, 0.003],
                                   [(x1 + x2) * CELL_METERS / 2 + offset,
                                    -(y1 + y2) * CELL_METERS / 2 + offset, 0.012],
                                   COLORS[node - 1], collision=False, yaw=yaw))
        self.path_bodies[node] = bodies

    def update(self, snapshot, dt):
        for state in snapshot["nodes"]:
            node = state["src"]
            best = state.get("route") or state.get("best_path", [])
            self._route(node, best)
            route = state.get("route", [])
            identity = (state.get("boot"), state.get("epoch"), tuple(route))
            target = state.get("progress", 0.0)
            display = self.display.get(node)
            if not display or display["identity"] != identity:
                display = {"identity": identity, "progress": target}
                self.display[node] = display
            # Avanzar sobre la polilinea recibida; nunca interpolar en diagonal.
            if state.get("online"):
                difference = target - display["progress"]
                display["progress"] += difference * min(1.0, max(0.0, dt) * 15.0)
            x, y, yaw = pose_on_path(route, display["progress"])
            p.resetBasePositionAndOrientation(self.cars[node],
                                               [x * CELL_METERS, -y * CELL_METERS + (node - 2) * 0.065, 0.095],
                                               p.getQuaternionFromEuler([0, 0, -yaw]), physicsClientId=self.client)
        p.stepSimulation(physicsClientId=self.client)

    def configure_camera(self, action, **values):
        with self.camera_lock:
            if action == "reset":
                self.camera = self.default_camera()
            elif action == "orbit":
                self.camera["yaw"] = (self.camera["yaw"] + float(values.get("dx", 0)) * 0.3) % 360
                self.camera["pitch"] = max(-88, min(-15, self.camera["pitch"] + float(values.get("dy", 0)) * 0.3))
            elif action == "zoom":
                self.camera["distance"] = max(self.default_distance * 0.25,
                                              min(self.default_distance * 3.0, self.camera["distance"] * math.exp(float(values.get("dy", 0)) * 0.001)))
            elif action == "pan":
                self.camera["pan_x"] += float(values.get("dx", 0)) * 0.003
                self.camera["pan_y"] -= float(values.get("dy", 0)) * 0.003

    def render(self):
        with self.camera_lock:
            camera = dict(self.camera)
        center = [(len(GRID[0]) - 1) * CELL_METERS / 2 + camera["pan_x"],
                  -(len(GRID) - 1) * CELL_METERS / 2 + camera["pan_y"], 0.0]
        view = p.computeViewMatrixFromYawPitchRoll(center, camera["distance"], camera["yaw"], camera["pitch"], 0, 2)
        projection = p.computeProjectionMatrixFOV(48, self.width / self.height, 0.05, 40)
        result = p.getCameraImage(self.width, self.height, viewMatrix=view,
                                  projectionMatrix=projection, renderer=p.ER_TINY_RENDERER,
                                  shadow=1, lightDirection=[-2, -3, 7], physicsClientId=self.client)
        rgba = np.asarray(result[2], dtype=np.uint8).reshape(self.height, self.width, 4)
        rgba = rgba.copy()
        background = np.asarray(result[4]).reshape(self.height, self.width) == -1
        rgba[background, :3] = [11, 17, 27]
        buffer = io.BytesIO()
        Image.fromarray(rgba[:, :, :3]).save(buffer, format="JPEG", quality=86)
        return buffer.getvalue()

    def wall_collisions(self):
        p.performCollisionDetection(physicsClientId=self.client)
        return [(node, wall) for node, car in self.cars.items() for wall in self.walls
                if any(contact[8] < -0.003 for contact in p.getClosestPoints(car, wall, 0,
                                                                          physicsClientId=self.client))]

    def close(self):
        if p.isConnected(self.client):
            p.disconnect(self.client)
