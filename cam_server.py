"""cam_server.py —— 手机摄像头桥（跨平台）

参考项目用的是 Node.js + iPhone Safari。这里用 Python + 任意手机浏览器。

为什么是浏览器而不是 App：
  · OPPO / 华为 / 苹果 / 任何安卓 —— 打开一个网址就能用，不用装东西
  · 不用适配机型，不用签名，不用上架
  · 换手机不用改任何代码

⚠ 关于 HTTPS（这是唯一的坑）：
  浏览器的 getUserMedia（连续取流）**只允许在安全源里用**。
  http://192.168.x.x 不是安全源 → 摄像头会被直接封掉，控制台报
  "NotAllowedError" 或 "Cannot use camera"。

  两条路：
    ① 简单模式（默认，零配置）
       <input type="file" accept="image/*" capture="environment">
       这个会调起系统相机 App，**在 http 下也能用**。
       代价：拍一张要点一下，不是连续的。
    ② 连续模式（需要 HTTPS）
       本脚本会生成自签名证书，用 https 起服务。
       手机上首次访问点"继续访问"即可。

  建议先跑简单模式确认链路通，再决定要不要上 HTTPS。

用法：
    python cam_server.py                 # http://0.0.0.0:8099
    python cam_server.py --https         # 自签名 HTTPS（连续模式需要）
    python cam_server.py --port 8099
"""
from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import os
import re
import socket
import ssl
import sys
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# ⚠ 不要强制 UTF-8 —— bat 用的是 chcp 936，强制 utf-8 会让中文乱码。
#   只设 errors，encoding 跟随环境。
try:
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")
except Exception:
    pass

BASE = Path(__file__).resolve().parent
SHOTS = BASE / "captures"
LATEST_JPG = SHOTS / "latest.jpg"
LATEST_JSON = SHOTS / "latest.json"
MAX_KEEP = 40                       # 最多留这么多张历史，防止无限涨
RING = SHOTS / "ring"               # 帧池：最近 N 张，给 vision 挑最好的
RING_N = 24
_ring_seq = 0

LOCK = threading.Lock()
STATS = {"frames": 0, "last_at": 0.0, "bytes": 0, "client": "-"}


# ---------------------------------------------------------------- 上传处理
def save_frame(raw: bytes, client: str, meta: dict | None = None) -> dict:
    """存一帧。同时覆写 latest.jpg / latest.json，并写进帧池。

    帧池的用处：手机息屏、手抖、自动对焦拉风箱的时候会有废帧。
    只留最新一张的话，废帧一来这段时间就白费了。
    留最近 RING_N 张，vision.py 就能从里面挑最清楚的一张。
    """
    global _ring_seq
    SHOTS.mkdir(exist_ok=True)
    now = time.time()

    with LOCK:
        LATEST_JPG.write_bytes(raw)

        # ---- 帧池 ----
        try:
            RING.mkdir(parents=True, exist_ok=True)
            _ring_seq += 1
            (RING / f"{int(now * 1000)}_{_ring_seq % 100000:05d}.jpg"
             ).write_bytes(raw)
            old = sorted(RING.glob("*.jpg"), key=lambda p: p.stat().st_mtime)
            for p in old[:-RING_N]:
                try:
                    p.unlink()
                except OSError:
                    pass
        except OSError:
            pass
        info = {
            "at": datetime.now().isoformat(timespec="seconds"),
            "ts": now,
            "bytes": len(raw),
            "client": client,
            **(meta or {}),
        }
        LATEST_JSON.write_text(json.dumps(info, ensure_ascii=False, indent=2),
                               encoding="utf-8")
        STATS.update(frames=STATS["frames"] + 1, last_at=now,
                     bytes=STATS["bytes"] + len(raw), client=client)

        # 留一份带时间戳的（按需，避免刷爆磁盘）
        keep = os.environ.get("CAM_KEEP", "0") == "1"
        if keep:
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            (SHOTS / f"{stamp}.jpg").write_bytes(raw)
            old = sorted(SHOTS.glob("2*.jpg"))
            for f in old[:-MAX_KEEP]:
                try:
                    f.unlink()
                except OSError:
                    pass

    return info


