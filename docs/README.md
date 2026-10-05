# 文档

| 文件 | 内容 |
|---|---|
| `CAMERA.md` | 手机摄像头接入完整流程 |
| `对照-XHTKJ-mcp.md` | 与同类实现的对照笔记 |

协议本身的文档在根目录 `PROTOCOL.md`。

## CAMERA.md 为什么那么长

因为这块踩的坑实在多。按顺序：

1. 公共热点有客户端隔离，同网段也连不上
2. HTTPS 自签证书两种报错 —— **`IP address mismatch` 不让跳过，`self-signed` 让跳过**
3. OPPO 报「需要私钥」其实是缺 `CA:TRUE`；补上又报「没有可安装的证书」—— **国产 ROM 把入口砍了**
4. 浏览器 flags 开关实测不生效
5. 最后解法：**`adb reverse` + `localhost`**
6. 手机息屏断流、帧池挑帧、光的要求

**只要结论的话：别跟证书较劲，用 adb reverse。**

---

*这份文档由 DeepSeek 协助整理。*
