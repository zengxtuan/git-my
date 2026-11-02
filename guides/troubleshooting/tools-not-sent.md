# ★ 模型说"我没有工具"——工具根本没发下去

这是排查最久的一个坑，单独成篇。

## 症状

机器人反复说这类话：

- 「换头像这活儿我手上没工具，说了不算数」
- 「名字和头像得在 QQ 资料里改，我这边动不了」
- 「我手上没这按钮」
- 偶尔还会吐 `<｜｜DSML｜｜> calls>` 这种乱码文本

但后台看得很清楚，工具都注册了：

```
[Core] [INFO] [provider.func_tool_manager:390]: Added llm tool: qq_do
[Core] [INFO] [provider.func_tool_manager:390]: Added llm tool: send_media
```

## 根因

AstrBot 源码 `core/agent/runners/tool_loop_agent_runner.py` 第 680 行附近：

```python
modalities = self.provider.provider_config.get("modalities", None)
if isinstance(modalities, list) and modalities and "tool_use" not in modalities:
    logger.warning("Provider %s does not support tool_use, clearing tools for request.")
    # 整张工具表被清空
    self.req.func_tool = None
```

provider 的 `modalities` 如果是个非空列表、且**不含 `"tool_use"`**，
AstrBot 就认定这个模型不支持工具调用，**每轮请求都把 tools 整个清空**。

常见错法：为了开图像理解写成 `["text", "image"]`，漏了 `tool_use`。

## 抓包证据

用反向代理看真实请求（方法见 [capture-requests.md](capture-requests.md)）。

**修复前：**

```
model: deepseek-flash
messages 条数: 14
system 长度: 5966
tools 数量: 0          ← 一个都没有
tool_choice: None
```

**修复后：**

```
tools 数量: 15
tool_choice: auto
工具: deepseek_fetch_web_page, future_task, llm_publish_feed, llm_view_feed,
      qq_do, qqprofile_set_avatar, qqprofile_set_nickname, send_media,
      recall_long_term_memory, web_search_deepseek, ...
```

紧接着模型就真的调了：

```
Agent 使用工具: ['qqprofile_set_nickname']
使用工具：qqprofile_set_nickname，参数：{'nickname': '小曦'}
Tool `qqprofile_set_nickname` Result: ...
```

## 修复

```json
"modalities": ["text", "image", "tool_use"]
```

改完重启 AstrBot。

**注意**：`modalities` 里每个值都是能力开关，配少一个就少一项能力：

| 值 | 含义 | 漏了会怎样 |
|---|---|---|
| `text` | 文本 | — |
| `image` | 看图 | 图片被替换成 `[Image]` 占位符 |
| `audio` | 听音频 | 音频被替换成 `[Audio]` |
| `tool_use` | 工具调用 | **整张工具表被清空** |

## 连带纠正的错误判断

排查过程中走过不少弯路，记下来避免重犯：

| 当时的判断 | 实际 |
|---|---|
| 工具太多（19 个）把轻量模型压崩了 | 工具从未发出，数量与崩溃无关 |
| thinking 模式导致格式错乱 | 关掉 thinking 后照样出现乱码，无关 |
| 模型能力不够，只能靠正则兜底 | 模型能正常调工具，只是没给 |

DSML 乱码的真正来源也是它：人格和工具描述里写着"调 qq_do"，
但请求里没有工具定义，模型只能用文本**模仿**调用格式，吐出来就是 `<｜｜DSML｜｜>`。

## 三分钟自查

```bash
# 1. 看配置
python3 -c "
import json
d=json.load(open('/opt/astrbot/data/cmd_config.json',encoding='utf-8-sig'))
for p in d['provider']:
    if p.get('id')=='deepseek':
        print('modalities =', p.get('modalities'))
"
```

必须看到 `tool_use`。

```bash
# 2. 看工具有没有注册
grep 'Added llm tool' /opt/astrbot/run.log | tail

# 3. 看实际调用
grep 'Agent 使用工具' /opt/astrbot/run.log | tail
```

第 2 步有、第 3 步没有 → 基本就是这个坑。
第 3 步也没有任何输出 → 用抓包确认 tools 数量（见下一篇）。
