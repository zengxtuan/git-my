# 装完必做清单

> 适用范围：AstrBot 4.29.3 + NapCat（QQ 9.9.x），Ubuntu 22.04 与 Windows 双端通用
> 最后实测：2026-11-04

装完能对话**不等于**能对外用。下面五项少做一项，别人就能白嫖你的模型余额、翻你的相册、甚至接管你的 QQ。

**建议做成一张表，逐项打勾再交出去。**

---

## 一、ID 白名单（最优先）

### 配置位置

`data/cmd_config.json` → `platform_settings`

```json
{
  "id_white_list": true,
  "platform_settings": {
    "enable_id_white_list": true,
    "id_whitelist": ["qq:FriendMessage:你的QQ号"],
    "whitelist": ["qq:FriendMessage:你的QQ号"]
  }
}
```

### 为什么必须开

关着的时候，**任何加到机器人好友的人都能直接调用模型**。一条消息几毛钱，一个陌生人刷一晚上就是几十块。

### 怎么验证真的生效

```bash
grep -iE "white.?list|白名单" /opt/astrbot/run.log | tail -5
```

再用**另一个不在白名单里的号**发一句，必须没反应。自己的号测不算数。

---

## 二、admins_id 只填本人

### 配置位置

`data/cmd_config.json` → 顶层 `admins_id`

```json
"admins_id": ["你的QQ号"]
```

### 为什么必须只填本人

`admins_id` 里的人能调用**所有** `admin` 级工具：改昵称头像、发空间、看好友列表、处理好友申请、看别人的记忆。

**多填一个号 = 多一个人能操作你的 QQ。**

### 常见错误

- 把机器人的号也填进去（它不需要）
- 把"测试用小号"填进去忘了删
- 填了群号而不是 QQ 号

---

## 三、tool_permissions 全设 admin + 代码内双锁

### 配置层

`data/cmd_config.json` → `tool_permissions`

```json
"tool_permissions": {
  "qq_do": "admin",
  "llm_publish_feed": "admin",
  "llm_view_feed": "admin",
  "set_signature": "admin",
  "send_media": "user"
}
```

**`send_media` 保持 `user` 是故意的**：表情包谁都能要。但**照片（相册、聊天截图）在代码层另有 `is_admin` 判断**，配置层放行不等于照片能拿。详见 [capabilities.md](capabilities.md)。

### 代码层（不可省略）

**实测结论**：`tool_permissions` 会被 AstrBot 在某些情况下**重置成 `null`**（改配置、重启、插件重载都可能触发）。配置一丢，`admin` 限制全部失效。

所以**每个会动手的工具，函数体第一段必须自己判一次**：

```python
# face_bridge/main.py
def _is_admin(event) -> bool:
    """判断失败一律当非管理员（fail-close）。"""
    try:
        checker = getattr(event, "is_admin", None)
        if callable(checker):
            return bool(checker())
    except Exception as e:
        logger.warning("is_admin 判断失败，按非管理员处理: %s", e)
    return False
```

```python
# qq_power/main.py —— qq_do 开头
try:
    if not event.is_admin():
        return "这个只有主人能让我做"
except Exception as e:
    logger.error("is_admin 判断失败，按非管理员处理: %s", e)
    return "权限校验没通过，这个我不能做"
```

**注意是 fail-close**：判断本身出错时**拒绝**，不是放行。早期版本写成 `except Exception: pass`，等于出异常就变成人人可用。

### 怎么验证

临时把 `tool_permissions` 改成 `null`，重启，用非管理员号调 `qq_do`，必须被拒。**过不了这关就等于没做。**

---

## 四、computer_use 全关

### 配置位置

`data/cmd_config.json`

```json
"computer_use_runtime": "none",
"computer_use_local_permissions": {
  "member": { "allow_execution": false, "allow_network": false },
  "admin":  { "allow_execution": false, "allow_network": false }
}
```

### 为什么

开着等于**给模型一只能读写你本机、能跑任意命令的手**。它不是"多一个功能"，是"把整台机器交出去"。

`admin` 那一份也要关。别以为"只有我能触发所以安全"—— 提示注入能让模型自己去调。

---

## 五、三个端口只绑 127.0.0.1

| 端口 | 是什么 | 暴露的后果 |
|---|---|---|
| 6185 | AstrBot 后台 | 改人格、改配置、看全部对话 |
| 6199 | OneBot 反向 WS | 伪造事件、冒充用户 |
| **6099** | **NapCat WebUI** | **直接操作 QQ 本体：发消息、删好友、改资料** |

### 配置

- AstrBot：`dashboard.host` = `"127.0.0.1"`
- NapCat：`webui.json` → `"host": "127.0.0.1"`

### 验证（这一步经常被"本机访问正常"骗过去）

```bash
ss -tlnp | grep -E '6185|6199|6099'
```

输出必须是 `127.0.0.1:6185`，**不能是 `0.0.0.0:6185` 或 `*:6185`**。

本机浏览器能打开 ≠ 安全。**必须用另一台设备（手机流量）访问公网 IP 试，打不开才算对。**

> 实测坑：本机访问自己的公网 IP 会走回环，绕过防火墙，所以"我在服务器上 curl 得通"完全不能证明没暴露。

---

## 附：五项速查表

| # | 项 | 关键值 | 验证方式 |
|---|---|---|---|
| 1 | 白名单 | `enable_id_white_list: true` + 真实 UMO | 陌生号发消息无反应 |
| 2 | admins_id | 只填本人 | `grep admins_id` 确认没多余项 |
| 3 | tool_permissions + 代码双锁 | 动手类全 `admin`；代码内 `is_admin()` fail-close | 置 null 后非管理员仍被拒 |
| 4 | computer_use | `runtime: none`，member/admin 全 false | 让模型"执行个命令"必须被拒 |
| 5 | 端口绑定 | 三个都 `127.0.0.1` | 手机流量访问公网 IP 打不开 |
