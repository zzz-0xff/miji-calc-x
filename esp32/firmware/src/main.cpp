/*
 * main.cpp —— 计算者-X 网关 + 心率监听（ESP32-S3 / PlatformIO）
 *
 * 一个 ESP32 同时干两件事：
 *   ① 当 BLE 主机连玩具  FF E0 / FFE1  → 收发控制帧
 *   ② 当 BLE 主机连手环  0x180D / 0x2A37 → 订阅心率广播
 *
 * 链路： [PC / toy_panel.py] --USB COM3--> [ESP32-S3] --BLE--> [玩具] + [华为手环]
 *
 * 串口命令（115200，每行一条）：
 *     SET <cmd> <payload逗号分隔> [hz]     例: SET 8 60,30,1 10
 *     STOP / STATE / SCAN / BSCAN / RAW <hex>
 *     HR                                   打印当前心率与连接状态
 *
 * 串口输出：
 *     HR 78                                 心率变化时打印
 *     NOTIFY ...                            玩具回传
 *     其他日志
 */

#include <Arduino.h>
#include <BLEDevice.h>
#include <BLEScan.h>
#include <BLEAdvertisedDevice.h>
#include <BLEUtils.h>
#include <esp_gap_ble_api.h>      // esp_ble_gap_set_device_name（冒充模式要改名字）

#ifndef USE_WIFI
#define USE_WIFI 0
#endif
#ifndef WIFI_SSID
#define WIFI_SSID "your-ssid"
#endif
#ifndef WIFI_PASS
#define WIFI_PASS "your-pass"
#endif

static const char* TARGET_NAME_PREFIX = "mizzzee";

static BLEUUID SVC_UUID("0000ffe0-0000-1000-8000-00805f9b34fb");
static BLEUUID CHR_UUID("0000ffe1-0000-1000-8000-00805f9b34fb");

// 标准心率服务
static BLEUUID HRS_SVC("0000180d-0000-1000-8000-00805f9b34fb");
static BLEUUID HRM_CHR("00002a37-0000-1000-8000-00805f9b34fb");

#if USE_WIFI
#include <WiFi.h>
#include <WebServer.h>
static WebServer http(8080);
#endif

// ------------------------------------------------------------------ 玩具状态
static bool     g_running   = false;
static uint8_t  g_cmd       = 0x08;
static uint8_t  g_payload[16];
static size_t   g_plen      = 0;
static uint32_t g_periodMs  = 100;
static uint32_t g_sent      = 0;
static uint32_t g_lastSend  = 0;

// 让出设备：断开玩具并且**不再自动重连**。手机 App 接管 / 冒充模式时要用。
static bool g_toyHold = false;
// 回传全量记录（关掉去重），抓按键/抓 App 写入时用
static bool g_notifyLogAll = false;

// 前置声明 —— 冒充模式那段要用，但那两个函数在后面才定义
static bool connectToy(uint32_t scanSeconds);
static bool connectBand(uint32_t scanSeconds);

// 最近一次扫到的 mizzzee 广播内容 —— 冒充时照抄，App 才会认出正确机型
// 下面的默认值是实测 mizzzee_0007 的广播，固件重启后也不会丢。
static String g_advName  = "mizzzee_0007";
static String g_advSdHex = "";      // service data（这个机型没有）
static String g_advSdUuid = "";
static String g_advMfrHex = "0F 0F 06 75 72 74 2D 30 31 00 00 00 00 00 00 00";
static bool     g_connected = false;
static String   g_devName   = "";
static String   g_devAddr   = "";
static BLEClient*               g_client = nullptr;
static BLERemoteCharacteristic* g_chr    = nullptr;
static uint8_t  g_bdAddr[6] = {0};
static bool     g_haveAddr  = false;
static esp_ble_addr_type_t g_bdType = BLE_ADDR_TYPE_PUBLIC;

