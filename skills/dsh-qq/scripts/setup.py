#!/usr/bin/env python3
"""AstrBot 实例一键配置（幂等）。

做四件事，都可以单独跑：
  1. 安全加固：后台只绑回环、强口令、关电脑能力、开白名单
  2. 平台：写 OneBot v11 反向 WS（token 随机生成并复用）
  3. 模型：写 OpenAI 兼容 provider（DeepSeek 等）
  4. 语音：写 Edge TTS provider

前置：**必须先停 AstrBot**，否则改动会被运行中的实例覆盖。

用法：
    python setup.py --root D:\\AstrBot \\
        --umo "qq:FriendMessage:123456789" --uid 123456789 \\
        --api-key sk-xxx --model deepseek-chat \\
        --tts edge --voice zh-CN-XiaoyiNeural
"""

from __future__ import annotations

import argparse
import hashlib
import json
import secrets
import string
import sys
from pathlib import Path

PBKDF2_ITERATIONS = 600_000


# ---------------------------------------------------------------- 基础工具

def load_cfg(root: Path) -> tuple[dict, Path]:
    path = root / "data" / "cmd_config.json"
    if not path.exists():
        sys.exit(f"配置不存在：{path}（先跑一次 astrbot run 生成）")
    # AstrBot 写出的 JSON 带 UTF-8 BOM
    return json.loads(path.read_text(encoding="utf-8-sig")), path


def save_cfg(cfg: dict, path: Path) -> None:
    path.write_text(
        json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8-sig"
    )


def gen_password(n: int = 24) -> str:
    alphabet = string.ascii_letters + string.digits
    chars = [
        secrets.choice(string.ascii_uppercase),
        secrets.choice(string.ascii_lowercase),
        secrets.choice(string.digits),
        *(secrets.choice(alphabet) for _ in range(n - 3)),
    ]
    secrets.SystemRandom().shuffle(chars)
    return "".join(chars)


def pbkdf2_hash(pw: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", pw.encode(), bytes.fromhex(salt), PBKDF2_ITERATIONS
    ).hex()
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt}${digest}"


# ---------------------------------------------------------------- 1. 安全

def harden(cfg: dict, root: Path) -> str:
    dash = cfg.setdefault("dashboard", {})
    dash["host"] = "127.0.0.1"
    dash["port"] = int(dash.get("port", 6185))
    dash["enable"] = True
    dash["trust_proxy_headers"] = False

    # 口令：优先复用已有文件里的，避免每次重置
    secret_file = root / "secrets" / "dashboard.txt"
    pw = None
    if secret_file.exists():
        try:
            for line in secret_file.read_text(encoding="utf-8-sig").splitlines():
                if line.lower().startswith("password:"):
                    pw = line.split(":", 1)[1].strip()
        except OSError:
            pw = None
    if not pw:
        pw = gen_password()
        try:
            secret_file.parent.mkdir(parents=True, exist_ok=True)
            secret_file.write_text(
                f"url: http://127.0.0.1:{dash['port']}\n"
                f"username: {dash.get('username', 'astrbot')}\n"
                f"password: {pw}\n",
                encoding="utf-8",
            )
        except OSError as exc:
            print(f"  [warn] 凭据文件写不进去：{exc}")

    dash["pbkdf2_password"] = pbkdf2_hash(pw)
    dash["password"] = hashlib.md5(secrets.token_bytes(32)).hexdigest()
    dash["password_storage_upgraded"] = True
    dash["password_change_required"] = False

    ps = cfg.setdefault("provider_settings", {})
    ps["computer_use_runtime"] = "none"
    ps["computer_use_require_admin"] = True
    perms = ps.setdefault("computer_use_local_permissions", {})
    for role in ("member", "admin"):
        p = perms.setdefault(role, {})
        p["allow_execution"] = False
        p["allow_network"] = False
        p["filesystem_scope"] = "workspace"

    pls = cfg.setdefault("platform_settings", {})
    # 注意：真实字段名是 enable_id_white_list（带下划线）
    pls["enable_id_white_list"] = True
    pls["id_whitelist_log"] = True
    pls["no_permission_reply"] = True
    pls.setdefault("rate_limit", {"time": 60, "count": 30, "strategy": "stall"})

    return pw


# ---------------------------------------------------------------- 2. 平台

