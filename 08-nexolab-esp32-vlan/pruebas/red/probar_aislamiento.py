"""Prueba de aislamiento entre VLAN del tema 11 (solo la red: router real + contenedores de relleno).

Qué comprueba (cada línea sale con OK / FALLO en la consola y en resultado_aislamiento.txt):
  1. vlan1 -> vlan3 y vlan2 -> vlan3 llegan (las zonas ven al plano de administración).
  2. vlan3 -> vlan1 y vlan3 -> vlan2 llegan (el admin ve las dos zonas).
  3. vlan1 -> vlan2 y vlan2 -> vlan1 NO llegan, ni con ping ni con UDP, aunque el contenedor se
     ponga a propósito una ruta hacia la otra zona por el router (caso "atacante").
  4. vlan1 -> vlan2 tampoco llega por la puerta por defecto de Docker (.1): Docker no deja pasar
     directo de un bridge a otro.
  5. Los contadores de la cadena AISLAR_V1_V2 del router suben exactamente con esos intentos.
  6. FRR está vivo: "vtysh -c 'show ip route'" muestra las tres redes como conectadas.
  7. El latido HB,router,... del router llega al admin (UDP 5300).

Uso (desde la carpeta del tema, con Docker Desktop corriendo y el stack real "nexolab-esp32" APAGADO,
porque usa las mismas subredes):
    python pruebas/red/probar_aislamiento.py              levanta, prueba, guarda y baja
    python pruebas/red/probar_aislamiento.py --no-bajar   deja los contenedores para mirar a mano

Solo biblioteca estándar: invoca el comando "docker" con subprocess.
"""

import argparse
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

AQUI = Path(__file__).resolve().parent
COMPOSE = AQUI / "docker-compose.prueba-red.yml"
PROYECTO = "t11-prueba-red"
SALIDA = AQUI / "resultado_aislamiento.txt"

lineas = []      # todo lo que se imprime, para guardarlo también en el .txt
fallos = []      # nombres de las comprobaciones que no dieron lo esperado


def decir(texto=""):
    print(texto, flush=True)
    lineas.append(texto)


def docker(*args, chequear=False, timeout=600):
    """Corre "docker <args>" y devuelve (código de salida, stdout+stderr como texto)."""
    r = subprocess.run(["docker", *args], capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=timeout)
    salida = (r.stdout or "") + (r.stderr or "")
    if chequear and r.returncode != 0:
        raise RuntimeError(f"docker {' '.join(args)} falló:\n{salida}")
    return r.returncode, salida


def en(servicio, comando):
    """Ejecuta un comando de shell dentro de un contenedor de la prueba."""
    return docker("exec", f"{PROYECTO}-{servicio}-1", "sh", "-c", comando)


def comprobar(nombre, ok):
    decir(f"  [{'OK   ' if ok else 'FALLO'}] {nombre}")
    if not ok:
        fallos.append(nombre)


def descartes_router():
    """Paquetes descartados por la cadena AISLAR_V1_V2 (contador exacto, -x sin abreviar a 'K')."""
    _, txt = en("router", "iptables -n -v -x -L AISLAR_V1_V2")
    for linea in txt.splitlines():
        campos = linea.split()
        if len(campos) >= 3 and campos[2] == "DROP":
            return int(campos[0])
    return -1


def ping(origen, ip, n=3):
    """True si llega al menos una respuesta. -W 1: espera 1 s por respuesta (no se cuelga)."""
    codigo, _ = en(origen, f"ping -c {n} -W 1 {ip}")
    return codigo == 0


