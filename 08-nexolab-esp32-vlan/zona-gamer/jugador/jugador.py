"""jugador.py - Contenedor "player-N" de la Zona Gamer (VLAN 1): puente entre la ESP32 maestra y la pista.

Qué hace, en una frase: recibe por UDP el joystick de SU ESP32 (`CTRL,...`) y lo reenvía por WebSocket
al servidor de pista (PyBullet), que es el que mueve el carro. Además mide la calidad del enlace y la
publica al plano de administración.

    ESP32 maestra --UDP 5000 (CTRL a 20 Hz)--> player-N --WebSocket JSON--> track-server :8765
                                                   |
                                                   +--HB UDP 5300 + MQTT lab/metricas/player-N--> admin

Por qué existe un contenedor por jugador (y no que la ESP32 hable directo con el servidor):
  - Cada jugador tiene su propia "identidad" en la red (IP fija 192.168.10.2N, nombre, color, estilo)
    y su propio LED en la ESP32 esclava: si un player se cae, solo se apaga su LED y su carro frena.
  - El ESP32 habla UDP (lo más liviano para un microcontrolador: snprintf + sendto, sin conexión ni
    reintentos), y el contenedor traduce a WebSocket (TCP, ordenado, con conexión), que es lo cómodo
    para el servidor. El contenedor es justo el lugar donde se puede medir la red del ESP32 (pérdidas
    y jitter) sin cargar al servidor de física.

Qué mide (lo publica cada 2 s en `lab/metricas/player-N` y lo imprime en la consola):
  - recibidos / perdidos / % de pérdida: por el número de secuencia `seq` del CTRL (ContadorSecuencia).
  - jitter ESP32 -> player (ms): RFC 3550 con el t_ms del ESP32 y el reloj de este contenedor (Jitter).
  - RTT player <-> servidor (ms): manda `{"tipo":"ping","t_ms":..}` cada 1 s y el servidor devuelve
    el mismo t_ms en un `pong`; RTT = ahora - t_ms (mismo reloj: no hace falta sincronizar nada).
  - IP:puerto del ESP32 que le está hablando y la posición/vuelta de su carro (del `estado` del servidor).

Failsafe (seguridad, como en un carro de radiocontrol): si en 1 s no llega ningún CTRL del ESP32
(se desconectó, se quedó sin batería, se cayó el WiFi), el player manda `vel 0` al servidor para que el
carro no siga acelerando solo con el último valor recibido.

Variables de entorno (las pone docker-compose.yml):
  JUGADOR      1, 2 o 3 (qué carro maneja; también da el nombre del servicio player-N)
  NOMBRE       nombre visible del piloto (por defecto "Jugador N")
  COLOR        "r,g,b" 0-255 (por defecto rojo/azul/verde según N)
  ESTILO       clasico | deportivo | rally
  SERVIDOR_WS  ws://192.168.10.10:8765
  PUERTO_UDP   5000
  (más las de comun/lab.py: ROUTER_IP, RUTAS, ADMIN_IP, MQTT_PUERTO, HB_PUERTO)

Se puede correr también en Windows sin Docker (desde la carpeta del tema):
    entorno\\Scripts\\python.exe zona-gamer\\jugador\\jugador.py
"""

import asyncio
import json
import os
import signal
import sys
import time

# comun/ está dos carpetas arriba en el repo (T/comun) y en /app/comun dentro de la imagen: se agregan
# las dos rutas posibles para que el mismo archivo funcione en los dos lados sin cambiar nada.
AQUI = os.path.dirname(os.path.abspath(__file__))
for _r in (os.path.normpath(os.path.join(AQUI, "..", "..")), AQUI):
    if os.path.isdir(os.path.join(_r, "comun")) and _r not in sys.path:
        sys.path.insert(0, _r)

from comun import lab  # noqa: E402
from comun import protocolo as pr  # noqa: E402

