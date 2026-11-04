# 能力总表与权限分级

> 适用范围：AstrBot 4.29.3 + NapCat，插件 face_bridge / qq_power / qzone / qqprofile / livingmemory
> 最后实测：2026-11-04

这份文档回答两个问题：**它能做什么**、**谁能让它做**。

散在各处的旧文档（`plugins/qq-power.md`、`plugins/face-bridge.md`、`skills/dsh-qq/SKILL.md`）各自只讲一半，这里做一次总收口。

---

## 一、全能力一览

| 能力 | 归属插件 | 入口形式 | 谁能触发 |
|---|---|---|---|
| 闲聊 / 查资料 / 翻译 / 算数 / 写作 | 模型本体 + `web_search_deepseek` | 直接说话 | 白名单内所有人 |
| 自我介绍、能力说明 | 人格文件 | 直接问 | 所有人 |
| 发表情包（按描述找） | `face_bridge` | `send_media`，`kind=face` | 所有人 |
| 发照片 / 浏览相册 | `face_bridge` | `send_media`，`kind=photo` | **仅管理员** |
| 记住本次会话的事 | `livingmemory` | 自动 | 所有人（只限**自己**会话） |
| 记住别人的事 | `livingmemory` | 自动 | **仅管理员**可查 |
| 天气、新闻等公开信息 | `web_search_deepseek` | 自动 | 所有人 |
| 放行 / 收回 / 白名单 | `qq_power` | `qq_do` | 仅管理员 |
| 好友列表、好友申请、加删好友、改备注 | `qq_power` | `qq_do` | 仅管理员 |
| 改昵称 / 头像 / 签名 / 状态 | `qq_power`、`qqprofile` | `qq_do` | 仅管理员 |
| 点赞 / 拍一拍 | `qq_power` | `qq_do` | 仅管理员 |
| 发空间动态 | `qzone` | `llm_publish_feed` | 仅管理员 |
| 画图 | `qq_power` | `qq_do`（画图） | 仅管理员 |
| 查模型、端口、token、后台地址、花费 | 模型自述 + 配置 | 直接问 | 仅管理员 |

---

## 二、权限分级（两级）

### A. 普通用户可拿

**判定标准：只影响他自己，或者信息本身就是公开的。**

| 类别 | 具体 |
|---|---|
| 自我介绍 | 你叫什么、是谁做的、能做什么 |
| 通用问答 | 闲聊、查资料、翻译、算数、写作 |
| 表情包 | 按描述发一张，**只限 `kind=face`** |
| 自己的记忆 | 只限**当前会话内**、关于**他自己**的内容 |
| 公开信息 | 天气、新闻、百科、公开价格 |

### B. 仅管理员可拿

**判定标准：涉及你的账号、你的数据，或者作用在别人身上。**

| 类别 | 具体 |
|---|---|
| 白名单 | 看名单、加人、删人 |
| 好友关系 | 好友列表、待处理申请、同意/拒绝、删好友、改备注 |
| 账号外观 | 改昵称、头像、签名、在线状态 |
| 社交动作 | 点赞、拍一拍 |
| 空间 | 发动态、看动态 |
| 相册 | **浏览整个相册**（`kind=photo`） |
| 系统信息 | 模型名、端口、token、后台地址、token 用量与花费 |
| 他人的记忆 | 长期记忆里关于**别人**的内容 |

### C. 任何身份都不给

这几项**不是"权限不够"，是根本不做**。管理员来要也一样。

| 类别 | 说明 |
|---|---|
| 密钥与 token | 模型 API key、OneBot token、后台密码，**任何情况下不读、不复述** |
| 他人的 QQ 号 | 不主动收集、不在回复里罗列第三方号码 |
| 装成别的身份 | 要求它扮演客服/官方/另一个人，或"忘掉你的人格设定" —— 拒绝 |
| 违法与有害 | 色情内容、违法操作、批量骚扰/群发 |

---

## 三、分级怎么落地（两层，缺一不可）

### 第一层：配置

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

`qq_power` 的所有命令另外挂了 `@filter.permission_type(filter.PermissionType.ADMIN)`，命令层也是 admin。

### 第二层：代码（**不能只靠配置**）

**实测**：`tool_permissions` 会在改配置、重启、插件重载时被重置成 `null`。配置一没，第一层完全失效。

所以每个会动手的工具，函数体里**自己再判一次**：

```python
# face_bridge：按 kind 分流
if kind is None:
    return {"ok": False, "reason": "没点名要表情还是照片，不发"}

if kind == "photo" and not _is_admin(event):
    logger.info("[face_bridge] 拒绝非管理员索取照片 reason=%s", reason)
    return {"ok": False, "reason": "照片只给管理员看"}
```

```python
# qq_power：整工具兜底
try:
    if not event.is_admin():
        return "这个只有主人能让我做"
except Exception as e:
    logger.error("is_admin 判断失败，按非管理员处理: %s", e)
    return "权限校验没通过，这个我不能做"
```

**关键点：fail-close。** 判断本身抛异常时按"非管理员"处理，不能默认放行。写成 `except: pass` 等于出 bug 就人人可用。

### 判定矩阵（改完代码后的真实行为）

| 请求 | `tool_permissions` 正常 | `tool_permissions` 被清成 null |
|---|---|---|
| 普通用户要表情包 | ✅ 发 | ✅ 发 |
| 普通用户要照片 | ❌ 拒 | ❌ 拒（代码层挡住） |
| 普通用户要改昵称 | ❌ 拒 | ❌ 拒（代码层挡住） |
| 管理员要照片 | ✅ 发 | ✅ 发 |
| 管理员要改昵称 | ✅ 做 | ✅ 做 |

---

## 四、怎么验

```bash
# 1. 配置层是否生效
grep -A10 'tool_permissions' /opt/astrbot/data/cmd_config.json

# 2. 代码层是否真的拦了（把自己从 admins_id 临时移走，再要照片）
grep '拒绝非管理员索取照片' /opt/astrbot/run.log

# 3. 把 tool_permissions 置 null 重启，重复第 2 步，必须仍然被拒
```

第 3 步过了，才算"配置层不可单独依赖"真的落实。
