"""Configured RSS feeds: trade press, regulatory announcements, funding news pages."""
from __future__ import annotations

import logging

import feedparser

from ...util import clean_text, parse_dt, since_dt
from ..base import feed_client

log = logging.getLogger("scout.news.rss")
NAME = "rss"


def describe(since_days: int, cfg: dict) -> list[str]:
    feeds = cfg.get("news", {}).get("feeds", []) or []
    return [f"news feeds: {len(feeds)} configured RSS feeds ({', '.join(f['name'] for f in feeds[:8])}{'…' if len(feeds) > 8 else ''})"]


def fetch(since_days: int, cfg: dict) -> list[dict]:
    ncfg = cfg.get("news", {})
    cap = int(ncfg.get("max_per_feed", 50))
    since = since_dt(since_days)
    out: list[dict] = []
    with feed_client() as client:
        for f in ncfg.get("feeds", []) or []:
            try:
                r = client.get(f["url"])
                r.raise_for_status()
            except Exception as e:  # noqa: BLE001
                log.warning("news feed %s: %s", f.get("name"), e)
                continue
            parsed = feedparser.parse(r.content)
            if not parsed.entries:
                log.info("news feed %s: no entries (blocked or empty)", f.get("name"))
            n = 0
            for e in parsed.entries:
                published = parse_dt(e.get("published") or e.get("updated"))
                if published and published < since:
                    continue
                link = e.get("link")
                if not link:
                    continue
                out.append({"feed": f["name"], "kind": f.get("kind", "press"), "title": e.get("title"), "url": link,
                            "published_at": published.isoformat() if published else None,
                            "summary_text": clean_text(e.get("summary"))[:1500], "language": f.get("language"),
                            "domain_hint": f.get("domain")})
                n += 1
                if n >= cap:
                    break
    log.info("news feeds: %d articles", len(out))
    return out
