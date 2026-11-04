---
name: dsh-qq
description: Use when deploying an LLM into QQ on Windows or Linux (AstrBot + NapCat), or when fixing long-term memory, TTS voice, sticker packs, Qzone posting, personality drift, or a bot/service that silently fails to start in such a deployment.
---

# AstrBot + NapCat QQ 机器人部署

把一个 OpenAI 兼容的大模型接进 QQ 小号，并挂上长期记忆、语音、表情包、空间动态、定时自启。

---

## 风险告知（先读这个）

**用小号不等于没有风险。**

* 第三方协议端（NapCat）的原理是注入 NTQQ 客户端，**这不是腾讯官方支持的接入方式**，随时可能被判为异常。
* 小号被风控的后果是渐进的：**限制发言 → 限制登录 → 永久封禁**。封了就是封了，没有申诉通道。
* 常见的触发因素：账号太新、异地登录、发言频率异常、群发、被人举报、同 IP 多号。
* **主号绝对不能碰。** 主号一旦被风控，绑定的微信、支付、好友关系、群聊身份全部受影响，且不可逆。这不是"小心一点就行"的事，是"碰了就可能失去账号"的事。
* 本项目不提供、也不承诺任何规避风控的手段。用不用、用哪个号、承担什么后果，由使用者自己决定。

**如果接受不了"小号可能被永久封禁"，就不要做这件事。**

---

## 硬约束（动手前必须确认）

* **只用 QQ 小号。** 第三方协议端登录主号 = 主号直接暴露在风控下，不可逆。
* **三个端口只绑 `127.0.0.1`**：AstrBot 后台 6185、OneBot 反向 WS 6199、NapCat WebUI 6099。6099 尤其危险——它能直接操作 QQ 本体。云端部署时这条必须做（本地 `0.0.0.0` 无所谓，公网上 `0.0.0.0` 等于把后台挂在互联网上）。
* **ID 白名单必须开启并填真实 ID**，否则任何陌生人都能调用模型烧余额。
* **`computer\_use\_runtime` 必须是 `none`**，且 `computer\_use\_local\_permissions` 的 `member`/`admin` 都要 `allow\_execution=false`、`allow\_network=false`。这项打开等于给模型一只能读写本机、能跑命令的手。
* **密钥不落仓库。** 运行时凭证写进实例的 `secrets/` 或 `data/cmd\_config.json`，仓库里只放模板与占位符。

## 架构（谁控制谁）

```
QQ 小号(NTQQ) ←注入← NapCat ←反向WS→ AstrBot → 模型 API
                              ↓
                     插件：记忆/表情/语音/空间
```

* **AstrBot 是唯一有决策权的主控**：人格、插件、模型、白名单、会话全在它这。
* NapCat 是哑管道 + 执行器：翻译 OneBot 事件、执行 `set\_qq\_avatar` / `send\_msg` 一类动作。
* 两个后台分工：`6185` 管 AI 怎么想怎么说；`6099` 能操作 QQ 本体，是高危入口。

细节见 `reference/architecture.md`。

### 两种部署形态

| | 本地 Windows | 云服务器 Linux |
|---|---|---|
| 电脑关机/睡眠 | ❌ 机器人离线 | ✅ 照常运行 |
| 崩溃恢复 | 轮询脚本 | systemd 自动重拉 |
| 开机不登录 | ❌ 不会启动（见第 8 阶段） | ✅ systemd 不需要登录 |
| 成本 | 电费 + 温度 | 约 109 元/年（4核4G） |
| 复杂度 | 低（本机操作） | 中（要会 SSH/命令行） |

**只有"电脑不能一直开着"时才有必要上云。** 本地能解决就别折腾服务器。
云端完整步骤见 `reference/cloud-linux.md`，下文标注 `[Linux]` 的段落在那份文档里。

---

## 分阶段流程

每阶段结束都要跑验证，别攒到最后一起调。
标注说明：**`[Win]` 只适用于 Windows 本地；`[Linux]` 见 `reference/cloud-linux.md`；`[通用]` 两边一样。**

### 0. 环境检查 `[通用]`

* Python ≥ 3.12、Node 可用、目标盘可写（D 盘根目录通常需要管理员，用用户目录）。
* **磁盘余量：Windows 留 10GB 以上**，NapCat 会把 NTQQ 复制成独立副本（约 1GB），加上 Python 环境、插件、表情包资源（一套 100+ 张可到几百 MB），5GB 只是勉强够，装到一半空间不足很难排查。
* Linux 上 20G 系统盘起步（云端见 `cloud-linux.md`）。

