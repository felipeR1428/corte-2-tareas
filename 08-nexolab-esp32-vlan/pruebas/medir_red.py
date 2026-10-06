"""medir_red.py - Validación experimental de latencia, jitter y disponibilidad (objetivo 5 del enunciado).

Mide durante N minutos, con el stack levantado (`docker compose --profile emulado up -d`), por TRES
caminos independientes, para que un número no dependa de un solo programa:

  A. Lo que calcula el propio admin: se suscribe a `lab/admin/resumen` (su tabla de latencia/jitter/
     disponibilidad por servicio, hecha con los latidos HB por UDP) y a `lab/metricas/+` (lo que
     publica cada contenedor: fps, recibidos, perdidos...). Si existe `resultados/admin_metricas.csv`
     (el registro que escribe el admin), también se lee la parte que cae dentro de la ventana.
  B. Una medición activa propia: ping ICMP desde el contenedor admin (VLAN 3) a cada contenedor de las
     zonas (VLAN 1 y 2), uno cada `--intervalo` s. Ese paquete hace el mismo camino que las métricas:
     admin -> router -> zona y vuelta, así que mide de verdad la latencia ENTRE VLAN a través del
     router. Con eso: promedio, p95, máximo, jitter RFC 3550 y pérdida.
  C. Los estados `lab/estado/<svc>` (OK/LENTO/CAIDO) que publica el admin: disponibilidad = fracción
     del tiempo de la ventana en que el servicio estuvo OK.

Escenarios (`--escenario`):

  base     Sin perturbar. Es la referencia.

  carga    Los ESP32 emulados mandan más rápido (de 20 Hz a `--hz-carga`, 100 por defecto): más
           datagramas por segundo que cada player/robot tiene que leer y más CPU ocupada en el PC.
           CÓMO se sube: el script recrea solo los servicios del emulador (ctrl-1..3, ctrl-spot/
           pepper/nao) con la variable de entorno EMU_HZ puesta:
               $env:EMU_HZ = 100
               docker compose -p zonas-esp32 --profile emulado up -d --no-deps --force-recreate ctrl-1 ...
           Para que eso tenga efecto, docker-compose.yml debe pasarle al emulador `--hz ${EMU_HZ:-20}`
           (el script lo revisa y avisa si no). Al terminar se recrean otra vez con el valor normal.
           Con `--sin-recrear` no toca nada (por si ya se subió a mano). Se verifica que la carga
           de verdad subió mirando la tasa de recepción que publican los players/robots.

  retardo  Se mete retardo artificial a la VLAN 2 DENTRO del router con `tc qdisc ... netem`
           (`--retardo-ms` 30 ± `--variacion-ms` 10 por defecto) en los dos sentidos:
             - interfaz del router hacia la VLAN 2: todo lo que sale hacia los robots se retrasa.
             - interfaz del router hacia la VLAN 3: una cola `prio` con un filtro u32 retrasa SOLO lo
               que viene de 192.168.20.0/24 (las respuestas y métricas de los robots), y deja sin
               tocar lo que viene de la VLAN 1. Así la VLAN 1 queda como grupo de control: si su
               latencia también sube, algo está mal en la medición.
           Esperado: RTT admin->robot sube ~2x30 = 60 ms con jitter de unos ms; RTT admin->player igual.
           Comprobado en este PC (Docker Desktop 29.8, kernel WSL2 6.6.87): el kernel trae sch_netem,
           sch_prio y cls_u32, y alpine:3.20 tiene `tc` en el paquete iproute2-tc. Si la imagen del
           router no trae `tc`, se usa un alpine auxiliar en la red del router que lo instala con apk
           (necesita internet una vez). Al terminar (o con Ctrl+C) se borran las colas; si algo quedó
           puesto: `python medir_red.py --limpiar-retardo`.

Salidas: pruebas/resultados/red_<escenario>.json y red_<escenario>_*.png.

Uso (desde la carpeta del tema, con el entorno del tema):
    entorno\\Scripts\\python.exe pruebas\\medir_red.py --escenario base --minutos 5
    entorno\\Scripts\\python.exe pruebas\\medir_red.py --escenario retardo --minutos 3
    entorno\\Scripts\\python.exe pruebas\\medir_red.py --escenario carga --minutos 3 --hz-carga 100
    entorno\\Scripts\\python.exe pruebas\\medir_red.py --sintetico      (sin stack: prueba el análisis
                                                                       y las gráficas con datos inventados)
"""

import argparse
import csv
import json
import math
import os
import random
import subprocess
import sys
import threading
import time

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)

import lab_pruebas as L  # noqa: E402

