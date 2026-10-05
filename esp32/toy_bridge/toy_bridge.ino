/*
 * toy_bridge.ino —— 谜姬「计算者-X」BLE 网关固件（ESP32）
 *
 * 作用：ESP32 自己当 BLE 主机连玩具，对外提供 HTTP 和串口两种控制接口。
 *       架构：  [PC / bridge_cli.py] --WiFi HTTP 或 USB串口--> [ESP32] --BLE--> [玩具]
 *
 * 依赖：只用 ESP32 Arduino 核心自带的库，不需要额外装包
 *       （BLEDevice / BLEScan / WiFi / WebServer）
 *
 * 烧录：
 *   Arduino IDE → 开发板选 "ESP32 Dev Module" → 直接上传
 *   如果编译报 BLE 相关错误，把 "Partition Scheme" 改成 Huge APP (3MB No OTA)
 *
 * 接线：插 USB 就能用（供电 + 串口），不需要接任何外设
 *
 * 配置：改下面的 WIFI_SSID / WIFI_PASS；不想用 WiFi 就把 USE_WIFI 设成 0，
 *       此时只能走 USB 串口（115200 波特率）
 *
 * 串口命令（每行一条）：
 *   SET <cmd十进制或0x十六进制> <payload逗号分隔> [hz]   例: SET 8 0,20,1 10
 *   STOP
 *   STATE
 *   SCAN
 *   RAW <十六进制字符串>                                例: RAW AA0803010A00C3
 *
 * HTTP 接口（和 PC 版 web_server.py 完全一致）：
 *   GET  /api/state
 *   POST /api/set      {"running":true,"cmd":8,"payload":[0,20,1],"hz":10}
 *   GET  /api/log
 *   GET  /api/notify
 */

#include <BLEDevice.h>
#include <BLEScan.h>
#include <BLEAdvertisedDevice.h>
#include <BLEUtils.h>

#define USE_WIFI 1
const char* WIFI_SSID = "改成你的WiFi名";
const char* WIFI_PASS = "改成你的WiFi密码";

static const char* TARGET_NAME_PREFIX = "mizzzee";

static BLEUUID SVC_UUID("0000ffe0-0000-1000-8000-00805f9b34fb");
static BLEUUID CHR_UUID("0000ffe1-0000-1000-8000-00805f9b34fb");

#if USE_WIFI
#include <WiFi.h>
#include <WebServer.h>
WebServer http(8080);
#endif

// ------------------------------------------------------------------ 状态
static bool     g_running   = false;
static uint8_t  g_cmd       = 0x08;
static uint8_t  g_payload[16];
static size_t   g_plen      = 0;
static uint32_t g_periodMs  = 100;
static uint32_t g_sent      = 0;
static uint32_t g_lastSend  = 0;
static bool     g_connected = false;
static String   g_devName   = "";
static String   g_devAddr   = "";

static BLEClient*  g_client = nullptr;
static BLERemoteCharacteristic* g_chr = nullptr;

static const int LOG_MAX = 4000;
static String g_logBuf = "";
static String g_lastNotify = "";
static uint32_t g_notifyCount = 0;

// ------------------------------------------------------------------ 工具
static String hexStr(const uint8_t* d, size_t n) {
  String s;
  for (size_t i = 0; i < n; i++) {
    if (i) s += ' ';
    char b[4];
    sprintf(b, "%02X", d[i]);
    s += b;
  }
  return s;
}

static void logLine(const String& s) {
  String line = "[" + String(millis()) + "] " + s;
  Serial.println(line);
  g_logBuf += line + "\n";
  if (g_logBuf.length() > LOG_MAX) g_logBuf = g_logBuf.substring(g_logBuf.length() - LOG_MAX);
}

