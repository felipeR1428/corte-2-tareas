"""servidor_ws_falso.py - Servidor de pista FALSO para probar el player y el --piloto del emulador sin
el servidor PyBullet real (que hace otro agente) y sin levantar el stack.

Habla el mismo WebSocket JSON del contrato, pero la "física" es un modelo de bicicleta de 10 líneas
sobre una pista ovalada (elipse 20 x 10 m): suficiente para ver que el player reenvía el control,
que el pong vuelve, que el failsafe manda vel 0 y que el piloto automático da vueltas.

    hola / observador  -> registra al cliente; a todos se les manda al conectar {"tipo":"pista","centro":[...]}
    control            -> mueve el carro player-N; se cuentan los controles y los "failsafe" (vel 0, seq < 0)
    ping               -> pong con el mismo t_ms, solo a ese cliente
    (20 Hz)            -> estado con player-N y un autónomo auto-1 que recorre el óvalo

Uso (Windows, desde la carpeta del tema):
    entorno\\Scripts\\python.exe emulador\\pruebas_locales\\servidor_ws_falso.py --puerto 18765 --duracion 60 --resumen resultados\\ws_falso.json
"""

import argparse
import asyncio
import json
import math
import time

import websockets

A, B = 20.0, 10.0  # semiejes del óvalo (m)
CENTRO = [[round(A * math.cos(2 * math.pi * k / 80), 3), round(B * math.sin(2 * math.pi * k / 80), 3)]
          for k in range(80)]
BATALLA_SIM = 1.5  # m (modelo de bicicleta del carro falso)
VEL_MAX = 6.0      # m/s con vel = 100
DELTA_MAX = 0.5    # rad con dir = ±100 (dir > 0 = derecha = el yaw baja)

clientes = set()
carros = {}         # id -> dict(x, y, yaw, vel, vuelta, dir, vmando, ang_prev)
stats = {}          # id -> contadores para el resumen
eventos = []
SIN_PISTA = False


def nuevo_carro(cid, fase):
    # Arranca sobre la pista, apuntando en sentido antihorario (el de los autónomos).
    x, y = A * math.cos(fase), B * math.sin(fase)
    yaw = math.atan2(B * math.cos(fase), -A * math.sin(fase))
    return {"id": cid, "x": x, "y": y, "yaw": yaw, "vel": 0.0, "vuelta": 0, "dir": 0, "vmando": 0,
            "ang_prev": math.atan2(y / B, x / A), "t_ctrl": None}


async def atender(ws):
    clientes.add(ws)
    quien = "?"
    if not SIN_PISTA:  # --sin-pista: prueba el respaldo del piloto (aprender la pista de auto-1)
        await ws.send(json.dumps({"tipo": "pista", "centro": CENTRO}))
    try:
        async for texto in ws:
            m = json.loads(texto)
            t = m.get("tipo")
            if t == "hola":
                quien = f"player-{m['jugador']}"
                carros.setdefault(quien, nuevo_carro(quien, 0.3 * m["jugador"]))
                stats.setdefault(quien, {"controles": 0, "failsafe": 0, "pings": 0, "hola": 0})
                stats[quien]["hola"] += 1
                eventos.append((time.monotonic(), f"hola de {quien}: {m}"))
                print(f"[falso] hola {m}", flush=True)
            elif t == "observador":
                quien = "observador"
                print("[falso] se conectó un observador", flush=True)
            elif t == "control":
                cid = f"player-{m['jugador']}"
                c = carros.setdefault(cid, nuevo_carro(cid, 0.0))
                s = stats.setdefault(cid, {"controles": 0, "failsafe": 0, "pings": 0, "hola": 0})
                s["controles"] += 1
                if m.get("seq", 0) < 0 or m.get("failsafe"):
                    if s["failsafe"] == 0:
                        eventos.append((time.monotonic(), f"primer failsafe de {cid}: {m}"))
                        print(f"[falso] FAILSAFE de {cid}: {m}", flush=True)
                    s["failsafe"] += 1
                c["dir"], c["vmando"], c["t_ctrl"] = m["dir"], m["vel"], time.monotonic()
            elif t == "ping":
                if quien in stats:
                    stats[quien]["pings"] += 1
                await ws.send(json.dumps({"tipo": "pong", "t_ms": m["t_ms"]}))
    except websockets.ConnectionClosed:
        pass
    finally:
        clientes.discard(ws)
        print(f"[falso] se fue {quien}", flush=True)


