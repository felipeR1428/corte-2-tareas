"""Datagramas JSON pequenos y deduplicacion de aportes de feromona."""
try:
    import ujson as json
except ImportError:
    import json
from world import MAP_ID, valid_path

MAX_PACKET = 1400


def encode(packet):
    if packet.get("type") == "state" and packet.get("route") and packet.get("route") == packet.get("best_path"):
        packet = dict(packet)
        del packet["route"]
        packet["route_same"] = True
    data = json.dumps(packet, separators=(",", ":")).encode("utf-8")
    if len(data) > MAX_PACKET:
        raise ValueError("Datagrama mayor de %d bytes" % MAX_PACKET)
    return data


def decode(data):
    if len(data) > MAX_PACKET:
        raise ValueError("Datagrama demasiado grande")
    packet = json.loads(data.decode("utf-8"))
    if not isinstance(packet, dict) or packet.get("v") != 1 or packet.get("map") != MAP_ID:
        raise ValueError("Version o mapa incompatible")
    if packet.get("src") not in (0, 1, 2, 3):
        raise ValueError("Nodo desconocido")
    if packet.get("type") not in ("hello", "cmd", "ack", "state", "ph", "plan"):
        raise ValueError("Tipo desconocido")
    if not isinstance(packet.get("boot"), str) or not 1 <= len(packet["boot"]) <= 40:
        raise ValueError("Sesion invalida")
    if not isinstance(packet.get("seq"), int) or packet["seq"] < 0:
        raise ValueError("Secuencia invalida")
    if packet.get("route_same") is not None:
        if (packet["type"] != "state" or packet["route_same"] is not True
                or "route" in packet or not valid_path(packet.get("best_path"))):
            raise ValueError("Referencia de ruta invalida")
        packet["route"] = list(packet["best_path"])
        del packet["route_same"]
    if packet["type"] == "plan":
        path = packet.get("path")
        if packet["src"] not in (1, 2, 3) or not valid_path(path) or packet.get("cost") != len(path) - 1:
            raise ValueError("Plan de ruta invalido")
    return packet


def envelope(src, boot, seq, kind, epoch="idle", **fields):
    result = {"v": 1, "map": MAP_ID, "src": src, "boot": boot,
              "seq": seq, "type": kind, "epoch": epoch}
    result.update(fields)
    return result


class SeenCache:
    def __init__(self, limit=96):
        self.limit = limit
        self.order = []
        self.keys = set()

    def new(self, packet):
        key = (packet["src"], packet["boot"], packet["seq"])
        if key in self.keys:
            return False
        self.keys.add(key)
        self.order.append(key)
        if len(self.order) > self.limit:
            self.keys.remove(self.order.pop(0))
        return True
