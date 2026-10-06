"""lab_pruebas.py - Piezas comunes de las pruebas experimentales del tema 11 (no es una prueba en sí).

Lo usan medir_red.py, prueba_aislamiento.py y prueba_disponibilidad.py. Agrupa tres cosas:

1. **Encontrar los contenedores del stack** sin depender de cómo se llamen. Docker Compose le pone a
   cada contenedor dos etiquetas: `com.docker.compose.project=zonas-esp32` y
   `com.docker.compose.service=<servicio>`. Filtrando por esas etiquetas se encuentra "el player-1 del
   proyecto zonas-esp32" se llame el contenedor `zonas-esp32-player-1-1` o `player-1` (si el compose
   usa container_name). Así las pruebas no se rompen si alguien cambia los nombres.

2. **Ejecutar cosas DENTRO de la red de un contenedor**: `docker exec` corre un comando en el
   contenedor, pero las imágenes python:3.11-slim (players, robots) no traen `ping`. Para no obligar a
   instalarlo en las imágenes, se usa un contenedor auxiliar `alpine:3.20` arrancado con
   `--network container:<id>`: comparte la MISMA pila de red (mismas IPs, mismas rutas, mismas
   interfaces) que el contenedor objetivo, así que un ping desde ahí sale exactamente como saldría del
   player-1, pero con el `ping` de busybox. El auxiliar se borra solo (`--rm`) al terminar.

3. **Observar el broker MQTT del admin** desde el PC (puerto 1883 publicado): un hilo que guarda con
   marca de tiempo cada mensaje de `lab/estado/+`, `lab/vivo/+`, `lab/metricas/+` y
   `lab/admin/resumen`. Con eso se miden tiempos ("cuánto tardó el admin en publicar CAIDO") y se
   calculan disponibilidades.

Además: el plan de direcciones del contrato (qué IP tiene cada servicio en qué VLAN), percentiles y el
jitter RFC 3550 (se reutiliza `comun.protocolo.Jitter`, el mismo que usa el admin).
"""

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time

AQUI = os.path.dirname(os.path.abspath(__file__))
TEMA = os.path.dirname(AQUI)
RESULTADOS = os.path.join(AQUI, "resultados")
if TEMA not in sys.path:
    sys.path.insert(0, TEMA)

from comun import protocolo  # noqa: E402

PROYECTO = os.environ.get("PROYECTO_COMPOSE", "nexolab-esp32")
IMAGEN_AUX = "alpine:3.20"   # la misma base del router y del admin: ya está descargada

# ---------------------------------------------------------------------------------------------
# Plan de direcciones del contrato (PLAN-TEMA-11, "Redes"). Una sola tabla para todas las pruebas.
# ---------------------------------------------------------------------------------------------
SERVICIOS = {
    # servicio        (vlan, ip)
    "track-server":  (1, "192.168.10.10"),
    "player-1":      (1, "192.168.10.21"),
    "player-2":      (1, "192.168.10.22"),
    "player-3":      (1, "192.168.10.23"),
    "ctrl-1":        (1, "192.168.10.31"),
    "ctrl-2":        (1, "192.168.10.32"),
    "ctrl-3":        (1, "192.168.10.33"),
    "sim-spot":      (2, "192.168.20.21"),
    "sim-pepper":    (2, "192.168.20.22"),
    "sim-nao":       (2, "192.168.20.23"),
    "ctrl-spot":     (2, "192.168.20.31"),
    "ctrl-pepper":   (2, "192.168.20.32"),
    "ctrl-nao":      (2, "192.168.20.33"),
    "admin":         (3, "192.168.30.10"),
    "esclava":       (3, "192.168.30.40"),
}
ROUTER = {1: "192.168.10.254", 2: "192.168.20.254", 3: "192.168.30.254"}
REDES = {1: "192.168.10.0/24", 2: "192.168.20.0/24", 3: "192.168.30.0/24"}
# Los 6 servicios que tienen LED en la esclava (y estado OK/LENTO/CAIDO en el admin).
CON_LED = ["player-1", "player-2", "player-3", "sim-spot", "sim-pepper", "sim-nao"]
# Servicios de las zonas que el admin debería alcanzar por el router (sin los ESP32 emulados).
ZONAS = ["track-server", "player-1", "player-2", "player-3", "sim-spot", "sim-pepper", "sim-nao"]


