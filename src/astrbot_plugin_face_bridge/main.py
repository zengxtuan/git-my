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
import shutil
import time
from pathlib import Path

from astrbot.api import AstrBotConfig, llm_tool, logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.message_components import Image
from astrbot.api.star import Context, Star, StarTools

PLUGIN_NAME = "astrbot_plugin_face_bridge"
EXT_WHITELIST = {".gif", ".jpg", ".jpeg", ".png", ".webp", ".bmp"}


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
    async def send_face_tool(self, event: AstrMessageEvent, face_id: str) -> str:
        """按表情 ID 直接把那张图发出去。

        ID 来自 list_faces 或 save_face 的返回，不要自己编。
        想发图时先 list_faces 拿 ID，再调用本工具。

        Args:
            face_id(string): 表情 ID，形如 3f2a9c1b7d4e

        Returns:
            发送结果说明。
        """
        face_id = str(face_id or "").strip().strip("`")
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
