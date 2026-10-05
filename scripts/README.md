# 启动脚本

PowerShell 写的，根目录的 `.bat` 只是包一层。

| 这里 | 对应 bat | 作用 |
|---|---|---|
| `start.ps1` | `点我-控制台.bat` | 起控制台（8090） |
| `level-test.ps1` | `档位测试台.bat` | 起档位实测台（8091） |
| `status.ps1` | `status.bat` | 看服务状态 |
| `stop.ps1` | `stop.bat` | 停掉服务 |

为什么不直接写 bat —— 要处理编码、进程管理、端口占用，PowerShell 好写。

---

*这份文档由 DeepSeek 协助整理。*
