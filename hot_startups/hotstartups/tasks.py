"""The weekly run: hands out tickets (search, read, judge, ...) within the limits and takes results back.

The program never calls a model itself. Claude, running the RUNBOOK in a Claude Code session, asks for
the next tickets, does them with its own web search and page reader, and submits the results here.
Every ticket is counted the moment it is issued, so the limits in config.yaml are hard limits.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from .config import REGION_NAMES, REGIONS, load_list_sites, month_year
from .fetch import cache_text, get, read_feed
from .store import Store
from .util import clip, domain_of, looks_like_company_site, now_iso, read_json, slugify, today, write_json

COUNTED = {"searches": "searches", "pages": "pages", "judged": "judged"}
PHASES = ["lists", "feeds", "discovery", "research", "struggling", "synthesis", "done"]


class RunError(Exception):
    pass


# ---------------------------------------------------------------------------
# run lifecycle
# ---------------------------------------------------------------------------
def _week_of(d: date) -> str:
    return (d - timedelta(days=d.weekday())).isoformat()


def start_run(cfg: dict, store: Store, force_first: bool = False) -> dict:
    if cfg.get("paused"):
        raise RunError("paused: config.yaml says paused: true, so no run starts. Set it to false to resume.")
    store.init()
    latest = store.latest_run()
    if latest and not latest.get("finished"):
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(latest["started"])).days
        if age < 7:
            latest["resumed"] = latest.get("resumed", 0) + 1
            store.save_run(latest)
            return latest
        latest["finished"] = now_iso()
        latest["abandoned"] = True
        store.save_run(latest)
    finished_before = any(r.get("finished") and not r.get("abandoned") for r in store.runs())
    limits = dict(cfg["limits"]) if (finished_before and not force_first) else dict(cfg["first_run"])
    number = (latest["number"] + 1) if latest else 1
    run = {
        "id": f"{today()}-r{number}",
        "number": number,
        "week_of": _week_of(date.today()),
        "started": now_iso(),
        "finished": None,
        "limits": limits,
        "first_run": not finished_before,
        "counts": {"searches": 0, "pages": 0, "judged": 0, "feeds_read": 0, "extractions": 0, "synthesis": 0},
        "phase": "lists",
        "tickets": {},
        "next_ticket": 1,
        "phase_state": {"lists": {}, "feeds": {"fetched": False, "batches": 0}, "discovery": {}, "research": {"refresh_ids": []}, "struggling": {"issued": 0, "results": [], "ticket": None}, "synthesis": {}},
        "failures": [],
        "unread_sites": [],
        "new_ids": [],
        "judged_ids": [],
        "time_up": False,
        "log": [],
    }
    store.save_run(run)
    return run


def elapsed_minutes(run: dict) -> float:
    return (datetime.now(timezone.utc) - datetime.fromisoformat(run["started"])).total_seconds() / 60


def remaining(run: dict, kind: str) -> int:
    key = {"searches": "searches_per_week", "pages": "pages_per_week", "judged": "startups_judged_per_week"}[kind]
    return max(0, int(run["limits"][key]) - int(run["counts"][kind]))


def counts_line(run: dict) -> str:
    c, l = run["counts"], run["limits"]
    mins = run.get("minutes") if run.get("finished") else elapsed_minutes(run)
    return (f"{c['searches']} searches of {l['searches_per_week']}, {c['pages']} pages of {l['pages_per_week']}, "
            f"{c['judged']} startups judged of {l['startups_judged_per_week']}, {int(round(mins or 0))} minutes")


def log(run: dict, msg: str) -> None:
    run["log"].append(f"{now_iso()} {msg}")


def fail(run: dict, what: str, url: str, error: str) -> None:
    run["failures"].append({"what": what, "url": url, "error": clip(error, 200), "at": now_iso()})


# ---------------------------------------------------------------------------
# tickets
# ---------------------------------------------------------------------------
def _issue(store: Store, run: dict, task: dict, count: str | None) -> dict:
    tid = f"t{run['next_ticket']:03d}"
    run["next_ticket"] += 1
    task["ticket"] = tid
    task["submit_with"] = f"python -m hotstartups submit {tid} work/{tid}.result.json"
    meta = {"type": task["type"], "status": "issued", "issued": now_iso(), "counted": count, "key": task.get("key")}
    if count:
        run["counts"][count] += 1
    run["tickets"][tid] = meta
    store.work.mkdir(parents=True, exist_ok=True)
    write_json(store.work / f"{tid}.json", task)
    store.save_run(run)
    return task


def pending_tickets(store: Store, run: dict) -> list[dict]:
    out = []
    for tid, meta in run["tickets"].items():
        if meta["status"] == "issued":
            task = read_json(store.work / f"{tid}.json")
            if task:
                out.append(task)
            else:
                meta["status"] = "lost"  # work/ was wiped (new session); the step will be re-planned from state
    return out


EXPECT = {
    "search": ("Run one web search with exactly this query (WebSearch tool). Save a JSON file: "
               "{\"results\": [{\"title\", \"url\", \"snippet\"}], \"candidates\": [{\"name\", \"url\", \"country\", \"sector\", \"one_line\", \"article_url\"}]}. "
               "candidates = startups (private companies, not giants) named in the results; url = the company's own website if visible, else null; "
               "one_line = what it does, in plain English. Empty lists are fine."),
    "research_search": ("Run one web search with exactly this query (WebSearch tool) about the named startup. Save: "
                        "{\"results\": [{\"title\", \"url\", \"snippet\"}], \"website\": \"the startup's own website or null\", \"candidates\": []}. "
                        "candidates may list OTHER startups named in the results, same shape as a search ticket."),
    "extract": ("Read the text below (feed entries or a list page). Save: {\"candidates\": [{\"name\", \"url\", \"country\", \"sector\", \"one_line\", \"article_url\", \"date\"}]}. "
                "List every startup named (private, small companies; skip big companies, funds, and people). url = company website if given, else null; "
                "article_url = the entry link the name came from, if any. Empty list is fine."),
    "fetch_extract": ("This page could not be read directly. Use the WebFetch tool on the URL with a prompt asking for every startup named on the page "
                      "with country, sector, website and a one-line description as JSON. Save: {\"candidates\": [{\"name\", \"url\", \"country\", \"sector\", \"one_line\"}]}. "
                      "If the page cannot be read at all, save {\"candidates\": [], \"unreadable\": \"reason\"}."),
    "fetch": ("This page could not be read directly. Use the WebFetch tool on the URL with a prompt asking for the full plain text, or a faithful summary of "
              "what the company does, its product, customers, team, funding and any numbers. Save: {\"text\": \"...\"}. If unreadable: {\"text\": \"\", \"unreadable\": \"reason\"}."),
    "judge": ("Write the entry for this startup from the dossier, following the rulebook. Save exactly the schema in \"schema\". "
              "Plain English, no jargon, short sentences. Every fact needs the URL it came from; a fact with two independent URLs counts as confirmed. "
              "Do not invent numbers: when unknown, say null. If it is not a startup (public company, or team over the limit), set is_startup false and say why."),
    "struggling": ("From these search results, list startups that shut down, pivoted or stalled. Save: {\"items\": [{\"name\", \"country\", \"sector\", "
                   "\"what\": \"shut_down|pivoted|stalled\", \"why\": \"one plain sentence\", \"source_url\"}]}. Only real, named startups; skip big companies. Empty list is fine."),
    "trends": ("Group the entries into trends: ideas that several startups chase. Save: {\"trends\": [{\"name\": \"short name\", \"sentence\": \"one plain sentence on the pattern\", "
               "\"startup_ids\": [ids from the entries]}]}. 3 to 6 trends, each with at least 2 startups. Order by how many startups."),
    "gaps": ("Write 3 to 5 idea sparks: holes nobody in the entries fills yet (a profession, a region, a combination). Save: {\"gaps\": [\"one or two plain sentences each\"]}. "
             "Mention the entries that inspired each gap by name. These are suggestions, not facts."),
    "brief": ("Write 'this week in one minute': exactly three plain sentences a non-expert can read: what joined, the strongest newcomer and why, the trend of the week. "
              "Save: {\"sentences\": \"...\"}. Use only the numbers and names given."),
}

JUDGE_SCHEMA = {
    "is_startup": "true|false",
    "not_startup_reason": "string or null",
    "name": "string",
    "website": "url or null",
    "country": "country name in English",
    "region": "one of " + ", ".join(REGIONS),
    "industry": "one or two words, e.g. Health, Robotics, AI tools, Fintech, Energy, Agriculture, Logistics",
    "idea": "one sentence: the problem and what they do about it",
    "twist": "one sentence: what is special",
    "why_now": "one sentence",
    "who_pays": "one sentence",
    "lesson": "one sentence: the reusable pattern behind the idea",
    "facts": {
        "founded": {"value": "year or null", "sources": ["url"]},
        "based_in": {"value": "city, country or null", "sources": ["url"]},
        "team_size": {"value": "number or 'about N' or null", "sources": ["url"]},
        "raised": {"value": "e.g. $6M or null", "sources": ["url"]},
        "growth_signal": {"value": "one short sentence or null", "sources": ["url"]},
    },
    "scores": {
        "market": {"score": "0-10", "reason": "one sentence", "source": "url or null"},
        "tech": {"score": "0-10", "reason": "one sentence", "source": "url or null"},
        "growth": {"score": "0-10 or null when unknown", "reason": "one sentence", "basis": "number | substitute: <what> | unknown", "source": "url or null"},
    },
    "status_signal": "active | shut_down | pivoted | stalled",
    "sources": [{"url": "string", "title": "string", "date": "YYYY-MM-DD or null"}],
}


# ---------------------------------------------------------------------------
# planning: what comes next
# ---------------------------------------------------------------------------
def next_tasks(cfg: dict, sources: dict, store: Store, run: dict, batch: int = 5) -> dict:
    if run.get("finished"):
        return {"done": True, "reason": "run already finished", "counts": run["counts"]}
    pending = pending_tickets(store, run)
    if pending:
        store.save_run(run)
        return {"run": run["id"], "tasks": pending[:batch], "note": "these tickets were issued before and not submitted yet"}
    if elapsed_minutes(run) > run["limits"]["run_minutes"] and not run["time_up"]:
        run["time_up"] = True
        log(run, "time limit reached; skipping to synthesis")
        run["phase"] = "synthesis"
    tasks: list[dict] = []
    while not tasks:
        phase = run["phase"]
        if phase == "lists":
            tasks = _plan_lists(cfg, store, run, batch)
        elif phase == "feeds":
            tasks = _plan_feeds(cfg, sources, store, run, batch)
        elif phase == "discovery":
            tasks = _plan_discovery(cfg, sources, store, run, batch)
        elif phase == "research":
            tasks = _plan_research(cfg, sources, store, run, batch)
        elif phase == "struggling":
            tasks = _plan_struggling(cfg, sources, store, run, batch)
        elif phase == "synthesis":
            tasks = _plan_synthesis(cfg, store, run)
        else:
            store.save_run(run)
            return {"done": True, "reason": "all phases complete", "counts": run["counts"], "line": counts_line(run)}
        if not tasks:
            if run["phase"] == "done":
                store.save_run(run)
                return {"done": True, "reason": "all phases complete", "counts": run["counts"], "line": counts_line(run)}
            # a phase may end without issuing anything; move on
            if run["phase"] == phase:
                _advance(run)
    store.save_run(run)
    return {"run": run["id"], "tasks": tasks, "counts": run["counts"], "remaining": {k: remaining(run, k) for k in COUNTED}}


def _advance(run: dict) -> None:
    i = PHASES.index(run["phase"])
    run["phase"] = PHASES[min(i + 1, len(PHASES) - 1)]
    log(run, f"phase -> {run['phase']}")


# ---- lists ---------------------------------------------------------------
def _plan_lists(cfg: dict, store: Store, run: dict, batch: int) -> list[dict]:
    state = run["phase_state"]["lists"]
    tasks = []
    for site in load_list_sites(Path(cfg["_root"])):
        url = site["url"]
        if url in state:
            continue
        if remaining(run, "pages") <= 0:
            state[url] = "skipped: page limit"
            continue
        run["counts"]["pages"] += 1
        ok, text, title = get(url)
        if ok and len(text) > 200:
            path = cache_text(store.cache, "list-" + slugify(domain_of(url) or url)[:60], text)
            state[url] = "read"
            tasks.append(_issue(store, run, {
                "type": "extract", "key": url, "source_kind": "list", "source": site["note"] or url, "url": url,
                "title": title, "instructions": EXPECT["extract"], "text": clip(text, 24000), "cached_text": str(path),
            }, None))
            run["counts"]["extractions"] += 1
        else:
            reason = text if not ok else "page had almost no text"
            fail(run, "list site (direct)", url, reason)
            state[url] = "needs claude reader"
            tasks.append(_issue(store, run, {
                "type": "fetch_extract", "key": url, "source_kind": "list", "source": site["note"] or url, "url": url,
                "instructions": EXPECT["fetch_extract"],
            }, None))
        if len(tasks) >= batch:
            break
    if not tasks:
        _advance(run)
    return tasks


# ---- feeds ---------------------------------------------------------------
def _looks_like_startup_news(cfg: dict, title: str, summary: str) -> bool:
    """Keep an entry only when it talks about funding, a launch or a new company, in any of our languages."""
    words = cfg["feeds"].get("keywords") or []
    if not words:
        return True
    text = f"{title} {summary}".lower()
    return any(w.lower() in text for w in words)


def _plan_feeds(cfg: dict, sources: dict, store: Store, run: dict, batch: int) -> list[dict]:
    state = run["phase_state"]["feeds"]
    entries_path = store.work / "feed_entries.json"
    if not state["fetched"] or not entries_path.exists():
        seen = store.seen_links()
        entries = []
        skipped = 0
        for region, rdef in sources.get("regions", {}).items():
            for feed in rdef.get("feeds", []):
                ok, items, err = read_feed(feed["url"])
                if not ok:
                    fail(run, "feed", feed["url"], err)
                    continue
                run["counts"]["feeds_read"] += 1
                for e in items:
                    if not e["link"] or e["link"] in seen:
                        continue
                    seen[e["link"]] = run["id"]
                    if not _looks_like_startup_news(cfg, e["title"], e["summary"]):
                        skipped += 1
                        continue
                    entries.append({"region": region, "feed": feed["name"], "title": e["title"], "link": e["link"], "summary": clip(e["summary"], 200), "date": e["date"][:25]})
        store.save_seen_links(seen)
        write_json(entries_path, entries)
        state["fetched"] = True
        state["total_entries"] = len(entries)
        state["skipped_entries"] = skipped
        log(run, f"feeds: {run['counts']['feeds_read']} read, {len(entries)} entries kept, {skipped} skipped as not startup news")
    entries = read_json(entries_path, [])
    size = int(cfg["feeds"]["batch_size"])
    max_batches = min(int(cfg["feeds"]["max_batches"]), max(1, int(run["limits"]["pages_per_week"] * 0.4)))
    tasks = []
    while state["batches"] < max_batches and remaining(run, "pages") > 0 and len(tasks) < batch:
        start = state["batches"] * size
        chunk = entries[start:start + size]
        if not chunk:
            break
        state["batches"] += 1
        text = "\n\n".join(f"[{e['feed']} | {e['region']} | {e['date']}] {e['title']}\n{e['link']}\n{e['summary']}" for e in chunk)
        run["counts"]["extractions"] += 1
        tasks.append(_issue(store, run, {
            "type": "extract", "key": f"feeds-{state['batches']}", "source_kind": "feed", "source": "news feeds",
            "instructions": EXPECT["extract"], "text": text,
        }, "pages"))
    if not tasks:
        _advance(run)
    return tasks


# ---- discovery searches ---------------------------------------------------
def _month_year_for(lang: str) -> str:
    d = date.today()
    return month_year(lang, d.year, d.month)


def _fill(template: str, lang: str, **kw) -> str:
    d = date.today()
    values = {"month_year": _month_year_for(lang), "year": str(d.year), "season": "Summer" if d.month in (6, 7, 8) else "Winter" if d.month in (12, 1, 2) else "Spring" if d.month < 6 else "Fall"}
    values.update(kw)
    out = template
    for k, v in values.items():
        out = out.replace("{" + k + "}", str(v or ""))
    return re.sub(r"\s+", " ", out).strip()


def _backlog(store: Store) -> int:
    """Startups found but not yet judged."""
    return sum(1 for r in store.startups().values() if r["status"] == "candidate")


def _plan_discovery(cfg: dict, sources: dict, store: Store, run: dict, batch: int) -> list[dict]:
    state = run["phase_state"]["discovery"]
    cap = int(cfg["discovery"].get("skip_when_backlog_over", 0))
    if cap and not state.get("_started") and _backlog(store) >= cap:
        log(run, f"discovery skipped: {_backlog(store)} startups already waiting to be judged (limit {cap})")
        state["skipped"] = True
        _advance(run)
        return []
    state["_started"] = True
    yields = read_json(store.data / "query_yield.json", {}) or {}
    budget = int(round(float(cfg["discovery"]["share_of_searches"]) * int(run["limits"]["searches_per_week"])))
    regions = sources.get("regions", {})
    tasks = []
    # allocate per region by share (at least 1 each when budget allows), rotate templates by run number
    alloc = {}
    for region, rdef in regions.items():
        alloc[region] = max(1, int(round(budget * float(rdef.get("share", 0)))))
    for region, rdef in regions.items():
        st = state.setdefault(region, {"issued": 0})
        templates = []
        for lang, tl in (rdef.get("queries") or {}).items():
            for t in tl:
                y = yields.get(t, {})
                if int(y.get("uses", 0)) >= 2 and int(y.get("candidates", 0)) == 0:
                    continue  # tried twice, never named a startup: retired
                templates.append((lang, t))
        if not templates:
            continue
        used_now = st.setdefault("queries", [])
        while st["issued"] < alloc[region] and remaining(run, "searches") > 0 and len(tasks) < batch:
            lang, template = templates[(run["number"] * 3 + st["issued"]) % len(templates)]
            st["issued"] += 1
            query = _fill(template, lang)
            if query in used_now:
                continue  # the same words twice in one run would only repeat results
            used_now.append(query)
            tasks.append(_issue(store, run, {
                "type": "search", "key": f"discover-{region}-{st['issued']}", "purpose": "discover", "region": region, "language": lang,
                "template": template, "query": query, "instructions": EXPECT["search"],
            }, "searches"))
        if len(tasks) >= batch:
            break
    if not tasks:
        _advance(run)
    return tasks


# ---- research + judge ------------------------------------------------------
def _priority(rec: dict, run_id: str) -> float:
    """Who gets researched first: several independent sources, fresh news, a curated list, a known website.
    Ties are broken by a per-run pseudo-random value so a run never works through a list alphabetically."""
    import hashlib
    mentions = rec.get("mentions", [])
    kinds = {m.get("source_kind") for m in mentions}
    p = min(len(mentions), 6) * 1.0
    p += 1.5 * max(0, len(kinds) - 1)
    p += 1.0 if "list" in kinds else 0
    p += 1.0 if rec.get("website") else 0
    cutoff = (date.today() - timedelta(days=14)).isoformat()
    if any((m.get("date") or "")[:10] >= cutoff and (m.get("date") or "")[:4].isdigit() for m in mentions):
        p += 2.5  # fresh news beats a months-old list: the page is about what is happening now
    p += 4.0 if rec.get("research", {}).get("refresh") else 0
    p += int(hashlib.md5(f"{rec['id']}:{run_id}".encode()).hexdigest()[:4], 16) / 65535.0
    return p


def _research_queue(cfg: dict, store: Store, run: dict) -> list[dict]:
    """Startups to work on this run: refresh-due ones (capped) plus new candidates by priority."""
    st = run["phase_state"]["research"]
    days = int(cfg["research"]["refresh_after_days"])
    cap = int(round(float(cfg["research"]["refresh_share"]) * int(run["limits"]["startups_judged_per_week"])))
    if "refresh_ids" not in st or not st.get("refresh_selected"):
        due = []
        for rec in store.startups().values():
            if rec["status"] != "judged":
                continue
            from .util import days_between
            age = days_between(rec.get("last_checked"), today())
            if age is not None and age >= days:
                due.append(rec)
        due.sort(key=lambda r: r.get("last_checked") or "")
        st["refresh_ids"] = [r["id"] for r in due[:cap]]
        st["refresh_selected"] = True
        for r in due[:cap]:
            r["research"] = {"searches": 0, "pages": 0, "queries_done": [], "refresh": True}
            store.save_startup(r)
    queue = []
    for rec in store.startups().values():
        rs = rec.setdefault("research", {"searches": 0, "pages": 0, "queries_done": []})
        if rec["status"] == "dropped" or rec["status"] == "struggling":
            continue
        if rec["status"] == "judged" and not rs.get("refresh"):
            continue
        if rs.get("judged_run") == run["id"]:
            continue
        if not rec.get("name"):
            continue
        queue.append(rec)
    queue.sort(key=lambda r: _priority(r, run["id"]), reverse=True)
    return queue


def _research_queries(sources: dict, rec: dict) -> list[str]:
    name = rec["name"]
    country = rec.get("country") or ""
    langs = ["en"]
    lang_of_country = (sources.get("country_languages") or {}).get(country)
    if lang_of_country and lang_of_country != "en":
        langs.append(lang_of_country)
    rq = sources.get("research_queries") or {"en": ['"{name}" startup {country}', '"{name}" founders funding']}
    out = []
    for lang in langs:
        for t in rq.get(lang, []):
            q = _fill(t, lang, name=name, country=country)
            if q not in out:
                out.append(q)
    return out or [f'"{name}" startup {country}'.strip()]


def _search_room(cfg: dict, run: dict) -> int:
    """Searches still usable by research: the remaining budget minus what the struggling step needs."""
    reserve = int(cfg["struggling"]["searches"]) if run["phase_state"]["struggling"]["issued"] == 0 else 0
    return max(0, remaining(run, "searches") - reserve)


def _plan_research(cfg: dict, sources: dict, store: Store, run: dict, batch: int) -> list[dict]:
    if remaining(run, "judged") <= 0:
        _advance(run)
        return []
    per_s = int(cfg["research"]["searches_per_startup"])
    per_p = int(cfg["research"]["pages_per_startup"])
    max_chars = int(cfg["text"]["max_chars_per_page"])
    queue = _research_queue(cfg, store, run)
    active = queue[: remaining(run, "judged") + 2]
    tasks = []
    judges = 0
    for rec in active:
        rs = rec["research"]
        queries = _research_queries(sources, rec)
        todo = [q for q in queries if q not in rs["queries_done"]][: max(0, per_s - rs["searches"])]
        if todo and _search_room(cfg, run) > 0:
            q = todo[0]
            rs["searches"] += 1
            rs["queries_done"].append(q)
            store.save_startup(rec)
            tasks.append(_issue(store, run, {
                "type": "research_search", "key": f"research-{rec['id']}-{rs['searches']}", "purpose": "research", "startup_id": rec["id"],
                "startup_name": rec["name"], "query": q, "instructions": EXPECT["research_search"],
            }, "searches"))
            if len(tasks) >= batch:
                break
            continue
        if _has_pending_for(run, rec["id"]):
            continue
        # read the company website, then one article, directly when possible
        if rec.get("website") and not rs.get("site_read") and rs["pages"] < per_p and remaining(run, "pages") > 0:
            rs["site_read"] = True
            rs["pages"] += 1
            run["counts"]["pages"] += 1
            ok, text, title = get(rec["website"])
            if ok and len(text) > 100:
                p = cache_text(store.cache, f"site-{rec['id']}", clip(text, max_chars * 2))
                rs["site_text"] = str(p)
                store.save_startup(rec)
            else:
                fail(run, "company site (direct)", rec["website"], text if not ok else "almost no text")
                store.save_startup(rec)
                tasks.append(_issue(store, run, {
                    "type": "fetch", "key": f"site-{rec['id']}", "startup_id": rec["id"], "url": rec["website"], "instructions": EXPECT["fetch"],
                }, None))
                if len(tasks) >= batch:
                    break
                continue
        if not rs.get("article_read") and rs["pages"] < per_p and remaining(run, "pages") > 0:
            rs["article_read"] = True
            art = _best_article(rec)
            if art:
                rs["pages"] += 1
                run["counts"]["pages"] += 1
                ok, text, title = get(art)
                if ok and len(text) > 200:
                    p = cache_text(store.cache, f"article-{rec['id']}", clip(text, max_chars * 2))
                    rs["article_text"] = str(p)
                    rs["article_url"] = art
                else:
                    fail(run, "article (direct)", art, text if not ok else "almost no text")
            store.save_startup(rec)
        if judges < 3 and remaining(run, "judged") > judges:
            tasks.append(_issue(store, run, _judge_task(cfg, store, run, rec), "judged"))
            judges += 1
            if len(tasks) >= batch:
                break
    if not tasks:
        _advance(run)
    return tasks


def _has_pending_for(run: dict, startup_id: str) -> bool:
    return any(m["status"] == "issued" and (m.get("key") or "").endswith(startup_id) for m in run["tickets"].values()) or \
        any(m["status"] == "issued" and f"-{startup_id}-" in (m.get("key") or "") for m in run["tickets"].values())


def _best_article(rec: dict) -> str | None:
    for m in rec.get("mentions", []):
        u = m.get("url")
        if u and u != rec.get("website") and domain_of(u) != rec.get("domain") and u.startswith("http"):
            return u
    return None


def _judge_task(cfg: dict, store: Store, run: dict, rec: dict) -> dict:
    rs = rec["research"]
    cap = int(cfg["text"]["max_chars_per_dossier"])
    site_text = Path(rs["site_text"]).read_text(encoding="utf-8") if rs.get("site_text") and Path(rs["site_text"]).exists() else ""
    art_text = Path(rs["article_text"]).read_text(encoding="utf-8") if rs.get("article_text") and Path(rs["article_text"]).exists() else ""
    mentions = [{"source": m.get("source"), "kind": m.get("source_kind"), "url": m.get("url"), "title": m.get("title"), "date": m.get("date"), "snippet": clip(m.get("snippet"), 250)}
                for m in rec.get("mentions", []) if m.get("snippet") or m.get("title")][:12]
    dossier = {
        "startup_id": rec["id"], "name": rec["name"], "aliases": rec.get("aliases", []), "website": rec.get("website"), "country": rec.get("country"),
        "industry_guess": rec.get("industry"), "mentions": mentions,
        "website_text": clip(site_text, cap // 4), "article_url": rs.get("article_url"), "article_text": clip(art_text, cap // 5),
        "previous_entry": rec.get("entry") if rs.get("refresh") else None,
    }
    return {
        "type": "judge", "key": f"judge-{rec['id']}", "startup_id": rec["id"], "instructions": EXPECT["judge"],
        "rulebook": "RULEBOOK.md in the hot_startups folder; read it once at the start of the run and apply it to every write-up",
        "startup_rule": {"max_team_size": cfg["startup_rule"]["max_team_size"], "must_be_private": True},
        "regions": REGIONS, "schema": JUDGE_SCHEMA, "dossier": dossier, "refresh": bool(rs.get("refresh")),
    }


# ---- struggling ------------------------------------------------------------
def _plan_struggling(cfg: dict, sources: dict, store: Store, run: dict, batch: int) -> list[dict]:
    st = run["phase_state"]["struggling"]
    n = int(cfg["struggling"]["searches"])
    templates = []
    for lang, tl in (sources.get("struggling_queries") or {}).items():
        for t in tl:
            templates.append((lang, t))
    tasks = []
    while templates and st["issued"] < n and remaining(run, "searches") > 0 and len(tasks) < batch:
        lang, t = templates[(run["number"] * 2 + st["issued"]) % len(templates)]
        st["issued"] += 1
        tasks.append(_issue(store, run, {
            "type": "search", "key": f"struggling-{st['issued']}", "purpose": "struggling", "language": lang, "query": _fill(t, lang),
            "instructions": EXPECT["search"] + " For this ticket, candidates may be empty; the results themselves are what matters.",
        }, "searches"))
    if tasks:
        return tasks
    if st["issued"] and not st.get("ticket") and not any(m["status"] == "issued" and (m.get("key") or "").startswith("struggling-") for m in run["tickets"].values()):
        st["ticket"] = "issued"
        return [_issue(store, run, {"type": "struggling", "key": "struggling-summary", "instructions": EXPECT["struggling"], "results": st["results"][:80]}, None)]
    if not st["issued"] or st.get("ticket") == "done" or st.get("ticket") is None:
        _advance(run)
    return []


# ---- synthesis -------------------------------------------------------------
def _entries_summary(cfg: dict, store: Store) -> list[dict]:
    out = []
    for rec in store.startups().values():
        e = rec.get("entry")
        if rec["status"] == "judged" and e and e.get("is_startup"):
            out.append({"id": rec["id"], "name": rec["name"], "country": e.get("country"), "region": e.get("region"), "industry": e.get("industry"),
                        "idea": e.get("idea"), "lesson": e.get("lesson"), "overall": e.get("overall")})
    out.sort(key=lambda x: x.get("overall") or 0, reverse=True)
    return out[: int(cfg["page"]["top_n"]) + 30]


def _plan_synthesis(cfg: dict, store: Store, run: dict) -> list[dict]:
    st = run["phase_state"]["synthesis"]
    entries = _entries_summary(cfg, store)
    if not entries:
        log(run, "no judged entries; skipping synthesis")
        run["phase"] = "done"
        return []
    for step in ("trends", "gaps", "brief"):
        if st.get(step) == "done":
            continue
        if st.get(step) == "issued":
            return []
        st[step] = "issued"
        run["counts"]["synthesis"] += 1
        payload = {"type": step, "key": f"synthesis-{step}", "instructions": EXPECT[step], "entries": entries}
        if step in ("gaps", "brief"):
            payload["trends"] = st.get("trends_result", [])
        if step == "brief":
            new_ids = set(run["new_ids"])
            payload["numbers"] = {"on_the_list": min(len(entries), int(cfg["page"]["top_n"])), "new_this_week": len([e for e in entries if e["id"] in new_ids]), "trends": len(st.get("trends_result", []))}
            payload["newcomers"] = [e for e in entries if e["id"] in new_ids][:10]
        return [_issue(store, run, payload, None)]
    run["phase"] = "done"
    return []


# ---------------------------------------------------------------------------
# submissions
# ---------------------------------------------------------------------------
def submit(cfg: dict, sources: dict, store: Store, run: dict, tid: str, result: dict) -> dict:
    meta = run["tickets"].get(tid)
    if not meta:
        raise RunError(f"unknown ticket {tid}")
    if meta["status"] == "done":
        return {"ok": True, "note": "already submitted", "counts": run["counts"]}
    task = read_json(store.work / f"{tid}.json")
    if not task:
        raise RunError(f"ticket {tid} has no task file in work/; run `next` again")
    if not isinstance(result, dict):
        raise RunError("result must be a JSON object")
    t = task["type"]
    handler = {
        "search": _take_search, "research_search": _take_search, "extract": _take_candidates, "fetch_extract": _take_candidates,
        "fetch": _take_fetch, "judge": _take_judge, "struggling": _take_struggling, "trends": _take_trends, "gaps": _take_gaps, "brief": _take_brief,
    }[t]
    summary = handler(cfg, sources, store, run, task, result)
    meta["status"] = "done"
    meta["submitted"] = now_iso()
    write_json(store.work / f"{tid}.result.json", result)
    store.save_run(run)
    return {"ok": True, "ticket": tid, "type": t, "summary": summary, "counts": run["counts"], "remaining": {k: remaining(run, k) for k in COUNTED}}


def _merge_candidate(store: Store, run: dict, cand: dict, source_kind: str, source: str, region: str | None = None) -> dict | None:
    name = (cand.get("name") or "").strip()
    if not name or len(name) > 80:
        return None
    url = cand.get("url") if looks_like_company_site(cand.get("url")) else None
    rec = store.find(name=name, url=url)
    if rec is None:
        rec = store.new_startup(name, url)
    else:
        store.add_alias(rec, name)
    store.set_website(rec, url)
    if cand.get("country") and not rec.get("country"):
        rec["country"] = str(cand["country"])[:60]
    if cand.get("sector") and not rec.get("industry"):
        rec["industry"] = str(cand["sector"])[:40]
    if region and not rec.get("region"):
        rec["region"] = region
    mention = {"source_kind": source_kind, "source": source, "url": cand.get("article_url") or cand.get("url"), "title": clip(cand.get("title") or "", 200),
               "date": (cand.get("date") or "")[:25], "snippet": clip(cand.get("one_line") or cand.get("snippet") or "", 300), "run": run["id"]}
    if not any(m.get("url") == mention["url"] and m.get("snippet") == mention["snippet"] for m in rec["mentions"]):
        rec["mentions"].append(mention)
    store.save_startup(rec)
    return rec


def _take_search(cfg, sources, store, run, task, result) -> str:
    results = result.get("results") or []
    if not isinstance(results, list):
        raise RunError("results must be a list")
    n_c = 0
    if task.get("purpose") == "struggling":
        st = run["phase_state"]["struggling"]
        for r in results[:15]:
            st["results"].append({"query": task["query"], "title": clip(r.get("title"), 200), "url": r.get("url"), "snippet": clip(r.get("snippet"), 400)})
    if task.get("purpose") == "research":
        rec = store.startups().get(task["startup_id"])
        if rec:
            for r in results[:10]:
                if r.get("url"):
                    m = {"source_kind": "research", "source": "web search", "url": r["url"], "title": clip(r.get("title"), 200), "date": "", "snippet": clip(r.get("snippet"), 400), "run": run["id"]}
                    if not any(x.get("url") == m["url"] for x in rec["mentions"]):
                        rec["mentions"].append(m)
            store.set_website(rec, result.get("website"))
            store.save_startup(rec)
    for c in (result.get("candidates") or [])[:40]:
        if isinstance(c, dict) and _merge_candidate(store, run, c, "search", task.get("query", "web search"), task.get("region")):
            n_c += 1
    if task.get("purpose") == "discover" and task.get("template"):
        path = store.data / "query_yield.json"
        yields = read_json(path, {}) or {}
        y = yields.setdefault(task["template"], {"uses": 0, "candidates": 0})
        y["uses"] += 1
        y["candidates"] += n_c
        write_json(path, yields)
    return f"{len(results)} results, {n_c} candidates"


def _take_candidates(cfg, sources, store, run, task, result) -> str:
    cands = result.get("candidates")
    if not isinstance(cands, list):
        raise RunError("candidates must be a list")
    if result.get("unreadable"):
        run["unread_sites"].append({"url": task.get("url"), "reason": clip(str(result["unreadable"]), 200)})
        run["phase_state"]["lists"][task.get("url", "")] = "unreadable"
    n = 0
    for c in cands[:200]:
        if isinstance(c, dict) and _merge_candidate(store, run, c, task.get("source_kind", "list"), task.get("source", "list"), None):
            n += 1
    return f"{n} candidates"


def _take_fetch(cfg, sources, store, run, task, result) -> str:
    rec = store.startups().get(task["startup_id"])
    text = result.get("text") or ""
    if rec and text:
        p = cache_text(store.cache, f"site-{rec['id']}", clip(text, int(cfg["text"]["max_chars_per_page"]) * 2))
        rec["research"]["site_text"] = str(p)
        store.save_startup(rec)
    elif result.get("unreadable"):
        run["unread_sites"].append({"url": task.get("url"), "reason": clip(str(result["unreadable"]), 200)})
    return f"{len(text)} chars"


def _score(v):
    try:
        if v is None:
            return None
        f = float(v)
        return max(0.0, min(10.0, f))
    except (TypeError, ValueError):
        return None


def _take_judge(cfg, sources, store, run, task, result) -> str:
    rec = store.startups().get(task["startup_id"])
    if not rec:
        raise RunError("startup vanished")
    for key in ("is_startup", "idea", "scores"):
        if key not in result:
            raise RunError(f"judge result is missing '{key}'")
    scores = result.get("scores") or {}
    m, t, g = (_score((scores.get(k) or {}).get("score")) for k in ("market", "tech", "growth"))
    known = [x for x in (m, t, g) if x is not None]
    overall = round(sum(known) / len(known), 1) if known else None
    entry = dict(result)
    entry["overall"] = overall
    entry["judged_run"] = run["id"]
    entry["judged_on"] = today()
    # confirmed = at least two distinct source domains
    for fk, fv in (entry.get("facts") or {}).items():
        if isinstance(fv, dict):
            doms = {domain_of(u) for u in (fv.get("sources") or []) if u}
            fv["confirmed"] = len(doms - {None}) >= 2
    if result.get("website") and not rec.get("website"):
        store.set_website(rec, result["website"])
    for k in ("country", "industry"):
        if result.get(k):
            rec[k] = str(result[k])[:60]
    if result.get("region") in REGIONS:
        rec["region"] = result["region"]
    if result.get("name") and result["name"].strip() and result["name"].strip() != rec["name"]:
        store.add_alias(rec, rec["name"])
        rec["name"] = result["name"].strip()
    rec["entry"] = entry
    rec["last_checked"] = today()
    rec["research"]["judged_run"] = run["id"]
    rec["research"]["refresh"] = False
    first_time = not rec.get("first_judged_run")
    if not result.get("is_startup"):
        rec["status"] = "dropped"
        rec["drop_reason"] = clip(result.get("not_startup_reason") or "not a startup", 300)
    elif result.get("status_signal") in ("shut_down", "pivoted", "stalled"):
        rec["status"] = "struggling"
        rec["struggling"] = {"what": result["status_signal"], "why": clip((scores.get("growth") or {}).get("reason") or entry.get("why_now") or "", 300), "run": run["id"]}
    else:
        rec["status"] = "judged"
        if first_time:
            rec["first_judged_run"] = run["id"]
            run["new_ids"].append(rec["id"])
    rec["score_history"].append({"run": run["id"], "date": today(), "market": m, "tech": t, "growth": g, "overall": overall})
    run["judged_ids"].append(rec["id"])
    store.save_startup(rec)
    return f"{rec['name']}: {rec['status']}, overall {overall}"


def _take_struggling(cfg, sources, store, run, task, result) -> str:
    items = result.get("items")
    if not isinstance(items, list):
        raise RunError("items must be a list")
    clean = []
    for it in items[:12]:
        if isinstance(it, dict) and it.get("name") and it.get("what") in ("shut_down", "pivoted", "stalled"):
            clean.append({"name": clip(it["name"], 80), "country": clip(it.get("country") or "", 40), "sector": clip(it.get("sector") or "", 40),
                          "what": it["what"], "why": clip(it.get("why") or "", 300), "source_url": it.get("source_url")})
    run["phase_state"]["struggling"]["items"] = clean
    run["phase_state"]["struggling"]["ticket"] = "done"
    return f"{len(clean)} struggling"


def _take_trends(cfg, sources, store, run, task, result) -> str:
    trends = result.get("trends")
    if not isinstance(trends, list):
        raise RunError("trends must be a list")
    ids = set(store.startups())
    clean = []
    for tr in trends[:8]:
        if isinstance(tr, dict) and tr.get("name"):
            sids = [s for s in (tr.get("startup_ids") or []) if s in ids]
            clean.append({"name": clip(tr["name"], 60), "sentence": clip(tr.get("sentence") or "", 300), "startup_ids": sids})
    st = run["phase_state"]["synthesis"]
    st["trends_result"] = clean
    st["trends"] = "done"
    return f"{len(clean)} trends"


def _take_gaps(cfg, sources, store, run, task, result) -> str:
    gaps = result.get("gaps")
    if not isinstance(gaps, list):
        raise RunError("gaps must be a list")
    st = run["phase_state"]["synthesis"]
    st["gaps_result"] = [clip(str(g), 400) for g in gaps[:6] if str(g).strip()]
    st["gaps"] = "done"
    return f"{len(st['gaps_result'])} gaps"


def _take_brief(cfg, sources, store, run, task, result) -> str:
    s = result.get("sentences")
    if not isinstance(s, str) or not s.strip():
        raise RunError("sentences must be a non-empty string")
    st = run["phase_state"]["synthesis"]
    st["brief_result"] = clip(s.strip(), 700)
    st["brief"] = "done"
    return "brief saved"


# ---------------------------------------------------------------------------
# finishing: the week file the page is built from
# ---------------------------------------------------------------------------
def finish_run(cfg: dict, store: Store, run: dict) -> dict:
    entries = []
    for rec in store.startups().values():
        e = rec.get("entry")
        if rec["status"] == "judged" and e and e.get("is_startup") and e.get("overall") is not None:
            entries.append(rec)
    entries.sort(key=lambda r: (r["entry"]["overall"], r.get("first_judged_run") or ""), reverse=True)
    top = entries[: int(cfg["page"]["top_n"])]
    mix: dict[str, int] = {}
    for r in top:
        region = r.get("region") or "other"
        mix[region] = mix.get(region, 0) + 1
    st = run["phase_state"]["synthesis"]
    strug = list(run["phase_state"]["struggling"].get("items") or [])
    for rec in store.startups().values():
        if rec["status"] == "struggling" and rec.get("struggling", {}).get("run") == run["id"]:
            strug.insert(0, {"name": rec["name"], "country": rec.get("country") or "", "sector": rec.get("industry") or "", "what": rec["struggling"]["what"], "why": rec["struggling"]["why"], "source_url": rec.get("website")})
    if not run.get("finished"):
        run["finished"] = now_iso()
        run["minutes"] = round(elapsed_minutes(run), 1)
        run["phase"] = "done"
    unread = list(run["unread_sites"])
    for f in run["failures"]:
        if f["what"].startswith("list site") and not any(u.get("url") == f["url"] for u in unread):
            if run["phase_state"]["lists"].get(f["url"]) == "unreadable":
                continue
    week = {
        "run_id": run["id"], "number": run["number"], "week_of": run["week_of"], "generated": now_iso(), "title": cfg["page"]["title"],
        "brief": st.get("brief_result") or "", "trends": st.get("trends_result") or [], "gaps": st.get("gaps_result") or [], "struggling": strug[:8],
        "mix": mix, "top_ids": [r["id"] for r in top], "new_ids": [r["id"] for r in top if r.get("first_judged_run") == run["id"]],
        "counts": run["counts"], "limits": run["limits"], "minutes": run.get("minutes"), "run_line": counts_line(run),
        "unread_sites": unread, "failures": len(run["failures"]), "feeds_read": run["counts"]["feeds_read"], "time_up": run["time_up"],
        "last_success": today(), "total_known": len(store.startups()), "total_judged": len(entries),
    }
    store.save_week(week)
    store.save_run(run)
    return week
