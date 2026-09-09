"""News source family. Each fetcher returns article dicts:
{feed, kind, title, url, published_at, summary_text, language, domain_hint}."""
from __future__ import annotations

from importlib import import_module

NEWS_FETCHERS = ["google_news", "rss", "gdelt"]


def fetchers(cfg: dict) -> list:
    ncfg = cfg.get("news", {})
    mods = []
    for name in NEWS_FETCHERS:
        sub = ncfg.get(name, {}) if name != "rss" else {"enabled": bool(ncfg.get("feeds"))}
        if sub.get("enabled", True):
            mods.append(import_module(f"scout.sources.news.{name}"))
    return mods
