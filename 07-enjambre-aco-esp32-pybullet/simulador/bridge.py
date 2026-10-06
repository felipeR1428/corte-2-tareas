import csv
import ipaddress
import math
import os
from pathlib import Path
import socket
import struct
import sys
import threading
import time
import uuid
from collections import deque

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "firmware"))
from world import ADJACENCY, START, GOAL, GRID, MAP_ID, CELL_METERS, valid_path, pose_on_path
from wire import decode, encode, envelope, MAX_PACKET


def trusted_udp_proxies(value=None, route_path="/proc/net/route"):
    """Proxy permitido por configuracion; auto usa el gateway del contenedor."""
    value = os.getenv("UDP_PROXY_IPS", "") if value is None else value
    value = value.strip()
    if not value:
        return frozenset()
    if value.lower() != "auto":
        return frozenset(str(ipaddress.IPv4Address(ip.strip())) for ip in value.split(","))
    try:
        lines = Path(route_path).read_text().splitlines()
    except OSError as exc:
        raise ValueError("No se pudo detectar el proxy Docker; define UDP_PROXY_IPS con su IPv4") from exc
    gateways = []
    for line in lines:
        fields = line.split()
        if len(fields) < 8 or fields[1] != "00000000" or fields[7] != "00000000":
            continue
        try:
            if int(fields[3], 16) & 3 != 3:
                continue
            gateway = socket.inet_ntoa(struct.pack("=I", int(fields[2], 16)))
            if gateway != "0.0.0.0":
                gateways.append((int(fields[6]), gateway))
        except (ValueError, struct.error):
            continue
    if not gateways:
        raise ValueError("No hay gateway IPv4; define UDP_PROXY_IPS con la IPv4 del proxy Docker")
    return frozenset((min(gateways)[1],))


def minimum_steps():
    """BFS SOLO para verificar el resultado; jamas se transmite una ruta al ESP."""
    queue = deque([(START, 0)])
    seen = {START}
    while queue:
        current, cost = queue.popleft()
        if current == GOAL:
            return cost
        for other in ADJACENCY[current]:
            if other not in seen:
                seen.add(other)
                queue.append((other, cost + 1))
    return None


def maze_metrics():
    distances = {START: 0}
    counts = {START: 1}
    queue = deque([START])
    while queue:
        current = queue.popleft()
        for other in ADJACENCY[current]:
            if other not in distances:
                distances[other] = distances[current] + 1
                counts[other] = counts[current]
                queue.append(other)
            elif distances[other] == distances[current] + 1:
                counts[other] += counts[current]
    degrees = [len(neighbors) for neighbors in ADJACENCY.values()]
    edges = sum(degrees) // 2
    return {"width": len(GRID[0]), "height": len(GRID),
            "free_cells": len(ADJACENCY), "edges": edges,
            "junctions": sum(d >= 3 for d in degrees),
            "dead_ends": sum(d == 1 for d in degrees),
            "cycles": edges - len(ADJACENCY) + 1,
            "minimum_routes": counts.get(GOAL, 0)}


