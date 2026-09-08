"""Stats: counts per source, domain and week, plus API usage and estimated daily cost."""
from __future__ import annotations

import sqlite3
from datetime import timedelta
from typing import Any

from .util import iso, now_utc


def gather(conn: sqlite3.Connection) -> dict[str, Any]:
    q = conn.execute
    now = now_utc()
    per_source = q("SELECT source, COUNT(*) AS n, MIN(created_at) AS first, MAX(created_at) AS last "
                   "FROM items GROUP BY source ORDER BY n DESC").fetchall()
    per_domain = q("SELECT domain, COUNT(*) AS n FROM problems WHERE is_problem = 1 GROUP BY domain "
                   "ORDER BY n DESC LIMIT 25").fetchall()
    per_week = q("SELECT strftime('%Y-W%W', created_at) AS week, COUNT(*) AS items, "
                 "SUM(CASE WHEN p.is_problem = 1 THEN 1 ELSE 0 END) AS problems FROM items i "
                 "LEFT JOIN problems p ON p.item_id = i.id WHERE created_at IS NOT NULL "
                 "GROUP BY week ORDER BY week DESC LIMIT 12").fetchall()
    per_lang = q("SELECT language, COUNT(*) AS n FROM items GROUP BY language ORDER BY n DESC LIMIT 12").fetchall()
    classification = {
        "items": q("SELECT COUNT(*) FROM items").fetchone()[0],
        "classified": q("SELECT COUNT(*) FROM problems").fetchone()[0],
        "problems": q("SELECT COUNT(*) FROM problems WHERE is_problem = 1").fetchone()[0],
        "failed": q("SELECT COUNT(*) FROM problems WHERE classification_failed = 1").fetchone()[0],
        "clusters": q("SELECT COUNT(*) FROM clusters").fetchone()[0],
        "clusters_5plus": q("SELECT COUNT(*) FROM clusters WHERE item_count >= 5").fetchone()[0],
        "evaluations": q("SELECT COUNT(DISTINCT cluster_id) FROM evaluations e JOIN clusters c ON c.id = e.cluster_id").fetchone()[0],
    }
    api_total = q("SELECT COUNT(*), COALESCE(SUM(input_tokens),0), COALESCE(SUM(output_tokens),0), "
                  "COALESCE(SUM(cache_read_tokens),0), COALESCE(SUM(cost_usd),0) FROM api_calls").fetchone()
    api_by_purpose = q("SELECT purpose, model, COUNT(*) AS calls, SUM(input_tokens) AS inp, SUM(output_tokens) AS outp, "
                       "SUM(cost_usd) AS cost, SUM(CASE WHEN ok = 0 THEN 1 ELSE 0 END) AS errors FROM api_calls "
                       "GROUP BY purpose, model ORDER BY cost DESC").fetchall()
    today = iso(now.replace(hour=0, minute=0, second=0))
    cost_today = q("SELECT COALESCE(SUM(cost_usd),0) FROM api_calls WHERE called_at >= ?", (today,)).fetchone()[0]
    week_ago = iso(now - timedelta(days=7))
    cost_7d = q("SELECT COALESCE(SUM(cost_usd),0) FROM api_calls WHERE called_at >= ?", (week_ago,)).fetchone()[0]
    active_days = q("SELECT COUNT(DISTINCT substr(called_at, 1, 10)) FROM api_calls").fetchone()[0]
    est_daily = (api_total[4] / active_days) if active_days else 0.0
    return {
        "per_source": [dict(r) for r in per_source], "per_domain": [dict(r) for r in per_domain],
        "per_week": [dict(r) for r in per_week], "per_lang": [dict(r) for r in per_lang],
        "classification": classification,
        "api": {"calls": api_total[0], "input_tokens": api_total[1], "output_tokens": api_total[2],
                "cache_read_tokens": api_total[3], "cost_usd": api_total[4], "cost_today": cost_today,
                "cost_7d": cost_7d, "active_days": active_days, "est_daily_cost": est_daily,
                "by_purpose": [dict(r) for r in api_by_purpose]},
    }


def _table(rows: list[dict], cols: list[tuple[str, str]]) -> str:
    if not rows:
        return "  (none)"
    widths = [max(len(h), *(len(str(r.get(k, ""))) for r in rows)) for k, h in cols]
    head = "  " + "  ".join(h.ljust(w) for (k, h), w in zip(cols, widths))
    lines = [head, "  " + "  ".join("-" * w for w in widths)]
    for r in rows:
        lines.append("  " + "  ".join(str(r.get(k, "")).ljust(w) for (k, h), w in zip(cols, widths)))
    return "\n".join(lines)


def render(s: dict[str, Any]) -> str:
    c, a = s["classification"], s["api"]
    parts = [
        "Items per source:",
        _table(s["per_source"], [("source", "source"), ("n", "items"), ("first", "oldest"), ("last", "newest")]),
        "", "Items per language:",
        _table(s["per_lang"], [("language", "lang"), ("n", "items")]),
        "", "Problems per domain (top 25):",
        _table(s["per_domain"], [("domain", "domain"), ("n", "problems")]),
        "", "Per week (by post date):",
        _table(s["per_week"], [("week", "week"), ("items", "items"), ("problems", "problems")]),
        "", "Pipeline:",
        f"  items={c['items']} classified={c['classified']} problems={c['problems']} failed={c['failed']} "
        f"clusters={c['clusters']} (>=5 items: {c['clusters_5plus']}) evaluated={c['evaluations']}",
        "", "Claude API usage:",
        f"  calls={a['calls']} input_tokens={a['input_tokens']} output_tokens={a['output_tokens']} "
        f"cache_read_tokens={a['cache_read_tokens']} total_cost=${a['cost_usd']:.4f}",
        f"  cost_today=${a['cost_today']:.4f} cost_last_7d=${a['cost_7d']:.4f} "
        f"estimated_daily_cost=${a['est_daily_cost']:.4f} (avg over {a['active_days']} active days)",
        _table(a["by_purpose"], [("purpose", "purpose"), ("model", "model"), ("calls", "calls"), ("inp", "in_tok"),
                                 ("outp", "out_tok"), ("cost", "cost_usd"), ("errors", "errors")]),
    ]
    return "\n".join(parts)
