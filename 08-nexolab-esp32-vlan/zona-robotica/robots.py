"""Los tres robots de la Zona Robótica y cómo cada uno convierte un mensaje JOINTS en movimiento.

Real-to-sim: el ESP32 lee "articulaciones reales" (potenciómetros / joystick que el usuario mueve con la
mano), las convierte a grados y las manda por UDP (JOINTS,<robot>,<seq>,<t_ms>,<j1>,<j2>,<j3>,<boton>).
Este módulo decide QUÉ articulación del robot simulado sigue cada j y la mueve con un control de
posición (POSITION_CONTROL de PyBullet: un PD interno que lleva la articulación al ángulo pedido con un
torque máximo), siempre dentro de los límites que trae el URDF del robot real.

Cada robot ofrece la misma interfaz, para que sim_robot.py no tenga que saber cuál es:
    robot.EJES            descripción de j1, j2, j3 (nombre, unidad, rango aceptado)
    robot.paso(t, modo, cmd)   se llama en CADA paso de física (240 Hz); modo = "reposo" | "siguiendo"
                               | "gesto" (humanoides: saludo) | "trotando" (Spot); cmd = (j1, j2, j3)
    robot.objetivo()      lo que el robot intenta alcanzar ahora, en las mismas unidades que j1..j3
    robot.medido()        lo que de verdad tiene la simulación (ángulos leídos), mismas unidades
    robot.camara()        hacia dónde mirar para el cuadro (objetivo, distancia, yaw, pitch)
    robot.modelo          (urdf, flags, pos, orn) para que el proceso de render cargue una copia visual

La diferencia objetivo - medido es el "error real-to-sim" que se publica por MQTT: cuánto le cuesta al
gemelo simulado seguir a la articulación real (inercia, torque limitado, gravedad, límites).
"""

import contextlib
import math
import os
import re
import sys
import tempfile

import numpy as np
import pybullet as p
import pybullet_data

MODELOS = os.environ.get("MODELOS", "/modelos")
DT = 1.0 / 240.0          # paso de física (el de PyBullet por defecto)


def _limitar(v, lo, hi):
    return max(lo, min(hi, v))


def _acercar(actual, objetivo, paso_max):
    """Mueve `actual` hacia `objetivo` como mucho `paso_max` (limitador de velocidad / rampa)."""
    d = objetivo - actual
    return objetivo if abs(d) <= paso_max else actual + math.copysign(paso_max, d)


@contextlib.contextmanager
def silencio():
    """Calla el stdout de C mientras dura el bloque.

    Con URDF_MERGE_FIXED_LINKS, PyBullet imprime (desde C, no desde Python) una línea por cada link que
    fusiona: unas 200 líneas por robot que taparían el log del contenedor. Se redirige el descriptor 1
    del proceso a /dev/null y después se devuelve.
    """
    sys.stdout.flush()
    fd = sys.stdout.fileno()
    copia, nulo = os.dup(fd), os.open(os.devnull, os.O_WRONLY)
    os.dup2(nulo, fd)
    try:
        yield
    finally:
        os.dup2(copia, fd)
        os.close(nulo)
        os.close(copia)


def cargar(urdf, pos, orn, fijo, flags):
    with silencio():
        return p.loadURDF(urdf, pos, orn, useFixedBase=fijo, flags=flags)


def _info_articulaciones(cuerpo):
    """{nombre: (indice, inferior, superior, torque_max, velocidad_max)} de las articulaciones móviles."""
    info = {}
    for i in range(p.getNumJoints(cuerpo)):
        j = p.getJointInfo(cuerpo, i)
        if j[2] == p.JOINT_FIXED:
            continue
        info[j[1].decode()] = (i, j[8], j[9], j[10], j[11])
    return info


