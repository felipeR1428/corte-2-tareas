"""emulador_esp32.py - Las ESP32 del tema 11, pero virtuales: hilos que hablan UDP/MQTT de verdad con
exactamente los mismos mensajes que el firmware (ver comun/protocolo.py y el contrato del tema).

Para qué sirve: el stack completo necesita 7 ESP32 (3 maestras gamer, 3 maestras robot y la esclava de
LEDs). Con este emulador se puede levantar todo sin hardware (en el docker-compose, un contenedor por
ESP32 que falte) o probar un solo contenedor desde Windows. Lo que mide NO es una medición de hardware:
la latencia entre contenedores o por 127.0.0.1 no tiene nada que ver con la del WiFi de una ESP32 real.

Roles (--rol, se pueden poner varios a la vez; cada uno es una "placa" con su propio socket y reloj):
  ctrl-1, ctrl-2, ctrl-3        maestra gamer: CTRL,<n>,<seq>,<t_ms>,<dir>,<vel>,<boton> a --hz
  ctrl-spot, ctrl-pepper,       maestra robot: JOINTS,<robot>,<seq>,<t_ms>,<j1>,<j2>,<j3>,<boton> a --hz
  ctrl-nao
  esclava                       se suscribe a lab/estado/+ (MQTT) y "enciende" 6 LEDs en la consola

Todas las placas, si se da --admin, mandan HB,<rol>,<seq>,<t_ms> cada 1 s al UDP 5300 del admin; las
maestras además mandan PING,<rol>,<seq>,<t_ms> cada 2 s y miden el RTT con el PONG que vuelve. Son los
mismos períodos que el firmware (PERIODO_HB_MS y PERIODO_PING_MS de esp32_maestra/config.h); la
esclava física no manda PING (solo late), así que la emulada tampoco.

Por la consola salen las mismas líneas que la ESP32 imprime por el puerto serie (115200 baudios):
  maestra: ENVIADO,<datagrama> a 5 Hz, RTT,<ms> con cada PONG, ESTADO,<rol>,<ip>,<rssi>,<wifi 0/1> cada 2 s
  esclava: LEDS,<p1>,<p2>,<p3>,<spot>,<pepper>,<nao> al cambiar algo y cada 2 s, ESTADO,esclava,<ip>,<rssi>,<mqtt 0/1>
Con una sola placa salen tal cual; con varias se antepone "[rol] " para saber de cuál es cada línea.

Ejemplos (Windows, desde la carpeta del tema; solo biblioteca estándar, paho-mqtt solo para la esclava):
  entorno\\Scripts\\python.exe emulador\\emulador_esp32.py --rol ctrl-1 --destino 127.0.0.1:5001
  entorno\\Scripts\\python.exe emulador\\emulador_esp32.py --rol ctrl-1 ctrl-2 ctrl-3 --destino 127.0.0.1 --admin 127.0.0.1
  entorno\\Scripts\\python.exe emulador\\emulador_esp32.py --rol ctrl-1 --destino 127.0.0.1:5001 --piloto --pista ws://127.0.0.1:8765
  entorno\\Scripts\\python.exe emulador\\emulador_esp32.py --rol esclava --admin 127.0.0.1
  entorno\\Scripts\\python.exe emulador\\emulador_esp32.py --rol ctrl-spot --destino 127.0.0.1:5101 --perdida 10 --duracion 30
En el docker-compose (lo usa así la infraestructura; no cambiar el formato):
  --rol ctrl-1 --destino 192.168.10.21:5000 --admin 192.168.30.10
"""

import argparse
import base64
import hashlib
import json
import math
import os
import random
import select
import signal
import socket
import struct
import sys
import threading
import time
from urllib.parse import urlparse

# comun/ está en la carpeta del tema (un nivel arriba de emulador/) o al lado, dentro de la imagen.
AQUI = os.path.dirname(os.path.abspath(__file__))
for _r in (os.path.normpath(os.path.join(AQUI, "..")), AQUI):
    if os.path.isdir(os.path.join(_r, "comun")) and _r not in sys.path:
        sys.path.insert(0, _r)

from comun import protocolo as pr  # noqa: E402

# La consola de Windows a veces no está en UTF-8: sin esto, imprimir los "LEDs" (●◐○) lanzaría
# UnicodeEncodeError y tumbaría el hilo de la esclava.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

ROLES_GAMER = {"ctrl-1": 1, "ctrl-2": 2, "ctrl-3": 3}
ROLES_ROBOT = {"ctrl-spot": "spot", "ctrl-pepper": "pepper", "ctrl-nao": "nao"}
ROLES = list(ROLES_GAMER) + list(ROLES_ROBOT) + ["esclava"]
# Puertos publicados en el PC (contrato): si --destino trae solo la IP se usa el de cada rol. Así
# "--rol ctrl-1 ctrl-2 ctrl-3 --destino 127.0.0.1" llega a los tres players publicados en el PC.
PUERTO_PC = {"ctrl-1": 5001, "ctrl-2": 5002, "ctrl-3": 5003,
             "ctrl-spot": 5101, "ctrl-pepper": 5102, "ctrl-nao": 5103}
LEDS = ("player-1", "player-2", "player-3", "sim-spot", "sim-pepper", "sim-nao")
SIMBOLO_LED = {"OK": "●", "LENTO": "◐", "CAIDO": "○", "?": "·"}

