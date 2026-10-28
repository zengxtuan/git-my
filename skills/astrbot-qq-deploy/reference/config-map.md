# 关键配置项对照表

路径基于 `data/cmd_config.json`（带 BOM，读写一律 `utf-8-sig`）。

## 安全（优先设置）

| 配置项 | 作用 | 推荐值 |
|---|---|---|
| `dashboard.host` | 后台绑定地址 | `"127.0.0.1"` |
| `dashboard.pbkdf2_password` | 后台口令哈希 | 用 `hashlib.pbkdf2_hmac('sha256', pw, salt, 600000)` 生成，格式 `pbkdf2_sha256$600000$<salt_hex>$<digest_hex>` |
| `dashboard.password` | 兼容用 MD5 字段 | 填随机值即可（有 pbkdf2 时不被采用） |
| `dashboard.password_change_required` | 强制改密 | `false`（已自设强口令时） |
| `platform_settings.enable_id_white_list` | 白名单开关 | `true` |
| `platform_settings.id_whitelist` | **UMO** 列表 | `["qq:FriendMessage:<QQ号>"]` |
| `admins_id` | 管理员 **UID** 列表 | `["<QQ号>"]` |
| `provider_settings.computer_use_runtime` | 电脑能力 | `"none"` |
| `provider_settings.computer_use_local_permissions.*.allow_execution` | 执行命令 | `false` |
| `provider_settings.computer_use_local_permissions.*.allow_network` | 联网 | `false` |
| `provider_settings.computer_use_local_permissions.*.filesystem_scope` | 文件范围 | `"workspace"` |

> **UMO 与 UID 的区别**：白名单用 UMO（`qq:FriendMessage:123`），管理员用裸 UID（`123`）。发 `/sid` 可以同时拿到两者。

## 平台（QQ 接入）

| 配置项 | 作用 | 推荐值 |
|---|---|---|
| `platform[].type` | 适配器类型 | `"aiocqhttp"` |
| `platform[].ws_reverse_host` | 反向 WS 绑定 | `"127.0.0.1"` |
| `platform[].ws_reverse_port` | 反向 WS 端口 | `6199` |
| `platform[].ws_reverse_token` | 握手 token | 随机 32 位 hex，**与 NapCat 侧一致** |

## 模型

配置会被自动迁移成两层结构：`provider_sources`（源 + key）+ `provider`（模型实例）。

| 配置项 | 作用 | 示例 |
|---|---|---|
| `provider_sources[].type` | 协议类型 | `"openai_chat_completion"` |
| `provider_sources[].api_base` | 接口地址 | `"https://api.deepseek.com/v1"` |
| `provider_sources[].key` | 密钥数组（可轮转） | `["sk-..."]` |
| `provider[].model` | 模型名 | `"deepseek-flash"` / `"deepseek-chat"` |
| `provider[].provider_source_id` | 关联哪个源 | `"deepseek_source"` |
| `agent_runner.config.model.provider_id` | 默认对话模型 | `"deepseek"` |
| `provider_settings.default_image_caption_provider_id` | 图片转述 | 同对话模型（若模型支持 image 输入） |

## 语音

| 配置项 | 作用 | 推荐值 |
|---|---|---|
| `provider_tts_settings.enable` | 总开关 | `true` |
| `provider_tts_settings.provider_id` | 用哪个 TTS | 指向具体 provider 的 `id` |
| `provider_tts_settings.trigger_probability` | 触发概率 | `1.0` 每条 / `0.33` 三条一次 / `0.1` 十条一次 |
| `provider[].type` | TTS 类型 | `"edge_tts"`（免费零显存） |
| `provider[].voice` | 音色 | `zh-CN-XiaoyiNeural` 等 |

**前置依赖：ffmpeg 必须在 PATH 里**，否则合成完转不了码，静默回退文字。

## 长期记忆

| 配置项 | 作用 | 推荐值 |
|---|---|---|
| embedding provider `type` | 协议 | `"openai_embedding"`（兼容绝大多数云服务） |
| `api_base` | 接口 | `https://api.siliconflow.cn/v1` |
| `embedding_model` | 模型 | `BAAI/bge-m3` |
| `embedding_dimensions` | 维度 | `1024`（bge-m3 固定 1024） |
| 记忆插件 `embedding_provider_id` | 关联 | 指向上面那个 provider 的 `id` |

本地替代：`"ollama_embedding"` + `http://127.0.0.1:11434` + `bge-m3`。

## 人格

不在 JSON，在 SQLite：

```sql
-- data/data_v4.db
INSERT INTO personas (created_at, updated_at, persona_id, system_prompt,
                      begin_dialogs, tools, skills, sort_order)
VALUES (datetime('now'), datetime('now'), 'my-persona', '<系统提示词>',
        '[]', 'null', 'null', 0);
```

然后 `agent_runner.config.persona.persona_id = "my-persona"`，并把 `safety_mode` 设为 `false`（否则会额外注入一段"守规矩"提示）。

## 平台设置杂项

| 配置项 | 作用 | 说明 |
|---|---|---|
| `platform_settings.rate_limit` | 限流 | `{time:60, count:30}` 防刷 |
| `platform_settings.no_permission_reply` | 无权限是否回复 | `true` 会提示，`false` 静默 |
| `provider_settings.datetime_system_prompt` | 注入当前时间 | `true`，人格才能"知道几点" |
| `agent_runner.config.compression.overflow_strategy` | 上下文超限策略 | `"llm_compress"` 摘要压缩 |
