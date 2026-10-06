# Imagen del servidor de pista de la Zona Gamer (track-server, VLAN 1): PyBullet sin pantalla +
# WebSocket para los jugadores + visor HTTP del último cuadro + métricas al plano de administración.
#
# El contexto de construcción es la carpeta del tema (para poder copiar comun/). Desde ella:
#   docker build -f zona-gamer/servidor-pista/Dockerfile -t t11-pista .
#   docker run --rm -p 18765:8765 -p 18010:8000 -e GRABAR_S=40 -v "%cd%\resultados:/app/resultados" t11-pista
# En el stack completo lo construye y lo corre docker-compose.yml (servicio track-server).

# Python 3.11 "slim" (Debian sin extras). Se fija 3.11 porque pybullet 3.2.7 publica una rueda
# (wheel) ya compilada para cp311 manylinux x86_64: pip la instala en segundos, sin compilador.
# --platform=linux/amd64: pybullet NO trae rueda para ARM (aarch64). En un Mac con Apple Silicon la
# imagen saldría linux/arm64 y pip intentaría compilar pybullet (y fallaría); así Docker usa la
# imagen x86_64 y la corre emulada (más lento, pero funciona). Igual que en el tema 10.
FROM --platform=linux/amd64 python:3.11-slim

# iproute2         -> el comando "ip": comun/lab.py agrega la ruta hacia la VLAN 3 (admin) por el
#                     router (ip route replace 192.168.30.0/24 via 192.168.10.254).
# fonts-dejavu-core -> DejaVuSans, la fuente del panel de posiciones del video.
# --no-install-recommends evita paquetes de sobra y el rm borra el índice de apt (imagen más chica).
RUN apt-get update \
    && apt-get install -y --no-install-recommends iproute2 fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

# Librerías de Python con versiones fijas (la imagen sale igual hoy y dentro de un año; el repo no
# trackea requirements.txt, las versiones viven aquí):
#   pybullet       -> la física (racecar de pybullet_data) y el render por software (ER_TINY_RENDERER)
#   numpy          -> matrices de la cámara e imágenes
#   pillow         -> compone cada cuadro (vista cenital + panel de texto) y lo guarda en JPEG
#   imageio        -> escribe el video cuadro por cuadro
#   imageio-ffmpeg -> trae su propio binario de ffmpeg (H.264): no hace falta instalarlo por apt
#   websockets     -> el servidor WebSocket (API asyncio, websockets.asyncio.server)
#   paho-mqtt      -> métricas al broker Mosquitto del admin (comun/lab.py)
# --no-cache-dir: no guarda la caché de pip dentro de la imagen.
RUN pip install --no-cache-dir \
    pybullet==3.2.7 \
    numpy==2.2.6 \
    pillow==11.3.0 \
    imageio==2.37.0 \
    imageio-ffmpeg==0.6.0 \
    websockets==15.0.1 \
    paho-mqtt==2.1.0

WORKDIR /app

# Lo común a todas las zonas (rutas, latido, MQTT, formato de mensajes) y el código del servidor.
# Se copian al final para que cambiar el código no reinstale las librerías (Docker reutiliza las
# capas anteriores que no cambiaron).
COPY comun/ ./comun/
COPY zona-gamer/servidor-pista/pista.py zona-gamer/servidor-pista/render_pista.py \
     zona-gamer/servidor-pista/servidor_pista.py ./

# PYTHONUNBUFFERED=1: los print salen enseguida en "docker logs".
# RESULTADOS: carpeta del video y del resumen (se monta como volumen para verlos desde el PC).
ENV PYTHONUNBUFFERED=1 \
    RESULTADOS=/app/resultados

# 8765/tcp = WebSocket de los jugadores; 8000/tcp = visor HTTP del último cuadro. EXPOSE solo
# documenta: los puertos se publican con "ports" en el compose (o -p en docker run).
EXPOSE 8765/tcp 8000/tcp

ENTRYPOINT ["python", "servidor_pista.py"]