RSSI_FIJO = -40          # dBm: no hay radio; se reporta "señal buena", como en el emulador del tema 10
PERIODO_HB_S = 1.0       # HB al admin (contrato)
PERIODO_PING_S = 2.0     # PING al admin, solo las maestras (PERIODO_PING_MS del firmware)
PERIODO_ESTADO_S = 2.0   # línea ESTADO por serie (contrato)
HZ_ENVIADO = 5.0         # ENVIADO por serie a 5 Hz, no a los 20 Hz completos (contrato)

_candado_consola = threading.Lock()
_con_prefijo = False


def serie(rol, linea):
    """Imprime una línea "del puerto serie" de la placa `rol` (con prefijo si hay varias placas)."""
    with _candado_consola:
        try:
            print(f"[{rol}] {linea}" if _con_prefijo else linea, flush=True)
        except (OSError, ValueError):
            # Consola cerrada o tubería rota (p. ej. "| head"): la placa sigue mandando igual; una
            # ESP32 real tampoco deja de transmitir porque nadie mire su monitor serie.
            pass


def ip_local_hacia(destino_ip):
    """La IP propia con la que se sale hacia `destino_ip` (lo que la ESP32 sabría por WiFi.localIP()).

    connect() en un socket UDP no manda nada: solo hace que el sistema elija la ruta y la interfaz,
    y getsockname() dice qué IP quedó. Si no hay ruta se devuelve 0.0.0.0 (como una placa sin WiFi).
    """
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect((destino_ip, 9))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "0.0.0.0"


# ------------------------------------------------------------------------------------------------
# Cliente WebSocket mínimo (solo biblioteca estándar) para el modo --piloto
# ------------------------------------------------------------------------------------------------
class ClienteWS:
    """Lo justo del RFC 6455 para leer el estado de la pista: handshake HTTP, tramas de texto,
    fragmentación, ping/pong y cierre.

    Por qué no la librería websockets: el emulador tiene que correr con el Python de cualquier PC sin
    instalar nada (y en el contenedor del emulador, con la misma imagen liviana). Un cliente que solo
    LEE JSON es corto de escribir: lo único raro es que el cliente debe enmascarar (XOR con 4 bytes
    al azar) todo lo que manda, y el servidor nunca enmascara lo que responde.
    """

    GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"  # constante fija del RFC 6455 para el handshake

    def __init__(self, url, timeout=5.0):
        u = urlparse(url)
        if u.scheme != "ws":
            raise ValueError("solo ws:// (sin TLS)")
        self.host, self.puerto = u.hostname, u.port or 80
        self.ruta = (u.path or "/") + (f"?{u.query}" if u.query else "")
        self.sock = socket.create_connection((self.host, self.puerto), timeout=3.0)
        clave = base64.b64encode(os.urandom(16)).decode()
        pedido = (f"GET {self.ruta} HTTP/1.1\r\nHost: {self.host}:{self.puerto}\r\n"
                  "Upgrade: websocket\r\nConnection: Upgrade\r\n"
                  f"Sec-WebSocket-Key: {clave}\r\nSec-WebSocket-Version: 13\r\n\r\n")
        self.sock.sendall(pedido.encode())
        resp = b""
        while b"\r\n\r\n" not in resp:
            trozo = self.sock.recv(1024)
            if not trozo:
                raise ConnectionError("el servidor cerró durante el handshake")
            resp += trozo
            if len(resp) > 16384:
                raise ConnectionError("respuesta de handshake demasiado larga")
        cabecera, self._resto = resp.split(b"\r\n\r\n", 1)
        lineas = cabecera.decode("latin-1").split("\r\n")
        if " 101 " not in lineas[0] + " ":
            raise ConnectionError(f"handshake rechazado: {lineas[0]}")
        # El servidor demuestra que entendió el pedido devolviendo SHA1(clave + GUID) en base64.
        esperado = base64.b64encode(hashlib.sha1((clave + self.GUID).encode()).digest()).decode()
        acepta = [ln.split(":", 1)[1].strip() for ln in lineas[1:]
                  if ln.lower().startswith("sec-websocket-accept:")]
        if acepta != [esperado]:
            raise ConnectionError("Sec-WebSocket-Accept no coincide")
        self.sock.settimeout(timeout)

    def _leer(self, n):
        datos = self._resto[:n]
        self._resto = self._resto[n:]
        while len(datos) < n:
            trozo = self.sock.recv(max(4096, n - len(datos)))
            if not trozo:
                raise ConnectionError("conexión cerrada")
            datos += trozo
        if len(datos) > n:
            self._resto = datos[n:] + self._resto
            datos = datos[:n]
        return datos

    def _enviar_trama(self, opcode, carga):
        mascara = os.urandom(4)
        n = len(carga)
        if n < 126:
            cab = struct.pack("!BB", 0x80 | opcode, 0x80 | n)
        elif n < 65536:
            cab = struct.pack("!BBH", 0x80 | opcode, 0x80 | 126, n)
        else:
            cab = struct.pack("!BBQ", 0x80 | opcode, 0x80 | 127, n)
        enmascarada = bytes(b ^ mascara[i % 4] for i, b in enumerate(carga))
        self.sock.sendall(cab + mascara + enmascarada)

    def enviar_texto(self, texto):
        self._enviar_trama(0x1, texto.encode("utf-8"))

    def recibir_texto(self):
        """Devuelve el siguiente mensaje de texto completo (une fragmentos; contesta los ping)."""
        partes = []
        while True:
            b0, b1 = self._leer(2)
            fin, opcode, n = b0 & 0x80, b0 & 0x0F, b1 & 0x7F
            if n == 126:
                n = struct.unpack("!H", self._leer(2))[0]
            elif n == 127:
                n = struct.unpack("!Q", self._leer(8))[0]
            mascara = self._leer(4) if b1 & 0x80 else None
            carga = self._leer(n)
            if mascara:
                carga = bytes(b ^ mascara[i % 4] for i, b in enumerate(carga))
            if opcode == 0x8:
                raise ConnectionError("el servidor cerró el WebSocket")
            if opcode == 0x9:
                self._enviar_trama(0xA, carga)  # ping del servidor -> pong con la misma carga
                continue
            if opcode == 0xA:
                continue
            if opcode in (0x1, 0x0, 0x2):
                partes.append(carga)
                if fin:
                    return b"".join(partes).decode("utf-8", errors="replace")

    def cerrar(self):
        try:
            self._enviar_trama(0x8, struct.pack("!H", 1000))
        except OSError:
            pass
        self.sock.close()