try:
    # websockets >= 13 trae la implementación nueva sobre asyncio en websockets.asyncio.
    from websockets.asyncio.client import connect as ws_connect
    from websockets.exceptions import WebSocketException
except ImportError:  # pragma: no cover - solo si alguien lo corre sin la librería
    ws_connect = None
    WebSocketException = Exception

# ------------------------------------------------------------------------------------------------
# Configuración
# ------------------------------------------------------------------------------------------------
JUGADOR = int(os.environ.get("JUGADOR", "1"))
if JUGADOR not in (1, 2, 3):
    raise SystemExit(f"JUGADOR debe ser 1, 2 o 3 (llegó {JUGADOR})")
SERVICIO = f"player-{JUGADOR}"

# Valores por defecto del contrato: player-1 rojo/deportivo, player-2 azul/clasico, player-3 verde/rally.
_POR_DEFECTO = {1: ("255,40,40", "deportivo"), 2: ("40,90,255", "clasico"), 3: ("40,200,70", "rally")}
NOMBRE = os.environ.get("NOMBRE", f"Jugador {JUGADOR}")
ESTILO = os.environ.get("ESTILO", _POR_DEFECTO[JUGADOR][1])


def _leer_color(texto):
    """'255, 40,40' -> [255, 40, 40]. Si viene mal escrito se usa el color por defecto (no se cae)."""
    try:
        c = [max(0, min(255, int(v))) for v in texto.split(",")]
        if len(c) == 3:
            return c
    except ValueError:
        pass
    print(f"[{SERVICIO}] COLOR '{texto}' no es 'r,g,b': uso el de por defecto", flush=True)
    return [int(v) for v in _POR_DEFECTO[JUGADOR][0].split(",")]


COLOR = _leer_color(os.environ.get("COLOR", _POR_DEFECTO[JUGADOR][0]))
SERVIDOR_WS = os.environ.get("SERVIDOR_WS", "ws://192.168.10.10:8765")
PUERTO_UDP = int(os.environ.get("PUERTO_UDP", "5000"))

FAILSAFE_S = 1.0          # sin CTRL durante este tiempo -> vel 0 (contrato)
FAILSAFE_PERIODO_S = 0.1  # mientras dura el failsafe se repite el "vel 0" a 10 Hz: así el servidor
                          # tampoco dispara su propio failsafe por "player mudo" y se ve que el player vive
PING_PERIODO_S = 1.0      # ping de RTT al servidor (contrato)
METRICAS_PERIODO_S = 2.0  # métricas MQTT y línea de consola (contrato)
AVISO_SILENCIO_S = 5.0    # sin nada por UDP tanto tiempo -> se imprime la ayuda de diagnóstico
ESPERA_MIN_S, ESPERA_MAX_S = 1.0, 10.0  # reconexión al servidor: 1, 2, 4, 8, 10, 10... segundos


def log(texto):
    print(f"[{SERVICIO}] {texto}", flush=True)


# ------------------------------------------------------------------------------------------------
# Estado compartido (todo corre en un solo hilo de asyncio: no hacen falta candados)
# ------------------------------------------------------------------------------------------------
class Estado:
    def __init__(self):
        self.contador = pr.ContadorSecuencia()   # pérdidas por seq del ESP32
        self.jitter = pr.Jitter()                # jitter ESP32 -> player (RFC 3550)
        self.ultimo_ctrl = None                  # último pr.Ctrl válido de NUESTRO jugador
        self.t_ultimo_ctrl = None                # time.monotonic() de ese CTRL
        self.esp32 = None                        # (ip, puerto) del último ESP32 que nos habló
        self.ultima_linea = ""                   # última línea cruda recibida (lo que sea), para depurar
        self.t_ultimo_udp = None                 # cualquier datagrama, válido o no
        self.ajenos = 0                          # CTRL de otro jugador (ESP32 mal configurada)
        self.basura = 0                          # datagramas que no son CTRL válidos
        self.ws = None                           # conexión WebSocket abierta (o None)
        self.ws_conexiones = 0                   # cuántas veces se conectó (reconexiones = esto - 1)
        self.rtt_ms = None                       # último RTT player <-> servidor
        self.rtt_prom_ms = None                  # promedio móvil (1/8) del RTT, más estable para la tabla
        self.rtt_jitter_ms = 0.0                 # variación del RTT (misma idea que el jitter RFC 3550)
        self.pings_sin_pong = 0
        self.carro = None                        # dict de nuestro carro en el último `estado`
        self.t_estado = None
        self.controles_enviados = 0
        self.failsafe = False                    # True mientras el player está mandando vel 0 por silencio
        self.failsafes = 0                       # cuántas veces se activó
        self.cola = None                         # asyncio.Queue de mensajes para el servidor