# =====================================================================================================
# NAO y Pepper (SoftBank Robotics), modelos de qiBullet
# =====================================================================================================
class Humanoide:
    """NAO o Pepper con base fija; j1 = hombro derecho (pitch), j2 = codo derecho (roll), j3 = cabeza (yaw).

    Base fija (useFixedBase=True), y por qué:
      - NAO es un bípedo de 58 cm. Si se suelta, cada vez que el usuario mueve el potenciómetro del
        hombro el brazo desplaza el centro de masa y el robot se tambalea o se cae (no hay un control
        de equilibrio corriendo; el de verdad lo trae el NAOqi del robot físico, que no existe aquí).
        Un humanoide en el piso no sirve para mostrar real-to-sim: el error medido sería el de la caída,
        no el del seguimiento de la articulación. El propio qiBullet hace lo mismo: al cargar a NAO le
        pone una restricción fija al torso ("balance_constraint") y a Pepper otra a la base.
      - Pepper no camina: se mueve con 3 ruedas omnidireccionales y aquí la base no tiene que
        desplazarse (el ESP32 solo manda 3 ángulos), así que también va fija, igual que en qiBullet.
    """

    # Postura "Stand" de qiBullet (robot_posture.py, Apache-2.0): los ángulos de reposo del robot real.
    REPOSO = {
        "nao": {"HeadYaw": 0.0, "HeadPitch": -0.17, "LShoulderPitch": 1.4426, "LShoulderRoll": 0.2245,
                "LElbowYaw": -1.2023, "LElbowRoll": -0.4173, "LWristYaw": 0.1, "LHand": 0.3,
                "LHipYawPitch": -0.17, "LHipRoll": 0.1191, "LHipPitch": 0.1274, "LKneePitch": -0.0923,
                "LAnklePitch": 0.0874, "LAnkleRoll": -0.1108, "RHipYawPitch": -0.17, "RHipRoll": -0.1191,
                "RHipPitch": 0.1274, "RKneePitch": -0.0923, "RAnklePitch": 0.0874, "RAnkleRoll": 0.1108,
                "RShoulderPitch": 1.4426, "RShoulderRoll": -0.2245, "RElbowYaw": 1.2023,
                "RElbowRoll": 0.4173, "RWristYaw": 0.1, "RHand": 0.3},
        "pepper": {"HeadPitch": -0.2117, "HeadYaw": -0.0077, "HipPitch": -0.0261, "HipRoll": -0.0046,
                   "KneePitch": 0.0, "LElbowRoll": -0.5185, "LElbowYaw": -1.2180, "LHand": 0.5896,
                   "LShoulderPitch": 1.58, "LShoulderRoll": 0.1166, "LWristYaw": -0.0307,
                   "RElbowRoll": 0.5185, "RElbowYaw": 1.2257, "RHand": 0.5888, "RShoulderPitch": 1.58,
                   "RShoulderRoll": -0.1150, "RWristYaw": 0.0276},
    }
    # Qué articulación del URDF sigue a cada j del ESP32 (las mismas en los dos robots: SoftBank usa
    # los mismos nombres y convenciones en NAO y Pepper).
    MAPEO = ("RShoulderPitch", "RElbowRoll", "HeadYaw")
    # Altura del origen de la base al cargar: el torso de NAO queda a 0,36 m (como lo pone qiBullet,
    # con los pies tocando el piso en la postura Stand); la base de Pepper ya está a nivel del piso.
    ALTURA = {"nao": 0.36, "pepper": 0.0}

    def __init__(self, nombre):
        self.nombre = nombre
        urdf = os.path.join(MODELOS, "qibullet", f"{nombre}.urdf")
        self.con_mallas = os.path.isdir(os.path.join(MODELOS, "qibullet", "meshes", nombre))
        if not self.con_mallas:
            urdf = self._urdf_sin_mallas(urdf)
        # URDF_USE_MATERIAL_COLORS_FROM_MTL: los .obj de SoftBank traen sus colores en un .mtl (blanco,
        # gris, los LED azules de los ojos); sin esta bandera PyBullet los pinta todos de un color.
        # URDF_MERGE_FIXED_LINKS: los URDF de SoftBank traen decenas de links "fijos" que solo marcan
        # dónde van sensores (cámaras, sonares, FSR, botones táctiles). Fusionarlos con su link padre
        # baja NAO de 78 a 42 cuerpos y Pepper de 98 a 48, y la física cuesta ~la mitad (medido aquí).
        # Sin URDF_USE_SELF_COLLISION: el brazo puede rozar el torso sin trabarse (el seguimiento del
        # potenciómetro importa más aquí que el choque brazo-cuerpo, que el robot real evita por software).
        flags = p.URDF_USE_MATERIAL_COLORS_FROM_MTL | p.URDF_MERGE_FIXED_LINKS
        pos = [0, 0, self.ALTURA[nombre]]
        self.modelo = (urdf, flags, pos, [0, 0, 0, 1])
        self.id = cargar(urdf, pos, [0, 0, 0, 1], True, flags)
        self.art = _info_articulaciones(self.id)
        self.reposo = self.REPOSO[nombre]
        # Se arranca YA en la postura de reposo (resetJointState teletransporta, sin física) para no
        # empezar con los brazos estirados en cero y verlos "caer" a la pose al primer segundo.
        for n, a in self.reposo.items():
            p.resetJointState(self.id, self.art[n][0], a)
        lim = [self.art[n][1:3] for n in self.MAPEO]
        self.EJES = (
            {"j": "j1", "nombre": "hombro der. (RShoulderPitch)", "unidad": "°",
             "rango": tuple(round(math.degrees(x), 1) for x in lim[0])},
            {"j": "j2", "nombre": "codo der. (RElbowRoll)", "unidad": "°",
             "rango": tuple(round(math.degrees(x), 1) for x in lim[1])},
            {"j": "j3", "nombre": "cabeza (HeadYaw)", "unidad": "°",
             "rango": tuple(round(math.degrees(x), 1) for x in lim[2])},
        )
        self._obj = {n: a for n, a in self.reposo.items()}
        self._enviado = {}       # último (objetivo, velocidad) mandado a cada motor
        self._t_gesto = None

    @staticmethod
    def _urdf_sin_mallas(urdf):
        """Sustituto si no se aceptó la licencia de las mallas: el MISMO URDF (cinemática, masas y
        límites reales) con cada <mesh> cambiado por una esfera chica, para la física. Lo que se VE lo
        arma el proceso de render con urdf_esqueleto() (huesos, torso y cabeza legibles)."""
        texto = open(urdf, encoding="utf-8").read()
        texto = re.sub(r"<mesh[^>]*/>", '<sphere radius="0.025"/>', texto)
        f = tempfile.NamedTemporaryFile("w", prefix="sin_mallas_", suffix=".urdf", delete=False, encoding="utf-8")
        f.write(texto)
        f.close()
        print(f"[robot] sin mallas de SoftBank: {os.path.basename(urdf)} (física con esferas, se dibuja como esqueleto)", flush=True)
        return f.name

    # --- mapeo j (grados) <-> articulaciones (radianes) ---------------------------------------------
    def _a_objetivos(self, cmd):
        """(j1, j2, j3) en grados -> {articulación: radianes}, recortado a los límites del URDF."""
        obj = {}
        for n, g in zip(self.MAPEO, cmd):
            _, lo, hi, _, _ = self.art[n]
            obj[n] = _limitar(math.radians(g), lo, hi)
        return obj

    def _saludo(self, t):
        """Gesto de saludo (2,5 s): brazo derecho arriba y el antebrazo yendo y viniendo a 2 Hz.

        Ángulos en la convención de SoftBank: ShoulderPitch negativo = brazo hacia arriba; en el brazo
        derecho ShoulderRoll negativo = hacia afuera y ElbowRoll positivo = doblar el codo.
        """
        osc = math.sin(2 * math.pi * 2.0 * t)
        return {"RShoulderPitch": -1.15, "RShoulderRoll": -0.35 + 0.15 * osc,
                "RElbowYaw": 1.6, "RElbowRoll": 0.75 + 0.45 * osc, "RWristYaw": 0.0, "RHand": 0.95,
                "HeadYaw": -0.35}

    DURACION_GESTO = 2.5

    def paso(self, t, modo, cmd):
        """Fija los objetivos de este paso de física según el modo y los manda a los motores."""
        vel = None   # None = la velocidad máxima del URDF
        obj = dict(self.reposo)
        if modo == "siguiendo":
            obj.update(self._a_objetivos(cmd))
        elif modo == "gesto":
            if self._t_gesto is None:
                self._t_gesto = t
            obj.update(self._saludo(t - self._t_gesto))
        else:
            # Reposo: se vuelve a la postura Stand "suavemente", limitando la velocidad de cada
            # articulación a 1 rad/s (~57 °/s) en vez de la máxima del motor (hasta 8 rad/s en NAO).
            vel = 1.0
        if modo != "gesto":
            self._t_gesto = None
        self._obj = obj
        # El motor de PyBullet RECUERDA su objetivo entre pasos: solo se le vuelve a mandar a la
        # articulación cuyo objetivo cambió (en reposo o con el potenciómetro quieto, ninguna).
        for n, a in obj.items():
            i, lo, hi, fuerza, vmax = self.art[n]
            orden = (_limitar(a, lo, hi), vel if vel else vmax)
            if self._enviado.get(n) != orden:
                p.setJointMotorControl2(self.id, i, p.POSITION_CONTROL, targetPosition=orden[0],
                                        force=fuerza, maxVelocity=orden[1])
                self._enviado[n] = orden

    def gesto_terminado(self, t):
        return self._t_gesto is not None and t - self._t_gesto >= self.DURACION_GESTO

    def objetivo(self):
        return tuple(math.degrees(self._obj[n]) for n in self.MAPEO)

    def medido(self):
        return tuple(math.degrees(p.getJointState(self.id, self.art[n][0])[0]) for n in self.MAPEO)

    def caido(self):
        return False   # base fija: no se puede caer

    def camara(self):
        # Vista de 3/4 desde el frente-derecha (el brazo que se mueve es el derecho).
        if self.nombre == "nao":
            return [0.0, 0.0, 0.33], 1.05, 40, -10
        return [0.0, 0.0, 0.75], 1.9, 45, -12



