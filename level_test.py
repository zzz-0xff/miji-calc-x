#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""level_test.py —— 档位测试台（网页）

用来搞清楚「计算者-X」的伸缩 / 震动档位分别对应什么字节、什么手感。

为什么需要它
─────────────────────────────────────────────────────────────
从厂商 app-service.js 里挖出来的两件事互相矛盾：

  1. App 的档位表（权威定义）：
       Thrust（伸缩）   max:6
       Vibration（震动）max:10
  2. App 真正发出去的载荷（pages/play/... 第 847786 行附近）：
       r = [...this.sliderValues, 100]

     也就是**把滑条的原始档位值直接当字节发**，末尾追加一个 100。

而我们现在一直在发 0-100 的百分比，尾字节是 1。
两套编码不可能都对 —— 所以直接实测。

而且你反馈：**只有前三档是「强度递增」，第四档以后变成波形**
（一长一短 / 连震 / 伸缩三次停顿…）。这意味着那个字节是**模式索引**，
不是振幅百分比 —— 那么 0-100 的映射根本到不了模式区。

用法
─────────────────────────────────────────────────────────────
  1. 先开着控制台：  双击 点我-控制台.bat   （或 python toy_panel.py）
  2. 再开测试台：    python level_test.py
  3. 浏览器打开：    http://127.0.0.1:8091

页面里做三件事：
  · 手动：拖两个滑条 + 尾字节，随便组合，立刻发出去
  · 扫描：选一条轴（伸缩 / 震动），逐档自动跑，每档停下来等你评价
  · 记录：把每个档位的评价填进去，一键导出 JSON —— 之后就照那张表写死映射
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

PANEL = "http://127.0.0.1:8090"
HTTP_PORT = 8091

try:
    # 不要强制 UTF-8 —— bat 里是 chcp 936 + PYTHONIOENCODING=gbk，
    # 这里改成 utf-8 会让中文全乱。只设 errors，encoding 跟随环境。
    sys.stdout.reconfigure(errors="replace", line_buffering=True)
    sys.stderr.reconfigure(errors="replace")
except Exception:
    pass


