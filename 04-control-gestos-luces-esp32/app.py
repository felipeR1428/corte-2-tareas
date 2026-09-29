"""Interfaz de cámara y control de tres luces mediante gestos de la mano."""

from __future__ import annotations

import queue
import sys
import threading
import time
import tkinter as tk
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from tkinter import messagebox, ttk
from urllib.request import urlopen
import zipfile

from logic import (ACTION_NAMES, GESTURE_NAMES, GestureGate, HandGesture,
                   preview_levels, read_hand_gestures, select_control_gesture)


BASE = Path(__file__).resolve().parent
MODEL_PATH = BASE / "models" / "gesture_recognizer.task"
MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/gesture_recognizer/"
    "gesture_recognizer/float16/latest/gesture_recognizer.task"
)

BG = "#101827"
PANEL = "#172335"
INSET = "#0a1220"
TEXT = "#edf3fb"
MUTED = "#a4b4c8"
ACCENT = "#65d6b1"
YELLOW = "#ffd54d"
BLUE = "#60a5fa"
RED = "#ff6675"

CONNECTIONS = (
    (0, 1), (1, 2), (2, 3), (3, 4),
    (0, 5), (5, 6), (6, 7), (7, 8),
    (5, 9), (9, 10), (10, 11), (11, 12),
    (9, 13), (13, 14), (14, 15), (15, 16),
    (13, 17), (17, 18), (18, 19), (19, 20), (0, 17),
)


@dataclass
class CameraFrame:
    image_rgb: object
    hands: tuple[HandGesture, ...]
    timestamp: float


def camera_worker(index: int, stop: threading.Event, frames: queue.Queue,
                  events: queue.Queue) -> None:
    """La captura y MediaPipe viven fuera del hilo de Tkinter."""
    cap = None
    try:
        import cv2
        import mediapipe as mp

        options = mp.tasks.vision.GestureRecognizerOptions(
            base_options=mp.tasks.BaseOptions(model_asset_path=str(MODEL_PATH)),
            running_mode=mp.tasks.vision.RunningMode.VIDEO,
            num_hands=2,
        )
        with mp.tasks.vision.GestureRecognizer.create_from_options(options) as recognizer:
            cap = cv2.VideoCapture(index)
            if not cap.isOpened():
                raise RuntimeError(f"No se pudo abrir la cámara {index}. Prueba otro índice.")
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
            events.put(("camera_ready", index))
            previous_ms = -1
            failed_reads = 0
            while not stop.is_set():
                ok, bgr = cap.read()
                if not ok:
                    failed_reads += 1
                    if failed_reads >= 12:
                        raise RuntimeError("Se perdió la señal de la cámara.")
                    stop.wait(0.08)
                    continue
                failed_reads = 0
                bgr = cv2.flip(bgr, 1)
                rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
                timestamp_ms = max(previous_ms + 1, time.monotonic_ns() // 1_000_000)
                previous_ms = timestamp_ms
                result = recognizer.recognize_for_video(
                    mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb), timestamp_ms
                )
                readings = read_hand_gestures(result)
                hands = result.hand_landmarks
                height, width = bgr.shape[:2]
                for index, hand in enumerate(hands):
                    pixels = [
                        (max(0, min(width - 1, int(point.x * width))),
                         max(0, min(height - 1, int(point.y * height))))
                        for point in hand
                    ]
                    color = ((80, 225, 190), (255, 170, 95))[index % 2]
                    for first, second in CONNECTIONS:
                        cv2.line(bgr, pixels[first], pixels[second], color, 2)
                    for point in pixels:
                        cv2.circle(bgr, point, 3, color, -1)
                    reading = readings[index]
                    side = {"Left": "Izquierda", "Right": "Derecha"}.get(
                        reading.side, f"Mano {index + 1}")
                    name = GESTURE_NAMES.get(reading.gesture, "Sin gesto")
                    caption = f"{side}: {name} {reading.score:.0%}"
                    caption = unicodedata.normalize("NFKD", caption).encode(
                        "ascii", "ignore").decode("ascii")
                    x = max(0, min(width - 285, min(p[0] for p in pixels)))
                    y = max(20, min(p[1] for p in pixels) - 8)
                    cv2.putText(bgr, caption, (x, y), cv2.FONT_HERSHEY_SIMPLEX,
                                0.48, color, 2, cv2.LINE_AA)

                image = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
                item = CameraFrame(image, readings, time.monotonic())
                try:
                    frames.put_nowait(item)
                except queue.Full:
                    try:
                        frames.get_nowait()  # Se prioriza el fotograma reciente.
                    except queue.Empty:
                        pass
                    frames.put_nowait(item)
    except ImportError as exc:
        events.put(("camera_error", f"Falta {exc.name}. Ejecuta instalar_windows.bat y abre iniciar_windows.bat."))
    except Exception as exc:
        events.put(("camera_error", str(exc)))
    finally:
        if cap is not None:
            cap.release()
        events.put(("camera_stopped", None))


