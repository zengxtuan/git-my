# 抓包：看 AstrBot 到底发了什么给模型

当"明明配了、就是不生效"时，别再猜。把真实请求抓下来看。

## 做法

在服务器上起一个反向代理，把 DeepSeek 的 `api_base` 临时指过去，记录完整 body 再转发。

## 1. 保存代理脚本

`/opt/embed-svc/ds_proxy.py`：

```python
"""临时抓包代理：把 AstrBot 发往 DeepSeek 的请求记录下来再转发。"""

from __future__ import annotations

import json
import time
from pathlib import Path

from aiohttp import ClientSession, web

UPSTREAM = "https://api.deepseek.com"
LOG = Path("/tmp/dsreq.jsonl")
HOP_BY_HOP = {"host", "content-length", "connection", "accept-encoding"}


async def handle(request: web.Request) -> web.StreamResponse:
    body = await request.read()
    try:
        parsed = json.loads(body.decode("utf-8"))
        with LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps({
                "ts": time.strftime("%H:%M:%S"),
                "model": parsed.get("model"),
                "messages_count": len(parsed.get("messages") or []),
                "tools_count": len(parsed.get("tools") or []),
                "tool_names": [t.get("function", {}).get("name")
                               for t in (parsed.get("tools") or [])],
                "tool_choice": parsed.get("tool_choice"),
                "thinking": parsed.get("thinking"),
                "body_sample": json.dumps(parsed, ensure_ascii=False)[:2000],
            }, ensure_ascii=False) + "\n")
    except Exception as e:
        with LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": time.strftime("%H:%M:%S"), "error": str(e)},
                               ensure_ascii=False) + "\n")

    headers = {k: v for k, v in request.headers.items() if k.lower() not in HOP_BY_HOP}
    async with ClientSession() as sess:
        async with sess.request(request.method, UPSTREAM + request.path_qs,
                                headers=headers, data=body) as resp:
            data = await resp.read()
            out = web.Response(status=resp.status, body=data)
            for k, v in resp.headers.items():
                if k.lower() not in ("content-length", "transfer-encoding", "content-encoding"):
                    out.headers[k] = v
            return out


app = web.Application(client_max_size=64 * 1024 * 1024)
app.router.add_route("*", "/{tail:.*}", handle)

if __name__ == "__main__":
    web.run_app(app, host="127.0.0.1", port=8787, print=None)
```

## 2. 启动

```bash
rm -f /tmp/dsreq.jsonl
nohup /opt/astrbot/venv/bin/python /opt/embed-svc/ds_proxy.py > /tmp/ds_proxy.log 2>&1 &
sleep 3
ss -tlnp | grep 8787      # 应该有监听
```

## 3. 把 provider 指过去

```python
import json, shutil
p = "/opt/astrbot/data/cmd_config.json"
shutil.copyfile(p, p + ".bak-proxy")
d = json.load(open(p, encoding="utf-8-sig"))
for s in d["provider_sources"]:
    s["api_base"] = "http://127.0.0.1:8787/v1"
json.dump(d, open(p, "w", encoding="utf-8-sig"), ensure_ascii=False, indent=4)
```

```bash
systemctl restart astrbot
```

## 4. 发一条消息，然后读记录

```python
import json
for line in open("/tmp/dsreq.jsonl", encoding="utf-8"):
    d = json.loads(line)
    print(d["ts"], "tools=", d["tools_count"], "choice=", d["tool_choice"],
          "thinking=", d["thinking"])
    print("  工具:", ", ".join(n for n in d.get("tool_names") or [] if n))
```

## 5. 一定要收尾

```bash
# 改回来
python3 - <<'PY'
import json
p = "/opt/astrbot/data/cmd_config.json"
d = json.load(open(p, encoding="utf-8-sig"))
for s in d["provider_sources"]:
    s["api_base"] = "https://api.deepseek.com/v1"
json.dump(d, open(p, "w", encoding="utf-8-sig"), ensure_ascii=False, indent=4)
PY

pkill -f ds_proxy.py
rm -f /tmp/dsreq.jsonl
systemctl restart astrbot
```

**不收尾会很慢**：多一层本地转发，而且代理挂了整个模型调用就断。

## 能看出什么

| 字段 | 异常值说明 |
|---|---|
| `tools_count: 0` | 工具没发下去 → [tools-not-sent.md](tools-not-sent.md) |
| `tool_choice: None` | 同上，通常是 tools 为空连带 |
| `thinking: {"type": "disabled"}` | 确认 thinking 关闭是否真生效 |
| `system` 长度过大 | 人格 + 记忆注入 + 插件提示词把上下文撑爆 |
| `messages_count` 很大 | 会话历史没压缩，`max_turns` 是不是 -1 |

## 这套方法的通用价值

不止 DeepSeek：任何 OpenAI 兼容接口都能这么抓。
排查"配置改了但没生效"时，先抓一次，比翻源码猜快十倍。

## 安全提醒

抓下来的 body 含完整人格和对话内容，`/tmp/dsreq.jsonl` 用完立刻删。
`api_base` 指向本机代理期间，数据不经过除上游以外的第三方，但仍然是明文落盘。
