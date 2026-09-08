"""Shared helpers for source modules."""
from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from ..util import clean_text, hash_handle, iso, parse_dt

log = logging.getLogger("scout.sources")

USER_AGENT = "problem-scout/0.1 (+https://github.com/stanislav-dxb/problems; research tool, low volume)"

# Keys that may carry personal names. Removed recursively from raw_json before storage.
PII_KEYS = frozenset({
    "author", "by", "user", "username", "name", "author_fullname", "authordisplayname",
    "authorchannelid", "authorprofileimageurl", "authorchannelurl", "from_id", "sender", "sender_id",
    "display_name", "displayname", "email", "maker", "makers", "hunter", "post_author", "profile",
    "first_name", "last_name", "author_flair_text", "author_flair_richtext", "author_premium",
    "author_patreon_flair", "author_flair_css_class", "author_flair_type", "author_flair_template_id",
    "author_flair_background_color", "author_flair_text_color", "author_is_blocked", "author_cakeday",
})


class SourceUnavailable(Exception):
    """Raised when a source cannot be used legitimately (missing keys, no SDK, forbidden)."""


def scrub(value: Any) -> Any:
    """Recursively drop keys that could hold personal names from a raw payload."""
    if isinstance(value, dict):
        return {k: scrub(v) for k, v in value.items() if k.lower() not in PII_KEYS}
    if isinstance(value, list):
        return [scrub(v) for v in value]
    return value


def make_item(source: str, source_id: str, url: str | None, title: str | None, body: str | None,
              author: str | None, created_at: Any, raw: Any = None, language: str | None = None) -> dict:
    """Normalise a fetched record into the items row shape. Hashes the author handle here so
    the raw handle never leaves the source module."""
    dt = parse_dt(created_at)
    return {
        "source": source,
        "source_id": str(source_id),
        "url": url,
        "title": clean_text(title)[:500] if title else None,
        "body": clean_text(body),
        "author_handle": hash_handle(author),
        "language": language,
        "created_at": iso(dt),
        "raw_json": scrub(raw) if raw is not None else None,
    }


def http_client(timeout: float = 30.0, headers: dict | None = None) -> httpx.Client:
    h = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if headers:
        h.update(headers)
    return httpx.Client(timeout=timeout, headers=h, follow_redirects=True)


def request_json(client: httpx.Client, method: str, url: str, retries: int = 3, sleep: float = 0.0,
                 **kw: Any) -> Any:
    """HTTP request with polite retry on 429/5xx. Returns parsed JSON. Raises httpx.HTTPStatusError
    for non-retryable errors so callers can decide (e.g. 403 => skip)."""
    delay = 2.0
    for attempt in range(retries + 1):
        if sleep:
            time.sleep(sleep)
        try:
            r = client.request(method, url, **kw)
        except httpx.TransportError as e:
            if attempt >= retries:
                raise
            log.warning("%s %s: transport error (%s); retrying in %.0fs", method, url, e, delay)
            time.sleep(delay)
            delay *= 2
            continue
        if r.status_code == 429 or r.status_code >= 500:
            if attempt >= retries:
                r.raise_for_status()
            ra = r.headers.get("retry-after")
            wait = float(ra) if ra and ra.replace(".", "", 1).isdigit() else delay
            log.warning("%s %s: HTTP %d; retrying in %.0fs", method, url, r.status_code, wait)
            time.sleep(min(wait, 120))
            delay *= 2
            continue
        r.raise_for_status()
        return r.json()
    raise RuntimeError("unreachable")


def long_enough(text: str, min_chars: int) -> bool:
    return len((text or "").strip()) >= min_chars
