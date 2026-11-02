# QQ 机器人「小曦」部署教程

把一个 OpenAI 兼容的大模型接进 QQ，做成能聊天、能发图、能操作 QQ 的机器人。
分两条路线，**先选路线，再照着对应的那份做**。

## 两条路线的区别

| | 本地端（Windows） | 服务器端（Linux） |
|---|---|---|
| 电脑关机/睡眠 | 机器人离线 | 照常运行 |
| 崩溃恢复 | 需要守护脚本 | systemd 自动重拉 |
| 成本 | 电费 | 约 100 元/年（4核4G 轻量） |
| 操作方式 | 本机命令行 | SSH |
| 难度 | 低 | 中 |

**只有"电脑没法一直开着"时才需要上服务器。** 本地能跑通就别折腾云。

## 文档结构

```
guides/
├── README.md                  ← 你在看的这份：选路线、看全局
├── local-windows.md           ← 本地端完整流程
├── server-linux.md            ← 服务器端完整流程
├── plugins/
│   ├── face-bridge.md         表情/照片桥：收图、发图、按描述找图
│   ├── qq-power.md            操作 QQ：放行、好友、备注、点赞、换头像、画图
│   └── local-embedding.md     本地向量服务：免费替代付费 embedding
└── troubleshooting/
    ├── tools-not-sent.md      ★ 最坑的一条：模型说"我没有工具"
    ├── capture-requests.md    抓包看真实请求
    └── dsml-leak.md           模型吐 `<||DSML||>` 乱码
```

## 组件全景

```
QQ 号（小号）
   ↕ NTQQ 协议
NapCat  ──反向 WebSocket──►  AstrBot
                              │
                              ├─ 大模型 API（对话）
                              ├─ 本地 embedding（长期记忆、按描述找图）
                              ├─ Edge TTS（按需语音）
                              └─ 插件层
                                  ├─ face_bridge   表情/照片
                                  ├─ qq_power      操作 QQ
                                  ├─ meme_manager  表情包库
                                  ├─ livingmemory  长期记忆
                                  └─ ...
```

## 三条硬约束（不分平台）

1. **只用 QQ 小号。** 第三方协议端登录主号 = 主号直接暴露在风控下，不可逆。
2. **三个端口只绑 `127.0.0.1`**：AstrBot 后台 6185、OneBot 反向 WS 6199、NapCat WebUI 6099。
   6099 尤其危险——它能直接操作 QQ 本体。
3. **ID 白名单必须开**，否则任何陌生人都能烧你的模型余额。

## 最容易踩的一个坑（先看这个）

配 provider 时，`modalities` 一定要写全：

```json
"modalities": ["text", "image", "tool_use"]
```

**漏掉 `tool_use`，AstrBot 会判定这个模型不支持工具调用，每轮请求都把整张工具表清空。**
表现是机器人反复说"我没有工具""我这边动不了"，还会吐 `<||DSML||>` 乱码。

这一条我们排查了整整一晚，详见 [troubleshooting/tools-not-sent.md](troubleshooting/tools-not-sent.md)。

## 推荐阅读顺序

1. 选路线 → 照着 `local-windows.md` 或 `server-linux.md` 做到"能对话"
2. 读 `troubleshooting/tools-not-sent.md`，确认工具真的发下去了
3. 再按需装插件（`plugins/`）
4. 最后配人格、记忆、语音这些"让它像人"的部分
