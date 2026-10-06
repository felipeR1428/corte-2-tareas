"""ACO distribuido. Este mismo archivo se carga en CADA ESP32."""
from world import ADJACENCY, EDGES, START, GOAL, coordinates, edge_key, valid_path


class Random32:
    """PRNG pequeno y reproducible sin dependencias de CPython."""
    def __init__(self, seed):
        self.state = int(seed) & 0xffffffff or 0x12345678

    def random(self):
        value = self.state
        value ^= (value << 13) & 0xffffffff
        value ^= value >> 17
        value ^= (value << 5) & 0xffffffff
        self.state = value & 0xffffffff
        return self.state / 4294967296.0


class Colony:
    def __init__(self, seed, ants=8, alpha=1.0, beta=2.0, rho=0.12, q=4.0, epsilon=0.25):
        self.rng = Random32(seed)
        self.ants = ants
        self.alpha = alpha
        self.beta = beta
        self.rho = rho
        self.q = q
        self.epsilon = epsilon
        self.tau_min = 0.05
        self.tau_max = 20.0
        self.tau = dict((edge, 1.0) for edge in EDGES)
        self.best_path = None
        self.archive = []
        self.archive_limit = 12
        self.candidates = 0
        self.share_cursor = 0
        self.local_best_cost = None
        self.iteration = 0

    def _choose(self, current, choices):
        if self.rng.random() < self.epsilon:
            return choices[min(len(choices) - 1, int(self.rng.random() * len(choices)))]
        gx, gy = coordinates(GOAL)
        weights = []
        for other in choices:
            x, y = coordinates(other)
            # Heuristica propia de este problema: cercania Manhattan a la meta.
            eta = 1.0 / (1.0 + abs(gx - x) + abs(gy - y))
            weights.append(self.tau[edge_key(current, other)] ** self.alpha * eta ** self.beta)
        target = self.rng.random() * sum(weights)
        total = 0.0
        for other, weight in zip(choices, weights):
            total += weight
            if target < total:
                return other
        return choices[-1]

    def construct(self):
        """Hormiga con seleccion ACO y retroceso en callejones sin salida.

        Una celda explorada no se vuelve a explorar por esa hormiga. El
        retroceso se elimina de la solucion final y evita ciclos infinitos.
        """
        path = [START]
        explored = set(path)
        while path:
            current = path[-1]
            if current == GOAL:
                return path
            choices = [n for n in ADJACENCY[current] if n not in explored]
            if not choices:
                path.pop()
                continue
            other = self._choose(current, choices)
            explored.add(other)
            path.append(other)
        return None

    def _deposit(self, path, amount):
        for a, b in zip(path, path[1:]):
            edge = edge_key(a, b)
            self.tau[edge] = min(self.tau_max, self.tau[edge] + amount)

    def _remember(self, path):
        if self.best_path is None or len(path) < len(self.best_path):
            self.best_path = list(path)
        if path not in self.archive:
            self.archive.append(list(path))
            self.archive.sort(key=lambda route: (len(route), tuple(route)))
            del self.archive[self.archive_limit:]

    def alternatives(self):
        """Solo alternativas del menor costo encontrado, sin rutas prefijadas."""
        if not self.archive:
            return []
        shortest = len(self.archive[0])
        return [route for route in self.archive if len(route) == shortest]

    def select_route(self, previous_routes=()):
        candidates = self.alternatives()
        if not candidates:
            return None
        used = set(edge_key(a, b) for route in previous_routes
                   for a, b in zip(route, route[1:]))
        # Primero conservar el costo minimo; entre empates, evitar repetir una
        # ruta y compartir el menor numero de aristas con los nodos anteriores.
        return list(min(candidates, key=lambda route: (
            route in previous_routes,
            sum(edge_key(a, b) in used for a, b in zip(route, route[1:])),
            tuple(route))))

    def share_path(self):
        if not self.archive:
            return None
        path = self.archive[self.share_cursor % len(self.archive)]
        self.share_cursor += 1
        return path

    def iterate(self):
        for edge in self.tau:
            self.tau[edge] = max(self.tau_min, self.tau[edge] * (1.0 - self.rho))
        winner = None
        for _ in range(self.ants):
            path = self.construct()
            if path is None:
                continue
            self.candidates += 1
            cost = len(path) - 1
            self._deposit(path, self.q / cost)
            self._remember(path)
            if self.local_best_cost is None or cost < self.local_best_cost:
                self.local_best_cost = cost
            if winner is None or len(path) < len(winner):
                winner = path
        alternatives = self.alternatives()
        if alternatives:
            elite = alternatives[self.iteration % len(alternatives)]
            self._deposit(elite, 0.5 * self.q / (len(elite) - 1))
        self.iteration += 1
        return winner

    def receive_pheromone(self, path, amount):
        # Nunca aceptar aristas que atraviesen muros ni cantidades no finitas.
        if not valid_path(path) or not isinstance(amount, (float, int)) or not 0 < amount <= self.q:
            return False
        self._deposit(path, amount)
        self._remember(path)
        return True
