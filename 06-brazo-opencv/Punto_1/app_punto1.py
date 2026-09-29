"""Punto 1: teclado de ocho pines, LCD I2C y brazo PyBullet que dibuja."""

from __future__ import annotations

import time
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, ttk

import serial
from serial.tools import list_ports
from PIL import Image, ImageTk

from draw_robot import DrawingRobot
from teclado_serial import KeyStream


BASE = Path(__file__).resolve().parent


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title("Punto 1 | Teclado 8 pines + LCD I²C + brazo PyBullet")
        root.geometry("1150x740")
        root.minsize(950, 660)
        self.robot = DrawingRobot(BASE / "brazo.urdf")
        self.stream = KeyStream()
        self.port = None
        self.closed = False
        self.photo = None
        self.last_render = 0.0
        self.last_drawn = None
        self.port_name = tk.StringVar()
        self.link_status = tk.StringVar(value="ESP32 teclado: desconectada")
        self.key_status = tk.StringVar(value="Última tecla: —")
        self.robot_status = tk.StringVar(value="Brazo listo: modelo URDF original")
        self._build()
        self.refresh_ports()
        self._log("Prueba 0–9 en la interfaz o conecta el teclado físico.")
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.after(50, self.tick)

    def _build(self):
        top = ttk.Frame(self.root, padding=12)
        top.pack(fill="x")
        ttk.Label(top, text="PUNTO 1 · TECLADO 8 PINES, LCD I²C Y BRAZO PYBULLET",
                  font=("Segoe UI", 17, "bold")).pack(side="left")
        ttk.Label(top, text="COM de la ESP32:").pack(side="left", padx=(28, 5))
        self.ports = ttk.Combobox(top, textvariable=self.port_name, width=14, state="readonly")
        self.ports.pack(side="left")
        ttk.Button(top, text="↻", width=3, command=self.refresh_ports).pack(side="left", padx=4)
        self.connect_button = ttk.Button(top, text="Conectar", command=self.connect)
        self.connect_button.pack(side="left", padx=4)

        main = ttk.Frame(self.root, padding=(12, 2))
        main.pack(fill="both", expand=True)
        left = ttk.Frame(main)
        left.pack(side="left", fill="both", expand=True)
        ttk.Label(left, text="Brazo en PyBullet: cifras curvas sobre un plano y pinza retirada al finalizar.",
                  font=("Segoe UI", 12, "bold")).pack(anchor="w")
        self.view = ttk.Label(left, text="Cargando vista PyBullet...", anchor="center")
        self.view.pack(fill="both", expand=True, pady=8)
        controls = ttk.Frame(left)
        controls.pack(fill="x", pady=4)
        for label, yaw, zoom in (("↶ Girar", -15, 0), ("Girar ↷", 15, 0),
                                 ("Acercar", 0, -0.1), ("Alejar", 0, 0.1)):
            ttk.Button(controls, text=label,
                       command=lambda a=yaw, b=zoom: self.move_camera(a, b)).pack(side="left", padx=3)
        ttk.Button(controls, text="Guardar imagen", command=self.capture).pack(side="left", padx=5)

        right = ttk.Frame(main, width=320)
        right.pack(side="right", fill="y", padx=(15, 3))
        ttk.Label(right, text="TECLADO 4×4", font=("Segoe UI", 16, "bold")).pack(anchor="w", pady=8)
        ttk.Label(right, text="Teclado conectado a ocho GPIO de la ESP32.\n"
                  "La LCD 16×2 usa I²C (GPIO21/22).",
                  justify="left").pack(anchor="w")
        ttk.Label(right, textvariable=self.link_status, wraplength=305).pack(anchor="w", pady=(12, 4))
        ttk.Label(right, textvariable=self.key_status, font=("Segoe UI", 11, "bold")).pack(anchor="w")
        keys = ttk.Frame(right)
        keys.pack(anchor="w", pady=(12, 3))
        for row, line in enumerate(("123A", "456B", "789C", "*0#D")):
            for col, key in enumerate(line):
                ttk.Button(keys, text=key, width=6,
                           command=lambda value=key: self.keypress(value, "Interfaz")).grid(
                               row=row, column=col, padx=3, pady=4, ipadx=2, ipady=8)
        ttk.Label(right, text="0–9: dibujar cifra\n*: borrar\n#: repetir última cifra\n"
                  "A/B: girar · C/D: acercar/alejar", justify="left").pack(anchor="w", pady=10)
        ttk.Label(right, textvariable=self.robot_status, wraplength=305, justify="left").pack(anchor="w")

        footer = ttk.Frame(self.root, padding=(12, 4))
        footer.pack(fill="x")
        self.logs = tk.Text(footer, height=5, state="disabled", font=("Consolas", 9))
        self.logs.pack(fill="x")

    def _log(self, message):
        if self.closed:
            return
        self.logs.configure(state="normal")
        self.logs.insert("end", f"{time.strftime('%H:%M:%S')}  {message}\n")
        if int(self.logs.index("end-1c").split(".")[0]) > 180:
            self.logs.delete("1.0", "50.0")
        self.logs.see("end")
        self.logs.configure(state="disabled")

    def refresh_ports(self):
        names = [item.device for item in list_ports.comports()]
        self.ports["values"] = names
        if self.port_name.get() not in names:
            self.port_name.set(names[0] if names else "")

    def disconnect(self):
        if self.port:
            self.port.close()
            self.port = None
        self.connect_button.configure(text="Conectar")
        self.link_status.set("ESP32 teclado: desconectada")

    def connect(self):
        if self.port:
            self.disconnect()
            return
        try:
            self.port = serial.Serial(self.port_name.get(), 115200, timeout=0)
        except (OSError, ValueError, serial.SerialException) as exc:
            messagebox.showerror("Puerto COM", f"No se puede abrir la ESP32: {exc}\nCierra Thonny.")
            return
        self.connect_button.configure(text="Desconectar")
        self.link_status.set("ESP32 teclado conectada a 115200")
        self._log("Esperando teclas @KEY desde los ocho GPIO de la ESP32.")

    def keypress(self, key: str, source="ESP32"):
        self.key_status.set(f"Última tecla: {key} ({source})")
        if key.isdigit():
            self.last_drawn = int(key)
            self.robot.draw(self.last_drawn)
            self.robot_status.set(f"Dibujando {key} con PyBullet...")
        elif key == "*":
            self.robot.clear()
            self.robot_status.set("Dibujo borrado.")
        elif key == "#":
            if self.last_drawn is not None:
                self.robot.draw(self.last_drawn)
                self.robot_status.set(f"Repitiendo {self.last_drawn}...")
        else:
            yaw, zoom = {"A": (-15, 0), "B": (15, 0), "C": (0, -0.1), "D": (0, 0.1)}[key]
            self.move_camera(yaw, zoom)
        self._log(f"Tecla {key} desde {source}")

    def move_camera(self, yaw, zoom):
        self.robot.camera_move(yaw, zoom)
        self.last_render = 0

    def capture(self):
        folder = BASE / "capturas"
        folder.mkdir(exist_ok=True)
        path = folder / f"punto1_pybullet_{datetime.now():%Y%m%d_%H%M%S}.png"
        Image.fromarray(self.robot.render()).save(path)
        self._log(f"Imagen guardada: {path}")

    def tick(self):
        if self.closed:
            return
        if self.port:
            try:
                for key in self.stream.feed(self.port.read(min(512, self.port.in_waiting or 1))):
                    self.keypress(key)
            except (OSError, serial.SerialException) as exc:
                self._log(f"Puerto desconectado: {exc}")
                self.disconnect()
        if self.robot.step():
            if not self.robot.pending:
                self.robot_status.set(f"Dígito {self.robot.last_digit} terminado; marcador retirado.")
        if time.monotonic() - self.last_render >= 0.17:
            try:
                self.photo = ImageTk.PhotoImage(Image.fromarray(self.robot.render()))
                self.view.configure(image=self.photo, text="")
            except Exception as exc:
                self.robot_status.set(f"Error de vista 3D: {exc}")
            self.last_render = time.monotonic()
        self.root.after(50, self.tick)

    def close(self):
        self.closed = True
        if self.port:
            self.port.close()
        self.robot.close()
        self.root.destroy()


if __name__ == "__main__":
    window = tk.Tk()
    try:
        App(window)
        window.mainloop()
    except Exception:
        window.destroy()
        raise