# =====================================================================================================
# "Pequeño Spot": Rex de rex-gym (SpotMicro), cuadrúpedo de 12 motores
# =====================================================================================================
class PataRex:
    """Cinemática inversa EXACTA de una pata del Rex, con las medidas tomadas de rex.urdf.

    rex-gym trae su propia IK (rex_gym/model/kinematics.py), pero con medidas que no son las del URDF
    (muslo 0,107 y pierna 0,145 m contra 0,120 y 0,115 m del URDF; caderas a 0,23 m contra 0,186 m):
    probada aquí, al pedir +30 mm de altura el cuerpo subía 55 mm y al pedir 10° de cabeceo daba 12°.
    Como el error real-to-sim se mide justamente contra lo pedido, se rehízo con las medidas reales.

    Marco del ROBOT (no el del URDF): x hacia adelante, y hacia la izquierda, z hacia arriba. El URDF
    del Rex mira hacia -x (su pata "front_left" está en x = -0,093, y = -0,036), por eso el robot se
    carga girado 180° y aquí todo se expresa ya girado; los ejes de los motores del URDF (+x, +y) quedan
    entonces como -x, -y del robot, y por eso al final cada ángulo cambia de signo.

    Cadena de una pata (valores del URDF):
      cadera (abducción, gira sobre x) -> 0,052 m hacia afuera -> muslo (gira sobre y), vector
      (0,01; 0; -0,12) -> rodilla (gira sobre y) -> pierna de 0,115 m hasta el pie.
    """

    OFFSET = 0.052                                 # de la cadera al eje del muslo, hacia afuera
    L1 = math.hypot(0.01, 0.12)                    # muslo (0,1204 m)
    D1 = math.atan2(0.01, 0.12)                    # el muslo en cero apunta 4,8° hacia adelante
    L2 = 0.115                                     # pierna

    def __init__(self, x_cadera, izquierda):
        self.cadera = np.array([x_cadera, 0.036 if izquierda else -0.036, 0.0])
        self.lado = 1.0 if izquierda else -1.0

    def ik(self, pie):
        """Pie (marco del cuerpo, m) -> (hombro, pierna, pie) en radianes del URDF."""
        px, py, pz = pie - self.cadera
        # 1) Abducción a: girar la pata alrededor de x hasta que el pie quede en el plano del muslo,
        #    que está a OFFSET de la cadera: y' = cos(a)·py + sen(a)·pz = d  ->  a = φ + acos(d / r).
        d = self.lado * self.OFFSET
        r = max(math.hypot(py, pz), abs(d) + 1e-6)
        a = math.atan2(pz, py) + math.acos(_limitar(d / r, -1.0, 1.0))
        zp = -math.sin(a) * py + math.cos(a) * pz      # altura del pie en el plano de la pata (< 0)
        # 2) Muslo + pierna en ese plano: brazo de 2 eslabones (ley de cosenos). Ángulos "desde la
        #    vertical hacia abajo, + hacia adelante"; la rodilla dobla hacia atrás (gamma < 0).
        dist2 = px * px + zp * zp
        c = (dist2 - self.L1 ** 2 - self.L2 ** 2) / (2 * self.L1 * self.L2)
        gamma = -math.acos(_limitar(c, -1.0, 1.0))
        psi = math.atan2(px, -zp)
        b1 = psi - math.atan2(self.L2 * math.sin(-gamma), self.L1 + self.L2 * math.cos(gamma))
        b2 = b1 - gamma
        t2 = self.D1 - b1                               # giro del muslo (sobre +y del robot)
        t3 = -t2 - b2                                   # giro de la rodilla
        return -a, -t2, -t3                             # ejes del URDF = -x, -y del robot

    def fk(self, hombro, pierna, pie):
        """Al revés (para sacar dónde queda el pie en la pose "stand" de rex-gym)."""
        a, t2, t3 = -hombro, -pierna, -pie
        b1 = self.D1 - t2
        b2 = -t2 - t3
        x = self.L1 * math.sin(b1) + self.L2 * math.sin(b2)
        zp = -(self.L1 * math.cos(b1) + self.L2 * math.cos(b2))
        d = self.lado * self.OFFSET
        y = math.cos(a) * d - math.sin(a) * zp
        z = math.sin(a) * d + math.cos(a) * zp
        return self.cadera + np.array([x, y, z])


