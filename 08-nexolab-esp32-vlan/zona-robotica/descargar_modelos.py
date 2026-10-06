"""Descarga el modelo REAL del "pequeño Spot" (Rex, de rex-gym) para la Zona Robótica.

De dónde sale: https://github.com/nicrusso7/rex-gym (el repositorio que nombra el enunciado), carpeta
rex_gym/util/pybullet_data/assets/urdf/. Es el URDF `rex.urdf` y sus mallas STL, que son las piezas
impresas en 3D del SpotMicro de Deok-yeon Kim (KDY0523, Thingiverse 3445283).

Licencias (se anotan en el README del tema):
  - el código y el URDF de rex-gym: Apache-2.0;
  - las mallas STL: Creative Commons Atribución 3.0 (CC BY 3.0) — se pueden usar y redistribuir
    citando al autor; por eso el archivo stl/LICENSE.txt también se baja y queda junto a las mallas.

Igual que en el taller del segundo corte (9-taller-segundo-corte/descargar_modelos.py), el modelo NO
se sube al repositorio: este script lo baja al construir la imagen Docker (paso RUN del Dockerfile)
o, si se quiere tener en Windows, a la carpeta que se le indique:

    entorno\\Scripts\\python zona-robotica\\descargar_modelos.py --destino zona-robotica\\modelos

Se baja de un COMMIT FIJO (no de "master"): si el autor cambia el repositorio mañana, la imagen sigue
saliendo idéntica (lo mismo que fijar versiones en pip). Solo usa la biblioteca estándar.
"""

import argparse
import re
import sys
import urllib.request
from pathlib import Path

REPO = "nicrusso7/rex-gym"
COMMIT = "26663048bd3c3da307714da4458b1a2a9dc81824"   # master el 2026-10-05
CARPETA = "rex_gym/util/pybullet_data/assets/urdf"
CRUDO = f"https://raw.githubusercontent.com/{REPO}/{COMMIT}/{CARPETA}/"


def bajar(url: str) -> bytes:
    """Baja una URL completa (timeout generoso: algunas mallas pesan cerca de 1 MB)."""
    with urllib.request.urlopen(url, timeout=120) as r:
        return r.read()


def mallas_del_urdf(texto: str) -> set:
    """Archivos que el URDF pide con filename="...".

    Primero se quitan los comentarios <!-- ... -->: rex.urdf deja comentado un lidar
    (stl/rplidar_main.STL) que no existe con esa mayúscula en el repositorio; si no se quitaran, la
    descarga fallaría por una malla que el robot ni siquiera usa.
    """
    sin_comentarios = re.sub(r"<!--.*?-->", "", texto, flags=re.S)
    return set(re.findall(r'filename="([^"]+)"', sin_comentarios))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--destino", default="/modelos/rex", help="carpeta donde queda rex.urdf (y stl/)")
    args = ap.parse_args()
    destino = Path(args.destino)
    destino.mkdir(parents=True, exist_ok=True)

    texto = bajar(CRUDO + "rex.urdf").decode("utf-8")
    (destino / "rex.urdf").write_text(texto, encoding="utf-8")
    pedidos = sorted(mallas_del_urdf(texto)) + ["stl/LICENSE.txt"]
    total = len(texto)
    for rel in pedidos:
        datos = bajar(CRUDO + rel)
        archivo = destino / rel
        archivo.parent.mkdir(parents=True, exist_ok=True)
        archivo.write_bytes(datos)
        total += len(datos)
    print(f"Rex (rex-gym @ {COMMIT[:7]}): rex.urdf + {len(pedidos)} archivos, {total / 1e6:.1f} MB en {destino}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except OSError as e:   # sin internet, GitHub caído...
        print("No se pudo descargar el modelo del Rex:", e)
        sys.exit(1)