def panel(path: str, payload: dict | None = None, timeout: float = 8.0) -> dict:
    url = PANEL + path
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        url, data=data, method="POST" if data is not None else "GET",
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        return {"ok": False, "error": f"HTTP {e.code}"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


PAGE = r"""<!doctype html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>计算者-X · 档位测试台</title>
<style>
  :root{
    --bg:#0f1420; --panel:#171d2c; --panel2:#1e2536; --line:#2b3347;
    --fg:#e6ebf5; --dim:#8494ad; --accent:#2d6cdf; --ok:#2fbf71;
    --warn:#e0a020; --bad:#d3455c; --big:#7aa2ff;
  }
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--fg);
       font:15px/1.6 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif}
  .wrap{max-width:1000px;margin:0 auto;padding:18px 16px 60px}
  h1{font-size:19px;margin:6px 0 4px}
  h2{font-size:16px;margin:22px 0 8px;padding-bottom:6px;border-bottom:1px solid var(--line)}
  .sub{color:var(--dim);font-size:13px;margin-bottom:14px}
  .card{background:var(--panel);border:1px solid var(--line);border-radius:12px;
        padding:14px 16px;margin:12px 0}
  .row{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin:8px 0}
  label{color:var(--dim);font-size:13px;min-width:66px}
  input[type=range]{flex:1;min-width:180px;accent-color:var(--accent);height:24px}
  input[type=number],input[type=text],select{
    background:var(--panel2);border:1px solid var(--line);color:var(--fg);
    border-radius:8px;padding:7px 9px;font:inherit}
  input[type=number]{width:78px}
  input[type=text]{width:100%}
  button{background:var(--accent);border:0;color:#fff;border-radius:9px;
         padding:9px 15px;font:inherit;cursor:pointer}
  button:active{filter:brightness(1.25)}
  button.ghost{background:var(--panel2);border:1px solid var(--line);color:var(--fg)}
  button.bad{background:var(--bad)}
  button.ok{background:var(--ok)}
  button.sm{padding:5px 9px;font-size:13px;border-radius:7px}
  button.sel{outline:2px solid var(--big)}
  .big{font-size:40px;font-weight:700;letter-spacing:1px;color:var(--big);
       text-align:center;line-height:1.2}
  .big small{display:block;font-size:13px;font-weight:400;color:var(--dim);letter-spacing:0}
  .hex{font-family:Consolas,Menlo,monospace;font-size:13px;color:var(--ok);
       word-break:break-all}
  .pill{display:inline-block;padding:2px 9px;border-radius:99px;font-size:12.5px;
        border:1px solid var(--line);background:var(--panel2);color:var(--dim)}
  .pill.on{color:#0b1220;background:var(--ok);border-color:var(--ok)}
  table{width:100%;border-collapse:collapse;font-size:13px}
  th,td{border-bottom:1px solid var(--line);padding:7px 6px;text-align:left;vertical-align:top}
  th{color:var(--dim);font-weight:500}
  .verdicts{display:flex;gap:4px;flex-wrap:wrap}
  .verdicts button.sel{background:var(--big);border-color:var(--big);color:#fff}
  .padrow{display:flex;align-items:center;gap:10px;margin:8px 0}
  .padlabel{width:74px;flex:none;color:var(--dim);font-size:13px}
  .pad{display:flex;flex-wrap:wrap;gap:6px}
  .padbtn{min-width:46px;height:46px;padding:0 12px;font-size:17px;font-weight:600;
          border-radius:10px;border:1px solid var(--line);background:#1b2130;
          color:var(--fg);cursor:pointer;transition:.12s}
  .padbtn:hover{background:#232b3d;border-color:var(--big)}
  .padbtn.sel{background:var(--big);border-color:var(--big);color:#fff;
              box-shadow:0 0 0 3px rgba(122,162,255,.25)}
  .padbtn.zero{color:var(--dim);font-size:14px}
  .padbtn.mode{font-size:14px;min-width:auto}
  textarea{width:100%;background:var(--panel2);border:1px solid var(--line);color:var(--fg);
           border-radius:8px;padding:8px;font:inherit;min-height:52px}
  .hint{color:var(--dim);font-size:12.5px}
  .bar{position:sticky;top:0;z-index:9;background:rgba(15,20,32,.94);
       backdrop-filter:blur(6px);padding:8px 0;border-bottom:1px solid var(--line);
       display:flex;gap:10px;align-items:center;flex-wrap:wrap}
</style>
</head>
<body>
<div class="wrap">

  <div class="bar">
    <b>档位测试台</b>
    <span id="conn" class="pill">连接状态…</span>
    <span id="cur" class="pill">空闲</span>
    <span style="flex:1"></span>
    <button class="ghost" id="relBtn" onclick="toggleRelease()">让出设备给手机</button>
    <button class="bad" onclick="panic()">紧急全停</button>
  </div>

  <h1>计算者-X · 档位 / 帧 测试台</h1>
  <div class="sub">
    目标：搞清楚 <b>伸缩 1-6</b> 和 <b>震动 1-9</b> 各自对应哪个字节，
    以及哪几档是「强度递增」、哪几档是「波形（一长一短 / 连震 / 有停顿）」。<br>
    所有发送都会在设定秒数后自动停；随时可以点<b>紧急全停</b>。
  </div>

  <div class="card">
    <h2 style="margin-top:0">① 手动发送</h2>
    <div class="row">
      <label>伸缩档</label>
      <input type="range" id="t" min="0" max="6" value="0" oninput="sync()">
      <span class="pill" id="tv">0</span>
      <label style="min-width:auto">震动档</label>
      <input type="range" id="v" min="0" max="9" value="0" oninput="sync()">
      <span class="pill" id="vv">0</span>
    </div>
    <div class="row">
      <label>帧布局</label>
      <select id="layout" onchange="sync()">
        <option value="ours">B · [伸缩, 震动, 1]　（我们一直在发的）</option>
        <option value="bare">C · [伸缩, 震动]　（2 字节，无尾字节）</option>
        <option value="app">A · [伸缩, 震动, 100]　（App 的实际载荷）</option>
        <option value="four">D · [伸缩, 震动, 0, 0]　（App 停止用的 4 字节）</option>
      </select>
      <label>尾字节</label>
      <input type="number" id="tail" value="100" min="0" max="255" oninput="sync()">
      <span class="hint">（选 A/B/D 时用这个覆盖尾部；扫描和手动都用这个布局）</span>
    </div>
    <div class="row">
      <label>持续</label>
      <input type="number" id="secs" value="20" min="1" max="120"><span>秒</span>
      <button onclick="sendManual()">发 送</button>
      <button class="ghost" onclick="stopAll()">停 止</button>
      <button class="ghost" onclick="setLayout('ours')">恢复默认布局 B</button>
      <button class="ghost" onclick="setLayout('bare')">切到 2 字节 C</button>
    </div>
    <div class="row"><label>将发送</label>
      <span class="hex" id="preview">—</span></div>
    <div class="row"><label>应答</label><span id="ack" class="hint">—</span></div>
  </div>

  <div class="card" style="border-color:var(--big)">
    <h2 style="margin-top:0">② 档位实测台</h2>
    <div class="hint" style="margin-bottom:10px">
      点一个档位 = 立刻发那条<b>档位命令</b>（`AA 06 05 …`，App 档位按钮走的同一条）。
      跑完自动停。测完在下面 ③ 打「强度」和「形态」。<br>
      <b>要确认的就一件事</b>：1/2/3 是不是越来越强，第 4 档是不是突然换了节奏。
    </div>

    <div class="row">
      <label>每档时长</label>
      <input type="number" id="lsecs" value="8" min="2" max="60"><span>秒</span>
      <label style="margin-left:8px">伸缩到</label>
      <input type="number" id="capT" value="6" min="0" max="9" oninput="buildPads()">
      <label>震动到</label>
      <input type="number" id="capV" value="9" min="0" max="9" oninput="buildPads()">
      <label>发送方式</label>
      <select id="repMode">
        <option value="0">只发一次　← 档位/模式用这个</option>
        <option value="1">每 0.1 秒重发（老做法）</option>
      </select>
      <button class="ghost" onclick="zeroAll()">归零（0 档）</button>
      <span class="hex" id="modeNow">当前：伸缩 0 / 震动 0</span>
    </div>
    <div class="hint" style="margin-top:-4px">
      <b>「发送方式」很关键。</b>档位帧是「设置状态」——
      设备收到就启动一段内置节奏程序。以 10Hz 反复发等于每秒把程序重启十次，
      本应平滑的推拉会碎成「嗡嗡的震动」，而且什么档位听起来都差不多。
      <b>所以默认「只发一次」。</b>切到重发可以自己对比区别。<br>
      「伸缩到」= 不生成该档以上的按钮，防止手滑。要测更高的档位把数字调大。
    </div>

    <div class="padrow"><span class="padlabel">伸缩</span><div class="pad" id="padT"></div></div>
    <div class="padrow"><span class="padlabel">震动</span><div class="pad" id="padV"></div></div>
    <div class="padrow"><span class="padlabel">内置模式</span><div class="pad" id="padM"></div></div>

    <div class="big" id="stageBox">
      <span id="stage">待机</span>
      <small id="stageSub">点上面的档位按钮开始测</small>
    </div>
    <div class="row" style="margin-top:10px"><label>当前帧</label><span class="hex" id="curframe">—</span></div>
  </div>

  <div class="card">
    <h2 style="margin-top:0">③ 自定义滑块（百分比模式）</h2>
    <div class="hint" style="margin-bottom:10px">
      这是 App <b>手动滑条</b>走的命令：<code>AA 08 06 &lt;伸缩%&gt; &lt;震动%&gt; 0 0 0 0 &lt;校验&gt;</code>，
      值 0–99。<b>和上面的档位命令是两条完全不同的命令。</b><br>
      <b>实测已确认：100% = 3 档。</b>所以这是一条<b>纯强度通道</b> ——
      量程正好覆盖基础档 0→3 档，<b>永远够不到 4 档以上的节奏程序</b>。
      换算：<code>档位 = 百分比 ÷ 100 × 3</code>。<br>
      按钮上标的就是按这个算出来的等效档位。想复验就点 <b>33%</b> 再去 ② 点 <b>1 档</b>，
      手感应该一样。
    </div>

    <div class="row">
      <label>伸缩 %</label>
      <input type="number" id="pctT" value="0" min="0" max="99" oninput="syncPct()" style="width:80px">
      <label>震动 %</label>
      <input type="number" id="pctV" value="0" min="0" max="99" oninput="syncPct()" style="width:80px">
      <button onclick="sendPct()">发 送</button>
      <button class="ghost" onclick="zeroAll()">归零</button>
      <span class="hex" id="pctFrame">—</span>
    </div>

    <div class="padrow"><span class="padlabel">伸缩 %</span><div class="pad" id="padPT"></div></div>
    <div class="padrow"><span class="padlabel">震动 %</span><div class="pad" id="padPV"></div></div>
    <div class="padrow"><span class="padlabel">整档扫</span><div class="pad" id="padPZ"></div></div>
  </div>

  <div class="card">
    <h2 style="margin-top:0">④ 记录表</h2>
    <div class="hint" style="margin-bottom:8px">
      每点一个档位就往表里加一行。强度 0-5：0 无反应、1 很弱、2 弱、3 中、4 强、5 太强。<br>
      形态选「波形」的话，备注写清楚是什么波形（一长一短 / 连震 / 三下停一下 …）。
    </div>
    <table id="tbl">
      <thead><tr>
        <th style="width:44px">轴</th><th style="width:44px">档</th>
        <th style="width:190px">帧</th><th style="width:200px">强度</th>
        <th style="width:150px">形态</th><th>备注</th>
      </tr></thead>
      <tbody></tbody>
    </table>
    <div class="row" style="margin-top:12px">
      <button class="ok" onclick="exportJson()">导出 JSON</button>
      <button class="ghost" onclick="clearTable()">清空表</button>
      <button class="ghost" onclick="exportCsv()">导出 CSV</button>
    </div>
    <textarea id="dump" placeholder="导出结果会出现在这里，可直接复制"></textarea>
  </div>


  <div class="card">
    <h2 style="margin-top:0">⑤ 面板日志</h2>
    <pre id="log" style="max-height:220px;overflow:auto;font-size:12.5px;color:var(--dim)">—</pre>
  </div>
</div>

<script>
const $ = id => document.getElementById(id);

/* ---------------- ② 档位实测台 ---------------- */
// 五个内置模式的档位组合 —— 冒充 App 抓到的实测值，见 capture/app-writes-modes.md
const MODES = {
  '如影随形': [4, 6, 5, 0],
  '疾风炫舞': [8, 9, 9, 0],
  '幻影随行': [6, 6, 8, 0],
  '水光环绕': [2, 2, 3, 0],
  '神龙摆尾': [3, 9, 3, 0]
};
const MAXL = { t: 9, v: 9 };
let cur = { t: 0, v: 0 };

function buildPads(){
  // 按「上限」生成按钮 —— 上限设成 4 就不会出现 5 档的按钮，
  // 手滑也点不到。要测更高的档位把数字调大即可。
  for (const [axis, el, capId] of [['t', 'padT', 'capT'], ['v', 'padV', 'capV']]){
    const cap = Math.max(0, Math.min(MAXL[axis], +$(capId).value || 0));
    const box = $(el);
    box.innerHTML = '';
    for (let n = 0; n <= cap; n++){
      const b = document.createElement('button');
      b.className = 'padbtn' + (n === 0 ? ' zero' : '');
      b.textContent = n;
      b.onclick = () => sendLevelBtn(axis, n, b);
      box.appendChild(b);
    }
    if (cur[axis] > cap) cur[axis] = 0;      // 当前档超出上限就归零
  }
  // 模式行不受上限影响
  const pm = $('padM');
  if (!pm.children.length){
    for (const name of Object.keys(MODES)){
      const b = document.createElement('button');
      b.className = 'padbtn mode';
      b.textContent = name;
      b.onclick = () => sendModeBtn(name, b);
      pm.appendChild(b);
    }
  }
  $('modeNow').textContent = `当前：伸缩 ${cur.t} / 震动 ${cur.v}`;
}

function markActive(btn){
  document.querySelectorAll('.padbtn.sel').forEach(x => x.classList.remove('sel'));
  if (btn) btn.classList.add('sel');
  $('modeNow').textContent = `当前：伸缩 ${cur.t} / 震动 ${cur.v}`;
}

async function sendLevelBtn(axis, n, btn){
  // 只驱动当前这条轴，另一条强制归零 ——
  // 否则「点过模式 → cur 里留着 8」再点震动，会连带把伸缩也拉到 8。
  cur.t = axis === 't' ? n : 0;
  cur.v = axis === 'v' ? n : 0;
  const secs = +$('lsecs').value;
  const rep = $('repMode').value === '1';
  const r = await call('/api/level', {thrust: cur.t, vibration: cur.v,
                                      seconds: secs, repeat: rep});
  markActive(btn);
  $('curframe').textContent = r.hex || '—';
  setStage(`${axis === 't' ? '伸缩' : '震动'} 第 ${n} 档`,
           `跑 ${secs} 秒 · ${rep ? '10Hz重发' : '只发一次'} · ${r.hex}`);
  ensureRow2(axis, n, r.hex);
  setTimeout(refreshStage, secs * 1000 + 400);
}

async function sendModeBtn(name, btn){
  const p = MODES[name];
  cur.t = p[0]; cur.v = p[1];
  const secs = +$('lsecs').value;
  const rep = $('repMode').value === '1';
  const r = await call('/api/mode', {name, seconds: secs, repeat: rep});
  markActive(btn);
  $('curframe').textContent = r.hex || '—';
  setStage(`模式：${name}`,
           `档位 [${p.join(', ')}] · ${rep ? '10Hz重发' : '只发一次'} · ${r.hex}`);
  ensureRow2('m', name, r.hex, p);
  setTimeout(refreshStage, secs * 1000 + 400);
}

async function zeroAll(){
  cur.t = 0; cur.v = 0;
  const r = await call('/api/level', {thrust: 0, vibration: 0, seconds: 2});
  markActive(null);
  $('curframe').textContent = r.hex || '—';
  setStage('归零', r.hex);
}

async function refreshStage(){
  try{
    const s = await call('/api/status');
    if (!s.running) setStage('已停止', '可以点下一个档位了');
  }catch(e){}
}

function ensureRow2(kind, key, hex, preset){
  const tb = $('tbl').querySelector('tbody');
  const k = kind + key;
  let tr = tb.querySelector(`tr[data-k="${k}"]`);
  if (tr) return tr;
  const label = kind === 'm' ? `模式 ${key}`
              : kind === 't' ? `伸缩 ${key}`
              : kind === 'v' ? `震动 ${key}`
              : kind === 'p' ? `滑块 ${String(key).replace('_', '% / ')}%`
              : `${key}`;
  tr = document.createElement('tr');
  tr.dataset.k = k;
  tr.dataset.kind = kind;
  tr.dataset.key = key;
  tr.innerHTML = `
    <td>${kind === 'm' ? '模式' : kind === 't' ? '伸缩' : kind === 'v' ? '震动' : '滑块'}</td>
    <td><b>${label}</b></td>
    <td class="hex">${hex || ''}</td>
    <td><div class="verdicts">${
      [0,1,2,3,4,5].map(n=>`<button class="sm ghost" data-s="${n}">${n}</button>`).join('')
    }</div></td>
    <td><select>
      <option value="">—</option>
      <option>幅度（变强）</option><option>波形（节奏）</option>
      <option>无反应</option><option>和上一档一样</option>
      <option>和档位N一样</option>
    </select></td>
    <td><input type="text" placeholder="备注" style="width:100%"></td>`;
  tb.appendChild(tr);
  tr.querySelectorAll('[data-s]').forEach(b=>{
    b.onclick = () => {
      tr.querySelectorAll('[data-s]').forEach(x=>x.classList.remove('sel','ok'));
      b.classList.add('sel','ok');
      tr.dataset.score = b.dataset.s;
    };
  });
  const sel = tr.querySelector('select');
  sel.onchange = () => { tr.dataset.form = sel.value; };
  tr.querySelector('input').oninput = e => { tr.dataset.note = e.target.value; };
  tr.scrollIntoView({block:'nearest'});
  return tr;
}

/* ---------------- ③ 自定义滑块（百分比模式） ----------------
   和档位模式是两条不同的命令：
     档位   cmd 0x06，5 字节，值 0–9（4+ 是节奏程序）
     百分比 cmd 0x08，6 字节，值 0–99
   **实测确认：100% = 3 档。** 所以这是纯强度通道，量程 0→3 档，
   够不到 4 档以上的节奏程序。
   换算：档位 = pct / 100 * 3  （我早先猜的 ÷10 是错的，实测推翻了） */
function pctToLevel(pct){ return Math.max(0, Math.min(100, pct)) / 100 * 3; }

function pctHex(t, v){
  const body = [0xAA, 0x08, 0x06, t & 0xFF, v & 0xFF, 0, 0, 0, 0];
  body.push(body.reduce((a, b) => (a + b) & 0xFF, 0) & 0xFF);
  return body.map(x => x.toString(16).padStart(2, '0').toUpperCase()).join(' ');
}

function syncPct(){
  const t = Math.max(0, Math.min(99, +$('pctT').value || 0));
  const v = Math.max(0, Math.min(99, +$('pctV').value || 0));
  $('pctFrame').textContent =
    `${pctHex(t, v)}　　= 强度 ${pctToLevel(t).toFixed(2)} / ${pctToLevel(v).toFixed(2)} 档`;
}

function buildPctPads(){
  // 百分比按钮：按「等效档位」分三档 —— 33%≈1档、67%≈2档、100%=3档
  for (const [el, axis] of [['padPT', 't'], ['padPV', 'v']]){
    const box = $(el);
    box.innerHTML = '';
    for (const pct of [10, 25, 33, 50, 67, 75, 90, 100]){
      const b = document.createElement('button');
      b.className = 'padbtn';
      const lv = pctToLevel(pct);
      b.innerHTML = `${pct}%<small style="display:block;font-size:10px;color:var(--dim)">${lv.toFixed(1)}档</small>`;
      b.onclick = () => sendPctBtn(axis, pct, b);
      box.appendChild(b);
    }
  }
  const bz = $('padPZ');
  bz.innerHTML = '';
  for (let pct = 0; pct <= 90; pct += 15){
    const b = document.createElement('button');
    b.className = 'padbtn zero';
    b.textContent = pct + '%';
    b.onclick = () => sendPctBtn('z', pct, b);
    bz.appendChild(b);
  }
}

async function sendPctBtn(axis, pct, btn){
  if (axis === 't') { $('pctT').value = pct; $('pctV').value = 0; }
  else if (axis === 'v') { $('pctT').value = 0; $('pctV').value = pct; }
  else { $('pctT').value = pct; $('pctV').value = pct; }
  syncPct();
  await sendPct(btn);
}

async function sendPct(btn){
  const t = Math.max(0, Math.min(99, +$('pctT').value || 0));
  const v = Math.max(0, Math.min(99, +$('pctV').value || 0));
  const secs = +$('lsecs').value;
  const rep = $('repMode').value === '1';
  const r = await call('/api/percent', {thrust: t, vibration: v,
                                        seconds: secs, repeat: rep});
  markActive(btn || null);
  $('curframe').textContent = r.hex || '—';
  setStage(`百分比：伸缩 ${t}% / 震动 ${v}%`,
           `= 强度 ${r.equiv_thrust_level} / ${r.equiv_vib_level} 档（100% = 3 档）· ${r.hex}`);
  ensureRow2('p', `${t}_${v}`, r.hex);
  setTimeout(refreshStage, secs * 1000 + 400);
}

function frameHex(payload){
  const b = [0xAA, 0x08, payload.length, ...payload];
  const sum = b.reduce((a,x)=>a+x, 0) & 0xFF;
  b.push(sum);
  return b.map(x=>x.toString(16).padStart(2,'0').toUpperCase()).join(' ');
}

function buildPayload(t, v){
  const layout = $('layout').value;
  const tail = Math.max(0, Math.min(255, parseInt($('tail').value||'0',10)));
  if (layout === 'app')  return [t, v, tail];
  if (layout === 'ours') return [t, v, tail];
  if (layout === 'bare') return [t, v];
  if (layout === 'four') return [t, v, 0, 0];
  return [t, v, tail];
}

function sync(){
  $('tv').textContent = $('t').value;
  $('vv').textContent = $('v').value;
  const p = buildPayload(+$('t').value, +$('v').value);
  $('preview').textContent = frameHex(p);
}

function setLayout(v){
  $('layout').value = v;
  sync();
  if (typeof showMap === 'function') showMap();
}

async function call(path, body){
  const r = await fetch(path, {
    method: body ? 'POST' : 'GET',
    headers: {'Content-Type':'application/json'},
    body: body ? JSON.stringify(body) : undefined
  });
  return await r.json();
}

async function sendManual(){
  const t = +$('t').value, v = +$('v').value;
  const hex = frameHex(buildPayload(t, v));
  const secs = +$('secs').value;
  const res = await call('/api/send', {hex, seconds: secs});
  $('ack').textContent = JSON.stringify(res);
  $('curframe').textContent = hex;
  lastSent = {axis: t>0 ? 't' : (v>0 ? 'v' : '?'), level: t>0 ? t : v, hex};
}

async function stopAll(){
  await call('/api/stop', {});
  $('curframe').textContent = '—';
  $('ack').textContent = '已停止';
}

let released = false;

async function toggleRelease(){
  released = !released;
  const r = await call('/api/release', {give: released});
  $('relBtn').textContent = released ? '收回设备' : '让出设备给手机';
  $('relBtn').className = released ? 'ok' : 'ghost';
  setStage(released ? '已让出设备' : '已收回设备',
           released
             ? '现在手机 App 可以连了 —— 断开玩具且暂停自动重连。你在 App 里按，我们这边继续听。'
             : '面板重新接管设备');
  if (released){
    // 让出前先停下来，别把设备留在某个档位上
    await call('/api/stop', {});
  }
}

async function panic(){
  await stopAll();
}

function setStage(a, b){ $('stage').textContent = a; $('stageSub').textContent = b; }

function stopSweep(){ /* 旧的自动扫描已由「② 档位实测台」取代 */ }

function rows(){
  const out = [];
  $('tbl').querySelectorAll('tbody tr').forEach(tr=>{
    const kind = tr.dataset.kind || tr.dataset.k[0];
    const isMode = kind === 'm';
    out.push({
      轴: isMode ? '模式' : (kind === 't' ? '伸缩' : '震动'),
      档: isMode ? tr.dataset.key : parseInt(tr.dataset.key ?? tr.dataset.k.slice(1), 10),
      帧: tr.querySelectorAll('td')[2].textContent.trim(),
      强度: tr.dataset.score ?? null,
      形态: tr.dataset.form || '',
      备注: tr.dataset.note || ''
    });
  });
  const order = {'伸缩':0, '震动':1, '模式':2};
  return out.sort((a,b)=> (a.轴===b.轴 ? 0 : order[a.轴]-order[b.轴]));
}

function exportJson(){
  const data = rows();
  $('dump').value = JSON.stringify(data, null, 1);
  navigator.clipboard?.writeText($('dump').value).catch(()=>{});
  setStage('已导出', `${data.length} 条，已复制到剪贴板（也可从下面文本框复制）`);
}

function exportCsv(){
  const data = rows();
  const head = '轴,档,帧,强度,形态,备注';
  const body = data.map(r=>[r.轴,r.档,r.帧,r.强度??'',r.形态,r.备注]
      .map(x=>`"${String(x).replace(/"/g,'""')}"`).join(',')).join('\n');
  $('dump').value = head + '\n' + body;
  navigator.clipboard?.writeText($('dump').value).catch(()=>{});
  setStage('已导出 CSV', `${data.length} 条`);
}

function clearTable(){
  $('tbl').querySelector('tbody').innerHTML = '';
  setStage('表已清空', '');
}

async function poll(){
  try{
    const s = await call('/api/status');
    const toy = s.toy_connected, band = s.band_connected;
    $('conn').className = 'pill' + (toy ? ' on' : '');
    $('conn').textContent = `玩具 ${toy?'已连':'未连'} · 手环 ${band?'已连':'未连'} · ${s.hr||0} bpm`;
    if (s.released !== undefined && s.released !== released){
      released = s.released;
      $('relBtn').textContent = released ? '收回设备' : '让出设备给手机';
      $('relBtn').className = released ? 'ok' : 'ghost';
    }
    if (s.raw_hex) $('cur').textContent = '发 ' + s.raw_hex;
    else if (s.running) $('cur').textContent = `伸缩 ${s.telescopic} / 震动 ${s.vibration} · 剩 ${s.remain}s`;
    else $('cur').textContent = '空闲';
    $('log').textContent = (s.msgs||[]).join('\n') || '—';
  }catch(e){
    $('conn').textContent = '连不上控制台（先启动 点我-控制台.bat）';
  }
}

sync();
buildPads();
buildPctPads();
syncPct();
poll();
setInterval(poll, 1500);
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):      # 别刷屏
        pass

    def _send(self, code: int, body: bytes, ctype="application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass

    def _body(self) -> dict:
        try:
            n = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(n).decode() or "{}") if n else {}
        except Exception:
            return {}

    def do_GET(self):  # noqa: N802
        p = self.path.split("?")[0]
        if p in ("/", "/index.html"):
            self._send(200, PAGE.encode(), "text/html")
        elif p == "/api/status":
            self._send(200, json.dumps(panel("/api/status"), ensure_ascii=False).encode())
        else:
            self._send(404, b'{"error":"nf"}')

    def do_POST(self):  # noqa: N802
        p = self.path.split("?")[0]
        d = self._body()
        if p == "/api/send":
            res = panel("/api/raw", {"hex": d.get("hex", ""),
                                     "seconds": d.get("seconds", 15)})
            self._send(200, json.dumps(res, ensure_ascii=False).encode())
        elif p == "/api/stop":
            self._send(200, json.dumps(panel("/api/stop", {}), ensure_ascii=False).encode())
        elif p == "/api/notify":
            res = panel("/api/notify", {"clear": bool(d.get("clear"))})
            self._send(200, json.dumps(res, ensure_ascii=False).encode())
        elif p == "/api/capture":
            res = panel("/api/capture", {"on": bool(d.get("on"))})
            self._send(200, json.dumps(res, ensure_ascii=False).encode())
        elif p == "/api/release":
            res = panel("/api/release", {"give": bool(d.get("give", True))})
            self._send(200, json.dumps(res, ensure_ascii=False).encode())
        elif p == "/api/percent":
            # 百分比模式（App 手动滑条走的那条命令 cmd 0x08）
            res = panel("/api/percent", {
                "thrust": int(d.get("thrust", 0)),
                "vibration": int(d.get("vibration", 0)),
                "seconds": float(d.get("seconds", 8)),
                "repeat": bool(d.get("repeat", False)),
            })
            self._send(200, json.dumps(res, ensure_ascii=False).encode())
        elif p == "/api/level":
            # 档位模式 —— App 档位按钮走的同一条命令（cmd 0x06）
            res = panel("/api/level", {
                "thrust": int(d.get("thrust", 0)),
                "vibration": int(d.get("vibration", 0)),
                "seconds": float(d.get("seconds", 8)),
                "repeat": bool(d.get("repeat", False)),
            })
            self._send(200, json.dumps(res, ensure_ascii=False).encode())
        elif p == "/api/mode":
            # 内置模式（如影随形 等）—— 就是一组档位预设
            res = panel("/api/mode", {
                "name": d.get("name", ""),
                "seconds": float(d.get("seconds", 30)),
                "repeat": bool(d.get("repeat", False)),
            })
            self._send(200, json.dumps(res, ensure_ascii=False).encode())
        else:
            self._send(404, b'{"error":"nf"}')


class FastServer(ThreadingHTTPServer):
    """跳过 HTTPServer.server_bind() 里的 socket.getfqdn() 反向 DNS 查询 ——
    那个查询在网络不通时会把服务器启动卡住几十秒。（同 toy_panel.py 的写法）"""
    daemon_threads = True
    allow_reuse_address = True

    def server_bind(self):
        import socketserver
        socketserver.TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name = host
        self.server_port = port


def main() -> None:
    print("=" * 64)
    print("   计算者-X · 档位测试台")
    print("=" * 64)
    st = panel("/api/status")
    if st.get("error"):
        print(f"   [警告] 连不上控制台（{PANEL}）：{st['error']}")
        print("          先双击 点我-控制台.bat 把控制台跑起来。页面照常能开。")
    else:
        print(f"   控制台已连：串口 {st.get('serial_port')}　"
              f"玩具 {'✓' if st.get('toy_connected') else '✗'}　"
              f"手环 {'✓' if st.get('band_connected') else '✗'}")
    print()
    print(f"   打开这个地址 →  http://127.0.0.1:{HTTP_PORT}")
    print("   关掉这个窗口即停止（会先全停设备）")
    print("=" * 64, flush=True)

    try:
        httpd = FastServer(("127.0.0.1", HTTP_PORT), Handler)
    except OSError as exc:
        print(f"[错误] 端口 {HTTP_PORT} 被占用了：{exc}", flush=True)
        return
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        panel("/api/stop", {})
        print("\n[已停止，设备已全停]")


if __name__ == "__main__":
    main()
