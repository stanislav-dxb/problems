"""Rebuild a run's memory from the published page, for runs whose session published but did not push.

Usage: python -m hotstartups.recover <saved page html> <run id> <week_of YYYY-MM-DD> <finished ISO datetime>
"""
from __future__ import annotations

import html as H
import re
import sys
from pathlib import Path

from .config import REGIONS, load_config, load_sources
from .store import Store


def text(s: str) -> str:
    return H.unescape(re.sub(r"<[^>]+>", "", s)).strip()


def fact(block: str, label: str) -> dict:
    m = re.search(r"<dt>%s</dt><dd>(.*?)</dd>" % re.escape(label), block, re.S)
    if not m:
        return {"value": None, "sources": [], "confirmed": False}
    raw = m.group(1)
    rep = re.search(r'<small>reported by <a href="([^"]*)">', raw)
    val = text(re.sub(r"<small>.*?</small>", "", raw, flags=re.S)).strip()
    if val in ("unknown", ""):
        return {"value": None, "sources": [], "confirmed": False}
    if rep:
        return {"value": val, "sources": [rep.group(1)], "confirmed": False}
    if "unverified" in raw:
        return {"value": val, "sources": [], "confirmed": False}
    return {"value": val, "sources": [], "confirmed": True, "note": "confirmed by two sources in the run; the URLs were not saved"}


