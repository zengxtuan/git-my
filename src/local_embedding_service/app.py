"""极简 OpenAI 兼容 embedding 服务，给 AstrBot 用。

模型：BAAI/bge-small-zh-v1.5（中文，512 维，ONNX，内存约 200MB）
只监听 127.0.0.1，不对外。
"""

from __future__ import annotations

import os
import time

os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

import uvicorn
from fastapi import FastAPI
from fastembed import TextEmbedding
from pydantic import BaseModel

MODEL_NAME = os.environ.get("EMBED_MODEL", "BAAI/bge-small-zh-v1.5")
CACHE_DIR = os.environ.get("EMBED_CACHE", "/opt/embed-svc/models")

_t0 = time.time()
model = TextEmbedding(model_name=MODEL_NAME, cache_dir=CACHE_DIR)
_load_seconds = round(time.time() - _t0, 1)

app = FastAPI(title="local-embedding")


class EmbeddingRequest(BaseModel):
    input: str | list[str]
    model: str | None = None
    encoding_format: str | None = None


def _vectors(texts: list[str]) -> list[list[float]]:
    return [list(map(float, v)) for v in model.embed(texts)]


def _response(texts: list[str]) -> dict:
    vecs = _vectors(texts)
    tokens = sum(len(t) for t in texts)
    return {
        "object": "list",
        "data": [
            {"object": "embedding", "index": i, "embedding": v}
            for i, v in enumerate(vecs)
        ],
        "model": MODEL_NAME,
        "usage": {"prompt_tokens": tokens, "total_tokens": tokens},
    }


@app.get("/health")
def health() -> dict:
    return {
        "ok": True,
        "model": MODEL_NAME,
        "dim": len(_vectors(["健康检查"])[0]),
        "load_seconds": _load_seconds,
    }


@app.post("/v1/embeddings")
def embeddings(body: EmbeddingRequest) -> dict:
    texts = body.input if isinstance(body.input, list) else [body.input]
    return _response([str(t) for t in texts])


@app.post("/embeddings")
def embeddings_alias(body: EmbeddingRequest) -> dict:
    return embeddings(body)


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8484, log_level="info")
