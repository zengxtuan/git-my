"""face_bridge 鎻掍欢鐨勭绾垮崟鍏冩祴璇曪紙涓嶉渶瑕?AstrBot 杩愯鐜锛夈€?
鍋氭硶锛氭敞鍏?astrbot.* 鐨勬々妯″潡锛屽啀鐢?importlib 鐩存帴鍔犺浇鎻掍欢 main.py銆?"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path

PLUGIN_MAIN = Path(__file__).resolve().parents[2] / "src" / "astrbot_plugin_face_bridge" / "main.py"


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
        def __init__(self, path: Path):
            self._path = path

        @staticmethod
        def fromFileSystem(path):
            return Image(Path(path))

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

        class EventMessageType:
            ALL = 7

    event_mod.AstrMessageEvent = AstrMessageEvent
    event_mod.MessageChain = MessageChain
    event_mod.filter = _Filter
    message.Image = Image
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
        return {"ok": True}


class FakeEvent:
    def __init__(self, images=(), bot=None):
        self._images = list(images)
        self.bot = bot or FakeBot()
        self.sent = []

    def get_messages(self):
        return self._images

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
        # 每个用例独立的数据目录，避免 index.json 互相污染
        for p in sorted(self.data_dir.rglob("*"), reverse=True):
            if p.is_file():
                p.unlink()
            elif p.is_dir():
                p.rmdir()
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def _gif(self, name="a.gif", payload=b"GIF89a" + b"x" * 32) -> Path:
        p = Path(self.tmp.name) / name
        p.write_bytes(payload)
        return p

    def test_md5_of(self):
        p = self._gif("md5.gif", b"GIF89a-hello")
        self.assertEqual(
            self.mod._md5_of(p), hashlib.md5(b"GIF89a-hello").hexdigest()
        )

    def test_store_and_add_to_qq_face(self):
        plugin = self.mod.FaceBridge(None, {"auto_archive": True})
        event = FakeEvent()
        import asyncio

        src = self._gif("store.gif", b"GIF89a-store" + b"y" * 16)
        rec = asyncio.run(plugin._store_image(event, self.FakeImage(src)))
        self.assertIsNotNone(rec)
        self.assertEqual(rec["id"], hashlib.md5(b"GIF89a-store" + b"y" * 16).hexdigest()[:12])
        self.assertTrue(Path(rec["path"]).exists())

        ok, _ = asyncio.run(plugin._add_to_qq_face(event, Path(rec["path"])))
        self.assertTrue(ok)
        self.assertEqual(event.bot.calls[0][0], "add_custom_face")
        self.assertTrue(event.bot.calls[0][1]["is_origin"])

    def test_dedupe_by_md5(self):
        plugin = self.mod.FaceBridge(None, {})
        src = self._gif("dup.gif", b"GIF89a-dup" + b"z" * 8)
        import asyncio

        a = asyncio.run(plugin._store_image(FakeEvent(), self.FakeImage(src)))
        b = asyncio.run(plugin._store_image(FakeEvent(), self.FakeImage(src)))
        self.assertEqual(a["id"], b["id"])
        self.assertEqual(len(plugin.index), 1)

    def test_send_face_unknown_id(self):
        plugin = self.mod.FaceBridge(None, {})
        import asyncio

        out = json.loads(asyncio.run(plugin.send_face_tool(FakeEvent(), "deadbeefcafe")))
        self.assertFalse(out["ok"])

    def test_send_face_by_id(self):
        plugin = self.mod.FaceBridge(None, {})
        import asyncio

        src = self._gif("send.gif", b"GIF89a-send" + b"q" * 12)
        rec = asyncio.run(plugin._store_image(FakeEvent(), self.FakeImage(src)))
        event = FakeEvent()
        out = json.loads(asyncio.run(plugin.send_face_tool(event, rec["id"])))
        self.assertTrue(out["ok"])
        self.assertEqual(len(event.sent), 1)

    def test_send_face_empty_id_sends_latest(self):
        plugin = self.mod.FaceBridge(None, {})
        import asyncio

        old = self._gif("old.gif", b"GIF89a-old" + b"a" * 10)
        new = self._gif("new.gif", b"GIF89a-new" + b"b" * 10)
        rec_old = asyncio.run(plugin._store_image(FakeEvent(), self.FakeImage(old)))
        rec_new = asyncio.run(plugin._store_image(FakeEvent(), self.FakeImage(new)))
        rec_old["added"] = 1
        rec_new["added"] = 2

        event = FakeEvent()
        out = json.loads(asyncio.run(plugin.send_face_tool(event, "")))
        self.assertTrue(out["ok"])
        self.assertEqual(out["sent"], rec_new["id"])
        self.assertEqual(len(event.sent), 1)

    def test_list_faces(self):
        plugin = self.mod.FaceBridge(None, {})
        import asyncio

        src = self._gif("list.gif", b"GIF89a-list" + b"w" * 4)
        asyncio.run(plugin._store_image(FakeEvent(), self.FakeImage(src)))
        out = json.loads(asyncio.run(plugin.list_faces_tool(FakeEvent(), 5)))
        self.assertGreaterEqual(out["total"], 1)
        self.assertIn("id", out["items"][0])

    def test_auto_archive_hook_disabled(self):
        plugin = self.mod.FaceBridge(None, {"auto_archive": False})
        src = self._gif("hook.gif", b"GIF89a-hook")
        event = FakeEvent([self.FakeImage(src)])
        import asyncio

        asyncio.run(plugin.on_any_message(event))
        self.assertEqual(event.bot.calls, [])
        self.assertEqual(plugin.index, {})

    def test_auto_archive_hook_runs(self):
        plugin = self.mod.FaceBridge(None, {"auto_archive": True})
        src = self._gif("hook2.gif", b"GIF89a-hook2" + b"m" * 6)
        event = FakeEvent([self.FakeImage(src)])
        import asyncio

        asyncio.run(plugin.on_any_message(event))
        self.assertEqual(len(event.bot.calls), 1)
        self.assertEqual(event.bot.calls[0][0], "add_custom_face")
        self.assertEqual(len(plugin.index), 1)


if __name__ == "__main__":
    unittest.main()