class Observador(threading.Thread):
    """Mira la pista sin tener carro: se conecta al servidor, manda {"tipo":"observador"} y guarda
    la línea central de la pista ({"tipo":"pista","centro":[[x,y],...]}) y el último `estado`.

    Por qué "observador" y no "hola": un `hola` registraría un jugador más (otro carro en la pista)
    y el servidor lo trataría como player; el piloto automático solo necesita VER dónde está el
    carro que maneja el player-N, como lo ve el humano en la pantalla. Lo que maneja de verdad sigue
    yendo por el camino normal: CTRL por UDP -> player-N -> servidor.
    """

    def __init__(self, url):
        super().__init__(daemon=True, name="observador")
        self.url = url
        self.candado = threading.Lock()
        self.centro = None        # [(x, y), ...] línea central de la pista, en metros
        self.carros = {}          # id -> dict del último estado
        self.t_estado = 0.0       # time.monotonic() del último estado recibido
        self.conectado = False
        self.detener = threading.Event()
        # Respaldo por si el servidor no manda la línea central: se "aprende" la pista grabando el
        # recorrido del autónomo auto-1 (que siempre va por la pista) hasta que cierra una vuelta.
        self.rastro = []
        self.pista_aprendida = False

    def run(self):
        espera = 1.0
        while not self.detener.is_set():
            try:
                ws = ClienteWS(self.url)
                ws.enviar_texto(json.dumps({"tipo": "observador"}))
                self.conectado = True
                espera = 1.0
                serie("piloto", f"observando la pista en {self.url}")
                while not self.detener.is_set():
                    self._atender(ws.recibir_texto())
                ws.cerrar()
            except (OSError, ConnectionError, ValueError) as e:
                if self.conectado:
                    serie("piloto", f"se perdió la pista ({e}); manejo con el patrón seno")
                self.conectado = False
            self.detener.wait(espera)
            espera = min(10.0, espera * 2)  # espera creciente, como el player

    def _atender(self, texto):
        try:
            m = json.loads(texto)
        except ValueError:
            return
        if not isinstance(m, dict):
            return
        # La pista puede venir sola (mensaje "pista") o dentro de "estado" (lo decide el servidor).
        # El servidor de pista la manda como "linea_central" en su mensaje {"tipo":"pista"}; se
        # aceptan también "centro" y la pista dentro de "estado" por si otro servidor lo hace distinto.
        centro = m.get("linea_central") or m.get("centro")
        if not centro and isinstance(m.get("pista"), dict):
            centro = m["pista"].get("linea_central") or m["pista"].get("centro")
        if centro:
            try:
                pts = [(float(p[0]), float(p[1])) for p in centro]
                if len(pts) >= 3:
                    with self.candado:
                        if self.centro is None:
                            serie("piloto", f"pista recibida: {len(pts)} puntos")
                        self.centro = pts
            except (TypeError, ValueError, IndexError):
                pass
        if m.get("tipo") == "estado":
            with self.candado:
                for c in m.get("carros") or []:
                    if isinstance(c, dict) and "id" in c:
                        self.carros[c["id"]] = c
                self.t_estado = time.monotonic()
                if self.centro is None:
                    self._aprender(self.carros.get("auto-1"))

    PASO_RASTRO = 0.3  # m entre puntos grabados del recorrido de auto-1

    def _aprender(self, auto):
        """Graba un punto cada 0.3 m del auto-1; cuando vuelve cerca del primero después de haber
        recorrido bastante (más de 60 puntos = 18 m), esa vuelta cerrada pasa a ser la pista."""
        try:
            p = (float(auto["x"]), float(auto["y"]))
        except (TypeError, KeyError, ValueError):
            return
        r = self.rastro
        if r and math.dist(r[-1], p) < self.PASO_RASTRO:
            return
        r.append(p)
        if len(r) > 60 and math.dist(r[0], p) < 2 * self.PASO_RASTRO:
            self.centro = list(r)
            self.pista_aprendida = True
            serie("piloto", f"el servidor no mandó la pista: aprendida del recorrido de auto-1 "
                            f"({len(r)} puntos)")
        elif len(r) > 20000:
            r.clear()  # nunca cerró la vuelta (no es un circuito): se empieza de nuevo

    def foto(self):
        """Copia consistente (pista, carros, edad del estado en s) para el piloto."""
        with self.candado:
            return self.centro, dict(self.carros), time.monotonic() - self.t_estado


