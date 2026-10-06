"""prueba_disponibilidad.py - Caídas y recuperación: cuánto tarda el admin en darse cuenta y en volver.

Objetivo 5 del enunciado (disponibilidad) y, de paso, el "aislamiento de fallas": que se caiga una
pieza no debe tumbar a las demás. Necesita el stack levantado y el broker en localhost:1883.

Parte 1 - se cae un contenedor de una zona (por defecto sim-pepper y después player-2):
  1. Se espera a que los 6 servicios con LED estén en OK (`lab/estado/<svc>` = OK).
  2. `docker stop <svc>` y se mide cuánto tarda el admin en publicar `lab/estado/<svc>` = CAIDO
     (también cuándo llega `lab/vivo/<svc>` = 0: el testamento MQTT o la despedida).
  3. Mientras está caído, los otros 5 deben seguir en OK (ningún cambio a LENTO/CAIDO) y seguir
     publicando métricas: eso es el aislamiento de fallas.
  4. `docker start <svc>` y se mide cuánto tarda en volver a OK.
  Los tiempos se cuentan desde que se DA la orden y también desde que `docker stop` terminó (el
  contenedor ya está muerto): la diferencia es lo que tarda el proceso en cerrarse.

Parte 2 - se cae el admin (que es el broker MQTT y el monitor):
  Con el admin caído no hay broker, así que por MQTT no se ve nada: se comprueba que las zonas siguen
  simulando de otra forma, cada 5 s:
    - `docker inspect`: los contenedores de las zonas siguen corriendo y NO se reiniciaron
      (mismo StartedAt, mismo RestartCount): ninguno se cayó por perder el broker.
    - `docker logs --since`: siguen escribiendo líneas (si el contenedor imprime algo periódico).
    - HTTP del visor de cada simulación (puertos 8010-8013 del PC): si responde, y si la respuesta
      cambia entre una consulta y la siguiente (= los cuadros siguen avanzando).
  Al arrancar el admin otra vez se mide: cuándo vuelve el broker, cuándo vuelve a llegar la primera
  métrica de cada zona (sus clientes MQTT se reconectaron solos, sin reiniciar nada) y cuándo están
  los 6 otra vez en OK.

Salida: pruebas/resultados/disponibilidad.json

Uso:
    entorno\\Scripts\\python.exe pruebas\\prueba_disponibilidad.py
    entorno\\Scripts\\python.exe pruebas\\prueba_disponibilidad.py --servicios sim-nao --sin-admin
"""

import argparse
import hashlib
import os
import sys
import time
import urllib.request

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)

import lab_pruebas as L  # noqa: E402

# Visores HTTP de las simulaciones publicados en el PC (contrato: 8010 pista, 8011-8013 robots).
VISORES = {"track-server": 8010, "sim-spot": 8011, "sim-pepper": 8012, "sim-nao": 8013}


def r2(x):
    return None if x is None else round(x, 2)


def todos_ok(servicios):
    """Condición para Observador.esperar: el último estado conocido de cada servicio es OK."""
    return lambda obs: all(obs.ultimo("lab/estado", s) == "OK" for s in servicios)


def estado_inicial(obs, servicios, timeout):
    t = obs.esperar(todos_ok(servicios), timeout)
    return t is not None, {s: obs.ultimo("lab/estado", s) for s in servicios}


