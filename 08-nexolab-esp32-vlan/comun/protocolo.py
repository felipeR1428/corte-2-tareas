"""Mensajes que viajan por UDP entre los ESP32 y los contenedores (y entre contenedores y el admin).

Todos son una sola línea de texto ASCII con campos separados por comas, igual que en el tema 10:
se leen en el monitor serie, en Wireshark o con `herramientas` sin decodificar nada, y el ESP32 los
arma con un simple snprintf. El primer campo dice qué tipo de mensaje es.

    CTRL,<id 1-3>,<seq>,<t_ms>,<dir -100..100>,<vel -100..100>,<boton 0/1>     ESP32 gamer -> player
    JOINTS,<robot>,<seq>,<t_ms>,<j1>,<j2>,<j3>,<boton 0/1>                      ESP32 robot -> sim
    HB,<origen>,<seq>,<t_ms>                                                     cualquiera -> admin
    PING,<origen>,<seq>,<t_ms>  /  PONG,<origen>,<seq>,<t_ms>                    eco para medir RTT

- seq: contador que sube de a 1 en cada envío del mismo origen. Si llega 7 y después 10, se perdieron
  2 datagramas (UDP no reenvía nada); así cada contenedor cuenta las pérdidas sin ayuda de nadie.
- t_ms: milisegundos desde que arrancó quien envía (millis() en el ESP32). No sirve para comparar con
  el reloj del que recibe (no están sincronizados), pero sí para el jitter: la diferencia entre lo que
  avanzó el reloj del emisor y lo que avanzó el del receptor entre dos paquetes es la variación del
  retardo de la red (RFC 3550, la misma cuenta que usa RTP para voz y video).

Este archivo no depende de nada fuera de la biblioteca estándar: lo usan los contenedores, el emulador
de ESP32 y las pruebas, y el firmware sigue exactamente el mismo formato.
"""

import math
from dataclasses import dataclass

ROBOTS = ("spot", "pepper", "nao")


@dataclass
class Ctrl:
    jugador: int   # 1, 2 o 3 (qué carro maneja)
    seq: int
    t_ms: int
    dir: int       # -100 (todo a la izquierda) .. 100 (todo a la derecha)
    vel: int       # -100 (reversa a fondo) .. 100 (acelerador a fondo)
    boton: int     # 1 mientras se aprieta el botón del joystick (turbo / reaparecer)


@dataclass
class Joints:
    robot: str     # "spot", "pepper" o "nao"
    seq: int
    t_ms: int
    j: tuple       # (j1, j2, j3) en grados; qué articulación es cada una depende del robot
    boton: int     # 1 mientras se aprieta el pulsador (cambia de gesto / camina)


@dataclass
class Latido:
    tipo: str      # "HB", "PING" o "PONG"
    origen: str
    seq: int
    t_ms: int


def _limitar(v, lo, hi):
    return max(lo, min(hi, v))


def armar_ctrl(jugador, seq, t_ms, dir_, vel, boton=0):
    return f"CTRL,{jugador},{seq},{t_ms},{_limitar(int(dir_), -100, 100)},{_limitar(int(vel), -100, 100)},{1 if boton else 0}"


def armar_joints(robot, seq, t_ms, j1, j2, j3=0.0, boton=0):
    return f"JOINTS,{robot},{seq},{t_ms},{j1:.1f},{j2:.1f},{j3:.1f},{1 if boton else 0}"


def armar_latido(tipo, origen, seq, t_ms):
    return f"{tipo},{origen},{seq},{t_ms}"


