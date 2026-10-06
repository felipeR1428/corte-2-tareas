# Imagen del plano de administración (VLAN 3, 192.168.30.10): broker MQTT + monitor + dashboard.
#
# Se construye con el contexto en la raíz del tema (para poder copiar comun/):
#   docker build -f plano-admin/admin/Dockerfile -t t11-admin .
#
# Por qué Alpine y no python:3.11-slim como las zonas: aquí no hay PyBullet (que necesita glibc y
# ruedas de x86_64); solo Mosquitto, Python y la biblioteca estándar. Alpine trae los tres ya
# compilados en su gestor de paquetes (apk) y la imagen queda en unos 60 MB en vez de ~150 MB.
FROM alpine:3.20

# Paquetes (todos de apk, sin pip: así no hace falta compilar nada ni descargar ruedas):
#   mosquitto       el broker MQTT (versión 2.0.x en Alpine 3.20)
#   python3         intérprete para monitor.py
#   py3-paho-mqtt   cliente MQTT para Python (1.6.1 en Alpine 3.20; monitor.py soporta 1.x y 2.x)
#   iproute2        el comando `ip route replace` que usa comun/lab.py para llegar a las zonas
#                   por el router (la versión de busybox es más limitada)
#   tzdata          para que las horas del CSV salgan en la zona horaria que se le pase (TZ)
# El `ping` lo pone busybox (viene en la base de Alpine). Para mandar ICMP necesita la capacidad
# NET_RAW, que Docker da por defecto a todos los contenedores.
RUN apk add --no-cache mosquitto python3 py3-paho-mqtt iproute2 tzdata

WORKDIR /app

# comun/ es el mismo código que usan las zonas (protocolo.leer, Jitter, ContadorSecuencia y
# lab.configurar_rutas): copiarlo y no reescribirlo garantiza que el admin entiende exactamente el
# mismo formato de mensajes que mandan los contenedores y los ESP32.
COPY comun/__init__.py comun/lab.py comun/protocolo.py /app/comun/
COPY panel/ /app/panel/
COPY img/ /app/img/
COPY plano-admin/admin/mosquitto.conf /etc/mosquitto/mosquitto.conf
COPY plano-admin/admin/monitor.py plano-admin/admin/arrancar.sh /app/

# Los archivos se escriben en Windows: si alguno quedó con fin de línea CRLF, el `\r` rompe el
# shebang de arrancar.sh ("/bin/sh\r: not found"). Se limpia aquí por las dudas.
RUN sed -i 's/\r$//' /app/arrancar.sh /etc/mosquitto/mosquitto.conf && chmod +x /app/arrancar.sh \
    && mkdir -p /app/resultados

# Puertos del contrato: MQTT (TCP 1883), latidos y PING/PONG (UDP 5300), dashboard (TCP 8080).
# EXPOSE es solo documentación; los publica el compose (o `docker run -p`).
EXPOSE 1883 5300/udp 8080

# Sin búfer en la salida de Python: los mensajes de `docker logs` salen en el momento, no en bloques.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Docker considera "sano" el contenedor si el dashboard responde (lo usa `docker ps` y, si el
# compose lo pide, `depends_on: condition: service_healthy`). wget viene en busybox.
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
    CMD wget -q -O /dev/null http://127.0.0.1:8080/api/resumen.json || exit 1

CMD ["/app/arrancar.sh"]
