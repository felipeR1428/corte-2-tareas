// esp32_esclava_leds.ino - Panel fisico de estado del laboratorio: seis LEDs, uno por contenedor de
// las zonas (player-1, player-2, player-3, sim-spot, sim-pepper, sim-nao), que siguen lo que publica
// el admin por MQTT.
//
// Papel maestro-esclavo: esta placa NO mide ni decide nada. Quien decide si un servicio esta OK,
// LENTO o CAIDO es el admin (plano de administracion, VLAN 3): junta los latidos HB de cada
// contenedor, calcula latencia, jitter y disponibilidad, y publica el veredicto retenido en
// lab/estado/<servicio>. La esclava solo se suscribe a esos seis topicos y obedece: pinta en cada
// LED lo que diga el ultimo mensaje. Si manana cambia el criterio de "LENTO" (otro umbral de
// latencia, por ejemplo), se cambia solo en el admin y la placa no se toca. Es la misma idea que
// las maestras de las zonas pero al reves: aquellas MANDAN datos y no escuchan ordenes; esta
// RECIBE ordenes y no manda datos (salvo su propio latido y su "estoy viva").
//
//   LED encendido fijo      OK      (el servicio late a tiempo)
//   parpadeo a 2 Hz         LENTO   (late, pero con latencia o jitter por encima del umbral)
//   apagado                 CAIDO   (el admin dejo de recibir sus latidos)
//   destello corto cada 1 s ?       (todavia no llego ningun estado de ese servicio, o el texto
//                                    no es ninguno de los tres)
//   una luz que recorre los seis LEDs de un lado a otro (vaiven): SIN BROKER hace mas de 5 s.
//     Se eligio un patron en MOVIMIENTO a proposito: cada estado de servicio es un LED quieto,
//     prendido, apagado o parpadeando EN SU LUGAR y todos al mismo ritmo; ninguna combinacion de
//     estados produce una luz que se desplaza. Asi nadie confunde "no se nada" con "todo caido"
//     (todo apagado) ni con "todo bien" (todo prendido), que es justo el error peligroso de un
//     panel de monitoreo: mostrar como vigente un estado viejo. Los primeros 5 s sin broker se
//     sigue mostrando lo ultimo que se supo (un corte cortito del WiFi no debe borrar el panel).
//   LED azul de la placa (GPIO 2): fijo = conectada al broker; parpadeo lento (1 Hz) = hay WiFi
//     pero no broker; parpadeo rapido (5 Hz) = sin WiFi.
//
// Ademas publica lab/vivo/esclava = 1 (retenido) al conectar, con testamento (LWT) = 0: si la placa
// se desenchufa, el broker publica el 0 solo, y el admin sabe que el panel fisico no esta. Y manda
// HB,esclava,<seq>,<t_ms> por UDP al admin cada 1 s, como cualquier servicio del laboratorio.
//
// Serie (115200):
//   LEDS,<player-1>,<player-2>,<player-3>,<sim-spot>,<sim-pepper>,<sim-nao>   (OK/LENTO/CAIDO/?)
//       al cambiar algo y cada 2 s
//   ESTADO,esclava,<ip>,<rssi>,<mqtt ok 0/1>   cada 2 s
//
// Compilar: arduino-cli compile -b esp32:esp32:esp32 firmware/esp32_esclava_leds
// (necesita la libreria PubSubClient: arduino-cli lib install PubSubClient)

#include <WiFi.h>
#include <WiFiUdp.h>
#include <PubSubClient.h>

#include "config.h"

// =============================================================================================
// Estado global
// =============================================================================================

// Estado de cada servicio tal como lo publico el admin. DESCONOCIDO = todavia nada o texto raro.
enum Estado { E_DESCONOCIDO = 0, E_OK, E_LENTO, E_CAIDO };
const char* const TEXTO_ESTADO[] = {"?", "OK", "LENTO", "CAIDO"};

const uint8_t PINES[N_SERVICIOS] = PINES_LEDS;
const char* const SERVICIO[N_SERVICIOS] = SERVICIOS;
Estado estado[N_SERVICIOS];
bool cambioPendiente = true;   // imprimir LEDS en la proxima vuelta

const IPAddress IP_PC(IP_PC_0, IP_PC_1, IP_PC_2, IP_PC_3);

WiFiClient red;               // la conexion TCP al broker
PubSubClient mqtt(red);
WiFiUDP udp;                  // solo para el HB
bool udpAbierto = false;