E = Estado()


# ------------------------------------------------------------------------------------------------
# UDP: lo que manda el ESP32
# ------------------------------------------------------------------------------------------------
class ReceptorUDP(asyncio.DatagramProtocol):
    """Recibe los datagramas del ESP32 dentro del bucle de asyncio.

    Por qué asyncio y no un recvfrom bloqueante: el mismo hilo atiende el UDP, el WebSocket, el ping
    y el failsafe. asyncio llama a datagram_received apenas llega algo, sin hilos ni sondeo, así que
    el CTRL se reenvía al servidor con el menor retardo posible y nunca se queda esperando nada.
    """

    def datagram_received(self, datos, dire):
        ahora_ms = lab.ms()
        E.t_ultimo_udp = time.monotonic()
        E.ultima_linea = datos[:120].decode("ascii", errors="replace").strip()
        msg = pr.leer(datos)
        if not isinstance(msg, pr.Ctrl):
            E.basura += 1
            return
        if msg.jugador != JUGADOR:
            # Una ESP32 configurada para otro carro nos está mandando a nosotros: se ignora (si no,
            # dos joysticks manejarían el mismo carro) y se cuenta para que se vea en las métricas.
            E.ajenos += 1
            if E.ajenos in (1, 100, 1000):
                log(f"AVISO: llega CTRL del jugador {msg.jugador} desde {dire[0]}:{dire[1]} "
                    f"(este es el {JUGADOR}); revisa el ID en config.h de esa ESP32")
            return
        if E.esp32 != dire:
            log(f"ESP32 hablando desde {dire[0]}:{dire[1]} (primera línea: {E.ultima_linea})")
            if E.esp32 is not None and E.esp32[0] != dire[0]:
                # Otra placa (otra IP): su seq y su reloj no tienen nada que ver con los de la anterior.
                E.contador = pr.ContadorSecuencia()
                E.jitter = pr.Jitter()
            E.esp32 = dire
        E.contador.registrar(msg.seq)
        E.jitter.registrar(msg.t_ms, ahora_ms)
        E.ultimo_ctrl = msg
        E.t_ultimo_ctrl = time.monotonic()
        if E.failsafe:
            E.failsafe = False
            log("vuelve el control del ESP32: fin del failsafe")
        encolar({"tipo": "control", "jugador": JUGADOR, "dir": msg.dir, "vel": msg.vel,
                 "boton": msg.boton, "seq": msg.seq, "t_ms": msg.t_ms})

    def error_received(self, exc):
        # Windows reporta aquí un ICMP "puerto inalcanzable" de un envío anterior; en Linux casi nunca.
        pass


def encolar(mensaje):
    """Deja un mensaje para el servidor. Si no hay conexión se descarta: un control viejo no sirve.

    La cola es corta (20 = 1 s a 20 Hz): si el servidor se atrasa, se tira lo más viejo para que el
    carro responda al joystick de AHORA y no a uno de hace varios segundos.
    """
    if E.ws is None or E.cola is None:
        return
    if E.cola.full():
        try:
            E.cola.get_nowait()
        except asyncio.QueueEmpty:
            pass
    E.cola.put_nowait(json.dumps(mensaje))


