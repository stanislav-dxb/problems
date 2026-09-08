"""Source registry. Each source module exposes NAME, available(cfg), describe(since_days, cfg)
and fetch(since_days, cfg) -> list[dict]. See base.py for the item shape."""
from __future__ import annotations

from importlib import import_module
from types import ModuleType

SOURCE_NAMES: list[str] = ["hn", "reddit", "producthunt", "appstore", "youtube", "telegram", "adzuna"]


def load_source(name: str) -> ModuleType:
    if name not in SOURCE_NAMES:
        raise KeyError(f"unknown source {name!r}; known: {', '.join(SOURCE_NAMES)}")
    return import_module(f"scout.sources.{name}")


def enabled_sources(cfg: dict, only: str | None = None) -> list[ModuleType]:
    names = [only] if only else SOURCE_NAMES
    mods = []
    for n in names:
        scfg = (cfg.get("sources") or {}).get(n, {})
        if only or scfg.get("enabled", False):
            mods.append(load_source(n))
    return mods
