"""News stage: fetch articles from every news fetcher and type catalysts with keyword rules."""
from __future__ import annotations

import logging
import sqlite3
from typing import Any

from . import db as dbm
from .extract import (detect_geographies, extract_numbers, future_year_horizon, match_taxonomy, matched_terms,
                      sentence_with)
from .sources.news import fetchers

log = logging.getLogger("scout.news")

CATALYST_TAXONOMY: dict[str, dict[str, int]] = {
    "regulation": {"regulation": 2, "regulator": 2, "new law": 3, "legislation": 2, "bill ": 1, "decree": 3,
                   "compliance": 1, "ban ": 2, "banned": 2, "licence": 1, "license requirement": 2, "rules for": 2,
                   "закон": 2, "регулятор": 2, "запрет": 2, "постановлени": 2, "лицензи": 1, "قانون": 2, "تنظيم": 2,
                   "حظر": 2, "لائحة": 2, "नियम": 2, "कानून": 2, "प्रतिबंध": 2, "विनियम": 2},
    "new_mandate": {"mandate": 3, "mandatory": 3, "must comply": 3, "required to": 2, "deadline": 2, "obligation": 2,
                    "e-invoicing": 2, "comes into force": 3, "takes effect": 2, "обязательн": 3, "вступает в силу": 3,
                    "إلزامي": 3, "يدخل حيز التنفيذ": 3, "अनिवार्य": 3, "लागू होगा": 2},
    "cost_collapse": {"price drop": 3, "prices fall": 3, "prices fell": 2, "cheaper": 2, "cost falls": 3, "cost drop": 3,
                      "halved": 2, "plunge": 2, "slump": 1, "подешев": 3, "снижение цен": 3, "падение цен": 3,
                      "انخفاض الأسعار": 3, "تراجع الأسعار": 2, "सस्ता": 2, "कीमतों में गिरावट": 3},
    "shortage": {"shortage": 3, "scarcity": 3, "supply crunch": 3, "backlog": 2, "delays": 1, "out of stock": 2,
                 "labour shortage": 3, "labor shortage": 3, "дефицит": 3, "нехватка": 3, "перебои": 2, "نقص": 3,
                 "شح": 2, "कमी": 3, "किल्लत": 3},
    "incumbent_exit": {"exits the market": 3, "exit the market": 3, "shuts down": 3, "shutting down": 3, "withdraws": 2,
                       "pulls out": 3, "discontinue": 2, "winding down": 3, "ceases operations": 3, "уходит с рынка": 3,
                       "закрывает": 2, "прекращает": 2, "ينسحب": 3, "يغلق": 2, "توقف": 1, "बंद कर": 2, "बाहर निकल": 2},
    "incumbent_failure": {"outage": 2, "data breach": 3, "recall": 2, "fined": 2, "fine of": 2, "lawsuit": 2,
                          "bankrupt": 3, "insolvency": 3, "collapse": 2, "layoffs": 1, "penalty": 1, "сбой": 2,
                          "утечка": 2, "штраф": 2, "банкрот": 3, "اختراق": 2, "غرامة": 2, "إفلاس": 3, "دعوى": 1,
                          "दिवालिया": 3, "जुर्माना": 2, "डेटा लीक": 2},
    "funding_signal": {"raises $": 3, "raised $": 3, "raises €": 3, "funding round": 3, "series a": 3, "series b": 3,
                       "series c": 2, "seed round": 3, "pre-seed": 2, "acquires": 2, "acquisition": 2, "valuation": 1,
                       "привлек": 3, "инвестиции": 2, "раунд": 2, "جمعت": 3, "تمويل": 2, "استحواذ": 2, "फंडिंग": 3,
                       "निवेश": 2, "अधिग्रहण": 2},
    "demographic": {"population": 2, "ageing": 2, "aging population": 3, "migration": 2, "workforce": 1,
                    "labour force": 2, "youth unemployment": 2, "urbanisation": 2, "urbanization": 2, "население": 2,
                    "миграц": 2, "старение": 2, "سكان": 2, "الهجرة": 2, "جनसंख्या": 2, "आबादी": 2},
    "geopolitical": {"sanctions": 3, "tariff": 3, "trade war": 3, "export controls": 3, "embargo": 3, "visa ": 1,
                     "санкци": 3, "пошлин": 3, "эмбарго": 3, "عقوبات": 3, "رسوم جمركية": 3, "حظر التصدير": 3,
                     "प्रतिबंध": 2, "टैरिफ": 3},
}
DEFAULT_HORIZON = {"funding_signal": "immediate", "incumbent_failure": "immediate", "shortage": "immediate",
                   "cost_collapse": "immediate", "incumbent_exit": "6_months", "regulation": "6_months",
                   "new_mandate": "6_months", "demographic": "3_plus_years", "geopolitical": "1_3_years"}