// ------------------------------------------------------------------ 手环状态
static BLEClient*               g_bandClient = nullptr;
static BLERemoteCharacteristic* g_bandChr    = nullptr;
static bool     g_bandConnected = false;
static uint8_t  g_bandAddr[6]   = {0};
static bool     g_haveBandAddr  = false;
static esp_ble_addr_type_t g_bandType = BLE_ADDR_TYPE_PUBLIC;
static String   g_bandAddrStr   = "";
static String   g_bandName      = "";
static int      g_hr            = 0;
static int      g_hrPrev        = -1;
static uint32_t g_hrLastMs      = 0;
static uint32_t g_hrCount       = 0;
static String   g_hrHistory     = "";      // "秒:心率," 序列

static const int LOG_MAX = 3000;
static String   g_logBuf     = "";
static String   g_lastNotify = "";
static uint32_t g_notifyCount = 0;
static String   g_serialBuf;

// ------------------------------------------------------------------ 双路输出
// 板子有两个 USB 口：UART 口（CH343）和原生 USB 口（USB-Serial/JTAG）。
// 开了 USB_CDC_ON_BOOT 之后 Serial 变成 USB CDC，只走原生口；
// 所以这里统一镜像到 Serial（USB CDC）和 Serial0（UART0 → CH343），
// **插哪个口都能收到日志**。
#if defined(ARDUINO_USB_CDC_ON_BOOT) && ARDUINO_USB_CDC_ON_BOOT
  #define HAS_SECOND_SERIAL 1
#else
  #define HAS_SECOND_SERIAL 0
#endif

static void outBoth(const String& s) {
#if HAS_SECOND_SERIAL
  if (Serial) Serial.println(s);      // 没主机连接时不写，避免 USBCDC 阻塞
  Serial0.println(s);
#else
  Serial.println(s);
#endif
}

static void outRaw(const char* s) {
#if HAS_SECOND_SERIAL
  if (Serial) Serial.print(s);
  Serial0.print(s);
#else
  Serial.print(s);
#endif
}

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
  outBoth(line);
  g_logBuf += line + "\n";
  if (g_logBuf.length() > LOG_MAX) g_logBuf = g_logBuf.substring(g_logBuf.length() - LOG_MAX);
}

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
  if (!g_connected || !g_chr) return;
  uint8_t buf[24];
  size_t n = buildFrame(buf, cmd, payload, plen);
  g_chr->writeValue(buf, n, false);
  g_sent++;
}

// ------------------------------------------------------------------ 冒充玩具（外设模式）
//
// 目的：让手机 App 连到**我们**身上，从而拿到它每按一个档位到底发什么字节。
// 这是唯一能拿到「App 真值」的办法 ——
//   设备只允许一个连接方，所以没法一边让 App 控、一边当主机旁听；
//   ColorOS 的 HCI snoop 是摆设，抓不到空中包。
// 做法：ESP32 起一个 GATT 外设，服务/特征和玩具一模一样（FFE0/FFE1），
//       名字用 App 认得的 mizzzee_0029（App 扫描时只按名字过滤，
//       Zx 表里 mizzzee_0029 → FFE0/FFE1，正好对得上）。
static BLEServer*         g_spoofServer = nullptr;
static BLECharacteristic* g_spoofChr    = nullptr;
static bool               g_spoofing    = false;
static uint32_t           g_spoofWrites = 0;

static void spoofNotifyBack(const uint8_t* d, size_t n) {
  // App 可能等设备应答；把收到的原样回一条，和玩具的行为一致
  if (g_spoofChr && g_spoofServer && g_spoofServer->getConnectedCount() > 0) {
    g_spoofChr->setValue((uint8_t*)d, n);
    g_spoofChr->notify();
  }
}

