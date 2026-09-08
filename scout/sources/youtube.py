"""YouTube comments via the YouTube Data API v3 (API key).

Requires YOUTUBE_API_KEY and channel IDs in config. Recent uploads are read from the channel's
uploads playlist (1 quota unit per call instead of 100 for search.list), then top-level comment
threads are fetched per video. Author fields are dropped before storage.
"""
from __future__ import annotations

import logging

import httpx

from ..config import env
from ..util import all_query_terms, matches_any_term, parse_dt, since_dt
from .base import SourceUnavailable, http_client, long_enough, make_item, request_json

log = logging.getLogger("scout.sources.youtube")
NAME = "youtube"
API = "https://www.googleapis.com/youtube/v3"


def _channels(cfg: dict) -> list[tuple[str, str, str]]:
    out = []
    for industry, chans in (cfg["sources"]["youtube"].get("channels") or {}).items():
        for c in chans or []:
            if isinstance(c, dict) and c.get("id"):
                out.append((industry, str(c["id"]), str(c.get("name") or c["id"])))
            elif isinstance(c, str):
                out.append((industry, c, c))
    return out


def available(cfg: dict) -> tuple[bool, str]:
    if not env("YOUTUBE_API_KEY"):
        return False, "YOUTUBE_API_KEY not set"
    if not _channels(cfg):
        return False, "no channel IDs configured under sources.youtube.channels"
    return True, "Data API v3"


def describe(since_days: int, cfg: dict) -> list[str]:
    scfg = cfg["sources"]["youtube"]
    chans = _channels(cfg)
    return [f"youtube: {len(chans)} channels, {scfg.get('videos_per_channel', 5)} recent videos each, "
            f"{scfg.get('comments_per_video', 100)} comments per video"]


def uploads_playlist(channel_id: str) -> str:
    # The uploads playlist of channel UCxxxx is UUxxxx.
    return "UU" + channel_id[2:] if channel_id.startswith("UC") else channel_id


def fetch(since_days: int, cfg: dict) -> list[dict]:
    key = env("YOUTUBE_API_KEY")
    if not key:
        raise SourceUnavailable("YOUTUBE_API_KEY not set; skipping")
    scfg = cfg["sources"]["youtube"]
    vpc = int(scfg.get("videos_per_channel", 5))
    cpv = int(scfg.get("comments_per_video", 100))
    require_match = bool(scfg.get("require_query_match", True))
    min_chars = int(scfg.get("min_body_chars", 60))
    terms = all_query_terms(cfg)
    since = since_dt(since_days)
    out: list[dict] = []
    with http_client() as client:
        for industry, channel_id, channel_name in _channels(cfg):
            try:
                pl = request_json(client, "GET", f"{API}/playlistItems",
                                  params={"part": "snippet", "playlistId": uploads_playlist(channel_id),
                                          "maxResults": min(vpc, 50), "key": key}, sleep=0.2)
            except httpx.HTTPStatusError as e:
                if e.response.status_code in (401, 403):
                    raise SourceUnavailable(f"youtube: HTTP {e.response.status_code}: {e.response.text[:200]}") from e
                log.warning("youtube: channel %s -> HTTP %d; skipping", channel_name, e.response.status_code)
                continue
            videos = []
            for it in pl.get("items", []):
                sn = it.get("snippet") or {}
                vid = ((sn.get("resourceId") or {}).get("videoId"))
                published = parse_dt(sn.get("publishedAt"))
                if vid and (published is None or published >= since):
                    videos.append((vid, sn.get("title") or "", published))
            for vid, vtitle, _pub in videos[:vpc]:
                try:
                    ct = request_json(client, "GET", f"{API}/commentThreads",
                                      params={"part": "snippet", "videoId": vid, "maxResults": min(cpv, 100),
                                              "order": "relevance", "textFormat": "plainText", "key": key},
                                      sleep=0.2)
                except httpx.HTTPStatusError as e:
                    log.info("youtube: comments for %s -> HTTP %d (disabled?); skipping", vid,
                             e.response.status_code)
                    continue
                for th in ct.get("items", []):
                    top = ((th.get("snippet") or {}).get("topLevelComment") or {}).get("snippet") or {}
                    body = top.get("textOriginal") or top.get("textDisplay") or ""
                    if not long_enough(body, min_chars):
                        continue
                    if require_match and not matches_any_term(body, terms):
                        continue
                    cid = th.get("id")
                    raw = {"video_id": vid, "video_title": vtitle, "channel_id": channel_id,
                           "channel_name": channel_name, "industry": industry,
                           "likeCount": top.get("likeCount"), "publishedAt": top.get("publishedAt")}
                    out.append(make_item(NAME, cid, f"https://www.youtube.com/watch?v={vid}&lc={cid}",
                                         vtitle, body, top.get("authorChannelId", {}).get("value")
                                         if isinstance(top.get("authorChannelId"), dict) else None,
                                         top.get("publishedAt"), raw))
    log.info("youtube: %d items", len(out))
    return out
