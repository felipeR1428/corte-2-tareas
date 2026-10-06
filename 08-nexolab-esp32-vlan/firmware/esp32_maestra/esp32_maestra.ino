// esp32_maestra.ino - Mando fisico de una zona: lee un joystick KY-023 o potenciometros y manda el
// control por WiFi/UDP al contenedor que le toca, a 20 Hz.
//
// El MISMO sketch va en las seis maestras; solo cambia ROL (ver config.h):
//   - CTRL_1, CTRL_2, CTRL_3 (zona gamer, VLAN 1): joystick -> CTRL,<id>,<seq>,<t_ms>,<dir>,<vel>,<boton>
//     al player-N (UDP 5001/5002/5003 del PC), que se lo pasa al servidor de la pista.
//   - CTRL_SPOT, CTRL_PEPPER, CTRL_NAO (zona robotica, VLAN 2): potenciometros -> JOINTS,<robot>,
//     <seq>,<t_ms>,<j1>,<j2>,<j3>,<boton> en grados al sim-<robot> (UDP 5101/5102/5103 del PC):
//     es el "real-to-sim" del enunciado, la mano mueve una perilla real y el robot simulado copia el
//     angulo.
//
// Por que a la IP del PC y no a la del contenedor: las VLAN (192.168.10/20/30.0/24) son redes de
// Docker que existen solo dentro del PC. La placa esta en la red WiFi real; lo unico que ve de ese
// mundo es el PC, y Docker reenvia cada puerto publicado del PC al contenedor (5001 -> player-1:5000,
// etc.). Para el contenedor el paquete llega como si viniera de la red de Docker.
//
// Ademas, como cualquier servicio del laboratorio, le manda al admin (plano de administracion, VLAN
// 3) un latido HB cada 1 s y un PING cada 2 s; el admin contesta el PING con un PONG y la placa mide
// la latencia ida y vuelta (RTT) con su propio reloj. El admin usa los HB para la disponibilidad y el
// jitter de cada mando.
//
// Por el puerto serie (115200) imprime, para depurar y para el preview.html en modo conectado:
//   ENVIADO,<el datagrama>   a 5 Hz (uno de cada cuatro envios)
//   RTT,<ms>                 cada vez que vuelve un PONG
//   ESTADO,<rol>,<ip>,<rssi>,<wifi 0/1>   cada 2 s
// y acepta MANUAL,<a>,<b>,<c>,<boton> (a,b,c en -100..100; durante 2 s reemplazan al ADC: asi el
// preview hace de joystick si no hay uno cableado) y MANUAL,OFF.
//
// Todo es NO bloqueante: loop() da vueltas de microsegundos y cada tarea (WiFi, muestreo del ADC,
// envio, latidos, serie, LED) mira millis() y hace su parte cuando le toca. No hay delay() en el
// loop: si lo hubiera, mientras se espera no se leeria el joystick ni el PONG, y el RTT medido
// incluiria esa espera.
//
// Compilar (arduino-cli, nucleo esp32 3.x; placa "ESP32 Dev Module"):
//   arduino-cli compile -b esp32:esp32:esp32
//     --build-property "compiler.cpp.extra_flags=-DROL=CTRL_SPOT" firmware/esp32_maestra
//   (todo en una sola linea; firmware/compilar.ps1 compila los seis roles y la esclava de una vez)
// o desde el Arduino IDE cambiando ROL en config.h antes de subir a cada placa.

#include <WiFi.h>
#include <WiFiUdp.h>
#include <math.h>

#include "config.h"
#include "logica.h"   // normalizar, zona muerta, grados, armar/leer mensajes (C++ puro)

// =============================================================================================
// Estado global (todo antes de la primera funcion: el preprocesador del .ino genera prototipos
// arriba del primer cuerpo de funcion y esos prototipos necesitan ver los tipos)
// =============================================================================================