def fisica(dt, t):
    for cid, c in carros.items():
        if not cid.startswith("player"):
            continue  # el autónomo se mueve aparte (abajo), sobre la elipse
        # failsafe propio del servidor (contrato): sin control en 1 s, frena
        vm = c["vmando"] if c["t_ctrl"] and time.monotonic() - c["t_ctrl"] < 1.0 else 0
        objetivo = vm / 100 * VEL_MAX
        c["vel"] += (objetivo - c["vel"]) * min(1.0, dt / 0.4)
        delta = -c["dir"] / 100 * DELTA_MAX
        c["yaw"] += c["vel"] / BATALLA_SIM * math.tan(delta) * dt
        c["yaw"] = math.atan2(math.sin(c["yaw"]), math.cos(c["yaw"]))  # -pi..pi, como PyBullet
        c["x"] += c["vel"] * math.cos(c["yaw"]) * dt
        c["y"] += c["vel"] * math.sin(c["yaw"]) * dt
        ang = math.atan2(c["y"] / B, c["x"] / A)
        if c["ang_prev"] < 0 <= ang and ang - c["ang_prev"] < 1.0:  # cruza el eje +x antihorario
            c["vuelta"] += 1
        c["ang_prev"] = ang
    fa = 0.25 * t  # el autónomo da una vuelta cada ~25 s
    carros["auto-1"] = {"id": "auto-1", "x": A * math.cos(fa), "y": B * math.sin(fa),
                        "yaw": math.atan2(B * math.cos(fa), -A * math.sin(fa)), "vel": 3.0,
                        "vuelta": int(fa // (2 * math.pi))}


async def bucle(args):
    t0 = time.monotonic()
    prox_log = 2.0
    while args.duracion <= 0 or time.monotonic() - t0 < args.duracion:
        await asyncio.sleep(0.05)
        t = time.monotonic() - t0
        fisica(0.05, t)
        lista = [{k: (round(v, 3) if isinstance(v, float) else v) for k, v in c.items()
                  if k in ("id", "x", "y", "yaw", "vel", "vuelta")} for c in carros.values()]
        msg = json.dumps({"tipo": "estado", "t": round(t, 2), "carros": lista})
        for ws in list(clientes):
            try:
                await ws.send(msg)
            except websockets.ConnectionClosed:
                pass
        if t >= prox_log:
            prox_log += 2.0
            for cid, s in stats.items():
                c = carros[cid]
                print(f"[falso] t={t:5.1f} {cid} controles={s['controles']} failsafe={s['failsafe']} "
                      f"pings={s['pings']} dir={c['dir']} vel={c['vmando']} pos=({c['x']:.1f},{c['y']:.1f}) "
                      f"vuelta={c['vuelta']}", flush=True)


async def principal(args):
    async with websockets.serve(atender, "0.0.0.0", args.puerto):
        print(f"[falso] servidor de pista falso en ws://0.0.0.0:{args.puerto}", flush=True)
        await bucle(args)
    if args.resumen:
        res = {"stats": stats,
               "carros": {k: {kk: (round(vv, 2) if isinstance(vv, float) else vv) for kk, vv in v.items()}
                          for k, v in carros.items()},
               "eventos": [[round(t, 2), e] for t, e in eventos]}
        with open(args.resumen, "w", encoding="utf-8") as f:
            json.dump(res, f, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--puerto", type=int, default=18765)
    ap.add_argument("--duracion", type=float, default=0)
    ap.add_argument("--resumen", default=None)
    ap.add_argument("--sin-pista", action="store_true", help="no mandar el mensaje pista")
    a = ap.parse_args()
    SIN_PISTA = a.sin_pista
    asyncio.run(principal(a))
