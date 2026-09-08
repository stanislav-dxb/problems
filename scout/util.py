"""Small shared helpers: hashing, language detection, text cleaning, time."""
from __future__ import annotations

import hashlib
import html
import logging
import os
import re
from datetime import datetime, timedelta, timezone

log = logging.getLogger("scout")

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t\r\f\v]+")
_NL_RE = re.compile(r"\n{3,}")


def setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        datefmt="%H:%M:%S")
    for noisy in ("httpx", "httpcore", "urllib3", "sentence_transformers", "transformers",
                  "huggingface_hub", "anthropic"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def since_dt(days: int) -> datetime:
    return now_utc() - timedelta(days=days)


def parse_dt(value) -> datetime | None:
    """Parse epoch seconds or ISO-8601 strings into aware UTC datetimes."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    s = str(value).strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        try:
            dt = datetime.fromtimestamp(float(s), tz=timezone.utc)
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def hash_handle(handle: str | None) -> str | None:
    """One-way hash of an author handle. Never store the raw handle."""
    if not handle:
        return None
    salt = os.environ.get("SCOUT_HASH_SALT", "scout-default-salt")
    return hashlib.sha256(f"{salt}:{handle.strip().lower()}".encode("utf-8")).hexdigest()[:16]


def clean_text(text: str | None) -> str:
    """Strip HTML tags, unescape entities and normalise whitespace."""
    if not text:
        return ""
    t = text.replace("<br>", "\n").replace("<br/>", "\n").replace("<br />", "\n")
    t = re.sub(r"</p>\s*<p>", "\n\n", t, flags=re.I)
    t = _TAG_RE.sub("", t)
    t = html.unescape(t)
    t = _WS_RE.sub(" ", t)
    t = _NL_RE.sub("\n\n", t)
    return t.strip()


def truncate_words(text: str, max_words: int) -> str:
    words = text.split()
    if len(words) <= max_words:
        return " ".join(words)
    return " ".join(words[:max_words]) + "…"


def detect_language(text: str) -> str:
    """Return an ISO-639-1 language code, or 'und' if detection fails."""
    sample = (text or "").strip()
    if len(sample) < 12:
        return "und"
    try:
        from langdetect import DetectorFactory, detect  # lazy import: slow to load
        DetectorFactory.seed = 0
        return detect(sample[:2000])
    except Exception:  # noqa: BLE001 - langdetect raises a generic exception
        return "und"


def matches_any_term(text: str, terms: list[str]) -> bool:
    low = (text or "").lower()
    return any(t.lower() in low for t in terms if t)


def all_query_terms(cfg: dict, languages: list[str] | None = None) -> list[str]:
    qt = cfg.get("query_terms", {}) or {}
    langs = languages or list(qt.keys())
    out: list[str] = []
    for lang in langs:
        out.extend(qt.get(lang, []) or [])
    return out
