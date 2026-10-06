"""Verificacion opcional SIN placas: mismos Agent y Transport, sobre UDP local.

No se inicia desde el gemelo ni desde Docker. La prueba NO sustituye los ESP32.
Uso, en dos consolas:
  python -m simulador.app --hub 127.0.0.1
  python tools/nodos_prueba.py
"""
from pathlib import Path
import sys
import time
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "firmware"))
from agent import Agent
from transport import Transport


def main():
    ports = {1: 4210, 2: 4212, 3: 4213}
    agents = {i: Agent(i, "prueba-%d" % i, 500 + i) for i in (1, 2, 3)}
    links = {i: Transport(i, "prueba-%d" % i, port=ports[i],
                          hub=("127.0.0.1", 4210), bind="127.0.0.1") for i in (1, 2, 3)}
    previous = time.monotonic()
    uptime = 0
    hello_at, state_at = -1000, -200
    print("PRUEBA LOCAL: nodos en CPython; no hay placas ESP32 conectadas.")
    print("Abre http://127.0.0.1:8080 y pulsa Iniciar. Ctrl+C para terminar.")
    try:
        while True:
            now = time.monotonic()
            dt = max(0, round((now - previous) * 1000))
            previous = now
            uptime += dt
            for i in (1, 2, 3):
                if uptime - hello_at >= 1000:
                    links[i].hello(uptime)
                for packet in links[i].receive(uptime):
                    for reply in agents[i].on_packet(packet):
                        links[i].publish(reply, uptime)
                for packet in agents[i].advance(dt):
                    links[i].publish(packet, uptime)
                if uptime - state_at >= 200:
                    state = agents[i].state()
                    state["test_source"] = "CPython sin placas"
                    links[i].publish(state, uptime)
            if uptime - hello_at >= 1000:
                hello_at = uptime
            if uptime - state_at >= 200:
                state_at = uptime
            time.sleep(0.01)
    except KeyboardInterrupt:
        pass
    finally:
        for link in links.values():
            link.close()


if __name__ == "__main__":
    main()
