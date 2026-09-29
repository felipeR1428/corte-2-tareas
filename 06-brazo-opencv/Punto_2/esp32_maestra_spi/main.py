"""Punto 2: ESP32-A MicroPython recibe digitos del PC y controla SPI.

Guardar como main.py en la ESP32-A desde Thonny. No usar UART(0):
la consola USB ya ocupa UART0 y permite print()/sys.stdin a 115200.
"""

import select
import sys
import time
from machine import Pin, SPI

CS = 5
READY = 27
SPI_BYTES = 12


def checksum(data):
    value = 0
    for byte in data:
        value ^= byte
    return value


def spi_frame(marker, sequence, digit, source):
    packet = bytearray(SPI_BYTES)
    packet[0] = marker
    packet[1] = sequence >> 8
    packet[2] = sequence & 255
    packet[3] = ord(digit)
    packet[4] = ord(source)
    packet[5] = checksum(packet[:5])
    return packet


def send_ack(sequence, digit, status):
    payload = "ACK,%s,%s,%s" % (sequence, digit, status)
    print("@%s*%02X" % (payload, checksum(payload.encode("ascii"))))


class Master:
    def __init__(self):
        self.cs = Pin(CS, Pin.OUT, value=1)
        self.ready = Pin(READY, Pin.IN, Pin.PULL_DOWN)
        self.spi = SPI(2, baudrate=200000, polarity=0, phase=0,
                       sck=Pin(18), mosi=Pin(23), miso=Pin(19))
        self.poll = select.poll()
        self.poll.register(sys.stdin, select.POLLIN)
        self.buffer = ""

    def exchange(self, packet):
        deadline = time.ticks_add(time.ticks_ms(), 700)
        while not self.ready.value():
            if time.ticks_diff(time.ticks_ms(), deadline) >= 0:
                raise OSError("ESP32-B no esta lista: revisar READY GPIO27")
            time.sleep_ms(1)
        answer = bytearray(SPI_BYTES)
        self.cs.value(0)
        try:
            self.spi.write_readinto(packet, answer)
        finally:
            self.cs.value(1)
        deadline = time.ticks_add(time.ticks_ms(), 50)
        while self.ready.value() and time.ticks_diff(time.ticks_ms(), deadline) < 0:
            time.sleep_ms(1)
        return answer

    def send_digit(self, sequence, digit, source):
        try:
            self.exchange(spi_frame(0xA5, sequence, digit, source))
            # La respuesta de B a COMMAND se recibe en el siguiente POLL.
            answer = self.exchange(spi_frame(0x5A, sequence, digit, source))
            if (answer[0] != 0xAC or answer[1] != sequence >> 8 or
                    answer[2] != (sequence & 255) or answer[3] != ord(digit) or
                    answer[5] != checksum(answer[:5])):
                raise OSError("ACK SPI corrupto")
            status = {0: "OK", 1: "SPI_ERR", 2: "OLED_ERR"}.get(answer[4], "SPI_ERR")
        except Exception as exc:
            print("Error SPI:", exc)
            status = "SPI_ERR"
        send_ack(sequence, digit, status)

    def handle_line(self, line):
        if len(line) > 80 or not line.startswith("@") or "*" not in line:
            return
        try:
            payload, check = line[1:].rsplit("*", 1)
            if len(check) != 2 or int(check, 16) != checksum(payload.encode("ascii")):
                return
            kind, seq_text, digit, source = payload.split(",")
            sequence = int(seq_text)
            if (kind == "DIG" and 0 <= sequence <= 65535 and
                    len(digit) == 1 and digit in "0123456789" and source in ("V", "T")):
                self.send_digit(sequence, digit, source)
        except (ValueError, UnicodeError):
            pass

    def serial_step(self):
        if self.poll.poll(0):
            char = sys.stdin.read(1)
            if char == "\n":
                self.handle_line(self.buffer.rstrip("\r"))
                self.buffer = ""
            elif len(self.buffer) < 80:
                self.buffer += char
            else:
                self.buffer = ""


def main():
    master = Master()
    print("Punto 2: ESP32-A maestra SPI lista a 200 kHz (PC por USB 115200).")
    while True:
        master.serial_step()
        time.sleep_ms(1)


if __name__ == "__main__":
    main()