def recover(html_path: str, run_id: str, week_of: str, finished: str) -> dict:
    cfg = load_config(); sources = load_sources(); store = Store(cfg["_root"])
    src = Path(html_path).read_text(encoding="utf-8")
    day = finished[:10]
    regions_of = sources.get("country_regions") or {}
    rows = re.findall(r'<li class="row" data-new="(\d)" data-ind="([^"]*)" data-score="([^"]*)" data-growth="([^"]*)">(.*?)</details>\s*</li>', src, re.S)
    recovered = 0; top_ids = []; new_ids = []
    for is_new, ind, score, growth, block in rows:
        name = H.unescape(re.search(r'<span class="name">(.*?)</span>', block).group(1))
        meta = H.unescape(re.search(r'<span class="meta">(.*?)</span>', block).group(1))
        country = meta.split(" · ")[-1].strip()
        idea = text(re.search(r'<p class="idea">(.*?)</p>', block, re.S).group(1))

        def sec(h):
            m = re.search(r"<h4>%s</h4><p>(.*?)</p>" % h, block, re.S)
            return text(m.group(1)) if m else ""
        reasons = dict(re.findall(r"<li><b>(\w+)</b>: (.*?)</li>", re.search(r"<h4>Why these scores</h4><ul class=\"sources\">(.*?)</ul>", block, re.S).group(1), re.S))
        subs = dict(re.findall(r"<span>(Market|Tech|Growth)<b(?: class=\"q\")?>(.*?)</b></span>", block))

        def sc(k):
            v = subs.get(k, "?")
            return None if v == "?" else float(v)
        gm = re.search(r'<dt>Growth</dt><dd>.*?<small>\((substitute: [^<)]*|unknown)\)</small>', block, re.S)
        srcblock = re.search(r"<h4>Where this comes from</h4><ul class=\"sources\">(.*?)</ul>", block, re.S).group(1)
        website = None; srcs = []
        for href, title, date in re.findall(r'<li><a href="([^"]*)">(.*?)</a>(?: <small>(.*?)</small>)?</li>', srcblock, re.S):
            if H.unescape(title) == "Company website":
                website = href
                continue
            srcs.append({"url": href, "title": H.unescape(title), "date": date or None})
        rec = store.find(name=name, url=website) or store.new_startup(name, website)
        if website and not rec.get("website"):
            store.set_website(rec, website)
        top_ids.append(rec["id"])
        e0 = rec.get("entry") or {}
        if e0 and e0.get("idea") == idea and e0.get("twist") == sec("The twist") and rec["status"] == "judged":
            continue  # unchanged since an earlier run: nothing to recover
        region = rec.get("region") if rec.get("region") in REGIONS else regions_of.get(country, "europe")
        entry = {
            "is_startup": True, "not_startup_reason": None, "name": name, "website": website, "country": country, "region": region, "industry": ind,
            "idea": idea, "twist": sec("The twist"), "why_now": sec("Why now"), "who_pays": sec("Who pays"), "lesson": sec("The lesson"),
            "facts": {"founded": fact(block, "Founded"), "based_in": fact(block, "Based in"), "team_size": fact(block, "Team"), "raised": fact(block, "Raised"), "growth_signal": fact(block, "Growth")},
            "scores": {"market": {"score": sc("Market"), "reason": H.unescape(reasons.get("Market", "")), "source": None},
                       "tech": {"score": sc("Tech"), "reason": H.unescape(reasons.get("Tech", "")), "source": None},
                       "growth": {"score": sc("Growth"), "reason": H.unescape(reasons.get("Growth", "")), "basis": (gm.group(1) if gm else ("number" if sc("Growth") is not None else "unknown")), "source": None}},
            "status_signal": "active", "sources": srcs, "overall": float(score), "judged_run": run_id, "judged_on": day, "recovered_from_page": True,
        }
        rec["entry"] = entry; rec["country"] = country; rec["industry"] = ind; rec["region"] = region
        rec["status"] = "judged"; rec["last_checked"] = day
        rec.setdefault("research", {})["judged_run"] = run_id; rec["research"]["refresh"] = False
        if not rec.get("first_judged_run"):
            rec["first_judged_run"] = run_id; new_ids.append(rec["id"])
        rec.setdefault("score_history", []).append({"run": run_id, "date": day, "market": sc("Market"), "tech": sc("Tech"), "growth": sc("Growth"), "overall": float(score)})
        store.save_startup(rec); recovered += 1

    brief = text(re.search(r'<p class="lead">(.*?)</p>', src, re.S).group(1))
    trends = []
    for tname, count, sentence, namesblock in re.findall(r'<li class="trend"><div class="head"><strong>(.*?)</strong><span class="count">(\d+) startups?</span></div><p>(.*?)</p><div class="names">(.*?)</div></li>', src, re.S):
        ids = [r["id"] for r in (store.find(name=H.unescape(n)) for n in re.findall(r"<span>(.*?)</span>", namesblock)) if r]
        trends.append({"name": H.unescape(tname), "sentence": text(sentence), "startup_ids": ids})
    gm2 = re.search(r'<section class="gaps".*?<ul>(.*?)</ul>', src, re.S)
    gaps = [text(g) for g in re.findall(r'<li>(.*?)</li>', gm2.group(1), re.S)] if gm2 else []
    labels = {"Shut down": "shut_down", "Pivoted": "pivoted", "Stalled": "stalled"}
    strug = [{"what": labels.get(w, "shut_down"), "name": H.unescape(n), "sector": H.unescape(s), "country": H.unescape(c) if H.unescape(c) != "unknown" else "", "why": text(why), "source_url": link or None}
             for w, n, s, c, why, link in re.findall(r'<li><span class="pill unk">(.*?)</span><div><span class="name">(.*?)</span> <span class="meta">(.*?) · (.*?)</span><p>(.*?)(?: <a href="([^"]*)">source</a>)?</p></div></li>', src, re.S)]
    mix: dict[str, int] = {}
    for i in top_ids:
        reg = store.startups()[i].get("region") or "other"
        mix[reg] = mix.get(reg, 0) + 1
    foot = re.search(r"This week's run: ([^<]*?)\. Last successful run: ([^.]*)\. Feeds read: (\d+)\. Known startups in memory: (\d+)\.", src)
    line = foot.group(1)
    nums = [int(x) for x in re.findall(r"\d+", line)]
    counts = {"searches": nums[0], "pages": nums[2], "judged": nums[4], "feeds_read": int(foot.group(3)), "extractions": None, "synthesis": 3}
    limits = {"searches_per_week": nums[1], "pages_per_week": nums[3], "startups_judged_per_week": nums[5], "run_minutes": 90}
    number = int(re.search(r"-r(\d+)$", run_id).group(1))
    week = {"run_id": run_id, "number": number, "week_of": week_of, "generated": finished, "title": cfg["page"]["title"], "brief": brief, "trends": trends, "gaps": gaps,
            "struggling": strug, "mix": mix, "top_ids": top_ids, "new_ids": [i for i in top_ids if store.startups()[i].get("first_judged_run") == run_id], "counts": counts, "limits": limits,
            "minutes": nums[6], "run_line": line, "unread_sites": [], "failures": None, "feeds_read": int(foot.group(3)), "time_up": False, "last_success": day,
            "total_known": int(foot.group(4)), "total_judged": len(top_ids), "recovered_from_page": True}
    store.save_week(week)
    store.save_run({"id": run_id, "number": number, "week_of": week_of, "started": finished, "finished": finished, "limits": limits, "first_run": False, "counts": counts, "phase": "done",
                    "tickets": {}, "next_ticket": 1, "phase_state": {}, "failures": [], "unread_sites": [], "new_ids": week["new_ids"], "judged_ids": [], "time_up": False, "minutes": nums[6],
                    "log": ["recovered from the published page: the run's session published the page but did not push its data"], "recovered_from_page": True})
    return {"recovered": recovered, "on_page": len(top_ids), "new": len(week["new_ids"]), "trends": len(trends), "struggling": len(strug), "gaps": len(gaps), "mix": mix, "line": line, "known_on_page": int(foot.group(4)), "known_now": len(store.startups())}


if __name__ == "__main__":
    print(recover(*sys.argv[1:5]))
