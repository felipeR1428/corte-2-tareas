# Imagen del contenedor "player-N" de la Zona Gamer (VLAN 1): recibe el joystick de una ESP32 por UDP
# y lo reenvía por WebSocket al servidor de pista. La misma imagen sirve para los 3 jugadores: cuál es
# cada uno lo dicen las variables JUGADOR, NOMBRE, COLOR y ESTILO del docker-compose.yml.
#
# El contexto de construcción es la carpeta del tema (para poder copiar comun/):
#   docker build -f zona-gamer/jugador/Dockerfile -t dathinel/zonas-esp32-jugador:1.0 .
# Prueba suelta (sin el resto del stack; el servidor no existe y el player debe seguir vivo):
#   docker run --rm --name t11-jugador -p 15001:5000/udp -e JUGADOR=1 dathinel/zonas-esp32-jugador:1.0

# Python 3.11 "slim" (Debian sin extras), igual que el resto de imágenes Python del tema.
# --platform=linux/amd64: todas las imágenes del tema se fijan a x86_64 (pybullet solo trae rueda para
# esa arquitectura y así todo el stack corre igual en un Mac con Apple Silicon, emulado).
FROM --platform=linux/amd64 python:3.11-slim

# iproute2 trae el comando "ip": comun/lab.py lo usa al arrancar para agregar la ruta
# "192.168.30.0/24 via 192.168.10.254" (llegar al plano de administración por el router inter-VLAN).
# --no-install-recommends y borrar el índice de apt dejan la imagen más chica.
RUN apt-get update \
    && apt-get install -y --no-install-recommends iproute2 \
    && rm -rf /var/lib/apt/lists/*

# Librerías con versiones fijas (la imagen sale igual hoy y dentro de un año):
#   websockets 15.0.1 -> cliente WebSocket sobre asyncio (conexión al servidor de pista)
#   paho-mqtt 2.1.0   -> métricas y "vivo" hacia el broker Mosquitto del admin (comun/lab.py)
# --no-cache-dir: no guarda la caché de pip dentro de la imagen.
RUN pip install --no-cache-dir \
    websockets==15.0.1 \
    paho-mqtt==2.1.0

WORKDIR /app

# Primero lo común (latido, MQTT, parsers del protocolo) y después el programa: se copian al final
# para que cambiar el código no vuelva a instalar las librerías (Docker reutiliza esas capas).
COPY comun/ ./comun/
COPY zona-gamer/jugador/jugador.py ./

# PYTHONUNBUFFERED=1: los print salen enseguida en "docker compose logs" (importante para ver el
# diagnóstico "no llega nada por UDP" y la última línea cruda recibida).
# Valores por defecto = jugador 1; el compose los cambia para player-2 y player-3.
ENV PYTHONUNBUFFERED=1 \
    JUGADOR=1 \
    SERVIDOR_WS=ws://192.168.10.10:8765 \
    PUERTO_UDP=5000

# Documenta que escucha el CTRL de la ESP32 en UDP 5000. EXPOSE no abre nada por sí solo: el puerto
# se publica en el compose (5001/5002/5003 del PC -> 5000 de cada player).
EXPOSE 5000/udp

ENTRYPOINT ["python", "jugador.py"]
