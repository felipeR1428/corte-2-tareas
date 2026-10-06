"""prueba_aislamiento.py - Comprueba que las VLAN están aisladas y que el admin las ve a las dos.

Es la prueba del objetivo 4 del enunciado ("el admin observa ambas zonas sin romper su aislamiento").
Necesita el stack levantado (`docker compose --profile emulado up -d`); no necesita MQTT.

Qué se prueba y por qué de cada forma:

  Zona gamer (VLAN 1) -> zona robótica (VLAN 2), y al revés: DEBE FALLAR. Se prueba dos veces:
    a) "tal como está": player-1 no tiene ruta a 192.168.20.0/24, así que su paquete sale por la
       puerta de enlace por defecto de su red (la .1, que es el propio Docker) y Docker lo descarta
       con sus reglas DOCKER-ISOLATION (dos redes bridge distintas no se hablan).
    b) "con ruta forzada por el router": se simula a alguien que, dentro de player-1, agrega
       `ip route add 192.168.20.0/24 via 192.168.10.254` (los contenedores tienen NET_ADMIN, así que
       podría). Ahora el paquete SÍ llega al router, y lo que lo detiene es la política iptables
       FORWARD del router (vlan1<->vlan2 prohibido). Por eso se leen los contadores de DROP del router
       antes y después: deben subir. Esta es la prueba de verdad del aislamiento: no depende de que
       "falte una ruta", sino de una regla que lo prohíbe. La ruta se quita al terminar.
  Admin (VLAN 3) -> todos: DEBE FUNCIONAR, con ttl=63 en la respuesta (64 de Linux menos 1 salto =
    pasó por exactamente un enrutador) y un traceroute que muestra el salto 192.168.30.254.
  Zonas -> admin: DEBE FUNCIONAR (es por donde van los latidos y el MQTT).
  TCP además de ICMP: sim-spot -> track-server:8765 (el WebSocket de la pista) debe fallar aun con
    ruta forzada; player-1 -> track-server:8765 debe funcionar (dentro de su zona). Así se descarta
    que el bloqueo sea "solo del ping".

Salida: pruebas/resultados/aislamiento.json con APROBADA/FALLADA por caso.

Uso:
    entorno\\Scripts\\python.exe pruebas\\prueba_aislamiento.py
    entorno\\Scripts\\python.exe pruebas\\prueba_aislamiento.py --autoprueba   (sin stack: solo los parsers)
"""

import argparse
import re
import sys
import os
import time

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)

import lab_pruebas as L  # noqa: E402

IP = {s: ip for s, (_, ip) in L.SERVICIOS.items()}


# ---------------------------------------------------------------------------------------------
# Contadores de iptables del router
# ---------------------------------------------------------------------------------------------
RE_CADENA = re.compile(r"^Chain (\S+) \(policy (\S+) (\d+) packets")
RE_REGLA = re.compile(r"^\s*(\d+)\s+(\d+)\s+(\S+)\s")


def contar_drops(texto):
    """Suma los paquetes descartados en la salida de `iptables -L -v -n -x`.

    Cuenta dos cosas: (1) el contador de la POLÍTICA de una cadena cuando la política es DROP (los
    paquetes que no coincidieron con ninguna regla y cayeron al final), y (2) los contadores de cada
    regla cuyo destino es DROP o REJECT. Devuelve {"total": n, "por_cadena": {cadena: n}}.
    Ejemplo de líneas:
        Chain FORWARD (policy DROP 12 packets, 1008 bytes)
            pkts      bytes target     prot opt in     out     source               destination
               4      336 DROP       all  --  eth1   eth2    192.168.10.0/24      192.168.20.0/24
    """
    por = {}
    cadena = None
    for linea in texto.splitlines():
        m = RE_CADENA.match(linea)
        if m:
            cadena = m.group(1)
            por.setdefault(cadena, 0)
            if m.group(2) == "DROP":
                por[cadena] += int(m.group(3))
            continue
        if linea.startswith("Chain "):          # cadena de usuario: "Chain X (1 references)"
            cadena = linea.split()[1]
            por.setdefault(cadena, 0)
            continue
        m = RE_REGLA.match(linea)
        if m and cadena and m.group(3) in ("DROP", "REJECT"):
            por[cadena] += int(m.group(1))
    return {"total": sum(por.values()), "por_cadena": por}


def leer_drops_router():
    c, out = L.exec_en("router", ["iptables", "-L", "-v", "-n", "-x"])
    if c != 0:
        return None, out.strip()[-400:]
    return contar_drops(out), out


