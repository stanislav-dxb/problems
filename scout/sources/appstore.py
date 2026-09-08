"""Apple App Store customer reviews via the public RSS/JSON feed.

Feed: https://itunes.apple.com/{country}/rss/customerreviews/page={n}/id={app_id}/sortBy=mostRecent/json
Only low-rated reviews (rating <= max_rating) are kept; those carry the problem signal.
"""
from __future__ import annotations

import logging
import time

import httpx

from ..util import parse_dt, since_dt
from .base import http_client, long_enough, make_item, request_json

log = logging.getLogger("scout.sources.appstore")
NAME = "appstore"


def available(cfg: dict) -> tuple[bool, str]:
    apps = _apps(cfg)
    if not apps:
        return False, "no app IDs configured under sources.appstore.apps"
    return True, "public RSS"


def _apps(cfg: dict) -> list[tuple[str, int, str]]:
    out = []
    for category, apps in (cfg["sources"]["appstore"].get("apps") or {}).items():
        for a in apps or []:
            if isinstance(a, dict) and a.get("id"):
                out.append((category, int(a["id"]), str(a.get("name") or a["id"])))
            elif isinstance(a, int):
                out.append((category, a, str(a)))
    return out


def feed_url(country: str, app_id: int, page: int) -> str:
    return (f"https://itunes.apple.com/{country}/rss/customerreviews/page={page}/id={app_id}"
            f"/sortBy=mostRecent/json")


def describe(since_days: int, cfg: dict) -> list[str]:
    scfg = cfg["sources"]["appstore"]
    apps = _apps(cfg)
    countries = scfg.get("countries") or ["us"]
    pages = int(scfg.get("pages", 2))
    return [f"appstore: {len(apps)} apps x {len(countries)} countries x {pages} pages = "
            f"{len(apps) * len(countries) * pages} feed requests; keep rating <= {scfg.get('max_rating', 2)}"]


def fetch(since_days: int, cfg: dict) -> list[dict]:
    scfg = cfg["sources"]["appstore"]
    countries = scfg.get("countries") or ["us"]
    pages = int(scfg.get("pages", 2))
    max_rating = int(scfg.get("max_rating", 2))
    min_chars = int(scfg.get("min_body_chars", 40))
    since = since_dt(since_days)
    out: list[dict] = []
    seen: set[str] = set()
    with http_client() as client:
        for category, app_id, app_name in _apps(cfg):
            for country in countries:
                for page in range(1, pages + 1):
                    try:
                        data = request_json(client, "GET", feed_url(country, app_id, page), sleep=0.3)
                    except httpx.HTTPStatusError as e:
                        log.info("appstore: %s/%s page %d -> HTTP %d (no feed); skipping",
                                 app_name, country, page, e.response.status_code)
                        break
                    except Exception as e:  # noqa: BLE001
                        log.warning("appstore: %s/%s page %d failed: %s", app_name, country, page, e)
                        break
                    entries = (data.get("feed") or {}).get("entry") or []
                    if isinstance(entries, dict):
                        entries = [entries]
                    stop = False
                    for e in entries:
                        rating = _label(e.get("im:rating"))
                        if not rating:
                            continue  # app metadata entry, not a review
                        rid = _label(e.get("id"))
                        if not rid or rid in seen:
                            continue
                        updated = parse_dt(_label(e.get("updated")))
                        if updated and updated < since:
                            stop = True
                            continue
                        if int(rating) > max_rating:
                            continue
                        body = _label(e.get("content"))
                        if not long_enough(body, min_chars):
                            continue
                        seen.add(rid)
                        link = ((e.get("link") or {}).get("attributes") or {}).get("href")
                        url = link or f"https://apps.apple.com/{country}/app/id{app_id}?see-all=reviews"
                        raw = {"app_id": app_id, "app_name": app_name, "category": category,
                               "country": country, "rating": int(rating),
                               "version": _label(e.get("im:version")), "review_id": rid}
                        out.append(make_item(NAME, rid, url, f"{app_name}: {_label(e.get('title'))}",
                                             body, _label((e.get("author") or {}).get("name")),
                                             updated, raw))
                    if stop or len(entries) < 50:
                        break
                time.sleep(0.1)
    log.info("appstore: %d items", len(out))
    return out


def _label(v) -> str | None:
    if isinstance(v, dict):
        return v.get("label")
    return v
