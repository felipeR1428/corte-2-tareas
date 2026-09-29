"""Punto 1: teclado 4x4 por ocho GPIO; LCD 16x2 por I2C.

Guardar en la ESP32 como main.py desde Thonny (MicroPython).
Enviar la tecla al PC por USB; no inicializar machine.UART(0).
"""

import time
from machine import I2C, Pin


ROW_PINS = (14, 27, 26, 25)  # R1, R2, R3, R4 del teclado de ocho cables.
COL_PINS = (33, 32, 18, 19)  # C1, C2, C3, C4; entradas con pull-up.
LCD_ADDRESS = 0x27           # Direccion de la pantalla LCD 16x2 I2C.
KEYS = ("123A", "456B", "789C", "*0#D")


def checksum(data):
    result = 0
    for byte in data:
        result ^= byte
    return result


def send_key(key):
    data = "KEY," + key
    print("@%s*%02X" % (data, checksum(data.encode("ascii"))))


class Keyboard:
    """Escanea directamente las cuatro filas y cuatro columnas del teclado."""

    def __init__(self):
        self.rows = [Pin(pin, Pin.OUT, value=1) for pin in ROW_PINS]
        self.columns = [Pin(pin, Pin.IN, Pin.PULL_UP) for pin in COL_PINS]

    def read(self):
        matches = []
        for row_number, row_pin in enumerate(self.rows):
            row_pin.value(0)
            try:
                time.sleep_us(80)
                for col_number, col_pin in enumerate(self.columns):
                    if col_pin.value() == 0:
                        matches.append(KEYS[row_number][col_number])
            finally:
                row_pin.value(1)
        # Varias teclas a la vez se descartan; evita un codigo ambiguo.
        return matches[0] if len(matches) == 1 else None


class LCD16x2:
    """LCD HD44780 con mochila I2C habitual: RS=P0, EN=P2, luz=P3."""

    def __init__(self, bus, address):
        self.bus, self.address = bus, address
        time.sleep_ms(50)
        for code in (0x30, 0x30, 0x30, 0x20):
            self.nibble(code)
            time.sleep_ms(5)
        for code in (0x28, 0x0C, 0x06, 0x01):
            self.command(code)

    def nibble(self, code, rs=0):
        data = (code & 0xF0) | 0x08 | rs
        self.bus.writeto(self.address, bytes((data | 0x04,)))
        time.sleep_us(2)
        self.bus.writeto(self.address, bytes((data,)))
        time.sleep_us(60)

    def command(self, code, rs=0):
        self.nibble(code, rs)
        self.nibble(code << 4, rs)
        if code in (0x01, 0x02):
            time.sleep_ms(3)

    def show(self, line1, line2):
        for position, value in ((0x80, line1), (0xC0, line2)):
            self.command(position)
            text = value[:16]
            for char in text + (" " * (16 - len(text))):
                self.command(ord(char), 1)


def main():
    keyboard = Keyboard()
    lcd = None
    try:
        bus = I2C(0, scl=Pin(22), sda=Pin(21), freq=100000)
        present = bus.scan()
        print("Dispositivos I2C (solo LCD):", present)
        if LCD_ADDRESS in present:
            lcd = LCD16x2(bus, LCD_ADDRESS)
            lcd.show("PUNTO 1", "TECLADO LISTO")
        else:
            print("LCD opcional no encontrada en 0x%02X" % LCD_ADDRESS)
    except OSError as exc:
        print("LCD I2C no disponible:", exc)
    print("Punto 1 listo: teclado en ocho GPIO + LCD I2C opcional + USB (115200).")
    pending = stable = None
    changed = time.ticks_ms()
    while True:
        try:
            key = keyboard.read()
        except OSError:
            key = None
        now = time.ticks_ms()
        if key != pending:
            pending, changed = key, now
        elif key != stable and time.ticks_diff(now, changed) >= 55:
            stable = key
            if key is not None:
                send_key(key)
                if lcd is not None:
                    try:
                        lcd.show("TECLA: " + key, "PC + PYBULLET")
                    except OSError:
                        lcd = None
        time.sleep_ms(15)


if __name__ == "__main__":
    main()
