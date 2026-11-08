// Classic Bluetooth (BR/EDR) test peer for bt_capture_roleswitch.sh: discoverable, connectable,
// answers L2CAP echo (l2ping), and once a peer opens its SPP (RFCOMM) channel it streams 512-byte
// chunks to it, so the ESP32 sends multi-slot packets - after a role switch, as the master.
// The peer pauses and resumes it with one byte: 'P' stops writing, 'G' resumes (the marker pauses).
// No pairing is requested; SSP is left off.
#include <Arduino.h>
#include "BluetoothSerial.h"
#include "esp_bt_device.h"

BluetoothSerial SerialBT;
static uint8_t chunk[512];
static bool paused = false;

static void printAddr() {
  const uint8_t *a = esp_bt_dev_get_address();
  Serial.printf("BD_ADDR %02X:%02X:%02X:%02X:%02X:%02X\n", a[0], a[1], a[2], a[3], a[4], a[5]);
}

void setup() {
  Serial.begin(115200);
  delay(300);
  for (size_t i = 0; i < sizeof(chunk); i++) chunk[i] = (uint8_t)i;
  SerialBT.begin("esp32-rstest");   // classic, general discoverable + connectable
  Serial.println("esp32-rstest: classic BT up, spp-sender v1 (512 B chunks, P/G control)");
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
    if (SerialBT.write(chunk, sizeof(chunk)) == 0) delay(2);   // queue full: let the stack drain
  } else {
    delay(5);
  }
}
