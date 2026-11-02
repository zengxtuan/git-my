# 本地 embedding 服务（免费替代付费向量）

长期记忆的向量召回、"按描述找图"都依赖 embedding。云端 API 要钱，这台机器上跑一个就行。

源码：`src/local_embedding_service/`

## 为什么需要

硅基流动等服务的余额一断，日志里就会刷：

```
Retrieval route 向量 failed: Error code: 402 - Sorry, your account balance is insufficient
```

连带影响：语义检索、长期记忆召回、按描述找图全部降级。
DeepSeek 本身**没有 embedding 端点**，只有 chat 和 search，所以必须另外找一家。

## 方案对比

| 方案 | 成本 | 内存 | 说明 |
|---|---|---|---|
| 硅基流动 bge-m3 | 约 0.1 元/百万 token | 无 | 最省事，要充值 |
| Ollama + 模型 | 0 | 274MB~1.2G | 要装 Ollama，下载大 |
| **fastembed（本方案）** | 0 | 约 200MB | 纯 pip，ONNX 推理，CPU 够用 |

选 fastembed 的理由：不依赖 GitHub 下大文件、模型从 hf-mirror 拉、内存占用小。

## 部署

```bash
apt install -y python3-venv
mkdir -p /opt/embed-svc && cd /opt/embed-svc
python3 -m venv venv
venv/bin/pip install fastembed fastapi uvicorn -i https://pypi.tuna.tsinghua.edu.cn/simple

cp <repo>/src/local_embedding_service/app.py /opt/embed-svc/
cp <repo>/src/local_embedding_service/local-embedding.service /etc/systemd/system/

systemctl daemon-reload
systemctl enable --now local-embedding
```

首次启动会从 `hf-mirror.com` 下模型（约 95MB，40 秒左右）。

## 验证

```bash
curl -s http://127.0.0.1:8484/health
```

```json
{"ok":true,"model":"BAAI/bge-small-zh-v1.5","dim":512,"load_seconds":0.3}
```

中文相似度自测：

```python
「周三有课，早上八点」 vs 「我星期三早上的课」   → 0.80
「主人爱吃辣」       vs 「他特别喜欢川菜和火锅」 → 0.60
「主人爱吃辣」       vs 「今天股市跌了不少」     → 0.33
```

## 接进 AstrBot

新增一个 OpenAI 兼容 embedding provider：

```json
{
  "id": "embedding-local",
  "type": "openai_embedding",
  "provider": "openai",
  "provider_type": "embedding",
  "enable": true,
  "embedding_api_key": "local",
  "embedding_api_base": "http://127.0.0.1:8484/v1",
  "embedding_model": "BAAI/bge-small-zh-v1.5",
  "embedding_dimensions": 512
}
```

然后把用到 embedding 的插件指过来：

- `livingmemory`：`provider_settings.embedding_provider_id = "embedding-local"`
- `meme_manager`：`semantic.embedding_provider_id`

## 换维度会自动重建索引

从 bge-m3（1024 维）换成 bge-small-zh（512 维）时，日志会刷：

```
检测到 FAISS 索引维度不匹配: 索引维度=1024, 当前 Embedding Provider 维度=512
旧索引将被删除，系统会自动重建索引
向量重建进度: processed=6/6, failed=0
索引重建结果: accepted=True, partial=False, bm25=6/6, vector=6/6, errors=0
```

这是正常流程，不用手工干预。

## 资源与安全

systemd unit 里已经加了内存上限：

```ini
MemoryHigh=1200M
MemoryMax=1500M
```

4G 内存机器建议同时开 2G swap，防 OOM。

服务只监听 `127.0.0.1:8484`，不对外。

## 常见问题

| 现象 | 原因 |
|---|---|
| 启动后 `/health` 404 | 端口被占，换 8484 或看 `journalctl -u local-embedding` |
| 首次启动卡很久 | 正在下模型，`tail -f` 看日志 |
| 相似度普遍偏低 | 用了英文模型跑中文；确认是 `bge-small-zh-v1.5` |
| 内存吃紧 | 换更小的模型，或把 `MemoryMax` 调低让它自动降级 |