# ------------------------------------------------------------------------------------------------
# WebSocket: el servidor de pista
# ------------------------------------------------------------------------------------------------
async def sesion_ws(ws):
    """Una conexión ya abierta: saluda, y corre en paralelo enviar / recibir / ping hasta que se corte."""
    E.cola = asyncio.Queue(maxsize=20)
    await ws.send(json.dumps({"tipo": "hola", "jugador": JUGADOR, "nombre": NOMBRE,
                              "color": COLOR, "estilo": ESTILO}))
    E.ws = ws
    E.ws_conexiones += 1
    E.pings_sin_pong = 0
    log(f"conectado a {SERVIDOR_WS} (conexión #{E.ws_conexiones}); hola enviado "
        f"({NOMBRE}, color {COLOR}, {ESTILO})")

    async def enviar():
        while True:
            texto = await E.cola.get()
            await ws.send(texto)
            E.controles_enviados += 1

    async def recibir():
        async for texto in ws:
            try:
                m = json.loads(texto)
            except (ValueError, TypeError):
                continue
            tipo = m.get("tipo") if isinstance(m, dict) else None
            if tipo == "pong":
                atender_pong(m)
            elif tipo == "estado":
                atender_estado(m)

    async def ping():
        while True:
            # El t_ms va y vuelve igual; al volver se resta del reloj de ESTE contenedor.
            await ws.send(json.dumps({"tipo": "ping", "t_ms": lab.ms()}))
            E.pings_sin_pong += 1
            await asyncio.sleep(PING_PERIODO_S)

    tareas = [asyncio.create_task(c()) for c in (enviar, recibir, ping)]
    try:
        # Si cualquiera termina (la conexión se cerró o falló un send) se baja todo y se reconecta.
        hechas, _ = await asyncio.wait(tareas, return_when=asyncio.FIRST_COMPLETED)
        for t in hechas:
            if t.exception() is not None:
                raise t.exception()
    finally:
        for t in tareas:
            t.cancel()
        E.ws = None
        E.cola = None


def atender_pong(m):
    try:
        rtt = lab.ms() - int(m["t_ms"])
    except (KeyError, ValueError, TypeError):
        return
    if rtt < 0 or rtt > 60000:
        return  # pong de otra conexión o basura
    E.pings_sin_pong = 0
    if E.rtt_ms is not None:
        E.rtt_jitter_ms += (abs(rtt - E.rtt_ms) - E.rtt_jitter_ms) / 16.0
    E.rtt_ms = rtt
    E.rtt_prom_ms = rtt if E.rtt_prom_ms is None else E.rtt_prom_ms + (rtt - E.rtt_prom_ms) / 8.0


def atender_estado(m):
    """Del estado de TODA la pista (20 Hz) solo guarda el carro propio: posición, vuelta, velocidad."""
    E.t_estado = time.monotonic()
    carros = m.get("carros") or []
    if not isinstance(carros, list):
        return      # estado mal formado: se ignora (no vale la pena cortar la conexión por eso)
    puesto = None
    for c in carros:
        if isinstance(c, dict) and c.get("id") == SERVICIO:
            E.carro = c
            # El servidor de pista manda el puesto en carrera como "posicion" (1 = primero, por
            # distancia recorrida); "puesto" se acepta también por si otro servidor lo llama así.
            puesto = c.get("posicion", c.get("puesto"))
            break
    if E.carro is not None and puesto is None:
        # Si el servidor no manda el puesto, se estima con las vueltas: es solo informativo.
        mias = E.carro.get("vuelta", 0) or 0
        E.carro["puesto_estimado"] = 1 + sum(1 for c in carros if isinstance(c, dict)
                                             and (c.get("vuelta", 0) or 0) > mias)


