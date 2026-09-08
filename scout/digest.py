"""Digest stage: render the ranked weekly Markdown digest."""
from __future__ import annotations

import json
import logging
import re
import sqlite3
from collections import Counter
from datetime import timedelta
from pathlib import Path
from typing import Any

from . import db as dbm
from .util import all_query_terms, iso, now_utc, truncate_words

log = logging.getLogger("scout.digest")

NON_EN_LANGS = ("ru", "ar", "hi")
_SENT_RE = re.compile(r"(?<=[.!?])\s+")


def pick_quote(body: str, terms: list[str], max_words: int) -> str:
    """Verbatim excerpt: the first sentence containing a query term, else the start of the body."""
    text = " ".join((body or "").split())
    if not text:
        return ""
    sentences = _SENT_RE.split(text)
    low_terms = [t.lower() for t in terms]
    for s in sentences:
        if any(t in s.lower() for t in low_terms) and len(s.split()) >= 4:
            return truncate_words(s, max_words)
    return truncate_words(text, max_words)


def _fmt_growth(g: float | None) -> str:
    return f"{g:.1f}x" if g is not None else "n/a"


def _sources_str(sources_json: str | None) -> str:
    try:
        d = json.loads(sources_json or "{}")
    except ValueError:
        d = {}
    return ", ".join(f"{k} {v}" for k, v in sorted(d.items(), key=lambda kv: -kv[1])) or "none"


def _lang_split(members: list[sqlite3.Row]) -> tuple[int, int, int]:
    en = other = und = 0
    for m in members:
        lang = (m["item_language"] or "und").lower()
        if lang == "en":
            en += 1
        elif lang in NON_EN_LANGS:
            other += 1
        else:
            und += 1
    return en, other, und


