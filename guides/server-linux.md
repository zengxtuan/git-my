# 服务器端（Linux）完整流程

在云服务器上把 AstrBot + NapCat 跑成常驻服务，关掉本地电脑也不影响。

本文以 Ubuntu 22.04 / 4核4G 轻量服务器为例，其他 VPS 同理。

## 0. 前置

| 项 | 建议 |
|---|---|
| 系统 | Ubuntu 22.04 LTS |
| 配置 | 2核2G 起步，4核4G 舒适（要跑本地 embedding 建议 4G） |
| 磁盘 | 40G 起 |
| 防火墙 | **只放行 22**，其余全关 |

腾讯云/阿里云的控制台安全组是**两套**：系统内 ufw 和云控制台安全组都要看。

## 1. 基础环境

```bash
apt update && apt install -y python3.12 python3.12-venv ffmpeg curl git
```

**ffmpeg 必须装**，否则 TTS 会静默退回文字。

如果系统仓库没有 python3.12，加 deadsnakes PPA：

```bash
add-apt-repository ppa:deadsnakes/ppa -y
apt update && apt install -y python3.12 python3.12-venv
```

## 2. 装 AstrBot

```bash
mkdir -p /opt/astrbot && cd /opt/astrbot
python3.12 -m venv venv
venv/bin/pip install -U pip
venv/bin/pip install astrbot -i https://pypi.tuna.tsinghua.edu.cn/simple
venv/bin/astrbot init -y
```

测试跑一次：

```bash
cd /opt/astrbot && venv/bin/astrbot run
```

出现 `Running on http://127.0.0.1:6185` 就对了。记下打印的初始后台密码。

## 3. 装 NapCat

NapCat 的 Linux 版需要图形环境，用 Xvfb 虚拟屏。

```bash
mkdir -p /root/Napcat && cd /root/Napcat
# 按官方 release 下载 Linux 包，校验 sha256 后解压
```

启动时需要 `DISPLAY`，用 Xvfb：

```bash
apt install -y xvfb
Xvfb :99 -screen 0 1080x1920x24 &
export DISPLAY=:99
```

登录二维码在 NapCat 安装目录下：

```
<NapCat目录>/opt/QQ/resources/app/app_launcher/napcat/cache/qrcode.png
```

**这是整个部署里唯一必须人工介入的点**：QQ 掉线后要重新扫码。

## 4. systemd 托管

### astrbot.service

`/etc/systemd/system/astrbot.service`：

```ini
[Unit]
Description=AstrBot QQ Bot
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/astrbot
ExecStart=/opt/astrbot/venv/bin/astrbot run
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

### napcat.service

`/etc/systemd/system/napcat.service`：

```ini
[Unit]
Description=NapCat QQ Client
After=network.target

[Service]
Type=simple
User=root
Environment=DISPLAY=:99
WorkingDirectory=/root/Napcat
ExecStart=/usr/bin/xvfb-run -a /root/Napcat/napcat.sh
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

启用：

```bash
systemctl daemon-reload
systemctl enable --now astrbot napcat
systemctl is-active astrbot napcat
```

## 5. 配模型

后台地址 `http://127.0.0.1:6185`（只绑本机，要远程访问就开 SSH 隧道：`ssh -L 6185:127.0.0.1:6185 root@服务器IP`）。

provider 配置与本地端一致，两个关键点再强调一次：

```json
{
  "model": "deepseek-flash",
  "modalities": ["text", "image", "tool_use"],
  "custom_extra_body": { "thinking": { "type": "disabled" } }
}
```

**`modalities` 必须含 `tool_use`**，否则工具永远发不下去，机器人会一直说"我没有工具"。
详见 [../troubleshooting/tools-not-sent.md](../troubleshooting/tools-not-sent.md)。

## 6. 装插件

```bash
cp -r <repo>/src/astrbot_plugin_face_bridge /opt/astrbot/data/plugins/
cp -r <repo>/src/astrbot_plugin_qq_power   /opt/astrbot/data/plugins/
cp -r <repo>/src/local_embedding_service   /opt/embed-svc   # 可选
systemctl restart astrbot
```

验证：

```bash
grep 'Added llm tool' /opt/astrbot/run.log | tail
```

## 7. 本地 embedding（可选，免费替代付费向量服务）

很多"长期记忆""按描述找图"的功能都要 embedding。云端 API 要钱，可以在本机跑一个。

