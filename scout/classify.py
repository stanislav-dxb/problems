"""Classify stage.

backend claude_code / anthropic_api: batches of `classify.batch_size` items go to the model with a strict-JSON
contract (is_problem, English problem_summary, who_has_it, domain, pain_score 1-5, money_mentioned,
workaround_described, solution_requested, existing_solutions_named). Batches run with at most
`llm.max_parallel` concurrent calls; a failed call is retried once, then the batch is marked failed.
A prompt over 60k characters halves the batch and retries.

backend rules: the multilingual keyword classifier below (no model calls).
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from . import db as dbm
from . import llm
from .util import iso, now_utc, truncate_words

log = logging.getLogger("scout.classify")

SYSTEM_PROMPT = """You are the classifier for Problem Scout, a research tool that finds recurring problems people describe in public discussions (forum posts, comments, app reviews, job ads).

You receive a JSON array of items. For EACH item return one JSON object with exactly these fields:

- "idx": integer, copied from the input item.
- "is_problem": boolean. true ONLY if a person describes a difficulty that they or their organisation actually face. General opinions, product announcements, news commentary, jokes, praise, abstract debates, hypothetical musings and job descriptions with no pain described are false.
- "domain": string or null. A short industry or function label such as "construction", "ecommerce logistics", "small business accounting", "freelancing", "clinic operations", "IT administration", "cross-border payments", "developer tooling". Describe the person's activity, not the medium (never "mobile apps" or "reddit"). null when is_problem is false.
- "who_has_it": string or null. Who experiences it, e.g. "small contractors in the US", "Shopify merchants shipping internationally", "sysadmins at mid-size companies". null when is_problem is false.
- "problem_summary": string or null. ONE sentence in English, regardless of the source language, stating the underlying difficulty concretely (what they are trying to do and what gets in the way). For app reviews describe the job the person cannot get done, not just "the app crashes". null when is_problem is false.
- "pain_score": integer 1-5 or null. 1 = mild annoyance; 2 = recurring irritation; 3 = costs real time or money regularly; 4 = serious and frequent cost or risk; 5 = described as blocking, expensive, or business-critical. null when is_problem is false.
- "money_mentioned": boolean. A cost, price, fee, budget, revenue or financial loss is mentioned.
- "workaround_described": boolean. The author describes how they currently cope (spreadsheets, manual work, scripts, hiring someone, duct-taping tools).
- "solution_requested": boolean. The author asks for, searches for, or wishes for a tool, service or solution.
- "existing_solutions_named": array of strings. Products, services or named workarounds mentioned. Empty array if none.