WiFiUDP udp;                 // UN solo socket: manda CTRL/JOINTS, HB y PING, y recibe el PONG
bool udpAbierto = false;     // se abre al conectar y se reabre en cada reconexion
bool wifiAntes = false;      // para detectar el flanco conectado/desconectado
uint32_t tReintentoWifiMs = 0;

// Entradas filtradas (cuentas del ADC, 0..4095) y centros calibrados.
float filtA = 2048, filtB = 2048, filtC = 2048;
float centroA = 2048, centroB = 2048, centroC = 2048;

// Pulsador con antirrebote.
int botonCrudoAnterior = 1;   // pull-up: 1 = suelto
int botonEstable = 0;         // 1 = apretado (ya invertido y sin rebotes)
uint32_t tCambioBotonMs = 0;

// Valores que se mandan (-100..100 y 0/1), ya con zona muerta.
int valA = 0, valB = 0, valC = 0, valBoton = 0;

// MANUAL por serie.
bool manualActivo = false;
uint32_t tManualMs = 0;
int manA = 0, manB = 0, manC = 0, manBoton = 0;
char lineaSerie[64];
size_t largoSerie = 0;

// Contadores de secuencia (uno por tipo de mensaje: el receptor cuenta perdidas por cada uno).
uint32_t seqControl = 0, seqHb = 0, seqPing = 0;

// Tiempos de la ultima vez que se hizo cada tarea.
uint32_t tMuestreoMs = 0, tControlMs = 0, tHbMs = 0, tPingMs = 0, tEstadoMs = 0;
uint32_t enviosDesdeEco = 0;  // para imprimir ENVIADO solo uno de cada N envios (5 Hz)
uint32_t tUltimoPongMs = 0;
bool hayPong = false;

const IPAddress IP_PC(IP_PC_0, IP_PC_1, IP_PC_2, IP_PC_3);

// =============================================================================================
// WiFi
// =============================================================================================

bool wifiArriba() { return WiFi.status() == WL_CONNECTED; }

void configurarIpFija() {
#if USAR_IP_FIJA
  // WiFi.config ANTES de begin: asi no se pide DHCP y la IP es siempre la misma. El DNS no se usa
  // (todo va por IP), pero se pone la puerta de enlace por si acaso.
  IPAddress ip(IP_RED_0, IP_RED_1, IP_RED_2, IP_FIJA_ULTIMO);
  IPAddress puerta(IP_RED_0, IP_RED_1, IP_RED_2, IP_PUERTA_ULTIMO);
  WiFi.config(ip, puerta, IPAddress(255, 255, 255, 0), puerta);
#endif
}

void iniciarWifi() {
  WiFi.persistent(false);     // no escribir la configuracion en la flash en cada arranque
  WiFi.mode(WIFI_STA);        // estacion: se conecta a la red del PC (no arma una red propia)
  WiFi.setSleep(false);       // sin ahorro de energia: con el modem dormido cada paquete puede
                              // esperar hasta un intervalo de beacon (~100 ms) y el RTT y el jitter
                              // medidos serian los del ahorro de energia, no los de la red
  WiFi.setAutoReconnect(true);
  configurarIpFija();
  WiFi.begin(WIFI_SSID, WIFI_CLAVE);   // no bloquea: la conexion sigue sola en segundo plano
  tReintentoWifiMs = millis();
  Serial.printf("LOG,conectando a \"%s\"...\n", WIFI_SSID);
}