class SpoofWriteCB : public BLECharacteristicCallbacks {
  void onWrite(BLECharacteristic* c) override {
    std::string v = c->getValue();
    if (v.empty()) return;
    g_spoofWrites++;
    logLine("APPWRITE " + hexStr((const uint8_t*)v.data(), v.length()) +
            "   (" + String((int)v.length()) + " 字节)");
  }
  void onRead(BLECharacteristic* c) override {
    logLine("APPREAD（App 读了特征）");
  }
};

class SpoofServerCB : public BLEServerCallbacks {
  void onConnect(BLEServer*) override { logLine("App 已连上来（冒充模式）"); }
  void onDisconnect(BLEServer* s) override {
    logLine("App 断开了（冒充模式）—— 重新开始广播");
    delay(300);
    if (g_spoofing) BLEDevice::startAdvertising();
  }
};

static int hexToBytes(const String& in, uint8_t* out, size_t maxN) {
  String h = in;
  h.replace(" ", "");
  h.replace(",", "");
  size_t n = 0;
  for (int i = 0; i + 1 < (int)h.length() && n < maxN; i += 2) {
    out[n++] = (uint8_t)strtol(h.substring(i, i + 2).c_str(), nullptr, 16);
  }
  return (int)n;
}

// GAP 事件回调原本放在这里做广播诊断，但当前 Arduino 核心没导出
// esp_ble_gap_cb_event_t，编译不过。冒充功能本身已经验证可用
// （手机 App 能连上并抓到写入），所以整段删掉，不再需要。

static void startSpoof(const String& argName) {
  if (g_spoofing) { logLine("已经在冒充模式里了"); return; }
  // 冒充期间必须让出玩具，否则射频/角色互抢，两边都不稳
  g_toyHold = true;
  g_connected = false;
  g_chr = nullptr;
  if (g_client) { g_client->disconnect(); }

  // 名字：命令行给的 > 刚扫到的真机型名 > mizzzee_0007
  // **必须用真机型名** —— 用 mizzzee_0029 那种别的型号，App 会当成另一款产品，
  // 发出来的指令就不是我们要抓的了。
  String nm = argName;
  nm.trim();
  if (!nm.length()) nm = g_advName.length() ? g_advName : String("mizzzee_0007");
  esp_ble_gap_set_device_name(nm.c_str());

  g_spoofServer = BLEDevice::createServer();
  g_spoofServer->setCallbacks(new SpoofServerCB());
  BLEService* svc = g_spoofServer->createService(SVC_UUID);
  g_spoofChr = svc->createCharacteristic(
      CHR_UUID,
      BLECharacteristic::PROPERTY_READ | BLECharacteristic::PROPERTY_WRITE |
      BLECharacteristic::PROPERTY_WRITE_NR | BLECharacteristic::PROPERTY_NOTIFY);
  g_spoofChr->setCallbacks(new SpoofWriteCB());
  svc->start();

  // 广播：照抄真设备的名字 + 服务 UUID。
  //
  // 踩过的坑：
  //   ① 一开始用手工拼原始广播包（setAdvertisementData），结果**广播根本没起来** ——
  //      ESP32 自己扫 12 秒，扫到 30 个设备却扫不到自己。换成库的标准 API 才稳。
  //   ② BLE 单个广播包上限 31 字节，超了会被直接拒绝、整个广播起不来。
  //   ③ 真设备广播的是 **FFF0**，不是 FFE0（控制服务是 FFE0/FFE1，两者不一样）。
  //   ④ 厂商数据里那串 "urt-01" 不用抄 —— App 读的是 uni 的 t.serviceData，
  //      而真设备压根没有 serviceData，所以厂商数据对认型号没影响。
  esp_ble_gap_set_device_name(nm.c_str());
  BLEAdvertising* adv = BLEDevice::getAdvertising();
  adv->addServiceUUID(BLEUUID((uint16_t)0xFFF0));   // 真设备广播的就是这个
  adv->addServiceUUID(SVC_UUID);                    // FFE0 也放上，多一层保险
  adv->setScanResponse(true);
  adv->setMinPreferred(0x06);
  adv->setMaxPreferred(0x12);
  BLEDevice::startAdvertising();
  delay(500);
  logLine("广播已启动：名字=" + nm + "  服务=FFF0+FFE0  扫描响应=开");

  g_spoofing = true;
  g_spoofWrites = 0;
  logLine("=== 冒充模式已开：名字=" + nm + " 服务=FFE0 特征=FFE1 ===");
  logLine("现在用手机 App 连它，App 发的每个字节都会以 APPWRITE 打出来。");
  logLine("退出：串口发 SPOOFOFF（或面板 /api/spoof off）");
}