def model_worker(events: queue.Queue) -> None:
    temp_path = MODEL_PATH.with_suffix(".task.part")
    try:
        MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
        with urlopen(MODEL_URL, timeout=45) as source, temp_path.open("wb") as target:
            while True:
                chunk = source.read(1024 * 256)
                if not chunk:
                    break
                target.write(chunk)
        if temp_path.stat().st_size < 1_000_000 or not zipfile.is_zipfile(temp_path):
            raise ValueError("La descarga no contiene un modelo .task válido.")
        temp_path.replace(MODEL_PATH)
        events.put(("model_ready", None))
    except Exception as exc:
        events.put(("model_error", str(exc)))
    finally:
        temp_path.unlink(missing_ok=True)


class LightingApp:
    def __init__(self, root: tk.Tk) -> None:
        from PIL import Image, ImageTk  # Mensaje claro si falta Pillow.

        self.Image = Image
        self.ImageTk = ImageTk
        self.root = root
        self.root.title("Control de iluminación por gestos | ESP32")
        self.root.configure(bg=BG)
        self.root.geometry("1220x830")
        self.root.minsize(1090, 740)
        self.closed = False
        self.frames: queue.Queue = queue.Queue(maxsize=1)
        self.events: queue.Queue = queue.Queue()
        self.camera_stop = threading.Event()
        self.camera_thread: threading.Thread | None = None
        self.model_thread: threading.Thread | None = None
        self.gate = GestureGate()
        self.serial = None
        self.serial_buffer = bytearray()
        self.serial_ready = False
        self.last_ping = 0.0
        self.action = "STOP"
        self.action_start = time.monotonic()
        self.photo = None
        self.auto_enabled = tk.BooleanVar(value=True)
        self.port_choice = tk.StringVar()
        self.camera_choice = tk.StringVar(value="0")
        self.model_text = tk.StringVar(value="Modelo listo" if MODEL_PATH.is_file() else "Modelo pendiente")
        self.camera_text = tk.StringVar(value="Cámara detenida")
        self.port_text = tk.StringVar(value="ESP32 desconectada · vista previa local")
        self.hand_texts = [tk.StringVar(value=f"Mano {n}: no detectada") for n in (1, 2)]
        self.hand_scores = [tk.StringVar(value="Confianza: —") for _ in range(2)]
        self.conflict_active = False
        self.action_text = tk.StringVar(value=ACTION_NAMES[self.action])
        self.mode_text = tk.StringVar(value="Control por gestos activo")
        self._build()
        self.refresh_ports()
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.after(50, self._poll)

    def _label(self, parent, content: str, *, size: int = 11, bold: bool = False,
               fg: str = TEXT, bg: str = PANEL, **options) -> tk.Label:
        return tk.Label(parent, text=content, font=("Segoe UI", size, "bold" if bold else "normal"),
                        fg=fg, bg=bg, **options)

    def _button(self, parent, label: str, callback, *, bg: str = "#29405a",
                fg: str = TEXT) -> tk.Button:
        return tk.Button(parent, text=label, command=callback,
                         bg=bg, fg=fg, activebackground=ACCENT,
                         activeforeground=INSET, relief="flat", bd=0,
                         font=("Segoe UI", 10, "bold"), padx=12, pady=9,
                         cursor="hand2")

    def _build(self) -> None:
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure("Dark.TCombobox", fieldbackground=INSET, background=PANEL,
                        foreground=TEXT, arrowcolor=TEXT, borderwidth=0)
        style.map("Dark.TCombobox", fieldbackground=[("readonly", INSET)],
                  foreground=[("readonly", TEXT)])
        style.configure("Dark.TNotebook", background=BG, borderwidth=0)
        style.configure("Dark.TNotebook.Tab", background=PANEL, foreground=TEXT,
                        padding=(20, 10), font=("Segoe UI", 10, "bold"))
        style.map("Dark.TNotebook.Tab", background=[("selected", "#29405a")])
        self.root.option_add("*TCombobox*Listbox.background", INSET)
        self.root.option_add("*TCombobox*Listbox.foreground", TEXT)

        header = tk.Frame(self.root, bg=BG)
        header.pack(fill="x", padx=24, pady=(16, 6))
        self._label(header, "GESTOS  /  ILUMINACIÓN", size=21, bold=True,
                    fg=TEXT, bg=BG).pack(anchor="w")
        self._label(header, "Cámara en directo, reconocimiento de mano y mando de tres luces",
                    size=10, fg=MUTED, bg=BG).pack(anchor="w")

        notebook = ttk.Notebook(self.root, style="Dark.TNotebook")
        notebook.pack(fill="both", expand=True, padx=20, pady=(4, 16))
        main = tk.Frame(notebook, bg=BG)
        help_page = tk.Frame(notebook, bg=BG)
        notebook.add(main, text="  Panel de control  ")
        notebook.add(help_page, text="  Gestos y conexiones  ")

        setup = tk.Frame(main, bg=PANEL, padx=14, pady=12)
        setup.pack(fill="x", pady=(10, 12))
        self._label(setup, "MODELO", size=9, fg=MUTED).grid(row=0, column=0, sticky="w")
        self._label(setup, "PUERTO USB", size=9, fg=MUTED).grid(row=0, column=2, sticky="w", padx=(15, 0))
        self._label(setup, "CÁMARA", size=9, fg=MUTED).grid(row=0, column=6, sticky="w", padx=(15, 0))
        self._label(setup, "", size=9).grid(row=0, column=8)
        tk.Label(setup, textvariable=self.model_text, bg=PANEL, fg=ACCENT,
                 font=("Segoe UI", 10, "bold")).grid(row=1, column=0, sticky="w")
        self.model_button = self._button(setup, "Descargar modelo", self.download_model)
        self.model_button.grid(row=1, column=1, padx=(8, 0))
        self.ports = ttk.Combobox(setup, textvariable=self.port_choice, width=10,
                                  state="readonly", style="Dark.TCombobox")
        self.ports.grid(row=1, column=2, padx=(15, 4))
        self._button(setup, "↻", self.refresh_ports).grid(row=1, column=3, padx=3)
        self.connect_button = self._button(setup, "Conectar", self.toggle_serial, bg="#266451")
        self.connect_button.grid(row=1, column=4, padx=4)
        self._label(setup, "USB", size=9, fg=MUTED).grid(row=1, column=5, padx=3)
        cameras = ttk.Combobox(setup, textvariable=self.camera_choice, width=3,
                               values=("0", "1", "2", "3"), state="readonly", style="Dark.TCombobox")
        cameras.grid(row=1, column=6, padx=(15, 4))
        self.camera_button = self._button(setup, "Iniciar cámara", self.toggle_camera, bg="#266451")
        self.camera_button.grid(row=1, column=7, padx=4)
        setup.grid_columnconfigure(8, weight=1)

        body = tk.Frame(main, bg=BG)
        body.pack(fill="both", expand=True)
        left = tk.Frame(body, bg=PANEL, padx=12, pady=12)
        left.pack(side="left", fill="both", expand=True, padx=(0, 12))
        self._label(left, "VISTA DE LA CÁMARA", size=10, bold=True, fg=MUTED).pack(anchor="w", pady=(0, 8))
        view = tk.Frame(left, bg=INSET, width=640, height=440)
        view.pack(fill="both", expand=True)
        view.pack_propagate(False)
        self.video_label = tk.Label(view, text="Activa la cámara para ver tu mano aquí",
                                    bg=INSET, fg=MUTED, font=("Segoe UI", 14))
        self.video_label.pack(fill="both", expand=True)
        tk.Label(left, textvariable=self.camera_text, bg=PANEL, fg=MUTED,
                 font=("Segoe UI", 10)).pack(anchor="w", pady=(8, 0))

        right = tk.Frame(body, bg=PANEL, padx=15, pady=12, width=380)
        right.pack(side="right", fill="y")
        right.pack_propagate(False)
        self._label(right, "LECTURA ACTUAL", size=10, bold=True, fg=MUTED).pack(anchor="w")
        for gesture_text, confidence_text in zip(self.hand_texts, self.hand_scores):
            tk.Label(right, textvariable=gesture_text, font=("Segoe UI", 13, "bold"),
                     fg=TEXT, bg=PANEL, anchor="w", justify="left",
                     wraplength=345).pack(fill="x", pady=(6, 0))
            tk.Label(right, textvariable=confidence_text, font=("Segoe UI", 10),
                     fg=MUTED, bg=PANEL).pack(anchor="w")
        tk.Frame(right, bg="#34445a", height=1).pack(fill="x", pady=13)
        self._label(right, "SALIDA", size=10, bold=True, fg=MUTED).pack(anchor="w")
        tk.Label(right, textvariable=self.action_text, font=("Segoe UI", 15, "bold"),
                 fg=ACCENT, bg=PANEL, anchor="w", wraplength=345).pack(fill="x", pady=6)
        tk.Label(right, textvariable=self.port_text, font=("Segoe UI", 9),
                 fg=MUTED, bg=PANEL, anchor="w", wraplength=345).pack(fill="x")
        self._label(right, "LUCES · VISTA PREVIA", size=10, bold=True,
                    fg=MUTED).pack(anchor="w", pady=(15, 5))
        self.leds: list[tuple[tk.Canvas, int, tk.Label]] = []
        for name, color in (("Amarillo", YELLOW), ("Azul", BLUE), ("Rojo", RED)):
            row = tk.Frame(right, bg=INSET, pady=5, padx=10)
            row.pack(fill="x", pady=3)
            lamp = tk.Canvas(row, width=28, height=28, bg=INSET, highlightthickness=0)
            lamp.pack(side="left")
            oval = lamp.create_oval(3, 3, 25, 25, fill="#39475a", outline=color, width=1)
            self._label(row, name, size=11, bg=INSET).pack(side="left", padx=12)
            intensity = self._label(row, "0 %", size=11, bold=True, bg=INSET, fg=MUTED)
            intensity.pack(side="right")
            self.leds.append((lamp, oval, intensity))
        self.led_colors = (YELLOW, BLUE, RED)
        tk.Frame(right, bg="#34445a", height=1).pack(fill="x", pady=14)
        self._label(right, "EVENTOS", size=10, bold=True, fg=MUTED).pack(anchor="w", pady=(0, 5))
        self.log_box = tk.Text(right, height=7, bg=INSET, fg=MUTED, relief="flat",
                               borderwidth=0, state="disabled", wrap="word",
                               font=("Consolas", 9), padx=8, pady=6)
        self.log_box.pack(fill="both", expand=True)

        controls = tk.Frame(main, bg=PANEL, padx=14, pady=12)
        controls.pack(fill="x", pady=(12, 0))
        auto = tk.Checkbutton(controls, text="Activar control por gestos", variable=self.auto_enabled,
                              command=self.toggle_automatic, bg=PANEL, fg=TEXT,
                              selectcolor=INSET, activebackground=PANEL, activeforeground=TEXT,
                              font=("Segoe UI", 11, "bold"))
        auto.pack(anchor="w")
        tk.Label(controls, textvariable=self.mode_text, bg=PANEL, fg=MUTED,
                 font=("Segoe UI", 9)).pack(anchor="w", pady=(0, 8))
        row = tk.Frame(controls, bg=PANEL)
        row.pack(fill="x")
        for label, action, color in (
            ("Amarillo 30 %", "SET 30", "#806520"),
            ("Azul 70 %", "SET 70", "#245385"),
            ("Rojo 100 %", "SET 100", "#9e3447"),
            ("Secuencia 1", "MODE 1", "#735173"),
            ("Secuencia 2", "MODE 2", "#376954"),
            ("Apagar todo", "STOP", "#6a3442"),
        ):
            self._button(row, label, lambda value=action: self.send_action(value, manual=True),
                         bg=color).pack(side="left", expand=True, fill="x", padx=3)

        self._build_help(help_page)
        self._show_leds()
        self._log("Interfaz lista. Descarga el modelo si falta; el control manual ya está disponible.")

    def _build_help(self, parent) -> None:
        frame = tk.Frame(parent, bg=PANEL, padx=22, pady=20)
        frame.pack(fill="both", expand=True, pady=10)
        content = (
            "GESTOS DEL TALLER\n\n"
            "Puño cerrado   →  LED amarillo al 30 %\n"
            "Señal de victoria   →  LED azul al 70 %\n"
            "Palma abierta   →  LED rojo al 100 %\n"
            "Pulgar abajo   →  Modo 1: amarillo → azul → rojo → pausa\n"
            "Pulgar arriba   →  Modo 2: rojo → azul → amarillo → azul\n\n"
            "Se detectan hasta dos manos y se muestran sus gestos por separado.\n"
            "Cada gesto debe mantenerse visible varios fotogramas. Si las dos\n"
            "manos hacen órdenes diferentes a la vez, no se envía ninguna nueva.\n"
            "El mismo gesto no se repite hasta que retires ambas manos o cambies\n"
            "de gesto.\n\n"
            "CONEXIONES PARA ESP32 DE 38 PINES\n\n"
            "GPIO25 → resistencia 330 Ω → ánodo LED amarillo; cátodo → GND\n"
            "GPIO26 → resistencia 330 Ω → ánodo LED azul; cátodo → GND\n"
            "GPIO27 → resistencia 330 Ω → ánodo LED rojo; cátodo → GND\n"
            "ESP32 ↔ computador: cable USB de datos, 115200 baudios.\n\n"
            "PARA USARLO\n\n"
            "1. Guarda esp32_gestos_luces/main.py como main.py en la ESP32 con Thonny.\n"
            "   Cierra Thonny y reinicia la placa antes de conectar el COM.\n"
            "2. En esta ventana pulsa Descargar modelo, si aún no está.\n"
            "3. Selecciona el COM, pulsa Conectar y luego Iniciar cámara.\n"
            "4. Puedes manejar las luces con los botones sin cámara ni ESP32.\n"
            "   En ese caso la vista previa es una simulación local.\n\n"
            "El botón manual pausa los gestos para que el siguiente fotograma no\n"
            "deshaga tu orden. Reactívalos con la casilla de control por gestos."
        )
        self._label(frame, "Guía rápida", size=20, bold=True).pack(anchor="w", pady=(0, 15))
        self._label(frame, content, size=11, justify="left", anchor="nw").pack(anchor="nw")

    def _log(self, message: str) -> None:
        if self.closed:
            return
        self.log_box.configure(state="normal")
        self.log_box.insert("end", f"{time.strftime('%H:%M:%S')}  {message}\n")
        lines = int(self.log_box.index("end-1c").split(".")[0])
        if lines > 100:
            self.log_box.delete("1.0", f"{lines - 80}.0")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def download_model(self) -> None:
        if MODEL_PATH.is_file():
            self._log("El modelo ya está descargado.")
            return
        if self.model_thread is not None and self.model_thread.is_alive():
            return
        self.model_text.set("Descargando…")
        self.model_button.configure(state="disabled")
        self.model_thread = threading.Thread(target=model_worker, args=(self.events,), daemon=True)
        self.model_thread.start()

    def refresh_ports(self) -> None:
        try:
            from serial.tools import list_ports
        except ImportError:
            self._log("Falta pyserial. Ejecuta instalar_windows.bat y abre iniciar_windows.bat.")
            return
        ports = [port.device for port in list_ports.comports()]
        self.ports.configure(values=ports)
        if ports and self.port_choice.get() not in ports:
            self.port_choice.set(ports[0])
        elif not ports:
            self.port_choice.set("")
        self._log(f"Puertos detectados: {', '.join(ports) if ports else 'ninguno'}")

    def toggle_serial(self) -> None:
        if self.serial is not None:
            self.disconnect()
            return
        port = self.port_choice.get()
        if not port:
            self._log("Selecciona un puerto COM. Conecta la ESP32 y pulsa ↻.")
            return
        try:
            import serial
            self.serial = serial.Serial(port, 115200, timeout=0, write_timeout=0.35)
            self.serial_buffer.clear()
            self.serial_ready = False
            self.last_ping = 0.0
            self.connect_button.configure(text="Desconectar", bg="#6a3442")
            self.port_text.set(f"{port} · esperando respuesta de la ESP32")
            self._log(f"Puerto {port} abierto. Esperando arranque de la ESP32…")
        except Exception as exc:
            self.serial = None
            self._log(f"No se pudo abrir el puerto: {exc}")

    def disconnect(self) -> None:
        if self.serial is not None:
            try:
                if self.serial_ready:
                    self.serial.write(b"STOP\n")
                self.serial.close()
            except Exception:
                pass
        self.serial = None
        self.serial_ready = False
        self.connect_button.configure(text="Conectar", bg="#266451")
        self.port_text.set("ESP32 desconectada · vista previa local")
        self.action = "STOP"
        self.action_start = time.monotonic()
        self.action_text.set(ACTION_NAMES[self.action])
        self._log("ESP32 desconectada. Se envió apagado al desconectar si estaba disponible.")

    def _poll_serial(self) -> None:
        if self.serial is None:
            return
        try:
            now = time.monotonic()
            if not self.serial_ready and now - self.last_ping >= 0.65:
                self.serial.write(b"PING\n")
                self.last_ping = now
            incoming = self.serial.read(self.serial.in_waiting) if self.serial.in_waiting else b""
            if incoming:
                self.serial_buffer.extend(incoming)
                if len(self.serial_buffer) > 512:
                    self.serial_buffer.clear()
                while b"\n" in self.serial_buffer:
                    raw, _, remaining = self.serial_buffer.partition(b"\n")
                    self.serial_buffer = bytearray(remaining)
                    line = raw.decode("utf-8", "replace").strip()
                    if line == "READY" or line == "PONG":
                        if not self.serial_ready:
                            self.serial_ready = True
                            self.port_text.set(f"ESP32 conectada · {self.port_choice.get()}")
                            self._log("ESP32 respondió correctamente.")
                            self.serial.write(b"STATUS\n")
                    elif line.startswith("STATE "):
                        value = line[6:]
                        action = "STOP" if value == "OFF" else value
                        if action in ACTION_NAMES:
                            if action != self.action:
                                self.action = action
                                self.action_start = time.monotonic()
                            self.action_text.set(ACTION_NAMES[action])
                    elif line.startswith("ERR "):
                        self._log(f"Respuesta ESP32: {line}")
        except Exception as exc:
            self._log(f"Se perdió la conexión: {exc}")
            self.disconnect()

    def toggle_camera(self) -> None:
        if self.camera_thread is not None and self.camera_thread.is_alive():
            self.camera_stop.set()
            self.camera_text.set("Deteniendo cámara…")
            self.camera_button.configure(state="disabled")
            return
        if not MODEL_PATH.is_file():
            self._log("Primero pulsa Descargar modelo.")
            return
        self.camera_stop = threading.Event()
        self.gate.reset()
        self.conflict_active = False
        self.camera_text.set("Abriendo cámara y cargando MediaPipe…")
        self.camera_button.configure(text="Detener cámara")
        self.camera_thread = threading.Thread(
            target=camera_worker,
            args=(int(self.camera_choice.get()), self.camera_stop, self.frames, self.events),
            daemon=True,
        )
        self.camera_thread.start()

    def toggle_automatic(self) -> None:
        self.gate.reset()
        self.conflict_active = False
        enabled = self.auto_enabled.get()
        self.mode_text.set("Control por gestos activo" if enabled else "Control por gestos en pausa; usa los botones")
        self._log("Control por gestos activado." if enabled else "Control por gestos pausado.")

    def send_action(self, action: str, *, manual: bool = False) -> None:
        if action not in ACTION_NAMES:
            return
        if manual and self.auto_enabled.get():
            self.auto_enabled.set(False)
            self.toggle_automatic()
        if self.serial is not None and self.serial_ready:
            try:
                self.serial.write((action + "\n").encode("ascii"))
                label = "ESP32"
            except Exception as exc:
                self._log(f"No se pudo enviar: {exc}")
                self.disconnect()
                return
        else:
            label = "vista previa local"
        if action != self.action or action.startswith("MODE "):
            self.action_start = time.monotonic()
        self.action = action
        self.action_text.set(ACTION_NAMES[action])
        self._log(f"{ACTION_NAMES[action]} · {label}")
        self._show_leds()

    def _show_leds(self) -> None:
        levels = preview_levels(self.action, time.monotonic() - self.action_start)
        for (canvas, oval, label), color, level in zip(self.leds, self.led_colors, levels):
            canvas.itemconfigure(oval, fill=color if level else "#39475a")
            label.configure(text=f"{level} %", fg=color if level else MUTED)

    def _process_frame(self, item: CameraFrame) -> None:
        from PIL import ImageOps

        image = self.Image.fromarray(item.image_rgb)
        image = ImageOps.contain(image, (640, 440))
        photo = self.ImageTk.PhotoImage(image)
        self.video_label.configure(image=photo, text="")
        self.photo = photo  # Conservar la referencia que Tk necesita.
        sides = [hand.side for hand in item.hands]
        ordered = sorted(item.hands, key=lambda hand: {"Left": 0, "Right": 1}.get(hand.side, 2))
        for index, (label, confidence) in enumerate(zip(self.hand_texts, self.hand_scores)):
            if index < len(ordered):
                hand = ordered[index]
                side = {"Left": "Mano izquierda", "Right": "Mano derecha"}.get(
                    hand.side, f"Mano {index + 1}")
                if sides.count(hand.side) > 1:
                    side = f"Mano {index + 1}"
                name = GESTURE_NAMES.get(hand.gesture, hand.gesture or "Sin gesto")
                label.set(f"{side}: {name}")
                confidence.set(f"Confianza: {hand.score:.0%}" if hand.gesture else "Confianza: —")
            else:
                label.set(f"Mano {index + 1}: no detectada")
                confidence.set("Confianza: —")
        if self.auto_enabled.get():
            gesture, score, conflict = select_control_gesture(item.hands, self.gate.min_score)
            if conflict:
                self.gate.pause_for_conflict()
                if not self.conflict_active:
                    self._log("Dos órdenes diferentes: esperando un gesto claro.")
                self.mode_text.set("Dos gestos distintos: no se envía una nueva orden")
                self.conflict_active = True
                return
            if self.conflict_active:
                self.mode_text.set("Control por gestos activo")
                self.conflict_active = False
            action = self.gate.observe(gesture, score, item.timestamp)
            if action:
                self.send_action(action)

    def _poll(self) -> None:
        if self.closed:
            return
        try:
            self._poll_serial()
            while True:
                try:
                    kind, detail = self.events.get_nowait()
                except queue.Empty:
                    break
                if kind == "model_ready":
                    self.model_text.set("Modelo listo")
                    self.model_button.configure(state="normal")
                    self._log("Modelo MediaPipe descargado correctamente.")
                elif kind == "model_error":
                    self.model_text.set("Descarga fallida")
                    self.model_button.configure(state="normal")
                    self._log(f"Error del modelo: {detail}")
                elif kind == "camera_ready":
                    self.camera_text.set(f"Cámara {detail} en directo · mano reflejada")
                    self._log(f"Cámara {detail} abierta.")
                elif kind == "camera_error":
                    self.camera_text.set("Error de cámara")
                    self._log(f"Error de cámara: {detail}")
                elif kind == "camera_stopped":
                    self.camera_text.set("Cámara detenida")
                    self.camera_button.configure(text="Iniciar cámara", state="normal")
                    self.gate.reset()
                    self.conflict_active = False
                    if self.auto_enabled.get():
                        self.mode_text.set("Control por gestos activo")
                    for n, (label, confidence) in enumerate(zip(self.hand_texts, self.hand_scores), 1):
                        label.set(f"Mano {n}: no detectada")
                        confidence.set("Confianza: —")
                    self.video_label.configure(image="", text="Activa la cámara para ver tu mano aquí")
                    self.photo = None
                    while not self.frames.empty():
                        try:
                            self.frames.get_nowait()
                        except queue.Empty:
                            break
            try:
                item = self.frames.get_nowait()
                if not self.camera_stop.is_set():
                    self._process_frame(item)
            except queue.Empty:
                pass
            self._show_leds()
        except Exception as exc:
            self._log(f"Error de interfaz: {exc}")
        finally:
            if not self.closed:
                self.root.after(50, self._poll)

    def close(self) -> None:
        self.camera_stop.set()
        if self.serial is not None:
            self.disconnect()
        self.closed = True
        self.root.destroy()


def main() -> None:
    try:
        root = tk.Tk()
        LightingApp(root)
        root.mainloop()
    except ImportError as exc:
        print(f"Falta {exc.name}. Ejecuta instalar_windows.bat y abre iniciar_windows.bat.", file=sys.stderr)
        raise SystemExit(1) from exc
    except tk.TclError as exc:
        print(f"No se pudo abrir la ventana gráfica: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