CSV_ADMIN = os.path.join(L.TEMA, "resultados", "admin_metricas.csv")
EMULADORES = ["ctrl-1", "ctrl-2", "ctrl-3", "ctrl-spot", "ctrl-pepper", "ctrl-nao"]


# =============================================================================================
# B. Ping continuo desde el admin
# =============================================================================================
class PingContinuo(threading.Thread):
    """Un `ping -c N -i intervalo <ip>` corriendo desde la red del admin hacia un servicio.

    Se lee la salida línea a línea mientras corre (no al final) para guardar cada respuesta con la
    hora en que llegó: así se puede graficar el RTT en el tiempo y ver, por ejemplo, el momento en
    que entra el retardo. Con -c el ping termina solo e imprime "N transmitted, M received", que da
    la pérdida exacta.
    """

    def __init__(self, servicio, ip, duracion_s, intervalo_s, via_exec):
        super().__init__(daemon=True, name=f"ping-{servicio}")
        self.servicio, self.ip, self.intervalo = servicio, ip, intervalo_s
        self.n = max(1, int(duracion_s / intervalo_s))
        self.via_exec = via_exec
        self.muestras = []          # (seq, rtt_ms, t_llegada)
        self.ttl = set()
        self.enviados = None
        self.recibidos = 0
        self.error = None
        self.proc = None

    def run(self):
        cmd_ping = f"ping -c {self.n} -i {self.intervalo} -W 2 {self.ip}"
        cid = L.contenedor("admin", incluir_detenidos=False)
        if not cid:
            self.error = "admin no está corriendo"
            return
        if self.via_exec:
            args = ["docker", "exec", cid, "sh", "-c", cmd_ping]
        else:
            args = ["docker", "run", "--rm", "--network", f"container:{cid}", L.IMAGEN_AUX,
                    "sh", "-c", cmd_ping]
        try:
            self.proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                         text=True, encoding="utf-8", errors="replace", bufsize=1)
        except OSError as e:
            self.error = str(e)
            return
        for linea in self.proc.stdout:
            m = L.RE_RESPUESTA.search(linea)
            if m:
                self.muestras.append((int(m.group(1)), float(m.group(3)), L.ahora()))
                self.ttl.add(int(m.group(2)))
            r = L.RE_RESUMEN.search(linea)
            if r:
                self.enviados, self.recibidos = int(r.group(1)), int(r.group(2))
        self.proc.wait()
        if self.enviados is None:
            # Se cortó antes del resumen (Ctrl+C): se estima con el último seq visto.
            self.recibidos = len(self.muestras)
            self.enviados = (max(s for s, _, _ in self.muestras) + 1) if self.muestras else 0

    def detener(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()

    def resultado(self):
        r = L.resumen_rtt([(s, rtt) for s, rtt, _ in self.muestras], self.intervalo)
        r["enviados"] = self.enviados
        r["recibidos"] = self.recibidos
        r["perdida_pct"] = round(100.0 * (1 - self.recibidos / self.enviados), 2) if self.enviados else None
        r["disponibilidad_ping_pct"] = round(100.0 * self.recibidos / self.enviados, 2) if self.enviados else None
        r["ttl"] = sorted(self.ttl)
        if self.error:
            r["error"] = self.error
        return r


# =============================================================================================
# A. Resumen del admin (MQTT) y CSV
# =============================================================================================
def numero(v):
    try:
        x = float(v)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def normalizar_resumen(obj):
    """Lleva el JSON de `lab/admin/resumen` a {servicio: {campo: valor}} sin suponer una forma exacta.

    El admin lo escribe otro agente; se aceptan las formas razonables:
      {"servicios": {"player-1": {...}, ...}}          {"servicios": [{"servicio": "player-1", ...}]}
      {"player-1": {...}, ...}                         [{"servicio": "player-1", ...}, ...]
    """
    if isinstance(obj, dict) and "servicios" in obj:
        obj = obj["servicios"]
    salida = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, dict):
                salida[k] = v
    elif isinstance(obj, list):
        for v in obj:
            if isinstance(v, dict):
                nombre = v.get("servicio") or v.get("nombre") or v.get("origen")
                if nombre:
                    salida[str(nombre)] = v
    return salida


def campo_por_palabra(d, palabras):
    """Primer valor numérico de `d` cuya clave contiene alguna de las `palabras` (minúsculas).
    Así 'latencia_ms', 'lat_prom_ms' o 'rtt_ms' sirven igual para la latencia."""
    for k, v in d.items():
        kl = k.lower()
        if any(p in kl for p in palabras):
            x = numero(v)
            if x is not None:
                return k, x
    return None, None


CLAVES = {
    "latencia": ("lat", "rtt"),
    "jitter": ("jit",),
    "disponibilidad": ("disp", "uptime"),
}