def leer(datos):
    """Convierte un datagrama (bytes o str) en Ctrl, Joints o Latido. Devuelve None si no se entiende.

    Nunca lanza excepción: por UDP puede llegar cualquier cosa (un paquete cortado, basura de otro
    programa que usa el mismo puerto) y un contenedor no debe caerse por eso, solo ignorarlo.
    """
    try:
        if isinstance(datos, (bytes, bytearray)):
            datos = datos.decode("ascii", errors="strict")
        c = datos.strip().split(",")
        if c[0] == "CTRL" and len(c) == 7:
            jug = int(c[1])
            if jug not in (1, 2, 3):
                return None
            return Ctrl(jug, int(c[2]), int(c[3]), _limitar(int(c[4]), -100, 100),
                        _limitar(int(c[5]), -100, 100), 1 if c[6] == "1" else 0)
        if c[0] == "JOINTS" and len(c) == 8:
            if c[1] not in ROBOTS:
                return None
            j = (float(c[4]), float(c[5]), float(c[6]))
            # float() acepta "nan" e "inf": un ángulo así llegaría a PyBullet y rompería la
            # simulación, así que el mensaje entero se descarta.
            if not all(math.isfinite(v) for v in j):
                return None
            return Joints(c[1], int(c[2]), int(c[3]), j, 1 if c[7] == "1" else 0)
        if c[0] in ("HB", "PING", "PONG") and len(c) == 4:
            return Latido(c[0], c[1], int(c[2]), int(c[3]))
    except (ValueError, UnicodeDecodeError, IndexError, AttributeError, TypeError):
        pass
    return None


class ContadorSecuencia:
    """Cuenta recibidos y perdidos de UN origen a partir de su número de secuencia.

    - seq mayor que el esperado: se perdieron los del medio.
    - seq menor o igual al último: llegó tarde o repetido (UDP puede reordenar); se cuenta como
      desordenado y no se resta de los perdidos para no complicar (en una LAN casi nunca pasa).
    - Si el emisor se reinicia, su seq vuelve a empezar desde 0. Se reconoce de dos formas:
      un salto hacia atrás de más de SALTO_REINICIO (un desorden real en una LAN es de unos pocos
      paquetes, nunca de 50), o REINICIO_TRAS desordenados seguidos (cubre el reinicio cuando el
      emisor iba todavía en un seq bajo). Sin esto, después de un reinicio todo contaría como
      "desordenado" hasta volver a pasar el seq viejo y las pérdidas de ese tramo no se verían.
    """

    SALTO_REINICIO = 50
    REINICIO_TRAS = 3

    def __init__(self):
        self.ultimo = None
        self.recibidos = 0
        self.perdidos = 0
        self.desordenados = 0
        self.reinicios = 0
        self._desordenados_seguidos = 0

    def registrar(self, seq):
        self.recibidos += 1
        if self.ultimo is None:
            self.ultimo = seq
            return
        if seq > self.ultimo:
            self.perdidos += seq - self.ultimo - 1
            self.ultimo = seq
            self._desordenados_seguidos = 0
        elif seq < self.ultimo - self.SALTO_REINICIO:
            self._reiniciar(seq)
        else:
            self.desordenados += 1
            self._desordenados_seguidos += 1
            if self._desordenados_seguidos >= self.REINICIO_TRAS:
                # Los últimos REINICIO_TRAS eran del emisor ya reiniciado, no desorden de la red.
                self.desordenados -= self.REINICIO_TRAS
                self._reiniciar(seq)

    def _reiniciar(self, seq):
        self.reinicios += 1
        self.ultimo = seq
        self._desordenados_seguidos = 0

    def perdida_pct(self):
        total = self.recibidos + self.perdidos
        return 100.0 * self.perdidos / total if total else 0.0


class Jitter:
    """Jitter entre llegadas según el RFC 3550 (sección 6.4.1), en milisegundos.

    Para cada par de paquetes consecutivos i-1, i:
        D = (llegada_i - llegada_{i-1}) - (envio_i - envio_{i-1})
    D es cuánto más (o menos) tardó el paquete i que el anterior en cruzar la red. El jitter es un
    promedio móvil de |D| con peso 1/16: J = J + (|D| - J) / 16. El 1/16 hace que un solo paquete raro
    no dispare el valor pero que un cambio sostenido se note en unos 16 paquetes.
    No necesita relojes sincronizados: solo se restan tiempos del mismo reloj.
    """

    def __init__(self):
        self.j = 0.0
        self._prev = None

    def registrar(self, envio_ms, llegada_ms):
        if self._prev is not None:
            d = (llegada_ms - self._prev[1]) - (envio_ms - self._prev[0])
            self.j += (abs(d) - self.j) / 16.0
        self._prev = (envio_ms, llegada_ms)
        return self.j