# ---------------------------------------------------------------------------------------------
# Parte 1
# ---------------------------------------------------------------------------------------------
def caida_de_un_servicio(obs, svc, args):
    print(f"\n== Caída de {svc} ==")
    otros = [s for s in L.CON_LED if s != svc]
    cid = L.contenedor(svc)
    if not cid:
        return {"servicio": svc, "error": "no existe el contenedor"}

    t_orden = L.ahora()
    c, out = L.docker("stop", cid, timeout=60)
    t_detenido = L.ahora()
    print(f"  docker stop: {t_detenido - t_orden:.2f} s (código {c})")

    t_caido = obs.esperar_mensaje(f"lab/estado/{svc}", "CAIDO", desde=t_orden, timeout=args.espera_caido)
    t_vivo0 = next((t for t, _, txt, ret in obs.eventos("lab/vivo", svc, desde=t_orden)
                    if txt.strip() == "0" and not ret), None)
    print(f"  CAIDO publicado a los {(t_caido - t_orden) if t_caido else float('nan'):.2f} s de la orden")

    # Se deja caído un rato (pausa), contado desde que se vio CAIDO (o desde la orden si no llegó).
    time.sleep(max(0.0, (t_caido or t_detenido) + args.pausa - L.ahora()))

    t_orden_start = L.ahora()
    c2, out2 = L.docker("start", cid, timeout=60)
    t_arrancado = L.ahora()
    t_ok = obs.esperar_mensaje(f"lab/estado/{svc}", "OK", desde=t_orden_start, timeout=args.espera_ok)
    t_vivo1 = next((t for t, _, txt, ret in obs.eventos("lab/vivo", svc, desde=t_orden_start)
                    if txt.strip() == "1"), None)
    print(f"  OK otra vez a los {(t_ok - t_orden_start) if t_ok else float('nan'):.2f} s del docker start")

    # Aislamiento de fallas: cambios de estado de los OTROS durante toda la caída.
    fin_ventana = t_ok or L.ahora()
    cambios_otros = [{"servicio": top.split("/")[-1], "estado": txt.strip(), "t_rel_s": r2(t - t_orden)}
                     for t, top, txt, ret in obs.eventos("lab/estado", desde=t_orden)
                     if not ret and t <= fin_ventana and top.split("/")[-1] in otros
                     and txt.strip() != "OK"]
    metricas_otros = {s: len([1 for t, *_ in obs.eventos("lab/metricas", s, desde=t_orden)
                              if t <= fin_ventana]) for s in otros}
    otros_siguen = not cambios_otros and all(n > 0 for n in metricas_otros.values())

    crit_caido = t_caido is not None and (t_caido - t_orden) <= args.max_deteccion
    crit_ok = t_ok is not None and (t_ok - t_orden_start) <= args.max_recuperacion
    return {
        "servicio": svc,
        "docker_stop_s": r2(t_detenido - t_orden),
        "docker_stop_codigo": c,
        "caido_desde_orden_s": r2(t_caido - t_orden) if t_caido else None,
        "caido_desde_detenido_s": r2(t_caido - t_detenido) if t_caido else None,
        "vivo0_desde_orden_s": r2(t_vivo0 - t_orden) if t_vivo0 else None,
        "docker_start_s": r2(t_arrancado - t_orden_start),
        "docker_start_codigo": c2,
        "ok_desde_start_s": r2(t_ok - t_orden_start) if t_ok else None,
        "vivo1_desde_start_s": r2(t_vivo1 - t_orden_start) if t_vivo1 else None,
        "tiempo_total_fuera_s": r2(t_ok - t_orden) if t_ok else None,
        "otros_cambios_no_ok": cambios_otros,
        "otros_mensajes_metricas": metricas_otros,
        "criterios": {
            f"CAIDO en <= {args.max_deteccion} s": "APROBADA" if crit_caido else "FALLADA",
            f"OK otra vez en <= {args.max_recuperacion} s": "APROBADA" if crit_ok else "FALLADA",
            "los otros 5 siguen OK y publicando": "APROBADA" if otros_siguen else "FALLADA",
        },
    }


def primer_ok_estable(obs, svc, desde):
    """Instante del primer OK de `svc` después de `desde` a partir del cual ya no hubo otro estado.
    (Si el admin republica OK cada pocos segundos, el ÚLTIMO mensaje no sirve: sería "ahora".)"""
    t_ok = None
    for t, _top, txt, _ret in obs.eventos("lab/estado", svc, desde=desde):
        if txt.strip() == "OK":
            t_ok = t if t_ok is None else t_ok
        else:
            t_ok = None
    return t_ok


# ---------------------------------------------------------------------------------------------
# Parte 2
# ---------------------------------------------------------------------------------------------
def inspeccionar(svc):
    cid = L.contenedor(svc)
    if not cid:
        return None
    c, out = L.docker("inspect", "-f", "{{.State.Running}}|{{.State.StartedAt}}|{{.RestartCount}}", cid)
    if c != 0:
        return None
    corr, inicio, reinicios = out.strip().split("|")
    return {"corriendo": corr == "true", "started_at": inicio, "restart_count": int(reinicios)}


def lineas_log_desde(svc, desde_epoch):
    cid = L.contenedor(svc)
    if not cid:
        return None
    c, out = L.docker("logs", "--since", str(int(desde_epoch)), cid, timeout=30)
    return len(out.splitlines()) if c == 0 else None


def visor(puerto):
    """(código HTTP, huella de la respuesta) del visor en localhost:puerto, o (None, motivo)."""
    try:
        with urllib.request.urlopen(f"http://localhost:{puerto}/", timeout=3) as r:
            cuerpo = r.read(2_000_000)
            return r.status, hashlib.sha1(cuerpo).hexdigest()[:12]
    except Exception as e:  # el visor es opcional en el contrato: que falte no es error de la prueba
        return None, type(e).__name__


