"""Recursos del panel y destinos HTTP cerrados; nunca acepta URLs del navegador."""
import json
import mimetypes
import os
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MODE = os.environ.get('NEXO_MODO', 'fisico')
if MODE not in ('fisico', 'emulado', 'software'):
    raise ValueError('NEXO_MODO debe ser fisico, emulado o software')
VIEWS = json.loads(os.environ.get('NEXO_VISORES', json.dumps({
    'pista': 'http://192.168.10.10:8000',
    'spot': 'http://192.168.20.21:8000',
    'pepper': 'http://192.168.20.22:8000',
    'nao': 'http://192.168.20.23:8000'})))
CONTROLS = json.loads(os.environ.get('NEXO_CONTROLES', json.dumps({
    'ctrl-1': 'http://192.168.10.31:9090', 'ctrl-2': 'http://192.168.10.32:9090',
    'ctrl-3': 'http://192.168.10.33:9090', 'ctrl-spot': 'http://192.168.20.31:9090',
    'ctrl-pepper': 'http://192.168.20.32:9090', 'ctrl-nao': 'http://192.168.20.33:9090',
    'esclava': 'http://192.168.30.40:9090'})))
HTTP = urllib.request.build_opener(urllib.request.ProxyHandler({}))

def metadata():
    return {'nombre': 'NexoLab', 'version': '3.0', 'modo': MODE,
            'control_emulado': MODE in ('software', 'emulado'),
            'red': 'Procesos locales / loopback' if MODE == 'software' else 'Tres redes de Docker',
            'evidencia': 'Software ejecutado con ESP32 emuladas; sin prueba física ni VLAN 802.1Q'
            if MODE == 'software' else 'El modo indica la configuración, no certifica una prueba física'}

def json_reply(h, code, value):
    h._responder(code, 'application/json; charset=utf-8',
                 json.dumps(value, ensure_ascii=False, allow_nan=False).encode())

def fetch(url, data=None):
    request = urllib.request.Request(url, data=data,
        headers={'Content-Type': 'application/json'} if data else {})
    with HTTP.open(request, timeout=1.2) as r:
        body = r.read(4 * 1024 * 1024 + 1)
        if len(body) > 4 * 1024 * 1024:
            raise ValueError('respuesta demasiado grande')
        return r.headers.get('Content-Type', 'application/json'), body

def get(h, path):
    if path == '/api/nexo':
        json_reply(h, 200, metadata())
        return True
    if path.startswith('/api/zona/'):
        parts = path.split('/')
        if len(parts) != 5 or parts[3] not in VIEWS or parts[4] not in ('estado', 'cuadro', 'detalle') or (parts[4] == 'detalle' and parts[3] != 'pista'):
            json_reply(h, 404, {'error': 'recurso desconocido'})
            return True
        suffix = {'estado':'/estado.json','cuadro':'/cuadro.jpg','detalle':'/detalle.jpg'}[parts[4]]
        try:
            typ, body = fetch(VIEWS[parts[3]] + suffix)
            h._responder(200, typ, body)
        except (OSError, ValueError):
            json_reply(h, 503, {'error': 'servicio sin respuesta', 'zona': parts[3]})
        return True
    files = {'/': ROOT/'index.html', '/index.html': ROOT/'index.html',
             '/nexo.css': ROOT/'nexo.css', '/nexo.js': ROOT/'nexo.js'}
    for kind in ('gamer', 'robot', 'esclava'):
        files['/montaje-' + kind + '.png'] = ROOT.parent/'img'/('montaje-realista-' + kind + '.png')
    if path in files:
        try:
            f = files[path]
            h._responder(200, mimetypes.guess_type(str(f))[0] or 'application/octet-stream', f.read_bytes())
        except OSError:
            json_reply(h, 404, {'error': 'archivo no disponible'})
        return True
    return False

def post(h, path):
    if not path.startswith('/api/control/'):
        json_reply(h, 404, {'error': 'recurso desconocido'})
        return
    role = path.removeprefix('/api/control/')
    if MODE == 'fisico':
        json_reply(h, 403, {'error': 'Los mandos web solo se habilitan en modo emulado.'})
        return
    if role not in CONTROLS or role == 'esclava':
        json_reply(h, 404, {'error': 'mando desconocido'})
        return
    if h.headers.get('X-Nexo-Control') != '1':
        json_reply(h, 403, {'error': 'cabecera de control requerida'})
        return
    try:
        length = int(h.headers.get('Content-Length', '0'))
        if not 0 < length <= 2048:
            raise ValueError('tamaño inválido')
        payload = json.loads(h.rfile.read(length))
        if not isinstance(payload, dict):
            raise ValueError('se espera un objeto')
        payload['rol'] = role
        body = json.dumps(payload, allow_nan=False).encode()
    except (ValueError, TypeError):
        json_reply(h, 400, {'error': 'control inválido'})
        return
    try:
        typ, body = fetch(CONTROLS[role] + '/control', body)
        h._responder(200, typ, body)
    except urllib.error.HTTPError as e:
        json_reply(h, e.code if e.code in (400, 404) else 502, {'error': 'el emulador rechazó el control'})
    except (OSError, ValueError):
        json_reply(h, 503, {'error': 'mando emulado sin respuesta'})
