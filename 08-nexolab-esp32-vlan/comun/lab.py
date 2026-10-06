"""Piezas que comparten todos los contenedores de las zonas: ruta hacia el admin, latido y MQTT.

Cada contenedor (track-server, player-N, sim-<robot>) hace al arrancar lo mismo:

    from comun import lab
    lab.configurar_rutas()                       # llegar al plano de administración por el router
    lat = lab.Latido("player-1"); lat.iniciar()  # HB por UDP al admin cada 1 s
    mq = lab.Metricas("player-1")                # MQTT: lab/vivo/player-1 y lab/metricas/player-1
    ...
    mq.publicar({"recibidos": 120, "perdidos": 0})

Nada de esto debe tumbar el contenedor si el admin todavía no arrancó o se cayó: la zona sigue
funcionando aislada (es justo lo que muestra la prueba de disponibilidad) y se reconecta sola.

Variables de entorno (las pone docker-compose.yml):
    ROUTER_IP    IP del router en la red (VLAN) de este contenedor, p. ej. 192.168.10.254
    RUTAS        redes a las que se llega por el router, separadas por coma (192.168.30.0/24)
    ADMIN_IP     IP del admin (broker MQTT y receptor de latidos), 192.168.30.10
    MQTT_PUERTO  1883      HB_PUERTO 5300
"""

import json
import os
import socket
import subprocess
import threading
import time

ADMIN_IP = os.environ.get("ADMIN_IP", "192.168.30.10")
MQTT_PUERTO = int(os.environ.get("MQTT_PUERTO", "1883"))
HB_PUERTO = int(os.environ.get("HB_PUERTO", "5300"))


def ms():
    """Milisegundos de un reloj monótono (no salta si cambia la hora del sistema), como millis()."""
    return int(time.monotonic() * 1000)


def configurar_rutas():
    """Agrega 'ip route replace <red> via <router>' por cada red de RUTAS.

    Por qué hace falta: la puerta de enlace por defecto de cada red de Docker es el propio Docker
    (la .1), que NO deja pasar de una red bridge a otra (sus reglas de aislamiento). Para que un
    paquete de la VLAN 1 llegue a la VLAN 3 hay que decirle al contenedor "esa red está detrás del
    router .254". Necesita la capacidad NET_ADMIN (cap_add en el compose) y el paquete iproute2.
    Si falla (se corre fuera de Docker, sin permisos) solo avisa: el resto sigue funcionando.
    """
    router = os.environ.get("ROUTER_IP")
    rutas = [r.strip() for r in os.environ.get("RUTAS", "").split(",") if r.strip()]
    if not router or not rutas:
        return []
    hechas = []
    for red in rutas:
        try:
            r = subprocess.run(["ip", "route", "replace", red, "via", router],
                               capture_output=True, text=True, timeout=5)
        except (OSError, subprocess.TimeoutExpired) as e:
            # OSError: no existe el comando "ip" (Windows, o una imagen sin iproute2). Sin esto, el
            # "solo avisa" de arriba no se cumplía: la excepción tumbaba el programa al arrancar.
            print(f"[lab] no pude agregar la ruta {red} via {router}: {e}", flush=True)
            continue
        if r.returncode == 0:
            hechas.append(red)
            print(f"[lab] ruta {red} via {router}", flush=True)
        else:
            print(f"[lab] no pude agregar la ruta {red} via {router}: {r.stderr.strip()}", flush=True)
    return hechas


