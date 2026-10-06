"""Prueba sin ESP32: las líneas que arma el firmware son EXACTAMENTE las que espera comun/protocolo.py.

Cómo funciona:
1. Compila `prueba_formato.cpp` en el PC con el compilador de Visual Studio (cl.exe). Ese .cpp incluye
   `esp32_maestra/logica.h`, el MISMO archivo que se compila para el ESP32: lo que se prueba es el
   código real del firmware, no una copia.
2. Lo ejecuta y lee cada caso que imprime.
3. Para cada línea CTRL / JOINTS / HB / PING comprueba tres cosas:
   - es idéntica, carácter por carácter, a la que arma protocolo.armar_ctrl / armar_joints /
     armar_latido con los mismos valores;
   - protocolo.leer la acepta (no devuelve None);
   - lo que lee leer() es lo que se quiso mandar (jugador, seq, t_ms, dir, vel, robot, j, botón).
4. Revisa normalizar + zona muerta, el mapeo a grados y los lectores de MANUAL y PONG contra lo
   esperado a mano.

Uso (desde la carpeta del tema):
    entorno\\Scripts\\python.exe firmware\\prueba_formato\\prueba_formato.py

Necesita Visual Studio Build Tools (cl.exe) instalado; si no lo encuentra, lo dice y termina con
código 2 (no es un fallo del firmware). El ejecutable se deja en la carpeta temporal del sistema.
"""

import glob
import os
import subprocess
import sys
import tempfile

AQUI = os.path.dirname(os.path.abspath(__file__))
TEMA = os.path.abspath(os.path.join(AQUI, "..", ".."))
sys.path.insert(0, TEMA)
from comun import protocolo  # noqa: E402


def buscar_vcvars():
    """vcvars64.bat deja listo el entorno de cl.exe (rutas de cabeceras y bibliotecas de Windows)."""
    patrones = [
        r"C:\Program Files (x86)\Microsoft Visual Studio\*\*\VC\Auxiliary\Build\vcvars64.bat",
        r"C:\Program Files\Microsoft Visual Studio\*\*\VC\Auxiliary\Build\vcvars64.bat",
    ]
    for p in patrones:
        hallados = sorted(glob.glob(p))
        if hallados:
            return hallados[-1]
    return None


def compilar_y_correr():
    vcvars = buscar_vcvars()
    if not vcvars:
        print("No encontré Visual Studio (vcvars64.bat): no se puede compilar la prueba en el PC.")
        sys.exit(2)
    salida = os.path.join(tempfile.gettempdir(), "t11_prueba_formato")
    os.makedirs(salida, exist_ok=True)
    exe = os.path.join(salida, "prueba_formato.exe")
    fuente = os.path.join(AQUI, "prueba_formato.cpp")
    # /W4 = casi todas las advertencias; /WX = una advertencia cuenta como error (así la prueba
    # también vigila que logica.h compile limpio con otro compilador distinto al del ESP32).
    orden = (f'call "{vcvars}" >nul && cl /nologo /W4 /WX /EHsc /std:c++17 "{fuente}" '
             f'/Fo"{salida}\\\\" /Fe"{exe}"')
    r = subprocess.run(orden, shell=True, capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stdout, r.stderr)
        print("FALLO: no compiló prueba_formato.cpp")
        sys.exit(1)
    r = subprocess.run([exe], capture_output=True, text=True, check=True)
    return r.stdout.splitlines()