static void stopSpoof() {
  if (!g_spoofing) { logLine("本来就不在冒充模式"); return; }
  BLEDevice::stopAdvertising();
  if (g_spoofChr) { g_spoofChr = nullptr; }
  if (g_spoofServer) {
    g_spoofServer->disconnect(0);
    g_spoofServer = nullptr;
  }
  g_spoofing = false;
  logLine("冒充模式已关。收回玩具 ...");
  g_toyHold = false;
  g_haveAddr = false;
  connectToy(8);
}

// ------------------------------------------------------------------ 玩具通知
static void notifyCallback(BLERemoteCharacteristic*, uint8_t* data, size_t len, bool) {
  g_notifyCount++;
  String h = hexStr(data, len);
  if (g_notifyLogAll || h != g_lastNotify) {
    g_lastNotify = h;
    logLine("NOTIFY " + h);
  }
}

// ------------------------------------------------------------------ 心率回调
static void hrNotify(BLERemoteCharacteristic*, uint8_t* data, size_t len, bool) {
  if (len < 2) return;
  uint8_t flags = data[0];
  int bpm;
  if (flags & 0x01) {                 // bit0 = 1 → uint16
    if (len < 3) return;
    bpm = data[1] | (data[2] << 8);
  } else {
    bpm = data[1];
  }
  if (bpm <= 0 || bpm > 250) return;
  g_hr = bpm;
  g_hrLastMs = millis();
  g_hrCount++;
  g_hrHistory += String((uint32_t)(millis() / 1000)) + ":" + String(bpm) + ",";
  if (g_hrHistory.length() > 800) g_hrHistory = g_hrHistory.substring(g_hrHistory.length() - 800);
  if (bpm != g_hrPrev) {
    g_hrPrev = bpm;
    logLine("HR " + String(bpm));     // 只打印变化，AI 直接读这行
  }
}

// ------------------------------------------------------------------ 扫描：回调只注册一次
// 坑：BLEScan::setAdvertisedDeviceCallbacks() 会 delete 上一个回调对象，
//     多次调用会导致扫描器持有已释放指针 → StoreProhibited 崩溃。
//     所以全局只 new 一次，之后靠 g_scanMode 区分目标。
enum ScanMode { SCAN_IDLE = 0, SCAN_TOY = 1, SCAN_BAND = 2, SCAN_ADV = 3 };
static volatile int g_scanMode = SCAN_IDLE;

