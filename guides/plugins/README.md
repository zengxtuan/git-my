# 插件：怎么装、装什么

## 一、插件目录结构

AstrBot 会扫描 `data/plugins/` 下的每个子目录：

```
data/plugins/
└── astrbot_plugin_xxx/
    ├── metadata.yaml    ← 必需，没有它插件加载不了
    ├── main.py          ← 入口
    ├── _conf_schema.json ← 可选，配置项定义
    └── ...
```

`metadata.yaml` 最小内容：

```yaml
name: astrbot_plugin_xxx
desc: 一句话说明
version: 1.0.0
author: 你的名字
repo: ""
```

**没有 `metadata.yaml` 会报"无法获取插件目录名"**，插件直接不加载。

## 二、装社区插件的两种方式

### 方式一：后台插件市场（推荐）

AstrBot 后台 → 插件 → 插件市场 → 搜名字 → 安装。

### 方式二：手动下载

市场装不上时（国内网络常见），手动来：

```bash
cd /tmp
# 用 curl，不要用 wget 或 Python urllib
curl -L -o plugin.zip "<下载地址>"
unzip plugin.zip -d /opt/astrbot/data/plugins/
```

**三个必踩的坑：**

| 现象 | 原因 | 处理 |
|---|---|---|
| 下载 403 | S3 拦 Python urllib 的 UA | **必须用 curl** |
| 解压出来目录名是哈希 | zip 顶层目录带 commit 哈希 | 重命名成纯插件名（要和 `metadata.yaml` 里的 name 一致） |
| 市场列表 301 | 插件市场的 URL 变了 | 看 AstrBot 版本，必要时改 `plugin_service.py` 里的 URL（别动 `plugins-md5`） |

GitHub raw 打不开时加代理前缀：

```
https://gh-proxy.com/https://raw.githubusercontent.com/<owner>/<repo>/main/...
```

## 三、常用插件清单

### 本仓库自带的三个

| 插件 | 装法 | 文档 |
|---|---|---|
| `astrbot_plugin_face_bridge` | `cp -r src/astrbot_plugin_face_bridge data/plugins/` | [face-bridge.md](face-bridge.md) |
| `astrbot_plugin_qq_power` | `cp -r src/astrbot_plugin_qq_power data/plugins/` | [qq-power.md](qq-power.md) |
| `local-embedding`（不是 AstrBot 插件，是独立服务） | 见文档 | [local-embedding.md](local-embedding.md) |

这两个插件互相独立，可以只装一个。但 `qq_power` 的"换头像 / 按描述找图"会去读 `face_bridge` 的索引，
没装 `face_bridge` 时只能从当前消息或现画一张。

### 社区插件（按需）

这些是第三方作者维护的，从插件市场或各自 GitHub 装：

| 插件 | 干什么 | 注意 |
|---|---|---|
| `meme_manager` | 表情包库、分类配图、语义检索 | 配置项多，版本升级字段会变 |
| `astrbot_plugin_livingmemory` | 长期记忆、摘要、向量召回 | 需要 embedding；占内存较多 |
| `astrbot_plugin_qqprofile` | 改头像/昵称/签名/状态（工具形式） | 它的换头像**只认当前消息里的图**，见 [qq-power.md](qq-power.md) |
| `astrbot_plugin_qzone` | 发空间动态 | 工具权限记得限 `admin` |
| `astrbot_plugin_group_chat_plus` | 群聊增强 | 启动时可能报可选模块缺失，不影响 |
| `astrbot_plugin_proactive_chat` | 主动发起对话 | 不开 `session_list` 就别开 enable，否则一直刷 warning |

## 四、工具权限（装完必做）

装完插件后，把会"动手"的工具限制成管理员：

```json
"tool_permissions": {
  "qq_do": "admin",
  "send_media": "user",
  "llm_publish_feed": "admin",
  "llm_view_feed": "admin",
  "set_signature": "admin"
}
```

**`send_media` 为什么是 `user`**：表情包谁都能要。但工具内按 `kind` 分流——
`kind=face`（表情包）放行，`kind=photo`（相册、聊天截图）在代码层再判 `is_admin()`，
非管理员一律拒绝。配置层放行 ≠ 照片能拿到。

**但配置不牢靠**：实测 `tool_permissions` 会被 AstrBot 在某些情况下重置成 `null`。
所以自己写的插件里**必须**再加一道代码判断，而且要是 **fail-close**：

```python
def _is_admin(event) -> bool:
    """判断失败一律当非管理员（fail-close），不能 except: pass 放行。"""
    try:
        checker = getattr(event, "is_admin", None)
        if callable(checker):
            return bool(checker())
    except Exception as e:
        logger.warning("is_admin 判断失败，按非管理员处理: %s", e)
    return False
```

完整的五项必做见 [../setup-checklist.md](../setup-checklist.md)，权限怎么分级见
[../capabilities.md](../capabilities.md)。

## 五、装完怎么验证

```bash
# 1. 插件加载了没
grep 'Loading plugin' /opt/astrbot/run.log | tail

# 2. 工具注册了没
grep 'Added llm tool' /opt/astrbot/run.log | tail

# 3. 工具真的发给模型了吗（关键，别只看 1 和 2）
grep 'Agent 使用工具' /opt/astrbot/run.log | tail
```

第 2 步有、第 3 步从来没有 → 大概率是 `modalities` 漏了 `tool_use`，
见 [../troubleshooting/tools-not-sent.md](../troubleshooting/tools-not-sent.md)。

## 六、插件冲突排查

两个插件都要处理同一条消息时（比如都监听 `event_message_type(ALL)`），
常见问题是"一个插件 `stop_event()` 把另一个饿死"。

排查方法：看日志里哪个插件的钩子先执行、有没有 `stop_event`。
自己写插件时，**只做副作用、不阻断**是最安全的做法（`face_bridge` 的收图钩子就是这样）。
