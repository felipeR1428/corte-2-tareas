"""Reglas independientes de la cámara y del puerto serie."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence


GESTURE_TO_ACTION = {
    "Closed_Fist": "SET 30",
    "Victory": "SET 70",
    "Open_Palm": "SET 100",
    "Thumb_Down": "MODE 1",
    "Thumb_Up": "MODE 2",
}

GESTURE_NAMES = {
    "Closed_Fist": "Puño cerrado",
    "Victory": "Señal de victoria",
    "Open_Palm": "Palma abierta",
    "Thumb_Down": "Pulgar abajo",
    "Thumb_Up": "Pulgar arriba",
    "Pointing_Up": "Dedo índice arriba",
    "ILoveYou": "Señal I love you",
    "None": "Gesto no reconocido",
}

ACTION_NAMES = {
    "SET 0": "Luces apagadas",
    "SET 30": "Amarillo · 30 %",
    "SET 70": "Azul · 70 %",
    "SET 100": "Rojo · 100 %",
    "MODE 1": "Secuencia 1 · escalonada",
    "MODE 2": "Secuencia 2 · ida y vuelta",
    "STOP": "Luces apagadas",
}

# Amarillo, azul, rojo. Los tiempos y fases coinciden con el firmware.
MODE_1 = (((30, 0, 0), 0.6), ((0, 70, 0), 0.6),
          ((0, 0, 100), 0.6), ((0, 0, 0), 0.3))
MODE_2 = (((0, 0, 100), 0.35), ((0, 70, 0), 0.35),
          ((30, 0, 0), 0.35), ((0, 70, 0), 0.35))


@dataclass(frozen=True)
class HandGesture:
    side: str | None
    gesture: str | None
    score: float


def read_hand_gestures(result) -> tuple[HandGesture, ...]:
    """MediaPipe alinea gestos, lateralidad y puntos por índice de mano."""
    readings = []
    for index, _ in enumerate(result.hand_landmarks):
        gestures = result.gestures[index] if index < len(result.gestures) else ()
        sides = result.handedness[index] if index < len(result.handedness) else ()
        category = gestures[0] if gestures else None
        side = sides[0].category_name if sides else None
        readings.append(HandGesture(
            side if side in ("Left", "Right") else None,
            category.category_name if category else None,
            float(category.score) if category else 0.0,
        ))
    return tuple(readings)


def select_control_gesture(hands: Sequence[HandGesture],
                           min_score: float = 0.72) -> tuple[str | None, float, bool]:
    """Acepta una orden de cualquiera de las manos; señala órdenes distintas."""
    active = [hand for hand in hands
              if hand.gesture in GESTURE_TO_ACTION and hand.score >= min_score]
    if len({hand.gesture for hand in active}) > 1:
        return (None, 0.0, True)
    if active:
        best = max(active, key=lambda hand: hand.score)
        return (best.gesture, best.score, False)
    return (None, 0.0, False)


def preview_levels(action: str, elapsed_s: float = 0.0) -> tuple[int, int, int]:
    """Devuelve (amarillo, azul, rojo) para la vista previa."""
    if action in ("STOP", "SET 0"):
        return (0, 0, 0)
    if action == "SET 30":
        return (30, 0, 0)
    if action == "SET 70":
        return (0, 70, 0)
    if action == "SET 100":
        return (0, 0, 100)
    phases = MODE_1 if action == "MODE 1" else MODE_2 if action == "MODE 2" else ()
    if not phases:
        raise ValueError(f"Acción desconocida: {action}")
    cycle_s = sum(duration for _, duration in phases)
    position = max(0.0, elapsed_s) % cycle_s
    for levels, duration in phases:
        if position < duration:
            return levels
        position -= duration
    return phases[-1][0]  # Protección frente al redondeo de flotantes.


@dataclass
class GestureGate:
    """Confirma un gesto antes de enviar una orden y evita repeticiones."""

    min_score: float = 0.72
    required_frames: int = 5
    clear_frames: int = 3
    cooldown_s: float = 0.9
    candidate: str | None = None
    streak: int = 0
    neutral_streak: int = 0
    last_triggered: str | None = None
    last_time: float = float("-inf")
    armed: bool = True

    def reset(self) -> None:
        self.candidate = None
        self.streak = 0
        self.neutral_streak = 0
        self.last_triggered = None
        self.last_time = float("-inf")
        self.armed = True

    def pause_for_conflict(self) -> None:
        """Detiene la confirmación sin rearmar un gesto que sigue sostenido."""
        self.candidate = None
        self.streak = 0
        self.neutral_streak = 0

    def observe(self, gesture: str | None, score: float, now_s: float) -> str | None:
        if gesture not in GESTURE_TO_ACTION or score < self.min_score:
            self.candidate = None
            self.streak = 0
            self.neutral_streak += 1
            if self.neutral_streak >= self.clear_frames:
                self.armed = True
            return None

        self.neutral_streak = 0
        if gesture == self.candidate:
            self.streak += 1
        else:
            self.candidate = gesture
            self.streak = 1

        if (self.streak >= self.required_frames
                and now_s - self.last_time >= self.cooldown_s
                and (self.armed or gesture != self.last_triggered)):
            self.last_time = now_s
            self.last_triggered = gesture
            self.armed = False
            return GESTURE_TO_ACTION[gesture]
        return None
