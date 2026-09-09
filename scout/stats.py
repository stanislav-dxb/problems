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
    ct = q("SELECT COALESCE(SUM(chunks_total),0), COALESCE(SUM(chunks_kept),0) FROM reports").fetchone()
    families = {
        "reports": q("SELECT COUNT(*) FROM reports").fetchone()[0],
        "report_claims": q("SELECT COUNT(*) FROM report_claims").fetchone()[0],
        "report_chunks": ct[0], "chunks_kept": ct[1],
        "prefilter_skip_rate": round(1 - ct[1] / ct[0], 2) if ct[0] else None,
        "news_articles": q("SELECT COUNT(*) FROM news_articles").fetchone()[0],
        "news_catalysts": q("SELECT COUNT(*) FROM news_catalysts WHERE is_catalyst = 1").fetchone()[0],
        "triangulations": q("SELECT COUNT(*) FROM triangulations").fetchone()[0],
        "with_all_legs": q("SELECT COUNT(*) FROM triangulations WHERE triangulation_score > 0").fetchone()[0],
        "hypotheses": q("SELECT COUNT(*) FROM hypotheses").fetchone()[0],
    }
    calls = q("SELECT substr(called_at, 1, 10) AS day, stage, backend, model, COUNT(*) AS calls, "
              "SUM(CASE WHEN ok = 0 THEN 1 ELSE 0 END) AS failed, COALESCE(SUM(items), 0) AS items, "
              "ROUND(AVG(duration_ms) / 1000.0, 1) AS avg_s FROM llm_calls GROUP BY day, stage, backend, model "
              "ORDER BY day DESC, stage LIMIT 40").fetchall()
    evals = q("SELECT COUNT(*) FROM evaluations e JOIN clusters c ON c.id = e.cluster_id").fetchone()[0]
    per_family = q("SELECT 'news' AS family, feed AS name, COUNT(*) AS n FROM news_articles GROUP BY feed UNION ALL "
                   "SELECT 'reports', publisher, COUNT(*) FROM reports GROUP BY publisher ORDER BY family, n DESC").fetchall()
    return {"per_source": [dict(r) for r in per_source], "per_domain": [dict(r) for r in per_domain],
            "per_week": [dict(r) for r in per_week], "per_lang": [dict(r) for r in per_lang],
            "pipeline": pipeline, "runs": [dict(r) for r in runs], "families": families,
            "per_family": [dict(r) for r in per_family], "calls": [dict(r) for r in calls], "evaluations": evals}


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
        "", "Reports & news layer:",
        _table(s["per_family"], [("family", "family"), ("name", "feed / publisher"), ("n", "items")]),
        "  " + " ".join(f"{k}={v}" for k, v in s["families"].items()),
        "", "Recent runs:",
        _table(s["runs"], [("stage", "stage"), ("started_at", "started"), ("finished_at", "finished"), ("notes", "notes")]),
        "", f"Model calls per stage per day (evaluated clusters: {s['evaluations']}):",
        _table(s["calls"], [("day", "day"), ("stage", "stage"), ("backend", "backend"), ("model", "model"), ("calls", "calls"),
                            ("failed", "failed"), ("items", "items"), ("avg_s", "avg_s")]),
        "  claude_code calls draw on the Claude subscription quota (no per-token bill); item counts are logged because "
        "the CLI does not expose token counts. Reports and news never call a model.",
    ])
