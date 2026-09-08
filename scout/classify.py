"""Classify stage: rule-based problem detection. No model calls, deterministic, multilingual.

Each item gets a signal score from problem phrases (EN/RU/AR/HI), first-person markers and source
metadata (a 1-2 star review is itself a signal), minus promotional markers. Items at or above
`classify.threshold` become problems. The summary is the first sentence carrying a signal phrase,
kept in its original language; the multilingual embedding model in the cluster stage handles the
cross-language grouping.
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
from typing import Any

from . import db as dbm
from .util import iso, now_utc, truncate_words

log = logging.getLogger("scout.classify")

# phrase -> weight. Lower-cased substring match.
PROBLEM_SIGNALS: dict[str, int] = {
    # English
    "is there a tool": 3, "is there any tool": 3, "is there a service": 3, "is there an app": 3,
    "why is there no": 3, "why isn't there": 3, "wish there was": 3, "wish there were": 3, "i wish": 2,
    "how do you handle": 2, "how do you deal with": 2, "how do you manage": 2, "what do you use for": 2,
    "anyone else struggle": 3, "struggle with": 2, "struggling": 2, "looking for a solution": 3,
    "looking for a tool": 3, "looking for a way": 2, "need a way to": 2, "no way to": 2, "no easy way": 3,
    "no good option": 3, "no good way": 3, "can't find a": 2, "cannot find a": 2, "pain in the": 3,
    "biggest headache": 3, "headache": 1, "nightmare": 2, "frustrat": 2, "annoying": 1, "tedious": 2,
    "time-consuming": 2, "time consuming": 2, "waste of time": 2, "wasting": 1, "hours every": 2,
    "hours a week": 2, "every single time": 1, "manually": 2, "by hand": 2, "spreadsheet": 1,
    "copy and paste": 2, "copy-paste": 2, "copy/paste": 2, "workaround": 2, "hack together": 2,
    "duct tape": 2, "error-prone": 2, "error prone": 2, "unreliable": 1, "bottleneck": 2,
    "would pay for": 3, "would happily pay": 3, "take my money": 3, "impossible to": 2, "doesn't work": 1,
    "does not work": 1, "keeps crashing": 2, "can't even": 1, "lost money": 2, "losing money": 2,
    "losing customers": 3, "any recommendations": 1, "recommend a": 1, "how do i": 1,
    # Russian
    "есть ли сервис": 3, "есть ли программа": 3, "есть ли инструмент": 3, "почему нет": 2,
    "как вы решаете": 2, "как вы справляетесь": 2, "вручную": 2, "таблиц": 1, "ищу решение": 3,
    "ищу сервис": 3, "костыль": 2, "бесит": 2, "неудобно": 2, "не работает": 1, "мучаюсь": 3,
    "мучаемся": 3, "приходится": 2, "нет нормального": 3, "посоветуйте": 2, "подскажите": 1,
    "проблема": 1, "головная боль": 3, "тратим": 1, "тратить время": 2, "руками": 2,
    # Arabic
    "هل يوجد": 3, "هل في": 2, "كيف تتعاملون مع": 2, "كيف تتعامل": 2, "يدويا": 2, "يدوياً": 2,
    "ابحث عن حل": 3, "أبحث عن حل": 3, "ابحث عن": 1, "مشكلة": 2, "لا يعمل": 1, "صعب": 1, "معاناة": 3,
    "اقتراح": 1, "نصيحة": 1, "ضياع وقت": 2, "مضيعة": 2,
    # Hindi / Hinglish
    "koi tool hai": 3, "koi app hai": 3, "koi solution": 3, "kaise karte ho": 2, "kaise karte hain": 2,
    "kaise manage": 2, "dikkat": 2, "pareshan": 2, "pareshani": 2, "nahi ho raha": 2, "nahi hota": 1,
    "manually karna": 3, "समस्या": 2, "मुश्किल": 2, "परेशान": 2, "नहीं हो रहा": 2, "कोई तरीका": 3,
    "कोई टूल": 3, "कैसे करते": 2,
}

PROMO_SIGNALS: dict[str, int] = {
    "show hn": 4, "launch hn": 4, "we just launched": 4, "we launched": 3, "i built": 3, "i made": 2,
    "we built": 3, "introducing": 3, "announcing": 3, "check out my": 3, "check out our": 3,
    "just released": 3, "now available": 2, "sign up": 1, "free trial": 2, "use code": 3, "discount": 1,
    "мы запустили": 4, "представляем": 3, "أطلقنا": 3, "hum ne launch": 3,
}

FIRST_PERSON = re.compile(r"\b(i|i'm|i've|we|we're|we've|my|our|me|us|я|мы|мне|нам|у меня|у нас|أنا|نحن|عندي|"
                          r"لدينا|main|hum|mera|hamara|मैं|हम|मेरा|हमारा)\b", re.I)

MONEY = re.compile(
    r"[$€£₹]\s?\d|\b\d[\d,.]*\s?(usd|aed|inr|rub|sar|eur|gbp|dollars?|rupees?|dirhams?|rubles?|riyals?|lakh|crore|k)\b|"
    r"\b(cost|costs|costing|expensive|paid|paying|fee|fees|revenue|budget|refund|charged|charge|invoice|"
    r"price|pricing|subscription|per month|a month|salary|payroll)\b|"
    r"рубл|деньги|дорого|платить|платим|стоит|бюджет|доход|زبائن|ريال|درهم|دولار|غالي|دفع|تكلفة|سعر|"
    r"paisa|paise|rupee|mehenga|mehnga|kharcha|पैसा|पैसे|महंगा|खर्च|रुपये", re.I)

WORKAROUND = re.compile(
    r"manually|by hand|spreadsheet|excel|google sheets?|airtable|notion|workaround|copy.?past|zapier|"
    r"my own script|wrote a script|hired someone|virtual assistant|whatsapp group|"
    r"вручную|руками|костыл|таблиц|эксел|excel|скрипт|يدويا|يدوياً|اكسل|إكسل|جدول|manually karna|khud se|"
    r"हाथ से|एक्सेल", re.I)

SOLUTION_REQUEST = re.compile(
    r"is there (a|an|any)|any (tool|app|service|software)|anyone know|recommend|looking for|wish there|"
    r"how do you (handle|deal|manage)|what do you use|need a (tool|way|service)|suggestions\??|"
    r"есть ли|посоветуйте|подскажите|ищу|порекомендуйте|هل يوجد|هل في|ابحث عن|أبحث عن|اقترح|نصيحة|"
    r"koi (tool|app|solution|tarika)|kaise|suggest|कोई (टूल|तरीका)|कैसे", re.I)

# Common tools people name as what they use today. Matched case-insensitively as whole words.
KNOWN_TOOLS = [
    "Excel", "Google Sheets", "Airtable", "Notion", "Zapier", "Make.com", "n8n", "QuickBooks", "Xero",
    "Zoho", "FreshBooks", "Wave", "Tally", "Vyapar", "Khatabook", "Shopify", "WooCommerce", "Magento",
    "Amazon", "Etsy", "Salesforce", "HubSpot", "Pipedrive", "Slack", "Trello", "Asana", "Jira",
    "Monday.com", "ClickUp", "Basecamp", "Odoo", "SAP", "Oracle", "NetSuite", "Procore", "Buildertrend",
    "Jobber", "Housecall Pro", "ServiceTitan", "Upwork", "Fiverr", "Deel", "Gusto", "Stripe", "PayPal",
    "Wise", "Payoneer", "Razorpay", "Paytm", "WhatsApp", "Telegram", "1C", "Bitrix24", "amoCRM",
    "Yandex", "Avito", "Careem", "Talabat", "Noon", "Zomato", "Swiggy", "Meesho", "Flipkart", "IndiaMART",
    "ShipStation", "Freightos", "Flexport", "Calendly", "Fresha", "Booksy", "Square", "Toast", "Xero",
    "Dropbox", "Google Drive", "Docusign", "Canva", "ChatGPT", "Claude", "Zendesk", "Intercom",
]
_TOOL_RE = re.compile(r"(?<![\w.])(" + "|".join(re.escape(t) for t in KNOWN_TOOLS) + r")(?![\w.])", re.I)
_TOOL_CANON = {t.lower(): t for t in KNOWN_TOOLS}

_SENT_RE = re.compile(r"(?<=[.!?؟।])\s+|\n+")
_URL_RE = re.compile(r"https?://\S+|www\.\S+")


def _clean(text: str) -> str:
    return " ".join(_URL_RE.sub(" ", text or "").split())


def signal_hits(text: str, table: dict[str, int]) -> list[tuple[str, int]]:
    low = text.lower()
    return [(p, w) for p, w in table.items() if p in low]


def signal_sentence(body: str, max_words: int = 40) -> str | None:
    """First sentence carrying a problem signal, in the original language."""
    text = _clean(body)
    for s in _SENT_RE.split(text):
        s = s.strip()
        if len(s.split()) >= 4 and signal_hits(s, PROBLEM_SIGNALS):
            return truncate_words(s, max_words)
    return None


def first_sentence(body: str, max_words: int = 30) -> str:
    text = _clean(body)
    parts = [x.strip() for x in _SENT_RE.split(text) if x.strip()]
    return truncate_words(parts[0], max_words) if parts else ""


def domain_for(row: sqlite3.Row) -> str:
    """Domain from source metadata: subreddit, app category, channel industry, job category."""
    try:
        raw = json.loads(row["raw_json"] or "{}")
    except (TypeError, ValueError):
        raw = {}
    src = row["source"]
    if src == "reddit":
        return f"r/{raw.get('subreddit')}" if raw.get("subreddit") else "reddit"
    if src == "appstore":
        return raw.get("category") or "app reviews"
    if src == "youtube":
        return raw.get("industry") or "youtube"
    if src == "adzuna":
        return raw.get("category") or "jobs"
    if src == "producthunt":
        topics = raw.get("topics") or []
        return topics[0] if topics else "producthunt"
    if src == "telegram":
        return f"tg/{raw.get('channel')}" if raw.get("channel") else "telegram"
    return "hn"


def classify_item(row: sqlite3.Row, threshold: int = 4) -> dict[str, Any]:
    title, body = row["title"] or "", row["body"] or ""
    text = f"{title}\n{body}"
    score = sum(w for _, w in signal_hits(text, PROBLEM_SIGNALS))
    score -= sum(w for _, w in signal_hits(text, PROMO_SIGNALS))
    if FIRST_PERSON.search(text):
        score += 1
    try:
        raw = json.loads(row["raw_json"] or "{}")
    except (TypeError, ValueError):
        raw = {}
    if row["source"] == "appstore" and raw.get("rating") in (1, 2):
        score += 2  # a low-star review is a complaint by construction
    money = bool(MONEY.search(text))
    workaround = bool(WORKAROUND.search(text))
    requested = bool(SOLUTION_REQUEST.search(text))
    tools = sorted({_TOOL_CANON[m.lower()] for m in _TOOL_RE.findall(text)})
    is_problem = score >= threshold
    summary = None
    pain = None
    if is_problem:
        summary = signal_sentence(body)
        if not summary and row["source"] == "appstore":
            # "App: review title" -> the complaint itself plus the opening sentence, so clusters
            # form around the problem rather than the app name.
            review_title = title.split(":", 1)[1].strip() if ":" in title else title
            summary = truncate_words(f"{review_title}. {first_sentence(body)}".strip(". "), 40)
        summary = summary or signal_sentence(title) or truncate_words(title or body, 40)
        pain = max(1, min(5, 1 + round(score / 2.5) + (1 if money else 0)))
    return {
        "is_problem": int(is_problem),
        "domain": domain_for(row) if is_problem else None,
        "who_has_it": None,
        "problem_summary": summary,
        "pain_score": pain,
        "money_mentioned": int(money),
        "workaround_described": int(workaround),
        "solution_requested": int(requested),
        "existing_solutions_named": tools,
        "signal_score": score,
    }


def classify(cfg: dict, conn: sqlite3.Connection, limit: int | None = None,
             reclassify: bool = False) -> dict[str, int]:
    threshold = int((cfg.get("classify") or {}).get("threshold", 3))
    if reclassify:
        conn.execute("DELETE FROM problems")
        conn.commit()
    rows = dbm.unclassified_items(conn, limit=limit)
    stats = {"items": len(rows), "problems": 0, "non_problems": 0}
    if not rows:
        log.info("classify: nothing to do")
        return stats
    run_id = dbm.start_run(conn, "classify")
    now = iso(now_utc())
    for row in rows:
        res = classify_item(row, threshold)
        res.update({"item_id": row["id"], "language": row["language"], "classified_at": now})
        dbm.upsert_problem(conn, res)
        stats["problems" if res["is_problem"] else "non_problems"] += 1
    dbm.finish_run(conn, run_id, notes=str(stats))
    conn.commit()
    log.info("classify: %d items -> %d problems, %d non-problems (threshold %d)", len(rows),
             stats["problems"], stats["non_problems"], threshold)
    return stats
