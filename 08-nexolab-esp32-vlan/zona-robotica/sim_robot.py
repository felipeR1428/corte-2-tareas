"""sim_robot.py - Contenedor de la Zona Robótica (VLAN 2): un robot en PyBullet que copia al ESP32.

Una sola imagen para los tres robots; cuál se simula lo dice la variable de entorno ROBOT:
    ROBOT=spot     Rex de rex-gym (el "pequeño Spot", SpotMicro)
    ROBOT=pepper   Pepper de SoftBank (modelo de qiBullet, el que usa humanoid-gym)
    ROBOT=nao      NAO de SoftBank (ídem)

Paradigma real-to-sim: lo "real" son los potenciómetros/joystick conectados al ESP32 maestro de este
robot. El ESP32 los lee, los pasa a grados y manda 20 veces por segundo un datagrama UDP
    JOINTS,<robot>,<seq>,<t_ms>,<j1>,<j2>,<j3>,<boton>
al puerto 5100 de este contenedor. La simulación convierte cada j en el objetivo de una articulación
(o de la pose del cuerpo, en el Spot) y lo sigue con control de posición; ver robots.py para el mapeo.

Cómo está repartido el trabajo (y por qué):
  - Hilo "udp": espera datagramas con recvfrom() y anota la HORA DE LLEGADA en el mismo instante en que
    el sistema operativo se lo entrega. Así el jitter (RFC 3550) mide la red y no lo ocupado que
    estaba el bucle de física. Solo deja los mensajes en una cola; no toca PyBullet.
  - Proceso principal: la física a 240 Hz en tiempo real (cuántos pasos tocan según el reloj de pared),
    la máquina de estados (reposo / siguiendo / gesto / trotando) y las métricas MQTT cada 2 s.
  - Proceso "render" (otro núcleo de CPU): tiene una COPIA VISUAL del robot, sin física. Diez veces por
    segundo recibe la pose de la base y los ángulos de todas las articulaciones, los copia, saca la
    imagen con ER_TINY_RENDERER (por software: en Docker no hay GPU ni pantalla), le pega el panel de
    texto y la sirve por HTTP / la graba. Medido aquí: un cuadro de NAO cuesta ~135 ms; dentro del
    mismo bucle frenaba la física a 100-160 Hz y "atrasaba" la lectura de UDP (jitter falso de 77 ms).
Si no llega JOINTS en 1 s, el robot vuelve suavemente a su pose de reposo (failsafe).

Variables de entorno:
    ROBOT          spot | pepper | nao (obligatoria)
    PUERTO_UDP     5100      PUERTO_HTTP 8000
    CUADROS_FPS    10        cuadros por segundo del visor y del video
    GRABAR_S       0         si > 0, graba los primeros N segundos a /app/resultados/robot_<robot>.mp4
    RESULTADOS     /app/resultados
    MODELOS        /modelos  dónde quedaron los modelos al construir la imagen
    (más las de comun/lab.py: ROUTER_IP, RUTAS, ADMIN_IP...)
"""

import collections
import json
import math
import multiprocessing as mp
import os
import queue
import signal
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO

import numpy as np
import pybullet as p
from PIL import Image, ImageDraw, ImageFont

# comun/ queda en /app/comun dentro de la imagen; fuera de Docker está una carpeta más arriba.
AQUI = os.path.dirname(os.path.abspath(__file__))
for _r in (AQUI, os.path.dirname(AQUI)):
    if os.path.isdir(os.path.join(_r, "comun")):
        sys.path.insert(0, _r)
from comun import lab, protocolo  # noqa: E402

import robots  # noqa: E402

ROBOT = os.environ.get("ROBOT", "").strip().lower()
PUERTO_UDP = int(os.environ.get("PUERTO_UDP", "5100"))
PUERTO_HTTP = int(os.environ.get("PUERTO_HTTP", "8000"))
CUADROS_FPS = float(os.environ.get("CUADROS_FPS", "10"))
GRABAR_S = float(os.environ.get("GRABAR_S", "0"))
RESULTADOS = os.environ.get("RESULTADOS", "/app/resultados")

