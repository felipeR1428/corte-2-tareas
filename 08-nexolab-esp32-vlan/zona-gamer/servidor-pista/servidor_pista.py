"""
servidor_pista.py - Servidor de pista de la Zona Gamer (VLAN 1, track-server, 192.168.10.10).

Qué hace:
  1. Simula en PyBullet, SIN ventana (modo DIRECT, "headless"), una circuito técnico Nexo GP con 6 carros
     racecar (el modelo del MIT RACECAR que trae pybullet_data, el mismo de los entornos de carros
     de pybullet_envs que usa rl-baselines3-zoo):
       - player-1..3: los manejan los contenedores jugador (cada uno controlado por su ESP32).
       - auto-1..3: autónomos, siguen la línea central con "pure pursuit" a velocidades distintas
         (así se alcanzan y se adelantan entre ellos y a los jugadores).
     La física corre a 240 Hz en TIEMPO REAL en un hilo propio.
  2. Atiende a los jugadores por WebSocket (ws://0.0.0.0:8765) con mensajes JSON (ver el contrato en
     el borrador pista.md): recibe "hola", "control", "ping" y "observador"; manda "pista" (la línea
     central, una vez al conectar), "estado" (a 20 Hz, a todos), "pong" y "bienvenida".
  3. Publica su salud en el plano de administración (VLAN 3): latido UDP cada 1 s y métricas MQTT
     cada 2 s (fps reales de la física, clientes, mensajes de control por segundo, vueltas...).
  4. Sirve por HTTP (puerto 8000) el último cuadro de la cámara cenital (/cuadro.jpg), una página
     que lo refresca (/) y el último estado en JSON (/estado.json). Con GRABAR_S=<segundos> además
     graba resultados/pista.mp4 con la tabla de posiciones.

Arquitectura (3 "trabajadores" para que nadie frene a nadie):
  - hilo de FÍSICA: stepSimulation a 240 Hz contra el reloj; cada 4 pasos (60 Hz) lee los mandos y
    calcula la dirección/velocidad de cada carro; cada 12 pasos (20 Hz) arma el mensaje "estado".
  - hilo PRINCIPAL con asyncio: todas las conexiones WebSocket. Nunca llama a PyBullet: solo deja
    los mandos en un diccionario que la física lee (asignar un valor en un dict es atómico en Python).
    El hilo de física le pasa cada "estado" con loop.call_soon_threadsafe.
  - PROCESO camarógrafo (render_pista.py): renderiza en otro núcleo con su propia copia de PyBullet.

Variables de entorno:
  PUERTO_WS=8765  PUERTO_HTTP=8000  GRABAR_S=0 (segundos de video; 0 = no graba)
  GRABAR_DESDE_S=3 (segundo de simulación en que empieza el video)  FPS_VIDEO=15
  RESULTADOS=/app/resultados  y las del plano de admin (ROUTER_IP, RUTAS, ADMIN_IP: ver comun/lab.py)

Uso:  python servidor_pista.py            (en Docker es el ENTRYPOINT)
"""

from __future__ import annotations

import asyncio
import http.server
import json
import math
import multiprocessing as mp
import os
import signal
import sys
import tempfile
import threading
import time

import pybullet as p

# Fuera de Docker (probando desde la carpeta del tema), comun/ está dos carpetas más arriba.
AQUI = os.path.dirname(os.path.abspath(__file__))
for _r in (AQUI, os.path.abspath(os.path.join(AQUI, "..", ".."))):
    if os.path.isdir(os.path.join(_r, "comun")) and _r not in sys.path:
        sys.path.insert(0, _r)

from comun import lab, protocolo   # noqa: E402
import pista as P                  # noqa: E402

PUERTO_WS = int(os.environ.get("PUERTO_WS", "8765"))
PUERTO_HTTP = int(os.environ.get("PUERTO_HTTP", "8000"))
GRABAR_S = float(os.environ.get("GRABAR_S", "0") or 0)
GRABAR_DESDE_S = float(os.environ.get("GRABAR_DESDE_S", "3"))
FPS_VIDEO = int(os.environ.get("FPS_VIDEO", "15"))
RESULTADOS = os.environ.get("RESULTADOS", os.path.join(AQUI, "..", "..", "resultados"))

HZ_FISICA = 240          # pasos de física por segundo (el valor por defecto de PyBullet: 1/240 s)
PASOS_CONTROL = 4        # cada 4 pasos (60 Hz) se recalculan dirección y acelerador
PASOS_ESTADO = 12        # cada 12 pasos (20 Hz) se manda "estado" a todos los clientes
FAILSAFE_S = 1.0         # un jugador sin mensajes de control por más de esto queda frenado
TURBO = 1.25             # con el botón apretado el jugador va un 25 % más rápido...
BOTON_REAPARECER_S = 1.5  # ...y si lo mantiene 1,5 s seguidos, su carro reaparece en el centro

# Colores y estilos por defecto (el contrato): player-1 rojo/deportivo, player-2 azul/clásico,
# player-3 verde/rally; autónomos en tres grises distintos (para distinguirlos entre ellos).
DEFECTO = {
    "player-1": ((0.88, 0.12, 0.10), "deportivo"),
    "player-2": ((0.12, 0.35, 0.90), "clasico"),
    "player-3": ((0.15, 0.70, 0.22), "rally"),
    "auto-1": ((0.78, 0.78, 0.80), "auto"),
    "auto-2": ((0.58, 0.58, 0.62), "auto"),
    "auto-3": ((0.40, 0.40, 0.44), "auto"),
}
# Parrilla de salida (de adelante hacia atrás, en dos filas como en una carrera real) y, para los
# autónomos, su velocidad crucero (m/s) y su carril preferido (desplazamiento lateral, m). Las
# velocidades distintas hacen que se alcancen; los carriles distintos, que al alcanzarse no choquen
# de frente sino que tengan por dónde pasar.
PARRILLA = ["auto-1", "player-1", "auto-2", "player-2", "auto-3", "player-3"]
AUTONOMOS = {"auto-1": (1.75, 0.35), "auto-2": (2.0, -0.35), "auto-3": (2.25, 0.0)}


