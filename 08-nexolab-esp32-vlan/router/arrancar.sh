#!/bin/sh
# Arranque del router inter-VLAN: identifica interfaces, configura FRR, pone el cortafuegos y
# manda el latido al admin. Corre como proceso principal del contenedor (debajo de tini).
#
# Variables (con valor por defecto en el Dockerfile; docker-compose.yml las repite):
#   SUBRED_VLAN1/2/3  prefijo de las tres VLAN (192.168.10 / .20 / .30)
#   ADMIN_IP, HB_PUERTO, HB_ORIGEN  destino y nombre del latido
#   LOG_DESCARTES=1   también registra (LOG) los paquetes vlan1<->vlan2 descartados
set -eu

log() { echo "[router] $*"; }

# ---------------------------------------------------------------------------------------------
# 1. ¿Qué interfaz es cada VLAN?
# ---------------------------------------------------------------------------------------------
# Docker crea eth0, eth1, eth2 dentro del contenedor, pero el orden en que las conecta NO está
# garantizado (depende del orden alfabético de las redes en algunas versiones, del orden de
# "networks:" en otras, y cambia si se reconecta una red). Si las reglas dijeran "eth1 = gamer" a
# ciegas, un día podría quedar la VLAN 2 en eth1 y el aislamiento se aplicaría al revés.
# Por eso se identifica cada interfaz por la SUBRED de su IP, que sí es fija (la pone el compose).
iface_de() {
    # "ip -4 -o addr show" imprime una línea por dirección: "3: eth1    inet 192.168.10.254/24 ...".
    # Se busca la que empieza con el prefijo pedido y se devuelve el nombre de la interfaz
    # (quitando el "@ifNN" que agrega Docker a los veth).
    ip -4 -o addr show | awk -v pre="$1." '$4 ~ "^"pre {sub(/@.*/, "", $2); print $2; exit}'
}

# Docker conecta las redes casi al mismo tiempo que arranca el proceso: se espera hasta 15 s a que
# estén las tres, en vez de fallar si una llega una fracción de segundo tarde.
i=0
while :; do
    IF1=$(iface_de "$SUBRED_VLAN1"); IF2=$(iface_de "$SUBRED_VLAN2"); IF3=$(iface_de "$SUBRED_VLAN3")
    [ -n "$IF1" ] && [ -n "$IF2" ] && [ -n "$IF3" ] && break
    i=$((i + 1))
    if [ "$i" -ge 30 ]; then
        log "ERROR: no encontré las tres VLAN (vlan1='$IF1' vlan2='$IF2' vlan3='$IF3')."
        ip -4 -o addr show
        exit 1
    fi
    sleep 0.5
done
log "interfaces: vlan1_gamer=$IF1  vlan2_robotica=$IF2  vlan3_admin=$IF3"

# ip_forward lo pone docker-compose.yml con "sysctls" (es por espacio de red: no toca el PC).
# Se comprueba en vez de escribirlo aquí, porque /proc/sys es de solo lectura dentro del contenedor.
if [ "$(cat /proc/sys/net/ipv4/ip_forward)" != "1" ]; then
    log "AVISO: net.ipv4.ip_forward=0: el router NO reenviará nada. Falta 'sysctls: net.ipv4.ip_forward: 1'."
fi

# ---------------------------------------------------------------------------------------------
# 2. FRR (zebra + mgmtd + staticd)
# ---------------------------------------------------------------------------------------------
# Se genera frr.conf con los nombres reales de las interfaces y una descripción, para que
# "vtysh -c 'show interface brief'" diga qué VLAN es cada una. No se declara ninguna ruta: las
# tres redes están directamente conectadas y zebra las aprende solas del kernel (rutas "C").
cat > /etc/frr/frr.conf <<EOF
! Generado por arrancar.sh al arrancar el contenedor (las interfaces cambian de nombre).
frr defaults traditional
hostname router-intervlan
log stdout informational
service integrated-vtysh-config
!
interface $IF1
 description vlan1_gamer (192.168.10.0/24)
exit
!
interface $IF2
 description vlan2_robotica (192.168.20.0/24)
exit
!
interface $IF3
 description vlan3_admin (192.168.30.0/24)
