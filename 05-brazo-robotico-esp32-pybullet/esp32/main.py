"""Guardar como main.py en la ESP32 con Thonny (MicroPython, ESP32 clásico).

Cuatro potenciómetros a 3.3 V y GND, cursores en GPIO32, 33, 34 y 35.
La salida USB usa la consola serie de MicroPython a 115200 baudios.
No inicializar UART(0): ese puerto ya está ocupado por la consola de Thonny.
Cerrar Thonny antes de abrir el puerto COM desde la app.
"""

from machine import ADC, Pin
import time


PINS = (32, 33, 34, 35)
PERIOD_MS = 50  # 20 tramas/s


def read_adc(sensor):
    try:
        return sensor.read_u16()
    except AttributeError:
        return sensor.read() * 16  # Firmwares antiguos con ADC de 12 bits.


def build_frame(sequence, ticks_ms, values):
    payload = "ARM,%d,%d,%d,%d,%d,%d" % (
        sequence, ticks_ms, values[0], values[1], values[2], values[3]
    )
    check = 0
    for byte in payload.encode("ascii"):
        check ^= byte
    return "@%s*%02X" % (payload, check)


def main():
    sensors = []
    for pin in PINS:
        adc = ADC(Pin(pin))
        adc.atten(ADC.ATTN_11DB)
        sensors.append(adc)

    # print() ya sale por la consola USB/serie de MicroPython (UART0).
    # Crear UART(0) aqui causa ESP_ERR_INVALID_STATE con algunas versiones.
    filtered = [read_adc(adc) for adc in sensors]
    sequence = 0
    next_send = time.ticks_ms()
    while True:
        now = time.ticks_ms()
        if time.ticks_diff(now, next_send) >= 0:
            for index, sensor in enumerate(sensors):
                raw = read_adc(sensor)
                filtered[index] = (3 * filtered[index] + raw) // 4
            print(build_frame(sequence, now, filtered))
            sequence = (sequence + 1) % 65536
            next_send = time.ticks_add(now, PERIOD_MS)
        time.sleep_ms(5)


if __name__ == "__main__":
    main()