class Piloto:
    """Pure pursuit ("persecución pura") sobre la línea central de la pista.

    Es el algoritmo clásico de seguimiento de trayectorias (Coulter, CMU 1992) y se parece a lo que
    hace una persona: mira un punto de la pista unos metros adelante (distancia de anticipación Ld)
    y gira el volante lo justo para que el carro describa un arco que pase por ese punto.

        alfa  = ángulo entre hacia dónde apunta el carro y hacia dónde está el punto
        delta = atan(2 * B * sen(alfa) / Ld)      (B = batalla, distancia entre ejes)

    delta es el ángulo de dirección de las ruedas; se pasa a dir -100..100 dividiendo por el máximo
    (DELTA_MAX). Convención del contrato: dir > 0 = a la derecha. En coordenadas matemáticas
    (yaw medido antihorario) girar a la derecha baja el yaw, por eso dir = -delta (el --signo-dir
    lo invierte si el servidor usara la otra convención). La velocidad baja en las curvas, y la
    salida pasa por un filtro y un poco de ruido para que parezca un joystick movido por una mano.
    """

    DELTA_MAX = 0.5  # rad (~29°), tope de giro de las ruedas que corresponde a dir = ±100

    def __init__(self, id_carro, args, azar):
        self.id = id_carro
        self.args = args
        self.azar = azar
        self.sentido = None     # +1 recorre la lista de puntos hacia adelante, -1 al revés
        self.dir_f = 0.0        # dir filtrado (la mano no salta de golpe de un lado al otro)

    @staticmethod
    def _yaw(c):
        # yaw en radianes, como lo da PyBullet (getEulerFromQuaternion). No se intenta "adivinar"
        # grados: un yaw acumulado de varias vueltas (sin envolver a -pi..pi) también pasa de 2*pi.
        return float(c.get("yaw", 0.0))

    def _sentido_de(self, pts, c):
        """+1 o -1 según hacia dónde mira el carro c respecto del orden de los puntos."""
        x, y, yaw = float(c["x"]), float(c["y"]), self._yaw(c)
        i = min(range(len(pts)), key=lambda k: (pts[k][0] - x) ** 2 + (pts[k][1] - y) ** 2)
        a, b = pts[i], pts[(i + 1) % len(pts)]
        return 1 if math.cos(yaw) * (b[0] - a[0]) + math.sin(yaw) * (b[1] - a[1]) >= 0 else -1

    def calcular(self, pts, carros, dt):
        """Devuelve (dir, vel) o None si el carro propio todavía no aparece en el estado."""
        c = carros.get(self.id)
        if c is None or "x" not in c or "y" not in c:
            return None
        # Sentido de carrera: lo marcan los autónomos (siempre van "bien"); si no hay, el propio
        # carro la primera vez. Se fija una vez para que un trompo no haga que el piloto dé la vuelta.
        if self.sentido is None:
            autos = [a for k, a in carros.items() if k.startswith("auto") and "x" in a]
            votos = [self._sentido_de(pts, a) for a in autos] or [self._sentido_de(pts, c)]
            self.sentido = 1 if sum(votos) >= 0 else -1
        x, y, yaw = float(c["x"]), float(c["y"]), self._yaw(c)
        n = len(pts)
        perimetro = sum(math.dist(pts[k], pts[(k + 1) % n]) for k in range(n))
        ld = self.args.adelanto or max(.80, min(1.65, .70 + .35*abs(float(c.get("vel",0)))))
        # Punto más cercano y, desde ahí, se camina por la pista Ld metros en el sentido de carrera.
        i = min(range(n), key=lambda k: (pts[k][0] - x) ** 2 + (pts[k][1] - y) ** 2)
        recorrido, k, objetivo = 0.0, i, pts[i]
        for _ in range(n):
            sig = (k + self.sentido) % n
            recorrido += math.dist(pts[k], pts[sig])
            k = sig
            objetivo = pts[k]
            if recorrido >= ld and math.dist((x, y), objetivo) >= ld * 0.7:
                break
        alfa = math.atan2(objetivo[1] - y, objetivo[0] - x) - yaw
        alfa = math.atan2(math.sin(alfa), math.cos(alfa))  # a -pi..pi
        delta = math.atan2(2.0 * self.args.batalla * math.sin(alfa), ld)
        dir_obj = -self.args.signo_dir * 100.0 * delta / self.DELTA_MAX
        if abs(alfa) > math.pi / 2:
            dir_obj = math.copysign(100.0, dir_obj)  # apunta para atrás: volante a fondo
        dir_obj = max(-100.0, min(100.0, dir_obj))
        # Filtro de primer orden con tau = 0.15 s: una mano tarda un poco en mover la palanca.
        a = min(1.0, dt / 0.15)
        self.dir_f += (dir_obj - self.dir_f) * a
        ruido = self.azar.gauss(0.0, 2.0)  # el pulgar nunca está perfectamente quieto
        vel = self.args.vel_piloto * (1.0 - 0.45 * abs(self.dir_f) / 100.0)
        if abs(alfa) > math.pi / 2:
            vel = min(vel, 40.0)
        return int(round(self.dir_f + ruido)), int(round(vel + self.azar.gauss(0.0, 1.5)))


