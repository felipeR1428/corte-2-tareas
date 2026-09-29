"""Cifras manuscritas con curvas de Bézier en una hoja de coordenadas 0..1.

Cada recorrido continuo tiene su propia lista; cambiar de recorrido exige
levantar el marcador antes de atravesar la hoja.
"""

from __future__ import annotations


# M: punto inicial; L: recta hasta el punto; C: dos controles y punto final.
# Los extremos de las curvas adyacentes coinciden, sin cierres bruscos.
GLYPHS = {
    0: ((('M', .50, .92),
         ('C', .74, .92, .84, .76, .84, .50),
         ('C', .84, .24, .72, .08, .50, .08),
         ('C', .28, .08, .16, .24, .16, .50),
         ('C', .16, .76, .28, .92, .50, .92)),),
    1: ((('M', .25, .73),
         ('C', .33, .77, .45, .89, .51, .91),
         ('C', .54, .92, .54, .87, .54, .82),
         ('L', .54, .09)),
        (('M', .32, .09), ('L', .74, .09))),
    2: ((('M', .18, .73),
         ('C', .18, .90, .34, .93, .51, .92),
         ('C', .76, .92, .85, .78, .80, .64),
         ('C', .75, .48, .51, .30, .23, .11),
         ('C', .36, .10, .62, .09, .82, .10)),),
    3: ((('M', .19, .82),
         ('C', .41, .97, .79, .91, .78, .70),
         ('C', .77, .58, .67, .52, .50, .50),
         ('C', .68, .48, .81, .39, .79, .25),
         ('C', .77, .05, .39, .03, .18, .16)),),
    4: ((('M', .72, .91), ('L', .23, .37),
         ('C', .21, .34, .23, .32, .27, .32),
         ('L', .81, .32)),
        (('M', .72, .91), ('L', .72, .09))),
    5: ((('M', .81, .89), ('L', .29, .89),
         ('C', .27, .89, .26, .87, .26, .84),
         ('L', .23, .51),
         ('C', .40, .60, .64, .60, .76, .45),
         ('C', .91, .25, .67, .02, .42, .09),
         ('C', .31, .11, .24, .15, .19, .20)),),
    6: ((('M', .76, .88),
         ('C', .49, .96, .29, .71, .23, .43),
         ('C', .13, .11, .43, .02, .62, .12),
         ('C', .86, .25, .78, .54, .53, .54),
         ('C', .37, .54, .27, .45, .23, .36)),),
    7: ((('M', .17, .89),
         ('C', .40, .90, .67, .89, .84, .89),
         ('C', .69, .67, .49, .30, .42, .09)),),
    8: ((('M', .50, .50),
         ('C', .19, .63, .24, .92, .50, .92),
         ('C', .78, .92, .82, .63, .50, .50)),
        (('M', .50, .50),
         ('C', .18, .37, .21, .08, .50, .08),
         ('C', .81, .08, .83, .36, .50, .50))),
    9: ((('M', .77, .53),
         ('C', .74, .78, .63, .92, .45, .92),
         ('C', .20, .92, .16, .70, .28, .55),
         ('C', .40, .40, .65, .43, .77, .54),
         ('C', .80, .35, .67, .04, .42, .08),
         ('C', .34, .09, .27, .12, .23, .16)),),
}


def strokes(digit: int) -> tuple[tuple[tuple[float, float], ...], ...]:
    """Muestrea recorridos suaves; solo los extremos válidos se dibujan."""
    if digit not in GLYPHS:
        raise ValueError("Solo se admiten dígitos de 0 a 9")
    result = []
    for commands in GLYPHS[digit]:
        points = []
        for command in commands:
            if command[0] == 'M':
                points.append((command[1], command[2]))
            elif command[0] == 'L':
                start, end = points[-1], (command[1], command[2])
                steps = max(2, round(max(abs(end[0] - start[0]),
                                         abs(end[1] - start[1])) / .035))
                for n in range(1, steps + 1):
                    t = n / steps
                    points.append((start[0] * (1 - t) + end[0] * t,
                                   start[1] * (1 - t) + end[1] * t))
            elif command[0] == 'C':
                start = points[-1]
                a = (command[1], command[2])
                b = (command[3], command[4])
                end = (command[5], command[6])
                for n in range(1, 19):
                    t = n / 18
                    s = 1 - t
                    points.append((s**3 * start[0] + 3 * s*s*t * a[0] +
                                   3 * s*t*t * b[0] + t**3 * end[0],
                                   s**3 * start[1] + 3 * s*s*t * a[1] +
                                   3 * s*t*t * b[1] + t**3 * end[1]))
            else:
                raise ValueError(f"Comando de trazo desconocido: {command[0]}")
        result.append(tuple(points))
    return tuple(result)
