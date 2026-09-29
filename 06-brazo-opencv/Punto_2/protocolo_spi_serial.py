"""Contrato del punto 2: PC→maestra por USB; maestra↔esclava por SPI."""

from __future__ import annotations

from dataclasses import dataclass

SPI_SIZE = 12
SPI_COMMAND = 0xA5
SPI_POLL = 0x5A
SPI_ACK = 0xAC


def checksum(data: bytes) -> int:
    value = 0
    for byte in data:
        value ^= byte
    return value


def encode_digit(sequence: int, digit: str, source: str) -> bytes:
    if not 0 <= sequence <= 65535 or len(digit) != 1 or digit not in "0123456789":
        raise ValueError("Digito o secuencia invalida")
    if source not in ("V", "T"):
        raise ValueError("El origen debe ser V (vision) o T (prueba)")
    payload = f"DIG,{sequence},{digit},{source}".encode("ascii")
    return b"@" + payload + b"*" + ("%02X" % checksum(payload)).encode() + b"\n"


@dataclass(frozen=True)
class Ack:
    sequence: int
    digit: str
    status: str


def decode_ack(line: bytes) -> Ack:
    raw = line.strip(b"\r\n")
    if len(raw) > 80 or not raw.startswith(b"@ACK,"):
        raise ValueError("No es un ACK")
    try:
        payload, check = raw[1:].rsplit(b"*", 1)
        if len(check) != 2 or int(check, 16) != checksum(payload):
            raise ValueError("Checksum invalido")
        kind, seq, digit, status = payload.decode("ascii").split(",")
        if (kind != "ACK" or not seq.isdecimal() or not 0 <= int(seq) <= 65535
                or len(digit) != 1 or digit not in "0123456789"
                or status not in ("OK", "SPI_ERR", "OLED_ERR")):
            raise ValueError("Campos invalidos")
    except (UnicodeError, ValueError) as exc:
        raise ValueError("ACK corrupto") from exc
    return Ack(int(seq), digit, status)


class AckStream:
    def __init__(self):
        self.buffer = bytearray()

    def feed(self, chunk: bytes) -> list[Ack]:
        self.buffer.extend(chunk)
        result = []
        while b"\n" in self.buffer:
            raw, _, rest = self.buffer.partition(b"\n")
            self.buffer = bytearray(rest)
            try:
                result.append(decode_ack(raw))
            except ValueError:
                pass  # Mensajes informativos o consola de arranque de la ESP32.
        if len(self.buffer) > 512:
            self.buffer.clear()
        return result


def spi_frame(marker: int, sequence: int, digit: str = "0", source: str = "V") -> bytes:
    if marker not in (SPI_COMMAND, SPI_POLL) or not 0 <= sequence <= 65535:
        raise ValueError("Trama SPI invalida")
    if len(digit) != 1 or digit not in "0123456789" or source not in ("V", "T"):
        raise ValueError("Digito/origen invalido")
    data = bytes((marker, sequence >> 8, sequence & 255, ord(digit), ord(source)))
    return data + bytes((checksum(data),)) + bytes(SPI_SIZE - 6)


def decode_spi_ack(frame: bytes, sequence: int, digit: str) -> str:
    if len(frame) != SPI_SIZE or frame[0] != SPI_ACK or frame[5] != checksum(frame[:5]):
        raise ValueError("ACK SPI corrupto")
    if (frame[1] << 8 | frame[2]) != sequence or frame[3] != ord(digit):
        raise ValueError("ACK SPI para otra secuencia")
    try:
        return {0: "OK", 1: "SPI_ERR", 2: "OLED_ERR"}[frame[4]]
    except KeyError as exc:
        raise ValueError("Estado SPI desconocido") from exc