// 组帧： AA <cmd> <len> <payload...> <sum8>
static size_t buildFrame(uint8_t* out, uint8_t cmd, const uint8_t* payload, size_t plen) {
  out[0] = 0xAA;
  out[1] = cmd;
  out[2] = (uint8_t)plen;
  for (size_t i = 0; i < plen; i++) out[3 + i] = payload[i];
  uint8_t sum = 0;
  for (size_t i = 0; i < 3 + plen; i++) sum += out[i];
  out[3 + plen] = sum;
  return 4 + plen;
}

static void sendFrame(uint8_t cmd, const uint8_t* payload, size_t plen) {
  if (!g_connected || !g_chr) { logLine("未连接，跳过发送"); return; }
  uint8_t buf[24];
  size_t n = buildFrame(buf, cmd, payload, plen);
  g_chr->writeValue(buf, n, false);   // false = 无需应答写
  g_sent++;
}

// ------------------------------------------------------------------ 通知回调
static void notifyCallback(BLERemoteCharacteristic* chr, uint8_t* data, size_t len, bool isNotify) {
  g_notifyCount++;
  String h = hexStr(data, len);
  if (h != g_lastNotify) {          // 只记变化，避免刷屏
    g_lastNotify = h;
    logLine("NOTIFY " + h);
  }
}

// ------------------------------------------------------------------ 扫描 + 连接
class ScanCB : public BLEAdvertisedDeviceCallbacks {
  void onResult(BLEAdvertisedDevice dev) {
    String name = dev.haveName() ? dev.getName().c_str() : String("");
    if (name.startsWith(TARGET_NAME_PREFIX)) {
      logLine("发现目标: " + name + "  " + dev.getAddress().toString().c_str());
      g_devName = name;
      g_devAddr = dev.getAddress().toString().c_str();
      BLEDevice::getScan()->stop();
    }
  }
};

static bool connectToy(uint32_t scanSeconds = 8) {
  if (g_connected) return true;
  logLine("开始扫描 BLE ...");
  BLEScan* scan = BLEDevice::getScan();
  scan->setAdvertisedDeviceCallbacks(new ScanCB(), false);
  scan->setActiveScan(true);
  scan->setInterval(100);
  scan->setWindow(99);
  scan->start(scanSeconds, false);
  scan->clearResults();

  if (g_devAddr.length() == 0) { logLine("没扫到设备（玩具要开机、且没被别的 App 占着）"); return false; }

  logLine("连接 " + g_devAddr);
  g_client = BLEDevice::createClient();
  if (!g_client->connect(g_devAddr.c_str())) { logLine("连接失败"); g_client = nullptr; return false; }

  BLERemoteService* svc = g_client->getService(SVC_UUID);
  if (!svc) { logLine("找不到服务 FFE0"); g_client->disconnect(); g_client = nullptr; return false; }

  g_chr = svc->getCharacteristic(CHR_UUID);
  if (!g_chr) { logLine("找不到特征 FFE1"); g_client->disconnect(); g_client = nullptr; return false; }

  if (g_chr->canNotify()) g_chr->registerForNotify(notifyCallback);
  g_connected = true;
  logLine("已连接，写特征 FFE1 就绪");
  return true;
}

// ------------------------------------------------------------------ 串口命令
static int parseIntSmart(const String& s) {
  if (s.startsWith("0x") || s.startsWith("0X")) return (int)strtol(s.c_str() + 2, nullptr, 16);
  return s.toInt();
}

static void parsePayload(const String& csv) {
  g_plen = 0;
  int start = 0;
  while (start < (int)csv.length() && g_plen < sizeof(g_payload)) {
    int comma = csv.indexOf(',', start);
    if (comma < 0) comma = csv.length();
    String tok = csv.substring(start, comma);
    tok.trim();
    if (tok.length()) g_payload[g_plen++] = (uint8_t)parseIntSmart(tok);
    start = comma + 1;
  }
}

