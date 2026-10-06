"""Monitor del plano de administración (VLAN 3): latidos, sondeo ICMP, estados por MQTT, CSV y dashboard.

Qué hace, en cinco hilos que comparten una tabla protegida por un candado:

1. UDP 5300 (hilo `udp`): recibe `HB,<origen>,<seq>,<t_ms>` de todos (contenedores, router, ESP32
   reales y emulados). Por origen guarda la IP, cuándo se lo vio por última vez, las pérdidas por número
   de secuencia (`protocolo.ContadorSecuencia`) y el jitter entre llegadas del RFC 3550
   (`protocolo.Jitter`). Si llega `PING,<origen>,<seq>,<t_ms>` contesta `PONG` con los mismos campos al
   remitente: así un ESP32 mide su propio RTT hasta el admin.
2. Sondeo activo (un hilo por destino): `ping` ICMP cada 1 s a cada contenedor de las zonas y al router.
   Este es el dato que demuestra que el admin "observa ambas zonas": el ICMP sale de la VLAN 3, cruza
   el router y vuelve; si el router o la ruta fallan, el ping falla aunque el contenedor esté sano.
   Por destino: RTT último/promedio/p95/máximo, jitter (promedio de |RTT_i - RTT_i-1|) y
   disponibilidad (respuestas / intentos) en la ventana de 60 s y desde el arranque.
3. MQTT (hilo de paho): suscrito a `lab/vivo/+` y `lab/metricas/+`. Publica `lab/estado/<servicio>`
   (OK | LENTO | CAIDO, retenido) cuando cambia y cada 10 s, y `lab/admin/resumen` cada 2 s.
4. Evaluación (hilo principal): cada 0,5 s decide el estado de cada servicio; cada 2 s escribe el CSV
   `/app/resultados/admin_metricas.csv` (una fila por servicio) para la validación experimental.
5. HTTP 8080 (hilo `http`): panel NexoLab y `/api/resumen.json`.

Todo con la biblioteca estándar salvo paho-mqtt (que viene de apk en la imagen).
"""

import csv
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# comun/ está copiado junto a este archivo dentro de la imagen (/app/comun). Si se corre desde el repo
# (plano-admin/admin/monitor.py), comun/ está dos carpetas más arriba.
AQUI = os.path.dirname(os.path.abspath(__file__))
for _base in (AQUI, os.path.abspath(os.path.join(AQUI, "..", ".."))):
    if os.path.isdir(os.path.join(_base, "comun")):
        sys.path.insert(0, _base)
        break
from comun import lab, protocolo  # noqa: E402
from panel import gateway
EVENTOS = deque(maxlen=500)


# ---------------------------------------------------------------------------------------------
# Configuración (todo por variables de entorno, con los valores del contrato por defecto)
# ---------------------------------------------------------------------------------------------

def _env_float(nombre, defecto):
    try:
        return float(os.environ.get(nombre, defecto))
    except ValueError:
        return float(defecto)


HB_PUERTO = int(os.environ.get("HB_PUERTO", "5300"))
HTTP_PUERTO = int(os.environ.get("HTTP_PUERTO", "8080"))
# El monitor vive en el mismo contenedor que el broker: se conecta por localhost.
MQTT_HOST = os.environ.get("MQTT_HOST", "127.0.0.1")
MQTT_PUERTO = int(os.environ.get("MQTT_PUERTO", "1883"))
RESULTADOS = os.environ.get("RESULTADOS_DIR", os.path.join(AQUI, "resultados"))

