"""face_bridge —— 让「大肥鱼」真的能把图存进 QQ 收藏表情、并按 ID 发图。

两条链路：

1. 存档：收到带图的消息 → 落地成本地文件 → 调用 NapCat 的
   ``add_custom_face``（file=本地路径, is_origin=True）把图塞进 QQ 收藏表情。
   不阻断事件，正常聊天不受影响。

2. 取用：给模型三个 LLM 工具，绕开语义检索（embedding 不可用时也能用）：
   ``list_faces`` / ``send_face`` / ``save_face``。
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
EXT_WHITELIST = {".gif", ".jpg", ".jpeg", ".png", ".webp", ".bmp"}

# deepseek-flash 会把内部工具标记当正文吐出来，竖线是全角 U+FF5C，不是 ASCII 竖线。
# 样例：
#   <｜｜DSML｜｜ calls>
#   <｜｜DSML｜｜ invoke name="web_search_deepseek">
#   <｜｜DSML｜｜ parameter name="query" string="true">...</｜｜DSML｜｜ parameter>
#   </｜｜DSML｜｜ invoke>
#   </｜｜DSML｜｜ calls>
DSML_RE = re.compile(
    r"<[|｜]{2}\s*DSML\s*[|｜]{2}[\s\S]*?(?:</[|｜]{2}\s*DSML\s*[|｜]{2}\s*calls>|\Z)"
)
DSML_LOOSE_RE = re.compile(r"<[|｜]{2}\s*DSML\s*[|｜]{2}[^\n]*")

# 整条回复都被 DSML 污染时替换用的兜底话，别连着几次都用同一句
DSML_FALLBACKS = (
    "嗯……刚走神了，你再说一遍",
    "诶，我这边没接住，再说一次",
    "等下，刚才脑子短路了，重说",
    "我好像跑偏了，你再说一遍",
)

# 用户明确要语音才合成：TTS 的 trigger_probability 已设为 0，主动发语音一律不发。
VOICE_REQUEST_WORDS = (
    "语音",
    "声音",
    "念给我听",
    "念给我",
    "说句话",
    "说话给我听",
    "读给我听",
)

# 模型明明有联网能力却不主动用（实测日志里 web_search 只在 DSML 那次出现过）。
# 所以遇到"问梗/问词"的消息，插件直接先搜一次，把资料塞进本轮请求。
LOOKUP_HINT_RE = re.compile(
    r"(梗|什么意思|啥意思|这是啥|怎么理解|什么鬼|啥啊|啥梗|啥玩意|啥东西|什么梗)"
)
ABBR_RE = re.compile(r"^[a-zA-Z]{3,12}$")

# 照片存档：大图不当表情处理，单独归到 photos/，能按日期翻回来再发
PHOTO_DIR_NAME = "photos"
PHOTO_INDEX_NAME = "photos.json"
PHOTO_SAVE_RE = re.compile(
    r"(?:(?:存|保存|留着|收着|收藏).{0,8}(?:这张|这图|照片|我发))"
    r"|(?:(?:这张|这图|这张图|这张照片|照片).{0,6}(?:存下来|保存|留着|收着))"
)
# 用户说到这些词，就是指照片/壁纸，而不是表情包
PHOTO_WORD_RE = re.compile(r"(照片|壁纸|截图|相片|大图|原图)")
# "发我看看"这类索图请求（装饰器和函数体共用，函数里再校验一次更稳）
SEND_REQUEST_RE = re.compile(
    r"(发我|发给我|发过来|发回来|发一下|发来看看|发我看看|那张|这张|壁纸)"
)


def strip_dsml(text: str) -> tuple[str, int]:
    """剥掉 DSML 标记块，返回 (清理后文本, 删除的字符数)。"""
    if not text or "DSML" not in text:
        return text, 0
    cleaned, n = DSML_RE.subn("", text)
    if n == 0:
        # 结构不完整（被截断）时按行兜底
        cleaned, n2 = DSML_LOOSE_RE.subn("", cleaned)
        n = n2
    cleaned = cleaned.strip()
    return cleaned, len(text) - len(cleaned)


def _md5_of(path: Path) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _image_size(path: Path) -> tuple[int, int]:
    """尽力取图片宽高，取不到返回 (0, 0)。"""
    try:
        from PIL import Image as PILImage

        with PILImage.open(path) as im:
            return int(im.width), int(im.height)
    except Exception:
        return 0, 0


def looks_like_meme(ext: str, size: int, w: int, h: int) -> bool:
    """判断收到的图该走表情链路还是照片链路。

    GIF 一律当表情；边长不超过 512 且体积不大（<=300KB）也当表情；
    其余（大图、长边超 512、体积大）当照片处理，不往 QQ 收藏表情里塞。
    """
    if ext == ".gif":
        return True
    if w and h and max(w, h) > 512:
        return False
    if size > 300 * 1024:
        return False
    return True


class FaceBridge(Star):
    """大肥鱼的表情桥：收图入库 + 按 ID 发图。"""

    def __init__(self, context: Context, config: AstrBotConfig | None = None) -> None:
        super().__init__(context, config)
        self.config = config or {}
        try:
            self.root = Path(StarTools.get_data_dir(PLUGIN_NAME))
        except Exception:  # 兜底：StarTools 未初始化时按约定路径
            self.root = Path("data/plugin_data") / PLUGIN_NAME
        self.img_dir = self.root / "images"
        self.img_dir.mkdir(parents=True, exist_ok=True)
        self.index_path = self.root / "index.json"
        self.index: dict[str, dict] = self._load_index()

        self.photo_dir = self.root / PHOTO_DIR_NAME
        self.photo_dir.mkdir(parents=True, exist_ok=True)
        self.photo_index_path = self.root / PHOTO_INDEX_NAME
        self.photos: dict[str, dict] = self._load_photos()
        # 刚刚在聊的那张图（含糊的"发我看看"就发它，而不是一律发最新照片）
        self.recent_media: dict | None = None

        self.max_bytes = int(self.config.get("max_send_bytes", 5242880) or 5242880)
        # 照片/壁纸往往是几 MB 的大图，单独给一个更宽松的上限
        self.photo_max_bytes = int(
            self.config.get("photo_max_bytes", 20971520) or 20971520
        )
        logger.info(
            "[face_bridge] loaded: %d faces / %d photos cached, dir=%s",
            len(self.index),
            len(self.photos),
            self.root,
        )

    # ------------------------------------------------------------------ storage

    def _load_index(self) -> dict[str, dict]:
        if not self.index_path.exists():
            return {}
        try:
            data = json.loads(self.index_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception as e:
            logger.error("[face_bridge] index.json 读取失败: %s", e)
            return {}

    def _save_index(self) -> None:
        try:
            self.index_path.write_text(
                json.dumps(self.index, ensure_ascii=False, indent=1),
                encoding="utf-8",
            )
        except Exception as e:
            logger.error("[face_bridge] index.json 写入失败: %s", e)

    def _load_photos(self) -> dict[str, dict]:
        if not self.photo_index_path.exists():
            return {}
        try:
            data = json.loads(self.photo_index_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception as e:
            logger.error("[face_bridge] photos.json 读取失败: %s", e)
            return {}

    def _save_photos(self) -> None:
        try:
            self.photo_index_path.write_text(
                json.dumps(self.photos, ensure_ascii=False, indent=1),
                encoding="utf-8",
            )
        except Exception as e:
            logger.error("[face_bridge] photos.json 写入失败: %s", e)

    async def _store_photo(
        self, event: AstrMessageEvent, img: Image, note: str = "", src: Path | None = None
    ) -> dict | None:
        """把消息里的图存进照片相册（不进 QQ 收藏表情）。"""
        try:
            src = src or Path(await img.convert_to_file_path())
        except Exception as e:
            logger.error("[face_bridge] 照片落地失败: %s", e)
            return None
        if not src.exists():
            return None
        size = src.stat().st_size
        if size > self.photo_max_bytes:
            logger.warning("[face_bridge] 照片过大跳过: %d bytes", size)
            return None
        ext = src.suffix.lower()
        if ext not in EXT_WHITELIST:
            ext = ".jpg"
        md5 = _md5_of(src)
        pid = md5[:12]
        if pid in self.photos:
            rec = self.photos[pid]
            logger.info("[face_bridge] 照片已存在: %s", pid)
            return rec
        w, h = _image_size(src)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        dest = self.photo_dir / f"{stamp}_{pid}{ext}"
        try:
            if src.resolve() != dest.resolve():
                shutil.copyfile(src, dest)
        except Exception as e:
            logger.error("[face_bridge] 照片复制失败: %s", e)
            return None
        try:
            sender = event.get_sender_id()
        except Exception:
            sender = ""
        rec = {
            "id": pid,
            "path": str(dest),
            "md5": md5,
            "size": size,
            "w": w,
            "h": h,
            "added": int(time.time()),
            "day": time.strftime("%Y-%m-%d"),
            "sender": sender,
            "note": note,
        }
        self.photos[pid] = rec
        self._save_photos()
        self.recent_media = {"id": pid, "path": str(dest), "kind": "photo"}
        logger.info(
            "[face_bridge] 照片已存 %s (%dx%d, %d bytes)", pid, w, h, size
        )
        return rec

    # --------------------------------------------------------------- qq action

    @staticmethod
    def _bot_of(event: AstrMessageEvent):
        bot = getattr(event, "bot", None)
        return bot if hasattr(bot, "call_action") else None

    async def _add_to_qq_face(self, event: AstrMessageEvent, path: Path) -> tuple[bool, str]:
        """把本地图片加进 QQ 收藏表情。"""
        bot = self._bot_of(event)
        if bot is None:
            return False, "当前平台无 call_action（非 aiocqhttp）"
        try:
            ret = await bot.call_action(
                "add_custom_face",
                file=str(path),
                is_origin=True,
            )
            logger.info("[face_bridge] add_custom_face ok: %s -> %s", path.name, ret)
            return True, str(ret)[:200]
        except Exception as e:
            logger.error("[face_bridge] add_custom_face 失败 %s: %s", path.name, e)
            return False, str(e)[:200]

    async def _store_image(
        self, event: AstrMessageEvent, img: Image, src: Path | None = None
    ) -> dict | None:
        """把消息里的 Image 落地成文件并登记。"""
        src = src or Path(await img.convert_to_file_path())
        if not src.exists():
            logger.warning("[face_bridge] 图片落地失败: %s", src)
            return None
        ext = src.suffix.lower()
        if ext not in EXT_WHITELIST:
            ext = ".gif" if b"GIF8" in src.read_bytes()[:8] else ".jpg"
        size = src.stat().st_size
        if size > self.max_bytes:
            logger.warning("[face_bridge] 图片过大跳过: %s (%d bytes)", src, size)
            return None
        md5 = _md5_of(src)
        face_id = md5[:12]
        if face_id in self.index:
            rec = self.index[face_id]
            logger.info("[face_bridge] 已存在，跳过入库: %s", face_id)
            return rec
        dest = self.img_dir / f"{face_id}{ext}"
        try:
            if src.resolve() != dest.resolve():
                shutil.copyfile(src, dest)
        except Exception as e:
            logger.error("[face_bridge] 复制失败 %s: %s", src, e)
            return None
        rec = {
            "id": face_id,
            "path": str(dest),
            "md5": md5,
            "size": size,
            "added": int(time.time()),
            "caption": "",
            "source": "user",
            "in_qq_face": False,
        }
        self.index[face_id] = rec
        self._save_index()
        self.recent_media = {"id": face_id, "path": str(dest), "kind": "face"}
        return rec

    # ------------------------------------------------------------------ hooks

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_any_message(self, event: AstrMessageEvent):
        """收到带图消息时静默存档，不打断正常对话。"""
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
        force_photo = bool(PHOTO_SAVE_RE.search(text))
        for img in images:
            try:
                try:
                    path = Path(await img.convert_to_file_path())
                except Exception:
                    path = None
                if force_photo:
                    await self._store_photo(event, img, src=path)
                    continue
                if path is not None and path.exists():
                    ext = path.suffix.lower()
                    size = path.stat().st_size
                    w, h = _image_size(path) if ext != ".gif" else (0, 0)
                    if not looks_like_meme(ext, size, w, h):
                        await self._store_photo(event, img, src=path)
                        continue
                rec = await self._store_image(event, img, src=path)
                if rec is None:
                    continue
                if rec.get("in_qq_face"):
                    continue
                ok, info = await self._add_to_qq_face(event, Path(rec["path"]))
                rec["in_qq_face"] = ok
                rec["qq_result"] = info
                self._save_index()
            except Exception as e:
                logger.error("[face_bridge] 存档异常: %s", e, exc_info=True)

    @filter.on_decorating_result()
    async def strip_dsml_leak(self, event: AstrMessageEvent):
        """发送前剥掉 DSML 泄漏文本，别让用户看到模型内部标记。"""
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
            logger.warning("[face_bridge] 整条回复都是 DSML，已替换为兜底文本")
        else:
            logger.warning("[face_bridge] 已剥掉 DSML 泄漏文本 %d 字符", removed)

    @filter.on_decorating_result(priority=50)
    async def voice_on_demand(self, event: AstrMessageEvent):
        """用户明确要语音时才合成一条语音附在回复上（主动发语音已全局关闭）。"""
        text = ""
        try:
            text = event.get_message_str() or ""
        except Exception:
            return
        if not any(w in text for w in VOICE_REQUEST_WORDS):
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
                logger.warning("[face_bridge] 要语音但当前没有可用的 TTS provider")
                return
            audio = await provider.get_audio(spoken[:200])
            if not audio:
                logger.warning("[face_bridge] TTS 合成返回空")
                return
            chain.append(Record(file=audio))
            logger.info("[face_bridge] 按需语音已附加: %s", audio)
        except Exception as e:
            logger.error("[face_bridge] 按需语音失败: %s", e, exc_info=True)

    @filter.on_llm_request(priority=3)
    async def inject_recent_media_hint(self, event: AstrMessageEvent, req):
        """把"刚才已经发过哪张图"告诉模型，免得它回头问"哪张啊"。"""
        rec = self.recent_media
        if not rec:
            return
        kind = "表情包" if rec.get("kind") == "face" else "照片"
        note = (
            f"【前提：你刚已经把那{kind}发出去了（编号 {rec.get('id')}），"
            "不要再问用户是哪张，也不要再重复发同一张，除非他明确要重发】"
        )
        parts = getattr(req, "extra_user_content_parts", None)
        try:
            if isinstance(parts, list):
                parts.append({"type": "text", "text": note})
            else:
                req.prompt = (req.prompt or "") + "\n\n" + note
        except Exception:
            return

    @filter.on_llm_request(priority=2)
    async def auto_lookup(self, event: AstrMessageEvent, req):
        """问梗/问生词的短消息，先联网查一次再把资料塞进本轮请求。"""
        text = ""
        try:
            text = (event.get_message_str() or "").strip()
        except Exception:
            return
        if not text or len(text) > 60:
            return
        if not (LOOKUP_HINT_RE.search(text) or ABBR_RE.fullmatch(text)):
            return
        key = self._websearch_key()
        if not key:
            return
        summary = await self._search_web(text, key)
        if not summary:
            return
        note = (
            "【刚联网查到的资料，别照抄原话，用你自己的口气讲】：\n" + summary
        )
        parts = getattr(req, "extra_user_content_parts", None)
        try:
            if isinstance(parts, list):
                parts.append({"type": "text", "text": note})
            else:
                req.prompt = (req.prompt or "") + "\n\n" + note
        except Exception as e:
            logger.warning("[face_bridge] 注入检索结果失败: %s", e)
            return
        logger.info("[face_bridge] 梗检索命中 %r -> %d 字", text[:20], len(summary))

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

            headers = {
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            }
            payload = {"query": query[:100], "max_results": 3}
            tm = aiohttp.ClientTimeout(total=timeout)
            async with aiohttp.ClientSession(timeout=tm) as sess:
                async with sess.post(
                    "https://api.deepseek.com/v1/tools/search",
                    json=payload,
                    headers=headers,
                ) as resp:
                    if resp.status != 200:
                        logger.warning(
                            "[face_bridge] 检索 HTTP %s", resp.status
                        )
                        return ""
                    data = await resp.json()
        except Exception as e:
            logger.warning("[face_bridge] 检索失败: %s", e)
            return ""
        items = data.get("results") or data.get("data") or []
        lines = []
        for it in items[:3]:
            if not isinstance(it, dict):
                continue
            title = str(it.get("title") or it.get("name") or "").strip()
            body = str(it.get("content") or it.get("snippet") or "").strip()
            body = re.sub(r"\s+", " ", body)[:200]
            if title or body:
                lines.append(f"- {title}：{body}")
        return "\n".join(lines)

    @filter.on_llm_request(priority=1)
    async def debug_toolset(self, event: AstrMessageEvent, req):
        """确认 send_face 是否随请求下发（排障用，轻日志）。"""
        try:
            ts = getattr(req, "func_tool", None)
            get_tool = getattr(ts, "get_tool", None)
            has = bool(callable(get_tool) and get_tool("send_face"))
            total = len(getattr(ts, "tools", []) or [])
            logger.info(
                "[face_bridge] req toolset: send_face=%s, total=%s", has, total
            )
        except Exception as e:
            logger.warning("[face_bridge] toolset probe failed: %s", e)

    # ---------------------------------------------------------------- rule path
    # 模型不一定会调工具，所以"把那张发我"这类明确请求走确定性规则，不赌模型。

    async def _send_latest(self, event: AstrMessageEvent, reason: str) -> bool:
        latest = sorted(
            self.index.values(), key=lambda r: r.get("added", 0), reverse=True
        )
        if not latest:
            await event.send(MessageChain([Plain("库里还没有图呢")]))
            return False
        rec = latest[0]
        path = Path(rec["path"])
        if not path.exists():
            logger.warning("[face_bridge] 规则触发但文件丢失: %s", rec["id"])
            return False
        logger.info("[face_bridge] 规则命中(%s) -> 发送最新图 %s", reason, rec["id"])
        await event.send(MessageChain([Image.fromFileSystem(path)]))
        self.recent_media = {"id": rec["id"], "path": str(path), "kind": "face"}
        return True

    @filter.command("发图")
    async def cmd_send_face(self, event: AstrMessageEvent):
        """直接发最新收进库的那张表情。"""
        await self._send_latest(event, reason="command")
        event.stop_event()

    # ------------------------------------------------------------- photo album

    async def _send_photo(self, event: AstrMessageEvent, photo_id: str = "") -> dict:
        photo_id = str(photo_id or "").strip().strip("`")
        if not photo_id or photo_id.lower() in ("latest", "new", "recent", "0"):
            ordered = sorted(
                self.photos.values(), key=lambda r: r.get("added", 0), reverse=True
            )
            if not ordered:
                return {"ok": False, "reason": "相册里还没有照片"}
            rec = ordered[0]
        else:
            rec = self.photos.get(photo_id)
            if not rec:
                hits = [k for k in self.photos if k.startswith(photo_id)][:3]
                if len(hits) == 1:
                    rec = self.photos[hits[0]]
                else:
                    return {"ok": False, "reason": f"没有编号 {photo_id} 的照片"}
        path = Path(rec["path"])
        if not path.exists():
            return {"ok": False, "reason": "照片文件丢了"}
        await event.send(MessageChain([Image.fromFileSystem(path)]))
        self.recent_media = {"id": rec["id"], "path": str(path), "kind": "photo"}
        logger.info("[face_bridge] 发照片 %s (%s)", rec["id"], rec.get("day", ""))
        return {"ok": True, "sent": rec["id"], "day": rec.get("day", "")}

    @filter.command("照片")
    async def cmd_list_photos(self, event: AstrMessageEvent):
        """看看相册里存了哪些照片。"""
        items = sorted(
            self.photos.values(), key=lambda r: r.get("added", 0), reverse=True
        )[:10]
        if not items:
            await event.send(MessageChain([Plain("相册还是空的呢")]))
        else:
            lines = [
                f"{r.get('day', '')}  {r['id']}  {r.get('w', 0)}x{r.get('h', 0)}"
                for r in items
            ]
            await event.send(
                MessageChain(
                    [Plain(f"相册里最近 {len(items)} 张：\n" + "\n".join(lines))]
                )
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
                if await self._store_photo(event, img):
                    saved += 1
            except Exception as e:
                logger.error("[face_bridge] 存照片失败: %s", e)
        await event.send(
            MessageChain([Plain(f"存好了，{saved} 张" if saved else "没存上，再看下？")])
        )
        event.stop_event()

    @filter.regex(r"(发我|发给我|发过来|发回来|发一下|发来看看|发我看看|那张|这张|壁纸)")
    async def regex_send_face(self, event: AstrMessageEvent):
        text = event.get_message_str() or ""
        if len(text) > 40:
            return
        if not SEND_REQUEST_RE.search(text):
            return
        if PHOTO_SAVE_RE.search(text):  # "把这张存下来"不该触发发图
            return
        asks_photo = bool(PHOTO_WORD_RE.search(text))
        asks_face = "表情" in text

        # "发我看看""发我啊"这种含糊请求，发刚刚在聊的那张，别一律发最新照片
        if not asks_photo and not asks_face and self.recent_media:
            rec = self.recent_media
            path = Path(rec.get("path", ""))
            if path.exists():
                await event.send(MessageChain([Image.fromFileSystem(path)]))
                logger.info(
                    "[face_bridge] 含糊请求 -> 发刚才那张 %s (%s)",
                    rec.get("id"),
                    rec.get("kind"),
                )
                event.stop_event()
                return

        if asks_photo:
            res = await self._send_photo(event, "")
            if res.get("ok"):
                event.stop_event()
            return
        if not asks_face and not any(k in text for k in ("图", "那张", "这张")):
            return
        ok = await self._send_latest(event, reason="regex")
        if ok:
            event.stop_event()

