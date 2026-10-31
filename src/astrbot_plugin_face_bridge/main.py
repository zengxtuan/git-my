"""face_bridge —— 大肥鱼的多媒体桥。

职责只有三件，尽量不互相干扰：

1. **收**：收到图 → 判类型（表情 / 照片）→ 落盘入库；
   表情顺手塞进 QQ 收藏表情（NapCat ``add_custom_face``），照片不进收藏。
2. **发**：模型调 ``send_media``，或用户说了明确的索图话，把库里那张发出去。
3. **护**：剥掉 DSML 泄漏文本；用户要语音时才合成语音；问梗时先联网查一次。

索引只有一个文件 ``media.json``，表情和照片靠 ``kind`` 字段区分。
"""

from __future__ import annotations

import hashlib
import json
import random
import re
import shutil
import time
from pathlib import Path

from astrbot.api import AstrBotConfig, llm_tool, logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.message_components import Image, Plain, Record
from astrbot.api.star import Context, Star, StarTools

PLUGIN_NAME = "astrbot_plugin_face_bridge"
MEDIA_INDEX = "media.json"
MEDIA_DIR = "media"
EXT_WHITELIST = {".gif", ".jpg", ".jpeg", ".png", ".webp", ".bmp"}

# 表情还是照片：GIF 一律表情；边长 ≤512 且 ≤300KB 也算表情；其余按照片存
MEME_MAX_SIDE = 512
MEME_MAX_BYTES = 300 * 1024

# ---------------------------------------------------------------- 文本匹配

# 明确的索图说法。"发这个给我""把这个还我"这种指代式也要认。
SEND_PATTERN = (
    r"(发我|发给我|发过来|发回来|发一下|发来看看|发我看看|那张|这张|壁纸)"
    r"|((发|还|回|给)[^，。！？]{0,6}(这个|这张|那张|它|原图|表情|照片|图))"
    r"|((这个|这张|那张|它)[^，。！？]{0,4}(还|发|回)(我|给我|来))"
)
SEND_RE = re.compile(SEND_PATTERN)
PHOTO_WORD_RE = re.compile(r"(照片|壁纸|截图|相片|大图|原图)")
PHOTO_SAVE_RE = re.compile(
    r"(?:(?:存|保存|留着|收着|收藏).{0,8}(?:这张|这图|照片|我发))"
    r"|(?:(?:这张|这图|这张图|这张照片|照片).{0,6}(?:存下来|保存|留着|收着))"
)
VOICE_WORDS = (
    "语音",
    "声音",
    "念给我听",
    "念给我",
    "说句话",
    "说话给我听",
    "读给我听",
)
LOOKUP_RE = re.compile(
    r"(梗|什么意思|啥意思|这是啥|怎么理解|什么鬼|啥啊|啥梗|啥玩意|啥东西|什么梗)"
)
ABBR_RE = re.compile(r"^[a-zA-Z]{3,12}$")

# deepseek-flash 会把内部工具标记当正文吐出来，竖线是全角 U+FF5C
DSML_RE = re.compile(
    r"<[|｜]{2}\s*DSML\s*[|｜]{2}[\s\S]*?(?:</[|｜]{2}\s*DSML\s*[|｜]{2}\s*calls>|\Z)"
)
DSML_LOOSE_RE = re.compile(r"<[|｜]{2}\s*DSML\s*[|｜]{2}[^\n]*")
DSML_FALLBACKS = (
    "嗯……刚走神了，你再说一遍",
    "诶，我这边没接住，再说一次",
    "等下，刚才脑子短路了，重说",
)


def strip_dsml(text: str) -> tuple[str, int]:
    """剥掉 DSML 标记块，返回 (清理后文本, 删除的字符数)。"""
    if not text or "DSML" not in text:
        return text, 0
    cleaned, n = DSML_RE.subn("", text)
    if n == 0:
        cleaned, n = DSML_LOOSE_RE.subn("", cleaned)
    cleaned = cleaned.strip()
    return cleaned, len(text) - len(cleaned)


def md5_of(path: Path) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def image_size(path: Path) -> tuple[int, int]:
    try:
        from PIL import Image as PILImage

        with PILImage.open(path) as im:
            return int(im.width), int(im.height)
    except Exception:
        return 0, 0


def looks_like_meme(ext: str, size: int, w: int, h: int) -> bool:
    if ext == ".gif":
        return True
    if w and h and max(w, h) > MEME_MAX_SIDE:
        return False
    return size <= MEME_MAX_BYTES


