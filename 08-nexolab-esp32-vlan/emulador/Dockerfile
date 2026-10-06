# Imagen del emulador de ESP32: reemplaza a las placas que falten (maestras gamer, maestras robot y
# la esclava de LEDs) con contenedores que mandan exactamente los mismos mensajes que el firmware.
# Una sola imagen para las 7 placas: cuál es cada una lo dice el "command" del docker-compose.yml.
#
# El contexto de construcción es la carpeta del tema (para poder copiar comun/):
#   docker build -f emulador/Dockerfile -t local/nexolab-emulador:3.0 .
#   docker run --rm local/nexolab-emulador:3.0 --rol ctrl-1 --destino 192.168.10.21:5000 --admin 192.168.30.10

# Misma base que las demás imágenes Python del tema (x86_64 fijo, ver zona-gamer/jugador/Dockerfile).
FROM --platform=linux/amd64 python:3.11-slim

# iproute2 ("ip route"): dentro del compose el emulador está en la VLAN 1 o 2 y llega al admin
# (HB y PING a 192.168.30.10:5300) por el router inter-VLAN, igual que los demás contenedores.
RUN apt-get update \
    && apt-get install -y --no-install-recommends iproute2 \
    && rm -rf /var/lib/apt/lists/*

# El emulador usa solo la biblioteca estándar (incluido su cliente WebSocket mínimo para --piloto);
# paho-mqtt (versión fija) es solo para la esclava, que sigue lab/estado/+ en el broker del admin.
RUN pip install --no-cache-dir paho-mqtt==2.1.0

WORKDIR /app
COPY comun/ ./comun/
COPY emulador/emulador_esp32.py emulador/control_http.py ./

# Sin buffer: las líneas "serie" (ENVIADO, RTT, LEDS, ESTADO) salen enseguida en docker compose logs.
ENV PYTHONUNBUFFERED=1

# Lo que va después de la imagen (o el "command" del compose) son argumentos del emulador.
ENTRYPOINT ["python", "emulador_esp32.py"]
CMD ["--help"]