# ---------------------------------------------------------------- 手机页面
PAGE = r"""<!doctype html>
<html lang="zh"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>手机摄像头 · 上传到电脑</title>
<style>
 :root{color-scheme:dark}
 body{margin:0;padding:14px;font-family:system-ui,"PingFang SC",sans-serif;
      background:#0f1116;color:#e8eaf0;-webkit-text-size-adjust:100%}
 h1{font-size:18px;margin:0 0 10px}
 .card{background:#171b26;border:1px solid #232838;border-radius:14px;
       padding:14px;margin-bottom:12px}
 video,img.prev{width:100%;border-radius:10px;background:#000;display:block}
 img.prev{object-fit:contain;max-height:44vh}
 .row{display:flex;align-items:center;gap:10px;margin:9px 0;font-size:15px}
 .row label{flex:0 0 74px;color:#9aa3b2}
 .row input[type=range]{flex:1;height:30px}
 .row output{width:52px;text-align:right;font-family:ui-monospace,monospace}
 button{font-size:16px;padding:11px 16px;border-radius:11px;border:0;
        background:#2a3145;color:#e8eaf0;margin:4px 6px 4px 0}
 button.go{background:#2f7d4f}
 button.stop{background:#8a3040}
 #st{font-family:ui-monospace,monospace;font-size:13px;color:#8ad;
     word-break:break-all;line-height:1.6}
 .big{font-size:17px;font-weight:700}
 .hint{color:#9aa3b2;font-size:13px;line-height:1.6}
 .warn{background:#3a2418;border-color:#5a3a20}
</style></head><body>

<h1>手机摄像头 → 电脑</h1>

<div class="card warn" id="httpsWarn" style="display:none">
  <div class="big">⚠ 当前不是 HTTPS</div>
  <div class="hint">连续拍摄需要 HTTPS（浏览器的安全限制）。
    下面「拍一张」按钮仍然可用 —— 它会调起你手机的系统相机。</div>
</div>

<div class="card">
  <div class="big" id="mode">正在检查摄像头 …</div>
  <!-- 诊断行：一眼看出 flags 有没有生效 -->
  <div class="hint" id="diag" style="font-family:ui-monospace,Consolas,monospace;
       font-size:12px;opacity:.75"></div>
  <div class="hint" id="modeHint">
    正在准备 …
  </div>
  <div style="margin-top:12px">
    <button class="go" onclick="pickShot()">📷 拍一张并上传</button>
    <button onclick="pickBatch()" style="background:#2b6cb0;color:#fff">
      🖼 批量上传（相册全选）</button>
    <button onclick="switchMode()" id="btnMode">切到连续拍摄</button>
  </div>
  <input type="file" id="file" accept="image/*" capture="environment"
         style="display:none" onchange="onPick(event)">
  <input type="file" id="fileBatch" accept="image/*" multiple
       style="display:none" onchange="onBatch(event)">
<input type="file" id="fileLib" accept="image/*"
         style="display:none" onchange="onPick(event)">
</div>

<div class="card" id="liveCard" style="display:none">
  <video id="v" playsinline autoplay muted></video>
  <div class="row" style="margin-top:12px">
    <button onclick="flip()" id="btnFlip">切换前后摄像头</button>
    <button class="stop" onclick="stopLive()">停止连续</button>
  </div>
  <div class="row"><label>间隔</label>
    <input type="range" id="iv" min="1500" max="15000" step="500" value="4000"
           oninput="ivV.value=(this.value/1000).toFixed(1)+'s'">
    <output id="ivV">3.5s</output></div>
  <div class="row"><label>画质</label>
    <input type="range" id="q" min="30" max="95" step="1" value="88"
           oninput="qV.value=this.value">
    <output id="qV">72</output></div>
  <div class="row"><label>宽度</label>
    <input type="range" id="w" min="320" max="1600" step="160" value="1280"
           oninput="wV.value=this.value">
    <output id="wV">960</output></div>
  <canvas id="cv" style="display:none"></canvas>
</div>

<div class="card">
  <div class="big">最近上传</div>
  <img class="prev" id="prev" alt="（还没收到画面）">
  <div id="st" style="margin-top:10px">等待…</div>
</div>

<script>
const $ = id => document.getElementById(id);
// localhost 也算安全源 —— 电脑上走 http://127.0.0.1 也能开摄像头
const isSecure = location.protocol === 'https:' ||
                 location.hostname === 'localhost' ||
                 location.hostname === '127.0.0.1';
let live = false, timer = null, facing = 'environment', stream = null;

if (!isSecure) $('httpsWarn').style.display = 'block';

// ---------- 模式一：拍一张（http 也能用）----------
function pickShot(){ $('file').click(); }

function onPick(e){
  const f = e.target.files && e.target.files[0];
  if (!f) return;
  const img = new Image();
  img.onload = () => {
    // 压到合适尺寸再传，省流量也省电
    const W = +$('w').value, ratio = img.height / img.width;
    const cv = $('cv'); cv.width = Math.min(W, img.width);
    cv.height = Math.round(cv.width * ratio);
    cv.getContext('2d').drawImage(img, 0, 0, cv.width, cv.height);
    cv.toBlob(b => b && upload(b, 'single'), 'image/jpeg', +$('q').value / 100);
    URL.revokeObjectURL(img.src);
  };
  img.src = URL.createObjectURL(f);
  e.target.value = '';       // 允许连续选同一张
}

// ---------- 批量上传（相册多选）----------
// 这个在 HTTP 下也能用，而且是手机原生相机的画质。
function pickBatch(){ $('fileBatch').click(); }

async function onBatch(e){
  const files = [...(e.target.files || [])];
  if (!files.length) return;
  shotCount = 0;
  $('mode').textContent = '批量上传中 … 0/' + files.length;

  for (let i = 0; i < files.length; i++){
    try {
      const blob = await shrink(files[i]);
      await upload(blob, 'batch');
      shotCount++;
      $('mode').textContent =
        '批量上传中 … ' + (i + 1) + '/' + files.length;
    } catch (err) {
      console.warn('跳过一张', err);
    }
  }
  $('mode').textContent = '批量上传完成（' + files.length + ' 张）';
  $('modeHint').textContent = '已全部传到电脑。手机可以继续拍，拍完再选一批。';
  refresh();
  e.target.value = '';
}

// 压到设定尺寸再传 —— 省流量也省手机电
function shrink(file){
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => {
      const W = +$('w').value, ratio = img.height / img.width;
      const cv = $('cv');
      cv.width  = Math.min(W, img.width);
      cv.height = Math.round(cv.width * ratio);
      cv.getContext('2d').drawImage(img, 0, 0, cv.width, cv.height);
      cv.toBlob(b => b ? resolve(b) : reject(new Error('toBlob 失败')),
                'image/jpeg', +$('q').value / 100);
      URL.revokeObjectURL(img.src);
    };
    img.onerror = () => reject(new Error('图片读不了'));
    img.src = URL.createObjectURL(file);
  });
}

// ---------- 模式二：连续（需要 HTTPS）----------
async function startLive(){
  if (!isSecure){ alert('连续拍摄需要 HTTPS。\n先切回来用「拍一张」，或者用 --https 重启服务。'); return; }
  try {
    stream = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: facing, width: {ideal: 1280} }, audio: false });
  } catch (err) {
    alert('打不开摄像头：' + err.name + '\n' + err.message +
          '\n\n如果是在 http 下访问，浏览器不允许连续取流 —— 用「拍一张」模式。');
    return;
  }
  const v = $('v'); v.srcObject = stream;
  live = true;
  $('liveCard').style.display = 'block';
  $('mode').textContent = '模式：连续拍摄';
  $('btnMode').textContent = '切到拍一张';
  $('modeHint').textContent = '每隔几秒自动拍一张并上传。保持这个页面在前台。';
  tick();
}

function tick(){
  if (!live) return;
  const v = $('v');
  if (v.videoWidth) {
    const cv = $('cv');
    const W = Math.min(+$('w').value, v.videoWidth);
    cv.width = W; cv.height = Math.round(W * v.videoHeight / v.videoWidth);
    cv.getContext('2d').drawImage(v, 0, 0, cv.width, cv.height);
    cv.toBlob(b => b && upload(b, 'live'), 'image/jpeg', +$('q').value / 100);
  }
  timer = setTimeout(tick, +$('iv').value);
}

function stopLive(){
  live = false;
  if (timer) clearTimeout(timer);
  if (stream) stream.getTracks().forEach(t => t.stop());
  stream = null;
  $('liveCard').style.display = 'none';
  $('mode').textContent = '模式：拍一张';
  $('btnMode').textContent = '切到连续拍摄';
  $('modeHint').textContent = '点下面的按钮 → 手机会打开相机 → 拍完自动上传。';
}

function flip(){
  facing = facing === 'environment' ? 'user' : 'environment';
  if (live){ stopLive(); startLive(); }
}

function switchMode(){
  if (live) stopLive();
  else startLive();
}

// 也可以从相册选（有时候更方便）
document.addEventListener('keydown', e => {
  if (e.key === 'l') $('fileLib').click();
});

// ---------- 打开就自动连拍 ----------
// 打开网址就，不用点任何按钮。
// 非 HTTPS 时浏览器不给 getUserMedia，自动退回「拍一张」。
let shotCount = 0;

async function autoStart(){
  // 先做个诊断，让人一眼看出 flags 有没有生效
  const hasGUM = !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia);
  const diag = '安全上下文=' + (isSecure ? '是' : '否') +
               '　摄像头接口=' + (hasGUM ? '有' : '无');
  $('diag').textContent = diag;

  if (!isSecure || !hasGUM){
    $('mode').textContent = '模式：拍一张';
    // 认出浏览器，给对应的 flags 地址
    const ua = navigator.userAgent;
    let flagBase = 'chrome://flags';
    let bname = 'Chrome';
    if (/EdgA|Edg\//.test(ua))            { flagBase = 'edge://flags';    bname = 'Edge'; }
    else if (/OPR|Opera/.test(ua))        { flagBase = 'opera://flags';   bname = 'Opera'; }
    else if (/Firefox|FxiOS/.test(ua))    { flagBase = 'about:config';    bname = 'Firefox'; }
    else if (/SamsungBrowser/.test(ua))   { flagBase = 'chrome://flags';  bname = '三星浏览器'; }

    let extra = '';
    if (!isSecure && hasGUM){
      extra = '<br><br><b style="color:#ffb35d">你的开关好像还没生效。</b>' +
        '检查三点：① 填的是不是正好 <code>' + location.origin + '</code>（不要多斜杠）；' +
        '② 有没有点底部 <b>Relaunch</b> 重启浏览器；' +
        '③ 重启后是不是重新打开了本页。';
    }
    const isFF = bname === 'Firefox';
    const certUrl = 'https://' + location.hostname + ':8099/';
    // 排版顺序按「能拿到实时画面的可能性」排：
    //   ① 装证书 → 实时连拍（最好）
    //   ② 浏览器开关 → 实时连拍（不装证书时的办法）
    //   ③ 批量上传 / 拍一张 → 兜底，随时能用
    $('modeHint').innerHTML =

      '<div style="padding:12px;border-radius:8px;background:rgba(43,108,176,.18);' +
      'border-left:3px solid #6ba3e8;margin-bottom:14px;text-align:left">' +
      '<b style="color:#9ecbff">① 想要实时连拍 —— 装一次证书</b><br>' +
      '<span style="font-size:13px;opacity:.9">装完之后打开 ' +
      '<code>' + certUrl + '</code> 就没有警告，摄像头自动开，' +
      '每几秒一张。以后换浏览器也不用再设置。</span><br>' +

      '<a href="/cert.p12" style="display:inline-block;margin:10px 6px 4px 0;' +
      'padding:12px 20px;background:#2b6cb0;color:#fff;border-radius:8px;' +
      'text-decoration:none;font-weight:700;font-size:15px">' +
      '⬇ 下载证书（.p12）</a>' +
      '<a href="/cert" style="display:inline-block;margin:10px 0 4px;' +
      'padding:10px 14px;background:#4a5568;color:#fff;border-radius:8px;' +
      'text-decoration:none;font-size:13px">.crt 备用</a><br>' +

      '<span style="font-size:12px;opacity:.85;line-height:1.7">' +
      '<b>OPPO / 一加 / realme 用 .p12</b>（ColorOS 要私钥，纯 .crt 会报错）；' +
      '小米 / 华为 / 原生安卓 两个都行。<br>' +
      '装法：设置 → 搜「<b>证书</b>」或「凭据」→ 从存储设备安装 → ' +
      '<b>CA 证书</b> → 选文件 → <b>密码留空直接确定</b> → ' +
      '提示风险时选「仍然安装」。<br>' +
      '装完重开浏览器，打开 <code>' + certUrl + '</code>。' +
      '</span></div>' +

      (isFF ? '' :
       '<div style="padding:12px;border-radius:8px;background:rgba(255,255,255,.05);' +
       'margin-bottom:14px;text-align:left">' +
       '<b style="color:#ffd479">② 不想装证书 —— 改 ' + bname + ' 的开关</b><br>' +
       '<span style="font-size:13px;opacity:.9">' +
       '① 地址栏输 <code>' + flagBase +
       '/#unsafely-treat-insecure-origin-as-secure</code><br>' +
       '② 输入框填 <code>' + location.origin + '</code>，下拉选 <b>Enabled</b><br>' +
       '③ 点底部 <b>Relaunch</b> 重启浏览器，再回来打开本页</span>' +
       (extra || '') + '</div>') +

      '<div style="padding:12px;border-radius:8px;background:rgba(255,255,255,.05);' +
      'text-align:left">' +
      '<b style="color:#7ee0a0">③ 上面都不行 —— 直接用下面的按钮</b><br>' +
      '<span style="font-size:13px;opacity:.9">' +
      '「🖼 批量上传」：用手机自带相机拍几张（画质最好），' +
      '然后点它 → 相册全选 → 一次全传上来。<br>' +
      '「📷 拍一张」：点一下拍一张，即时上传。</span></div>';
    return;
  }
  $('mode').textContent = '正在启动摄像头 …';
  await startLive();
  if (live){
    $('mode').textContent = '模式：自动连拍中';
    $('modeHint').textContent =
      '每 ' + (+$('iv').value / 1000).toFixed(1) + ' 秒自动拍一张并上传。' +
      '保持这个页面在前台。';
  }
}
autoStart();

// ---------- 上传 ----------
async function upload(blob, src){
  try {
    const r = await fetch('/api/cam', {
      method: 'POST',
      headers: {'Content-Type': 'image/jpeg', 'X-Source': src},
      body: blob
    });
    const j = await r.json();
    if (j.ok){
      shotCount++;
      $('mode').textContent = '模式：自动连拍中（已传 ' + shotCount + ' 张）';
      refresh();
    }
  } catch (err) {
    $('st').textContent = '上传失败：' + err.message;
  }
}

async function refresh(){
  try {
    const s = await (await fetch('/api/cam/status')).json();
    $('st').textContent =
      '已收到 ' + s.frames + ' 张　最后 ' + s.age_s + ' 秒前　' +
      Math.round(s.bytes / 1024) + ' KB　来自 ' + s.client;
    $('prev').src = '/api/cam/latest?t=' + Date.now();
    $('prev').style.display = 'block';
  } catch (e) {}
}
setInterval(refresh, 2000);
refresh();
</script></body></html>
"""