def resumir_admin(mensajes_resumen):
    """De la serie de resúmenes del admin saca por servicio: promedio, p95 y máximo de su latencia y
    jitter, y su disponibilidad (último valor publicado y promedio en la ventana)."""
    series = {}   # servicio -> magnitud -> [(t, valor)]
    nombres = {}  # magnitud -> nombre real de la clave en el JSON del admin (para documentarlo)
    for t, _top, txt, _ret in mensajes_resumen:
        try:
            tabla = normalizar_resumen(json.loads(txt))
        except (ValueError, TypeError):
            continue
        for svc, d in tabla.items():
            for mag, palabras in CLAVES.items():
                k, x = campo_por_palabra(d, palabras)
                if k is not None:
                    series.setdefault(svc, {}).setdefault(mag, []).append((t, x))
                    nombres.setdefault(mag, k)
            if "estado" in d:
                series.setdefault(svc, {}).setdefault("estado", []).append((t, d["estado"]))
    salida = {}
    for svc, mags in series.items():
        s = {}
        for mag, pts in mags.items():
            if mag == "estado":
                s["ultimo_estado"] = pts[-1][1]
                continue
            vals = [v for _, v in pts]
            s[mag] = {"n": len(vals), "prom": round(sum(vals) / len(vals), 3),
                      "p95": round(L.percentil(vals, 95), 3), "max": round(max(vals), 3),
                      "ultimo": round(vals[-1], 3)}
        salida[svc] = s
    return salida, nombres, series


def leer_csv_admin(t_ini, t_fin, ruta=CSV_ADMIN):
    """Lee el CSV que escribe el admin y devuelve las filas de la ventana [t_ini, t_fin].

    Igual que con el resumen, no se supone el formato exacto: se busca una columna de tiempo (t, ts,
    tiempo, timestamp, epoch, fecha) en segundos epoch o ISO 8601; si no hay o no se entiende, se
    devuelven todas las filas y se avisa. Devuelve (filas, nota)."""
    if not os.path.exists(ruta):
        return [], f"no existe {ruta}"
    from datetime import datetime
    with open(ruta, newline="", encoding="utf-8", errors="replace") as f:
        filas = list(csv.DictReader(f))
    if not filas:
        return [], "CSV vacío"
    col_t = next((c for c in filas[0] if c and c.lower() in
                  ("t", "ts", "tiempo", "timestamp", "epoch", "fecha", "hora", "t_s")), None)
    if col_t is None:
        return filas, "el CSV no tiene columna de tiempo reconocible: se usan todas las filas"

    def a_epoch(v):
        x = numero(v)
        if x is not None:
            return x / 1000.0 if x > 1e11 else x   # ms o s
        try:
            return datetime.fromisoformat(str(v)).timestamp()
        except ValueError:
            return None

    dentro = [f for f in filas if (lambda e: e is not None and t_ini <= e <= t_fin)(a_epoch(f[col_t]))]
    return dentro, f"{len(dentro)} de {len(filas)} filas dentro de la ventana (columna '{col_t}')"


def resumir_csv(filas):
    """Por servicio, promedio/p95/máximo de las columnas de latencia y jitter, y disponibilidad."""
    col_svc = None
    if filas:
        col_svc = next((c for c in filas[0] if c and c.lower() in ("servicio", "origen", "nombre")), None)
    if not col_svc:
        return {}
    por = {}
    for f in filas:
        por.setdefault(f[col_svc], []).append(f)
    salida = {}
    for svc, fs in por.items():
        s = {}
        for mag, palabras in CLAVES.items():
            vals = [x for x in (campo_por_palabra(f, palabras)[1] for f in fs) if x is not None]
            if vals:
                s[mag] = {"n": len(vals), "prom": round(sum(vals) / len(vals), 3),
                          "p95": round(L.percentil(vals, 95), 3), "max": round(max(vals), 3)}
        salida[svc] = s
    return salida


