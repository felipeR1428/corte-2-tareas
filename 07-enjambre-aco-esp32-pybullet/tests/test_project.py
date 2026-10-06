"""Pruebas con el MISMO ACO y transporte del firmware; sin emular hardware WiFi."""
from pathlib import Path
from http.server import ThreadingHTTPServer
import io
import json
import sys
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "firmware"))
sys.path.insert(0, str(ROOT))
from aco import Colony
from agent import Agent
from transport import Transport
from wire import envelope, encode, decode, MAX_PACKET
from world import START, GOAL, valid_path, pose_on_path, coordinates
from simulador.bridge import Bridge, TelemetryStore, minimum_steps


def command(seq, action="start", epoch="ensayo", boot="test-pc"):
    return envelope(0, boot, seq, "cmd", epoch, action=action)


class Fleet:
    """Tres procesos logicos sobre sockets UDP reales de loopback."""
    def __init__(self, paused_node=None):
        self.agents = {i: Agent(i, "boot-%d" % i, 100 + i, 160, 4, 12, 4) for i in (1, 2, 3)}
        self.links = {1: Transport(1, "boot-1", port=0, bind="127.0.0.1")}
        self.hub = self.links[1].sock.getsockname()
        for i in (2, 3):
            self.links[i] = Transport(i, "boot-%d" % i, port=0, hub=self.hub, bind="127.0.0.1")
        self.paused_node = paused_node
        self.stop = threading.Event()
        self.started = time.monotonic()
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def run(self):
        previous = 0
        hello_at = -100
        state_at = -20
        while not self.stop.is_set():
            now = int((time.monotonic() - self.started) * 1000)
            dt, previous = now - previous, now
            for i in (1, 2, 3):
                if i == self.paused_node:
                    continue
                if now - hello_at >= 100:
                    self.links[i].hello(now)
                for packet in self.links[i].receive(now):
                    for reply in self.agents[i].on_packet(packet):
                        self.links[i].publish(reply, now)
                for packet in self.agents[i].advance(dt):
                    self.links[i].publish(packet, now)
                if now - state_at >= 20:
                    state = self.agents[i].state()
                    state["test_source"] = "CPython sin placas"
                    self.links[i].publish(state, now)
            if now - hello_at >= 100:
                hello_at = now
            if now - state_at >= 20:
                state_at = now
            self.stop.wait(0.002)

    def close(self):
        self.stop.set()
        self.thread.join(timeout=1)
        for link in self.links.values():
            link.close()


def until(predicate, timeout=8):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        threading.Event().wait(0.01)
    return False


class ACOTests(unittest.TestCase):
    def test_shortest_path_and_100_seeds(self):
        self.assertEqual(minimum_steps(), 40)
        candidates = set()
        for seed in range(1, 101):
            colony = Colony(seed)
            for _ in range(160):
                path = colony.iterate()
                self.assertTrue(valid_path(path))
                candidates.add(len(path) - 1)
            self.assertEqual(len(colony.best_path) - 1, 40)
            self.assertTrue(all(colony.tau_min <= value <= colony.tau_max for value in colony.tau.values()))
        self.assertEqual(min(candidates), 40)
        self.assertGreater(max(candidates), 40)

    def test_shared_pheromone_dedup_epoch_and_validation(self):
        agent = Agent(2, "boot-2", 2)
        agent.on_packet(command(1))
        path = Colony(1).construct()
        packet = envelope(1, "boot-1", 50, "ph", "ensayo", path=path, amount=0.2)
        before = dict(agent.colony.tau)
        agent.on_packet(decode(encode(packet)))
        after = dict(agent.colony.tau)
        self.assertNotEqual(before, after)
        agent.on_packet(packet)
        self.assertEqual(after, agent.colony.tau)
        self.assertEqual(agent.shared_rx, 1)
        old = dict(packet, epoch="otro-ensayo", seq=51)
        agent.on_packet(old)
        self.assertEqual(after, agent.colony.tau)
        invalid = dict(packet, path=[START, GOAL], seq=52)
        agent.on_packet(invalid)
        self.assertEqual(after, agent.colony.tau)
        self.assertFalse(agent.colony.receive_pheromone(path, float("nan")))

    def test_pause_reset_and_reordered_controls(self):
        agent = Agent(1, "boot-1", 1, 2, 1, 1)
        agent.on_packet(command(1))
        agent.advance(20)
        agent.on_packet(command(2, "pause"))
        iteration = agent.colony.iteration
        progress = agent.progress
        agent.advance(100)
        self.assertEqual(agent.colony.iteration, iteration)
        self.assertEqual(agent.progress, progress)
        agent.on_packet(command(1))
        self.assertFalse(agent.running)
        agent.on_packet(command(3, "reset", "nuevo"))
        self.assertEqual(agent.colony.iteration, 0)
        self.assertEqual(agent.phase, "IDLE")
        agent.on_packet(command(4, "start", "nuevo"))
        for _ in range(40):
            agent.advance(250)
        self.assertEqual(agent.phase, "ARRIVED")
        state = agent.state()
        self.assertEqual((state["x"], state["y"]), coordinates(GOAL))
        self.assertLessEqual(len(encode(state)), MAX_PACKET)


class UDPTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.store = TelemetryStore(self.directory.name)
        self.fleet = Fleet()
        self.bridge = Bridge(self.store, self.fleet.hub[0], self.fleet.hub[1], 0, bind="127.0.0.1")
        self.bridge.start()

    def tearDown(self):
        self.bridge.close()
        self.fleet.close()
        self.store.close()
        self.directory.cleanup()

    def test_three_nodes_real_udp_aco_and_arrival(self):
        self.assertTrue(until(lambda: all(n["online"] for n in self.store.snapshot()["nodes"])))
        self.bridge.issue("start")
        self.assertTrue(until(lambda: len(self.bridge.status()["confirmed"]) == 3))
        self.assertTrue(until(lambda: all(n.get("phase") == "ARRIVED" for n in self.store.snapshot()["nodes"])))
        nodes = self.store.snapshot()["nodes"]
        self.assertEqual([n["best_cost"] for n in nodes], [40, 40, 40])
        self.assertTrue(all(n["shared_rx"] > 0 and n["shared_tx"] > 0 for n in nodes))
        self.assertTrue(all(n["local_cost"] == 40 for n in nodes))
        self.assertTrue(all(n["route_cost"] == 40 for n in nodes))
        self.assertEqual(len(set(tuple(n["route"]) for n in nodes)), 3)
        self.assertTrue(all(n["alternatives"] >= 3 for n in nodes))
        self.assertEqual(self.store.error_count, 0)
        self.assertGreater(len(self.store.export().splitlines()), 10)
        self.bridge.issue("reset")
        self.assertTrue(until(lambda: all(n.get("phase") == "IDLE" for n in self.store.snapshot()["nodes"])))
        self.assertTrue(all(n["best_cost"] is None for n in self.store.snapshot()["nodes"]))

    def test_lost_command_retry_and_late_node(self):
        self.fleet.paused_node = 3
        original = self.bridge.send
        dropped = []

        def drop_first(packet):
            if packet["type"] == "cmd" and not dropped:
                dropped.append(packet)
                return
            original(packet)

        self.bridge.send = drop_first
        self.bridge.issue("start")
        self.assertTrue(until(lambda: 1 in self.bridge.status()["confirmed"] and 2 in self.bridge.status()["confirmed"]))
        self.assertEqual(len(dropped), 1)
        self.fleet.paused_node = None
        self.assertTrue(until(lambda: len(self.bridge.status()["confirmed"]) == 3))
        self.assertTrue(until(lambda: self.fleet.agents[3].phase == "ARRIVED"))
        self.assertEqual(self.fleet.agents[3].epoch, self.bridge.control_epoch)

    def test_monitor_rejects_position_and_old_sequence(self):
        agent = Agent(1, "extra", 9)
        packet = agent.state()
        self.store.set_epoch("idle")
        self.assertTrue(self.store.accept(packet))
        self.assertFalse(self.store.accept(packet))
        invalid = dict(agent.state(), x=500)
        self.assertFalse(self.store.accept(invalid))


class PyBulletTests(unittest.TestCase):
    def test_render_and_no_wall_intersection(self):
        from simulador.scene import Scene
        scene = Scene(640, 400)
        try:
            colony = Colony(20)
            for _ in range(160):
                colony.iterate()
            route = colony.best_path
            for step in range(0, 4 * (len(route) - 1) + 1):
                progress = step / 4
                nodes = [{"src": i, "boot": "render", "epoch": "test", "route": route,
                          "best_path": route, "progress": progress, "online": True} for i in (1, 2, 3)]
                scene.update({"nodes": nodes}, 1)
                self.assertEqual(scene.wall_collisions(), [])
            frame = scene.render()
            self.assertEqual(frame[:2], b"\xff\xd8")
            self.assertGreater(len(frame), 10000)
            scene.configure_camera("orbit", dx=40, dy=10)
            self.assertNotEqual(frame, scene.render())
        finally:
            scene.close()


class HTTPTests(unittest.TestCase):
    def test_http_control_three_nodes_and_export(self):
        from simulador.app import Runtime, handler_for
        from PIL import Image
        fleet = Fleet()
        with tempfile.TemporaryDirectory() as directory:
            options = SimpleNamespace(data=directory, hub=fleet.hub[0], hub_port=fleet.hub[1],
                                      udp_port=0, reply_port=0, width=960, height=600)
            runtime = Runtime(options)
            server = ThreadingHTTPServer(("127.0.0.1", 0), handler_for(runtime))
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            runtime.start()
            base = "http://127.0.0.1:%d" % server.server_port

            def get(path):
                with urllib.request.urlopen(base + path, timeout=3) as response:
                    return response.read()

            def post(action):
                request = urllib.request.Request(base + "/api/command", data=json.dumps({"action": action}).encode(),
                                                 headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(request, timeout=3) as response:
                    return json.loads(response.read())

            try:
                self.assertIn("Un enjambre".encode(), get("/"))
                self.assertTrue(until(lambda: all(n["online"] for n in runtime.snapshot()["nodes"])))
                post("start")
                self.assertTrue(until(lambda: all(n.get("phase") == "ARRIVED" for n in runtime.snapshot()["nodes"]), timeout=20))
                state = json.loads(get("/api/state"))
                self.assertEqual(state["source"], "PRUEBA_LOCAL")
                self.assertEqual(state["control"]["confirmed"], [1, 2, 3])
                self.assertTrue(all(n["optimal"] for n in state["nodes"]))
                self.assertEqual(state["distinct_routes"], 3)
                self.assertEqual(state["maze"]["minimum_routes"], 23)
                self.assertTrue(all(n["saved_paths"] for n in state["nodes"]))
                self.assertEqual(json.loads(get("/health")), {"ok": True})
                frame = get("/frame.jpg")
                self.assertEqual(Image.open(io.BytesIO(frame)).size, (960, 600))
                self.assertIn(b"PRUEBA_LOCAL", get("/api/export"))
                self.assertEqual(state["errors"], 0)
                self.assertGreater(state["udp"]["received"], 0)
                self.assertEqual(state["udp"]["ignored"], 0)
                self.assertEqual(state["udp"]["proxy_ips"], [])
            finally:
                server.shutdown()
                server.server_close()
                worker.join(timeout=1)
                runtime.close()
                fleet.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