def limitar(v, lo, hi):
    return max(lo, min(hi, v))


# =============================================================================================
# Un carro (lado de la física)
# =============================================================================================
class Carro:
    def __init__(self, cid: str, s0: float, d0: float):
        self.id = cid
        self.es_jugador = cid.startswith("player")
        self.jugador = int(cid[-1]) if self.es_jugador else None
        self.color, self.estilo = DEFECTO[cid]
        self.nombre = cid
        x, y, rumbo = P.punto(s0, d0)
        self.body = P.cargar_carro(x, y, rumbo, self.color)
        # Ruedas delanteras libres: por defecto PyBullet les pone un motor de velocidad 0 que las
        # FRENA; con fuerza 0 se apaga ese motor y giran solas al rodar (como en el ejemplo racecar.py).
        for j in P.RUEDAS_LIBRES:
            p.setJointMotorControl2(self.body, j, p.VELOCITY_CONTROL, targetVelocity=0, force=0)
        # Estado de carrera
        self.s_prev = P.envolver(s0)
        self.distancia = P.envolver_delta(s0)   # negativo: arranca detrás de la meta
        self.vuelta = 0
        self.mitad = False          # pasó por la mitad de la pista desde el último cruce de meta
        self.t_inicio_vuelta = None
        self.mejor = None
        self.ultima = None
        self.reapariciones = 0
        # Pose y velocidad (las actualiza leer())
        self.x = self.y = self.yaw = self.vel = 0.0
        self.s = self.d = 0.0
        self.arriba = 1.0
        # Detectores de atasco / vuelco
        self.t_atascado = 0.0
        self.t_volcado = 0.0
        self.t_boton = 0.0
        # Mando
        self.v_cmd = 0.0
        self.giro_cmd = 0.0
        self.failsafe = self.es_jugador
        self.conectado = False
        # Autónomo
        self.v_crucero, self.carril = AUTONOMOS.get(cid, (0.0, 0.0))
        self.desvio = self.carril        # desplazamiento lateral al que apunta ahora
        self.desvio_obj = self.carril
        self.t_desvio = 0.0

    # ------------------------------------------------------------------ lectura
    def leer(self):
        pos, orn = p.getBasePositionAndOrientation(self.body)
        v, _ = p.getBaseVelocity(self.body)
        self.x, self.y = pos[0], pos[1]
        self.z = pos[2]
        self.pos, self.orn = pos, orn
        m = p.getMatrixFromQuaternion(orn)      # matriz de rotación 3x3 por filas
        self.yaw = math.atan2(m[3], m[0])       # hacia dónde apunta el eje x del carro
        self.arriba = m[8]                      # componente z del eje z del carro: 1 = derecho, <0 = volcado
        # Velocidad "de avance": la proyección de la velocidad sobre la dirección del carro (con signo,
        # negativa en reversa). Es lo que mostraría un velocímetro.
        self.vel = v[0] * math.cos(self.yaw) + v[1] * math.sin(self.yaw)
        self.s, self.d = P.proyectar(self.x, self.y)

    # ------------------------------------------------------------------ vueltas
    def contar_vuelta(self, t: float):
        """Vueltas = cruces de la meta HACIA ADELANTE habiendo pasado antes por la mitad de la pista.

        La condición de la mitad evita trampas y falsos positivos: ir y venir sobre la línea de meta,
        o reaparecer justo detrás de ella, no suma vueltas. Cruzarla hacia atrás borra la marca de la
        mitad (hay que volver a dar la vuelta entera)."""
        P_ = P.PERIMETRO
        ds = P.envolver_delta(self.s - self.s_prev)
        self.distancia += ds
        if self.s_prev > P_ - 3 and self.s < 3 and ds > 0:          # cruzó la meta hacia adelante
            if self.mitad:
                self.vuelta += 1
                if self.t_inicio_vuelta is not None:
                    self.ultima = t - self.t_inicio_vuelta
                    self.mejor = self.ultima if self.mejor is None else min(self.mejor, self.ultima)
            self.mitad = False
            self.t_inicio_vuelta = t
        elif self.s_prev < 3 and self.s > P_ - 3 and ds < 0:        # la cruzó hacia atrás
            self.mitad = False
        if abs(self.s - P_ / 2) < 3:
            self.mitad = True
        self.s_prev = self.s

    # ------------------------------------------------------------------ actuar
    def aplicar(self, giro: float, v: float, frenar: bool = False):
        """giro en rad (+ = izquierda), v en m/s sobre el piso (+ adelante).

        Cómo se maneja un racecar en PyBullet (igual que el ejemplo racecar.py de bullet3):
          - tracción: VELOCITY_CONTROL en las dos ruedas traseras: un motor que hace hasta
            FUERZA_RUEDA N*m para que la rueda gire a v / radio rad/s. Velocidad 0 con fuerza = freno.
          - dirección: POSITION_CONTROL en las dos bisagras delanteras: un servo que lleva la bisagra
            al ángulo pedido (las dos al mismo ángulo: no es Ackermann exacto, pero con giros chicos y
            0,2 m entre ruedas la diferencia es de décimas de grado).
        """
        self.giro_cmd, self.v_cmd = giro, v
        w = 0.0 if frenar else v / P.RADIO_RUEDA
        for j in P.RUEDAS_TRACCION:
            p.setJointMotorControl2(self.body, j, p.VELOCITY_CONTROL, targetVelocity=w,
                                    force=P.FUERZA_RUEDA * (1.5 if frenar else 1.0))
        for j in P.DIRECCION:
            p.setJointMotorControl2(self.body, j, p.POSITION_CONTROL, targetPosition=giro, force=10)

    def reaparecer(self, otros: list, motivo: str):
        """Lo pone derecho en el mismo s de la pista, en un hueco libre (centro o carriles), quieto."""
        s = self.s
        for d in (self.carril, 0.0, 0.4, -0.4, 0.6, -0.6):
            x, y, rumbo = P.punto(s, d)
            if all(math.hypot(o.x - x, o.y - y) > 0.7 for o in otros if o is not self):
                break
        p.resetBasePositionAndOrientation(self.body, [x, y, 0.02], p.getQuaternionFromEuler([0, 0, rumbo]))
        p.resetBaseVelocity(self.body, [0, 0, 0], [0, 0, 0])
        for j in P.JUNTAS_MOVILES:
            p.resetJointState(self.body, j, 0.0, 0.0)
        self.t_atascado = self.t_volcado = self.t_boton = 0.0
        self.desvio = self.desvio_obj = self.carril
        self.reapariciones += 1
        self.leer()
        self.s_prev = self.s
        print(f"[pista] {self.id} reaparece ({motivo}) en s={s:.1f} m", flush=True)

    def dict_estado(self, posicion: int) -> dict:
        return {"id": self.id, "x": round(self.x, 3), "y": round(self.y, 3), "yaw": round(self.yaw, 3),
                "vel": round(self.vel, 2), "vuelta": self.vuelta, "posicion": posicion,
                "s": round(self.s, 2), "sector": min(3,1+int(3*self.s/P.PERIMETRO)), "distancia": round(self.distancia, 2),
                "mejor": None if self.mejor is None else round(self.mejor, 2),
                "ultima": None if self.ultima is None else round(self.ultima, 2),
                "nombre": self.nombre, "color": [round(c, 3) for c in self.color], "estilo": self.estilo,
                "conectado": self.conectado, "failsafe": self.failsafe, "reapariciones": self.reapariciones}