def resumir_metricas(mensajes):
    """Para cada servicio que publica en lab/metricas: cuántos mensajes llegaron, y de cada campo
    numérico su promedio, mínimo y máximo. Además, si hay un contador acumulado de paquetes
    ('recibidos', 'rx', 'paquetes'), la tasa de recepción en Hz = Δcontador / Δt; es lo que confirma
    que el escenario de carga de verdad subió la frecuencia."""
    por = {}
    for t, top, txt, ret in mensajes:
        if ret:
            continue
        svc = top.split("/", 2)[-1]
        try:
            d = json.loads(txt)
        except ValueError:
            continue
        if isinstance(d, dict):
            por.setdefault(svc, []).append((t, d))
    salida = {}
    for svc, pts in por.items():
        campos = {}
        for _, d in pts:
            for k, v in d.items():
                x = numero(v)
                if x is not None and k != "t_ms":
                    campos.setdefault(k, []).append(x)
        s = {"mensajes": len(pts),
             "campos": {k: {"prom": round(sum(v) / len(v), 3), "min": min(v), "max": max(v)}
                        for k, v in campos.items()}}
        for clave in ("recibidos", "rx", "paquetes"):
            serie = [(t, numero(d.get(clave))) for t, d in pts if numero(d.get(clave)) is not None]
            if len(serie) >= 2 and serie[-1][0] > serie[0][0] and serie[-1][1] >= serie[0][1]:
                s["tasa_recepcion_hz"] = round((serie[-1][1] - serie[0][1]) / (serie[-1][0] - serie[0][0]), 2)
                s["tasa_calculada_con"] = clave
                break
        salida[svc] = s
    return salida


def disponibilidad_por_estado(obs_mensajes, t_ini, t_fin, servicios):
    """C. Fracción de la ventana en OK según `lab/estado/<svc>` (incluye el retenido inicial)."""
    salida = {}
    for svc in servicios:
        ev = [(t, txt.strip()) for t, top, txt, _ in obs_mensajes if top == f"lab/estado/{svc}"]
        # El retenido llega al suscribirse (antes de t_ini): vale como estado al empezar la ventana.
        f = L.fraccion_en_estado(ev, t_ini, t_fin)
        cambios = [{"t_rel_s": round(t - t_ini, 2), "estado": v} for t, v in ev if t_ini < t < t_fin]
        salida[svc] = {"disponibilidad_pct": None if f is None else round(100 * f, 2),
                       "cambios": cambios}
    return salida


# =============================================================================================
# Escenarios: retardo (tc netem en el router) y carga (EMU_HZ)
# =============================================================================================
def _interfaces_router():
    """{vlan: nombre de interfaz} del router, buscando qué interfaz tiene la IP .254 de cada VLAN.
    Docker no garantiza que eth0 sea siempre la misma red, por eso se busca por IP y no por nombre."""
    c, out = L.exec_en("router", ["ip", "-o", "-4", "addr", "show"])
    if c != 0:
        c, out = L.en_red_de("router", "ip -o -4 addr show")
    ifs = {}
    for linea in out.splitlines():
        partes = linea.split()
        if len(partes) >= 4 and partes[2] == "inet":
            dev = partes[1].split("@")[0]
            ip = partes[3].split("/")[0]
            for vlan, ip_router in L.ROUTER.items():
                if ip == ip_router:
                    ifs[vlan] = dev
    return ifs


def _tc_en_router(script):
    """Corre comandos de `tc` en la red del router: primero con el tc del propio router y, si no lo
    tiene, con un alpine auxiliar (que instala iproute2-tc) en la red del router."""
    if L.tiene_comando("router", "tc"):
        return L.exec_en("router", ["sh", "-c", script]) + ("tc del router",)
    c, out = L.en_red_de("router", "apk add --no-cache iproute2-tc >/dev/null 2>&1 || exit 97; " + script,
                         net_admin=True, timeout=180)
    return c, out, "alpine auxiliar con iproute2-tc en la red del router"


def aplicar_retardo(retardo_ms, variacion_ms):
    ifs = _interfaces_router()
    if 2 not in ifs or 3 not in ifs:
        return {"aplicado": False, "motivo": f"no encontré las interfaces del router: {ifs}"}
    d2, d3 = ifs[2], ifs[3]
    net = f"netem delay {retardo_ms}ms {variacion_ms}ms"
    script = (
        "set -e; "
        # Sentido admin -> robots: todo lo que sale por la interfaz de la VLAN 2.
        f"tc qdisc replace dev {d2} root {net}; "
        # Sentido robots -> admin: en la interfaz de la VLAN 3, cola prio de 4 bandas con priomap todo
        # a la banda 1 (sin retardo) y netem SOLO en la banda 4, a la que el filtro u32 manda lo que
        # tiene origen 192.168.20.0/24. Así la VLAN 1 no se toca (grupo de control).
        f"tc qdisc replace dev {d3} root handle 1: prio bands 4 priomap 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0; "
        f"tc qdisc replace dev {d3} parent 1:4 handle 40: {net}; "
        f"tc filter add dev {d3} parent 1:0 protocol ip prio 1 u32 match ip src {L.REDES[2]} flowid 1:4; "
        f"tc qdisc show dev {d2}; tc qdisc show dev {d3}; tc filter show dev {d3}"
    )
    c, out, via = _tc_en_router(script)
    return {"aplicado": c == 0, "codigo": c, "via": via, "interfaces": ifs,
            "comando_netem": net, "salida": out.strip()[-1500:]}