def _rot_xy(ala, cab):
    """Matriz de rotación del cuerpo: alabeo (sobre x) y cabeceo (sobre y), en el marco del robot."""
    ca, sa, cc, sc = math.cos(ala), math.sin(ala), math.cos(cab), math.sin(cab)
    rx = np.array([[1, 0, 0], [0, ca, -sa], [0, sa, ca]])
    ry = np.array([[cc, 0, sc], [0, 1, 0], [-sc, 0, cc]])
    return ry @ rx


class Spot:
    """Rex (SpotMicro). j1 = altura del cuerpo, j2 = cabeceo, j3 = alabeo; botón = trotar / parar.

    Por qué así y no "un potenciómetro = un motor": un cuadrúpedo tiene 12 motores y solo hay 3
    potenciómetros; mover un motor suelto de una pata lo desequilibra. Lo natural (es lo que hace el
    entorno "poses" de rex-gym con sus deslizadores) es que las 3 entradas muevan la POSE DEL CUERPO y la
    cinemática inversa reparta el movimiento entre las 12 articulaciones con los pies quietos en el piso.

    Unidades de j (lo que manda el ESP32, en "grados" como dice el contrato):
      j1: -30..30  ->  altura de las caderas = 190 mm + 1,5 mm por grado, recortada a 160..220 mm
      j2: cabeceo del cuerpo en grados (+ = nariz arriba), recortado a ±20°
      j3: alabeo del cuerpo en grados (+ = se inclina hacia la derecha), recortado a ±20°
    """

    ALTURA_BASE, MM_POR_GRADO = 0.19, 0.0015
    ALT_MIN, ALT_MAX, ANG_MAX = 0.16, 0.22, math.radians(20)
    # Velocidades máximas de la pose del cuerpo (rampa): sin esto un salto del potenciómetro se vuelve
    # un salto de los motores, y un cuadrúpedo con un tirón así patina o se vuelca.
    V_ALTURA, V_ANG = 0.10, math.radians(60)          # m/s y rad/s
    # CPG del trote (ver _pies()); valores encontrados probando en esta misma imagen (robots.md).
    F_TROTE = float(os.environ.get("TROTE_HZ", "3.0"))
    PASO_X = float(os.environ.get("TROTE_PASO", "0.015"))
    ALZA_Z = float(os.environ.get("TROTE_ALZA", "0.015"))
    RAMPA_S = 0.6
    FUERZA = float(os.environ.get("FUERZA_SPOT", "12"))   # N·m por motor
    # Orden de las patas en todo este código: delantera izq., delantera der., trasera izq., trasera der.
    PATAS = ("front_left", "front_right", "rear_left", "rear_right")
    STAND = (0.0, -0.88643435, 1.30197369)   # pose "stand" de rex-gym (rex_constants.py)

    def __init__(self):
        urdf = os.path.join(MODELOS, "rex", "rex.urdf")
        if not os.path.exists(urdf):
            raise FileNotFoundError(f"No está {urdf}: correr descargar_modelos.py (lo hace el Dockerfile)")
        self.nombre = "spot"
        # Girado 180° (el URDF mira hacia -x): así "adelante" es +x del mundo y la cámara lo ve de frente.
        orn = p.getQuaternionFromEuler([0, 0, math.pi])
        self.modelo = (urdf, 0, [0, 0, 0.21], orn)
        self.id = cargar(urdf, [0, 0, 0.21], orn, False, 0)
        self.art = _info_articulaciones(self.id)
        self.motores = []
        for pata in self.PATAS:
            self.motores += [self.art[f"motor_{pata}_shoulder"][0], self.art[f"motor_{pata}_leg"][0],
                             self.art[f"foot_motor_{pata}"][0]]
        # Fricción alta en todo (en la práctica, en los pies) para que el trote empuje y no patine.
        for i in range(-1, p.getNumJoints(self.id)):
            p.changeDynamics(self.id, i, lateralFriction=1.0)
        self.patas = [PataRex(0.093, True), PataRex(0.093, False), PataRex(-0.093, True), PataRex(-0.093, False)]
        # Dónde se apoya cada pie: el de la pose "stand" de rex-gym (4 cm detrás de cada cadera), con
        # el cuerpo a ALTURA_BASE. Son coordenadas del "suelo" bajo el centro del cuerpo.
        self.pies0 = np.array([pt.fk(*self.STAND) for pt in self.patas])
        self.pies0[:, 2] = -self.ALTURA_BASE
        # Pose actual del cuerpo (lo que ya se le pide a la IK, con rampa) y su objetivo.
        self.alt, self.cab, self.ala = self.ALTURA_BASE, 0.0, 0.0
        self._obj = (self.alt, 0.0, 0.0)
        self.amp = 0.0           # 0 = de pie, 1 = trote completo (rampa de RAMPA_S segundos)
        self.fase = 0.0
        self.trotando = False
        self.calib_z = None      # z de la base asentada con ALTURA_BASE (ver medido())
        self.caidas = 0
        self._estaba_caido = False
        for idx, a in zip(self.motores, self._ik_angulos()):
            p.resetJointState(self.id, idx, a)
        self.EJES = (
            {"j": "j1", "nombre": "altura del cuerpo", "unidad": "°",
             "rango": tuple(round((h - self.ALTURA_BASE) / self.MM_POR_GRADO, 1) for h in (self.ALT_MIN, self.ALT_MAX))},
            {"j": "j2", "nombre": "cabeceo (+ nariz arriba)", "unidad": "°", "rango": (-20.0, 20.0)},
            {"j": "j3", "nombre": "alabeo (+ a la derecha)", "unidad": "°", "rango": (-20.0, 20.0)},
        )

    def asentar(self, pasos=480):
        """Deja que el robot se apoye en el piso 2 s ANTES de aceptar órdenes (lección del Laikago:
        si se le pide moverse recién cargado, cae casi siempre). Al final toma la altura de referencia."""
        for _ in range(pasos):
            self._mandar(self._ik_angulos())
            p.stepSimulation()
        self.calib_z = p.getBasePositionAndOrientation(self.id)[0][2]

    def _pies(self):
        """Posición de los 4 pies (marco del suelo) con el CPG del trote aplicado.

        CPG (generador central de patrones): cada pie describe x = A·sen(θ), z = H·max(0, cos θ).
        Mientras cos θ > 0 el pie va hacia adelante EN EL AIRE (vuelo); mientras cos θ < 0 va hacia atrás
        APOYADO (eso empuja el cuerpo hacia adelante). Patas diagonales (DI con TD, DD con TI) con medio
        ciclo de diferencia = trote: siempre hay dos pies en el piso formando una diagonal.
        """
        pies = self.pies0.copy()
        if self.amp > 0:
            for k, desfase in enumerate((0.0, math.pi, math.pi, 0.0)):
                th = self.fase + desfase
                pies[k, 0] += self.amp * self.PASO_X * math.sin(th)
                pies[k, 2] += self.amp * self.ALZA_Z * max(0.0, math.cos(th))
        return pies

    def _ik_angulos(self):
        # Pies fijos en el suelo, cuerpo a altura alt y girado: cada pie visto desde el cuerpo es
        # R^T · (pie_suelo - (0, 0, alt) + (0, 0, ALTURA_BASE)). Cabeceo "nariz arriba" = giro negativo
        # sobre y (la regla de la mano derecha baja la nariz con un giro positivo).
        rot = _rot_xy(self.ala, -self.cab)
        desplazo = np.array([0.0, 0.0, self.alt - self.ALTURA_BASE])
        angulos = []
        for pata, pie in zip(self.patas, self._pies()):
            angulos += pata.ik(rot.T @ (pie - desplazo))
        return angulos

    def _mandar(self, angulos):
        p.setJointMotorControlArray(self.id, self.motores, p.POSITION_CONTROL, targetPositions=angulos,
                                    forces=[self.FUERZA] * len(angulos))

    def paso(self, t, modo, cmd):
        alt = _limitar(self.ALTURA_BASE + cmd[0] * self.MM_POR_GRADO, self.ALT_MIN, self.ALT_MAX)
        cab = _limitar(math.radians(cmd[1]), -self.ANG_MAX, self.ANG_MAX)
        ala = _limitar(math.radians(cmd[2]), -self.ANG_MAX, self.ANG_MAX)
        if modo == "reposo":
            alt, cab, ala = self.ALTURA_BASE, 0.0, 0.0
        elif modo == "trotando":
            # Trotando se respeta solo la altura (en un rango seguro) y el cuerpo va derecho: inclinarlo
            # mientras trota lo desequilibra (igual que girar con zancadas asimétricas, ver pybullet.md).
            alt, cab, ala = _limitar(alt, 0.175, 0.205), 0.0, 0.0
        elif self.amp > 0:
            # Recién se pidió parar: mientras la rampa de frenado no termine (quedan pasos), el cuerpo
            # sigue derecho; recién con las 4 patas apoyadas se le deja inclinarse.
            alt, cab, ala = _limitar(alt, 0.175, 0.205), 0.0, 0.0
        self.trotando = modo == "trotando"
        self._obj = (alt, cab, ala)
        self.alt = _acercar(self.alt, alt, self.V_ALTURA * DT)
        self.cab = _acercar(self.cab, cab, self.V_ANG * DT)
        self.ala = _acercar(self.ala, ala, self.V_ANG * DT)
        # Rampa del trote: la amplitud sube/baja en RAMPA_S segundos (frenar de golpe a media zancada
        # lo tiraba al piso, ver pybullet.md). La fase sigue avanzando mientras quede amplitud, así el
        # paso termina suave en vez de congelarse con una pata en el aire.
        self.amp = _acercar(self.amp, 1.0 if self.trotando else 0.0, DT / self.RAMPA_S)
        if self.amp > 0:
            self.fase = (self.fase + 2 * math.pi * self.F_TROTE * DT) % (2 * math.pi)
        else:
            self.fase = 0.0
        self._mandar(self._ik_angulos())
        caido = self.caido()
        if caido and not self._estaba_caido:
            self.caidas += 1
        self._estaba_caido = caido

    def _altura_a_j(self, h):
        return (h - self.ALTURA_BASE) / self.MM_POR_GRADO

    def objetivo(self):
        alt, cab, ala = self._obj
        return self._altura_a_j(alt), math.degrees(cab), math.degrees(ala)

    def _ejes(self):
        """Ejes del robot en el mundo: adelante, izquierda, arriba (el URDF mira hacia -x, -y)."""
        q = p.getBasePositionAndOrientation(self.id)[1]
        m = np.array(p.getMatrixFromQuaternion(q)).reshape(3, 3)
        return -m[:, 0], -m[:, 1], m[:, 2]

    def medido(self):
        """Pose MEDIDA del cuerpo, como la daría la IMU (MPU-6050) del SpotMicro real.

        - cabeceo = cuánto sube la punta del eje "adelante"; alabeo = cuánto sube el eje "izquierda"
          (si sube la izquierda, el cuerpo se inclina a la derecha). No depende del giro de carga.
        - altura: la z de la base (centro de las caderas) relativa a la que tenía asentada con la
          altura de referencia (calib_z), para no depender del tamaño del pie; en "grados de j1".
        """
        adelante, izquierda, _ = self._ejes()
        z = p.getBasePositionAndOrientation(self.id)[0][2]
        alt = self.ALTURA_BASE + (z - (self.calib_z if self.calib_z is not None else z))
        return (self._altura_a_j(alt), math.degrees(math.asin(_limitar(adelante[2], -1, 1))),
                math.degrees(math.asin(_limitar(izquierda[2], -1, 1))))

    def caido(self):
        return self._ejes()[2][2] < 0.5    # el eje "arriba" a más de 60° de la vertical

    def posicion(self):
        return p.getBasePositionAndOrientation(self.id)[0]

    def camara(self):
        x, y, _ = self.posicion()
        return [x, y, 0.10], 0.80, 35, -20


