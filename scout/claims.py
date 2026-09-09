"""Reports stage: fetch reports, extract and chunk text, keyword-prefilter, type claims with rules."""
from __future__ import annotations

import logging
import sqlite3
from typing import Any

from . import db as dbm
from .extract import detect_geographies, extract_numbers, keyword_prefilter, match_taxonomy, sentence_with
from .sources.reports import fetchers
from .sources.reports.pdf import chunk_text, download_pdf, extract_text

log = logging.getLogger("scout.reports")

CLAIM_TAXONOMY: dict[str, dict[str, int]] = {
    "market_size": {"market size": 3, "valued at": 2, "market worth": 3, "market is worth": 3, "billion market": 3,
                    "addressable market": 3, "total market": 2, "spending on": 1, "annual spend": 2, "объем рынка": 3,
                    "объём рынка": 3, "рынок оценивается": 3, "حجم السوق": 3, "قيمة السوق": 3, "बाजार का आकार": 3,
                    "बाज़ार": 1},
    "growth_rate": {"cagr": 3, "grow at": 2, "growth rate": 2, "expected to grow": 3, "projected to grow": 3,
                    "annual growth": 2, "per year": 1, "year-on-year": 1, "темпы роста": 3, "вырастет": 2,
                    "рост на": 1, "معدل النمو": 3, "من المتوقع أن ينمو": 3, "विकास दर": 3, "बढ़ने की उम्मीद": 3},
    "structural_gap": {"gap": 2, "underserved": 3, "fragmented": 3, "lack of": 2, "no standard": 3, "inefficien": 3,
                       "manual": 2, "informal": 2, "unmet": 3, "shortage of": 2, "paper-based": 3, "bottleneck": 2,
                       "underdeveloped": 2, "пробел": 2, "фрагментирован": 3, "неэффективн": 3, "отсутств": 2,
                       "вручную": 2, "нехватк": 2, "فجوة": 3, "مجزأ": 3, "غير فعال": 3, "يدوي": 2, "نقص": 2,
                       "गैर-औपचारिक": 2, "कमी": 2, "अकुशल": 3, "खंडित": 3},
    "regulatory_change": {"regulation": 2, "reform": 2, "new law": 3, "mandate": 3, "compliance": 1, "policy change": 3,
                          "liberalis": 2, "liberaliz": 2, "регулирован": 2, "реформ": 2, "закон": 2, "تنظيم": 2,
                          "إصلاح": 2, "قانون": 2, "सुधार": 2, "नियम": 2},
    "technology_shift": {"adoption of": 2, "digitalisation": 3, "digitalization": 3, "digitisation": 3, "digitization": 3,
                         "automation": 2, "artificial intelligence": 2, "platform": 1, "cloud": 1, "e-commerce": 1,
                         "цифровизац": 3, "автоматизац": 2, "внедрени": 1, "رقمنة": 3, "التحول الرقمي": 3, "أتمتة": 2,
                         "डिजिटल": 2, "स्वचालन": 2},
    "incumbent_weakness": {"legacy": 2, "outdated": 3, "struggl": 2, "declin": 2, "losing share": 3, "loss of market share": 3,
                           "underperform": 2, "устарев": 3, "теряет долю": 3, "متقادم": 3, "يفقد حصته": 3, "पुराना": 2},
    "demand_shift": {"rising demand": 3, "growing demand": 3, "demand for": 2, "shift in demand": 3, "increasingly demand": 2,
                     "consumers increasingly": 2, "рост спроса": 3, "спрос на": 2, "ارتفاع الطلب": 3, "الطلب على": 2,
                     "बढ़ती मांग": 3, "मांग": 1},
}
ASK_WHOM = [
    (("construction", "contractor", "building", "infrastructure", "строит", "بناء", "निर्माण"), "a site manager or subcontractor"),
    (("logistic", "freight", "shipping", "supply chain", "warehouse", "логист", "شحن", "लॉजिस्टिक"), "a freight forwarder or dispatcher"),
    (("health", "clinic", "hospital", "pharma", "медиц", "صحة", "स्वास्थ्य"), "a clinic operations manager"),
    (("retail", "e-commerce", "ecommerce", "merchant", "seller", "ритейл", "تجزئة", "खुदरा"), "a store owner or marketplace seller"),
    (("payment", "bank", "credit", "lending", "fintech", "finance", "платеж", "банк", "دفع", "بنك", "भुगतान"), "a small-business accountant or finance manager"),
    (("agri", "farm", "food", "сельск", "زراع", "कृषि"), "a distributor or cooperative manager"),
    (("energy", "power", "solar", "oil", "gas", "энерг", "طاقة", "ऊर्जा"), "a plant or facilities manager"),
    (("labour", "labor", "workforce", "hiring", "recruit", "hr ", "кадр", "توظيف", "भर्ती"), "an HR manager at a mid-size company"),
    (("education", "school", "training", "образован", "تعليم", "शिक्षा"), "a school or training-centre administrator"),
    (("manufactur", "factory", "industrial", "производ", "تصنيع", "विनिर्माण"), "a plant manager or procurement lead"),
]


