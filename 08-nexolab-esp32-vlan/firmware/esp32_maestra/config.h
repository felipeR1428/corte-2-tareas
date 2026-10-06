// config.h - Todo lo que se ajusta de una ESP32 maestra (los mandos de las zonas), en un solo lugar.
//
// El MISMO sketch (esp32_maestra.ino) se sube a las seis maestras; lo unico que cambia entre ellas
// es ROL. Hay dos formas de elegirlo:
//   1. Cambiar el valor de ROL abajo antes de subir desde el Arduino IDE.
//   2. Compilar desde la linea de comandos con
//        arduino-cli compile ... --build-property "compiler.cpp.extra_flags=-DROL=CTRL_SPOT"
//      (por eso el #define va dentro de #ifndef: si el compilador ya trae ROL, gana ese).
//      firmware/compilar.ps1 hace esto para los seis roles de una vez.
//
// Lo que hay que editar SI o SI antes de subir a una placa de verdad:
//   - WIFI_SSID / WIFI_CLAVE: la red WiFi (2,4 GHz) a la que esta conectado el PC.
//   - IP_PC_*: la IP del PC en ESA red (en Windows: ipconfig, "Direccion IPv4" del adaptador WiFi).
//   - (opcional) USAR_IP_FIJA y la IP fija de cada rol, si se quiere lo que pide el diagrama.
//
// Los formatos y puertos tienen que ser LOS MISMOS que en comun/protocolo.py y en el contrato del
// tema (docker-compose publica 5001-5003, 5101-5103 y 5300 en el PC): si se cambia uno aqui, hay
// que cambiarlo alla tambien.
#pragma once

// =============================================================================================
// Rol de la placa
// =============================================================================================
// Los nombres de los roles son numeros para poder compararlos en el preprocesador (#if).
#define CTRL_1       1   // mando del carro de player-1 (zona gamer, VLAN 1)
#define CTRL_2       2   // mando del carro de player-2
#define CTRL_3       3   // mando del carro de player-3
#define CTRL_SPOT    4   // mando del Spot simulado (zona robotica, VLAN 2)
#define CTRL_PEPPER  5   // mando del Pepper simulado
#define CTRL_NAO     6   // mando del NAO simulado

#ifndef ROL
#define ROL CTRL_1
#endif

#if (ROL < CTRL_1) || (ROL > CTRL_NAO)
#error "ROL tiene que ser CTRL_1, CTRL_2, CTRL_3, CTRL_SPOT, CTRL_PEPPER o CTRL_NAO"
#endif

// Todo lo que depende del rol se deriva aqui, para que el .ino no tenga un #if por cada cosa.
//   ES_GAMER        1 = manda CTRL (dir/vel) a un player; 0 = manda JOINTS (grados) a un sim.
//   ORIGEN          nombre con el que se presenta en HB/PING y en la linea ESTADO por serie
//                   (son los nombres del contrato: el admin los muestra tal cual en el dashboard).
//   PUERTO_DESTINO  puerto PUBLICADO en el PC que docker reenvia al contenedor de este mando
//                   (5001-5003 -> player-1..3:5000, 5101-5103 -> sim-spot/pepper/nao:5100).
#if ROL == CTRL_1
  #define ES_GAMER 1
  #define ID_JUGADOR 1
  #define ORIGEN "ctrl-1"
  #define PUERTO_DESTINO 5001
#elif ROL == CTRL_2
  #define ES_GAMER 1
  #define ID_JUGADOR 2
  #define ORIGEN "ctrl-2"
  #define PUERTO_DESTINO 5002
#elif ROL == CTRL_3
  #define ES_GAMER 1
  #define ID_JUGADOR 3
  #define ORIGEN "ctrl-3"
  #define PUERTO_DESTINO 5003
#elif ROL == CTRL_SPOT
  #define ES_GAMER 0
  #define NOMBRE_ROBOT "spot"
  #define ORIGEN "ctrl-spot"
  #define PUERTO_DESTINO 5101
#elif ROL == CTRL_PEPPER
  #define ES_GAMER 0
  #define NOMBRE_ROBOT "pepper"
  #define ORIGEN "ctrl-pepper"
  #define PUERTO_DESTINO 5102
#else
  #define ES_GAMER 0
  #define NOMBRE_ROBOT "nao"
  #define ORIGEN "ctrl-nao"
  #define PUERTO_DESTINO 5103
#endif

// =============================================================================================
// Red WiFi (la red REAL de la casa / laboratorio / hotspot del celular, no las VLAN de Docker)
// =============================================================================================
#define WIFI_SSID    "MI_RED_WIFI"     // el ESP32 solo ve redes de 2,4 GHz
#define WIFI_CLAVE   "mi_clave_wifi"

// IP del PC donde corre docker compose, en la red WiFi. Los ESP32 NO hablan con las IP de los
// contenedores (192.168.10.x / .20.x / .30.x): esas redes existen solo dentro de Docker, en el PC.
// Le mandan todo a la IP del PC y Docker reenvia cada puerto publicado al contenedor que toca.
#define IP_PC_0 192
#define IP_PC_1 168
#define IP_PC_2 1
#define IP_PC_3 100

