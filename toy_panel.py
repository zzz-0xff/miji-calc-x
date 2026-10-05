#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""toy_panel.py —— 计算者-X 网页控制台 + 心率监控（点一下就能用）

启动：双击 点我-控制台.bat   或   python toy_panel.py
页面：http://127.0.0.1:8090

链路： 浏览器 --HTTP--> 本脚本 --USB COM3--> ESP32-S3 --BLE--> 玩具 + 华为手环

API（AI 也用这个）：
    GET  /api/status
    POST /api/set        {"telescopic":60,"vibration":30,"seconds":15}
    POST /api/stop
    POST /api/reconnect  {"what":"toy"|"band"|"both"}   掉线手动重连

自愈：
    · 串口拔了/换了口 → 每 3 秒自动重试
    · 玩具掉线       → 自动 SCAN 重连（15/30/60/120 秒退避）
    · 手环没读数     → 超过 90 秒自动 BSCAN
    · 每 20 秒问一次 STATE，所以晚启动也能看到真实连接状态
"""
from __future__ import annotations

import json
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

try:
    import serial
except ImportError:
    sys.exit("缺少 pyserial：python -m pip install pyserial")

import findport

# 关键：重定向到文件时 Python 默认块缓冲，日志会迟迟不落盘 —— 强制行缓冲
try:
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)
except Exception:
    pass

BASE = Path(__file__).resolve().parent
PORT = None            # None = 自动识别（--port COM5 或环境变量 TOY_PORT 可指定）
BAUD = 115200
HZ = 10.0
HTTP_PORT = 8090
MAX_SECONDS = 600


# 档位语义、上限、内置模式表 —— 全部来自 levels.py（和 ai_play 共用一份）
from levels import (  # noqa: E402
    MAX_THRUST_LEVEL, MAX_VIB_LEVEL,
    THRUST_LEVELS, VIB_LEVELS,
    THRUST_ENGAGES_VIB, THRUST_AMPLITUDE_LEVELS, VIB_AMPLITUDE_LEVELS,
    MODES, MODES_RAW, MODE_NAMES, MODE_ALIASES,
    level_frame, pct_frame, pct_to_level,
    aa, hexs, STOP_FRAMES,
)


def motor_frame(tele: int, vib: int) -> bytes:
    """百分比帧：0–100 连续强度。真机有效，但**语义和档位不是一回事**。

    这是「手动滑条」那条路。要把设备切到某一档，用下面的 level_frame。
    """
    return aa(0x08, [tele & 0xFF, vib & 0xFF, 1])




class Ctl:
    def __init__(self, port: str | None):
        self.want_port = port
        self.ser = None
        self.port_name = ""
        self.serial_error = "正在查找串口 ..."
        self._last_open_try = 0.0
        self._last_hr_watchdog = 0.0
        self._last_toy_watchdog = 0.0
        self._last_state_poll = 0.0
        self._last_toy_scan = 0.0
        self._serial_opened_ms = 0.0
        self.toy_scan_tries = 0
        self.state_polls = 0
        self.lock = threading.Lock()
        self._wlock = threading.Lock()   # 串口写互斥：HTTP 线程和电机帧不能交错
        self.running = False
        self.tele = 0
        self.vib = 0
        self.raw_frame: bytes | None = None    # 非 None = 测试模式，原样重发这条帧
        self.deadline = 0.0
        self.sent = 0
        self.msgs: list[str] = []
        self.last_notify = ""
        self.notify_log: list[dict] = []      # 玩具回传全量记录（按键测试用）
        self.raw_capture = False              # 固件是否处于「回传全量记录」模式
        self.level_mode = False               # 当前是否用档位帧（cmd 0x06）驱动
        # 是否以 10Hz 重复发这条帧。**档位/模式必须只发一次** ——
        # 见下面的注释，这是整晚「什么档位都像在震动」的元凶。
        self.raw_repeat = True
        self._raw_sent = False
        self.released = False                 # 是否已让出玩具给手机
        self.spoofing = False                 # 是否处于「冒充玩具」模式
        self.adv_lines: list[str] = []        # 扫广播的原始结果
        self._watching = False
        self._watch_found = False
        self._watch_end = 0.0
        self.hr = 0
        self.hr_history: list[int] = []
        self.hr_last_ms = 0.0
        self.band_connected = False
        self.toy_connected = False
        self._open_serial()
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def _open_serial(self) -> bool:
        """按可能性顺序试开串口。失败不抛异常、不退出，返回 False 让主循环稍后重试。
        注意：绝不在这里做"打开探测"，S3 原生 USB 打开会复位重枚举，可能死锁。"""
        if self.ser is not None and getattr(self.ser, "is_open", False):
            return True
        cands = findport.candidates(self.want_port)
        if not cands:
            self.ser = None
            self.port_name = ""
            self.serial_error = "系统里没有任何串口（ESP32 没插好？）"
            return False
        last_err = ""
        for port, score, desc in cands:
            try:
                s = serial.Serial(port, BAUD, timeout=0.02)
                try:
                    s.dtr = False       # 尽量别触发板子复位
                    s.rts = False
                except Exception:
                    pass
                self.ser = s
                self.port_name = port
                self.serial_error = ""
                self._serial_opened_ms = time.time()
                self._last_state_poll = 0.0
                self._last_toy_scan = 0.0
                self.toy_scan_tries = 0
                self.say(f"串口已打开 {port}（{desc or '未知设备'}）")
                # 这里绝对不能马上写！ESP32-S3 原生 USB 在端口打开瞬间会因
                # DTR/RTS 翻转而复位重枚举，此时句柄已失效，write() 会永久阻塞。
                # 交给 _loop 的读取线程去处理即可。
                return True
            except Exception as exc:  # noqa: BLE001
                last_err = f"{port}: {exc}"
                continue
        self.ser = None
        self.port_name = ""
        self.serial_error = f"所有候选端口都打不开（{last_err}）—— 被别的程序占着？"
        return False

    def say(self, text: str) -> None:
        line = f"[{time.strftime('%H:%M:%S')}] {text}"
        self.msgs.append(line)
        del self.msgs[:-40]
        print(line, flush=True)

    def _write(self, b: bytes) -> None:
        if self.ser is None:
            return
        try:
            with self._wlock:
                self.ser.write(("RAW " + hexs(b).replace(" ", "") + "\n").encode())
                self.ser.flush()
        except Exception as exc:  # noqa: BLE001
            self.say(f"串口写失败: {exc}")
            try:
                self.ser.close()
            except Exception:
                pass
            self.ser = None

    def send(self, cmd: str) -> bool:
        """给 ESP32 发一条文本命令（SCAN / BSCAN / STATE 之类）。

        写失败会把串口关掉交给主循环重开，绝不在这里抛异常。
        """
        if self.ser is None:
            return False
        try:
            with self._wlock:
                self.ser.write((cmd.strip() + "\n").encode())
                self.ser.flush()
            return True
        except Exception as exc:  # noqa: BLE001
            self.say(f"串口写失败: {exc}")
            try:
                self.ser.close()
            except Exception:
                pass
            self.ser = None
            return False

    def _handle(self, body: str) -> None:
        if body.startswith("HR "):
            try:
                bpm = int(body[3:].strip())
            except ValueError:
                return
            with self.lock:
                self.hr = bpm
                self.hr_last_ms = time.time()
                self.hr_history.append(bpm)
                del self.hr_history[:-120]
            return

        if body.startswith("APPWRITE"):
            # 冒充模式下手机 App 发过来的原始帧 —— 这是本次实验唯一要的东西。
            # 以前没有这个分支，会被 _handle 静默丢掉（那整个实验就白做了）。
            raw = body[8:].strip()
            with self.lock:
                self.notify_log.append({"t": time.strftime("%H:%M:%S"),
                                        "hex": raw, "src": "app"})
                del self.notify_log[:-600]
            self.say("【App 发来的】" + raw)
            return

        if body.startswith("ADV"):
            # 扫广播的结果 —— 冒充前要照着抄的东西，必须看得见
            with self.lock:
                self.adv_lines.append(body)
                del self.adv_lines[:-200]
            if "已记住这个机型" in body:
                self._watch_found = True       # 抓到了，别继续扫了
            self.say(body)
            return

        if body.startswith("APP"):
            # 冒充模式下的其它动作（读特征、连接、断开…）也要看得见，
            # 不然 App 卡在哪一步完全没法判断
            self.say(body)
            return

        if body.startswith("NOTIFY"):
            hexs_ = body[7:].strip()
            self.last_notify = hexs_
            # 全量记下来并打时间戳 —— 你在玩具上按键时，设备会回传，
            # 这些回传是搞清档位/模式最直接的证据。以前只留最后一条，等于没记。
            with self.lock:
                self.notify_log.append({"t": time.strftime("%H:%M:%S"), "hex": hexs_})
                del self.notify_log[:-600]
            # 只把「非 10Hz 应答」的包写进控制台日志 —— 应答包每帧都来，
            # 全写会把日志刷没；而按键上报是稀疏的，值得单独显示。
            if not hexs_.startswith("AA 09 06"):
                self.say(f"设备上报 {hexs_}")
            return

        if body.startswith("running="):
            self.toy_connected = "conn=1" in body
            self.band_connected = "band=1" in body
            m = re.search(r"hr=(\d+)", body)
            if m and int(m.group(1)) > 0:
                self.hr = int(m.group(1))
                self.hr_last_ms = time.time()
            self.say(body)
            return

        if "已连接" in body or "失败" in body or "订阅" in body or "掉线" in body \
           or "冒充" in body or "让出" in body or "收回" in body \
           or "GAP" in body or "广播" in body:
            if "玩具已连接" in body:
                self.toy_connected = True
            if "手环已订阅" in body:
                self.band_connected = True
            if "手环连接失败" in body or "没扫到心率广播" in body:
                self.band_connected = False
            if "没扫到玩具" in body:
                self.toy_connected = False
            self.say(body)

    def _loop(self) -> None:
        buf = b""
        nxt = 0.0
        while True:
            # 串口没开就每 3 秒重试一次（插好了会自动接上，不用重启面板）
            if self.ser is None:
                if time.time() - self._last_open_try > 3.0:
                    self._last_open_try = time.time()
                    self._open_serial()
                time.sleep(0.2)
                continue

            try:
                chunk = self.ser.read(512)
            except Exception:
                chunk = b""
                try:
                    self.ser.close()
                except Exception:
                    pass
                self.ser = None
            if chunk:
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    t = line.decode("utf-8", "replace").strip("\r")
                    if not t:
                        continue
                    body = t.split("] ", 1)[1] if (t.startswith("[") and "] " in t) else t
                    self._handle(body)

            now = time.time()

            # ---- 心率看门狗：手环连着但长时间没数据 → 让 ESP32 重新扫一遍 ----
            if now - self._last_hr_watchdog > 60 and self.ser is not None:
                self._last_hr_watchdog = now
                stale = (self.hr_last_ms == 0) or (now - self.hr_last_ms > 90)
                if self.band_connected and stale:
                    self.say("心率已超过 90 秒没更新（手环广播多半自动关了），"
                             "让 ESP32 重扫手环 ...")
                    self.send("BSCAN")

            # ---- 玩具看门狗：ESP32 掉线后不会自己找回来，得这边推一把 ----
            # 顺便定时问一次 STATE，好让面板知道玩具/手环的真实连接状态
            # （面板晚启动时，ESP32 早就连上了，不主动问就永远显示未连接）。
            if now - self._last_toy_watchdog > 20 and self.ser is not None:
                self._last_toy_watchdog = now
                # 端口刚打开的头几秒别写 —— S3 原生 USB 正在复位重枚举
                if now - self._serial_opened_ms > 5.0:
                    if self.toy_connected:
                        self.toy_scan_tries = 0
                        if now - self._last_state_poll > 20:
                            self._last_state_poll = now
                            self.state_polls += 1
                            self.send("STATE")
                    else:
                        # 连不上的时候别一直催：connectToy 是阻塞的，
                        # 越催越挤。15s / 30s / 60s / 120s 退避，连上就清零。
                        # ⚠ 已经「让出设备给手机」时绝不能发 SCAN ——
                        #   那会跟手机 App 抢连接，两边都连不稳。
                        backoff = min(120.0, 15.0 * (2 ** min(self.toy_scan_tries, 3)))
                        if self.released:
                            pass
                        elif now - self._last_toy_scan > backoff:
                            self._last_toy_scan = now
                            self.toy_scan_tries += 1
                            self.say(f"玩具掉线了，让 ESP32 重连"
                                     f"（第 {self.toy_scan_tries} 次）...")
                            self.send("SCAN")
                            self._last_state_poll = 0.0

            with self.lock:
                running, tele, vib, deadline = self.running, self.tele, self.vib, self.deadline
                raw = self.raw_frame

            if running and now > deadline:
                self._do_stop("时间到，自动停")
                nxt = 0.0
                continue

            if not running:
                nxt = 0.0          # 停了就把节奏归零，下次开始重新对齐
            elif now >= nxt:
                # 注意别写 nxt = now + 1/HZ：每轮都把读取抖动累加进去，
                # 实测只能跑到 6Hz，而这设备要 ~10Hz 才跟手。
                if nxt == 0.0 or now - nxt > 0.5:
                    nxt = now      # 刚起步 / 卡太久 → 重新对齐，不要疯狂补帧
                nxt += 1.0 / HZ
                if raw:
                    # ⚠ 关键：档位/模式帧（cmd 0x06）**只能发一次**。
                    #   设备把它们当「设置状态」，收到就启动一段内置节奏程序。
                    #   以 10Hz 重复发 = 每秒把那个程序重启十次 ——
                    #   本该平滑的推拉被切成碎片，听感就是「嗡嗡的震动」，
                    #   而且什么档位听起来都差不多。
                    #   App 实测每次改档只发一条，所以它的档位是正常的。
                    if self.raw_repeat or not self._raw_sent:
                        self._write(raw)
                        self._raw_sent = True
                        self.sent += 1
                else:
                    self._write(motor_frame(tele, vib))
                    self.sent += 1
                # 注意：sent 只统计**真的写到串口**的次数。
                # 以前放在 if 外面，导致「只发一次」模式下计数照样涨，
                # 看起来像没生效 —— 排查时白绕了一圈。

            time.sleep(0.005)

    def _do_stop(self, why: str) -> None:
        with self.lock:
            was = self.running
            self.running = False
            self._raw_sent = False          # 下一轮重新开始时要能再发一次
        for f in STOP_FRAMES:
            self._write(f)
            time.sleep(0.05)
        if was:
            self.say(why)

    # ------- 对外 -------
    def start(self, tele: int, vib: int, seconds: float) -> dict:
        tele = max(0, min(100, int(tele)))
        vib = max(0, min(100, int(vib)))
        seconds = max(1.0, min(MAX_SECONDS, float(seconds or 15)))
        with self.lock:
            self.tele, self.vib = tele, vib
            self.raw_frame = None
            self.running = True
            self.deadline = time.time() + seconds
        self.say(f"开始：伸缩={tele} 震动={vib} 持续={seconds:g}s  帧={hexs(motor_frame(tele, vib))}")
        return {"ok": True, "telescopic": tele, "vibration": vib, "seconds": seconds}

    def play_mode(self, name: str, seconds: float = 30, repeat: bool = False) -> dict:
        """按名字切到内置模式（如影随形 / 疾风炫舞 / 幻影随行 / 水光环绕 / 神龙摆尾）。

        这些模式的字节是冒充 App 实测抓到的，见 capture/app-writes-modes.md。
        """
        key = str(name or "").strip()
        if key not in MODES:
            near = [k for k in MODES if key and key in k]
            if len(near) == 1:
                key = near[0]
            else:
                return {"ok": False, "error": f"没有这个模式: {name!r}",
                        "available": list(MODES)}
        t, v, c2, c3 = MODES[key]
        res = self.level(t, v, seconds, c2, c3, 0, repeat=repeat)
        res["mode"] = key
        res["preset"] = [t, v, c2, c3]
        self.say(f"模式：{key}  档位={[t, v, c2, c3]}")
        return res

    def level(self, thrust: int, vibration: int, seconds: float,
              ch2: int = 0, ch3: int = 0, ch4: int = 0, repeat: bool = False) -> dict:
        """按**档位**驱动 —— App 档位按钮走的同一条命令（cmd 0x06）。

        和 start()（百分比）是两套语义，别混用：
        - 档位 1/2/3 = 幅度递增，4 以上 = 设备内置节奏
        - 百分比 0–100 = 连续强度，够不到档位

        **repeat 默认 False（只发一次）** —— 这是对的。
        档位帧是「设置状态」，设备收到就启动一段内置程序；反复发等于
        每秒把程序重启十次，本该平滑的推拉会碎成「嗡嗡的震动」。
        """
        thrust = max(0, min(9, int(thrust)))
        vibration = max(0, min(9, int(vibration)))
        # 超过真机上限的档位设备没反应 —— 钳到上限，别静悄悄什么都不做
        if thrust > MAX_THRUST_LEVEL:
            thrust = MAX_THRUST_LEVEL
        if vibration > MAX_VIB_LEVEL:
            vibration = MAX_VIB_LEVEL
        seconds = max(1.0, min(MAX_SECONDS, float(seconds or 15)))
        fr = level_frame(thrust, vibration, ch2, ch3, ch4)

        # ---- 续期检测：同一条档位帧、还在跑 → 只延长超时，**不重发** ----
        # 重发会重启设备的内置程序（见档位实测表里的「发送方式」一节）。
        # AI 要「保持这个档位」时会周期性调用，必须走这条续期路径，
        # 否则每次续期都会把程序打断一次。
        with self.lock:
            if (self.running and self.level_mode and self._raw_sent
                    and self.raw_frame == fr):
                self.deadline = time.time() + seconds
                self.tele, self.vib = thrust, vibration
                renew = True
            else:
                renew = False

        if renew:
            self.say(f"档位续期（不重发）：伸缩={thrust} 震动={vibration} "
                     f"+{seconds:g}s")
            return {"ok": True, "renewed": True, "thrust_level": thrust,
                    "vibration_level": vibration, "hex": hexs(fr),
                    "seconds": seconds, "repeat": bool(repeat),
                    "thrust_desc": THRUST_LEVELS.get(thrust, ""),
                    "vib_desc": VIB_LEVELS.get(vibration, "")}

        with self.lock:
            self.raw_frame = fr
            self.raw_repeat = bool(repeat)
            self._raw_sent = False
            self.level_mode = True
            self.tele, self.vib = thrust, vibration
            self.running = True
            self.deadline = time.time() + seconds
        self.say(f"档位：伸缩={thrust}({THRUST_LEVELS.get(thrust,'?')}) "
                 f"震动={vibration}({VIB_LEVELS.get(vibration,'?')}) "
                 f"持续={seconds:g}s {'（10Hz重复）' if repeat else '（只发一次）'}")
        if thrust in THRUST_ENGAGES_VIB:
            self.say(f"⚠ 伸缩第 {thrust} 档的内置程序会自行驱动震动电机 —— "
                     f"即使震动字节是 0，实际也会有震动")
        return {"ok": True, "thrust_level": thrust, "vibration_level": vibration,
                "thrust_desc": THRUST_LEVELS.get(thrust, ""),
                "vib_desc": VIB_LEVELS.get(vibration, ""),
                "engages_vib": thrust in THRUST_ENGAGES_VIB,
                "hex": hexs(fr), "seconds": seconds, "repeat": bool(repeat)}

    def percent(self, thrust: int, vibration: int, seconds: float,
                repeat: bool = False) -> dict:
        """百分比模式 —— App **手动滑条**走的命令（cmd 0x08，6 字节载荷）。

        和 level() 是两套完全不同的命令：
        - level()   → cmd 0x06，5 字节，值是**档位 0–9**（4+ 是节奏程序）
        - percent() → cmd 0x08，6 字节，值是**百分比 0–99**

        **实测确认：100% = 3 档。** 这是一条**纯强度通道**，
        量程正好覆盖基础档 0→3 档，够不到 4 档以上的节奏程序。
        """
        thrust = max(0, min(99, int(thrust)))
        vibration = max(0, min(99, int(vibration)))
        seconds = max(1.0, min(MAX_SECONDS, float(seconds or 15)))
        fr = pct_frame(thrust, vibration)
        with self.lock:
            self.raw_frame = fr
            self.raw_repeat = bool(repeat)
            self._raw_sent = False
            self.level_mode = False
            self.tele, self.vib = thrust, vibration
            self.running = True
            self.deadline = time.time() + seconds
        lt, lv = pct_to_level(thrust), pct_to_level(vibration)
        self.say(f"百分比：伸缩={thrust}%（≈{lt:.2f} 档，100%=3档） "
                 f"震动={vibration}%（≈{lv:.2f} 档） "
                 f"持续={seconds:g}s {'（10Hz重复）' if repeat else '（只发一次）'}")
        return {"ok": True, "thrust_pct": thrust, "vibration_pct": vibration,
                "equiv_thrust_level": round(lt, 2), "equiv_vib_level": round(lv, 2),
                "max_level": 3,
                "hex": hexs(fr), "seconds": seconds, "repeat": bool(repeat)}

    def raw(self, hexstr: str, seconds: float, repeat: bool = True) -> dict:
        """测试模式：原样重发一条自定义帧，用来标定。

        这是给「档位测试台」用的 —— 不走 motor_frame()，你给什么字节就发什么。

        repeat=False 时只发一次。**发档位/模式帧必须用 False** ——
        那些是「设置状态」命令，10Hz 重发会把设备的内置程序反复重启，
        听感变成嗡嗡的震动（实测踩过，见 capture/档位4以上会牵连震动.md）。
        """
        s = "".join(ch for ch in str(hexstr) if ch in "0123456789abcdefABCDEF")
        if len(s) < 6 or len(s) % 2:
            return {"ok": False, "error": f"hex 字符串无效: {hexstr!r}"}
        frame = bytes.fromhex(s)
        seconds = max(1.0, min(MAX_SECONDS, float(seconds or 15)))
        with self.lock:
            self.raw_frame = frame
            self.raw_repeat = bool(repeat)
            self._raw_sent = False          # ← 必须重置，否则上一轮的「已发」会挡住这次
            self.running = True
            self.deadline = time.time() + seconds
        self.say(f"测试帧：{hexs(frame)}  持续={seconds:g}s"
                 f"{'（重复 ' + str(int(HZ)) + 'Hz）' if repeat else '（只发一次）'}")
        return {"ok": True, "hex": hexs(frame), "bytes": len(frame),
                "seconds": seconds, "repeat": bool(repeat)}

    def spoof(self, on: bool, name: str = "") -> dict:
        """冒充玩具，让手机 App 连到 ESP32 上 —— 抓 App 真正发出来的字节。

        为什么需要：设备只允许一个连接方，没法一边让 App 控一边当主机旁听；
        手机又是 ColorOS，HCI snoop 是摆设。所以干脆把 ESP32 变成"玩具"。

        name 留空 = 用刚用 ADV 扫到的**真机型名**。
        千万不要随便编名字 —— 用别的型号（比如 mizzzee_0029）App 会当成
        另一款产品，发出来的指令就不是我们要抓的了。
        """
        on = bool(on)
        if on:
            self.released = True          # 冒充模式下玩具连接必须让出来
        ok = self.send(("SPOOF " + name).strip() if on else "SPOOFOFF")
        self.spoofing = on
        tail = f"（名字={name}）" if name else "（照抄刚扫到的真机型）"
        self.say(("冒充模式已开：App 现在可以连了 " + tail)
                 if on else "冒充模式已关，正在收回玩具")
        return {"ok": ok, "spoofing": on, "name": name or "(照抄)"}

    def watch_start(self, minutes: float = 6.0) -> dict:
        """反复扫广播，直到扫到 mizzzee 为止。

        为什么需要：被手机 App 连着的玩具**不会广播**。你得先把 App 关掉、
        确认玩具开着——但你不可能掐着时间配合我。所以让面板一直盯着，
        它一出现就自动抄下广播内容。
        """
        if self._watching:
            return {"ok": True, "watching": True, "note": "已经在盯着了"}
        self._watching = True
        self._watch_found = False
        self._watch_end = time.time() + max(0.5, float(minutes)) * 60
        threading.Thread(target=self._watch_loop, daemon=True).start()
        self.say(f"开始盯着广播（最多 {minutes:g} 分钟）—— "
                 f"请关掉手机 App、确认玩具开着，它一广播我就能抓到")
        return {"ok": True, "watching": True, "minutes": minutes}

    def watch_stop(self) -> dict:
        self._watching = False
        self.say("停止监视广播")
        return {"ok": True, "watching": False}

    def _watch_loop(self) -> None:
        while self._watching and time.time() < self._watch_end:
            if self._watch_found:
                break
            self.send("ADV 8")
            time.sleep(9.5)
        self._watching = False
        if self._watch_found:
            self.say("已抓到真设备的广播，可以开冒充模式了")
        else:
            self.say("监视结束，还是没扫到 mizzzee —— 玩具是开着的吗？App 关干净了吗？")

    def adv(self, seconds: int = 10) -> dict:
        """扫附近的广播并**原样打印** —— 冒充前先抓真设备的广播内容。

        注意：必须先让出玩具（TDIS），否则玩具被我们连着就不会广播，
        什么都扫不到。
        """
        seconds = max(2, min(30, int(seconds)))
        ok = self.send(f"ADV {seconds}")
        self.say(f"扫广播 {seconds} 秒（先把玩具让出去，否则它不广播）")
        return {"ok": ok, "seconds": seconds}

    def release(self, give: bool = True) -> dict:
        """让出 / 收回玩具。

        让出时会断开连接**并暂停自动重连** —— 否则面板和手机 App
        会互相抢，两边都连不稳。用来做「手机 App 操作 + 我们监听」的对照实验。
        """
        give = bool(give)
        ok = self.send("TDIS" if give else "TRES")
        self.released = give
        self.say("已让出玩具给手机（暂停自动重连）" if give else "已收回玩具")
        return {"ok": ok, "released": give}

    def capture(self, on: bool) -> dict:
        """开关固件的「回传全量记录」。

        默认固件会去重（只记和上一条不同的包），否则 10Hz 应答流会刷屏。
        但抓按键上报时必须全量，不然重复的包会被吞掉、看起来像「设备没反应」。
        """
        on = bool(on)
        ok = self.send(f"NLOG {1 if on else 0}")
        self.raw_capture = on
        self.say("回传全量记录 → " + ("已开（抓按键，串口会变吵）" if on else "已关（恢复去重）"))
        return {"ok": ok, "capture": on}

    def stop(self) -> dict:
        self._do_stop("手动停止")
        with self.lock:
            self.raw_frame = None
        return {"ok": True}

    def reconnect(self, what: str = "toy") -> dict:
        """手动让 ESP32 重新连接（toy / band / both）"""
        cmds = {"toy": ["SCAN"], "band": ["BSCAN"], "both": ["SCAN", "BSCAN"]}
        picked = cmds.get(what, ["SCAN"])
        sent = [c for c in picked if self.send(c)]
        if "SCAN" in picked:
            self._last_toy_scan = time.time()
            self.toy_scan_tries += 1
        self.say(f"手动重连：{what} → {sent or '串口没开'}")
        return {"ok": bool(sent), "sent": sent}

    def status(self) -> dict:
        with self.lock:
            remain = max(0.0, self.deadline - time.time()) if self.running else 0.0
            age = (time.time() - self.hr_last_ms) if self.hr_last_ms else -1
            return {
                "running": self.running,
                "telescopic": self.tele,
                "vibration": self.vib,
                "remain": round(remain, 1),
                "sent": self.sent,
                "frame": hexs(motor_frame(self.tele, self.vib)) if self.running else "",
                "raw_hex": hexs(self.raw_frame) if (self.running and self.raw_frame) else None,
                "last_notify": self.last_notify,
                "notify_log": self.notify_log[-40:],
                "capture": self.raw_capture,
                "released": self.released,
                "spoofing": self.spoofing,
                "level_mode": self.level_mode,
                "thrust_levels": THRUST_LEVELS,
                "vib_levels": VIB_LEVELS,
                "max_thrust": MAX_THRUST_LEVEL,
                "max_vib": MAX_VIB_LEVEL,
                "modes": MODES,
                "adv_lines": self.adv_lines[-80:],
                "hr": self.hr,
                "hr_age": round(age, 1),
                "hr_history": self.hr_history[-60:],
                "toy_connected": self.toy_connected,
                "band_connected": self.band_connected,
                "toy_scan_tries": self.toy_scan_tries,
                "state_polls": self.state_polls,
                "serial_port": self.port_name or None,
                "serial_error": self.serial_error or None,
                "msgs": self.msgs[-10:],
            }


CTL: Ctl | None = None

PAGE = """<!doctype html>
<html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>计算者-X 控制台</title>
<style>
 :root{color-scheme:dark}
 body{margin:0;padding:18px;font-family:system-ui,"PingFang SC",sans-serif;background:#0f1116;color:#e8eaf0}
 h1{font-size:22px;margin:0 0 14px}
 #hrbox{display:flex;gap:18px;align-items:center;background:#1a1220;border:1px solid #3a2136;
   border-radius:14px;padding:16px;margin-bottom:14px}
 .hrleft{min-width:150px}
 .hrnum{font-size:52px;font-weight:800;color:#ff5d7a;line-height:1;font-family:ui-monospace,monospace}
 .hrunit{font-size:13px;color:#b98aa0;margin-top:6px}
 .hrright{flex:1}
 .hrbars{display:flex;gap:2px;align-items:flex-end;height:52px}
 .hrbars i{flex:1;background:#ff5d7a;border-radius:2px 2px 0 0;min-height:2px;opacity:.85}
 .hrlegend{font-size:12px;color:#b98aa0;margin-top:6px}
 #banner{padding:18px;border-radius:14px;background:#1b2030;margin-bottom:16px}
 #banner.on{background:#0f3d24}
 #banner .big{font-size:24px;font-weight:700}
 #banner .sub{font-family:ui-monospace,monospace;font-size:14px;color:#8ad;margin-top:6px}
 .ctl{background:#171b26;border:1px solid #232838;border-radius:14px;padding:14px;margin-bottom:12px}
 .row{display:flex;align-items:center;gap:12px;margin-bottom:10px}
 .row label{width:56px;font-size:16px}
 .row input[type=range]{flex:1;height:34px}
 .val{width:56px;text-align:right;font-size:20px;font-weight:700;font-family:ui-monospace,monospace}
 .btns{display:flex;gap:10px;flex-wrap:wrap}
 button{padding:14px 18px;border:0;border-radius:11px;background:#2d6cdf;color:#fff;font-size:16px;cursor:pointer}
 button.stop{background:#b32b3a}
 button.q{background:#252c3e;font-size:14px;padding:10px 14px}
 button:active{filter:brightness(1.3)}
 input[type=number]{width:90px;padding:10px;border-radius:9px;border:1px solid #2b3244;
   background:#0d1017;color:#e8eaf0;font-size:16px}
 pre{background:#0b0d12;padding:10px;border-radius:9px;font-size:11px;height:130px;overflow:auto;white-space:pre-wrap}
</style></head><body>
<h1>计算者-X 控制台</h1>

<div id="hrbox">
  <div class="hrleft">
    <div class="hrnum" id="hrNum">--</div>
    <div class="hrunit">bpm <span id="hrAge"></span></div>
  </div>
  <div class="hrright">
    <div class="hrbars" id="hrBars"></div>
    <div class="hrlegend" id="hrLegend">心率（华为手环 HRS 广播）</div>
  </div>
</div>

<div id="banner">
  <div class="big" id="b1">空闲</div>
  <div class="sub" id="b2"></div>
</div>

<div class="ctl">
  <div class="row"><label>伸缩</label>
    <input type="range" id="tele" min="0" max="100" value="0"><div class="val" id="teleV">0</div></div>
  <div class="row"><label>震动</label>
    <input type="range" id="vib" min="0" max="100" value="0"><div class="val" id="vibV">0</div></div>
  <div class="row"><label>时长</label>
    <input type="number" id="secs" value="15" min="1" max="600"><span>秒</span></div>
  <div class="btns">
    <button onclick="go()">开 始</button>
    <button class="stop" onclick="stopAll()">停 止</button>
  </div>
</div>

<div class="ctl">
  <div class="btns">
    <button class="q" onclick="quick(20,0)">伸缩 20</button>
    <button class="q" onclick="quick(50,0)">伸缩 50</button>
    <button class="q" onclick="quick(100,0)">伸缩 100</button>
    <button class="q" onclick="quick(0,30)">震动 30</button>
    <button class="q" onclick="quick(0,80)">震动 80</button>
    <button class="q" onclick="quick(80,60)">两个一起来</button>
    <button class="q" onclick="quick(0,0)">全 0（停）</button>
    <button class="q" onclick="recon('toy')">重连玩具</button>
    <button class="q" onclick="recon('band')">重连手环</button>
  </div>
</div>

<pre id="log"></pre>

<script>
const tele = document.getElementById('tele'), vib = document.getElementById('vib');
tele.oninput = () => document.getElementById('teleV').textContent = tele.value;
vib.oninput  = () => document.getElementById('vibV').textContent  = vib.value;

function quick(t, v) {
  tele.value = t; vib.value = v;
  document.getElementById('teleV').textContent = t;
  document.getElementById('vibV').textContent = v;
  go();
}
async function go(){
  const body = JSON.stringify({
    telescopic: +tele.value, vibration: +vib.value,
    seconds: +document.getElementById('secs').value
  });
  await fetch('/api/set', {method:'POST', body});
}
async function stopAll(){ await fetch('/api/stop', {method:'POST'}); }
async function recon(what){
  const r = await (await fetch('/api/reconnect',
    {method:'POST', body: JSON.stringify({what})})).json();
  if (!r.ok) alert('串口没开，重连指令发不出去');
}

async function tick(){
  try {
    const s = await (await fetch('/api/status')).json();
    const b = document.getElementById('banner');
    b.className = s.running ? 'on' : '';
    document.getElementById('b1').textContent = s.running
      ? ('\\u25b6 运行中   伸缩 ' + s.telescopic + '   震动 ' + s.vibration + '   还剩 ' + s.remain + 's')
      : '空闲';
    document.getElementById('b2').textContent =
      (s.frame ? ('帧 ' + s.frame + '   ') : '') +
      ('玩具' + (s.toy_connected ? '\\u2714' : '\\u2718') +
       '  手环' + (s.band_connected ? '\\u2714' : '\\u2718'));

    if (s.hr) {
      document.getElementById('hrNum').textContent = s.hr;
      document.getElementById('hrAge').textContent = (s.hr_age >= 0 ? (s.hr_age + ' 秒前更新') : '');
    } else {
      document.getElementById('hrNum').textContent = '--';
      document.getElementById('hrAge').textContent =
        (s.band_connected ? '已连手环，等读数...' : '手环未连接');
    }
    const h = s.hr_history || [];
    const lo = 40, hi = Math.max(120, h.length ? Math.max.apply(null, h) : 120);
    document.getElementById('hrBars').innerHTML = h.slice(-60).map(function(v){
      const pct = Math.max(3, Math.min(100, (v - lo) / (hi - lo) * 100));
      return '<i style="height:' + pct + '%"></i>';
    }).join('');
    document.getElementById('hrLegend').textContent =
      '心率（华为手环 HRS 广播） 最近 ' + h.length + ' 次' +
      (h.length ? (' 最低 ' + Math.min.apply(null, h) + ' / 最高 ' + Math.max.apply(null, h)) : '');

    document.getElementById('log').textContent =
      (s.msgs || []).join('\\n') + (s.last_notify ? ('\\n设备回: ' + s.last_notify) : '');
  } catch(e){}
  setTimeout(tick, 400);
}
tick();
</script></body></html>
"""


class FastServer(ThreadingHTTPServer):
    """跳过 HTTPServer.server_bind() 里的 socket.getfqdn() 反向 DNS 查询。
    那个查询在网络不通/DNS 无响应时会阻塞几十秒甚至永久 —— 面板会「起不来」。"""
    daemon_threads = True
    allow_reuse_address = True

    def server_bind(self):
        import socketserver
        socketserver.TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name = host
        self.server_port = port


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _send(self, code: int, body: bytes, ctype="application/json") -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        try:
            return json.loads(self.rfile.read(n) or b"{}")
        except json.JSONDecodeError:
            return {}

    def log_message(self, *a):
        return

    def do_GET(self):  # noqa: N802
        p = self.path.split("?")[0]
        if p in ("/", "/index.html"):
            self._send(200, PAGE.encode(), "text/html")
        elif p == "/api/status":
            self._send(200, json.dumps(CTL.status(), ensure_ascii=False).encode())
        else:
            self._send(404, b'{"error":"nf"}')

    def do_POST(self):  # noqa: N802
        p = self.path.split("?")[0]
        d = self._body()
        if p == "/api/set":
            self._send(200, json.dumps(CTL.start(d.get("telescopic", 0), d.get("vibration", 0),
                                                 d.get("seconds", 15)), ensure_ascii=False).encode())
        elif p == "/api/stop":
            self._send(200, json.dumps(CTL.stop(), ensure_ascii=False).encode())
        elif p == "/api/reconnect":
            self._send(200, json.dumps(
                CTL.reconnect(d.get("what", "toy")), ensure_ascii=False).encode())
        elif p == "/api/mode":
            # 按名字切内置模式（如影随形 等）
            self._send(200, json.dumps(
                CTL.play_mode(d.get("name", ""), d.get("seconds", 30)),
                ensure_ascii=False).encode())
        elif p == "/api/level":
            # 档位模式（App 档位按钮的同一条命令 cmd 0x06）
            self._send(200, json.dumps(
                CTL.level(d.get("thrust", 0), d.get("vibration", 0),
                          d.get("seconds", 15), d.get("ch2", 0),
                          d.get("ch3", 0), d.get("ch4", 0),
                          repeat=bool(d.get("repeat", False))),
                ensure_ascii=False).encode())
        elif p == "/api/percent":
            # 百分比模式（App 手动滑条走的那条命令 cmd 0x08）
            self._send(200, json.dumps(
                CTL.percent(d.get("thrust", 0), d.get("vibration", 0),
                            d.get("seconds", 15),
                            repeat=bool(d.get("repeat", False))),
                ensure_ascii=False).encode())
        elif p == "/api/raw":
            # 档位测试台用：原样重发任意帧（不走 motor_frame）
            self._send(200, json.dumps(
                CTL.raw(d.get("hex", ""), d.get("seconds", 15),
                        repeat=bool(d.get("repeat", True))),
                ensure_ascii=False).encode())
        elif p == "/api/notify":
            # 玩具回传全量：读 / 清空。按键测试靠这个。
            if d.get("clear"):
                with CTL.lock:
                    CTL.notify_log.clear()
            with CTL.lock:
                log = list(CTL.notify_log)
            self._send(200, json.dumps({"count": len(log), "log": log},
                                       ensure_ascii=False).encode())
        elif p == "/api/capture":
            # 开关固件的回传全量记录模式
            self._send(200, json.dumps(CTL.capture(bool(d.get("on"))),
                                       ensure_ascii=False).encode())
        elif p == "/api/release":
            # 让出 / 收回玩具（给手机 App 接管用的）
            self._send(200, json.dumps(CTL.release(bool(d.get("give", True))),
                                       ensure_ascii=False).encode())
        elif p == "/api/spoof":
            # 冒充玩具：手机 App 连到 ESP32 上，抓它发的每个字节
            self._send(200, json.dumps(
                CTL.spoof(bool(d.get("on")), d.get("name", "")),
                ensure_ascii=False).encode())
        elif p == "/api/adv":
            # 扫广播并原样打印（冒充前先抓真设备的广播内容）
            self._send(200, json.dumps(CTL.adv(d.get("seconds", 10)),
                                       ensure_ascii=False).encode())
        elif p == "/api/watch":
            if d.get("stop"):
                self._send(200, json.dumps(CTL.watch_stop(), ensure_ascii=False).encode())
            else:
                self._send(200, json.dumps(CTL.watch_start(d.get("minutes", 6)),
                                           ensure_ascii=False).encode())
        else:
            self._send(404, b'{"error":"nf"}')


def main() -> None:
    global CTL
    port = sys.argv[1] if len(sys.argv) > 1 else PORT
    # 注意：这里绝不 input() 阻塞 —— 面板常以隐藏窗口运行，卡住就再也起不来了
    CTL = Ctl(port)

    print("=" * 62)
    print(f"  控制台地址：  http://127.0.0.1:{HTTP_PORT}")
    if CTL.ser is not None:
        print(f"  串口: {CTL.port_name}")
    else:
        print(f"  [警告] 暂时没接上串口：{CTL.serial_error}")
        print("         面板照常运行，每 3 秒自动重试；插好 ESP32 会自动接上。")
    print("  关掉这个窗口即停止")
    print("=" * 62, flush=True)

    try:
        httpd = FastServer(("127.0.0.1", HTTP_PORT), Handler)
    except OSError as exc:
        print(f"[错误] 端口 {HTTP_PORT} 被占用了：{exc}", flush=True)
        print("       是不是已经开了一个控制台？先跑 stop.bat。", flush=True)
        return
    httpd.serve_forever()


if __name__ == "__main__":
    main()
