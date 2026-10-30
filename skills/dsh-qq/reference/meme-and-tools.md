# 表情包 · 图像理解 · 工具调用

这一份记录 meme_manager 的配置全貌、NapCat 的收藏表情 API、以及工具调用相关的坑。

## 一、meme_manager 配置地图

配置文件：`data/config/meme_manager_config.json`（带 BOM，用 `utf-8-sig`）

顶层结构：

```
settings_center / webui / storage / sync / community / semantic / auto_collect / generation
```

### 1. 发送控制（generation.emotion）

```json
{
  "llm": {
    "enabled": true,          // 让 LLM 判断这轮该不该发
    "provider_id": "deepseek",
    "context_turns": 4,       // 给 LLM 看几轮上下文
    "inject_persona": true
  },
  "max_memes_per_message": -1,
  "probability": 15           // 基础概率门槛
}
```

**关键代码逻辑**（`mixins/event_handlers.py`）：

```python
legacy_probability_hit = probability_hit(self.emotions_probability)
if self.emotion_llm_enabled and legacy_probability_hit:
    ...
```

`and` —— **概率必须先命中，LLM 判断才生效**。

- 概率设 5 → 95% 的情况连 LLM 都不问，直接不发
- 概率设 100 → 每轮都进 LLM 判断，由它决定

想"理解语境后决定"，就把 `probability` 拉到 100，决策权交给 `llm.enabled`。

### 2. 自动收集（auto_collect）

```json
{
  "enabled": true,
  "vision_provider_id": "deepseek",   // 用哪个模型识别图
  "manual_review": false,             // false = 直接入库（日志仍打印"等待审核"，是旧文案）
  "target_pack_id": "<pack-id>",      // 空着会不知道往哪存
  "pending_limit": 400,
  "sampling_probability": 100,
  "max_images_per_message": 1,
  "cooldown_seconds": 1,
  "daily_recognition_limit": 200,
  "min_meme_confidence": 0.0,         // 置信度门槛，DeepSeek 判断很保守，0.85 基本过不了
  "min_category_confidence": 0.0
}
```

**两个必踩的坑**：

| 现象 | 原因 |
|---|---|
| 图收了但不入库 | `target_pack_id` 为空 |
| 日志一直"自动收集跳过图片：表情包置信度不足" | `min_meme_confidence` 默认 0.85，DeepSeek 达不到，要降到 0 |

`min_meme_confidence` 降下来后收图正常（日志会变成"已收集图片"），文件名格式是 `<时间戳>_<md5前12>.gif`。

### 3. 语义匹配（semantic）——需要 embedding

```json
{
  "enabled": false,
  "top_k": 5,
  "min_score": 0.25,
  "vision_provider_id": "",
  "embedding_provider_id": ""
}
```

**开启的前提**：
1. 每张图都有文字描述（`descriptions.json`，要在 WebUI 的"语义化页面"生成，命令行触发不了）
2. 有可用的 **embedding provider**

**硬约束**：**DeepSeek 没有 embedding 端点**（只有 chat 和 search）。所以语义匹配必须另找：
- 硅基流动 `BAAI/bge-m3`（0.1 元/百万 token）
- OpenAI `text-embedding-3`
- 本地 `sentence-transformers`（免费但要下模型，CPU 推理慢）

没有余额时的日志：

```
Retrieval route 向量 failed: Error 402 - Sorry, your account balance is insufficient
[meme_manager] 首次开启语义检索: xxx 的图片描述尚未全部完成，暂不自动建立向量
```

### 4. 存储（storage）

```json
{ "provider": "cloudflare_r2", "providers": { ... 全空 ... } }
```

配置全空也能工作——有本地文件兜底（`data/plugin_data/meme_manager/packs/`）。不用管它。

### 5. 没有"总张数上限"字段

搜遍配置只有这些：

```
auto_collect.pending_limit = 200          // 待审核队列，不是总数
auto_collect.daily_recognition_limit = 100 // 每天最多识别多少张
generation.emotion.max_memes_per_message = -1
```

**要做 400 张硬上限，只能写定时清理**：查 `packs/*/memes/**` 文件数，超了就按 mtime 删最旧。

### 6. 目录结构

```
data/plugin_data/meme_manager/
├── packs/<pack-id>/
│   ├── manifest.json
│   ├── memes_data.json
│   ├── memes/<分类>/<时间戳>_<md5>.gif
│   ├── previews/
│   ├── semantic_metadata.json
│   └── source_index.json
├── registry.json          // 已装 pack 列表
├── selection_rules.json
├── semantic_indexes/      // 向量索引（空 = 没建）
├── community_cache.json
├── backup/
└── temp/auto_collect/     // 收图中转，正常应该是空的
```

### 7. LLM 只有 `search_memes` 一个工具

```
Added llm tool: search_memes
```

**只有搜索，没有"添加"「删除」「按 ID 取图」**。所以模型说"我存不下来"是实话——它手里没有那个工具。

---

## 二、NapCat 自定义表情（收藏表情）API

NapCat 的 OneBot action 里有一整套自定义表情管理：

| Action | 作用 | 主要参数 |
|---|---|---|
| `fetch_custom_face_detail` | 列出收藏表情 | `count`（默认 48） |
| **`add_custom_face`** | **添加收藏表情** | `file`（本地路径）、`is_origin`（是否原图） |
| `delete_custom_face` | 删除 | `res_id` 或 `ids` |
| `set_custom_face_desc` | 改描述 | `emoji_id`、`res_id`、`md5`、`desc` |
| `fetch_custom_face` | 拉取自定义表情 | — |

