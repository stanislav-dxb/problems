"""Publisher insight feeds (consultancies, institutes, trade associations) configured in config.yaml."""
from __future__ import annotations

import logging

import feedparser

from ...util import clean_text, parse_dt, since_dt
from ..base import feed_client

log = logging.getLogger("scout.reports.rss")
NAME = "rss"


def describe(since_days: int, cfg: dict) -> list[str]:
    pubs = cfg.get("reports", {}).get("publishers", []) or []
    return [f"publisher feeds: {len(pubs)} ({', '.join(p['name'] for p in pubs[:8])})"]


def fetch(since_days: int, cfg: dict) -> list[dict]:
    rcfg = cfg.get("reports", {})
    cap = int(rcfg.get("max_per_publisher", 30))
    since = since_dt(since_days)
    out: list[dict] = []
    with feed_client() as client:
        for p in rcfg.get("publishers", []) or []:
            try:
                r = client.get(p["url"])
                r.raise_for_status()
            except Exception as e:  # noqa: BLE001
                log.warning("publisher %s: %s", p.get("name"), e)
                continue
            parsed = feedparser.parse(r.content)
            if not parsed.entries:
                log.info("publisher %s: no entries (blocked or empty)", p.get("name"))
            n = 0
            for e in parsed.entries:
                dt = parse_dt(e.get("published") or e.get("updated"))
                if dt and dt < since:
                    continue
                link = e.get("link")
                if not link:
                    continue
                pdf = link if link.lower().endswith(".pdf") else None
                for enc in e.get("enclosures", []) or []:
                    if "pdf" in (enc.get("type") or "") or (enc.get("href") or "").lower().endswith(".pdf"):
                        pdf = enc.get("href")
                out.append({"source": NAME, "publisher": p["name"], "title": clean_text(e.get("title")), "url": link,
                            "pdf_url": pdf, "published_at": dt.isoformat() if dt else None,
                            "summary_text": clean_text(e.get("summary"))[:3000], "language": p.get("language"),
                            "confidence": int(p.get("confidence", 3)), "meta": {"region": p.get("region")}})
                n += 1
                if n >= cap:
                    break
    log.info("publisher feeds: %d reports", len(out))
    return out
