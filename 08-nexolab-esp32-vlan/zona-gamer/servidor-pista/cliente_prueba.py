"""
cliente_prueba.py - Cliente WebSocket que maneja un carro del servidor de pista, para probar el
servidor sin el contenedor jugador ni el ESP32.

Habla exactamente el protocolo del contrato (el mismo que usa zona-gamer/jugador):
  1. se conecta y recibe {"tipo":"pista", "linea_central":[[x,y],...], ...}
  2. manda {"tipo":"hola","jugador":N,"nombre":...,"color":[r,g,b],"estilo":...}
  3. a 20 Hz manda {"tipo":"control","jugador":N,"dir":..,"vel":..,"boton":0,"seq":n,"t_ms":..}
  4. cada 1 s manda {"tipo":"ping","t_ms":..} y con el "pong" mide el RTT (ida y vuelta)
  5. con cada {"tipo":"estado",...} se entera de dónde está su carro (x, y, yaw) y de sus vueltas.

Cómo maneja (--modo):
  pursuit  pure pursuit sobre la línea central que mandó el servidor (como haría un piloto que mira
           un punto de la pista un poco más adelante y gira hacia él). Da vueltas solo.
  circulo  volante fijo y acelerador fijo (patrón tonto: choca con los muros; sirve para ver que el
           servidor hace reaparecer un carro atascado).
  quieto   conecta y manda vel 0 (para ver el failsafe y el carro estacionado).
Con --boton-en S mantiene apretado el botón 2 s (turbo y, a los 1,5 s, el carro reaparece).
Con --cortar-en S deja de mandar control a los S segundos SIN cerrar la conexión: el servidor debe
frenar el carro por failsafe en 1 s (se ve en "failsafe": true del estado). --observador no maneja.

Ejemplos (desde la carpeta del tema, con su entorno, que tiene websockets):
  entorno\\Scripts\\python zona-gamer\\servidor-pista\\cliente_prueba.py --url ws://localhost:18765 --jugador 1
  entorno\\Scripts\\python zona-gamer\\servidor-pista\\cliente_prueba.py --jugador 2 --vel 70 --cortar-en 20
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import statistics
import time

import websockets


def ms() -> int:
    return int(time.monotonic() * 1000)


class Piloto:
    def __init__(self, args):
        self.a = args
        self.id = f"player-{args.jugador}"
        self.linea = None           # puntos de la línea central
        self.paso = 0.25
        self.giro_max = 0.5
        self.ejes = 0.325
        self.yo = None              # mi carro en el último estado
        self.rtts = []
        self.estados = 0
        self.seq = 0
        self.t0 = time.monotonic()
        self.vueltas_ini = None
        self.failsafe_visto = None  # segundo en que se vio failsafe=true después de cortar
        self.max_vel_tras_corte = None

    # ------------------------------------------------------------------ pure pursuit
    def mando(self):
        """(dir, vel) en -100..100 según el modo."""
        a = self.a
        if a.modo == "quieto" or self.yo is None:
            return 0, 0
        if a.modo == "circulo":
            return 60, a.vel
        x, y, yaw, v = self.yo["x"], self.yo["y"], self.yo["yaw"], abs(self.yo["vel"])
        n = len(self.linea)
        # Punto de la línea más cercano (búsqueda lineal: son ~150 puntos, sobra a 20 Hz).
        i = min(range(n), key=lambda k: (self.linea[k][0] - x) ** 2 + (self.linea[k][1] - y) ** 2)
        ld = max(0.8, 0.6 + 0.4 * v)                    # mirar más lejos cuanto más rápido
        tx, ty = self.linea[(i + int(round(ld / self.paso))) % n]
        # Corrimiento lateral opcional (--carril, + = hacia la izquierda = adentro del óvalo).
        if a.carril:
            j = (i + int(round(ld / self.paso)) + 1) % n
            hx, hy = self.linea[j][0] - tx, self.linea[j][1] - ty
            h = math.hypot(hx, hy) or 1.0
            tx, ty = tx - a.carril * hy / h, ty + a.carril * hx / h
        alfa = math.atan2(ty - y, tx - x) - yaw
        giro = math.atan2(2 * self.ejes * math.sin(alfa), ld)   # rad, + = izquierda
        # dir del protocolo: +100 = derecha, por eso el signo menos.
        return int(max(-100, min(100, round(-giro / self.giro_max * 100)))), a.vel

    # ------------------------------------------------------------------ tareas
    async def enviar(self, ws):
        a = self.a
        await ws.send(json.dumps({"tipo": "hola", "jugador": a.jugador, "nombre": a.nombre or self.id,
                                  "color": [float(c) for c in a.color.split(",")] if a.color else None,
                                  "estilo": a.estilo}))
        siguiente_ping = time.monotonic()
        while True:
            t = time.monotonic() - self.t0
            if a.duracion and t > a.duracion:
                return
            if not (a.cortar_en and t > a.cortar_en):
                d, v = self.mando()
                # --boton-en S: botón apretado de S a S+2 s (turbo, y a los 1,5 s el servidor lo
                # hace reaparecer en el centro de la pista).
                boton = 1 if a.boton_en and a.boton_en <= t < a.boton_en + 2.0 else 0
                await ws.send(json.dumps({"tipo": "control", "jugador": a.jugador, "dir": d, "vel": v,
                                          "boton": boton, "seq": self.seq, "t_ms": ms()}))
                self.seq += 1
            if time.monotonic() >= siguiente_ping:
                await ws.send(json.dumps({"tipo": "ping", "t_ms": ms()}))
                siguiente_ping += 1.0
            await asyncio.sleep(0.05)                    # 20 Hz, como el ESP32

    async def recibir(self, ws):
        a = self.a
        ultimo_log = 0.0
        async for texto in ws:
            m = json.loads(texto)
            tipo = m.get("tipo")
            if tipo == "pista":
                self.linea = m["linea_central"]
                self.paso = m["perimetro"] / len(self.linea)
                self.giro_max = m.get("giro_max_rad", 0.5)
                self.ejes = m.get("distancia_ejes", 0.325)
            elif tipo == "pong":
                self.rtts.append(ms() - m["t_ms"])
            elif tipo == "bienvenida":
                print(f"[{self.id}] bienvenida: {m}", flush=True)
            elif tipo == "estado":
                self.estados += 1
                for c in m["carros"]:
                    if c["id"] == self.id:
                        self.yo = c
                t = time.monotonic() - self.t0
                if self.yo is not None:
                    if self.vueltas_ini is None:
                        self.vueltas_ini = self.yo["vuelta"]
                    if a.cortar_en and t > a.cortar_en:
                        if self.yo["failsafe"] and self.failsafe_visto is None:
                            self.failsafe_visto = t - a.cortar_en
                        if t > a.cortar_en + 2.0:
                            self.max_vel_tras_corte = max(self.max_vel_tras_corte or 0.0, abs(self.yo["vel"]))
                if t - ultimo_log >= 5.0 and self.yo is not None:
                    ultimo_log = t
                    y = self.yo
                    rtt = f"{statistics.mean(self.rtts[-5:]):.1f} ms" if self.rtts else "--"
                    print(f"[{self.id}] t={t:5.1f}s vuelta={y['vuelta']} pos={y['posicion']} vel={y['vel']:.2f} "
                          f"failsafe={y['failsafe']} rtt={rtt}", flush=True)

    def resumen(self):
        t = time.monotonic() - self.t0
        r = {"id": self.id, "segundos": round(t, 1), "estados_recibidos": self.estados,
             "estado_hz": round(self.estados / t, 1) if t else 0,
             "vueltas": (self.yo or {}).get("vuelta"), "mejor_vuelta_s": (self.yo or {}).get("mejor"),
             "rtt_ms_medio": round(statistics.mean(self.rtts), 2) if self.rtts else None,
             "rtt_ms_max": max(self.rtts) if self.rtts else None, "controles_enviados": self.seq}
        if self.a.cortar_en:
            r["failsafe_tras_corte_s"] = None if self.failsafe_visto is None else round(self.failsafe_visto, 2)
            r["vel_max_2s_despues_del_corte"] = self.max_vel_tras_corte
        return r


async def principal(args):
    pil = Piloto(args)
    async with websockets.connect(args.url, max_size=2 ** 20) as ws:
        if args.observador:
            await ws.send(json.dumps({"tipo": "observador"}))
            t0 = time.monotonic()
            async for texto in ws:
                m = json.loads(texto)
                if m.get("tipo") == "estado":
                    pil.estados += 1
                    if pil.estados % 40 == 1:
                        print("[observador]", {c["id"]: c["vuelta"] for c in m["carros"]}, flush=True)
                elif m.get("tipo") != "estado":
                    print("[observador] recibido:", m.get("tipo"), flush=True)
                if args.duracion and time.monotonic() - t0 > args.duracion:
                    break
            return
        rec = asyncio.create_task(pil.recibir(ws))
        await pil.enviar(ws)
        rec.cancel()
    print("RESUMEN " + json.dumps(pil.resumen()), flush=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default="ws://localhost:8765")
    ap.add_argument("--jugador", type=int, default=1, choices=(1, 2, 3))
    ap.add_argument("--modo", default="pursuit", choices=("pursuit", "circulo", "quieto"))
    ap.add_argument("--vel", type=int, default=80, help="acelerador -100..100")
    ap.add_argument("--carril", type=float, default=0.0, help="corrimiento lateral en m (+ = adentro)")
    ap.add_argument("--duracion", type=float, default=60.0, help="segundos (0 = sin fin)")
    ap.add_argument("--cortar-en", type=float, default=0.0, help="deja de mandar control a los S s")
    ap.add_argument("--boton-en", type=float, default=0.0, help="mantiene el botón 2 s desde los S s")
    ap.add_argument("--nombre", default="")
    ap.add_argument("--color", default="", help="r,g,b de 0 a 1 o de 0 a 255 (vacío = el de defecto)")
    ap.add_argument("--estilo", default=None, choices=(None, "clasico", "deportivo", "rally"))
    ap.add_argument("--observador", action="store_true")
    asyncio.run(principal(ap.parse_args()))


if __name__ == "__main__":
    main()
