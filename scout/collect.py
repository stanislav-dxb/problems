"""Collect stage: run every enabled source, detect language, hash handles, dedup, store."""
from __future__ import annotations

import logging
import sqlite3
from typing import Any

from . import db as dbm
from .sources import enabled_sources
from .sources.base import SourceUnavailable
from .util import detect_language

log = logging.getLogger("scout.collect")


def dry_run_plan(cfg: dict, since_days: int, only: str | None = None) -> list[str]:
    lines: list[str] = []
    for mod in enabled_sources(cfg, only):
        ok, why = mod.available(cfg)
        status = "READY" if ok else f"SKIP ({why})"
        lines.append(f"[{mod.NAME}] {status}")
        if ok:
            lines.extend("    " + ln for ln in mod.describe(since_days, cfg))
    return lines


def collect(cfg: dict, conn: sqlite3.Connection, since_days: int, only: str | None = None) -> dict[str, dict[str, Any]]:
    results: dict[str, dict[str, Any]] = {}
    run_id = dbm.start_run(conn, "collect")
    for mod in enabled_sources(cfg, only):
        name = mod.NAME
        ok, why = mod.available(cfg)
        if not ok:
            log.info("%s: skipped — %s", name, why)
            results[name] = {"skipped": why}
            continue
        try:
            items = mod.fetch(since_days, cfg)
        except SourceUnavailable as e:
            log.warning("%s: unavailable — %s", name, e)
            results[name] = {"skipped": str(e)}
            continue
        except Exception as e:  # noqa: BLE001 - one source failing must not stop the others
            log.exception("%s: fetch failed: %s", name, e)
            results[name] = {"error": str(e)}
            continue
        for it in items:
            if not it.get("language"):
                it["language"] = detect_language(f"{it.get('title') or ''}\n{it.get('body') or ''}")
        inserted, dupes = dbm.insert_items(conn, items)
        conn.commit()
        results[name] = {"fetched": len(items), "inserted": inserted, "duplicates": dupes}
        log.info("%s: fetched=%d inserted=%d duplicates=%d", name, len(items), inserted, dupes)
    dbm.finish_run(conn, run_id, notes=str(results))
    conn.commit()
    return results
