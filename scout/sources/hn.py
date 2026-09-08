"""Hacker News via the Algolia search API (no key required).

Docs: https://hn.algolia.com/api  — search_by_date returns newest first; we filter by
created_at_i and page through up to max_pages_per_query pages per (term, tag).
"""
from __future__ import annotations

import logging
import time

from ..util import all_query_terms, matches_any_term, since_dt
from .base import http_client, long_enough, make_item, request_json

log = logging.getLogger("scout.sources.hn")
NAME = "hn"
API = "https://hn.algolia.com/api/v1/search_by_date"


def available(cfg: dict) -> tuple[bool, str]:
    return True, "public API"


def _terms(cfg: dict) -> list[str]:
    scfg = cfg["sources"]["hn"]
    return all_query_terms(cfg, scfg.get("languages") or ["en"])


def describe(since_days: int, cfg: dict) -> list[str]:
    scfg = cfg["sources"]["hn"]
    terms = _terms(cfg)
    tags = scfg.get("tags") or ["comment", "story"]
    n = len(terms) * len(tags) * int(scfg.get("max_pages_per_query", 3))
    return [f"hn: {len(terms)} terms x {len(tags)} tags, up to {n} Algolia requests, since {since_days}d",
            f"hn: terms = {terms}"]


def fetch(since_days: int, cfg: dict) -> list[dict]:
    scfg = cfg["sources"]["hn"]
    terms = _terms(cfg)
    tags = scfg.get("tags") or ["comment", "story"]
    max_pages = int(scfg.get("max_pages_per_query", 3))
    min_chars = int(scfg.get("min_body_chars", 80))
    since_ts = int(since_dt(since_days).timestamp())
    seen: set[str] = set()
    out: list[dict] = []
    with http_client() as client:
        for tag in tags:
            for term in terms:
                for page in range(max_pages):
                    params = {"query": f'"{term}"', "tags": tag, "hitsPerPage": 100, "page": page,
                              "numericFilters": f"created_at_i>{since_ts}"}
                    try:
                        data = request_json(client, "GET", API, params=params, sleep=0.25)
                    except Exception as e:  # noqa: BLE001
                        log.warning("hn: query %r tag %s page %d failed: %s", term, tag, page, e)
                        break
                    hits = data.get("hits", [])
                    for h in hits:
                        oid = str(h.get("objectID"))
                        if oid in seen:
                            continue
                        item = _to_item(h, tag)
                        if item is None:
                            continue
                        text = f"{item['title'] or ''}\n{item['body']}"
                        if not long_enough(item["body"], min_chars):
                            continue
                        if not matches_any_term(text, terms):
                            continue
                        seen.add(oid)
                        out.append(item)
                    if len(hits) < 100 or page + 1 >= data.get("nbPages", 1):
                        break
                time.sleep(0.1)
    log.info("hn: %d items", len(out))
    return out


def _to_item(h: dict, tag: str) -> dict | None:
    oid = h.get("objectID")
    if not oid:
        return None
    url = f"https://news.ycombinator.com/item?id={oid}"
    if tag == "comment" or h.get("comment_text"):
        body = h.get("comment_text") or ""
        title = h.get("story_title") or ""
    else:
        body = h.get("story_text") or ""
        title = h.get("title") or ""
        if not body and h.get("url"):
            body = f"{title}\n{h.get('url')}"
    raw = {k: h.get(k) for k in ("objectID", "story_id", "story_title", "story_url", "points",
                                  "num_comments", "created_at", "created_at_i", "_tags", "parent_id")}
    return make_item(NAME, oid, url, title, body, h.get("author"), h.get("created_at_i"), raw)
