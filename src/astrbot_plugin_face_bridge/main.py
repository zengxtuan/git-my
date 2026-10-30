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
        self.max_bytes = int(self.config.get("max_send_bytes", 5242880) or 5242880)
        logger.info(
            "[face_bridge] loaded: %d faces cached, dir=%s",
            len(self.index),
            self.img_dir,
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

    async def _store_image(self, event: AstrMessageEvent, img: Image) -> dict | None:
        """把消息里的 Image 落地成文件并登记。"""
        src = Path(await img.convert_to_file_path())
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
        for img in images:
            try:
                rec = await self._store_image(event, img)
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
            chain.append(Plain("……我这边刚卡了一下，你再说一遍？"))
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
        return True

    @filter.command("发图")
    async def cmd_send_face(self, event: AstrMessageEvent):
        """直接发最新收进库的那张表情。"""
        await self._send_latest(event, reason="command")
        event.stop_event()

    @filter.regex(r"(发我|发给我|发过来|发回来|发一下|发来看看|发我看看|那张|这张)")
    async def regex_send_face(self, event: AstrMessageEvent):
        text = event.get_message_str() or ""
        if len(text) > 40:
            return
        if not any(k in text for k in ("图", "表情", "那张", "这张")):
            return
        ok = await self._send_latest(event, reason="regex")
        if ok:
            event.stop_event()

    # ------------------------------------------------------------------ tools

    @llm_tool(name="save_face")
    async def save_face_tool(self, event: AstrMessageEvent) -> str:
        """把本条消息里的图片存进 QQ 收藏表情。

        用户发来一张图并希望“收下/存起来/以后用”时调用。
        本条消息没有图片时直接返回失败说明。

        Returns:
            结果说明，含表情 ID。
        """
        images = [c for c in event.get_messages() if isinstance(c, Image)]
        if not images:
            return json.dumps(
                {"ok": False, "reason": "这条消息里没有图片"}, ensure_ascii=False
            )
        out = []
        for img in images:
            rec = await self._store_image(event, img)
            if rec is None:
                continue
            if not rec.get("in_qq_face"):
                ok, info = await self._add_to_qq_face(event, Path(rec["path"]))
                rec["in_qq_face"] = ok
                rec["qq_result"] = info
                self._save_index()
            out.append(
                {
                    "id": rec["id"],
                    "saved_to_qq_face": rec.get("in_qq_face", False),
                }
            )
        return json.dumps(
            {"ok": bool(out), "results": out}, ensure_ascii=False
        )

    @llm_tool(name="send_face")
    async def send_face_tool(self, event: AstrMessageEvent, face_id: str = "") -> str:
        """把表情库里的一张图直接发出去。

        想发表情时调用一次即可。不填 ID 就发最近存进来的那一张，
        所以用户说“把你刚收的那张发我”时直接调用、不用先查列表。

        Args:
            face_id(string): 表情 ID，可留空；留空表示发最新的那一张

        Returns:
            发送结果说明。
        """
        face_id = str(face_id or "").strip().strip("`")
        logger.info("[face_bridge] LLM 调用 send_face, face_id=%r", face_id)
        if not face_id or face_id.lower() in ("latest", "new", "recent", "0"):
            latest = sorted(
                self.index.values(), key=lambda r: r.get("added", 0), reverse=True
            )
            if not latest:
                return json.dumps(
                    {"ok": False, "reason": "表情库是空的"}, ensure_ascii=False
                )
            rec = latest[0]
        else:
            rec = self.index.get(face_id)
            if not rec:
                hits = [k for k in self.index if k.startswith(face_id)][:3]
                if len(hits) == 1:
                    rec = self.index[hits[0]]
                else:
                    return json.dumps(
                        {"ok": False, "reason": f"没有 ID 为 {face_id} 的表情"},
                        ensure_ascii=False,
                    )
        path = Path(rec["path"])
        if not path.exists():
            return json.dumps(
                {"ok": False, "reason": "文件已丢失"}, ensure_ascii=False
            )
        await event.send(MessageChain([Image.fromFileSystem(path)]))
        return json.dumps({"ok": True, "sent": rec["id"]}, ensure_ascii=False)

    @llm_tool(name="list_faces")
    async def list_faces_tool(self, event: AstrMessageEvent, limit: int = 12) -> str:
        """列出表情库里最新的若干张，返回 ID 列表。

        不知道有什么图、或用户问“你有哪些表情”时调用。

        Args:
            limit(number): 返回条数，最多 30

        Returns:
            JSON，含 ID、大小、是否已进 QQ 收藏。
        """
        try:
            n = max(1, min(int(limit), 30))
        except Exception:
            n = 12
        items = sorted(
            self.index.values(), key=lambda r: r.get("added", 0), reverse=True
        )[:n]
        return json.dumps(
            {
                "total": len(self.index),
                "items": [
                    {
                        "id": r["id"],
                        "size": r.get("size", 0),
                        "in_qq_face": r.get("in_qq_face", False),
                        "caption": r.get("caption", ""),
                    }
                    for r in items
                ],
            },
            ensure_ascii=False,
        )
