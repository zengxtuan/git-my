"""convo_tally —— 会话计费：让模型自己知道这轮 / 今天 / 累计花了多少钱。

数据来源：每轮 LLM 响应的 ``TokenUsage``（``input_other`` / ``input_cached`` / ``output``），
按配置里的价目表逐次累加，**按会话 ID 分开记**，落盘 JSON，重启接着算。

设计约束：
* 价目表只从配置读，改价不改代码。
* 任何一个环节拿不到（无 usage / 模型不在价目表 / 字段缺失），返回「未计价 + 原因」，
  绝不编数字。
* 记账失败不抛异常，不影响正常聊天。
* 金额一律注明「按价目表估算，非账单」。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from astrbot.api import AstrBotConfig, llm_tool, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.provider import LLMResponse, ProviderRequest
from astrbot.api.star import Context, Star, StarTools

PLUGIN_NAME = "astrbot_plugin_convo_tally"
HINT = "\n\n（可用 get_chat_cost 查本会话花了多少钱。）"


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _blank_record() -> dict:
    return {
        "today": _today(),
        "today_cny": 0.0,
        "total_cny": 0.0,
        "in": 0,
        "cached": 0,
        "out": 0,
        "priced": 0,      # 成功计价过的轮数
        "unpriced": 0,    # 拿不到价格的轮数
        "hint_sent": False,
        "last": {},
    }


class ConvoTally(Star):
    """按会话累计 token 与花费，并向模型提供一个查询工具。"""

    def __init__(self, context: Context, config: AstrBotConfig | None = None) -> None:
        super().__init__(context, config)
        self.config = config or {}
        try:
            self.root = Path(StarTools.get_data_dir(PLUGIN_NAME))
        except Exception:
            # StarTools 未就绪时的约定路径兜底
            self.root = Path("data/plugin_data") / PLUGIN_NAME
        self.root.mkdir(parents=True, exist_ok=True)
        self.store = self.root / "costs.json"

        self.data: dict[str, dict] = self._load()
        self.prices: dict = self.config.get("prices", {}) or {}
        self.fallback_prices: dict = self.config.get("fallback_prices", {}) or {}
        self.usd_rate = self._as_float(self.config.get("usd_rate"), 7.3)
        self.default_model = str(self.config.get("default_model", "") or "")
        self.off_peak: dict = self.config.get("off_peak", {}) or {}

        logger.info(
            "[convo_tally] loaded: %d sessions, %d price entries, usd_rate=%s",
            len(self.data),
            len(self.prices),
            self.usd_rate,
        )

    # ────────────────────────────── 基础

    @staticmethod
    def _as_float(v, fallback: float) -> float:
        try:
            return float(v)
        except (TypeError, ValueError):
            return fallback

    # ────────────────────────────── 持久化

    def _load(self) -> dict:
        if not self.store.exists():
            return {}
        try:
            raw = json.loads(self.store.read_text(encoding="utf-8"))
            return raw if isinstance(raw, dict) else {}
        except Exception as e:
            logger.error("[convo_tally] costs.json 读取失败，按空账本启动: %s", e)
            return {}

    def _save(self) -> None:
        try:
            self.store.write_text(
                json.dumps(self.data, ensure_ascii=False, indent=1),
                encoding="utf-8",
            )
        except Exception as e:
            logger.error("[convo_tally] costs.json 写入失败: %s", e)

    # ────────────────────────────── 价格

    def _prices_for(self, model: str) -> dict | None:
        if model and model in self.prices:
            return self.prices[model]
        if self.fallback_prices:
            return self.fallback_prices
        return None

    def _in_off_peak(self) -> bool:
        """按服务器本地时间判断是否处于谷时。支持跨午夜区间。"""
        if not self.off_peak.get("enabled", False):
            return False
        start = str(self.off_peak.get("start", "00:30"))
        end = str(self.off_peak.get("end", "08:30"))
        now = datetime.now().strftime("%H:%M")
        if start <= end:
            return start <= now < end
        return now >= start or now < end

    def _cost_cny(self, usage, model: str) -> tuple[float | None, str]:
        """按价目表算人民币。返回 (金额 或 None, 说明)。"""
        prices = self._prices_for(model)
        if prices is None:
            return None, f"价目表里没有模型 {model or '(未知)'}"

        try:
            p_in = float(prices.get("input", 0))
            p_cached = float(prices.get("input_cached", p_in))
            p_out = float(prices.get("output", 0))
        except (TypeError, ValueError) as e:
            return None, f"价目表格式错误: {e}"

        ratio = self._as_float(prices.get("off_peak_ratio"), 0.5) if self._in_off_peak() else 1.0

        input_other = getattr(usage, "input_other", 0) or 0
        input_cached = getattr(usage, "input_cached", 0) or 0
        output = getattr(usage, "output", 0) or 0

        cny = (
            input_other / 1_000_000 * p_in
            + input_cached / 1_000_000 * p_cached
            + output / 1_000_000 * p_out
        ) * ratio
        return cny, ("谷时" if ratio < 1 else "峰时")

    def _model_of(self, response: LLMResponse) -> str:
        """优先从原始响应里取 model，其次用配置的默认模型。"""
        raw = getattr(response, "raw_completion", None)
        model = getattr(raw, "model", None)
        if isinstance(model, str) and model:
            return model
        return self.default_model

    # ────────────────────────────── 记账（钩子）

    @filter.on_agent_done()
    async def record_usage(
        self,
        event: AstrMessageEvent,
        run_context,
        response: LLMResponse,
    ) -> None:
        """每轮 Agent 跑完后累计本轮 usage。失败只记日志，不影响聊天。"""
        usage = getattr(response, "usage", None)
        if usage is None:
            return

        try:
            model = self._model_of(response)
            cny, note = self._cost_cny(usage, model)

            key = getattr(event, "unified_msg_origin", "") or "unknown"
            rec = self.data.get(key)
            if not isinstance(rec, dict):
                rec = _blank_record()
                self.data[key] = rec

            if rec.get("today") != _today():
                rec["today"] = _today()
                rec["today_cny"] = 0.0

            rec["in"] = int(rec.get("in", 0)) + int(getattr(usage, "input_other", 0) or 0)
            rec["cached"] = int(rec.get("cached", 0)) + int(getattr(usage, "input_cached", 0) or 0)
            rec["out"] = int(rec.get("out", 0)) + int(getattr(usage, "output", 0) or 0)

            if cny is not None:
                rec["today_cny"] = float(rec.get("today_cny", 0.0)) + cny
                rec["total_cny"] = float(rec.get("total_cny", 0.0)) + cny
                rec["priced"] = int(rec.get("priced", 0)) + 1
            else:
                rec["unpriced"] = int(rec.get("unpriced", 0)) + 1

            rec["last"] = {
                "model": model,
                "cny": cny,
                "note": note,
                "used": int(getattr(usage, "total", 0) or 0),
                "at": datetime.now().strftime("%H:%M:%S"),
            }
            self._save()
        except Exception as e:
            logger.error("[convo_tally] 记账异常（已忽略，不影响聊天）: %s", e)

    # ────────────────────────────── 会话首次注入一句提示

    @filter.on_llm_request()
    async def inject_hint(self, event: AstrMessageEvent, req: ProviderRequest) -> None:
        """每个会话只注入一次极短说明，不每轮塞统计。"""
        try:
            key = getattr(event, "unified_msg_origin", "") or "unknown"
            rec = self.data.get(key)
            if not isinstance(rec, dict):
                rec = _blank_record()
                self.data[key] = rec
            if rec.get("hint_sent"):
                return
            req.system_prompt = (req.system_prompt or "") + HINT
            rec["hint_sent"] = True
            self._save()
        except Exception as e:
            logger.error("[convo_tally] 注入提示失败: %s", e)

    # ────────────────────────────── 工具

    @llm_tool(name="get_chat_cost")
    async def get_chat_cost(self, event: AstrMessageEvent, session: str = "") -> str:
        """查询会话花费（按本地价目表估算，非账单）。

