# 云端部署（Linux 服务器）

把同一套 AstrBot + NapCat 搬到云服务器上，摆脱"电脑必须一直开着"。

实测环境：腾讯云轻量 4 核 4G / Ubuntu 22.04 / 上海，2026-10 完成，总耗时约 2 小时。

## 为什么值得搬

| | 本地 Windows | 云服务器 |
|---|---|---|
| 电脑关机/睡眠 | ❌ 机器人离线 | ✅ 照常运行 |
| 崩溃恢复 | 靠脚本轮询 | ✅ systemd 自动重拉 |
| 电费/温度 | 24h 开机有代价 | 与你无关 |
| 成本 | — | 约 109 元/年 |

## 0. 服务器选型

腾讯云轻量应用服务器活动页 `https://cloud.tencent.com/act/pro/lhsale`，选「新用户专享」的 **4核4G3M**（约 109 元/年）。

配置要点：

- **地域**：国内（上海/广州/北京），连腾讯 QQ 服务器快
- **镜像**：**Ubuntu 22.04 LTS**，不要 Windows（多一份授权费，4G 也跑不动）
- **带宽**：3M 够文字聊天，发语音/表情包略慢

> 2核2G 会跑到 90% 内存，QQ 抽风就 OOM。4G 是舒适线。

## 1. 基础环境

### 换源（必做，否则慢 100 倍）

```bash
cp /etc/apt/sources.list /etc/apt/sources.list.bak
sed -i 's@//.*archive.ubuntu.com@//mirrors.tencent.com@g; s@//.*security.ubuntu.com@//mirrors.tencent.com@g' /etc/apt/sources.list
apt update -y
```

### 装 Python 3.12

Ubuntu 22.04 自带 3.10，而 AstrBot 4.29+ 要求 ≥3.12。

```bash
apt install -y software-properties-common
add-apt-repository -y ppa:deadsnakes/ppa
apt update
apt install -y python3.12 python3.12-venv python3.12-dev
python3.12 --version   # 应显示 3.12.x
```

> 系统自带的 3.10 不能删，Ubuntu 自己的工具依赖它。

## 2. 装 AstrBot

```bash
mkdir -p /opt/astrbot && cd /opt/astrbot
python3.12 -m venv venv
source venv/bin/activate
pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple
pip install astrbot
astrbot init -y
astrbot --version      # 4.29.x
```

首次 `astrbot run` 会打印初始后台密码，记下来。

### 修插件市场地址（关键）

AstrBot 4.29 里写死的市场地址已失效，返回 301 且不跟随：

```
Failed to get online plugin list: Redirect response '301 Moved Permanently'
for url 'https://api.soulter.top/astrbot/plugins'
Redirect location: 'https://cloud.astrbot.app/api/v1/market/plugins.json'
```

改源码里的地址（注意保留 `plugins-md5` 那个不动，用带引号的精确匹配）：

```bash
sed -i 's#https://api.soulter.top/astrbot/plugins"#https://cloud.astrbot.app/api/v1/market/plugins.json"#g' \
  /opt/astrbot/venv/lib/python3.12/site-packages/astrbot/cli/utils/plugin.py \
  /opt/astrbot/venv/lib/python3.12/site-packages/astrbot/dashboard/services/plugin_service.py
```

改完 `plugins.json` 能下载，但 **CLI 仍装不了**——新市场返回的 JSON 结构变了：

```
旧：{"plugins": [{"name": "...", ...}]}
新：{"$meta": {...}, "作者/插件名": {"name":..., "download_url":...}}
```

所以插件要按下面的方式手动装。

## 3. 装插件（绕过 CLI）

### 拿插件列表和下载地址

```bash
curl -s https://cloud.astrbot.app/api/v1/market/plugins.json -o /tmp/plugins.json
python3 -c "import json;d=json.load(open('/tmp/plugins.json'));[print(v['name'],v['download_url']) for v in d.values() if isinstance(v,dict) and v.get('name') in ('meme_manager','astrbot_plugin_livingmemory','astrbot_plugin_qzone','astrbot_plugin_proactive_chat','astrbot_plugin_group_chat_plus','astrbot_plugin_qqprofile')]" > /tmp/urls.txt
```

### 批量下载安装

```bash
while read n u; do
  [ -d /opt/astrbot/data/plugins/$n ] && { echo "SKIP $n"; continue; }
  echo "GET $n"
  curl -sL -o /tmp/p.zip "$u" \
    && rm -rf /tmp/x && mkdir -p /tmp/x \
    && unzip -q -o /tmp/p.zip -d /tmp/x \
    && mv /tmp/x/* /opt/astrbot/data/plugins/$n \
    && echo "  OK $n"
done < /tmp/urls.txt
```

**两个坑**：

