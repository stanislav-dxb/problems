"""Optional full-text extraction with trafilatura, honouring robots.txt. Off by default."""
from __future__ import annotations

import logging
from urllib import robotparser
from urllib.parse import urlsplit

from ..base import USER_AGENT, http_client

log = logging.getLogger("scout.news.text")
_robots: dict[str, robotparser.RobotFileParser | None] = {}


def allowed(url: str) -> bool:
    parts = urlsplit(url)
    host = f"{parts.scheme}://{parts.netloc}"
    rp = _robots.get(host)
    if rp is None and host not in _robots:
        rp = robotparser.RobotFileParser()
        try:
            rp.set_url(f"{host}/robots.txt")
            rp.read()
        except Exception:  # noqa: BLE001
            rp = None
        _robots[host] = rp
    return True if rp is None else rp.can_fetch(USER_AGENT, url)


def article_text(url: str, max_chars: int = 6000) -> str | None:
    try:
        import trafilatura  # lazy: optional dependency
    except ImportError:
        return None
    if not allowed(url):
        log.info("robots.txt disallows %s; keeping headline only", url)
        return None
    try:
        # Fetch with our own client (proxy/CA aware, shared User-Agent); trafilatura only parses.
        with http_client(timeout=30, headers={"Accept": "text/html,application/xhtml+xml,*/*;q=0.8"}) as client:
            r = client.get(url)
            if r.status_code != 200 or "html" not in (r.headers.get("content-type") or ""):
                return None
            html = r.text
        text = trafilatura.extract(html, url=url, include_comments=False, include_tables=False)
        return text[:max_chars] if text else None
    except Exception as e:  # noqa: BLE001
        log.info("text extraction failed for %s: %s", url, e)
        return None
