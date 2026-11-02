# 模型吐 `<｜｜DSML｜｜>` 乱码

## 长什么样

```
<｜｜DSML｜｜ calls>
<｜｜DSML｜｜ invoke name="web_search_deepseek">
<｜｜DSML｜｜ parameter name="query" string="true">深圳今天天气</｜｜DSML｜｜ parameter>
</｜｜DSML｜｜ invoke>
</｜｜DSML｜｜ calls>
```

**注意竖线是全角 `｜`（U+FF5C），不是 ASCII 的 `|`。** 按半角竖线去匹配会全部落空。

## 成因

这是模型想调工具、但**没有拿到工具定义**时，用纯文本模仿调用格式的结果。

所以先查根因：[tools-not-sent.md](tools-not-sent.md)。
如果 `modalities` 里没有 `tool_use`，工具根本没发出去，DSML 就一定会出现。

根因修好之后基本不再出现。

## 兜底处理

就算根因修好，轻量模型偶尔还是会漏。在发送前拦一层：

```python
DSML_RE = re.compile(
    r"<[|｜]{2}\s*DSML\s*[|｜]{2}[\s\S]*?(?:</[|｜]{2}\s*DSML\s*[|｜]{2}\s*calls>|\Z)"
)
DSML_LOOSE_RE = re.compile(r"<[|｜]{2}\s*DSML\s*[|｜]{2}[^\n]*")


def strip_dsml(text: str) -> tuple[str, int]:
    if not text or "DSML" not in text:
        return text, 0
    cleaned, n = DSML_RE.subn("", text)
    if n == 0:                        # 结构不完整（被截断）按行兜底
        cleaned, n = DSML_LOOSE_RE.subn("", cleaned)
    cleaned = cleaned.strip()
    return cleaned, len(text) - len(cleaned)
```

正则用 `[|｜]{2}` 兼容全角和半角两种写法。

挂在 `on_decorating_result`（发送前）：

```python
@filter.on_decorating_result()
async def sanitize_output(self, event):
    result = event.get_result()
    chain = getattr(result, "chain", None)
    if not chain:
        return
    removed = 0
    for comp in chain:
        text = getattr(comp, "text", None)
        if not isinstance(text, str) or "DSML" not in text:
            continue
        new_text, cut = strip_dsml(text)
        removed += cut
        comp.text = new_text
    if not removed:
        return
    alive = [c for c in chain if getattr(c, "text", None) or not hasattr(c, "text")]
    if not alive:
        chain.clear()
        chain.append(Plain(random.choice(DSML_FALLBACKS)))
```

## 兜底话要多样化

整条回复都是垃圾时得替换成一句人话。**不要固定一句**——连着几轮都是同一句会很怪：

```python
DSML_FALLBACKS = (
    "嗯……刚走神了，你再说一遍",
    "诶，我这边没接住，再说一次",
    "等下，刚才脑子短路了，重说",
    "我好像跑偏了，你再说一遍",
)
```

我们第一版固定用"我这边刚卡了一下，你再说一遍"，
结果连续三轮都回这句，用户以为机器人死了。原文：

> 你是不是耍我？

## 只做减法，不做反向注入

有人会想"把 DSML 文本解析回真正的 tool_calls"。
不建议：那要伪造调用并重入请求链路，一旦解析出错会把正常回复也吞掉。

现在这层只做减法，失败最坏是少显示一句话，风险可控。

## 排查清单

| 现象 | 处理 |
|---|---|
| 频繁出现 | 查 `modalities` 是否含 `tool_use` |
| 偶尔出现 | 兜底层在工作，不用管 |
| 出现且兜底话重复 | 把 `DSML_FALLBACKS` 改成多句随机 |
| 兜底后整条为空 | 检查 `alive` 判断，确保有兜底文本 |