# =============================================================================================
# El servidor
# =============================================================================================
class ServidorPista:
    def __init__(self):
        # Mandos de los jugadores: lo escribe el hilo de red y lo lee el de física. Cada valor es un
        # dict NUEVO (nunca se modifica uno existente), así la física nunca ve un mando a medio escribir.
        self.mandos = {1: None, 2: None, 3: None}
        self.duenos = {1: None, 2: None, 3: None}    # jugador -> conexión WebSocket que lo maneja
        self.conexiones = set()
        self.observadores = set()
        self.seq = {j: protocolo.ContadorSecuencia() for j in (1, 2, 3)}
        self.jitter = {j: protocolo.Jitter() for j in (1, 2, 3)}
        self.ctrl_cuenta = {1: 0, 2: 0, 3: 0}
        self.ctrl_rechazados = 0
        self.msg_invalidos = 0
        self.estados_enviados = 0
        self.ultimo_estado = "{}"
        self.pasos = 0
        self.atraso_max = 0.0
        self.recuperaciones = 0     # veces que la física se atrasó tanto que hubo que saltar pasos
        self.parar = threading.Event()
        self.loop = None
        self.ev_parar = None        # asyncio.Event que detiene el servidor (señal o error de la física)
        self.error_fisica = False
        self.ruta_cuadro = os.path.join(tempfile.gettempdir(), "pista_cuadro.jpg")
        self.mensaje_pista = json.dumps({
            "tipo": "pista", "linea_central": P.linea_central(0.25), "sentido": "antihorario",
            "ancho": P.ANCHO, "perimetro": round(P.PERIMETRO, 3), "largo_recta": P.LARGO_RECTA,
            "nombre": P.NOMBRE, "meta": dict(zip(("x","y","rumbo"),P.punto(0))),
            "giro_max_rad": P.GIRO_MAX, "vel_max_ms": P.VEL_MAX, "distancia_ejes": P.DISTANCIA_EJES,
            "dir_positivo": "derecha", "hz_estado": HZ_FISICA // PASOS_ESTADO})

    # ------------------------------------------------------------------ física
    def crear_mundo(self):
        p.connect(p.DIRECT)
        p.setGravity(0, 0, -9.81)
        p.setTimeStep(1.0 / HZ_FISICA)
        P.construir_pista(visual=False, colision=True)   # la física no necesita lo que solo se ve
        self.carros = []
        for i, cid in enumerate(PARRILLA):
            s0 = -1.0 - 0.75 * i                          # detrás de la meta, cada 0,75 m
            d0 = 0.45 if i % 2 == 0 else -0.45            # dos filas
            self.carros.append(Carro(cid, s0, d0))
        self.por_id = {c.id: c for c in self.carros}
        for c in self.carros:
            c.leer()
            c.aplicar(0.0, 0.0, frenar=True)

    def _pure_pursuit(self, c: Carro, dt: float):
        """Piloto automático de los autónomos: pure pursuit sobre la línea central (corrida c.desvio).

        Pure pursuit (Coulter, 1992): se elige un punto de la trayectoria a una distancia Ld por
        delante (el "punto de mira") y se calcula el arco de circunferencia que lleva desde el eje
        trasero hasta él. Para un modelo de bicicleta con distancia entre ejes Wb y alfa = ángulo del
        punto de mira visto desde el carro, la curvatura es 2*sen(alfa)/Ld, y el ángulo de dirección
        que la produce es  delta = atan(2 * Wb * sen(alfa) / Ld).
        Ld crece con la velocidad: mirar lejos a alta velocidad suaviza (no zigzaguea), mirar cerca
        a baja velocidad sigue mejor las curvas cerradas.
        """
        # --- evitar choques, en coordenadas de pista (s = cuánto más adelante, d = de qué lado):
        # "delante" = otro carro entre 0 y 2 m más adelante sobre la pista que me tapa el carril al
        # que voy (diferencia lateral menor que un ancho de carro más margen, 0,45 m).
        v_obj = min(c.v_crucero, max(.95, math.sqrt(1.55/max(abs(P.curvatura(c.s+1)),.04))))
        LIBRE = 0.45
        delante = None
        for o in self.carros:
            if o is c:
                continue
            ds = P.envolver_delta(o.s - c.s)
            if 0 < ds < 2.0 and abs(o.d - c.desvio_obj) < LIBRE and (delante is None or ds < delante[0]):
                delante = (ds, o)
        if delante is not None:
            ds, o = delante
            # Elegir por dónde pasar: a cada lado del otro (o.d +/- 0,55 m), dentro del asfalto
            # (|d| <= 0,85: el borde está a 1,2 m y el carro mide 0,29 m de ancho) y sin un tercer
            # carro ahí mismo. Entre los posibles, el que pida menos volantazo.
            opciones = []
            for cand in (o.d + 0.55, o.d - 0.55):
                if abs(cand) > P.ANCHO/2 - .24:
                    continue
                ocupado = any(abs(P.envolver_delta(q.s - c.s)) < 2.0 and abs(q.d - cand) < LIBRE
                              for q in self.carros if q is not c and q is not o)
                opciones.append((ocupado, abs(cand - c.desvio), cand))
            hueco = bool(opciones) and not min(opciones)[0]
            if opciones:
                c.desvio_obj = min(opciones)[2]
                c.t_desvio = 1.5            # mantener el carril de adelantamiento un rato
            # Mientras todavía no me corrí lo suficiente, ir más despacio que él (sin empujarlo).
            # Si hay hueco, nunca menos de 0,35 m/s (detrás de un jugador estacionado hay que seguir
            # rodando para poder rodearlo); si los dos lados están ocupados, esperar detrás.
            if abs(o.d - c.d) < LIBRE and ds < 1.0:
                v_obj = min(v_obj, max(0.35 if hueco else 0.0, 0.9 * o.vel))
        elif c.t_desvio > 0:
            c.t_desvio -= dt
        elif all(not (-0.8 < P.envolver_delta(q.s - c.s) < 2.0 and abs(q.d - c.carril) < LIBRE)
                 for q in self.carros if q is not c):
            # Volver a mi carril solo si está libre desde un poco atrás (-0,8 m: el carro que acabo de
            # pasar) hasta 2 m adelante; si no, me le cerraría encima al que voy dejando atrás.
            c.desvio_obj = c.carril
        # El desvío se mueve hacia su objetivo a 1 m/s como máximo: cambios de carril suaves.
        paso = 1.0 * dt
        c.desvio += limitar(c.desvio_obj - c.desvio, -paso, paso)

        ld = max(0.7, 0.6 + 0.4 * abs(c.vel))
        tx, ty, _ = P.punto(c.s + ld, c.desvio)
        alfa = math.atan2(ty - c.y, tx - c.x) - c.yaw
        giro = math.atan2(2 * P.DISTANCIA_EJES * math.sin(alfa), ld)
        c.aplicar(limitar(giro, -P.GIRO_MAX, P.GIRO_MAX), v_obj)

    def _controlar(self, t: float, dt: float):
        ahora = time.monotonic()
        for c in self.carros:
            c.leer()
            c.contar_vuelta(t)
        for c in self.carros:
            if c.es_jugador:
                m = self.mandos[c.jugador]
                c.conectado = self.duenos[c.jugador] is not None
                if m is None or ahora - m["t"] > FAILSAFE_S or m.get("failsafe", False):
                    # FAILSAFE: sin noticias del jugador en 1 s (se cayó la red, el contenedor o el
                    # ESP32) el carro frena y endereza: un carro sin piloto no debe seguir acelerando.
                    c.failsafe = True
                    c.t_boton = 0.0
                    c.aplicar(0.0, 0.0, frenar=True)
                else:
                    c.failsafe = False
                    # dir: -100 = izquierda, +100 = derecha. En PyBullet un ángulo de dirección
                    # positivo gira a la IZQUIERDA (regla de la mano derecha con z hacia arriba),
                    # por eso el signo menos.
                    giro = -m["dir"] / 100.0 * P.GIRO_MAX
                    v = m["vel"] / 100.0 * P.VEL_MAX * (TURBO if m["boton"] else 1.0)
                    c.aplicar(giro, v)
                    c.t_boton = c.t_boton + dt if m["boton"] else 0.0
                    if c.t_boton >= BOTON_REAPARECER_S:
                        c.reaparecer(self.carros, "botón")
            else:
                self._pure_pursuit(c, dt)

            # Vuelco / atasco / fuera de la pista -> reaparecer. Los tiempos evitan falsas alarmas:
            # un carro que roza un muro 0,5 s o que el jugador frena a propósito no reaparece.
            c.t_volcado = c.t_volcado + dt if c.arriba < 0.5 else 0.0
            # Atascado = pide acelerar pero no avanza (contra un muro u otro carro) por 2,5 s. Un
            # autónomo que ESPERA detrás de otro (pide 0) también cuenta, pero con 6 s de paciencia:
            # si el de adelante es un jugador frenado y el otro lado sigue ocupado, no se queda ahí
            # para siempre.
            if c.es_jugador:
                quiere_moverse, paciencia = abs(c.v_cmd) > 0.3 and not c.failsafe, 2.5
            else:
                quiere_moverse, paciencia = True, (2.5 if abs(c.v_cmd) > 0.3 else 6.0)
            c.t_atascado = c.t_atascado + dt if quiere_moverse and abs(c.vel) < 0.08 else 0.0
            if c.t_volcado > 1.0:
                c.reaparecer(self.carros, "volcado")
            elif c.t_atascado > paciencia:
                c.reaparecer(self.carros, "atascado")
            elif abs(c.d) > P.ANCHO / 2 + 0.5 or c.z < -0.5:
                c.reaparecer(self.carros, "fuera de la pista")

    def _posiciones(self):
        orden = sorted(self.carros, key=lambda c: -c.distancia)
        return {c.id: i + 1 for i, c in enumerate(orden)}

    def _armar_estado(self, t: float) -> str:
        pos = self._posiciones()
        return json.dumps({"tipo": "estado", "t": round(t, 3),
                           "pista": {"nombre":P.NOMBRE,"perimetro":round(P.PERIMETRO,3),"ancho":P.ANCHO,"sectores":3},
                           "carros": [c.dict_estado(pos[c.id]) for c in self.carros]},
                          separators=(",", ":"))

    def _foto(self, t: float, fps: float, grabar: bool, fin: bool) -> dict:
        """Lo que necesita el camarógrafo para dibujar un cuadro (poses + datos de la tabla)."""
        pos = self._posiciones()
        carros = []
        for c in self.carros:
            juntas = [s[0] for s in p.getJointStates(c.body, P.JUNTAS_MOVILES)]
            carros.append({"id": c.id, "pos": c.pos, "orn": c.orn, "yaw": c.yaw, "juntas": juntas,
                           "color": c.color, "estilo": c.estilo, "vuelta": c.vuelta, "mejor": c.mejor,
                           "posicion": pos[c.id], "vel": c.vel, "conectado": c.conectado,
                           "failsafe": c.failsafe, "reapariciones": c.reapariciones})
        return {"t": t, "fps": fps, "clientes": len(self.conexiones), "carros": carros,
                "grabar": grabar, "fin": fin}

    def bucle_fisica(self):
        """Hilo de física: pasos de 1/240 s sincronizados con el reloj real.

        En vez de "step(); sleep(1/240)" (que acumula el tiempo que tarda cada paso y se va atrasando)
        se lleva la cuenta de cuántos pasos DEBERÍAN haberse dado desde el arranque y se dan los que
        falten: si un paso tardó de más, el siguiente ciclo da dos seguidos y se recupera. Si el atraso
        pasa de 0,2 s (la máquina estuvo ocupada) no se intenta recuperar todo de golpe, se "resincroniza".
        """
        try:
            self._bucle_fisica()
        except Exception:
            # Si la física se rompe, este hilo muere en silencio pero el WebSocket y el latido siguen
            # vivos: el healthcheck (TCP 8765) y el admin verían "OK" una pista congelada. Por eso se
            # imprime el error y se detiene el servidor entero: el contenedor termina y Docker lo
            # reinicia (restart: unless-stopped), que es lo que un fallo así necesita.
            import traceback
            traceback.print_exc()
            print("[pista] ERROR: el hilo de física se detuvo; se cierra el servidor para reiniciarlo",
                  flush=True)
            self.error_fisica = True
            if self.loop is not None and self.ev_parar is not None:
                self.loop.call_soon_threadsafe(self.ev_parar.set)

    def _bucle_fisica(self):
        dt = 1.0 / HZ_FISICA
        pasos_foto = max(1, HZ_FISICA // FPS_VIDEO)
        t0 = time.perf_counter()
        n = 0
        ventana_t, ventana_n, fps = t0, 0, 0.0
        fin_grab = GRABAR_DESDE_S + GRABAR_S
        grabacion_terminada = GRABAR_S <= 0
        while not self.parar.is_set():
            ahora = time.perf_counter()
            debidos = int((ahora - t0) / dt)
            atraso = (debidos - n) * dt
            self.atraso_max = max(self.atraso_max, atraso)
            if debidos - n > int(0.2 * HZ_FISICA):
                t0 = ahora - n * dt      # resincronizar: se pierde ese tiempo en vez de acelerar
                debidos = n + 1
                self.recuperaciones += 1
            while n < debidos:
                t = n * dt
                if n % PASOS_CONTROL == 0:
                    self._controlar(t, dt * PASOS_CONTROL)
                p.stepSimulation()
                n += 1
                self.pasos = n
                if n % PASOS_ESTADO == 0:
                    self.ultimo_estado = self._armar_estado(t)
                    if self.loop is not None:
                        self.loop.call_soon_threadsafe(self._difundir, self.ultimo_estado)
                if n % pasos_foto == 0:
                    grabar = not grabacion_terminada and GRABAR_DESDE_S <= t < fin_grab
                    fin = grabar and t + pasos_foto * dt >= fin_grab
                    if fin:
                        grabacion_terminada = True
                    # Sin grabar, el cuadro en vivo es descartable: si el camarógrafo todavía está con
                    # el anterior no se le acumulan fotos viejas. Grabando, van todas (el video debe
                    # durar lo grabado aunque el render se atrase un poco).
                    if grabar or self.cola.empty():
                        self.cola.put(self._foto(t, fps, grabar, fin))
            # fps REALES de la física: pasos dados en el último segundo de reloj (si la máquina no da
            # abasto, aquí se ve menos de 240 aunque el código "pida" 240).
            if ahora - ventana_t >= 1.0:
                fps = (n - ventana_n) / (ahora - ventana_t)
                ventana_t, ventana_n = ahora, n
                self.fps = fps
            espera = t0 + (n + 1) * dt - time.perf_counter()
            if espera > 0:
                time.sleep(espera)

    # ------------------------------------------------------------------ red (asyncio)
    def _difundir(self, texto: str):
        from websockets.asyncio.server import broadcast
        # broadcast no espera a nadie: si un cliente está lento (su buffer de salida lleno), se salta
        # ese mensaje para él en vez de frenar a los demás. Para un estado que se repite 20 veces por
        # segundo eso es lo correcto: un estado viejo no sirve de nada.
        broadcast(self.conexiones, texto)
        self.estados_enviados += 1

    @staticmethod
    def _color(valor, defecto):
        """Acepta [r,g,b] de 0 a 1 o de 0 a 255."""
        try:
            c = [float(v) for v in valor][:3]
            if len(c) != 3:
                return defecto
            if max(c) > 1.0:
                c = [v / 255.0 for v in c]
            return tuple(limitar(v, 0.0, 1.0) for v in c)
        except (TypeError, ValueError):
            return defecto

    @staticmethod
    def _num_jugador(m: dict) -> int:
        """El campo "jugador" como entero; 0 si falta o no es un número (nunca lanza)."""
        try:
            return int(m.get("jugador", 0))
        except (TypeError, ValueError):
            return 0

    async def _tomar_jugador(self, ws, m: dict):
        j = self._num_jugador(m)
        if j not in (1, 2, 3):
            await ws.send(json.dumps({"tipo": "error", "motivo": "jugador debe ser 1, 2 o 3"}))
            return None
        viejo = self.duenos[j]
        if viejo is not None and viejo is not ws:
            # Se reconectó (o hay dos clientes con el mismo número): gana el último. El carro NO se
            # crea de nuevo: conserva su lugar en la pista y sus vueltas.
            print(f"[pista] player-{j}: nueva conexión reemplaza a la anterior", flush=True)
            self.duenos[j] = ws
            try:
                await viejo.close(4001, "otro cliente tomó este carro")
            except Exception:
                pass
        self.duenos[j] = ws
        c = self.por_id[f"player-{j}"]
        c.color = self._color(m.get("color"), c.color)
        if m.get("estilo") in P.ESTILOS:
            c.estilo = m["estilo"]
        if isinstance(m.get("nombre"), str) and m["nombre"].strip():
            c.nombre = m["nombre"].strip()[:24]
        self.seq[j] = protocolo.ContadorSecuencia()
        self.jitter[j] = protocolo.Jitter()
        print(f"[pista] player-{j} conectado desde {ws.remote_address}: {c.nombre}, "
              f"estilo {c.estilo}, color {c.color}", flush=True)
        await ws.send(json.dumps({"tipo": "bienvenida", "id": c.id, "color": c.color, "estilo": c.estilo,
                                  "vuelta": c.vuelta}))
        return j

    async def atender(self, ws):
        """Una conexión WebSocket (corre como tarea de asyncio, una por cliente)."""
        self.conexiones.add(ws)
        jugador = None
        try:
            # Lo primero: la forma de la pista, para que cualquier cliente (jugador, emulador de
            # ESP32, observador) pueda dibujarla o hacer pure pursuit sin tenerla escrita a mano.
            await ws.send(self.mensaje_pista)
            async for texto in ws:
                try:
                    m = json.loads(texto)
                    tipo = m.get("tipo")
                except (ValueError, AttributeError):
                    self.msg_invalidos += 1
                    continue
                if tipo == "ping":
                    # Eco inmediato con el mismo t_ms: el jugador resta y obtiene el RTT (ida y vuelta).
                    await ws.send(json.dumps({"tipo": "pong", "t_ms": m.get("t_ms")}))
                elif tipo == "control":
                    if jugador is None and self._num_jugador(m) in (1, 2, 3):
                        jugador = await self._tomar_jugador(ws, m)   # control sin "hola": se acepta igual
                    j = self._num_jugador(m) if "jugador" in m else (jugador or 0)
                    if jugador is None or j != jugador or self.duenos[jugador] is not ws:
                        self.ctrl_rechazados += 1     # observador, o carro ajeno: se ignora
                        continue
                    try:
                        mando = {"dir": limitar(int(m.get("dir", 0)), -100, 100),
                                 "vel": limitar(int(m.get("vel", 0)), -100, 100),
                                 "boton": 1 if m.get("boton") else 0, "t": time.monotonic()}
                    except (TypeError, ValueError):
                        self.msg_invalidos += 1
                        continue
                    mando["failsafe"] = bool(m.get("failsafe")) or (isinstance(m.get("seq"), int) and m["seq"] < 0)
                    self.mandos[jugador] = mando
                    self.ctrl_cuenta[jugador] += 1
                    # El "vel 0" que manda el player en SU failsafe (ESP32 muda) trae seq negativo y el
                    # t_ms del reloj del player, no los del ESP32: si entrara en el contador, el salto de
                    # -k a la seq real del ESP32 al volver contaría miles de "perdidos" falsos, y mezclar
                    # dos relojes daría un jitter enorme. Se aplica como mando (frena el carro), pero no
                    # se mide: pérdidas y jitter son solo del control que de verdad viene del ESP32.
                    if m.get("failsafe") or (isinstance(m.get("seq"), int) and m["seq"] < 0):
                        continue
                    if isinstance(m.get("seq"), int):
                        self.seq[jugador].registrar(m["seq"])
                    if isinstance(m.get("t_ms"), (int, float)):
                        self.jitter[jugador].registrar(m["t_ms"], lab.ms())
                elif tipo == "hola":
                    # Una misma conexión que cambia de carro suelta el anterior.
                    if jugador is not None and self._num_jugador(m) != jugador and self.duenos[jugador] is ws:
                        self.duenos[jugador] = None
                    jugador = await self._tomar_jugador(ws, m)
                elif tipo == "observador":
                    self.observadores.add(ws)
                    await ws.send(json.dumps({"tipo": "bienvenida", "id": None, "rol": "observador"}))
                else:
                    self.msg_invalidos += 1
        except Exception as e:      # conexión cortada de golpe, etc.: no debe tumbar el servidor
            if "ConnectionClosed" not in type(e).__name__:
                print(f"[pista] error en una conexión: {type(e).__name__}: {e}", flush=True)
        finally:
            self.conexiones.discard(ws)
            self.observadores.discard(ws)
            if jugador is not None and self.duenos[jugador] is ws:
                # Se fue: el carro queda donde está; el failsafe lo frena en 1 s. Si vuelve, lo recupera.
                self.duenos[jugador] = None
                print(f"[pista] player-{jugador} desconectado (su carro queda en la pista)", flush=True)

    # ------------------------------------------------------------------ métricas
    async def metricas(self, mq):
        """Cada 2 s: métricas al broker MQTT del admin (si está) y, cada 10 s, una línea al log."""
        antes = dict(self.ctrl_cuenta)
        estados_antes = self.estados_enviados
        t_antes = time.monotonic()
        k = 0
        while not self.parar.is_set():
            await asyncio.sleep(2.0)
            ahora = time.monotonic()
            dt = ahora - t_antes
            hz = {f"player-{j}": round((self.ctrl_cuenta[j] - antes[j]) / dt, 1) for j in (1, 2, 3)}
            datos = {
                "fps_fisica": round(getattr(self, "fps", 0.0), 1),
                "atraso_max_ms": round(1000 * self.atraso_max, 1),
                "resincronizaciones": self.recuperaciones,
                "clientes": len(self.conexiones),
                "observadores": len(self.observadores),
                "jugadores_conectados": [f"player-{j}" for j in (1, 2, 3) if self.duenos[j] is not None],
                "control_hz": hz,
                "control_perdidos": {f"player-{j}": self.seq[j].perdidos for j in (1, 2, 3)},
                "jitter_control_ms": {f"player-{j}": round(self.jitter[j].j, 1) for j in (1, 2, 3)},
                "control_rechazados": self.ctrl_rechazados,
                "mensajes_invalidos": self.msg_invalidos,
                "estado_hz": round((self.estados_enviados - estados_antes) / dt, 1),
                "failsafe": {c.id: c.failsafe for c in self.carros if c.es_jugador},
                "vueltas": {c.id: c.vuelta for c in self.carros},
                "mejor_vuelta_s": {c.id: (round(c.mejor, 2) if c.mejor else None) for c in self.carros},
                "reapariciones": {c.id: c.reapariciones for c in self.carros},
            }
            self.atraso_max = 0.0
            antes, estados_antes, t_antes = dict(self.ctrl_cuenta), self.estados_enviados, ahora
            if mq is not None:
                mq.publicar(datos)
            k += 1
            if k % 5 == 0:
                print(f"[pista] fisica {datos['fps_fisica']} Hz, atraso max {datos['atraso_max_ms']} ms, "
                      f"clientes {datos['clientes']}, control {hz}, vueltas {datos['vueltas']}, "
                      f"mqtt {'si' if mq is not None and mq.conectado else 'no'}", flush=True)

    # ------------------------------------------------------------------ HTTP
    def iniciar_http(self):
        srv = self

        class Manejador(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):      # sin una línea de log por cada cuadro pedido
                pass

            def _responder(self, cuerpo: bytes, tipo: str):
                self.send_response(200)
                self.send_header("Content-Type", tipo)
                self.send_header("Content-Length", str(len(cuerpo)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(cuerpo)

            def do_GET(self):
                ruta = self.path.split("?")[0]
                if ruta in ("/cuadro.jpg", "/detalle.jpg"):
                    try:
                        with open(srv.ruta_cuadro if ruta == "/cuadro.jpg" else srv.ruta_cuadro.replace(".jpg","_detalle.jpg"), "rb") as f:
                            self._responder(f.read(), "image/jpeg")
                    except OSError:
                        self.send_error(503, "todavia no hay cuadro")
                elif ruta == "/estado.json":
                    self._responder(srv.ultimo_estado.encode(), "application/json")
                elif ruta in ("/", "/index.html"):
                    self._responder(PAGINA.encode("utf-8"), "text/html; charset=utf-8")
                else:
                    self.send_error(404)

        http_srv = http.server.ThreadingHTTPServer(("0.0.0.0", PUERTO_HTTP), Manejador)
        http_srv.daemon_threads = True
        threading.Thread(target=http_srv.serve_forever, daemon=True, name="http").start()
        print(f"[pista] visor HTTP en http://0.0.0.0:{PUERTO_HTTP}/", flush=True)

    # ------------------------------------------------------------------ arranque
    async def correr(self):
        from websockets.asyncio.server import serve

        self.loop = asyncio.get_running_loop()
        ev_parar = self.ev_parar = asyncio.Event()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                self.loop.add_signal_handler(sig, ev_parar.set)
            except (NotImplementedError, RuntimeError):
                pass   # Windows: Ctrl+C llega como KeyboardInterrupt
        mq = lab.Metricas("track-server")
        # compression=None: los mensajes son chicos y la red es una LAN; comprimir gasta CPU en el
        # servidor y suma latencia por nada. asyncio ya pone TCP_NODELAY (sin esperar a juntar
        # paquetes chicos, el algoritmo de Nagle), clave para un control en tiempo real.
        async with serve(self.atender, "0.0.0.0", PUERTO_WS, compression=None,
                         ping_interval=5, ping_timeout=5, max_size=2 ** 16):
            print(f"[pista] WebSocket en ws://0.0.0.0:{PUERTO_WS}", flush=True)
            hilo = threading.Thread(target=self.bucle_fisica, daemon=True, name="fisica")
            hilo.start()
            tarea = asyncio.create_task(self.metricas(mq))
            try:
                await ev_parar.wait()
            finally:
                self.parar.set()
                tarea.cancel()
                hilo.join(timeout=2)
                mq.cerrar()


PAGINA = """<!doctype html><html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>NexoLab · Pista · Zona Gamer (VLAN 1)</title>
<style>body{margin:0;background:#14161c;color:#e8e9ee;font-family:system-ui,sans-serif}
main{max-width:1280px;margin:auto;padding:12px 16px}img{width:100%;height:auto;display:block;border-radius:6px}
h1{font-size:1.25rem;margin:6px 0 2px}p{margin:4px 0;color:#b9bdc8;font-size:.9rem;line-height:1.5}
small{color:#969ba8}b{color:#e8e9ee;font-weight:600}
nav{display:flex;gap:8px;flex-wrap:wrap;margin:8px 0 12px}nav a{color:#e8e9ee;text-decoration:none;font-size:.82rem;
border:1px solid #2c303a;border-radius:8px;padding:5px 10px;background:#1b1e26}nav a:hover{border-color:#e8b930}
nav a.aqui{border-color:#e8b930;color:#e8b930}#e{font-family:ui-monospace,monospace}</style></head><body><main>
<nav id="nav"><a data-p="8080">Dashboard del admin</a><a data-p="8010" class="aqui">Pista</a><a data-p="8011">Spot</a>
<a data-p="8012">Pepper</a><a data-p="8013">NAO</a></nav>
<h1>Servidor de pista · Zona Gamer (VLAN 1)</h1>
<p>Simulación en <b>PyBullet</b> (física a 240 Hz, sin ventana) de una circuito técnico Nexo GP con seis carros:
<b>P1, P2 y P3</b> los manejan los contenedores <b>player-1..3</b>, que reciben el mando de su ESP32 por UDP
(aquí, ESP32 emulados con piloto automático) y se lo pasan al servidor por WebSocket; <b>A1, A2 y A3</b> son
autónomos que siguen la línea central. A la derecha, la tabla de posiciones y una cámara que rota entre los carros.
Si un jugador deja de recibir su mando durante 1 s, su carro frena (failsafe).</p>
<img id="c" src="/cuadro.jpg" alt="vista cenital de la pista">
<p><small id="e">cargando...</small></p></main>
<script>
// Enlaces a los otros visores: mismo host con el que se abrió esta página, otro puerto publicado por Docker.
document.querySelectorAll('#nav a').forEach(a => a.href = location.protocol + '//' + (location.hostname || 'localhost') + ':' + a.dataset.p + '/');
// Pide el cuadro nuevo apenas termina de cargar el anterior (sin acumular pedidos si la red va lenta).
const img = document.getElementById('c');
img.onload = img.onerror = () => setTimeout(() => img.src = '/cuadro.jpg?t=' + Date.now(), 150);
setInterval(async () => {
  try {
    const e = await (await fetch('/estado.json')).json();
    document.getElementById('e').textContent = 't = ' + e.t + ' s | ' +
      e.carros.map(c => c.id + ': ' + c.vuelta + ' vta' + (c.failsafe && c.id.startsWith('player') ? ' (frenado)' : '')).join(' | ');
  } catch (err) {}
}, 1000);
</script></body></html>"""


def main():
    print("[pista] arrancando servidor de pista (PyBullet DIRECT)", flush=True)
    # Plano de administración: rutas hacia la VLAN 3 por el router y latido UDP. Nada de esto es
    # obligatorio: sin router ni admin la pista funciona igual (zona aislada).
    lab.configurar_rutas()
    lab.Latido("track-server").iniciar()

    srv = ServidorPista()
    srv.crear_mundo()
    srv.fps = 0.0
    os.makedirs(RESULTADOS, exist_ok=True)
    import render_pista
    ctx = mp.get_context("spawn")
    srv.cola = ctx.Queue()
    camara = ctx.Process(target=render_pista.correr, name="camara",
                         args=(srv.cola, [c.id for c in srv.carros], RESULTADOS, srv.ruta_cuadro, FPS_VIDEO),
                         daemon=True)
    camara.start()
    srv.iniciar_http()
    if GRABAR_S > 0:
        print(f"[pista] se grabarán {GRABAR_S:.0f} s de video desde t={GRABAR_DESDE_S:.0f} s "
              f"en {RESULTADOS}/pista.mp4", flush=True)
    try:
        asyncio.run(srv.correr())
    except KeyboardInterrupt:
        srv.parar.set()
    finally:
        srv.cola.put(None)          # avisa al camarógrafo que cierre el video (si quedó abierto)
        camara.join(timeout=10)
        print("[pista] fin", flush=True)
    if srv.error_fisica:
        sys.exit(1)     # código de error: en "docker ps -a" se ve que no fue un cierre normal


if __name__ == "__main__":
    main()
