#!/bin/sh
# Latido del router: manda "HB,<origen>,<seq>,<t_ms>" por UDP al admin cada 1 s (mismo formato que
# comun/protocolo.py y que los ESP32). Solo shell de busybox + nc, sin Python: la imagen queda chica.
#
# t_ms sale de /proc/uptime (segundos desde que arrancó el kernel, con 2 decimales): es un reloj
# monótono como millis() del ESP32. Su resolución es de 10 ms, suficiente para un latido de 1 s
# (el admin lo usa para el jitter: solo le importan las DIFERENCIAS entre latidos del mismo origen).

ADMIN_IP="${ADMIN_IP:-192.168.30.10}"
HB_PUERTO="${HB_PUERTO:-5300}"
HB_ORIGEN="${HB_ORIGEN:-router}"

# Centésimas de segundo desde el arranque del kernel ("1234.56 ..." -> 123456), solo con enteros
# porque el shell de busybox no hace cuentas con decimales.
centesimas() {
    read -r up _ </proc/uptime
    echo "${up%.*}${up#*.}" | sed 's/^0*//; s/^$/0/'
}

seq=0
siguiente=$(centesimas)
while :; do
    t_ms=$(( $(centesimas) * 10 ))
    # nc -u: UDP; -w 1: a lo sumo 1 s de espera. Va en segundo plano (&) para que un admin lento
    # o caído no atrase el siguiente latido. Los nc que terminan los recoge este mismo shell (ash
    # espera a sus hijos cada vez que corre un comando en primer plano, como el sed de centesimas);
    # tini, como PID 1, recoge los que quedaran huérfanos. Medido: sin zombis tras 40 min (~2600 latidos).
    echo "HB,$HB_ORIGEN,$seq,$t_ms" | nc -u -w 1 "$ADMIN_IP" "$HB_PUERTO" >/dev/null 2>&1 &
    seq=$((seq + 1))
    # Se programa contra un reloj fijo (siguiente += 100 cs) y no con "sleep 1" a secas: así el
    # tiempo que tarda el propio envío no se acumula y el emisor no agrega jitter que no es de la red.
    siguiente=$((siguiente + 100))
    resto=$((siguiente - $(centesimas)))
    if [ "$resto" -gt 0 ]; then
        sleep "$((resto / 100)).$(printf '%02d' $((resto % 100)))"
    else
        siguiente=$(centesimas)   # se atrasó más de un período (contenedor pausado): se re-sincroniza
    fi
done
