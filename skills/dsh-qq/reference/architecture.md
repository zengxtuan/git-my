# 架构与数据流

## 四层职责

| 层 | 组件 | 职责 | 有无决策权 |
|---|---|---|---|
| 接入层 | NapCat + NTQQ | 把 QQ 协议翻译成 OneBot v11 事件；反向执行动作 | 无（哑管道） |
| 调度层 | **AstrBot** | 收事件、管会话、调模型、跑插件、发指令 | **有（唯一主控）** |
| 模型层 | DeepSeek / 任意 OpenAI 兼容 API | 生成回复 | 无（无状态） |
| 增强层 | 插件 | 记忆、表情包、语音、空间、签名、主动聊天 | 由 AstrBot 编排 |

运维层（keepalive 脚本）不在业务链路上，只负责"进程死了拉起来"。

## 一条消息的完整旅程

```
1. 你对 QQ 小号发消息
2. 腾讯服务器 → NTQQ 客户端（被 NapCat 注入 hook）
3. NapCat → 反向 WebSocket → AstrBot:6199
4. AstrBot 过白名单 → 会话上下文 → 人格 system prompt
5. 检索长期记忆（向量召回）→ 拼进上下文
6. 调模型 API → 拿到文字
7. 插件后处理：分句、配表情包、决定要不要转语音
8. AstrBot → 反向 WS 下发 send_msg / set_qq_avatar ……
9. NapCat → NTQQ → QQ 服务器 → 你看到回复
```

## 端口对照

| 端口 | 服务 | 绑定 | 用途 | 暴露风险 |
|---|---|---|---|---|
| 6185 | AstrBot Dashboard | `127.0.0.1` | 管理 AI、插件、人格、记忆 | 中（能改 AI 行为） |
| 6199 | OneBot 反向 WS | `127.0.0.1` | NapCat 连进来 | 低（有 token） |
| 6099 | NapCat WebUI | `127.0.0.1` | **操作 QQ 本体** | **高** |
| 3000 | NapCat HTTP API | `127.0.0.1` | 本地 OneBot HTTP 调用 | 中 |

6099 和 3000 是命门：前者能登出、发消息、改资料；后者能直接调 OneBot 接口。都必须只绑回环，必要时再叠一层防火墙 Block 入站。

## 进程对照

| 进程名 | 属于 | 说明 |
|---|---|---|
| `astrbot.exe` | AstrBot | 主进程，venv 里的入口 |
| `NapCatWinBootMain.exe` | NapCat | 启动器，负责注入 hook |
| `QQ.exe` ×N | NTQQ | 多进程结构，4 个左右属正常 |
| `powershell.exe`（keepalive） | 运维 | 常驻守护 |

判断"链路通没通"最快的方式：`Get-NetTCPConnection -State Established | ? {$_.RemotePort -eq 6199}` 有记录即为通。

## 存储位置

| 内容 | 位置 |
|---|---|
| 主配置 | `data/cmd_config.json`（**带 UTF-8 BOM**，读写要用 `utf-8-sig`） |
| 人格 | `data/data_v4.db` 的 `personas` 表（`persona_id` + `system_prompt`） |
| 会话历史 | `data/data_v4.db` 的 `conversations` 表 |
| 长期记忆 | `data/plugin_data/<memory-plugin>/livingmemory.db` |
| 插件配置 | `data/config/<plugin>_config.json` |
| 表情包 | `data/plugin_data/<meme-plugin>/` |
| 运行日志 | 启动时重定向的输出文件（如 `run.out.log`） |
| NapCat 配置 | `NapCat.XXXX.Shell/config/`（`webui.json`、`onebot11_<uin>.json`） |
| QQ 登录态 | NapCat 独立副本的 `versions/<build>` 内，与主 QQ 隔离 |

## 关键设计事实

- **人格存数据库，不在 JSON 里**。`cmd_config.json` 只存 `persona_id` 引用。
- **模型不决定身份**。换成任何模型，只要 system prompt 没写人格，都会自称"AstrBot 助手"并复述工具清单。
- **NapCat 用独立 QQ 副本**，与主号完全隔离；扫描哪个号就登录哪个号。
- **反向 WS 是 NapCat 主动连 AstrBot**，不是反过来。所以 AstrBot 先起、NapCat 后起也能自动重连（重连间隔在 `onebot11.json` 里配）。
