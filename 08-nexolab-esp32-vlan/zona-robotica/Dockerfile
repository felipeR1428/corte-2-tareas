# Imagen de la Zona Robótica (VLAN 2): UNA imagen para los tres robots (Spot, Pepper y NAO) en PyBullet.
# Cuál se simula lo elige la variable de entorno ROBOT=spot|pepper|nao al correr el contenedor.
#
# Se construye con el contexto en la carpeta del tema (para poder copiar comun/):
#   docker build -f zona-robotica/Dockerfile -t t11-robot .
# Correr uno a mano (el docker-compose.yml del tema ya lo hace con las IP de la VLAN 2):
#   docker run --rm -e ROBOT=nao -p 5103:5100/udp -p 8013:8000 t11-robot
#
# Los modelos 3D NO están en el repositorio: se bajan aquí, al construir, de sus repositorios
# originales (ver robots.md / README para las licencias).

# -----------------------------------------------------------------------------------------------------
# Etapa 1: mallas de NAO y Pepper con el instalador OFICIAL de qiBullet
# -----------------------------------------------------------------------------------------------------
# qiBullet (de SoftBank Robotics; humanoid-gym lo usa para NAO y Pepper) trae las mallas cifradas y un
# instalador compilado que solo existe hasta Python 3.9. Por eso esta etapa usa python:3.9-slim: instala
# el paquete (sin dependencias: no hace falta pybullet para extraer), corre el instalador y la imagen
# final solo copia el resultado. Esta etapa no queda dentro de la imagen final (multi-stage build).
FROM --platform=linux/amd64 python:3.9-slim AS mallas-softbank

# Aceptar la licencia de las mallas de SoftBank (EULA + CC BY-NC-ND 4.0: uso no comercial, sin
# modificarlas y sin publicarlas para que otros las copien). Construir con "si" equivale a responder
# "y" al instalador de qiBullet. Con --build-arg ACEPTO_LICENCIA_SOFTBANK=no la imagen sale SIN las
# mallas (NAO y Pepper se ven con figuras simples, misma cinemática): es la variante para publicar la
# imagen en Docker Hub sin redistribuir las mallas.
# Por defecto es "no": aceptar una licencia es decisión de quien construye la imagen, no de este archivo.
# Para verlos con sus mallas: ACEPTO_LICENCIA_SOFTBANK=si docker compose build (ver el README).
ARG ACEPTO_LICENCIA_SOFTBANK=no
RUN pip install --no-cache-dir --no-deps qibullet==1.4.6
COPY zona-robotica/instalar_mallas_softbank.py /tmp/
RUN ACEPTO_LICENCIA_SOFTBANK=${ACEPTO_LICENCIA_SOFTBANK} DESTINO=/modelos/qibullet \
    python /tmp/instalar_mallas_softbank.py

# -----------------------------------------------------------------------------------------------------
# Etapa 2: la imagen que corre la simulación
# -----------------------------------------------------------------------------------------------------
# Python 3.11 "slim": pybullet 3.2.7 publica una rueda ya compilada para cp311 manylinux x86_64, así
# pip la instala en segundos sin compilador. --platform=linux/amd64: pybullet no trae rueda para ARM;
# en un Mac M1/M2 Docker corre esta imagen x86_64 emulada (más lento, pero funciona).
FROM --platform=linux/amd64 python:3.11-slim

# iproute2          -> el comando "ip": comun/lab.py agrega la ruta hacia la VLAN 3 por el router
# fonts-dejavu-core -> la fuente DejaVu con que se escribe el panel de texto de cada cuadro
# --no-install-recommends y el rm del índice de apt: imagen más chica.
RUN apt-get update \
    && apt-get install -y --no-install-recommends iproute2 fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

# Librerías de Python con versiones fijas (la imagen sale igual hoy y dentro de un año):
#   pybullet       -> física y render por software (ER_TINY_RENDERER, sin GPU ni pantalla)
#   numpy          -> la imagen de la cámara y la cinemática inversa del Spot
#   pillow         -> compone cada cuadro (vista 3D + panel) y lo comprime a JPEG para el visor
#   imageio(+ffmpeg) -> el video mp4 opcional (GRABAR_S); imageio-ffmpeg trae su propio ffmpeg
#   paho-mqtt      -> métricas al broker del plano de administración (comun/lab.py)
RUN pip install --no-cache-dir \
    pybullet==3.2.7 \
    numpy==2.2.6 \
    pillow==11.3.0 \
    imageio==2.37.0 \
    imageio-ffmpeg==0.6.0 \
    paho-mqtt==2.1.0

# Modelo del "pequeño Spot": rex.urdf y sus mallas STL desde un commit fijo de rex-gym
# (código Apache-2.0, mallas CC BY 3.0 de KDY0523). Pesa ~2 MB; urllib, sin instalar curl.
COPY zona-robotica/descargar_modelos.py /tmp/
RUN python /tmp/descargar_modelos.py --destino /modelos/rex

# NAO y Pepper: los URDF (Apache-2.0) y, si se aceptó la licencia, sus mallas, desde la etapa 1.
COPY --from=mallas-softbank /modelos/qibullet /modelos/qibullet

WORKDIR /app
# Código: lo común a todos los contenedores (rutas, latido, MQTT, protocolo) y lo de esta zona. Va al
# final para que cambiar el código no repita las descargas (Docker reutiliza las capas de arriba).
COPY comun/ ./comun/
COPY zona-robotica/robots.py zona-robotica/sim_robot.py ./

# PYTHONUNBUFFERED=1: los print salen enseguida en "docker logs".
ENV PYTHONUNBUFFERED=1 \
    MODELOS=/modelos \
    RESULTADOS=/app/resultados \
    PUERTO_UDP=5100 \
    PUERTO_HTTP=8000

# 5100/udp: los JOINTS del ESP32; 8000/tcp: visor del último cuadro. EXPOSE solo documenta: los puertos
# se publican en el compose (5101-5103 y 8011-8013 en el PC).
EXPOSE 5100/udp 8000/tcp

ENTRYPOINT ["python", "sim_robot.py"]