1. **必须用 `curl` 下载，不能用 Python urllib** —— S3 有 UA 防盗链，`Python-urllib/3.12` 直接 403，`curl/8.x` 放行。要用 Python 得手动加 `User-Agent: curl/8.5.0`。
2. **zip 里顶层目录带哈希**（`astrbot_plugin_xxx-2.8.2-a1e9c20a.zip` 解压出同名目录），必须重命名成纯插件名，否则 AstrBot 认不出。

## 4. 装 NapCat

GitHub 直连不通，用镜像下安装脚本：

```bash
curl -sL https://gh-proxy.com/https://raw.githubusercontent.com/NapNeko/NapCat-Installer/main/script/install.sh -o napcat-install.sh
bash napcat-install.sh --docker n --cli y --github-proxy https://gh-proxy.com/
```

安装过程中可能弹 `Which services should be restarted?`：

- **列表里有 `ssh.service` → 必须选 Cancel**（重启 SSH 会断开你的终端）
- 没有 ssh 就可以按回车（Ok）

装完启动（Linux 无显示器，必须用 Xvfb）：

```bash
screen -dmS napcat xvfb-run -a /root/Napcat/opt/QQ/qq --no-sandbox
```

二维码在：

```
/root/Napcat/opt/QQ/resources/app/app_launcher/napcat/cache/qrcode.png
```

把它弄出来扫（临时开 80 端口服务，用完立刻关）：

```bash
cd /root/Napcat/opt/QQ/resources/app/app_launcher/napcat/cache/
python3 -m http.server 80        # 然后浏览器访问 http://<公网IP>/qrcode.png
```

## 5. 打通与配置

### NapCat 侧（反向 WS 指向 AstrBot）

编辑 `/root/Napcat/opt/QQ/resources/app/app_launcher/napcat/config/onebot11_<QQ号>.json`：

```json
"websocketClients": [{
  "name": "AstrBot", "enable": true,
  "url": "ws://127.0.0.1:6199/ws",
  "token": "<随机32位hex，与 AstrBot 侧一致>",
  "messagePostFormat": "array",
  "heartInterval": 30000, "reconnectInterval": 5000
}]
```

改完重启 NapCat 才生效（**会掉登录，需要重扫码**）。

### AstrBot 侧

`data/cmd_config.json`（**带 UTF-8 BOM，读写一律 `utf-8-sig`**）需要写：

- `platform`：`aiocqhttp` + `127.0.0.1:6199` + 同一个 token
- `provider_sources` / `provider`：对话模型 + embedding + TTS
- `platform_settings.id_whitelist`：`["qq:FriendMessage:<你的QQ>"]`
- `admins_id`：`["<你的QQ>"]`
- `dashboard.host`：`127.0.0.1`
- `tool_permissions`：`{"llm_publish_feed":"admin","llm_view_feed":"admin","set_signature":"admin"}`

**改配置前必须先停进程**，否则被内存态回写覆盖。

验证：`ss -tn | grep 6199` 有 2 条 ESTAB 连接 + 日志出现 `适配器已连接`。

### 迁移人格（从本地搬）

人格在 SQLite `data_v4.db` 的 `personas` 表。小文本走 gzip+base64 传：

```bash
# 本地
python3 -c "..."   # 读出 system_prompt，gzip 压缩，base64 编码
# 服务器
echo '<base64>' | base64 -d | gunzip > /tmp/persona.txt
```

然后写库 + 设默认：

```python
db.execute("insert into personas (created_at,updated_at,persona_id,system_prompt,begin_dialogs,tools,skills,sort_order) values (datetime('now'),datetime('now'),?,?,'[]','null','null',0)", ("人格名", prompt))
# 并把 cmd_config.json 的 agent_runner.config.persona.persona_id 指向它
```

### 语音

```bash
apt install -y ffmpeg
/opt/astrbot/venv/bin/pip install edge-tts -i https://pypi.tuna.tsinghua.edu.cn/simple
```

然后加 `edge_tts` provider + `provider_tts_settings.enable=true`。

**缺 ffmpeg 时语音会静默回退成文字**，日志里是 `TTS failed; sending text instead`。

### 表情包

在 WebUI 里操作：插件 → meme_manager → 「打开」→ 资源广场 → 搜索 → 安装。

如果报 `加速源 HTTP 403 / GitHub 原生源 HTTP 429`，改加速器地址：

```
meme_manager 配置 → community.github_accelerator_url → https://gh-proxy.com/
```

## 6. systemd 托管（关键）

手动 `nohup` 跑的东西，服务器一重启就没了。

`/etc/systemd/system/astrbot.service`：

```ini
[Unit]
Description=AstrBot QQ Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/astrbot
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONIOENCODING=utf-8
ExecStart=/opt/astrbot/venv/bin/astrbot run
Restart=always
RestartSec=10
StandardOutput=append:/opt/astrbot/run.log
StandardError=append:/opt/astrbot/run.err.log

[Install]
WantedBy=multi-user.target
```

