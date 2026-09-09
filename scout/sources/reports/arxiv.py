"""arXiv API (Atom): economics / finance working papers, abstract only."""
from __future__ import annotations

import logging

import feedparser

from ...util import clean_text, since_dt
from ..base import entry_date, feed_client

log = logging.getLogger("scout.reports.arxiv")
NAME = "arxiv"
API = "https://export.arxiv.org/api/query"


def describe(since_days: int, cfg: dict) -> list[str]:
    a = cfg.get("reports", {}).get("arxiv", {})
    return [f"arxiv: categories {a.get('categories', [])}, {a.get('max_results', 50)} newest abstracts"]


def fetch(since_days: int, cfg: dict) -> list[dict]:
    a = cfg.get("reports", {}).get("arxiv", {})
    cats = a.get("categories") or ["econ.GN"]
    since = since_dt(since_days)
    query = " OR ".join(f"cat:{c}" for c in cats)
    out: list[dict] = []
    with feed_client(timeout=60) as client:
        try:
            r = client.get(API, params={"search_query": query, "sortBy": "submittedDate", "sortOrder": "descending",
                                        "max_results": int(a.get("max_results", 50))})
            r.raise_for_status()
        except Exception as e:  # noqa: BLE001
            log.warning("arxiv: %s", e)
            return out
        for e in feedparser.parse(r.content).entries:
            dt = entry_date(e)
            if dt and dt < since:
                continue
            out.append({"source": NAME, "publisher": "arXiv", "title": clean_text(e.get("title")), "url": e.get("link"),
                        "pdf_url": None, "published_at": dt.isoformat() if dt else None,
                        "summary_text": clean_text(e.get("summary"))[:3000], "language": "en",
                        "confidence": int(a.get("confidence", 3)), "meta": {}})
    log.info("arxiv: %d abstracts", len(out))
    return out
