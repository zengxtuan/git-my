"""face_bridge 插件离线单元测试（不需要 AstrBot 运行环境）。

注入 astrbot.* 桩模块后用 importlib 直接加载插件 main.py。
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path

PLUGIN_MAIN = (
    Path(__file__).resolve().parents[2] / "src" / "astrbot_plugin_face_bridge" / "main.py"
)


def _install_stubs(data_dir: Path):
    astrbot = types.ModuleType("astrbot")
    api = types.ModuleType("astrbot.api")

    class AstrBotConfig(dict):
        pass

    def llm_tool(name=None, **_kw):
        def deco(fn):
            fn._llm_tool_name = name or fn.__name__
            return fn

        return deco

    class _Logger:
        def info(self, *a, **k):
            pass

        warning = error = debug = info
        exception = info

    api.AstrBotConfig = AstrBotConfig
    api.llm_tool = llm_tool
    api.logger = _Logger()

    event_mod = types.ModuleType("astrbot.api.event")
    message = types.ModuleType("astrbot.api.message_components")
    star_mod = types.ModuleType("astrbot.api.star")

    class AstrMessageEvent:
        pass

    class MessageChain(list):
        def __init__(self, chain=None):
            super().__init__(chain or [])

    class Image:
        def __init__(self, path=None):
            self._path = Path(path) if path else None

        @staticmethod
        def fromFileSystem(path):
            return Image(path)

    class Plain:
        def __init__(self, text=""):
            self.text = text

    class Record:
        def __init__(self, file=""):
            self.file = file

    class Face:
        def __init__(self, id=None, **_):
            self.id = id

    class Star:
        def __init__(self, context, config=None):
            self.context = context

    class Context:
        pass

    class StarTools:
        @classmethod
        def get_data_dir(cls, name=None):
            return data_dir

    class _Filter:
        @staticmethod
        def event_message_type(*_a, **_k):
            def deco(fn):
                fn._handler = "event_message_type"
                return fn

            return deco

        @staticmethod
        def on_llm_request(*_a, **_k):
            def deco(fn):
                fn._handler = "on_llm_request"
                return fn

            return deco

        @staticmethod
        def command(*_a, **_k):
            def deco(fn):
                fn._handler = "command"
                return fn

            return deco

        @staticmethod
        def regex(*_a, **_k):
            def deco(fn):
                fn._handler = "regex"
                return fn

            return deco

        @staticmethod
        def on_decorating_result(*_a, **_k):
            def deco(fn):
                fn._handler = "on_decorating_result"
                return fn

            return deco

        class EventMessageType:
            ALL = 7

    event_mod.AstrMessageEvent = AstrMessageEvent
    event_mod.MessageChain = MessageChain
    event_mod.filter = _Filter
    message.Image = Image
    message.Plain = Plain
    message.Record = Record
    message.Face = Face
    star_mod.Context = Context
    star_mod.Star = Star
    star_mod.StarTools = StarTools

    astrbot.api = api
    sys.modules["astrbot"] = astrbot
    sys.modules["astrbot.api"] = api
    sys.modules["astrbot.api.event"] = event_mod
    sys.modules["astrbot.api.message_components"] = message
    sys.modules["astrbot.api.star"] = star_mod
    return AstrBotConfig


def _load_module():
    spec = importlib.util.spec_from_file_location("face_bridge_main", PLUGIN_MAIN)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FakeBot:
    def __init__(self):
        self.calls = []

    async def call_action(self, action, **kwargs):
        self.calls.append((action, kwargs))
        return {"result": 0, "errMsg": "", "isExist": 0}


class FakeEvent:
    def __init__(self, images=(), bot=None, text="", reply=""):
        self._images = list(images)
        self.bot = bot or FakeBot()
        self.sent = []
        self._text = text
        self.stopped = False
        self.unified_msg_origin = "qq:FriendMessage:1"
        self._result = None
        if reply:
            PlainCls = sys.modules["astrbot.api.message_components"].Plain
            self._result = types.SimpleNamespace(chain=[PlainCls(reply)])
    def get_messages(self):
        return self._images

    def get_message_str(self):
        return self._text

    def get_sender_id(self):
        return "3588255101"

    def get_result(self):
        return self._result

    def stop_event(self):
        self.stopped = True

    async def send(self, chain):
        self.sent.append(chain)


class FaceBridgeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.data_dir = Path(cls.tmp.name) / "plugin_data"
        cls.data_dir.mkdir(parents=True, exist_ok=True)
        cls.config_cls = _install_stubs(cls.data_dir)
        cls.mod = _load_module()

        base_image = sys.modules["astrbot.api.message_components"].Image

        class FakeImage(base_image):
            def __init__(self, path):
                self._path = Path(path)

            async def convert_to_file_path(self):
                return str(self._path)

        cls.FakeImage = FakeImage

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        for p in sorted(self.data_dir.rglob("*"), reverse=True):
            if p.is_file():
                p.unlink()
            elif p.is_dir():
                p.rmdir()
        self.data_dir.mkdir(parents=True, exist_ok=True)

    # ---------------------------------------------------------------- helpers

    def _file(self, name: str, payload: bytes) -> Path:
        p = Path(self.tmp.name) / name
        p.write_bytes(payload)
        return p

    def _gif(self, name="a.gif", payload=b"GIF89a" + b"x" * 32) -> Path:
        return self._file(name, payload)

    def _jpg(self, name="a.jpg", size=6000) -> Path:
        return self._file(name, b"\xff\xd8\xff" + b"j" * size)

    # ------------------------------------------------------------------ 纯函数

    def test_md5_of(self):
        p = self._gif("md5.gif", b"GIF89a-hello")
        self.assertEqual(self.mod.md5_of(p), hashlib.md5(b"GIF89a-hello").hexdigest())

    def test_looks_like_meme(self):
        f = self.mod.looks_like_meme
        self.assertTrue(f(".gif", 500_000, 0, 0))
        self.assertTrue(f(".jpg", 50_000, 200, 200))
        self.assertFalse(f(".jpg", 900_000, 3000, 2000))
        self.assertFalse(f(".jpg", 400_000, 200, 200))
        self.assertFalse(f(".jpg", 90_000, 1200, 900))

    def test_strip_dsml_full_block(self):
        raw = (
            "<｜｜DSML｜｜ calls>\n"
            '<｜｜DSML｜｜ invoke name="web_search_deepseek">\n'
            '<｜｜DSML｜｜ parameter name="query" string="true">深圳天气</｜｜DSML｜｜ parameter>\n'
            "</｜｜DSML｜｜ invoke>\n"
            "</｜｜DSML｜｜ calls>"
        )
        cleaned, cut = self.mod.strip_dsml(raw)
        self.assertEqual(cleaned, "")
        self.assertGreater(cut, 0)

    def test_strip_dsml_truncated(self):
        raw = '随便说一句\n<｜｜DSML｜｜ invoke name="search_memes">\n<｜｜DSML｜｜ parameter name="q"'
        cleaned, _ = self.mod.strip_dsml(raw)
        self.assertNotIn("DSML", cleaned)
        self.assertTrue(cleaned.startswith("随便说一句"))

    def test_strip_dsml_plain_untouched(self):
        raw = "今天好累啊，早点睡"
        self.assertEqual(self.mod.strip_dsml(raw), (raw, 0))

    def test_strip_dsml_ascii_pipes(self):
        raw = '<||DSML||> calls>\n<||DSML||> invoke name="x">'
        cleaned, _ = self.mod.strip_dsml(raw)
        self.assertNotIn("DSML", cleaned)

    def test_regex_patterns(self):
        send = self.mod.SEND_RE
        self.assertTrue(send.search("发这个给我"))
        self.assertTrue(send.search("我让你发这个表情包给我"))
        self.assertTrue(send.search("发我看看啊"))
        self.assertTrue(send.search("把这个还我"))
        self.assertFalse(send.search("今天好累啊"))
        self.assertFalse(send.search("这个不错吧"))

        save = self.mod.PHOTO_SAVE_RE
        self.assertTrue(save.search("把这张存下来"))
        self.assertTrue(save.search("帮我保存这张照片"))
        self.assertFalse(save.search("把刚才那张表情包发我看看"))

        self.assertTrue(self.mod.PHOTO_WORD_RE.search("把刚才那张壁纸发我"))
        self.assertTrue(self.mod.LOOKUP_RE.search("班味是什么梗"))
        self.assertTrue(self.mod.ABBR_RE.fullmatch("kskbl"))

    # ------------------------------------------------------------------ 入库

    def test_ingest_face_and_qq_action(self):
        import asyncio

        plugin = self.mod.FaceBridge(None, {})
        ev = FakeEvent()
        src = self._gif("f.gif", b"GIF89a-f" + b"f" * 20)
        rec = asyncio.run(plugin._ingest(ev, self.FakeImage(src)))
        self.assertIsNotNone(rec)
        self.assertEqual(rec["kind"], "face")
        self.assertTrue(Path(rec["path"]).exists())

        ok, _ = asyncio.run(plugin._add_to_qq_face(ev, Path(rec["path"])))
        self.assertTrue(ok)
        self.assertEqual(ev.bot.calls[0][0], "add_custom_face")
        self.assertTrue(ev.bot.calls[0][1]["is_origin"])

    def test_ingest_photo_when_large(self):
        import asyncio

        plugin = self.mod.FaceBridge(None, {})
        src = self._jpg("big.jpg", 400_000)  # 400KB，判照片
        rec = asyncio.run(plugin._ingest(FakeEvent(), self.FakeImage(src)))
        self.assertEqual(rec["kind"], "photo")

    def test_ingest_dedupe(self):
        import asyncio

        plugin = self.mod.FaceBridge(None, {})
        src = self._gif("dup.gif", b"GIF89a-dup" + b"z" * 8)
        a = asyncio.run(plugin._ingest(FakeEvent(), self.FakeImage(src)))
        b = asyncio.run(plugin._ingest(FakeEvent(), self.FakeImage(src)))
        self.assertEqual(a["id"], b["id"])
        self.assertEqual(len(plugin.media), 1)

    def test_ingest_force_photo(self):
        import asyncio

        plugin = self.mod.FaceBridge(None, {})
        src = self._gif("as_photo.gif", b"GIF89a-p" + b"p" * 10)
        rec = asyncio.run(
            plugin._ingest(FakeEvent(), self.FakeImage(src), force_kind="photo")
        )
        self.assertEqual(rec["kind"], "photo")

    def test_auto_archive_hook_disabled(self):
        import asyncio

        plugin = self.mod.FaceBridge(None, {"auto_archive": False})
        ev = FakeEvent([self.FakeImage(self._gif("h.gif"))])
        asyncio.run(plugin.on_any_message(ev))
        self.assertEqual(ev.bot.calls, [])
        self.assertEqual(plugin.media, {})

    def test_auto_archive_hook_runs(self):
        import asyncio

        plugin = self.mod.FaceBridge(None, {"auto_archive": True})
        ev = FakeEvent([self.FakeImage(self._gif("h2.gif", b"GIF89a-h2" + b"m" * 6))])
        asyncio.run(plugin.on_any_message(ev))
        self.assertEqual(len(ev.bot.calls), 1)
        self.assertEqual(len(plugin.media), 1)

    # ------------------------------------------------------------------ 发送

    def test_deliver_best_prefers_recent(self):
        import asyncio

        plugin = self.mod.FaceBridge(None, {})
        photo = asyncio.run(
            plugin._ingest(FakeEvent(), self.FakeImage(self._jpg("ph.jpg", 400_000)))
        )
        face = asyncio.run(
            plugin._ingest(FakeEvent(), self.FakeImage(self._gif("now.gif", b"GIF89a-n" + b"n" * 9)))
        )
        ev = FakeEvent()
        res = asyncio.run(plugin._deliver_best(ev, "发我看看啊", reason="test"))
        self.assertTrue(res["ok"])
        self.assertEqual(res["sent"], face["id"])
        self.assertEqual(len(ev.sent), 1)

    def test_deliver_best_photo_wording(self):
        import asyncio

        plugin = self.mod.FaceBridge(None, {})
        photo = asyncio.run(
            plugin._ingest(FakeEvent(), self.FakeImage(self._jpg("wp.jpg", 500_000)))
        )
        ev = FakeEvent()
        res = asyncio.run(plugin._deliver_best(ev, "把刚才那张壁纸发我", reason="test"))
        self.assertTrue(res["ok"])
        self.assertEqual(res["sent"], photo["id"])

    def test_deliver_best_empty_library(self):
        import asyncio

        plugin = self.mod.FaceBridge(None, {})
        res = asyncio.run(plugin._deliver_best(FakeEvent(), "发我看看", reason="test"))
        self.assertFalse(res["ok"])

    def test_send_media_tool(self):
        import asyncio

        plugin = self.mod.FaceBridge(None, {})
        rec = asyncio.run(
            plugin._ingest(FakeEvent(), self.FakeImage(self._gif("t.gif", b"GIF89a-t" + b"t" * 7)))
        )
        ev = FakeEvent()
        out = json.loads(asyncio.run(plugin.send_media_tool(ev, "刚才那张")))
        self.assertTrue(out["ok"])
        self.assertEqual(out["sent"], rec["id"])

    def test_find_by_caption_then_prefix(self):
        import asyncio

        plugin = self.mod.FaceBridge(None, {})
        rec = asyncio.run(
            plugin._ingest(FakeEvent(), self.FakeImage(self._gif("c.gif", b"GIF89a-c" + b"c" * 5)))
        )
        rec["caption"] = "傲娇脸 蓝色小鱼"
        self.assertIsNotNone(plugin.find("傲娇脸"))
        self.assertIsNone(plugin.find("完全不相干"))
        self.assertIsNotNone(plugin.find(rec["id"]))

    # ------------------------------------------------------------------ 规则

    def test_regex_send_triggers_and_stops(self):
        import asyncio

        plugin = self.mod.FaceBridge(None, {})
        asyncio.run(
            plugin._ingest(FakeEvent(), self.FakeImage(self._gif("r.gif", b"GIF89a-r" + b"r" * 5)))
        )
        ev = FakeEvent(text="把刚才那张表情包发我看看")
        asyncio.run(plugin.regex_send(ev))
        self.assertEqual(len(ev.sent), 1)
        self.assertTrue(ev.stopped)

    def test_regex_send_ignores_unrelated(self):
        import asyncio

        plugin = self.mod.FaceBridge(None, {})
        asyncio.run(
            plugin._ingest(FakeEvent(), self.FakeImage(self._gif("r2.gif", b"GIF89a-r2" + b"t" * 5)))
        )
        ev = FakeEvent(text="今天好累啊")
        asyncio.run(plugin.regex_send(ev))
        self.assertEqual(ev.sent, [])
        self.assertFalse(ev.stopped)

    def test_regex_send_ignores_save_request(self):
        import asyncio

        plugin = self.mod.FaceBridge(None, {})
        asyncio.run(
            plugin._ingest(FakeEvent(), self.FakeImage(self._gif("r3.gif", b"GIF89a-r3" + b"u" * 5)))
        )
        ev = FakeEvent(text="把这张存下来")
        asyncio.run(plugin.regex_send(ev))
        self.assertEqual(ev.sent, [])

    # ------------------------------------------------------------------ 输出处理

    def test_voice_added_only_when_requested(self):
        import asyncio

        class FakeTTS:
            def __init__(self):
                self.calls = []

            async def get_audio(self, text):
                self.calls.append(text)
                return "/tmp/fake.wav"

        class Ctx:
            def __init__(self):
                self.tts = FakeTTS()

            def get_using_tts_provider(self, umo=None):
                return self.tts

        ctx = Ctx()
        plugin = self.mod.FaceBridge(ctx, {})

        ev = FakeEvent(text="说句话给我听", reply="行啊")
        asyncio.run(plugin.voice_on_demand(ev))
        self.assertEqual(ctx.tts.calls, ["行啊"])
        self.assertEqual(len(ev._result.chain), 2)

        ev2 = FakeEvent(text="今天吃了啥", reply="随便吃了点")
        asyncio.run(plugin.voice_on_demand(ev2))
        self.assertEqual(len(ctx.tts.calls), 1)

    def test_recent_media_hint(self):
        import asyncio

        plugin = self.mod.FaceBridge(None, {})
        req = types.SimpleNamespace(prompt="hi", extra_user_content_parts=[])
        asyncio.run(plugin.inject_recent_media_hint(FakeEvent(), req))
        self.assertEqual(req.extra_user_content_parts, [])

        plugin.recent_media = {"id": "abc123", "path": "/tmp/x.gif", "kind": "face"}
        asyncio.run(plugin.inject_recent_media_hint(FakeEvent(), req))
        self.assertEqual(len(req.extra_user_content_parts), 1)
        self.assertIn("abc123", req.extra_user_content_parts[0]["text"])

    def test_annotate_qq_faces(self):
        import asyncio

        FaceCls = sys.modules["astrbot.api.message_components"].Face
        plugin = self.mod.FaceBridge(None, {})
        # 名称表在真实插件目录里，桩环境下可能为空，直接注入两条
        plugin.face_table = {"178": "斜眼笑", "14": "微笑"}

        ev = FakeEvent([FaceCls(id=178), FaceCls(id=14)])
        req = types.SimpleNamespace(prompt="hi", extra_user_content_parts=[])
        asyncio.run(plugin.annotate_qq_faces(ev, req))
        self.assertEqual(len(req.extra_user_content_parts), 1)
        text = req.extra_user_content_parts[0]["text"]
        self.assertIn("斜眼笑", text)
        self.assertIn("微笑", text)

    def test_annotate_qq_faces_skips_without_face(self):
        import asyncio

        plugin = self.mod.FaceBridge(None, {})
        ev = FakeEvent()
        req = types.SimpleNamespace(prompt="hi", extra_user_content_parts=[])
        asyncio.run(plugin.annotate_qq_faces(ev, req))
        self.assertEqual(req.extra_user_content_parts, [])

    def test_auto_lookup_injects(self):
        import asyncio

        plugin = self.mod.FaceBridge(None, {})
        plugin._websearch_key = lambda: "k"

        async def fake_search(q, k, timeout=6.0, max_results=3):
            return "- 班味：职场疲惫状态"

        plugin._search_web = fake_search
        req = types.SimpleNamespace(prompt="hi", extra_user_content_parts=[])
        asyncio.run(plugin.auto_lookup(FakeEvent(text="班味是什么梗"), req))
        self.assertEqual(len(req.extra_user_content_parts), 1)
        self.assertIn("班味", req.extra_user_content_parts[0]["text"])

    def test_auto_lookup_skips_greeting(self):
        import asyncio

        plugin = self.mod.FaceBridge(None, {})
        plugin._websearch_key = lambda: "k"
        calls = []

        async def fake_search(q, k, timeout=6.0, max_results=3):
            calls.append(q)
            return "- x"

        plugin._search_web = fake_search
        for greeting in ("在吗", "早", "晚安", "哈哈哈", "吃了吗"):
            req = types.SimpleNamespace(prompt="hi", extra_user_content_parts=[])
            asyncio.run(plugin.auto_lookup(FakeEvent(text=greeting), req))
            self.assertEqual(req.extra_user_content_parts, [], f"{greeting} 不该触发搜索")
        self.assertEqual(calls, [])

    def test_auto_lookup_triggers_on_short_chat(self):
        import asyncio

        plugin = self.mod.FaceBridge(None, {})
        plugin._websearch_key = lambda: "k"
        calls = []

        async def fake_search(q, k, timeout=6.0, max_results=3):
            calls.append(q)
            return "- 相关内容"

        plugin._search_web = fake_search
        req = types.SimpleNamespace(prompt="hi", extra_user_content_parts=[])
        asyncio.run(plugin.auto_lookup(FakeEvent(text="这也太kskbl了"), req))
        self.assertEqual(calls, ["这也太kskbl了"])
        self.assertEqual(len(req.extra_user_content_parts), 1)

    def test_auto_lookup_skips_normal_chat(self):
        import asyncio

        plugin = self.mod.FaceBridge(None, {"lookup_every_message": False})
        plugin._websearch_key = lambda: "k"
        calls = []

        async def fake_search(q, k, timeout=6.0, max_results=3):
            calls.append(q)
            return "- x"

        plugin._search_web = fake_search
        req = types.SimpleNamespace(prompt="hi", extra_user_content_parts=[])
        asyncio.run(plugin.auto_lookup(FakeEvent(text="今天好累啊"), req))
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
