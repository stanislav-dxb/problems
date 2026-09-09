"""Digest stage: render the ranked weekly Markdown digest. No model calls."""
from __future__ import annotations

import json
import logging
import re
import sqlite3
from datetime import timedelta
from pathlib import Path

from . import db as dbm
from .extract import is_corridor
from .util import all_query_terms, iso, now_utc, truncate_words

log = logging.getLogger("scout.digest")

NON_EN_LANGS = ("ru", "ar", "hi")
_SENT_RE = re.compile(r"(?<=[.!?؟।])\s+")


def pick_quote(body: str, terms: list[str], max_words: int) -> str:
    """Verbatim excerpt: the first sentence containing a query term, else the start of the body."""
    text = " ".join((body or "").split())
    if not text:
        return ""
    low_terms = [t.lower() for t in terms]
    for s in _SENT_RE.split(text):
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


def _lang_split(members: list[sqlite3.Row]) -> tuple[int, int]:
    en = other = 0
    for m in members:
        lang = (m["item_language"] or "und").lower()
        if lang == "en":
            en += 1
        elif lang in NON_EN_LANGS:
            other += 1
    return en, other


def _score_line(s: sqlite3.Row) -> str:
    parts = [f"{k} {s[k]:.2f}" for k in ("volume", "growth", "sources", "languages", "pain", "money", "demand",
                                          "workaround", "triangulation")]
    return " · ".join(parts)


def _geos(row: sqlite3.Row) -> list[str]:
    try:
        return json.loads(row["geography"] or "[]")
    except (ValueError, TypeError):
        return []


