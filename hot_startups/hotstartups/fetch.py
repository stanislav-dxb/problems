"""Reading the web directly: feeds, list sites and company pages. No model involved here."""
from __future__ import annotations

import html as htmllib
import re
import time
from html.parser import HTMLParser
from pathlib import Path

import feedparser
import httpx

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36 hotstartups/0.1"
TIMEOUT = 25.0


class _Text(HTMLParser):
    """Plain text from HTML: drops scripts, styles, nav and footer; keeps link targets next to link text."""

    SKIP = {"script", "style", "noscript", "svg", "nav", "footer", "header", "form", "iframe"}
    BLOCK = {"p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "br", "section", "article", "td", "th", "dd", "dt"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip = 0
        self.href: str | None = None

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip += 1
        if tag == "a":
            self.href = dict(attrs).get("href")
        if tag in self.BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.skip:
            self.skip -= 1
        if tag == "a" and self.href and self.href.startswith("http") and not self.skip:
            self.parts.append(f" <{self.href}> ")
            self.href = None
        if tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.skip and data.strip():
            self.parts.append(data)

    def text(self) -> str:
        raw = "".join(self.parts)
        raw = re.sub(r"[ \t\r\f\v]+", " ", raw)
        raw = re.sub(r"\n\s*\n+", "\n", raw)
        return raw.strip()


def html_to_text(html: str) -> str:
    p = _Text()
    try:
        p.feed(html)
        p.close()
    except Exception:
        return htmllib.unescape(re.sub(r"<[^>]+>", " ", html))
    return p.text()


def title_of(html: str) -> str:
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I)
    return htmllib.unescape(re.sub(r"\s+", " ", m.group(1))).strip() if m else ""


def get(url: str, max_bytes: int = 1_500_000) -> tuple[bool, str, str]:
    """(ok, text_or_error, title). Follows redirects, one retry on transient errors."""
    last = ""
    for attempt in range(2):
        try:
            with httpx.Client(follow_redirects=True, timeout=TIMEOUT, headers={"User-Agent": UA, "Accept-Language": "en,*;q=0.5"}) as c:
                r = c.get(url)
            if r.status_code >= 400:
                return False, f"http {r.status_code}", ""
            body = r.text[:max_bytes]
            ctype = r.headers.get("content-type", "")
            if "html" in ctype or "<html" in body[:2000].lower():
                return True, html_to_text(body), title_of(body)
            return True, body, ""
        except httpx.HTTPError as e:
            last = f"{type(e).__name__}: {e}"
            time.sleep(1.5)
    return False, last or "unknown error", ""


def read_feed(url: str, limit: int = 60) -> tuple[bool, list[dict], str]:
    """Entries of an RSS/Atom feed as dicts (title, link, summary, date)."""
    try:
        with httpx.Client(follow_redirects=True, timeout=TIMEOUT, headers={"User-Agent": UA}) as c:
            r = c.get(url)
        if r.status_code >= 400:
            return False, [], f"http {r.status_code}"
        parsed = feedparser.parse(r.content)
    except httpx.HTTPError as e:
        return False, [], f"{type(e).__name__}: {e}"
    if getattr(parsed, "bozo", False) and not parsed.entries:
        return False, [], "not a feed"
    out = []
    for e in parsed.entries[:limit]:
        summary = e.get("summary") or (e.get("content") or [{}])[0].get("value", "") or ""
        summary = html_to_text(summary) if "<" in summary else summary
        date = e.get("published") or e.get("updated") or ""
        out.append({"title": (e.get("title") or "").strip(), "link": e.get("link") or "", "summary": summary.strip(), "date": date})
    return True, out, ""


def cache_text(cache_dir: Path, key: str, text: str) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    p = cache_dir / f"{key}.txt"
    p.write_text(text, encoding="utf-8")
    return p