// Se llama en cada vuelta. Detecta cuando la red sube o se cae y, si lleva REINTENTO_WIFI_MS sin
// red, vuelve a llamar a begin() (el autoreconnect del nucleo a veces se queda quieto si el router
// se reinicio). Nunca espera: begin() y disconnect() solo dan la orden y vuelven.
void gestionarWifi(uint32_t ahora) {
  bool arriba = wifiArriba();
  if (arriba && !wifiAntes) {
    Serial.printf("LOG,WiFi conectado: IP %s, RSSI %d dBm, PC %s\n",
                  WiFi.localIP().toString().c_str(), (int)WiFi.RSSI(), IP_PC.toString().c_str());
    udp.stop();
    udpAbierto = udp.begin(PUERTO_LOCAL) == 1;
    if (!udpAbierto) Serial.println("LOG,no pude abrir el puerto UDP local");
  } else if (!arriba && wifiAntes) {
    Serial.println("LOG,WiFi perdido; reintentando sin detener el programa");
    udp.stop();
    udpAbierto = false;
    tReintentoWifiMs = ahora;
  }
  wifiAntes = arriba;
  if (!arriba && (uint32_t)(ahora - tReintentoWifiMs) >= REINTENTO_WIFI_MS) {
    tReintentoWifiMs = ahora;
    WiFi.disconnect();
    configurarIpFija();
    WiFi.begin(WIFI_SSID, WIFI_CLAVE);
  }
}

// Manda una linea por UDP. Si no hay red no hace nada (UDP no tiene conexion que esperar: el
// datagrama simplemente no sale; el seq igual avanzo, asi que el receptor contara esos como
// perdidos, que es lo honesto: el mando no llego).
bool enviar(uint16_t puerto, const char* linea, int largo) {
  if (!udpAbierto || !wifiArriba() || largo <= 0) return false;
  if (!udp.beginPacket(IP_PC, puerto)) return false;
  udp.write((const uint8_t*)linea, (size_t)largo);
  return udp.endPacket() == 1;
}

// =============================================================================================
// Entradas: ADC, pulsador, modo demo y MANUAL
// =============================================================================================

void calibrarCentro() {
  // Se promedian MUESTRAS_CALIBRACION lecturas de cada eje con el joystick suelto (el resorte lo
  // deja en el centro). Esto si puede esperar un poco: es una sola vez, en setup(), antes de
  // empezar a mandar. 64 lecturas x ~3 canales x ~20 us = unos 4 ms.
  float sa = 0, sb = 0, sc = 0;
  for (int i = 0; i < MUESTRAS_CALIBRACION; i++) {
    sa += analogRead(PIN_A);
    sb += analogRead(PIN_B);
    sc += analogRead(PIN_C);
  }
  centroA = sa / MUESTRAS_CALIBRACION;
  centroB = sb / MUESTRAS_CALIBRACION;
  centroC = sc / MUESTRAS_CALIBRACION;
  // Un centro muy corrido (menos de 1/4 o mas de 3/4 de la escala) quiere decir que al encender se
  // estaba moviendo el joystick o que no hay nada conectado: se usa la mitad de la escala.
  if (centroA < 1024 || centroA > 3072) centroA = 2048;
  if (centroB < 1024 || centroB > 3072) centroB = 2048;
  if (centroC < 1024 || centroC > 3072) centroC = 2048;
  Serial.printf("LOG,centros ADC: A=%.0f B=%.0f C=%.0f\n", centroA, centroB, centroC);
}

// Muestrea el ADC a 200 Hz y lo pasa por el filtro exponencial: y += alfa * (x - y).
// El ADC del ESP32 tiene ruido de +-10 a 20 cuentas aun con el joystick quieto; sin filtro el
// carro "tiembla". Muestrear mas rapido de lo que se manda (200 Hz contra 20 Hz) hace que el
// filtro promedie unas 4 lecturas nuevas por envio sin agregar retraso visible.
void muestrear() {
  filtA += FILTRO_ALFA * ((float)analogRead(PIN_A) - filtA);
  filtB += FILTRO_ALFA * ((float)analogRead(PIN_B) - filtB);
#if CANAL_C_CONECTADO
  filtC += FILTRO_ALFA * ((float)analogRead(PIN_C) - filtC);
#endif
}