def ahora():
    """Segundos de reloj de pared (time.time): se usan para marcar eventos de MQTT y de Docker en la
    misma escala. Para medir intervalos cortos dentro de un mismo proceso basta y sobra (ms)."""
    return time.time()


def guardar_json(nombre, datos):
    os.makedirs(RESULTADOS, exist_ok=True)
    ruta = os.path.join(RESULTADOS, nombre)
    with open(ruta, "w", encoding="utf-8") as f:
        json.dump(datos, f, ensure_ascii=False, indent=2)
    print(f"[guardado] {ruta}")
    return ruta


# ---------------------------------------------------------------------------------------------
# Docker
# ---------------------------------------------------------------------------------------------
def docker(*args, timeout=60, entrada=None):
    """Corre `docker <args>` y devuelve (código, salida estándar + error juntas). Nunca lanza: si
    docker no está o tarda más que `timeout`, devuelve código 999 y el motivo en el texto."""
    try:
        r = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout,
                           input=entrada, encoding="utf-8", errors="replace")
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except FileNotFoundError:
        return 999, "docker no está instalado o no está en el PATH"
    except subprocess.TimeoutExpired:
        return 999, f"docker {' '.join(args)} tardó más de {timeout} s"


def docker_disponible():
    c, _ = docker("info", "--format", "{{.ServerVersion}}", timeout=20)
    return c == 0


def contenedor(servicio, incluir_detenidos=True):
    """Id del contenedor del `servicio` en el proyecto compose, o None si no existe.
    Con incluir_detenidos=True (docker ps -a) también encuentra uno parado (para `docker start`)."""
    args = ["ps", "-q", "--filter", f"label=com.docker.compose.project={PROYECTO}",
            "--filter", f"label=com.docker.compose.service={servicio}"]
    if incluir_detenidos:
        args.insert(1, "-a")
    c, out = docker(*args, timeout=20)
    ids = out.split() if c == 0 else []
    return ids[0] if ids else None


def corriendo(servicio):
    cid = contenedor(servicio)
    if not cid:
        return False
    c, out = docker("inspect", "-f", "{{.State.Running}}", cid, timeout=20)
    return c == 0 and out.strip() == "true"


def servicios_presentes():
    """Servicios del contrato que tienen contenedor corriendo ahora mismo."""
    return [s for s in list(SERVICIOS) + ["router"] if corriendo(s)]


def exec_en(servicio, comando, timeout=60):
    """`docker exec <contenedor> <comando...>`. Devuelve (código, salida)."""
    cid = contenedor(servicio, incluir_detenidos=False)
    if not cid:
        return 998, f"el servicio {servicio} no está corriendo"
    return docker("exec", cid, *comando, timeout=timeout)


def en_red_de(servicio, script_sh, net_admin=False, timeout=90):
    """Corre `sh -c script_sh` en un alpine auxiliar que comparte la red del contenedor `servicio`.

    net_admin=True le da la capacidad NET_ADMIN (para `ip route` o `tc`): como comparte el espacio de
    red, lo que cambie (una ruta, una cola de tc) queda puesto en el contenedor objetivo aunque el
    auxiliar ya haya terminado. Por eso quien lo use para cambiar algo debe deshacerlo después.
    """
    cid = contenedor(servicio, incluir_detenidos=False)
    if not cid:
        return 998, f"el servicio {servicio} no está corriendo"
    args = ["run", "--rm", "--network", f"container:{cid}"]
    if net_admin:
        args += ["--cap-add", "NET_ADMIN"]
    args += [IMAGEN_AUX, "sh", "-c", script_sh]
    return docker(*args, timeout=timeout)


def tiene_comando(servicio, programa):
    c, _ = exec_en(servicio, ["sh", "-c", f"command -v {programa}"], timeout=20)
    return c == 0


