"""Settings: config.yaml (limits, rules, page) and sources.yaml (regions, feeds, search words)."""
from __future__ import annotations

import os
from pathlib import Path

import yaml

REGIONS = ["north_america", "europe", "asia", "latin_america", "mea", "other"]
REGION_NAMES = {
    "north_america": "North America",
    "europe": "Europe",
    "asia": "Asia",
    "latin_america": "Latin America",
    "mea": "Middle East and Africa",
    "other": "Elsewhere",
}

DEFAULTS = {
    "limits": {"searches_per_week": 250, "pages_per_week": 200, "startups_judged_per_week": 60, "run_minutes": 90},
    "first_run": {"searches_per_week": 60, "pages_per_week": 40, "startups_judged_per_week": 20, "run_minutes": 90},
    "paused": False,
    "page": {"top_n": 50, "artifact_url": "", "title": "Hot Startups"},
    "startup_rule": {"max_team_size": 500},
    "discovery": {"share_of_searches": 0.4},
    "research": {"searches_per_startup": 2, "pages_per_startup": 2, "refresh_after_days": 30, "refresh_share": 0.2},
    "struggling": {"searches": 6},
    "feeds": {"batch_size": 40, "max_batches": 12},
    "text": {"max_chars_per_page": 6000, "max_chars_per_dossier": 14000},
    "git": {"branch": ""},
}


def home() -> Path:
    """The hot_startups folder: HOTSTARTUPS_HOME, else the folder above this package."""
    env = os.environ.get("HOTSTARTUPS_HOME")
    return Path(env).resolve() if env else Path(__file__).resolve().parent.parent


def _deep_merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config(root: Path | None = None) -> dict:
    root = root or home()
    path = root / "config.yaml"
    data = {}
    if path.exists():
        with path.open(encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    cfg = _deep_merge(DEFAULTS, data)
    cfg["_root"] = str(root)
    return cfg


def load_sources(root: Path | None = None) -> dict:
    root = root or home()
    path = root / "sources.yaml"
    if not path.exists():
        return {"regions": {}, "struggling_queries": {}, "research_queries": {}}
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    data.setdefault("regions", {})
    data.setdefault("struggling_queries", {})
    data.setdefault("research_queries", {})
    return data


def load_list_sites(root: Path | None = None) -> list[dict]:
    """list_sites.txt: one site per line, 'url  note'. Lines starting with # are notes."""
    root = root or home()
    path = root / "list_sites.txt"
    sites = []
    if not path.exists():
        return sites
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(None, 1)
        sites.append({"url": parts[0], "note": parts[1].strip() if len(parts) > 1 else ""})
    return sites


MONTHS = {
    "en": ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"],
    "pt": ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro", "novembro", "dezembro"],
    "es": ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"],
    "fr": ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre", "novembre", "décembre"],
    "de": ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober", "November", "Dezember"],
    "it": ["gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio", "agosto", "settembre", "ottobre", "novembre", "dicembre"],
    "nl": ["januari", "februari", "maart", "april", "mei", "juni", "juli", "augustus", "september", "oktober", "november", "december"],
    "sv": ["januari", "februari", "mars", "april", "maj", "juni", "juli", "augusti", "september", "oktober", "november", "december"],
    "pl": ["styczeń", "luty", "marzec", "kwiecień", "maj", "czerwiec", "lipiec", "sierpień", "wrzesień", "październik", "listopad", "grudzień"],
    "id": ["Januari", "Februari", "Maret", "April", "Mei", "Juni", "Juli", "Agustus", "September", "Oktober", "November", "Desember"],
    "tr": ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"],
    "ru": ["январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь"],
    "ar": ["يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو", "يوليو", "أغسطس", "سبتمبر", "أكتوبر", "نوفمبر", "ديسمبر"],
    "he": ["ינואר", "פברואר", "מרץ", "אפריל", "מאי", "יוני", "יולי", "אוגוסט", "ספטמבר", "אוקטובר", "נובמבר", "דצמבר"],
    "vi": ["tháng 1", "tháng 2", "tháng 3", "tháng 4", "tháng 5", "tháng 6", "tháng 7", "tháng 8", "tháng 9", "tháng 10", "tháng 11", "tháng 12"],
}


def month_year(lang: str, year: int, month: int) -> str:
    """'September 2026' in the given language; CJK languages use their year-month form."""
    if lang in ("ja", "zh"):
        return f"{year}年{month}月"
    if lang == "ko":
        return f"{year}년 {month}월"
    names = MONTHS.get(lang) or MONTHS["en"]
    name = names[month - 1]
    if lang == "pt":
        return f"{name} de {year}"
    return f"{name} {year}"
