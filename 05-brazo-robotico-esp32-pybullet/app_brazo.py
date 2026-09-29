"""Panel del brazo URDF: UART de la ESP32, simulación 3D y controles manuales."""

from __future__ import annotations

import csv
import math
import time
import tkinter as tk
from collections import deque
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, ttk

import serial
from serial.tools import list_ports
from PIL import Image, ImageTk

from protocol import PacketStream, SensorPacket, positions_from_adc
from robot_sim import RobotScene


BASE = Path(__file__).resolve().parent
BG = "#101827"
CARD = "#192638"
INK = "#f2f6fa"
MUTED = "#b4c5d8"
ACCENT = "#64dac1"
CONTROLS = (
    ("GPIO32 · Giro de la base", "joint_1", "°"),
    ("GPIO33 · Giro del brazo", "joint_2", "°"),
    ("GPIO34 · Deslizamiento de pinza", "joint_gripper", "mm"),
    ("GPIO35 · Apertura de dedos", "joint_dedo_izq", "mm"),
)


class ArmApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Brazo robótico · ESP32 UART y PyBullet")
        self.root.geometry("1350x880")
        self.root.minsize(1240, 780)
        self.root.configure(bg=BG)
        self.scene = RobotScene(BASE / "brazo.urdf")
        self.port = None
        self.packet_stream = PacketStream()
        self.mode = "MANUAL"
        self.last_packet_time = 0.0
        self.last_sequence = None
        self.last_frame_time = time.monotonic()
        self.demo_time = 0.0
        self.demo_sequence = 0
        self.received = 0
        self.lost = 0
        self.times = deque(maxlen=40)
        self.record_file = None
        self.record_writer = None
        self.record_path = None
        self.last_image = None
        self.photo = None
        self.render_failed = False
        self.closed = False

        self.port_choice = tk.StringVar()
        self.mode_text = tk.StringVar(value="MANUAL · ajusta los controles")
        self.status_text = tk.StringVar(value="Sin ESP32 conectada")
        self.telemetry_text = tk.StringVar(value="UART: 0 tramas · 0 erróneas · 0 perdidas")
        self.freq_text = tk.StringVar(value="Frecuencia: —   Última trama: —")
        self.sensor_values = [tk.DoubleVar(value=50) for _ in CONTROLS]
        self.sensor_texts = [tk.StringVar(value="") for _ in CONTROLS]
        self._build()
        self.refresh_ports()
        self._set_target_from_controls()
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.after(50, self._tick)

    def _label(self, parent, content=None, *, variable=None, size=11, color=INK,
               bold=False, background=CARD, **kwargs):
        return tk.Label(parent, text=content, textvariable=variable, bg=background,
                        fg=color, font=("Segoe UI", size, "bold" if bold else "normal"),
                        **kwargs)

    def _button(self, parent, label, command, *, background="#2d4962"):
        return tk.Button(parent, text=label, command=command, bg=background, fg=INK,
                         activebackground=ACCENT, activeforeground="#102028", bd=0,
                         cursor="hand2", padx=12, pady=8,
                         font=("Segoe UI", 10, "bold"))

    def _build(self):
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure("Brazo.TCombobox", fieldbackground="#203249", background=CARD,
                        foreground=INK, arrowcolor=INK)
        style.configure("Brazo.Horizontal.TScale", background=CARD,
                        troughcolor="#314962")

        header = tk.Frame(self.root, bg=BG)
        header.pack(fill="x", padx=24, pady=(16, 10))
        self._label(header, "BRAZO ROBÓTICO  /  UART EN TIEMPO REAL", size=21,
                    bold=True, background=BG).pack(anchor="w")
        self._label(header, "URDF original · ESP32 con cuatro sensores · Python 3.14",
                    color=MUTED, background=BG).pack(anchor="w")

        connection = tk.Frame(self.root, bg=CARD, padx=14, pady=12)
        connection.pack(fill="x", padx=22, pady=(0, 12))
        self._label(connection, "PUERTO COM", size=9, color=MUTED).pack(side="left", padx=(0, 8))
        self.ports = ttk.Combobox(connection, textvariable=self.port_choice, width=14,
                                  state="readonly", style="Brazo.TCombobox")
        self.ports.pack(side="left", padx=3)
        self._button(connection, "↻", self.refresh_ports).pack(side="left", padx=3)
        self.connect_button = self._button(connection, "Conectar ESP32", self.toggle_connection,
                                           background="#21745c")
        self.connect_button.pack(side="left", padx=7)
        self._button(connection, "Control manual", self.manual_mode).pack(side="left", padx=7)
        self._button(connection, "Demostración", self.demo_mode).pack(side="left", padx=7)
        self._label(connection, "115200 baudios", color=MUTED).pack(side="right")

        body = tk.Frame(self.root, bg=BG)
        body.pack(fill="both", expand=True, padx=22)
        left = tk.Frame(body, bg=CARD, padx=12, pady=12)
        left.pack(side="left", fill="both", expand=True, padx=(0, 12))
        self._label(left, "VISTA 3D DEL ROBOT · PyBullet", size=12, bold=True).pack(anchor="w")
        view = tk.Frame(left, bg="#111c2c", width=720, height=490)
        view.pack(fill="both", expand=True, pady=(9, 12))
        view.pack_propagate(False)
        self.visual = tk.Label(view, bg="#111c2c", text="Cargando modelo URDF…", fg=INK)
        self.visual.pack(fill="both", expand=True)
        orbit = tk.Frame(left, bg=CARD)
        orbit.pack(fill="x")
        for label, args in (("↶ Girar", (15, 0, 0)), ("Girar ↷", (-15, 0, 0)),
                            ("Inclinar ↑", (0, 10, 0)), ("Inclinar ↓", (0, -10, 0)),
                            ("Acercar", (0, 0, -0.15)), ("Alejar", (0, 0, 0.15))):
            self._button(orbit, label,
                         lambda values=args: self.scene.camera_move(*values)).pack(
                             side="left", expand=True, fill="x", padx=2)
        tools = tk.Frame(left, bg=CARD)
        tools.pack(fill="x", pady=(10, 0))
        self._button(tools, "Guardar captura 3D", self.save_capture,
                     background="#525693").pack(side="left", padx=(0, 8))
        self.record_button = self._button(tools, "Grabar CSV", self.toggle_record,
                                           background="#705076")
        self.record_button.pack(side="left")
        self._label(tools, "La demostración está marcada como DEMO",
                    color=MUTED, size=9).pack(side="right")

        right = tk.Frame(body, bg=CARD, padx=18, pady=13, width=440)
        right.pack(side="right", fill="y")
        right.pack_propagate(False)
        self._label(right, "ESTADO", size=10, color=MUTED, bold=True).pack(anchor="w")
        self._label(right, variable=self.mode_text, size=14, color=ACCENT,
                    bold=True, wraplength=400, justify="left").pack(anchor="w", pady=(6, 2))
        self._label(right, variable=self.status_text, color=MUTED,
                    wraplength=400, justify="left").pack(anchor="w")
        self._label(right, variable=self.telemetry_text, size=9, color=MUTED,
                    wraplength=400, justify="left").pack(anchor="w", pady=(10, 0))
        self._label(right, variable=self.freq_text, size=9, color=MUTED).pack(anchor="w")
        tk.Frame(right, bg="#344e65", height=1).pack(fill="x", pady=12)
        self._label(right, "SENSORES / ARTICULACIONES", size=10,
                    color=MUTED, bold=True).pack(anchor="w")
        self._label(right, "En MANUAL puedes arrastrar las barras; en UART son lecturas reales.",
                    size=9, color=MUTED, wraplength=390,
                    justify="left").pack(anchor="w", pady=(4, 12))
        self.sliders = []
        for index, (description, _, _) in enumerate(CONTROLS):
            row = tk.Frame(right, bg=CARD)
            row.pack(fill="x", pady=(3, 9))
            self._label(row, description, bold=True, size=10).pack(anchor="w")
            slide = ttk.Scale(row, from_=0, to=100, variable=self.sensor_values,
                              style="Brazo.Horizontal.TScale",
                              command=lambda _value, i=index: self._on_slider(i))
            slide.pack(fill="x", pady=(4, 0))
            self.sliders.append(slide)
            self._label(row, variable=self.sensor_texts[index], size=9,
                        color=MUTED).pack(anchor="w")
        self._label(right, "GPIO34 mueve la base de la pinza; GPIO35 abre los dos dedos.",
                    size=9, color=MUTED, wraplength=400,
                    justify="left").pack(anchor="w", pady=(8, 0))
        self._label(right, "Los dedos siguen el mismo sensor para abrir simétricamente.",
                    size=9, color=MUTED, wraplength=400,
                    justify="left").pack(anchor="w")

        log_panel = tk.Frame(self.root, bg=CARD, padx=14, pady=9)
        log_panel.pack(fill="x", padx=22, pady=(12, 16))
        self._label(log_panel, "EVENTOS", color=MUTED, bold=True, size=9).pack(anchor="w")
        self.logs = tk.Text(log_panel, height=4, bg="#101a29", fg=INK,
                            font=("Consolas", 9), bd=0, state="disabled")
        self.logs.pack(fill="x", pady=(4, 0))
        self._log("URDF cargado en PyBullet. Ajusta las barras o conecta la ESP32.")

    def _log(self, message: str):
        if self.closed:
            return
        self.logs.configure(state="normal")
        self.logs.insert("end", f"{time.strftime('%H:%M:%S')}  {message}\n")
        count = int(self.logs.index("end-1c").split(".")[0])
        if count > 140:
            self.logs.delete("1.0", f"{count - 100}.0")
        self.logs.see("end")
        self.logs.configure(state="disabled")

    def refresh_ports(self):
        ports = [port.device for port in list_ports.comports()]
        self.ports.configure(values=ports)
        if self.port_choice.get() not in ports:
            self.port_choice.set(ports[0] if ports else "")
        self._log("Puertos detectados: " + (", ".join(ports) if ports else "ninguno"))

    def _close_port(self):
        if self.port is not None:
            try:
                self.port.close()
            except Exception:
                pass
        self.port = None
        self.connect_button.configure(text="Conectar ESP32", bg="#21745c")

    def toggle_connection(self):
        if self.port is not None:
            self._close_port()
            self.manual_mode()
            return
        if not self.port_choice.get():
            self._log("Conecta la ESP32 por USB y pulsa ↻ para elegir el COM.")
            return
        try:
            self.port = serial.Serial(self.port_choice.get(), 115200, timeout=0)
        except serial.SerialException as exc:
            self._log(f"No se puede abrir el COM: {exc}. Cierra Thonny.")
            self.status_text.set("No se pudo abrir el puerto")
            return
        self.packet_stream = PacketStream()
        self.last_sequence = None
        self.last_packet_time = time.monotonic()
        self.received = 0
        self.lost = 0
        self.times.clear()
        self.mode = "UART"
        self.mode_text.set("UART · control por sensores ESP32")
        self.status_text.set(f"{self.port.port} abierto · esperando primeras lecturas")
        self.connect_button.configure(text="Desconectar", bg="#934558")
        self._log("ESP32 conectada. Esperando tramas @ARM desde main.py.")

    def manual_mode(self):
        self.mode = "MANUAL"
        self.mode_text.set("MANUAL · ajusta los controles")
        self.status_text.set("ESP32 conectada (sin controlar el robot)" if self.port
                             else "Sin ESP32 conectada")
        self._set_target_from_controls()
        self._log("Control manual activado.")

    def demo_mode(self):
        self._close_port()
        self.mode = "DEMO"
        self.demo_time = 0.0
        self.demo_sequence = 0
        self.received = 0
        self.lost = 0
        self.times.clear()
        self.mode_text.set("DEMO · datos simulados, sin ESP32")
        self.status_text.set("Demostración local: no valida la conexión UART")
        self._log("Demostración activada; los datos no provienen de sensores físicos.")

    def _on_slider(self, _index):
        if self.mode == "MANUAL":
            self._set_target_from_controls()

    def _set_target_from_controls(self):
        adc = tuple(max(0, min(65535, round(var.get() * 655.35)))
                    for var in self.sensor_values)
        self.scene.set_targets(positions_from_adc(adc))
        self._update_control_text(adc)

    def _update_control_text(self, adc):
        targets = positions_from_adc(adc)
        for index, (_, joint, units) in enumerate(CONTROLS):
            position = targets[joint]
            display = f"{math.degrees(position):+.1f}°" if units == "°" else f"{position * 1000:.1f} mm"
            self.sensor_texts[index].set(
                f"{adc[index] / 65535:.0%} del recorrido · ADC {adc[index]} · articulación {display}")

    def _receive(self):
        if self.port is None:
            return
        try:
            available = self.port.in_waiting
            if available:
                for packet in self.packet_stream.feed(self.port.read(min(available, 2048))):
                    self._apply_packet(packet, source="UART")
        except (OSError, serial.SerialException) as exc:
            self._log(f"Se perdió el puerto: {exc}")
            self._close_port()
            self.manual_mode()

    def _apply_packet(self, packet: SensorPacket, source: str):
        now = time.monotonic()
        if source == "UART":
            if self.last_sequence is not None:
                gap = (packet.sequence - self.last_sequence - 1) % 65536
                if gap <= 100:
                    self.lost += gap
            self.last_sequence = packet.sequence
            self.last_packet_time = now
            if self.mode == "UART":
                self.status_text.set(f"{self.port.port} · recibiendo sensores en directo")
        self.received += 1
        self.times.append(now)
        if self.mode == source:
            self.scene.set_targets(positions_from_adc(packet.values))
            for index, value in enumerate(packet.values):
                self.sensor_values[index].set(value * 100 / 65535)
            self._update_control_text(packet.values)
        self.telemetry_text.set(
            f"{source}: {self.received} tramas · {self.packet_stream.bad} erróneas · {self.lost} perdidas")
        if self.record_writer is not None:
            values = packet.values
            joints = positions_from_adc(values)
            self.record_writer.writerow([datetime.now().isoformat(timespec="milliseconds"),
                                         source, packet.sequence, packet.ticks_ms, *values,
                                         *(f"{joints[name]:.5f}" for name in
                                           ("joint_1", "joint_2", "joint_gripper",
                                            "joint_dedo_izq", "joint_dedo_der"))])
            self.record_file.flush()

    def _tick(self):
        if self.closed:
            return
        try:
            now = time.monotonic()
            elapsed = now - self.last_frame_time
            self.last_frame_time = now
            self._receive()
            if self.mode == "DEMO" and now - self.demo_time >= 0.05:
                phase = now
                values = tuple(round(32768 + 30000 * math.sin(phase * speed + offset))
                               for speed, offset in ((0.65, 0), (0.45, 1),
                                                     (0.85, 2), (1.1, 3)))
                self._apply_packet(SensorPacket(self.demo_sequence, int(now * 1000), values),
                                   source="DEMO")
                self.demo_sequence = (self.demo_sequence + 1) % 65536
                self.demo_time = now
            if self.mode == "UART" and now - self.last_packet_time > 1.2:
                self.status_text.set("Sin nuevas tramas UART: revisa la ESP32 y cierra Thonny")
            if self.times:
                rate = (len(self.times) - 1) / (self.times[-1] - self.times[0]) \
                    if len(self.times) >= 2 and self.times[-1] > self.times[0] else 0
                age = int((now - self.times[-1]) * 1000)
                self.freq_text.set(f"Frecuencia: {rate:.1f} Hz   Última trama: hace {age} ms")
            self.scene.advance(elapsed)
            if not self.render_failed:
                try:
                    frame = self.scene.render()
                    self.last_image = Image.fromarray(frame).resize(
                        (720, 490), Image.Resampling.BILINEAR
                    )
                    self.photo = ImageTk.PhotoImage(self.last_image)
                    self.visual.configure(image=self.photo, text="")
                except Exception as exc:
                    self.render_failed = True
                    self._log(f"No se pudo dibujar el modelo 3D: {exc}")
                    self.visual.configure(text="Error de visualización 3D; revisa el controlador de video")
        except Exception as exc:
            self._log(f"Error de interfaz: {exc}")
        finally:
            if not self.closed:
                self.root.after(50, self._tick)

    def save_capture(self):
        if self.last_image is None:
            self._log("Espera a que aparezca el modelo 3D antes de guardar una captura.")
            return
        folder = BASE / "capturas"
        folder.mkdir(exist_ok=True)
        filename = folder / ("vista_3d_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f") + ".png")
        self.last_image.save(filename)
        self._log(f"Captura 3D guardada: {filename.name} ({self.mode}).")

    def toggle_record(self):
        if self.record_file is not None:
            self.record_file.close()
            self.record_file = None
            self.record_writer = None
            self.record_button.configure(text="Grabar CSV")
            self._log(f"Registro guardado en {self.record_path}.")
            return
        folder = BASE / "registros"
        folder.mkdir(exist_ok=True)
        self.record_path = folder / ("uart_brazo_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f") + ".csv")
        self.record_file = self.record_path.open("w", newline="", encoding="utf-8")
        self.record_writer = csv.writer(self.record_file)
        self.record_writer.writerow(("hora_pc", "origen", "secuencia", "ticks_esp_ms",
                                     "gpio32", "gpio33", "gpio34", "gpio35",
                                     "joint_1_rad", "joint_2_rad", "joint_gripper_m",
                                     "dedo_izq_m", "dedo_der_m"))
        self.record_button.configure(text="Detener CSV")
        self._log("Grabación iniciada. Cada fila indica UART o DEMO.")

    def close(self):
        if self.closed:
            return
        self._close_port()
        if self.record_file is not None:
            self.record_file.close()
        self.closed = True
        self.scene.close()
        self.root.destroy()


def main():
    root = tk.Tk()
    try:
        ArmApp(root)
    except Exception as exc:
        messagebox.showerror("No se pudo iniciar", str(exc), parent=root)
        root.destroy()
        return
    root.mainloop()


if __name__ == "__main__":
    main()
