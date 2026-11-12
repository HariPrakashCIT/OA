/*
  OsteoCare AI insole firmware - ESP8266 NodeMCU + 3 FSRs (heel, ball, toe)

  The ESP8266 has only ONE analog input (A0), so the three FSRs share it.
  Each FSR gets power from its own digital pin; the code switches on one
  FSR at a time, reads A0, then switches it off. No multiplexer needed.

  Wiring:
      D5 (GPIO14) ---- FSR heel ----+
      D6 (GPIO12) ---- FSR ball ----+---- A0
      D7 (GPIO13) ---- FSR toe  ----+
                                    |
                                  10k resistor
                                    |
                                   GND
  (one shared 10k resistor from A0 to GND)

  Do not use D3, D4 or D8 for the FSRs: they control how the board boots.

  Arduino IDE: install "esp8266" boards (Boards Manager), choose
  "NodeMCU 1.0 (ESP-12E Module)". Wi-Fi must be 2.4 GHz.
*/
#include <ESP8266WiFi.h>
#include <ESP8266HTTPClient.h>
#include <WiFiClientSecureBearSSL.h>

// ----- change these -----
const char* WIFI_SSID     = "YOUR_WIFI_NAME";
const char* WIFI_PASSWORD = "YOUR_WIFI_PASSWORD";
const char* API_URL       = "https://osteocare-api.onrender.com/api/ingest";
const char* DEVICE_KEY    = "oc_paste_the_device_key_here";

const uint8_t PIN_HEEL = D5;
const uint8_t PIN_BALL = D6;
const uint8_t PIN_TOE  = D7;

const uint32_t SAMPLE_MS = 100;   // 10 readings per second
const uint32_t SEND_MS   = 1000;  // send once per second
const int MAX_BUFFER     = 150;   // ~15 s kept if Wi-Fi drops (ESP8266 RAM is small)
// ------------------------

struct Sample { uint16_t heel, ball, toe; uint32_t t; };
Sample buf[MAX_BUFFER];
int count = 0;
uint32_t lastSample = 0, lastSend = 0;

void allOff() {
  // High-impedance input = that FSR gets no power and does not affect A0
  pinMode(PIN_HEEL, INPUT);
  pinMode(PIN_BALL, INPUT);
  pinMode(PIN_TOE, INPUT);
}

uint16_t readFsr(uint8_t pin) {
  allOff();
  pinMode(pin, OUTPUT);
  digitalWrite(pin, HIGH);
  delayMicroseconds(300);              // let the voltage settle
  uint32_t s = 0;
  for (int i = 0; i < 4; i++) s += analogRead(A0);   // 0..1023
  allOff();
  uint16_t v = (s / 4) * 4;            // scale 10-bit to the 0..4095 range the website uses
  return v > 4095 ? 4095 : v;
}

void connectWiFi() {
  if (WiFi.status() == WL_CONNECTED) return;
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  Serial.print("Connecting to Wi-Fi");
  uint32_t start = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - start < 15000) {
    delay(300);
    Serial.print(".");
  }
  Serial.println(WiFi.status() == WL_CONNECTED ? " connected" : " failed, will retry");
}

void takeSample() {
  if (count == MAX_BUFFER) {
    memmove(buf, buf + 1, sizeof(Sample) * (MAX_BUFFER - 1));
    count--;
  }
  buf[count++] = { readFsr(PIN_HEEL), readFsr(PIN_BALL), readFsr(PIN_TOE), millis() };
}

void sendBatch() {
  if (count == 0) return;
  connectWiFi();
  if (WiFi.status() != WL_CONNECTED) return;

  int n = min(count, 30);
  uint32_t now = millis();
  String body;
  body.reserve(n * 60 + 20);
  body = "{\"readings\":[";
  for (int i = 0; i < n; i++) {
    if (i) body += ",";
    body += "{\"heel\":" + String(buf[i].heel) + ",\"ball\":" + String(buf[i].ball) +
            ",\"toe\":" + String(buf[i].toe) + ",\"age_ms\":" + String(now - buf[i].t) + "}";
  }
  body += "]}";

  std::unique_ptr<BearSSL::WiFiClientSecure> client(new BearSSL::WiFiClientSecure);
  client->setInsecure();               // simple setup; fine for a prototype
  client->setBufferSizes(4096, 1024);  // saves RAM on the ESP8266
  HTTPClient http;
  http.begin(*client, API_URL);
  http.addHeader("Content-Type", "application/json");
  http.addHeader("X-Device-Key", DEVICE_KEY);
  http.setTimeout(8000);
  int code = http.POST(body);
  http.end();

  if (code == 200) {
    memmove(buf, buf + n, sizeof(Sample) * (count - n));
    count -= n;
    Serial.printf("sent %d samples, %d waiting\n", n, count);
  } else {
    Serial.printf("send failed: HTTP %d (%s), keeping %d samples\n",
                  code, http.errorToString(code).c_str(), count);
  }
}

void setup() {
  Serial.begin(115200);
  allOff();
  connectWiFi();
}

void loop() {
  uint32_t now = millis();
  if (now - lastSample >= SAMPLE_MS) { lastSample = now; takeSample(); }
  if (now - lastSend >= SEND_MS)     { lastSend = now; sendBatch(); }
  yield();
}
