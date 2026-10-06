"""Topologia, coordinacion y limites de los mensajes del laberinto V3."""
from collections import deque
import json
import sys
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "firmware"))
sys.path.insert(0, str(ROOT))
from aco import Colony
from agent import Agent
from world import ADJACENCY, FREE_CELLS, START, GOAL, GRID, valid_path, pose_on_path
from wire import MAX_PACKET, envelope, encode, decode
from simulador.bridge import maze_metrics, minimum_steps


def shortest_paths():
    distances = {GOAL: 0}
    queue = deque([GOAL])
    while queue:
        current = queue.popleft()
        for other in ADJACENCY[current]:
            if other not in distances:
                distances[other] = distances[current] + 1
                queue.append(other)
    paths = []
    stack = [[START]]
    while stack:
        path = stack.pop()
        if path[-1] == GOAL:
            paths.append(path)
        else:
            for other in ADJACENCY[path[-1]]:
                if distances[other] == distances[path[-1]] - 1:
                    stack.append(path + [other])
    return paths


class MultirouteTests(unittest.TestCase):
    def test_maze_has_23_real_minimum_routes_and_dead_ends(self):
        paths = shortest_paths()
        self.assertEqual((len(GRID[0]), len(GRID)), (21, 17))
        self.assertEqual(len(FREE_CELLS), 181)
        self.assertEqual(minimum_steps(), 40)
        self.assertEqual(len(paths), 23)
        self.assertTrue(all(valid_path(p) and len(p) == 41 for p in paths))
        metrics = maze_metrics()
        self.assertEqual(metrics["junctions"], 42)
        self.assertEqual(metrics["dead_ends"], 9)
        self.assertEqual(metrics["cycles"], 22)
        self.assertEqual(metrics["minimum_routes"], len(paths))

    def test_three_agents_select_different_routes_despite_plan_loss_and_pause(self):
        agents = [Agent(i, "node-%s" % i, 230 + i, 160, 1, 1) for i in (1, 2, 3)]
        for agent in agents:
            agent.on_packet(envelope(0, "pc", 1, "cmd", "diversidad", action="start"))
        dropped = False
        paused = False
        for step in range(1200):
            outgoing = []
            for agent in agents:
                if agent.node_id == 2 and agent.phase == "SELECTING" and not paused:
                    agent.on_packet(envelope(0, "pc", 2, "cmd", "diversidad", action="pause"))
                    before = (agent.selection_ms, agent.progress)
                    agent.advance(100)
                    self.assertEqual(before, (agent.selection_ms, agent.progress))
                    agent.on_packet(envelope(0, "pc", 3, "cmd", "diversidad", action="start"))
                    paused = True
                outgoing.extend(agent.advance(10))
            for packet in outgoing:
                if packet["type"] == "plan" and packet["src"] == 1 and not dropped:
                    dropped = True
                    continue
                wire_packet = decode(encode(packet))
                for agent in agents:
                    if agent.node_id != packet["src"]:
                        agent.on_packet(wire_packet)
            if all(a.phase == "ARRIVED" for a in agents):
                break
        self.assertTrue(dropped and paused)
        self.assertTrue(all(a.phase == "ARRIVED" for a in agents))
        self.assertEqual(len(set(tuple(a.route) for a in agents)), 3)
        self.assertEqual([len(a.route) - 1 for a in agents], [40, 40, 40])
        self.assertTrue(all(a.shared_rx > 0 for a in agents))
        self.assertTrue(all(a.colony.candidates == 1280 for a in agents))
        self.assertTrue(all(len(a.colony.archive) <= 12 for a in agents))

    def test_old_reordered_and_invalid_plans_are_rejected(self):
        agent = Agent(3, "three", 3)
        agent.on_packet(envelope(0, "pc", 1, "cmd", "current", action="start"))
        paths = shortest_paths()
        first = envelope(1, "one", 10, "plan", "current", path=paths[0], cost=40)
        agent.on_packet(decode(encode(first)))
        agent.on_packet(dict(first, seq=9, path=paths[1]))
        agent.on_packet(dict(first, seq=11, epoch="old", path=paths[2]))
        agent.on_packet(dict(first, seq=12, cost=39))
        self.assertEqual(agent.peer_routes[1], paths[0])
        for invalid in (dict(first, src=0), dict(first, cost=39), dict(first, path=[START, GOAL])):
            with self.assertRaises(ValueError):
                decode(encode(invalid))
        with self.assertRaises(ValueError):
            decode(encode(dict(first, map="almacen-11x9-v1")))

    def test_long_routes_fit_udp_and_compaction_preserves_state(self):
        longest = 0
        for seed in range(1, 201):
            path = Colony(seed).construct()
            self.assertTrue(valid_path(path))
            longest = max(longest, len(path) - 1)
            agent = Agent(1, "b" * 40, seed)
            agent.epoch = "e" * 40
            agent.colony.best_path = list(path)
            agent.route = list(path)
            agent.phase = "MOVING"
            agent.progress = 0.25
            state = agent.state()
            state["net_errors"] = 100
            state["test_source"] = "CPython sin placas"
            serialized = encode(state)
            self.assertLessEqual(len(serialized), MAX_PACKET)
            self.assertIn(b'"route_same":true', serialized)
            self.assertNotIn(b'"route":', serialized)
            self.assertEqual(decode(serialized), state)
            self.assertEqual(state["route"], path)
        self.assertGreater(longest, 80)
        with self.assertRaises(ValueError):
            decode(b"x" * (MAX_PACKET + 1))
        bad = envelope(1, "bad", 1, "state", best_path=shortest_paths()[0], route_same=True, route=[])
        with self.assertRaises(ValueError):
            decode(json.dumps(bad).encode())

    def test_new_epoch_changes_exploration_and_clears_plans(self):
        agent = Agent(2, "two", 100)
        agent.on_packet(envelope(0, "pc", 1, "cmd", "first", action="start"))
        seed = agent.colony.rng.state
        agent.advance(100)
        agent.peer_routes[1] = shortest_paths()[0]
        agent.on_packet(envelope(0, "pc", 2, "cmd", "second", action="reset"))
        self.assertNotEqual(agent.colony.rng.state, seed)
        self.assertEqual(agent.peer_routes, {})
        self.assertEqual(agent.colony.archive, [])
        self.assertEqual(agent.colony.candidates, 0)
        self.assertEqual(agent.route, [])
        self.assertEqual(agent.phase, "IDLE")


if __name__ == "__main__":
    unittest.main(verbosity=2)