# ---------------------------------------------------------------------------------------------
# traceroute
# ---------------------------------------------------------------------------------------------
RE_SALTO = re.compile(r"^\s*(\d+)\s+(\d+\.\d+\.\d+\.\d+|\*)")


def analizar_traceroute(texto):
    """Lista de IPs de cada salto ('*' si no respondió) a partir de la salida de traceroute -n."""
    saltos = []
    for linea in texto.splitlines():
        m = RE_SALTO.match(linea)
        if m:
            saltos.append(m.group(2))
    return saltos


def traceroute_desde(servicio, ip):
    # -I: sondas ICMP eco (más simple de explicar y de permitir en iptables que UDP a puertos altos);
    # -q 1: una sonda por salto; -w 1: espera 1 s; -m 5: con 2 saltos basta, se deja margen.
    cmd = f"traceroute -n -I -q 1 -w 1 -m 5 {ip} 2>&1 || traceroute -n -q 1 -w 1 -m 5 {ip} 2>&1"
    if L.tiene_comando(servicio, "traceroute"):
        c, out = L.exec_en(servicio, ["sh", "-c", cmd], timeout=60)
    else:
        c, out = L.en_red_de(servicio, cmd, timeout=90)
    return analizar_traceroute(out), out.strip()


# ---------------------------------------------------------------------------------------------
# Rutas forzadas y TCP
# ---------------------------------------------------------------------------------------------
def forzar_ruta(servicio, red, via):
    c, out = L.en_red_de(servicio, f"ip route replace {red} via {via}", net_admin=True)
    return c == 0, out.strip()


def quitar_ruta(servicio, red, via):
    L.en_red_de(servicio, f"ip route del {red} via {via} 2>/dev/null; true", net_admin=True)


def tcp_desde(servicio, ip, puerto, espera=3):
    """True si se pudo abrir una conexión TCP desde la red de `servicio` a ip:puerto (nc -z)."""
    c, out = L.en_red_de(servicio, f"nc -z -w {espera} {ip} {puerto}", timeout=60)
    return c == 0, out.strip()


# ---------------------------------------------------------------------------------------------
# Casos
# ---------------------------------------------------------------------------------------------
class Registro:
    def __init__(self):
        self.casos = []

    def caso(self, nombre, esperado, obtenido, detalle):
        """esperado/obtenido: "OK" (hay comunicación) o "FALLA" (no la hay)."""
        aprobada = esperado == obtenido
        self.casos.append({"caso": nombre, "esperado": esperado, "obtenido": obtenido,
                           "resultado": "APROBADA" if aprobada else "FALLADA", "detalle": detalle})
        print(f"  [{'APROBADA' if aprobada else 'FALLADA '}] {nombre}: esperado {esperado}, obtenido {obtenido}")
        return aprobada


def ping_caso(reg, origen, destino_svc, esperado, etiqueta=None, ip=None):
    ip = ip or IP[destino_svc]
    r = L.ping_desde(origen, ip, n=3, espera_s=2)
    obtenido = "OK" if r["recibidos"] else "FALLA"
    nombre = etiqueta or f"ping {origen} -> {destino_svc} ({ip})"
    reg.caso(nombre, esperado, obtenido,
             {"enviados": r["enviados"], "recibidos": r["recibidos"], "rtt_ms": r["rtt_ms"],
              "ttl": r["ttl"], "via": r["via"], "salida": r["salida"]})
    return r


