# 主动性：会主动找人、能当场记事、会到点提醒

前两件事让它"像个伙伴"，这一篇补的是最后一块：**关系感**。

一个从不主动找你、你说"记住"它也不记的机器人，能力再强也只是个工具。

## 一、主动消息

### 现状

`astrbot_plugin_proactive_chat` 装了但全关着：

```
friend_settings.enable = False
session_list = []
auto_trigger = False
```

结果是它永远只会被动应答。

### 开启配置

`data/config/astrbot_plugin_proactive_chat_config.json`：

```json
{
  "friend_settings": {
    "enable": true,
    "session_list": ["qq:FriendMessage:<你的QQ号>"],
    "auto_trigger_settings": {
      "enable_auto_trigger": true,
      "auto_trigger_after_minutes": 180
    },
    "schedule_settings": {
      "min_interval_minutes": 60,
      "max_interval_minutes": 600,
      "quiet_hours": "1-7",
      "max_unanswered_times": 2
    },
    "tts_settings": { "enable_tts": false }
  }
}
```

各字段的实际含义：

| 字段 | 值 | 作用 |
|---|---|---|
| `session_list` | 完整 UMO | 必须是 `qq:FriendMessage:<QQ>` 格式，不是纯 QQ 号 |
| `auto_trigger_after_minutes` | 180 | **三小时没说话**才考虑主动找你 |
| `min_interval_minutes` | 60 | 两次主动消息最短间隔 |
| `max_interval_minutes` | 600 | 最长间隔，随机落在这个区间 |
| `quiet_hours` | `1-7` | 凌晨 1 点到 7 点绝对不发 |
| `max_unanswered_times` | 2 | **连发两次没回就停**，避免刷屏 |
| `enable_tts` | false | 不主动发语音（口播很打扰） |

### 验证

```bash
grep '主动消息' /opt/astrbot/run.log | tail
```

应该看到：

```
[主动消息] 已为 私聊 3588255101 设置自动触发器喵，将在 180 分钟后检查是否...
[主动消息] 已为 1 个会话设置自动主动消息触发器喵。
```

## 二、立即记忆

### 现状

`livingmemory` 有两个工具默认关着：

```json
"agent_tools": {
  "enable_recall_tool": true,       // 能查
  "enable_memorize_tool": false,    // 不能主动记
  "enable_forget_tool": false       // 不能主动忘
}
```

不打开的话，它只能在每 10 轮自动总结时"顺带"记住，用户说"记住这个"它没法立刻执行。

### 开启

```json
"agent_tools": {
  "enable_recall_tool": true,
  "enable_memorize_tool": true,
  "enable_forget_tool": true
}
```

重启后日志会多两行：

```
added LLM tool: memorize_long_term_memory
added LLM tool: forget_long_term_memory
```

之后用户说"记住我下周三要去重庆"、"把刚才那条忘了"，模型会主动调用。

### 人格里要配合

工具存在不等于模型会用。人格里最好显式写：

```
- 主人说「记住这个」「别忘了」时，调 memorize_long_term_memory 真的记下来，别嘴上说记住了。
```

## 三、定时提醒

AstrBot 自带 `future_task` 工具，能定未来某个时间点执行一次动作。
默认就在工具列表里，不需要额外装插件。

配合人格里这句：

```
- 主人在意的大事（面试、考试、出远门），你可以定个提醒，到时候提他一句。
  答应了就真去定，没定就别说「到时候提醒你」。
```

**验证方法**：说一句「明天早上九点提醒我带身份证」，
然后看日志里有没有 `Agent 使用工具: ['future_task']`。

如果模型只回"好，我记住了"却没调工具——那它就是嘴上答应。
这时候要在人格里加强，或者接受"它做不到到点提醒"。

## 四、这三件事的关系

| 能力 | 解决什么 | 依赖 |
|---|---|---|
| 主动消息 | 它会不会主动找你 | proactive_chat 的调度器 |
| 立即记忆 | 你说"记住"它记不记 | livingmemory 的 memorize 工具 |
| 定时提醒 | 它答应的提醒兑不兑现 | future_task + 人格约束 |

三个都开，机器人才从"问答工具"变成"有连续性的对象"。

## 五、风控影响

主动消息会**主动发消息**，这是行为特征里比较显眼的一项。所以：

- 间隔别太短（`min_interval_minutes` 至少 60）
- 一定要有免打扰时段（`quiet_hours`）
- 一定要设 `max_unanswered_times`，别对着空气连发
- 主动内容别模板化（"在吗"重复三次最像机器人）

详见 [risk-control.md](risk-control.md)。

## 六、工具数量提醒

开了 memorize / forget 之后，工具从 15 个变成 17 个。

工具不是越多越好——`deepseek-flash` 这类轻量模型在工具表变长时更容易选错或不选。
如果发现它开始"该调工具时不调"，优先考虑砍掉用不上的工具，而不是加更多。
