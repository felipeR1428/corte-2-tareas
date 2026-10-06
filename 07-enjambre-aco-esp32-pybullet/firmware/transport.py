"""Transporte UDP: nodo 1 reenvia; no calcula rutas para los otros nodos."""
import socket
from wire import encode, decode, envelope, MAX_PACKET


class Transport:
    def __init__(self, node_id, boot, port=4210, hub=("192.168.4.1", 4210), bind="0.0.0.0"):
        self.node_id = node_id
        self.boot = boot
        self.hub = hub
        self.port = port
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setblocking(False)
        self.sock.bind((bind, port))
        self.peers = {}
        self.last_cmd = None
        self.states = {}
        self.plans = {}
        self.errors = 0
        self.hello_seq = 0

    def _send(self, packet, address):
        try:
            self.sock.sendto(encode(packet), address)
        except (OSError, ValueError):
            self.errors += 1

    def _fanout(self, packet, now_ms, exclude=None):
        for src in list(self.peers):
            address, seen_at = self.peers[src]
            if now_ms - seen_at > 7000:
                del self.peers[src]
                continue
            # Solo aportes ACO y control para los ESP; telemetria para el PC.
            if src != exclude and (src == 0 or packet["type"] in ("ph", "cmd", "plan")):
                self._send(packet, address)

    def hello(self, now_ms):
        if self.node_id == 1:
            return
        self.hello_seq += 1
        self._send(envelope(self.node_id, self.boot, self.hello_seq, "hello"), self.hub)

    def publish(self, packet, now_ms):
        if self.node_id == 1:
            if packet["type"] == "state":
                self.states[1] = packet
            elif packet["type"] == "plan":
                self.plans[1] = packet
            self._fanout(packet, now_ms)
        else:
            self._send(packet, self.hub)

    def receive(self, now_ms):
        result = []
        for _ in range(24):
            try:
                data, address = self.sock.recvfrom(MAX_PACKET + 1)
            except OSError:
                break
            try:
                packet = decode(data)
            except (ValueError, TypeError, KeyError):
                self.errors += 1
                continue
            src = packet["src"]
            if self.node_id == 1:
                if src == 1:
                    continue
                if packet["type"] == "hello":
                    reply_port = packet.get("reply_port", address[1]) if src == 0 else address[1]
                    if not isinstance(reply_port, int) or not 1 <= reply_port <= 65535:
                        continue
                    self.peers[src] = ((address[0], reply_port), now_ms)
                    if src == 0:
                        for state in self.states.values():
                            self._send(state, self.peers[0][0])
                    elif self.last_cmd is not None:
                        self._send(self.last_cmd, address)
                        for plan in self.plans.values():
                            self._send(plan, address)
                    continue
                if src not in self.peers or self.peers[src][0][0] != address[0]:
                    continue
                self.peers[src] = (self.peers[src][0], now_ms)
                if packet["type"] == "cmd":
                    if src != 0:
                        continue
                    # Un comando viejo reordenado no debe sustituir al actual.
                    if self.last_cmd and packet["boot"] == self.last_cmd["boot"] and packet["seq"] < self.last_cmd["seq"]:
                        continue
                    if (not self.last_cmd or packet["epoch"] != self.last_cmd["epoch"]
                            or packet.get("action") == "reset"):
                        self.plans = {}
                    self.last_cmd = packet
                if packet["type"] == "state" and src in (2, 3):
                    self.states[src] = packet
                elif packet["type"] == "plan" and src in (2, 3):
                    old = self.plans.get(src)
                    if old and packet["boot"] == old["boot"] and packet["seq"] <= old["seq"]:
                        continue
                    if self.last_cmd and packet["epoch"] != self.last_cmd["epoch"]:
                        continue
                    self.plans[src] = packet
                self._fanout(packet, now_ms, exclude=src)
            else:
                # En el nodo cliente todos los mensajes llegan a traves del AP.
                if address[0] != self.hub[0] or address[1] != self.hub[1]:
                    continue
            result.append(packet)
        return result

    def close(self):
        self.sock.close()
