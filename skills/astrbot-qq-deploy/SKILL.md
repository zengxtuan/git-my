---
name: astrbot-qq-deploy
description: Use when deploying an LLM into QQ on Windows (AstrBot + NapCat), or when fixing long-term memory, TTS voice, sticker packs, Qzone posting, personality drift, or a bot/service that silently fails to start in such a deployment.
---

# AstrBot + NapCat QQ 机器人部署

把一个 OpenAI 兼容的大模型接进 QQ 小号，并挂上长期记忆、语音、表情包、空间动态、定时自启。

## 硬约束（动手前必须确认）

- **只用 QQ 小号。** 第三方协议端登录主号 = 主号直接暴露在风控下，不可逆。
- **三个端口只绑 `127.0.0.1`**：AstrBot 后台 6185、OneBot 反向 WS 6199、NapCat WebUI 6099。6099 尤其危险——它能直接操作 QQ 本体。
- **ID 白名单必须开启并填真实 ID**，否则任何陌生人都能调用模型烧余额。
- **`computer_use_runtime` 必须是 `none`**，且 `computer_use_local_permissions` 的 `member`/`admin` 都要 `allow_execution=false`、`allow_network=false`。这项打开等于给模型一只能读写本机、能跑命令的手。
- **密钥不落仓库。** 运行时凭证写进实例的 `secrets/` 或 `data/cmd_config.json`，仓库里只放模板与占位符。

## 架构（谁控制谁）

```
QQ 小号(NTQQ) ←注入← NapCat ←反向WS→ AstrBot → 模型 API
                              ↓
                     插件：记忆/表情/语音/空间
```

- **AstrBot 是唯一有决策权的主控**：人格、插件、模型、白名单、会话全在它这。
- NapCat 是哑管道 + 执行器：翻译 OneBot 事件、执行 `set_qq_avatar` / `send_msg` 一类动作。
- 两个后台分工：`6185` 管 AI 怎么想怎么说；`6099` 能操作 QQ 本体，是高危入口。

细节见 `reference/architecture.md`。

## 分阶段流程

每阶段结束都要跑验证，别攒到最后一起调。

### 0. 环境检查
确认 Python ≥3.12、Node 可用、磁盘余量 >5GB、目标盘可写（D 盘根目录通常需要管理员，用用户目录）。
验证：`python -c "import sys;print(sys.version)"`

### 1. AstrBot
装法优先级：`pip install astrbot`（PyPI 有正式包）> 源码 > 第三方启动器。用独立 venv，别污染系统 Python。

```bash
python -m venv .venv
.venv/Scripts/python -m pip install astrbot -i <镜像源>
.venv/Scripts/astrbot init -y
.venv/Scripts/astrbot run
```

验证：`curl -o NUL -w "%{http_code}" http://127.0.0.1:6185/` 返回 200。

### 2. NapCat
用官方 `NapCat.Shell.Windows.OneKey.zip`，**必须校验 sha256 与 GitHub release 的 digest 一致**。它会下载约 300MB 的 NTQQ 并复制成独立副本，不影响本机大号。

验证：`D:\...\NapCat.XXXX.Shell\versions\<build>` 下有 1000+ 文件、约 1GB。

### 3. 打通链路
- NapCat 侧：`config/onebot11.json`（模板，登录后复制成 `onebot11_<uin>.json`）里配 `websocketClients` 指向 `ws://127.0.0.1:6199/ws` + token。
- AstrBot 侧：`platform` 数组加 `{"type":"aiocqhttp","ws_reverse_host":"127.0.0.1","ws_reverse_port":6199,"ws_reverse_token":"<同一 token>"}`。

验证：`Get-NetTCPConnection -State Established | ? {$_.RemotePort -eq 6199}` 有记录，且 AstrBot 日志出现 `适配器已连接`。

### 4. 人格
人格存在 SQLite `personas` 表（`persona_id` + `system_prompt`），不是在 JSON 里。把 `agent_runner.config.persona.persona_id` 指向它。

**模型不决定身份，system prompt 决定。** 不写人格就会自称"AstrBot 助手"并复述工具清单。

### 5. 长期记忆
链路：`对话 → 整理 → embedding → 向量库 → 按语义召回`。必须单独配 embedding provider（推荐 `BAAI/bge-m3`，1024 维）。

二选一：云端（硅基流动，免费额度，零下载）或本地（Ollama + bge-m3，约 2.7GB 下载）。

### 6. 语音
`Edge TTS` 是零成本零显存的默认解，但**必须装 ffmpeg**：AstrBot 合成 MP3 后要转 WAV，缺 ffmpeg 会静默回退成文字（日志里是 `TTS failed; sending text instead`）。

`trigger_probability` 控制频率：`1.0` 每条、`0.33` 三条一次、`0.1` 十条一次。

### 7. 表情包 / 空间 / 签名
都靠插件 + `tool_permissions` 限制成 `admin`，避免陌生人让机器人发东西。

### 8. 自启与守护
计划任务注册常因 UAC 弹不出来而失败。更稳的是**启动文件夹 + 常驻守护脚本**：
- `%APPDATA%\...\Startup\*.vbs` 静默启动 PowerShell 守护进程
- 守护每 5 分钟检查进程，只在状态变化时写日志

验证：`Stop-Process -Name astrbot -Force`，等一轮，看进程是否被拉起。

## 已知坑（按踩到的顺序）

完整清单见 `reference/pitfalls.md`，这里是最致命的几条：

| 现象 | 根因 | 修法 |
|---|---|---|
| `.bat` 满屏 `is not recognized as an internal or external command` | 文件带 UTF-8 BOM，cmd 把 `\xEF\xBB\xBF` 当命令 | 写文件时用无 BOM 编码 |
| `.bat` 里 `set "NAPCA...` 被吞掉几个字符 | LF 换行，cmd 只认 CRLF | 全部改用 `\r\n` |
| PowerShell 里 `&&` 报 `InvalidEndOfLine` | `&&` 是 cmd 语法 | 用 `;` 或分两行 |
| 配置改了但没生效 / 又被覆盖 | AstrBot 运行中会用内存态回写 `cmd_config.json` | **先停进程再改配置** |
| NapCat WebUI 的 token 怎么填都 invalid | 启动时 host 绑定失败走 catch 分支，token 被换成随机值 | 改 `webui.json` 的 host 后重启（需重扫码） |
| 语音哑了但日志没明显报错 | 缺 ffmpeg，静默回退文字 | 装 ffmpeg 并进 PATH，重启 |
| 手机打不开但本机访问局域网 IP 仍通 | 本机访问自己 IP 走回环，不过防火墙 | 用另一台设备验证 |

## 参考

- `reference/architecture.md` — 组件职责、数据流、端口与进程对照
- `reference/pitfalls.md` — 完整踩坑清单（含命令行/日志特征）
- `reference/config-map.md` — 关键配置项 → 作用 → 推荐值
- `scripts/setup.py` — 一键配置：安全加固 + 平台 + 模型 + 语音 + 白名单（幂等，先停进程再跑）
- `scripts/keepalive.ps1` — 守护进程（每 N 秒检查，进程死了自动拉起，只在状态变化时写日志）
- `templates/onebot11.example.json` — NapCat 反向 WS 模板（脱敏，token 需与 AstrBot 侧一致）
- `templates/persona.md` — 人格模板（含防提示注入、短句节奏约束）
- `templates/startup-keeper.vbs` — 开机启动项（免管理员的静默拉起）
