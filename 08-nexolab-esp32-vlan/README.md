# Tarea 8 · NexoLab 3.0 · Carreras, robótica y redes con ESP32

**Juan Felipe Romero González · Ingeniería Mecatrónica · Universidad Militar Nueva Granada**

Un laboratorio con tres zonas: una carrera multijugador en PyBullet, tres simuladores robóticos y un plano de administración que mide la comunicación y publica el estado de los servicios mediante MQTT.

Esta es la **tarea 8 del segundo corte** y se integra en [corte-2-tareas](https://github.com/felipeR1428/corte-2-tareas), dentro de `08-nexolab-esp32-vlan`. El [índice principal](../README.md) permite volver a las tareas 4, 5, 6 y 7, cuyas carpetas se conservan. Para ejecutar este trabajo, sitúate en la carpeta de la tarea 8: las rutas y los comandos de esta guía parten de ella, salvo la sección de actualización del repositorio.

**Este README contiene toda la documentación y las evidencias de la entrega.** Las capturas y las animaciones se muestran aquí; los videos originales se incluyen como archivos MP4 locales. No hay PDF, documentación repartida en otros README ni una página web para consultar las evidencias. Para revisarlas no hace falta iniciar Docker: extrae el ZIP completo y abre este archivo en un visor Markdown que muestre imágenes y GIF, o consulta el README del repositorio con sus carpetas adjuntas.

> **Alcance de las evidencias:** se ejecutaron los servicios Python, cuatro simulaciones PyBullet, Mosquitto y siete roles de ESP32 emulados. Las imágenes de los montajes son ilustraciones generadas con IA, identificadas como tales. Esta sesión no acredita placas físicas, contenedores Docker ejecutados, aislamiento 802.1Q ni publicaciones realizadas en GitHub/Docker Hub. La configuración y los procedimientos correspondientes sí están incluidos.

![Vista general de NexoLab 3.0 durante la ejecución](evidencias/capturas/01-vista-general.png)

## Contenido del README

1. [Objetivo y requisitos](#1-objetivo-y-requisitos)
2. [Arquitectura y comunicaciones](#2-arquitectura-y-comunicaciones)
3. [Funcionamiento de la carrera](#3-funcionamiento-de-la-carrera)
4. [Funcionamiento de los robots](#4-funcionamiento-de-los-robots)
5. [Monitoreo, MQTT y métricas](#5-monitoreo-mqtt-y-métricas)
6. [Conexiones de las ESP32](#6-conexiones-de-las-esp32)
7. [Instalación y ejecución](#7-instalación-y-ejecución)
8. [Cómo funcionan los códigos](#8-cómo-funcionan-los-códigos)
9. [Evidencias de la versión 3.0](#9-evidencias-de-la-versión-30)
10. [Resultados y análisis](#10-resultados-y-análisis)
11. [Reproducir las pruebas](#11-reproducir-las-pruebas)
12. [Publicación de la entrega](#12-publicación-de-la-entrega)
13. [Solución de problemas](#13-solución-de-problemas)
14. [Recursos técnicos y registro de imágenes](#14-recursos-técnicos-y-registro-de-imágenes)
15. [Conclusiones relacionadas con los objetivos](#15-conclusiones-relacionadas-con-los-objetivos)

**Para la sustentación:** la sección 1 conecta objetivos con pruebas; las secciones 3, 4 y 5 muestran los estados; la sección 8 desarrolla las funciones con sus entradas, decisiones y salidas; la sección 10 reúne los resultados. La conservación de datos se explica en 5.2–5.4 y la ejecución desde terminal en 7 y 11.

## 1. Objetivo y requisitos

### 1.1 Objetivo general

Mi objetivo es desarrollar un laboratorio distribuido que conecte señales de seis mandos ESP32 con una carrera y tres robots simulados, separe las comunicaciones en zonas gamer, robótica y administración, y supervise la disponibilidad y calidad de comunicación de los servicios mediante métricas, MQTT y una ESP32 esclava con seis indicadores. Busco que el recorrido entrada → mensaje → simulación → supervisión → evidencia pueda revisarse tanto en el código como en pruebas repetibles.

En la sesión entregada comprobé la cadena de software usando entradas emuladas, comunicaciones locales y modelos PyBullet. El diseño también incluye el firmware y el despliegue previsto para placas y redes segmentadas; su comprobación experimental requiere ejecutar esas variantes.

### 1.2 Objetivos específicos y criterio de comprobación

| ID | Objetivo específico | Prueba que permite comprobarlo | Criterio y alcance |
|---|---|---|---|
| O1 | Adquirir y convertir joystick, potenciómetros y pulsadores en un contrato común | Pruebas del protocolo y comparación de formato C++/Python; después lectura ADC física por monitor serie | El receptor interpreta el mismo jugador/robot, secuencia y valores. El ADC y la carga de placas quedan pendientes |
| O2 | Controlar tres carros desde tres fuentes UDP independientes | Observar seis carros y pausar/reanudar el mando 2 | Los tres jugadores reciben control; `player-2` frena sin señal y recupera el mando. Evidencia con mandos emulados |
| O3 | Aplicar y medir objetivos de Spot, Pepper y NAO | Enviar los tres conjuntos de objetivos manuales registrados | Comando, objetivo y medición quedan disponibles; las tres muestras cumplen el criterio de error menor de 5 unidades adoptado por el guion, con la unidad equivalente de Spot aclarada |
| O4 | Llevar los actuadores simulados a una condición de seguridad cuando se interrumpe el mando | Pausa del jugador 2 y del mando de NAO durante más de un segundo | Carro en `failsafe` y robot en `reposo`; el heartbeat puede continuar |
| O5 | Segmentar gamer, robótica y administración con una política explícita | Ejecutar los ensayos de aislamiento y revisar contadores del router; probar la variante 802.1Q cuando exista infraestructura | Bloqueo gamer ↔ robótica y comunicación prevista con administración. Configuración incluida; ensayo de red pendiente |
| O6 | Detectar degradación, caída y recuperación de servicios | Registrar latidos, sondeos y MQTT; detener/reiniciar Pepper | El estado, el motivo y el evento cambian de forma coherente; comprobado para caída/recuperación local. Degradación de red con `netem` pendiente |
| O7 | Representar los seis servicios en la esclava y en el panel | Comparar `lab/estado/<servicio>` con la confirmación de la esclava emulada | Coincidencia de estados MQTT; la luz de seis LED físicos requiere observación en la placa |
| O8 | Conservar evidencia reproducible y consultar los datos de la sesión | Descargar CSV, consultar JSON, revisar imágenes, videos y huellas SHA-256 | Archivos presentes, datos trazables y lecturas coherentes; conservación histórica separada del estado vivo |

### 1.3 Requisitos, implementación, escenario y evidencia

Esta tabla es la ruta para demostrar cada requisito. **“Registrado” se refiere a la sesión incluida**, no a una nueva prueba física. Los enlaces de video abren archivos de la entrega; las capturas y los GIF correspondientes se muestran en la sección 9.

| Requisito | Código que lo implementa | Escenario de comprobación | Evidencia concreta | Resultado y condición |
|---|---|---|---|---|
| Tres fuentes gamer independientes | `Maestra.paso` → `ReceptorUDP.datagram_received` → `ServidorPista.atender` | Tres roles envían `CTRL` a sus receptores | [Video de carrera](evidencias/videos/01-panel-y-carrera.mp4), [datos de avance](evidencias/datos/circuito_gp_movimiento.json) | Registrado: seis vehículos avanzan; tres usan el circuito de mandos UDP emulados |
| Frenar al perder el mando | `bucle_failsafe` y `ServidorPista._controlar` | Pausar `ctrl-2`, mantener vivo `player-2` | [Captura de freno](evidencias/capturas/02b-freno-seguridad.png), [estado de freno](evidencias/datos/carrera_failsafe.json) | Registrado: `failsafe=true`; no equivale a caída del servicio |
| Recuperar el carro sin recargar la escena | Recepción de un `CTRL` nuevo y `_controlar` | Reanudar `ctrl-2` | [Estado recuperado](evidencias/datos/carrera_recuperada.json), video de carrera | Registrado: `failsafe=false` y seis carros presentes |
| Recorrer un circuito cerrado y contar vueltas | `pista.punto`, `proyectar`, `Carro.contar_vuelta` | Pasar por mitad de pista y cruzar meta hacia delante | [Muestra final](evidencias/datos/circuito_gp_final.json), [registro de pruebas](evidencias/datos/pruebas_unitarias.txt) | Registrado en simulación: rivales 3 vueltas y jugadores 2; cierre geométrico probado |
| Controlar las tres entradas de cada robot | `sim_robot.main`, `Humanoide.paso`, `Spot.paso` | Objetivos manuales distintos por robot | [Video de robots](evidencias/videos/02-robots-control-manual.mp4), [Spot](evidencias/datos/robot_spot_manual.json), [Pepper](evidencias/datos/robot_pepper_manual.json), [NAO](evidencias/datos/robot_nao_manual.json) | Registrado: respuesta en PyBullet; valores y errores reunidos en sección 10 |
| Detener gesto o trote sin datos frescos | Máquina de estados de `sim_robot.main` | Pausar entradas de NAO por más de un segundo | [NAO en reposo](evidencias/capturas/05b-nao-reposo.png), [JSON de reposo](evidencias/datos/robot_nao_failsafe.json) | Registrado: `estado="reposo"`; motores físicos no ensayados |
| Clasificar disponibilidad y registrar motivo | `monitor.decidir`, `main` y `armar_resumen` | Detener y reiniciar `sim-pepper` | [Video de monitoreo](evidencias/videos/03-monitoreo-falla-recuperacion.mp4), [eventos](evidencias/datos/eventos.jsonl) | Registrado: `OK → CAIDO → OK` por señales locales |
| Traducir disponibilidad a seis indicadores | `Esclava._al_mensaje`; firmware `alRecibir` y `actualizarLeds` | Pepper caído y después recuperado | [Estado de caída](evidencias/datos/estado_caida.json), [estado recuperado](evidencias/datos/estado_recuperado.json), [indicador](evidencias/capturas/07b-led-apagado.png) | Registrado: confirmación de la esclava emulada; encendido físico pendiente |
| Medir la calidad del transporte | `Sondeo.registrar`, `Jitter.registrar`, `Origen.registrar_hb` | Eco UDP local y recepción periódica | [CSV exportado](evidencias/datos/metricas_exportadas.csv), [estado final](evidencias/datos/estado_final.json) | Medido entre procesos en loopback; cifras no trasladables al Wi-Fi |
| Separar las tres zonas | `docker-compose.yml`, `router/arrancar.sh`, `configurar_rutas` | Intentar tráfico gamer ↔ robótica y verificar acceso administrativo | `pruebas/prueba_aislamiento.py`, `pruebas/red/probar_aislamiento.py` | Implementado en configuración; no hay resultado ejecutado de Docker/aislamiento en esta sesión |
| Usar etiquetas VLAN 10, 20 y 30 | `docker-compose.vlan-real.yml` | Host Linux, interfaces `eth0.10/.20/.30` y switch compatible | Procedimiento de la sección 11.5 | Pendiente; los procesos locales y los bridges no acreditan 802.1Q |
| Presentar conexiones y una entrega reproducible | `config.h`, `tools/`, `evidencias/` y README | Revisar pines, reproducción y consistencia de archivos | [Video de conexiones](evidencias/videos/04-conexiones-esp32.mp4), [manifiesto](evidencias/MANIFIESTO_SHA256.txt), [integración](evidencias/datos/pruebas_integracion.json) | Evidencia y configuración incluidas; montajes ilustrados con IA |

La siguiente tabla resume el despliegue previsto y permite ubicar rápidamente cada parte:

| Requisito | Implementación | Comprobación incluida |
|---|---|---|
| Tres jugadores controlados por ESP32 | Tres receptores UDP y servidor común de carrera | Seis carros en movimiento; pausa y recuperación de un mando emulado |
| Tres simuladores robóticos | SpotMicro/Rex, Pepper y NAO independientes | Objetivos manuales, respuesta de articulaciones y reposo sin señal |
| Tres zonas de red | Gamer, robótica y administración en Compose | Configuración bridge y variante macvlan; aislamiento pendiente de ejecutar |
| Router y administración | Alpine, FRR, iptables, monitor Python y Mosquitto | Código y reglas incluidos; monitor y broker probados como procesos locales |
| Medición de comunicación | RTT, jitter, pérdida, latidos y eventos | CSV y JSON obtenidos en esta sesión local |
| ESP32 esclava | Seis salidas GPIO, suscripción MQTT | Recepción y confirmación MQTT de la esclava emulada |
| Interfaz del proyecto | Cinco vistas, gráficos, controles y montajes | Capturas y cuatro grabaciones nuevas |
| Publicación en GitHub y Docker Hub | Proyecto y script para publicar seis imágenes | Procedimiento incluido; enlaces reales pendientes de publicación |

### Mejoras de esta versión

- Circuito **Nexo GP de 74,3 m**, con recta de salida, horquilla, eses enlazadas y cambios de dirección.
- Asfalto continuo, pianos rojos/blancos, barreras, parrilla de salida, boxes, torre de control, gradas y vegetación.
- Carrocerías **Nexo GT** con cabina, franjas, luces, splitter y alerón; cámara de seguimiento adicional en la interfaz.
- Pilotos ajustados al trazado nuevo: anticipación más corta y reducción de velocidad según la curvatura.
- Tres ilustraciones de conexiones con aspecto realista, manteniendo las tablas de pines y el firmware.
- Un único README con funcionamiento, código, montaje, procedimientos, resultados, capturas y animaciones.

## 2. Arquitectura y comunicaciones

```mermaid
flowchart TB
    A["Administración: monitor y MQTT"] --> R["Router: FRR e iptables"]
    R --> G["Zona gamer: pista y tres jugadores"]
    R --> B["Zona robótica: tres simuladores"]
    E["Tres ESP32 con joystick"] --> G
    M["Tres ESP32 con potenciómetros"] --> B
    A --> L["ESP32 esclava: seis LED"]
```

El navegador consulta al administrador. La pasarela del panel accede a los estados y renders de cada zona mediante rutas permitidas. Los mensajes de control viajan por UDP; entre los jugadores y la pista se utiliza WebSocket; las métricas y los estados se distribuyen mediante MQTT.

### Direcciones internas de Docker

| Zona | Subred | Servicios | Router |
|---|---|---|---|
| Gamer | `192.168.10.0/24` | Pista `.10`; jugadores `.21–.23`; mandos emulados `.31–.33` | `.254` |
| Robótica | `192.168.20.0/24` | Spot `.21`, Pepper `.22`, NAO `.23`; mandos `.31–.33` | `.254` |
| Administración | `192.168.30.0/24` | Admin/MQTT `.10`; esclava emulada `.40` | `.254` |

La configuración contiene **9 contenedores de infraestructura y simulación**: router, admin, pista, tres jugadores y tres robots. El perfil `emulado` agrega siete ESP32 virtuales: **16 contenedores en total**. Se construyen seis imágenes distintas: router, admin, servidor-pista, jugador, robot y emulador.

### Puertos publicados en el computador

| Uso | Puerto del computador | Destino |
|---|---|---|
| Interfaz principal | TCP `127.0.0.1:18180` | Admin HTTP `8080` |
| Jugadores 1, 2 y 3 | UDP `5001`, `5002`, `5003` | Cada jugador, UDP `5000` |
| Spot, Pepper y NAO | UDP `5101`, `5102`, `5103` | Cada robot, UDP `5100` |
| Latidos y eco PING/PONG | UDP `5300` | Admin UDP `5300` |
| MQTT de la esclava | TCP `1883` | Mosquitto |

En Windows, las placas físicas envían a la **IPv4 del adaptador Wi-Fi del computador**, en la misma red que ellas. Las IP `192.168.10.x`, `.20.x` y `.30.x` pertenecen a las redes internas de Docker; no se colocan como destino de una placa conectada al Wi-Fi doméstico.

### Segmentación y VLAN

`docker-compose.yml` define tres redes **bridge**, rutas hacia administración mediante `.254` y reglas del router que bloquean gamer ↔ robótica. Este despliegue representa switches virtuales separados; no inserta etiquetas 802.1Q en la interfaz Wi-Fi de Windows.

`docker-compose.vlan-real.yml` permite la variante **macvlan** sobre `eth0.10`, `eth0.20` y `eth0.30`, para un host Linux y una red física configurados para ese uso. Los identificadores 802.1Q son 10, 20 y 30; “zona 1, 2 y 3” son nombres de organización. Se deben adaptar la interfaz troncal, las subredes y el switch al laboratorio.

El bloqueo de iptables cubre el tráfico que pasa por el router. Los puertos publicados en el host ofrecen otras entradas y deben incluirse en los ensayos del firewall. Declarar tres bridges no demuestra por sí solo el aislamiento de todas las rutas. Los scripts para comprobarlo se explican en la sección 11.

### Contrato de mensajes

Se emplea texto CSV en datagramas UDP:

```text
CTRL,<jugador>,<seq>,<t_ms>,<direccion>,<velocidad>,<boton>
JOINTS,<robot>,<seq>,<t_ms>,<j1>,<j2>,<j3>,<boton>
HB,<origen>,<seq>,<t_ms>
PING,<origen>,<seq>,<t_ms>
PONG,<origen>,<seq>,<t_ms>
```

`jugador` vale 1, 2 o 3; `robot` es `spot`, `pepper` o `nao`. Dirección y velocidad se normalizan entre −100 y 100. El botón vale 0 o 1. `seq` permite identificar pérdidas, duplicados y reinicios; `t_ms` es el tiempo del emisor. El eco permite medir RTT en el mismo reloj sin requerir sincronizar los relojes de las placas.

Los mandos transmiten control cada **50 ms (20 Hz)**, latidos cada **1 s** y PING cada **2 s**. El receptor valida el tipo, los campos, los números y el destino antes de actuar.

## 3. Funcionamiento de la carrera

### Circuito Nexo GP

El eje central se construye con una **B-spline cúbica uniforme cerrada**, usando 26 puntos de control y 32 muestras por tramo. La longitud calculada sobre los 832 segmentos es **74,287 m**; el asfalto mide **1,8 m de ancho**. La progresión se divide en tres sectores iguales por distancia.

La geometría incorpora una recta larga de salida, una horquilla, eses y curvas de sentidos opuestos. Los pianos, barreras, gradas y boxes hacen legible el recorrido. Los boxes son elementos visuales: esta versión no simula repostaje, reparaciones ni paradas obligatorias.

![Circuito técnico y seis vehículos](evidencias/capturas/02-carrera.png)

La función `punto(s, d)` convierte una distancia recorrida `s` y un desplazamiento lateral `d` en posición y rumbo. `proyectar(x, y)` busca el segmento más cercano y obtiene el avance sobre la pista. `curvatura(s)` estima el cambio de rumbo local. Estas funciones permiten seguir el trazado, ordenar posiciones y calcular vueltas con la misma geometría que se dibuja.

### Los seis vehículos

| Vehículos | Fuente del mando | Identificación |
|---|---|---|
| `player-1`, `player-2`, `player-3` | Joystick físico o ESP32 emulada → UDP → jugador → WebSocket | Coral, Océano y Bosque |
| `auto-1`, `auto-2`, `auto-3` | Controlador autónomo del servidor | Rivales de la simulación |

La física conserva el modelo articulado Racecar de PyBullet: motores traseros, ruedas delanteras libres y dirección mediante articulaciones. La carrocería visual Nexo GT agrega una malla perfilada, cabina, cristales, franjas, luces y aerodinámica. Estos detalles visuales no cambian el modelo de colisión del vehículo.

![Detalle real del render de los carros Nexo GT](evidencias/capturas/02c-carro-competicion.png)

La cámara general muestra la clasificación y los seis carros; la cámara de seguimiento rota entre los vehículos y se ofrece también como imagen independiente en el panel.

### Control, vueltas y seguridad

1. La placa o el emulador envía `CTRL` a su receptor jugador.
2. `jugador.py` valida el mensaje y lo reenvía por WebSocket a la pista.
3. La pista traduce dirección y velocidad a objetivos de sus motores. La física se integra a **240 pasos por segundo** y el estado se publica a **20 Hz**; la frecuencia del render es independiente.
4. Los rivales utilizan seguimiento de trayectoria *pure pursuit*: apuntan a un punto adelantado de la pista y reducen velocidad en las curvas. Los mandos emulados utilizan un observador WebSocket para producir sus controles UDP, sin modificar directamente la física.
5. Una vuelta cuenta al cruzar la meta hacia delante después de pasar por la mitad del circuito. Oscilar sobre la línea no suma vueltas. Se registran posición, distancia, sector y mejor vuelta.
6. Si falta el control durante aproximadamente un segundo, el jugador entra en `failsafe` y frena. Pausar el mando mantiene el heartbeat de la placa, por lo que el servicio puede seguir disponible mientras el carro está frenado.
7. El servidor puede recolocar un carro atascado o volcado; cada reposición incrementa `reapariciones`. Ese contador queda incluido en los datos de la evidencia.

El piloto emulado usa una anticipación entre **0,8 y 1,65 m**, ajustada a su velocidad. El controlador autónomo consulta la curvatura que tiene por delante y limita la velocidad para negociar las curvas del circuito nuevo.

### 3.1 Estados del control de un jugador

El siguiente diagrama resume las condiciones de `jugador.py` y `_controlar`. En la carrera se representan mediante tiempos, conexión y la bandera `failsafe`; los nombres del diagrama son explicativos, no un `enum` adicional del programa.

```mermaid
stateDiagram-v2
    direction TB
    state "Esperando control" as Espera
    state "Control reciente" as Activo
    state "Freno de seguridad" as Freno
    state "Recolocación" as Recoloca
    [*] --> Espera
    Espera --> Activo: CTRL válido y conexión con pista
    Espera --> Freno: Sin mando utilizable
    Activo --> Freno: Señal vencida o conexión perdida
    Freno --> Activo: Nuevo CTRL válido llega a la pista
    Activo --> Recoloca: Volcado, atascado, fuera de pista o botón prolongado
    Recoloca --> Activo: Pose restablecida y mando vigente
    Recoloca --> Freno: Mando ausente o vencido
```

**Esperar:** al iniciar, no hay un mando reciente. El carro no debe acelerar por valores antiguos. **Avanzar:** la pista recibe un control de su jugador, registra el tiempo local de llegada y lo transforma en motor y dirección. **Detenerse:** una pausa del emulador, una desconexión WebSocket o el vencimiento de la señal obliga a aplicar velocidad cero y freno. La detención es una orden al modelo físico: no significa que la velocidad medida se vuelva cero en el mismo paso.

**Continuar:** el siguiente `CTRL` utilizable sustituye la orden de seguridad; no es necesario reconstruir el mundo. **Recolocar:** el servidor restablece pose y velocidad del carro cuando detecta ciertas condiciones de recuperación. Esto incrementa `reapariciones` y debe informarse al analizar una carrera. En la muestra final entregada ese contador fue cero para los seis vehículos.

### 3.2 Estados de la conexión jugador–pista

```mermaid
stateDiagram-v2
    direction TB
    state "Sin WebSocket" as SinWS
    state "Conectando" as Conecta
    state "Sesión activa" as Sesion
    state "Espera de reintento" as Reintento
    [*] --> SinWS
    SinWS --> Conecta: Inicia bucle_ws
    Conecta --> Sesion: Conexión abierta y mensaje hola
    Conecta --> Reintento: Error o tiempo de apertura agotado
    Sesion --> Reintento: Termina envío, recepción o conexión
    Reintento --> Conecta: Espera progresiva hasta 10 segundos
```

Esta conexión es independiente de UDP y del heartbeat. `bucle_ws` reintenta con esperas de 1, 2, 4, 8 y hasta 10 s. Al cerrarse una sesión, descarta su cola de controles: reenviar órdenes viejas después de reconectar produciría movimientos que ya no corresponden al mando. El servidor mantiene su propio control de frescura, de modo que la seguridad no depende solo del cliente.

## 4. Funcionamiento de los robots

Cada robot tiene su propio receptor UDP, simulación física, render, latidos y publicación de métricas. La entrada `JOINTS` contiene tres objetivos y un botón; el simulador limita los valores al rango del modelo y aplica control de posición.

| Robot | Entrada j1 | Entrada j2 | Entrada j3 | Botón |
|---|---|---|---|---|
| SpotMicro/Rex | Altura del cuerpo mediante entrada equivalente | Cabeceo | Alabeo | Activa el movimiento de trote |
| Pepper | Hombro derecho | Codo derecho | Giro de cabeza | Activa un saludo |
| NAO | Hombro derecho | Codo derecho | Giro de cabeza | Activa un saludo |

En Spot las entradas se limitan a −20…20 y se transforman a objetivos de las patas mediante cinemática inversa. En los humanoides se utilizan `RShoulderPitch`, `RElbowRoll` y `HeadYaw`, con límites compatibles con el modelo. Los humanoides usan una base fija para observar el movimiento articulado; no se presenta una prueba de marcha bípeda.

El estado diferencia **comando recibido**, **objetivo aplicado** y **valor medido en PyBullet**. El panel representa la evolución de j1 y muestra las tres lecturas. Al dejar de recibir control durante un segundo, el robot pasa a `reposo`.

En modo emulado, mover los deslizadores y pulsar **Aplicar** modifica la fuente de `JOINTS`, por lo que la respuesta sigue la cadena UDP → simulador → telemetría. En modo físico, esos controles web se deshabilitan y los potenciómetros de las placas determinan las entradas.

### 4.1 Máquina de estados de Spot

Los nombres `reposo`, `siguiendo` y `trotando` sí corresponden al estado que publica `sim_robot.py`.

```mermaid
stateDiagram-v2
    direction TB
    [*] --> reposo
    reposo --> siguiendo: JOINTS reciente sin flanco del botón
    reposo --> trotando: JOINTS reciente con flanco del botón
    siguiendo --> trotando: Flanco de subida del botón
    trotando --> siguiendo: Nuevo flanco de subida del botón
    siguiendo --> reposo: Más de 1 segundo sin JOINTS
    trotando --> reposo: Más de 1 segundo sin JOINTS
```

En `siguiendo`, el cuerpo persigue altura, cabeceo y alabeo limitados. En `trotando`, se genera un patrón para las patas diagonales, se conserva una altura dentro del intervalo seguro y se mantiene el cuerpo derecho. En `reposo`, la altura vuelve al valor base y las inclinaciones van a cero. La amplitud del trote baja mediante una rampa: la física continúa mientras termina el paso, en lugar de congelar una pata en el aire.

El pulsador se interpreta por **flanco 0 → 1**. Mantenerlo en 1 durante varios mensajes no debe alternar el trote veinte veces por segundo. Para hacer otro cambio, tiene que llegar primero una liberación y después una nueva pulsación. Un primer paquete con botón pulsado puede iniciar directamente el trote al salir de reposo; la lógica de botón se procesa antes de la transición automática a seguimiento.

### 4.2 Máquina de estados de Pepper y NAO

```mermaid
stateDiagram-v2
    direction TB
    [*] --> reposo
    reposo --> siguiendo: JOINTS reciente sin gesto activo
    siguiendo --> gesto: Flanco de subida del botón
    reposo --> gesto: JOINTS con flanco del botón
    gesto --> siguiendo: Gesto completado a los 2.5 segundos
    siguiendo --> reposo: Más de 1 segundo sin JOINTS
    gesto --> reposo: Más de 1 segundo sin JOINTS
```

En `siguiendo` se aplican los tres objetivos del mando. En `gesto`, la función `_saludo` sustituye temporalmente algunos objetivos por una trayectoria de saludo. El gesto dura 2,5 s de tiempo de simulación y luego vuelve al seguimiento, siempre que exista una señal reciente. Una ausencia de datos lo interrumpe y lleva a `reposo`. En reposo se usa una velocidad articular limitada para regresar a la postura inicial; no se desconectan los motores de posición del modelo.

Hay dos condiciones diferentes que el panel presenta por separado: **el robot sin mando** puede estar en reposo con su proceso vivo; **el servicio robótico detenido** deja de publicar vida/métricas y puede pasar a `CAIDO` en administración. La prueba de NAO corresponde al primer caso y la interrupción deliberada de Pepper al segundo.

## 5. Monitoreo, MQTT y métricas

`monitor.py` recibe latidos y realiza sondeos. Los servicios publican además su disponibilidad y métricas por MQTT. El administrador asigna estados **OK**, **LENTO** y **CAIDO**, conserva transiciones en `eventos.jsonl` y registra muestras en `admin_metricas.csv`.

| Indicador | Cómo se obtiene | Interpretación |
|---|---|---|
| RTT | Tiempo entre la solicitud y su respuesta validada | Retardo de ida y vuelta del sondeo |
| Jitter de RTT | Promedio de diferencias absolutas entre RTT consecutivos | Variación de los tiempos de respuesta |
| Jitter de heartbeat | Filtro exponencial de variación de tránsito, factor 1/16 | Regularidad de llegada de los latidos |
| Pérdida | Huecos de secuencia o sondeos sin respuesta, según la columna | No confundir pérdida de HB con pérdida de sondeos |
| Disponibilidad de sondeo | Respuestas válidas / intentos × 100 | Disponibilidad observada por ese instrumento |
| Edad del heartbeat | Tiempo desde el último latido | Frescura de la señal del proceso |

Los valores por defecto son: heartbeat vencido a **3 s**, RTT p95 máximo **20 ms**, jitter máximo **10 ms** y pérdida máxima **5 %**; la pérdida se interpreta con un mínimo de muestras. MQTT puede informar una caída mediante el testamento LWT o la despedida del proceso. Si no existe esa señal, el monitor combina heartbeat vencido y fallo del sondeo para declarar una caída. Un heartbeat vencido con sondeo válido indica degradación, no necesariamente un proceso apagado.

El administrador publica mensajes retenidos en `lab/estado/<servicio>`. La esclava utiliza seis indicadores: **OK encendido, LENTO parpadeando, CAIDO apagado**. El firmware también representa la falta de conexión al broker. La esclava emulada publica una confirmación de los estados que recibió; el panel compara esa confirmación con el administrador. Confirmar MQTT no demuestra que un LED físico haya encendido.

**Transporte de esta prueba:** eco UDP sobre loopback. En Docker, el monitor está configurado para sondeos ICMP. Las cifras de esta entrega describen la ejecución local; no son medidas de la red Wi-Fi, del switch ni de las placas. El RSSI del emulador es un valor de prueba y no una lectura de radio.

### 5.1 Estados del supervisor y orden de decisión

`decidir(nombre)` no toma una captura como prueba de vida. Consulta señales recibidas por el monitor y devuelve **dos valores: estado y motivo**. Primero revisa caída, después degradación y finalmente normalidad. Esta prioridad evita que un RTT antiguo haga aparecer un servicio apagado simplemente como lento.

```mermaid
stateDiagram-v2
    direction TB
    state "Sin evaluación" as Inicio
    [*] --> Inicio
    Inicio --> CAIDO: MQTT vivo igual a 0 o sin HB y sondeo fallido
    Inicio --> OK: Señales suficientes y sin degradación
    Inicio --> LENTO: Vivo con degradación observada
    OK --> LENTO: RTT, jitter, pérdida o HB superan criterio
    LENTO --> OK: Señales frescas y métricas dentro del criterio
    OK --> CAIDO: Señal explícita MQTT o doble ausencia
    LENTO --> CAIDO: Señal explícita MQTT o doble ausencia
    CAIDO --> OK: Recupera señales y ventana actual sana
    CAIDO --> LENTO: Recupera señales con degradación actual
```

El monitor empieza con `?` y motivo `arrancando`. Un `lab/vivo/<servicio>` con valor `0` tiene prioridad y declara caída. Si no existe esa señal explícita, combina heartbeat no fresco con sondeo inexistente, nunca válido o con suficientes fallos consecutivos. Un solo paquete perdido no constituye por sí mismo una prueba de caída. El umbral de fallos es configurable y un sondeo que nunca respondió no se usa para castigar como `LENTO` a un proceso que mantiene su heartbeat.

Para degradación utiliza RTT p95, jitter de sondeo, jitter de heartbeat y pérdida en la ventana, con un mínimo de muestras cuando corresponde. Si el heartbeat venció pero el sondeo responde, puede señalar `LENTO`: hay conectividad con el destino, aunque el hilo del programa no esté latiendo a tiempo. El texto `motivo` permite distinguir ese caso de una pérdida alta o un RTT elevado.

Al recuperar un servicio desde `CAIDO`, `main` llama a `olvidar_fallos` y `olvidar_ventana`, vuelve a decidir y registra la transición. Así separa la caída anterior de la calidad del intervalo posterior. Se conservan los contadores acumulados que corresponden a la sesión; limpiar una ventana no borra automáticamente toda la historia escrita a disco.

### 5.2 Dónde están los datos y cómo se relacionan

Este proyecto utiliza **estructuras en memoria, archivos CSV, JSON y JSONL**. No implementa SQLite. La decisión actual se obtiene de señales vivas; el CSV y los eventos permiten revisar lo ocurrido después. Tener un CSV en la carpeta no hace que el monitor reconstruya desde él los estados o las vueltas al arrancar.

```mermaid
flowchart TB
    HB["Latidos y sondeos"] --> RAM["Origen y Sondeo en memoria"]
    MQ["Vida y métricas MQTT"] --> RAM
    RAM --> D["decidir y armar_resumen"]
    D --> WEB["HTTP y dashboard"]
    D --> CSV["admin_metricas.csv"]
    D --> CAMBIO{"¿Cambió el estado?"}
    CAMBIO -->|Sí| EVT["eventos.jsonl"]
    CAMBIO -->|Sí| TOP["lab/estado del servicio"]
    TOP --> LED["Esclava y seis indicadores"]
    WEB --> EXPORT["JSON y CSV descargados"]
```

| Conjunto | Identificación y campos principales | Cómo se produce y se consulta |
|---|---|---|
| `origenes` en memoria | Nombre de origen, IP, último HB, secuencia, contadores y jitter | `atender_datagrama` actualiza `Origen`; `armar_resumen` lo expone en `hb` |
| `sondeos` en memoria | Nombre, destino, intentos, respuestas, RTT, fallos seguidos y ventana | `hilo_ping` registra en `Sondeo`; el resumen expone `ping` o `sondeos_extra` |
| `vivos` y `metricas` en memoria | Nombre de servicio, valor/carga MQTT y tiempo de recepción | Callback de Paho; la web recibe el dato y su edad |
| `estados` en memoria | Servicio, estado, motivo, `desde`, última publicación | `decidir` y `main`; se consulta por JSON y se publica por MQTT |
| `admin_metricas.csv` | Servicio y momento de la muestra, estado, motivo, HB, RTT y disponibilidad | Una fila por servicio, en cada tanda del monitor; descarga en `/api/metricas.csv` |
| `eventos.jsonl` | `t`, `servicio`, `anterior`, `estado`, `motivo` | Una línea JSON por cambio de estado; lectura histórica directa del archivo |
| `estado_*.json` | Resumen del monitor completo en un instante | Herramienta de evidencias o descarga del usuario; no es una base que consulte continuamente el panel |
| `robot_*.json` y `circuito_gp_*.json` | Robot o carro, comando/objetivo/medición, tiempo y contadores | Capturas del estado de las simulaciones; permiten contrastar las imágenes con números |

El campo `servicio` conecta de manera lógica muestras y eventos; el tiempo permite relacionarlos con una interrupción. No hay claves foráneas, transacciones SQL ni una tabla de sesiones: si se concatenan distintas ejecuciones en el mismo CSV, `t_s` puede empezar otra vez desde cero. Para una comparación ordenada conviene archivar una carpeta por sesión junto con su fecha, configuración y alcance.

El CSV guarda 19 columnas. `fecha` es una fecha legible y `t_s` el tiempo desde el inicio del monitor; `vlan` es la zona configurada, no una captura de etiqueta 802.1Q. `hb_recibidos`/`hb_perdidos` son contadores; `hb_perdida_60s_pct` pertenece a la ventana. `ping_ultimo_ms`, `ping_prom_ms`, `ping_p95_ms` y `ping_max_ms` describen RTT. `disp_60s_pct` y `disp_total_pct` diferencian ventana y acumulado. Una celda vacía expresa que no hay medida disponible; no se debe convertir automáticamente en cero.

### 5.3 Qué permanece después de reiniciar

| Elemento | Después de reiniciar el proceso o contenedor | Límite de conservación |
|---|---|---|
| CSV y JSONL de `resultados/` | Se conservan si el directorio existe; el monitor abre en modo añadir y escribe la cabecera CSV solo si hace falta | Compose monta `./resultados:/app/resultados`; se pierden si se borra esa carpeta del host |
| Datos de `evidencias/` de esta entrega | Permanecen como archivos del repositorio | Regenerar evidencias sobrescribe rutas; hacer copia antes de obtener una sesión nueva |
| Ventanas de RTT/HB, estado actual y cola de eventos del monitor | Se crean otra vez y se llenan con nuevas señales | El monitor no importa automáticamente el CSV/JSONL anterior |
| Retenidos MQTT | Sobreviven a una nueva suscripción mientras el broker conserva su memoria | `mosquitto.conf` usa `persistence false`: reiniciar el broker pierde sus retenidos |
| Estado de la esclava | Se reconstruye al suscribirse a los seis tópicos retenidos | Tras más de 5 s sin broker, el firmware marca datos desconocidos y muestra el patrón de falta de conexión |
| Vueltas, mandos y pose de simulación | Se inicializan al reiniciar la pista o el robot | No se carga una partida guardada desde los JSON de evidencia |
| Historia de gráficos del navegador | Se llena al consultar de nuevo | Los últimos valores guardados en JavaScript no constituyen archivo histórico permanente |

Los mensajes retenidos ayudan a una esclava que se reconecta al **mismo broker en marcha**. No son persistencia de disco en esta configuración. Después de reiniciar el broker, los clientes vuelven a publicar vida y el monitor fuerza la republicación de estados; el refresco normal de estados también ocurre cada 10 s. El CSV se vacía hacia el archivo con `flush` en cada tanda; esto permite leer lo escrito sin esperar al cierre, aunque no es una garantía de transacción ante cualquier fallo del almacenamiento.

### 5.4 Ejemplo de consulta desde la terminal

Con el panel funcionando, en Git Bash o Linux:

```bash
curl --fail http://127.0.0.1:18180/api/resumen.json
curl --fail http://127.0.0.1:18180/api/zona/pepper/estado
curl --fail -o metricas-consulta.csv http://127.0.0.1:18180/api/metricas.csv
```

La primera consulta devuelve la disponibilidad y las métricas del monitor; la segunda devuelve el estado articulado de Pepper; la tercera descarga el CSV existente. No son tres lecturas atómicas del mismo instante. Para trabajar con la sesión incluida sin arrancar servicios, este ejemplo obtiene las transiciones de Pepper de su JSONL:

```python
import json
from pathlib import Path

ruta = Path("evidencias/datos/eventos.jsonl")
for linea in ruta.read_text(encoding="utf-8").splitlines():
    evento = json.loads(linea)
    if evento["servicio"] == "sim-pepper":
        print(evento["t"], evento["anterior"], "->", evento["estado"], evento["motivo"])
```

Se pueden identificar las marcas de la caída deliberada y la recuperación antes de restarlas. No se mezclan con los cambios `? → CAIDO → OK` del arranque. Si se analiza `admin_metricas.csv` con `csv.DictReader`, los números llegan como texto: conviértelos solo cuando la celda tenga contenido y conserva la distinción entre dato ausente y valor cero.

## 6. Conexiones de las ESP32

Se mantienen los pines definidos en el firmware. El montaje completo utiliza **seis ESP32 maestras y una esclava**, tres joysticks, nueve potenciómetros de 10 kΩ, tres pulsadores para robótica, seis LED y seis resistencias de 220 Ω, además de protoboards, cables y alimentación USB.

**Las tablas de esta sección y los archivos `config.h` son la referencia para cablear.** Las imágenes tienen apariencia realista, pero son ilustraciones de orientación generadas con IA: la posición aparente de un cable o la serigrafía dibujada no sustituye la identificación real de los GPIO de la placa. No son fotografías de un montaje probado.

### 6.1 Maestra gamer: tres montajes iguales

| Pin ESP32 | Conexión en KY-023 | Función |
|---|---|---|
| GPIO 34 | VRx | Dirección |
| GPIO 35 | VRy | Acelerador y reversa |
| GPIO 32 | SW | Pulsador activo en bajo, `INPUT_PULLUP` |
| 3V3 | VCC, a veces rotulado `+5V` en el módulo | Alimentación del módulo a **3,3 V** |
| GND | GND | Tierra común |

![Ilustración realista del ESP32 con joystick](img/montaje-realista-gamer.png)

No alimentes este montaje del joystick a 5 V: sus salidas analógicas llegan directamente a las entradas del ESP32. Al encender, deja el joystick en reposo para que el firmware promedie las 64 muestras de calibración. GPIO 39 no se utiliza en este rol.

### 6.2 Maestra robótica: una para cada robot

| Pin ESP32 | Conexión | Función |
|---|---|---|
| GPIO 34 | Cursor central del potenciómetro 1 | j1 |
| GPIO 35 | Cursor central del potenciómetro 2 | j2 |
| GPIO 39 / VN | Cursor central del potenciómetro 3 | j3 |
| GPIO 32 | Un terminal del pulsador; el otro a GND | Trote o saludo |
| 3V3 | Un extremo de cada potenciómetro | Referencia superior |
| GND | El otro extremo de cada potenciómetro | Tierra común |

![Ilustración realista del ESP32 con tres potenciómetros](img/montaje-realista-robot.png)

El firmware robótico usa `CANAL_C_CONECTADO=1`. Si el montaje disponible tiene solo dos potenciómetros, cambia esa opción a **0** para que j3 permanezca en cero y GPIO 39 sin conectar no introduzca ruido. Los potenciómetros no calibran su centro al iniciar: utilizan la mitad del rango ADC como referencia.

### 6.3 Esclava de seis indicadores

| GPIO | LED | Servicio |
|---|---|---|
| 16 | 1 | `player-1` |
| 17 | 2 | `player-2` |
| 18 | 3 | `player-3` |
| 19 | 4 | `sim-spot` |
| 21 | 5 | `sim-pepper` |
| 22 | 6 | `sim-nao` |

Cada circuito sigue: **GPIO → ánodo del LED → cátodo → resistencia propia de 220 Ω → GND**. Une GND de la ESP32 a la línea común de la protoboard. No compartas una sola resistencia entre varios LED. Como referencia, con un LED de caída aproximada de 2 V, la corriente es `(3,3 − 2) / 220 ≈ 5,9 mA`; depende del LED usado.

![Ilustración realista de la esclava con seis indicadores](img/montaje-realista-esclava.png)

Los GPIO 34, 35 y 39 son entradas ADC1. GPIO 2 se utiliza como indicador integrado de estado en el firmware. Verifica el modelo de placa: algunas variantes WROVER utilizan GPIO 16/17 para PSRAM y requieren adaptar esas dos salidas en `PINES_LEDS` y en el cableado.

### 6.4 Configurar y cargar el firmware

Este proyecto usa **Arduino/C++**, no archivos MicroPython para Thonny.

1. Abre `firmware/esp32_maestra/esp32_maestra.ino` en Arduino IDE con soporte para ESP32. Selecciona la placa y su puerto serie.
2. En `config.h`, cambia `WIFI_SSID`, `WIFI_CLAVE` y los cuatro valores `IP_PC_0` a `IP_PC_3` por la IPv4 Wi-Fi del computador. Las placas y el computador deben estar en la misma red de 2,4 GHz, sin aislamiento entre clientes.
3. Cambia `ROL` antes de cargar cada maestra, según la tabla siguiente. Conserva `MODO_DEMO=0` para leer los componentes físicos.
4. Carga `firmware/esp32_esclava_leds/esp32_esclava_leds.ino` en la séptima placa. Configura en su propio `config.h` la misma red e IP; instala la biblioteca **PubSubClient** requerida por ese sketch.
5. Abre el monitor serie a **115200 baudios** y verifica Wi-Fi, destino, envío de mensajes y respuestas.
6. `USAR_IP_FIJA=0` usa DHCP. Para direcciones estáticas, activa la opción y elige direcciones libres de la red física, compatibles con el router y sin duplicar otros equipos. No copies las IP internas de Docker a la red Wi-Fi.

| Placa | `ROL` | Puerto UDP de control del computador |
|---|---|---:|
| Gamer 1 | `CTRL_1` | 5001 |
| Gamer 2 | `CTRL_2` | 5002 |
| Gamer 3 | `CTRL_3` | 5003 |
| Spot | `CTRL_SPOT` | 5101 |
| Pepper | `CTRL_PEPPER` | 5102 |
| NAO | `CTRL_NAO` | 5103 |

`firmware/compilar.ps1` ofrece una alternativa con Arduino CLI para compilar los seis roles. `firmware/prueba_formato/` permite comprobar el formato serializado fuera de una placa; esto no reemplaza la compilación y prueba física del sketch.

## 7. Instalación y ejecución

### 7.1 Iniciar con Docker Desktop en Windows

Docker Desktop debe estar instalado y con su motor iniciado. La primera construcción necesita internet para descargar imágenes base, bibliotecas y modelos. Extrae el ZIP en una carpeta independiente de la actividad del enjambre ACO.

1. Abre Docker Desktop y espera a que esté listo.
2. Abre PowerShell en la carpeta que contiene este README y `docker-compose.yml`. Por ejemplo, si la extrajiste en el escritorio:

```powershell
cd "$env:USERPROFILE\Desktop\08-NexoLab-ESP32-VLAN"
.\iniciar.ps1 -Modo emulado -Reconstruir
```

3. Si prefieres escribir los comandos directamente:

```powershell
$env:NEXO_MODO = "emulado"
docker compose --profile emulado up -d --build
```

4. Abre **http://127.0.0.1:18180**. Espera a que lleguen los primeros latidos y renders. El puerto 18180 evita reutilizar 8080 y el 18080 de la actividad anterior.
5. Revisa **Vista general**, **Zona de carreras**, **Zona robótica**, **Red y monitoreo** y **Montajes ESP32**.

Si PowerShell bloquea el archivo descargado, revisa su contenido y desbloquea solo ese script con `Unblock-File .\iniciar.ps1`, o usa los dos comandos directos anteriores.

### 7.2 Iniciar después con el botón Run de Docker

Después de construir e iniciar el proyecto por primera vez, entra en **Docker Desktop → Containers → nexolab-esp32** y usa **▶ Start/Run** en el grupo existente. Abre después `http://127.0.0.1:18180`.

El botón inicia los contenedores que ya existen. Si cambias el código, el modo o la configuración de Compose, vuelve a ejecutar `iniciar.ps1` con las opciones correspondientes. Para incorporar esta versión a una carpeta anterior, copia también el código y ejecuta `-Reconstruir`; iniciar una imagen vieja no incorpora los cambios de la pista.

### 7.3 Conectar placas físicas

Tras completar la sección 6, inicia:

```powershell
.\iniciar.ps1 -Modo fisico
```

El lanzador detiene los siete emuladores para que no compitan con las placas reales. El panel deshabilita el control manual de entradas emuladas. Verifica en `ipconfig` la IPv4 del Wi-Fi, comprueba que coincide con el firmware y observa los latidos y mensajes en el monitor serie.

Si el firewall de Windows bloquea el tráfico, crea reglas específicas para el laboratorio. En **PowerShell como administrador**, con el computador conectado a la red de las placas:

```powershell
New-NetFirewallRule -DisplayName "NexoLab UDP ESP32" -Direction Inbound -Action Allow -Protocol UDP -LocalPort 5001,5002,5003,5101,5102,5103,5300 -RemoteAddress LocalSubnet -Profile Any
New-NetFirewallRule -DisplayName "NexoLab MQTT ESP32" -Direction Inbound -Action Allow -Protocol TCP -LocalPort 1883 -RemoteAddress LocalSubnet -Profile Any
```

Estas reglas se limitan a los puertos usados y a la subred local. No es necesario desactivar todo el firewall. No las repitas si ya existen y cubren este laboratorio.

### 7.4 Detener y diagnosticar

```powershell
docker compose --profile emulado stop
docker compose ps
docker compose logs --tail=60 admin
docker compose logs --tail=60 track-server
docker compose logs --tail=60 sim-pepper
```

`stop` conserva los contenedores para reiniciarlos desde Docker Desktop. `docker compose --profile emulado down` elimina los contenedores y las redes de este proyecto; después se crean otra vez con `up`.

### 7.5 Uso de las vistas

| Vista | Qué permite observar o hacer |
|---|---|
| Vista general | Estado de los siete servicios de simulación, renders y seis indicadores MQTT |
| Zona de carreras | Clasificación, velocidad, vueltas, mandos emulados y cámara GT de seguimiento |
| Zona robótica | Elegir Spot/Pepper/NAO, aplicar tres objetivos, comparar objetivo y medición, pausar o volver a automático |
| Red y monitoreo | RTT, jitter, pérdida, edad de latidos, eventos, descarga de CSV y JSON |
| Montajes ESP32 | Tres ilustraciones nuevas y las tablas GPIO de cada montaje |

Los controles **Pausar mando** y **Piloto automático** actúan sobre la ESP32 emulada. No apagan el contenedor del jugador. En robótica, **Aplicar**, **Pausar** y **Automático** cambian la fuente de las entradas; la respuesta visual sigue viniendo de la simulación.

### 7.6 Arranque desde Git Bash sin el script de PowerShell

Desde la raíz del repositorio, entra primero a esta tarea. Docker Desktop debe estar en marcha y configurado para contenedores Linux. Estos comandos usan la configuración Compose incluida; no requieren instalar los paquetes Python directamente en Windows para ejecutar los servicios dentro de las imágenes.

```bash
cd 08-nexolab-esp32-vlan
docker version
docker compose version
export NEXO_MODO=emulado
docker compose --profile emulado config --quiet
docker compose --profile emulado up -d --build
docker compose --profile emulado ps
```

Si Git Bash ya está dentro de `08-nexolab-esp32-vlan`, omite el primer `cd`. `config --quiet` valida el archivo y no arranca contenedores. `up` construye o utiliza las imágenes, crea redes y servicios. `ps` debe mostrar los servicios iniciados; el perfil emulado contiene 16 contenedores. Que estén iniciados no demuestra aún que los modelos cargaron o que existe control: espera el estado y revisa los logs si falta una zona.

Para arrancar por grupos y observar qué parte falla, después de construir las imágenes se pueden iniciar los servicios así:

```bash
export NEXO_MODO=emulado
docker compose --profile emulado build
docker compose up -d router admin
docker compose up -d track-server player-1 player-2 player-3
docker compose up -d sim-spot sim-pepper sim-nao
docker compose --profile emulado up -d
```

El último comando agrega los siete roles emulados según el perfil. Las dependencias declaradas pueden iniciar también el router cuando se solicita una zona. Para consultar sin recorrer toda la interfaz:

```bash
curl --fail http://127.0.0.1:18180/api/nexo
curl --fail http://127.0.0.1:18180/api/resumen.json
curl --fail http://127.0.0.1:18180/api/zona/pista/estado
docker compose logs --tail=50 admin
docker compose logs --tail=50 player-2
docker compose logs --tail=50 sim-nao
```

`/api/nexo` debe indicar `emulado` y control habilitado. En el resumen se espera conexión MQTT, latidos recientes y métricas; la pista debe contener seis carros. Los nombres de contenedores pueden tener un prefijo del proyecto, pero los comandos Compose usan los nombres de servicio declarados.

Para pasar a placas físicas, detén primero el perfil completo y luego inicia infraestructura sin emuladores:

```bash
docker compose --profile emulado stop
export NEXO_MODO=fisico
docker compose up -d --build
```

Después carga el firmware configurado, comprueba puertos y firewall como en 6 y 7.3, y revisa `/api/nexo`. Este cambio selecciona la fuente prevista; no certifica que una placa esté correctamente conectada. No ejecutes la variante física y la emulada enviando órdenes a los mismos receptores.

### 7.7 Iniciar los procesos locales por separado para diagnóstico

La opción más breve para esta variante es `python tools/laboratorio_local.py --modelos modelos --broker mosquitto`, explicada en 11.3. Para entender y diagnosticar cada componente se puede reproducir su configuración manualmente. Los siguientes ejemplos son **Bash en Linux**, con el entorno Python activado y modelos preparados según 11.1. No ejecutan router, bridges ni VLAN. En PowerShell cambia la sintaxis de variables o usa el lanzador local.

En **cada terminal de un proceso Python**, entra en la carpeta de la tarea y copia este bloque común antes del comando particular:

```bash
export ADMIN_IP=127.0.0.1 MQTT_HOST=127.0.0.1 MQTT_PUERTO=18883
export HB_PUERTO=15300 HTTP_PUERTO=18180
export MODELOS="$PWD/modelos" RESULTADOS="$PWD/resultados/manual"
export RESULTADOS_DIR="$RESULTADOS" NEXO_MODO=software
export ROUTER_IP='' RUTAS='' PYTHONUNBUFFERED=1
export OBJETIVOS='eco-admin-local=127.0.0.1'
export NEXO_SONDEO=udp NEXO_ECO_PUERTO=15300
export NEXO_VISORES='{"pista":"http://127.0.0.1:18010","spot":"http://127.0.0.1:18011","pepper":"http://127.0.0.1:18012","nao":"http://127.0.0.1:18013"}'
export NEXO_CONTROLES='{"ctrl-1":"http://127.0.0.1:19090","ctrl-2":"http://127.0.0.1:19090","ctrl-3":"http://127.0.0.1:19090","ctrl-spot":"http://127.0.0.1:19090","ctrl-pepper":"http://127.0.0.1:19090","ctrl-nao":"http://127.0.0.1:19090","esclava":"http://127.0.0.1:19090"}'
mkdir -p "$RESULTADOS"
```

Primero prepara el broker de prueba. En una terminal Linux aparte, desde esta misma carpeta:

```bash
mkdir -p resultados/manual
cat > resultados/manual/mosquitto.conf <<'CONFIG'
listener 18883 127.0.0.1
allow_anonymous true
persistence false
CONFIG
mosquitto -c resultados/manual/mosquitto.conf
```

Mantén ese proceso abierto. A continuación inicia cada fila en una terminal distinta con el bloque común ya cargado. Los comandos quedan en primer plano para que se vean sus errores:

| Parte | Comando particular | Qué verificar |
|---|---|---|
| Monitor y panel | `python plano-admin/admin/monitor.py` | Mensaje MQTT conectado, ruta del CSV y respuesta HTTP en 18180 |
| Pista | `PUERTO_WS=18765 PUERTO_HTTP=18010 FPS_VIDEO=6 python zona-gamer/servidor-pista/servidor_pista.py` | Carga del circuito, seis carros y estado/render disponible |
| Jugador 1 | `JUGADOR=1 PUERTO_UDP=15001 SERVIDOR_WS=ws://127.0.0.1:18765 python zona-gamer/jugador/jugador.py` | Conexión WebSocket y control recibido al iniciar emulador |
| Jugador 2 | `JUGADOR=2 PUERTO_UDP=15002 SERVIDOR_WS=ws://127.0.0.1:18765 python zona-gamer/jugador/jugador.py` | Mensajes destinados a jugador 2 |
| Jugador 3 | `JUGADOR=3 PUERTO_UDP=15003 SERVIDOR_WS=ws://127.0.0.1:18765 python zona-gamer/jugador/jugador.py` | Mensajes destinados a jugador 3 |
| Spot | `ROBOT=spot PUERTO_UDP=15101 PUERTO_HTTP=18011 CUADROS_FPS=6 python zona-robotica/sim_robot.py` | Modelo cargado y transición desde reposo al recibir JOINTS |
| Pepper | `ROBOT=pepper PUERTO_UDP=15102 PUERTO_HTTP=18012 CUADROS_FPS=6 python zona-robotica/sim_robot.py` | Articulaciones y mediciones de Pepper |
| NAO | `ROBOT=nao PUERTO_UDP=15103 PUERTO_HTTP=18013 CUADROS_FPS=6 python zona-robotica/sim_robot.py` | Articulaciones y mediciones de NAO |

Finalmente inicia las entradas emuladas en otra terminal, con el mismo bloque común:

```bash
python emulador/emulador_esp32.py \
  --rol ctrl-1 ctrl-2 ctrl-3 ctrl-spot ctrl-pepper ctrl-nao esclava \
  --destino 127.0.0.1:15001 127.0.0.1:15002 127.0.0.1:15003 \
            127.0.0.1:15101 127.0.0.1:15102 127.0.0.1:15103 \
  --admin 127.0.0.1 --puerto-admin 15300 --mqtt 127.0.0.1:18883 \
  --piloto --pista ws://127.0.0.1:18765 --http 19090 --hz 20
```

Abre el panel en `http://127.0.0.1:18180`. Al principio habrá estados de arranque y reposo; después deben aparecer controles recibidos, vueltas y mediciones. El router seguirá sin ensayo en esta variante y no se debe presentar su ausencia como una falla de infraestructura probada. Para terminar, detén primero el emulador y luego jugadores, robots, pista, monitor y broker con Ctrl+C; cada servicio tiene sus mecanismos de cierre. Si otra sesión está usando esos puertos, ciérrala antes de este arranque manual.

## 8. Cómo funcionan los códigos

### 8.1 Organización del proyecto

| Ruta | Responsabilidad |
|---|---|
| `README.md` | Única documentación del proyecto y de las evidencias |
| `panel/` | Interfaz, estilos, gráficos y pasarela HTTP |
| `zona-gamer/` | Jugadores UDP/WebSocket, circuito, física y render de la carrera |
| `zona-robotica/` | Modelos, cinemática, simulación y render de los tres robots |
| `plano-admin/admin/` | Monitor, almacenamiento de métricas y configuración MQTT |
| `router/` | Arranque de FRR, rutas, firewall y latidos del router |
| `firmware/` | Sketches Arduino y configuración de pines/red de las ESP32 |
| `emulador/` | Fuentes de entradas de prueba y esclava MQTT virtual |
| `comun/` | Contrato de mensajes, estadísticas, rutas y comunicaciones comunes |
| `pruebas/` | Pruebas unitarias y ensayos del despliegue Docker |
| `tools/` | Laboratorio local, grabación automatizada y creación de GIF |
| `img/` | Tres ilustraciones realistas de montajes |
| `evidencias/` | Capturas, MP4, GIF, CSV, JSON y registros de esta sesión |
| `docker-compose.yml` | Servicios, imágenes, puertos, redes y perfil emulado |
| `docker-compose.vlan-real.yml` | Variante macvlan para un laboratorio Linux compatible |
| `iniciar.ps1` | Arranque y cambio entre modo emulado y físico |
| `publicar_dockerhub.ps1` | Construcción y publicación en la cuenta indicada |
| `requirements-local.txt`, `package.json` | Dependencias para repetir la ejecución local y la grabación |

### 8.2 Firmware: leer, filtrar y enviar

`esp32_maestra.ino` ejecuta la configuración inicial y un ciclo de tareas temporizadas. `config.h` concentra el rol, los GPIO, el destino, los periodos y los rangos. `logica.h` contiene operaciones de normalización y formato que se pueden probar sin hardware.

El ADC se muestrea cada 5 ms y se suaviza con `y += 0,25 × (x − y)`. Para joystick, el centro se calibra al inicio y se aplica una zona muerta del 8 % para evitar movimiento involuntario. Los potenciómetros se convierten a los rangos del robot. El pulsador utiliza antirrebote de 30 ms. Cada 50 ms se serializa `CTRL` o `JOINTS`; los latidos y PING se programan por separado. Así la lectura de entradas no depende del periodo del monitor serie.

`esp32_esclava_leds.ino` conecta Wi-Fi y MQTT, se suscribe a los estados y traduce sus mensajes a los seis GPIO. Sus reintentos y la representación de falta de broker evitan conservar indefinidamente un estado antiguo. Su `config.h` contiene los mismos nombres de servicios que usa el administrador.

### 8.3 Protocolo y utilidades compartidas

| Archivo / elemento | Funcionamiento |
|---|---|
| `comun/protocolo.py → armar_ctrl`, `armar_joints`, `armar_latido` | Construyen los mensajes de texto del contrato |
| `comun/protocolo.py → leer` | Decodifica y valida tipos y campos antes de devolver una estructura de datos |
| `ContadorSecuencia` | Lleva la cuenta de recibidos, huecos y orden de las secuencias |
| `Jitter` | Actualiza la estimación de variación de tránsito con las marcas temporales |
| `comun/lab.py → configurar_rutas` | Agrega las rutas indicadas por el entorno dentro de los contenedores |
| `comun/lab.py → Latido` | Envía heartbeat periódico desde un hilo independiente |
| `comun/lab.py → Metricas` | Gestiona MQTT, publicación de métricas y señal de vida/LWT |

### 8.4 Carrera: recepción, física y render separados

| Archivo / elemento | Qué hace y por qué |
|---|---|
| `zona-gamer/jugador/jugador.py → ReceptorUDP` | Recibe el mando destinado al jugador y descarta entradas inválidas |
| `jugador.py → Estado`, cola y conexión WebSocket | Conserva el mando reciente, reenvía controles, procesa respuestas y genera métricas |
| `pista.py → punto`, `proyectar`, `curvatura` | Define una geometría única para el recorrido, el progreso y los pilotos |
| `pista.py → malla_gt` y construcción de escena | Genera la carrocería GT, asfalto, pianos, barreras, meta y decoración |
| `servidor_pista.py → Carro` | Lee la pose física, acciona motores, cuenta vueltas y gestiona recolocaciones |
| `servidor_pista.py → ServidorPista` | Coordina conexiones, mandos, rivales, reloj de simulación, clasificación y HTTP |
| `render_pista.py` | Reconstruye la escena visual y produce el plano general y la cámara de seguimiento en un proceso separado |
| `cliente_prueba.py` | Cliente auxiliar para comprobar el servidor sin utilizar una placa |

Separar render y física evita que dibujar un cuadro determine el paso de integración. La pasarela expone `/api/zona/pista/estado`, `/api/zona/pista/cuadro` y `/api/zona/pista/detalle`. La imagen de seguimiento procede del render de PyBullet; no es una secuencia predibujada.

### 8.5 Robótica: objetivos, cinemática y medición

| Archivo / elemento | Función |
|---|---|
| `zona-robotica/robots.py → Humanoide` | Carga Pepper/NAO, encuentra articulaciones, limita objetivos y genera el gesto |
| `robots.py → PataRex.ik` / `fk` | Convierte entre posición del pie y ángulos de las articulaciones |
| `robots.py → Spot` | Transforma altura, cabeceo y alabeo en objetivos de las cuatro patas; implementa el trote |
| `robots.py → crear_visual` / `urdf_esqueleto` | Prepara la representación visual compatible con los recursos disponibles |
| `sim_robot.py` | Atiende UDP, aplica estados de seguimiento/reposo/gesto, integra física, calcula error y publica telemetría |
| `sim_robot.py → proceso_render` | Recibe el estado articulado y produce los cuadros sin bloquear la física |
| `descargar_modelos.py` | Descarga Rex y sus recursos desde un commit fijado, incluyendo la atribución de las mallas |
| `instalar_mallas_softbank.py` | Prepara los URDF de qiBullet; por defecto utiliza la variante sin mallas privadas |
| `probar_joints.py` | Envía entradas de diagnóstico al simulador elegido |

### 8.6 Emulación: entradas de prueba con la misma red

`emulador_esp32.py` permite seleccionar uno o varios roles. `Placa` reúne temporización, heartbeat y eco; `Maestra` produce `CTRL` o `JOINTS`; `Esclava` se suscribe al broker y confirma los estados recibidos. `Observador` y `Piloto` leen el WebSocket de la pista y calculan las entradas de los jugadores emulados.

`control_http.py` valida solicitudes de pausa, modo automático y objetivos manuales. El panel cambia estas entradas mediante la pasarela; los comandos siguen viajando a los receptores por UDP. El servidor WebSocket falso de `emulador/pruebas_locales/` sirve para diagnóstico aislado del emulador, no para las grabaciones entregadas.

En Docker hay un contenedor por rol. El laboratorio local utilizado para grabar inicia los siete roles en un proceso con hilos; esto reduce los requisitos de la prueba, pero no verifica la separación de contenedores.

### 8.7 Administración e interfaz

| Archivo / elemento | Función |
|---|---|
| `plano-admin/admin/monitor.py → atender_datagrama` | Registra latidos y atiende el eco UDP |
| `monitor.py → Sondeo`, `decidir` | Mantiene resultados de sondeos y combina sus señales para clasificar servicios |
| `monitor.py → armar_resumen`, `RegistroCSV` | Prepara el estado de la web y almacena muestras y eventos |
| `mosquitto.conf`, `arrancar.sh` | Configuración y arranque del broker y monitor en la imagen admin |
| `panel/gateway.py` | Sirve archivos del panel, limita los destinos de consulta y valida controles; rechaza el control emulado en modo físico |
| `panel/index.html` | Estructura de las cinco vistas y controles accesibles de la aplicación |
| `panel/nexo.css` | Colores, tipografía, tarjetas, tablas y adaptación a móvil |
| `panel/nexo.js` | Consulta estados, refresca renders, dibuja gráficos, cambia vistas y envía acciones del usuario |

`panel/index.html` forma parte de la **aplicación que se ejecuta**. La documentación y la revisión de sus evidencias están íntegramente en este README.

### 8.8 Router, Docker y herramientas

`router/arrancar.sh` identifica las interfaces por su subred, genera la configuración FRR y aplica reglas de reenvío. No presupone que `eth0`, `eth1` y `eth2` correspondan siempre a la misma zona. `router/latido.sh` informa al administrador y los archivos de `router/frr/` habilitan los demonios utilizados.

Los seis `Dockerfile` instalan dependencias y definen cómo iniciar cada tipo de servicio. Compose asigna redes, IP, puertos, capacidades y política de reinicio. `iniciar.ps1` fija el modo antes de llamar a Compose. `publicar_dockerhub.ps1` valida los comandos, construye las imágenes y solo genera el registro de publicación cuando los envíos terminan correctamente.

`tools/laboratorio_local.py` inicia procesos locales, espera a los renders y los cierra al terminar. `tools/ejecutar_evidencias.py` coordina la grabación y detiene/reinicia Pepper cuando lo solicita el guion. `tools/grabar_evidencias.js` opera el navegador con Playwright, verifica estados, toma capturas y convierte las grabaciones a MP4. `tools/crear_animaciones.py` convierte esos MP4 a GIF para mostrarlos en este archivo, conservando su secuencia temporal.

Las pruebas unitarias de `pruebas/test_protocolo.py`, `test_nexo.py` y `test_pista_gp.py` cubren mensajes, controles/pasarela y geometría. `lab_pruebas.py` reúne utilidades para observar el despliegue. `prueba_aislamiento.py`, `prueba_disponibilidad.py`, `medir_red.py` y `pruebas/red/` realizan ensayos de red con Docker; `correr_todo.ps1` los ordena.

### 8.9 Maestra Arduino: recorrido de una lectura hasta un datagrama

**Archivos:** `firmware/esp32_maestra/esp32_maestra.ino`, `config.h` y `logica.h`. El mismo sketch se configura para seis roles: tres jugadores y tres robots. Cambiar el rol cambia el nombre del origen, el puerto y la transformación final; no exige escribir seis programas independientes.

**Paso 1 — `setup`: preparar entradas y referencia.** Configura la comunicación serie a 115200, el LED, el pulsador con `INPUT_PULLUP` y el ADC de 12 bits. En los roles gamer, cuando está habilitado, llama a `calibrarCentro`: promedia 64 lecturas con el joystick suelto. Si un centro queda fuera de 1024…3072, usa 2048 como referencia. Esto evita tomar un extremo del joystick como posición neutral. Los potenciómetros robóticos utilizan el centro configurado; no se debe calibrar su posición momentánea como si fueran un joystick con resorte.

**Paso 2 — `muestrear`: suavizar el ADC.** Cada 5 ms lee los canales activos. Recibe implícitamente las lecturas de `analogRead` y modifica `filtA`, `filtB` y, si está conectado, `filtC`. La actualización `filtro += 0.25 × (lectura − filtro)` conserva parte del valor anterior y reduce variaciones rápidas. Es un filtro causal: no elimina todo ruido y introduce respuesta gradual. Las 10 oportunidades de muestreo nominales entre envíos de 50 ms no significan diez muestras independientes promediadas aritméticamente.

**Paso 3 — `leerBoton(ahora)`: aceptar una pulsación estable.** Compara la lectura actual con la anterior y registra cuándo cambió. Solo actualiza `botonEstable` si el nivel se mantuvo durante 30 ms. Con pull-up, `LOW` significa pulsado y se convierte a 1. La maestra transmite el nivel estable; la lógica de robot detecta después el flanco de subida. Son dos responsabilidades diferentes: quitar rebotes eléctricos y evitar repetir una acción mientras se mantiene pulsado.

**Paso 4 — `normalizar(crudo, centro, invertir)`: producir −100…100.** El código escala por separado la mitad inferior y superior del ADC, porque el centro puede no ser 2048. Redondea, limita e invierte cuando el eje está configurado al revés. Devuelve un entero; no manda todavía una orden al carro. `aplicarZonaMuerta(v, zm)` convierte la región central en cero y vuelve a escalar lo restante hasta ±100, evitando un salto de amplitud al salir de la zona muerta.

**Paso 5 — `actualizarValores(ahora)`: escoger la fuente.** Primero revisa si venció una entrada `MANUAL` recibida por serie; ese reemplazo dura 2 s sin actualización. Si sigue activo, usa sus valores. En modo demo genera senos temporizados. En modo normal utiliza ADC filtrado, normalización, zona muerta y botón. Cuando `CANAL_C_CONECTADO=0`, fuerza el tercer canal a cero: una entrada analógica sin conectar no debe convertirse en un mando aleatorio.

**Paso 6 — `enviarControl(ahora)`: convertir y serializar.** Para gamer, `armarCtrl` escribe jugador, secuencia, tiempo, dirección, velocidad y botón. Para robótica, `aGrados` convierte cada entrada normalizada al intervalo `J1_MIN/J1_MAX`, etc., y `armarJoints` incluye el nombre del robot. Las funciones de formato devuelven la longitud producida por `snprintf`; si la línea no cabe en el buffer, no se envía. El contador de secuencia avanza con la generación de la orden.

**Paso 7 — `enviar`: usar la red disponible.** Comprueba Wi-Fi y socket UDP antes de iniciar el datagrama hacia `IP_PC` y el puerto de control. Devuelve si el envío se pudo realizar a través de esa interfaz; no recibe una confirmación de que PyBullet aplicó el comando. La línea serie `ENVIADO,...` se muestra a 5 Hz incluso si no hay Wi-Fi y representa lo que se intentaría enviar. Para acreditar conexión hay que revisar `ESTADO` y respuestas, además del eco del mensaje.

**Paso 8 — `enviarLatido` y `atenderUdp`: supervisar por un canal separado.** `enviarLatido` construye HB/PING con sus propios contadores. `atenderUdp` vacía los datagramas disponibles, conserva PONG del mismo origen y calcula RTT con `ahora − tEnvio`. `leerPong` verifica formato, origen e intervalo de los enteros. Se descartan valores absurdos de RTT de 60 s o más. La resta modular de enteros sin signo permite tratar el desbordamiento normal de `millis` sin mezclar relojes de dos máquinas.

**Paso 9 — `gestionarWifi`, `actualizarLed` y `loop`: continuar sin bloquear cada tarea.** La conexión se vigila y reintenta; el LED de placa distingue falta de Wi-Fi, ausencia de PONG reciente y comunicación disponible. El bucle usa marcas de tiempo por tarea, en vez de un `delay(50)` que detenga lectura, red y diagnóstico juntos. Los envíos avanzan contra un reloj fijo y se resincronizan si el retraso es grande, evitando una ráfaga de órdenes atrasadas.

**Ejemplo numérico de transformación, calculado para explicar el código:** supongamos centro 2048, lectura ya filtrada 3072 y eje sin inversión. La normalización resulta aproximadamente 50; con zona muerta 8 se vuelve 46. Un eje Spot configurado entre −20 y 20 se convierte a 9,2 unidades equivalentes. En gamer esa misma salida 46 sería una dirección/velocidad porcentual según el canal. Este cálculo ilustra el mapeo; no es una lectura ADC física de la sesión entregada.

### 8.10 Protocolo Python: validar y separar control de diagnóstico

**Archivo:** `comun/protocolo.py`. Sus estructuras `Ctrl`, `Joints` y `Latido` representan mensajes ya interpretados. El receptor trabaja con campos identificados por nombre después de la validación, en lugar de repetir `split` y conversiones en cada simulador.

1. `armar_ctrl`, `armar_joints` y `armar_latido` reciben los valores y devuelven una línea CSV. `armar_joints` conserva un decimal en los tres ejes, compatible con el formato de `logica.h`.
2. `leer(datos)` acepta bytes o texto, decodifica, quita espacios exteriores y separa campos. Revisa el tipo y la cantidad: siete campos en `CTRL`, ocho en `JOINTS` y cuatro en los mensajes de latido/eco.
3. Convierte identificadores, secuencia, tiempo y valores. `CTRL` admite jugadores 1…3 y limita dirección/velocidad a ±100. `JOINTS` admite `spot`, `pepper` y `nao`, y exige números finitos. Un error de formato o conversión devuelve `None`.
4. El receptor comprueba el **destino específico** después de interpretar: un `JOINTS` válido para NAO no se aplica a Pepper. Esa separación permite contar mensajes ajenos sin tratarlos como órdenes de este robot.
5. `ContadorSecuencia.registrar(seq)` modifica los contadores de recibidos, huecos y mensajes fuera de orden. Si el número nuevo es mayor, un salto permite estimar cuántas secuencias faltan. Retrocesos grandes o varios retrocesos consecutivos se tratan como indicios de reinicio del emisor.
6. `perdida_pct()` calcula la proporción estimada usando recibidos y huecos. No equivale a contar paquetes con un analizador en todos los enlaces: duplicados, reordenamiento, reinicios y pausa de generación requieren interpretación. La clase es un instrumento de diagnóstico; no impone por sí sola rechazo de todas las órdenes antiguas o duplicadas.
7. `Jitter.registrar(t_envio, llegada)` compara los intervalos de envío y llegada consecutivos: `D = delta_llegada − delta_envio`; después actualiza `J += (abs(D) − J)/16`. La diferencia de intervalos evita depender del mismo origen de reloj; no calcula latencia unidireccional absoluta ni certifica ausencia de deriva.

```mermaid
flowchart TB
    D["Datagrama recibido"] --> P{"¿Formato y números válidos?"}
    P -->|No| B["Contar basura e ignorar"]
    P -->|Sí| R{"¿Destino de este receptor?"}
    R -->|No| A["Contar ajenos e ignorar"]
    R -->|Sí| C["Actualizar secuencia y jitter"]
    C --> F["Renovar mando y tiempo de llegada"]
    F --> S["Aplicar límites y estado de seguridad"]
```

Una línea `JOINTS,pepper,25,4000,20.0,55.0,-25.0,0` cumple el contrato y contiene objetivos claros. `JOINTS,pepper,25,4000,nan,55,-25,0` devuelve `None`: un valor no finito no debe propagarse hacia el motor de posición. Los ejemplos son del contrato; los números de secuencia y tiempo no se presentan como registros históricos.

**Utilidades de `comun/lab.py`:** `configurar_rutas` lee `ROUTER_IP/RUTAS`, ejecuta `ip route replace` dentro del contenedor y devuelve la lista de rutas configuradas; sus errores se informan. `Latido.iniciar` lanza un hilo con envíos periódicos y `parar` solicita terminarlo. `Metricas` prepara Paho, testamento, reconexión y publicación: al conectar informa `lab/vivo/<servicio>=1`; al cierre intenta publicar 0. Publicar una métrica devuelve si se pudo tramitar en ese momento, sin convertir esa devolución en confirmación de lectura por la esclava.

### 8.11 `jugador.py`: puente UDP–WebSocket y freno de seguridad

**Entrada:** datagramas `CTRL` de un solo jugador. **Salida:** mensajes JSON hacia el servidor WebSocket, métricas MQTT y heartbeat administrativo. El proceso jugador no integra la física de su carro: traduce transportes, vigila el mando y conserva lo necesario para el diagnóstico.

`Estado` contiene la identidad del jugador, el último control, tiempos de llegada, contadores, cola WebSocket y datos recibidos de la pista. `ReceptorUDP.datagram_received` recibe bytes y dirección de origen. Registra la llegada, llama a `protocolo.leer` y clasifica entradas inválidas o de otro jugador. Para el destino correcto actualiza secuencia/jitter, guarda el control reciente, quita el estado de seguridad local y llama a `encolar` con un mensaje de control JSON.

`encolar` solo conserva mensajes cuando existe una sesión WebSocket. La cola tiene un máximo de 20 controles y elimina el más antiguo si se llena; a 20 Hz eso representa aproximadamente un segundo de generación nominal. Sin conexión no acumula una larga historia para ejecutarla luego. Esto reduce el desfase entre el mando presente y el comando que recibe la pista, aunque una cola no garantiza por sí sola tiempo real estricto.

`sesion_ws` abre una sesión, envía `hola` con identidad/nombre/color y coordina tres trabajos asíncronos: transmitir la cola, recibir estados/respuestas y hacer PING. Cuando uno termina, cancela los restantes y limpia sesión y cola. `bucle_ws` rodea ese trabajo con reconexión progresiva. UDP, métricas y revisión de seguridad continúan como tareas separadas mientras se reintenta.

`atender_pong` obtiene RTT con el reloj del jugador, valida que el intervalo sea plausible y actualiza medias y jitter. `atender_estado` busca el carro de este jugador dentro del estado general y conserva su posición, vueltas y movimiento. Son datos de observación, no una segunda simulación del automóvil.

`bucle_failsafe` revisa la edad del último CTRL. Al faltar o vencer durante aproximadamente un segundo, marca seguridad y encola dirección/velocidad cero con `failsafe=true`. La pista también controla la frescura de su propio último mensaje. Esta doble revisión cubre que el jugador esté vivo mientras el mando calla y que el puente WebSocket falle antes de poder enviar la orden de freno.

`metricas_actuales` prepara el diagnóstico; `bucle_metricas` lo publica periódicamente. `principal` configura rutas, socket, heartbeat y tareas. El dato importante para la sustentación es seguir cada frontera: **UDP válido no garantiza WebSocket conectado; WebSocket conectado no garantiza mando fresco; heartbeat fresco no garantiza que el carro deba acelerar**.

### 8.12 `pista.py` y `servidor_pista.py`: de geometría a movimiento

**La geometría compartida.** `_trazado` crea la curva cerrada y las muestras con longitud acumulada. `envolver(s)` lleva una distancia al intervalo de una vuelta; `envolver_delta` obtiene la diferencia corta entre dos posiciones sobre el circuito. Esto resuelve el salto numérico de aproximadamente 74 m a 0 al cruzar meta.

`punto(s, d)` localiza la distancia sobre el eje y obtiene posición/rumbo, añadiendo el desplazamiento lateral. `proyectar(x, y)` realiza el recorrido inverso: compara segmentos y devuelve avance `s` y separación lateral `d`. `curvatura` estima cómo cambia el rumbo en la zona. Los pilotos, el conteo y la representación consumen esas mismas funciones; dibujar un circuito diferente del que sigue el controlador produciría una inconsistencia visible.

`linea_central` y `muestras_borde` proporcionan puntos para clientes y mallas. `construir_pista` crea elementos de la escena; `cargar_carro` incorpora el vehículo articulado. `malla_gt` genera la apariencia de competición. El detalle visual y la distancia geométrica son resultados calculados por el programa; las vueltas y velocidades se observan después en la simulación.

**El estado físico de cada `Carro`.** `leer` toma posición, orientación y velocidad de PyBullet, proyecta el carro sobre el circuito y obtiene el sentido de movimiento. `aplicar(giro, v, frenar)` convierte la velocidad lineal a velocidad de ruedas traseras y fija la dirección delantera; al frenar manda velocidad cero y fuerza de frenado. No escribe directamente la nueva posición en cada paso: esa posición surge de integrar motores, contactos y colisiones.

`contar_vuelta(t)` acumula avance usando la diferencia envuelta y comprueba un cruce **hacia delante**, cerca de meta, después de haber pasado la mitad. Guarda tiempo de vuelta anterior y mejor tiempo cuando hay información suficiente. Cruzar al revés o mover el carro de un lado a otro de la meta no cumple ese recorrido completo. `dict_estado` reúne campos que muestra la clasificación: identificador, pose, velocidad, vueltas, distancia, sector y seguridad.

`reaparecer` elige una posición lateral disponible sobre la pista, restablece pose/velocidad/articulaciones e incrementa el contador de reposiciones. Es una recuperación discreta del estado físico. No se debe ocultar ese contador al comparar desempeño, pues recorrer una vuelta con recolocación no es lo mismo que completarla sin ayuda.

**La decisión de `ServidorPista._controlar`.** Para un jugador, comprueba propietario conectado y control reciente. Si no son utilizables, frena. Si son utilizables, convierte dirección porcentual al límite de giro y velocidad al límite del modelo; el botón puede activar turbo y, si se mantiene el tiempo configurado, una recolocación. Para los tres rivales calcula su mando geométrico dentro del servidor.

Después revisa condiciones físicas de recuperación: vuelco persistente, inmovilidad con intención de avanzar, salida demasiado lejos del ancho de pista o caída por debajo del mundo. Son condiciones del modelo; un mando legítimo cero no se interpreta simplemente como atasco del jugador. Los tiempos y umbrales están definidos en el archivo y el contador registra cuándo actuó la recuperación.

**El seguimiento `_pure_pursuit`.** Recibe el carro con su pose y progreso, escoge un punto adelantado, calcula el error de rumbo y lo traduce a giro limitado. La anticipación aumenta con la velocidad. La curvatura próxima modifica el objetivo de velocidad para evitar entrar a una curva cerrada con el mismo mando que en la recta. No hay red neuronal ni entrenamiento de un agente en esta función: la decisión se calcula con geometría y reglas explícitas.

**Conexiones y reloj.** `atender` interpreta los mensajes WebSocket y asocia cada cliente con un jugador; `_tomar_jugador` gestiona esa asignación. `_bucle_fisica`/`bucle_fisica` avanzan el mundo con paso nominal 1/240 s, aplican controles y preparan estados. `_armar_estado` y `_posiciones` producen información consistente para clientes; `_difundir` envía el estado sin obligar a dibujar un cuadro por cada paso. `iniciar_http` expone los resultados e imágenes para la pasarela.

El render se prepara mediante snapshots y un proceso dedicado en `render_pista.py`. Se dibujan el plano general y la cámara de seguimiento; el navegador pide el último cuadro disponible. Física a 240 Hz, publicación a 20 Hz y refresco del navegador son tres frecuencias diferentes. Una máquina con menos CPU puede reducir cuadros sin que eso autorice a afirmar que ejecutó exactamente 240 pasos reales cada segundo.

### 8.13 `robots.py`: diferencias entre humanoides y cuadrúpedo

**Carga y articulaciones.** `crear(nombre)` selecciona `Humanoide` para Pepper/NAO o `Spot` para Rex. Las funciones de carga leen URDF y la información de articulaciones: índices, nombres, límites, fuerzas y velocidades. El control se asocia por nombres para evitar depender de un número de articulación que cambie al editar el modelo.

En `Humanoide._a_objetivos(cmd)`, j1, j2 y j3 se convierten a radianes y a las articulaciones elegidas. Los límites del URDF restringen el objetivo. `Humanoide.paso(t, modo, cmd)` parte de la postura de reposo; en `siguiendo` la combina con las tres entradas y en `gesto` con `_saludo`. En reposo utiliza velocidad máxima 1 rad/s para regresar gradualmente. Envía `POSITION_CONTROL` solo cuando cambia la pareja objetivo/velocidad de una articulación, ya que el motor de PyBullet conserva su consigna entre pasos.

`_saludo` produce una trayectoria temporal para hombro, codo, muñeca/mano y cabeza. `gesto_terminado(t)` compara el tiempo transcurrido con 2,5 s. `objetivo()` devuelve los tres objetivos publicados y `medido()` convierte los ángulos actuales de `getJointState` a grados. Ambos son necesarios: mostrar únicamente el objetivo escondería que el motor todavía está llegando o que la física no logra seguirlo.

**Cinemática de las patas.** `PataRex.ik` recibe la posición deseada del pie en el marco de la pata y calcula tres ángulos con la geometría de sus segmentos. `fk` reconstruye la posición a partir de ángulos. Esta pareja conecta una petición de postura del cuerpo con las articulaciones del cuadrúpedo; no se manda j1 directamente a una sola rodilla.

`Spot.asentar` ejecuta 480 pasos antes del control y toma una referencia de altura tras apoyarse en el suelo. `Spot.paso` transforma j1 a altura y j2/j3 a cabeceo/alabeo, aplica límites y aproxima gradualmente sus valores mediante `_acercar`. `_ik_angulos` transforma los cuatro pies al marco del cuerpo inclinado y llama a la cinemática de cada pata. `_mandar` entrega los ángulos a `setJointMotorControlArray`.

Para trotar, `_pies` agrega trayectorias periódicas de avance y elevación. Las diagonales comparten fase y la otra pareja lleva medio ciclo de diferencia. La amplitud sube/baja en una rampa; mientras queda amplitud la fase continúa para terminar la zancada. Durante trote o frenado de la rampa, el cuerpo se mantiene derecho con altura limitada. Por eso el objetivo aplicado puede diferir del comando solicitado en esos modos.

`Spot.medido` obtiene la pose real del cuerpo **dentro del mundo simulado**: orientación de sus ejes y altura relativa al asentamiento. j1 se vuelve a expresar en unidades equivalentes del mando para poder compararlo. No es una lectura de un MPU-6050 conectado a una ESP32. `caido` comprueba si el eje vertical se inclinó más de aproximadamente 60°; se cuenta el inicio de la caída, no cada paso durante el mismo vuelco.

`crear_visual`, `urdf_esqueleto` y `foto` preparan la representación y snapshots. Los humanoides pueden verse con geometría simplificada sin redistribuir mallas privadas. Cambiar su apariencia no transforma esta práctica en una prueba de equilibrio o marcha bípeda: sus bases están fijas en el experimento entregado.

### 8.14 `sim_robot.py`: recepción, estado, física y telemetría

`main` valida `ROBOT`, configura rutas, inicia heartbeat y métricas, conecta PyBullet en modo `DIRECT` y crea el modelo. Después inicia un hilo UDP y un proceso de render con cola de tamaño uno. El render tiene su propio cliente de PyBullet: recibe la pose y articulaciones para dibujar, mientras la física principal sigue integrando el robot.

Cada iteración recorre seis grupos de trabajo:

1. **Vaciar el buzón de red.** `hilo_udp` deja bytes y tiempo de llegada en una cola acotada. El bucle principal llama a `leer`, cuenta basura y destinatarios ajenos, actualiza secuencia/jitter y registra el comando más reciente. Recibir un valor fuera del rango del modelo incrementa `recortes`; la función del robot limita lo que aplica.
2. **Interpretar el botón.** Compara el nivel actual con `ultimo_boton`. Un flanco activa o detiene trote en Spot, o inicia gesto en humanoides si no había uno en curso. No crea veinte acciones distintas al mantener pulsado un segundo.
3. **Decidir estado.** Sin comando o con edad mayor de `SIN_DATOS_S` entra a `reposo`. Una entrada reciente permite seguimiento y un gesto terminado vuelve a seguimiento. El tiempo de llegada usado para frescura pertenece al receptor, no al reloj remoto del mando.
4. **Integrar los pasos debidos.** Calcula cuántos pasos de tamaño `robots.DT` corresponden al reloj. Si el atraso excede el máximo permitido, reajusta el origen temporal y limita el trabajo acumulado: evita una larga ejecución acelerada para ponerse al día. Para cada paso llama a `robot.paso` y `p.stepSimulation`.
5. **Medir y preparar imágenes.** Compara `robot.objetivo()` y `robot.medido()`, conserva error por eje y prepara el estado HTTP. Envía un snapshot a la cola de render sin bloquear; si está llena, salta ese cuadro. La prioridad es la actualización del estado de control.
6. **Publicar diagnóstico.** Cada 2 s calcula medias/máximos de error y frecuencia física observada y los envía por MQTT. Para Spot agrega caída, trote y avance. El estado HTTP incluye edad de control, comando, objetivo, medición y contadores para revisar qué ocurrió.

El error instantáneo es la media de tres diferencias absolutas; `error_2s` resume una ventana de varios pasos. Un valor puede ser pequeño después de estabilizarse y mayor durante transiciones. El nombre `error_realsim_deg` del código designa comparación objetivo–modelo: en esta sesión ambos extremos pertenecen al control y a la física simulada, no a sensores de un robot físico.

Al recibir señal de parada, solicita cerrar el render, detiene latido/MQTT y desconecta PyBullet. Solo cuando está habilitada la grabación/resumen de simulación escribe el JSON de error por estado. Los JSON manuales de la evidencia, por su parte, son snapshots recogidos por el guion del navegador.

### 8.15 `monitor.py`: decisiones explicadas función por función

**`atender_datagrama`: entrada UDP.** Reconoce el contrato del protocolo. Un HB actualiza su `Origen`; un PING válido genera el PONG que devuelve origen, secuencia y tiempo recibidos. El nombre del servicio conecta el emisor con la tabla, mientras los orígenes adicionales se muestran aparte. Existe un límite configurable para evitar crecimiento indefinido de orígenes desconocidos; los servicios vigilados se aceptan aunque esa tabla se llene.

**`Origen.registrar_hb`: vida y estadística.** Recibe la secuencia, marca del emisor y llegada local, renueva el último heartbeat y actualiza los contadores y jitter. `edad()` usa el reloj monótono: un ajuste del reloj calendario no debería convertir un latido en fresco por error. La ventana de muestras permite distinguir pérdida reciente del total acumulado. `resumen()` devuelve una representación serializable; no escribe todavía una fila del CSV.

**`ping_una_vez`, `hilo_ping` y `Sondeo.registrar`: prueba activa.** El hilo intenta alcanzar un destino a intervalos y registra respuesta o ausencia. Según `NEXO_SONDEO`, utiliza ICMP o el eco UDP del laboratorio local. `Sondeo` conserva intentos, RTT y fallos consecutivos. `resumen` devuelve media, p95, máximo, disponibilidad y, cuando se solicita, historial de la ventana. El tiempo sin respuesta es un fallo del instrumento; no se convierte en un RTT de cero.

**`iniciar_mqtt`: señales publicadas por procesos.** Configura callbacks, testamento del propio monitor y reconexión. Se suscribe a `lab/vivo/+` y `lab/metricas/+`. El callback guarda la carga por servicio y el momento de llegada bajo el candado compartido. Una métrica JSON se conserva como estructura; si el formato no es JSON, queda texto limitado para diagnóstico. Paho atiende la red en su hilo, mientras el bucle principal toma decisiones.

**`decidir(nombre)`: salida estado y motivo.** Sigue la prioridad de la sección 5.1. Recibe el identificador, consulta origen/sondeo/MQTT y devuelve una tupla; no enciende un GPIO ni reinicia un contenedor. Los umbrales provienen del entorno. Su motivo hace visible la condición que ganó la decisión, por ejemplo `vivo=0 por MQTT` o un p95 que excede el máximo.

**`main`: orquestación y transiciones.** Repite la evaluación cada 0,5 s. Si un estado cambia, agrega un objeto a la cola de eventos y una línea a `eventos.jsonl`, cambia `desde` y programa su publicación inmediata. Si el estado sigue igual, actualiza el motivo y renueva el retenido cada 10 s. Cada 2 s prepara el resumen, publica `lab/admin/resumen` y escribe la tanda CSV. Estos periodos explican por qué un dato no aparece exactamente en el instante del comando.

**`armar_resumen(con_historial=False)`: lectura coherente del monitor.** Recibe si se desea historial, reúne las ocho filas vigiladas y separa otros orígenes, sondeos y métricas. Incluye umbrales, modo, conexión MQTT, eventos y edades. Se llama con el candado tomado para evitar leer parte de una actualización mientras otro hilo cambia el mismo conjunto. La coherencia del resumen del monitor no significa que todos los renders de las zonas tengan exactamente su mismo tiempo.

**`RegistroCSV.escribir`: archivo histórico.** Recibe el resumen, extrae las 19 columnas por servicio y escribe una fila para cada uno. Abre el archivo en modo añadir, conserva encabezado y vacía el buffer en cada tanda. Los eventos se registran por cambio, mientras las filas CSV se registran por muestreo; esas dos frecuencias son distintas y se complementan para analizar una interrupción.

**`Manejador.do_GET/do_POST` e `hilo_http`: consulta y control web.** Entregan JSON del monitor, CSV y recursos; para las zonas delegan en `gateway`. El servidor HTTP puede atender lecturas mientras los hilos de sondeo y MQTT continúan. El supervisor identifica y publica una falla, pero no implementa una política propia de reinicio automático: en Docker la política de contenedores y, en la prueba local, la herramienta de evidencias realizan la reanudación.

### 8.16 Esclava: de texto MQTT al patrón de cada LED

**Firmware:** `firmware/esp32_esclava_leds/esp32_esclava_leds.ino`. Su `config.h` fija el orden `player-1`, `player-2`, `player-3`, `sim-spot`, `sim-pepper`, `sim-nao` y la correspondencia GPIO 16, 17, 18, 19, 21, 22. Pista y router se supervisan en el dashboard, pero no tienen uno de estos seis LED.

`conectarMqtt` abre la sesión con el broker, configura vida/testamento y se suscribe a **seis tópicos concretos**. Los mensajes retenidos actualizan los estados apenas se suscribe. `alRecibir(topico, payload, largo)` comprueba el prefijo `lab/estado/`, llama a `indiceServicio` y copia la carga a un buffer propio con terminador. Quita espacios y saltos finales; no supone que el payload entregado por PubSubClient sea ya una cadena C terminada.

`estadoDeTexto` convierte `OK`, `LENTO` y `CAIDO` a los estados internos, y cualquier otro texto a desconocido. `ponerEstado` solo marca cambio cuando el valor cambió; `imprimirLeds` muestra el arreglo por serie para diagnóstico. El mensaje de red no determina un color: cada posición corresponde a un servicio y su patrón luminoso expresa disponibilidad.

`actualizarLeds(ahora)` aplica nivel fijo para OK, parpadeo a 2 Hz para LENTO, apagado para CAIDO y un destello corto para desconocido. Usa `millis` sin un `delay` por LED, por lo que MQTT sigue atendiendo mensajes. `gestionarMqtt` vigila conexión y reintento; al superar 5 s sin broker marca los seis estados desconocidos y activa un vaivén. Al volver la conexión retira ese modo y recibe nuevamente los retenidos. El LED integrado, separado de los seis indicadores, informa sobre Wi-Fi/broker.

**Esclava emulada:** `Esclava._al_mensaje` sigue los tópicos y conserva el estado recibido; publica una métrica con su arreglo `leds`. El panel compara ese arreglo con los estados administrativos. Esta confirmación comprueba recepción e interpretación MQTT en software. El firmware físico muestra diagnóstico serie y controla GPIO; no se debe atribuirle automáticamente la misma telemetría de confirmación del emulador ni interpretar un indicador web como corriente medida en un LED.

### 8.17 Emulador: producir entradas sin saltarse los receptores

**Archivo:** `emulador/emulador_esp32.py`. `Placa` mantiene rol, socket, reloj y periodicidad. `periodicos` atiende heartbeat/PING; `leer_udp` procesa eco y `run` coordina el hilo de cada rol. `resumen` devuelve sus contadores al finalizar. Una placa emulada representa el contrato y temporización de software; no reproduce ruido ADC, batería, propagación radio o problemas eléctricos.

`Maestra.paso(ahora)` revisa si toca enviar y consulta `control_ui`. En `pausa` no genera CTRL/JOINTS, pero la parte periódica de la placa sigue viva. En `manual` usa el conjunto validado por HTTP. En `auto` llama a `entradas_gamer` o `entradas_robot`. Arma el datagrama con `protocolo`, lo envía al destino y avanza la secuencia. La opción de pérdida artificial puede omitir un envío conservando el avance de secuencia; así el receptor ve un hueco sin que el simulador tenga que inventarlo.

`Observador` abre un WebSocket propio, lee la pista y entrega una copia reciente a `Piloto.calcular`. El piloto obtiene pose, rumbo y puntos adelantados y calcula dirección/acelerador. Es una fuente de mandos que vuelve a pasar por UDP → jugador → WebSocket; no cambia directamente los motores desde el observador. Para robots, `entradas_robot` genera senos dentro de rangos elegidos y un botón periódico, de modo que las tres entradas tengan movimientos distinguibles.

`control_http.validate(payload, gamer)` recibe un objeto, exige `auto`, `manual` o `pausa`, revisa cantidad de ejes, números finitos, límites y botón 0/1. Rechaza booleanos donde se espera una magnitud numérica. Devuelve un objeto nuevo con la parte aceptada; una pausa no conserva accidentalmente un arreglo manual viejo. El servidor HTTP aplica ese objeto a la placa elegida y responde JSON; después el hilo de la placa produce el mensaje UDP a su frecuencia.

Se distinguen tres controles que podrían confundirse al presentar: **manual web del emulador** establece objetivos mientras siga ese modo; **MANUAL por serie del firmware** es un reemplazo temporal que vence tras 2 s; **pulsador físico/emulado** genera una entrada binaria que el robot convierte en evento por flanco.

### 8.18 Pasarela y JavaScript: consultar servicios y mostrar frescura

**`panel/gateway.py`.** `metadata()` devuelve modo, versión y si se habilita control emulado. `get` acepta recursos de una lista cerrada: estado/cuadro para las cuatro zonas y detalle para pista. Construye el destino con `VIEWS`, nunca con una URL arbitraria suministrada por el navegador. `fetch` hace la solicitud con timeout de 1,2 s y límite de respuesta de 4 MiB; un servicio no disponible se presenta con HTTP 503.

`post` exige un rol conocido, modo permitido, la cabecera `X-Nexo-Control: 1` y un objeto JSON de hasta 2048 bytes. Reemplaza el rol del cuerpo por el de la ruta antes de enviar a `CONTROLS`. En modo físico responde 403 para mandos web: la fuente debe ser la placa configurada. La cabecera y los destinos cerrados reducen solicitudes accidentales o destinos indebidos; no implementan por sí mismos una autenticación de usuarios para una red pública.

**`panel/nexo.js`.** `poll` consulta `/api/resumen.json` cada segundo, evita duplicar una lectura en marcha, marca conexión y llama a `render`. Si falla, indica que no hay respuesta, deshabilita controles y deja claro que la lectura retenida no es actual. `pollZones` consulta estados de las zonas necesarias para la vista activa y guarda una historia corta del objetivo/medición del robot.

`renderZones` ordena la clasificación por la posición publicada; no vuelve a calcular las vueltas desde píxeles. `renderRobot` presenta estado, paquetes, error y tres pares objetivo/medido. `drawCharts` llama a `chart` para dibujar j1 y RTT; las muestras no finitas se excluyen y los huecos no se unen como si fueran mediciones válidas. Esa historia es del navegador, no un archivo de laboratorio.

`sendControl(role, payload)` envía el POST y solo confirma modo si obtuvo una respuesta válida. Los botones construyen el arreglo de los deslizadores y solicitan manual/auto/pausa. `applyControlAccess` los habilita según modo y conexión. `refreshFrames` actualiza las imágenes visibles, evita otra descarga simultánea de la misma imagen y marca el cuadro como antiguo cuando hay error. El refresco nominal es cada 650 ms; no representa la frecuencia física de 240 Hz.

`setView` cambia la sección visible y `chooseRobot` configura nombres y rangos de sus controles. `showMount` muestra la ilustración y tabla de pines correspondiente. HTML y CSS organizan las cinco vistas y la adaptación al móvil. Las pruebas de 390 px del guion respaldan la condición observada; no constituyen un ensayo de cada teléfono o navegador existente.

### 8.19 Router, despliegue y guiones: qué ejecuta cada uno

**`router/arrancar.sh`.** `iface_de` busca la interfaz cuya IP pertenece al prefijo de cada zona. El arranque espera las tres interfaces, genera `frr.conf` y levanta los demonios configurados. FRR muestra las redes conectadas; no se plantea aquí un aprendizaje OSPF/BGP. El reenvío lo habilita la configuración del contenedor y el script verifica su valor.

Después vacía las reglas de su espacio de red, establece política `DROP` y crea `AISLAR_V1_V2`. Las dos primeras reglas bloquean gamer → robótica y robótica → gamer, incluso antes de permitir conexiones establecidas. Las reglas administrativas permiten comunicación prevista con la zona 3 y sus respuestas. El contador de la cadena es una evidencia cuantitativa del descarte, pero hay que ejecutar tráfico para observarlo; la mera presencia de la regla no es un resultado medido.

**Compose y scripts de PowerShell.** Las redes, IP y puertos de cada rol están declarados en `docker-compose.yml`; las anclas YAML reutilizan logging, rutas, volúmenes y reinicio. `depends_on` ordena el comienzo y no es un supervisor de vida continuo. `restart: unless-stopped` cubre una salida del proceso según la política de Docker; un `docker compose stop` intencional requiere que se lo vuelva a iniciar. `iniciar.ps1` selecciona emulado/físico y llama a Compose; al cambiar a físico detiene los mandos emulados para que no compitan con las placas.

**Herramientas de evidencia.** `Laboratorio.start_all` en `tools/laboratorio_local.py` asigna puertos de loopback y lanza broker, monitor, pista, jugadores, robots y los siete roles emulados. `wait_ready` exige heartbeat, MQTT, JSON de las zonas y cabeceras JPEG válidas antes de continuar. `stop` termina un proceso/grupo y `close` recorre los procesos en orden inverso; `start_robot` permite reanudar Pepper en el ensayo. Ninguna de esas funciones crea una red Docker.

`tools/ejecutar_evidencias.py` prepara la sesión y coordina grabación y registros. `tools/grabar_evidencias.js` usa Playwright para elegir vistas, enviar acciones, verificar estados, tomar capturas y grabar. `tools/crear_animaciones.py` reduce tamaño/paleta de los MP4 a GIF; los GIF son vistas de los mismos videos, no ensayos adicionales. Los registros de pruebas y el manifiesto permiten seguir el origen de cada dato sin convertir las ilustraciones de montaje en fotografías experimentales.

`pruebas/test_protocolo.py`, `test_nexo.py` y `test_pista_gp.py` comprueban contratos, pasarela/control y geometría. `firmware/prueba_formato/prueba_formato.cpp` ejercita las funciones C++ sin Arduino; su Python compara líneas contra el protocolo y busca Visual Studio Build Tools en Windows. `firmware/compilar.ps1` prepara compilación para los roles Arduino cuando está disponible Arduino CLI. Probar las funciones puras en un compilador del PC no equivale a cargar y medir una ESP32.

### 8.20 Un ejemplo completo: aplicar Pepper y detectar su interrupción

Este recorrido conecta el código anterior con la evidencia `robot_pepper_manual.json`. Los valores **20, 55 y −25** son los objetivos de la prueba incluida; las secuencias y tiempos del datagrama mostrado son ilustrativos.

1. En modo software/emulado, selecciono Pepper y pulso **Aplicar** con j1=20, j2=55 y j3=−25. JavaScript construye `{"modo":"manual","entradas":[20,55,-25,0]}` y lo manda a `/api/control/ctrl-pepper`.
2. `gateway.post` comprueba modo, rol, cabecera y cuerpo y fija `rol="ctrl-pepper"`. El servidor del emulador llama a `validate`; obtiene un conjunto manual válido y actualiza `control_ui`.
3. En la siguiente oportunidad de 50 ms, `Maestra.paso` genera una línea como `JOINTS,pepper,25,4000,20.0,55.0,-25.0,0` y la transmite al receptor Pepper. Haber recibido respuesta HTTP confirma la orden al emulador; el JSON del robot confirma la fase posterior.
4. `hilo_udp` coloca el mensaje en el buzón. `sim_robot.main` lo interpreta, comprueba destino, renueva la edad y mantiene `siguiendo`. `Humanoide.paso` convierte a radianes, limita objetivos y aplica motores de posición; PyBullet integra la respuesta.
5. `medido()` toma los ángulos efectivos de las articulaciones simuladas. El estado publicado permite revisar comando, objetivo, medido y error. La muestra entregada redondea los tres valores a 20, 55 y −25 y guarda error medio aproximado 0,00093° antes del redondeo.
6. En paralelo, el proceso publica heartbeat, vida y métricas. El monitor no necesita mirar la imagen del robot para decidir disponibilidad: registra sus señales, produce la fila `sim-pepper` y publica su estado MQTT. El panel consulta el estado del robot y el resumen del monitor por rutas distintas.
7. Al detener el proceso Pepper en el guion, llegan despedida/LWT o se agotan las señales de vida. `decidir` declara `CAIDO`, `main` escribe el evento y publica `lab/estado/sim-pepper`. La esclava emulada lo confirma y el panel lo muestra; el CSV registra las muestras de ese intervalo.
8. Al volver a iniciar Pepper, reaparecen vida y HB. El monitor limpia las ventanas de caída, decide otra vez y registra `OK`. La esclava recibe el nuevo retenido. Los eventos anteriores permanecen escritos y permiten obtener los 4,503 s entre transiciones del mismo reloj del monitor.

Así la evidencia contiene **acción solicitada, comando recibido, respuesta de modelo, estado de disponibilidad y archivo histórico**. Un fallo puede localizarse en una frontera concreta: HTTP sin confirmación, UDP sin recepción, objetivo sin respuesta física simulada, MQTT sin conexión o datos sin registro. Esa lectura por etapas también orienta la comprobación futura con placas reales.

## 9. Evidencias de la versión 3.0

**Todas las capturas y grabaciones siguientes se obtuvieron después de cambiar el circuito, los carros y las ilustraciones de conexión.** La sesión se registró el 6 de octubre de 2026; el JSON conserva la fecha UTC de inicio del guion. Los estados y las respuestas se contrastaron con los servicios, además de grabar lo que mostraba el navegador.

Los cuatro GIF reproducen las grabaciones dentro de este README, a 720 px de ancho y 5 cuadros por segundo. Los MP4 guardan la resolución original de **1440 × 1000**, sin audio. Los enlaces siguientes apuntan directamente a archivos incluidos en el proyecto, no a páginas de evidencias externas.

| Demostración | Duración | Archivo original |
|---|---:|---|
| Vista general, nueva carrera y freno de seguridad | 42,00 s | [01-panel-y-carrera.mp4](evidencias/videos/01-panel-y-carrera.mp4) |
| Control de Spot, Pepper y NAO | 35,32 s | [02-robots-control-manual.mp4](evidencias/videos/02-robots-control-manual.mp4) |
| Monitoreo, caída y recuperación | 17,48 s | [03-monitoreo-falla-recuperacion.mp4](evidencias/videos/03-monitoreo-falla-recuperacion.mp4) |
| Consulta de los tres montajes actualizados | 17,84 s | [04-conexiones-esp32.mp4](evidencias/videos/04-conexiones-esp32.mp4) |

**Total de video: 112,64 s. Capturas: 17 PNG.** Las tres imágenes de montaje se incluyen adicionalmente en la sección 6.

### 9.1 Carrera y funcionamiento de los mandos

Se observa el circuito nuevo, la clasificación y el movimiento físico de los seis carros. La cámara de seguimiento muestra la carrocería GT. A continuación se suspende el mando 2 y se verifica su `failsafe`; al reanudarlo, recupera el control.

![Grabación animada de la nueva carrera y sus controles](evidencias/animaciones/01-panel-y-carrera.gif)

**Recorrido por las curvas:** los seis vehículos avanzaron más de 32 m entre las dos muestras de esta comprobación.

![Carros recorriendo el circuito](evidencias/capturas/02d-circuito-en-marcha.png)

**Pérdida de control:** el jugador 2 activa el frenado de seguridad.

![Freno de seguridad por ausencia de mando](evidencias/capturas/02b-freno-seguridad.png)

Los datos completos son `circuito_gp_inicio.json`, `circuito_gp_movimiento.json`, `carrera_failsafe.json`, `carrera_recuperada.json` y `circuito_gp_final.json`, en `evidencias/datos/`. El plano completo y el detalle GT también están incrustados en la sección 3.

### 9.2 Robots: entrada manual y respuesta

El guion cambia los tres objetivos de cada robot desde el panel. El simulador recibe los comandos por UDP, mueve las articulaciones y publica sus mediciones. Se comprueba también el paso de NAO a reposo al suspender las entradas.

![Grabación animada del control de los tres robots](evidencias/animaciones/02-robots-control-manual.gif)

**SpotMicro: altura, cabeceo y alabeo.**

![Control y seguimiento de SpotMicro](evidencias/capturas/03-spot.png)

**Pepper: hombro, codo y cabeza.**

![Control y seguimiento de Pepper](evidencias/capturas/04-pepper.png)

**NAO: hombro, codo y cabeza.**

![Control y seguimiento de NAO](evidencias/capturas/05-nao.png)

**NAO sin entrada reciente: reposo.**

![NAO en reposo tras pausar el mando](evidencias/capturas/05b-nao-reposo.png)

### 9.3 Monitoreo, caída y recuperación de Pepper

Se detiene realmente el proceso `sim-pepper`. El administrador registra la caída, publica el nuevo estado y la esclava emulada confirma su recepción. Los demás servicios siguen enviando latidos. Al reiniciar Pepper se verifica la recuperación del servicio y su indicador.

![Grabación animada de una caída y recuperación reales del proceso](evidencias/animaciones/03-monitoreo-falla-recuperacion.gif)

**Métricas antes de la interrupción.**

![Vista de red y métricas locales](evidencias/capturas/06-red-y-metricas.png)

**Pepper detenido.**

![Pepper marcado como caído](evidencias/capturas/07-pepper-caido.png)

**Confirmación del indicador de la esclava emulada.**

![Indicador de Pepper apagado en la vista general](evidencias/capturas/07b-led-apagado.png)

**Pepper recuperado.**

![Recuperación y eventos del monitor](evidencias/capturas/08-recuperacion.png)

### 9.4 Montajes dentro de la interfaz

La grabación recorre las tres ilustraciones nuevas y las tablas GPIO. Demuestra la consulta de la información de montaje en la aplicación, no el funcionamiento eléctrico de placas físicas.

![Grabación animada de los montajes actualizados](evidencias/animaciones/04-conexiones-esp32.gif)

![Vista del montaje gamer con su tabla de pines](evidencias/capturas/09-montaje-gamer.png)

![Vista del montaje robótico con su tabla de pines](evidencias/capturas/10-montaje-robot.png)

![Vista del montaje de la esclava con su tabla de pines](evidencias/capturas/11-montaje-esclava.png)

### 9.5 Adaptación a móvil

La comprobación utiliza un ancho de 390 px y verifica que la página no desborde horizontalmente.

![Vista móvil de la aplicación](evidencias/capturas/12-vista-movil.png)

## 10. Resultados y análisis

### 10.0 Resumen conjunto y naturaleza de cada resultado

Los valores de esta sección proceden de **la sesión archivada en el ZIP original**. La ampliación del README conserva sus archivos de evidencia. “Medido en software” significa una observación entre procesos realmente ejecutados; “simulado” indica un valor del mundo PyBullet; “calculado” es una operación sobre geometría o registros. Ninguna fila se identifica como medida física de ESP32/robot/switch cuando no existe ese ensayo.

| Objetivo / indicador | Resultado reunido | Naturaleza y condiciones | Fuente verificable |
|---|---|---|---|
| O1 · Contrato y validación | 57 pruebas unitarias históricas aprobadas; 51 de protocolo/pasarela repetidas al revisar esta documentación y aprobadas | Prueba de software. Las 6 de geometría constan en el registro original; no se repitieron en la revisión sin PyBullet instalado | [Registro original](evidencias/datos/pruebas_unitarias.txt); comandos de 11.2 |
| O2 · Movimiento de carrera | Seis carros con avances de 32,66…41,37 m entre las muestras de integración | Simulado en PyBullet; 3 mandos emulados y 3 rivales geométricos | [Integración](evidencias/datos/pruebas_integracion.json), `circuito_gp_inicio/movimiento.json` |
| O2 · Vueltas completas | 3 vueltas por rival y 2 por jugador a 133,096 s; 0 recolocaciones en los seis | Simulado, dentro de la sesión observada | [Estado final de pista](evidencias/datos/circuito_gp_final.json) |
| O2 · Geometría | Perímetro 74,287 m, ancho 1,8 m, tres sectores | Calculado con las muestras de la B-spline y los parámetros del código | `pista.py` y estado de la pista |
| O3 · Seguimiento de Spot | Comando (10; −8; 5), medido (10,03; −8,18; 5,18), error medio 0,12942 | Simulado; muestra puntual y j1 de altura en unidad equivalente | [Spot manual](evidencias/datos/robot_spot_manual.json) |
| O3 · Seguimiento de Pepper | Comando (20; 55; −25), error medio 0,00093° | Simulado; muestra estabilizada, base fija | [Pepper manual](evidencias/datos/robot_pepper_manual.json) |
| O3 · Seguimiento de NAO | Comando (30; 60; 20), error medio 0,00166° | Simulado; muestra estabilizada, base fija | [NAO manual](evidencias/datos/robot_nao_manual.json) |
| O4 · Seguridad sin mando | `player-2` en `failsafe=true` y NAO en `reposo`; recuperación del carro confirmada | Control observado en software y respuesta simulada; pausa de entradas, no caída de ambos servicios | `carrera_failsafe/recuperada.json`, `robot_nao_failsafe.json` |
| O5 · Segmentación y 802.1Q | Configuración y guiones incluidos; sin resultado de aislamiento ejecutado | Implementado, pendiente de prueba Docker/red física | Compose y `pruebas/` |
| O6 · Detección combinada de Pepper | Estado CAIDO y confirmación emulada observados 0,553 s después de solicitar detener | Medido en software por el guion; incluye orden y consultas, no latencia pura del transporte | [Pruebas de integración](evidencias/datos/pruebas_integracion.json) |
| O6 · Intervalo entre CAIDO y OK | 4,503 s entre 1791298855,394 y 1791298859,897 | Calculado sobre dos eventos del mismo monitor; ensayo de interrupción y reinicio | [Eventos JSONL](evidencias/datos/eventos.jsonl) |
| O6 · Calidad del eco | 134/134 respuestas, 100 % observado; RTT medio 0,169 ms, p95 0,263 ms, máximo 3,753 ms, jitter 0,222 ms | Medido en software sobre `127.0.0.1`; estadísticas de la ventana final de 60 muestras salvo el acumulado indicado | [Estado final](evidencias/datos/estado_final.json), `sondeos_extra.eco-admin-local` |
| O7 · Indicadores | CAIDO/OK de Pepper confirmado por esclava emulada | Recepción MQTT probada en software; luz física pendiente | `estado_caida/recuperado.json`, video de monitoreo |
| O8 · Integración e interfaz | 14 comprobaciones aprobadas, 0 errores JS detectados y vista de 390 px sin desbordamiento | Medido por el guion de navegador durante su recorrido | [Integración](evidencias/datos/pruebas_integracion.json) |
| O8 · Material de evidencia | 17 PNG, 4 MP4 con 112,64 s en total, 4 GIF y registros; 3 ilustraciones de montaje | Conteo y duración registrados; GIF derivados e ilustraciones identificadas como IA | [Sesión](evidencias/datos/sesion_v3.json), manifiesto y sección 9 |

No se calcula una disponibilidad universal del sistema a partir de 134 respuestas de un solo eco local. Tampoco se compara el error de Spot como si sus tres canales fueran tres articulaciones homogéneas. El tiempo de las vueltas es tiempo de simulación; el tiempo de detección de la falla es un intervalo observado por el guion/monitor y debe conservar su instrumento de origen.

### 10.1 Pruebas automatizadas

Se aprobaron **57 pruebas unitarias**: 40 de protocolo, 11 de controles/pasarela y 6 de geometría. Las seis nuevas comprueban el cierre periódico de la pista, proyección del centro, proyección de carriles, curvas en ambos sentidos sin plegar el borde interior, continuidad del muestreo y validez de la malla GT. El registro íntegro está en `evidencias/datos/pruebas_unitarias.txt`.

El navegador aprobó **14 comprobaciones de integración**, conservadas en `evidencias/datos/pruebas_integracion.json`:

| Comprobación | Resultado |
|---|---|
| Carga del circuito técnico | Nexo GP, 74,287 m y 1,8 m de ancho |
| Movimiento de los seis carros | Avances de 32,66 a 41,37 m en el intervalo observado |
| Pausa del mando 2 | `failsafe=true` |
| Reanudación del mando 2 | `failsafe=false`; seis vehículos presentes |
| Control manual de Spot | Comando recibido y error inferior al criterio de 5 unidades |
| Control manual de Pepper | Comando recibido y error inferior a 5° |
| Control manual de NAO | Comando recibido y error inferior a 5° |
| Pausa de entradas de NAO | Estado `reposo` |
| Caída de Pepper | Servicio e indicador confirmado en `CAIDO` |
| Recuperación de Pepper | Servicio e indicador confirmado en `OK` |
| Descarga CSV | Archivo recibido con muestras |
| Vista móvil | Sin desbordamiento a 390 px |
| JavaScript | Ninguna excepción detectada durante el recorrido |
| Vueltas del nuevo trazado | Los seis vehículos completan vueltas |

### 10.2 Carrera prolongada durante las demostraciones

La simulación de carrera continuó mientras se grababan las otras vistas. En la muestra final, a **133,096 s de simulación**, se registró:

| Vehículo | Vueltas completadas | Recolocaciones |
|---|---:|---:|
| `auto-1` | 3 | 0 |
| `auto-2` | 3 | 0 |
| `auto-3` | 3 | 0 |
| `player-1` | 2 | 0 |
| `player-2` | 2 | 0 |
| `player-3` | 2 | 0 |

Esto demuestra que el nuevo recorrido es transitable por los seis pilotos durante la sesión observada. No es una garantía de ausencia de colisiones o atascos para cualquier entrada humana o una ejecución indefinida.

### 10.3 Seguimiento de los robots

| Robot | Comando j1, j2, j3 | Medición publicada | Error medio de la muestra |
|---|---|---|---:|
| SpotMicro | 10; −8; 5 | 10,03; −8,18; 5,18 | 0,12942 |
| Pepper | 20; 55; −25 | 20,00; 55,00; −25,00 | 0,00093° |
| NAO | 30; 60; 20 | 30,00; 60,00; 20,00 | 0,00166° |

El error es la media del valor absoluto de las diferencias entre objetivo y medición, antes de redondear los valores mostrados. En Spot, j1 expresa altura en unidades equivalentes del mando, por lo que el error combinado no debe interpretarse como el ángulo de una sola articulación. Son muestras puntuales tras estabilizarse; no son una caracterización estadística ni mediciones de motores reales.

### 10.4 Interrupción y recuperación

El guion observó conjuntamente `sim-pepper=CAIDO` y la confirmación MQTT de la esclava **0,553 s** después de emitir la orden de detención. El intervalo incluye orquestación y consultas del navegador; no mide la latencia de la red ni el tiempo de un LED físico.

El monitor registró `CAIDO` en t=1791298855,394 y `OK` en t=1791298859,897: **4,503 s entre ambas transiciones** de la interrupción deliberada. Las transiciones iniciales de arranque se conservan en los datos y no se contabilizan como esta falla.

### 10.5 Sondeo UDP local

Fuente: `estado_final.json → sondeos_extra → eco-admin-local`.

| Magnitud | Valor observado |
|---|---:|
| Solicitudes / respuestas acumuladas | 134 / 134 |
| Disponibilidad del eco observada | 100 % |
| Muestras en la ventana final | 60 |
| RTT medio de la ventana | 0,169 ms |
| RTT p95 | 0,263 ms |
| RTT máximo | 3,753 ms |
| Jitter RTT | 0,222 ms |

El destino fue `127.0.0.1`; origen, secuencia y marca temporal de las respuestas se validaron. Las columnas de sondeo que no tienen una medición se mantienen vacías. Este resultado no permite concluir nada sobre interferencia Wi-Fi, latencia entre VLAN o pérdidas de las ESP32 físicas.

### 10.6 Entorno, datos y límites

La sesión utilizó Python 3.12.14, NumPy 2.3.5, Pillow 12.3.0, paho-mqtt 2.1.0, websockets 15.0.1, qiBullet 1.4.6, Mosquitto 2.0.18, Chromium 138 y Playwright 1.62.1. PyBullet se cargó desde un binario disponible en el entorno sin metadatos de distribución que certifiquen su versión exacta. `requirements-local.txt` y los Dockerfile fijan las dependencias propuestas para una instalación reproducible con Python 3.11; esa construcción Docker no se ejecutó durante estas grabaciones.

| Archivos de evidencia | Contenido |
|---|---|
| `evidencias/capturas/` | Las 17 capturas PNG incrustadas en este README |
| `evidencias/videos/` | Cuatro grabaciones MP4 originales |
| `evidencias/animaciones/` | Cuatro GIF derivados de esas grabaciones |
| `evidencias/datos/pruebas_integracion.json` | Condiciones comprobadas, duración de videos y alcance de la sesión |
| `evidencias/datos/circuito_gp_*.json` | Progreso, geometría, vueltas y recolocaciones del circuito nuevo |
| `evidencias/datos/robot_*` | Comandos, objetivos, mediciones y reposo de los robots |
| `evidencias/datos/estado_*.json` | Estado inicial, caída, recuperación y estado final |
| `evidencias/datos/metricas_exportadas.csv` | Descarga realizada desde el panel durante la prueba |
| `evidencias/datos/admin_metricas.csv`, `eventos.jsonl` | Registro completo del monitor durante la sesión |
| `evidencias/datos/logs/` | Salidas de los procesos ejecutados |
| `evidencias/MANIFIESTO_SHA256.txt` | Huellas de integridad de los archivos de evidencia |

Las pruebas respaldan la cadena de software y las interfaces. Quedan por validar con el entorno correspondiente: compilación/carga y lectura de las placas, LED físicos, construcción y ejecución Docker, aislamiento de redes, VLAN 802.1Q y publicación de los repositorios. El manifiesto permite detectar modificaciones de archivos, pero no sustituye esas validaciones.

## 11. Reproducir las pruebas

Esta sección es opcional para revisar la entrega: las evidencias ya están integradas arriba. Sirve para repetir la ejecución y obtener una sesión nueva.

### 11.1 Preparación local

Se necesitan Python 3.11, Mosquitto, Node.js/npm, Chromium con Playwright y FFmpeg/ffprobe. El guion inicia un broker de prueba propio; no necesita que Mosquitto esté previamente activo como servicio. La primera instalación y descarga de modelos necesitan conexión a internet.

En Linux, desde la raíz del proyecto:

```bash
python3.11 -m venv entorno
. entorno/bin/activate
python -m pip install -r requirements-local.txt
npm install
npx playwright install chromium
python zona-robotica/descargar_modelos.py --destino modelos/rex
ACEPTO_LICENCIA_SOFTBANK=no DESTINO=modelos/qibullet python zona-robotica/instalar_mallas_softbank.py
```

Instala Mosquitto, FFmpeg y las bibliotecas de Chromium necesarias con el gestor de paquetes de tu sistema. Si el ejecutable de Mosquitto no está en PATH, indica su ruta completa al parámetro `--broker`.

La preparación equivalente en PowerShell es:

```powershell
py -3.11 -m venv entorno
.\entorno\Scripts\python.exe -m pip install -r requirements-local.txt
npm install
npx playwright install chromium
.\entorno\Scripts\python.exe zona-robotica\descargar_modelos.py --destino modelos\rex
$env:ACEPTO_LICENCIA_SOFTBANK = "no"
$env:DESTINO = "modelos\qibullet"
.\entorno\Scripts\python.exe zona-robotica\instalar_mallas_softbank.py
```

En Windows también se requieren Mosquitto y FFmpeg instalados. La grabación incluida se ejecutó en Linux; no se afirma que el procedimiento nativo de Windows se haya ensayado en esta sesión.

### 11.2 Pruebas unitarias

Las primeras 51 pruebas solo necesitan la biblioteca estándar:

```bash
python -m unittest pruebas.test_protocolo pruebas.test_nexo -v
```

Con NumPy y PyBullet instalados, ejecuta las **57**:

```bash
python -m unittest pruebas.test_protocolo pruebas.test_nexo pruebas.test_pista_gp -v
```

En PowerShell sin activar el entorno, sustituye `python` por `.\entorno\Scripts\python.exe`. La salida archivada corresponde a las 57 pruebas de esta entrega.

### 11.3 Laboratorio local interactivo

```bash
python tools/laboratorio_local.py --modelos modelos --broker mosquitto
```

Abre `http://127.0.0.1:18180`. Para salir, pulsa Ctrl+C. En Windows puedes pasar `--broker "C:\Program Files\mosquitto\mosquitto.exe"` si esa es la ruta instalada.

| Servicio local | Puerto |
|---|---|
| Panel / monitor | TCP 18180 |
| Broker de prueba | TCP 18883 |
| Heartbeat y eco | UDP 15300 |
| Pista / WebSocket | TCP 18010 / 18765 |
| HTTP de los robots | TCP 18011, 18012, 18013 |
| Mandos gamer | UDP 15001, 15002, 15003 |
| Mandos robóticos | UDP 15101, 15102, 15103 |
| Control de entradas emuladas | TCP 19090 |

Cierra el despliegue Docker si está ocupando 18180 antes de iniciar el laboratorio local. Esta variante ejecuta procesos y comunicaciones sobre loopback; no crea contenedores ni redes VLAN.

### 11.4 Regenerar capturas, videos y animaciones

Con el laboratorio anterior cerrado:

```bash
python tools/ejecutar_evidencias.py --modelos modelos --broker mosquitto
```

El guion espera a los servicios y renders, recorre la interfaz, pausa/reanuda el mando 2, cambia objetivos de los robots, detiene/reinicia Pepper, descarga el CSV y comprueba vueltas completas. Al terminar archiva los registros y crea los GIF a partir de los MP4. Para convertir únicamente los videos ya existentes:

```bash
python tools/crear_animaciones.py
```

`NEXO_CHROMIUM` permite indicar un ejecutable de Chromium compatible. Los guiones sobrescriben las capturas, videos, animaciones y datos de sus mismas rutas y reinician `resultados/sesion-evidencias`; utiliza una copia del proyecto si quieres conservar la sesión original.

Este README describe los números de la sesión entregada: **no se actualizan automáticamente sus tablas al repetir las pruebas**. Antes de entregar otra sesión, sustituye sus valores por los nuevos resultados y regenera el manifiesto. Las métricas pueden variar entre ejecuciones. Usa el mismo reloj para restar tiempos de un intervalo; no mezcles directamente las marcas de procesos diferentes.

Para regenerar las huellas de las evidencias después de una nueva sesión:

```bash
python -c "from pathlib import Path; import hashlib; p=Path('evidencias'); m=p/'MANIFIESTO_SHA256.txt'; m.write_text(''.join(hashlib.sha256(f.read_bytes()).hexdigest()+'  '+f.relative_to(p).as_posix()+'\n' for f in sorted(p.rglob('*')) if f.is_file() and f!=m), encoding='utf-8')"
```

### 11.5 Ensayos del despliegue Docker y de red

Estos ensayos no están marcados como aprobados en esta entrega. Para prepararlos en Windows:

```powershell
py -3.11 -m venv entorno
.\entorno\Scripts\python.exe -m pip install paho-mqtt matplotlib
.\pruebas\correr_todo.ps1
```

El guion ejecuta primero las 51 pruebas de protocolo/pasarela, construye el laboratorio, verifica aislamiento, provoca interrupciones de contenedores y mide red base y red con `tc netem`. `-ConCarga` agrega el escenario de mayor frecuencia de emisión. Los resultados van a `pruebas/resultados/`. La geometría se comprueba por separado con las seis pruebas adicionales y las dependencias de simulación.

Para inspeccionar manualmente el router en el despliegue:

```powershell
docker compose exec router vtysh -c "show ip route"
docker compose exec router iptables -n -v -L AISLAR_V1_V2
```

La variante macvlan se aplica con ambos archivos Compose en un host Linux cuya interfaz y switch estén configurados para la troncal:

```bash
docker compose -f docker-compose.yml -f docker-compose.vlan-real.yml up -d --build
```

Revisa primero los valores de `parent` y las subredes del archivo. La opción no convierte por sí sola una red Wi-Fi de Windows en un switch 802.1Q.

## 12. Publicación de la entrega

### GitHub: agregar al repositorio de segundo corte

Esta tarea pertenece al repositorio existente [felipeR1428/corte-2-tareas](https://github.com/felipeR1428/corte-2-tareas), rama `main`. Su contenido se ubica en **`08-nexolab-esp32-vlan/`**, con este README y Compose dentro de la carpeta 8. El README principal del repositorio mantiene el índice de todas las tareas.

La entrega completa reúne las tareas 4, 5, 6, 7 y 8. Para actualizar una copia local que ya contiene las cuatro primeras, basta copiar **la carpeta 8 completa y el README principal**. Las carpetas 4–7 y sus nombres se mantienen. Conserva `.git` de la copia que ya tiene `origin`; los ZIP de entrega contienen archivos de proyecto y no una segunda configuración Git.

En Git Bash, **desde la raíz de `corte-2-tareas`**, después de copiar:

```bash
git status
git remote -v
git add README.md 08-nexolab-esp32-vlan
git diff --cached --stat
git commit -m "Agregar tarea 8 NexoLab con documentación y evidencias"
git push origin main
```

En el estado preparado deben aparecer el índice principal y las adiciones dentro de `08-nexolab-esp32-vlan`. Usa la misma rama y remoto existentes. Al subir, comprueba [la carpeta de la tarea 8](https://github.com/felipeR1428/corte-2-tareas/tree/main/08-nexolab-esp32-vlan) y que carguen sus imágenes y GIF. El enlace anterior [de la tarea 6](https://github.com/felipeR1428/corte-2-tareas/tree/main/06-brazo-opencv) conserva la misma ruta.

Si `push` informa que el remoto tiene commits nuevos, revisa primero que no haya cambios locales pendientes, actualiza con `git pull --rebase origin main` y vuelve a intentar el push. Si aparece un conflicto, detente para resolver el archivo indicado antes de continuar; no lo soluciones renombrando las carpetas de las tareas. Los MP4 se incluyen dentro de esta tarea y GitHub puede ofrecerlos como descarga según el visor; los cuatro GIF se muestran directamente en el README.

`entorno/`, `.venv/`, `node_modules/`, modelos descargados y resultados temporales están excluidos por el `.gitignore` de la tarea. Las evidencias archivadas, las ilustraciones y los programas sí se conservan; los modelos que se descargan al instalar no se deben confundir con los datos ya registrados de la demostración.

### Docker Hub

En la carpeta del proyecto, con Docker Desktop iniciado:

```powershell
docker login
.\publicar_dockerhub.ps1 -Usuario TU_USUARIO -Version 3.0
```

El script construye y publica `nexolab-router`, `nexolab-admin`, `nexolab-servidor-pista`, `nexolab-jugador`, `nexolab-robot` y `nexolab-emulador`. Solo si todos los comandos terminan correctamente escribe `resultados/publicacion_dockerhub.json` con fecha y enlaces. La imagen robótica se publica en la variante sin mallas privadas de SoftBank.

Comprueba que las seis imágenes aparecen con la etiqueta `3.0`. Para verificar una descarga después de publicarlas:

```powershell
$env:DOCKERHUB_USUARIO = "TU_USUARIO"
$env:NEXO_VERSION = "3.0"
$env:NEXO_MODO = "emulado"
docker compose --profile emulado pull
docker compose --profile emulado up -d --no-build
```

Si el docente exige enlaces publicados, agrégalos a este README cuando existan. Esta carpeta no contiene URLs inventadas de repositorios ni una confirmación de publicación que no se haya realizado.

## 13. Solución de problemas

| Síntoma | Comprobación y acción |
|---|---|
| `no configuration file provided` | Entra primero en la carpeta que contiene `docker-compose.yml` |
| El botón Run abre una versión antigua | Reconstruye con `.\iniciar.ps1 -Modo emulado -Reconstruir` y recarga el navegador |
| Puerto 18180 ocupado | Cierra el laboratorio local u otro proyecto que use ese puerto; comprueba los puertos de Compose antes de cambiar la URL |
| Los servicios aún no aparecen | Espera los primeros latidos y la carga de modelos; revisa `docker compose ps` y los logs del servicio |
| El panel abre, pero una ESP32 no responde | Revisa Wi-Fi de 2,4 GHz, IP del computador, rol, puerto UDP y reglas del firewall |
| El mando físico compite con el emulado | Usa `iniciar.ps1 -Modo fisico`, que detiene los emuladores |
| Un eje del joystick va invertido | Ajusta `INVERTIR_A` o `INVERTIR_B` en el firmware y vuelve a cargarlo |
| j3 se mueve sin potenciómetro | Cambia `CANAL_C_CONECTADO` a 0 |
| La esclava no conecta a MQTT | Revisa IP, TCP 1883, broker y monitor serie; verifica que no haya aislamiento de clientes Wi-Fi |
| Pausar mando no marca el jugador como CAIDO | Es esperado: el heartbeat sigue activo; revisa el `failsafe` del carro |
| Un robot pasa a reposo | Comprueba si ha dejado de recibir `JOINTS` durante un segundo |
| Falló la descarga del modelo | Revisa internet y el log de construcción; el primer arranque necesita descargar recursos |
| No se ven las imágenes del README | Extrae también `img/` y `evidencias/`; usa un visor Markdown, no un editor de texto sin vista previa |
| El GIF se ve pequeño o con menos colores | Abre el MP4 local incluido para ver la grabación original a 1440 × 1000 |

## 14. Recursos técnicos y registro de imágenes

### Modelos y dependencias públicas

- **Racecar:** modelo articulado de `pybullet_data`, proyecto [Bullet/PyBullet](https://github.com/bulletphysics/bullet3). La carrocería visual Nexo GT se genera desde el código del proyecto.
- **SpotMicro/Rex:** [rex-gym](https://github.com/nicrusso7/rex-gym), commit `26663048bd3c3da307714da4458b1a2a9dc81824`. El descargador obtiene el URDF, las mallas y su archivo de atribución. El código/URDF de rex-gym utiliza Apache-2.0; las mallas reconocen a Deok-yeon Kim / KDY0523 mediante CC BY 3.0.
- **Pepper y NAO:** URDF de [qiBullet](https://github.com/softbankrobotics-research/qibullet) 1.4.6. Esta entrega utiliza geometría visual simplificada, sin incluir las mallas privadas opcionales.
- **Redes:** documentación de Docker sobre [bridge](https://docs.docker.com/engine/network/drivers/bridge/) y [macvlan](https://docs.docker.com/engine/network/drivers/macvlan/).
- **Publicación:** documentación de [GitHub](https://docs.github.com/en/repositories/working-with-files/managing-files/adding-a-file-to-a-repository) y [Docker Compose push](https://docs.docker.com/reference/cli/docker/compose/push/).
- **Actualización Git:** [enviar commits al repositorio remoto](https://docs.github.com/en/get-started/using-git/pushing-commits-to-a-remote-repository), documentación de GitHub.
- **Retenidos y persistencia del broker:** [configuración de Mosquitto](https://mosquitto.org/man/mosquitto-conf-5.html); la configuración incluida selecciona `persistence false`.
- **Referencias del área:** [RL Baselines3 Zoo](https://github.com/DLR-RM/rl-baselines3-zoo) y [humanoid-gym](https://github.com/0aqz0/humanoid-gym). Los pilotos de esta entrega son controladores geométricos; no se afirma haber entrenado un agente de aprendizaje por refuerzo.

Los recursos externos conservan sus condiciones de uso. El ZIP no incorpora navegadores, intérpretes, entornos instalados ni las mallas privadas de SoftBank.

### Ilustraciones de conexión

Las imágenes se generaron mediante **edición de imagen / cambio de estilo con el generador de imágenes de ChatGPT**, usando un montaje de referencia por imagen. Se pidió aspecto de fotografía de producto, componentes realistas, luz suave y una leyenda que identifica su origen como ilustración. No se generaron las capturas del programa con IA: esas proceden de la ejecución grabada.

| Archivo generado | Descripción |
|---|---|
| `img/montaje-realista-gamer.png` | ESP32, joystick KY-023 y conexiones de orientación |
| `img/montaje-realista-robot.png` | ESP32, tres potenciómetros y pulsador |
| `img/montaje-realista-esclava.png` | ESP32, seis LED y seis resistencias |

Los prompts usados se conservan a continuación dentro de este mismo README para documentar el proceso de creación de las ilustraciones.

<details>
<summary>Prompts completos de las tres ilustraciones</summary>

**GAMER — modo edición / cambio de estilo**

```text
Use case: style-transfer. Edit target: the attached image of an ESP32 DevKit 38-pin and KY-023 joystick wiring. Replace the crude polygonal 3D-render appearance with a highly realistic educational product-photo illustration on a neutral electronics workbench: real black PCB, ESP32 metal shielding, metallic pin headers, supple colored Dupont cables, detailed joystick board and black joystick cap, natural soft lighting and believable shadows. Maintain one ESP32, one joystick and USB power, no extra instruments or hands. Preserve intended connections: joystick VRx to GPIO34, VRy to GPIO35, SW to GPIO32, GND to GND, module VCC to ESP32 3V3 (not 5V). Use a clear overhead three-quarter composition, landscape, components unobstructed. Remove the old diagram's crowded labels and title; add only a restrained heading 'ESP32 + joystick' and a readable small footer 'Ilustración generada con IA · montaje orientativo'. Do not depict a functioning experimental measurement or claim this is a real hardware photograph. No 3D-render look, no plastic polygonal electronics, no company logos added.
```

**ROBOT — modo edición / cambio de estilo**

```text
Use case: style-transfer. Edit target: the attached image of the ESP32 robotic controller with three potentiometers and a pushbutton on a breadboard. Transform this into a photorealistic educational product-photo illustration: one real-looking ESP32 DevKit 38-pin, exactly three rotary 10k potentiometers with black ribbed knobs, one tactile pushbutton, a white solderless breadboard, supple Dupont wires and USB cable. Neutral electronics desk, soft natural light, realistic metal, plastic and PCB surfaces, clear overhead three-quarter view, landscape. Preserve the intended wiring: pot1 wiper GPIO34, pot2 wiper GPIO35, pot3 wiper GPIO39/VN; outer terminals 3V3 and GND; button GPIO32 to GND with internal pull-up. GND shared, no external power supply. Remove the old image's dense callouts and 3D aesthetic. Add only heading 'ESP32 + 3 potenciómetros' and small clearly legible footer 'Ilustración generada con IA · montaje orientativo'. Preserve exactly three potentiometers and one button. No hands, no oscilloscopes, no assertion of tested physical operation.
```

**ESCLAVA — modo edición / cambio de estilo**

```text
Use case: style-transfer. Edit target: the attached ESP32 slave and six-LED breadboard illustration. Make a photorealistic educational product-photo style image on a clean electronics workbench. Exactly one ESP32 DevKit 38-pin with USB power, exactly six individual through-hole LEDs on a white solderless breadboard in the order red, yellow, green, red, yellow, green, exactly six individual axial 220-ohm resistors, colored Dupont jumpers, realistic PCB, metal leads and breadboard texture. Preserve intended circuit: GPIO16,17,18,19,21,22 respectively drive LED1..6 anodes; each LED cathode connects through its own 220-ohm resistor to the shared GND rail; ESP32 GND to that rail. LEDs need not glow, no measurement or claimed successful experiment. Overhead three-quarter landscape, neutral desk, soft light and realistic shadows. Remove old crowded connector callouts and polygonal 3D aesthetic. Only heading 'ESP32 esclava · 6 indicadores LED' and small readable footer 'Ilustración generada con IA · montaje orientativo'. No extra LEDs, resistors, boards, meters or hands.
```

</details>

## 15. Conclusiones relacionadas con los objetivos

**Sobre la integración de entradas y comunicaciones (O1–O2).** Logré revisar una cadena en la que cada fuente identifica su destino, construye el mismo contrato y pasa por los receptores antes de afectar la simulación. La prueba de tres jugadores y tres rivales muestra que puedo mantener varias fuentes de control en un mundo común. Aprendí que el éxito de un envío o una conexión abierta no es suficiente para afirmar que una orden se aplicó: tengo que observar recepción, frescura y estado del modelo. Me falta comprobar las lecturas ADC, el cableado y la transmisión de las seis maestras físicas con el firmware cargado.

**Sobre el movimiento y la seguridad (O3–O4).** Los registros permiten comprobar los tres objetivos manuales de cada robot y la respuesta de sus modelos. También pude distinguir reposo por falta de mando de caída del servicio: NAO puede continuar vivo mientras retorna a una postura segura, y un carro puede frenar mientras su jugador sigue publicando heartbeat. Aprendí a separar comando, objetivo limitado y medición, especialmente en Spot, donde la altura y la inclinación del cuerpo se convierten a movimientos de varias patas. Estos resultados respaldan la lógica en PyBullet; todavía no caracterizan precisión, fuerza, fricción o seguridad de actuadores físicos.

**Sobre disponibilidad y recuperación (O6–O7).** La interrupción de Pepper deja una secuencia verificable `OK → CAIDO → OK`, con motivo MQTT, tiempos y confirmación de la esclava emulada. Pude relacionar el evento del monitor con el indicador del panel y con el archivo histórico. La recuperación demuestra la actualización del estado después de reanudar el proceso en ese ensayo. Aprendí que los retenidos ayudan a reconectar un consumidor, pero con la configuración actual no sobreviven al reinicio del broker. Debo completar la prueba con LED físicos y ensayar también pérdida de Wi-Fi, caída del broker y degradación sostenida, en lugar de generalizar desde una sola interrupción local.

**Sobre las redes (O5).** Dejé definidas tres zonas, sus direcciones, rutas y una política de reenvío que bloquea gamer ↔ robótica. La ejecución archivada utiliza loopback, por lo que no puedo concluir que ya medí aislamiento entre contenedores o etiquetas 802.1Q. La diferencia entre bridges y VLAN reales fue un aprendizaje del diseño: el diagrama expresa la política y las reglas la implementan, pero el resultado debe demostrarse con tráfico y contadores en el despliegue. Mi siguiente comprobación debe construir las imágenes, ejecutar los guiones de aislamiento y, si se dispone de la infraestructura, observar la variante macvlan y el switch configurado.

**Sobre resultados y evidencia (O8).** Reuní resultados con su archivo, condición e instrumento: geometría calculada, movimiento simulado, eco UDP medido entre procesos y tiempos obtenidos de eventos. Las 17 capturas y los cuatro videos permiten explicar lo que se observa, mientras los JSON/CSV permiten contrastarlo con datos. Aprendí que una ilustración de conexiones, aunque tenga aspecto realista, no acredita un montaje ensayado; por eso la identifico y mantengo los GPIO del firmware como referencia. La revisión documental conserva la evidencia original, vuelve a comprobar protocolo/pasarela y distingue esos controles de una ejecución nueva completa.

**Pendientes concretos.** Para cerrar experimentalmente el objetivo general necesito cargar las siete ESP32, probar valores y botón por monitor serie, verificar los seis LED, construir y ejecutar Docker, medir aislamiento/latencia/pérdida en las rutas reales y conservar una carpeta por sesión. También conviene repetir varias veces cada escenario de falla y seguimiento, reunir distribución de errores/tiempos y registrar condiciones de carga antes de comparar versiones. El criterio de esta entrega es presentar lo comprobado con su alcance y dejar una ruta reproducible para esas pruebas siguientes.