# ------------------------------------------------------------------------------------------------
# Las placas
# ------------------------------------------------------------------------------------------------
class Placa(threading.Thread):
    """Una ESP32 virtual: su propio socket UDP, su propio reloj (t_ms desde que "arrancó") y su
    propio bucle, igual que el loop() del firmware: enviar lo periódico y leer lo que llegó, sin
    quedarse bloqueada nunca (el PONG se lee con select de pocos milisegundos)."""

    def __init__(self, rol, args, destino):
        super().__init__(daemon=True, name=rol)
        self.rol = rol
        self.args = args
        self.destino = destino
        self.admin = (args.admin, args.puerto_admin) if args.admin else None
        self.t0 = time.monotonic()
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("0.0.0.0", 0))
        self.sock.setblocking(False)
        self.detener = threading.Event()
        self.seq_hb = 0
        self.seq_ping = 0
        self.pings = {}           # seq -> t_ms del PING (para descartar PONG ajenos)
        self.rtts = []
        self.pongs = 0
        self.prox_hb = self.prox_ping = self.prox_estado = time.monotonic()
        self.manda_ping = True    # la Esclava lo apaga: el firmware de la esclava no hace PING
        ref = destino[0] if destino else (args.admin or "8.8.8.8")
        self.ip = ip_local_hacia(ref)

    def t_ms(self):
        return int((time.monotonic() - self.t0) * 1000)  # como millis(): desde el arranque de la placa

    def _udp(self, texto, dire):
        try:
            self.sock.sendto(texto.encode("ascii"), dire)
            return True
        except OSError:
            # Sin ruta (router arrancando) o, en Windows, ICMP de un envío anterior: en la placa real
            # un datagrama que no sale tampoco la detiene.
            return False

    def periodicos(self, ahora):
        if self.admin and ahora >= self.prox_hb:
            self._udp(pr.armar_latido("HB", self.rol, self.seq_hb, self.t_ms()), self.admin)
            self.seq_hb += 1
            self.prox_hb += PERIODO_HB_S
        if self.admin and self.manda_ping and ahora >= self.prox_ping:
            t = self.t_ms()
            self._udp(pr.armar_latido("PING", self.rol, self.seq_ping, t), self.admin)
            self.pings[self.seq_ping] = t
            self.pings.pop(self.seq_ping - 30, None)  # no acumular PING sin respuesta para siempre
            self.seq_ping += 1
            self.prox_ping += PERIODO_PING_S
        if ahora >= self.prox_estado:
            serie(self.rol, self.linea_estado())
            self.prox_estado += PERIODO_ESTADO_S

    def linea_estado(self):
        wifi = 1 if self.ip != "0.0.0.0" else 0
        return f"ESTADO,{self.rol},{self.ip},{RSSI_FIJO},{wifi}"

    def leer_udp(self, espera):
        try:
            listo, _, _ = select.select([self.sock], [], [], max(0.0, espera))
        except (OSError, ValueError):
            time.sleep(max(0.0, espera))
            return
        if not listo:
            return
        for _ in range(100):
            try:
                datos, _ = self.sock.recvfrom(2048)
            except (BlockingIOError, ConnectionResetError):
                # ConnectionResetError: Windows avisa así que un envío anterior fue a un puerto
                # cerrado (nadie escuchando todavía). No es un error de este datagrama.
                return
            except OSError:
                return
            m = pr.leer(datos)
            if isinstance(m, pr.Latido) and m.tipo == "PONG" and m.origen == self.rol:
                t_envio = self.pings.pop(m.seq, None)
                if t_envio is not None and t_envio == m.t_ms:
                    rtt = self.t_ms() - m.t_ms
                    self.rtts.append(rtt)
                    self.pongs += 1
                    serie(self.rol, f"RTT,{rtt}")

    def paso(self, ahora):
        """Lo propio de cada tipo de placa (mandar CTRL/JOINTS, dibujar LEDs...)."""

    def run(self):
        while not self.detener.is_set():
            ahora = time.monotonic()
            self.periodicos(ahora)
            proximo = self.paso(ahora) or (ahora + 0.05)
            self.leer_udp(proximo - time.monotonic())
        self.sock.close()

    def resumen(self):
        r = {"rol": self.rol, "ip": self.ip, "hb_enviados": self.seq_hb, "pings": self.seq_ping,
             "pongs": self.pongs}
        if self.rtts:
            s = sorted(self.rtts)
            r.update(rtt_prom_ms=round(sum(s) / len(s), 2), rtt_p95_ms=s[int(0.95 * (len(s) - 1))],
                     rtt_max_ms=s[-1])
        return r


