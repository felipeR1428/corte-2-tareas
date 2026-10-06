"""probar_joints.py - Hace de ESP32 maestro de un robot para probar sim_robot.py sin hardware.

Manda JOINTS a 20 Hz (como el firmware) con un guion fijo que barre los rangos de j1, j2 y j3, deja
quieto el comando (para medir el error real-to-sim en régimen), aprieta el botón (saludo / trote),
y al final deja de mandar para comprobar el failsafe (vuelta al reposo en 1 s). Mientras tanto lee el
/estado.json del visor HTTP del contenedor y resume el error por fase.

    entorno\\Scripts\\python zona-robotica\\probar_joints.py --robot nao --puerto 15103 --http 18013

Opciones: --perdida 0.02 salta un 2 % de los números de secuencia (simula datagramas perdidos, para
ver que el contenedor los cuenta). Solo usa la biblioteca estándar + comun/protocolo.py.
"""

import argparse
import json
import math
import os
import random
import socket
import statistics
import sys
import threading
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from comun import protocolo  # noqa: E402

# Pose "de reposo" en unidades de j para cada robot (lo que mandaría el ESP32 con los potenciómetros
# en la posición de descanso del robot) y amplitudes de barrido seguras.
REPOSO = {"nao": (82.7, 23.9, 0.0), "pepper": (90.5, 29.7, 0.0), "spot": (0.0, 0.0, 0.0)}
BARRIDO = {   # (centro, amplitud) de cada j
    "nao": ((15.0, 75.0), (45.0, 40.0), (0.0, 60.0)),
    "pepper": ((15.0, 75.0), (45.0, 40.0), (0.0, 60.0)),
    "spot": ((0.0, 20.0), (0.0, 15.0), (0.0, 15.0)),
}
QUIETO = {"nao": (0.0, 60.0, 30.0), "pepper": (0.0, 60.0, 30.0), "spot": (15.0, 10.0, -10.0)}


def guion(robot, t):
    """(fase, j1, j2, j3, boton) en el segundo t de la prueba; None = no mandar nada."""
    rep, bar, qui = REPOSO[robot], BARRIDO[robot], QUIETO[robot]
    if t < 2:
        return "sin_esp32", None
    if t < 6:
        return "reposo_quieto", (*rep, 0)
    if t < 18:                       # barrido de a una articulación, 4 s cada una (1 ciclo seno)
        k = int((t - 6) // 4)
        j = list(rep)
        c, a = bar[k]
        j[k] = c + a * math.sin(2 * math.pi * (t - 6 - 4 * k) / 4)
        return f"barrido_j{k + 1}", (*j, 0)
    if t < 21:
        return "pose_quieta", (*qui, 0)
    if robot == "spot":
        if t < 21.4:
            return "boton", (*rep, 1)    # flanco -> trotar
        if t < 33:
            return "trotando", (*rep, 0)
        if t < 33.4:
            return "boton", (*rep, 1)    # flanco -> parar
        if t < 37:
            return "despues_trote", (*rep, 0)
        return "failsafe", None
    if t < 21.4:
        return "boton", (*qui, 1)        # flanco -> saludo (2,5 s)
    if t < 25:
        return "gesto", (*qui, 0)
    if t < 33:                        # las tres a la vez, como con un joystick
        u = t - 25
        j = [bar[k][0] + bar[k][1] * math.sin(2 * math.pi * u / (4 + k)) for k in range(3)]
        return "barrido_conjunto", (*j, 0)
    if t < 37:
        return "reposo_quieto2", (*rep, 0)
    return "failsafe", None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--robot", required=True, choices=protocolo.ROBOTS)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--puerto", type=int, default=5100)
    ap.add_argument("--http", type=int, default=8000, help="puerto del visor del contenedor")
    ap.add_argument("--duracion", type=float, default=40.0)
    ap.add_argument("--perdida", type=float, default=0.0)
    ap.add_argument("--salida", default="", help="json con el resumen por fase")
    args = ap.parse_args()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    muestras = []                       # (fase, estado del contenedor)
    fase_actual = ["sin_esp32"]
    parar = threading.Event()

    def leer_estado():
        url = f"http://{args.host}:{args.http}/estado.json"
        while not parar.is_set():
            try:
                with urllib.request.urlopen(url, timeout=1) as r:
                    muestras.append((fase_actual[0], json.loads(r.read())))
            except OSError:
                pass
            parar.wait(0.1)

    threading.Thread(target=leer_estado, daemon=True).start()
    t0 = time.monotonic()
    seq = enviados = saltados = 0
    proximo = t0
    while True:
        t = time.monotonic() - t0
        if t >= args.duracion:
            break
        fase, val = guion(args.robot, t)
        fase_actual[0] = fase
        if val is not None:
            if args.perdida and random.random() < args.perdida:
                saltados += 1           # "se perdió en la red": se gasta el seq sin mandar nada
            else:
                j1, j2, j3, b = val
                msg = protocolo.armar_joints(args.robot, seq, int(t * 1000), j1, j2, j3, b)
                sock.sendto(msg.encode(), (args.host, args.puerto))
                enviados += 1
            seq += 1
        proximo += 0.05                 # 20 Hz, contra reloj fijo (sin acumular deriva)
        time.sleep(max(0.0, proximo - time.monotonic()))
    parar.set()
    time.sleep(0.2)

    # Resumen por fase: error real-to-sim que reportaba el contenedor y estados vistos.
    resumen = {"robot": args.robot, "enviados": enviados, "saltados_a_proposito": saltados, "fases": {}}
    for fase in dict.fromkeys(f for f, _ in muestras):
        ests = [e for f, e in muestras if f == fase and e]
        if not ests:
            continue
        errs = [e["error_deg"] for e in ests]
        resumen["fases"][fase] = {
            "muestras": len(ests), "estados": sorted({e["estado"] for e in ests}),
            "error_medio_deg": round(statistics.mean(errs), 2), "error_max_deg": round(max(errs), 2),
            "error_final_deg": round(errs[-1], 2),
        }
        if args.robot == "spot":
            resumen["fases"][fase]["caidas"] = ests[-1].get("caidas")
            resumen["fases"][fase]["avance_m"] = round(ests[-1].get("avance_m", 0), 3)
    if muestras:
        ult = muestras[-1][1]
        resumen["contenedor"] = {k: ult.get(k) for k in ("recibidos", "perdidos", "perdida_pct", "jitter_ms",
                                                          "fps_fisica", "estado", "objetivo", "medido")}
    texto = json.dumps(resumen, indent=1, ensure_ascii=False)
    print(texto)
    if args.salida:
        with open(args.salida, "w", encoding="utf-8") as f:
            f.write(texto)


if __name__ == "__main__":
    main()