SIN_DATOS_S = 1.0          # sin JOINTS durante esto -> reposo (failsafe del contrato)
PERIODO_METRICAS = 2.0
MAX_PASOS_SEGUIDOS = 48    # si el proceso se atrasó más de 0,2 s, no se intenta recuperar todo de golpe
# Muestras de error por estado que se guardan para el resumen final (solo con GRABAR_S > 0): 5 min de
# física a 240 Hz por estado. Sin tope, la lista crecía 240 números por segundo para siempre (cientos de
# MB por día en un contenedor que corre horas) aunque el resumen solo se escribe al grabar.
MAX_MUESTRAS_HIST = 240 * 300
# Datagramas en espera entre el hilo de red y la física. Normalmente hay 0-1 (la física los vacía 240
# veces por segundo); el tope solo importa si alguien inunda el puerto publicado: se descartan los más
# viejos (el contador de seq los verá como perdidos) en vez de llenar la memoria.
MAX_BUZON = 1000
ANCHO_3D, ALTO = 640, 480  # vista 3D; el panel de texto va a la derecha
ANCHO_PANEL = 320          # 640 + 320 = 960: múltiplo de 16, lo que pide el códec H.264


# =====================================================================================================
# Proceso de render: copia visual + panel + visor HTTP + video
# =====================================================================================================
PAGINA = """<!doctype html><html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>NexoLab · sim-__ROBOT__ · Zona Robótica (VLAN 2)</title>
<style>body{margin:0;background:#16181e;color:#e8e9ee;font-family:system-ui,sans-serif}
main{max-width:1000px;margin:auto;padding:12px 16px}h1{font-size:1.25rem;margin:6px 0 2px}
p{margin:4px 0;color:#b9bdc8;font-size:.9rem;line-height:1.5}b{color:#e8e9ee;font-weight:600}
img{width:100%;max-width:960px;height:auto;display:block;margin:10px auto 6px;border:1px solid #333;border-radius:6px}
nav{display:flex;gap:8px;flex-wrap:wrap;margin:8px 0 12px}nav a{color:#e8e9ee;text-decoration:none;font-size:.82rem;
border:1px solid #2c303a;border-radius:8px;padding:5px 10px;background:#1b1e26}nav a:hover{border-color:#4f8fce}
nav a.aqui{border-color:#4f8fce;color:#7fb2e6}details{margin-top:6px;color:#9aa}summary{cursor:pointer;font-size:.85rem}
pre{font-size:12px;color:#9aa;overflow-x:auto}</style></head><body><main>
<nav id="nav"><a data-p="8080">Dashboard del admin</a><a data-p="8010">Pista</a><a data-p="8011">Spot</a>
<a data-p="8012">Pepper</a><a data-p="8013">NAO</a></nav>
<h1>sim-__ROBOT__ · real-to-sim en vivo (Zona Robótica, VLAN 2)</h1>
<p id="intro"></p>
<img id="c" src="cuadro.jpg" alt="último cuadro de la simulación">
<details><summary>Ver el estado crudo (estado.json)</summary><pre id="e"></pre></details></main>
<script>
const ROBOT = '__ROBOT__';
const PUERTOS = {spot: '8011', pepper: '8012', nao: '8013'};
// Enlaces a los otros visores: mismo host con el que se abrió esta página, otro puerto publicado por Docker.
document.querySelectorAll('#nav a').forEach(a => {
  a.href = location.protocol + '//' + (location.hostname || 'localhost') + ':' + a.dataset.p + '/';
  if (a.dataset.p === PUERTOS[ROBOT]) a.className = 'aqui';
});
const QUE = ROBOT === 'spot'
  ? '<b>Rex</b> (un "pequeño Spot" de rex-gym). Los tres ángulos mueven la <b>pose del cuerpo</b> con cinemática inversa: j1 = altura, j2 = cabeceo, j3 = alabeo; el botón lo pone a <b>trotar</b> o lo detiene'
  : '<b>' + (ROBOT === 'nao' ? 'NAO' : 'Pepper') + '</b> de SoftBank (URDF de qiBullet; sin las mallas se dibuja como esqueleto). j1 = hombro derecho, j2 = codo derecho (el brazo celeste), j3 = giro de la cabeza; el botón hace un <b>saludo</b>';
document.getElementById('intro').innerHTML = 'Simulación en <b>PyBullet</b> (240 Hz) del ' + QUE +
  '. Los ángulos llegan por UDP desde su <b>ESP32 maestra</b> (potenciómetros; aquí, un ESP32 emulado que mueve senos lentos) y el robot los copia en tiempo real. ' +
  'El panel muestra, para cada ángulo, lo pedido (comando), lo que acepta el robot (objetivo) y lo que consiguió (medido); su diferencia es el <b>error real-to-sim</b>. Si deja de llegar el mando durante 1 s, vuelve a reposo.';
// Se pide un cuadro nuevo apenas termina de cargar el anterior (y como mucho ~8 por segundo):
// así no se acumulan pedidos si la red o el contenedor van lentos.
const img = document.getElementById('c');
img.onload = img.onerror = () => setTimeout(() => img.src = 'cuadro.jpg?t=' + Date.now(), 120);
setInterval(() => fetch('estado.json').then(r => r.json())
  .then(d => document.getElementById('e').textContent = JSON.stringify(d, null, 1)).catch(() => {}), 1000);
</script></body></html>"""