# ---------------------------------------------------------------------------------------------
# ping: análisis de la salida (busybox e iputils) y ping "desde" un servicio
# ---------------------------------------------------------------------------------------------
RE_RESPUESTA = re.compile(r"(?:icmp_)?seq=(\d+)\s+ttl=(\d+)\s+time[=<]\s*([\d.]+)\s*ms")
RE_RESUMEN = re.compile(r"(\d+) packets transmitted, (\d+) (?:packets )?received")


def analizar_ping(texto):
    """Saca de la salida de ping las respuestas (seq, ttl, rtt_ms) y el resumen enviados/recibidos.

    Sirve para las dos variantes que hay en los contenedores:
      busybox (alpine):  64 bytes from 192.168.20.23: seq=0 ttl=63 time=0.412 ms
      iputils (debian):  64 bytes from 192.168.20.23: icmp_seq=1 ttl=63 time=0.412 ms
    El ttl importa: Linux sale con 64 y cada router le resta 1, así que una respuesta con ttl=63
    prueba que el paquete cruzó exactamente un enrutador (el router inter-VLAN).
    """
    respuestas = [(int(s), int(t), float(r)) for s, t, r in RE_RESPUESTA.findall(texto)]
    m = RE_RESUMEN.search(texto)
    enviados, recibidos = (int(m.group(1)), int(m.group(2))) if m else (None, len(respuestas))
    return {"enviados": enviados, "recibidos": recibidos,
            "rtt_ms": [r for _, _, r in respuestas],
            "ttl": sorted({t for _, t, _ in respuestas})}


def ping_desde(servicio, ip, n=3, espera_s=2):
    """Hace `n` pings desde el contenedor `servicio` a `ip` y devuelve el análisis + cómo se hizo.

    -w (plazo total) asegura que no se quede colgado si no hay respuesta: con la red bloqueada el
    ping de busybox esperaría -W segundos por CADA paquete. Si el contenedor no tiene ping (las
    imágenes slim de Debian no lo traen) se usa el alpine auxiliar en su misma red.
    """
    plazo = n + espera_s
    cmd = f"ping -c {n} -W {espera_s} -w {plazo} {ip}"
    if tiene_comando(servicio, "ping"):
        c, out = exec_en(servicio, ["sh", "-c", cmd], timeout=plazo + 30)
        via = "docker exec"
    else:
        c, out = en_red_de(servicio, cmd, timeout=plazo + 60)
        via = f"auxiliar {IMAGEN_AUX} en la red del contenedor"
    r = analizar_ping(out)
    r.update({"codigo": c, "via": via, "salida": out.strip()[-600:]})
    return r


# ---------------------------------------------------------------------------------------------
# Estadística
# ---------------------------------------------------------------------------------------------
def percentil(valores, p):
    """Percentil p (0-100) con interpolación lineal entre los dos vecinos (el método por defecto de
    numpy), escrito a mano para no depender de numpy en los contenedores ni en otras máquinas."""
    v = sorted(valores)
    if not v:
        return None
    if len(v) == 1:
        return v[0]
    k = (len(v) - 1) * p / 100.0
    i = int(k)
    f = k - i
    return v[i] + (v[min(i + 1, len(v) - 1)] - v[i]) * f