验证：`python -c "import sys;print(sys.version)"`

### 1. AstrBot `[通用]`

装法优先级：`pip install astrbot`（PyPI 有正式包）> 源码 > 第三方启动器。用独立 venv，别污染系统 Python。

```bash
# Windows
python -m venv .venv
.venv/Scripts/python -m pip install astrbot -i <镜像源>
.venv/Scripts/astrbot init -y
.venv/Scripts/astrbot run
```

```bash
# Linux（需要 3.12，Ubuntu 22.04 自带 3.10 不够）
python3.12 -m venv venv
venv/bin/pip install astrbot
venv/bin/astrbot init -y
venv/bin/astrbot run
```

验证：访问 `http://127.0.0.1:6185/` 返回 200。

### 2. NapCat `[Win]` / `[Linux]`

**Windows**：用官方 `NapCat.Shell.Windows.OneKey.zip`，**必须校验 sha256 与 GitHub release 的 digest 一致**。它会下载约 300MB 的 NTQQ 并复制成独立副本，不影响本机大号。

验证：`D:\...\NapCat.XXXX.Shell\versions\<build>` 下有 1000+ 文件、约 1GB。

**Linux**：用官方安装脚本，国内需走镜像：

```bash
curl -sL https://gh-proxy.com/https://raw.githubusercontent.com/NapNeko/NapCat-Installer/main/script/install.sh -o napcat-install.sh
bash napcat-install.sh --docker n --cli y --github-proxy https://gh-proxy.com/
```

服务器没有显示器，必须用 Xvfb 跑：`xvfb-run -a /root/Napcat/opt/QQ/qq --no-sandbox`

### 3. 打通链路 `[通用]`

* NapCat 侧：`config/onebot11.json`（模板，登录后复制成 `onebot11_<uin>.json`）里配 `websocketClients` 指向 `ws://127.0.0.1:6199/ws` + token。
* AstrBot 侧：`platform` 数组加 `{"type":"aiocqhttp","ws_reverse_host":"127.0.0.1","ws_reverse_port":6199,"ws_reverse_token":"<同一 token>"}`。

验证：
- Windows：`Get-NetTCPConnection -State Established | ? {$_.RemotePort -eq 6199}`
- Linux：`ss -tn | grep 6199`（应有 2 条 ESTAB）
- 两边都看 AstrBot 日志出现 `适配器已连接`

### 4. 人格 `[通用]`

人格存在 SQLite `personas` 表（`persona_id` + `system_prompt`），**不是在 JSON 里**。把 `agent_runner.config.persona.persona_id` 指向它。

**模型不决定身份，system prompt 决定。** 不写人格就会自称"AstrBot 助手"并复述工具清单。

#### 怎么查看和修改

**推荐走后台**：`http://127.0.0.1:6185` → 人格页面，能直接编辑、保存、立即生效。这是最不容易出错的方式。

**CLI 排查/批量改**（数据库路径 `data/data_v4.db`）：

```bash
# 看有哪几个人格、各多长
sqlite3 data/data_v4.db "SELECT persona_id, length(system_prompt) FROM personas;"

# 看某个人的开头（确认是不是你要那个）
sqlite3 data/data_v4.db "SELECT substr(system_prompt,1,200) FROM personas WHERE persona_id='xxx';"
```

```python
# 改文本（用 Python，避免 SQL 里的引号转义地狱）
import sqlite3
con = sqlite3.connect("data/data_v4.db")
con.execute("UPDATE personas SET system_prompt=? WHERE persona_id=?", ("新的人格文本", "xxx"))
con.commit()
con.close()
```

**注意**：
- 数据库是 SQLite，AstrBot 运行时可能持有连接，**改之前先停进程**（`systemctl stop astrbot` / 关掉 Windows 进程）。
- 改完要重启 AstrBot 才生效（人格缓存在内存里）。
- 配置里的 `persona_id` 必须和表里的 `persona_id` **完全一致**，差一个字就退回默认人格。

### 5. 长期记忆 `[通用]`

链路：`对话 → 整理 → embedding → 向量库 → 按语义召回`。必须单独配 embedding provider（推荐 `BAAI/bge-m3`，1024 维）。

二选一：云端（硅基流动，免费额度，零下载）或本地（Ollama + bge-m3，约 2.7GB 下载）。

**注意模型边界**：DeepSeek 官方 API **没有 embedding 端点**（只有 chat 和 search），所以"用 DeepSeek 顺便做向量"这条路不存在，必须另找 embedding 服务。