def ensure_platform(cfg: dict, root: Path, token: str | None) -> str:
    secret_file = root / "secrets" / "platform.json"
    stored = {}
    if secret_file.exists():
        try:
            stored = json.loads(secret_file.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            stored = {}

    tok = token or stored.get("ws_reverse_token") or secrets.token_hex(16)
    host = "127.0.0.1"
    port = 6199

    try:
        secret_file.parent.mkdir(parents=True, exist_ok=True)
        secret_file.write_text(
            json.dumps(
                {"ws_reverse_host": host, "ws_reverse_port": port, "ws_reverse_token": tok},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    except OSError as exc:
        print(f"  [warn] 平台凭据写不进去：{exc}")

    platforms = cfg.setdefault("platform", [])
    entry = next((p for p in platforms if p.get("type") == "aiocqhttp"), None)
    if entry is None:
        entry = {"id": "qq", "type": "aiocqhttp", "enable": True}
        platforms.append(entry)
    entry.update(
        {
            "id": entry.get("id") or "qq",
            "type": "aiocqhttp",
            "enable": True,
            "ws_reverse_host": host,
            "ws_reverse_port": port,
            "ws_reverse_token": tok,
        }
    )
    return tok


# ---------------------------------------------------------------- 3. 模型

def ensure_model(cfg: dict, api_key: str, model: str, api_base: str, pid: str) -> None:
    sources = cfg.setdefault("provider_sources", [])
    src = next((s for s in sources if s.get("id") == f"{pid}_source"), None)
    if src is None:
        src = {"id": f"{pid}_source"}
        sources.append(src)
    src.update(
        {
            "id": f"{pid}_source",
            "provider": pid,
            "type": "openai_chat_completion",
            "provider_type": "chat_completion",
            "key": [api_key],
            "api_base": api_base,
            "timeout": 120,
            "proxy": "",
            "custom_headers": {},
        }
    )

    providers = cfg.setdefault("provider", [])
    entry = next((p for p in providers if p.get("id") == pid), None)
    if entry is None:
        entry = {"id": pid}
        providers.append(entry)
    entry.update(
        {
            "id": pid,
            "enable": True,
            "model": model,
            "provider_source_id": f"{pid}_source",
            "modalities": [],
            "custom_extra_body": {},
        }
    )

    ps = cfg.setdefault("provider_settings", {})
    ps["default_image_caption_provider_id"] = pid
    model_cfg = (
        cfg.setdefault("agent_runner", {}).setdefault("config", {}).setdefault("model", {})
    )
    model_cfg["provider_id"] = pid


# ---------------------------------------------------------------- 4. 语音

def ensure_tts(cfg: dict, kind: str, voice: str, pid: str) -> None:
    providers = cfg.setdefault("provider", [])
    if kind == "edge":
        entry = next((p for p in providers if p.get("id") == pid), None)
        if entry is None:
            entry = {"id": pid}
            providers.append(entry)
        entry.update(
            {
                "id": pid,
                "provider": "microsoft",
                "type": "edge_tts",
                "provider_type": "text_to_speech",
                "enable": True,
                "voice": voice,
                "rate": "+0%",
                "volume": "+0%",
                "pitch": "+0Hz",
                "timeout": 20,
                "proxy": "",
            }
        )
    else:
        sys.exit(f"暂不支持的 tts 类型：{kind}")

    tts = cfg.setdefault("provider_tts_settings", {})
    tts["enable"] = True
    tts["provider_id"] = pid
    tts.setdefault("dual_output", False)
    tts.setdefault("use_file_service", False)
    tts.setdefault("trigger_probability", 0.33)


# ---------------------------------------------------------------- 5. 访问控制

def ensure_access(cfg: dict, umo: list[str], uid: list[str]) -> None:
    pls = cfg.setdefault("platform_settings", {})
    wl = pls.setdefault("id_whitelist", [])
    for u in umo:
        if u and u not in wl:
            wl.append(u)

    admins = cfg.get("admins_id") or []
    for a in uid:
        if a and a not in admins:
            admins.append(a)
    placeholders = {"astrbot", "机器人管理员", ""}
    cfg["admins_id"] = [a for a in admins if a not in placeholders]


# ---------------------------------------------------------------- main

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True, help="AstrBot 实例根目录")
    ap.add_argument("--umo", action="append", default=[], help="白名单 UMO，如 qq:FriendMessage:123")
    ap.add_argument("--uid", action="append", default=[], help="管理员 UID（裸 QQ 号）")
    ap.add_argument("--api-key", default="", help="模型 API Key")
    ap.add_argument("--model", default="deepseek-chat")
    ap.add_argument("--api-base", default="https://api.deepseek.com/v1")
    ap.add_argument("--provider-id", default="deepseek")
    ap.add_argument("--tts", choices=["edge", ""], default="", help="语音类型")
    ap.add_argument("--voice", default="zh-CN-XiaoyiNeural")
    ap.add_argument("--tts-id", default="edge-tts-main")
    ap.add_argument("--platform-token", default="", help="复用已有 token")
    args = ap.parse_args()

    root = Path(args.root).resolve()
    cfg, cfg_path = load_cfg(root)

    pw = harden(cfg, root)
    print(f"[安全] 后台 127.0.0.1 ；口令存放于 {root / 'secrets' / 'dashboard.txt'}")

    tok = ensure_platform(cfg, root, args.platform_token or None)
    print(f"[平台] OneBot v11 反向 WS -> ws://127.0.0.1:6199  token={tok[:8]}…")

    if args.api_key:
        ensure_model(cfg, args.api_key, args.model, args.api_base, args.provider_id)
        print(f"[模型] {args.provider_id} / {args.model}")

    if args.tts:
        ensure_tts(cfg, args.tts, args.voice, args.tts_id)
        print(f"[语音] {args.tts_id} / {args.voice}")

    if args.umo or args.uid:
        ensure_access(cfg, args.umo, args.uid)
        print(f"[访问] 白名单={cfg['platform_settings'].get('id_whitelist')}  管理员={cfg.get('admins_id')}")

    save_cfg(cfg, cfg_path)
    print(f"\n[ok] 已写入 {cfg_path}")
    print(f"[!] 请确保 AstrBot 已停止，现在启动它才会读到新配置")
    print(f"[!] 后台口令：{pw}")


if __name__ == "__main__":
    main()