def ask_whom(text: str) -> str:
    low = (text or "").lower()
    for keys, who in ASK_WHOM:
        if any(k in low for k in keys):
            return who
    return "a distributor or operations manager in this sector"


_JUNK = ("journal of", "et al", "abbreviations", "table of contents", "note:", "source:", "figure ", "annex",
         "appendix", "references", "isbn", "doi:", "http", "public disclosure authorized", "hint :", "hint:",
         "box 1", "box 2", "box 3", "acknowledg", "this report was", "the findings, interpretations")


def looks_like_prose(summary: str, languages: tuple[str, ...] | list[str] = ("en", "ru", "ar", "hi")) -> bool:
    """Reject table rows, headers, survey questions, references and boilerplate, and text outside the
    configured languages. Needs enough words, few numeric tokens and mostly lower-case letters."""
    words = summary.split()
    if len(words) < 8 or summary.rstrip("…").rstrip().endswith("?"):
        return False
    numeric = sum(1 for w in words if any(ch.isdigit() for ch in w))
    if numeric / len(words) > 0.3:
        return False
    letters = [ch for ch in summary if ch.isalpha()]
    if letters and sum(1 for ch in letters if ch.isupper()) / len(letters) > 0.3:
        return False
    low = summary.lower()
    if any(j in low for j in _JUNK):
        return False
    if languages:
        from .util import detect_language
        lang = detect_language(summary)
        if lang != "und" and lang not in languages:
            return False
    return True


def classify_chunk(text: str, heading: str | None, prefilter: list[str], confidence: int,
                   domain_hint: str | None = None, languages: tuple[str, ...] | list[str] = ("en", "ru", "ar", "hi")) -> dict[str, Any] | None:
    """Rule-based claim extraction. Returns None when the chunk fails the prefilter, has no claim signal,
    or yields no prose-like claim sentence."""
    if not keyword_prefilter(text, prefilter):
        return None
    scores = match_taxonomy(text, CLAIM_TAXONOMY)
    if not scores:
        return None
    numbers = extract_numbers(text, max_items=10)
    has_money = any(n["kind"] == "money" for n in numbers)
    if has_money and "market_size" in scores:
        ctype = "market_size"
    else:
        ctype = max(scores, key=lambda k: (scores[k], k))
    summary = sentence_with(text, CLAIM_TAXONOMY[ctype]) or sentence_with(text, prefilter) or ""
    if not looks_like_prose(summary, languages):
        return None
    return {
        "heading": heading,
        "is_relevant": 1,
        "domain": domain_hint or (heading[:100] if heading else None),
        "claim_type": ctype,
        "claim_summary": summary[:400],
        "numbers": numbers,
        "geography": detect_geographies(text),
        "confidence_in_source": confidence,
        "chunk_excerpt": " ".join(text.split())[:1500],
    }


def process_report(conn: sqlite3.Connection, report_id: int, text: str, cfg: dict, confidence: int,
                   domain_hint: str | None = None) -> tuple[int, int]:
    rcfg = cfg.get("reports", {})
    prefilter = rcfg.get("prefilter_keywords", []) or []
    languages = tuple(rcfg.get("languages") or ("en", "ru", "ar", "hi"))
    chunks = chunk_text(text, int(rcfg.get("chunk_chars", 8000)))
    kept = 0
    for ch in chunks:
        row = classify_chunk(ch["text"], ch.get("heading"), prefilter, confidence, domain_hint, languages)
        if row is None:
            continue
        row.update({"report_id": report_id, "source_chunk_id": ch["chunk_id"]})
        dbm.insert_claim(conn, row)
        kept += 1
    dbm.update_report(conn, report_id, chunks_total=len(chunks), chunks_kept=kept)
    return len(chunks), kept