def main():
    lineas = compilar_y_correr()
    fallos = []
    cuenta = {}

    def fallo(msg):
        fallos.append(msg)

    for ln in lineas:
        c = ln.split("|")
        tipo = c[0]
        cuenta[tipo] = cuenta.get(tipo, 0) + 1
        if tipo == "C":
            jug, seq, t, d, v, b = (int(x) for x in c[1:7])
            hecha = c[7]
            esperada = protocolo.armar_ctrl(jug, seq, t, d, v, b)
            if hecha != esperada:
                fallo(f"CTRL distinto: firmware={hecha!r} protocolo={esperada!r}")
            m = protocolo.leer(hecha)
            if not isinstance(m, protocolo.Ctrl):
                fallo(f"leer() no acepta {hecha!r}")
                continue
            lim = lambda x: max(-100, min(100, x))  # noqa: E731
            if (m.jugador, m.seq, m.t_ms, m.dir, m.vel, m.boton) != (jug, seq, t, lim(d), lim(v), b):
                fallo(f"CTRL leído distinto: {m} de {hecha!r}")
        elif tipo == "J":
            robot, seq, t = c[1], int(c[2]), int(c[3])
            j = tuple(float(x) for x in c[4:7])   # el float exacto del ESP32 (9 cifras)
            b = int(c[7])
            hecha = c[8]
            esperada = protocolo.armar_joints(robot, seq, t, j[0], j[1], j[2], b)
            if hecha != esperada:
                fallo(f"JOINTS distinto: firmware={hecha!r} protocolo={esperada!r}")
            m = protocolo.leer(hecha)
            if not isinstance(m, protocolo.Joints):
                fallo(f"leer() no acepta {hecha!r}")
                continue
            if (m.robot, m.seq, m.t_ms, m.boton) != (robot, seq, t, b) or \
                    any(abs(a - e) > 0.05 + 1e-9 for a, e in zip(m.j, j)):
                fallo(f"JOINTS leído distinto: {m} de {hecha!r}")
        elif tipo == "L":
            tl, origen, seq, t, hecha = c[1], c[2], int(c[3]), int(c[4]), c[5]
            if hecha != protocolo.armar_latido(tl, origen, seq, t):
                fallo(f"latido distinto: {hecha!r}")
            m = protocolo.leer(hecha)
            if not isinstance(m, protocolo.Latido) or (m.tipo, m.origen, m.seq, m.t_ms) != (tl, origen, seq, t):
                fallo(f"latido leído distinto: {m} de {hecha!r}")
        elif tipo == "N":
            crudo, centro, inv, zm, res = float(c[1]), float(c[2]), int(c[3]), int(c[4]), int(c[5])
            # Lo mismo calculado en Python, a mano.
            rango = (4095 - centro) if crudo >= centro else centro
            v = (crudo - centro) * 100 / rango
            e = max(-100, min(100, int(v + 0.5) if v >= 0 else -int(-v + 0.5)))
            e = -e if inv else e
            if zm and -zm < e < zm:
                e = 0
            elif zm:
                s = 1 if e > 0 else -1
                e = s * max(0, min(100, ((abs(e) - zm) * 100 + (100 - zm) // 2) // (100 - zm)))
            if res != e:
                fallo(f"normalizar({crudo}, {centro}, inv={inv}, zm={zm}) = {res}, esperaba {e}")
            if crudo in (0, 4095) and abs(res) != 100:
                fallo(f"el tope de la escala no da 100: crudo={crudo} centro={centro} -> {res}")
            if crudo == centro and res != 0:
                fallo(f"el centro no da 0: {res}")
        elif tipo == "M":
            entrada, r, a, b, cc, bt = c[1], int(c[2]), int(c[3]), int(c[4]), int(c[5]), int(c[6])
            esperado = {
                "MANUAL,10,-20,30,1": (1, 10, -20, 30, 1),
                "MANUAL,OFF": (0,),
                "MANUAL,500,-500,0,0": (1, 100, -100, 0, 0),
                "MANUAL,-100,100,0,7": (1, -100, 100, 0, 1),
            }.get(entrada, (-1,))
            obtenido = (r, a, b, cc, bt) if r == 1 else (r,)
            if obtenido != esperado:
                fallo(f"leerManual({entrada!r}) = {obtenido}, esperaba {esperado}")
        elif tipo == "P":
            entrada, ok, s, t = c[1], int(c[2]), int(c[3]), int(c[4])
            esperado = {
                "PONG,ctrl-1,5,1234": (1, 5, 1234),
                "PONG,ctrl-1,4294967295,4294967295": (1, 4294967295, 4294967295),
            }.get(entrada, (0,))
            obtenido = (ok, s, t) if ok else (ok,)
            if obtenido != esperado:
                fallo(f"leerPong({entrada!r}) = {obtenido}, esperaba {esperado}")
            # Y lo que el admin manda tiene que poder armarse con protocolo (mismo formato que PING).
            if ok and protocolo.leer(entrada) is None:
                fallo(f"protocolo.leer no acepta el PONG {entrada!r}")

    # Ejemplos para mirar a ojo.
    for ln in lineas:
        if ln.startswith(("C|1|1|", "J|spot|200|")) or ln.startswith("L|PING|ctrl-spot"):
            print("  ejemplo:", ln.split("|")[-1])
    print("casos:", ", ".join(f"{k}={v}" for k, v in sorted(cuenta.items())))
    if fallos:
        print(f"FALLARON {len(fallos)}:")
        for f in fallos[:40]:
            print("  -", f)
        sys.exit(1)
    print("OK: todas las líneas del firmware coinciden con comun/protocolo.py y leer() las acepta.")


if __name__ == "__main__":
    main()
