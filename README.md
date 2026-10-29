# git-my

个人技能库：把踩过的坑固化成可复用的 AI Agent Skills。

## 什么是 Skill

一份写给 AI 的操作手册 + 配套脚本模板。它不是可执行程序，而是在任务匹配时被 AI 加载、照着执行的文档。

```
<skill-name>/
├── SKILL.md      ← 入口，YAML frontmatter 里写触发条件
├── reference/    ← 详细参考文档，按需加载
├── scripts/      ← 可直接调用的脚本
└── templates/    ← 配置模板
```

`SKILL.md` 的 frontmatter 决定何时触发：

```yaml
---
name: skill-name
description: Use when ...（AI 靠这句判断该不该加载）
---
```

支持 Agent Skills 的工具（DSH、Claude Code、Cursor 等）可以直接使用。

## 技能列表

### `dsh-qq`

把一个 OpenAI 兼容的大模型接进 QQ 小号，并挂上长期记忆、语音、表情包、空间动态、定时自启。

支持两种部署形态：**本地 Windows**（无需服务器，但电脑不能关机）和 **云端 Linux 服务器**（约 109 元/年，彻底摆脱"电脑必须一直开着"）。

**包含内容**

|文件|内容|
|-|-|
|`SKILL.md`|硬约束、四层架构、本地/云端形态对比、8 个阶段流程（每步带验证命令）|
|`reference/architecture.md`|组件职责、一条消息的完整旅程、端口/进程/存储对照|
|`reference/cloud-linux.md`|**云端部署全流程**：换源、Python 3.12、AstrBot、插件绕过 CLI、NapCat+Xvfb、配置迁移、systemd、安全收尾|
|`reference/pitfalls.md`|15 节实战踩坑：BOM、CRLF、配置覆盖、token 随机化、ffmpeg、ACL 锁死、操作层面的手滑…|
|`reference/config-map.md`|配置项 → 作用 → 推荐值|
|`scripts/setup.py`|幂等一键配置（安全加固 + 平台 + 模型 + 语音 + 白名单）|
|`scripts/keepalive.ps1`|进程级自愈守护|
|`templates/`|OneBot 配置、人格模板、开机启动项|

**核心结论（省得重新踩一遍）**

* 只用 QQ 小号，第三方协议端登录主号不可逆
* 三个端口只绑 `127.0.0.1`：`6185` AstrBot 后台 / `6199` OneBot 反向 WS / `6099` NapCat WebUI（后者能直接操作 QQ 本体，最危险）
* 模型不决定身份，**system prompt 决定**——不写人格就会自称"AstrBot 助手"
* 语音哑了先查 ffmpeg，缺它时是**静默**回退成文字的
* 改配置前必须停进程，否则会被内存态回写覆盖

## 使用方式

把这些目录放到工具会扫描的 skills 根目录即可：

|工具|常见位置|
|-|-|
|DSH|`<project>/.dsh/skills/` 或 `<dshHome>/skills/`|
|Claude Code|`\~/.claude/skills/`|
|其他|见各自文档|

也可以用 junction / symlink 链到仓库里的目录，保持单一副本。

## 许可

[MIT](skills/dsh-qq/LICENSE) —— 可自由使用、修改、再分发，只需保留版权声明。

