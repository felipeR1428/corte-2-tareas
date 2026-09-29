"""Cinemática del URDF original para escribir sobre el plano vertical X=0.30 m."""

from __future__ import annotations

import math


SURFACE_X = .30              # Frente de la hoja virtual (metros).
PAGE_WIDTH = .17            # Eje horizontal del texto: Y del mundo.
PAGE_BOTTOM = .86           # Eje vertical del texto: Z del mundo.
PAGE_HEIGHT = .17
PIVOT_Z = .50               # Origen joint_2: base .15 + brazo1 .35.
TIP_FROM_ELBOW = .465       # Brazo2 .30 + punta respecto a pinza .165.
GRIPPER_LIMIT = .15
CLEARANCE = .05             # Separación del marcador respecto al papel.
PARK = (1.85, .35)          # Fuera del número; pinza y marcador no lo tapan.


def tip_target(u: float, v: float, clearance: float = 0.0):
    """Coordenada mundial del extremo: tinta a X constante o punta retirada."""
    if not 0 <= clearance <= CLEARANCE:
        raise ValueError("Separación fuera del espacio de trabajo")
    x = SURFACE_X - clearance
    y = (u - .5) * PAGE_WIDTH
    z = PAGE_BOTTOM + v * PAGE_HEIGHT
    if clearance:
        # Cerca de la base, el prisma no puede retraerse por debajo de cero.
        # Subir unos milímetros mientras se retira evita una postura imposible.
        radius = math.hypot(x, y)
        min_z = PIVOT_Z + math.sqrt(max(0, TIP_FROM_ELBOW**2 - radius**2)) + .002
        z = max(z, min_z)
    return x, y, z


def joint_targets(tip):
    """Solución analítica (base, codo, prisma) para el extremo del marcador."""
    x, y, z = tip
    radial = math.hypot(x, y)
    length = math.hypot(radial, z - PIVOT_Z)
    extension = length - TIP_FROM_ELBOW
    if not -.00001 <= extension <= GRIPPER_LIMIT + .00001:
        raise ValueError(f"Punto fuera del alcance del brazo: {tip}")
    return math.atan2(y, x), math.atan2(radial, z - PIVOT_Z), max(0.0, extension)


def plan_strokes(paths, start=PARK):
    """Planifica contacto, retiradas y viaje lateral sin arrastrar la punta."""
    program = []
    current = start

    def travel(a, b):
        steps = max(1, math.ceil(math.dist(a, b) / .055))
        for n in range(1, steps + 1):
            ratio = n / steps
            program.append((a[0] + (b[0] - a[0]) * ratio,
                            a[1] + (b[1] - a[1]) * ratio, False, CLEARANCE))

    for path in paths:
        first = path[0]
        travel(current, first)
        for n in range(1, 6):
            program.append((*first, False, CLEARANCE * (1 - n / 5)))
        program.append((*first, True, 0.0))
        for point in path[1:]:
            program.append((*point, True, 0.0))
        current = path[-1]
        for n in range(1, 6):
            program.append((*current, False, CLEARANCE * n / 5))
    travel(current, PARK)
    return tuple(program)
