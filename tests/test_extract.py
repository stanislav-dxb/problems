from scout.extract import (detect_geographies, extract_numbers, future_year_horizon, is_corridor, keyword_prefilter,
                           match_taxonomy, sentence_with)


def test_geographies_and_corridor():
    assert detect_geographies("Dubai firms importing from Shenzhen") == ["china", "gulf"]
    assert is_corridor(["china", "gulf"]) and not is_corridor(["gulf"]) and not is_corridor(["gulf", "us"])
    assert detect_geographies("Компании из Москвы выходят на рынок ОАЭ") == ["gulf", "russian"]
    assert "india" in detect_geographies("भारत में छोटे व्यापार")


def test_numbers_money_percent_year():
    nums = extract_numbers("The market was valued at $3.5 billion in 2024 and grows 12% a year; AED 2 million spent.")
    values = [n["value"] for n in nums]
    assert "$3.5 billion" in values and "12%" in values and "AED 2 million" in values
    assert next(n for n in nums if n["value"] == "$3.5 billion")["year"] == 2024
    assert extract_numbers("Объем рынка 120 млрд руб.")[0]["value"].startswith("120 млрд")


def test_taxonomy_and_sentence():
    tax = {"shortage": {"shortage": 3}, "regulation": {"new law": 3}}
    assert match_taxonomy("A new law addresses the labour shortage.", tax) == {"shortage": 3, "regulation": 3}
    assert sentence_with("Nice day. There is a shortage of drivers now. End.", ["shortage"]).startswith("There is a shortage")


def test_horizon_and_prefilter():
    from datetime import datetime, timezone
    now = datetime(2026, 9, 9, tzinfo=timezone.utc)
    assert future_year_horizon("mandatory from 2026", now) == "immediate"
    assert future_year_horizon("comes into force in 2027", now) == "6_months"
    assert future_year_horizon("target by 2035", now) == "3_plus_years"
    assert future_year_horizon("no dates here", now) is None
    assert keyword_prefilter("A fragmented market", ["fragmented"]) and not keyword_prefilter("fine", ["gap"])
