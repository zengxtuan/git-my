# 插件：face_bridge（表情 / 照片桥）

让机器人真的能"收图、存图、按描述找图、把图发出去"。

源码：`src/astrbot_plugin_face_bridge/`

## 为什么需要它

AstrBot 自带的 meme_manager 解决的是"随口配一张表情包"，靠分类 + 随机。
它解决不了三件事：

1. **用户发来的图存进 QQ 收藏表情**（调 NapCat 的 `add_custom_face`）
2. **用户说"把刚才那张发我"时精确发那张**（不是随机）
3. **照片和表情分开存**（照片塞进收藏表情会占额度、显乱）

## 数据长什么样

一个索引文件 `data/plugin_data/astrbot_plugin_face_bridge/media.json`：

```json
{
  "a276c1f6b19d": {
    "id": "a276c1f6b19d",
    "kind": "face",
    "path": ".../media/20261031-161530_a276c1f6b19d.jpg",
    "caption": "金发少女闭眼鼓腮，满脸傲娇不服气",
    "w": 512, "h": 512, "size": 20309,
    "added": 1793380762, "day": "2026-10-31",
    "in_qq_face": true
  }
}
```

- `kind` 只有两个值：`face`（表情）/ `photo`（照片）
- `caption` 是入库时用视觉模型自动生成的**一句中文描述**，是"按描述找图"的基础
- 文件统一放 `media/`，命名 `YYYYmmdd-HHMMSS_<md5前12>.ext`，md5 去重

旧版本用过 `index.json` + `photos.json` 双索引，启动时会自动迁移并搬文件。

## 收：怎么判断是表情还是照片

```python
def looks_like_meme(ext, size, w, h):
    if ext == ".gif":                                    return True
    if w and h and max(w, h) > 512:                      return False
    return size <= 300 * 1024
```

| 条件 | 结果 |
|---|---|
| GIF | 表情 |
| 边长 ≤512 且 ≤300KB | 表情 |
| 长边 >512 或 >300KB | 照片 |

表情会顺手调 NapCat 塞进 QQ 收藏表情；**照片不进收藏**。

上限分开：表情 5MB、照片 20MB（壁纸常常好几 MB）。

## 发：三个入口，优先级从高到低

### 1. LLM 工具 `send_media(want)`

模型判断"用户想看某张图"时调用，`want` 用它自己的说法填（"刚才那张""傲娇脸""昨天的壁纸"）。

内部三级兜底：

```
find(描述/编号)  →  recent_media（刚才在聊的那张）  →  latest（最新一张）
```

### 2. 正则兜底

模型没调工具时，明确说法立刻响应：

```
发我 / 发给我 / 发过来 / 发回来 / 发一下 / 发来看看 / 发我看看
发这个给我 / 把这个还我 / 那张 / 这张 / 壁纸
```

说"照片/壁纸/截图/原图"走相册，说"表情包"限定 `kind=face`，兜底也不许串类型。

### 3. 命令

| 命令 | 作用 |
|---|---|
| `/发图` | 发最新一张 |
| `/照片` | 列相册最近 10 张 |
| `/存照片` | 把这条消息里的图存进相册 |

## 按描述找图怎么实现

两步打分，**光有向量不够**：

```python
base = cosine(embed(want), embed(caption))
overlap = len(ngrams(want) & ngrams(caption))     # 2-gram 字面重叠
score = base + min(overlap * 0.08, 0.35)
```

为什么加字面分？实测中光用向量时，问"那张委屈的"会给回"傲娇与嫌弃"——
两个词在向量空间太近。加上"委屈"这个 2-gram 的字面命中加成后才选对。

阈值 0.45。embedding 服务不可用时退回纯 2-gram 匹配。

实测效果（环境见 [../README.md#实测环境说明](../README.md#实测环境说明)，embedding 用 `BAAI/bge-small-zh-v1.5`）：

| 你说 | 发出 | 分 |
|---|---|---|
| 发个傲娇表情包我看看 | 金发少女闭眼鼓腮，满脸傲娇不服气 | 0.656 |
| 发那张委屈的 | 粉色猫耳娘闭眼大哭，委屈破防 | 0.544 |
| 把那张笑脸盯的发我 | 简笔画笑脸配"盯"字 | 0.689 |

## 顺带处理的三件事

### DSML 剥离

模型偶尔把内部工具标记当正文吐出来：

```
<｜｜DSML｜｜ calls>
<｜｜DSML｜｜ invoke name="send_media">
```

注意竖线是**全角 U+FF5C**，不是 ASCII 的 `|`。发送前统一剥掉，整条都是垃圾时替换成一句自然的兜底话（多句随机，不重复）。

详见 [../troubleshooting/dsml-leak.md](../troubleshooting/dsml-leak.md)。

### QQ 自带表情翻译

AstrBot 解析消息时 `Face` 段**不进 `message_str`**，模型完全不知道用户发了表情。

插件从 QQ 客户端自带的 `default_config.json` 提取 264 条 `emojiId → 名称`，请求时翻译成"用户这条消息里带了 QQ 表情：斜眼笑"再注入。

### 按需语音

全局 TTS 概率设 0（不主动发语音）。用户说「语音/声音/念给我听/说句话」时，插件自己合成一条附在回复上。

## 配置项

`data/config/astrbot_plugin_face_bridge_config.json`：

| 项 | 默认 | 说明 |
|---|---|---|
| `auto_archive` | true | 收到图自动归档 |
| `max_send_bytes` | 5242880 | 表情上限 |
| `photo_max_bytes` | 20971520 | 照片上限 |

## 依赖

- 视觉模型（生成 caption）：复用 `provider_sources` 里的 key
- 本地 embedding：`http://127.0.0.1:8484/v1/embeddings`（没有就退回 2-gram）
- Pillow（取图片尺寸）

## 常见问题

| 现象 | 原因 |
|---|---|
| 收到图没反应 | `auto_archive` 关了，或图片超过上限 |
| 表情没进 QQ 收藏 | `add_custom_face` 失败，日志里有 `add_custom_face 失败` |
| 说"发我看看"发了同一张 | `recent_media` 没更新（旧版本 bug，已在 md5 去重分支修复） |
| 按描述找不对 | caption 为空（早期入库的图），可跑回填脚本补描述 |
