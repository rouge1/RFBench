// Classic Bluetooth (BR/EDR) test peer for bt_capture_roleswitch.sh: discoverable, connectable,
// answers L2CAP echo (l2ping), and once a peer opens its SPP (RFCOMM) channel it streams 512-byte
// chunks to it, so the ESP32 sends multi-slot packets - after a role switch, as the master.
// The peer pauses and resumes it with one byte: 'P' stops writing, 'G' resumes (the marker pauses).
// The sketch asks for no pairing itself, but Simple Pairing is on in the ESP32 and its SPP
// server refuses an unauthenticated RFCOMM channel, so opening SPP pairs the link (Just Works: NoInputNoOutput,
// no bonding, no MITM) and encrypts it. l2ping alone stays unpaired. Seen in take 3's btsnoop, 2026-11-08.
// v2 flash backup (before v3): /media/user/4TB/data/archive/esp32/esp32_spp_sender_v2_flash_20261119.bin (SHA256SUMS beside it).
#include <Arduino.h>
#include "BluetoothSerial.h"
#include "esp_bt_device.h"
#include "esp_gap_bt_api.h"
#include "esp_bt.h"

// v3 (2026-11-19, bluey-bb copy-source capture (ii)): LED on GPIO 2 (solid while streaming SPP, slow blink when
// no client or paused) and BR/EDR TX power pinned at P9 (+9 dBm) so power control cannot turn it down.
#define LED_PIN 2

BluetoothSerial SerialBT;
static uint8_t chunk[512];
static bool paused = false;

static void printAddr() {
  const uint8_t *a = esp_bt_dev_get_address();
  Serial.printf("BD_ADDR %02X:%02X:%02X:%02X:%02X:%02X\n", a[0], a[1], a[2], a[3], a[4], a[5]);
}

// Every link starts unpaired. A bond left by an earlier session makes the ESP32 challenge the next link with
// a key the peer no longer has (daPie does not store it) and hang up with "Authentication Failure", so every
// bond is erased at boot. Only the count is printed.
static void eraseBonds() {
  int n = esp_bt_gap_get_bond_device_num();
  Serial.printf("bonded devices at boot: %d\n", n);
  if (n <= 0) return;
  esp_bd_addr_t *list = (esp_bd_addr_t *)malloc(n * sizeof(esp_bd_addr_t));
  if (!list) return;
  int m = n;
  if (esp_bt_gap_get_bond_device_list(&m, list) == ESP_OK)
    for (int i = 0; i < m; i++) esp_bt_gap_remove_bond_device(list[i]);
  free(list);
  Serial.printf("bonded devices now: %d\n", esp_bt_gap_get_bond_device_num());
}

void setup() {
  Serial.begin(115200);
  delay(300);
  for (size_t i = 0; i < sizeof(chunk); i++) chunk[i] = (uint8_t)i;
  SerialBT.begin("esp32-rstest");   // classic, general discoverable + connectable
  esp_err_t perr = esp_bredr_tx_power_set(ESP_PWR_LVL_P9, ESP_PWR_LVL_P9);
  Serial.printf("bredr tx power set: %s\n", esp_err_to_name(perr));
  esp_power_level_t lo, hi;
  esp_bredr_tx_power_get(&lo, &hi);
  Serial.printf("bredr tx power min %d max %d (7 = P9)\n", (int)lo, (int)hi);
  pinMode(LED_PIN, OUTPUT);
  eraseBonds();
  Serial.println("esp32-rstest: classic BT up, spp-sender v3 (512 B chunks, P/G control, LED, TX P9)");
  printAddr();
}

void loop() {
  static uint32_t t = 0;
  if (millis() - t > 5000) { t = millis(); printAddr(); }
  while (SerialBT.available()) {
    int c = SerialBT.read();
    if (c == 'P') paused = true;
    else if (c == 'G') paused = false;
  }
  if (SerialBT.hasClient() && !paused) {
    digitalWrite(LED_PIN, HIGH);                                 // solid while transmitting SPP data
    if (SerialBT.write(chunk, sizeof(chunk)) == 0) delay(2);   // queue full: let the stack drain
  } else {
    digitalWrite(LED_PIN, (millis() / 500) % 2);                 // slow blink: up, no client (or paused)
    delay(5);
  }
}
