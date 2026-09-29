/* ESP32-B: esclavo SPI por hardware (ESP-IDF del core Arduino ESP32 3.x).
   Muestra en la SSD1306 I2C el digito verificado de ESP32-A.
   Cargar desde Arduino IDE o arduino-cli; MicroPython machine.SPI no
   implementa modo esclavo en ESP32. OLED: SDA21/SCL22, 3V3, GND.
*/

#include <Arduino.h>
#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include "driver/spi_slave.h"
#include "driver/gpio.h"

constexpr uint8_t PIN_SCK = 18;
constexpr uint8_t PIN_MOSI = 23;
constexpr uint8_t PIN_MISO = 19;
constexpr uint8_t PIN_CS = 5;
constexpr gpio_num_t PIN_READY = GPIO_NUM_27;
constexpr size_t FRAME_BYTES = 12;

Adafruit_SSD1306 oled(128, 64, &Wire, -1);
bool oled_ready = false;
uint8_t rx_buffer[FRAME_BYTES] = {};
uint8_t tx_buffer[FRAME_BYTES] = {};
uint8_t last_ack[FRAME_BYTES] = {};
int previous_sequence = -1;

static uint8_t check_xor(const uint8_t *data, size_t count) {
  uint8_t value = 0;
  for (size_t i = 0; i < count; ++i) value ^= data[i];
  return value;
}

// El READY pasa a uno SOLAMENTE cuando el controlador ya preparo el slave.
void IRAM_ATTR post_setup(spi_slave_transaction_t *) {
  gpio_set_level(PIN_READY, 1);
}

void IRAM_ATTR post_trans(spi_slave_transaction_t *) {
  gpio_set_level(PIN_READY, 0);
}

void set_ack(uint16_t seq, uint8_t digit, uint8_t status) {
  memset(last_ack, 0, FRAME_BYTES);
  last_ack[0] = 0xAC;
  last_ack[1] = seq >> 8;
  last_ack[2] = seq & 255;
  last_ack[3] = digit;
  last_ack[4] = status;     // 0 OK, 1 SPI error, 2 OLED error.
  last_ack[5] = check_xor(last_ack, 5);
}

void show_digit(char value, char source) {
  if (!oled_ready) return;
  oled.clearDisplay();
  oled.setTextColor(SSD1306_WHITE);
  oled.setTextSize(1);
  oled.setCursor(0, 0);
  oled.println(F("ESP32-B / SPI + I2C"));
  oled.setCursor(0, 55);
  oled.print(F("Camara: "));
  oled.print(source == 'V' ? F("CNN") : F("PRUEBA"));
  oled.setTextSize(5);
  oled.setCursor(47, 16);
  oled.print(value);
  oled.display();
}

void setup() {
  Serial.begin(115200);
  pinMode(static_cast<uint8_t>(PIN_READY), OUTPUT);
  gpio_set_level(PIN_READY, 0);
  Wire.begin(21, 22);
  const uint8_t oled_addresses[] = {0x3C, 0x3D};
  for (uint8_t address : oled_addresses) {
    Wire.beginTransmission(address);
    if (Wire.endTransmission() == 0) {
      oled_ready = oled.begin(SSD1306_SWITCHCAPVCC, address);
      break;
    }
  }
  if (oled_ready) show_digit('-', 'T');
  else Serial.println("OLED no encontrada en 0x3C/0x3D");

  spi_bus_config_t bus = {};
  bus.mosi_io_num = PIN_MOSI;
  bus.miso_io_num = PIN_MISO;
  bus.sclk_io_num = PIN_SCK;
  bus.quadwp_io_num = -1;
  bus.quadhd_io_num = -1;
  bus.max_transfer_sz = FRAME_BYTES;

  spi_slave_interface_config_t config = {};
  config.spics_io_num = PIN_CS;
  config.flags = 0;
  config.queue_size = 1;
  config.mode = 0;
  config.post_setup_cb = post_setup;
  config.post_trans_cb = post_trans;

  esp_err_t result = spi_slave_initialize(SPI3_HOST, &bus, &config, SPI_DMA_DISABLED);
  if (result != ESP_OK) {
    Serial.printf("No se pudo iniciar SPI slave: %d\n", result);
    while (true) delay(1000);
  }
  Serial.println("ESP32-B lista: SPI esclavo + OLED I2C");
}

void loop() {
  memcpy(tx_buffer, last_ack, FRAME_BYTES);
  memset(rx_buffer, 0, FRAME_BYTES);
  spi_slave_transaction_t transaction = {};
  transaction.length = FRAME_BYTES * 8;
  transaction.tx_buffer = tx_buffer;
  transaction.rx_buffer = rx_buffer;

  // Bloquea hasta que ESP32-A envia exactamente 12 bytes. Sin DMA.
  esp_err_t result = spi_slave_transmit(SPI3_HOST, &transaction, portMAX_DELAY);
  if (result != ESP_OK || transaction.trans_len != FRAME_BYTES * 8) return;
  if (rx_buffer[0] == 0x5A) return; // POLL: el ACK ya salio por MISO.
  if (rx_buffer[0] != 0xA5) return;

  const uint16_t seq = (static_cast<uint16_t>(rx_buffer[1]) << 8) | rx_buffer[2];
  const uint8_t digit = rx_buffer[3];
  const uint8_t source = rx_buffer[4];
  if (rx_buffer[5] != check_xor(rx_buffer, 5) ||
      digit < '0' || digit > '9' || (source != 'V' && source != 'T')) {
    set_ack(seq, digit, 1);
    Serial.println("Paquete SPI corrupto");
    return;
  }
  if (previous_sequence != static_cast<int>(seq)) {
    show_digit(static_cast<char>(digit), static_cast<char>(source));
    previous_sequence = seq;
    Serial.printf("SPI recibido: secuencia=%u, digito=%c\n", seq, digit);
  }
  set_ack(seq, digit, oled_ready ? 0 : 2);
}
