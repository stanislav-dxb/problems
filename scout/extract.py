"""Shared rule-based extractors: geographies, figures, taxonomy matching, time horizons."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Iterable

GEO: dict[str, list[str]] = {
    "gulf": ["uae", "united arab emirates", "dubai", "abu dhabi", "sharjah", "saudi", "riyadh", "jeddah", "qatar",
             "doha", "kuwait", "bahrain", "oman", "muscat", "gcc", "gulf", "оаэ", "дубай", "дубае", "саудовск",
             "катар", "кувейт", "бахрейн", "оман", "الإمارات", "الامارات", "دبي", "أبوظبي", "ابوظبي", "السعودية",
             "الرياض", "جدة", "قطر", "الكويت", "البحرين", "عمان", "الخليج", "दुबई", "सऊदी", "यूएई"],
    "russian": ["russia", "moscow", "kazakhstan", "uzbekistan", "belarus", "kyrgyz", "cis", "россия", "россии",
                "москв", "казахстан", "узбекистан", "беларус", "снг", "روسيا", "रूस"],
    "india": ["india", "mumbai", "delhi", "bengaluru", "bangalore", "chennai", "hyderabad", "pune", "kolkata",
              "индия", "индии", "الهند", "भारत", "मुंबई", "दिल्ली", "बेंगलुरु"],
    "china": ["china", "chinese", "beijing", "shanghai", "shenzhen", "hong kong", "guangzhou", "китай", "китая",
              "пекин", "الصين", "चीन"],
    "eu": ["germany", "german", "berlin", "munich", "frankfurt", "european union", "eu ", "brussels", "france",
           "netherlands", "italy", "spain", "poland", "германи", "евросоюз", "ес ", "брюссел", "франци",
           "ألمانيا", "الاتحاد الأوروبي", "जर्मनी", "यूरोप"],
    "us": ["united states", "u.s.", "usa", "america", "washington", "new york", "california", "сша", "أمريكا",
           "الولايات المتحدة", "अमेरिका"],
    "uk": ["united kingdom", "britain", "british", "london", "великобритани", "بريطانيا", "ब्रिटेन"],
}
CORRIDOR = ("gulf", "russian", "india", "china", "eu")

_NUM = r"\d{1,3}(?:[,\s]\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?"
_MONEY_RE = re.compile(
    rf"((?:US\$|USD|AED|SAR|INR|RUB|EUR|GBP|Rs\.?|₹|\$|€|£)\s?(?:{_NUM})\s?(?:trillion|billion|million|thousand|bn|mn|tn|crore|lakh|m|b|k)?\b"
    rf"|(?:{_NUM})\s?(?:trillion|billion|million|bn|mn|tn|crore|lakh)\s?(?:US\$|USD|AED|SAR|INR|RUB|EUR|GBP|dollars|dirhams|riyals|rupees|rubles|euros|"
    rf"долл|руб|млрд|млн|دولار|درهم|ريال|روبية)?"
    rf"|(?:{_NUM})\s?(?:млрд|млн|тыс)\.?\s?(?:долл|руб|евро)?"
    rf"|(?:{_NUM})\s?(?:مليار|مليون|ألف)\s?(?:دولار|درهم|ريال)?"
    rf"|(?:{_NUM})\s?(?:करोड़|लाख|अरब)\s?(?:रुपये|डॉलर)?)", re.I)
_PCT_RE = re.compile(rf"((?:{_NUM})\s?(?:%|percent|per cent|процент|٪|प्रतिशत))", re.I)
_YEAR_RE = re.compile(r"\b(19[89]\d|20[0-4]\d)\b")
_URL_RE = re.compile(r"https?://\S+|www\.\S+")
_SENT_RE = re.compile(r"(?<=[.!?؟।])\s+|\n+")


def clean(text: str | None) -> str:
    return " ".join(_URL_RE.sub(" ", text or "").split())


def detect_geographies(text: str) -> list[str]:
    low = f" {(text or '').lower()} "
    return sorted(k for k, terms in GEO.items() if any(t in low for t in terms))


def is_corridor(geos: Iterable[str], min_regions: int = 2) -> bool:
    return len(set(geos) & set(CORRIDOR)) >= min_regions


def extract_numbers(text: str, max_items: int = 12) -> list[dict]:
    """Figures with units and, when present nearby, a year. Context is a short window around the match."""
    text = clean(text)
    out: list[dict] = []
    seen: set[str] = set()
    for kind, rx in (("money", _MONEY_RE), ("percent", _PCT_RE)):
        for m in rx.finditer(text):
            val = " ".join(m.group(1).split())
            if val in seen or len(val) < 2 or not any(ch.isdigit() for ch in val):
                continue
            seen.add(val)
            window = text[max(0, m.start() - 80): m.end() + 80]
            years = _YEAR_RE.findall(window)
            out.append({"value": val, "kind": kind, "year": int(years[0]) if years else None,
                        "context": window.strip()})
            if len(out) >= max_items:
                return out
    return out


def match_taxonomy(text: str, taxonomy: dict[str, dict[str, int]]) -> dict[str, int]:
    """{type: score} for every type with at least one phrase hit."""
    low = (text or "").lower()
    scores: dict[str, int] = {}
    for typ, phrases in taxonomy.items():
        s = sum(w for p, w in phrases.items() if p in low)
        if s:
            scores[typ] = s
    return scores


def matched_terms(text: str, taxonomy: dict[str, dict[str, int]]) -> list[str]:
    low = (text or "").lower()
    return sorted({p for phrases in taxonomy.values() for p in phrases if p in low})


def future_year_horizon(text: str, now: datetime | None = None) -> str | None:
    """Time horizon from the latest future year mentioned (e.g. 'by 2028'), else None."""
    now = now or datetime.now(timezone.utc)
    years = [int(y) for y in _YEAR_RE.findall(text or "")]
    future = [y for y in years if y >= now.year]
    if not future:
        return None
    delta = max(future) - now.year
    if delta == 0:
        return "immediate"
    if delta == 1:
        return "6_months" if now.month >= 7 else "1_3_years"
    return "1_3_years" if delta <= 3 else "3_plus_years"


def sentence_with(text: str, phrases: Iterable[str], max_words: int = 40) -> str | None:
    """First sentence containing any of the phrases, else None."""
    low_phrases = [p.lower() for p in phrases]
    for s in _SENT_RE.split(clean(text)):
        s = s.strip()
        if len(s.split()) >= 4 and any(p in s.lower() for p in low_phrases):
            words = s.split()
            return " ".join(words[:max_words]) + ("…" if len(words) > max_words else "")
    return None


def keyword_prefilter(text: str, keywords: Iterable[str]) -> bool:
    low = (text or "").lower()
    return any(k.lower() in low for k in keywords if k)