class Latido:
    """Manda 'HB,<origen>,<seq>,<t_ms>' por UDP al admin cada `periodo` segundos, en un hilo aparte.

    UDP y no MQTT a propósito: el latido mide la red cruda (sin la cola ni los reintentos de TCP),
    así el jitter que calcula el admin es el de la red y no el del broker. Si el admin no está, el
    datagrama simplemente se pierde; no hay conexión que reabrir.
    """

    def __init__(self, origen, periodo=1.0, destino=None):
        self.origen = origen
        self.periodo = periodo
        self.destino = destino or (ADMIN_IP, HB_PUERTO)
        self.seq = 0
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._parar = threading.Event()

    def iniciar(self):
        threading.Thread(target=self._bucle, daemon=True, name=f"latido-{self.origen}").start()
        return self

    def _bucle(self):
        siguiente = time.monotonic()
        while not self._parar.is_set():
            try:
                self._sock.sendto(f"HB,{self.origen},{self.seq},{ms()}".encode(), self.destino)
            except OSError:
                pass  # sin ruta todavía (router arrancando): se intenta en el siguiente período
            self.seq += 1
            # Se programa contra un reloj fijo (siguiente += periodo) y no con sleep(periodo) a secas,
            # para que el tiempo que tarda el envío no se acumule: si no, el propio emisor
            # agregaría "jitter" que no es de la red.
            siguiente += self.periodo
            self._parar.wait(max(0.0, siguiente - time.monotonic()))

    def parar(self):
        self._parar.set()


class Metricas:
    """Cliente MQTT mínimo para publicar el estado de un contenedor en el broker del admin.

    - Al conectar publica `lab/vivo/<servicio>` = "1" (retenido) y deja un testamento (LWT) con "0":
      si el contenedor muere sin despedirse, el broker publica el "0" solo cuando nota que la
      conexión se cortó (keepalive de 5 s). Así el admin y la ESP32 esclava se enteran sin sondear.
    - `publicar(dict)` manda el JSON a `lab/metricas/<servicio>`.
    - Si no hay broker (o paho-mqtt no está instalado) no lanza nada: se reintenta en segundo plano.
    """

    def __init__(self, servicio, host=None, puerto=None, keepalive=5):
        self.servicio = servicio
        self.cliente = None
        self.conectado = False
        try:
            import paho.mqtt.client as mqtt
        except ImportError:
            print("[lab] paho-mqtt no está instalado: sin métricas MQTT", flush=True)
            return
        # paho-mqtt 2.x pide elegir la versión de la API de callbacks; la 1.x no tiene ese parámetro.
        if hasattr(mqtt, "CallbackAPIVersion"):
            c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=servicio)
        else:
            c = mqtt.Client(client_id=servicio)
        c.will_set(f"lab/vivo/{servicio}", "0", qos=1, retain=True)
        c.on_connect = self._al_conectar
        c.on_disconnect = self._al_desconectar
        c.reconnect_delay_set(min_delay=1, max_delay=5)
        self.cliente = c
        # connect_async + loop_start: la conexión y las reconexiones van en el hilo de paho; si el
        # broker no está, este constructor vuelve enseguida en vez de bloquear el arranque.
        c.connect_async(host or ADMIN_IP, puerto or MQTT_PUERTO, keepalive=keepalive)
        c.loop_start()

    def _al_conectar(self, cliente, userdata, flags, rc, *props):
        self.conectado = (rc == 0) if isinstance(rc, int) else not getattr(rc, "is_failure", False)
        if self.conectado:
            cliente.publish(f"lab/vivo/{self.servicio}", "1", qos=1, retain=True)
            print(f"[lab] MQTT conectado como {self.servicio}", flush=True)

    def _al_desconectar(self, *args):
        self.conectado = False

    def publicar(self, datos, sub="metricas"):
        if self.cliente is None or not self.conectado:
            return False
        datos = dict(datos)
        datos.setdefault("servicio", self.servicio)
        datos.setdefault("t_ms", ms())
        self.cliente.publish(f"lab/{sub}/{self.servicio}", json.dumps(datos), qos=0)
        return True

    def cerrar(self):
        """Despedida limpia: publica vivo=0 a mano (el LWT solo salta si la conexión se corta mal)."""
        if self.cliente is not None:
            try:
                self.cliente.publish(f"lab/vivo/{self.servicio}", "0", qos=1, retain=True).wait_for_publish(1)
            except Exception:
                pass
            self.cliente.loop_stop()
            self.cliente.disconnect()