# ---------------------------------------------------------------- HTTP
class CamHandler(BaseHTTPRequestHandler):
    server_version = "CamBridge/1.0"

    def log_message(self, fmt, *args):
        pass                                  # 别刷屏

    # ---- 工具 ----
    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, obj, code: int = 200) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False).encode(), "application/json")

    # ---- GET ----
    def do_GET(self):  # noqa: N802
        path = self.path.split("?", 1)[0]

        if path in ("/", "/cam", "/camera"):
            self._send(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")
            return

        if path in ("/cert.p12", "/cam-bridge.p12", "/cert-p12"):
            # OPPO / ColorOS 等要 p12（证书 + 私钥）才能装 CA
            from pathlib import Path as _P
            f = _P(__file__).parent / "certs" / "cam-bridge.p12"
            if not f.exists():
                self._send(404, b"no p12", "text/plain")
                return
            data = f.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "application/x-pkcs12")
            self.send_header("Content-Disposition",
                             'attachment; filename="cam-bridge.p12"')
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return

        if path in ("/cert", "/cert.crt"):
            # 证书下载 —— 手机拿去装成「受信任的 CA 凭据」
            from pathlib import Path as _P
            f = _P(__file__).parent / "certs" / "cert.pem"
            if not f.exists():
                self._send(404, b"no cert", "text/plain")
                return
            data = f.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type",
                             "application/x-x509-ca-cert")
            self.send_header("Content-Disposition",
                             'attachment; filename="cam-bridge.crt"')
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return

        if path == "/cert-info":
            import json as _j
            from pathlib import Path as _P
            from cryptography import x509 as _x
            f = _P(__file__).parent / "certs" / "cert.pem"
            if not f.exists():
                self._send(404, b"{}", "application/json")
                return
            c = _x.load_pem_x509_certificate(f.read_bytes())
            ext = c.extensions.get_extension_for_class(
                _x.SubjectAlternativeName).value
            info = {
                "subject": c.subject.rfc4514_string(),
                "not_before": str(c.not_valid_before_utc.date()),
                "not_after": str(c.not_valid_after_utc.date()),
                "san_ip": [str(v) for v in
                           ext.get_values_for_type(_x.IPAddress)],
                "san_dns": [str(v) for v in
                            ext.get_values_for_type(_x.DNSName)],
            }
            self._send(200, _j.dumps(info, ensure_ascii=False).encode(),
                       "application/json")
            return

        if path == "/api/cam/status":
            with LOCK:
                age = (time.time() - STATS["last_at"]) if STATS["last_at"] else -1
                self._json({
                    "ok": True,
                    "frames": STATS["frames"],
                    "age_s": round(age, 1) if age >= 0 else None,
                    "bytes": STATS["bytes"],
                    "client": STATS["client"],
                    "has_latest": LATEST_JPG.exists(),
                })
            return

        if path == "/api/cam/latest":
            if LATEST_JPG.exists():
                self._send(200, LATEST_JPG.read_bytes(), "image/jpeg")
            else:
                self._json({"ok": False, "error": "还没有收到画面"}, 404)
            return

        if path == "/api/cam/meta":
            if LATEST_JSON.exists():
                self._json(json.loads(LATEST_JSON.read_text(encoding="utf-8")))
            else:
                self._json({"ok": False}, 404)
            return

        self._json({"ok": False, "error": "not found"}, 404)

    # ---- POST ----
    def do_POST(self):  # noqa: N802
        path = self.path.split("?", 1)[0]

        if path == "/api/cam":
            try:
                n = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                n = 0
            if n <= 0 or n > 20 * 1024 * 1024:
                self._json({"ok": False, "error": "空的或者太大（限 20MB）"}, 400)
                return
            raw = self.rfile.read(n)

            # 粗略校验是不是 JPEG/PNG
            if not (raw[:2] == b"\xff\xd8" or raw[:8] == b"\x89PNG\r\n\x1a\n"):
                self._json({"ok": False, "error": "不是 JPEG/PNG"}, 400)
                return

            ctype = (self.headers.get("Content-Type") or "").lower()
            info = save_frame(raw, self.client_address[0],
                              {"source": self.headers.get("X-Source", "?"),
                               "content_type": ctype})
            self._json({"ok": True, "bytes": info["bytes"],
                        "frames": STATS["frames"]})
            return

        self._json({"ok": False, "error": "not found"}, 404)