Args:
        session(string): 会话标识，留空表示当前会话
"""
        key = session or getattr(event, "unified_msg_origin", "") or "unknown"
        rec = self.data.get(key)
        if not isinstance(rec, dict) or not rec:
            return "这个会话还没有计费记录——可能刚开，或还没产生过 LLM 调用。"

        today = self._as_float(rec.get("today_cny"), 0.0)
        total = self._as_float(rec.get("total_cny"), 0.0)
        priced = int(rec.get("priced", 0))
        unpriced = int(rec.get("unpriced", 0))
        last = rec.get("last") or {}
        last_cny = last.get("cny")

        if last_cny is None:
            last_text = f"未计价（{last.get('note', '原因未知')}）"
        else:
            last_text = f"¥{float(last_cny):.4f}"

        # 一次都没计过价：金额位显示「未计价」，不写 ¥0.0000 误导成没花钱
        if priced == 0:
            money = f"未计价（{unpriced} 次调用拿不到价格）" if unpriced else "暂无记录"
        else:
            money = (
                f"今日 ¥{today:.4f} / ${today / self.usd_rate:.4f}；"
                f"累计 ¥{total:.4f} / ${total / self.usd_rate:.4f}"
            )
            if unpriced:
                money += f"（另有 {unpriced} 次未计价）"

        return (
            f"本轮 {last_text}；"
            f"{money}；"
            f"tokens 输入{int(rec.get('in', 0))}+缓存{int(rec.get('cached', 0))}、输出{int(rec.get('out', 0))}。"
            f"按价目表估算，非账单。"
        )
