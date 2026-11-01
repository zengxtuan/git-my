"""qq_power —— 补上"一个正常人用 QQ 该有的操作"。

NapCat 底层早就支持这些 action，只是 AstrBot 没把它们暴露出来。
这里全部做成**命令**而不是 LLM 工具：
一是主人专属、可控；二是工具数量一多 deepseek-flash 就会崩（有实测）。

命令（都在好友私聊里发）：
    /好友                列出好友
    /申请                待处理的好友申请
    /同意 <flag> [备注]   通过申请
    /拒绝 <flag>         拒绝申请
    /删好友 <QQ>         删除好友
    /备注 <QQ> <备注>     改好友备注
    /点赞 <QQ> [次数]     给好友名片点赞
    /拍 <QQ>             拍一拍
    /资料 <QQ>           看陌生人资料
    /改昵称 <昵称>        改机器人自己的昵称
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from astrbot.api import AstrBotConfig, llm_tool, logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.message_components import Plain
from astrbot.api.star import Context, Star, StarTools

PLUGIN_NAME = "astrbot_plugin_qq_power"
CACHE_FILE = "friend_requests.json"


class QQPower(Star):
    """QQ 权限补全。"""

    def __init__(self, context: Context, config: AstrBotConfig | None = None) -> None:
        super().__init__(context, config)
        self.config = config or {}
        try:
            self.root = Path(StarTools.get_data_dir(PLUGIN_NAME))
        except Exception:
            self.root = Path("data/plugin_data") / PLUGIN_NAME
        self.root.mkdir(parents=True, exist_ok=True)
        self.req_path = self.root / CACHE_FILE
        self.requests: dict[str, dict] = self._load_requests()
        logger.info("[qq_power] loaded, %d 条申请缓存", len(self.requests))

    # ------------------------------------------------------------------ LLM 工具

    async def _do_allow(self, event: AstrMessageEvent, qq: str) -> str:
        entry = f"qq:FriendMessage:{qq}"
        try:
            cfg = self.context.get_config()
        except Exception as e:
            return f"读配置失败：{e}"
        ps = cfg.get("platform_settings") or {}
        wl = list(ps.get("id_whitelist") or [])
        if entry in wl:
            return f"{qq} 本来就能跟机器人说话（白名单共 {len(wl)} 人）"
        wl.append(entry)
        ps["id_whitelist"] = wl
        cfg["platform_settings"] = ps
        try:
            await cfg.save_config_async()
        except Exception as e:
            return f"写配置失败：{e}"
        return (
            f"放行了 {qq}，现在共 {len(wl)} 人能跟机器人说话。"
            "他跟机器人的记忆是分开存的，看不到你这边的聊天。"
        )

    async def _do_revoke(self, event: AstrMessageEvent, qq: str) -> str:
        entry = f"qq:FriendMessage:{qq}"
        try:
            cfg = self.context.get_config()
        except Exception as e:
            return f"读配置失败：{e}"
        ps = cfg.get("platform_settings") or {}
        wl = list(ps.get("id_whitelist") or [])
        if entry not in wl:
            return f"{qq} 本来就不在白名单里"
        wl.remove(entry)
        ps["id_whitelist"] = wl
        cfg["platform_settings"] = ps
        try:
            await cfg.save_config_async()
        except Exception as e:
            return f"写配置失败：{e}"
        return f"收回了 {qq}，还剩 {len(wl)} 人"

    async def _do_list_whitelist(self, event: AstrMessageEvent) -> str:
        try:
            cfg = self.context.get_config()
        except Exception as e:
            return f"读配置失败：{e}"
        ps = cfg.get("platform_settings") or {}
        wl = list(ps.get("id_whitelist") or [])
        head = "白名单开着" if ps.get("enable_id_white_list") else "白名单关着（谁都能聊）"
        return head + (f"，共 {len(wl)} 人：\n" + "\n".join(wl) if wl else "，名单是空的")

    @llm_tool(name="qq_do")
    async def qq_do_tool(
        self,
        event: AstrMessageEvent,
        action: str,
        target: str = "",
        extra: str = "",
    ) -> str:
        """替主人操作 QQ，主人用大白话说要干什么时调用它。

        能做的事（action 取值）：
            放行 —— 让某人能跟机器人说话（target=他的QQ号）
            收回 —— 不让某人了（target=QQ号）
            白名单 —— 看谁在名单里
            好友 —— 列好友
            申请 —— 看待处理的好友申请
            同意 / 拒绝 —— 处理申请（target=申请里的 flag）
            删好友 —— 删掉某人（target=QQ号）
            备注 —— 改备注（target=QQ号，extra=备注名）
            点赞 —— 名片点赞（target=QQ号，extra=次数）
            拍 —— 拍一拍（target=QQ号）
            资料 —— 查资料（target=QQ号）
            改昵称 —— 改机器人自己的昵称（extra=新昵称）

        用户说「加他进来」「让他能跟我说话」就用 放行；
        说「别让他烦我」「踢了」就用 收回；说「删了他」才用 删好友。

        Args:
            action(string): 要做的事，取上面列出的值之一
            target(string): 目标 QQ 号或 flag，没有就留空
            extra(string): 附加内容，比如备注名、点赞次数、新昵称

        Returns:
            做完了的结果，直接用自己的话转述给主人。
        """
        act = (action or "").strip()
        tg = (target or "").strip()
        ex = (extra or "").strip()

        try:
            if act == "放行":
                if not tg.isdigit():
                    return "需要对方的 QQ 号才能放行"
                return await self._do_allow(event, tg)
            if act == "收回":
                if not tg.isdigit():
                    return "需要 QQ 号"
                return await self._do_revoke(event, tg)
            if act == "白名单":
                return await self._do_list_whitelist(event)

            if act == "好友":
                data, err = await self._call(event, "get_friend_list")
                if err:
                    return err
                items = data if isinstance(data, list) else (data or {}).get("data") or []
                return f"共 {len(items)} 个好友：" + "、".join(
                    f"{f.get('user_id') or f.get('uin')}({f.get('remark') or f.get('nickname') or ''})"
                    for f in items[:30]
                )
            if act == "申请":
                data, err = await self._call(event, "get_doubt_friends_add_request")
                if err:
                    return err
                items = data if isinstance(data, list) else (data or {}).get("data") or []
                if not items:
                    return "现在没有待处理的好友申请"
                lines = []
                for r in items[:10]:
                    flag = str(r.get("flag") or r.get("request_id") or "")
                    qq = r.get("user_id") or r.get("uin") or "?"
                    msg = str(r.get("message") or r.get("msg") or "")[:20]
                    if flag:
                        self.requests[flag] = {"flag": flag, "user_id": qq, "message": msg}
                    lines.append(f"{qq} 说「{msg}」flag={flag[:12]}")
                self._save_requests()
                return "待处理：\n" + "\n".join(lines) + "\n（要同意就把 flag 告诉我）"
            if act in ("同意", "拒绝"):
                flag = tg or (list(self.requests) or [""])[0]
                if not flag:
                    return "没有已知的申请 flag，先让我看一眼申请列表"
                data, err = await self._call(
                    event,
                    "set_friend_add_request",
                    flag=flag,
                    approve=(act == "同意"),
                    **({"remark": ex} if ex else {}),
                )
                if err:
                    return err
                self.requests.pop(flag, None)
                self._save_requests()
                return f"{act}了（{flag[:12]}）"
            if act == "删好友":
                if not tg.isdigit():
                    return "需要 QQ 号"
                data, err = await self._call(event, "delete_friend", user_id=int(tg))
                return err or f"删了 {tg}"
            if act == "备注":
                if not tg.isdigit() or not ex:
                    return "需要 QQ 号和备注名"
                data, err = await self._call(
                    event, "set_friend_remark", user_id=int(tg), remark=ex
                )
                return err or f"{tg} 的备注改成「{ex}」了"
            if act == "点赞":
                if not tg.isdigit():
                    return "需要 QQ 号"
                times = int(ex) if ex.isdigit() else 10
                data, err = await self._call(
                    event, "send_like", user_id=int(tg), times=times
                )
                return err or f"给 {tg} 点了 {times} 个赞"
            if act == "拍":
                if not tg.isdigit():
                    return "需要 QQ 号"
                data, err = await self._call(event, "friend_poke", user_id=int(tg))
                if err:
                    data, err = await self._call(event, "send_poke", user_id=int(tg))
                return err or f"拍了 {tg} 一下"
            if act == "资料":
                if not tg.isdigit():
                    return "需要 QQ 号"
                data, err = await self._call(
                    event, "get_stranger_info", user_id=int(tg), no_cache=True
                )
                if err:
                    return err
                d = data if isinstance(data, dict) else {}
                d = d.get("data", d)
                return (
                    f"{tg}：昵称 {d.get('nick') or d.get('nickname') or '?'}，"
                    f"{d.get('sex') or '?'}，{d.get('age') or '?'} 岁，"
                    f"签名：{d.get('longNick') or d.get('sign') or '（空）'}"
                )
            if act == "改昵称":
                if not ex:
                    return "要改成什么昵称？"
                data, err = await self._call(event, "set_qq_profile", nickname=ex)
                return err or f"昵称改成「{ex}」了"
        except Exception as e:
            logger.error("[qq_power] qq_do(%s) 异常: %s", act, e)
            return f"操作失败：{str(e)[:120]}"
        return f"不认识「{act}」这个操作"

    # ------------------------------------------------------------------ 命令（精确入口）

    def _load_requests(self) -> dict[str, dict]:
        if not self.req_path.exists():
            return {}
        try:
            return json.loads(self.req_path.read_text(encoding="utf-8")) or {}
        except Exception:
            return {}

    def _save_requests(self) -> None:
        try:
            self.req_path.write_text(
                json.dumps(self.requests, ensure_ascii=False, indent=1), encoding="utf-8"
            )
        except Exception as e:
            logger.warning("[qq_power] 申请缓存写入失败: %s", e)

    @staticmethod
    def _bot(event: AstrMessageEvent):
        bot = getattr(event, "bot", None)
        return bot if hasattr(bot, "call_action") else None

    @staticmethod
    def _args(event: AstrMessageEvent, cmd: str) -> list[str]:
        text = (event.get_message_str() or "").strip()
        text = text.lstrip("/／").strip()
        if text.startswith(cmd):
            text = text[len(cmd):].strip()
        return text.split()

    async def _call(self, event: AstrMessageEvent, action: str, **kwargs):
        bot = self._bot(event)
        if bot is None:
            return None, "当前平台不支持直接调 action"
        try:
            return await bot.call_action(action, **kwargs), ""
        except Exception as e:
            logger.error("[qq_power] %s 失败: %s", action, e)
            return None, f"{action} 失败：{str(e)[:160]}"

    async def _reply(self, event: AstrMessageEvent, text: str) -> None:
        await event.send(MessageChain([Plain(text[:1500])]))
        event.stop_event()

    # ------------------------------------------------------------------ 命令

    @filter.command("好友")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def cmd_friends(self, event: AstrMessageEvent):
        """列出好友。"""
        data, err = await self._call(event, "get_friend_list")
        if err:
            return await self._reply(event, err)
        items = data if isinstance(data, list) else (data or {}).get("data") or []
        if not items:
            return await self._reply(event, "好友列表空的？")
        lines = []
        for f in items[:40]:
            qq = f.get("user_id") or f.get("uin") or "?"
            name = f.get("remark") or f.get("nickname") or ""
            lines.append(f"{qq}  {name}")
        await self._reply(
            event, f"一共 {len(items)} 个好友，前 {len(lines)} 个：\n" + "\n".join(lines)
        )

    @filter.command("申请")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def cmd_requests(self, event: AstrMessageEvent):
        """看待处理的好友申请。"""
        data, err = await self._call(event, "get_doubt_friends_add_request")
        if err:
            data, err2 = await self._call(event, "get_group_ignore_add_request")
            if err2:
                return await self._reply(event, f"{err}；{err2}")
        items = data if isinstance(data, list) else (data or {}).get("data") or []
        if not items:
            cached = list(self.requests.values())
            if not cached:
                return await self._reply(event, "现在没有待处理的好友申请")
            items = cached
        lines = []
        for r in items[:20]:
            flag = r.get("flag") or r.get("request_id") or ""
            qq = r.get("user_id") or r.get("uin") or r.get("invitor_uin") or "?"
            msg = r.get("message") or r.get("msg") or ""
            if flag:
                self.requests[str(flag)] = {"flag": str(flag), "user_id": qq, "message": msg}
            lines.append(f"{qq}  {msg[:30]}  flag={flag[:16]}")
        self._save_requests()
        await self._reply(
            event,
            f"待处理 {len(items)} 条：\n"
            + "\n".join(lines)
            + "\n\n用 /同意 <flag> 或 /拒绝 <flag>",
        )

    @filter.command("同意")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def cmd_approve(self, event: AstrMessageEvent):
        """同意好友申请。"""
        args = self._args(event, "同意")
        if not args:
            return await self._reply(event, "用法：/同意 <flag> [备注]")
        flag = args[0]
        remark = " ".join(args[1:]) if len(args) > 1 else ""
        kwargs = {"flag": flag, "approve": True}
        if remark:
            kwargs["remark"] = remark
        data, err = await self._call(event, "set_friend_add_request", **kwargs)
        if err:
            return await self._reply(event, err)
        self.requests.pop(flag, None)
        self._save_requests()
        await self._reply(event, f"同意了（{flag[:16]}）")

    @filter.command("拒绝")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def cmd_reject(self, event: AstrMessageEvent):
        """拒绝好友申请。"""
        args = self._args(event, "拒绝")
        if not args:
            return await self._reply(event, "用法：/拒绝 <flag>")
        data, err = await self._call(
            event, "set_friend_add_request", flag=args[0], approve=False
        )
        if err:
            return await self._reply(event, err)
        self.requests.pop(args[0], None)
        self._save_requests()
        await self._reply(event, f"拒绝了（{args[0][:16]}）")

    @filter.command("删好友")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def cmd_delete_friend(self, event: AstrMessageEvent):
        """删除好友。"""
        args = self._args(event, "删好友")
        if not args or not args[0].isdigit():
            return await self._reply(event, "用法：/删好友 <QQ号>")
        data, err = await self._call(event, "delete_friend", user_id=int(args[0]))
        if err:
            return await self._reply(event, err)
        await self._reply(event, f"删了 {args[0]}")

    @filter.command("备注")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def cmd_remark(self, event: AstrMessageEvent):
        """改好友备注。"""
        args = self._args(event, "备注")
        if len(args) < 2 or not args[0].isdigit():
            return await self._reply(event, "用法：/备注 <QQ号> <备注>")
        data, err = await self._call(
            event, "set_friend_remark", user_id=int(args[0]), remark=" ".join(args[1:])
        )
        if err:
            return await self._reply(event, err)
        await self._reply(event, f"备注已改成：{' '.join(args[1:])}")

    @filter.command("点赞")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def cmd_like(self, event: AstrMessageEvent):
        """给好友名片点赞。"""
        args = self._args(event, "点赞")
        if not args or not args[0].isdigit():
            return await self._reply(event, "用法：/点赞 <QQ号> [次数]")
        times = int(args[1]) if len(args) > 1 and args[1].isdigit() else 10
        data, err = await self._call(event, "send_like", user_id=int(args[0]), times=times)
        if err:
            return await self._reply(event, err)
        await self._reply(event, f"给 {args[0]} 点了 {times} 个赞")

    @filter.command("拍")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def cmd_poke(self, event: AstrMessageEvent):
        """拍一拍。"""
        args = self._args(event, "拍")
        if not args or not args[0].isdigit():
            return await self._reply(event, "用法：/拍 <QQ号>")
        data, err = await self._call(event, "friend_poke", user_id=int(args[0]))
        if err:
            data, err = await self._call(event, "send_poke", user_id=int(args[0]))
            if err:
                return await self._reply(event, err)
        await self._reply(event, f"拍了 {args[0]} 一下")

    @filter.command("资料")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def cmd_stranger(self, event: AstrMessageEvent):
        """看陌生人资料。"""
        args = self._args(event, "资料")
        if not args or not args[0].isdigit():
            return await self._reply(event, "用法：/资料 <QQ号>")
        data, err = await self._call(
            event, "get_stranger_info", user_id=int(args[0]), no_cache=True
        )
        if err:
            return await self._reply(event, err)
        d = data if isinstance(data, dict) else {}
        d = d.get("data", d)
        await self._reply(
            event,
            f"QQ {args[0]}\n昵称：{d.get('nick') or d.get('nickname') or '?'}\n"
            f"性别：{d.get('sex') or '?'}  年龄：{d.get('age') or '?'}\n"
            f"签名：{d.get('longNick') or d.get('sign') or '（空）'}",
        )

    @filter.command("改昵称")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def cmd_nick(self, event: AstrMessageEvent):
        """改机器人自己的昵称。"""
        args = self._args(event, "改昵称")
        if not args:
            return await self._reply(event, "用法：/改昵称 <新昵称>")
        name = " ".join(args)
        data, err = await self._call(event, "set_qq_profile", nickname=name)
        if err:
            return await self._reply(event, err)
        await self._reply(event, f"昵称改成「{name}」了")

    @filter.command("放行")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def cmd_allow(self, event: AstrMessageEvent):
        """把某人加进白名单，他才能跟机器人说话。"""
        args = self._args(event, "放行")
        if not args or not args[0].isdigit():
            return await self._reply(
                event, "用法：/放行 <QQ号>\n只有白名单里的人跟机器人说话才有回应"
            )
        qq = args[0]
        entry = f"qq:FriendMessage:{qq}"
        try:
            cfg = self.context.get_config()
        except Exception as e:
            return await self._reply(event, f"读配置失败：{e}")
        ps = cfg.get("platform_settings") or {}
        wl = list(ps.get("id_whitelist") or [])
        if entry in wl:
            return await self._reply(event, f"{qq} 已经在白名单里了（共 {len(wl)} 人）")
        wl.append(entry)
        ps["id_whitelist"] = wl
        cfg["platform_settings"] = ps
        try:
            await cfg.save_config_async()
        except Exception as e:
            return await self._reply(event, f"写配置失败：{e}")
        await self._reply(
            event,
            f"放行了 {qq}，白名单现在 {len(wl)} 人：\n"
            + "\n".join(wl)
            + "\n（他跟机器人的记忆是分开存的，看不到你这边的内容）",
        )

    @filter.command("收回")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def cmd_revoke(self, event: AstrMessageEvent):
        """把某人从白名单拿掉。"""
        args = self._args(event, "收回")
        if not args:
            return await self._reply(event, "用法：/收回 <QQ号>")
        qq = args[0]
        entry = f"qq:FriendMessage:{qq}"
        try:
            cfg = self.context.get_config()
        except Exception as e:
            return await self._reply(event, f"读配置失败：{e}")
        ps = cfg.get("platform_settings") or {}
        wl = list(ps.get("id_whitelist") or [])
        if entry not in wl:
            return await self._reply(event, f"{qq} 本来就不在白名单里")
        wl.remove(entry)
        ps["id_whitelist"] = wl
        cfg["platform_settings"] = ps
        try:
            await cfg.save_config_async()
        except Exception as e:
            return await self._reply(event, f"写配置失败：{e}")
        await self._reply(event, f"收回了 {qq}，白名单还剩 {len(wl)} 人")

    @filter.command("白名单")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def cmd_whitelist(self, event: AstrMessageEvent):
        """看谁能跟机器人说话。"""
        try:
            cfg = self.context.get_config()
        except Exception as e:
            return await self._reply(event, f"读配置失败：{e}")
        ps = cfg.get("platform_settings") or {}
        wl = list(ps.get("id_whitelist") or [])
        enabled = ps.get("enable_id_white_list")
        head = "白名单已开启\n" if enabled else "白名单已关闭（任何人都能对话）\n"
        await self._reply(
            event,
            head + (f"当前 {len(wl)} 人：\n" + "\n".join(wl) if wl else "名单是空的"),
        )

    @filter.command("我的资料")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def cmd_me(self, event: AstrMessageEvent):
        """看机器人自己的资料。"""
        data, err = await self._call(event, "get_login_info")
        if err:
            return await self._reply(event, err)
        d = data if isinstance(data, dict) else {}
        d = d.get("data", d)
        await self._reply(
            event, f"登录号：{d.get('user_id') or d.get('uin') or '?'}\n昵称：{d.get('nickname') or '?'}"
        )