def build_digest(cfg: dict, conn: sqlite3.Connection, top_n: int | None = None, corridor: bool = False) -> str:
    dcfg = cfg.get("digest", {})
    top_n = top_n or int(dcfg.get("top_n", 10))
    quotes_n = int(dcfg.get("quotes_per_cluster", 3))
    quote_words = int(dcfg.get("quote_max_words", 25))
    rising_thr = float(dcfg.get("rising_growth_threshold", 2.0))
    min_size = int(cfg.get("scoring", {}).get("min_cluster_size", 3))
    terms = all_query_terms(cfg)
    now = now_utc()
    week_ago = iso(now - timedelta(days=7))

    items_week = conn.execute("SELECT COUNT(*) FROM items WHERE collected_at >= ?", (week_ago,)).fetchone()[0]
    items_total = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
    problems_total = conn.execute("SELECT COUNT(*) FROM problems WHERE is_problem = 1").fetchone()[0]
    clusters = list(conn.execute("SELECT * FROM clusters ORDER BY item_count DESC, id"))
    new_clusters = [c for c in clusters if (c["first_seen"] or "") >= week_ago]
    scores = dbm.scores_by_cluster(conn)
    evals = dbm.latest_evaluations(conn)
    # rank by the model evaluation when one exists, else by the evidence score
    ranked = sorted((c for c in clusters if c["id"] in scores or c["id"] in evals),
                    key=lambda c: (-(evals[c["id"]]["overall_score"] or 0) if c["id"] in evals else -1,
                                   -(scores[c["id"]]["overall_score"] or 0) if c["id"] in scores else 0, -c["item_count"]))
    fastest = sorted((c for c in clusters if c["item_count"] >= 3 and c["growth_30d"] is not None),
                     key=lambda c: -c["growth_30d"])[:3]

    tri = dbm.triangulations_by_cluster(conn)
    out: list[str] = [f"# Problem Scout digest — {now.date().isoformat()}", ""]
    if corridor:
        out.append("_Corridor view: report/news signals limited to items touching two or more of Gulf, "
                   "Russian-speaking, India, China, EU._")
        out.append("")
    out.append(f"- Run date: {iso(now)}")
    out.append(f"- Items collected this week: {items_week} (total stored: {items_total}; flagged as problems: {problems_total})")
    out.append(f"- Clusters: {len(clusters)} total, {len(new_clusters)} first seen this week, "
               f"{len(scores)} with ≥{min_size} items scored")
    if fastest:
        out.append("- Fastest-growing clusters (30d): " + "; ".join(
            f"{c['label']} ({_fmt_growth(c['growth_30d'])}, {c['item_count']} items)" for c in fastest))
    out.append("")

    out.append(f"## Top {min(top_n, len(ranked))} problems" + (" by evaluation score" if evals else " by evidence score"))
    out.append("")
    if evals:
        out.append("_Ranked by the model evaluation (criteria scores, triangulation, growth; weights in config.yaml → "
                   "evaluate.weights). The evidence score and signals are shown for each cluster too._")
    else:
        out.append("_Score 0–100 from volume, growth, source spread, language spread, pain, money, demand and "
                   "workaround signals (weights in config.yaml). No model judgement is involved; read the quotes._")
    out.append("")
    if not ranked:
        out.append(f"_No scored clusters yet. Run `scout classify`, `scout cluster`, `scout score` (clusters need ≥{min_size} items)._")
        out.append("")
    for rank, c in enumerate(ranked[:top_n], 1):
        s = scores.get(c["id"])
        e = evals.get(c["id"])
        members = dbm.cluster_members(conn, c["id"])
        head = f"evaluation {e['overall_score']:.0f}/100" if e else f"score {s['overall_score']:.0f}/100"
        out.append(f"### {rank}. {c['label']} — {head}")
        out.append("")
        out.append(f"**Representative post.** {c['canonical_summary']}")
        out.append("")
        out.append(f"**Evidence.** {c['item_count']} items ({_sources_str(c['sources'])}); domain: {c['domain'] or 'n/a'}; "
                   f"growth 30d: {_fmt_growth(c['growth_30d'])}; seen {str(c['first_seen'] or '')[:10]} → {str(c['last_seen'] or '')[:10]}.")
        out.append("")
        if s is not None:
            out.append(f"**Signals.** evidence score {s['overall_score']:.0f}/100 · {_score_line(s)}")
        if e is not None:
            out.append("")
            out.append(f"**Evaluation ({e['model'] or 'model'}).** Market size: {e['market_size_estimate'] or 'n/a'}. "
                       f"Source: {e['market_size_source'] or 'n/a'}")
            out.append("")
            out.append("| Criterion | Score |")
            out.append("|---|---|")
            out.append(f"| Path to $1B credibility | {e['path_to_1b_score'] or '-'} / 5 |")
            out.append(f"| Monopoly potential | {e['monopoly_potential'] or '-'} / 5 |")
            out.append(f"| Location-independent | {e['location_independent'] or '-'} / 5 |")
            out.append(f"| Runs without the founder | {e['runs_without_founder'] or '-'} / 5 |")
            out.append(f"| Capital-light | {e['capital_light'] or '-'} / 5 |")
            out.append(f"| Measurable in 90 days | {e['measurable_90d'] or '-'} / 5 |")
            out.append("")
            if e["market_size_reasoning"]:
                out.append(f"**Market arithmetic.** {e['market_size_reasoning']}")
                out.append("")
            out.append(f"**Path to $1B.** {e['path_to_1b'] or 'n/a'}")
            out.append("")
            out.append(f"**Why now.** {e['why_now'] or 'n/a'}")
            out.append("")
            out.append(f"**What would kill it.** {e['what_would_kill_it'] or 'n/a'}")
            out.append("")
            out.append(f"**Quickest test.** {e['quickest_test'] or 'n/a'}")
            out.append("")
            out.append(f"**Evidence gaps.** {e['evidence_gaps'] or 'n/a'}")
            if e["corridor_advantage"]:
                out.append("")
                out.append(f"**Corridor advantage.** {e['corridor_advantage']}")
        try:
            sols = json.loads(s["existing_solutions"] or "[]") if s is not None else []
        except ValueError:
            sols = []
        if sols:
            out.append("")
            out.append("**Tools people already use.** " + ", ".join(f"{n} ({k})" for n, k in sols[:8]))
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
        out.append("")

    # ---- Triangulated: pain + market + timing ----
    out.append("## Triangulated — pain, market and timing all present")
    out.append("")
    out.append("_Geometric mean of the three legs; a missing leg gives 0 by design. Market figures come from "
               "reports, catalysts from news; both are matched by embedding similarity, so read the sources._")
    out.append("")
    tri_rows = [(t, next((c for c in clusters if c["id"] == cid), None)) for cid, t in tri.items()]
    tri_rows = [(t, c) for t, c in tri_rows if c is not None and (t["triangulation_score"] or 0) > 0]
    if corridor:
        tri_rows = [(t, c) for t, c in tri_rows if is_corridor(_geos(t))]
    tri_rows.sort(key=lambda tc: -(tc[0]["triangulation_score"] or 0))
    if not tri_rows:
        out.append("_No cluster has all three legs yet. Run `scout news`, `scout reports`, `scout triangulate`; "
                   "see each cluster's evidence gap below._")
    for t, c in tri_rows[:top_n]:
        members = dbm.cluster_members(conn, c["id"])
        out.append(f"### {c['label']} — triangulation {t['triangulation_score']:.0f}/100 "
                   f"(pain {t['pain_evidence']:.2f} · market {t['market_evidence']:.2f} · timing {t['timing_evidence']:.2f})")
        out.append("")
        quoted = 0
        for m in members:
            q = pick_quote(m["body"], terms, quote_words)
            if q:
                out.append(f"> \"{q}\" — [{m['source']}]({m['url']})")
                quoted += 1
            if quoted >= 2:
                break
        out.append("")
        out.append(f"**Market figure.** {t['market_size_source'] or 'none matched'}")
        out.append("")
        out.append(f"**Catalysts.** {t['why_now'] or 'none matched'}")
        out.append("")
        if _geos(t):
            out.append(f"**Geographies in evidence.** {', '.join(_geos(t))}")
            out.append("")
    # evidence gaps for the top pain clusters
    gaps_lines = [f"- **{c['label']}** — {tri[c['id']]['evidence_gaps']}" for c in ranked[:5] if c["id"] in tri]
    if gaps_lines:
        out.append("**Evidence gaps for the top pain clusters.**")
        out.append("")
        out.extend(gaps_lines)
        out.append("")

    # ---- Hypotheses ----
    out.append("## Hypotheses — reports or news say it is broken, nobody is complaining publicly yet")
    out.append("")
    out.append("_UNVERIFIED. Each line is a single report claim or news catalyst with no matching pain cluster. "
               "Talk to the suggested person before believing it._")
    out.append("")
    hyps = list(conn.execute("SELECT * FROM hypotheses ORDER BY strength DESC, id"))
    if corridor:
        hyps = [h for h in hyps if is_corridor(_geos(h))]
    per_source: dict[str, int] = {}
    seen_h: set[str] = set()
    kept_h = []
    for h in hyps:  # at most two lines per report or article so one long PDF cannot fill the section
        key = " ".join((h["summary"] or "").lower().split())[:80]
        per_source[h["source_url"]] = per_source.get(h["source_url"], 0) + 1
        if per_source[h["source_url"]] <= 2 and key not in seen_h:
            seen_h.add(key)
            kept_h.append(h)
    hyps = kept_h
    if not hyps:
        out.append("_None yet._")
    for h in hyps[:15]:
        geo = f" [{', '.join(_geos(h))}]" if _geos(h) else ""
        out.append(f"- ({h['kind']}, strength {h['strength']}){geo} {h['summary']} — ask **{h['ask_whom']}**. "
                   f"[source]({h['source_url']})")
    out.append("")

    # ---- Catalyst watch ----
    out.append("## Catalyst watch — strongest news catalysts this week")
    out.append("")
    cats = [k for k in dbm.catalysts(conn, since=week_ago) if (k["catalyst_strength"] or 0) >= 2]
    if corridor:
        cats = [k for k in cats if is_corridor(_geos(k))]
    seen_titles: set[str] = set()
    uniq = []
    for k in cats:  # syndicated stories repeat across outlets; keep the first
        key = " ".join((k["summary"] or "").lower().split())[:60]
        if key in seen_titles:
            continue
        seen_titles.add(key)
        uniq.append(k)
    cats = uniq
    if not cats:
        out.append("_No catalysts this week. Run `scout news`._")
    for k in cats[:10]:
        geo = f" [{', '.join(_geos(k))}]" if _geos(k) else ""
        out.append(f"- **{k['catalyst_type']}** (strength {k['catalyst_strength']}, {k['time_horizon']}){geo} "
                   f"{k['summary']} — {k['feed']}, {(k['published_at'] or '')[:10]}. [link]({k['url']})")
    out.append("")

    out.append("## Cross-market gaps")
    out.append("")
    out.append("_Clusters heavy in English-language posts but thin in Russian/Arabic/Hindi ones, or the reverse._")
    out.append("")
    gaps: list[str] = []
    for c in clusters:
        if c["item_count"] < 3:
            continue
        en, other = _lang_split(dbm.cluster_members(conn, c["id"]))
        known = en + other
        if known < 3:
            continue
        if en / known >= 0.8 and other <= 1:
            gaps.append(f"- **{c['label']}** — {en} EN vs {other} RU/AR/HI items ({c['item_count']} total). "
                        "Heavy in English; check whether the same pain exists in your markets.")
        elif other / known >= 0.6:
            gaps.append(f"- **{c['label']}** — {other} RU/AR/HI vs {en} EN items ({c['item_count']} total). "
                        "Concentrated in non-English sources; a solution may already exist in the English market.")
    out.extend(gaps if gaps else ["_No clusters with enough language-tagged items yet._"])
    out.append("")

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
            sc = scores[c["id"]]["overall_score"] if c["id"] in scores else None
            out.append(f"- **{c['label']}** — {_fmt_growth(c['growth_30d'])}, {c['item_count']} items"
                       + (f", score {sc:.0f}" if sc is not None else "") + ".")
    else:
        out.append("_Nothing above the threshold yet (growth needs two 30-day windows of data)._")
    out.append("")
    out.append("---")
    out.append("_Generated by Problem Scout. Quotes are verbatim excerpts of public posts; author handles are hashed and never shown._")
    out.append("")
    return "\n".join(out)


def write_digest(cfg: dict, conn: sqlite3.Connection, top_n: int | None = None, out: str | None = None,
                 corridor: bool = False) -> Path:
    text = build_digest(cfg, conn, top_n, corridor)
    path = Path(out) if out else Path(cfg.get("digest", {}).get("output_dir", "digests")) / f"{now_utc().date().isoformat()}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    log.info("digest: wrote %s (%d bytes)", path, len(text.encode("utf-8")))
    return path
