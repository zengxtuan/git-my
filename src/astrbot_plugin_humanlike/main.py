"""humanlike —— 让机器人"像个人在打字"，同时降低被识别为协议端的概率。

做四件小事，都是协议端能做的、真人也都会做的事：

1. 收到消息先标记已读（真人会点开）
2. 回复前随机停顿一下（真人不会秒回）
3. 尽量开启"正在输入"状态
4. 单条消息之间也留出打字间隔

这些不能消除封号风险，但能把"机器味"降下去。风险说明见 guides/risk-control.md。
"""

from __future__ import annotations

import asyncio
import random

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, StarTools

PLUGIN_NAME = "astrbot_plugin_humanlike"


class HumanLike(Star):
    """把机器的节奏调成人的节奏。"""

    def __init__(self, context: Context, config: AstrBotConfig | None = None) -> None:
        super().__init__(context, config)
        self.config = config or {}
        self.min_delay = float(self.config.get("min_delay", 0.6) or 0.6)
        self.max_delay = float(self.config.get("max_delay", 1.8) or 1.8)
        self.mark_read = bool(self.config.get("mark_read", True))
        self.typing = bool(self.config.get("typing_indicator", True))
        logger.info(
            "[humanlike] loaded, 延迟 %.1f-%.1fs，已读=%s，正在输入=%s",
            self.min_delay,
            self.max_delay,
            self.mark_read,
            self.typing,
        )

    @staticmethod
    def _bot(event: AstrMessageEvent):
        bot = getattr(event, "bot", None)
        return bot if hasattr(bot, "call_action") else None

    async def _mark_read(self, event: AstrMessageEvent) -> None:
        bot = self._bot(event)
        if bot is None:
            return
        try:
            uid = event.get_sender_id()
            if not str(uid).isdigit():
                return
            await bot.call_action("mark_private_msg_as_read", user_id=int(uid))
        except Exception as e:
            logger.debug("[humanlike] 标记已读失败: %s", e)

    async def _set_typing(self, event: AstrMessageEvent) -> None:
        bot = self._bot(event)
        if bot is None:
            return
        try:
            uid = event.get_sender_id()
            if not str(uid).isdigit():
                return
            await bot.call_action("set_input_status", user_id=int(uid), event_type=1)
        except Exception as e:
            # 参数不对就安静跳过，别刷日志
            logger.debug("[humanlike] 正在输入状态设置失败: %s", e)

    @filter.on_decorating_result(priority=90)
    async def humanize(self, event: AstrMessageEvent):
        """发送前：标已读 → 停一下 → 亮"正在输入" → 再停一下。"""
        if self.mark_read:
            await self._mark_read(event)

        delay = random.uniform(min(self.min_delay, self.max_delay), self.max_delay)
        await asyncio.sleep(delay)

        if self.typing:
            await self._set_typing(event)
            await asyncio.sleep(random.uniform(0.3, 0.9))

    @filter.on_llm_request(priority=9)
    async def noop(self, event: AstrMessageEvent, req):
        """占位：留个扩展点，不影响请求。"""
        return
