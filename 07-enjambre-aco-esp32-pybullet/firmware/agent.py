"""Maquina de estados del nodo: buscar, recorrer, pausar y llegar."""
from aco import Colony
from world import START, coordinates, pose_on_path, valid_path
from wire import SeenCache, envelope


class Agent:
    def __init__(self, node_id, boot, seed, max_iterations=160,
                 iteration_ms=100, edge_ms=650, share_every=4):
        self.node_id = node_id
        self.boot = boot
        self.seed = seed
        self.max_iterations = max_iterations
        self.iteration_ms = iteration_ms
        self.edge_ms = edge_ms
        self.share_every = share_every
        self.seq = 0
        self.epoch = "idle"
        self.last_control = None
        self.seen = SeenCache()
        self.reset()

    def reset(self, epoch=None):
        if epoch is not None:
            self.epoch = epoch
        mixed = self.seed ^ (self.node_id * 0x9e3779b9)
        for character in self.epoch:
            mixed = ((mixed ^ ord(character)) * 16777619) & 0xffffffff
        self.colony = Colony(mixed)
        self.running = False
        self.phase = "IDLE"
        self.route = []
        self.progress = 0.0
        self.search_ms = 0
        self.elapsed_ms = 0
        self.shared_rx = 0
        self.shared_tx = 0
        self.seen = SeenCache()
        self.peer_routes = {}
        self.peer_plans = {}
        self.selection_ms = 0
        self.settle_ms = 0
        self.plan_ms = 0

    def packet(self, kind, **fields):
        self.seq += 1
        return envelope(self.node_id, self.boot, self.seq, kind, self.epoch, **fields)

    def on_packet(self, packet):
        outgoing = []
        if packet["type"] == "cmd" and packet["src"] == 0:
            action = packet.get("action")
            epoch = packet.get("epoch")
            if action not in ("start", "pause", "reset") or not isinstance(epoch, str) or len(epoch) > 40:
                return outgoing
            control = (packet["boot"], packet["seq"])
            if self.last_control and control[0] == self.last_control[0] and control[1] < self.last_control[1]:
                return outgoing
            if control != self.last_control:
                if epoch != self.epoch or action == "reset":
                    self.reset(epoch)
                self.running = action == "start"
                if self.running and self.phase == "IDLE":
                    self.phase = "SEARCHING"
                self.last_control = control
            # Tambien confirmar los reintentos sin ejecutar dos veces el reset.
            outgoing.append(self.packet("ack", cmd_boot=packet["boot"], cmd_seq=packet["seq"], action=action))
        elif (packet["type"] == "ph" and packet["src"] in (1, 2, 3)
              and packet["src"] != self.node_id and packet.get("epoch") == self.epoch):
            if self.seen.new(packet) and self.colony.receive_pheromone(packet.get("path"), packet.get("amount")):
                self.shared_rx += 1
        elif (packet["type"] == "plan" and packet["src"] in (1, 2, 3)
              and packet["src"] != self.node_id and packet.get("epoch") == self.epoch):
            src = packet["src"]
            identity = (packet["boot"], packet["seq"])
            old = self.peer_plans.get(src)
            path = packet.get("path")
            if (not valid_path(path) or packet.get("cost") != len(path) - 1
                    or (old and old[0] == identity[0] and old[1] >= identity[1])):
                return outgoing
            self.peer_plans[src] = identity
            self.peer_routes[src] = list(path)
            self.colony._remember(path)
        return outgoing

    def plan(self):
        return self.packet("plan", path=self.route, cost=len(self.route) - 1)

    def select(self, dt_ms):
        self.selection_ms += dt_ms
        lower = [i for i in (1, 2, 3) if i < self.node_id]
        if not all(i in self.peer_routes for i in lower) and self.selection_ms < 2500:
            return []
        previous = [self.peer_routes[i] for i in lower if i in self.peer_routes]
        proposed = self.colony.select_route(previous)
        if proposed is None:
            self.phase = "NO_PATH"
            self.running = False
            return []
        outgoing = []
        if (not self.route or len(proposed) < len(self.route)
                or (self.route in previous and proposed not in previous)):
            self.route = proposed
            self.colony.best_path = list(proposed)
            self.settle_ms = 0
            self.plan_ms = 0
            outgoing.append(self.plan())
        self.settle_ms += dt_ms
        all_planned = all(i in self.peer_routes for i in (1, 2, 3) if i != self.node_id)
        if self.settle_ms >= 600 and (all_planned or self.selection_ms >= 4000):
            self.phase = "MOVING"
        return outgoing

    def advance(self, dt_ms):
        outgoing = []
        dt_ms = max(0, min(int(dt_ms), 250))
        if self.route:
            self.plan_ms += dt_ms
            if self.plan_ms >= 500:
                self.plan_ms = 0
                outgoing.append(self.plan())
        if not self.running or self.phase in ("IDLE", "ARRIVED", "NO_PATH"):
            return outgoing
        self.elapsed_ms += dt_ms
        if self.phase == "SEARCHING":
            self.search_ms += dt_ms
            if self.search_ms < self.iteration_ms:
                return outgoing
            self.search_ms -= self.iteration_ms
            self.colony.iterate()
            winner = self.colony.share_path() if self.colony.iteration % self.share_every == 0 else None
            if winner is not None and self.colony.iteration % self.share_every == 0:
                self.shared_tx += 1
                outgoing.append(self.packet("ph", path=winner,
                                            amount=round(self.colony.q / (len(winner) - 1), 6),
                                            iteration=self.colony.iteration))
            if self.colony.iteration >= self.max_iterations:
                if self.colony.best_path is None:
                    self.phase = "NO_PATH"
                    self.running = False
                else:
                    self.phase = "SELECTING"
        elif self.phase == "SELECTING":
            outgoing.extend(self.select(dt_ms))
        elif self.phase == "MOVING":
            self.progress = min(len(self.route) - 1.0, self.progress + dt_ms / self.edge_ms)
            if self.progress >= len(self.route) - 1:
                self.phase = "ARRIVED"
                self.running = False
        return outgoing

    def state(self):
        if self.route:
            x, y, heading = pose_on_path(self.route, self.progress)
        else:
            x, y = coordinates(START)
            heading = 0.0
        best = self.colony.best_path or []
        return self.packet("state", phase=self.phase, running=self.running,
                           iteration=self.colony.iteration, max_iterations=self.max_iterations,
                           best_cost=len(best) - 1 if best else None,
                           local_cost=self.colony.local_best_cost,
                           best_path=best, route=self.route,
                           progress=round(self.progress, 4),
                           x=round(x, 4), y=round(y, 4), heading=round(heading, 4),
                           shared_rx=self.shared_rx, shared_tx=self.shared_tx,
                           alternatives=len(self.colony.alternatives()),
                           archive_size=len(self.colony.archive),
                           candidates=self.colony.candidates,
                           elapsed_ms=self.elapsed_ms)