class DualScanCB : public BLEAdvertisedDeviceCallbacks {
  void onResult(BLEAdvertisedDevice dev) {
    int mode = g_scanMode;
    String name = dev.haveName() ? String(dev.getName().c_str()) : String("");

    if (mode == SCAN_ADV) {
      // 把广播内容原样吐出来 —— 冒充时必须一模一样，
      // 否则 App 会把它认成别的机型，发的指令也就不是我们要的。
      logLine("ADV name=[" + name + "] addr=" + dev.getAddress().toString().c_str() +
              " type=" + String((int)dev.getAddressType()) +
              " rssi=" + String(dev.getRSSI()));
      for (int i = 0; i < dev.getServiceUUIDCount(); i++) {
        logLine("ADV   svcUUID[" + String(i) + "]=" +
                dev.getServiceUUID(i).toString().c_str());
      }
      if (dev.haveManufacturerData()) {
        std::string md = dev.getManufacturerData();
        g_advMfrHex = hexStr((const uint8_t*)md.data(), md.size());
        logLine("ADV   mfr=" + g_advMfrHex);
      }
      for (int i = 0; i < dev.getServiceDataCount(); i++) {
        std::string sd = dev.getServiceData(i);
        g_advSdHex = hexStr((const uint8_t*)sd.data(), sd.size());
        g_advSdUuid = dev.getServiceDataUUID(i).toString().c_str();
        logLine("ADV   svcData[" + String(i) + "] uuid=" + g_advSdUuid +
                " data=" + g_advSdHex);
      }
      logLine("ADV   full=" + String(dev.toString().c_str()));
      if (name.indexOf("mizzzee") >= 0 || name.indexOf("MIZZZEE") >= 0) {
        g_advName = name;
        logLine("ADV >>> 已记住这个机型，SPOOF 时照抄：" + g_advName);
      }
      return;
    }

    if (mode == SCAN_TOY && !g_haveAddr) {
      if (name.startsWith(TARGET_NAME_PREFIX)) {
        g_devName = name;
        BLEAddress a = dev.getAddress();
        memcpy(g_bdAddr, a.getNative(), 6);
        g_bdType = dev.getAddressType();
        g_haveAddr = true;
        g_devAddr = a.toString().c_str();
        logLine("发现玩具: " + g_devName + "  " + g_devAddr + "  addrType=" + String((int)g_bdType));
        BLEDevice::getScan()->stop();
      }
      return;
    }

    if (mode == SCAN_BAND && !g_haveBandAddr) {
      // 只认名字 —— 别再按「有没有 HRS 服务 0x180D」筛了。
      // 实测踩坑：邻居设备 BS003B 也广播 0x180D，固件扑错人，
      // 结果真正的手环从头到尾连不上，日志一直刷「手环掉线，重连」。
      // （那个设备在回调触发时名字还是空的 —— 扫描响应还没到，
      //   所以「按服务 UUID 匹配」这条路本身就不可靠。）
      if (name.length() == 0) return;        // 名字还没拿到，等下一个回调
      bool byName = name.startsWith("USER") || name.startsWith("HONOR") ||
                    name.indexOf("Band") >= 0 || name.indexOf("band") >= 0 ||
                    name.startsWith("华为");
      if (!byName) return;
      BLEAddress a = dev.getAddress();
      memcpy(g_bandAddr, a.getNative(), 6);
      g_bandType = dev.getAddressType();
      g_haveBandAddr = true;
      g_bandAddrStr = a.toString().c_str();
      g_bandName = name;
      logLine("发现手环广播: " + name + "  " + g_bandAddrStr +
              "  addrType=" + String((int)g_bandType));
      BLEDevice::getScan()->stop();
    }
  }
};

static void scanConfigureOnce() {
  static bool done = false;
  if (done) return;
  BLEScan* scan = BLEDevice::getScan();
  scan->setAdvertisedDeviceCallbacks(new DualScanCB(), false);   // ← 只调一次
  scan->setActiveScan(true);
  scan->setInterval(100);
  scan->setWindow(99);
  done = true;
}

static void scanFor(int mode, uint32_t seconds) {
  scanConfigureOnce();
  g_scanMode = mode;
  BLEScan* scan = BLEDevice::getScan();
  scan->start(seconds, false);
  scan->clearResults();
  g_scanMode = SCAN_IDLE;
}