# Umbrales de la decisión OK / LENTO / CAIDO. Por qué estos valores por defecto:
# - HB_TIMEOUT_S = 3: los latidos salen cada 1 s; perder 3 seguidos por casualidad en una LAN es
#   casi imposible (con 1 % de pérdida, 1 en un millón), así que 3 s sin latido es una caída real y no
#   ruido, y todavía es lo bastante rápido para verlo en el LED "en vivo".
# - UMBRAL_RTT_P95_MS = 20: el control de los ESP32 llega a 20 Hz (un paquete cada 50 ms). Entre
#   contenedores del mismo PC, aun cruzando el router, el RTT es de décimas de ms; si el p95 pasa de
#   20 ms (≈ 10 ms de ida, un 20 % del período de control) algo está saturado (CPU al 100 % por la
#   física, cola en el router) y el control ya se nota "tarde". Se usa el p95 y no el promedio para
#   que unos pocos picos no lo disparen, pero un 5 % de paquetes lentos sí.
# - UMBRAL_JITTER_MS = 10: la misma cuenta; si la variación del retardo supera ~20 % del período de
#   50 ms, dos órdenes consecutivas pueden llegar casi juntas o con un hueco de un ciclo entero y el
#   carro/robot se mueve a tirones aunque el promedio sea bueno.
# - UMBRAL_PERDIDA_PCT = 5: perder más de 1 de cada 20 latidos (o pings) en el último minuto ya no es
#   un descarte aislado de UDP sino un enlace degradado (en una LAN sana la pérdida es 0 %).
HB_TIMEOUT_S = _env_float("HB_TIMEOUT_S", 3.0)
UMBRAL_RTT_P95_MS = _env_float("UMBRAL_RTT_P95_MS", 20.0)
UMBRAL_JITTER_MS = _env_float("UMBRAL_JITTER_MS", 10.0)
UMBRAL_PERDIDA_PCT = _env_float("UMBRAL_PERDIDA_PCT", 5.0)
VENTANA_S = _env_float("VENTANA_S", 60.0)          # ventana de las métricas "recientes"
PERIODO_PING_S = _env_float("PERIODO_PING_S", 1.0)
FALLOS_PING_CAIDO = int(os.environ.get("FALLOS_PING_CAIDO", "2"))  # pings seguidos sin respuesta
# Muestras mínimas en la ventana para juzgar la PÉRDIDA (latidos o pings). Con pocas muestras un solo
# paquete perdido ya pasa del 5 % (1 de 10 = 10 %): pasaba justo al arrancar o al volver de una caída,
# y el servicio quedaba unos 20 s en LENTO por un único ping. Con 20 muestras, 1 perdido = 5 % (no
# pasa) y 2 = 10 % (sí): la regla del 5 % recién significa algo con al menos 20 intentos.
MIN_MUESTRAS_PERDIDA = int(os.environ.get("MIN_MUESTRAS_PERDIDA", "20"))
# Tope de orígenes de latido distintos que se recuerdan. En el laboratorio son 16 (8 servicios + 7 ESP32
# + la esclava); el puerto UDP 5300 está publicado en el PC y cualquiera de la WiFi podría mandar HB con
# un origen inventado distinto en cada datagrama: sin tope, la tabla (y el JSON del dashboard) crecería
# sin límite. Los 8 servicios vigilados se aceptan siempre, aunque la tabla esté llena.
MAX_ORIGENES = int(os.environ.get("MAX_ORIGENES", "64"))

# Los servicios que el admin vigila: nombre, VLAN, IP por defecto (la del contrato) y si tienen LED
# en la ESP32 esclava. track-server y router no tienen LED pero sí aparecen en el dashboard.
SERVICIOS = [
    ("track-server", 1, "192.168.10.10", False),
    ("player-1", 1, "192.168.10.21", True),
    ("player-2", 1, "192.168.10.22", True),
    ("player-3", 1, "192.168.10.23", True),
    ("sim-spot", 2, "192.168.20.21", True),
    ("sim-pepper", 2, "192.168.20.22", True),
    ("sim-nao", 2, "192.168.20.23", True),
    ("router", 3, "192.168.30.254", False),
]
NOMBRES = [s[0] for s in SERVICIOS]


def _leer_objetivos():
    """OBJETIVOS="player-1=192.168.10.21,router=192.168.30.254,..." cambia a quién se le hace ping.

    Si no está, se usan las IPs del contrato. Un servicio que no aparece en OBJETIVOS (cuando se pasa)
    no se sondea por ICMP y se decide solo por latidos y MQTT. Se pueden agregar destinos que no son
    servicios (aparecen en el dashboard como sondeos sueltos)."""
    texto = os.environ.get("OBJETIVOS", "").strip()
    if not texto:
        return {nombre: ip for nombre, _, ip, _ in SERVICIOS}
    objetivos = {}
    for par in texto.split(","):
        if "=" in par:
            nombre, ip = par.split("=", 1)
            if nombre.strip() and ip.strip():
                objetivos[nombre.strip()] = ip.strip()
    return objetivos


OBJETIVOS = _leer_objetivos()

candado = threading.Lock()
INICIO = time.time()


def ahora():
    """Reloj monótono en segundos: no salta si alguien cambia la hora del sistema."""
    return time.monotonic()


# ---------------------------------------------------------------------------------------------
# 1. Latidos UDP (HB) y eco PING/PONG
# ---------------------------------------------------------------------------------------------

