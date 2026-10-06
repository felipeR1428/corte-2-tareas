"""
render_pista.py - Proceso "camarógrafo": dibuja la pista vista desde arriba, el panel de posiciones
y una cámara de persecución; guarda el último cuadro (para la página HTTP) y, si se pide, el video.

¿Por qué un PROCESO aparte y no un hilo del servidor?
  getCameraImage con el renderizador por software (ER_TINY_RENDERER, el único que funciona sin
  pantalla ni GPU, igual que en el tema 10) tarda decenas de milisegundos por cuadro. Si se hiciera
  en el mismo proceso que la física, durante ese tiempo no se podría llamar a stepSimulation (y
  además Python solo deja correr un hilo a la vez: el GIL), así que la física dejaría de ir a 240 Hz y
  el WebSocket se atrasaría. En otro proceso corre en OTRO núcleo de la CPU, con su propia copia de
  PyBullet: recibe por una cola las poses que calculó la física (posición y orientación de cada
  carro y el ángulo de sus 6 juntas móviles) y coloca unos carros "títere" en esas poses con
  resetBasePositionAndOrientation / resetJointState (sin simular nada) antes de sacar la foto.

Se arranca con multiprocessing en modo "spawn" (un intérprete nuevo, no una copia con fork del
proceso de la física) porque PyBullet guarda estado global en C y un fork lo duplicaría a medias.
"""

from __future__ import annotations

import collections
import json
import math
import os
import queue
import time

import numpy as np
import pybullet as p
from PIL import Image, ImageDraw, ImageFont

import pista as P

ANCHO_3D, ALTO_3D = 1024, 576      # vista cenital 16:9
ANCHO_PANEL = 320                  # panel de texto a la derecha -> cuadro de 1344 x 576 (múltiplos de 16)
INSET = (320, 180)                 # cámara de persecución dentro del panel
# Los adornos de los estilos que no se usan se guardan bajo el piso y FUERA del encuadre: debajo del
# piso no se ven, pero si quedaran en el centro el renderizador igual los rasterizaría (y luego los
# descartaría por profundidad) en cada recorte que pase por ahí.
ESCONDIDO = (60.0, 60.0, -5.0)


def fuente(tam: int, estilo: str = ""):
    """DejaVuSans (en Docker la instala fonts-dejavu-core). Si no está, la de Pillow."""
    nombre = {"": "DejaVuSans.ttf", "negrita": "DejaVuSans-Bold.ttf", "mono": "DejaVuSansMono.ttf"}[estilo]
    for r in (nombre, os.path.join("/usr/share/fonts/truetype/dejavu", nombre),
              os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts", nombre)):
        try:
            return ImageFont.truetype(r, tam)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=tam)
    except TypeError:
        return ImageFont.load_default()


