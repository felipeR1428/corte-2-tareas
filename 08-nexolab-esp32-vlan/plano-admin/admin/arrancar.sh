#!/bin/sh
# Arranque del contenedor admin: lanza Mosquitto y el monitor, y si CUALQUIERA de los dos muere,
# termina el contenedor entero con código 1 para que Docker lo reinicie (restart: unless-stopped).
#
# Por qué no basta con lanzarlos y hacer `wait`: `wait` sin argumentos espera a que terminen TODOS;
# si muriera solo Mosquitto, el monitor seguiría vivo y el contenedor parecería sano sin broker.
# Bash tiene `wait -n` (espera al primero que termine), pero el sh de Alpine es el ash de busybox,
# que no lo tiene. Tampoco sirve preguntar en un bucle `kill -0 $PID`: un proceso que murió y aún
# no fue recogido (zombi) sigue respondiendo a kill -0.
#
# Solución: cada programa corre dentro de una subshell que, cuando el programa termina (por lo que
# sea), le manda la señal USR1 a este script ($$ dentro de una subshell sigue siendo el PID del
# script padre). El script queda dormido en `wait` y la trampa (trap) de USR1 lo despierta: detiene
# al otro programa y sale con 1. La trampa de TERM/INT es para `docker stop`: este script es el
# PID 1 del contenedor y, sin trampa, el PID 1 ignora SIGTERM y Docker tendría que matarlo a los 10 s.
set -u

detener_todo() {
    # pkill viene en busybox. -f compara con la línea de comandos completa (para encontrar el
    # python que corre monitor.py).
    pkill -TERM -x mosquitto 2>/dev/null
    pkill -TERM -f monitor.py 2>/dev/null
    sleep 1
}

# Lo primero de cada trampa es ignorar las demás señales: al detener al otro programa, su subshell
# también manda USR1, y sin esto la trampa se ejecutaría dos veces.
trap 'trap "" USR1 TERM INT; echo "[arranque] un proceso terminó: se detiene el contenedor para que Docker lo reinicie"; detener_todo; exit 1' USR1
trap 'trap "" USR1 TERM INT; echo "[arranque] docker stop: cerrando"; detener_todo; exit 0' TERM INT

# 1) Broker MQTT. Corre como root solo para arrancar y baja solo al usuario "mosquitto" (lo trae el
#    paquete de Alpine).
( mosquitto -c /etc/mosquitto/mosquitto.conf; echo "[arranque] mosquitto terminó (código $?)"; kill -USR1 $$ ) &

# 2) Monitor (latidos, ping, estados, CSV y dashboard). Se le da un segundo de ventaja al broker;
#    igual el monitor se reconecta solo si el broker tarda más.
sleep 1
( python3 /app/monitor.py; echo "[arranque] monitor.py terminó (código $?)"; kill -USR1 $$ ) &

# Dormir hasta que llegue una señal. `wait` vuelve antes de tiempo cuando llega una señal con
# trampa (la trampa se ejecuta y sale con exit); el bucle cubre el caso raro de que vuelva sin ella.
while true; do
    wait
    sleep 1
done