class Maestra(Placa):
    """Maestra gamer (CTRL) o robot (JOINTS) a --hz, con pérdida simulada opcional."""

    def __init__(self, rol, args, destino, observador=None):
        super().__init__(rol, args, destino)
        self.periodo = 1.0 / args.hz
        self.prox_envio = time.monotonic()
        self.prox_serie = time.monotonic()
        self.seq = 0
        self.enviados = 0
        self.descartados = 0
        # Generador propio por placa y por semilla: la pérdida es reproducible y distinta por placa.
        self.azar = random.Random(f"{args.semilla}-{rol}")
        self.jugador = ROLES_GAMER.get(rol)
        self.robot = ROLES_ROBOT.get(rol)
        self.fase = self.azar.uniform(0, 2 * math.pi)  # cada placa con su propio "pulgar"
        self.observador = observador
        self.piloto = Piloto(f"player-{self.jugador}", args, self.azar) if self.jugador else None
        self.control_ui = {"modo": "auto"}
        self.modo = None
        self.t_ultimo = time.monotonic()

    def entradas_gamer(self, t, dt):
        """(dir, vel, boton). Con piloto y pista: pure pursuit; si no, un patrón suave (seno)."""
        if self.observador is not None:
            pts, carros, edad = self.observador.foto()
            if pts and edad < 0.5:
                r = self.piloto.calcular(pts, carros, dt)
                if r is not None:
                    self._modo("piloto (pure pursuit sobre la pista)")
                    return r[0], r[1], 0
        self._modo("patrón seno")
        # Seno lento en la dirección (un "zig-zag" de ~10 s) y acelerador entre 40 y 80: el carro se
        # mueve sin quedarse quieto, y cada placa con un desfase distinto para no ir en paralelo.
        d = 60.0 * math.sin(2 * math.pi * 0.1 * t + self.fase)
        v = 60.0 + 20.0 * math.sin(2 * math.pi * 0.05 * t + self.fase)
        boton = 1 if (t % 15.0) < 0.3 else 0  # un toque de turbo cada 15 s
        return int(round(d)), int(round(v)), boton

    def _modo(self, modo):
        if modo != self.modo:
            self.modo = modo
            serie(self.rol, f"# modo: {modo}")  # '#' = comentario, no es una línea del contrato

    def entradas_robot(self, t):
        """(j1, j2, j3, boton) en grados: senos lentos (~0.1-0.2 Hz) como alguien moviendo potes."""
        # (centro, amplitud) de cada j, dentro de los rangos que manda el firmware (config.h) y que
        # acepta el robot: Spot +-20 en los tres; el codo de NAO/Pepper solo dobla de 0 a ~88 grados,
        # así que oscila alrededor de 44 (si oscilara alrededor de 0, la mitad del tiempo se recortaría).
        rango = {"spot": ((0, 18), (0, 15), (0, 15)),
                 "pepper": ((0, 60), (44, 40), (0, 30)),
                 "nao": ((0, 60), (44, 40), (0, 30))}[self.robot]
        f = (0.15, 0.11, 0.07)
        j1, j2, j3 = (c + a * math.sin(2 * math.pi * f[k] * t + self.fase + k)
                      for k, (c, a) in enumerate(rango))
        boton = 1 if (t % 12.0) < 0.5 else 0  # gesto cada 12 s (medio segundo apretado)
        return j1, j2, j3, boton

    def paso(self, ahora):
        if ahora < self.prox_envio:
            return self.prox_envio
        t = ahora - self.t0
        dt = ahora - self.t_ultimo
        self.t_ultimo = ahora
        control = self.control_ui
        if control["modo"] == "pausa":
            self.prox_envio = ahora + self.periodo
            return self.prox_envio
        if self.jugador:
            d, v, b = control["entradas"] if control["modo"] == "manual" else self.entradas_gamer(t, dt)
            linea = pr.armar_ctrl(self.jugador, self.seq, self.t_ms(), d, v, b)
        else:
            j1, j2, j3, b = control["entradas"] if control["modo"] == "manual" else self.entradas_robot(t)
            linea = pr.armar_joints(self.robot, self.seq, self.t_ms(), j1, j2, j3, b)
        # --perdida: el seq avanza igual pero el datagrama no sale. Del lado del receptor se ve
        # exactamente como una pérdida en el aire, y su ContadorSecuencia la debe contar.
        if self.args.perdida > 0 and self.azar.random() * 100.0 < self.args.perdida:
            self.descartados += 1
        elif self._udp(linea, self.destino):
            self.enviados += 1
        self.seq += 1
        if ahora >= self.prox_serie:
            serie(self.rol, f"ENVIADO,{linea}")
            self.prox_serie = ahora + 1.0 / HZ_ENVIADO
        # Reloj fijo (+= periodo) y no sleep(periodo): el tiempo de cálculo no se acumula como jitter
        # del emisor. Si la máquina se atrasó mucho (suspensión) se resincroniza en vez de ráfaga.
        self.prox_envio += self.periodo
        if self.prox_envio < ahora - 1.0:
            self.prox_envio = ahora + self.periodo
        return self.prox_envio

    def resumen(self):
        r = super().resumen()
        total = self.enviados + self.descartados
        r.update(destino=f"{self.destino[0]}:{self.destino[1]}", generados=self.seq,
                 enviados=self.enviados, descartados=self.descartados,
                 descartados_pct=round(100.0 * self.descartados / total, 2) if total else 0.0)
        return r