static void handleSerialLine(String line) {
  line.trim();
  if (!line.length()) return;
  int sp1 = line.indexOf(' ');
  String op = (sp1 < 0) ? line : line.substring(0, sp1);
  op.toUpperCase();
  String rest = (sp1 < 0) ? "" : line.substring(sp1 + 1);
  rest.trim();

  if (op == "SET") {
    int sp2 = rest.indexOf(' ');
    String cmdS = (sp2 < 0) ? rest : rest.substring(0, sp2);
    String payS = (sp2 < 0) ? "" : rest.substring(sp2 + 1);
    int sp3 = payS.indexOf(' ');
    String hzS = (sp3 < 0) ? "" : payS.substring(sp3 + 1);
    if (sp3 >= 0) payS = payS.substring(0, sp3);
    g_cmd = (uint8_t)parseIntSmart(cmdS);
    parsePayload(payS);
    if (hzS.length()) g_periodMs = max(20, (int)(1000.0 / hzS.toFloat()));
    g_running = true;
    logLine("SET cmd=0x" + String(g_cmd, HEX) + " payload=" + payS + " period=" + String(g_periodMs) + "ms");
  } else if (op == "STOP") {
    g_running = false;
    uint8_t z[5] = {0, 0, 0, 0, 0};
    sendFrame(0x06, z, 5);
    delay(120);
    uint8_t z1[1] = {0};
    sendFrame(0x08, z1, 1);
    logLine("STOP（已下发全 0 帧）");
  } else if (op == "STATE") {
    logLine("running=" + String(g_running ? "true" : "false") + " cmd=0x" + String(g_cmd, HEX) +
            " plen=" + String(g_plen) + " period=" + String(g_periodMs) + " conn=" + String(g_connected ? 1 : 0) +
            " sent=" + String(g_sent) + " notifies=" + String(g_notifyCount));
  } else if (op == "SCAN") {
    g_connected = false;
    connectToy(8);
  } else if (op == "RAW") {
    rest.replace(" ", "");
    uint8_t buf[32];
    size_t n = 0;
    for (size_t i = 0; i + 1 < rest.length() && n < sizeof(buf); i += 2) {
      buf[n++] = (uint8_t)strtol(rest.substring(i, i + 2).c_str(), nullptr, 16);
    }
    if (g_connected && g_chr) { g_chr->writeValue(buf, n, false); g_sent++; logLine("RAW " + hexStr(buf, n)); }
  } else {
    logLine("未知命令: " + line);
  }
}

// ------------------------------------------------------------------ HTTP
#if USE_WIFI
static String jsonState() {
  String s = "{\"running\":" + String(g_running ? "true" : "false") +
             ",\"cmd\":" + String(g_cmd) + ",\"payload\":[";
  for (size_t i = 0; i < g_plen; i++) { if (i) s += ","; s += String(g_payload[i]); }
  s += "],\"hz\":" + String(1000.0 / g_periodMs, 1) +
       ",\"connected\":" + String(g_connected ? "true" : "false") +
       ",\"device\":\"" + g_devAddr + "\",\"sent\":" + String(g_sent) +
       ",\"notifies\":" + String(g_notifyCount) + "}";
  return s;
}

// 极简 JSON 取值，避免引入 ArduinoJson
static bool jsonBool(const String& b, const char* key) {
  int i = b.indexOf(String("\"") + key + "\"");
  if (i < 0) return false;
  i = b.indexOf(':', i);
  return b.substring(i + 1, i + 8).indexOf("true") >= 0;
}
static long jsonNum(const String& b, const char* key, long def) {
  int i = b.indexOf(String("\"") + key + "\"");
  if (i < 0) return def;
  i = b.indexOf(':', i) + 1;
  while (i < (int)b.length() && (b[i] == ' ')) i++;
  return b.substring(i).toInt();
}
static bool jsonPayload(const String& b, uint8_t* out, size_t& n) {
  int i = b.indexOf("\"payload\"");
  if (i < 0) return false;
  int lb = b.indexOf('[', i), rb = b.indexOf(']', lb);
  if (lb < 0 || rb < 0) return false;
  n = 0;
  int start = lb + 1;
  while (start < rb && n < 16) {
    int comma = b.indexOf(',', start);
    if (comma < 0 || comma > rb) comma = rb;
    String tok = b.substring(start, comma);
    tok.trim();
    if (tok.length()) out[n++] = (uint8_t)tok.toInt();
    start = comma + 1;
  }
  return true;
}