def build_digest(cfg: dict, conn: sqlite3.Connection, top_n: int | None = None) -> str:
    dcfg = cfg.get("digest", {})
    top_n = top_n or int(dcfg.get("top_n", 10))
    quotes_n = int(dcfg.get("quotes_per_cluster", 3))
    quote_words = int(dcfg.get("quote_max_words", 25))
    rising_thr = float(dcfg.get("rising_growth_threshold", 2.0))
    min_size = int(cfg.get("evaluation", {}).get("min_cluster_size", 5))
    terms = all_query_terms(cfg)
    now = now_utc()
    week_ago = iso(now - timedelta(days=7))

    items_week = conn.execute("SELECT COUNT(*) FROM items WHERE collected_at >= ?", (week_ago,)).fetchone()[0]
    items_total = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
    problems_total = conn.execute("SELECT COUNT(*) FROM problems WHERE is_problem = 1").fetchone()[0]
    clusters = list(conn.execute("SELECT * FROM clusters ORDER BY item_count DESC, id"))
    new_clusters = [c for c in clusters if (c["first_seen"] or "") >= week_ago]
    evals = dbm.latest_evaluations(conn)
    stub = any("STUB" in (e["path_to_1b"] or "") for e in evals.values())
    cost = conn.execute("SELECT COALESCE(SUM(cost_usd), 0), COUNT(*) FROM api_calls WHERE called_at >= ?",
                        (week_ago,)).fetchone()

    ranked = sorted((c for c in clusters if c["id"] in evals),
                    key=lambda c: (-(evals[c["id"]]["overall_score"] or 0), -c["item_count"]))
    fastest = sorted((c for c in clusters if c["item_count"] >= 3 and c["growth_30d"] is not None),
                     key=lambda c: -c["growth_30d"])[:3]

    out: list[str] = []
    out.append(f"# Problem Scout digest — {now.date().isoformat()}")
    out.append("")
    if stub:
        out.append("> **STUB MODE** — evaluations below were produced by the offline stub (`SCOUT_LLM=stub`), "
                   "not by Claude. Numbers and text are placeholders; set `ANTHROPIC_API_KEY` and re-run.")
        out.append("")
    out.append(f"- Run date: {iso(now)}")
    out.append(f"- Items collected this week: {items_week} (total stored: {items_total}; classified as problems: {problems_total})")
    out.append(f"- Clusters: {len(clusters)} total, {len(new_clusters)} first seen this week, "
               f"{sum(1 for c in clusters if c['item_count'] >= min_size)} with ≥{min_size} items, {len(evals)} evaluated")
    if fastest:
        out.append("- Fastest-growing clusters (30d): " + "; ".join(
            f"{c['label']} ({_fmt_growth(c['growth_30d'])}, {c['item_count']} items)" for c in fastest))
    out.append(f"- Claude API this week: {int(cost[1])} calls, ≈${cost[0]:.2f}")
    out.append("")

    out.append(f"## Top {min(top_n, len(ranked))} problems by overall score")
    out.append("")
    if not ranked:
        out.append("_No evaluated clusters yet. Run `scout classify`, `scout cluster`, `scout evaluate` "
                   f"(clusters need ≥{min_size} items to be evaluated)._")
        out.append("")
    for rank, c in enumerate(ranked[:top_n], 1):
        e = evals[c["id"]]
        members = dbm.cluster_members(conn, c["id"])
        out.append(f"### {rank}. {c['label']} — score {e['overall_score']:.0f}/100")
        out.append("")
        out.append(f"**Summary.** {c['canonical_summary']}")
        out.append("")
        out.append(f"**Evidence.** {c['item_count']} items ({_sources_str(c['sources'])}); domain: {c['domain'] or 'n/a'}; "
                   f"growth 30d: {_fmt_growth(c['growth_30d'])}; seen {str(c['first_seen'] or '')[:10]} → {str(c['last_seen'] or '')[:10]}.")
        out.append("")
        quoted = 0
        seen_q: set[str] = set()
        for m in members:
            q = pick_quote(m["body"], terms, quote_words)
            if not q or q in seen_q:
                continue
            seen_q.add(q)
            out.append(f"> \"{q}\" — [{m['source']}]({m['url']})")
            quoted += 1
            if quoted >= quotes_n:
                break
        if quoted:
            out.append("")
        out.append("**Evaluation.** Market size: " + (e["market_size_estimate"] or "n/a") + ".")
        out.append("")
        out.append("| Criterion | Score |")
        out.append("|---|---|")
        out.append(f"| Path to $1B credibility | {e['path_to_1b_score'] or '-'} / 5 |")
        out.append(f"| Monopoly potential | {e['monopoly_potential'] or '-'} / 5 |")
        out.append(f"| Location-independent | {e['location_independent'] or '-'} / 5 |")
        out.append(f"| Capital-light | {e['capital_light'] or '-'} / 5 |")
        out.append(f"| Measurable in 90 days | {e['measurable_90d'] or '-'} / 5 |")
        out.append("")
        if e["market_size_reasoning"]:
            out.append(f"**Market arithmetic.** {e['market_size_reasoning']}")
            out.append("")
        out.append(f"**Path to $1B.** {e['path_to_1b'] or 'n/a'}")
        out.append("")
        out.append(f"**What would kill it.** {e['what_would_kill_it'] or 'n/a'}")
        out.append("")
        out.append(f"**Quickest test.** {e['quickest_test'] or 'n/a'}")
        out.append("")
        if e["cross_market_advantage"]:
            out.append(f"**Cross-market angle.** {e['cross_market_advantage']}")
            out.append("")

    # Cross-market gaps
    out.append("## Cross-market gaps")
    out.append("")
    out.append("_Clusters heavy in English-language sources but thin in Russian/Arabic/Hindi ones, or the reverse._")
    out.append("")
    gaps: list[str] = []
    for c in clusters:
        if c["item_count"] < 3:
            continue
        members = dbm.cluster_members(conn, c["id"])
        en, other, und = _lang_split(members)
        known = en + other
        if known < 3:
            continue
        if en / known >= 0.8 and other <= 1:
            gaps.append(f"- **{c['label']}** — {en} EN vs {other} RU/AR/HI items ({c['item_count']} total). "
                        f"Heavy in English; check whether the same pain exists in the founder's markets.")
        elif other / known >= 0.6:
            gaps.append(f"- **{c['label']}** — {other} RU/AR/HI vs {en} EN items ({c['item_count']} total). "
                        f"Concentrated in non-English sources; possible import of an existing English-market solution.")
    out.extend(gaps if gaps else ["_No clusters with enough language-tagged items yet._"])
    out.append("")

    # Rising
    out.append(f"## Rising (growth 30d > {rising_thr:g}x, regardless of score)")
    out.append("")
    oldest = conn.execute("SELECT MIN(created_at) FROM items").fetchone()[0]
    if oldest and oldest > iso(now - timedelta(days=30)):
        out.append("_Note: less than 30 days of history, so the previous window is empty and growth ratios are "
                   "inflated by the floored denominator. They become meaningful after ~60 days of collection._")
        out.append("")
    rising = [c for c in clusters if (c["growth_30d"] or 0) > rising_thr and c["item_count"] >= 2]
    rising.sort(key=lambda c: -(c["growth_30d"] or 0))
    if rising:
        for c in rising[:15]:
            score = evals[c["id"]]["overall_score"] if c["id"] in evals else None
            out.append(f"- **{c['label']}** — {_fmt_growth(c['growth_30d'])}, {c['item_count']} items"
                       + (f", score {score:.0f}" if score is not None else ", not evaluated") + ".")
    else:
        out.append("_Nothing above the threshold yet (growth needs two 30-day windows of data)._")
    out.append("")
    out.append("---")
    out.append("_Generated by Problem Scout. Quotes are verbatim excerpts of public posts; author handles are hashed and never shown._")
    out.append("")
    return "\n".join(out)


def write_digest(cfg: dict, conn: sqlite3.Connection, top_n: int | None = None, out: str | None = None) -> Path:
    text = build_digest(cfg, conn, top_n)
    if out:
        path = Path(out)
    else:
        path = Path(cfg.get("digest", {}).get("output_dir", "digests")) / f"{now_utc().date().isoformat()}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    log.info("digest: wrote %s (%d bytes)", path, len(text.encode("utf-8")))
    return path
