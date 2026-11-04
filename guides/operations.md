# 运维三件事

> 适用范围：AstrBot 4.29.3 + NapCat，云端 Linux 为主，Windows 端差异单列
> 最后实测：2026-11-04

出事的时候没时间翻文档。这份只回答三个问题：**日志在哪、先看哪几行、备份什么**。

---

## 一、日志在哪

### Linux（云端，推荐）

| 内容 | 路径 | 说明 |
|---|---|---|
| AstrBot 主日志 | `/opt/astrbot/run.log` | 全部运行日志，**颜色码是 ANSI，看之前先 `sed` 去色** |
| AstrBot 服务状态 | `journalctl -u astrbot -n 100 --no-pager` | 进程级崩溃、OOM、启动失败 |
| NapCat 日志 | `/root/Napcat/logs/` 或 `journalctl -u napcat` | 掉线、扫码、协议异常 |
| 二维码 | `/root/Napcat/.../cache/qrcode.png` | 掉线后要重扫 |

**看日志前先去掉颜色码**，否则满屏 `[32m`：

```bash
tail -50 /opt/astrbot/run.log | sed 's/\x1b\[[0-9;]*m//g'
```

### Windows（本地）

| 内容 | 路径 |
|---|---|
| AstrBot 日志 | 启动目录下 `data/logs/` 或控制台输出 |
| 守护脚本日志 | `scripts/keepalive.ps1` 写的那个 `keepalive.log` |

### 关键路径速查

```
/opt/astrbot/data/              ← 全部状态数据
/opt/astrbot/data/cmd_config.json      ← 主配置（带 BOM！读写用 utf-8-sig）
/opt/astrbot/data/data_v4.db           ← 人格、会话、消息历史
/opt/astrbot/data/config/              ← 各插件配置
/opt/astrbot/data/plugins/             ← 插件代码
/opt/astrbot/data/plugin_data/         ← 插件数据（表情库、记忆库）
```

---

## 二、出事优先查哪几行

按"最可能出问题"的顺序排。**从上往下查，找到就不往下走。**

### 1. 机器人不回话

```bash
systemctl is-active astrbot && systemctl is-active napcat
grep -iE "适配器已连接|websocket|connection" /opt/astrbot/run.log | tail -5
```

先看**链路通不通**。`6199` 上没连接，后面全白查。

### 2. 回话但没工具（"我没有这个功能"）

```bash
grep "Agent 使用工具" /opt/astrbot/run.log | tail -3
grep -iE "modalities|tool_use" /opt/astrbot/run.log | tail -3
```

**`Added llm tool` 有、但 `Agent 使用工具` 从来没有** → `modalities` 漏了 `tool_use`，工具表每轮被清空。

### 3. 报错 / 崩溃

```bash
grep -iE "error|exception|traceback" /opt/astrbot/run.log | tail -20
journalctl -u astrbot -n 50 --no-pager | grep -iE "error|killed|oom"
```

`Killed` / `oom` = 内存不够，先看 `free -h`。

### 4. 配置改了没生效

```bash
grep -iE "配置|config.*reload|被覆盖" /opt/astrbot/run.log | tail -5
```

**八成是这两件事**：改配置时没停进程（被内存态回写覆盖），或者字段没写进 `_conf_schema.json`（重启时按 schema 重建，未声明的键被丢弃）。

### 5. 乱发消息 / 答非所问

```bash
grep -E "face_bridge] 发送" /opt/astrbot/run.log | tail -10
grep -iE "proactive" /opt/astrbot/run.log | tail -10
```

看**是不是正则或定时任务误触发**，而不是模型自己乱说。

### 6. 掉线要重扫码

```bash
journalctl -u napcat -n 30 --no-pager | grep -iE "offline|logout|二维码|qrcode"
```

---

## 三、要备份哪些目录

### 必备份（丢了要重来）

| 目录/文件 | 内容 | 频率 |
|---|---|---|
| `/opt/astrbot/data/` | 全部状态：配置、人格、会话、插件数据 | 每次改配置后 |
| `/opt/astrbot/data/data_v4.db` | 人格 + 会话历史 | 每天 |
| `/opt/astrbot/data/plugin_data/` | 表情库、记忆库、相册 | 每天（体积大） |
| `/opt/astrbot/napcat_token.txt` | OneBot token（丢了要重配链路） | 改一次备一次 |

### 可选备份

| 目录 | 说明 |
|---|---|
| `/opt/astrbot/data/plugins/` | 插件代码。如果都来自 Git，可以不备 |
| `/root/Napcat/config/` | NapCat 配置。掉线重扫会重写一部分 |

### 不要备份

| 目录 | 原因 |
|---|---|
| `/root/Napcat/opt/QQ/` | 1GB+ 的 QQ 本体，随时能重下 |
| `__pycache__/`、`.venv/` | 可重建 |
| `data/temp/` | 临时图片中转 |

### 备份命令（带时间戳，别覆盖）

```bash
BK=/root/backup/$(date +%Y%m%d_%H%M)
mkdir -p "$BK"
tar czf "$BK/astrbot-data.tgz" -C /opt/astrbot data \
  --exclude='temp' --exclude='__pycache__' --exclude='*.log'
cp /opt/astrbot/napcat_token.txt "$BK/" 2>/dev/null
ls -lh "$BK"
```

### 保留策略

按周留 4 份就够。**但改配置之前一定要手动备一份** —— 大多数事故是"改坏了想回退"，不是磁盘坏了。

```bash
# 改配置前的固定动作
cp /opt/astrbot/data/cmd_config.json /opt/astrbot/data/cmd_config.json.bak.$(date +%s)
systemctl stop astrbot          # 必须停，否则改完被覆盖
# ...改...
systemctl start astrbot
```

---

## 附：三条救命命令

```bash
# 1. 两个服务都活着吗
systemctl is-active astrbot napcat

# 2. 最近 50 行日志（去色，能直接看）
tail -50 /opt/astrbot/run.log | sed 's/\x1b\[[0-9;]*m//g'

# 3. 磁盘和内存（OOM 前兆）
df -h / && free -h
```