Rules:
- Return ONLY a JSON array with exactly one object per input item, in the same order, with the same idx values. No prose, no markdown fences, no comments.
- Never include personal names or usernames in any field.
- problem_summary must be English even when the source text is Russian, Arabic, Hindi or any other language.
- Be strict about is_problem. When in doubt, false."""


# ---------------------------------------------------------------- model path

def _fields_for_item(row: dict, idx: int, max_chars: int = 1500) -> dict[str, Any]:
    body = (row.get("body") or "").strip()
    if len(body) > max_chars:
        body = body[:max_chars] + " …[truncated]"
    return {"idx": idx, "source": row.get("source"), "language": row.get("language") or "und",
            "title": (row.get("title") or "")[:200], "body": body}


def _bool(v: Any) -> int:
    if isinstance(v, str):
        return 1 if v.strip().lower() in ("true", "yes", "1") else 0
    return 1 if v else 0


def _int_or_none(v: Any, lo: int = 1, hi: int = 5) -> int | None:
    try:
        i = int(round(float(v)))
    except (TypeError, ValueError):
        return None
    return max(lo, min(hi, i))


def parse_classification(payload: Any, expected_idx: list[int]) -> dict[int, dict[str, Any]]:
    """Validate and normalise the model's JSON. Missing or invalid items are absent (caller marks them failed)."""
    if isinstance(payload, dict):
        for k in ("results", "items", "classifications", "data"):
            if isinstance(payload.get(k), list):
                payload = payload[k]
                break
    if not isinstance(payload, list):
        raise ValueError("classifier output is not a JSON array")
    out: dict[int, dict[str, Any]] = {}
    for pos, obj in enumerate(payload):
        if not isinstance(obj, dict):
            continue
        idx = obj.get("idx")
        if idx is None and len(payload) == len(expected_idx):
            idx = expected_idx[pos]
        try:
            idx = int(idx)
        except (TypeError, ValueError):
            continue
        if idx not in expected_idx or idx in out:
            continue
        is_problem = _bool(obj.get("is_problem"))
        sols = obj.get("existing_solutions_named") or []
        if isinstance(sols, str):
            sols = [sols]
        sols = [str(s)[:100] for s in sols if s][:20]
        summary = obj.get("problem_summary")
        summary = str(summary).strip() if summary else None
        if is_problem and not summary:
            is_problem = 0
        out[idx] = {
            "is_problem": is_problem,
            "domain": (str(obj.get("domain")).strip()[:100] if obj.get("domain") else None) if is_problem else None,
            "who_has_it": (str(obj.get("who_has_it")).strip()[:200] if obj.get("who_has_it") else None) if is_problem else None,
            "problem_summary": summary[:500] if (is_problem and summary) else None,
            "pain_score": _int_or_none(obj.get("pain_score")) if is_problem else None,
            "money_mentioned": _bool(obj.get("money_mentioned")),
            "workaround_described": _bool(obj.get("workaround_described")),
            "solution_requested": _bool(obj.get("solution_requested")),
            "existing_solutions_named": sols,
        }
    return out


def build_batch_prompt(rows: list[dict]) -> str:
    payload = [_fields_for_item(r, i) for i, r in enumerate(rows)]
    return "Classify these items:\n\n" + json.dumps(payload, ensure_ascii=False)


def classify_batch_model(rows: list[dict], model: str) -> dict[int, dict[str, Any]]:
    """Classify one batch with the model. Returns {item_id: normalised}. Halves the batch when the prompt is
    too long. Raises LLMError after the call itself fails (the caller decides about retrying)."""
    if not rows:
        return {}
    user = build_batch_prompt(rows)
    if len(SYSTEM_PROMPT) + len(user) > llm.MAX_PROMPT_CHARS and len(rows) > 1:
        mid = len(rows) // 2
        log.info("classify: prompt %d chars > %d; halving batch of %d", len(SYSTEM_PROMPT) + len(user),
                 llm.MAX_PROMPT_CHARS, len(rows))
        out = classify_batch_model(rows[:mid], model)
        out.update(classify_batch_model(rows[mid:], model))
        return out
    try:
        parsed = llm.complete_json(SYSTEM_PROMPT, user, model, stage="classify", items=len(rows), retries=1)
    except llm.PromptTooLong:
        if len(rows) == 1:
            raise
        mid = len(rows) // 2
        out = classify_batch_model(rows[:mid], model)
        out.update(classify_batch_model(rows[mid:], model))
        return out
    normalised = parse_classification(parsed, list(range(len(rows))))
    return {rows[i]["id"]: v for i, v in normalised.items()}