// Antirrebote por tiempo: un cambio del pin solo se acepta si se mantiene ANTIRREBOTE_MS. Los
// contactos de un pulsador rebotan unos milisegundos al cerrarse; sin esto un solo apreton
// llegaria como varios 0/1 seguidos.
void leerBoton(uint32_t ahora) {
  int crudo = digitalRead(PIN_BOTON);
  if (crudo != botonCrudoAnterior) {
    botonCrudoAnterior = crudo;
    tCambioBotonMs = ahora;
  } else if ((uint32_t)(ahora - tCambioBotonMs) >= ANTIRREBOTE_MS) {
    botonEstable = (crudo == LOW) ? 1 : 0;   // pull-up: apretado = LOW
  }
}

// Calcula valA..valBoton (-100..100) a partir de lo que toque: MANUAL, MODO_DEMO o el ADC.
void actualizarValores(uint32_t ahora) {
  if (manualActivo && (uint32_t)(ahora - tManualMs) >= MANUAL_DURACION_MS) {
    manualActivo = false;
    Serial.println("LOG,MANUAL vencido: vuelvo a las entradas propias");
  }
  if (manualActivo) {
    valA = manA; valB = manB; valC = manC; valBoton = manBoton;
    return;
  }
#if MODO_DEMO
  // Senos lentos con periodos distintos (8 s, 11 s, 5 s) para que los tres ejes no se muevan
  // juntos y se vea en la simulacion que cada uno llega por separado. El boton se aprieta 0,5 s
  // cada 6 s.
  // El tiempo se toma modulo 440 s (minimo comun multiplo de 8, 11 y 5 s): los tres senos valen
  // exactamente lo mismo que sin el modulo, pero el float queda chico. Con millis()/1000 directo,
  // a los pocos dias de encendida el float ya no distingue un envio del siguiente (a 4 millones de
  // segundos su paso es de 0,25 s) y los senos saldrian a escalones.
  float t = (float)(ahora % 440000UL) / 1000.0f;
  // Amplitudes 80, 60 y 70 (no 100): con el mapeo a grados de config.h quedan dentro del rango de
  // cada robot con margen (Spot +-16, +-12, +-14; NAO/Pepper hombro +-72, codo 17,6..70,4 alrededor
  // de 44, cabeza +-63), asi que el simulador nunca recorta lo que manda el demo.
  valA = (int)lroundf(80.0f * sinf(2.0f * (float)M_PI * t / 8.0f));
  valB = (int)lroundf(60.0f * sinf(2.0f * (float)M_PI * t / 11.0f));
  valC = (int)lroundf(70.0f * sinf(2.0f * (float)M_PI * t / 5.0f));
  valBoton = (ahora % 6000) < 500 ? 1 : 0;
#else
  valA = aplicarZonaMuerta(normalizar(filtA, centroA, INVERTIR_A), ZONA_MUERTA);
  valB = aplicarZonaMuerta(normalizar(filtB, centroB, INVERTIR_B), ZONA_MUERTA);
#if CANAL_C_CONECTADO
  valC = aplicarZonaMuerta(normalizar(filtC, centroC, INVERTIR_C), ZONA_MUERTA);
#else
  valC = 0;
#endif
  valBoton = botonEstable;
#endif
}

// Lee el puerto serie caracter por caracter (sin readStringUntil, que espera hasta 1 s si la linea
// no termina) y procesa cada linea completa.
void atenderSerie(uint32_t ahora) {
  while (Serial.available() > 0) {
    char ch = (char)Serial.read();
    if (ch == '\r') continue;
    if (ch != '\n') {
      if (largoSerie + 1 < sizeof(lineaSerie)) lineaSerie[largoSerie++] = ch;
      continue;   // si la linea es demasiado larga se recorta; leerManual la rechazara
    }
    lineaSerie[largoSerie] = '\0';
    largoSerie = 0;
    int a, b, c, bt;
    int r = leerManual(lineaSerie, a, b, c, bt);
    if (r == 1) {
      if (!manualActivo) Serial.println("LOG,MANUAL activo (reemplaza al ADC durante 2 s)");
      manualActivo = true;
      tManualMs = ahora;
      manA = a; manB = b; manC = c; manBoton = bt;
    } else if (r == 0) {
      manualActivo = false;
      Serial.println("LOG,MANUAL apagado");
    }
  }
}

