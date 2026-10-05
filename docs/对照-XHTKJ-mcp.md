# 对照记录：Gaoshu705/XHTKJ-mcp

来源：<https://github.com/Gaoshu705/XHTKJ-mcp>（别人做的谜姬 BLE MCP 服务）
结论：**不是同一代设备，协议完全不通，表抄不过来。**

## 硬件层对比

| | XHTKJ-mcp | 我们的「计算者-X」(`mizzzee_0007`) |
|---|---|---|
| 广播服务 | `0000d34e-…` | — |
| 设备服务 | `0000ff10-…` | **`0000ffe0-…`** |
| 写特征 | `0000ff12-…` | **`0000ffe1-…`** |
| 通知特征 | `0000ff11-…` | **`0000ffe1-…`**（读写同一个） |
| 产品 ID 特征 | `00002a50`（PnP ID） | 未使用 |
| 协议栈 | `bleak`（PC 直连蓝牙） | ESP32-S3 当 BLE 主机 → 串口 |

## 帧格式对比

```
XHTKJ-mcp：  03 12 <cmd> <param> …       固定 20 字节
              ↑  ↑
              |  命令
              包头

我们：       AA <cmd> <len> <payload…> <sum8>    变长
             ↑                    ↑
             包头                  全部字节求和 & 0xFF
```

## 强度编码对比

```python
# XHTKJ-mcp：10 位值，低 6 位塞一个常量标签
def encoded_strength(scale, product_id):        # scale 0..1
    if product_id == 4:       adjusted = 0.8*scale + 0.2
    elif product_id in {1, 3}: adjusted = 0.6*scale + 0.4
    else:                      adjusted = 0.7*scale + 0.3
    return (int(1023 * adjusted) << 6) | 60

# 我们：直接一个字节
payload = [伸缩, 震动, 尾字节]
```

## 但有一点很值得参考 —— 它印证了「档位 4 以上是波形」这件事

XHTKJ-mcp 把「强度」和「模式」**分成两个完全不同的命令**：

```python
async def set_strength(self, primary, secondary, duration):
    await self._write(strength_packet(...))      # 0xF3：连续的强度值

async def set_mode(self, mode: int, duration):   # mode = 1..255
    await self._write(command_packet(mode))      # 就是「内置节奏」编号
```

README 里的原话：

> `set_strength` 只发送一个固定强度。如果需要连续变化，应使用 `play_strength_sequence`…
> `set_mode` 切换设备模式…`play_custom_wave` 下发自定义百分比波形

**也就是说，谜姬这一系的设备设计上就是「强度」与「内置节奏/波形」分开的。**

实测：「只有前 3 档是幅度递增，第 4 档起变成一长一短 / 连震 / 三下停顿」——
这正好符合「设备内置了若干节奏程序」的模型。

**推论（待验证）**：我们那个 `AA 08 03 <伸缩> <震动> <尾>` 的 payload
很可能只是「强度」通道；要选内置节奏得走**别的命令字节**
（候选：`cmd 0x06` 那个 5 路命令，或者 `cmd 0x01` 状态上报里那个恒为 3 的字节
就是当前模式编号）。

## 自发的状态上报（本机实测，与 XHTKJ-mcp 无关）

```
AA 01 06 64 06 CC 03 00 00 EA     payload = 100 6  CC 3  0 0
AA 01 06 64 06 18 03 00 00 36     payload = 100 6  18 3  0 0
AA 01 06 64 08 A2 03 00 00 C2     payload = 100 8  A2 3  0 0
AA 01 06 64 08 AC 03 00 00 CC     payload = 100 8  AC 3  0 0
```

- `cmd 0x01` = 设备主动上报，不需要我们发任何东西
- 第 0 字节恒为 `100`（像电量）
- 第 1 字节 `06`/`08`
- 第 2 字节在变（`CC` `18` `A2` `AC` 是**按位取反**关系：`CC^FF=33`… 待观察）
- **第 3 字节恒为 `3`** ← 如果那是模式编号，说明设备当前停在模式 3
- 末两字节恒为 `0`

**这是目前最接近「设备自己认为当前是什么状态」的证据**，比从 App 里猜靠谱。
