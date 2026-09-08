import sqlite3

from scout.classify import classify_item, signal_sentence


def _row(title="", body="", source="hn", raw_json="{}", language="en"):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    return conn.execute("SELECT ? AS title, ? AS body, ? AS source, ? AS raw_json, ? AS language, 1 AS id",
                        (title, body, source, raw_json, language)).fetchone()


def test_problem_post_is_flagged_with_signals():
    r = classify_item(_row("Invoicing", "I run a small shop and I do invoicing manually in a spreadsheet every "
                                        "month, is there a tool for this? It costs me hours and about $200 a month."))
    assert r["is_problem"] == 1
    assert r["money_mentioned"] == 1 and r["workaround_described"] == 1 and r["solution_requested"] == 1
    assert 1 <= r["pain_score"] <= 5 and r["problem_summary"]
    assert "manually" in r["problem_summary"].lower() or "is there a tool" in r["problem_summary"].lower()


def test_promo_post_is_not_a_problem():
    r = classify_item(_row("Show HN: my new invoicing app", "We just launched a shiny new invoicing app, "
                                                          "check out our site and sign up for the free trial."))
    assert r["is_problem"] == 0 and r["problem_summary"] is None and r["domain"] is None


def test_low_star_review_gets_a_boost_and_app_category_domain():
    body = "Cannot export my reports, keeps crashing when I try and support does not answer."
    r = classify_item(_row("QuickBooks: broken", body, source="appstore",
                           raw_json='{"rating": 1, "category": "small_business_ops"}'))
    assert r["is_problem"] == 1 and r["domain"] == "small_business_ops"


def test_multilingual_signals():
    ru = classify_item(_row("", "У нас маленькая клиника, приходится вручную вести таблицу записей, есть ли сервис "
                                "для этого? Мучаемся каждый день.", language="ru"))
    ar = classify_item(_row("", "عندي مشكلة في إدارة الطلبات يدويا كل يوم، هل يوجد حل لهذا الأمر؟", language="ar"))
    hi = classify_item(_row("", "Mera chhota business hai aur mujhe stock manually update karna padta hai, koi tool "
                                "hai iske liye? Bahut pareshan hoon.", language="hi"))
    assert ru["is_problem"] == 1 and ar["is_problem"] == 1 and hi["is_problem"] == 1
    assert ru["solution_requested"] == 1 and ar["solution_requested"] == 1 and hi["solution_requested"] == 1


def test_known_tools_are_extracted_and_deduplicated():
    r = classify_item(_row("", "I wish there was something better; I use Excel and google sheets and EXCEL again, "
                               "plus Zapier, and it is a pain in the neck."))
    assert r["existing_solutions_named"] == ["Excel", "Google Sheets", "Zapier"]


def test_signal_sentence_picks_first_sentence_with_signal():
    body = "Nice weather today. Anyone else struggle with reconciling payouts manually? It takes hours."
    assert signal_sentence(body).startswith("Anyone else struggle")
    assert signal_sentence("Just a happy note with nothing in it.") is None


def test_reddit_domain_is_subreddit():
    r = classify_item(_row("Help", "How do you handle change orders? We track them by hand and it is a nightmare.",
                           source="reddit", raw_json='{"subreddit": "Construction"}'))
    assert r["is_problem"] == 1 and r["domain"] == "r/Construction"
