"""face_bridge 权限分流测试。

覆盖两件事：
1. `_is_admin` 的 fail-close 语义（判断出错时按非管理员处理）
2. `_deliver_best` 的 kind 分流（photo 只给管理员，face 放行）

注入 astrbot.* 桩模块后直接加载插件 main.py，不依赖 AstrBot 运行环境。
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import tempfile
import types
import unittest
from pathlib import Path

PLUGIN_MAIN = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "astrbot_plugin_face_bridge"
    / "main.py"
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
    msg_mod = types.ModuleType("astrbot.api.message_components")
    star_mod = types.ModuleType("astrbot.api.star")

    class AstrMessageEvent:
        def __init__(self, admin=False, text=""):
            self._admin = admin
            self._text = text

        def is_admin(self):
            return self._admin

        def get_message_str(self):
            return self._text

    class MessageChain(list):
        def __init__(self, chain=None):
            super().__init__(chain or [])

    class _Filter:
        class EventMessageType:
            ALL = 7
            PRIVATE_MESSAGE = 1
            GROUP_MESSAGE = 2

        class PermissionType:
            ADMIN = 1

        @staticmethod
        def regex(*_a, **_k):
            def deco(fn):
                return fn

            return deco

        @staticmethod
        def command(*_a, **_k):
            def deco(fn):
                return fn

            return deco

        @staticmethod
        def event_message_type(*_a, **_k):
            def deco(fn):
                return fn

            return deco

        @staticmethod
        def on_llm_request(*_a, **_k):
            def deco(fn):
                return fn

            return deco

        @staticmethod
        def on_decorating_result(*_a, **_k):
            def deco(fn):
                return fn

            return deco

        @staticmethod
        def on_agent_done(*_a, **_k):
            def deco(fn):
                return fn

            return deco

        @staticmethod
        def after_message_sent(*_a, **_k):
            def deco(fn):
                return fn

            return deco

        @staticmethod
        def on_astrbot_loaded(*_a, **_k):
            def deco(fn):
                return fn

            return deco

        @staticmethod
        def permission_type(*_a, **_k):
            def deco(fn):
                return fn

            return deco

    class Image:
        def __init__(self, path=None):
            self._path = path

        @staticmethod
        def fromFileSystem(path):
            return Image(path)

    class Face:
        def __init__(self, id=None, **_):
            self.id = id

    class Plain:
        def __init__(self, text=""):
            self.text = text

    class Record:
        def __init__(self, file=""):
            self.file = file

    class Star:
        def __init__(self, context, config=None):
            self.context = context

    class Context:
        pass

    class StarTools:
        @classmethod
        def get_data_dir(cls, name=None):
            return data_dir

    event_mod.AstrMessageEvent = AstrMessageEvent
    event_mod.MessageChain = MessageChain
    event_mod.filter = _Filter
    msg_mod.Image = Image
    msg_mod.Face = Face
    msg_mod.Plain = Plain
    msg_mod.Record = Record
    star_mod.Context = Context
    star_mod.Star = Star
    star_mod.StarTools = StarTools

    astrbot.api = api
    sys.modules["astrbot"] = astrbot
    sys.modules["astrbot.api"] = api
    sys.modules["astrbot.api.event"] = event_mod
    sys.modules["astrbot.api.message_components"] = msg_mod
    sys.modules["astrbot.api.star"] = star_mod


def _load_plugin():
    spec = importlib.util.spec_from_file_location("fb_perm_main", PLUGIN_MAIN)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class IsAdminTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        _install_stubs(Path(self.tmp.name))
        self.mod = _load_plugin()

    def tearDown(self):
        self.tmp.cleanup()

    def test_admin_true(self):
        ev = self.mod.AstrMessageEvent(admin=True)
        self.assertTrue(self.mod._is_admin(ev))

    def test_admin_false(self):
        ev = self.mod.AstrMessageEvent(admin=False)
        self.assertFalse(self.mod._is_admin(ev))

    def test_is_admin_raises_fail_close(self):
        """is_admin 抛异常时必须当非管理员，不能放行。"""

        class Boom:
            def is_admin(self):
                raise RuntimeError("boom")

        self.assertFalse(self.mod._is_admin(Boom()))

    def test_missing_is_admin_fail_close(self):
        """对象没有 is_admin 方法时也必须当非管理员。"""

        class Bare:
            pass

        self.assertFalse(self.mod._is_admin(Bare()))


class DeliverBestTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        _install_stubs(Path(self.tmp.name))
        self.mod = _load_plugin()
        self.plugin = self.mod.FaceBridge(None, {})
        # 塞两条记录，一条 face 一条 photo
        self.face = {"id": "f1", "path": "/tmp/none_f1.gif", "kind": "face"}
        self.photo = {"id": "p1", "path": "/tmp/none_p1.jpg", "kind": "photo"}
        self.plugin.media = {"f1": self.face, "p1": self.photo}
        self.sent = []

        async def fake_deliver(event, rec, reason):
            self.sent.append((rec["id"], reason))
            return True

        self.plugin._deliver = fake_deliver

        async def fake_afind(want, kind=None):
            for rec in (self.face, self.photo):
                if kind is None or rec["kind"] == kind:
                    return rec
            return None

        self.plugin.afind = fake_afind
        self.plugin.recent_media = None
        self.plugin.latest = lambda kind=None: None
        self.plugin.by_kind = lambda kind=None: []

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, event, want):
        return asyncio.run(self.plugin._deliver_best(event, want, reason="test"))

    def test_photo_denied_for_normal_user(self):
        ev = self.mod.AstrMessageEvent(admin=False)
        out = self._run(ev, "把照片发我看看")
        self.assertFalse(out["ok"])
        self.assertIn("照片只给管理员看", out["reason"])
        self.assertEqual(self.sent, [], "非管理员不应收到任何照片")

    def test_photo_allowed_for_admin(self):
        ev = self.mod.AstrMessageEvent(admin=True)
        out = self._run(ev, "把照片发我看看")
        self.assertTrue(out["ok"])

    def test_face_allowed_for_normal_user(self):
        ev = self.mod.AstrMessageEvent(admin=False)
        out = self._run(ev, "发我那个表情包")
        self.assertTrue(out["ok"])

    def test_no_kind_refused(self):
        """既没说表情也没说照片 → 直接拒，不发任何东西。"""
        ev = self.mod.AstrMessageEvent(admin=True)
        out = self._run(ev, "分成两段对话发我，我好复制")
        self.assertFalse(out["ok"])
        self.assertIn("没点名要表情还是照片", out["reason"])
        self.assertEqual(self.sent, [])


if __name__ == "__main__":
    unittest.main()