bool wifiAntes = false;
bool mqttAntes = false;
uint32_t tReintentoWifiMs = 0;
uint32_t tReintentoMqttMs = 0;
uint32_t tSinBrokerDesdeMs = 0;   // desde cuando NO hay broker (o desde el arranque)
bool modoSinBroker = false;       // ya pasaron SIN_BROKER_MS sin broker

uint32_t seqHb = 0;
uint32_t tHbMs = 0, tLedsMs = 0, tEstadoMs = 0;

// =============================================================================================
// Estados de los servicios
// =============================================================================================

int indiceServicio(const char* nombre) {
  for (int i = 0; i < N_SERVICIOS; i++) {
    if (strcmp(nombre, SERVICIO[i]) == 0) return i;
  }
  return -1;
}

Estado estadoDeTexto(const char* t) {
  if (strcmp(t, "OK") == 0) return E_OK;
  if (strcmp(t, "LENTO") == 0) return E_LENTO;
  if (strcmp(t, "CAIDO") == 0) return E_CAIDO;
  return E_DESCONOCIDO;
}

void ponerEstado(int i, Estado e) {
  if (estado[i] != e) {
    estado[i] = e;
    cambioPendiente = true;
  }
}

void imprimirLeds() {
  Serial.print("LEDS");
  for (int i = 0; i < N_SERVICIOS; i++) {
    Serial.print(',');
    Serial.print(TEXTO_ESTADO[estado[i]]);
  }
  Serial.println();
}

// Llega un mensaje de un topico suscrito. PubSubClient entrega el payload SIN '\0' al final, asi
// que se copia a un buffer propio (y se recortan espacios y saltos de linea por si el admin manda
// "OK\n").
void alRecibir(char* topico, byte* payload, unsigned int largo) {
  const char* prefijo = "lab/estado/";
  size_t lp = strlen(prefijo);
  if (strncmp(topico, prefijo, lp) != 0) return;
  int i = indiceServicio(topico + lp);
  if (i < 0) return;   // un servicio que no tiene LED (track-server, por ejemplo)
  char texto[16];
  unsigned int n = largo < sizeof(texto) - 1 ? largo : sizeof(texto) - 1;
  memcpy(texto, payload, n);
  texto[n] = '\0';
  while (n > 0 && (texto[n - 1] == ' ' || texto[n - 1] == '\n' || texto[n - 1] == '\r')) texto[--n] = '\0';
  ponerEstado(i, estadoDeTexto(texto));
}

// =============================================================================================
// WiFi y MQTT
// =============================================================================================

bool wifiArriba() { return WiFi.status() == WL_CONNECTED; }

void configurarIpFija() {
#if USAR_IP_FIJA
  IPAddress ip(IP_RED_0, IP_RED_1, IP_RED_2, IP_FIJA_ULTIMO);
  IPAddress puerta(IP_RED_0, IP_RED_1, IP_RED_2, IP_PUERTA_ULTIMO);
  WiFi.config(ip, puerta, IPAddress(255, 255, 255, 0), puerta);
#endif
}

void iniciarWifi() {
  WiFi.persistent(false);
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);       // sin ahorro de energia: los mensajes MQTT llegan sin esperar al beacon
  WiFi.setAutoReconnect(true);
  configurarIpFija();
  WiFi.begin(WIFI_SSID, WIFI_CLAVE);   // no bloquea
  tReintentoWifiMs = millis();
  Serial.printf("LOG,conectando a \"%s\"...\n", WIFI_SSID);
}