class Origen:
    """Lo que se sabe de un emisor de latidos (un contenedor, el router o un ESP32)."""

    def __init__(self, nombre, ip):
        self.nombre = nombre
        self.ip = ip
        self.primero = ahora()
        self.ultimo = None          # último HB (monótono)
        self.seq = None
        self.contador = protocolo.ContadorSecuencia()
        self.jitter = protocolo.Jitter()
        # Instantáneas (t, recibidos, perdidos) para calcular la pérdida de los últimos VENTANA_S
        # segundos: el ContadorSecuencia acumula desde el arranque y, sin ventana, un corte de red de
        # hace una hora dejaría el servicio en LENTO para siempre.
        self.hist = deque()
        self.pings = 0              # PING recibidos (un ESP32 midiendo su RTT)
        self.ultimo_ping = None

    def registrar_hb(self, seq, t_ms, ip):
        self.ip = ip
        reinicios_antes = self.contador.reinicios
        self.contador.registrar(seq)
        if self.contador.reinicios != reinicios_antes:
            # El emisor se reinició: su reloj t_ms volvió a 0. Si se siguiera con el mismo Jitter,
            # el primer par daría una D enorme (horas) que no es retardo de red.
            self.jitter = protocolo.Jitter()
        # Llegada medida con el reloj monótono local en ms (el mismo tipo de reloj que millis()).
        self.jitter.registrar(t_ms, lab.ms())
        self.seq = seq
        t = ahora()
        self.ultimo = t
        if getattr(self, "_rebase", False):
            self.hist.clear()          # este latido (con el salto ya contado) es la nueva base
            self._rebase = False
        self.hist.append((t, self.contador.recibidos, self.contador.perdidos))
        # Se conserva UNA instantánea más vieja que la ventana como línea de base.
        while len(self.hist) > 2 and self.hist[1][0] < t - VENTANA_S:
            self.hist.popleft()

    def olvidar_ventana(self):
        """Empieza la ventana de pérdida en el PRÓXIMO latido (ver Sondeo.olvidar_fallos).

        No se toma como base el último latido de antes de la caída: si lo que cayó fue la red (el
        router) y no el contenedor, su seq siguió subiendo, y el salto entre ese latido viejo y el
        primero nuevo se contaría como pérdida justo al volver. Con la base en el primer latido
        nuevo, ese salto (que es la propia caída, ya informada como CAIDO) queda fuera."""
        self.hist.clear()
        self._rebase = True

    def muestras_ventana(self):
        if len(self.hist) < 2:
            return 0
        _, r0, p0 = self.hist[0]
        _, r1, p1 = self.hist[-1]
        return (r1 - r0) + (p1 - p0)

    def perdida_ventana_pct(self):
        if len(self.hist) < 2:
            return 0.0
        _, r0, p0 = self.hist[0]
        _, r1, p1 = self.hist[-1]
        dr, dp = r1 - r0, p1 - p0
        return 100.0 * dp / (dr + dp) if (dr + dp) > 0 else 0.0

    def edad(self):
        return None if self.ultimo is None else ahora() - self.ultimo

    def resumen(self):
        e = self.edad()
        return {
            "origen": self.nombre,
            "ip": self.ip,
            "edad_s": None if e is None else round(e, 2),
            "seq": self.seq,
            "recibidos": self.contador.recibidos,
            "perdidos": self.contador.perdidos,
            "desordenados": self.contador.desordenados,
            "reinicios": self.contador.reinicios,
            "perdida_total_pct": round(self.contador.perdida_pct(), 2),
            "perdida_60s_pct": round(self.perdida_ventana_pct(), 2),
            "jitter_ms": round(self.jitter.j, 3),
            "pings": self.pings,
        }


origenes = {}      # nombre -> Origen


def hilo_udp():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("0.0.0.0", HB_PUERTO))
    print(f"[monitor] escuchando latidos y PING en UDP {HB_PUERTO}", flush=True)
    while True:
        try:
            datos, remitente = s.recvfrom(512)
        except OSError:
            continue
        try:
            atender_datagrama(s, datos, remitente)
        except Exception as e:
            # Este hilo es el único que escucha los latidos: si un datagrama raro lo matara, todos los
            # servicios pasarían a CAIDO "por falta de latido" sin que la red tenga nada. Se registra
            # y se sigue con el siguiente.
            print(f"[monitor] error con un datagrama de {remitente[0]}: {type(e).__name__}: {e}", flush=True)


def atender_datagrama(s, datos, remitente):
    """Un datagrama de UDP 5300: HB se registra; PING se contesta con PONG y se cuenta."""
    m = protocolo.leer(datos)            # nunca lanza: basura -> None
    if not isinstance(m, protocolo.Latido):
        return
    if m.tipo == "PING":
        # Eco inmediato, ANTES de tomar el candado: cualquier espera aquí se sumaría al RTT que
        # mide el ESP32 y ya no sería solo la red.
        try:
            s.sendto(protocolo.armar_latido("PONG", m.origen, m.seq, m.t_ms).encode(), remitente)
        except OSError:
            pass
    with candado:
        o = origenes.get(m.origen)
        if o is None:
            if len(origenes) >= MAX_ORIGENES and m.origen not in NOMBRES:
                return
            o = origenes[m.origen] = Origen(m.origen, remitente[0])
            print(f"[monitor] nuevo origen de latidos: {m.origen} desde {remitente[0]}", flush=True)
        if m.tipo == "HB":
            o.registrar_hb(m.seq, m.t_ms, remitente[0])
        elif m.tipo == "PING":
            o.pings += 1
            o.ultimo_ping = ahora()
            o.ip = remitente[0]