def correr(args):
    problemas = L.comprobar_entorno(requiere_broker=False)
    faltan = [s for s in ("router", "admin", "player-1", "sim-spot", "sim-nao", "track-server")
              if not L.corriendo(s)]
    if faltan:
        problemas.append(f"no están corriendo: {', '.join(faltan)}")
    if problemas:
        print("No se puede correr la prueba:\n  - " + "\n  - ".join(problemas))
        return 2

    reg = Registro()
    info = {}
    # --- 0. Estado del router (informativo) ---
    c, out = L.exec_en("router", ["cat", "/proc/sys/net/ipv4/ip_forward"])
    info["ip_forward"] = out.strip()
    c, out = L.exec_en("router", ["sh", "-c", "vtysh -c 'show ip route' 2>&1 | tail -n 15"])
    info["frr_show_ip_route"] = out.strip()
    c, out = L.exec_en("router", ["iptables", "-S", "FORWARD"])
    info["iptables_forward_reglas"] = out.strip().splitlines()
    print(f"router: ip_forward={info['ip_forward']}")

    drops_ini, _ = leer_drops_router()
    info["drops_inicio"] = drops_ini

    # --- 1. Zona gamer -> zona robótica, sin tocar nada: debe fallar ---
    print("\n1. VLAN 1 -> VLAN 2 tal como está (sin ruta):")
    ping_caso(reg, "player-1", "sim-nao", "FALLA")
    for s in ("sim-spot", "sim-pepper", "ctrl-spot", "ctrl-pepper", "ctrl-nao"):
        if L.corriendo(s) or s.startswith("sim"):
            ping_caso(reg, "player-1", s, "FALLA")

    print("\n2. VLAN 2 -> VLAN 1 tal como está (sin ruta):")
    ping_caso(reg, "sim-spot", "track-server", "FALLA")
    ping_caso(reg, "sim-spot", "player-1", "FALLA")

    # --- 3. Con ruta forzada por el router: lo debe frenar iptables FORWARD ---
    print("\n3. Con la ruta forzada por el router (debe bloquearlo la política FORWARD):")
    forzadas = [("player-1", L.REDES[2], L.ROUTER[1]), ("sim-spot", L.REDES[1], L.ROUTER[2])]
    antes_forzado, _ = leer_drops_router()
    try:
        for svc, red, via in forzadas:
            ok, out = forzar_ruta(svc, red, via)
            info.setdefault("rutas_forzadas", []).append({"servicio": svc, "red": red, "via": via,
                                                          "puesta": ok, "salida": out})
        ping_caso(reg, "player-1", "sim-nao", "FALLA",
                  etiqueta="ping player-1 -> sim-nao con ruta forzada via 192.168.10.254")
        ping_caso(reg, "player-1", "sim-spot", "FALLA",
                  etiqueta="ping player-1 -> sim-spot con ruta forzada via 192.168.10.254")
        ping_caso(reg, "sim-spot", "track-server", "FALLA",
                  etiqueta="ping sim-spot -> track-server con ruta forzada via 192.168.20.254")
        ok, out = tcp_desde("sim-spot", IP["track-server"], 8765)
        reg.caso("TCP sim-spot -> track-server:8765 (WebSocket) con ruta forzada", "FALLA",
                 "OK" if ok else "FALLA", {"salida": out})
    finally:
        for svc, red, via in forzadas:
            quitar_ruta(svc, red, via)
    time.sleep(1)
    despues_forzado, _ = leer_drops_router()
    if antes_forzado and despues_forzado:
        delta = despues_forzado["total"] - antes_forzado["total"]
        # 3 pings x 3 paquetes + los SYN del nc (con reintentos): al menos 9 descartes esperados.
        reg.caso("contadores DROP del router suben con los intentos VLAN1<->VLAN2", "OK",
                 "OK" if delta >= 9 else "FALLA",
                 {"antes": antes_forzado, "despues": despues_forzado, "delta_paquetes": delta,
                  "minimo_esperado": 9})
    else:
        reg.caso("contadores DROP del router suben con los intentos VLAN1<->VLAN2", "OK", "FALLA",
                 {"error": "no se pudo leer iptables en el router"})

    # --- 4. Control: dentro de la misma zona sí hay comunicación (descarta un "todo roto") ---
    print("\n4. Control dentro de la misma VLAN:")
    ok, out = tcp_desde("player-1", IP["track-server"], 8765)
    reg.caso("TCP player-1 -> track-server:8765 (misma VLAN)", "OK", "OK" if ok else "FALLA",
             {"salida": out})

    # --- 5. Admin -> todos: debe funcionar, pasando por el router (ttl 63) ---
    print("\n5. Admin (VLAN 3) -> zonas:")
    for s in ["track-server", "player-1", "player-2", "player-3", "sim-spot", "sim-pepper", "sim-nao",
              "ctrl-1", "ctrl-2", "ctrl-3", "ctrl-spot", "ctrl-pepper", "ctrl-nao"]:
        if s.startswith("ctrl") and not L.corriendo(s):
            continue
        r = ping_caso(reg, "admin", s, "OK")
        if r["recibidos"]:
            reg.caso(f"la respuesta de {s} cruzó exactamente un router (ttl 63)", "OK",
                     "OK" if r["ttl"] == [63] else "FALLA", {"ttl": r["ttl"]})

    # --- 6. Zonas -> admin ---
    print("\n6. Zonas -> admin:")
    ping_caso(reg, "player-1", "admin", "OK")
    ping_caso(reg, "sim-nao", "admin", "OK")
    ping_caso(reg, "track-server", "admin", "OK")

    # --- 7. traceroute admin -> zona ---
    print("\n7. traceroute del admin a cada zona:")
    for destino in ("sim-nao", "player-1"):
        saltos, salida = traceroute_desde("admin", IP[destino])
        esperado = [L.ROUTER[3], IP[destino]]
        reg.caso(f"traceroute admin -> {destino}: {' > '.join(esperado)}", "OK",
                 "OK" if saltos[:2] == esperado else "FALLA",
                 {"saltos": saltos, "esperado": esperado, "salida": salida})

    drops_fin, _ = leer_drops_router()
    info["drops_fin"] = drops_fin
    aprobadas = sum(c["resultado"] == "APROBADA" for c in reg.casos)
    resultado = {
        "fecha_epoch": round(L.ahora(), 1),
        "resumen": {"casos": len(reg.casos), "aprobadas": aprobadas,
                    "falladas": len(reg.casos) - aprobadas,
                    "veredicto": "APROBADA" if aprobadas == len(reg.casos) else "FALLADA"},
        "router": info,
        "casos": reg.casos,
    }
    L.guardar_json("aislamiento.json", resultado)
    print(f"\n{aprobadas}/{len(reg.casos)} casos aprobados -> {resultado['resumen']['veredicto']}")
    return 0 if aprobadas == len(reg.casos) else 1