def crear(nombre):
    """Crea el mundo (piso + gravedad) y el robot pedido por la variable ROBOT."""
    p.setAdditionalSearchPath(pybullet_data.getDataPath())
    p.setGravity(0, 0, -9.81)
    p.setTimeStep(DT)
    p.loadURDF("plane.urdf")
    if nombre == "spot":
        r = Spot()
        r.asentar()
        return r
    if nombre in ("nao", "pepper"):
        # 20 iteraciones del solver en vez de 50: con la base fija no hay contactos difíciles que
        # resolver, el error de seguimiento no cambia de forma visible y cada paso cuesta ~35 % menos.
        p.setPhysicsEngineParameter(numSolverIterations=20)
        r = Humanoide(nombre)
        for _ in range(240):            # 1 s para que los motores lleguen a la postura de reposo
            r.paso(0.0, "reposo", (0, 0, 0))
            p.stepSimulation()
        return r
    raise ValueError(f"ROBOT debe ser spot, pepper o nao (llegó {nombre!r})")


# =====================================================================================================
# "Esqueleto" visual de NAO y Pepper cuando NO están las mallas de SoftBank
# =====================================================================================================
# Sin aceptar la licencia de SoftBank no hay mallas; con solo una esfera por link el robot se ve como
# una nube de bolitas y no se distingue el brazo que mueve el real-to-sim. Así que el proceso de render
# carga una versión del MISMO URDF (mismos links, mismas articulaciones y en el mismo orden, por eso
# los ángulos se copian igual) en la que solo cambian los <visual>: huesos (cilindros) entre una
# articulación y la siguiente, y torso, cabeza, manos y pies con cajas y esferas de proporciones
# parecidas a las del robot real. La física y la cinemática NO cambian (las calcula el otro proceso).
# Las medidas salen de los <origin> de las articulaciones del URDF (metros, en el marco de cada link;
# x = adelante, y = izquierda, z = arriba, igual que el robot).

