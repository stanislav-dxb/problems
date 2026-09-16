import json

import pytest

from hotstartups import tasks


def _submit(ctx, run, ticket, result):
    cfg, src, store = ctx
    return tasks.submit(cfg, src, store, run, ticket["ticket"], result)


def test_limits_are_hard_and_counted_on_issue(ctx):
    cfg, src, store = ctx
    run = tasks.start_run(cfg, store)
    assert run["first_run"] is True
    res = tasks.next_tasks(cfg, src, store, run, batch=10)
    # discovery: 50% of 4 searches = 2 tickets, counted when issued
    assert [t["type"] for t in res["tasks"]] == ["search", "search"]
    assert run["counts"]["searches"] == 2
    # asking again returns the same pending tickets, no new counting
    again = tasks.next_tasks(cfg, src, store, run, batch=10)
    assert [t["ticket"] for t in again["tasks"]] == [t["ticket"] for t in res["tasks"]]
    assert run["counts"]["searches"] == 2
    # submit both with candidates -> research phase
    for i, t in enumerate(res["tasks"]):
        _submit(ctx, run, t, {"results": [{"title": "x", "url": f"https://news.example/{i}", "snippet": "s"}],
                              "candidates": [{"name": f"Startup {i}", "url": f"https://startup{i}.io", "country": "Germany", "sector": "Health", "one_line": "does health"},
                                             {"name": "Startup 0", "url": "https://startup0.io", "one_line": "again"}]})
    assert len(store.startups()) == 2  # Startup 0 merged by domain
    # research: one search per startup, then a judge ticket; searches cap at 4 total
    issued = []
    for _ in range(10):
        res = tasks.next_tasks(cfg, src, store, run, batch=10)
        if res.get("done"):
            break
        for t in res["tasks"]:
            issued.append(t["type"])
            if t["type"] == "research_search":
                _submit(ctx, run, t, {"results": [], "website": None, "candidates": []})
            elif t["type"] == "judge":
                _submit(ctx, run, t, {"is_startup": True, "idea": "helps clinics", "twist": "t", "why_now": "w", "who_pays": "p", "lesson": "l",
                                      "country": "Germany", "region": "europe", "industry": "Health",
                                      "facts": {"founded": {"value": "2023", "sources": ["https://a.example", "https://b.example"]}, "raised": {"value": "$1M", "sources": ["https://a.example"]}},
                                      "scores": {"market": {"score": 8, "reason": "r"}, "tech": {"score": 6, "reason": "r"}, "growth": {"score": None, "reason": "no numbers", "basis": "unknown"}},
                                      "status_signal": "active", "sources": [{"url": "https://a.example", "title": "A", "date": "2026-09-01"}]})
            elif t["type"] == "trends":
                _submit(ctx, run, t, {"trends": [{"name": "Health tools", "sentence": "s", "startup_ids": [e["id"] for e in t["entries"]]}]})
            elif t["type"] == "gaps":
                _submit(ctx, run, t, {"gaps": ["nobody does X"]})
            elif t["type"] == "brief":
                _submit(ctx, run, t, {"sentences": "Two joined. One leads. Health is the trend."})
    assert run["counts"]["searches"] <= 4
    assert run["counts"]["judged"] == 2
    assert issued.count("judge") == 2
    week = tasks.finish_run(cfg, store, run)
    assert len(week["top_ids"]) == 2 and len(week["new_ids"]) == 2
    rec = store.startups()[week["top_ids"][0]]
    assert rec["entry"]["overall"] == 7.0  # (8 + 6) / 2, growth unknown ignored
    assert rec["entry"]["facts"]["founded"]["confirmed"] is True
    assert rec["entry"]["facts"]["raised"]["confirmed"] is False
    assert "startups judged of 2" in week["run_line"]
    from hotstartups.page import render
    html = render(store, week)
    assert "Startup 0" in html and "Growth unknown" in html and "reported by" in html and "Health tools" in html


def test_not_a_startup_is_dropped(ctx):
    cfg, src, store = ctx
    run = tasks.start_run(cfg, store)
    rec = store.new_startup("BigCo", "https://bigco.com")
    store.save_startup(rec)
    run["phase"] = "research"
    res = tasks.next_tasks(cfg, src, store, run, batch=5)
    t = res["tasks"][0]
    _submit(ctx, run, t, {"results": [], "website": None, "candidates": []})
    res = tasks.next_tasks(cfg, src, store, run, batch=5)
    judge = [t for t in res["tasks"] if t["type"] == "judge"][0]
    _submit(ctx, run, judge, {"is_startup": False, "not_startup_reason": "listed on Nasdaq", "idea": "-", "scores": {}})
    assert store.startups()[rec["id"]]["status"] == "dropped"
    assert run["new_ids"] == []


def test_paused_and_bad_submission(ctx):
    cfg, src, store = ctx
    cfg["paused"] = True
    with pytest.raises(tasks.RunError):
        tasks.start_run(cfg, store)
    cfg["paused"] = False
    run = tasks.start_run(cfg, store)
    res = tasks.next_tasks(cfg, src, store, run, batch=1)
    t = res["tasks"][0]
    with pytest.raises(tasks.RunError):
        tasks.submit(cfg, src, store, run, t["ticket"], {"results": "not a list"})
    assert run["tickets"][t["ticket"]]["status"] == "issued"
