"""Digest stage: render the ranked weekly Markdown digest. No model calls."""
from __future__ import annotations

import json
import logging
import re
import sqlite3
from datetime import timedelta
from pathlib import Path

from . import db as dbm
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
    parts = [f"{k} {s[k]:.2f}" for k in ("volume", "growth", "sources", "languages", "pain", "money", "demand", "workaround")]
    return " · ".join(parts)


def build_digest(cfg: dict, conn: sqlite3.Connection, top_n: int | None = None) -> str:
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
    ranked = sorted((c for c in clusters if c["id"] in scores),
                    key=lambda c: (-(scores[c["id"]]["overall_score"] or 0), -c["item_count"]))
    fastest = sorted((c for c in clusters if c["item_count"] >= 3 and c["growth_30d"] is not None),
                     key=lambda c: -c["growth_30d"])[:3]

    out: list[str] = [f"# Problem Scout digest — {now.date().isoformat()}", ""]
    out.append(f"- Run date: {iso(now)}")
    out.append(f"- Items collected this week: {items_week} (total stored: {items_total}; flagged as problems: {problems_total})")
    out.append(f"- Clusters: {len(clusters)} total, {len(new_clusters)} first seen this week, "
               f"{len(scores)} with ≥{min_size} items scored")
    if fastest:
        out.append("- Fastest-growing clusters (30d): " + "; ".join(
            f"{c['label']} ({_fmt_growth(c['growth_30d'])}, {c['item_count']} items)" for c in fastest))
    out.append("")

    out.append(f"## Top {min(top_n, len(ranked))} problems by evidence score")
    out.append("")
    out.append("_Score 0–100 from volume, growth, source spread, language spread, pain, money, demand and "
               "workaround signals (weights in config.yaml). No model judgement is involved; read the quotes._")
    out.append("")
    if not ranked:
        out.append(f"_No scored clusters yet. Run `scout classify`, `scout cluster`, `scout score` (clusters need ≥{min_size} items)._")
        out.append("")
    for rank, c in enumerate(ranked[:top_n], 1):
        s = scores[c["id"]]
        members = dbm.cluster_members(conn, c["id"])
        out.append(f"### {rank}. {c['label']} — score {s['overall_score']:.0f}/100")
        out.append("")
        out.append(f"**Representative post.** {c['canonical_summary']}")
        out.append("")
        out.append(f"**Evidence.** {c['item_count']} items ({_sources_str(c['sources'])}); domain: {c['domain'] or 'n/a'}; "
                   f"growth 30d: {_fmt_growth(c['growth_30d'])}; seen {str(c['first_seen'] or '')[:10]} → {str(c['last_seen'] or '')[:10]}.")
        out.append("")
        out.append(f"**Signals.** {_score_line(s)}")
        try:
            sols = json.loads(s["existing_solutions"] or "[]")
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


def write_digest(cfg: dict, conn: sqlite3.Connection, top_n: int | None = None, out: str | None = None) -> Path:
    text = build_digest(cfg, conn, top_n)
    path = Path(out) if out else Path(cfg.get("digest", {}).get("output_dir", "digests")) / f"{now_utc().date().isoformat()}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    log.info("digest: wrote %s (%d bytes)", path, len(text.encode("utf-8")))
    return path