class Esclava(Placa):
    """ESP32 esclava: sigue lab/estado/<servicio> por MQTT y muestra los 6 LEDs en la consola."""

    def __init__(self, rol, args):
        super().__init__(rol, args, None)
        self.manda_ping = False
        self.leds = {s: "?" for s in LEDS}
        # Como el firmware (SIN_BROKER_MS): tras 5 s sin broker lo que se sabía ya no vale y los seis
        # pasan a "?" (la placa real hace además el vaivén). Al reconectar, los retenidos los reponen.
        self.t_sin_broker = time.monotonic()
        self.sin_broker = False
        self.candado = threading.Lock()
        self.cambio = False
        self.mqtt_ok = False
        self.prox_leds = time.monotonic()
        host, _, puerto = (args.mqtt or args.admin or "127.0.0.1").partition(":")
        self.broker = (host, int(puerto or 1883))
        self.cliente = None
        try:
            import paho.mqtt.client as mqtt
        except ImportError:
            serie(rol, "# paho-mqtt no está instalado: la esclava no puede seguir el broker")
            return
        cid = f"esclava-emulada-{os.getpid()}"  # distinto de la placa real, por si están las dos
        if hasattr(mqtt, "CallbackAPIVersion"):
            c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=cid)
        else:
            c = mqtt.Client(client_id=cid)
        c.on_connect = self._al_conectar
        c.on_disconnect = self._al_desconectar
        c.on_message = self._al_mensaje
        c.reconnect_delay_set(min_delay=1, max_delay=5)
        # Igual que el firmware de la esclava (PubSubClient con keepalive corto): connect_async y el
        # hilo de paho hacen las reconexiones solas si el broker todavía no arrancó.
        c.connect_async(self.broker[0], self.broker[1], keepalive=5)
        c.loop_start()
        self.cliente = c

    def _al_conectar(self, cliente, userdata, flags, rc, *props):
        ok = (rc == 0) if isinstance(rc, int) else not getattr(rc, "is_failure", False)
        self.mqtt_ok = ok
        if ok:
            # Suscribirse en on_connect (y no una sola vez al arrancar): tras una reconexión el
            # broker no recuerda la suscripción de una sesión limpia. Los "retenidos" llegan enseguida.
            cliente.subscribe("lab/estado/+", qos=1)
            serie(self.rol, f"# MQTT conectado a {self.broker[0]}:{self.broker[1]}")

    def _al_desconectar(self, *a):
        if self.mqtt_ok:
            self.t_sin_broker = time.monotonic()
        self.mqtt_ok = False

    def _al_mensaje(self, cliente, userdata, msg):
        servicio = msg.topic.rsplit("/", 1)[-1]
        if servicio not in self.leds:
            return
        valor = msg.payload.decode("ascii", errors="replace").strip().upper()
        valor = valor if valor in ("OK", "LENTO", "CAIDO") else "?"
        with self.candado:
            if self.leds[servicio] != valor:
                self.leds[servicio] = valor
                self.cambio = True

    def linea_estado(self):
        return f"ESTADO,esclava,{self.ip},{RSSI_FIJO},{1 if self.mqtt_ok else 0}"

    def paso(self, ahora):
        with self.candado:
            if self.mqtt_ok:
                self.sin_broker = False
            elif not self.sin_broker and ahora - self.t_sin_broker >= 5.0:
                self.sin_broker = True
                serie(self.rol, "# sin broker hace más de 5 s: los seis LEDs pasan a '?'")
                for s in LEDS:
                    if self.leds[s] != "?":
                        self.leds[s] = "?"
                        self.cambio = True
            toca = self.cambio or ahora >= self.prox_leds
            self.cambio = False
            valores = [self.leds[s] for s in LEDS]
        if toca:
            serie(self.rol, "LEDS," + ",".join(valores))
            if self.mqtt_ok:
                self.cliente.publish("lab/metricas/esclava", json.dumps({
                    "origen": "ESP32 emulada", "leds": dict(zip(LEDS, valores)),
                    "mqtt_ok": True, "t_ms": self.t_ms()}))
            # El dibujo NO es una línea del contrato (por eso empieza con '#'): es lo que se vería en
            # la protoboard. ● encendido = OK, ◐ parpadeando = LENTO, ○ apagado = CAIDO, · sin dato.
            dibujo = "  ".join(f"{SIMBOLO_LED[v]} {s}" for s, v in zip(LEDS, valores))
            placa = "●" if self.mqtt_ok else "○"
            serie(self.rol, f"# [{placa} placa/MQTT]  {dibujo}")
            self.prox_leds = ahora + 2.0
        return ahora + 0.05

    def resumen(self):
        r = super().resumen()
        r["leds"] = dict(self.leds)
        r["mqtt_ok"] = self.mqtt_ok
        return r


# ------------------------------------------------------------------------------------------------
def leer_destino(texto, rol):
    """'ip:puerto' -> (ip, puerto); 'ip' -> (ip, puerto del PC para ese rol, del contrato)."""
    ip, _, puerto = texto.partition(":")
    return ip, int(puerto) if puerto else PUERTO_PC[rol]


