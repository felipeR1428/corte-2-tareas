"""Protocolo independiente del punto 1: pulsaciones del teclado por USB."""

from __future__ import annotations

KEYS = frozenset("0123456789ABCD*#")


def checksum(data: bytes) -> int:
    result = 0
    for byte in data:
        result ^= byte
    return result


def encode_key(key: str) -> bytes:
    if key not in KEYS or len(key) != 1:
        raise ValueError("Tecla no valida")
    data = ("KEY," + key).encode("ascii")
    return b"@" + data + b"*" + ("%02X" % checksum(data)).encode("ascii") + b"\n"


def decode_key(line: bytes) -> str:
    line = line.strip(b"\r\n")
    if len(line) > 25 or not line.startswith(b"@KEY,"):
        raise ValueError("No es una pulsacion")
    try:
        payload, check = line[1:].rsplit(b"*", 1)
        key = payload.decode("ascii").split(",", 1)[1]
        if len(check) != 2 or int(check, 16) != checksum(payload):
            raise ValueError("Checksum invalido")
        if len(key) != 1 or key not in KEYS:
            raise ValueError("Tecla invalida")
    except (UnicodeError, ValueError) as exc:
        raise ValueError("Trama de teclado corrupta") from exc
    return key


class KeyStream:
    def __init__(self):
        self.buffer = bytearray()

    def feed(self, chunk: bytes) -> list[str]:
        self.buffer.extend(chunk)
        keys = []
        while b"\n" in self.buffer:
            line, _, remaining = self.buffer.partition(b"\n")
            self.buffer = bytearray(remaining)
            try:
                keys.append(decode_key(line))
            except ValueError:
                pass  # Mensajes del REPL al arrancar la placa.
        if len(self.buffer) > 128:
            self.buffer.clear()
        return keys