def classify_article(article: dict[str, Any], threshold: int = 2) -> dict[str, Any]:
    text = f"{article.get('title') or ''}\n{article.get('summary_text') or ''}"
    scores = match_taxonomy(text, CATALYST_TAXONOMY)
    numbers = extract_numbers(text, max_items=6)
    geos = detect_geographies(text)
    if not scores or max(scores.values()) < threshold:
        return {"is_catalyst": 0, "geography": geos, "numbers": numbers}
    ctype = max(scores, key=lambda k: (scores[k], k))
    strength = 1 + min(2, (scores[ctype] - threshold) // 2) + (1 if len(scores) > 1 else 0) + (1 if numbers else 0)
    if article.get("kind") == "regulatory":
        strength += 1
    strength = max(1, min(5, strength))
    return {
        "is_catalyst": 1,
        "catalyst_type": ctype,
        "domain": article.get("domain_hint") or None,
        "geography": geos,
        "summary": (article.get("title") or sentence_with(text, CATALYST_TAXONOMY[ctype]) or "")[:300],
        "affected_parties": None,  # needs a model; left empty in rule-based mode
        "time_horizon": future_year_horizon(text) or DEFAULT_HORIZON.get(ctype, "6_months"),
        "catalyst_strength": strength,
        "numbers": numbers,
        "evidence_terms": matched_terms(text, {ctype: CATALYST_TAXONOMY[ctype]}),
    }


def run_news(cfg: dict, conn: sqlite3.Connection, since_days: int) -> dict[str, Any]:
    ncfg = cfg.get("news", {})
    threshold = int(ncfg.get("catalyst_threshold", 2))
    full_text = bool(ncfg.get("fetch_full_text", False))
    stats: dict[str, Any] = {"fetched": 0, "new": 0, "catalysts": 0}
    run_id = dbm.start_run(conn, "news")
    for mod in fetchers(cfg):
        try:
            articles = mod.fetch(since_days, cfg)
        except Exception as e:  # noqa: BLE001
            log.exception("news %s failed: %s", mod.NAME, e)
            continue
        stats["fetched"] += len(articles)
        for a in articles:
            aid = dbm.insert_article(conn, a)
            if aid is None:
                continue
            stats["new"] += 1
            if full_text:
                from .sources.news.text import article_text
                body = article_text(a["url"])
                if body:
                    a["summary_text"] = body
                    conn.execute("UPDATE news_articles SET summary_text = ? WHERE id = ?", (body[:6000], aid))
            row = classify_article(a, threshold)
            row["article_id"] = aid
            dbm.insert_catalyst(conn, row)
            stats["catalysts"] += row["is_catalyst"]
        conn.commit()
    dbm.finish_run(conn, run_id, notes=str(stats))
    conn.commit()
    log.info("news: fetched=%d new=%d catalysts=%d", stats["fetched"], stats["new"], stats["catalysts"])
    return stats


def describe(since_days: int, cfg: dict) -> list[str]:
    lines = []
    for mod in fetchers(cfg):
        lines.extend(mod.describe(since_days, cfg))
    return lines