static void setupHttp() {
  http.on("/api/state", HTTP_GET, []() { http.send(200, "application/json", jsonState()); });

  http.on("/api/set", HTTP_POST, []() {
    String body = http.arg("plain");
    if (body.indexOf("\"running\"") >= 0) g_running = jsonBool(body, "running");
    if (body.indexOf("\"cmd\"") >= 0) g_cmd = (uint8_t)jsonNum(body, "cmd", g_cmd);
    uint8_t tmp[16]; size_t n = 0;
    if (jsonPayload(body, tmp, n)) { memcpy(g_payload, tmp, n); g_plen = n; }
    if (body.indexOf("\"hz\"") >= 0) {
      float hz = jsonNum(body, "hz", 10);
      g_periodMs = max(20, (int)(1000.0 / (hz <= 0 ? 10 : hz)));
    }
    logLine("HTTP /api/set " + body);
    http.send(200, "application/json", jsonState());
  });

  http.on("/api/stop", HTTP_POST, []() {
    g_running = false;
    uint8_t z[5] = {0, 0, 0, 0, 0};
    sendFrame(0x06, z, 5);
    delay(120);
    uint8_t z1[1] = {0};
    sendFrame(0x08, z1, 1);
    http.send(200, "application/json", jsonState());
  });

  http.on("/api/log", HTTP_GET, []() { http.send(200, "text/plain; charset=utf-8", g_logBuf); });

  http.on("/api/notify", HTTP_GET, []() { http.send(200, "text/plain", g_lastNotify); });

  http.on("/", HTTP_GET, []() {
    http.send(200, "text/plain; charset=utf-8",
              "toy_bridge on ESP32\n" + jsonState() + "\n\n串口命令: SET/STOP/STATE/SCAN/RAW\n");
  });

  http.begin();
  logLine("HTTP 服务已启动: http://" + WiFi.localIP().toString() + ":8080");
}
#endif

// ------------------------------------------------------------------ 生命周期
void setup() {
  Serial.begin(115200);
  delay(400);
  Serial.println();
  logLine("=== 计算者-X BLE 网关启动 ===");

  BLEDevice::init("toy-bridge");
  connectToy(8);

#if USE_WIFI
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  logLine("连接 WiFi ...");
  uint32_t t0 = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - t0 < 20000) delay(300);
  if (WiFi.status() == WL_CONNECTED) {
    logLine("WiFi OK: " + WiFi.localIP().toString());
    setupHttp();
  } else {
    logLine("WiFi 失败，只能用串口");
  }
#endif
}

static String g_serialBuf;

void loop() {
  // 串口
  while (Serial.available()) {
    char c = (char)Serial.read();
    if (c == '\n' || c == '\r') {
      if (g_serialBuf.length()) { handleSerialLine(g_serialBuf); g_serialBuf = ""; }
    } else {
      g_serialBuf += c;
      if (g_serialBuf.length() > 200) g_serialBuf = "";
    }
  }

#if USE_WIFI
  if (WiFi.status() == WL_CONNECTED) http.handleClient();
#endif

  // BLE 断开自动重连
  if (!g_connected || (g_client && !g_client->isConnected())) {
    g_connected = false;
    g_chr = nullptr;
    if (g_client) { g_client = nullptr; }
    static uint32_t lastTry = 0;
    if (millis() - lastTry > 5000) {
      lastTry = millis();
      logLine("掉线，尝试重连 ...");
      connectToy(6);
    }
  }

  // 定时下发
  if (g_running && g_connected && g_chr && millis() - g_lastSend >= g_periodMs) {
    g_lastSend = millis();
    sendFrame(g_cmd, g_payload, g_plen);
  }

  delay(2);
}