// =============================================================================================
// Envios periodicos
// =============================================================================================

void enviarControl(uint32_t ahora) {
  char linea[96];
  int n;
#if ES_GAMER
  // Joystick: eje A = direccion (izquierda -100 .. derecha 100), eje B = velocidad (reversa -100 ..
  // adelante 100). El player los pasa tal cual al servidor de la pista.
  n = armarCtrl(linea, sizeof(linea), ID_JUGADOR, seqControl, ahora, valA, valB, valBoton);
#else
  // Robot: cada entrada se lleva a grados con el rango de config.h. El contenedor del robot decide
  // a que articulacion va cada j.
  n = armarJoints(linea, sizeof(linea), NOMBRE_ROBOT, seqControl, ahora,
                  aGrados(valA, J1_MIN, J1_MAX), aGrados(valB, J2_MIN, J2_MAX),
                  aGrados(valC, J3_MIN, J3_MAX), valBoton);
#endif
  seqControl++;
  if (n <= 0 || n >= (int)sizeof(linea)) return;
  enviar(PUERTO_DESTINO, linea, n);
  // Eco por serie a 5 Hz. Sale aunque no haya WiFi (es lo que se mandaria), para que el preview
  // muestre los valores con una placa sin red; la linea ESTADO dice si de verdad hay WiFi.
  if (++enviosDesdeEco >= PERIODO_ENVIADO_MS / PERIODO_CONTROL_MS) {
    enviosDesdeEco = 0;
    Serial.print("ENVIADO,");
    Serial.println(linea);
  }
}

void enviarLatido(const char* tipo, uint32_t& seq, uint32_t ahora) {
  char linea[48];
  int n = armarLatido(linea, sizeof(linea), tipo, ORIGEN, seq, ahora);
  seq++;
  if (n > 0 && n < (int)sizeof(linea)) enviar(PUERTO_ADMIN, linea, n);
}

// Lee TODOS los datagramas que haya (no solo uno por vuelta) y se queda con los PONG propios.
void atenderUdp(uint32_t ahora) {
  if (!udpAbierto) return;
  while (udp.parsePacket() > 0) {
    char linea[64];
    int n = udp.read(linea, sizeof(linea) - 1);
    if (n <= 0) continue;
    linea[n] = '\0';
    while (n > 0 && (linea[n - 1] == '\n' || linea[n - 1] == '\r')) linea[--n] = '\0';
    uint32_t seq, tEnvio;
    if (leerPong(linea, ORIGEN, seq, tEnvio)) {
      uint32_t rtt = ahora - tEnvio;   // aritmetica modular: sale bien aunque millis() de la vuelta
      if (rtt < 60000) {               // un PONG de antes de un reinicio daria un valor absurdo
        Serial.printf("RTT,%lu\n", (unsigned long)rtt);
        tUltimoPongMs = ahora;
        hayPong = true;
      }
    }
  }
}

void imprimirEstado() {
  bool ok = wifiArriba();
  Serial.printf("ESTADO,%s,%s,%d,%d\n", ORIGEN, ok ? WiFi.localIP().toString().c_str() : "0.0.0.0",
                ok ? (int)WiFi.RSSI() : 0, ok ? 1 : 0);
}

// LED de la placa:
//   parpadeo rapido (5 Hz)             sin WiFi (conectando o reintentando)
//   parpadeo lento (1 Hz)              con WiFi, pero el admin no contesta los PING (no hay PONG
//                                      hace 5 s: Docker apagado, IP del PC mal o firewall)
//   encendido fijo                     con WiFi y el admin contestando: todo bien
// Asi, sin monitor serie, se sabe en que paso esta el problema.
void actualizarLed(uint32_t ahora) {
  bool encendido;
  if (!wifiArriba()) {
    encendido = (ahora / 100) % 2;
  } else if (!hayPong || (uint32_t)(ahora - tUltimoPongMs) > PONG_RECIENTE_MS) {
    encendido = (ahora / 500) % 2;
  } else {
    encendido = true;
  }
  digitalWrite(PIN_LED, encendido ? HIGH : LOW);
}