async def bucle_ws():
    """Conecta al servidor y, si no está o se cae, reintenta con espera creciente (1, 2, 4, 8, 10 s).

    Espera creciente (backoff) y no reintentar a lo loco: si el servidor tarda en arrancar (PyBullet
    cargando la pista) o se reinicia, 3 players reintentando 10 veces por segundo solo lo cargan más.
    El player NUNCA se cae por esto: sigue recibiendo UDP, midiendo y publicando métricas.
    """
    if ws_connect is None:
        log("ERROR: falta la librería websockets; sin conexión al servidor (solo se mide el UDP)")
        return
    espera = ESPERA_MIN_S
    while True:
        try:
            # open_timeout: si la IP no responde (router caído) no se queda colgado el intento.
            # max_size chico: el player solo recibe estado/pong, nunca mensajes grandes.
            async with ws_connect(SERVIDOR_WS, open_timeout=3, close_timeout=1,
                                  max_size=2 ** 20) as ws:
                espera = ESPERA_MIN_S
                await sesion_ws(ws)
            log("el servidor cerró la conexión")
        except (OSError, asyncio.TimeoutError, WebSocketException) as e:
            log(f"sin servidor de pista en {SERVIDOR_WS} ({type(e).__name__}: {e}); "
                f"reintento en {espera:.0f} s")
        except Exception as e:
            # Cualquier otro error de la sesión (p. ej. un mensaje del servidor con un formato que no
            # se esperaba) sale de asyncio.gather y cerraría el contenedor entero. Mejor registrarlo y
            # reconectar: el UDP, el failsafe y las métricas siguen funcionando mientras tanto.
            # (CancelledError no es Exception: el SIGTERM sigue cerrando el programa normalmente.)
            log(f"error inesperado en la sesión con el servidor ({type(e).__name__}: {e}); "
                f"reintento en {espera:.0f} s")
        await asyncio.sleep(espera)
        espera = min(ESPERA_MAX_S, espera * 2)


# ------------------------------------------------------------------------------------------------
# Failsafe y métricas
# ------------------------------------------------------------------------------------------------
async def bucle_failsafe():
    """Cada 100 ms: si hace más de 1 s que no llega CTRL, manda vel 0 (y dir 0) al servidor."""
    seq_fs = 0
    while True:
        await asyncio.sleep(FAILSAFE_PERIODO_S)
        silencio = None if E.t_ultimo_ctrl is None else time.monotonic() - E.t_ultimo_ctrl
        if silencio is not None and silencio <= FAILSAFE_S:
            continue
        if not E.failsafe:
            E.failsafe = True
            E.failsafes += 1
            log("failsafe: sin CTRL del ESP32 hace más de 1 s -> vel 0")
        # seq negativo a propósito: así en el servidor se distingue un "vel 0" del failsafe de un
        # control real del ESP32 (que siempre trae seq >= 0).
        seq_fs -= 1
        encolar({"tipo": "control", "jugador": JUGADOR, "dir": 0, "vel": 0, "boton": 0,
                 "seq": seq_fs, "t_ms": lab.ms(), "failsafe": True})


def metricas_actuales():
    c = E.contador
    carro = E.carro or {}

    def r(v, n=1):
        return None if v is None else round(float(v), n)

    return {
        "recibidos": c.recibidos,
        "perdidos": c.perdidos,
        "perdida_pct": round(c.perdida_pct(), 2),
        "desordenados": c.desordenados,
        "jitter_esp32_ms": round(E.jitter.j, 2),
        "rtt_servidor_ms": E.rtt_ms,
        "rtt_servidor_prom_ms": r(E.rtt_prom_ms),
        "rtt_servidor_jitter_ms": round(E.rtt_jitter_ms, 2),
        "esp32_ip": None if E.esp32 is None else f"{E.esp32[0]}:{E.esp32[1]}",
        "ajenos": E.ajenos,
        "basura": E.basura,
        "ws_conectado": E.ws is not None,
        "ws_conexiones": E.ws_conexiones,
        "controles_enviados": E.controles_enviados,
        "failsafe": E.failsafe,
        "failsafes": E.failsafes,
        "dir": None if E.ultimo_ctrl is None else E.ultimo_ctrl.dir,
        "vel": None if E.ultimo_ctrl is None else E.ultimo_ctrl.vel,
        "carro_x": r(carro.get("x"), 2),
        "carro_y": r(carro.get("y"), 2),
        "carro_vuelta": carro.get("vuelta"),
        "carro_puesto": carro.get("posicion", carro.get("puesto", carro.get("puesto_estimado"))),
        "nombre": NOMBRE,
        "estilo": ESTILO,
    }


