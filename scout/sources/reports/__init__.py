"""Reports source family. Each fetcher returns report dicts:
{source, publisher, title, url, pdf_url, published_at, summary_text, language, confidence, html_text?}."""
from __future__ import annotations

from importlib import import_module

REPORT_FETCHERS = ["worldbank", "rss", "arxiv"]


def fetchers(cfg: dict) -> list:
    rcfg = cfg.get("reports", {})
    mods = []
    for name in REPORT_FETCHERS:
        sub = rcfg.get(name, {}) if name != "rss" else {"enabled": bool(rcfg.get("publishers"))}
        if sub.get("enabled", True):
            mods.append(import_module(f"scout.sources.reports.{name}"))
    return mods
