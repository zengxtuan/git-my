# 本地端（Windows）完整流程

在本机把 AstrBot + NapCat 跑起来，接大模型，装齐插件。全程不需要服务器。

## 0. 环境检查

需要：

| 项 | 版本 | 验证 |
|---|---|---|
| Python | ≥ 3.12 | `python -c "import sys;print(sys.version)"` |
| Node.js | ≥ 18（NapCat 自带时可不装） | `node -v` |
| ffmpeg | 任意新版本 | `ffmpeg -version` |
| 磁盘 | 留 5GB 以上 | |

**ffmpeg 必须装。** AstrBot 合成语音后要转格式，缺了会静默退回文字，日志里只有一句 `TTS failed; sending text instead`。

安装路径建议用用户目录（`D:\你\AstrBot`），D 盘根目录通常需要管理员权限。

## 1. 装 AstrBot

用独立 venv，不要污染系统 Python。

```powershell
mkdir D:\Users\你\AstrBot
cd D:\Users\你\AstrBot
python -m venv .venv
.venv\Scripts\python -m pip install -U pip
.venv\Scripts\python -m pip install astrbot -i https://pypi.tuna.tsinghua.edu.cn/simple
.venv\Scripts\astrbot init -y
.venv\Scripts\astrbot run
```

**验证**：浏览器打开 `http://127.0.0.1:6185/`，出现登录页就算成功。
首次启动会打印初始后台密码，记下来。

## 2. 装 NapCat

用官方 `NapCat.Shell.Windows.OneKey.zip`。

**必须校验 sha256** 与 GitHub release 页面上的 digest 一致，再解压。

它会下载约 300MB 的 NTQQ 并复制成独立副本，不影响本机大号。

**验证**：`NapCat.XXXX.Shell\versions\<build>` 下有 1000+ 文件、约 1GB。

## 3. 打通链路

### NapCat 侧

`config/onebot11.json`（模板，登录后会复制成 `onebot11_<uin>.json`）：

```json
{
  "network": {
    "websocketClients": [
      { "name": "astrbot", "enable": true, "url": "ws://127.0.0.1:6199/ws", "token": "换成你的token" }
    ]
  }
}
```

### AstrBot 侧

后台 → 平台 → 新增 `aiocqhttp`：

```json
{
  "id": "qq",
  "type": "aiocqhttp",
  "enable": true,
  "ws_reverse_host": "127.0.0.1",
  "ws_reverse_port": 6199,
  "ws_reverse_token": "同一个token"
}
```

**验证**：

```powershell
Get-NetTCPConnection -State Established | ? {$_.RemotePort -eq 6199}
```

有记录，且 AstrBot 日志出现 `适配器已连接`。

## 4. 配模型（这一步决定后面所有工具能不能用）

后台 → 服务提供商 → 新建 OpenAI 兼容 provider：

```json
{
  "id": "deepseek",
  "enable": true,
  "model": "deepseek-flash",
  "provider_source_id": "deepseek_source",
  "modalities": ["text", "image", "tool_use"],
  "custom_extra_body": { "thinking": { "type": "disabled" } }
}
```

**两个关键点：**

1. `modalities` 必须含 **`tool_use`**。漏了的话，AstrBot 每轮都会把工具表清空，机器人会反复说"我没有工具"。
2. `thinking: disabled` 是 DeepSeek 专有的：默认开着思考模式，闲聊场景没必要，关掉更快更省。

**验证**：故意问一句需要工具的话（比如"帮我查下今天几号"），日志里应该出现 `Agent 使用工具: [...]`。
没出现就读 [../troubleshooting/tools-not-sent.md](../troubleshooting/tools-not-sent.md)。

## 5. 安全设置

后台 → 配置：

```json
{
  "platform_settings": {
    "enable_id_white_list": true,
    "id_whitelist": ["qq:FriendMessage:你的QQ号"]
  },
  "admins_id": ["你的QQ号"],
  "dashboard": { "host": "127.0.0.1" },
  "provider_settings": { "computer_use_runtime": "none" }
}
```