def main():
    # La consola de Windows (cp1252) muestra mal las tildes: se fuerza UTF-8 en la salida.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--no-bajar", action="store_true", help="no bajar el compose de prueba al final")
    args = ap.parse_args()

    decir(f"Prueba de aislamiento entre VLAN - tema 11 - {datetime.now():%Y-%m-%d %H:%M:%S}")
    _, version = docker("version", "--format", "{{.Server.Version}} ({{.Server.Os}}/{{.Server.Arch}})")
    decir(f"Docker Engine {version.strip()}")
    decir(f"Compose de prueba: pruebas/red/{COMPOSE.name} (proyecto {PROYECTO})")
    decir()

    decir("== Levantando router real + contenedores de relleno ==")
    codigo, txt = docker("compose", "-f", str(COMPOSE), "up", "-d", "--build", "--force-recreate")
    if codigo != 0:
        decir(txt)
        decir("No se pudo levantar la prueba. ¿Está corriendo el stack real (mismas subredes)?")
        return 2

    # Se espera a que el router termine de poner las reglas (lo último que imprime antes del latido).
    for _ in range(60):
        _, logs = docker("logs", f"{PROYECTO}-router-1")
        if "latido HB" in logs:
            break
        time.sleep(0.5)
    else:
        decir(logs)
        decir("El router no terminó de arrancar en 30 s.")
        fallos.append("arranque del router")
    m = re.search(r"interfaces: .*", logs)
    decir(f"router -> {m.group(0) if m else '(no encontré la línea de interfaces)'}")
    time.sleep(1)
    decir()

    antes = descartes_router()
    decir(f"Contador de descartes AISLAR_V1_V2 al empezar: {antes} paquetes")
    decir()

    decir("== 1-2. Tráfico permitido (debe llegar) ==")
    comprobar("vlan1 player-1 (192.168.10.21) -> vlan3 admin (192.168.30.10)  ping", ping("gamer", "192.168.30.10"))
    comprobar("vlan2 sim-spot (192.168.20.21) -> vlan3 admin (192.168.30.10)  ping", ping("robot", "192.168.30.10"))
    comprobar("vlan3 admin -> vlan1 player-1 (192.168.10.21)                  ping", ping("admin", "192.168.10.21"))
    comprobar("vlan3 admin -> vlan2 sim-spot (192.168.20.21)                  ping", ping("admin", "192.168.20.21"))
    decir()

    decir("== 3. Tráfico prohibido por el router (no debe llegar) ==")
    tras_permitido = descartes_router()
    comprobar("vlan1 -> vlan2 (192.168.10.21 -> 192.168.20.21) ping, con ruta por el router",
              not ping("gamer", "192.168.20.21"))
    comprobar("vlan2 -> vlan1 (192.168.20.21 -> 192.168.10.21) ping, con ruta por el router",
              not ping("robot", "192.168.10.21"))
    # UDP como el de los ESP32 / latidos: 5 datagramas de vlan1 al puerto de joints de un robot.
    # UDP no avisa si no llega, así que lo que demuestra el bloqueo es el contador del router.
    en("gamer", "for i in 1 2 3 4 5; do echo CTRL,1,$i,0,0,50,0 | nc -u -w 1 192.168.20.21 5100; done")
    despues = descartes_router()
    intentos = 3 + 3 + 5   # 3 pings de cada lado (cada eco cuenta 1 paquete) + 5 datagramas UDP
    decir(f"  descartes AISLAR_V1_V2: {tras_permitido} -> {despues} (+{despues - tras_permitido}; "
          f"se esperaban +{intentos}: 3 pings vlan1->vlan2, 3 pings vlan2->vlan1, 5 UDP)")
    comprobar("el tráfico permitido NO sumó descartes de aislamiento", tras_permitido == antes)
    comprobar(f"el contador del router subió exactamente {intentos}", despues - tras_permitido == intentos)
    decir()

    decir("== 4. Sin ruta por el router: la puerta por defecto de Docker tampoco deja cruzar ==")
    comprobar("vlan1 -> vlan2 (192.168.10.22 -> 192.168.20.21) ping por la .1 de Docker",
              not ping("gamer-sin-ruta", "192.168.20.21"))
    comprobar("ese intento no pasó por el router (contador sin cambios)", descartes_router() == despues)
    decir()

    decir("== 5. Cortafuegos del router (iptables -n -v -L) ==")
    _, txt = en("router", "iptables -n -v --line-numbers -L FORWARD; echo; iptables -n -v -x -L AISLAR_V1_V2")
    for l in txt.rstrip().splitlines():
        decir("  " + l)
    decir()

    decir("== 6. FRR: vtysh -c 'show ip route' ==")
    codigo, rutas = en("router", "vtysh -c 'show ip route'")
    for l in rutas.rstrip().splitlines():
        decir("  " + l)
    conectadas = all(re.search(rf"C>\* 192\.168\.{n}\.0/24 is directly connected", rutas) for n in (10, 20, 30))
    comprobar("FRR (zebra) responde y ve las tres VLAN como redes conectadas", codigo == 0 and conectadas)
    _, ifs = en("router", "vtysh -c 'show interface brief'")
    for l in ifs.rstrip().splitlines():
        decir("  " + l)
    decir()

    decir("== 7. Latido del router al admin (UDP 5300) ==")
    _, hb = en("admin", "grep HB /tmp/latidos.txt | tail -5")
    for l in hb.strip().splitlines():
        decir("  recibido en 192.168.30.10: " + l)
    decir("  (el receptor de relleno es un 'nc' que se reabre tras cada datagrama y se pierde algunos;"
          " el admin real escucha sin cortes)")
    comprobar("llegan latidos HB,router,<seq>,<t_ms> al admin", "HB,router," in hb)
    decir()

    decir(f"RESULTADO: {'TODO OK' if not fallos else 'FALLÓ: ' + '; '.join(fallos)}")
    SALIDA.write_text("\n".join(lineas) + "\n", encoding="utf-8")
    print(f"(guardado en {SALIDA})")

    if not args.no_bajar:
        # Se baja siempre: estas redes usan las mismas subredes que el stack real y no pueden
        # convivir con él.
        docker("compose", "-p", PROYECTO, "down", "--remove-orphans", "-t", "2")
        print(f"compose de prueba bajado (docker compose -p {PROYECTO} down)")
    return 0 if not fallos else 1


if __name__ == "__main__":
    sys.exit(main())