class TelemetryStore:
    columns = ("rx_utc", "epoch", "node", "boot", "seq", "phase", "running",
               "iteration", "best_steps", "local_steps", "x_cells", "y_cells",
               "progress", "shared_tx", "shared_rx", "elapsed_ms", "source",
               "alternatives", "archive_size", "candidates", "route_steps")

    def __init__(self, data_dir):
        self.lock = threading.RLock()
        self.nodes = {}
        self.history = {i: [] for i in (1, 2, 3)}
        self.alternatives = {i: [] for i in (1, 2, 3)}
        self.maze = maze_metrics()
        self.expected_epoch = None
        self.optimum = minimum_steps()
        self.packet_count = 0
        self.error_count = 0
        self.last_error = ""
        self.path = Path(data_dir) / "telemetria_multirruta_v3.csv"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        new = not self.path.exists() or self.path.stat().st_size == 0
        self.file = self.path.open("a", newline="", encoding="utf-8")
        self.writer = csv.writer(self.file)
        if new:
            self.writer.writerow(self.columns)
            self.file.flush()

    def error(self, text):
        with self.lock:
            self.error_count += 1
            self.last_error = str(text)[:180]

    def set_epoch(self, epoch):
        with self.lock:
            self.expected_epoch = epoch
            self.nodes = {}
            self.history = {i: [] for i in (1, 2, 3)}
            self.alternatives = {i: [] for i in (1, 2, 3)}

    def remember_alternative(self, node, path):
        if not path or not valid_path(path):
            return
        paths = self.alternatives[node]
        if path not in paths:
            paths.append(list(path))
            paths.sort(key=lambda route: (len(route), tuple(route)))
            del paths[12:]

    def accept(self, packet, now=None):
        now = time.monotonic() if now is None else now
        with self.lock:
            self.packet_count += 1
            if packet["src"] not in (1, 2, 3):
                return False
            if self.expected_epoch is not None and packet.get("epoch") != self.expected_epoch:
                return False
            if packet["type"] in ("ph", "plan"):
                if packet["type"] == "ph":
                    amount = packet.get("amount")
                    if not isinstance(amount, (int, float)) or not math.isfinite(amount) or not 0 < amount <= 4.0:
                        return False
                self.remember_alternative(packet["src"], packet.get("path"))
                return False
            if packet["type"] != "state":
                return False
            node = packet["src"]
            previous = self.nodes.get(node)
            if previous and packet["boot"] == previous["boot"] and packet["seq"] <= previous["seq"]:
                return False
            best = packet.get("best_path", [])
            route = packet.get("route", [])
            if best and (not valid_path(best) or packet.get("best_cost") != len(best) - 1):
                self.error("Ruta o costo invalido del nodo %s" % node)
                return False
            if not best and packet.get("best_cost") is not None:
                self.error("Costo sin ruta del nodo %s" % node)
                return False
            if route and not valid_path(route):
                self.error("Recorrido invalido del nodo %s" % node)
                return False
            if packet.get("phase") not in ("IDLE", "SEARCHING", "SELECTING", "MOVING", "ARRIVED", "NO_PATH"):
                return False
            if packet.get("phase") in ("MOVING", "ARRIVED") and not route:
                return False
            progress = packet.get("progress", 0)
            if not isinstance(progress, (int, float)) or not math.isfinite(progress) or not 0 <= progress <= max(0, len(route) - 1):
                return False
            x, y, _ = pose_on_path(route, progress)
            for value, expected in ((packet.get("x"), x), (packet.get("y"), y)):
                if not isinstance(value, (int, float)) or not math.isfinite(value) or abs(value - expected) > 0.02:
                    self.error("Posicion incompatible con la ruta del nodo %s" % node)
                    return False
            state = dict(packet)
            state["received_at"] = now
            self.nodes[node] = state
            self.remember_alternative(node, best)
            self.remember_alternative(node, route)
            points = self.history[node]
            if best and (not points or points[-1][0] != packet["iteration"] or points[-1][1] != packet["best_cost"]):
                points.append([packet["iteration"], packet["best_cost"]])
                if len(points) > 200:
                    del points[0]
            self.writer.writerow([
                time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), packet["epoch"], node,
                packet["boot"], packet["seq"], packet["phase"], packet["running"],
                packet["iteration"], packet["best_cost"], packet["local_cost"],
                packet["x"], packet["y"], progress, packet["shared_tx"],
                packet["shared_rx"], packet["elapsed_ms"],
                "PRUEBA_LOCAL" if packet.get("test_source") else "ESP32",
                packet.get("alternatives", 0), packet.get("archive_size", 0),
                packet.get("candidates", 0), len(route) - 1 if route else "",
            ])
            self.file.flush()
            return True

    def snapshot(self):
        with self.lock:
            now = time.monotonic()
            nodes = []
            for node in (1, 2, 3):
                state = dict(self.nodes.get(node, {"src": node}))
                age = now - state.pop("received_at", now - 1000)
                state["online"] = age < 3.0
                state["age_s"] = round(age, 1) if age < 100 else None
                state["optimal"] = state.get("best_cost") == self.optimum and self.optimum is not None
                route = state.get("route", [])
                state["route_cost"] = len(route) - 1 if route else None
                state["history"] = list(self.history[node])
                state["saved_paths"] = [list(path) for path in self.alternatives[node]]
                nodes.append(state)
            return {"nodes": nodes, "grid": GRID, "map_id": MAP_ID,
                    "cell_meters": CELL_METERS, "optimal_steps": self.optimum,
                    "optimal_meters": self.optimum * CELL_METERS if self.optimum is not None else None,
                    "packets": self.packet_count, "errors": self.error_count,
                    "last_error": self.last_error, "epoch": self.expected_epoch,
                    "source": "PRUEBA_LOCAL" if any(n.get("test_source") for n in nodes) else "ESP32",
                    "twin": "cinematico", "maze": dict(self.maze),
                    "distinct_routes": len(set(tuple(n["route"]) for n in nodes if n.get("route")))}

    def export(self):
        with self.lock:
            self.file.flush()
            return self.path.read_bytes()

    def close(self):
        with self.lock:
            self.file.close()