def quitar_retardo():
    ifs = _interfaces_router()
    script = "; ".join(f"tc qdisc del dev {ifs[v]} root 2>/dev/null" for v in (2, 3) if v in ifs) or "true"
    c, out, via = _tc_en_router(script + "; true")
    print(f"[retardo] colas tc borradas ({via})")
    return c == 0


def recrear_emuladores(hz):
    """Recrea los servicios del emulador con EMU_HZ=hz (o sin la variable si hz es None)."""
    entorno = dict(os.environ)
    if hz is None:
        entorno.pop("EMU_HZ", None)
    else:
        entorno["EMU_HZ"] = str(hz)
    presentes = [s for s in EMULADORES if L.contenedor(s)]
    if not presentes:
        return False, "no hay contenedores del emulador (¿se levantó con --profile emulado?)"
    cmd = ["docker", "compose", "-p", L.PROYECTO, "--profile", "emulado", "up", "-d", "--no-deps",
           "--force-recreate", *presentes]
    r = subprocess.run(cmd, cwd=L.TEMA, env=entorno, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=300)
    return r.returncode == 0, (r.stdout + r.stderr)[-1500:]


def compose_tiene_emu_hz():
    ruta = os.path.join(L.TEMA, "docker-compose.yml")
    if not os.path.exists(ruta):
        return None
    with open(ruta, encoding="utf-8", errors="replace") as f:
        return "EMU_HZ" in f.read()


# =============================================================================================
# Gráficas (matplotlib sin ventana)
# =============================================================================================
# Un color por zona (es lo único categórico de las gráficas): VLAN 1 azul, VLAN 2 naranja, de la
# paleta validada para daltonismo (azul/naranja es el par más seguro). Texto en gris oscuro, no en el
# color de la serie; rejilla tenue.
COLOR_VLAN = {1: "#2a78d6", 2: "#eb6834"}
TINTA, TINTA2, REJILLA = "#0b0b0b", "#52514e", "#e4e3df"


def _estilo(ax, titulo, ylabel):
    ax.set_title(titulo, loc="left", fontsize=11, color=TINTA)
    ax.set_ylabel(ylabel, color=TINTA2)
    ax.grid(axis="y", color=REJILLA, linewidth=0.8)
    ax.set_axisbelow(True)
    for lado in ("top", "right"):
        ax.spines[lado].set_visible(False)
    for lado in ("left", "bottom"):
        ax.spines[lado].set_color(REJILLA)
    ax.tick_params(colors=TINTA2)