def caida_del_admin(obs, args):
    print("\n== Caída del admin (broker + monitor) ==")
    zonas = [s for s in L.ZONAS if L.corriendo(s)]
    antes = {s: inspeccionar(s) for s in zonas}
    cid = L.contenedor("admin")
    t_orden = L.ahora()
    c, _ = L.docker("stop", cid, timeout=60)
    t_detenido = L.ahora()
    print(f"  admin detenido ({t_detenido - t_orden:.2f} s); observando las zonas {args.pausa_admin} s")

    muestras = []
    huellas = {s: set() for s in VISORES}
    fin = t_detenido + args.pausa_admin
    while L.ahora() < fin:
        m = {"t_rel_s": r2(L.ahora() - t_orden), "zonas": {}}
        for s in zonas:
            ins = inspeccionar(s)
            d = {"corriendo": ins["corriendo"] if ins else False,
                 "lineas_log_desde_caida": lineas_log_desde(s, t_orden)}
            if s in VISORES:
                cod, huella = visor(VISORES[s])
                d["visor_http"] = cod
                if cod:
                    huellas[s].add(huella)
            m["zonas"][s] = d
        muestras.append(m)
        time.sleep(5)
    despues = {s: inspeccionar(s) for s in zonas}

    t_orden_start = L.ahora()
    c2, _ = L.docker("start", cid, timeout=60)
    t_arrancado = L.ahora()
    print("  admin arrancado; esperando broker, métricas y estados OK")
    t_broker = None
    if obs.esperar(lambda o: any(t >= t_orden_start for t in o.conexiones), args.espera_ok):
        t_broker = min(t for t in obs.conexiones if t >= t_orden_start)   # instante real de reconexión
    t_metricas = {}
    for s in zonas:
        t = obs.esperar_mensaje(f"lab/metricas/{s}", desde=t_orden_start,
                                timeout=max(1, args.espera_ok - (L.ahora() - t_orden_start)))
        t_metricas[s] = r2(t - t_orden_start) if t else None
    # Estado OK de los 6 publicado DESPUÉS del arranque (se aceptan retenidos que llegaron en la nueva
    # suscripción, porque el admin pudo publicarlos antes de que este observador se reconectara).
    def ok_nuevo(o):
        for s in L.CON_LED:
            ev = [x for x in o.eventos("lab/estado", s, desde=t_orden_start)]
            if not ev or ev[-1][2].strip() != "OK":
                return False
        return True
    t_todos_ok = None
    if obs.esperar(ok_nuevo, max(1, args.espera_ok - (L.ahora() - t_orden_start))) is not None:
        # esperar() devuelve cuándo SE COMPROBÓ la condición, que puede ser bastante después (se
        # esperaron antes las métricas). El instante real es la llegada del último OK que faltaba.
        t_todos_ok = max(primer_ok_estable(obs, s, t_orden_start) for s in L.CON_LED)

    # Criterios
    no_reiniciados = all(antes[s] and despues[s] and despues[s]["corriendo"]
                         and antes[s]["started_at"] == despues[s]["started_at"]
                         and antes[s]["restart_count"] == despues[s]["restart_count"] for s in zonas)
    con_logs = {s: max((m["zonas"][s]["lineas_log_desde_caida"] or 0) for m in muestras) if muestras else 0
                for s in zonas}
    visores_cambian = {s: len(h) for s, h in huellas.items() if h}
    # "Siguen simulando": evidencia positiva de actividad (logs que crecen o visor que cambia). Si un
    # contenedor no imprime nada ni tiene visor, no hay forma de verlo sin MQTT: queda "sin evidencia".
    actividad = {}
    for s in zonas:
        if con_logs.get(s, 0) > 0 or visores_cambian.get(s, 0) > 1:
            actividad[s] = "con actividad"
        else:
            actividad[s] = "sin evidencia (no imprime logs ni tiene visor que cambie)"
    metricas_vuelven = all(v is not None for v in t_metricas.values())

    return {
        "docker_stop_admin_s": r2(t_detenido - t_orden),
        "duracion_caida_observada_s": args.pausa_admin,
        "muestras_durante_caida": muestras,
        "zonas_antes": antes, "zonas_despues": despues,
        "actividad_durante_caida": actividad,
        "visor_respuestas_distintas": visores_cambian,
        "docker_start_admin_s": r2(t_arrancado - t_orden_start),
        "broker_vuelve_desde_start_s": r2(t_broker - t_orden_start) if t_broker else None,
        "primera_metrica_desde_start_s": t_metricas,
        "todos_ok_desde_start_s": r2(t_todos_ok - t_orden_start) if t_todos_ok else None,
        "criterios": {
            "ninguna zona se cayó ni se reinició sin el admin": "APROBADA" if no_reiniciados else "FALLADA",
            "hay evidencia de actividad en todas las zonas": (
                "APROBADA" if all(v == "con actividad" for v in actividad.values()) else "SIN EVIDENCIA COMPLETA"),
            "las métricas de todas las zonas vuelven solas": "APROBADA" if metricas_vuelven else "FALLADA",
            f"los 6 vuelven a OK en <= {args.max_recuperacion} s": (
                "APROBADA" if t_todos_ok and (t_todos_ok - t_orden_start) <= args.max_recuperacion else "FALLADA"),
        },
    }