BLANCO, GRIS, OSCURO, AZUL = (0.94, 0.94, 0.96), (0.50, 0.52, 0.56), (0.22, 0.23, 0.26), (0.20, 0.45, 0.88)
CELESTE = (0.45, 0.68, 0.98)   # brazo derecho (el que sigue al ESP32) resaltado en los dos robots

# Cada pieza es (link, forma, datos, color):
#   ("hueso", (desde, hasta, radio))       cilindro entre dos puntos del marco del link
#   ("esfera", (centro, radio))
#   ("caja", (centro, (x, y, z), rpy))
#   ("cil", (centro, radio, largo, rpy))   cilindro con su eje z girado por rpy
PIEZAS = {
    "nao": [
        # Torso, cadera, botón azul del pecho y cuello
        ("torso", "caja", ((0.0, 0, 0.025), (0.075, 0.105, 0.135), (0, 0, 0)), BLANCO),
        ("torso", "caja", ((0.0, 0, -0.065), (0.065, 0.10, 0.04), (0, 0, 0)), GRIS),
        ("torso", "cil", ((0.039, 0, 0.055), 0.018, 0.004, (0, 1.5708, 0)), AZUL),
        ("torso", "hueso", ((0, 0, 0.09), (0, 0, 0.1265), 0.015), GRIS),
        # Cabeza: esfera blanca, "orejas" grises y ojos azules (mira hacia +x)
        ("Head", "esfera", ((0.0, 0, 0.058), 0.058), BLANCO),
        ("Head", "cil", ((-0.005, 0, 0.052), 0.03, 0.118, (1.5708, 0, 0)), GRIS),
        ("Head", "esfera", ((0.052, 0.022, 0.066), 0.012), AZUL),
        ("Head", "esfera", ((0.052, -0.022, 0.066), 0.012), AZUL),
        # Brazo izquierdo: hombro, brazo, codo, antebrazo y mano
        ("LShoulder", "esfera", ((0, 0, 0), 0.028), GRIS),
        ("LBicep", "hueso", ((0, 0, 0), (0.105, 0.015, 0), 0.021), BLANCO),
        ("LElbow", "esfera", ((0, 0, 0), 0.022), GRIS),
        ("LForeArm", "hueso", ((0, 0, 0), (0.056, 0, 0), 0.019), BLANCO),
        ("l_wrist", "caja", ((0.035, 0, -0.008), (0.06, 0.035, 0.035), (0, 0, 0)), GRIS),
        # Brazo derecho (j1 hombro, j2 codo): celeste con articulaciones azules
        ("RShoulder", "esfera", ((0, 0, 0), 0.028), AZUL),
        ("RBicep", "hueso", ((0, 0, 0), (0.105, -0.015, 0), 0.021), CELESTE),
        ("RElbow", "esfera", ((0, 0, 0), 0.024), AZUL),
        ("RForeArm", "hueso", ((0, 0, 0), (0.056, 0, 0), 0.019), CELESTE),
        ("r_wrist", "caja", ((0.035, 0, -0.008), (0.06, 0.035, 0.035), (0, 0, 0)), AZUL),
        # Piernas: cadera, muslo, rodilla, pierna y pie
        ("LHip", "esfera", ((0, 0, 0), 0.026), GRIS),
        ("LThigh", "hueso", ((0, 0, 0), (0, 0, -0.1), 0.026), BLANCO),
        ("LTibia", "esfera", ((0, 0, 0), 0.025), GRIS),
        ("LTibia", "hueso", ((0, 0, 0), (0, 0, -0.1029), 0.023), BLANCO),
        ("l_ankle", "caja", ((0.02, 0, -0.028), (0.155, 0.08, 0.035), (0, 0, 0)), BLANCO),
        ("RHip", "esfera", ((0, 0, 0), 0.026), GRIS),
        ("RThigh", "hueso", ((0, 0, 0), (0, 0, -0.1), 0.026), BLANCO),
        ("RTibia", "esfera", ((0, 0, 0), 0.025), GRIS),
        ("RTibia", "hueso", ((0, 0, 0), (0, 0, -0.1029), 0.023), BLANCO),
        ("r_ankle", "caja", ((0.02, 0, -0.028), (0.155, 0.08, 0.035), (0, 0, 0)), BLANCO),
    ],
    "pepper": [
        # Base con ruedas (la "falda") y la columna de la pierna: el link Tibia está a 0,334 m del piso
        ("Tibia", "cil", ((-0.02, 0, -0.27), 0.20, 0.10, (0, 0, 0)), BLANCO),
        ("Tibia", "cil", ((-0.02, 0, -0.326), 0.205, 0.012, (0, 0, 0)), OSCURO),
        ("Tibia", "hueso", ((0, 0, -0.22), (0, 0, 0), 0.10), BLANCO),
        ("Pelvis", "esfera", ((0, 0, 0), 0.085), GRIS),
        ("Pelvis", "hueso", ((0, 0, 0), (0, 0, 0.268), 0.08), BLANCO),
        ("Hip", "esfera", ((0, 0, 0), 0.085), GRIS),
        # Torso, la tableta del pecho y el cuello
        ("torso", "caja", ((-0.035, 0, 0.12), (0.16, 0.24, 0.20), (0, 0, 0)), BLANCO),
        ("torso", "caja", ((0.047, 0, 0.115), (0.012, 0.20, 0.14), (0, -0.25, 0)), OSCURO),
        ("torso", "hueso", ((-0.038, 0, 0.21), (-0.038, 0, 0.309), 0.04), GRIS),
        # Cabeza: esfera blanca grande con ojos de anillo azul
        ("Head", "esfera", ((0.0, 0, 0.12), 0.105), BLANCO),
        ("Head", "esfera", ((0.088, 0.04, 0.125), 0.022), AZUL),
        ("Head", "esfera", ((0.088, -0.04, 0.125), 0.022), AZUL),
        # Brazo izquierdo
        ("LShoulder", "esfera", ((0, 0, 0), 0.05), BLANCO),
        ("LBicep", "hueso", ((0, 0, 0), (0.1812, 0.015, 0), 0.04), BLANCO),
        ("LElbow", "esfera", ((0, 0, 0), 0.042), GRIS),
        ("LForeArm", "hueso", ((0, 0, 0), (0.15, 0, 0), 0.035), BLANCO),
        ("l_wrist", "esfera", ((0.055, 0, -0.012), 0.045), GRIS),
        # Brazo derecho (j1 hombro, j2 codo) resaltado
        ("RShoulder", "esfera", ((0, 0, 0), 0.05), AZUL),
        ("RBicep", "hueso", ((0, 0, 0), (0.1812, -0.015, 0), 0.04), CELESTE),
        ("RElbow", "esfera", ((0, 0, 0), 0.044), AZUL),
        ("RForeArm", "hueso", ((0, 0, 0), (0.15, 0, 0), 0.035), CELESTE),
        ("r_wrist", "esfera", ((0.055, 0, -0.012), 0.045), AZUL),
    ],
}


