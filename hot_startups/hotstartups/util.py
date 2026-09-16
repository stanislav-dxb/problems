"""Small helpers shared by every module."""
from __future__ import annotations

import json
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

_STOP = {"inc", "ltd", "llc", "gmbh", "sa", "sas", "bv", "ab", "oy", "srl", "co", "corp", "the", "ag", "plc", "pte"}


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def days_between(iso_a: str | None, iso_b: str | None) -> int | None:
    """Whole days from iso_a to iso_b (dates or datetimes). None when either is missing."""
    if not iso_a or not iso_b:
        return None
    a = datetime.fromisoformat(iso_a[:10])
    b = datetime.fromisoformat(iso_b[:10])
    return (b - a).days


def slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return text or "x"


def normalize_name(name: str) -> str:
    """Company name to a comparison key: lowercase, no punctuation, no legal suffixes."""
    text = unicodedata.normalize("NFKD", name or "").lower()
    text = "".join(c for c in text if not unicodedata.combining(c))
    words = re.findall(r"[a-z0-9぀-ヿ㐀-鿿가-힯Ѐ-ӿ֐-ۿ]+", text)
    words = [w for w in words if w not in _STOP]
    return "".join(words)


def domain_of(url: str | None) -> str | None:
    """'https://www.example.co.uk/about' -> 'example.co.uk'. None for empty or non-http input."""
    if not url or not isinstance(url, str):
        return None
    if "://" not in url:
        url = "https://" + url
    try:
        host = urlsplit(url).hostname or ""
    except ValueError:
        return None
    host = host.lower().strip(".")
    for prefix in ("www.", "m.", "en."):
        if host.startswith(prefix):
            host = host[len(prefix):]
    return host or None


# Sites that are never a startup's own website (news, social, directories).
NOT_COMPANY_DOMAINS = {
    "linkedin.com", "twitter.com", "x.com", "facebook.com", "instagram.com", "youtube.com", "crunchbase.com",
    "techcrunch.com", "medium.com", "github.com", "producthunt.com", "wikipedia.org", "bloomberg.com",
    "reuters.com", "forbes.com", "sifted.eu", "tech.eu", "eu-startups.com", "techinasia.com", "inc42.com",
    "yourstory.com", "e27.co", "wamda.com", "techcabal.com", "startupi.com.br", "contxto.com", "prnewswire.com",
    "businesswire.com", "globenewswire.com", "apple.com", "google.com", "play.google.com", "ycombinator.com",
    "notion.site", "substack.com", "tiktok.com", "threads.net", "vivatech.com", "dealroom.co", "pitchbook.com",
}


def looks_like_company_site(url: str | None) -> bool:
    d = domain_of(url)
    if not d:
        return False
    return not any(d == bad or d.endswith("." + bad) for bad in NOT_COMPANY_DOMAINS)


def read_json(path: Path, default=None):
    if not path.exists():
        return default
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")
    tmp.replace(path)


def clip(text: str | None, limit: int) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"
