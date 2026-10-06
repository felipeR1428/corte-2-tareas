"""Regresion del reenvio UDP de Docker; sockets reales locales, sin placas."""
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "firmware"))
sys.path.insert(0, str(ROOT))
from agent import Agent
from wire import encode, MAX_PACKET
from simulador.bridge import Bridge, TelemetryStore
from test_project import Fleet, until


class ReturnProxy:
    """Reenvia las respuestas del AP cambiando IP y alternando dos puertos."""
    def __init__(self, hub):
        self.hub = hub
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.listener.bind(("127.0.0.1", 0))
        self.listener.settimeout(0.02)
        self.port = self.listener.getsockname()[1]
        self.senders = []
        for _ in range(2):
            sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sender.bind(("127.0.0.2", 0))
            self.senders.append(sender)
        self.destination = None
        self.forwarded = 0
        self.ports_used = set()
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=True)

    def run(self):
        while not self.stop.is_set():
            try:
                data, address = self.listener.recvfrom(MAX_PACKET + 1)
            except socket.timeout:
                continue
            except OSError:
                break
            if address != self.hub or self.destination is None:
                continue
            sender = self.senders[self.forwarded % len(self.senders)]
            sender.sendto(data, self.destination)
            self.ports_used.add(sender.getsockname()[1])
            self.forwarded += 1

    def start(self, destination):
        self.destination = destination
        self.thread.start()

    def close(self):
        self.stop.set()
        if self.thread.is_alive():
            self.thread.join(timeout=1)
        self.listener.close()
        for sender in self.senders:
            sender.close()


class DockerUDPTests(unittest.TestCase):
    def test_proxy_rewrites_source_and_keeps_three_node_control(self):
        with tempfile.TemporaryDirectory() as directory:
            store = TelemetryStore(directory)
            fleet = Fleet()
            proxy = ReturnProxy(fleet.hub)
            with patch.dict(os.environ, {"UDP_PROXY_IPS": "127.0.0.2"}):
                bridge = Bridge(store, fleet.hub[0], fleet.hub[1], 0,
                                bind="127.0.0.1", advertised_port=proxy.port)
            proxy.start(("127.0.0.1", bridge.port))
            bridge.start()
            try:
                self.assertTrue(until(lambda: all(n["online"] for n in store.snapshot()["nodes"])))
                bridge.issue("start")
                self.assertTrue(until(lambda: bridge.status()["confirmed"] == [1, 2, 3]))
                self.assertTrue(until(lambda: all(n.get("phase") == "ARRIVED" for n in store.snapshot()["nodes"])))
                self.assertEqual([n["best_cost"] for n in store.snapshot()["nodes"]], [40, 40, 40])
                self.assertEqual(len(set(tuple(n["route"]) for n in store.snapshot()["nodes"])), 3)
                self.assertEqual(len(proxy.ports_used), 2)
                network = bridge.network_status()
                self.assertEqual(network["proxy_ips"], ["127.0.0.2"])
                self.assertGreater(network["received"], 0)
                self.assertEqual(network["ignored"], 0)
                self.assertTrue(network["last_sender"].startswith("127.0.0.2:"))
                self.assertEqual(store.error_count, 0)
                # Un proxy autorizado tampoco puede saltarse el protocolo JSON.
                proxy.senders[0].sendto(b"{}", ("127.0.0.1", bridge.port))
                self.assertTrue(until(lambda: store.error_count == 1))
                self.assertIn("incompatible", store.last_error)
            finally:
                proxy.close()
                bridge.close()
                fleet.close()
                store.close()

    def test_native_mode_rejects_unknown_ip_and_wrong_hub_port(self):
        with tempfile.TemporaryDirectory() as directory:
            store = TelemetryStore(directory)
            hub = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            hub.bind(("127.0.0.1", 0))
            wrong_port = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            wrong_port.bind(("127.0.0.1", 0))
            unknown = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            unknown.bind(("127.0.0.2", 0))
            with patch.dict(os.environ, {"UDP_PROXY_IPS": ""}):
                bridge = Bridge(store, *hub.getsockname(), 0, bind="127.0.0.1")
            bridge.start()
            try:
                destination = ("127.0.0.1", bridge.port)
                packet = encode(Agent(1, "native-1", 123).state())
                unknown.sendto(packet, destination)
                wrong_port.sendto(packet, destination)
                hub.sendto(packet, destination)
                self.assertTrue(until(lambda: bridge.network_status()["received"] == 3 and store.packet_count == 1))
                self.assertEqual(bridge.network_status()["ignored"], 2)
                self.assertEqual(bridge.network_status()["proxy_ips"], [])
                self.assertEqual(store.packet_count, 1)
                self.assertTrue(store.snapshot()["nodes"][0]["online"])
            finally:
                bridge.close()
                hub.close()
                wrong_port.close()
                unknown.close()
                store.close()


class ProxyConfigurationTests(unittest.TestCase):
    def test_auto_detects_only_the_default_gateway_with_lowest_metric(self):
        from simulador.bridge import trusted_udp_proxies
        if sys.byteorder != "little":
            self.skipTest("Tabla /proc de ejemplo de Linux x86/ARM little endian")
        table = (
            "Iface Destination Gateway Flags RefCnt Use Metric Mask MTU Window IRTT\n"
            "eth0 00000000 010014AC 0003 0 0 100 00000000 0 0 0\n"
            "eth1 00000000 010012AC 0003 0 0 10 00000000 0 0 0\n"
            "eth2 00000000 010015AC 0002 0 0 0 00000000 0 0 0\n"
            "eth3 00000000 010016AC 0003 0 0 0 00FFFFFF 0 0 0\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            route = Path(directory) / "route"
            route.write_text(table)
            self.assertEqual(trusted_udp_proxies("auto", route), {"172.18.0.1"})

    def test_explicit_proxy_list_disabled_mode_and_invalid_ip(self):
        from simulador.bridge import trusted_udp_proxies
        self.assertEqual(trusted_udp_proxies(""), set())
        self.assertEqual(trusted_udp_proxies("172.18.0.1, 172.20.0.1"), {"172.18.0.1", "172.20.0.1"})
        with self.assertRaises(ValueError):
            trusted_udp_proxies("0.0.0.0/0")
        with self.assertRaises(ValueError):
            trusted_udp_proxies("cualquier-ip")

    def test_auto_fails_if_no_gateway_is_available(self):
        from simulador.bridge import trusted_udp_proxies
        with tempfile.TemporaryDirectory() as directory:
            route = Path(directory) / "route"
            with self.assertRaises(ValueError):
                trusted_udp_proxies("auto", route)
            route.write_text("Iface Destination Gateway Flags RefCnt Use Metric Mask\n")
            with self.assertRaises(ValueError):
                trusted_udp_proxies("auto", route)


if __name__ == "__main__":
    unittest.main(verbosity=2)
