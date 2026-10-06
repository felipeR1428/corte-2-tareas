// config.h - Ajustes de la ESP32 esclava de LEDs (plano de administracion, VLAN 3).
//
// Hay una sola esclava, asi que aqui no hay roles: solo la red, la IP del PC (donde Docker publica
// el broker MQTT del admin) y los pines de los seis LEDs. Los nombres de los servicios y los textos
// de estado son los del contrato del tema (los publica el admin en lab/estado/<servicio>).
#pragma once

// =============================================================================================
// Red (misma red WiFi y misma IP del PC que en esp32_maestra/config.h)
// =============================================================================================
#define WIFI_SSID    "MI_RED_WIFI"     // solo 2,4 GHz
#define WIFI_CLAVE   "mi_clave_wifi"

// IP del PC en la red WiFi. El broker Mosquitto vive en el contenedor admin (192.168.30.10), pero esa
// IP solo existe dentro de Docker: la placa se conecta a la IP del PC, puerto 1883, y Docker lo
// reenvia al admin.
#define IP_PC_0 192
#define IP_PC_1 168
#define IP_PC_2 1
#define IP_PC_3 100

// IP fija opcional (mismo criterio que en la maestra: en la subred del WiFi real, fuera del rango
// del DHCP del router; NUNCA la 192.168.30.40 de la esclava emulada, que es de la red de Docker).
#define USAR_IP_FIJA 0
#define IP_RED_0 192
#define IP_RED_1 168
#define IP_RED_2 1
#define IP_FIJA_ULTIMO 220
#define IP_PUERTA_ULTIMO 1

#define PUERTO_MQTT       1883
#define PUERTO_ADMIN      5300   // HB por UDP al admin
#define PUERTO_LOCAL      5400   // puerto UDP propio de donde sale el HB
#define REINTENTO_WIFI_MS 5000
#define REINTENTO_MQTT_MS 2000   // cada cuanto se intenta reconectar al broker
#define TIMEOUT_TCP_MS    800    // tope de espera del connect() TCP al broker (ver el .ino)
#define MQTT_KEEPALIVE_S  5      // igual que los contenedores (comun/lab.py): si la placa se cuelga
                                 // o se apaga, el broker lo nota en ~1,5 x 5 s y publica el LWT

// Nombre de este cliente en MQTT y en el HB. El "origen" del HB y el topico lab/vivo/ usan
// "esclava" (el nombre del contrato). El client id del broker lleva ademas un sufijo: si la esclava
// emulada (contenedor) y esta placa estuvieran conectadas a la vez con el MISMO client id, Mosquitto
// echaria a una cada vez que entra la otra y se pasarian el dia reconectando.
#define NOMBRE_SERVICIO "esclava"
#define CLIENT_ID_MQTT  "esclava-esp32"

// =============================================================================================
// Tiempos
// =============================================================================================
#define PERIODO_HB_MS       1000   // HB,esclava,... al admin
#define PERIODO_LEDS_MS     2000   // linea LEDS por serie aunque no cambie nada
#define PERIODO_ESTADO_MS   2000   // linea ESTADO por serie
#define SIN_BROKER_MS       5000   // sin broker mas de 5 s -> patron "sin datos" en todos los LEDs

// =============================================================================================
// Pines: un LED por servicio, en el orden del contrato (player-1..3, sim-spot, sim-pepper, sim-nao).
// Todos son salidas "tranquilas" del ESP32: no son de arranque (strapping), no van a la flash, no
// son solo-entrada y existen en las placas de 30 y de 38 pines. Estan uno al lado del otro en el
// mismo costado de la placa (16, 17, 18, 19, 21, 22: el 20 no existe en el ESP32), asi que los seis
// LEDs quedan en fila en la protoboard en el mismo orden que el dashboard.
// =============================================================================================
#define N_SERVICIOS 6
#define PIN_LED_PLACA 2   // LED azul de la placa = conectada al broker

// Los LEDs van de GPIO a anodo, catodo a resistencia, resistencia a GND: un 1 los enciende.
// Con 220-330 ohm la corriente queda en 4-6 mA por LED (ver el calculo en el README).
// OJO: en placas con modulo WROVER (con PSRAM) GPIO 16 y 17 estan ocupados por la PSRAM; ahi se
// cambian los dos primeros por 25 y 26. Las DevKit comunes traen WROOM-32 y no tienen ese problema.
#define PINES_LEDS {16, 17, 18, 19, 21, 22}
#define SERVICIOS  {"player-1", "player-2", "player-3", "sim-spot", "sim-pepper", "sim-nao"}
