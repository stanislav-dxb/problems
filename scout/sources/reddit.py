"""Reddit via the official OAuth API (script app, password grant).

Requires REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET, REDDIT_USERNAME, REDDIT_PASSWORD and a
descriptive REDDIT_USER_AGENT. Without them the source is skipped; HTML is never scraped.
Rate limit: 100 requests/minute per OAuth client — we sleep between calls and back off on 429.
"""
from __future__ import annotations

import logging
import time

import httpx

from ..config import env
from ..util import all_query_terms, matches_any_term, since_dt
from .base import SourceUnavailable, http_client, long_enough, make_item, request_json

log = logging.getLogger("scout.sources.reddit")
NAME = "reddit"
TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
API = "https://oauth.reddit.com"
SLEEP = 0.7


def _creds() -> dict[str, str] | None:
    keys = ("REDDIT_CLIENT_ID", "REDDIT_CLIENT_SECRET", "REDDIT_USERNAME", "REDDIT_PASSWORD")
    vals = {k: env(k) for k in keys}
    if not all(vals.values()):
        return None
    vals["REDDIT_USER_AGENT"] = env("REDDIT_USER_AGENT", f"problem-scout/0.1 by u/{vals['REDDIT_USERNAME']}")
    return vals  # type: ignore[return-value]


def available(cfg: dict) -> tuple[bool, str]:
    if _creds() is None:
        return False, "REDDIT_CLIENT_ID/SECRET/USERNAME/PASSWORD not set"
    if not cfg["sources"]["reddit"].get("subreddits"):
        return False, "no subreddits configured"
    return True, "OAuth script app"


def describe(since_days: int, cfg: dict) -> list[str]:
    scfg = cfg["sources"]["reddit"]
    subs = scfg.get("subreddits") or []
    return [f"reddit: {len(subs)} subreddits, newest {scfg.get('posts_per_subreddit', 100)} posts each, "
            f"top-level comments on up to {scfg.get('max_posts_with_comments', 25)} matching posts each",
            f"reddit: subreddits = {subs}"]


def _token(client: httpx.Client, creds: dict[str, str]) -> str:
    r = client.post(TOKEN_URL, auth=(creds["REDDIT_CLIENT_ID"], creds["REDDIT_CLIENT_SECRET"]),
                    data={"grant_type": "password", "username": creds["REDDIT_USERNAME"],
                          "password": creds["REDDIT_PASSWORD"]},
                    headers={"User-Agent": creds["REDDIT_USER_AGENT"]})
    if r.status_code != 200:
        raise SourceUnavailable(f"reddit token request failed: HTTP {r.status_code} {r.text[:200]}")
    tok = r.json().get("access_token")
    if not tok:
        raise SourceUnavailable(f"reddit token response had no access_token: {r.text[:200]}")
    return tok


def fetch(since_days: int, cfg: dict) -> list[dict]:
    creds = _creds()
    if creds is None:
        raise SourceUnavailable("Reddit credentials not set; skipping (no HTML scraping)")
    scfg = cfg["sources"]["reddit"]
    subs = scfg.get("subreddits") or []
    per_sub = int(scfg.get("posts_per_subreddit", 100))
    cpp = int(scfg.get("comments_per_post", 20))
    max_cposts = int(scfg.get("max_posts_with_comments", 25))
    require_match = bool(scfg.get("require_query_match", True))
    min_chars = int(scfg.get("min_body_chars", 60))
    terms = all_query_terms(cfg)
    since = since_dt(since_days)
    out: list[dict] = []
    with http_client(headers={"User-Agent": creds["REDDIT_USER_AGENT"]}) as client:
        token = _token(client, creds)
        client.headers["Authorization"] = f"bearer {token}"
        for sub in subs:
            try:
                data = request_json(client, "GET", f"{API}/r/{sub}/new",
                                    params={"limit": min(per_sub, 100), "raw_json": 1}, sleep=SLEEP)
            except httpx.HTTPStatusError as e:
                log.warning("reddit: r/%s -> HTTP %d; skipping (private, banned or unreachable)",
                            sub, e.response.status_code)
                continue
            except Exception as e:  # noqa: BLE001
                log.warning("reddit: r/%s failed: %s", sub, e)
                continue
            posts = [c.get("data", {}) for c in (data.get("data") or {}).get("children", [])]
            matched_posts: list[dict] = []
            for p in posts:
                created = p.get("created_utc")
                if created and created < since.timestamp():
                    continue
                title, body = p.get("title") or "", p.get("selftext") or ""
                text = f"{title}\n{body}"
                is_match = matches_any_term(text, terms)
                if is_match or not require_match:
                    if long_enough(body, min_chars) or (is_match and long_enough(title, 20)):
                        out.append(_post_item(p, sub))
                if is_match and p.get("num_comments", 0) > 0:
                    matched_posts.append(p)
            for p in matched_posts[:max_cposts]:
                try:
                    thread = request_json(client, "GET", f"{API}/comments/{p['id']}",
                                          params={"depth": 1, "limit": cpp, "sort": "top", "raw_json": 1},
                                          sleep=SLEEP)
                except Exception as e:  # noqa: BLE001
                    log.warning("reddit: comments for %s failed: %s", p.get("id"), e)
                    continue
                if not isinstance(thread, list) or len(thread) < 2:
                    continue
                for c in (thread[1].get("data") or {}).get("children", []):
                    if c.get("kind") != "t1":
                        continue
                    d = c.get("data", {})
                    body = d.get("body") or ""
                    if body in ("[deleted]", "[removed]") or not long_enough(body, min_chars):
                        continue
                    if require_match and not matches_any_term(body, terms):
                        continue
                    out.append(_comment_item(d, p, sub))
    log.info("reddit: %d items", len(out))
    return out


def _post_item(p: dict, sub: str) -> dict:
    url = f"https://www.reddit.com{p.get('permalink', '')}"
    raw = {k: p.get(k) for k in ("id", "subreddit", "score", "num_comments", "created_utc",
                                  "link_flair_text", "over_18", "is_self", "url")}
    return make_item(NAME, p.get("name") or f"t3_{p.get('id')}", url, p.get("title"),
                     p.get("selftext") or "", p.get("author"), p.get("created_utc"), raw)


def _comment_item(c: dict, post: dict, sub: str) -> dict:
    url = f"https://www.reddit.com{c.get('permalink', '')}"
    raw = {"id": c.get("id"), "subreddit": sub, "score": c.get("score"), "created_utc": c.get("created_utc"),
           "post_id": post.get("id"), "post_title": post.get("title")}
    return make_item(NAME, c.get("name") or f"t1_{c.get('id')}", url, post.get("title"),
                     c.get("body") or "", c.get("author"), c.get("created_utc"), raw)