### 6. 语音 `[通用]`

`Edge TTS` 是零成本零显存的默认解，但**必须装 ffmpeg**：AstrBot 合成 MP3 后要转 WAV，缺 ffmpeg 会静默回退成文字（日志里是 `TTS failed; sending text instead`）。

`trigger_probability` 控制频率：`1.0` 每条、`0.33` 三条一次、`0.1` 十条一次。

### 7. 表情包 / 空间 / 签名 `[通用]`

都靠插件 + `tool_permissions` 限制成 `admin`，避免陌生人让机器人发东西。

表情包的配置细节、自动收集、以及"存进 QQ 收藏表情"的 NapCat API，见 `reference/meme-and-tools.md`。

### 8. 自启与守护 `[Win]` / `[Linux]`

**Windows**：计划任务注册常因 UAC 弹不出来而失败。更稳的是**启动文件夹 + 常驻守护脚本**：

* `%APPDATA%\...\Startup\*.vbs` 静默启动 PowerShell 守护进程
* 守护每 5 分钟检查进程，只在状态变化时写日志

验证：`Stop-Process -Name astrbot -Force`，等一轮，看进程是否被拉起。

> ⚠️ **这个方法只在用户登录后才运行。** 机器重启停在登录界面时，机器人不会启动。
> 要真正的开机自启（不依赖登录），Windows 上得用「计划任务 + 触发器选"启动时" + 勾选"不管用户是否登录都要运行" + 填账号密码」；或者干脆上 Linux + systemd（`systemctl enable` 即可，不需要任何人登录）。

**Linux**：systemd，见 `reference/cloud-linux.md` 第 6 节。`Restart=always` + `enable`，崩溃自动重拉，开机不需要登录。

---

## 已知坑（按踩到的顺序）

完整清单见 `reference/pitfalls.md`，这里是最致命的几条：

|现象|根因|修法|
|-|-|-|
|`.bat` 满屏 `is not recognized as an internal or external command`|文件带 UTF-8 BOM，cmd 把 `\xEF\xBB\xBF` 当命令|写文件时用无 BOM 编码|
|`.bat` 里 `set "NAPCA...` 被吞掉几个字符|LF 换行，cmd 只认 CRLF|全部改用 `\r\n`|
|PowerShell 里 `&&` 报 `InvalidEndOfLine`|`&&` 是 cmd 语法|用 `;` 或分两行|
|配置改了但没生效 / 又被覆盖|AstrBot 运行中会用内存态回写 `cmd_config.json`|**先停进程再改配置**|
|NapCat WebUI 的 token 怎么填都 invalid|启动时 host 绑定失败走 catch 分支，token 被换成随机值|改 `webui.json` 的 host 后重启（需重扫码）|
|语音哑了但日志没明显报错|缺 ffmpeg，静默回退文字|装 ffmpeg 并进 PATH，重启|
|手机打不开但本机访问局域网 IP 仍通|本机访问自己 IP 走回环，不过防火墙|用另一台设备验证|
|**重启机器后机器人没起来**|启动文件夹的方案**只在用户登录后**才执行；停在登录界面 = 不启动|改用「启动时触发 + 不管用户是否登录」的计划任务，或上 Linux/systemd|
|Linux 上 `raw.githubusercontent.com` 返回 0 字节|国内直连不通|命令前加 `https://gh-proxy.com/` 前缀|

## 参考

* `reference/architecture.md` — 组件职责、数据流、端口与进程对照
* `reference/pitfalls.md` — 完整踩坑清单（含命令行/日志特征）
* `reference/config-map.md` — 关键配置项 → 作用 → 推荐值
* `reference/cloud-linux.md` — **云端部署**：Linux 服务器完整步骤（摆脱"电脑必须一直开着"）
* `reference/meme-and-tools.md` — **表情包 / 图像理解 / 工具调用**：meme_manager 全配置、NapCat 收藏表情 API、DSML 泄漏、DeepSeek 无 embedding
* `scripts/setup.py` — 一键配置：安全加固 + 平台 + 模型 + 语音 + 白名单（幂等，先停进程再跑）
* `scripts/keepalive.ps1` — 守护进程（每 N 秒检查，进程死了自动拉起，只在状态变化时写日志）
* `templates/onebot11.example.json` — NapCat 反向 WS 模板（脱敏，token 需与 AstrBot 侧一致）
* `templates/persona.md` — 人格模板（含防提示注入、短句节奏约束）
* `templates/startup-keeper.vbs` — 开机启动项（免管理员的静默拉起，**注意只在登录后运行**）