`add_custom_face` 内部实现：读文件 → 算 md5 → 调 `MsgApi.addFavEmoji`。其余参数（md5/文件名/大小）不给会自动补。

调用示例（OneBot action 格式）：

```json
{
  "action": "add_custom_face",
  "params": { "file": "/path/to/meme.gif", "is_origin": true }
}
```

**架构注意**：NapCat 在反向 WS 模式下是**客户端**（主动连 AstrBot），自己不开 HTTP 端口。所以要调 action，必须走 AstrBot 的连接（写插件用 `call_action`），或者给 NapCat 加 `httpServers` 配置（但那要重启 NapCat，**会导致 QQ 掉线重扫码**）。

---

## 三、工具调用的三个坑

### 坑 1：内置工具不打 `Added llm tool` 日志

日志里只能看到插件注册的工具：

```
Added llm tool: qqprofile_set_signature
Added llm tool: search_memes
```

**内置工具（`web_search_*`、`web_search_deepseek` 等 47 个）不会打这条日志**，但它们一直注册着。验证方法：

```bash
cd /opt/astrbot && venv/bin/python -c "
from astrbot.core.tools.registry import ensure_builtin_tools_loaded, iter_builtin_tool_classes
ensure_builtin_tools_loaded()
names = [c().name for c in iter_builtin_tool_classes()]
print('内置工具总数:', len(names))
print([n for n in names if 'search' in n])
"
```

打这条日志的代码在 `func_tool_manager.py`，只覆盖 `add_tool()` 显式添加的路径。

### 坑 2：DSML 泄漏

`deepseek-flash` 想调工具时，有时会输出 DeepSeek 训练时的内部标记，而不是标准 `tool_calls`：

```
<||DSML||> calls>
<||DSML||> invoke name="search_memes">
<||DSML||> parameter name="query">关键词</||DSML||> parameter>
</||DSML||> invoke>
</||DSML||> calls>
```

AstrBot 解析不了这个格式，就把它当普通文本发给用户了。

**影响**：所有工具（`web_search_deepseek`、`search_memes`）都可能中招。

**已知方向**：
- 换 `deepseek-chat` / `deepseek-v4-pro`（工具调用更稳，但贵 3~5 倍）
- 或者调 `agent_runner.config.misc.tool_schema_mode`（`full` → 更简的模式，减少 schema 压力）

**注意**：曾经在人格里写「调用 web_search_deepseek 搜一下」这种带具体函数名的指令，**会加剧** DSML 泄漏（模型模仿文本格式）。人格里只写意图（"联网查一下"），不给函数名。

### 坑 3：`enable_tools_reminder` 只管插件工具

```json
// astrbot_plugin_group_chat_plus_config.json
"enable_tools_reminder": true,
"tools_reminder_persona_filter": false
```

这个开关**不会**让模型知道内置工具（`web_search` 等）的存在。内置工具要靠模型自己的能力去发现——而 DSML 问题会让这个过程失败。

---

## 四、图像理解配置

```json
// astrbot_plugin_group_chat_plus_config.json
"enable_image_processing": true,
"image_to_text_scope": "all",
"image_to_text_provider_id": "deepseek",
"image_to_text_prompt": "把这张图看仔细了，分四点说：1)画面主体是谁或什么 2)在做什么动作、什么表情 3)图上的文字一个字不落地抄下来 4)这张图想表达什么情绪或梗。不要省略细节，不要评价，只描述。",
"image_to_text_timeout": 60,
"max_images_per_message": 4,
"enable_image_description_cache": true,
"private_enable_image_processing": true,
"private_image_to_text_provider_id": "deepseek"
```

同时 `cmd_config.json` 里对应 provider 要声明支持图片：

```json
"provider": [
  { "id": "deepseek", "model": "deepseek-chat", "modalities": ["text", "image"], ... }
]
```

**验证 DeepSeek 真的能吃图**（别信文档，直接测）：

```bash
curl -s https://api.deepseek.com/chat/completions \
  -H "Authorization: Bearer $KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"deepseek-chat","messages":[{"role":"user","content":[
       {"type":"image_url","image_url":{"url":"data:image/png;base64,<BASE64>"}},
       {"type":"text","text":"描述这张图"}]}]}'
```

返回正常描述 = 支持。报 `unsupported image` = 格式不对，不是不支持。

**提示词写得越具体，描述质量越高**。只写「请详细描述这张图片的内容」会得到很糊的答案，模型就只能"看到个影子"。

---

## 五、联网搜索配置

```json
// cmd_config.json → provider_settings
{
  "web_search": true,
  "websearch_provider": "deepseek",
  "websearch_deepseek_key": ["sk-xxx"]
}
```

**API 端点**（源码 `core/tools/web_search_tools.py`）：

```
搜索：https://api.deepseek.com/v1/tools/search
抓页：https://api.deepseek.com/v1/tools/fetch
```

计费：**5 元 / 1000 次**（0.005 元一次）。

支持的 provider 合法值：

```
tavily / bocha / brave / firecrawl / baidu_ai_search / exa / anysearch / deepseek
```

直接验证搜索是否可用：

```bash
curl -s -X POST https://api.deepseek.com/v1/tools/search \
  -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  -d '{"query":"今天深圳天气","max_results":3}'
```

返回 `results` 数组 = 可用。字段有 `title` / `url` / `snippet` / `summary` / `site_name` / `rerank_score`。

**别用 PowerShell 测中文**：`Invoke-RestMethod` 默认编码会把中文变成问号，必须手动转 UTF-8 字节：

```powershell
$bytes = [Text.Encoding]::UTF8.GetBytes($json)
Invoke-RestMethod -Body $bytes -ContentType "application/json; charset=utf-8"
```