`computer_use_runtime` 必须是 `none`。这项打开等于给模型一只能读写本机、能跑命令的手。

## 6. 装插件

插件目录：`data/plugins/<插件名>/`，每个插件至少要有 `metadata.yaml` + `main.py`。

### face_bridge（表情/照片）

见 [../plugins/face-bridge.md](../plugins/face-bridge.md)。

```powershell
git clone <本仓库> D:\tmp\repo
copy D:\tmp\repo\src\astrbot_plugin_face_bridge .\data\plugins\astrbot_plugin_face_bridge -Recurse
```

restart 后 `data/plugin_data/astrbot_plugin_face_bridge/media.json` 会自动生成。

### qq_power（操作 QQ）

见 [../plugins/qq-power.md](../plugins/qq-power.md)。同样复制到 `data/plugins/`。

### local_embedding（免费向量服务，可选）

想在 Windows 上跑长期记忆的向量检索，见 [../plugins/local-embedding.md](../plugins/local-embedding.md)。
不想折腾可以先跳过，不影响对话。

## 7. 人格

人格存在 SQLite `data/data_v4.db` 的 `personas` 表（`persona_id` + `system_prompt`），**不是 JSON**。

后台 → 人格 → 新建，把 `agent_runner.config.persona.persona_id` 指向它。

**模型不决定身份，system prompt 决定。** 不写人格，它会自称"AstrBot 助手"并复述工具清单。

人格里建议显式写清"你能做什么"，否则模型不知道自己有工具：

```
【你自己能做的事】
- 改昵称、换头像、查好友、放行谁能跟你说话——这些你都能自己做，调 qq_do 就行。
- 主人让你做的时候直接做，别说「我这边动不了」。
```

## 8. 让它像人：分段回复

默认一整段发出去，像客服。打开分段：

```json
"segmented_reply": {
  "enable": true,
  "only_llm_result": true,
  "words_count_threshold": 300,
  "interval": "1.0,2.2",
  "split_mode": "regex",
  "regex": ".*?[。？！~…\n]+|.+$"
}
```

**反直觉的地方**：`words_count_threshold` 是上限，**超过它反而不切**。设 150 会让稍长的回复整段落下来，设 300 才切得开。

正则里加了 `\n`，所以人格里可以教它"想分两条就换行"。

## 9. 自启与守护

计划任务注册常因 UAC 弹不出来而失败。更稳的是启动文件夹 + 守护脚本：

- `%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\*.vbs` 静默启动 PowerShell 守护进程
- 守护每 5 分钟检查进程，只在状态变化时写日志

脚本见 `skills/dsh-qq/scripts/keepalive.ps1`。

**验证**：`Stop-Process -Name astrbot -Force`，等一轮，看进程是否被拉起。

## 10. 本地端高频坑

| 现象 | 根因 | 修法 |
|---|---|---|
| `.bat` 满屏 `is not recognized` | 文件带 UTF-8 BOM | 写文件用无 BOM 编码 |
| `.bat` 里变量名被吞几个字符 | LF 换行 | 全改 CRLF |
| PowerShell 里 `&&` 报错 | `&&` 是 cmd 语法 | 用 `;` |
| 改了配置不生效 / 被覆盖 | AstrBot 运行中用内存态回写配置 | **先停进程再改** |
| NapCat WebUI token 怎么填都 invalid | 启动时 host 绑定失败走 catch，token 被换成随机值 | 改 `webui.json` 的 host 后重启（需重扫码） |
| 语音哑了但没报错 | 缺 ffmpeg | 装 ffmpeg 并进 PATH |
| 手机打不开但本机访问局域网 IP 通 | 本机访问自己走回环，不过防火墙 | 用另一台设备验证 |
| 机器人说"我没有工具" | provider 的 `modalities` 漏了 `tool_use` | 见第 4 节 |