def resumen_rtt(muestras, intervalo_s):
    """Resumen de una serie de RTT de ping tomada a intervalo fijo.

    muestras: lista de (seq, rtt_ms). El jitter se calcula con la misma fórmula RFC 3550 del admin
    (comun.protocolo.Jitter): como el ping sale cada `intervalo_s` exactos, el "envío" del paquete
    seq es seq*intervalo y su "llegada" es envío + rtt. Entonces D = rtt_i - rtt_{i-1}: el jitter
    resulta ser el promedio móvil (1/16) de cuánto cambia el RTT de un paquete al siguiente.
    También se da el promedio simple de |ΔRTT| (más fácil de explicar, sin filtro).
    """
    if not muestras:
        return {"n": 0}
    muestras = sorted(muestras)
    rtts = [r for _, r in muestras]
    j = protocolo.Jitter()
    for seq, r in muestras:
        envio = seq * intervalo_s * 1000.0
        j.registrar(envio, envio + r)
    difs = [abs(b - a) for a, b in zip(rtts, rtts[1:])]
    return {
        "n": len(rtts),
        "prom_ms": round(sum(rtts) / len(rtts), 3),
        "p50_ms": round(percentil(rtts, 50), 3),
        "p95_ms": round(percentil(rtts, 95), 3),
        "max_ms": round(max(rtts), 3),
        "min_ms": round(min(rtts), 3),
        "jitter_rfc3550_ms": round(j.j, 3),
        "jitter_prom_abs_dif_ms": round(sum(difs) / len(difs), 3) if difs else 0.0,
    }


def fraccion_en_estado(eventos, t_ini, t_fin, estado_bueno="OK"):
    """Fracción del intervalo [t_ini, t_fin] en que un servicio estuvo en `estado_bueno`.

    eventos: lista ordenada de (t, valor) con los cambios de estado vistos (el primero puede ser el
    retenido que el broker manda al suscribirse, con t <= t_ini). Entre dos eventos el estado es el
    del primero (escalón). Si no se vio ningún estado, devuelve None (no se sabe: no es 0 %).
    """
    if not eventos or t_fin <= t_ini:
        return None
    total_bueno = 0.0
    previo = None  # (t, valor) vigente al empezar cada tramo
    for t, v in sorted(eventos):
        if t <= t_ini:
            previo = (t_ini, v)
            continue
        if t >= t_fin:
            break
        if previo is not None and previo[1] == estado_bueno:
            total_bueno += t - previo[0]
        elif previo is None:
            # Antes del primer evento no se sabe el estado: ese tramo no cuenta como bueno.
            pass
        previo = (t, v)
    if previo is not None and previo[1] == estado_bueno:
        total_bueno += t_fin - previo[0]
    return total_bueno / (t_fin - t_ini)


# ---------------------------------------------------------------------------------------------
# MQTT: observador del broker del admin
# ---------------------------------------------------------------------------------------------
TOPICOS = ["lab/estado/+", "lab/vivo/+", "lab/metricas/+", "lab/admin/resumen"]