# ---------------------------------------------------------------------------------------------
# 2. Sondeo activo por ICMP (ping) a cada contenedor de las zonas y al router
# ---------------------------------------------------------------------------------------------

_RE_TIEMPO = re.compile(r"time[=<]([\d.]+)\s*ms")


def ping_una_vez(ip, espera_s=1):
    """Un ping ICMP con el `ping` del sistema. Devuelve el RTT en ms o None si no hubo respuesta.

    Por qué el ping del sistema y no un socket ICMP propio: abrir un socket crudo en Python exige armar
    y verificar a mano la cabecera ICMP (suma de verificación, identificador); el ping de busybox ya lo
    hace bien y su salida ('time=0.123 ms') es fácil de leer. Cuesta un proceso por segundo por destino
    (9 procesos/s), nada para el contenedor. -W 1: esperar como máximo 1 s la respuesta, así un
    destino caído no frena el siguiente intento."""
    if os.environ.get("NEXO_SONDEO", "icmp") == "udp":
        # Alternativa explícita para ensayos de software sin permisos ICMP. Mide un eco UDP real.
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(espera_s)
            seq = time.monotonic_ns() % 1000000000
            stamp = int(time.monotonic() * 1000)
            text = protocolo.armar_latido("PING", "sondeo-admin", seq, stamp)
            try:
                t0 = time.perf_counter()
                destino = (ip, int(os.environ.get("NEXO_ECO_PUERTO", str(HB_PUERTO))))
                sock.sendto(text.encode(), destino)
                data, addr = sock.recvfrom(1024)
                rtt = (time.perf_counter() - t0) * 1000
                m = protocolo.leer(data)
                if (addr != destino or not isinstance(m, protocolo.Latido) or m.tipo != "PONG"
                        or m.origen != "sondeo-admin" or m.seq != seq or m.t_ms != stamp):
                    return None
                return rtt
            except OSError:
                return None
    try:
        r = subprocess.run(["ping", "-c", "1", "-W", str(int(max(1, espera_s))), ip],
                           capture_output=True, text=True, timeout=espera_s + 2)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0:
        return None
    m = _RE_TIEMPO.search(r.stdout)
    return float(m.group(1)) if m else None


