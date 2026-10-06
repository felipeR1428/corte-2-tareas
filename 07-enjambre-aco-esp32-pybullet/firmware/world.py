"""Mapa compartido por los ESP32 y el gemelo digital; compatible con MicroPython."""

MAP_ID = "almacen-21x17-multirruta-v3"
GRID = (
    "#####################",
    "#A#...........#.....#",
    "#.#####.#.#.#.###.#.#",
    "#.....#.#.#.......#.#",
    "#####.###.#.###.#.#.#",
    "#.........#.#...#...#",
    "#.#.#######.###.#.###",
    "#.....#.....#...#...#",
    "#.#####.#.#.#.#.#.#.#",
    "#...................#",
    "#.###.#.#.#.#.###.#.#",
    "#.........#.....#...#",
    "###.#.#.#.#.###.#.#.#",
    "#...#.#.......#.#...#",
    "#.###.#.#.#.###.#.#.#",
    "#.....#.#.........#G#",
    "#####################",
)
WIDTH = len(GRID[0])
HEIGHT = len(GRID)
CELL_METERS = 0.50


def cell_id(x, y):
    return y * WIDTH + x


def coordinates(node):
    return node % WIDTH, node // WIDTH


def free(node):
    if not isinstance(node, int) or node < 0 or node >= WIDTH * HEIGHT:
        return False
    x, y = coordinates(node)
    return GRID[y][x] != "#"


FREE_CELLS = [i for i in range(WIDTH * HEIGHT) if free(i)]
START = next(i for i in FREE_CELLS if GRID[i // WIDTH][i % WIDTH] == "A")
GOAL = next(i for i in FREE_CELLS if GRID[i // WIDTH][i % WIDTH] == "G")


def neighbors(node):
    x, y = coordinates(node)
    result = []
    for nx, ny in ((x + 1, y), (x, y + 1), (x - 1, y), (x, y - 1)):
        if 0 <= nx < WIDTH and 0 <= ny < HEIGHT:
            other = cell_id(nx, ny)
            if free(other):
                result.append(other)
    return result


ADJACENCY = dict((i, neighbors(i)) for i in FREE_CELLS)


def edge_key(a, b):
    return (a, b) if a < b else (b, a)


EDGES = [edge_key(a, b) for a in FREE_CELLS for b in ADJACENCY[a] if a < b]


def valid_path(path):
    if not isinstance(path, (list, tuple)) or not 2 <= len(path) <= len(FREE_CELLS):
        return False
    if path[0] != START or path[-1] != GOAL:
        return False
    visited = set()
    for index, node in enumerate(path):
        if not free(node) or node in visited:
            return False
        if index and node not in ADJACENCY[path[index - 1]]:
            return False
        visited.add(node)
    return True


def pose_on_path(path, progress):
    """Posicion en celdas, sin cortar las esquinas del laberinto."""
    import math
    if not path:
        x, y = coordinates(START)
        return float(x), float(y), 0.0
    progress = max(0.0, min(float(progress), len(path) - 1.0))
    index = min(int(progress), len(path) - 2)
    fraction = progress - index
    x1, y1 = coordinates(path[index])
    x2, y2 = coordinates(path[index + 1])
    return x1 + (x2 - x1) * fraction, y1 + (y2 - y1) * fraction, math.atan2(y2 - y1, x2 - x1)
