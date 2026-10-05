# ESP32 固件

设备与手环的蓝牙网关。

## 为什么不用电脑直连蓝牙

| | 电脑直连 BLE | ESP32 网关 |
|---|---|---|
| 稳定性 | Windows BLE 栈会随机断 | 稳定 |
| 距离 | 受机箱和 USB3 干扰 | 可放到设备旁边 |
| 双设备 | 同时连设备和手环很吃力 | 没问题 |
| 可移植 | 绑死 Windows | 换台电脑插上就行 |

```
esp32/firmware/     PlatformIO 工程（推荐）
esp32/toy_bridge/   Arduino IDE 单文件版
```

## 串口命令

| 命令 | 作用 |
|---|---|
| `SET <伸缩> <震动>` | 下发档位 |
| `STOP` | 全停 |
| `STATE` | 查当前状态 |
| `HR` | 查心率 |
| `SCAN` | 扫经典蓝牙 |
| `BSCAN` | 扫 BLE |
| `RAW <hex>` | 直接发原始帧 |

`RAW` 在调协议时很有用 —— 不用改代码就能试新帧。

## 烧录

```bash
cd firmware
pio run -t upload
```

找不到串口跑 `python ../findport.py`。

---

*这份文档由 DeepSeek 协助整理。*