def run_reports(cfg: dict, conn: sqlite3.Connection, since_days: int) -> dict[str, Any]:
    rcfg = cfg.get("reports", {})
    data_dir = rcfg.get("data_dir", "data/reports")
    max_mb = int(rcfg.get("max_pdf_mb", 25))
    fetch_html = bool(rcfg.get("fetch_html", True))
    stats: dict[str, Any] = {"fetched": 0, "new": 0, "pdfs": 0, "chunks": 0, "claims": 0}
    run_id = dbm.start_run(conn, "reports")
    for mod in fetchers(cfg):
        try:
            reports = mod.fetch(since_days, cfg)
        except Exception as e:  # noqa: BLE001
            log.exception("reports %s failed: %s", mod.NAME, e)
            continue
        stats["fetched"] += len(reports)
        for rep in reports:
            if dbm.report_url_known(conn, rep["url"]):
                continue
            meta = rep.pop("meta", {}) or {}
            rid = dbm.insert_report(conn, rep)
            if rid is None:
                continue
            stats["new"] += 1
            text = rep.get("summary_text") or ""
            if rep.get("pdf_url"):
                path = download_pdf(rep["pdf_url"], data_dir, max_mb)
                if path is not None:
                    try:
                        text = extract_text(path) or text
                        stats["pdfs"] += 1
                        dbm.update_report(conn, rid, local_path=str(path))
                    except Exception as e:  # noqa: BLE001
                        log.info("pdf text failed for %s: %s", rep["url"], e)
            elif fetch_html and rep.get("source") == "rss":
                from .sources.news.text import article_text
                body = article_text(rep["url"], max_chars=60000)
                if body:
                    text = body
            total, kept = process_report(conn, rid, f"{rep.get('title') or ''}\n\n{text}", cfg,
                                         int(rep.get("confidence") or 3), meta.get("region") and None)
            stats["chunks"] += total
            stats["claims"] += kept
            conn.commit()
    if stats["chunks"]:
        stats["prefilter_skip_rate"] = round(1 - stats["claims"] / stats["chunks"], 2)
    dbm.finish_run(conn, run_id, notes=str(stats))
    conn.commit()
    log.info("reports: %s", stats)
    return stats


def reprocess_reports(cfg: dict, conn: sqlite3.Connection) -> dict[str, Any]:
    """Re-chunk and re-classify every stored report from its local PDF or stored summary (no fetching).
    Use after changing prefilter keywords or claim rules."""
    from pathlib import Path
    stats: dict[str, Any] = {"reports": 0, "chunks": 0, "claims": 0}
    run_id = dbm.start_run(conn, "reports-reprocess")
    conn.execute("DELETE FROM report_claims")
    for rep in conn.execute("SELECT * FROM reports ORDER BY id").fetchall():
        text = rep["summary_text"] or ""
        if rep["local_path"] and Path(rep["local_path"]).exists():
            try:
                text = extract_text(rep["local_path"]) or text
            except Exception as e:  # noqa: BLE001
                log.info("pdf text failed for %s: %s", rep["url"], e)
        total, kept = process_report(conn, rep["id"], f"{rep['title'] or ''}\n\n{text}", cfg,
                                     int(rep["confidence"] or 3))
        stats["reports"] += 1
        stats["chunks"] += total
        stats["claims"] += kept
        conn.commit()
    if stats["chunks"]:
        stats["prefilter_skip_rate"] = round(1 - stats["claims"] / stats["chunks"], 2)
    dbm.finish_run(conn, run_id, notes=str(stats))
    conn.commit()
    log.info("reports reprocess: %s", stats)
    return stats


def describe(since_days: int, cfg: dict) -> list[str]:
    lines = []
    for mod in fetchers(cfg):
        lines.extend(mod.describe(since_days, cfg))
    return lines