async def bucle_metricas(mq):
    t0 = time.monotonic()
    avisado = False
    while True:
        await asyncio.sleep(METRICAS_PERIODO_S)
        m = metricas_actuales()
        mq.publicar(m)
        ws = "conectado" if m["ws_conectado"] else "SIN servidor"
        rtt = "-" if m["rtt_servidor_ms"] is None else f"{m['rtt_servidor_ms']} ms"
        carro = ("-" if m["carro_x"] is None else
                 f"({m['carro_x']}, {m['carro_y']}) vuelta {m['carro_vuelta']}")
        fs = " FAILSAFE" if m["failsafe"] else ""
        log(f"rx {m['recibidos']} perd {m['perdidos']} ({m['perdida_pct']:.1f} %) "
            f"jitter {m['jitter_esp32_ms']:.1f} ms | ws {ws} rtt {rtt} | esp32 {m['esp32_ip'] or '-'} "
            f"| carro {carro}{fs} | última: {E.ultima_linea or '(nada)'}")
        # Ayuda para el caso más común con hardware: "no llega nada". Igual que en el tema 10.
        silencio = time.monotonic() - (E.t_ultimo_udp or t0)
        if silencio >= AVISO_SILENCIO_S and not avisado:
            avisado = True
            log(f"AVISO: no llega nada por UDP {PUERTO_UDP} hace {silencio:.0f} s. Revisa: (1) que la "
                f"ESP32 tenga en config.h la IP del PC y el puerto {5000 + JUGADOR} (publicado -> "
                f"{PUERTO_UDP} de este contenedor), (2) que el firewall de Windows deje entrar UDP "
                f"{5000 + JUGADOR}, (3) que el PC y la ESP32 estén en la misma red WiFi, (4) el monitor "
                f"serie de la ESP32 (debe imprimir ENVIADO,CTRL,{JUGADOR},...).")
        elif silencio < AVISO_SILENCIO_S:
            avisado = False


# ------------------------------------------------------------------------------------------------
async def principal():
    lab.configurar_rutas()  # VLAN 1 -> VLAN 3 (admin) por el router; en Windows no hace nada
    lab.Latido(SERVICIO).iniciar()
    mq = lab.Metricas(SERVICIO)
    loop = asyncio.get_running_loop()
    # "docker stop" manda SIGTERM: se cancela la tarea principal para que corra el finally de abajo
    # y se publique lab/vivo/player-N = 0 a mano (despedida limpia, sin esperar al testamento LWT).
    # En Windows add_signal_handler no existe (NotImplementedError): ahí basta con Ctrl+C.
    try:
        loop.add_signal_handler(signal.SIGTERM, asyncio.current_task().cancel)
    except (NotImplementedError, AttributeError, RuntimeError):
        pass
    await loop.create_datagram_endpoint(ReceptorUDP, local_addr=("0.0.0.0", PUERTO_UDP))
    log(f"escuchando CTRL del ESP32 en UDP {PUERTO_UDP}; servidor {SERVIDOR_WS}; "
        f"{NOMBRE} color {COLOR} estilo {ESTILO}")
    try:
        await asyncio.gather(bucle_ws(), bucle_failsafe(), bucle_metricas(mq))
    except asyncio.CancelledError:
        log("detenido (SIGTERM)")
    finally:
        mq.cerrar()


if __name__ == "__main__":
    try:
        asyncio.run(principal())
    except KeyboardInterrupt:
        log("detenido")