exit
!
EOF
chown frr:frr /etc/frr/frr.conf
# Si el contenedor se reinicia (restart: unless-stopped), quedan los .pid y sockets de la corrida
# anterior en /var/run/frr y frrinit.sh creería que los demonios ya están corriendo.
rm -f /var/run/frr/*.pid /var/run/frr/*.vty /var/run/frr/*.sock 2>/dev/null || true
/usr/lib/frr/frrinit.sh start >/tmp/frr-arranque.log 2>&1 || {
    log "AVISO: FRR no arrancó bien (el reenvío y el cortafuegos siguen funcionando igual):"
    cat /tmp/frr-arranque.log
}

# ---------------------------------------------------------------------------------------------
# 3. Cortafuegos: política de la cadena FORWARD
# ---------------------------------------------------------------------------------------------
# FORWARD es la cadena por la que pasa todo paquete que el router REENVÍA de una interfaz a otra
# (no los que van dirigidos al propio router, que pasan por INPUT). Es justo donde se decide qué
# VLAN puede hablar con cuál.
R1="$SUBRED_VLAN1.0/24"; R2="$SUBRED_VLAN2.0/24"; R3="$SUBRED_VLAN3.0/24"

# Se parte de cero (por si el contenedor se reinicia con "restart: unless-stopped" y las reglas
# viejas siguieran ahí) y la política por defecto es DROP: lo que no esté permitido abajo,
# explícitamente, no pasa. Es el principio de "lista blanca": más seguro que ir prohibiendo casos.
iptables -F FORWARD
# -X (borrar la cadena) solo funciona si nadie la referencia (por eso va después de vaciar FORWARD)
# y si está VACÍA: sin el -F previo fallaría en silencio, el -N de abajo diría "la cadena ya existe"
# y con "set -e" el router se cerraría en cada reinicio.
iptables -F AISLAR_V1_V2 2>/dev/null || true
iptables -X AISLAR_V1_V2 2>/dev/null || true
iptables -P FORWARD DROP

# Cadena propia para el tráfico prohibido entre las dos zonas. Tenerla aparte sirve para dos
# cosas: (a) sus contadores (paquetes/bytes) son exactamente "lo que el aislamiento bloqueó",
# separado de cualquier otro descarte; (b) se puede registrar con LOG antes de descartar.
iptables -N AISLAR_V1_V2
if [ "${LOG_DESCARTES:-1}" = "1" ]; then
    # LOG con límite de 5 por minuto: si alguien manda una ráfaga, no se inunda el registro.
    # Nota: en Docker Desktop el kernel no suele mostrar el LOG de otros espacios de red
    # (nf_log_all_netns=0), así que la prueba se apoya en los CONTADORES, que siempre funcionan.
    iptables -A AISLAR_V1_V2 -m limit --limit 5/min -j LOG --log-prefix "T11-AISLADO-V1V2: " 2>/dev/null \
        || log "AVISO: el kernel no tiene el destino LOG; solo se cuentan los descartes."
fi
iptables -A AISLAR_V1_V2 -j DROP

# Regla 1 y 2 (van PRIMERO): vlan1 -> vlan2 y vlan2 -> vlan1 se mandan a la cadena de aislamiento.
# Están antes que la de ESTABLISHED a propósito: así ni siquiera una conexión "ya establecida"
# (que no puede existir, pero por si se agregara una regla por error) cruza entre las zonas, y
# todo intento queda contado.
iptables -A FORWARD -i "$IF1" -o "$IF2" -j AISLAR_V1_V2
iptables -A FORWARD -i "$IF2" -o "$IF1" -j AISLAR_V1_V2

# Regla 3: las respuestas de conversaciones ya permitidas pasan sin revisar todo de nuevo.
# conntrack recuerda cada flujo (también los UDP, como "pseudo-conexiones" con tiempo de vida):
# ESTABLISHED = paquetes de un flujo ya visto en ambos sentidos; RELATED = mensajes ICMP de error
# asociados (p. ej. "puerto inalcanzable" cuando el admin está caído), que conviene dejar pasar
# para que el emisor se entere enseguida en vez de esperar un tiempo de espera.
iptables -A FORWARD -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT

# Reglas 4-7: el plano de administración ve las dos zonas y las dos zonas ven al admin.
# Se exige también la subred de ORIGEN y DESTINO (no solo la interfaz) como protección contra
# suplantación: un contenedor de la VLAN 1 que se pusiera una IP falsa de otra red no pasa.
# Ambos sentidos se abren con NEW porque los dos inician tráfico: las zonas mandan latidos y
# MQTT al admin, y el admin puede iniciar un ping o una consulta hacia una zona.
iptables -A FORWARD -i "$IF1" -o "$IF3" -s "$R1" -d "$R3" -j ACCEPT   # gamer     -> admin
iptables -A FORWARD -i "$IF3" -o "$IF1" -s "$R3" -d "$R1" -j ACCEPT   # admin     -> gamer
iptables -A FORWARD -i "$IF2" -o "$IF3" -s "$R2" -d "$R3" -j ACCEPT   # robótica  -> admin
iptables -A FORWARD -i "$IF3" -o "$IF2" -s "$R3" -d "$R2" -j ACCEPT   # admin     -> robótica

# Todo lo demás (p. ej. un paquete que intente salir por la interfaz de la que vino, o con
# origen falso) cae en la política DROP de la cadena. INPUT/OUTPUT quedan en ACCEPT: el propio
# router debe poder responder ping y mandar su latido.
log "reglas FORWARD:"
iptables -n -v --line-numbers -L FORWARD
iptables -n -v -L AISLAR_V1_V2

log "tabla de rutas de FRR:"
vtysh -c "show ip route" 2>/dev/null || ip route

# ---------------------------------------------------------------------------------------------
# 4. Latido al admin (proceso principal del contenedor)
# ---------------------------------------------------------------------------------------------
# exec: el latido reemplaza a este shell, así queda como hijo directo de tini y recibe la señal
# de parada. Si el admin no está, los datagramas se pierden sin error: el router sigue igual.
log "latido HB,$HB_ORIGEN,<seq>,<t_ms> -> $ADMIN_IP:$HB_PUERTO/udp cada 1 s"
exec /usr/local/bin/latido.sh
