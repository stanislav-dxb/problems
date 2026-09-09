"""Configuration loading: config.yaml merged over built-in defaults, plus .env."""
from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

DEFAULTS: dict[str, Any] = {
    "default_since_days": 7,
    "classify": {"threshold": 4},
    "query_terms": {
        "en": [
            "is there a tool", "why is there no", "I wish", "how do you handle",
            "anyone else struggle", "spreadsheet", "manually", "pain in the",
            "looking for a solution", "how do I find", "workaround",
        ],
        "ru": ["есть ли сервис", "почему нет", "как вы решаете", "вручную",
               "таблица", "ищу решение", "костыль"],
        "ar": ["هل يوجد", "كيف تتعاملون مع", "يدويا", "ابحث عن حل"],
        "hi": ["koi tool hai", "manually", "kaise karte ho"],
    },
    "sources": {
        "hn": {"enabled": True, "languages": ["en"], "tags": ["comment", "story"],
               "max_pages_per_query": 3, "min_body_chars": 80},
        "reddit": {"enabled": True, "subreddits": [], "posts_per_subreddit": 100,
                   "comments_per_post": 20, "max_posts_with_comments": 25,
                   "require_query_match": True, "min_body_chars": 60},
        "producthunt": {"enabled": True, "posts_per_run": 60, "comments_per_post": 20,
                        "min_body_chars": 40},
        "appstore": {"enabled": True, "countries": ["us"], "max_rating": 2, "pages": 2,
                     "min_body_chars": 40, "apps": {}},
        "youtube": {"enabled": True, "videos_per_channel": 5, "comments_per_video": 100,
                    "require_query_match": True, "min_body_chars": 60, "channels": {}},
        "telegram": {"enabled": False, "channels": [], "messages_per_channel": 200,
                     "require_query_match": True, "min_body_chars": 60},
        "adzuna": {"enabled": False, "countries": ["gb"], "queries": [],
                   "results_per_query": 50, "min_body_chars": 80},
    },
    "clustering": {
        "distance_threshold": 0.55,
        "embedding_model": "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
        "min_growth_denominator": 3,
    },
    "scoring": {
        "min_cluster_size": 3,
        "volume_cap": 50,
        "weights": {"volume": 0.20, "growth": 0.12, "sources": 0.08, "languages": 0.08, "pain": 0.12,
                    "money": 0.08, "demand": 0.08, "workaround": 0.04, "triangulation": 0.20},
    },
    "news": {
        "max_per_feed": 50,
        "catalyst_threshold": 2,
        "fetch_full_text": False,
        "google_news": {"enabled": True, "locales": [{"lang": "en", "gl": "US"}], "queries": {"en": []}},
        "gdelt": {"enabled": False, "max_records": 50, "spacing_seconds": 8, "phrases": {}},
        "feeds": [],
    },
    "reports": {
        "since_days": 730,
        "data_dir": "data/reports",
        "max_pdf_mb": 25,
        "chunk_chars": 8000,
        "fetch_html": True,
        "max_per_publisher": 30,
        "prefilter_keywords": ["gap", "underserved", "fragmented", "manual", "inefficien", "shortage", "opportunity",
                               "lack of", "no standard"],
        "worldbank": {"enabled": True, "queries": [], "rows": 20, "confidence": 4},
        "publishers": [],
        "arxiv": {"enabled": False, "categories": ["econ.GN"], "max_results": 50, "confidence": 3},
    },
    "triangulation": {
        "distance_threshold": 0.45,
        "min_claim_confidence_for_hypothesis": 3,
        "min_catalyst_strength_for_hypothesis": 4,
    },
    "digest": {"top_n": 10, "quotes_per_cluster": 3, "quote_max_words": 25,
               "rising_growth_threshold": 2.0, "output_dir": "digests"},
}


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def config_path() -> Path:
    return Path(os.environ.get("SCOUT_CONFIG", "config.yaml"))


def load_config(path: str | os.PathLike | None = None) -> dict[str, Any]:
    """Load .env (if present) and config.yaml merged over DEFAULTS."""
    load_dotenv(override=False)
    p = Path(path) if path else config_path()
    user: dict = {}
    if p.exists():
        with open(p, "r", encoding="utf-8") as f:
            user = yaml.safe_load(f) or {}
    cfg = _deep_merge(DEFAULTS, user)
    cfg["_path"] = str(p)
    return cfg


def db_path() -> str:
    return os.environ.get("SCOUT_DB", "scout.db")


def env(name: str, default: str | None = None) -> str | None:
    v = os.environ.get(name)
    if v is None or v.strip() == "":
        return default
    return v.strip()