class Camara:
    """Dos "mundos" de PyBullet (dos conexiones DIRECT independientes) dentro de este proceso:
      - ESCENA (cli 0): pista + carros + adornos. Da el fondo cenital (una sola vez) y la cámara de
        persecución.
      - SOLO CARROS (cli 1): carros + adornos, sin pista. Da los recortes de la vista cenital.
    Por qué dos: el renderizador por software tarda (a) según los píxeles que pinta y (b) según
    cuántos cuerpos y triángulos hay en la escena, en CADA llamada. Ver cenital().
    """

    def __init__(self, ids_carros: list):
        self.escena = p.connect(p.DIRECT)           # la primera conexión es la "por defecto" (0)
        P.construir_pista(visual=True, colision=False)
        self.solo_carros = p.connect(p.DIRECT)
        self.mundos = (self.escena, self.solo_carros)
        self.carros = {m: {} for m in self.mundos}
        self.adornos = {m: {} for m in self.mundos}   # mundo -> id -> {estilo: [(body, relpos, es_color)]}
        self.estilo = {}
        self.color = {}
        for m in self.mundos:
            for i, cid in enumerate(ids_carros):
                # Fuera de cuadro (y = -20) hasta la primera foto: así el fondo sale sin carros.
                self.carros[m][cid] = P.cargar_carro(0, -20 - i, 0, (0.5, 0.5, 0.5), liviano=True, cli=m)
                # Se crean UNA vez las piezas de los 4 estilos para cada carro y se esconden las que
                # no se usan. Crear y borrar cuerpos en cada cambio es lento y PyBullet reutiliza los
                # ids de los cuerpos borrados (ver las lecciones de PyBullet del repo).
                self.adornos[m][cid] = {}
        for cid in ids_carros:
            self.estilo[cid] = None
            self.color[cid] = None

        # Cámara cenital casi ortogonal (como en el tema 10): muy alta y con un campo de visión
        # chico, así los muros no se ven "inclinados hacia afuera" en los bordes de la imagen.
        alto_cam = 40.0
        visto_alto = P.ALTO_VISTA                  # metros que entran de arriba a abajo (la pista mide ~9,6)
        fov = math.degrees(2 * math.atan(visto_alto / 2 / alto_cam))
        self.vista = p.computeViewMatrix([*P.CENTRO_CAMARA, alto_cam], [*P.CENTRO_CAMARA, 0], [0, 1, 0])
        self.cerca, self.lejos = alto_cam - 4, alto_cam + 3
        self.proy = p.computeProjectionMatrixFOV(fov, ANCHO_3D / ALTO_3D, self.cerca, self.lejos)
        # Medio alto y medio ancho del "frustum" (la pirámide que ve la cámara) en el plano cercano:
        # sirven para armar sub-frustums que ven solo un recorte de la imagen (ver cenital()).
        self.hh = self.cerca * math.tan(math.radians(fov) / 2)
        self.hw = self.hh * ANCHO_3D / ALTO_3D
        # Matriz proyección*vista (las de PyBullet vienen en orden de columnas: se transponen) para
        # pasar un punto 3D a píxel y escribir con Pillow la etiqueta de cada carro encima.
        self._pv = np.array(self.proy).reshape(4, 4).T @ np.array(self.vista).reshape(4, 4).T
        self.proy_inset = p.computeProjectionMatrixFOV(55, INSET[0] / INSET[1], 0.05, 30)
        # FONDO: la pista sola, renderizada UNA vez al arrancar.
        self.fondo = self._foto(ANCHO_3D, ALTO_3D, self.vista, self.proy, self.escena)[0].copy()
        self.f_titulo = fuente(17, "negrita")
        self.f = fuente(13)
        self.f_mono = fuente(13, "mono")
        self.f_chica = fuente(11)
        self.f_etiqueta = fuente(12, "negrita")

    # ------------------------------------------------------------------ colocar los títeres
    def _poner_estilo(self, m, cid, estilo, color, cambio_estilo, cambio_color):
        if estilo not in self.adornos[m][cid]:
            grupos = {}
            for medias,rel,col in P.ADORNOS[estilo]:
                grupos.setdefault(col,[]).append((medias,rel))
            lista=[]
            for col,piezas in grupos.items():
                rgba=(*color,1) if col=="carro" else col
                vis=p.createVisualShapeArray(shapeTypes=[p.GEOM_BOX]*len(piezas),
                    halfExtents=[e[0] for e in piezas],visualFramePositions=[e[1] for e in piezas],
                    rgbaColors=[rgba]*len(piezas),physicsClientId=m)
                body=p.createMultiBody(0,-1,vis,ESCONDIDO,physicsClientId=m)
                lista.append((body,(0,0,0),col=="carro"))
            self.adornos[m][cid][estilo]=lista
        if cambio_estilo:
            for est, piezas in self.adornos[m][cid].items():
                if est != estilo:
                    for b, _, _ in piezas:
                        p.resetBasePositionAndOrientation(b, ESCONDIDO, (0, 0, 0, 1), physicsClientId=m)
        if cambio_color:
            rgba = [*color, 1]
            p.changeVisualShape(self.carros[m][cid], 0, rgbaColor=rgba, physicsClientId=m)
            for b, _, es_color in self.adornos[m][cid][estilo]:
                if es_color:
                    p.changeVisualShape(b, -1, rgbaColor=rgba, physicsClientId=m)

    def colocar(self, carros: list):
        for c in carros:
            cid = c["id"]
            estilo = c["estilo"] if c["estilo"] in P.ADORNOS else "clasico"
            cambio_estilo = self.estilo[cid] != estilo
            cambio_color = cambio_estilo or self.color[cid] != tuple(c["color"])
            # El chasis está 5 cm por encima de la base (junta fija base_link_joint): los adornos se
            # ubican relativos al chasis componiendo las dos transformaciones.
            for m in self.mundos:
                self._poner_estilo(m,cid,estilo,c["color"],cambio_estilo,cambio_color)
            pos_ch, orn_ch = p.multiplyTransforms(c["pos"], c["orn"], [0, 0, 0.05], [0, 0, 0, 1])
            poses = [p.multiplyTransforms(pos_ch, orn_ch, rel, [0, 0, 0, 1])
                     for _, rel, _ in self.adornos[self.escena][cid][estilo]]
            for m in self.mundos:
                body = self.carros[m][cid]
                p.resetBasePositionAndOrientation(body, c["pos"], c["orn"], physicsClientId=m)
                for j, ang in zip(P.JUNTAS_MOVILES, c["juntas"]):
                    p.resetJointState(body, j, ang, physicsClientId=m)
                for (b, _, _), (pos, orn) in zip(self.adornos[m][cid][estilo], poses):
                    p.resetBasePositionAndOrientation(b, pos, orn, physicsClientId=m)
            self.estilo[cid] = estilo
            self.color[cid] = tuple(c["color"])

    # ------------------------------------------------------------------ imágenes
    def a_pixel(self, x, y, z=0.0):
        c = self._pv @ np.array([x, y, z, 1.0])
        return ((c[0] / c[3] + 1) / 2 * ANCHO_3D, (1 - c[1] / c[3]) / 2 * ALTO_3D)

    @staticmethod
    def _foto(w, h, vista, proy, mundo):
        """(imagen RGB como arreglo h x w x 3, máscara de segmentación h x w). La máscara dice, píxel
        por píxel, qué cuerpo se ve ahí (-1 = nada: el vacío del mundo)."""
        _, _, rgba, _, seg = p.getCameraImage(w, h, vista, proy, renderer=p.ER_TINY_RENDERER,
                                              lightDirection=[0.3, -0.5, 1.0], shadow=0,
                                              physicsClientId=mundo)
        rgb = np.reshape(np.asarray(rgba, dtype=np.uint8), (h, w, 4))[:, :, :3]
        return rgb, np.reshape(np.asarray(seg), (h, w))

    def cenital(self, carros: list) -> Image.Image:
        """Vista cenital = fondo fijo + los carros, renderizados en recortes y pegados encima.

        El renderizador por software es lento: la vista completa de 1024 x 576 tardaba ~250 ms en
        Docker, demasiado para un video de 15 cuadros/s. Medido: el costo es (a) ~0,4 microsegundos
        por píxel pintado y (b) un costo fijo por llamada que crece con los cuerpos y triángulos de
        la escena (~3 ms solo por los ~290 tramos de la pista y ~2 ms por cada carro con sus mallas
        originales). Como la pista NO se mueve:
          1. el fondo (la pista sola) se renderizó una vez al arrancar;
          2. en cada cuadro, por cada carro, se renderiza un recorte de 56 x 56 px con la MISMA
             cámara pero un frustum descentrado (computeProjectionMatrix con izquierda/derecha/
             abajo/arriba de ese trozo del plano cercano): los píxeles caen exactamente donde caerían
             en la imagen completa (comprobado: diferencia 0 entre las dos formas);
          3. ese recorte se renderiza en el mundo SOLO CARROS (sin los ~290 cuerpos de la pista) y,
             con la máscara de segmentación, se pegan sobre el fondo solo los píxeles donde hay carro.
        Las sombras están apagadas: un carro no oscurece la pista, así que no hace falta re-renderizarla.
        """
        img = self.fondo.copy()
        lado = 56                               # px: el carro mide ~25 px de largo, más el alerón
        for c in carros:
            u, v = self.a_pixel(c["pos"][0], c["pos"][1], 0.05)
            u0 = int(max(0, min(ANCHO_3D - lado, round(u) - lado // 2)))
            v0 = int(max(0, min(ALTO_3D - lado, round(v) - lado // 2)))
            u1, v1 = u0 + lado, v0 + lado
            izq = -self.hw + 2 * self.hw * u0 / ANCHO_3D
            der = -self.hw + 2 * self.hw * u1 / ANCHO_3D
            arriba = self.hh - 2 * self.hh * v0 / ALTO_3D
            abajo = self.hh - 2 * self.hh * v1 / ALTO_3D
            proy = p.computeProjectionMatrix(izq, der, abajo, arriba, self.cerca, self.lejos)
            rgb, seg = self._foto(lado, lado, self.vista, proy, self.solo_carros)
            hay = seg >= 0
            img[v0:v1, u0:u1][hay] = rgb[hay]
        return Image.fromarray(img)

    def persecucion(self, c) -> Image.Image:
        """Cámara detrás y arriba del carro c, mirando hacia donde va (se ven alerón, faros...)."""
        x, y, z = c["pos"]
        yaw = c["yaw"]
        fx, fy = math.cos(yaw), math.sin(yaw)
        vista = p.computeViewMatrix([x - .75 * fx - .42 * fy, y - .75 * fy + .42 * fx, .48], [x + .18 * fx, y + .18 * fy, .10],
                                    [0, 0, 1])
        rgb, seg = self._foto(640, 360, vista, self.proy_inset, self.escena)
        rgb = rgb.copy()
        rgb[seg < 0] = (150, 190, 235)          # donde no hay ningún cuerpo: cielo celeste, no blanco
        return Image.fromarray(rgb)


def rgb255(color):
    return tuple(int(255 * max(0.0, min(1.0, v))) for v in color[:3])


class Compositor:
    FONDO = (16, 44, 48)
    TEXTO = (232, 233, 238)
    TENUE = (150, 155, 168)
    VERDE = (110, 210, 120)
    ROJO = (255, 95, 85)

    def __init__(self, cam: Camara):
        self.cam = cam

    def cuadro(self, foto: dict) -> Image.Image:
        cam = self.cam
        img = Image.new("RGB", (ANCHO_3D + ANCHO_PANEL, ALTO_3D), self.FONDO)
        img.paste(cam.cenital(foto["carros"]), (0, 0))
        d = ImageDraw.Draw(img)

        # Etiquetas sobre cada carro en la vista cenital (P1, A2...): getCameraImage no dibuja
        # textos de depuración de PyBullet, así que se escriben con Pillow en el píxel del carro.
        for c in foto["carros"]:
            u, v = cam.a_pixel(c["pos"][0], c["pos"][1], 0.1)
            corto = ("P" if c["id"].startswith("player") else "A") + c["id"][-1]
            col = rgb255(c["color"])
            w = d.textlength(corto, font=cam.f_etiqueta)
            caja = (u - w / 2 - 3, v - 30, u + w / 2 + 3, v - 15)
            d.rectangle(caja, fill=(15, 15, 18), outline=col)
            d.text((u - w / 2, v - 30), corto, fill=(255, 255, 255), font=cam.f_etiqueta)

        # Rótulo arriba a la izquierda de la vista.
        d.rectangle((8, 8, 330, 52), fill=(15, 15, 18))
        d.text((16, 12), "NEXO GP / recta, eses y horquilla", fill=self.TEXTO, font=cam.f)
        d.text((16, 31), f"t = {foto['t']:6.1f} s   fisica {foto['fps']:5.1f} Hz   "
                         f"clientes {foto['clientes']}", fill=self.TENUE, font=cam.f_chica)

        # ---------- panel derecho: tabla de posiciones
        x0 = ANCHO_3D + 12
        d.text((x0, 10), "Posiciones", fill=self.TEXTO, font=cam.f_titulo)
        d.text((x0, 36), "#  carro       vta  mejor   vel", fill=self.TENUE, font=cam.f_mono)
        y = 56
        for c in sorted(foto["carros"], key=lambda c: c["posicion"]):
            d.rectangle((x0, y + 2, x0 + 10, y + 13), fill=rgb255(c["color"]))
            nombre = c["id"]
            texto = f"{c['posicion']}  {nombre:<9} {c['vuelta']:>3}  " \
                    f"{('%5.1f' % c['mejor']) if c['mejor'] else '  -- '} {abs(c['vel']):4.1f}"
            d.text((x0 + 14, y), texto, fill=self.TEXTO, font=cam.f_mono)
            estado = []
            if c["id"].startswith("player"):
                estado.append(c["estilo"])
                estado.append("conectado" if c["conectado"] else "sin conexión")
                if c["failsafe"]:
                    estado.append("FRENADO (failsafe)")
            else:
                estado.append("autónomo")
            color_est = self.ROJO if c.get("failsafe") and c["id"].startswith("player") else self.TENUE
            d.text((x0 + 26, y + 16), " · ".join(estado), fill=color_est, font=cam.f_chica)
            y += 38
        d.text((x0, y + 4), "vta = vueltas completas", fill=self.TENUE, font=cam.f_chica)
        d.text((x0, y + 18), "mejor = mejor vuelta (s), vel en m/s", fill=self.TENUE, font=cam.f_chica)

        # ---------- cámara de persecución: rota entre los 6 carros cada 4 s
        orden = sorted(foto["carros"], key=lambda c: c["id"])
        c = orden[int(foto["t"] / 4.0) % len(orden)]
        yi = ALTO_3D - INSET[1]
        self.detalle = cam.persecucion(c)
        det = ImageDraw.Draw(self.detalle)
        det.rectangle((0,0,640,32),fill=self.FONDO)
        det.text((12,8),f"NEXO GT  |  {c['id']}  |  {abs(c['vel']):.1f} m/s",fill=self.TEXTO,font=cam.f)
        img.paste(self.detalle.resize(INSET), (ANCHO_3D, yi))
        d.rectangle((ANCHO_3D, yi - 20, ANCHO_3D + ANCHO_PANEL, yi), fill=(15, 15, 18))
        d.text((x0, yi - 18), f"cámara: {c['id']} ({c['estilo']})", fill=self.TEXTO, font=cam.f_chica)
        return img


def correr(cola, ids_carros: list, carpeta: str, ruta_cuadro: str, fps_video: int) -> None:
    """Bucle del proceso camarógrafo. Cada elemento de la cola es una "foto" de la física:
        {"t", "fps", "clientes", "carros": [...], "grabar": bool, "fin": bool}
    None = terminar. Las fotos con grabar=True van al video (en orden y sin descartar ninguna, para
    que el video dure exactamente lo grabado); las demás solo actualizan el cuadro en vivo."""
    import imageio.v2 as imageio

    cam = Camara(ids_carros)
    comp = Compositor(cam)
    video = None
    ruta_mp4 = os.path.join(carpeta, "pista.mp4")
    cuadros_video = 0
    # Tiempos de render: los últimos 100 (para el promedio que se imprime) más una suma y una cuenta
    # (para el promedio del video). Una lista con TODOS crecía sin límite: 15 cuadros/s son ~1,3
    # millones de números por día en un contenedor que corre horas.
    t_render = collections.deque(maxlen=100)
    suma_render, n_render = 0.0, 0
    ultimo = None
    while True:
        try:
            foto = cola.get(timeout=1.0)
        except queue.Empty:
            continue
        if foto is None:
            break
        t0 = time.perf_counter()
        cam.colocar(foto["carros"])
        img = comp.cuadro(foto)
        t_render.append(time.perf_counter() - t0)
        suma_render += t_render[-1]
        n_render += 1
        ultimo = img
        # Cuadro en vivo para la página HTTP: se escribe a un archivo temporal y se renombra con
        # os.replace, que es atómico: el servidor HTTP nunca lee un JPEG a medio escribir.
        tmp = ruta_cuadro + ".tmp"
        img.save(tmp, "JPEG", quality=85)
        os.replace(tmp, ruta_cuadro)
        detalle = ruta_cuadro.replace('.jpg','_detalle.jpg')
        comp.detalle.save(detalle+'.tmp','JPEG',quality=91)
        os.replace(detalle+'.tmp',detalle)

        if foto.get("grabar"):
            if video is None:
                os.makedirs(carpeta, exist_ok=True)
                # H.264 + yuv420p: lo que abren GitHub, Windows y los celulares (igual que el tema 10).
                video = imageio.get_writer(ruta_mp4, fps=fps_video, codec="libx264",
                                           pixelformat="yuv420p", quality=7, macro_block_size=16)
                print(f"[camara] grabando {ruta_mp4} a {fps_video} cuadros/s", flush=True)
            video.append_data(np.asarray(img))
            cuadros_video += 1
        if foto.get("fin") and video is not None:
            video.close()
            video = None
            img.save(os.path.join(carpeta, "pista_final.png"))
            with open(os.path.join(carpeta, "pista_resumen.json"), "w", encoding="utf-8") as f:
                json.dump({"t": foto["t"], "cuadros_video": cuadros_video, "fps_video": fps_video,
                           "render_ms_medio": round(1000 * suma_render / n_render, 1),
                           "carros": [{k: c[k] for k in ("id", "vuelta", "mejor", "posicion", "reapariciones")}
                                      for c in foto["carros"]]}, f, indent=2, ensure_ascii=False)
            print(f"[camara] video listo: {cuadros_video} cuadros "
                  f"(render medio {1000 * suma_render / n_render:.0f} ms)", flush=True)
        if n_render % 100 == 0:
            print(f"[camara] render medio {1000 * np.mean(t_render):.0f} ms/cuadro, "
                  f"cola {cola.qsize() if hasattr(cola, 'qsize') else '?'}", flush=True)
    if video is not None:     # se cortó antes de terminar la grabación: se cierra lo que haya
        video.close()
        if ultimo is not None:
            ultimo.save(os.path.join(carpeta, "pista_final.png"))
        print(f"[camara] video cerrado antes de tiempo: {cuadros_video} cuadros", flush=True)