void gestionarWifi(uint32_t ahora) {
  bool arriba = wifiArriba();
  if (arriba && !wifiAntes) {
    Serial.printf("LOG,WiFi conectado: IP %s, RSSI %d dBm, broker %s:%d\n",
                  WiFi.localIP().toString().c_str(), (int)WiFi.RSSI(), IP_PC.toString().c_str(),
                  PUERTO_MQTT);
    udp.stop();
    udpAbierto = udp.begin(PUERTO_LOCAL) == 1;
    tReintentoMqttMs = ahora - REINTENTO_MQTT_MS;   // intentar el broker ya, sin esperar
  } else if (!arriba && wifiAntes) {
    Serial.println("LOG,WiFi perdido; reintentando sin detener el programa");
    udp.stop();
    udpAbierto = false;
    // Se cierra el socket TCP del broker a mano: sin WiFi, el socket puede seguir "conectado" para
    // el sistema hasta que venza el timeout de TCP, y mqtt.connected() diria que si durante ese
    // rato (el contador de "sin broker" no arrancaria). stop() no manda DISCONNECT, asi que el
    // broker, cuando note el corte, publica el LWT como corresponde.
    red.stop();
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

// Intenta conectar al broker. Es la UNICA parte que puede frenar el loop un rato: PubSubClient
// abre la conexion TCP y espera el CONNACK de forma bloqueante. Se acota asi:
//   - el connect() TCP tiene un tope de TIMEOUT_TCP_MS (si el PC no existe en esa IP);
//   - si el PC existe pero Docker no publica el 1883, Windows contesta RST al instante;
//   - la espera del CONNACK tiene un tope de 1 s (setSocketTimeout);
//   - y solo se intenta cada REINTENTO_MQTT_MS.
// En el peor caso el loop se frena menos de 2 s cada 2 s MIENTRAS NO HAY BROKER (cuando ya se esta
// mostrando el patron "sin broker", que solo se ve un poco entrecortado). Con broker no frena nunca.
void conectarMqtt() {
  Serial.printf("LOG,conectando al broker %s:%d...\n", IP_PC.toString().c_str(), PUERTO_MQTT);
  // connect(id, usuario, clave, topicoLWT, qosLWT, retenerLWT, mensajeLWT, sesionLimpia)
  bool ok = mqtt.connect(CLIENT_ID_MQTT, nullptr, nullptr, "lab/vivo/" NOMBRE_SERVICIO, 1, true, "0",
                         true);
  if (!ok) {
    Serial.printf("LOG,broker no disponible (estado %d)\n", mqtt.state());
    return;
  }
  mqtt.publish("lab/vivo/" NOMBRE_SERVICIO, "1", true);   // retenido: "estoy viva"
  // Una suscripcion por servicio (y no lab/estado/#) para recibir SOLO los seis que tienen LED.
  // Como el admin publica los estados RETENIDOS, el broker manda el ultimo de cada uno apenas nos
  // suscribimos: el panel se pone al dia al instante despues de un arranque o una reconexion.
  char topico[40];
  for (int i = 0; i < N_SERVICIOS; i++) {
    snprintf(topico, sizeof(topico), "lab/estado/%s", SERVICIO[i]);
    mqtt.subscribe(topico, 1);
  }
  Serial.println("LOG,conectada al broker y suscrita a lab/estado/<servicio> x6");
}

void gestionarMqtt(uint32_t ahora) {
  if (wifiArriba()) {
    if (mqtt.connected()) {
      mqtt.loop();   // atiende lo que llegue (llama a alRecibir) y manda el PINGREQ del keepalive
    } else if ((uint32_t)(ahora - tReintentoMqttMs) >= REINTENTO_MQTT_MS) {
      tReintentoMqttMs = ahora;
      conectarMqtt();
      ahora = millis();   // conectarMqtt pudo tardar: el resto usa la hora nueva
    }
  }
  bool conectado = mqtt.connected();
  if (conectado && !mqttAntes) {
    modoSinBroker = false;
    cambioPendiente = true;
  } else if (!conectado && mqttAntes) {
    Serial.println("LOG,se perdio el broker");
    tSinBrokerDesdeMs = ahora;
  }
  mqttAntes = conectado;
  // Sin broker mas de SIN_BROKER_MS: lo que se sabia ya no es confiable. Se marcan los seis como
  // desconocidos (asi la linea LEDS tambien lo dice) y los LEDs pasan al patron de vaiven. Al
  // volver el broker, los mensajes retenidos reponen el estado real de cada uno.
  if (!conectado && !modoSinBroker && (uint32_t)(ahora - tSinBrokerDesdeMs) >= SIN_BROKER_MS) {
    modoSinBroker = true;
    for (int i = 0; i < N_SERVICIOS; i++) ponerEstado(i, E_DESCONOCIDO);
    Serial.println("LOG,sin broker hace mas de 5 s: patron de vaiven en los LEDs");
  }
}

void enviarHb(uint32_t ahora) {
  if (!udpAbierto || !wifiArriba()) {
    seqHb++;   // igual avanza: el admin contara como perdidos los que no salieron
    return;
  }
  char linea[48];
  int n = snprintf(linea, sizeof(linea), "HB,%s,%lu,%lu", NOMBRE_SERVICIO, (unsigned long)seqHb,
                   (unsigned long)ahora);
  seqHb++;
  if (n <= 0 || n >= (int)sizeof(linea)) return;
  if (udp.beginPacket(IP_PC, PUERTO_ADMIN)) {
    udp.write((const uint8_t*)linea, (size_t)n);
    udp.endPacket();
  }
  // Se descarta lo que llegue al puerto UDP (nadie deberia mandarle nada; asi no se llena el buffer).
  // (clear() y no flush(): en el nucleo 3.x flush() quedo obsoleto para esto).
  while (udp.parsePacket() > 0) udp.clear();
}

void imprimirEstado() {
  bool ok = wifiArriba();
  Serial.printf("ESTADO,%s,%s,%d,%d\n", NOMBRE_SERVICIO,
                ok ? WiFi.localIP().toString().c_str() : "0.0.0.0", ok ? (int)WiFi.RSSI() : 0,
                mqtt.connected() ? 1 : 0);
}

// =============================================================================================
// LEDs
// =============================================================================================

// Todos los patrones salen de millis(): no hay temporizadores ni delay, y todos los LEDs con el
// mismo estado parpadean sincronizados (se ve mas claro que cada uno a su ritmo).
void actualizarLeds(uint32_t ahora) {
  if (modoSinBroker) {
    // Vaiven: posiciones 0,1,2,3,4,5,4,3,2,1,0,... a 120 ms por paso (ciclo de 10 pasos = 1,2 s).
    int paso = (ahora / 120) % (2 * (N_SERVICIOS - 1));
    int pos = paso < N_SERVICIOS ? paso : 2 * (N_SERVICIOS - 1) - paso;
    for (int i = 0; i < N_SERVICIOS; i++) digitalWrite(PINES[i], i == pos ? HIGH : LOW);
  } else {
    bool fase2Hz = (ahora % 500) < 250;    // 250 ms prendido, 250 ms apagado = 2 Hz
    bool destello = (ahora % 1000) < 60;   // 60 ms cada segundo
    for (int i = 0; i < N_SERVICIOS; i++) {
      bool on;
      switch (estado[i]) {
        case E_OK:    on = true; break;
        case E_LENTO: on = fase2Hz; break;
        case E_CAIDO: on = false; break;
        default:      on = destello; break;
      }
      digitalWrite(PINES[i], on ? HIGH : LOW);
    }
  }
  bool placa;
  if (!wifiArriba()) placa = (ahora / 100) % 2;             // 5 Hz
  else if (!mqtt.connected()) placa = (ahora / 500) % 2;    // 1 Hz
  else placa = true;
  digitalWrite(PIN_LED_PLACA, placa ? HIGH : LOW);
}

// =============================================================================================
// setup y loop
// =============================================================================================

void setup() {
  Serial.begin(115200);
  pinMode(PIN_LED_PLACA, OUTPUT);
  for (int i = 0; i < N_SERVICIOS; i++) {
    pinMode(PINES[i], OUTPUT);
    estado[i] = E_DESCONOCIDO;
  }
  // Prueba de cableado al encender: cada LED se prende 150 ms en orden (player-1 ... sim-nao).
  // Si alguno no prende o el orden no es el del dashboard, esta mal conectado. Es lo unico que
  // espera con delay, y es en setup(), antes de que haya nada que atender.
  for (int i = 0; i < N_SERVICIOS; i++) {
    digitalWrite(PINES[i], HIGH);
    delay(150);
    digitalWrite(PINES[i], LOW);
  }
  Serial.printf("LOG,esclava de LEDs -> broker %d.%d.%d.%d:%d\n", IP_PC_0, IP_PC_1, IP_PC_2, IP_PC_3,
                PUERTO_MQTT);

  red.setConnectionTimeout(TIMEOUT_TCP_MS);
  mqtt.setServer(IP_PC, PUERTO_MQTT);
  mqtt.setCallback(alRecibir);
  mqtt.setKeepAlive(MQTT_KEEPALIVE_S);
  mqtt.setSocketTimeout(1);   // segundos de espera del CONNACK (el valor por defecto es 15)

  iniciarWifi();
  uint32_t ahora = millis();
  tSinBrokerDesdeMs = ahora;   // al arrancar tampoco hay broker: a los 5 s, patron de vaiven
  tHbMs = tLedsMs = tEstadoMs = ahora;
  tReintentoMqttMs = ahora;
}

void loop() {
  uint32_t ahora = millis();
  gestionarWifi(ahora);
  gestionarMqtt(ahora);
  ahora = millis();

  if ((uint32_t)(ahora - tHbMs) >= PERIODO_HB_MS) {
    tHbMs += PERIODO_HB_MS;
    if ((uint32_t)(ahora - tHbMs) >= PERIODO_HB_MS) tHbMs = ahora;   // atrasado: resincronizar
    enviarHb(ahora);
  }
  if (cambioPendiente || (uint32_t)(ahora - tLedsMs) >= PERIODO_LEDS_MS) {
    cambioPendiente = false;
    tLedsMs = ahora;
    imprimirLeds();
  }
  if ((uint32_t)(ahora - tEstadoMs) >= PERIODO_ESTADO_MS) {
    tEstadoMs = ahora;
    imprimirEstado();
  }
  actualizarLeds(ahora);
}
