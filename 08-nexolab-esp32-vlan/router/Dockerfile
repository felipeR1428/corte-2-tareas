# Imagen del router inter-VLAN del tema 11 (Alpine + FRR + iptables).
#
# Construir sola (desde la carpeta del tema, que es el contexto de todas las imágenes):
#   docker build -f router/Dockerfile -t dathinel/zonas-esp32-router:1.0 .
# Normalmente la construye docker-compose.yml ("docker compose build router").
#
# Qué hace este contenedor: está conectado a las tres redes (VLAN 1 gamer, VLAN 2 robótica y
# VLAN 3 administración) con la IP .254 en cada una. Con ip_forward=1 el kernel de su espacio de
# red reenvía paquetes de una interfaz a otra (eso es "ser un router"), FRR mantiene la tabla de
# rutas (zebra) y iptables decide QUÉ se reenvía: la VLAN 3 habla con las dos zonas, pero la VLAN 1
# y la VLAN 2 nunca se hablan entre sí. Ver arrancar.sh para el detalle de cada regla.

# Alpine 3.20: imagen base de ~8 MB. El router no necesita Python ni nada pesado: todo lo que hace
# (identificar interfaces, poner reglas, mandar latidos) se resuelve con el shell de busybox.
FROM alpine:3.20

# Paquetes (versiones de los repositorios de Alpine 3.20; apk no permite fijar versión exacta sin
# romperse cuando el repositorio actualiza un parche de seguridad, por eso se fija la rama 3.20):
#   frr       -> la suite de enrutamiento: zebra (gestor de la tabla de rutas del kernel), mgmtd y
#                staticd (rutas estáticas) y vtysh (la consola tipo Cisco: "show ip route").
#   iptables  -> el cortafuegos que hace cumplir el aislamiento entre VLAN (cadena FORWARD).
#   iproute2  -> "ip" completo (el de busybox no muestra todo lo de "ip -d link" ni "ip rule").
#   tini      -> init mínimo como PID 1: recoge los procesos hijos zombis (cada latido lanza un nc)
#                y pasa bien la señal de parada a los demonios de FRR.
# El "nc" de busybox (ya viene en Alpine) basta para mandar el latido UDP: no hace falta socat.
# --no-cache: apk no guarda el índice de paquetes dentro de la imagen (imagen más chica).
RUN apk add --no-cache frr iptables iproute2 tini

# Configuración base de FRR:
#   daemons    -> qué demonios arranca frrinit.sh (aquí solo los necesarios: zebra y staticd).
#   vtysh.conf -> "service integrated-vtysh-config": toda la configuración en un solo frr.conf.
# El frr.conf con los nombres reales de las interfaces se genera al arrancar (arrancar.sh), porque
# dentro del contenedor no se sabe de antemano cuál de eth0/eth1/eth2 quedó en cada VLAN.
COPY router/frr/daemons router/frr/vtysh.conf /etc/frr/
COPY router/arrancar.sh router/latido.sh /usr/local/bin/

# Los scripts pueden llegar con fin de línea de Windows (CRLF) si el repositorio se clona en
# Windows con autocrlf: el "\r" rompe el shebang ("sh\r: not found"). Se quita por si acaso.
# frr:frr es el usuario con el que corren los demonios: necesita poder leer su configuración.
RUN sed -i 's/\r$//' /usr/local/bin/arrancar.sh /usr/local/bin/latido.sh /etc/frr/daemons /etc/frr/vtysh.conf \
    && chmod +x /usr/local/bin/arrancar.sh /usr/local/bin/latido.sh \
    && chown -R frr:frr /etc/frr

# Valores por defecto (docker-compose.yml los repite para que se vean en un solo lugar):
#   SUBRED_VLAN1/2/3 -> prefijo de cada VLAN, con el que se identifica cada interfaz al arrancar.
#   ADMIN_IP / HB_PUERTO / HB_ORIGEN -> a dónde y con qué nombre manda el router su latido.
#   LOG_DESCARTES=1  -> además de contar, registrar (LOG) los paquetes vlan1<->vlan2 descartados.
ENV SUBRED_VLAN1=192.168.10 \
    SUBRED_VLAN2=192.168.20 \
    SUBRED_VLAN3=192.168.30 \
    ADMIN_IP=192.168.30.10 \
    HB_PUERTO=5300 \
    HB_ORIGEN=router \
    LOG_DESCARTES=1

# Healthcheck barato (solo lee archivos y consulta un contador): el router está sano si el kernel
# reenvía (ip_forward=1), la regla de aislamiento existe y zebra responde por vtysh.
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
    CMD [ "$(cat /proc/sys/net/ipv4/ip_forward)" = "1" ] \
        && iptables -n -L AISLAR_V1_V2 >/dev/null 2>&1 \
        && vtysh -c "show version" >/dev/null 2>&1 || exit 1

ENTRYPOINT ["/sbin/tini", "--", "/usr/local/bin/arrancar.sh"]
