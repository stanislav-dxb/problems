from urllib.parse import parse_qs, urlsplit

from scout.catalysts import classify_article
from scout.sources.news.gdelt import build_query, build_url
from scout.sources.news.google_news import build_url as gn_url, plan


def test_google_news_urls_for_every_configured_language(cfg):
    cfg["news"]["google_news"] = {
        "enabled": True,
        "locales": [{"lang": "en", "gl": "AE", "ceid": "AE:en"}, {"lang": "ru", "gl": "RU", "ceid": "RU:ru"},
                    {"lang": "ar", "gl": "SA", "ceid": "SA:ar"}, {"lang": "hi", "gl": "IN", "ceid": "IN:hi"}],
        "queries": {"en": [{"q": "e-invoicing mandate", "domain": "accounting"}], "ru": ["дефицит кадров"],
                    "ar": ["نقص العمالة"], "hi": ["नया नियम"]},
    }
    rows = plan(cfg)
    assert len(rows) == 4 and {r[2] for r in rows} == {"en", "ru", "ar", "hi"}
    for url, feed, lang, domain, query in rows:
        parts = urlsplit(url)
        assert parts.scheme == "https" and parts.netloc == "news.google.com" and parts.path == "/rss/search"
        qs = parse_qs(parts.query)
        assert qs["q"] == [query] and qs["ceid"][0].endswith(f":{lang}") and "gl" in qs and "hl" in qs
        assert " " not in url and "\n" not in url
    assert parse_qs(urlsplit(gn_url("x y", "en", "US")).query)["hl"] == ["en"]


def test_gdelt_query_and_url_per_language():
    assert build_query(["labour shortage", "new law"], "en") == '("labour shortage" OR "new law") sourcelang:english'
    assert build_query(["дефицит"], "ru") == '"дефицит" sourcelang:russian'
    for lang in ("en", "ru", "ar", "hi"):
        url = build_url(["a b", "c"], lang, 7)
        parts = urlsplit(url)
        assert parts.netloc == "api.gdeltproject.org" and " " not in url
        qs = parse_qs(parts.query)
        assert qs["mode"] == ["artlist"] and qs["format"] == ["json"] and qs["timespan"] == ["7d"]
        assert "sourcelang:" in qs["query"][0]


def test_catalyst_rules():
    row = classify_article({"title": "UAE makes e-invoicing mandatory for all businesses from July 2027",
                            "summary_text": "The mandate takes effect in 2027 for companies above AED 3 million revenue.",
                            "kind": "regulatory", "domain_hint": "accounting"})
    assert row["is_catalyst"] == 1 and row["catalyst_type"] == "new_mandate"
    assert row["geography"] == ["gulf"] and row["catalyst_strength"] >= 3
    assert row["time_horizon"] in ("6_months", "1_3_years") and row["numbers"]
    ru = classify_article({"title": "Крупный ритейлер уходит с рынка Казахстана", "summary_text": "", "kind": "press"})
    assert ru["is_catalyst"] == 1 and ru["catalyst_type"] == "incumbent_exit" and "russian" in ru["geography"]
    assert classify_article({"title": "Ten tips for a productive Monday", "summary_text": "", "kind": "press"})["is_catalyst"] == 0
