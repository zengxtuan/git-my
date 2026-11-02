# 插件：qq_power（操作 QQ）

把 NapCat 早就支持、但 AstrBot 没暴露出来的日常 QQ 操作，做成一个工具。

源码：`src/astrbot_plugin_qq_power/`

## 为什么用工具而不是命令

第一版做成了 `/放行 <QQ>` `\\删好友 <QQ>` 这种命令。用户的原话：

> 怎么写入，这种是不是太弱智了，像 ai

正常人不记命令，会说「让 123456 加进来」「这人烦死了别让他跟我说话」。
所以最终形态是**一个工具 `qq_do(action, target, extra)`**，让模型听懂人话再翻译成动作。

命令保留，但只作为精确入口。

## 一个工具覆盖 14 种操作

```
放行 / 收回 / 白名单          ← 谁能跟机器人说话
好友 / 申请 / 同意 / 拒绝      ← 好友关系
删好友 / 备注 / 点赞 / 拍      ← 对好友的动作
资料 / 改昵称 / 换头像 / 画图  ← 自己和别人
```

工具描述里直接写清人话对应关系：

```
用户说「加他进来」「让他能跟我说话」就用 放行；
说「别让他烦我」「踢了」就用 收回；说「删了他」才用 删好友。
```

## 参数

```python
qq_do(action: str, target: str = "", extra: str = "")
```

| action | target | extra |
|---|---|---|
| 放行 / 收回 | QQ 号 | — |
| 白名单 | — | — |
| 好友 / 申请 | — | — |
| 同意 / 拒绝 | 申请里的 flag | 备注 |
| 删好友 | QQ 号 | — |
| 备注 | QQ 号 | 备注名 |
| 点赞 | QQ 号 | 次数（默认 10） |
| 拍 | QQ 号 | — |
| 资料 | QQ 号 | — |
| 改昵称 | — | 新昵称 |
| 换头像 | 图描述（可空） | 同左 |
| 画图 | 画面描述 | 同左 |

## 权限：两道锁

1. **`tool_permissions`**（配置层）

```json
"tool_permissions": { "qq_do": "admin" }
```

2. **`is_admin()` 内部判断**（代码层）

```python
if not event.is_admin():
    return "这个只有主人能让我做"
```

为什么两道都要？实测 `tool_permissions` 会被 AstrBot 在某些情况下重置成 `null`，
只靠配置等于没锁。所有命令也统一加了 `@filter.permission_type(ADMIN)`。

**分层是清楚的**：白名单决定"谁能说话"，`admins_id` 决定"谁能操作 QQ"。

## 换头像：三级取图

AstrBot 自带的 `qqprofile_set_avatar` 只能从**当前消息或引用里**拿图，
用户说「把那个表情包换成头像」时消息里没图，工具直接返回"需要引用一张图片"。

本插件的取图顺序：

```
1. 这条消息里带的图
2. 按描述从 media.json 里挑（复用 face_bridge 的描述匹配）
3. 都没有 → 现画一张（Pollinations 文生图）
```

拿到本地路径后调 `set_qq_avatar(file=路径)`，NapCat 支持本地文件。

## 画图：为什么不搜图

理想是"搜一张"，实测三条免费路线都不行（环境见 [../README.md#实测环境说明](../README.md#实测环境说明)）：

| 方案 | 结果 |
|---|---|
| Bing 图片搜索 | 能拿到 8 个可下载链接，视觉模型逐张打分**全 0 分**（来源全是无关站） |
| 搜狗图片 API | 返回 0 条 |
| AstrBot 内置图片搜索 | 需要 SerpApi 付费 key |

所以走生成：Pollinations 免费文生图，说画什么画什么，无版权问题。

- 说「壁纸 / 横版」自动 1024×576，否则 1024×1024
- 超时 120s（实测 6–15 秒出图）
- 生成后直接 `event.send(Image.fromFileSystem(path))` 发给用户

## 好友申请怎么拿到 flag

用 NapCat 扩展 action `get_doubt_friends_add_request`，返回带 flag 的列表，
插件本地缓存到 `data/plugin_data/astrbot_plugin_qq_power/friend_requests.json`，
`/同意 <flag>` 或让模型说"同意"即可。

## NapCat 底层 action 对照

| 功能 | action |
|---|---|
| 好友列表 | `get_friend_list` |
| 待处理申请 | `get_doubt_friends_add_request` |
| 处理申请 | `set_friend_add_request` |
| 删好友 | `delete_friend` |
| 改备注 | `set_friend_remark` |
| 名片点赞 | `send_like` |
| 拍一拍 | `friend_poke` / `send_poke` |
| 陌生人资料 | `get_stranger_info` |
| 改昵称 | `set_qq_profile` |
| 换头像 | `set_qq_avatar` |
| 登录信息 | `get_login_info` |

NapCat 一共暴露 723 个 action，需要什么自己 `grep actionName` 去挖。

## 记忆隔离（重要）

把某人加进白名单后，他和机器人的记忆是**分开的**。

`livingmemory` 的解析逻辑（`core/memory_scope.py`）：

```python
mode = config["filtering_settings"]["memory_scope_mode"]   # legacy
if mode == "session": return session_id
if mode == "user":    return f"livingmemory:user:{platform}:{identity}"
if use_session_filtering: return session_id                 # ← 默认走这里
```

`legacy + use_session_filtering=true` → 作用域是会话 ID（`qq:FriendMessage:<QQ>`）。

| 内容 | 你和机器人 | 他和机器人 |
|---|---|---|
| 人格 | 同一个 | 同一个 |
| 短期上下文 | 各自一份 | 各自一份 |
| 长期记忆 | 各自一份 | 各自一份 |

想让记忆在某个会话里全局共享，把 `memory_scope_mode` 改成 `user` 或 `global`。
