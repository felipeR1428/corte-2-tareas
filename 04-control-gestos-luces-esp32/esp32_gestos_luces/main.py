"""ESP32 con MicroPython: tres LED y dos secuencias por el USB serial.

Guardar ESTE archivo en la ESP32 como main.py mediante Thonny.
El programa de PC (app.py) se ejecuta en Windows, no en la placa.
"""

from machine import Pin, PWM
import select
import sys
from time import sleep_ms, ticks_diff, ticks_ms


FRECUENCIA_PWM = 5000
PIN_AMARILLO = 25
PIN_AZUL = 26
PIN_ROJO = 27


class ControladorLuces:
    def __init__(self):
        self.amarillo = PWM(Pin(PIN_AMARILLO), freq=FRECUENCIA_PWM)
        self.azul = PWM(Pin(PIN_AZUL), freq=FRECUENCIA_PWM)
        self.rojo = PWM(Pin(PIN_ROJO), freq=FRECUENCIA_PWM)
        self.estado = "OFF"
        self.fase = 0
        self.inicio_fase = ticks_ms()
        self.poner_luces(0, 0, 0)

    @staticmethod
    def poner_porcentaje(pwm, porcentaje):
        # MicroPython reciente: 0..65535. Compatibilidad con ESP32 antiguos: 0..1023.
        if hasattr(pwm, "duty_u16"):
            pwm.duty_u16((porcentaje * 65535 + 50) // 100)
        else:
            pwm.duty((porcentaje * 1023 + 50) // 100)

    def poner_luces(self, amarillo, azul, rojo):
        self.poner_porcentaje(self.amarillo, amarillo)
        self.poner_porcentaje(self.azul, azul)
        self.poner_porcentaje(self.rojo, rojo)

    def mostrar_fase(self):
        if self.estado == "MODE 1":
            niveles = ((30, 0, 0), (0, 70, 0), (0, 0, 100), (0, 0, 0))
        elif self.estado == "MODE 2":
            niveles = ((0, 0, 100), (0, 70, 0), (30, 0, 0), (0, 70, 0))
        else:
            return
        self.poner_luces(*niveles[self.fase])

    def cambiar_estado(self, nuevo):
        self.estado = nuevo
        self.fase = 0
        self.inicio_fase = ticks_ms()
        if nuevo == "SET 30":
            self.poner_luces(30, 0, 0)
        elif nuevo == "SET 70":
            self.poner_luces(0, 70, 0)
        elif nuevo == "SET 100":
            self.poner_luces(0, 0, 100)
        elif nuevo == "MODE 1" or nuevo == "MODE 2":
            self.mostrar_fase()
        else:
            self.poner_luces(0, 0, 0)
        print("STATE " + self.estado)

    def procesar_orden(self, orden):
        if orden == "PING":
            print("PONG")
        elif orden == "STATUS":
            print("STATE " + self.estado)
        elif orden == "STOP" or orden == "SET 0":
            self.cambiar_estado("OFF")
        elif orden in ("SET 30", "SET 70", "SET 100", "MODE 1", "MODE 2"):
            self.cambiar_estado(orden)
        else:
            print("ERR Orden desconocida")

    def actualizar(self, ahora=None):
        if self.estado != "MODE 1" and self.estado != "MODE 2":
            return
        if ahora is None:
            ahora = ticks_ms()
        duracion = 300 if self.estado == "MODE 1" and self.fase == 3 else (
            600 if self.estado == "MODE 1" else 350
        )
        if ticks_diff(ahora, self.inicio_fase) >= duracion:
            self.inicio_fase = ahora
            self.fase = (self.fase + 1) % 4
            self.mostrar_fase()


def ejecutar():
    luces = ControladorLuces()
    puerto = select.poll()
    puerto.register(sys.stdin, select.POLLIN)
    linea = ""
    linea_larga = False
    print("READY")
    try:
        while True:
            # Procesar hasta 32 caracteres disponibles y luego volver a las secuencias.
            for _ in range(32):
                if not puerto.poll(0):
                    break
                caracter = sys.stdin.read(1)
                if caracter == "\r":
                    continue
                if caracter == "\n":
                    if linea_larga:
                        print("ERR Orden demasiado larga")
                    elif linea:
                        luces.procesar_orden(linea)
                    linea = ""
                    linea_larga = False
                elif caracter and not linea_larga:
                    if len(linea) < 31:
                        linea += caracter
                    else:
                        linea_larga = True
            luces.actualizar()
            sleep_ms(5)
    except KeyboardInterrupt:
        luces.cambiar_estado("OFF")
        raise


if __name__ == "__main__":
    ejecutar()