def _classify_with_model(cfg: dict, conn: sqlite3.Connection, rows: list[sqlite3.Row], stats: dict[str, Any]) -> None:
    batch_size = int(cfg.get("classify", {}).get("batch_size", 15))
    model = llm.classify_model()
    parallel = llm.settings()["max_parallel"]
    backend = llm.backend_name()
    dict_rows = [dict(r) for r in rows]
    batches = [dict_rows[i:i + batch_size] for i in range(0, len(dict_rows), batch_size)]
    stats.update({"batches": len(batches), "batch_failures": 0, "backend": backend, "model": model})
    t0 = time.monotonic()

    def work(batch: list[dict]) -> tuple[list[dict], dict[int, dict] | None, str | None]:
        for attempt in (1, 2):
            try:
                return batch, classify_batch_model(batch, model), None
            except llm.LLMUnavailable:
                raise
            except llm.LLMError as e:
                log.warning("classify: batch of %d failed (attempt %d): %s", len(batch), attempt, e)
                err = str(e)
        return batch, None, err

    now = iso(now_utc())
    with ThreadPoolExecutor(max_workers=parallel) as ex:
        futures = [ex.submit(work, b) for b in batches]
        done = 0
        for fut in as_completed(futures):
            batch, results, err = fut.result()
            done += 1
            if results is None:
                stats["batch_failures"] += 1
                for row in batch:
                    stats["failed"] += 1
                    dbm.upsert_problem(conn, {"item_id": row["id"], "is_problem": 0, "classification_failed": 1,
                                              "language": row["language"], "classified_by": backend, "classified_at": now})
                conn.commit()
                continue
            for row in batch:
                res = results.get(row["id"])
                if res is None:
                    stats["failed"] += 1
                    dbm.upsert_problem(conn, {"item_id": row["id"], "is_problem": 0, "classification_failed": 1,
                                              "language": row["language"], "classified_by": backend, "classified_at": now})
                    continue
                res.update({"item_id": row["id"], "language": row["language"], "classified_at": now,
                            "classification_failed": 0, "classified_by": backend})
                dbm.upsert_problem(conn, res)
                stats["problems" if res["is_problem"] else "non_problems"] += 1
            conn.commit()
            log.info("classify: batch %d/%d done (problems so far %d, failed %d)", done, len(batches),
                     stats["problems"], stats["failed"])
    stats["wall_s"] = round(time.monotonic() - t0, 1)


# ---------------------------------------------------------------- rules path (no model)