def graficar(escenario, ping_series, ping_res, t_ini, carpeta=L.RESULTADOS):
    import matplotlib
    matplotlib.use("Agg")  # sin ventana: corre igual en una consola o en un servidor
    import matplotlib.pyplot as plt
    from matplotlib.patches import Patch

    rutas = []
    svcs = [s for s in L.ZONAS if s in ping_res and ping_res[s].get("n")]
    if not svcs:
        return rutas

    # 1) RTT en el tiempo: pequeños múltiplos (un panel por servicio, mismo eje y) en vez de 7 líneas
    #    encimadas, que no se podrían leer.
    filas = (len(svcs) + 1) // 2
    fig, ejes = plt.subplots(filas, 2, figsize=(11, 2.2 * filas), sharex=True, sharey=True,
                             squeeze=False)
    for i, s in enumerate(svcs):
        ax = ejes[i // 2][i % 2]
        pts = ping_series.get(s, [])
        ax.plot([t - t_ini for _, _, t in pts], [r for _, r, _ in pts], linewidth=1.2,
                color=COLOR_VLAN[L.SERVICIOS[s][0]])
        _estilo(ax, f"{s}  (VLAN {L.SERVICIOS[s][0]})", "RTT (ms)")
    for j in range(len(svcs), filas * 2):
        ejes[j // 2][j % 2].axis("off")
    for ax in ejes[-1]:
        ax.set_xlabel("tiempo desde el inicio (s)", color=TINTA2)
    fig.suptitle(f"RTT admin -> servicio por ping, escenario '{escenario}'", x=0.01, ha="left",
                 color=TINTA, fontsize=12)
    fig.tight_layout()
    ruta = os.path.join(carpeta, f"red_{escenario}_rtt_tiempo.png")
    fig.savefig(ruta, dpi=120)
    plt.close(fig)
    rutas.append(ruta)

    # 2) Barras: promedio y p95 de RTT, y jitter, por servicio (dos paneles: escalas distintas, nunca
    #    dos ejes y en la misma gráfica).
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(11, 3.8))
    x = range(len(svcs))
    colores = [COLOR_VLAN[L.SERVICIOS[s][0]] for s in svcs]
    prom = [ping_res[s]["prom_ms"] for s in svcs]
    p95 = [ping_res[s]["p95_ms"] for s in svcs]
    a1.bar([i - 0.2 for i in x], prom, width=0.38, color=colores, label="promedio")
    a1.bar([i + 0.2 for i in x], p95, width=0.38, color=colores, alpha=0.45, label="p95")
    for i, (v1, v2) in enumerate(zip(prom, p95)):
        a1.text(i + 0.2, v2, f"{v2:.1f}", ha="center", va="bottom", fontsize=7, color=TINTA2)
    _estilo(a1, "RTT: promedio (lleno) y p95 (claro)", "ms")
    jit = [ping_res[s]["jitter_rfc3550_ms"] for s in svcs]
    a2.bar(list(x), jit, width=0.6, color=colores)
    for i, v in enumerate(jit):
        a2.text(i, v, f"{v:.2f}", ha="center", va="bottom", fontsize=7, color=TINTA2)
    _estilo(a2, "Jitter RFC 3550", "ms")
    for ax in (a1, a2):
        ax.set_xticks(list(x))
        ax.set_xticklabels(svcs, rotation=30, ha="right", fontsize=8)
    fig.legend(handles=[Patch(color=COLOR_VLAN[1], label="VLAN 1 (gamer)"),
                        Patch(color=COLOR_VLAN[2], label="VLAN 2 (robótica)")],
               loc="upper right", frameon=False, fontsize=8)
    fig.suptitle(f"Latencia y jitter entre VLAN, escenario '{escenario}'", x=0.01, ha="left",
                 color=TINTA, fontsize=12)
    fig.tight_layout(rect=(0, 0, 1, 0.93))
    ruta = os.path.join(carpeta, f"red_{escenario}_resumen.png")
    fig.savefig(ruta, dpi=120)
    plt.close(fig)
    rutas.append(ruta)
    for r in rutas:
        print(f"[gráfica] {r}")
    return rutas


# =============================================================================================
# Programa principal
# =============================================================================================
def medir(args):
    problemas = L.comprobar_entorno(requiere_broker=not args.sin_mqtt, host=args.broker, puerto=args.puerto)
    if not L.corriendo("admin"):
        problemas.append("el contenedor admin no está corriendo")
    if problemas:
        print("No se puede medir:\n  - " + "\n  - ".join(problemas))
        return 2

    duracion = args.minutos * 60.0
    escenario_info = {"nombre": args.escenario}
    # --- preparar el escenario ---
    if args.escenario == "retardo":
        info = aplicar_retardo(args.retardo_ms, args.variacion_ms)
        escenario_info.update(info)
        print(f"[retardo] aplicado={info['aplicado']} {info.get('via', '')} {info.get('interfaces', '')}")
        if not info["aplicado"]:
            print(info.get("salida") or info.get("motivo"))
            print("No se pudo meter el retardo: se cancela (medir 'retardo' sin retardo sería engañoso).")
            return 3
        time.sleep(2)
    elif args.escenario == "carga":
        escenario_info["hz_carga"] = args.hz_carga
        escenario_info["compose_usa_EMU_HZ"] = compose_tiene_emu_hz()
        if escenario_info["compose_usa_EMU_HZ"] is False:
            print("AVISO: docker-compose.yml no menciona EMU_HZ; recrear los emuladores no cambiará su "
                  "frecuencia. Se mide igual y la tasa de recepción dirá si la carga subió.")
        if not args.sin_recrear:
            ok, salida = recrear_emuladores(args.hz_carga)
            escenario_info["recreados"] = ok
            print(f"[carga] emuladores recreados con EMU_HZ={args.hz_carga}: {ok}")
            if not ok:
                print(salida)
            time.sleep(10)  # que arranquen y el admin vuelva a verlos OK

    obs = None
    if not args.sin_mqtt:
        obs = L.Observador(args.broker, args.puerto)
        if not obs.iniciar():
            print("AVISO: no pude conectarme al broker; sigo solo con ping.")
        time.sleep(1.5)  # recibir los retenidos antes de abrir la ventana

    via_exec = L.tiene_comando("admin", "ping")
    destinos = [s for s in L.ZONAS if L.corriendo(s)]
    hilos = {s: PingContinuo(s, L.SERVICIOS[s][1], duracion, args.intervalo, via_exec) for s in destinos}
    t_ini = L.ahora()
    print(f"[medir] escenario '{args.escenario}', {args.minutos} min, ping cada {args.intervalo} s "
          f"a {len(destinos)} servicios ({'docker exec' if via_exec else 'alpine auxiliar'})")
    try:
        for h in hilos.values():
            h.start()
        fin = t_ini + duracion
        while time.time() < fin + 5 and any(h.is_alive() for h in hilos.values()):
            time.sleep(5)
            resta = max(0, int(fin - time.time()))
            vivos = sum(len(h.muestras) for h in hilos.values())
            print(f"  ... quedan {resta} s, {vivos} respuestas de ping", flush=True)
    except KeyboardInterrupt:
        print("Interrumpido: se guarda lo medido hasta ahora.")
    finally:
        for h in hilos.values():
            h.detener()
        for h in hilos.values():
            h.join(timeout=10)
        t_fin = L.ahora()
        if args.escenario == "retardo":
            quitar_retardo()
        elif args.escenario == "carga" and not args.sin_recrear:
            ok, _ = recrear_emuladores(None)
            print(f"[carga] emuladores devueltos a su frecuencia normal: {ok}")
        if obs:
            obs.parar()

    # --- análisis ---
    ping_res = {s: h.resultado() for s, h in hilos.items()}
    ping_series = {s: h.muestras for s, h in hilos.items()}
    resultado = armar_resultado(args, escenario_info, t_ini, t_fin, ping_res,
                                obs.copia() if obs else [])
    L.guardar_json(f"red_{args.escenario}.json", resultado)
    resultado["graficas"] = [os.path.relpath(r, L.TEMA) for r in
                             graficar(args.escenario, ping_series, ping_res, t_ini)]
    L.guardar_json(f"red_{args.escenario}.json", resultado)
    imprimir_tabla(resultado)
    return 0


def armar_resultado(args, escenario_info, t_ini, t_fin, ping_res, mensajes):
    resumenes = [m for m in mensajes if m[1] == "lab/admin/resumen" and t_ini <= m[0] <= t_fin]
    metricas = [m for m in mensajes if m[1].startswith("lab/metricas/") and t_ini <= m[0] <= t_fin]
    admin, nombres, _ = resumir_admin(resumenes)
    filas_csv, nota_csv = leer_csv_admin(t_ini, t_fin)
    por_servicio = {}
    for s in sorted(set(ping_res) | set(admin) | set(L.CON_LED)):
        por_servicio[s] = {
            "vlan": L.SERVICIOS.get(s, (None,))[0],
            "ping_desde_admin": ping_res.get(s),
            "segun_admin_mqtt": admin.get(s),
        }
    disp = disponibilidad_por_estado(mensajes, t_ini, t_fin, L.CON_LED)
    for s, d in disp.items():
        por_servicio.setdefault(s, {})["disponibilidad_por_estado"] = d
    return {
        "escenario": escenario_info,
        "inicio_epoch": round(t_ini, 3),
        "duracion_s": round(t_fin - t_ini, 1),
        "intervalo_ping_s": args.intervalo,
        "metodo": {
            "ping_desde_admin": "ICMP desde el contenedor admin (VLAN 3) a cada servicio, cruzando el "
                                "router; jitter = RFC 3550 sobre la serie de RTT (comun.protocolo.Jitter)",
            "segun_admin_mqtt": f"tabla de lab/admin/resumen; claves usadas: {nombres}",
            "disponibilidad_por_estado": "fracción de la ventana con lab/estado/<svc> = OK",
        },
        "mensajes_mqtt": {"resumenes": len(resumenes), "metricas": len(metricas)},
        "por_servicio": por_servicio,
        "metricas_contenedores": resumir_metricas(metricas),
        "csv_admin": {"nota": nota_csv, "por_servicio": resumir_csv(filas_csv)},
    }


def imprimir_tabla(res):
    print(f"\nEscenario {res['escenario']['nombre']}, {res['duracion_s']} s")
    print(f"{'servicio':<13}{'VLAN':>5}{'prom':>9}{'p95':>9}{'máx':>9}{'jitter':>9}{'pérd %':>8}{'disp %':>8}")
    for s, d in res["por_servicio"].items():
        p = d.get("ping_desde_admin") or {}
        disp = (d.get("disponibilidad_por_estado") or {}).get("disponibilidad_pct")
        if not p.get("n") and disp is None:
            continue
        f = lambda v: "-" if v is None else f"{v:.2f}"  # noqa: E731
        print(f"{s:<13}{str(d.get('vlan')):>5}{f(p.get('prom_ms')):>9}{f(p.get('p95_ms')):>9}"
              f"{f(p.get('max_ms')):>9}{f(p.get('jitter_rfc3550_ms')):>9}{f(p.get('perdida_pct')):>8}"
              f"{f(disp):>8}")


# ---------------------------------------------------------------------------------------------
# Modo sintético: prueba el análisis y las gráficas sin stack (datos inventados, se marca así)
# ---------------------------------------------------------------------------------------------
def sintetico(args):
    """Genera RTT inventados (VLAN 1 ~0,3 ms; VLAN 2 con +60 ms ± 10 si el escenario es 'retardo'),
    mensajes MQTT de resumen/estado/métricas falsos con varias formas de JSON, y corre el mismo
    análisis que con el stack. Sirve para comprobar que el script no se cae y que las gráficas se
    leen. El json se guarda como red_<escenario>_SINTETICO.json para que nunca se confunda."""
    rnd = random.Random(7)
    t_ini = 1_000_000.0
    dur = args.minutos * 60.0
    n = int(dur / args.intervalo)
    ping_res, ping_series = {}, {}
    for s in L.ZONAS:
        vlan = L.SERVICIOS[s][0]
        base = 0.3 + rnd.random() * 0.2
        extra = 60.0 if (args.escenario == "retardo" and vlan == 2) else 0.0
        muestras = []
        for k in range(n):
            if rnd.random() < 0.005:
                continue  # pérdida del 0,5 %
            r = base + abs(rnd.gauss(0, 0.05)) + (extra + rnd.uniform(-10, 10) * 2 if extra else 0)
            muestras.append((k, round(r, 3), t_ini + k * args.intervalo + r / 1000))
        ping_series[s] = muestras
        r = L.resumen_rtt([(a, b) for a, b, _ in muestras], args.intervalo)
        r.update({"enviados": n, "recibidos": len(muestras),
                  "perdida_pct": round(100 * (1 - len(muestras) / n), 2),
                  "disponibilidad_ping_pct": round(100 * len(muestras) / n, 2),
                  "ttl": [63]})
        ping_res[s] = r
    mensajes = []
    for s in L.CON_LED:
        mensajes.append((t_ini - 1, f"lab/estado/{s}", "OK", True))
    mensajes.append((t_ini + dur * 0.5, "lab/estado/sim-pepper", "CAIDO", False))
    mensajes.append((t_ini + dur * 0.6, "lab/estado/sim-pepper", "OK", False))
    for k in range(int(dur / 2)):
        t = t_ini + 2 * k
        tabla = {s: {"latencia_ms": ping_res[s]["prom_ms"] + rnd.random(), "jitter_ms": rnd.random(),
                     "disponibilidad_pct": 100.0, "estado": "OK"} for s in L.CON_LED}
        forma = k % 3   # las tres formas de JSON que acepta normalizar_resumen
        if forma == 0:
            obj = {"servicios": tabla}
        elif forma == 1:
            obj = {"servicios": [dict(v, servicio=s) for s, v in tabla.items()]}
        else:
            obj = tabla
        mensajes.append((t, "lab/admin/resumen", json.dumps(obj), False))
        for s in L.CON_LED:
            mensajes.append((t, f"lab/metricas/{s}",
                             json.dumps({"recibidos": 40 * k, "perdidos": 0, "fps": 240 + rnd.random()}), False))
    esc = {"nombre": args.escenario, "SINTETICO": True}
    res = armar_resultado(args, esc, t_ini, t_ini + dur, ping_res, mensajes)
    nombre = f"{args.escenario}_SINTETICO"
    res["graficas"] = [os.path.relpath(r, L.TEMA) for r in graficar(nombre, ping_series, ping_res, t_ini)]
    L.guardar_json(f"red_{nombre}.json", res)
    imprimir_tabla(res)
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--escenario", choices=["base", "carga", "retardo"], default="base")
    ap.add_argument("--minutos", type=float, default=5.0, help="duración de la medición (5 por defecto)")
    ap.add_argument("--intervalo", type=float, default=0.5, help="segundos entre pings (0,5 por defecto)")
    ap.add_argument("--broker", default="localhost")
    ap.add_argument("--puerto", type=int, default=1883)
    ap.add_argument("--sin-mqtt", action="store_true", help="medir solo con ping")
    ap.add_argument("--retardo-ms", type=int, default=30)
    ap.add_argument("--variacion-ms", type=int, default=10)
    ap.add_argument("--hz-carga", type=int, default=100)
    ap.add_argument("--sin-recrear", action="store_true", help="carga: no recrear los emuladores")
    ap.add_argument("--limpiar-retardo", action="store_true", help="solo borra las colas tc del router")
    ap.add_argument("--sintetico", action="store_true", help="sin stack: datos inventados")
    args = ap.parse_args()
    if args.limpiar_retardo:
        return 0 if quitar_retardo() else 1
    if args.sintetico:
        return sintetico(args)
    return medir(args)


if __name__ == "__main__":
    sys.exit(main())