class Sondeo:
    """Historial de pings a un destino: ventana de VENTANA_S segundos más totales desde el arranque."""

    def __init__(self, nombre, ip):
        self.nombre = nombre
        self.ip = ip
        self.intentos = deque()     # (t, rtt_ms o None)
        self.total = 0
        self.total_ok = 0
        self.fallos_seguidos = 0
        self.alguna_vez_ok = False
        self.ultimo_rtt = None

    def registrar(self, rtt):
        t = ahora()
        self.intentos.append((t, rtt))
        while self.intentos and self.intentos[0][0] < t - VENTANA_S:
            self.intentos.popleft()
        self.total += 1
        if rtt is None:
            self.fallos_seguidos += 1
        else:
            self.total_ok += 1
            self.fallos_seguidos = 0
            self.alguna_vez_ok = True
            self.ultimo_rtt = rtt

    def olvidar_fallos(self):
        """Saca de la ventana los pings sin respuesta. Se usa cuando el servicio vuelve de CAIDO: esos
        fallos son la propia caída, que ya se informó como CAIDO. Si se quedaran, el servicio pasaría
        casi un minuto en LENTO por "pérdida" aunque ya responde perfecto (se midió: 57 s en la
        prueba de disponibilidad). La disponibilidad total desde el arranque sí los conserva."""
        self.intentos = deque((t, r) for t, r in self.intentos if r is not None)

    def resumen(self, con_historial=False):
        rtts = [r for _, r in self.intentos if r is not None]
        n = len(self.intentos)
        res = {
            "ip": self.ip,
            "intentos_60s": n,
            "disp_60s_pct": round(100.0 * len(rtts) / n, 2) if n else None,
            "disp_total_pct": round(100.0 * self.total_ok / self.total, 2) if self.total else None,
            "total": self.total,
            "total_ok": self.total_ok,
            "fallos_seguidos": self.fallos_seguidos,
            "alguna_vez_ok": self.alguna_vez_ok,
            "rtt_ultimo_ms": None,
            "rtt_prom_ms": None,
            "rtt_p95_ms": None,
            "rtt_max_ms": None,
            "jitter_ms": None,
        }
        if self.intentos and self.intentos[-1][1] is not None:
            res["rtt_ultimo_ms"] = round(self.intentos[-1][1], 3)
        if rtts:
            orden = sorted(rtts)
            # p95 por "rango más cercano": el valor que deja por debajo al 95 % de las muestras.
            p95 = orden[max(0, -(-95 * len(orden) // 100) - 1)]
            res["rtt_prom_ms"] = round(sum(rtts) / len(rtts), 3)
            res["rtt_p95_ms"] = round(p95, 3)
            res["rtt_max_ms"] = round(orden[-1], 3)
            if len(rtts) >= 2:
                # Jitter del sondeo: promedio de |RTT_i - RTT_(i-1)| entre respuestas consecutivas.
                # Es la misma idea que el RFC 3550 (cuánto varía el retardo de un paquete al siguiente)
                # pero sin el filtro de 1/16: con 60 muestras por minuto el promedio simple ya es estable.
                difs = [abs(b - a) for a, b in zip(rtts, rtts[1:])]
                res["jitter_ms"] = round(sum(difs) / len(difs), 3)
        if con_historial:
            t = ahora()
            # Para la mini gráfica del dashboard: [segundos hacia atrás, rtt o null].
            res["historial"] = [[round(t - ti, 1), None if r is None else round(r, 3)] for ti, r in self.intentos]
        return res


sondeos = {nombre: Sondeo(nombre, ip) for nombre, ip in OBJETIVOS.items()}


def hilo_ping(sondeo):
    # Igual que el Latido de comun/lab.py: se programa contra un reloj fijo (siguiente += período)
    # para que el tiempo que tarda cada ping no se acumule y el período se mantenga en 1 s.
    siguiente = ahora()
    while True:
        rtt = ping_una_vez(sondeo.ip)
        with candado:
            sondeo.registrar(rtt)
        siguiente += PERIODO_PING_S
        espera = siguiente - ahora()
        if espera > 0:
            time.sleep(espera)
        else:
            siguiente = ahora()      # se atrasó (timeout de 1 s): no intentar "recuperar" de golpe


# ---------------------------------------------------------------------------------------------
# 3. MQTT: lo que publican los contenedores y lo que publica el admin
# ---------------------------------------------------------------------------------------------

vivos = {}         # servicio -> ("1"|"0", t)
metricas = {}      # servicio -> (dict, t)
mqtt_estado = {"conectado": False, "cliente": None}


def iniciar_mqtt():
    try:
        import paho.mqtt.client as mqtt
    except ImportError:
        print("[monitor] paho-mqtt no está instalado: sin MQTT (el dashboard sigue)", flush=True)
        return None
    # paho 2.x (Windows, pip) pide elegir la API de callbacks; la 1.6.1 de Alpine no tiene ese
    # parámetro. Los callbacks de abajo aceptan *args para servir en las dos.
    if hasattr(mqtt, "CallbackAPIVersion"):
        c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="admin-monitor")
    else:
        c = mqtt.Client(client_id="admin-monitor")
    # Testamento del propio admin: si el monitor muere, el broker (si sigue vivo) avisa con "0".
    c.will_set("lab/vivo/admin", "0", qos=1, retain=True)

    def al_conectar(cliente, userdata, flags, rc, *props):
        ok = (rc == 0) if isinstance(rc, int) else not getattr(rc, "is_failure", False)
        mqtt_estado["conectado"] = ok
        if ok:
            # '+' es el comodín de UN nivel: lab/vivo/+ recibe lab/vivo/player-1, lab/vivo/sim-nao...
            cliente.subscribe([("lab/vivo/+", 1), ("lab/metricas/+", 0)])
            cliente.publish("lab/vivo/admin", "1", qos=1, retain=True)
            print(f"[monitor] MQTT conectado a {MQTT_HOST}:{MQTT_PUERTO}", flush=True)
            # Al (re)conectar se vuelven a publicar todos los estados: si el broker se reinició
            # perdió los retenidos y la esclava se quedaría sin saber nada hasta el próximo cambio.
            with candado:
                for nombre in estados:
                    estados[nombre]["publicado_t"] = 0.0

    def al_desconectar(*args):
        mqtt_estado["conectado"] = False

    def al_mensaje(cliente, userdata, msg):
        partes = msg.topic.split("/")
        if len(partes) != 3:
            return
        servicio = partes[2]
        texto = msg.payload.decode("utf-8", errors="replace").strip()
        with candado:
            if partes[1] == "vivo":
                vivos[servicio] = (texto, time.time())
            elif partes[1] == "metricas":
                try:
                    datos = json.loads(texto)
                    if not isinstance(datos, dict):
                        datos = {"valor": datos}
                except ValueError:
                    datos = {"texto": texto[:200]}
                metricas[servicio] = (datos, time.time())

    c.on_connect = al_conectar
    c.on_disconnect = al_desconectar
    c.on_message = al_mensaje
    c.reconnect_delay_set(min_delay=1, max_delay=5)
    # connect_async + loop_start: si el broker todavía no arrancó, paho reintenta solo en su hilo.
    c.connect_async(MQTT_HOST, MQTT_PUERTO, keepalive=10)
    c.loop_start()
    mqtt_estado["cliente"] = c
    return c


def publicar(topico, carga, retener=False, qos=0):
    c = mqtt_estado["cliente"]
    if c is None or not mqtt_estado["conectado"]:
        return False
    c.publish(topico, carga, qos=qos, retain=retener)
    return True


# ---------------------------------------------------------------------------------------------
# 4. Decisión OK / LENTO / CAIDO
# ---------------------------------------------------------------------------------------------

estados = {nombre: {"estado": "?", "motivo": "arrancando", "desde": time.time(), "publicado_t": 0.0}
           for nombre in NOMBRES}


def decidir(nombre):
    """Devuelve (estado, motivo) de un servicio. Se llama con el candado tomado.

    CAIDO  si el servicio dijo vivo=0 por MQTT (se despidió o saltó su testamento LWT), o si no hay
           latido fresco (más de HB_TIMEOUT_S sin HB, o nunca llegó uno) Y además el ping falla.
           Se piden las dos cosas a la vez porque cada una sola puede fallar sin que el servicio esté
           caído: el latido viaja por UDP y su hilo podría trabarse un momento; el ping depende de
           que exista la ruta por el router (fuera del stack, por ejemplo, nunca responde).
    LENTO  si está vivo pero: el p95 del RTT o el jitter (del ping o de los latidos) pasan su umbral,
           la pérdida del último minuto (latidos o pings) pasa del 5 %, o el contenedor responde al
           ping pero su latido se cortó (la red está bien pero el programa no late: algo lo frena).
    OK     en otro caso.
    Un sondeo que NUNCA respondió (sin ruta ICMP, como en la prueba aislada) no se usa para LENTO:
    sería castigar al servicio por un problema del método de medida; el dashboard lo muestra aparte.
    """
    o = origenes.get(nombre)
    s = sondeos.get(nombre)
    vivo = vivos.get(nombre, (None, 0))[0]
    edad = o.edad() if o else None
    hb_fresco = edad is not None and edad <= HB_TIMEOUT_S
    # "El ping falla" = no hay sondeo, todavía no respondió nunca, o fallaron los últimos
    # FALLOS_PING_CAIDO seguidos (uno solo perdido no basta: UDP/ICMP puede descartar uno suelto).
    ping_falla = (s is None or not s.alguna_vez_ok or s.fallos_seguidos >= FALLOS_PING_CAIDO)
    if vivo == "0":
        return "CAIDO", "vivo=0 por MQTT (se despidió o saltó su LWT)"
    if not hb_fresco and ping_falla:
        if edad is None:
            return "CAIDO", "nunca mandó latido y no responde al ping"
        return "CAIDO", f"sin latido hace {edad:.1f} s y no responde al ping"

    motivos = []
    if not hb_fresco and edad is not None:
        motivos.append(f"responde al ping pero sin latido hace {edad:.1f} s")
    if o is not None and o.contador.recibidos >= 2:
        if o.jitter.j > UMBRAL_JITTER_MS:
            motivos.append(f"jitter HB {o.jitter.j:.1f} ms > {UMBRAL_JITTER_MS:g}")
        p = o.perdida_ventana_pct()
        if p > UMBRAL_PERDIDA_PCT and o.muestras_ventana() >= MIN_MUESTRAS_PERDIDA:
            motivos.append(f"pérdida HB {p:.1f} % > {UMBRAL_PERDIDA_PCT:g}")
    if s is not None and s.alguna_vez_ok:
        r = s.resumen()
        if r["rtt_p95_ms"] is not None and r["rtt_p95_ms"] > UMBRAL_RTT_P95_MS:
            motivos.append(f"RTT p95 {r['rtt_p95_ms']:.1f} ms > {UMBRAL_RTT_P95_MS:g}")
        if r["jitter_ms"] is not None and r["jitter_ms"] > UMBRAL_JITTER_MS:
            motivos.append(f"jitter ping {r['jitter_ms']:.1f} ms > {UMBRAL_JITTER_MS:g}")
        if (r["disp_60s_pct"] is not None and 100.0 - r["disp_60s_pct"] > UMBRAL_PERDIDA_PCT
                and r["intentos_60s"] >= MIN_MUESTRAS_PERDIDA):
            motivos.append(f"pérdida ping {100.0 - r['disp_60s_pct']:.1f} % > {UMBRAL_PERDIDA_PCT:g}")
    if motivos:
        return "LENTO", "; ".join(motivos)
    if hb_fresco:
        if s is not None and s.alguna_vez_ok and s.fallos_seguidos == 0:
            return "OK", "latido al día y ping responde"
        if s is not None and not s.alguna_vez_ok and s.total > 0:
            return "OK", "latido al día (el ping nunca respondió: sin ruta ICMP)"
        return "OK", "latido al día"
    return "OK", "responde al ping"


def armar_resumen(con_historial=False):
    """La tabla completa (lo que va a lab/admin/resumen, al CSV y al dashboard). Con el candado tomado."""
    servicios = {}
    for nombre, vlan, ip, led in SERVICIOS:
        o = origenes.get(nombre)
        s = sondeos.get(nombre)
        vivo = vivos.get(nombre)
        met = metricas.get(nombre)
        e = estados[nombre]
        servicios[nombre] = {
            "vlan": vlan,
            "ip": (s.ip if s else None) or ip,
            "led": led,
            "estado": e["estado"],
            "motivo": e["motivo"],
            "desde": round(e["desde"], 1),
            "vivo": vivo[0] if vivo else None,
            "hb": o.resumen() if o else None,
            "ping": s.resumen(con_historial) if s else None,
            "metricas": met[0] if met else None,
            "metricas_edad_s": round(time.time() - met[1], 1) if met else None,
        }
    otros = [o.resumen() for n, o in sorted(origenes.items()) if n not in NOMBRES]
    extra = {n: s.resumen(con_historial) for n, s in sondeos.items() if n not in NOMBRES}
    otras_metricas = {n: {"datos": d, "edad_s": round(time.time() - t, 1)}
                      for n, (d, t) in metricas.items() if n not in NOMBRES}
    return {
        "nexo": gateway.metadata(),
        "sondeo_transporte": os.environ.get("NEXO_SONDEO", "icmp"),
        "eventos": list(EVENTOS),
        "t": round(time.time(), 1),
        "en_marcha_s": round(time.time() - INICIO, 1),
        "mqtt_conectado": mqtt_estado["conectado"],
        "umbrales": {
            "hb_timeout_s": HB_TIMEOUT_S, "rtt_p95_ms": UMBRAL_RTT_P95_MS,
            "jitter_ms": UMBRAL_JITTER_MS, "perdida_pct": UMBRAL_PERDIDA_PCT, "ventana_s": VENTANA_S,
        },
        "leds": [n for n, _, _, led in SERVICIOS if led],
        "servicios": servicios,
        "otros_origenes": otros,
        "sondeos_extra": extra,
        "otras_metricas": otras_metricas,
        "vivos": {n: v for n, (v, _) in vivos.items()},
    }


# ---------------------------------------------------------------------------------------------
# CSV para la validación experimental
# ---------------------------------------------------------------------------------------------

COLUMNAS_CSV = [
    "fecha", "t_s", "servicio", "vlan", "estado", "motivo", "vivo",
    "hb_edad_s", "hb_recibidos", "hb_perdidos", "hb_perdida_60s_pct", "hb_jitter_ms",
    "ping_ultimo_ms", "ping_prom_ms", "ping_p95_ms", "ping_max_ms", "ping_jitter_ms",
    "disp_60s_pct", "disp_total_pct",
]


class RegistroCSV:
    def __init__(self, ruta):
        self.ruta = ruta
        self.archivo = None
        try:
            os.makedirs(os.path.dirname(ruta), exist_ok=True)
            nuevo = not os.path.exists(ruta) or os.path.getsize(ruta) == 0
            # newline="" es lo que pide el módulo csv para no duplicar saltos de línea en Windows.
            self.archivo = open(ruta, "a", newline="", encoding="utf-8")
            self.escritor = csv.writer(self.archivo)
            if nuevo:
                self.escritor.writerow(COLUMNAS_CSV)
            print(f"[monitor] CSV en {ruta}", flush=True)
        except OSError as e:
            print(f"[monitor] no pude abrir el CSV {ruta}: {e}", flush=True)

    def escribir(self, resumen):
        if self.archivo is None:
            return
        fecha = time.strftime("%Y-%m-%d %H:%M:%S")
        for nombre, d in resumen["servicios"].items():
            hb = d["hb"] or {}
            pg = d["ping"] or {}
            self.escritor.writerow([
                fecha, resumen["en_marcha_s"], nombre, d["vlan"], d["estado"], d["motivo"], d["vivo"],
                hb.get("edad_s"), hb.get("recibidos"), hb.get("perdidos"), hb.get("perdida_60s_pct"),
                hb.get("jitter_ms"),
                pg.get("rtt_ultimo_ms"), pg.get("rtt_prom_ms"), pg.get("rtt_p95_ms"), pg.get("rtt_max_ms"),
                pg.get("jitter_ms"), pg.get("disp_60s_pct"), pg.get("disp_total_pct"),
            ])
        # flush en cada tanda: si el contenedor muere, lo escrito hasta ahí queda en el disco.
        self.archivo.flush()


# ---------------------------------------------------------------------------------------------
# 5. Dashboard HTTP
# ---------------------------------------------------------------------------------------------

class Manejador(BaseHTTPRequestHandler):
    def _responder(self, codigo, tipo, cuerpo):
        self.send_response(codigo)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(cuerpo)))
        # Sin caché: el navegador debe pedir el resumen fresco cada segundo.
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(cuerpo)

    def do_GET(self):
        ruta = self.path.split("?", 1)[0]
        if gateway.get(self, ruta):
            return
        if ruta == "/api/exportar.json":
            with candado:
                datos = armar_resumen(con_historial=True)
            gateway.json_reply(self, 200, datos)
            return
        if ruta == "/api/metricas.csv":
            try:
                with open(os.path.join(RESULTADOS, "admin_metricas.csv"), "rb") as f:
                    self._responder(200, "text/csv; charset=utf-8", f.read())
            except OSError:
                gateway.json_reply(self, 404, {"error": "Todavía no hay muestras"})
            return
        if ruta == "/api/resumen.json":
            with candado:
                datos = armar_resumen(con_historial=True)
            self._responder(200, "application/json; charset=utf-8",
                            json.dumps(datos, ensure_ascii=False).encode("utf-8"))
        else:
            self._responder(404, "text/plain; charset=utf-8", b"no existe")

    def do_POST(self):
        gateway.post(self, self.path.split("?", 1)[0])

    def log_message(self, *args):
        pass    # sin una línea por pedido en `docker logs` (el dashboard pide uno por segundo)