# ---------------------------------------------------------------------------------------------
# Autoprueba sin stack: los parsers con salidas reales copiadas de busybox/iptables
# ---------------------------------------------------------------------------------------------
def autoprueba():
    iptables = """Chain INPUT (policy ACCEPT 120 packets, 9000 bytes)
    pkts      bytes target     prot opt in     out     source               destination

Chain FORWARD (policy DROP 7 packets, 588 bytes)
    pkts      bytes target     prot opt in     out     source               destination
     500    42000 ACCEPT     all  --  *      *       0.0.0.0/0            0.0.0.0/0            ctstate RELATED,ESTABLISHED
      30     2520 ACCEPT     all  --  *      *       192.168.30.0/24      0.0.0.0/0
       9      756 DROP       all  --  *      *       192.168.10.0/24      192.168.20.0/24
       2      120 REJECT     tcp  --  *      *       192.168.20.0/24      192.168.10.0/24      reject-with icmp-port-unreachable

Chain ZONAS (1 references)
    pkts      bytes target     prot opt in     out     source               destination
       4      336 DROP       all  --  *      *       0.0.0.0/0            0.0.0.0/0
"""
    d = contar_drops(iptables)
    assert d == {"total": 7 + 9 + 2 + 4, "por_cadena": {"INPUT": 0, "FORWARD": 18, "ZONAS": 4}}, d

    trace = """traceroute to 192.168.20.23 (192.168.20.23), 5 hops max, 46 byte packets
 1  192.168.30.254  0.071 ms
 2  192.168.20.23  0.105 ms
"""
    assert analizar_traceroute(trace) == ["192.168.30.254", "192.168.20.23"]
    assert analizar_traceroute(" 1  *\n 2  192.168.20.23  0.2 ms") == ["*", "192.168.20.23"]

    ping_bb = """PING 192.168.20.23 (192.168.20.23): 56 data bytes
64 bytes from 192.168.20.23: seq=0 ttl=63 time=0.412 ms
64 bytes from 192.168.20.23: seq=2 ttl=63 time=0.300 ms

--- 192.168.20.23 ping statistics ---
3 packets transmitted, 2 packets received, 33% packet loss
"""
    r = L.analizar_ping(ping_bb)
    assert (r["enviados"], r["recibidos"], r["ttl"], r["rtt_ms"]) == (3, 2, [63], [0.412, 0.3]), r
    ping_ip = """64 bytes from 192.168.30.10: icmp_seq=1 ttl=63 time=0.231 ms
3 packets transmitted, 3 received, 0% packet loss, time 2003ms"""
    r = L.analizar_ping(ping_ip)
    assert (r["enviados"], r["recibidos"], r["ttl"]) == (3, 3, [63]), r
    r = L.analizar_ping("PING x\n--- x ping statistics ---\n3 packets transmitted, 0 packets received, 100% packet loss")
    assert (r["enviados"], r["recibidos"], r["rtt_ms"]) == (3, 0, []), r
    print("autoprueba de prueba_aislamiento: parsers OK")
    return 0


def main():
    ap = argparse.ArgumentParser(description="Prueba de aislamiento entre VLAN (tema 11)")
    ap.add_argument("--autoprueba", action="store_true", help="solo prueba los parsers, sin stack")
    args = ap.parse_args()
    return autoprueba() if args.autoprueba else correr(args)


if __name__ == "__main__":
    sys.exit(main())