class Compartido:
    """Último JPEG y último estado, compartidos con los hilos del servidor HTTP (con un candado)."""

    def __init__(self):
        self.candado = threading.Lock()
        self.jpeg = b""
        self.estado = {}

    def poner(self, jpeg, estado):
        with self.candado:
            self.jpeg, self.estado = jpeg, estado

    def leer(self):
        with self.candado:
            return self.jpeg, dict(self.estado)


def servidor_http(compartido):
    """Visor mínimo: / (página que se refresca sola), /cuadro.jpg (último cuadro) y /estado.json."""

    class Manejador(BaseHTTPRequestHandler):
        def do_GET(self):
            jpeg, estado = compartido.leer()
            ruta = self.path.split("?")[0]
            if ruta == "/cuadro.jpg" and jpeg:
                cuerpo, tipo = jpeg, "image/jpeg"
            elif ruta == "/estado.json":
                cuerpo, tipo = json.dumps(estado).encode(), "application/json"
            elif ruta in ("/", "/index.html"):
                cuerpo, tipo = PAGINA.replace("__ROBOT__", ROBOT).encode(), "text/html; charset=utf-8"
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", tipo)
            self.send_header("Content-Length", str(len(cuerpo)))
            self.send_header("Cache-Control", "no-store")   # que el navegador no guarde cuadros viejos
            self.end_headers()
            self.wfile.write(cuerpo)

        def log_message(self, *args):
            pass   # sin una línea de log por cada cuadro pedido

    srv = ThreadingHTTPServer(("0.0.0.0", PUERTO_HTTP), Manejador)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True, name="http").start()
    return srv