`/etc/systemd/system/napcat.service`：

```ini
[Unit]
Description=NapCat QQ Client
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory=/root/Napcat
Environment=HOME=/root
ExecStart=/usr/bin/xvfb-run -a /root/Napcat/opt/QQ/qq --no-sandbox
Restart=always
RestartSec=15

[Install]
WantedBy=multi-user.target
```

启用：

```bash
systemctl daemon-reload
systemctl enable --now astrbot
systemctl enable --now napcat
```

**启用前先杀掉手动跑的进程**，否则两个抢同一个端口：

```bash
systemctl stop astrbot; pkill -f "astrbot run"; sleep 3; systemctl start astrbot
ps aux | grep -c "[a]strbot run"    # 必须是 1
```

## 7. 安全收尾（必做）

### 后台只绑本机

```bash
python3 -c "import json,pathlib;p=pathlib.Path('/opt/astrbot/data/cmd_config.json');c=json.loads(p.read_text(encoding='utf-8-sig'));c['dashboard']['host']='127.0.0.1';p.write_text(json.dumps(c,ensure_ascii=False,indent=2),encoding='utf-8-sig')"
systemctl restart astrbot
```

**为什么必须做**：本地部署时 `0.0.0.0` 无所谓，云服务器上 `0.0.0.0:6185` = 全世界能打开你的管理后台。

### 腾讯云防火墙

白名单模式，只放行：

```
22   SSH
80   HTTP（临时用）
Ping
```

**绝对不要放行 6099**（NapCat WebUI，监听 `*:6099`，能直接操作 QQ 本体）。

需要临时开 WebUI 时：防火墙加一条规则，来源填**你自己的 IP**（不是 `0.0.0.0/0`），用完立刻删。

## 8. 最终体检

```bash
systemctl is-active astrbot napcat
ss -tlnp | grep -E "6185|6199|6099"
ss -tn | grep -c 6199
ls /opt/astrbot/data/plugins | wc -l
python3 -c "import json;c=json.load(open('/opt/astrbot/data/cmd_config.json',encoding='utf-8-sig'));print('host=',c['dashboard']['host']);print('computer_use=',c['provider_settings']['computer_use_runtime']);print('whitelist=',c['platform_settings']['id_whitelist'])"
```

预期：

```
active / active
6185 → 127.0.0.1 ；6199 → 127.0.0.1 ；6099 → *:6099（靠防火墙挡）
2
6
host= 127.0.0.1 ；computer_use= none ；whitelist= [...]
```

## 云端特有的坑

| 现象 | 根因 | 修法 |
|---|---|---|
| `raw.githubusercontent.com` 返回 0 字节 | 国内直连不通 | 用 `gh-proxy.com` 前缀 |
| 插件市场 301 后找不到插件 | 市场返回结构变了 | 见上文，手动按 `download_url` 装 |
| 下载插件包 403 | S3 防盗链查 UA | 用 `curl`，或 Python 里加 `User-Agent: curl/8.5.0` |
| `apt` 弹窗要重启 ssh | needrestart 检测到库更新 | **有 ssh 就选 Cancel** |
| AstrBot 启动后终端卡住 | `nohup` 没断开 stdin | 用 `setsid ... < /dev/null &` |
| 两个 AstrBot 抢端口 | 手动进程 + systemd 并存 | `pkill` 后再 `systemctl start` |
| 后台密码登录失败 | 复制出错/自动填充 | 用 pbkdf2 脚本重置 |
| 长命令粘贴变 `base64: invalid input` | 终端截断 | 拆短，或减少脚本体积 |
| `python -c` 读配置报 BOM 错 | AstrBot 的 JSON 带 BOM | `encoding='utf-8-sig'` |
| 命令报 `ParserError` / `&&不是有效分隔符` | 敲在了 Windows 而不是服务器 | 看提示符区分，见 `pitfalls.md` 15.1 |
| `base64: invalid input` 且解出的代码有怪符号 | 手工重排过 base64 | 重新生成，不许编辑，见 `pitfalls.md` 15.3 |
| 长命令执行到一半断掉 | 网页终端单行长度限制 | 压到 1500 字符内或拆步，见 `pitfalls.md` 15.4 |

## 成本与风险

**成本**：服务器约 109 元/年；API 按量（对话用 deepseek-flash 很便宜）。

**唯一仍需人工的场景**：QQ 掉线要重新扫码。服务器重启、进程崩溃、网络抖动都能自愈。

**结构性风险**：第三方协议端（NapCat）长期在线，腾讯风控无法根治。用小号、低频、稳定运行能降低触发概率，但不能归零。
