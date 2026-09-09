"""GDELT 2.0 DOC API (free, multilingual). One request per language, spaced >= 6 s apart because
GDELT throttles to one query every 5 seconds per client."""
from __future__ import annotations

import logging
import time
from urllib.parse import quote

from ...util import parse_dt, since_dt
from ..base import http_client

log = logging.getLogger("scout.news.gdelt")
NAME = "gdelt"
API = "https://api.gdeltproject.org/api/v2/doc/doc"
LANG = {"en": "english", "ru": "russian", "ar": "arabic", "hi": "hindi", "de": "german", "zh": "chinese"}


def build_query(phrases: list[str], lang: str) -> str:
    quoted = " OR ".join(f'"{p}"' for p in phrases if p)
    q = f"({quoted})" if len(phrases) > 1 else quoted
    return f"{q} sourcelang:{LANG.get(lang, lang)}"


def build_url(phrases: list[str], lang: str, since_days: int, max_records: int = 50) -> str:
    return (f"{API}?query={quote(build_query(phrases, lang))}&mode=artlist&format=json"
            f"&maxrecords={max_records}&timespan={max(1, since_days)}d&sort=datedesc")


def describe(since_days: int, cfg: dict) -> list[str]:
    g = cfg.get("news", {}).get("gdelt", {})
    return [f"gdelt: {len(g.get('phrases', {}))} language queries, {g.get('max_records', 50)} records each, "
            f"{g.get('spacing_seconds', 8)} s apart"]


def fetch(since_days: int, cfg: dict) -> list[dict]:
    g = cfg.get("news", {}).get("gdelt", {})
    spacing = float(g.get("spacing_seconds", 8))
    out: list[dict] = []
    with http_client(timeout=60) as client:
        first = True
        for lang, phrases in (g.get("phrases") or {}).items():
            if not phrases:
                continue
            if not first:
                time.sleep(spacing)
            first = False
            url = build_url(phrases, lang, since_days, int(g.get("max_records", 50)))
            try:
                r = client.get(url)
                data = r.json()
            except Exception as e:  # noqa: BLE001
                log.warning("gdelt %s: no JSON (throttled or error): %s", lang, str(e)[:80])
                continue
            for a in data.get("articles", []):
                dt = parse_dt(_gdelt_date(a.get("seendate")))
                out.append({"feed": f"gdelt:{lang}", "kind": "gdelt", "title": a.get("title"), "url": a.get("url"),
                            "published_at": dt.isoformat() if dt else None, "summary_text": "",
                            "language": lang, "domain_hint": None})
    log.info("gdelt: %d articles", len(out))
    return out


def _gdelt_date(s: str | None) -> str | None:
    # 20260908T113000Z -> 2026-09-08T11:30:00Z
    if not s or len(s) < 15:
        return None
    return f"{s[0:4]}-{s[4:6]}-{s[6:8]}T{s[9:11]}:{s[11:13]}:{s[13:15]}Z"