def _visual_xml(forma, datos, color):
    """Un elemento <visual> de URDF con su color propio."""
    rpy = (0.0, 0.0, 0.0)
    if forma == "hueso":
        # Cilindro desde `a` hasta `b`: se centra en el punto medio y se gira para que su eje z apunte
        # de un extremo al otro (primero cabeceo sobre y, después guiñada sobre z).
        a, b, radio = np.asarray(datos[0], float), np.asarray(datos[1], float), datos[2]
        v = b - a
        centro = (a + b) / 2
        rpy = (0.0, math.atan2(math.hypot(v[0], v[1]), v[2]), math.atan2(v[1], v[0]))
        geo = f'<cylinder radius="{radio}" length="{float(np.linalg.norm(v)):.4f}"/>'
    elif forma == "esfera":
        centro, geo = datos[0], f'<sphere radius="{datos[1]}"/>'
    elif forma == "caja":
        centro, rpy = datos[0], datos[2]
        geo = '<box size="{:.4f} {:.4f} {:.4f}"/>'.format(*datos[1])
    else:   # "cil"
        centro, rpy = datos[0], datos[3]
        geo = f'<cylinder radius="{datos[1]}" length="{datos[2]}"/>'
    rgba = " ".join(f"{c:.3f}" for c in color) + " 1"
    return (f'<visual><origin xyz="{centro[0]:.4f} {centro[1]:.4f} {centro[2]:.4f}" '
            f'rpy="{rpy[0]:.4f} {rpy[1]:.4f} {rpy[2]:.4f}"/><geometry>{geo}</geometry>'
            f'<material name="c{rgba.replace(" ", "_")}"><color rgba="{rgba}"/></material></visual>')


