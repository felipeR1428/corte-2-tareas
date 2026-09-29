"""Punto 2: cámara, CNN y cadena PC→ESP32-A→SPI→ESP32-B→OLED."""

from __future__ import annotations

import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
from collections import deque
from pathlib import Path
from tkinter import messagebox, ttk

import cv2
import serial
from serial.tools import list_ports
from PIL import Image, ImageTk

from protocolo_spi_serial import AckStream, encode_digit
from vision import orient_frame, process_roi


BASE = Path(__file__).resolve().parent
MODEL = BASE / "modelos" / "digitos_cnn.pt"


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title("Punto 2 | Cámara + CNN + ESP32 SPI + OLED")
        root.geometry("1200x845")
        root.minsize(1030, 765)
        self.port = None
        self.stream = AckStream()
        self.camera = None
        self.model = None
        self.torch = None
        self.training = None
        self.train_log = queue.Queue()
        self.closed = False
        self.photo_camera = None
        self.photo_digit = None
        self.history = deque(maxlen=5)
        self.sequence = 0
        self.pending_ack = {}
        self.last_camera = 0
        self.stable_digit = None
        self.auto_last = None
        self.port_name = tk.StringVar()
        self.camera_index = tk.StringVar(value="0")
        self.roi_ratio = tk.DoubleVar(value=0.63)
        self.mirror = tk.BooleanVar(value=False)
        self.polarity = tk.StringVar(value="Automático")
        self.auto = tk.BooleanVar(value=False)
        self.test_digit = tk.StringVar(value="0")
        self.serial_status = tk.StringVar(value="ESP32-A: desconectada · selecciona COM para usar OLED")
        self.esp_b_status = tk.StringVar(value="ESP32-B: sin confirmación")
        self.camera_status = tk.StringVar(value="Cámara apagada")
        self.model_status = tk.StringVar(value="CNN: sin cargar")
        self._build()
        self.polarity.trace_add("write", self.reset_reading)
        self.refresh_ports()
        self._log("Puedes probar OLED/SPI con el botón de prueba antes de entrenar.")
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.after(50, self.tick)

    def _build(self):
        top = ttk.Frame(self.root, padding=12)
        top.pack(fill="x")
        ttk.Label(top, text="PUNTO 2 · CÁMARA, CNN, SPI Y OLED",
                  font=("Segoe UI", 17, "bold")).pack(side="left")
        ttk.Label(top, text="COM de la ESP32-A:").pack(side="left", padx=(23, 4))
        self.ports = ttk.Combobox(top, textvariable=self.port_name, width=15, state="readonly")
        self.ports.pack(side="left")
        ttk.Button(top, text="↻", width=3, command=self.refresh_ports).pack(side="left", padx=4)
        self.connect_button = ttk.Button(top, text="Conectar", command=self.connect)
        self.connect_button.pack(side="left", padx=4)

        main = ttk.Frame(self.root, padding=(12, 3))
        main.pack(fill="both", expand=True)
        left = ttk.Frame(main)
        left.pack(side="left", fill="both", expand=True)
        ttk.Label(left, text="CÁMARA DEL PC / DÍGITO ESCRITO A MANO",
                  font=("Segoe UI", 13, "bold")).pack(anchor="w")
        self.view_camera = ttk.Label(left, text="Pulsa Iniciar cámara.", anchor="center")
        self.view_camera.pack(fill="both", expand=True, pady=8)
        ttk.Label(left, text="Un solo número oscuro en papel claro o en una pantalla. "
                  "Coloca el número completo dentro del recuadro; revisa el 28×28.",
                  wraplength=660).pack(anchor="w")

        right = ttk.Frame(main, width=355)
        right.pack(side="right", fill="y", padx=(15, 2))
        ttk.Label(right, text="RECONOCIMIENTO Y ENVÍO", font=("Segoe UI", 14, "bold")).pack(anchor="w")
        ttk.Label(right, textvariable=self.serial_status, wraplength=340).pack(anchor="w", pady=5)
        ttk.Label(right, textvariable=self.model_status, wraplength=340).pack(anchor="w", pady=5)
        ttk.Button(right, text="Entrenar CNN ampliada (300.000 imágenes)",
                   command=self.train).pack(fill="x", pady=3)
        ttk.Button(right, text="Cargar modelo guardado", command=self.load_model).pack(fill="x", pady=3)
        line = ttk.Frame(right)
        line.pack(fill="x", pady=(12, 3))
        ttk.Label(line, text="Índice de cámara:").pack(side="left")
        ttk.Combobox(line, textvariable=self.camera_index, values=("0", "1", "2", "3"),
                     width=5, state="readonly").pack(side="left", padx=7)
        self.camera_button = ttk.Button(right, text="Iniciar cámara", command=self.toggle_camera)
        self.camera_button.pack(fill="x", pady=4)
        self.mirror_button = ttk.Button(right, text="Invertir imagen ↔  |  Espejo: NO",
                                        command=self.toggle_mirror)
        self.mirror_button.pack(fill="x", pady=4)
        ttk.Label(right, text="Invierte izquierda/derecha en la vista y en la CNN.",
                  wraplength=340).pack(anchor="w")
        ttk.Label(right, text="Tamaño de zona de lectura:").pack(anchor="w", pady=(8, 0))
        ttk.Scale(right, from_=0.4, to=0.85, variable=self.roi_ratio,
                  orient="horizontal").pack(fill="x")
        ttk.Label(right, text="Color del número (Automático recomendado):").pack(anchor="w")
        ttk.Combobox(right, textvariable=self.polarity,
                     values=("Automático", "Oscuro sobre claro", "Claro sobre oscuro"),
                     state="readonly", width=27).pack(fill="x", pady=(0, 3))
        ttk.Label(right, text="Vista del dígito procesado (28×28):").pack(anchor="w", pady=(10, 3))
        self.view_digit = ttk.Label(right, text="—")
        self.view_digit.pack(anchor="w")
        ttk.Label(right, textvariable=self.camera_status, wraplength=335,
                  font=("Segoe UI", 11, "bold")).pack(anchor="w", pady=9)
        ttk.Button(right, text="Enviar reconocido → ESP-A → SPI → OLED",
                   command=self.send_recognized).pack(fill="x", pady=3)
        ttk.Checkbutton(right, text="Enviar automáticamente al estabilizar",
                        variable=self.auto).pack(anchor="w", pady=4)
        trial = ttk.Frame(right)
        trial.pack(fill="x", pady=(11, 5))
        ttk.Label(trial, text="Prueba sin CNN:").pack(side="left")
        ttk.Combobox(trial, textvariable=self.test_digit, values=tuple("0123456789"),
                     width=4, state="readonly").pack(side="left", padx=6)
        ttk.Button(trial, text="Enviar prueba", command=self.send_test).pack(side="left")
        ttk.Label(right, text="La OLED muestra CNN o PRUEBA según el origen.\n"
                  "El estado OK llega de ESP32-B por MISO.", wraplength=345,
                  justify="left").pack(anchor="w", pady=5)

        footer = ttk.Frame(self.root, padding=(12, 6))
        footer.pack(fill="x")
        ttk.Label(footer, textvariable=self.esp_b_status,
                  font=("Segoe UI", 11, "bold")).pack(anchor="w")
        self.logs = tk.Text(footer, height=5, state="disabled", font=("Consolas", 9))
        self.logs.pack(fill="x", pady=(3, 0))

    def _log(self, message):
        if self.closed:
            return
        self.logs.configure(state="normal")
        self.logs.insert("end", f"{time.strftime('%H:%M:%S')}  {message}\n")
        if int(self.logs.index("end-1c").split(".")[0]) > 220:
            self.logs.delete("1.0", "65.0")
        self.logs.see("end")
        self.logs.configure(state="disabled")

    def refresh_ports(self):
        names = [item.device for item in list_ports.comports()]
        self.ports["values"] = names
        if self.port_name.get() not in names:
            self.port_name.set(names[0] if names else "")
        if not names and not self.port:
            self.serial_status.set("ESP32-A: sin puerto COM · conecta USB y cierra Thonny")
        elif names and not self.port:
            self.serial_status.set("ESP32-A: desconectada · selecciona COM y pulsa Conectar")

    def disconnect(self):
        if self.port:
            self.port.close()
            self.port = None
        self.pending_ack.clear()
        self.connect_button.configure(text="Conectar")
        self.serial_status.set("ESP32-A: desconectada · selecciona COM para usar OLED")

    def connect(self):
        if self.port:
            self.disconnect()
            return
        if not self.port_name.get():
            self.refresh_ports()
            if not self.port_name.get():
                messagebox.showerror("Puerto COM", "No aparece la ESP32-A. Conecta el USB, "
                                     "cierra Thonny y pulsa ↻ para actualizar los puertos.")
                return
        try:
            self.port = serial.Serial(self.port_name.get(), 115200,
                                      timeout=0, write_timeout=0.5)
        except (OSError, ValueError, serial.SerialException) as exc:
            messagebox.showerror("Puerto COM", f"No pude abrir la ESP32-A: {exc}\nCierra Thonny.")
            return
        self.connect_button.configure(text="Desconectar")
        self.serial_status.set("ESP32-A: conectada (115200)")
        self._log("Puerto abierto. ESP32-B debe estar conectada y alimentada.")

    def load_model(self):
        if not MODEL.is_file():
            self.model_status.set("CNN aún sin entrenar: pulsa Entrenar CNN.")
            return False
        try:
            import torch
            from cnn_model import load_model
            torch.set_num_threads(2)
            self.model = load_model(MODEL)
            self.torch = torch
        except Exception as exc:
            self.model = None
            self.model_status.set(f"Error al cargar CNN: {exc}")
            self._log(f"Error CNN: {exc}")
            return False
        if "EMNIST Digits" in self.model.training_sources:
            self.model_status.set("CNN ampliada: MNIST + EMNIST Digits (300.000 muestras)")
        else:
            self.model_status.set("CNN antigua (solo MNIST): pulsa Entrenar CNN ampliada.")
        self._log("CNN lista.")
        return True

    def train(self):
        if self.training and self.training.poll() is None:
            self._log("El entrenamiento sigue en curso.")
            return
        try:
            self.training = subprocess.Popen(
                [sys.executable, "-u", str(BASE / "entrenar_cnn.py"), "--epochs", "5"],
                cwd=BASE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1,
            )
        except OSError as exc:
            messagebox.showerror("Entrenamiento", str(exc))
            return
        self.model_status.set("CNN entrenando; primera vez descarga MNIST y EMNIST...")
        process = self.training

        def read_output():
            for line in process.stdout:
                self.train_log.put(line.strip())
            self.train_log.put(("DONE", process.wait()))

        threading.Thread(target=read_output, daemon=True).start()

    def toggle_mirror(self):
        mirrored = not self.mirror.get()
        self.mirror.set(mirrored)
        self.mirror_button.configure(text=f"Invertir imagen ↔  |  Espejo: {'SÍ' if mirrored else 'NO'}")
        self.reset_reading()
        self.camera_status.set("Orientación cambiada; espera una nueva predicción.")
        self._log(f"Espejo horizontal {'activado' if mirrored else 'desactivado'}.")

    def reset_reading(self, *_):
        self.history.clear()
        self.stable_digit = None
        self.auto_last = None
        self.photo_digit = None
        self.view_digit.configure(image="", text="Esperando nueva lectura")

    def toggle_camera(self):
        if self.camera is not None:
            self.camera.release()
            self.camera = None
            self.camera_button.configure(text="Iniciar cámara")
            self.camera_status.set("Cámara apagada")
            self.history.clear()
            self.stable_digit = None
            self.auto_last = None
            return
        index = int(self.camera_index.get())
        cap = cv2.VideoCapture(index, cv2.CAP_DSHOW) if sys.platform == "win32" else cv2.VideoCapture(index)
        if not cap.isOpened():
            cap.release()
            messagebox.showerror("Cámara", "No pude abrir la cámara. Cambia el índice o cierra otras aplicaciones.")
            return
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        self.camera = cap
        self.camera_button.configure(text="Detener cámara")
        if self.model is None:
            self.load_model()
        self._log(f"Cámara {index} activa.")

    def send_digit(self, digit: str, source: str):
        if not self.port:
            self._log("Conecta la ESP32-A antes de enviar el dígito.")
            return
        sequence = self.sequence
        self.sequence = (self.sequence + 1) % 65536
        try:
            self.port.write(encode_digit(sequence, digit, source))
            self.pending_ack[sequence] = (digit, time.monotonic())
            self.esp_b_status.set(f"ESP32-B: esperando {digit}...")
            self._log(f"Enviado {digit} ({'CNN' if source == 'V' else 'PRUEBA'}), secuencia {sequence}")
        except (OSError, serial.SerialException) as exc:
            self._log(f"Error de envío serial: {exc}")

    def send_test(self):
        self.send_digit(self.test_digit.get(), "T")

    def send_recognized(self):
        if self.stable_digit is None:
            self._log("Todavía no hay dígito estable en el recuadro.")
            return
        self.send_digit(str(self.stable_digit), "V")

    def serial_tick(self):
        if self.port:
            try:
                data = self.port.read(min(512, self.port.in_waiting or 1))
                for ack in self.stream.feed(data):
                    expected = self.pending_ack.get(ack.sequence)
                    if expected and expected[0] == ack.digit:
                        self.pending_ack.pop(ack.sequence)
                        self.esp_b_status.set(f"ESP32-B: {ack.digit} · {ack.status}")
                        self._log(f"ACK de ESP32-B: {ack.digit} → {ack.status}")
            except (OSError, serial.SerialException) as exc:
                self._log(f"Puerto desconectado: {exc}")
                self.disconnect()
        now = time.monotonic()
        for sequence, (digit, sent_at) in list(self.pending_ack.items()):
            if now - sent_at > 3:
                self.pending_ack.pop(sequence)
                self.esp_b_status.set(f"ESP32-B: sin confirmación de {digit}")
                self._log(f"Sin ACK para secuencia {sequence}; revisa SPI, READY y alimentación.")

    def camera_tick(self):
        if self.camera is None:
            return
        ok, frame = self.camera.read()
        if not ok:
            self.camera_status.set("No se recibió imagen de la cámara.")
            return
        frame = orient_frame(frame, self.mirror.get())
        height, width = frame.shape[:2]
        side = min(round(min(height, width) * self.roi_ratio.get()), width - 10, height - 10)
        x1, y1 = (width - side) // 2, (height - side) // 2
        modes = {"Automático": "auto", "Oscuro sobre claro": "dark",
                 "Claro sobre oscuro": "light"}
        output = process_roi(frame[y1:y1 + side, x1:x1 + side],
                             modes[self.polarity.get()])
        color = (0, 190, 0) if output is not None else (0, 160, 255)
        if output is not None:
            image, (x, y, w, h), preview = output
            cv2.rectangle(frame, (x1 + x, y1 + y), (x1 + x + w, y1 + y + h),
                          (0, 240, 0), 2)
            enlarged = cv2.resize(preview, (140, 140), interpolation=cv2.INTER_NEAREST)
            self.photo_digit = ImageTk.PhotoImage(Image.fromarray(enlarged))
            self.view_digit.configure(image=self.photo_digit, text="")
            if self.model is not None:
                with self.torch.inference_mode():
                    tensor = self.torch.from_numpy(image).unsqueeze(0).unsqueeze(0)
                    probabilities = self.model(tensor).softmax(dim=1)
                    confidence, prediction = probabilities.max(dim=1)
                digit, confidence = int(prediction.item()), float(confidence.item())
                self.history.append(digit if confidence >= 0.80 else None)
                stable = digit if self.history.count(digit) >= 4 else None
                self.stable_digit = stable
                if stable is not None:
                    description = f"Reconocido: {stable} · confianza {confidence:.0%}"
                elif confidence < 0.80:
                    description = (f"Lectura dudosa ({confidence:.0%}): centra el número "
                                   "y evita reflejos. No se enviará.")
                else:
                    description = f"Posible {digit} ({confidence:.0%}); espera estabilidad"
                self.camera_status.set(description)
                label = (f"RECONOCIDO: {stable}  {confidence:.0%}" if stable is not None
                         else f"LEYENDO: {confidence:.0%}" if confidence >= 0.80
                         else f"LECTURA DUDOSA: {confidence:.0%}")
                cv2.rectangle(frame, (0, height - 38), (width, height), (25, 25, 25), -1)
                cv2.putText(frame, label, (12, height - 13),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.65, (255, 255, 255), 2)
                if stable is not None and self.auto.get() and self.port and self.auto_last != stable:
                    self.send_digit(str(stable), "V")
                    self.auto_last = stable
            else:
                self.camera_status.set("Cámara activa. Entrena/carga la CNN para reconocer.")
        else:
            self.history.clear()
            self.stable_digit = None
            self.auto_last = None
            self.view_digit.configure(image="", text="Sin dígito")
            self.camera_status.set("No hay un dígito aislado: acerca el número, "
                                   "reduce reflejos o usa papel liso.")
        cv2.rectangle(frame, (x1, y1), (x1 + side, y1 + side), color, 2)
        cv2.putText(frame, "ESPEJO: SI" if self.mirror.get() else "ESPEJO: NO",
                    (12, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 220, 255), 2)
        rgb = cv2.cvtColor(cv2.resize(frame, (640, 480)), cv2.COLOR_BGR2RGB)
        self.photo_camera = ImageTk.PhotoImage(Image.fromarray(rgb))
        self.view_camera.configure(image=self.photo_camera, text="")

    def tick(self):
        if self.closed:
            return
        self.serial_tick()
        try:
            while True:
                message = self.train_log.get_nowait()
                if isinstance(message, tuple) and message[0] == "DONE":
                    if message[1] == 0:
                        self.load_model()
                    else:
                        self.model_status.set("Falló el entrenamiento; revisa los eventos.")
                    break
                self._log(message)
                if message.startswith("Epoca"):
                    self.model_status.set(message)
        except queue.Empty:
            pass
        if time.monotonic() - self.last_camera >= 0.13:
            try:
                self.camera_tick()
            except Exception as exc:
                self.camera_status.set(f"Cámara/CNN: {exc}")
            self.last_camera = time.monotonic()
        self.root.after(50, self.tick)

    def close(self):
        self.closed = True
        if self.camera is not None:
            self.camera.release()
        if self.port:
            self.port.close()
        if self.training and self.training.poll() is None:
            self.training.terminate()
        self.root.destroy()


if __name__ == "__main__":
    window = tk.Tk()
    try:
        App(window)
        window.mainloop()
    except Exception:
        window.destroy()
        raise