# ---------------------------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="Prueba de disponibilidad (caídas y recuperación)")
    ap.add_argument("--broker", default="localhost")
    ap.add_argument("--puerto", type=int, default=1883)
    ap.add_argument("--servicios", nargs="+", default=["sim-pepper", "player-2"])
    ap.add_argument("--pausa", type=float, default=10, help="s que el servicio queda caído tras el CAIDO")
    ap.add_argument("--pausa-admin", type=float, default=30, help="s que el admin queda caído")
    ap.add_argument("--espera-inicial", type=float, default=180, help="s máximos para ver los 6 en OK")
    ap.add_argument("--espera-caido", type=float, default=60)
    ap.add_argument("--espera-ok", type=float, default=180)
    ap.add_argument("--max-deteccion", type=float, default=10, help="criterio: CAIDO en <= s")
    ap.add_argument("--max-recuperacion", type=float, default=60, help="criterio: OK otra vez en <= s")
    ap.add_argument("--sin-admin", action="store_true", help="omitir la parte 2 (caída del admin)")
    args = ap.parse_args()

    problemas = L.comprobar_entorno(host=args.broker, puerto=args.puerto)
    if problemas:
        print("No se puede correr la prueba:\n  - " + "\n  - ".join(problemas))
        return 2
    obs = L.Observador(args.broker, args.puerto)
    if not obs.iniciar():
        print("No pude conectarme al broker.")
        return 2
    resultado = {"parametros": vars(args), "fecha_epoch": round(L.ahora(), 1)}
    try:
        listo, inicial = estado_inicial(obs, L.CON_LED, args.espera_inicial)
        resultado["estado_inicial"] = inicial
        print(f"estado inicial: {inicial}")
        if not listo:
            print("AVISO: no todos están en OK al empezar; se sigue igual y queda anotado.")
        resultado["caidas"] = []
        for svc in args.servicios:
            resultado["caidas"].append(caida_de_un_servicio(obs, svc, args))
            obs.esperar(todos_ok(L.CON_LED), args.espera_ok)   # que todo se asiente antes del siguiente
        if not args.sin_admin:
            resultado["caida_admin"] = caida_del_admin(obs, args)
    finally:
        # Pase lo que pase (Ctrl+C, error), nada queda apagado.
        for svc in list(args.servicios) + ["admin"]:
            if L.contenedor(svc) and not L.corriendo(svc):
                print(f"  (restaurando: docker start {svc})")
                L.docker("start", L.contenedor(svc))
        obs.parar()
    criterios = [v for c in resultado.get("caidas", []) for v in c.get("criterios", {}).values()]
    criterios += list(resultado.get("caida_admin", {}).get("criterios", {}).values())
    resultado["veredicto"] = "APROBADA" if criterios and all(v == "APROBADA" for v in criterios) else "REVISAR"
    L.guardar_json("disponibilidad.json", resultado)
    for c in resultado.get("caidas", []):
        print(f"{c['servicio']}: CAIDO a {c.get('caido_desde_orden_s')} s, OK a {c.get('ok_desde_start_s')} s "
              f"del start; {c.get('criterios')}")
    if "caida_admin" in resultado:
        a = resultado["caida_admin"]
        print(f"admin: broker a {a['broker_vuelve_desde_start_s']} s, todos OK a "
              f"{a['todos_ok_desde_start_s']} s; {a['criterios']}")
    print(f"veredicto: {resultado['veredicto']}")
    return 0 if resultado["veredicto"] == "APROBADA" else 1


if __name__ == "__main__":
    sys.exit(main())
