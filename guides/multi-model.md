# 多模型分工：一个便宜的说话，一个聪明的记事

## 为什么要拆

机器人有两个完全不同的任务：

1. **对话**：高频、要快、要便宜，质量要求是"说得过去"
2. **记忆整理 / 上下文压缩**：低频、要准，质量差了整条记忆链都废

用同一个模型做这两件事，等于让便宜的模型去干精细活：

- 记忆摘要质量差 → 检索出来的东西没用 → 表现为"它不记得我说过什么"
- 上下文压缩丢重点 → 后面的对话越聊越偏

**这不是模型不行，是分工错了。**

## 怎么拆

| 任务 | provider | 模型 | 理由 |
|---|---|---|---|
| 主对话 | `deepseek` | `deepseek-flash` | 高频，便宜优先 |
| 记忆总结 | `deepseek-pro` | `deepseek-v4-pro` | 每 10 轮一次，质量优先 |
| 上下文压缩 | `deepseek-pro` | `deepseek-v4-pro` | 低频，压缩一次影响很久 |
| 图像理解 | `deepseek` | `deepseek-flash` | **pro 不支持图像**，只能 flash |
| 表情描述生成 | `deepseek` | `deepseek-flash` | 每张图一次，便宜够用 |
| embedding | `embedding-local` | bge-small-zh | 本地免费 |

**关键约束**：`deepseek-v4-pro` 不支持视觉输入。所以图像理解必须留在 flash 上，
`modalities` 要分别配：

```json
// flash：文本 + 图像 + 工具
"modalities": ["text", "image", "tool_use"]

// pro：文本 + 工具（没有 image）
"modalities": ["text", "tool_use"]
```

## 配置

### 1. 新增 pro provider

```json
{
  "id": "deepseek-pro",
  "enable": true,
  "model": "deepseek-v4-pro",
  "provider_source_id": "deepseek_source",
  "modalities": ["text", "tool_use"],
  "custom_extra_body": { "thinking": { "type": "disabled" } }
}
```

共用同一个 `provider_source`（同一个 key），不用配两份密钥。

### 2. 记忆总结指向 pro

`data/config/astrbot_plugin_livingmemory_config.json`：

```json
"provider_settings": {
  "embedding_provider_id": "embedding-local",
  "llm_provider_id": "deepseek-pro"
}
```

### 3. 上下文压缩指向 pro

`cmd_config.json`：

```json
"agent_runner": {
  "config": {
    "compression": {
      "provider_id": "deepseek-pro",
      "overflow_strategy": "llm_compress"
    }
  }
}
```

### 4. 主对话保持 flash

```json
"agent_runner": { "config": { "model": { "provider_id": "deepseek" } } }
```

## 效果对比

同一个输入，两个模型的记忆总结质量：

**输入**：主人说他爱吃辣，周三早八有课，最近在写论文，下周三要去重庆。

**pro 输出**：

```
【主人信息】
- 饮食偏好：爱吃辣
- 课程安排：周三早八有课
- 近期任务：写论文中
- 出行计划：下周三去重庆
```

结构清晰、可直接入库、将来检索命中率高。flash 也能总结，但容易把多条信息揉成一段话。

## 成本

后台任务 token 量很小：

| 任务 | 频率 | 单次 token |
|---|---|---|
| 记忆总结 | 每 10 轮 | 输入 2–4k，输出 200–500 |
| 上下文压缩 | 溢出时 | 输入 5–10k，输出 500–1k |

按每天 100 轮算：10 次总结 + 少量压缩 ≈ 每天 5 万 token 输入。
pro 单价是 flash 的 4.4 倍，这部分多花约 **每天 0.03 元**，一个月不到 1 块钱。

**主对话不换**，所以总成本几乎不变。

## 如果还想更聪明

主对话也换 pro 是另一笔账：按每天 100 轮、每轮 4k 输入算，
flash 约 16 元/月 → pro 约 70 元/月。

建议顺序：

1. 先把分工做好（本文，几乎不花钱）
2. 观察几天记忆是否变准
3. 还嫌笨再换主对话

## 验证

```bash
# 看 provider 都加载了
grep 'Loading model openai_chat_completion' /opt/astrbot/run.log

# 应该看到 deepseek 和 deepseek-pro 两个
```

```bash
# 记忆总结用了哪个模型（聊满 10 轮后看）
grep -A3 'memory_reflection' /opt/astrbot/run.log | tail -20
```

`livingmemory` 初始化时也会打 `Provider check passed: embedding and llm providers are ready.`