def hilo_http():
    srv = ThreadingHTTPServer(("0.0.0.0", HTTP_PUERTO), Manejador)
    srv.daemon_threads = True
    print(f"[monitor] dashboard en http://0.0.0.0:{HTTP_PUERTO}/", flush=True)
    srv.serve_forever()


# ---------------------------------------------------------------------------------------------
# Programa principal
# ---------------------------------------------------------------------------------------------

def main():
    # Rutas hacia la VLAN 1 y la VLAN 2 por el router (RUTAS y ROUTER_IP las pone el compose).
    lab.configurar_rutas()
    print(f"[monitor] sondeo ICMP a: {', '.join(f'{n}={ip}' for n, ip in OBJETIVOS.items()) or '(nadie)'}", flush=True)
    print(f"[monitor] umbrales: HB {HB_TIMEOUT_S:g} s, RTT p95 {UMBRAL_RTT_P95_MS:g} ms, "
          f"jitter {UMBRAL_JITTER_MS:g} ms, pérdida {UMBRAL_PERDIDA_PCT:g} %", flush=True)

    registro = RegistroCSV(os.path.join(RESULTADOS, "admin_metricas.csv"))
    iniciar_mqtt()
    # daemon=True en todos: si el hilo principal termina (error), el proceso entero termina y
    # arrancar.sh detiene el contenedor para que Docker lo reinicie.
    threading.Thread(target=hilo_udp, daemon=True, name="udp").start()
    threading.Thread(target=hilo_http, daemon=True, name="http").start()
    for s in sondeos.values():
        threading.Thread(target=hilo_ping, args=(s,), daemon=True, name=f"ping-{s.nombre}").start()

    ultimo_resumen = 0.0
    while True:
        t = time.time()
        a_publicar = []
        with candado:
            for nombre in NOMBRES:
                estado, motivo = decidir(nombre)
                e = estados[nombre]
                if e["estado"] == "CAIDO" and estado != "CAIDO":
                    # Volvió: lo perdido durante la caída no es "lentitud" (ver olvidar_fallos).
                    if nombre in sondeos:
                        sondeos[nombre].olvidar_fallos()
                    if nombre in origenes:
                        origenes[nombre].olvidar_ventana()
                    estado, motivo = decidir(nombre)
                if estado != e["estado"]:
                    print(f"[monitor] {nombre}: {e['estado']} -> {estado} ({motivo})", flush=True)
                    evento = {"t": round(t, 3), "servicio": nombre, "anterior": e["estado"],
                              "estado": estado, "motivo": motivo}
                    EVENTOS.append(evento)
                    try:
                        with open(os.path.join(RESULTADOS, "eventos.jsonl"), "a", encoding="utf-8") as archivo:
                            archivo.write(json.dumps(evento, ensure_ascii=False) + "\n")
                    except OSError as exc:
                        print(f"[monitor] No se pudo guardar el evento: {exc}", flush=True)
                    e["estado"], e["desde"] = estado, t
                    e["publicado_t"] = 0.0          # publicar ya, no esperar al refresco
                e["motivo"] = motivo
                # Retenido: quien se suscriba después (la esclava al reconectarse) recibe al instante el
                # último estado sin esperar. Se publica al cambiar y cada 10 s para refrescar.
                if t - e["publicado_t"] >= 10.0:
                    a_publicar.append((nombre, estado))
            resumen = armar_resumen() if t - ultimo_resumen >= 2.0 else None
        for nombre, estado in a_publicar:
            if publicar(f"lab/estado/{nombre}", estado, retener=True, qos=1):
                with candado:
                    estados[nombre]["publicado_t"] = t
        if resumen is not None:
            ultimo_resumen = t
            publicar("lab/admin/resumen", json.dumps(resumen, ensure_ascii=False))
            registro.escribir(resumen)
        time.sleep(0.5)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
