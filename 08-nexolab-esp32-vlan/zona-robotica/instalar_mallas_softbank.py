"""Instala los modelos REALES de NAO y Pepper (SoftBank Robotics) con el instalador oficial de qiBullet.

Contexto: humanoid-gym (el repositorio que nombra el enunciado para NAO y Pepper) no trae los robots:
los toma de qiBullet (https://github.com/softbankrobotics-research/qibullet), el simulador oficial de
SoftBank sobre PyBullet. qiBullet reparte las cosas así:
  - nao.urdf y pepper.urdf: licencia Apache-2.0 (libres);
  - las mallas 3D (meshes.zip, ~17 MB): vienen CIFRADAS dentro del paquete. Solo las extrae su
    "instalador" compilado (meshes_installer_3X.pyc), y únicamente si se acepta el contrato de
    licencia (EULA de SoftBank + Creative Commons BY-NC-ND 4.0: uso no comercial, sin modificar las
    mallas, y SIN publicarlas para que otros las copien).

Por qué un Python 3.9 aparte: qiBullet 1.4.6 (la última) solo trae el instalador compilado hasta
Python 3.9 (archivos .pyc de 2.7 y 3.5 a 3.9). Nuestra imagen usa 3.11 (por la rueda de pybullet), así
que el Dockerfile corre ESTE script en una etapa previa con python:3.9-slim y copia el resultado (los
URDF + las mallas ya extraídas) a la imagen final. No se descifra ni se modifica nada a mano (lo
prohíbe la licencia): se llama a la misma función que llama qibullet.tools._install_resources().

Aceptar la licencia: el instalador la imprime completa y solo extrae si se le pasa agreement=True.
Aquí eso lo decide la variable ACEPTO_LICENCIA_SOFTBANK (argumento --build-arg del Dockerfile):
  - "si"  -> se acepta (es lo que hace "pip install qibullet" + "y" en su pregunta) y se instalan;
  - otra cosa (lo de por defecto) -> no se instala nada; sim_robot.py carga los mismos URDF y los
    dibuja como un "esqueleto" legible (huesos, torso, cabeza; ver robots.urdf_esqueleto).
"""

import os
import shutil
import sys
import sysconfig

DESTINO = os.environ.get("DESTINO", "/modelos/qibullet")


def main() -> int:
    os.makedirs(DESTINO, exist_ok=True)
    # Se ubica el paquete SIN importarlo: "import qibullet" importa pybullet, que en esta etapa no
    # hace falta instalar (solo se necesitan los datos del paquete).
    datos = os.path.join(sysconfig.get_paths()["purelib"], "qibullet", "robot_data")

    # Los URDF (Apache-2.0) se copian siempre: sirven también para el sustituto sin mallas.
    for urdf in ("nao.urdf", "pepper.urdf"):
        shutil.copy2(os.path.join(datos, urdf), DESTINO)
    shutil.copy2(os.path.join(datos, "LICENSE"), os.path.join(DESTINO, "LICENSE-mallas-softbank.txt"))

    if os.environ.get("ACEPTO_LICENCIA_SOFTBANK", "no").strip().lower() != "si":
        print("ACEPTO_LICENCIA_SOFTBANK != si: NO se instalan las mallas de NAO/Pepper "
              "(sim_robot.py los dibuja como esqueleto, con la misma cinemática del URDF).")
        return 0

    # Mismo mecanismo que qibullet.tools._install_resources(): agrega la carpeta de instaladores al
    # path e importa el que corresponde a esta versión de Python.
    sys.path.insert(0, os.path.join(datos, "installers"))
    if sys.version_info[:2] != (3, 9):
        print("Este paso necesita Python 3.9 (el instalador de qiBullet solo existe hasta 3.9).")
        return 1
    import meshes_installer_39 as instalador   # noqa: E402  (se importa tras ajustar sys.path)

    # agreement=True: equivale a responder "y" a la pregunta del EULA (el texto completo de la
    # licencia queda impreso en el log del docker build).
    if not instalador._install_meshes(DESTINO, agreement=True):
        print("El instalador de qiBullet no pudo extraer las mallas.")
        return 1

    # El paquete trae también a Romeo (otro robot de SoftBank) que no se usa: se borra su carpeta
    # para no cargar la imagen con ~8 MB de más (no se modifica ninguna malla, solo no se guardan).
    shutil.rmtree(os.path.join(DESTINO, "meshes", "romeo"), ignore_errors=True)
    print("Mallas de NAO y Pepper instaladas en", DESTINO)
    return 0


if __name__ == "__main__":
    sys.exit(main())
