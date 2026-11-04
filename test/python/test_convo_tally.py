"""convo_tally 插件离线单元测试（不需要 AstrBot 运行环境）。

注入 astrbot.* 桩模块后用 importlib 直接加载插件 main.py。
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path

PLUGIN_MAIN = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "astrbot_plugin_convo_tally"
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
    provider_mod = types.ModuleType("astrbot.api.provider")
    star_mod = types.ModuleType("astrbot.api.star")

    class AstrMessageEvent:
        def __init__(self, umo="qq:FriendMessage:1"):
            self.unified_msg_origin = umo

    class LLMResponse:
        pass

    class ProviderRequest:
        def __init__(self, system_prompt=""):
            self.system_prompt = system_prompt

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
        def on_agent_done(*_a, **_k):
            def deco(fn):
                fn._handler = "on_agent_done"
                return fn

            return deco

        @staticmethod
        def on_llm_request(*_a, **_k):
            def deco(fn):
                fn._handler = "on_llm_request"
                return fn

            return deco

    event_mod.AstrMessageEvent = AstrMessageEvent
    event_mod.filter = _Filter
    provider_mod.LLMResponse = LLMResponse
    provider_mod.ProviderRequest = ProviderRequest
    star_mod.Context = Context
    star_mod.Star = Star
    star_mod.StarTools = StarTools

    astrbot.api = api
    sys.modules["astrbot"] = astrbot
    sys.modules["astrbot.api"] = api
    sys.modules["astrbot.api.event"] = event_mod
    sys.modules["astrbot.api.provider"] = provider_mod
    sys.modules["astrbot.api.star"] = star_mod


def _load_plugin():
    spec = importlib.util.spec_from_file_location("convo_tally_main", PLUGIN_MAIN)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _Usage:
    def __init__(self, i=0, c=0, o=0):
        self.input_other = i
        self.input_cached = c
        self.output = o

    @property
    def total(self):
        return self.input_other + self.input_cached + self.output


def _resp(usage, model="m1"):
    return types.SimpleNamespace(
        usage=usage,
        raw_completion=types.SimpleNamespace(model=model),
    )


BASE_PRICES = {
    "m1": {"input": 2.0, "input_cached": 0.5, "output": 8.0, "off_peak_ratio": 0.5}
}


class TallyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.tmp.name)
        _install_stubs(self.data_dir)
        self.mod = _load_plugin()

    def tearDown(self):
        self.tmp.cleanup()

    def _mk(self, **cfg):
        base = {
            "prices": BASE_PRICES,
            "fallback_prices": {},
            "usd_rate": 7.3,
            "default_model": "m1",
            "off_peak": {"enabled": False},
        }
        base.update(cfg)
        return self.mod.ConvoTally(None, base)

    def _record(self, plugin, event, usage):
        asyncio.run(plugin.record_usage(event, None, _resp(usage)))

    # ─────────────────────────── 1. 正常记账 + 工具输出

    def test_record_then_query(self):
        p = self._mk()
        ev = self.mod.AstrMessageEvent("s1")
        # 1M 输入 + 1M 缓存 + 1M 输出 = 2 + 0.5 + 8 = 10.5 元
        self._record(p, ev, _Usage(1_000_000, 1_000_000, 1_000_000))

        out = asyncio.run(p.get_chat_cost(ev))
        self.assertIn("¥10.5000", out)
        self.assertIn("/ $1.4384", out)  # 10.5 / 7.3
        self.assertIn("非账单", out)
        self.assertNotIn("{", out)  # 不输出 JSON

    # ─────────────────────────── 2. 未知模型 → 未计价

    def test_unknown_model_is_unpriced(self):
        p = self._mk(prices={"other": {"input": 1, "output": 1}})
        ev = self.mod.AstrMessageEvent("s2")
        self._record(p, ev, _Usage(1000, 0, 500))

        out = asyncio.run(p.get_chat_cost(ev))
        self.assertIn("未计价", out)
        self.assertIn("价目表里没有模型 m1", out)
        # 关键：一次都没计过价时，不能出现 ¥0.0000（会被读成「没花钱」）
        self.assertNotIn("¥0.0", out)
        self.assertIn("1 次调用拿不到价格", out)

    def test_fallback_prices_applied(self):
        p = self._mk(
            prices={},
            fallback_prices={"input": 1.0, "input_cached": 1.0, "output": 1.0},
        )
        ev = self.mod.AstrMessageEvent("s2b")
        self._record(p, ev, _Usage(1_000_000, 0, 0))
        out = asyncio.run(p.get_chat_cost(ev))
        self.assertIn("¥1.0000", out)

    # ─────────────────────────── 3. 谷时半价

    def test_off_peak_halves_cost(self):
        p = self._mk(off_peak={"enabled": True, "start": "00:00", "end": "23:59"})
        ev = self.mod.AstrMessageEvent("s3")
        self._record(p, ev, _Usage(1_000_000, 0, 0))
        out = asyncio.run(p.get_chat_cost(ev))
        self.assertIn("¥1.0000", out)  # 2.0 * 0.5

    def test_off_peak_overnight_window(self):
        p = self._mk()
        p.off_peak = {"enabled": True, "start": "23:00", "end": "01:00"}
        # 用一个一定落在窗口内或外的时间无法稳定断言，这里只验证跨午夜分支不抛异常
        p._in_off_peak()

    # ─────────────────────────── 4. 跨天：today 清零、total 保留

    def test_day_rollover_keeps_total(self):
        p = self._mk()
        ev = self.mod.AstrMessageEvent("s4")
        self._record(p, ev, _Usage(1_000_000, 0, 0))  # 2 元

        rec = p.data["s4"]
        self.assertEqual(rec["total_cny"], 2.0)
        self.assertEqual(rec["today_cny"], 2.0)

        rec["today"] = "1970-01-01"  # 伪造跨天

        self._record(p, ev, _Usage(1_000_000, 0, 0))
        rec = p.data["s4"]
        self.assertEqual(rec["today_cny"], 2.0)  # 今天重新开始
        self.assertEqual(rec["total_cny"], 4.0)  # 累计保留

    # ─────────────────────────── 5. 持久化

    def test_persistence_across_reload(self):
        p = self._mk()
        ev = self.mod.AstrMessageEvent("s5")
        self._record(p, ev, _Usage(500_000, 0, 0))  # 1 元

        p2 = self.mod.ConvoTally(None, dict(p.config))
        out = asyncio.run(p2.get_chat_cost(ev))
        self.assertIn("¥1.0000", out)
        self.assertIn("累计", out)

    def test_corrupt_store_falls_back_to_empty(self):
        (self.data_dir / "costs.json").write_text("{not json", encoding="utf-8")
        p = self._mk()
        self.assertEqual(p.data, {})

    # ─────────────────────────── 6. 提示只注入一次

    def test_hint_injected_once(self):
        p = self._mk()
        ev = self.mod.AstrMessageEvent("s6")

        req1 = self.mod.ProviderRequest("base")
        asyncio.run(p.inject_hint(ev, req1))
        self.assertIn("get_chat_cost", req1.system_prompt)
        self.assertTrue(req1.system_prompt.startswith("base"))

        req2 = self.mod.ProviderRequest("base")
        asyncio.run(p.inject_hint(ev, req2))
        self.assertEqual(req2.system_prompt, "base")  # 第二次不再加

        # 另一个会话仍会得到提示
        ev2 = self.mod.AstrMessageEvent("s6-other")
        req3 = self.mod.ProviderRequest("base")
        asyncio.run(p.inject_hint(ev2, req3))
        self.assertIn("get_chat_cost", req3.system_prompt)

    # ─────────────────────────── 7. 无 usage 不抛异常

    def test_no_usage_is_noop(self):
        p = self._mk()
        ev = self.mod.AstrMessageEvent("s7")
        asyncio.run(p.record_usage(ev, None, types.SimpleNamespace(usage=None)))
        self.assertNotIn("s7", p.data)

    def test_empty_query_message(self):
        p = self._mk()
        ev = self.mod.AstrMessageEvent("nobody")
        out = asyncio.run(p.get_chat_cost(ev))
        self.assertIn("还没有计费记录", out)

    def test_tool_name_registered(self):
        p = self._mk()
        self.assertEqual(p.get_chat_cost._llm_tool_name, "get_chat_cost")


if __name__ == "__main__":
    unittest.main()
