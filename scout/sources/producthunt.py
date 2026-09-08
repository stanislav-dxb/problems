"""Product Hunt via the official GraphQL API (developer token).

Requires PRODUCTHUNT_TOKEN. Pulls newest launches and their comments; comments often read
"I wish this did X" which is exactly the signal we want. Author fields are not requested.
"""
from __future__ import annotations

import logging

import httpx

from ..config import env
from ..util import iso, since_dt
from .base import SourceUnavailable, http_client, long_enough, make_item, request_json

log = logging.getLogger("scout.sources.producthunt")
NAME = "producthunt"
API = "https://api.producthunt.com/v2/api/graphql"

QUERY = """
query($first: Int!, $after: String, $postedAfter: DateTime, $comments: Int!) {
  posts(first: $first, after: $after, order: NEWEST, postedAfter: $postedAfter) {
    pageInfo { hasNextPage endCursor }
    edges { node {
      id name tagline description url createdAt votesCount commentsCount
      topics(first: 5) { edges { node { name } } }
      comments(first: $comments, order: VOTES) { edges { node { id body url createdAt votesCount } } }
    } }
  }
}
"""


def available(cfg: dict) -> tuple[bool, str]:
    if not env("PRODUCTHUNT_TOKEN"):
        return False, "PRODUCTHUNT_TOKEN not set"
    return True, "GraphQL API"


def describe(since_days: int, cfg: dict) -> list[str]:
    scfg = cfg["sources"]["producthunt"]
    return [f"producthunt: newest {scfg.get('posts_per_run', 60)} launches since {since_days}d with up to "
            f"{scfg.get('comments_per_post', 20)} comments each"]


def fetch(since_days: int, cfg: dict) -> list[dict]:
    token = env("PRODUCTHUNT_TOKEN")
    if not token:
        raise SourceUnavailable("PRODUCTHUNT_TOKEN not set; skipping")
    scfg = cfg["sources"]["producthunt"]
    want = int(scfg.get("posts_per_run", 60))
    cpp = int(scfg.get("comments_per_post", 20))
    min_chars = int(scfg.get("min_body_chars", 40))
    posted_after = iso(since_dt(since_days))
    out: list[dict] = []
    after = None
    got = 0
    with http_client(headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}) as client:
        while got < want:
            variables = {"first": min(20, want - got), "after": after, "postedAfter": posted_after,
                         "comments": cpp}
            try:
                data = request_json(client, "POST", API, json={"query": QUERY, "variables": variables},
                                    sleep=1.0)
            except httpx.HTTPStatusError as e:
                if e.response.status_code in (401, 403):
                    raise SourceUnavailable(f"producthunt: HTTP {e.response.status_code} (bad token?)") from e
                raise
            if data.get("errors"):
                log.warning("producthunt: GraphQL errors: %s", data["errors"][:3])
                break
            posts = (data.get("data") or {}).get("posts") or {}
            for edge in posts.get("edges", []):
                n = edge.get("node") or {}
                got += 1
                topics = [t["node"]["name"] for t in (n.get("topics") or {}).get("edges", []) if t.get("node")]
                body = "\n".join(x for x in (n.get("tagline"), n.get("description")) if x)
                raw = {"id": n.get("id"), "votesCount": n.get("votesCount"), "commentsCount": n.get("commentsCount"),
                       "topics": topics, "createdAt": n.get("createdAt")}
                if long_enough(body, min_chars):
                    out.append(make_item(NAME, f"post_{n.get('id')}", n.get("url"), n.get("name"), body,
                                         None, n.get("createdAt"), raw))
                for ce in (n.get("comments") or {}).get("edges", []):
                    c = ce.get("node") or {}
                    if not long_enough(c.get("body"), min_chars):
                        continue
                    craw = {"id": c.get("id"), "post_id": n.get("id"), "post_name": n.get("name"),
                            "votesCount": c.get("votesCount"), "topics": topics}
                    out.append(make_item(NAME, f"comment_{c.get('id')}", c.get("url") or n.get("url"),
                                         n.get("name"), c.get("body"), None, c.get("createdAt"), craw))
            info = posts.get("pageInfo") or {}
            if not info.get("hasNextPage"):
                break
            after = info.get("endCursor")
    log.info("producthunt: %d items", len(out))
    return out
