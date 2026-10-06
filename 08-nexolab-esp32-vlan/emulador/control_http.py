"""Control interactivo de las entradas emuladas. Conserva el envío UDP a 20 Hz."""
import json
import math
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

def validate(payload, gamer):
    if not isinstance(payload, dict) or payload.get('modo') not in ('auto', 'manual', 'pausa'):
        raise ValueError('modo inválido')
    mode = payload['modo']
    result = {'modo': mode}
    if mode == 'manual':
        limits = [(-100, 100), (-100, 100), (0, 1)] if gamer else [(-90, 90), (-90, 90), (-90, 90), (0, 1)]
        values = payload.get('entradas')
        if not isinstance(values, list) or len(values) != len(limits):
            raise ValueError('entradas incompletas')
        if any(isinstance(v, bool) or not isinstance(v, (float, int)) or not math.isfinite(v)
               or not lo <= v <= hi for v, (lo, hi) in zip(values, limits)):
            raise ValueError('entrada fuera de rango')
        if values[-1] not in (0, 1):
            raise ValueError('botón inválido')
        result['entradas'] = list(values)
    return result

def start(boards, port):
    roles = {p.rol: p for p in boards}
    class Handler(BaseHTTPRequestHandler):
        def reply(self, code, obj):
            data = json.dumps(obj, allow_nan=False).encode()
            self.send_response(code)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(data)
        def do_GET(self):
            self.reply(200, {r: dict(p.resumen(), control=getattr(p, 'control_ui', None)) for r, p in roles.items()})
        def do_POST(self):
            if self.path != '/control':
                return self.reply(404, {'error': 'ruta desconocida'})
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 < size <= 2048: raise ValueError('tamaño inválido')
                payload = json.loads(self.rfile.read(size))
                p = roles.get(payload.get('rol'))
                if p is None or not hasattr(p, 'jugador'): raise ValueError('rol inválido')
                control = validate(payload, bool(p.jugador))
                p.control_ui = control
                self.reply(200, {'rol': p.rol, **control})
            except (ValueError, TypeError, AttributeError):
                self.reply(400, {'error': 'control inválido'})
        def log_message(self, *args): pass
    server = ThreadingHTTPServer(('0.0.0.0', port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server
