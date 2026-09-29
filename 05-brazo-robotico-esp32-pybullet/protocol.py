"""Protocolo de telemetría UART entre ESP32 y computador (sin dependencias)."""

from __future__ import annotations

from dataclasses import dataclass


JOINT_NAMES = ("joint_1", "joint_2", "joint_gripper", "joint_dedo_izq", "joint_dedo_der")
ADC_MAX = 65535


@dataclass(frozen=True)
class SensorPacket:
    sequence: int
    ticks_ms: int
    values: tuple[int, int, int, int]


def checksum(payload: bytes) -> int:
    check = 0
    for byte in payload:
        check ^= byte
    return check


def parse_packet(line: bytes) -> SensorPacket:
    """Acepta únicamente @ARM,secuencia,tiempo,a,b,c,d*XX\\n."""
    frame = line.strip(b"\r\n")
    if len(frame) > 100 or not frame.startswith(b"@ARM,"):
        raise ValueError("Trama UART desconocida")
    try:
        payload, check_hex = frame[1:].rsplit(b"*", 1)
        if len(check_hex) != 2 or int(check_hex, 16) != checksum(payload):
            raise ValueError("Checksum incorrecto")
        parts = payload.decode("ascii").split(",")
        if len(parts) != 7 or parts[0] != "ARM":
            raise ValueError("Número de campos incorrecto")
        numbers = tuple(int(value) for value in parts[1:])
    except (UnicodeError, ValueError) as exc:
        raise ValueError("Trama UART corrupta") from exc
    sequence, ticks_ms, *values = numbers
    if not 0 <= sequence <= 65535 or not 0 <= ticks_ms < 1 << 31:
        raise ValueError("Secuencia o tiempo fuera de rango")
    if any(value < 0 or value > ADC_MAX for value in values):
        raise ValueError("Lectura ADC fuera de rango")
    return SensorPacket(sequence, ticks_ms, tuple(values))


def positions_from_adc(values: tuple[int, int, int, int]) -> dict[str, float]:
    """Rangos en radianes/metros: dos giros, carro vertical, dos dedos simétricos."""
    if len(values) != 4 or any(not 0 <= value <= ADC_MAX for value in values):
        raise ValueError("Se esperan cuatro lecturas ADC de 0 a 65535")
    base, elbow, slide, opening = (value / ADC_MAX for value in values)
    fingers = 0.05 * opening
    return {
        "joint_1": -2.5 + 5.0 * base,
        "joint_2": -2.0 + 4.0 * elbow,
        "joint_gripper": 0.15 * slide,
        "joint_dedo_izq": fingers,
        "joint_dedo_der": fingers,
    }


class PacketStream:
    """Reconstruye líneas incluso cuando Windows entrega bytes por fragmentos."""

    def __init__(self) -> None:
        self.buffer = bytearray()
        self.bad = 0

    def feed(self, chunk: bytes) -> list[SensorPacket]:
        self.buffer.extend(chunk)
        packets = []
        while b"\n" in self.buffer:
            raw, _, rest = self.buffer.partition(b"\n")
            self.buffer = bytearray(rest)
            if not raw.startswith(b"@ARM,"):
                continue  # Mensajes del arranque o la consola de MicroPython.
            try:
                packets.append(parse_packet(raw))
            except ValueError:
                self.bad += 1
        if len(self.buffer) > 512:
            self.buffer.clear()  # Limitar ruido o líneas sin terminador.
            self.bad += 1
        return packets