def _now_day() -> str:
    return time.strftime("%Y-%m-%d")


def _ngrams(text: str, n: int = 2) -> set[str]:
    """中文没空格，用 2-gram 做粗匹配。"""
    s = re.sub(r"[^\u4e00-\u9fa5a-zA-Z0-9]", "", text or "")
    if len(s) < n:
        return {s} if s else set()
    return {s[i : i + n] for i in range(len(s) - n + 1)}


def _cosine(a: list[float], b: list[float]) -> float:
    import math

    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if not na or not nb:
        return 0.0
    return dot / (na * nb)


CAPTION_PROMPT = "用一句中文说清这张图的画面主体和情绪或梗，30 字以内，不要客套。"


def _image_data_url(path: Path, max_side: int = 512) -> str:
    """把图片压成 base64 data URL（体积小、省 token）。"""
    import base64
    import io

    try:
        from PIL import Image as PILImage

        with PILImage.open(path) as im:
            im = im.convert("RGB")
            im.thumbnail((max_side, max_side))
            buf = io.BytesIO()
            im.save(buf, format="JPEG", quality=80)
            return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
    except Exception:
        raw = path.read_bytes()
        return "data:image/jpeg;base64," + base64.b64encode(raw).decode()


class FaceBridge(Star):
    """大肥鱼的表情/照片桥。"""

    def __init__(self, context: Context, config: AstrBotConfig | None = None) -> None:
        super().__init__(context, config)
        self.config = config or {}
        try:
            self.root = Path(StarTools.get_data_dir(PLUGIN_NAME))
        except Exception:
            self.root = Path("data/plugin_data") / PLUGIN_NAME
        self.media_dir = self.root / MEDIA_DIR
        self.media_dir.mkdir(parents=True, exist_ok=True)
        self.index_path = self.root / MEDIA_INDEX

        self.media: dict[str, dict] = self._load_media()
        self.max_bytes = int(self.config.get("max_send_bytes", 5242880) or 5242880)
        self.photo_max_bytes = int(
            self.config.get("photo_max_bytes", 20971520) or 20971520
        )
        self.recent_media: dict | None = None
        self._caption_vec: dict[str, list[float]] = {}

        faces = sum(1 for r in self.media.values() if r.get("kind") == "face")
        logger.info("[face_bridge] loaded: %d faces / %d photos", faces, len(self.media) - faces)

    # ------------------------------------------------------------------ 索引

    def _load_media(self) -> dict[str, dict]:
        if self.index_path.exists():
            try:
                data = json.loads(self.index_path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return data
            except Exception as e:
                logger.error("[face_bridge] media.json 读取失败: %s", e)
        return self._migrate_legacy()

    def _migrate_legacy(self) -> dict[str, dict]:
        """把旧的 index.json / photos.json 合并进 media.json，并把文件搬进 media/。"""
        merged: dict[str, dict] = {}
        for name, kind in (("index.json", "face"), ("photos.json", "photo")):
            p = self.root / name
            if not p.exists():
                continue
            try:
                old = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            for key, rec in (old or {}).items():
                if not isinstance(rec, dict):
                    continue
                rec.setdefault("id", key)
                rec["kind"] = kind
                src = Path(rec.get("path", ""))
                if src.exists() and src.parent != self.media_dir:
                    dest = self.media_dir / src.name
                    try:
                        if not dest.exists():
                            shutil.move(str(src), str(dest))
                        rec["path"] = str(dest)
                    except Exception as e:
                        logger.warning("[face_bridge] 迁移文件失败 %s: %s", src, e)
                merged[key] = rec
        if merged:
            self.media = merged
            self._save_media()
            logger.info("[face_bridge] 旧索引已迁移到 media.json（%d 条）", len(merged))
        return merged

    def _save_media(self) -> None:
        try:
            self.index_path.write_text(
                json.dumps(self.media, ensure_ascii=False, indent=1), encoding="utf-8"
            )
        except Exception as e:
            logger.error("[face_bridge] media.json 写入失败: %s", e)

    def by_kind(self, kind: str) -> list[dict]:
        return [r for r in self.media.values() if r.get("kind") == kind]

    def latest(self, kind: str | None = None) -> dict | None:
        pool = [r for r in self.media.values() if kind is None or r.get("kind") == kind]
        if not pool:
            return None
        return max(pool, key=lambda r: r.get("added", 0))

    def find(self, want: str, kind: str | None = None) -> dict | None:
        """同步兜底匹配：2-gram 重叠 + 编号前缀（embedding 不可用时用）。"""
        want = (want or "").strip()
        if not want:
            return None
        pool = [r for r in self.media.values() if kind is None or r.get("kind") == kind]
        want_grams = _ngrams(want)
        best, best_score = None, 0
        for rec in pool:
            cap = str(rec.get("caption") or "")
            if not cap:
                continue
            score = len(want_grams & _ngrams(cap))
            if score > best_score:
                best, best_score = rec, score
        if best is not None and best_score >= 2:
            return best
        if len(want) >= 6:
            for key, rec in self.media.items():
                if key.startswith(want) and (kind is None or rec.get("kind") == kind):
                    return rec
        return None

    async def afind(self, want: str, kind: str | None = None) -> dict | None:
        """优先用本地 embedding 做语义匹配，失败退回 2-gram。"""
        want = (want or "").strip()
        if not want:
            return None
        pool = [r for r in self.media.values() if kind is None or r.get("kind") == kind]
        pool = [r for r in pool if str(r.get("caption") or "").strip()]
        if pool:
            vec = await self._embed(want)
            if vec:
                scored = []
                for rec in pool:
                    cap = str(rec.get("caption"))
                    cvec = self._caption_vec.get(cap) or await self._embed(cap)
                    if not cvec:
                        continue
                    self._caption_vec[cap] = cvec
                    # 向量相似度 + 字面命中加成（"委屈"这种实词命中要压过语义近似）
                    base = _cosine(vec, cvec)
                    overlap = len(_ngrams(want) & _ngrams(cap))
                    score = base + min(overlap * 0.08, 0.35)
                    scored.append((score, base, rec))
                if scored:
                    scored.sort(key=lambda x: x[0], reverse=True)
                    top, base, rec = scored[0]
                    logger.info(
                        "[face_bridge] 匹配 %.3f(基础%.3f) -> %s",
                        top,
                        base,
                        str(rec.get("caption", ""))[:24],
                    )
                    if top >= 0.45:
                        return rec
        # 退回 2-gram / 编号
        rec = self.find(want, kind=kind)
        if rec is None and kind is not None:
            rec = self.find(want)
        return rec

    async def _embed(self, text: str) -> list[float] | None:
        try:
            import aiohttp

            tm = aiohttp.ClientTimeout(total=8)
            async with aiohttp.ClientSession(timeout=tm) as sess:
                async with sess.post(
                    "http://127.0.0.1:8484/v1/embeddings",
                    json={"input": text[:200]},
                ) as resp:
                    if resp.status != 200:
                        return None
                    data = await resp.json()
            return list(data["data"][0]["embedding"])
        except Exception:
            return None

    # ------------------------------------------------------------------ 入库

    async def _ingest(
        self,
        event: AstrMessageEvent,
        img: Image,
        force_kind: str | None = None,
        note: str = "",
        src: Path | None = None,
    ) -> dict | None:
        """把消息里的图落盘入库；force_kind 为 None 时自动判别。"""
        try:
            src = src or Path(await img.convert_to_file_path())
        except Exception as e:
            logger.error("[face_bridge] 图片落地失败: %s", e)
            return None
        if not src.exists():
            return None

        size = src.stat().st_size
        ext = src.suffix.lower()
        if ext not in EXT_WHITELIST:
            ext = ".jpg"
        w, h = image_size(src) if ext != ".gif" else (0, 0)
        kind = force_kind or ("face" if looks_like_meme(ext, size, w, h) else "photo")

        limit = self.max_bytes if kind == "face" else self.photo_max_bytes
        if size > limit:
            logger.warning("[face_bridge] %s 过大跳过: %d bytes", kind, size)
            return None

        md5 = md5_of(src)
        mid = md5[:12]
        if mid in self.media:
            rec = self.media[mid]
            self.recent_media = {
                "id": mid,
                "path": rec.get("path", ""),
                "kind": rec.get("kind"),
            }
            logger.info("[face_bridge] 已存在，跳过入库: %s", mid)
            return rec

        stamp = time.strftime("%Y%m%d-%H%M%S")
        dest = self.media_dir / f"{stamp}_{mid}{ext}"
        try:
            if src.resolve() != dest.resolve():
                shutil.copyfile(src, dest)
        except Exception as e:
            logger.error("[face_bridge] 复制失败: %s", e)
            return None

        try:
            sender = event.get_sender_id()
        except Exception:
            sender = ""

        rec = {
            "id": mid,
            "kind": kind,
            "path": str(dest),
            "md5": md5,
            "size": size,
            "w": w,
            "h": h,
            "added": int(time.time()),
            "day": _now_day(),
            "sender": sender,
            "caption": "",
            "note": note,
            "in_qq_face": False,
        }
        self.media[mid] = rec
        self._save_media()
        self.recent_media = {"id": mid, "path": str(dest), "kind": kind}
        logger.info("[face_bridge] 入库 %s (%s, %dx%d, %d bytes)", mid, kind, w, h, size)

        cap = await self._caption(dest)
        if cap:
            rec["caption"] = cap
            self._save_media()
        return rec

    async def _caption(self, path: Path) -> str:
        """让视觉模型给这张图写一句描述，用于以后按内容找图。"""
        key = self._deepseek_key()
        if not key or not path.exists():
            return ""
        try:
            import aiohttp

            payload = {
                "model": "deepseek-flash",
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": CAPTION_PROMPT},
                            {"type": "image_url", "image_url": {"url": _image_data_url(path)}},
                        ],
                    }
                ],
                "max_tokens": 200,
                "thinking": {"type": "disabled"},
            }
            tm = aiohttp.ClientTimeout(total=25)
            async with aiohttp.ClientSession(timeout=tm) as sess:
                async with sess.post(
                    "https://api.deepseek.com/v1/chat/completions",
                    json=payload,
                    headers={
                        "Authorization": f"Bearer {key}",
                        "Content-Type": "application/json",
                    },
                ) as resp:
                    if resp.status != 200:
                        logger.warning("[face_bridge] 生成描述 HTTP %s", resp.status)
                        return ""
                    data = await resp.json()
            text = (data["choices"][0]["message"].get("content") or "").strip()
            if text:
                logger.info("[face_bridge] 描述已生成: %s", text[:40])
            return text[:80]
        except Exception as e:
            logger.warning("[face_bridge] 生成描述失败: %s", e)
            return ""

    def _deepseek_key(self) -> str:
        try:
            cfg = self.context.get_config()
            for src in cfg.get("provider_sources", []):
                keys = src.get("key") or []
                if keys:
                    return str(keys[0])
        except Exception:
            pass
        return ""

    async def _add_to_qq_face(self, event: AstrMessageEvent, path: Path) -> tuple[bool, str]:
        bot = getattr(event, "bot", None)
        if not hasattr(bot, "call_action"):
            return False, "当前平台无 call_action"
        try:
            ret = await bot.call_action("add_custom_face", file=str(path), is_origin=True)
            logger.info("[face_bridge] add_custom_face ok: %s", path.name)
            return True, str(ret)[:200]
        except Exception as e:
            logger.error("[face_bridge] add_custom_face 失败: %s", e)
            return False, str(e)[:200]

    # ------------------------------------------------------------------ 发送

    async def _deliver(self, event: AstrMessageEvent, rec: dict, reason: str) -> bool:
        path = Path(rec.get("path", ""))
        if not path.exists():
            logger.warning("[face_bridge] 文件丢失: %s", rec.get("id"))
            return False
        await event.send(MessageChain([Image.fromFileSystem(path)]))
        self.recent_media = {
            "id": rec.get("id"),
            "path": str(path),
            "kind": rec.get("kind"),
        }
        logger.info("[face_bridge] 发送 %s (%s) <- %s", rec.get("id"), rec.get("kind"), reason)
        return True

    async def _deliver_best(
        self, event: AstrMessageEvent, want: str = "", reason: str = "tool"
    ) -> dict:
        """按 want 找最合适的一张发出去。"""
        wants_photo = any(k in want for k in ("照片", "壁纸", "截图", "相片", "原图"))
        wants_face = "表情" in want
        kind = "photo" if wants_photo else ("face" if wants_face else None)

        rec = await self.afind(want, kind=kind)
        if rec and await self._deliver(event, rec, reason):
            return {"ok": True, "sent": rec["id"], "kind": rec.get("kind")}

        # 兜底也要守住类型：说"表情包"时别把照片甩过去
        if self.recent_media:
            cand = self.media.get(self.recent_media.get("id") or "")
            if cand and (kind is None or cand.get("kind") == kind):
                if await self._deliver(event, cand, f"{reason}/recent"):
                    return {"ok": True, "sent": cand["id"], "kind": cand.get("kind")}

        rec = self.latest(kind) or (self.latest() if kind is None else None)
        if rec and await self._deliver(event, rec, f"{reason}/latest"):
            return {"ok": True, "sent": rec["id"], "kind": rec.get("kind")}
        return {"ok": False, "reason": "库里还没有图"}

    # ------------------------------------------------------------------ 钩子

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_any_message(self, event: AstrMessageEvent):
        """收到图就静默入库，不打断对话。"""
        if not self.config.get("auto_archive", True):
            return
        try:
            images = [c for c in event.get_messages() if isinstance(c, Image)]
        except Exception as e:
            logger.error("[face_bridge] 取消息链失败: %s", e)
            return
        if not images:
            return
        text = ""
        try:
            text = event.get_message_str() or ""
        except Exception:
            pass
        force_kind = "photo" if PHOTO_SAVE_RE.search(text) else None

        for img in images:
            try:
                src = None
                try:
                    src = Path(await img.convert_to_file_path())
                except Exception:
                    pass
                rec = await self._ingest(event, img, force_kind=force_kind, src=src)
                if rec is None or rec.get("kind") != "face" or rec.get("in_qq_face"):
                    continue
                ok, info = await self._add_to_qq_face(event, Path(rec["path"]))
                rec["in_qq_face"] = ok
                rec["qq_result"] = info
                self._save_media()
            except Exception as e:
                logger.error("[face_bridge] 入库异常: %s", e, exc_info=True)

    @filter.on_decorating_result()
    async def sanitize_output(self, event: AstrMessageEvent):
        """发送前剥掉 DSML 泄漏文本。"""
        try:
            result = event.get_result()
        except Exception:
            return
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
            logger.warning("[face_bridge] 整条回复都是 DSML，已替换兜底文本")
        else:
            logger.warning("[face_bridge] 已剥掉 DSML %d 字符", removed)

    @filter.on_decorating_result(priority=50)
    async def voice_on_demand(self, event: AstrMessageEvent):
        """只有用户明确要语音时才合成。"""
        try:
            text = event.get_message_str() or ""
        except Exception:
            return
        if not any(w in text for w in VOICE_WORDS):
            return
        try:
            result = event.get_result()
            chain = getattr(result, "chain", None)
            if not chain:
                return
            spoken = "".join(
                c.text for c in chain if isinstance(c, Plain) and getattr(c, "text", None)
            ).strip()
            if not spoken:
                return
            provider = self.context.get_using_tts_provider(event.unified_msg_origin)
            if provider is None:
                logger.warning("[face_bridge] 要语音但没有可用 TTS provider")
                return
            audio = await provider.get_audio(spoken[:200])
            if audio:
                chain.append(Record(file=audio))
                logger.info("[face_bridge] 按需语音已附加: %s", audio)
        except Exception as e:
            logger.error("[face_bridge] 按需语音失败: %s", e)

    @filter.on_llm_request(priority=3)
    async def inject_recent_media_hint(self, event: AstrMessageEvent, req):
        """告诉模型刚才已经发过哪张，免得它回头问"哪张啊"。"""
        rec = self.recent_media
        if not rec:
            return
        kind = "表情包" if rec.get("kind") == "face" else "照片"
        note = (
            f"【前提：你刚已经把那{kind}发出去了（编号 {rec.get('id')}），"
            "不要再问用户是哪张，也不要重复发同一张，除非他明确要重发】"
        )
        self._append_context(req, note)

    @filter.on_llm_request(priority=2)
    async def auto_lookup(self, event: AstrMessageEvent, req):
        """问梗/问生词的短消息，先联网查一次。"""
        try:
            text = (event.get_message_str() or "").strip()
        except Exception:
            return
        if not text or len(text) > 60:
            return
        if not (LOOKUP_RE.search(text) or ABBR_RE.fullmatch(text)):
            return
        key = self._websearch_key()
        if not key:
            return
        summary = await self._search_web(text, key)
        if not summary:
            return
        self._append_context(
            req, "【刚联网查到的资料，别照抄原话，用你自己的口气讲】：\n" + summary
        )
        logger.info("[face_bridge] 梗检索命中 %r -> %d 字", text[:20], len(summary))

    @staticmethod
    def _append_context(req, note: str) -> None:
        parts = getattr(req, "extra_user_content_parts", None)
        try:
            if isinstance(parts, list):
                parts.append({"type": "text", "text": note})
            elif hasattr(req, "prompt"):
                req.prompt = (req.prompt or "") + "\n\n" + note
        except Exception as e:
            logger.warning("[face_bridge] 注入上下文失败: %s", e)

    def _websearch_key(self) -> str:
        try:
            cfg = self.context.get_config()
            keys = cfg.get("provider_settings", {}).get("websearch_deepseek_key") or []
            return str(keys[0]) if keys else ""
        except Exception:
            return ""

    async def _search_web(self, query: str, key: str, timeout: float = 6.0) -> str:
        try:
            import aiohttp

            tm = aiohttp.ClientTimeout(total=timeout)
            async with aiohttp.ClientSession(timeout=tm) as sess:
                async with sess.post(
                    "https://api.deepseek.com/v1/tools/search",
                    json={"query": query[:100], "max_results": 3},
                    headers={
                        "Authorization": f"Bearer {key}",
                        "Content-Type": "application/json",
                    },
                ) as resp:
                    if resp.status != 200:
                        logger.warning("[face_bridge] 检索 HTTP %s", resp.status)
                        return ""
                    data = await resp.json()
        except Exception as e:
            logger.warning("[face_bridge] 检索失败: %s", e)
            return ""
        lines = []
        for it in (data.get("results") or data.get("data") or [])[:3]:
            if not isinstance(it, dict):
                continue
            title = str(it.get("title") or it.get("name") or "").strip()
            body = re.sub(r"\s+", " ", str(it.get("content") or it.get("snippet") or ""))[:200]
            if title or body:
                lines.append(f"- {title}：{body}")
        return "\n".join(lines)

    # ------------------------------------------------------------------ 工具

    @llm_tool(name="send_media")
    async def send_media_tool(self, event: AstrMessageEvent, want: str = "") -> str:
        """用户想看某张图时调用它，把图直接发出去。

        用户说"发我看看""把刚才那张发我""那天的壁纸呢"这类话时都调本工具，
        不要用文字回答"发了"，直接调。

        Args:
            want(string): 用户想要的那张图长什么样，用他自己的说法填，比如"刚才那张""傲娇脸""昨天的壁纸""照片"；没线索就留空

        Returns:
            JSON，说明发了哪一张。
        """
        result = await self._deliver_best(event, str(want or "").strip(), reason="tool")
        return json.dumps(result, ensure_ascii=False)

    # ------------------------------------------------------------------ 规则兜底

    @filter.regex(SEND_PATTERN)
    async def regex_send(self, event: AstrMessageEvent):
        """模型没调工具时的兜底：明确的索图话立刻响应。"""
        text = event.get_message_str() or ""
        if len(text) > 40 or not SEND_RE.search(text):
            return
        if PHOTO_SAVE_RE.search(text):
            return
        result = await self._deliver_best(event, text, reason="regex")
        if result.get("ok"):
            event.stop_event()

    @filter.command("发图")
    async def cmd_send(self, event: AstrMessageEvent):
        """发最新收进库的那张。"""
        result = await self._deliver_best(event, "", reason="command")
        if not result.get("ok"):
            await event.send(MessageChain([Plain("库里还没有图呢")]))
        event.stop_event()

    @filter.command("照片")
    async def cmd_photos(self, event: AstrMessageEvent):
        """看看相册里有哪些照片。"""
        items = sorted(
            self.by_kind("photo"), key=lambda r: r.get("added", 0), reverse=True
        )[:10]
        if not items:
            await event.send(MessageChain([Plain("相册还是空的呢")]))
        else:
            lines = [
                f"{r.get('day', '')}  {r['id']}  {r.get('w', 0)}x{r.get('h', 0)}"
                for r in items
            ]
            await event.send(
                MessageChain([Plain(f"相册里最近 {len(items)} 张：\n" + "\n".join(lines))])
            )
        event.stop_event()

    @filter.command("存照片")
    async def cmd_save_photo(self, event: AstrMessageEvent):
        """把这条消息里的图存进相册。"""
        images = [c for c in event.get_messages() if isinstance(c, Image)]
        if not images:
            await event.send(MessageChain([Plain("你得先把照片发给我呀")]))
            event.stop_event()
            return
        saved = 0
        for img in images:
            try:
                if await self._ingest(event, img, force_kind="photo"):
                    saved += 1
            except Exception as e:
                logger.error("[face_bridge] 存照片失败: %s", e)
        await event.send(
            MessageChain([Plain(f"存好了，{saved} 张" if saved else "没存上，再看下？")])
        )
        event.stop_event()