// ------------------------------------------------------------------ 连接：玩具
static bool connectToy(uint32_t scanSeconds = 8) {
  if (g_connected) return true;
  if (!g_haveAddr) {
    logLine("扫描玩具 ...");
    scanFor(SCAN_TOY, scanSeconds);
  }
  if (!g_haveAddr) { logLine("没扫到玩具（开机了？被别的 App 占着？）"); return false; }

  logLine("连接玩具 " + g_devAddr + "  (addrType=" + String((int)g_bdType) +
          " free heap=" + String(ESP.getFreeHeap()) + ")");
  if (!g_client) g_client = BLEDevice::createClient();
  BLEAddress target(g_bdAddr);
  if (!g_client->connect(target, g_bdType)) { logLine("玩具连接失败"); return false; }

  BLERemoteService* svc = g_client->getService(SVC_UUID);
  if (!svc) { logLine("玩具找不到 FFE0"); g_client->disconnect(); return false; }

  g_chr = svc->getCharacteristic(CHR_UUID);
  if (!g_chr) { logLine("玩具找不到 FFE1"); g_client->disconnect(); return false; }

  if (g_chr->canNotify()) g_chr->registerForNotify(notifyCallback);
  g_connected = true;
  logLine("玩具已连接（写特征 FFE1 就绪）");
  return true;
}

// ------------------------------------------------------------------ 连接：手环（标准 HRS）
static bool connectBand(uint32_t scanSeconds = 10) {
  if (g_bandConnected) return true;
  if (!g_haveBandAddr) {
    logLine("扫描手环（心率广播）...");
    scanFor(SCAN_BAND, scanSeconds);
  }

  if (!g_haveBandAddr) {
    logLine("没扫到心率广播（手环要开「心率广播」，且没被手机占着）");
    return false;
  }

  logLine("连接手环 " + g_bandAddrStr + "  (addrType=" + String((int)g_bandType) +
          " free heap=" + String(ESP.getFreeHeap()) + ")");
  if (!g_bandClient) g_bandClient = BLEDevice::createClient();
  BLEAddress target(g_bandAddr);
  if (!g_bandClient->connect(target, g_bandType)) {
    // 连不上就把缓存地址丢掉，下一轮重新扫描。
    // 华为手环用的是会轮换的随机地址：地址一变，缓存的那个就成了死地址，
    // 而重连逻辑「有缓存就跳过扫描直接连」—— 于是每 30 秒失败一次，
    // 永远连不上，只能等控制台 90 秒的看门狗发 BSCAN 来清缓存。
    // 实测踩到过：连续失败约 90 秒，BSCAN 后 1 秒就连上了。
    logLine("手环连接失败（地址可能已轮换，下轮重扫）");
    g_haveBandAddr = false;
    return false;
  }

  BLERemoteService* svc = g_bandClient->getService(HRS_SVC);
  if (!svc) { logLine("手环没有 0x180D 服务"); g_bandClient->disconnect(); return false; }

  g_bandChr = svc->getCharacteristic(HRM_CHR);
  if (!g_bandChr) { logLine("手环没有 0x2A37 特征"); g_bandClient->disconnect(); return false; }

  if (g_bandChr->canNotify()) g_bandChr->registerForNotify(hrNotify);
  g_bandConnected = true;
  logLine("手环已订阅心率 0x2A37（等第一条读数）");
  return true;
}
// ------------------------------------------------------------------ 解析
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

static void doStop() {
  g_running = false;
  uint8_t z6[5] = {0, 0, 0, 0, 0};
  sendFrame(0x06, z6, 5);
  delay(120);
  uint8_t z8[1] = {0};
  sendFrame(0x08, z8, 1);
}