def main(argv=None):
    global _con_prefijo
    ap = argparse.ArgumentParser(description="Emulador de las ESP32 del tema 11 (maestras y esclava).")
    ap.add_argument("--http", type=int, default=0, help="API de control emulado; 0 deshabilitada")
    ap.add_argument("--rol", nargs="+", required=True,
                    help=f"una o varias placas: {', '.join(ROLES)} (también separadas por coma)")
    ap.add_argument("--destino", nargs="+", default=["127.0.0.1"],
                    help="ip:puerto a donde van CTRL/JOINTS. Uno para todas las placas, o uno por "
                         "placa en el mismo orden. Solo 'ip' = el puerto publicado en el PC de cada "
                         "rol (5001-5003 / 5101-5103). Por defecto 127.0.0.1")
    ap.add_argument("--admin", default=None,
                    help="IP del admin: HB y PING a su UDP 5300 (y broker MQTT de la esclava)")
    ap.add_argument("--puerto-admin", type=int, default=5300)
    ap.add_argument("--mqtt", default=None, help="broker de la esclava ip[:puerto] (por defecto --admin:1883)")
    ap.add_argument("--hz", type=float, default=20.0, help="frecuencia de CTRL/JOINTS (20 como el firmware)")
    ap.add_argument("--duracion", type=float, default=0.0, help="segundos; 0 = hasta Ctrl+C")
    ap.add_argument("--perdida", type=float, default=0.0,
                    help="porcentaje 0-100 de CTRL/JOINTS que se descartan a propósito")
    ap.add_argument("--semilla", type=int, default=1, help="semilla de la pérdida y del ruido")
    ap.add_argument("--piloto", action="store_true",
                    help="ctrl-N: maneja con pure pursuit mirando la pista (si no hay, patrón seno)")
    ap.add_argument("--pista", default="ws://192.168.10.10:8765", help="WebSocket del servidor de pista")
    ap.add_argument("--adelanto", type=float, default=0.0,
                    help="piloto: distancia de anticipación Ld en m (0 = automática, perímetro/25)")
    ap.add_argument("--batalla", type=float, default=0.5, help="piloto: distancia entre ejes B en m")
    ap.add_argument("--vel-piloto", type=float, default=70.0, help="piloto: acelerador en recta (0-100)")
    ap.add_argument("--signo-dir", type=int, default=1, choices=(1, -1),
                    help="piloto: -1 si el servidor usa dir > 0 = izquierda")
    ap.add_argument("--resumen", default=None, help="archivo JSON donde dejar el resumen al terminar")
    args = ap.parse_args(argv)

    roles = [r.strip() for grupo in args.rol for r in grupo.split(",") if r.strip()]
    malos = [r for r in roles if r not in ROLES]
    if malos or not roles:
        ap.error(f"rol desconocido: {', '.join(malos)} (válidos: {', '.join(ROLES)})")
    if not (0.0 <= args.perdida <= 100.0) or args.hz <= 0:
        ap.error("--perdida va de 0 a 100 y --hz debe ser > 0")
    _con_prefijo = len(roles) > 1

    # Dentro del compose el emulador vive en una VLAN y llega al admin por el router: misma ruta
    # que agregan los contenedores (si no hay ROUTER_IP, p. ej. en Windows, no hace nada).
    if os.environ.get("ROUTER_IP"):
        from comun import lab
        lab.configurar_rutas()

    maestras = [r for r in roles if r != "esclava"]
    if len(args.destino) not in (1, len(maestras)) and maestras:
        ap.error("--destino: uno para todas o uno por cada placa maestra, en el mismo orden")
    observador = None
    if args.piloto and any(r in ROLES_GAMER for r in roles):
        observador = Observador(args.pista)
        observador.start()

    placas = []
    for i, rol in enumerate(roles):
        if rol == "esclava":
            placas.append(Esclava(rol, args))
            continue
        texto = args.destino[0] if len(args.destino) == 1 else args.destino[maestras.index(rol)]
        destino = leer_destino(texto, rol)
        placas.append(Maestra(rol, args, destino, observador if rol in ROLES_GAMER else None))
        serie(rol, f"# {rol} -> {destino[0]}:{destino[1]} a {args.hz:g} Hz"
                   + (f", admin {args.admin}:{args.puerto_admin}" if args.admin else ", sin admin")
                   + (f", pérdida simulada {args.perdida:g} %" if args.perdida else ""))
    for p in placas:
        p.start()
    if args.http:
        from control_http import start
        start(placas, args.http)

    # En el contenedor el emulador es el proceso 1, y Linux NO le aplica la acción por defecto de
    # SIGTERM: sin esto "docker stop" esperaría 10 s y lo mataría con SIGKILL. Se convierte en un
    # Ctrl+C para salir por el mismo camino (resumen, desconexión MQTT limpia).
    def _sigterm(*_):
        raise KeyboardInterrupt
    try:
        signal.signal(signal.SIGTERM, _sigterm)
    except (ValueError, OSError):
        pass

    t_fin = time.monotonic() + args.duracion if args.duracion > 0 else None
    try:
        while t_fin is None or time.monotonic() < t_fin:
            time.sleep(0.2)
    except KeyboardInterrupt:
        pass
    for p in placas:
        p.detener.set()
    if observador:
        observador.detener.set()
    for p in placas:
        p.join(timeout=1.0)
    resumen = [p.resumen() for p in placas]
    for r in resumen:
        serie(r["rol"], "# resumen " + json.dumps(r, ensure_ascii=False))
    if args.resumen:
        with open(args.resumen, "w", encoding="utf-8") as f:
            json.dump(resumen, f, indent=2, ensure_ascii=False)
    for p in placas:
        if isinstance(p, Esclava) and p.cliente is not None:
            p.cliente.loop_stop()
            p.cliente.disconnect()


if __name__ == "__main__":
    main()