// =============================================================================================
// setup y loop
// =============================================================================================

void setup() {
  Serial.begin(115200);
  pinMode(PIN_LED, OUTPUT);
  digitalWrite(PIN_LED, LOW);
  pinMode(PIN_BOTON, INPUT_PULLUP);
  // GPIO 34-39 no tienen pull-up/pull-down: se dejan como entrada simple.

  // ADC a 12 bits (0..4095) con atenuacion de 11 dB: asi el rango de entrada llega hasta ~3,1 V
  // y cubre casi todo el recorrido del joystick alimentado a 3,3 V (con 0 dB solo llegaria a ~1 V).
  analogReadResolution(12);
  analogSetPinAttenuation(PIN_A, ADC_11db);
  analogSetPinAttenuation(PIN_B, ADC_11db);
  analogSetPinAttenuation(PIN_C, ADC_11db);

  Serial.printf("LOG,maestra %s -> PC %d.%d.%d.%d puerto %d%s\n", ORIGEN, IP_PC_0, IP_PC_1, IP_PC_2,
                IP_PC_3, PUERTO_DESTINO, MODO_DEMO ? " (MODO_DEMO: entradas senoidales)" : "");
#if CALIBRAR_CENTRO && !MODO_DEMO
  calibrarCentro();
#endif
  filtA = centroA; filtB = centroB; filtC = centroC;   // el filtro arranca en el centro (sin salto)

  iniciarWifi();
  uint32_t ahora = millis();
  tMuestreoMs = tControlMs = tHbMs = tPingMs = tEstadoMs = ahora;
}

void loop() {
  uint32_t ahora = millis();

  gestionarWifi(ahora);
  atenderSerie(ahora);
  atenderUdp(ahora);

  if ((uint32_t)(ahora - tMuestreoMs) >= PERIODO_MUESTREO_MS) {
    tMuestreoMs += PERIODO_MUESTREO_MS;
#if !MODO_DEMO
    muestrear();
#endif
  }
  leerBoton(ahora);

  // Los periodos se programan contra un reloj fijo (t += periodo) y no con t = ahora: asi una vuelta
  // que tarde unos ms de mas no corre todos los envios siguientes, y el propio mando no agrega un
  // jitter que despues el admin confundiria con el de la red. Si se atraso mucho (por ejemplo, el
  // WiFi tuvo la CPU ocupada), se resincroniza en vez de mandar una rafaga para "ponerse al dia".
  if ((uint32_t)(ahora - tControlMs) >= PERIODO_CONTROL_MS) {
    tControlMs += PERIODO_CONTROL_MS;
    if ((uint32_t)(ahora - tControlMs) >= PERIODO_CONTROL_MS) tControlMs = ahora;
    actualizarValores(ahora);
    enviarControl(ahora);
  }
  if ((uint32_t)(ahora - tHbMs) >= PERIODO_HB_MS) {
    tHbMs += PERIODO_HB_MS;
    if ((uint32_t)(ahora - tHbMs) >= PERIODO_HB_MS) tHbMs = ahora;
    enviarLatido("HB", seqHb, ahora);
  }
  if ((uint32_t)(ahora - tPingMs) >= PERIODO_PING_MS) {
    tPingMs += PERIODO_PING_MS;
    if ((uint32_t)(ahora - tPingMs) >= PERIODO_PING_MS) tPingMs = ahora;
    enviarLatido("PING", seqPing, ahora);
  }
  if ((uint32_t)(ahora - tEstadoMs) >= PERIODO_ESTADO_MS) {
    tEstadoMs += PERIODO_ESTADO_MS;
    if ((uint32_t)(ahora - tEstadoMs) >= PERIODO_ESTADO_MS) tEstadoMs = ahora;
    imprimirEstado();
  }

  actualizarLed(ahora);
}