```bash
apt install -y python3-venv
mkdir -p /opt/embed-svc && cd /opt/embed-svc
python3 -m venv venv
venv/bin/pip install fastembed fastapi uvicorn -i https://pypi.tuna.tsinghua.edu.cn/simple
cp <repo>/src/local_embedding_service/app.py /opt/embed-svc/
cp <repo>/src/local_embedding_service/local-embedding.service /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now local-embedding
```

验证：

```bash
curl -s http://127.0.0.1:8484/health
# {"ok":true,"model":"BAAI/bge-small-zh-v1.5","dim":512,...}
```

然后在 AstrBot 里加一个 OpenAI 兼容 embedding provider：

```json
{
  "id": "embedding-local",
  "type": "openai_embedding",
  "provider": "openai",
  "provider_type": "embedding",
  "enable": true,
  "embedding_api_key": "local",
  "embedding_api_base": "http://127.0.0.1:8484/v1",
  "embedding_model": "BAAI/bge-small-zh-v1.5",
  "embedding_dimensions": 512
}
```

再把用到 embedding 的插件指过来（比如 livingmemory 的 `provider_settings.embedding_provider_id`）。

**换维度会重建索引**：从 1024 换成 512 时插件会自动删旧 FAISS 索引并重建，日志里能看到 `检测到 FAISS 索引维度不匹配`，属正常。

## 8. 内存保护

4G 机器跑 AstrBot + NapCat + embedding，建议加 swap：

```bash
fallocate -l 2G /swapfile && chmod 600 /swapfile
mkswap /swapfile && swapon /swapfile
echo '/swapfile none swap sw 0 0' >> /etc/fstab
```

embedding 服务再加个内存上限（systemd unit 里）：

```ini
MemoryHigh=1200M
MemoryMax=1500M
```

## 9. 配置文件的坑

**所有 AstrBot 的 JSON 都带 UTF-8 BOM**，读写一律 `encoding='utf-8-sig'`：

```python
import json
d = json.load(open('/opt/astrbot/data/cmd_config.json', encoding='utf-8-sig'))
json.dump(d, open('/opt/astrbot/data/cmd_config.json', 'w', encoding='utf-8-sig'),
          ensure_ascii=False, indent=4)
```

写回时也要带 BOM，否则 AstrBot 可能读不出来。

**改配置前先停服务**：

```bash
systemctl stop astrbot
# 改配置
systemctl start astrbot
```

运行中改文件会被内存态回写覆盖。

## 10. 备份与回滚

```bash
# 配置
cp /opt/astrbot/data/cmd_config.json /opt/astrbot/data/cmd_config.json.bak-$(date +%Y%m%d-%H%M%S)
# 数据库（人格在里面）
cp /opt/astrbot/data/data_v4.db /opt/astrbot/data/data_v4.db.bak-$(date +%Y%m%d-%H%M%S)
# 插件数据
cp -a /opt/astrbot/data/plugin_data/astrbot_plugin_face_bridge /root/face_bridge_backup_$(date +%Y%m%d-%H%M%S)
```

备份别无限堆，每类留最近 2-3 个就够（曾经堆到 14 个 14MB）。

## 11. 服务器端体检清单

```bash
systemctl is-active astrbot napcat local-embedding
ss -tlnp | grep -E "6185|6199|6099|8484"
ss -tn | grep -c 6199     # 应该是 1，表示 NapCat 连着
free -m | head -2
df -h /
```

正常表现：

```
active / active / active
6185 → 127.0.0.1:6185
6199 → 127.0.0.1:6199
6099 → *:6099          ← 靠防火墙挡，绝对不要放行
8484 → 127.0.0.1:8484
```

## 12. 服务器端高频坑

| 现象 | 根因 | 修法 |
|---|---|---|
| `raw.githubusercontent.com` 不通 | 国内网络 | 用 `gh-proxy.com` 前缀 |
| 插件市场下载 403 | S3 拦 Python urllib 的 UA | **用 curl 下** |
| 插件 zip 顶层目录带哈希 | 解压后目录名不对 | 重命名成纯插件名 |
| `nohup ... &` 占住终端 | 没脱离 | 用 `setsid ... < /dev/null &` |
| 手动进程 + systemd 抢端口 | 两套并存 | `pkill` 后再 `systemctl start` |
| 改配置不生效 | 运行中回写 | 先 stop 再改 |
| 长命令粘进网页终端被截断 | 单行上限 | 拆成多行或写成脚本上传 |
| 机器人说"我没有工具" | `modalities` 漏 `tool_use` | 见第 5 节 |