def urdf_esqueleto(urdf_sin_mallas):
    """Del URDF sin mallas (esferas) arma otro con los mismos links pero con los <visual> de PIEZAS."""
    texto = open(urdf_sin_mallas, encoding="utf-8").read()
    nombre = "nao" if 'name="NaoH25V50"' in texto else "pepper"
    texto = re.sub(r"<visual>.*?</visual>", "", texto, flags=re.S)
    por_link = {}
    for link, forma, datos, color in PIEZAS[nombre]:
        por_link.setdefault(link, []).append(_visual_xml(forma, datos, color))
    for link, piezas in por_link.items():
        # Se insertan justo después de la etiqueta de apertura del link (también si era <link .../>).
        texto, n = re.subn(rf'<link name="{re.escape(link)}"([^>]*?)(/?)>',
                           lambda m: f'<link name="{link}"{m.group(1)}>' + "".join(piezas)
                           + ("</link>" if m.group(2) else ""), texto, count=1)
        if n != 1:
            print(f"[robot] esqueleto: no encontré el link {link}", flush=True)
    f = tempfile.NamedTemporaryFile("w", prefix="esqueleto_", suffix=".urdf", delete=False, encoding="utf-8")
    f.write(texto)
    f.close()
    return f.name


def crear_visual(modelo):
    """Copia SOLO VISUAL del robot para el proceso de render (sin física: base fija y nunca se llama a
    stepSimulation). En cada cuadro se le copian la pose de la base y los ángulos del robot real."""
    p.setAdditionalSearchPath(pybullet_data.getDataPath())
    p.loadURDF("plane.urdf")
    urdf, flags, pos, orn = modelo
    if os.path.basename(urdf).startswith("sin_mallas_"):
        # Sin las mallas de SoftBank: el mismo robot dibujado como un esqueleto legible (ver PIEZAS).
        urdf = urdf_esqueleto(urdf)
    cuerpo = cargar(urdf, pos, orn, True, flags)
    movibles = [i for i in range(p.getNumJoints(cuerpo)) if p.getJointInfo(cuerpo, i)[2] != p.JOINT_FIXED]
    return cuerpo, movibles


def foto(cuerpo):
    """Pose de la base + ángulo de cada articulación móvil (lo que el render necesita copiar)."""
    pos, orn = p.getBasePositionAndOrientation(cuerpo)
    movibles = [i for i in range(p.getNumJoints(cuerpo)) if p.getJointInfo(cuerpo, i)[2] != p.JOINT_FIXED]
    return pos, orn, [s[0] for s in p.getJointStates(cuerpo, movibles)]
