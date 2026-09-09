"""Google News RSS per query and locale: news.google.com/rss/search?q=...&hl=..&gl=..&ceid=.."""
from __future__ import annotations

import logging
from urllib.parse import quote

import feedparser

from ...util import parse_dt, since_dt
from ..base import feed_client

log = logging.getLogger("scout.news.google")
NAME = "google_news"


def build_url(query: str, lang: str, country: str, ceid: str | None = None) -> str:
    hl = lang if lang == "en" and country == "US" else (f"{lang}-{country}" if lang == "en" else lang)
    ceid = ceid or f"{country}:{lang}"
    return f"https://news.google.com/rss/search?q={quote(query)}&hl={hl}&gl={country}&ceid={ceid}"


def plan(cfg: dict) -> list[tuple[str, str, str, str, str]]:
    """(url, feed_name, language, domain_hint, query) for every locale x query."""
    g = cfg.get("news", {}).get("google_news", {})
    out = []
    for loc in g.get("locales", []):
        lang, country = loc.get("lang", "en"), loc.get("gl", "US")
        for q in g.get("queries", {}).get(lang, []) or []:
            query, domain = (q, None) if isinstance(q, str) else (q.get("q"), q.get("domain"))
            if query:
                out.append((build_url(query, lang, country, loc.get("ceid")), f"google_news:{lang}-{country}",
                            lang, domain, query))
    return out


def describe(since_days: int, cfg: dict) -> list[str]:
    return [f"google_news: {len(plan(cfg))} query x locale feeds, cap {cfg.get('news', {}).get('max_per_feed', 50)} each"]


def fetch(since_days: int, cfg: dict) -> list[dict]:
    cap = int(cfg.get("news", {}).get("max_per_feed", 50))
    since = since_dt(since_days)
    out: list[dict] = []
    with feed_client() as client:
        for url, feed_name, lang, domain, query in plan(cfg):
            try:
                r = client.get(url)
                r.raise_for_status()
            except Exception as e:  # noqa: BLE001
                log.warning("google_news: %r failed: %s", query, e)
                continue
            parsed = feedparser.parse(r.content)
            n = 0
            for e in parsed.entries:
                published = parse_dt(e.get("published") or e.get("updated"))
                if published and published < since:
                    continue
                link = e.get("link")
                if not link:
                    continue
                out.append({"feed": feed_name, "kind": "google_news", "title": e.get("title"), "url": link,
                            "published_at": published.isoformat() if published else None,
                            "summary_text": _strip(e.get("summary")), "language": lang, "domain_hint": domain})
                n += 1
                if n >= cap:
                    break
    log.info("google_news: %d articles", len(out))
    return out


def _strip(html: str | None) -> str:
    from ...util import clean_text
    return clean_text(html)[:1500]