class Bridge:
    def __init__(self, store, hub_ip="192.168.4.1", hub_port=4210, listen_port=4211,
                 bind="0.0.0.0", advertised_port=None, proxy_ips=None):
        self.store = store
        self.hub = (hub_ip, hub_port)
        self.proxy_ips = trusted_udp_proxies(proxy_ips)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind((bind, listen_port))
        self.sock.settimeout(0.05)
        self.port = self.sock.getsockname()[1]
        self.advertised_port = advertised_port or self.port
        self.boot = "pc-" + uuid.uuid4().hex[:16]
        self.seq = 0
        self.lock = threading.RLock()
        self.received_datagrams = 0
        self.ignored_datagrams = 0
        self.last_sender = ""
        self.pending = None
        self.control_epoch = None
        self.stopped = threading.Event()
        self.thread = None

    def sender_is_allowed(self, address):
        # El AP directo conserva su puerto. Docker puede cambiar IP y puerto.
        if address[0] == self.hub[0]:
            return address[1] == self.hub[1]
        return address[0] in self.proxy_ips

    def network_status(self):
        with self.lock:
            return {"hub": "%s:%s" % self.hub, "listen_port": self.port,
                    "reply_port": self.advertised_port,
                    "proxy_ips": sorted(self.proxy_ips),
                    "received": self.received_datagrams,
                    "ignored": self.ignored_datagrams,
                    "last_sender": self.last_sender}

    def packet(self, kind, **fields):
        with self.lock:
            self.seq += 1
            return envelope(0, self.boot, self.seq, kind, self.control_epoch or "idle", **fields)

    def send(self, packet):
        try:
            self.sock.sendto(encode(packet), self.hub)
        except (OSError, ValueError) as exc:
            if not self.stopped.is_set():
                self.store.error("UDP: " + str(exc))

    def hello(self):
        self.send(self.packet("hello", reply_port=self.advertised_port))

    def issue(self, action):
        if action not in ("start", "pause", "reset"):
            raise ValueError("Accion invalida")
        with self.lock:
            if self.control_epoch is None or action == "reset":
                self.control_epoch = uuid.uuid4().hex[:12]
                self.store.set_epoch(self.control_epoch)
            packet = self.packet("cmd", action=action)
            self.pending = {"packet": packet, "acks": set(), "created": time.monotonic(), "last_sent": 0.0}
            # Registrar el puerto de vuelta antes del primer comando.
            self.hello()
            self.send(packet)
            self.pending["last_sent"] = time.monotonic()
            return {"action": action, "epoch": self.control_epoch, "seq": packet["seq"]}

    def status(self):
        with self.lock:
            if not self.pending:
                return {"action": "none", "confirmed": [], "pending": [], "timed_out": False}
            waiting = [i for i in (1, 2, 3) if i not in self.pending["acks"]]
            return {"action": self.pending["packet"]["action"],
                    "confirmed": sorted(self.pending["acks"]), "pending": waiting,
                    "timed_out": bool(waiting) and time.monotonic() - self.pending["created"] > 10,
                    "hub": "%s:%s" % self.hub, "listen_port": self.port}

    def run(self):
        hello_at = 0.0
        while not self.stopped.is_set():
            now = time.monotonic()
            if now - hello_at > 1.0:
                self.hello()
                hello_at = now
            with self.lock:
                pending = self.pending
                if pending and len(pending["acks"]) < 3 and now - pending["created"] < 10 and now - pending["last_sent"] > 0.4:
                    self.send(pending["packet"])
                    pending["last_sent"] = now
            try:
                data, address = self.sock.recvfrom(MAX_PACKET + 1)
            except socket.timeout:
                continue
            except OSError:
                break
            allowed = self.sender_is_allowed(address)
            with self.lock:
                self.received_datagrams += 1
                self.last_sender = "%s:%s" % address
                if not allowed:
                    self.ignored_datagrams += 1
            if not allowed:
                continue
            try:
                packet = decode(data)
            except (ValueError, TypeError, KeyError) as exc:
                self.store.error("Paquete descartado: " + str(exc))
                continue
            if packet["type"] == "ack":
                with self.lock:
                    if self.pending and packet["src"] in (1, 2, 3) and packet.get("cmd_boot") == self.boot and packet.get("cmd_seq") == self.pending["packet"]["seq"]:
                        self.pending["acks"].add(packet["src"])
            try:
                self.store.accept(packet)
            except (ValueError, TypeError, KeyError) as exc:
                self.store.error("Telemetria descartada: " + str(exc))

    def start(self):
        self.thread = threading.Thread(target=self.run, name="UDP-ESP32", daemon=True)
        self.thread.start()

    def close(self):
        self.stopped.set()
        self.sock.close()
        if self.thread:
            self.thread.join(timeout=1)
