import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import signal
import threading
import time
from urllib.parse import urlparse
from .bridge import Bridge, TelemetryStore
from .scene import Scene


class Runtime:
    def __init__(self, options):
        self.store = TelemetryStore(options.data)
        self.bridge = Bridge(self.store, options.hub, options.hub_port, options.udp_port,
                             advertised_port=options.reply_port)
        self.scene = Scene(options.width, options.height)
        self.stop = threading.Event()
        self.frame_lock = threading.Lock()
        self.frame = self.scene.render()
        self.thread = threading.Thread(target=self.physics, name="PyBullet", daemon=True)

    def physics(self):
        previous = time.monotonic()
        frame_at = previous
        try:
            while not self.stop.is_set():
                now = time.monotonic()
                self.scene.update(self.store.snapshot(), now - previous)
                previous = now
                if now - frame_at >= 0.12:
                    frame = self.scene.render()
                    with self.frame_lock:
                        self.frame = frame
                    frame_at = now
                self.stop.wait(0.025)
        except Exception as exc:
            self.store.error("PyBullet: " + str(exc))
            self.stop.set()

    def start(self):
        self.bridge.start()
        self.thread.start()

    def snapshot(self):
        result = self.store.snapshot()
        result["control"] = self.bridge.status()
        result["udp"] = self.bridge.network_status()
        result["renderer_running"] = not self.stop.is_set()
        return result

    def close(self):
        self.stop.set()
        self.thread.join(timeout=3)
        self.bridge.close()
        self.scene.close()
        self.store.close()


def handler_for(runtime):
    html_path = Path(__file__).resolve().parent / "web" / "index.html"

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def respond(self, status, content, mime, extra=None):
            self.send_response(status)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            for name, value in (extra or {}).items():
                self.send_header(name, value)
            self.end_headers()
            try:
                self.wfile.write(content)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def json(self, status, value):
            self.respond(status, json.dumps(value, ensure_ascii=False).encode(), "application/json; charset=utf-8")

        def do_GET(self):
            path = urlparse(self.path).path
            if path == "/":
                self.respond(200, html_path.read_bytes(), "text/html; charset=utf-8")
            elif path == "/frame.jpg":
                with runtime.frame_lock:
                    frame = runtime.frame
                self.respond(200, frame, "image/jpeg")
            elif path in ("/api/state", "/api/results"):
                self.json(200, runtime.snapshot())
            elif path == "/api/export":
                self.respond(200, runtime.store.export(), "text/csv; charset=utf-8",
                             {"Content-Disposition": 'attachment; filename="telemetria_ACO_multirruta_v3.csv"'})
            elif path == "/health":
                self.json(200 if not runtime.stop.is_set() else 503, {"ok": not runtime.stop.is_set()})
            else:
                self.json(404, {"error": "Ruta desconocida"})

        def do_POST(self):
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 2048:
                    raise ValueError("Cuerpo invalido")
                body = json.loads(self.rfile.read(length))
                path = urlparse(self.path).path
                if path == "/api/command":
                    self.json(200, runtime.bridge.issue(body.get("action")))
                elif path == "/api/camera":
                    action = body.get("action")
                    if action not in ("reset", "orbit", "pan", "zoom"):
                        raise ValueError("Accion de camara invalida")
                    dx = max(-1000, min(1000, float(body.get("dx", 0))))
                    dy = max(-1000, min(1000, float(body.get("dy", 0))))
                    runtime.scene.configure_camera(action, dx=dx, dy=dy)
                    self.json(200, {"ok": True})
                else:
                    self.json(404, {"error": "Ruta desconocida"})
            except (ValueError, TypeError, AttributeError) as exc:
                self.json(400, {"error": str(exc)})

    return Handler


def main():
    parser = argparse.ArgumentParser(description="Gemelo PyBullet: los tres ESP32 ejecutan ACO.")
    parser.add_argument("--hub", default=os.getenv("HUB_IP", "192.168.4.1"))
    parser.add_argument("--hub-port", type=int, default=int(os.getenv("HUB_PORT", "4210")))
    parser.add_argument("--udp-port", type=int, default=int(os.getenv("UDP_PORT", "4211")))
    parser.add_argument("--reply-port", type=int, default=int(os.getenv("REPLY_PORT", "4211")))
    parser.add_argument("--http-port", type=int, default=int(os.getenv("HTTP_PORT", "8080")))
    parser.add_argument("--http-bind", default=os.getenv("HTTP_BIND", "127.0.0.1"))
    parser.add_argument("--data", default=os.getenv("DATA_DIR", "data"))
    parser.add_argument("--width", type=int, default=960)
    parser.add_argument("--height", type=int, default=600)
    options = parser.parse_args()
    runtime = Runtime(options)
    server = ThreadingHTTPServer((options.http_bind, options.http_port), handler_for(runtime))
    runtime.start()
    panel_url = os.getenv("PANEL_URL", "http://127.0.0.1:%d" % options.http_port)
    print("PyBullet listo. Panel: %s" % panel_url, flush=True)
    print("Esperando ESP32 por UDP %d; AP %s:%d" % (options.udp_port, options.hub, options.hub_port), flush=True)
    if runtime.bridge.proxy_ips:
        print("Proxy UDP Docker permitido: %s" % ", ".join(sorted(runtime.bridge.proxy_ips)), flush=True)

    def shutdown(*_):
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGINT, shutdown)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, shutdown)
    try:
        server.serve_forever(poll_interval=0.2)
    finally:
        server.server_close()
        runtime.close()


if __name__ == "__main__":
    main()
