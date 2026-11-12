/*
  OsteoCare AI insole firmware (ESP32 + 3 FSRs: heel, ball, toe)

  Wiring (each FSR as a voltage divider):
    3V3 -- FSR -- GPIO pin -- 10k resistor -- GND
  Use ADC1 pins only (ADC2 does not work while Wi-Fi is on).

  Reads all three sensors 10 times a second and sends them to the backend
  once a second as a batch. Board: "ESP32 Dev Module", Arduino core 2.x or 3.x.
*/
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <HTTPClient.h>

// ----- change these -----
const char* WIFI_SSID     = "YOUR_WIFI_NAME";
const char* WIFI_PASSWORD = "YOUR_WIFI_PASSWORD";
const char* API_URL       = "https://your-backend.onrender.com/api/ingest";
const char* DEVICE_KEY    = "oc_paste_the_device_key_here";

const int PIN_HEEL = 34;
const int PIN_BALL = 35;
const int PIN_TOE  = 32;

const uint32_t SAMPLE_MS = 100;   // 10 Hz
const uint32_t SEND_MS   = 1000;  // one HTTP request per second
const int MAX_BUFFER     = 300;   // keeps ~30 s of data if Wi-Fi drops
// ------------------------

struct Sample { uint16_t heel, ball, toe; uint32_t t; };
Sample buf[MAX_BUFFER];
int count = 0;
uint32_t lastSample = 0, lastSend = 0;

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

uint16_t readAvg(int pin) {
  uint32_t s = 0;
  for (int i = 0; i < 4; i++) s += analogRead(pin);
  return s / 4;
}

void takeSample() {
  if (count == MAX_BUFFER) {               // buffer full: drop the oldest sample
    memmove(buf, buf + 1, sizeof(Sample) * (MAX_BUFFER - 1));
    count--;
  }
  buf[count++] = { readAvg(PIN_HEEL), readAvg(PIN_BALL), readAvg(PIN_TOE), millis() };
}

void sendBatch() {
  if (count == 0) return;
  connectWiFi();
  if (WiFi.status() != WL_CONNECTED) return;

  int n = min(count, 100);
  uint32_t now = millis();
  String body = "{\"readings\":[";
  for (int i = 0; i < n; i++) {
    if (i) body += ",";
    body += "{\"heel\":" + String(buf[i].heel) + ",\"ball\":" + String(buf[i].ball) +
            ",\"toe\":" + String(buf[i].toe) + ",\"age_ms\":" + String(now - buf[i].t) + "}";
  }
  body += "]}";

  WiFiClientSecure client;
  client.setInsecure();  // simple setup; add the server's root CA for production
  HTTPClient http;
  http.begin(client, API_URL);
  http.addHeader("Content-Type", "application/json");
  http.addHeader("X-Device-Key", DEVICE_KEY);
  http.setTimeout(5000);
  int code = http.POST(body);
  http.end();

  if (code == 200) {
    memmove(buf, buf + n, sizeof(Sample) * (count - n));
    count -= n;
    Serial.printf("sent %d samples, %d waiting\n", n, count);
  } else {
    Serial.printf("send failed: HTTP %d, keeping %d samples\n", code, count);
  }
}

void setup() {
  Serial.begin(115200);
  analogReadResolution(12);        // 0..4095, matches the website
  analogSetAttenuation(ADC_11db);  // full 0..3.3 V range
  connectWiFi();
}

void loop() {
  uint32_t now = millis();
  if (now - lastSample >= SAMPLE_MS) { lastSample = now; takeSample(); }
  if (now - lastSend >= SEND_MS)     { lastSend = now; sendBatch(); }
}