static void printHr() {
  logLine("HR状态 当前=" + String(g_hr) + " bpm  手环连接=" + String(g_bandConnected ? 1 : 0) +
          "  设备=" + g_bandName + " " + g_bandAddrStr +
          "  读数=" + String(g_hrCount) +
          "  最近更新=" + String(g_hrLastMs ? (millis() - g_hrLastMs) / 1000 : -1) + "秒前");
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
    String hzS = "";
    if (sp3 >= 0) { hzS = payS.substring(sp3 + 1); payS = payS.substring(0, sp3); }
    g_cmd = (uint8_t)parseIntSmart(cmdS);
    parsePayload(payS);
    if (hzS.length()) { float hz = hzS.toFloat(); g_periodMs = (hz > 0) ? max(20, (int)(1000.0 / hz)) : 100; }
    g_running = true;
    logLine("SET cmd=0x" + String(g_cmd, HEX) + " payload=[" + payS + "] period=" + String(g_periodMs) + "ms");
  } else if (op == "STOP") {
    doStop();
    logLine("STOP（已下发全 0 帧）");
  } else if (op == "STATE") {
    logLine("running=" + String(g_running ? "true" : "false") +
            " cmd=0x" + String(g_cmd, HEX) + " plen=" + String(g_plen) +
            " conn=" + String(g_connected ? 1 : 0) + " sent=" + String(g_sent) +
            " | band=" + String(g_bandConnected ? 1 : 0) + " hr=" + String(g_hr));
  } else if (op == "HR") {
    printHr();
  } else if (op == "SCAN") {
    g_connected = false;
    g_chr = nullptr;
    g_client = nullptr;
    connectToy(8);
  } else if (op == "BSCAN") {
    g_bandConnected = false;
    g_bandChr = nullptr;
    g_bandClient = nullptr;
    g_haveBandAddr = false;
    connectBand(10);
  } else if (op == "NLOG") {
    // 打开/关闭「回传全量记录」—— 抓按键上报时用
    g_notifyLogAll = (rest == "1" || rest == "on" || rest == "ON" || rest == "true");
    logLine("回传全量记录 = " + String(g_notifyLogAll ? 1 : 0));
  } else if (op == "TDIS") {
    // 让出玩具：断开连接**并且停止自动重连**，好让手机 App 接管
    g_toyHold = true;
    doStop();
    if (g_client) { g_client->disconnect(); }
    g_connected = false;
    g_chr = nullptr;
    logLine("已让出玩具（断开 + 暂停自动重连）—— 现在手机 App 可以连了。发 TRES 收回。");
  } else if (op == "TRES") {
    // 收回玩具
    g_toyHold = false;
    g_haveAddr = false;          // 地址可能变了，重扫
    logLine("收回玩具，重新扫描连接 ...");
    connectToy(8);
  } else if (op == "SPOOF") {
    // 冒充玩具，让手机 App 连上来 —— 抓 App 真正发的字节
    startSpoof(rest);
  } else if (op == "SPOOFOFF") {
    stopSpoof();
  } else if (op == "ADV") {
    // 把附近 mizzzee 广播的完整内容打出来（冒充前必须照着抄）
    uint32_t sec = rest.length() ? (uint32_t)rest.toInt() : 8;
    if (sec < 2) sec = 2;
    if (sec > 30) sec = 30;
    logLine("扫广播 " + String(sec) + " 秒，找 mizzzee ...");
    scanFor(SCAN_ADV, sec);
    logLine("扫广播结束");
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
  s += "],\"connected\":" + String(g_connected ? "true" : "false") +
       ",\"sent\":" + String(g_sent) +
       ",\"band_connected\":" + String(g_bandConnected ? "true" : "false") +
       ",\"hr\":" + String(g_hr) +
       ",\"hr_age_ms\":" + String(g_hrLastMs ? (millis() - g_hrLastMs) : -1) + "}";
  return s;
}

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
  while (i < (int)b.length() && b[i] == ' ') i++;
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
  http.on("/api/hr", HTTP_GET, []() {
    http.send(200, "application/json",
              "{\"hr\":" + String(g_hr) + ",\"connected\":" + String(g_bandConnected ? "true" : "false") +
              ",\"age_ms\":" + String(g_hrLastMs ? (millis() - g_hrLastMs) : -1) +
              ",\"history\":\"" + g_hrHistory + "\"}");
  });
  http.on("/api/set", HTTP_POST, []() {
    String body = http.arg("plain");
    if (body.indexOf("\"running\"") >= 0) g_running = jsonBool(body, "running");
    if (body.indexOf("\"cmd\"") >= 0) g_cmd = (uint8_t)jsonNum(body, "cmd", g_cmd);
    uint8_t tmp[16];
    size_t n = 0;
    if (jsonPayload(body, tmp, n)) { memcpy(g_payload, tmp, n); g_plen = n; }
    if (body.indexOf("\"hz\"") >= 0) {
      float hz = jsonNum(body, "hz", 10);
      g_periodMs = max(20, (int)(1000.0 / (hz <= 0 ? 10 : hz)));
    }
    logLine("HTTP /api/set " + body);
    http.send(200, "application/json", jsonState());
  });
  http.on("/api/stop", HTTP_POST, []() {
    doStop();
    http.send(200, "application/json", jsonState());
  });
  http.on("/api/log", HTTP_GET, []() { http.send(200, "text/plain; charset=utf-8", g_logBuf); });
  http.on("/", HTTP_GET, []() {
    http.send(200, "text/plain; charset=utf-8", "toy+hr bridge\n" + jsonState() + "\n");
  });
  http.begin();
  logLine("HTTP 服务: http://" + WiFi.localIP().toString() + ":8080");
}
#endif