// IP fija (el diagrama del enunciado dice "IP estatica"). 0 = la da el router por DHCP (funciona en
// cualquier red sin tocar nada mas); 1 = la de la tabla de abajo.
// Las IP fijas TIENEN que estar en la subred del WiFi real (la misma que la del PC: si el PC es la
// 192.168.1.100 con mascara 255.255.255.0, la placa tiene que ser 192.168.1.algo). Si se le pone,
// por ejemplo, la 192.168.10.31 de la VLAN 1 de Docker, la placa cree que el PC esta "en otra red",
// le manda todo a la puerta de enlace (el router WiFi), y el router no sabe nada de las redes de
// Docker: no llega ni un paquete. Ademas conviene que queden FUERA del rango que reparte el DHCP
// del router (muchos reparten .2-.199 o .100-.200), para que otro equipo no reciba la misma.
//
//   Rol          ultimo octeto sugerido     (con la red 192.168.1.0/24 del ejemplo)
//   ctrl-1       201                        192.168.1.201
//   ctrl-2       202                        192.168.1.202
//   ctrl-3       203                        192.168.1.203
//   ctrl-spot    211                        192.168.1.211
//   ctrl-pepper  212                        192.168.1.212
//   ctrl-nao     213                        192.168.1.213
//   esclava      220                        192.168.1.220  (ver esp32_esclava_leds/config.h)
#define USAR_IP_FIJA 0
#define IP_RED_0 192          // los tres primeros octetos de la red WiFi
#define IP_RED_1 168
#define IP_RED_2 1
#define IP_PUERTA_ULTIMO 1    // el router WiFi (puerta de enlace), casi siempre la .1
#if ROL == CTRL_1
  #define IP_FIJA_ULTIMO 201
#elif ROL == CTRL_2
  #define IP_FIJA_ULTIMO 202
#elif ROL == CTRL_3
  #define IP_FIJA_ULTIMO 203
#elif ROL == CTRL_SPOT
  #define IP_FIJA_ULTIMO 211
#elif ROL == CTRL_PEPPER
  #define IP_FIJA_ULTIMO 212
#else
  #define IP_FIJA_ULTIMO 213
#endif

#define PUERTO_ADMIN      5300   // HB y PING del admin (UDP 5300 publicado en el PC)
#define PUERTO_LOCAL      5400   // puerto propio de la placa: de aqui salen TODOS los datagramas y
                                 // aqui vuelve el PONG (el admin contesta a la direccion y puerto
                                 // de origen del PING, asi que hay que mandar y escuchar con el
                                 // mismo socket)
#define REINTENTO_WIFI_MS 5000   // si sigue sin WiFi tanto tiempo, se vuelve a llamar a begin()

// =============================================================================================
// Tiempos (contrato del tema)
// =============================================================================================
#define PERIODO_CONTROL_MS   50     // CTRL / JOINTS a 20 Hz
#define PERIODO_HB_MS        1000   // HB al admin cada 1 s
#define PERIODO_PING_MS      2000   // PING al admin cada 2 s (el PONG da el RTT)
#define PERIODO_ENVIADO_MS   200    // linea ENVIADO por serie a 5 Hz (no los 20 Hz: saturaria el monitor)
#define PERIODO_ESTADO_MS    2000   // linea ESTADO por serie cada 2 s
#define MANUAL_DURACION_MS   2000   // un MANUAL por serie manda durante 2 s (el preview lo repite)
#define PONG_RECIENTE_MS     5000   // sin PONG en 5 s, el LED avisa que el admin no contesta

// =============================================================================================
// Pines (ver la tabla y el porque de cada uno en el README, seccion Conexiones)
// =============================================================================================
// Las tres entradas analogicas estan en el ADC1 (GPIO 32-39). El ADC2 (GPIO 0, 2, 4, 12-15, 25-27)
// lo usa el driver de WiFi para su calibracion de radio: con el WiFi encendido, analogRead() en un
// pin del ADC2 falla o devuelve basura. Como estas placas viven conectadas al WiFi, ADC1 si o si.
// GPIO 34, 35 y 39 ademas son solo de entrada (no tienen salida ni pull-up interno), que es justo lo
// que necesita un potenciometro: no se pueden quemar por configurarlos mal como salida.
#define PIN_A      34   // gamer: VRx del joystick (direccion)  | robot: potenciometro 1 (j1)
#define PIN_B      35   // gamer: VRy del joystick (velocidad)  | robot: potenciometro 2 (j2)
#define PIN_C      39   // robot: potenciometro 3 (j3, opcional) | gamer: no se usa
#define PIN_BOTON  32   // SW del joystick o pulsador a GND. GPIO 32 si tiene pull-up interno (34-39
                        // no), asi que no hace falta resistencia externa: suelto = 1, apretado = 0
#define PIN_LED    2    // LED azul de la placa (estado del WiFi). Es pin de arranque: el LED con su
                        // resistencia a GND lo deja en bajo al reset, que es lo que necesita