class Observador:
    """Se suscribe al broker y guarda cada mensaje como (t_llegada, tópico, texto, retenido).

    - paho-mqtt corre su propio hilo (loop_start) y se reconecta solo: si se apaga el admin (que es el
      broker) el observador queda esperando y, al volver, se vuelve a suscribir (on_connect se llama
      en cada reconexión) y recibe otra vez los retenidos.
    - `retenido=True` marca los mensajes que el broker tenía guardados de antes de suscribirse: no son
      eventos nuevos, son "el último valor conocido". Para medir tiempos se ignoran.
    """

    def __init__(self, host="localhost", puerto=1883, topicos=None, id_cliente=None):
        import paho.mqtt.client as mqtt
        self.host, self.puerto = host, puerto
        self.topicos = topicos or TOPICOS
        self.mensajes = []
        self.conexiones = []       # marcas de tiempo de cada (re)conexión al broker
        self.desconexiones = []
        self._lock = threading.Lock()
        self._nuevo = threading.Condition(self._lock)
        cid = id_cliente or f"prueba-t11-{os.getpid()}"
        if hasattr(mqtt, "CallbackAPIVersion"):
            c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=cid)
        else:  # paho 1.x
            c = mqtt.Client(client_id=cid)
        c.on_connect = self._al_conectar
        c.on_disconnect = self._al_desconectar
        c.on_message = self._al_mensaje
        c.reconnect_delay_set(min_delay=1, max_delay=2)
        self.cliente = c

    def iniciar(self, esperar_s=10):
        """Conecta y devuelve True si en `esperar_s` quedó conectado."""
        self.cliente.connect_async(self.host, self.puerto, keepalive=10)
        self.cliente.loop_start()
        fin = time.time() + esperar_s
        while time.time() < fin:
            if self.conexiones:
                return True
            time.sleep(0.1)
        return False

    def parar(self):
        try:
            self.cliente.loop_stop()
            self.cliente.disconnect()
        except Exception:
            pass

    # --- callbacks de paho (corren en su hilo) ---
    def _al_conectar(self, cliente, userdata, flags, rc, *props):
        ok = (rc == 0) if isinstance(rc, int) else not getattr(rc, "is_failure", False)
        if ok:
            with self._lock:
                self.conexiones.append(ahora())
            for t in self.topicos:
                cliente.subscribe(t, qos=1)

    def _al_desconectar(self, *args):
        with self._lock:
            self.desconexiones.append(ahora())

    def _al_mensaje(self, cliente, userdata, msg):
        texto = msg.payload.decode("utf-8", errors="replace")
        with self._nuevo:
            self.mensajes.append((ahora(), msg.topic, texto, bool(msg.retain)))
            self._nuevo.notify_all()

    # --- consultas ---
    def copia(self):
        with self._lock:
            return list(self.mensajes)

    def eventos(self, prefijo, servicio=None, desde=None, incluir_retenidos=True):
        """Lista de (t, texto, retenido) del tópico `prefijo/servicio` (o de todos si servicio=None)."""
        salida = []
        for t, top, txt, ret in self.copia():
            if servicio is not None and top != f"{prefijo}/{servicio}":
                continue
            if servicio is None and not top.startswith(prefijo):
                continue
            if desde is not None and t < desde:
                continue
            if ret and not incluir_retenidos:
                continue
            salida.append((t, top, txt, ret))
        return salida

    def ultimo(self, prefijo, servicio):
        ev = self.eventos(prefijo, servicio)
        return ev[-1][2].strip() if ev else None

    def esperar(self, condicion, timeout):
        """Espera hasta que condicion(self) sea verdadera (se reevalúa con cada mensaje nuevo, y cada
        0,5 s por si la condición depende del tiempo). Devuelve el instante en que se cumplió o None."""
        fin = time.time() + timeout
        with self._nuevo:
            while True:
                self._lock.release()
                try:
                    if condicion(self):
                        return ahora()
                finally:
                    self._lock.acquire()
                resta = fin - time.time()
                if resta <= 0:
                    return None
                self._nuevo.wait(min(0.5, resta))

    def esperar_mensaje(self, topico, valor=None, desde=None, timeout=60):
        """Espera el primer mensaje NO retenido de `topico` llegado después de `desde` (y con texto
        `valor`, si se da). Devuelve su instante de llegada o None si no llegó a tiempo."""
        def cumple(obs):
            for t, top, txt, ret in obs.copia():
                if top == topico and not ret and (desde is None or t >= desde) \
                        and (valor is None or txt.strip() == valor):
                    return True
            return False

        if self.esperar(cumple, timeout) is None:
            return None
        for t, top, txt, ret in self.copia():
            if top == topico and not ret and (desde is None or t >= desde) \
                    and (valor is None or txt.strip() == valor):
                return t
        return None


def hay_broker(host="localhost", puerto=1883, timeout=3):
    """True si algo escucha en host:puerto (TCP). Sirve para avisar claro "no está el stack" en vez
    de quedarse esperando a paho."""
    import socket
    try:
        with socket.create_connection((host, puerto), timeout=timeout):
            return True
    except OSError:
        return False


def comprobar_entorno(requiere_docker=True, requiere_broker=True, host="localhost", puerto=1883):
    """Revisa lo mínimo antes de empezar una prueba y devuelve la lista de problemas (vacía = listo)."""
    problemas = []
    if requiere_docker:
        if shutil.which("docker") is None:
            problemas.append("no se encuentra el comando docker")
        elif not docker_disponible():
            problemas.append("Docker no responde (¿Docker Desktop abierto?)")
    if requiere_broker and not hay_broker(host, puerto):
        problemas.append(f"no hay broker MQTT en {host}:{puerto} (¿stack levantado? "
                         "docker compose --profile emulado up -d)")
    return problemas
