"""Telegram public channels via Telethon with a user account. OFF by default.

Enable with sources.telegram.enabled: true, TELEGRAM_API_ID / TELEGRAM_API_HASH in .env and
`pip install telethon`. The first run is interactive (Telegram sends a login code) and creates a
local session file (TELEGRAM_SESSION, git-ignored). Only public channels listed in config are read.
"""
from __future__ import annotations

import logging

from ..config import env
from ..util import all_query_terms, matches_any_term, since_dt
from .base import SourceUnavailable, long_enough, make_item

log = logging.getLogger("scout.sources.telegram")
NAME = "telegram"


def available(cfg: dict) -> tuple[bool, str]:
    scfg = cfg["sources"]["telegram"]
    if not scfg.get("enabled"):
        return False, "disabled in config"
    if not (env("TELEGRAM_API_ID") and env("TELEGRAM_API_HASH")):
        return False, "TELEGRAM_API_ID/HASH not set"
    try:
        import telethon  # noqa: F401
    except ImportError:
        return False, "telethon not installed (pip install telethon)"
    if not scfg.get("channels"):
        return False, "no channels configured"
    return True, "Telethon user session"


def describe(since_days: int, cfg: dict) -> list[str]:
    scfg = cfg["sources"]["telegram"]
    return [f"telegram: {len(scfg.get('channels') or [])} channels, up to "
            f"{scfg.get('messages_per_channel', 200)} messages each since {since_days}d"]


def fetch(since_days: int, cfg: dict) -> list[dict]:
    ok, why = available(cfg)
    if not ok:
        raise SourceUnavailable(f"telegram: {why}")
    from telethon.sync import TelegramClient  # type: ignore

    scfg = cfg["sources"]["telegram"]
    per_channel = int(scfg.get("messages_per_channel", 200))
    require_match = bool(scfg.get("require_query_match", True))
    min_chars = int(scfg.get("min_body_chars", 60))
    terms = all_query_terms(cfg)
    since = since_dt(since_days)
    session = env("TELEGRAM_SESSION", "scout_telegram")
    out: list[dict] = []
    with TelegramClient(session, int(env("TELEGRAM_API_ID")), env("TELEGRAM_API_HASH")) as client:
        for channel in scfg.get("channels") or []:
            uname = str(channel).lstrip("@")
            try:
                msgs = client.iter_messages(uname, offset_date=since, reverse=True, limit=per_channel)
                for m in msgs:
                    text = m.text or ""
                    if not long_enough(text, min_chars):
                        continue
                    if require_match and not matches_any_term(text, terms):
                        continue
                    raw = {"channel": uname, "id": m.id, "date": m.date.isoformat() if m.date else None,
                           "views": getattr(m, "views", None), "forwards": getattr(m, "forwards", None)}
                    out.append(make_item(NAME, f"{uname}/{m.id}", f"https://t.me/{uname}/{m.id}",
                                         uname, text, str(m.sender_id) if m.sender_id else None,
                                         m.date, raw))
            except Exception as e:  # noqa: BLE001
                log.warning("telegram: channel %s failed: %s", uname, e)
    log.info("telegram: %d items", len(out))
    return out