# ---------------------------------------------------------------- 证书
def ensure_cert() -> tuple[Path, Path]:
    """生成自签名证书（连续拍摄需要 HTTPS）。已存在就直接用。"""
    cert_dir = BASE / "certs"
    cert_dir.mkdir(exist_ok=True)
    key, cert = cert_dir / "key.pem", cert_dir / "cert.pem"
    if key.exists() and cert.exists():
        return cert, key

    print("  生成自签名证书 ...")
    # 用 Python 自带的 cryptography 生成（如果没装就退回 openssl 命令行）
    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID
        import datetime as dt

        k = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        subj = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, u"cam-bridge")])
        # 把所有本机 IP 都放进 SAN —— 否则手机上会报证书不匹配
        ips = {u"127.0.0.1"}
        try:
            for info in socket.getaddrinfo(socket.gethostname(), None):
                ips.add(info[4][0])
        except OSError:
            pass
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ips.add(s.getsockname()[0])
            s.close()
        except OSError:
            pass
        san = x509.SubjectAlternativeName(
            [x509.DNSName(u"localhost")]
            + [x509.IPAddress(__import__("ipaddress").ip_address(i))
               for i in ips if ":" not in i])
        now = dt.datetime.now(dt.timezone.utc)
        c = (x509.CertificateBuilder()
             .subject_name(subj).issuer_name(subj)
             .public_key(k.public_key())
             .serial_number(x509.random_serial_number())
             .not_valid_before(now - dt.timedelta(days=1))
             .not_valid_after(now + dt.timedelta(days=3650))
             .add_extension(san, critical=False)
             .sign(k, hashes.SHA256()))
        key.write_bytes(k.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption()))
        cert.write_bytes(c.public_bytes(serialization.Encoding.PEM))
        print(f"  ✓ 证书已生成（含 SAN: {', '.join(sorted(ips))}）")
    except ImportError:
        print("  没装 cryptography，改用 openssl ...")
        import subprocess
        subprocess.run(
            ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
             "-keyout", str(key), "-out", str(cert), "-days", "3650",
             "-subj", "/CN=cam-bridge",
             "-addext", "subjectAltName=DNS:localhost,IP:127.0.0.1"],
            check=True)
        print("  ✓ 证书已生成")

    return cert, key


