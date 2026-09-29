"""Extrae un único dígito como trazo claro sobre fondo negro para la CNN.

La fotografía puede contener una hoja o una pantalla luminosa. En ambos casos
un número negro debe transformarse en un trazo blanco, como en MNIST.
"""

from __future__ import annotations

import cv2
import numpy as np


def orient_frame(frame: np.ndarray, mirror: bool) -> np.ndarray:
    """La vista y el recorte que recibe la CNN tienen la misma orientación."""
    return cv2.flip(frame, 1) if mirror else frame


def _bright_screen(gray: np.ndarray):
    """Delimita una pantalla brillante dentro de una escena oscura, si existe."""
    height, width = gray.shape
    illuminated = cv2.GaussianBlur(gray, (5, 5), 0)
    _, bright = cv2.threshold(illuminated, 195, 255, cv2.THRESH_BINARY)
    contours, _ = cv2.findContours(bright, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    choices = []
    for contour in contours:
        area = cv2.contourArea(contour) / (height * width)
        x, y, w, h = cv2.boundingRect(contour)
        if not (0.12 < area < 0.8 and 0.22 < w / width < 0.95
                and 0.3 < h / height <= 1.0 and 0.35 < w / h < 1.8):
            continue
        if abs((x + w / 2) / width - 0.5) > 0.35:
            continue
        choices.append((area, contour, (x, y, w, h)))
    if not choices:
        return None
    _, contour, box = max(choices, key=lambda item: item[0])
    mask = np.zeros_like(gray)
    cv2.drawContours(mask, [contour], -1, 255, thickness=cv2.FILLED)
    # Un margen interior elimina bisel y borde oscuro del teléfono.
    margin = max(3, round(box[2] * 0.025))
    mask = cv2.erode(mask, np.ones((margin, margin), dtype=np.uint8))
    return mask


def _candidate(binary: np.ndarray, height: int, width: int):
    contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    options = []
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        if not (w >= width * 0.075 and h >= height * 0.14
                and width * height * 0.012 <= w * h <= width * height * 0.72):
            continue
        if not 0.11 <= w / h <= 2.4:
            continue
        if abs((x + w / 2) / width - 0.5) > 0.38:
            continue
        crop = binary[y:y + h, x:x + w]
        if np.count_nonzero(crop) < max(80, width * height * 0.005):
            continue
        # Se favorece el número completo sobre pequeños iconos o sombras.
        options.append((w * h, (x, y, w, h)))
    return max(options, default=None)


def process_roi(roi: np.ndarray, polarity: str = "auto"):
    """Devuelve (imagen 28x28, caja en el ROI, vista previa) o None.

    polarity: 'auto', 'dark' (tinta negra) o 'light' (tinta blanca).
    """
    if roi is None or roi.size == 0 or min(roi.shape[:2]) < 50:
        return None
    if isinstance(polarity, bool):  # Compatibilidad con scripts anteriores.
        polarity = "light" if polarity else "dark"
    if polarity not in ("auto", "dark", "light"):
        raise ValueError("Polaridad desconocida: auto, dark o light")

    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    height, width = gray.shape
    screen = _bright_screen(gray) if polarity != "light" else None

    if screen is not None:
        # Medir el umbral solo sobre la pantalla; el fondo oscuro no debe
        # convertirse en un rectángulo gigante que la red confunda con un 1.
        screen_pixels = gray[screen > 0]
        if screen_pixels.size < height * width * 0.07:
            return None
        threshold, _ = cv2.threshold(screen_pixels.reshape(-1, 1), 0, 255,
                                     cv2.THRESH_BINARY | cv2.THRESH_OTSU)
        threshold = min(float(threshold), 190.0)
        binary = np.where((screen > 0) & (gray < threshold), 255, 0).astype(np.uint8)
    else:
        # Quitar iluminación desigual y líneas muy claras de cuaderno; así
        # las sombras de la habitación no se vuelven un dígito.
        background = cv2.GaussianBlur(gray, (0, 0), max(8, min(height, width) / 16))
        if polarity == "light":
            contrast = cv2.subtract(gray, background)
        else:
            contrast = cv2.subtract(background, gray)
        cutoff = max(12, min(55, int(np.percentile(contrast, 86)) + 3))
        binary = np.where(contrast >= cutoff, 255, 0).astype(np.uint8)
        binary = cv2.morphologyEx(binary, cv2.MORPH_CLOSE,
                                  np.ones((3, 3), dtype=np.uint8))

    border = max(3, round(min(height, width) * 0.015))
    binary[:border, :] = 0
    binary[-border:, :] = 0
    binary[:, :border] = 0
    binary[:, -border:] = 0
    if not 0.004 < np.mean(binary > 0) < 0.45:
        return None
    candidate = _candidate(binary, height, width)
    if candidate is None:
        return None
    _, (x, y, w, h) = candidate
    digit = binary[y:y + h, x:x + w]
    # Cuadrado de 20 px centrado como las imágenes usadas para entrenar.
    target_w = max(1, round(20 * w / max(w, h)))
    target_h = max(1, round(20 * h / max(w, h)))
    digit = cv2.resize(digit, (target_w, target_h), interpolation=cv2.INTER_AREA)
    canvas = np.zeros((28, 28), dtype=np.uint8)
    left, top = (28 - target_w) // 2, (28 - target_h) // 2
    canvas[top:top + target_h, left:left + target_w] = digit
    moments = cv2.moments(canvas)
    if moments["m00"]:
        dx = round(13.5 - moments["m10"] / moments["m00"])
        dy = round(13.5 - moments["m01"] / moments["m00"])
        canvas = cv2.warpAffine(canvas, np.float32([[1, 0, dx], [0, 1, dy]]),
                                (28, 28), flags=cv2.INTER_LINEAR,
                                borderMode=cv2.BORDER_CONSTANT, borderValue=0)
    if screen is None:
        strokes = canvas[canvas > 0]
        if strokes.size and np.median(strokes) < 100:
            # El lápiz sobre cuadriculado pierde intensidad al bajar a 28 px.
            scale = min(3.0, 255 / max(85.0, np.percentile(strokes, 80)))
            canvas = np.clip(canvas.astype(np.float32) * scale, 0, 255).astype(np.uint8)
    return canvas.astype(np.float32) / 255, (x, y, w, h), canvas