// ------------------------------------------------------------------ 生命周期
void setup() {
  Serial.begin(115200);                 // USB CDC（原生 USB 口）
#if HAS_SECOND_SERIAL
  Serial0.begin(115200);                // UART0 → CH343（UART 口）
#endif
  delay(600);
  outBoth("");
  logLine("=== 计算者-X 网关 + 心率监听 启动 (ESP32-S3) ===");
#if HAS_SECOND_SERIAL
  logLine("日志同时输出到 UART 口 和 原生 USB 口 —— 插哪个都能用");
#endif

  BLEDevice::init("toy-bridge");
  connectToy(8);
  connectBand(10);

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
  logLine("就绪。串口命令: SET / STOP / STATE / HR / SCAN / BSCAN / RAW");
}

// 从某个串口读一行命令
static void feedChar(char c) {
  if (c == '\n' || c == '\r') {
    if (g_serialBuf.length()) { handleSerialLine(g_serialBuf); g_serialBuf = ""; }
  } else {
    g_serialBuf += c;
    if (g_serialBuf.length() > 200) g_serialBuf = "";
  }
}

void loop() {
  // 两个口都收命令 —— 插哪个口都能控
  while (Serial.available()) feedChar((char)Serial.read());
#if HAS_SECOND_SERIAL
  while (Serial0.available()) feedChar((char)Serial0.read());
#endif

#if USE_WIFI
  if (WiFi.status() == WL_CONNECTED) http.handleClient();
#endif

  // ---- 玩具掉线重连 ----
  //   g_toyHold = true 时完全不碰玩具 —— 这是「让出设备给手机 App」用的。
  //   否则面板的自动重连会和手机抢连接，两边都连不稳。
  if (!g_toyHold && (!g_connected || (g_client && !g_client->isConnected()))) {
    g_connected = false;
    g_chr = nullptr;
    static uint32_t lastTry = 0;
    if (millis() - lastTry > 5000) {
      lastTry = millis();
      logLine("玩具掉线，重连 ...");
      connectToy(6);
    }
  }

  // ---- 手环掉线重连（间隔拉长，避免打断玩具；地址已缓存时直接重连不重扫）----
  if (!g_bandConnected || (g_bandClient && !g_bandClient->isConnected())) {
    g_bandConnected = false;
    g_bandChr = nullptr;
    static uint32_t lastBandTry = 0;
    if (millis() - lastBandTry > 30000) {
      lastBandTry = millis();
      logLine("手环掉线，重连 ...");
      connectBand(8);
    }
  }

  if (g_running && g_connected && g_chr && (millis() - g_lastSend >= g_periodMs)) {
    g_lastSend = millis();
    sendFrame(g_cmd, g_payload, g_plen);
  }

  delay(2);
}
