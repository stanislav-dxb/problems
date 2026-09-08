"""Stats: counts per source, language, domain and week, plus pipeline state."""
from __future__ import annotations

import sqlite3
from typing import Any


def gather(conn: sqlite3.Connection) -> dict[str, Any]:
    q = conn.execute
    per_source = q("SELECT source, COUNT(*) AS n, MIN(created_at) AS first, MAX(created_at) AS last "
                   "FROM items GROUP BY source ORDER BY n DESC").fetchall()
    per_domain = q("SELECT domain, COUNT(*) AS n FROM problems WHERE is_problem = 1 GROUP BY domain "
                   "ORDER BY n DESC LIMIT 25").fetchall()
    per_week = q("SELECT strftime('%Y-W%W', created_at) AS week, COUNT(*) AS items, "
                 "SUM(CASE WHEN p.is_problem = 1 THEN 1 ELSE 0 END) AS problems FROM items i "
                 "LEFT JOIN problems p ON p.item_id = i.id WHERE created_at IS NOT NULL "
                 "GROUP BY week ORDER BY week DESC LIMIT 12").fetchall()
    per_lang = q("SELECT language, COUNT(*) AS n FROM items GROUP BY language ORDER BY n DESC LIMIT 12").fetchall()
    pipeline = {
        "items": q("SELECT COUNT(*) FROM items").fetchone()[0],
        "classified": q("SELECT COUNT(*) FROM problems").fetchone()[0],
        "problems": q("SELECT COUNT(*) FROM problems WHERE is_problem = 1").fetchone()[0],
        "clusters": q("SELECT COUNT(*) FROM clusters").fetchone()[0],
        "clusters_3plus": q("SELECT COUNT(*) FROM clusters WHERE item_count >= 3").fetchone()[0],
        "scored": q("SELECT COUNT(*) FROM scores").fetchone()[0],
    }
    runs = q("SELECT stage, started_at, finished_at, notes FROM runs ORDER BY id DESC LIMIT 8").fetchall()
    return {"per_source": [dict(r) for r in per_source], "per_domain": [dict(r) for r in per_domain],
            "per_week": [dict(r) for r in per_week], "per_lang": [dict(r) for r in per_lang],
            "pipeline": pipeline, "runs": [dict(r) for r in runs]}


def _table(rows: list[dict], cols: list[tuple[str, str]]) -> str:
    if not rows:
        return "  (none)"
    widths = [max(len(h), *(len(str(r.get(k, ""))) for r in rows)) for k, h in cols]
    lines = ["  " + "  ".join(h.ljust(w) for (k, h), w in zip(cols, widths)),
             "  " + "  ".join("-" * w for w in widths)]
    for r in rows:
        lines.append("  " + "  ".join(str(r.get(k, "")).ljust(w) for (k, h), w in zip(cols, widths)))
    return "\n".join(lines)


def render(s: dict[str, Any]) -> str:
    p = s["pipeline"]
    return "\n".join([
        "Items per source:",
        _table(s["per_source"], [("source", "source"), ("n", "items"), ("first", "oldest"), ("last", "newest")]),
        "", "Items per language:",
        _table(s["per_lang"], [("language", "lang"), ("n", "items")]),
        "", "Problems per domain (top 25):",
        _table(s["per_domain"], [("domain", "domain"), ("n", "problems")]),
        "", "Per week (by post date):",
        _table(s["per_week"], [("week", "week"), ("items", "items"), ("problems", "problems")]),
        "", "Pipeline:",
        f"  items={p['items']} classified={p['classified']} problems={p['problems']} "
        f"clusters={p['clusters']} (>=3 items: {p['clusters_3plus']}) scored={p['scored']}",
        "", "Recent runs:",
        _table(s["runs"], [("stage", "stage"), ("started_at", "started"), ("finished_at", "finished"), ("notes", "notes")]),
        "", "Cost: $0.00 — no external model calls; everything runs locally.",
    ])