def fuente(tam, negrita=False, mono=False):
    nombre = "DejaVuSansMono.ttf" if mono else ("DejaVuSans-Bold.ttf" if negrita else "DejaVuSans.ttf")
    for r in (os.path.join("/usr/share/fonts/truetype/dejavu", nombre), nombre,
              os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts", nombre)):
        try:
            return ImageFont.truetype(r, tam)
        except OSError:
            continue
    return ImageFont.load_default()


COLOR_ESTADO = {"reposo": (150, 155, 168), "siguiendo": (110, 210, 120), "gesto": (255, 190, 70),
                "trotando": (255, 190, 70)}
BLANCO, TENUE, ROJO = (232, 233, 238), (150, 155, 168), (255, 95, 85)


def renderizar(camara):
    """Vista 3D con la cámara que propone el robot (sigue al Spot cuando trota)."""
    objetivo, dist, yaw, pitch = camara
    vista = p.computeViewMatrixFromYawPitchRoll(objetivo, dist, yaw, pitch, 0, 2)
    proy = p.computeProjectionMatrixFOV(fov=50, aspect=ANCHO_3D / ALTO, nearVal=0.02, farVal=20)
    # shadow=0: la sombra duplica el costo del render por software (284 contra 135 ms en NAO).
    w, h, rgba, _, _ = p.getCameraImage(ANCHO_3D, ALTO, vista, proy, renderer=p.ER_TINY_RENDERER,
                                        lightDirection=[1.0, 1.5, 2.5], shadow=0)
    return Image.fromarray(np.reshape(np.asarray(rgba, dtype=np.uint8), (h, w, 4))[:, :, :3])


def componer(img3d, ejes, est, f):
    """Pega a la derecha un panel con lo que importa para real-to-sim: comando / objetivo / medido."""
    cuadro = Image.new("RGB", (ANCHO_3D + ANCHO_PANEL, ALTO), (16, 44, 48))
    cuadro.paste(img3d, (0, 0))
    d = ImageDraw.Draw(cuadro)
    x, y = ANCHO_3D + 14, 12
    d.text((x, y), f"sim-{ROBOT}", font=f["tit"], fill=BLANCO)
    y += 30
    d.text((x, y), f"estado: {est['estado']}", font=f["txt"], fill=COLOR_ESTADO.get(est["estado"], BLANCO))
    y += 22
    d.text((x, y), f"t = {est['t_s']:.1f} s   física {est['fps_fisica']:.0f} Hz", font=f["chica"], fill=TENUE)
    y += 26
    d.text((x, y), "      comando  objetivo  medido", font=f["mono"], fill=TENUE)
    y += 20
    for k, eje in enumerate(ejes):
        cmd = est["comando"][k]
        cmd_txt = f"{cmd:7.1f}" if cmd is not None else "    ---"
        d.text((x, y), f"{eje['j']}: {cmd_txt} {est['objetivo'][k]:8.1f} {est['medido'][k]:7.1f}",
               font=f["mono"], fill=BLANCO)
        y += 18
        d.text((x + 8, y), f"{eje['nombre']} ({eje['unidad']})", font=f["chica"], fill=TENUE)
        y += 22
    y += 4
    d.text((x, y), f"error real-to-sim: {est['error_deg']:.2f}°", font=f["txt"], fill=BLANCO)
    y += 20
    d.text((x, y), f"  (promedio 2 s: {est['error_2s']:.2f}°)", font=f["chica"], fill=TENUE)
    y += 26
    d.text((x, y), f"JOINTS recibidos: {est['recibidos']}", font=f["txt"], fill=BLANCO)
    y += 20
    d.text((x, y), f"perdidos: {est['perdidos']} ({est['perdida_pct']:.1f} %)", font=f["txt"], fill=BLANCO)
    y += 20
    d.text((x, y), f"jitter (RFC 3550): {est['jitter_ms']:.2f} ms", font=f["txt"], fill=BLANCO)
    y += 20
    edad = est["edad_ms"]
    d.text((x, y), "último: " + ("nunca" if edad is None else f"hace {edad} ms"), font=f["txt"],
           fill=ROJO if edad is None or edad > 1000 else BLANCO)
    if ROBOT == "spot":
        y += 26
        d.text((x, y), f"caídas: {est.get('caidas', 0)}   avance: {est.get('avance_m', 0):.2f} m",
               font=f["txt"], fill=BLANCO)
    d.text((x, ALTO - 22), "botón: " + ("trotar / parar" if ROBOT == "spot" else "saludo"),
           font=f["chica"], fill=TENUE)
    return cuadro


def proceso_render(modelo, ejes, cola):
    """Corre en OTRO proceso (multiprocessing). Recibe (base_pos, base_orn, ángulos, cámara, estado)."""
    signal.signal(signal.SIGINT, signal.SIG_IGN)     # lo cierra el principal (mensaje None), no Ctrl+C
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    p.connect(p.DIRECT)
    cuerpo, movibles = robots.crear_visual(modelo)
    compartido = Compartido()
    servidor_http(compartido)
    f = {"tit": fuente(20, negrita=True), "txt": fuente(14), "mono": fuente(14, mono=True), "chica": fuente(12)}
    video, ruta_mp4, cuadros, t_video = None, os.path.join(RESULTADOS, f"robot_{ROBOT}.mp4"), 0, None
    if GRABAR_S > 0:
        import imageio.v2 as imageio
        os.makedirs(RESULTADOS, exist_ok=True)
        # H.264 + yuv420p: lo que reproducen GitHub, Windows y los celulares (igual que en el tema 10).
        video = imageio.get_writer(ruta_mp4, fps=CUADROS_FPS, codec="libx264", pixelformat="yuv420p",
                                   quality=7, macro_block_size=16)
    img = None
    while True:
        item = cola.get()
        if item is None:
            break
        pos, orn, angulos, camara, est = item
        p.resetBasePositionAndOrientation(cuerpo, pos, orn)
        for i, a in zip(movibles, angulos):
            p.resetJointState(cuerpo, i, a)
        img = componer(renderizar(camara), ejes, est, f)
        buf = BytesIO()
        img.save(buf, "JPEG", quality=85)
        compartido.poner(buf.getvalue(), est)
        if video is not None:
            # El video va en TIEMPO REAL aunque el render no alcance los CUADROS_FPS: si un cuadro
            # tardó el doble, se escribe dos veces (si no, el video se vería acelerado).
            ahora = time.monotonic()
            if t_video is None:
                t_video = ahora
            debidos = int((ahora - t_video) * CUADROS_FPS) + 1 - cuadros
            arr = np.asarray(img)
            for _ in range(max(0, debidos)):
                video.append_data(arr)
                cuadros += 1
            if ahora - t_video >= GRABAR_S:
                video.close()
                video = None
                img.save(os.path.join(RESULTADOS, f"robot_{ROBOT}.png"))
                print(f"[sim-{ROBOT}] video listo: {ruta_mp4} ({cuadros} cuadros, {GRABAR_S:.0f} s)", flush=True)
    if video is not None:
        video.close()
        if img is not None:
            img.save(os.path.join(RESULTADOS, f"robot_{ROBOT}.png"))
        print(f"[sim-{ROBOT}] video cortado al parar: {ruta_mp4} ({cuadros} cuadros)", flush=True)
    p.disconnect()


# =====================================================================================================
# Red: hilo que recibe los JOINTS y marca la hora de llegada
# =====================================================================================================
def hilo_udp(sock, buzon, parar):
    while not parar.is_set():
        try:
            datos, _ = sock.recvfrom(512)
        except socket.timeout:
            continue
        except OSError:
            break
        buzon.append((datos, lab.ms()))   # deque.append es seguro entre hilos


# =====================================================================================================
# Programa principal: física, estados, métricas
# =====================================================================================================
def main():
    if ROBOT not in protocolo.ROBOTS:
        print(f"ROBOT debe ser uno de {protocolo.ROBOTS} (llegó {ROBOT!r})", flush=True)
        return 2
    servicio = f"sim-{ROBOT}"
    lab.configurar_rutas()                 # llegar al admin (VLAN 3) por el router
    latido = lab.Latido(servicio).iniciar()
    metricas = lab.Metricas(servicio)

    p.connect(p.DIRECT)                    # sin ventana: dentro de Docker no hay pantalla
    robot = robots.crear(ROBOT)
    print(f"[{servicio}] robot cargado; ejes: " +
          "; ".join(f"{e['j']}={e['nombre']} {e['rango']}" for e in robot.EJES), flush=True)
    if hasattr(robot, "con_mallas"):
        print(f"[{servicio}] mallas de SoftBank: {'sí' if robot.con_mallas else 'NO (se dibuja como esqueleto)'}", flush=True)

    # Proceso de render: "spawn" arranca un intérprete nuevo (no hereda el cliente de PyBullet de este).
    ctx = mp.get_context("spawn")
    cola = ctx.Queue(maxsize=1)            # tamaño 1: si el render va atrasado, se salta cuadros
    render = ctx.Process(target=proceso_render, args=(robot.modelo, robot.EJES, cola), name="render", daemon=True)
    render.start()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("0.0.0.0", PUERTO_UDP))
    sock.settimeout(0.5)                   # para que el hilo pueda ver "parar" de vez en cuando
    buzon = collections.deque(maxlen=MAX_BUZON)
    parar = threading.Event()
    threading.Thread(target=hilo_udp, args=(sock, buzon, parar), daemon=True, name="udp").start()
    signal.signal(signal.SIGTERM, lambda *_: parar.set())   # docker stop -> cierre limpio del video
    signal.signal(signal.SIGINT, lambda *_: parar.set())

    seq = protocolo.ContadorSecuencia()
    jit = protocolo.Jitter()
    ajenos = basura = recortes = 0
    ultimo_cmd, ultimo_boton, t_ultimo = None, 0, None
    estado = "reposo"
    errores = []                          # error (3 valores) de cada iteración en la ventana de 2 s
    # Error medio por estado, para el resumen final (robot_<robot>.json, solo al grabar).
    hist = collections.defaultdict(lambda: collections.deque(maxlen=MAX_MUESTRAS_HIST))
    pos0 = robot.posicion() if hasattr(robot, "posicion") else None

    t0 = time.monotonic()
    pasos = 0
    pasos_ventana, t_ventana = 0, t0
    fps_fisica, error_2s = 0.0, 0.0
    prox_cuadro, prox_metricas = t0, t0 + PERIODO_METRICAS
    est = {}

    while not parar.is_set():
        ahora = time.monotonic()

        # --- 1. mensajes que dejó el hilo de red -------------------------------------------------------
        while buzon:
            datos, llegada = buzon.popleft()
            m = protocolo.leer(datos)
            if not isinstance(m, protocolo.Joints):
                basura += 1
                continue
            if m.robot != ROBOT:
                ajenos += 1         # un JOINTS para otro robot: se cuenta y se ignora
                continue
            seq.registrar(m.seq)
            jit.registrar(m.t_ms, llegada)
            # Recortes: cuántas veces el ESP32 pidió algo fuera del rango del robot (el sim lo limita).
            for k, eje in enumerate(robot.EJES):
                lo, hi = eje["rango"]
                if not lo - 0.05 <= m.j[k] <= hi + 0.05:
                    recortes += 1
            # Botón: se reacciona al FLANCO de subida (0 -> 1), no a cada datagrama con 1: el ESP32
            # repite el 1 a 20 Hz mientras se mantiene apretado (lección del tema 9).
            if m.boton and not ultimo_boton:
                if ROBOT == "spot":
                    estado = "siguiendo" if estado == "trotando" else "trotando"
                elif estado != "gesto":
                    estado = "gesto"
            ultimo_boton = m.boton
            ultimo_cmd, t_ultimo = m.j, ahora

        # --- 2. máquina de estados -----------------------------------------------------------------
        if t_ultimo is None or ahora - t_ultimo > SIN_DATOS_S:
            estado = "reposo"       # failsafe: sin ESP32 durante 1 s (deja de trotar y corta el gesto)
        elif estado == "reposo":
            estado = "siguiendo"
        if estado == "gesto" and robot.gesto_terminado(pasos * robots.DT):
            estado = "siguiendo"
        cmd = ultimo_cmd if ultimo_cmd is not None else (0.0, 0.0, 0.0)

        # --- 3. física a 240 Hz en tiempo real -------------------------------------------------------
        debidos = int((ahora - t0) / robots.DT) - pasos
        if debidos > MAX_PASOS_SEGUIDOS:
            # Atraso grande (el contenedor se quedó sin CPU): se descarta el atraso en vez de correr
            # la física "en cámara rápida" para alcanzar al reloj.
            t0 += (debidos - MAX_PASOS_SEGUIDOS) * robots.DT
            debidos = MAX_PASOS_SEGUIDOS
        for _ in range(max(0, debidos)):
            robot.paso(pasos * robots.DT, estado, cmd)
            p.stepSimulation()
            pasos += 1
            pasos_ventana += 1
        if debidos > 0:
            # Error real-to-sim: |objetivo - medido| de cada j (grados; en el Spot j1 en "grados de j1").
            e = [abs(a - b) for a, b in zip(robot.objetivo(), robot.medido())]
            errores.append(e)
            if GRABAR_S > 0:
                hist[estado].append(sum(e) / 3)

        # --- 4. cuadro para el proceso de render -----------------------------------------------------
        if ahora >= prox_cuadro:
            prox_cuadro = max(prox_cuadro + 1.0 / CUADROS_FPS, ahora)
            obj, med = robot.objetivo(), robot.medido()
            ult = errores[-1] if errores else [0, 0, 0]
            est = {
                "robot": ROBOT, "estado": estado, "t_s": round(pasos * robots.DT, 2),
                "comando": list(cmd) if ultimo_cmd is not None else [None, None, None],
                "objetivo": [round(v, 2) for v in obj], "medido": [round(v, 2) for v in med],
                "error_deg": sum(ult) / 3, "error_2s": error_2s,
                "recibidos": seq.recibidos, "perdidos": seq.perdidos, "perdida_pct": seq.perdida_pct(),
                "jitter_ms": jit.j, "fps_fisica": fps_fisica,
                "edad_ms": None if t_ultimo is None else int((ahora - t_ultimo) * 1000),
            }
            if ROBOT == "spot":
                pos = robot.posicion()
                est["caidas"] = robot.caidas
                est["avance_m"] = math.hypot(pos[0] - pos0[0], pos[1] - pos0[1])
            pos, orn, angulos = robots.foto(robot.id)
            try:
                cola.put_nowait((pos, orn, angulos, robot.camara(), est))
            except queue.Full:
                pass                # el render todavía no terminó el anterior: este se salta

        # --- 5. métricas MQTT cada 2 s -------------------------------------------------------------
        if ahora >= prox_metricas:
            fps_fisica = pasos_ventana / (ahora - t_ventana)
            pasos_ventana, t_ventana = 0, ahora
            prox_metricas += PERIODO_METRICAS
            if errores:
                error_2s = float(np.mean(errores))
                maximo = float(np.max(errores))
                por_j = [round(float(v), 3) for v in np.mean(errores, axis=0)]
            else:
                error_2s, maximo, por_j = 0.0, 0.0, [0.0, 0.0, 0.0]
            errores = []
            datos = {
                "robot": ROBOT, "estado": estado, "recibidos": seq.recibidos, "perdidos": seq.perdidos,
                "perdida_pct": round(seq.perdida_pct(), 2), "desordenados": seq.desordenados,
                "jitter_ms": round(jit.j, 3), "error_realsim_deg": round(error_2s, 3),
                "error_realsim_deg_max": round(maximo, 3), "error_por_j_deg": por_j,
                "fps_fisica": round(fps_fisica, 1), "recortes": recortes, "ajenos": ajenos,
                "basura": basura, "edad_ultimo_ms": est.get("edad_ms"),
                "comando": est.get("comando"), "medido": est.get("medido"),
            }
            if ROBOT == "spot":
                datos.update({"caidas": robot.caidas, "trotando": estado == "trotando",
                              "avance_m": round(est.get("avance_m", 0.0), 3)})
            metricas.publicar(datos)
            print(f"[{servicio}] {estado:9s} rx={seq.recibidos} perd={seq.perdidos} "
                  f"jit={jit.j:.2f}ms err={error_2s:.2f}° (máx {maximo:.2f}°) física={fps_fisica:.0f}Hz"
                  + (f" caídas={robot.caidas} avance={datos['avance_m']:.2f}m" if ROBOT == "spot" else ""),
                  flush=True)

        # --- 6. dormir hasta el próximo paso de física ---------------------------------------------
        espera = t0 + (pasos + 1) * robots.DT - time.monotonic()
        if espera > 0:
            time.sleep(espera)

    # Cierre limpio: el render cierra el video; aquí, el resumen con el error por estado.
    try:
        cola.put(None, timeout=3)
    except queue.Full:
        try:
            cola.get_nowait()
            cola.put(None, timeout=3)
        except (queue.Empty, queue.Full):
            pass
    render.join(timeout=8)
    if GRABAR_S > 0:
        resumen = {k: {"muestras": len(v), "error_medio_deg": round(float(np.mean(v)), 3),
                       "error_p95_deg": round(float(np.percentile(v, 95)), 3)} for k, v in hist.items() if v}
        resumen.update({"robot": ROBOT, "recibidos": seq.recibidos, "perdidos": seq.perdidos,
                        "jitter_ms": round(jit.j, 3), "recortes": recortes, "fps_fisica": round(fps_fisica, 1),
                        "segundos": round(pasos * robots.DT, 1)})
        if ROBOT == "spot":
            resumen.update({"caidas": robot.caidas, "avance_m": round(est.get("avance_m", 0.0), 3)})
        with open(os.path.join(RESULTADOS, f"robot_{ROBOT}.json"), "w", encoding="utf-8") as f:
            json.dump(resumen, f, indent=2, ensure_ascii=False)
        print(f"[{servicio}] resumen: {json.dumps(resumen, ensure_ascii=False)}", flush=True)
    latido.parar()
    metricas.cerrar()
    p.disconnect()
    return 0


if __name__ == "__main__":
    sys.exit(main())