// 1 = hay un tercer potenciometro en GPIO 39. Con 0, j3 vale 0 siempre (salvo MANUAL o MODO_DEMO).
// Un pin analogico al aire no lee 0: lee ruido que salta por toda la escala; por eso, si un robot
// se arma con solo dos potenciometros, hay que poner 0 aqui o j3 se moveria solo. Por defecto:
// robots = 1 (montaje completo de tres pots), gamer = 0 (el joystick solo tiene dos ejes).
#ifndef CANAL_C_CONECTADO
  #if ES_GAMER
    #define CANAL_C_CONECTADO 0
  #else
    #define CANAL_C_CONECTADO 1
  #endif
#endif

// =============================================================================================
// Lectura de las entradas
// =============================================================================================
// MODO_DEMO 1 = no lee el ADC ni el pulsador: genera entradas senoidales lentas (y aprieta el boton
// medio segundo cada 6 s). Sirve para probar toda la cadena (WiFi -> Docker -> simulacion -> admin)
// con una placa pelada, sin nada cableado. El resto del programa no cambia.
#ifndef MODO_DEMO
#define MODO_DEMO 0
#endif

#define PERIODO_MUESTREO_MS  5      // se lee el ADC a 200 Hz y se filtra; se manda a 20 Hz
#define FILTRO_ALFA          0.25f  // filtro exponencial: y += alfa * (x - y). Con 200 Hz y 0,25 la
                                    // constante de tiempo es ~17 ms: quita el ruido del ADC (salta
                                    // +-20 cuentas) sin que se note retraso al mover el joystick
#define MUESTRAS_CALIBRACION 64     // lecturas promediadas para hallar el centro al arrancar
#define ZONA_MUERTA          8      // % alrededor del centro que cuenta como 0 (el joystick no
                                    // vuelve exacto al centro: sin esto el carro se arrastra solo)

// Calibrar el centro al arrancar: 1 tiene sentido con un joystick (vuelve solo al centro por
// resorte, asi que lo que se lee al encender ES el centro). Con potenciometros no: un pot se queda
// donde se dejo, y su "centro" es la mitad de la escala. Por defecto: gamer (joystick) = 1,
// robots (potenciometros) = 0. Si un robot se maneja con el joystick, poner 1.
#ifndef CALIBRAR_CENTRO
  #if ES_GAMER
    #define CALIBRAR_CENTRO 1
  #else
    #define CALIBRAR_CENTRO 0
  #endif
#endif

// 1 = invierte el sentido de un eje (segun como quede montado el joystick en la protoboard).
#define INVERTIR_A 0
#define INVERTIR_B 1   // en el KY-023 con los pines hacia abajo, empujar "hacia adelante" (lejos de
                       // los pines) BAJA el voltaje de VRy: se invierte para que adelante = vel
                       // positiva. Si el carro va al reves, poner 0 (depende de como se monte)
#define INVERTIR_C 0

#define ANTIRREBOTE_MS 30   // el pulsador tiene que estar estable 30 ms para aceptar el cambio

// =============================================================================================
// Mapeo a grados (solo robots). Cada entrada normalizada (-100..100) se lleva linealmente a
// [MIN, MAX] grados. El rango por defecto depende del robot y coincide con lo que el simulador
// acepta (zona-robotica/robots.py), para que la carrera completa del potenciometro sirva:
//   Spot:        j1 altura, j2 cabeceo, j3 alabeo -> -20..20 los tres (0,2 grados por unidad).
//                Con -90..90 el robot igual recortaria a +-20, pero solo serviria la quinta
//                parte del giro del potenciometro.
//   NAO/Pepper:  j1 hombro -90..90, j2 codo 0..88 (el codo solo dobla hacia un lado: valores
//                negativos se recortarian a 0), j3 cabeza -90..90.
// Que articulacion mueve cada j lo decide el contenedor del robot; aqui solo se fija el rango.
// =============================================================================================
#if ROL == CTRL_SPOT
#define J_DEF1_MIN -20.0f
#define J_DEF1_MAX  20.0f
#define J_DEF2_MIN -20.0f
#define J_DEF2_MAX  20.0f
#define J_DEF3_MIN -20.0f
#define J_DEF3_MAX  20.0f
#else
#define J_DEF1_MIN -90.0f
#define J_DEF1_MAX  90.0f
#define J_DEF2_MIN   0.0f
#define J_DEF2_MAX  88.0f
#define J_DEF3_MIN -90.0f
#define J_DEF3_MAX  90.0f
#endif
#ifndef J1_MIN
#define J1_MIN J_DEF1_MIN
#endif
#ifndef J1_MAX
#define J1_MAX J_DEF1_MAX
#endif
#ifndef J2_MIN
#define J2_MIN J_DEF2_MIN
#endif
#ifndef J2_MAX
#define J2_MAX J_DEF2_MAX
#endif
#ifndef J3_MIN
#define J3_MIN J_DEF3_MIN
#endif
#ifndef J3_MAX
#define J3_MAX J_DEF3_MAX
#endif
