# 控制台前端

一个 `index.html`，没有构建步骤，改完刷新就生效。

后端在 `toy_panel.py`，通过 `web_server.py` 提供。

## 调用的接口

| 接口 | 作用 |
|---|---|
| `GET /api/status` | 拿当前状态（档位、心率、连接情况） |
| `POST /api/level` | 下发档位 |
| `POST /api/percent` | 下发百分比 |
| `POST /api/mode` | 下发内置模式 |
| `POST /api/stop` | 全停 |

⚠ 档位和百分比是两套东西，别混 —— **百分比 100% 才等于 3 档**，永远够不到节奏档。

---

*这份文档由 DeepSeek 协助整理。*