# ---------------------------------------------------------------- 主入口
def local_ips() -> list[str]:
    ips = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ips.append(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None):
            ip = info[4][0]
            if ":" not in ip and ip not in ips:
                ips.append(ip)
    except OSError:
        pass
    return ips


def main() -> None:
    ap = argparse.ArgumentParser(description="手机摄像头桥")
    ap.add_argument("--port", type=int, default=8099)
    ap.add_argument("--https", action="store_true",
                    help="用自签名证书起 HTTPS（手机连拍必须）")
    ap.add_argument("--http-port", type=int, default=8098,
                    help="同时再开一个纯 HTTP 端口（电脑上走 localhost 用，"
                         "localhost 是安全源，不需要证书）")
    args = ap.parse_args()

    ips = local_ips()
    scheme = "https" if args.https else "http"

    print("=" * 62)
    print("   手机摄像头桥")
    print("=" * 62)
    print(f"   本机地址：")
    for ip in ips or ["<查不到，用 ipconfig 看>"]:
        print(f"     {scheme}://{ip}:{args.port}/")
    print(f">  手机浏览器打开上面任意一个（手机和电脑要在同一个 WiFi）")
    print(f"   画面会存到：{LATEST_JPG.relative_to(BASE)}")
    print()
    if args.https:
        print("   模式：HTTPS —— 连续拍摄可用")
    else:
        print("   模式：HTTP —— 用「拍一张」（调系统相机），零配置")
        print("   想要连续拍摄就加 --https")
    print("=" * 62)

    httpd = ThreadingHTTPServer(("0.0.0.0", args.port), CamHandler)

    if args.https:
        cert, key = ensure_cert()
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(certfile=str(cert), keyfile=str(key))
        httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)

    # ---- 顺带开一个纯 HTTP（电脑 localhost 用）----
    # 浏览器把 localhost 视为安全源，HTTP 下也能开摄像头 ——
    # 所以电脑上直接打开 http://127.0.0.1:8098/ 就能自动拍，
    # 不弹证书警告，也不受热点隔离影响。
    http_plain = None
    if args.https and args.http_port and args.http_port != args.port:
        try:
            http_plain = ThreadingHTTPServer(("0.0.0.0", args.http_port),
                                             CamHandler)
            threading.Thread(target=http_plain.serve_forever,
                             daemon=True).start()
            print(f"   电脑上打开（不用 HTTPS、不弹警告）：")
            print(f"     http://127.0.0.1:{args.http_port}/")
            print()
            print(f"  ⚠ 走 localhost 时浏览器算安全源，摄像头能直接开。")
        except OSError as exc:
            print(f"   [警告] HTTP 端口 {args.http_port} 开不了: {exc}")

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n  已停止")
    finally:
        httpd.server_close()
        if http_plain:
            http_plain.shutdown()
            http_plain.server_close()


if __name__ == "__main__":
    main()