PROBLEM_SIGNALS: dict[str, int] = {
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
    "есть ли сервис": 3, "есть ли программа": 3, "есть ли инструмент": 3, "почему нет": 2,
    "как вы решаете": 2, "как вы справляетесь": 2, "вручную": 2, "таблиц": 1, "ищу решение": 3,
    "ищу сервис": 3, "костыль": 2, "бесит": 2, "неудобно": 2, "не работает": 1, "мучаюсь": 3,
    "мучаемся": 3, "приходится": 2, "нет нормального": 3, "посоветуйте": 2, "подскажите": 1,
    "проблема": 1, "головная боль": 3, "тратим": 1, "тратить время": 2, "руками": 2,
    "هل يوجد": 3, "هل في": 2, "كيف تتعاملون مع": 2, "كيف تتعامل": 2, "يدويا": 2, "يدوياً": 2,
    "ابحث عن حل": 3, "أبحث عن حل": 3, "ابحث عن": 1, "مشكلة": 2, "لا يعمل": 1, "صعب": 1, "معاناة": 3,
    "اقتراح": 1, "نصيحة": 1, "ضياع وقت": 2, "مضيعة": 2,
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
KNOWN_TOOLS = [
    "Excel", "Google Sheets", "Airtable", "Notion", "Zapier", "Make.com", "n8n", "QuickBooks", "Xero",
    "Zoho", "FreshBooks", "Wave", "Tally", "Vyapar", "Khatabook", "Shopify", "WooCommerce", "Magento",
    "Amazon", "Etsy", "Salesforce", "HubSpot", "Pipedrive", "Slack", "Trello", "Asana", "Jira",
    "Monday.com", "ClickUp", "Basecamp", "Odoo", "SAP", "Oracle", "NetSuite", "Procore", "Buildertrend",
    "Jobber", "Housecall Pro", "ServiceTitan", "Upwork", "Fiverr", "Deel", "Gusto", "Stripe", "PayPal",
    "Wise", "Payoneer", "Razorpay", "Paytm", "WhatsApp", "Telegram", "1C", "Bitrix24", "amoCRM",
    "Yandex", "Avito", "Careem", "Talabat", "Noon", "Zomato", "Swiggy", "Meesho", "Flipkart", "IndiaMART",
    "ShipStation", "Freightos", "Flexport", "Calendly", "Fresha", "Booksy", "Square", "Toast",
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
    text = _clean(body)
    for s in _SENT_RE.split(text):
        s = s.strip()
        if len(s.split()) >= 4 and signal_hits(s, PROBLEM_SIGNALS):
            return truncate_words(s, max_words)
    return None


def first_sentence(body: str, max_words: int = 30) -> str:
    parts = [x.strip() for x in _SENT_RE.split(_clean(body)) if x.strip()]
    return truncate_words(parts[0], max_words) if parts else ""


def domain_for(row: sqlite3.Row | dict) -> str:
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


def classify_item(row: sqlite3.Row | dict, threshold: int = 4) -> dict[str, Any]:
    """Rules backend: keyword scoring, summary in the original language."""
    title, body = row["title"] or "", row["body"] or ""
    text = f"{title}\n{body}"
    score = sum(w for _, w in signal_hits(text, PROBLEM_SIGNALS)) - sum(w for _, w in signal_hits(text, PROMO_SIGNALS))
    if FIRST_PERSON.search(text):
        score += 1
    try:
        raw = json.loads(row["raw_json"] or "{}")
    except (TypeError, ValueError):
        raw = {}
    if row["source"] == "appstore" and raw.get("rating") in (1, 2):
        score += 2
    money = bool(MONEY.search(text))
    is_problem = score >= threshold
    summary = pain = None
    if is_problem:
        summary = signal_sentence(body)
        if not summary and row["source"] == "appstore":
            review_title = title.split(":", 1)[1].strip() if ":" in title else title
            summary = truncate_words(f"{review_title}. {first_sentence(body)}".strip(". "), 40)
        summary = summary or signal_sentence(title) or truncate_words(title or body, 40)
        pain = max(1, min(5, 1 + round(score / 2.5) + (1 if money else 0)))
    return {
        "is_problem": int(is_problem), "domain": domain_for(row) if is_problem else None, "who_has_it": None,
        "problem_summary": summary, "pain_score": pain, "money_mentioned": int(money),
        "workaround_described": int(bool(WORKAROUND.search(text))),
        "solution_requested": int(bool(SOLUTION_REQUEST.search(text))),
        "existing_solutions_named": sorted({_TOOL_CANON[m.lower()] for m in _TOOL_RE.findall(text)}),
        "signal_score": score,
    }


def _classify_with_rules(cfg: dict, conn: sqlite3.Connection, rows: list[sqlite3.Row], stats: dict[str, Any]) -> None:
    threshold = int((cfg.get("classify") or {}).get("threshold", 4))
    now = iso(now_utc())
    for row in rows:
        res = classify_item(row, threshold)
        res.update({"item_id": row["id"], "language": row["language"], "classified_at": now, "classified_by": "rules"})
        dbm.upsert_problem(conn, res)
        stats["problems" if res["is_problem"] else "non_problems"] += 1
    stats["backend"] = "rules"


# ---------------------------------------------------------------- stage entry point

def classify(cfg: dict, conn: sqlite3.Connection, limit: int | None = None, reclassify: bool = False,
             retry_failed: bool = False) -> dict[str, Any]:
    if reclassify:
        conn.execute("DELETE FROM problems")
        conn.commit()
    rows = dbm.unclassified_items(conn, limit=limit, retry_failed=retry_failed)
    stats: dict[str, Any] = {"items": len(rows), "problems": 0, "non_problems": 0, "failed": 0}
    if not rows:
        log.info("classify: nothing to do")
        return stats
    run_id = dbm.start_run(conn, "classify")
    if llm.backend_name() == "rules":
        _classify_with_rules(cfg, conn, rows, stats)
    else:
        _classify_with_model(cfg, conn, rows, stats)
    dbm.finish_run(conn, run_id, notes=str(stats))
    conn.commit()
    log.info("classify: %s", stats)
    return stats
