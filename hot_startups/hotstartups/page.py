"""Renders the weekly page (out/index.html) from data/weeks/latest.json and the startup files."""
from __future__ import annotations

from html import escape as esc
from pathlib import Path

from .config import REGION_NAMES
from .store import Store

CSS = """
  :root { color-scheme: light; --bg:#f3f4f6; --surface:#fff; --surface-2:#f8f9fb; --ink:#16202b; --ink-2:#4b5563; --muted:#737d89; --line:#dfe3e8;
    --accent:#2a78d6; --accent-ink:#fff; --meter-track:#cde2fb; --meter-fill:#2a78d6; --new-bg:#fdeae0; --new-ink:#9a3a10; --unk-bg:#eceef1; --unk-ink:#5b6570; --shadow:0 1px 2px rgba(22,32,43,.06); }
  @media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) { color-scheme: dark; --bg:#0f1216; --surface:#181c22; --surface-2:#1f242b; --ink:#f2f4f7; --ink-2:#c0c6ce; --muted:#8b939e; --line:#2a3038;
    --accent:#3987e5; --accent-ink:#fff; --meter-track:#104281; --meter-fill:#3987e5; --new-bg:#3a2016; --new-ink:#f5a27a; --unk-bg:#262b32; --unk-ink:#aab2bc; --shadow:none; } }
  :root[data-theme="dark"] { color-scheme: dark; --bg:#0f1216; --surface:#181c22; --surface-2:#1f242b; --ink:#f2f4f7; --ink-2:#c0c6ce; --muted:#8b939e; --line:#2a3038;
    --accent:#3987e5; --accent-ink:#fff; --meter-track:#104281; --meter-fill:#3987e5; --new-bg:#3a2016; --new-ink:#f5a27a; --unk-bg:#262b32; --unk-ink:#aab2bc; --shadow:none; }
  * { box-sizing: border-box; }
  body { margin:0; background:var(--bg); color:var(--ink); font-family:"IBM Plex Sans", system-ui, -apple-system, "Segoe UI", sans-serif; font-size:15px; line-height:1.5; padding-block:20px 48px; padding-inline:clamp(16px, 4vw, 40px); }
  .wrap { max-width:1080px; margin:0 auto; display:grid; gap:28px; }
  h1,h2,h3,h4 { font-family:"Sora","IBM Plex Sans",system-ui,sans-serif; margin:0; text-wrap:balance; }
  h1 { font-size:26px; font-weight:700; letter-spacing:-0.01em; } h2 { font-size:17px; font-weight:600; }
  h4 { font-size:12px; font-weight:600; text-transform:uppercase; letter-spacing:.06em; color:var(--muted); margin-bottom:4px; }
  p { margin:0; } a { color:var(--accent); } button,select { font:inherit; color:inherit; }
  :is(button,select,summary):focus-visible { outline:2px solid var(--accent); outline-offset:2px; }
  .top { display:flex; flex-wrap:wrap; align-items:baseline; justify-content:space-between; gap:8px 16px; }
  .brand { display:flex; align-items:baseline; gap:14px; flex-wrap:wrap; } .week { color:var(--ink-2); }
  .card { background:var(--surface); border:1px solid var(--line); border-radius:12px; padding:20px 22px; box-shadow:var(--shadow); display:grid; gap:14px; }
  .card p.lead { max-width:68ch; font-size:16px; }
  .kpis { display:flex; flex-wrap:wrap; gap:8px 28px; color:var(--ink-2); }
  .kpis b { font-family:"Sora",system-ui,sans-serif; font-size:22px; font-weight:600; color:var(--ink); margin-right:6px; }
  .mix { color:var(--ink-2); font-size:14px; }
  .section { display:grid; gap:12px; }
  .trend-list { list-style:none; margin:0; padding:0; display:grid; grid-template-columns:repeat(auto-fit, minmax(260px, 1fr)); gap:12px; }
  .trend { background:var(--surface); border:1px solid var(--line); border-radius:12px; padding:16px 18px; display:grid; gap:8px; align-content:start; box-shadow:var(--shadow); }
  .trend .head { display:flex; justify-content:space-between; gap:8px; align-items:baseline; }
  .trend .head strong { font-family:"Sora",system-ui,sans-serif; font-weight:600; font-size:15px; } .trend .count { color:var(--muted); font-size:13px; white-space:nowrap; }
  .trend p { color:var(--ink-2); } .names { color:var(--ink-2); font-size:13px; } .names span { display:inline-block; padding:2px 8px; border:1px solid var(--line); border-radius:999px; margin:2px 4px 2px 0; }
  .strug { list-style:none; margin:0; padding:0; display:grid; gap:8px; }
  .strug li { background:var(--surface); border:1px solid var(--line); border-radius:12px; padding:12px 16px; display:grid; grid-template-columns:auto 1fr; gap:12px; align-items:start; box-shadow:var(--shadow); }
  .strug .pill { margin-top:3px; white-space:nowrap; } .strug p { color:var(--ink-2); max-width:70ch; }
  .gaps { background:var(--surface); border:1px solid var(--line); border-radius:12px; padding:18px 22px; display:grid; gap:10px; box-shadow:var(--shadow); }
  .gaps .head { display:flex; flex-wrap:wrap; align-items:center; gap:10px; } .gaps ul { margin:0; padding-left:20px; display:grid; gap:8px; max-width:72ch; }
  .toolbar { display:flex; flex-wrap:wrap; gap:10px 18px; align-items:center; }
  .seg { display:inline-flex; border:1px solid var(--line); border-radius:8px; overflow:hidden; background:var(--surface); }
  .seg button { border:0; background:transparent; padding:7px 12px; cursor:pointer; color:var(--ink-2); } .seg button.on { background:var(--accent); color:var(--accent-ink); }
  .chips { display:flex; flex-wrap:wrap; gap:6px; } .chips button { border:1px solid var(--line); background:var(--surface); border-radius:999px; padding:4px 11px; cursor:pointer; color:var(--ink-2); font-size:13px; }
  .chips button.on { border-color:var(--accent); color:var(--accent); }
  .sort { margin-left:auto; color:var(--ink-2); display:flex; gap:6px; align-items:center; } .sort select { border:1px solid var(--line); background:var(--surface); border-radius:8px; padding:6px 8px; }
  .startups { list-style:none; margin:0; padding:0; display:grid; gap:8px; }
  .row { background:var(--surface); border:1px solid var(--line); border-radius:12px; box-shadow:var(--shadow); } .row[hidden] { display:none; }
  details > summary { list-style:none; cursor:pointer; padding:14px 16px; display:grid; grid-template-columns:28px 1fr auto; grid-template-areas:"rank who score" "rank idea score" "rank subs subs"; column-gap:14px; row-gap:4px; align-items:start; }
  details > summary::-webkit-details-marker { display:none; }
  .rank { grid-area:rank; font-family:"Sora",system-ui,sans-serif; font-weight:600; color:var(--muted); font-variant-numeric:tabular-nums; padding-top:2px; }
  .who { grid-area:who; display:flex; flex-wrap:wrap; align-items:baseline; gap:4px 10px; } .name { font-family:"Sora",system-ui,sans-serif; font-weight:600; font-size:16px; } .meta { color:var(--muted); font-size:13px; }
  .pill { font-size:12px; font-weight:500; padding:1px 8px; border-radius:999px; } .pill.new { background:var(--new-bg); color:var(--new-ink); } .pill.unk { background:var(--unk-bg); color:var(--unk-ink); }
  .idea { grid-area:idea; max-width:62ch; }
  .score { grid-area:score; display:grid; justify-items:end; gap:4px; min-width:120px; } .score .num { font-family:"Sora",system-ui,sans-serif; font-weight:600; font-size:22px; line-height:1; }
  .score .lbl { font-size:11px; text-transform:uppercase; letter-spacing:.06em; color:var(--muted); }
  .meter { display:block; width:120px; height:6px; border-radius:3px; background:var(--meter-track); overflow:hidden; } .meter i { display:block; height:100%; background:var(--meter-fill); border-radius:3px; }
  .subs { grid-area:subs; display:flex; flex-wrap:wrap; gap:4px 16px; font-size:13px; color:var(--muted); margin-top:2px; }
  .subs b { color:var(--ink-2); font-weight:600; font-variant-numeric:tabular-nums; margin-left:4px; } .subs b.q { color:var(--unk-ink); } .subs .hint { font-size:12px; font-style:italic; }
  .detail { border-top:1px solid var(--line); padding:16px 16px 18px 58px; display:grid; grid-template-columns:1.4fr 1fr; gap:18px 32px; background:var(--surface-2); border-radius:0 0 12px 12px; }
  .detail .col { display:grid; gap:12px; align-content:start; } .detail p { max-width:60ch; }
  dl { margin:0; display:grid; grid-template-columns:auto 1fr; gap:4px 14px; } dt { color:var(--muted); font-size:13px; } dd { margin:0; } dd small { color:var(--muted); }
  .sources { margin:0; padding-left:18px; } .sources li { font-size:14px; }
  .foot { color:var(--muted); font-size:13px; max-width:80ch; } .unread { font-size:13px; color:var(--muted); } .unread summary { cursor:pointer; }
  .empty { color:var(--ink-2); }
  @media (max-width: 640px) { details > summary { grid-template-columns:24px 1fr; grid-template-areas:"rank who" "rank idea" "rank score" "rank subs"; } .score { justify-items:start; }
    .detail { grid-template-columns:1fr; padding-left:16px; } .sort { margin-left:0; } .strug li { grid-template-columns:1fr; } }
  @media (prefers-reduced-motion: no-preference) { .row { transition:border-color .15s; } .row:hover { border-color:var(--accent); } }
"""


RENDER_JS = r"""
(function () {
  var D = window.HOT || {}; var W = D.week || {}; var E = D.entries || [];
  var esc = function (s) { return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) { return ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[c]; }); };
  var nice = function (iso) { if (!iso) return ""; var d = new Date(iso.slice(0, 10) + "T00:00:00Z"); if (isNaN(d)) return iso;
    return d.getUTCDate() + " " + ["January","February","March","April","May","June","July","August","September","October","November","December"][d.getUTCMonth()] + " " + d.getUTCFullYear(); };
  var factHtml = function (f) {
    if (!f || f.value == null || f.value === "") return "<dd>unknown</dd>";
    var v = esc(f.value);
    if (f.confirmed) return "<dd>" + v + "</dd>";
    if (f.source) return '<dd>' + v + ' <small>reported by <a href="' + esc(f.source) + '">' + esc(f.source_domain || "source") + '</a></small></dd>';
    return "<dd>" + v + " <small>unverified</small></dd>";
  };
  var cell = function (label, v) { return v == null ? "<span>" + label + '<b class="q">?</b></span>' : "<span>" + label + "<b>" + esc(v) + "</b></span>"; };
  var entryHtml = function (e, i) {
    var gu = e.scores.growth == null;
    var pills = (e.is_new ? '<span class="pill new">New</span>' : "") + (gu ? '<span class="pill unk">Growth unknown</span>' : "");
    var hint = gu ? '<span class="hint">score uses market and tech only</span>' : "";
    var srcs = (e.website ? '<li><a href="' + esc(e.website) + '">Company website</a></li>' : "") + e.sources.map(function (s) {
      return '<li><a href="' + esc(s.url) + '">' + esc(s.title || s.url) + "</a>" + (s.date ? " <small>" + esc(s.date) + "</small>" : "") + "</li>"; }).join("");
    var reasons = ["market", "tech", "growth"].map(function (k) { return "<li><b>" + k.charAt(0).toUpperCase() + k.slice(1) + "</b>: " + esc(e.reasons[k] || "") + "</li>"; }).join("");
    var growthFact = factHtml(e.facts.growth_signal);
    if (e.growth_basis) growthFact = growthFact.replace(/<\/dd>$/, " <small>(" + esc(e.growth_basis) + ")</small></dd>");
    return '<li class="row" data-new="' + (e.is_new ? 1 : 0) + '" data-ind="' + esc(e.industry) + '" data-score="' + e.overall + '" data-growth="' + (e.scores.growth == null ? -1 : e.scores.growth) + '">' +
      "<details><summary>" +
      '<span class="rank">' + (i + 1) + "</span>" +
      '<div class="who"><span class="name">' + esc(e.name) + '</span><span class="meta">' + esc(e.industry) + " · " + esc(e.country) + "</span>" + pills + "</div>" +
      '<p class="idea">' + esc(e.idea) + "</p>" +
      '<div class="score"><span class="num">' + e.overall + '</span><span class="meter"><i style="width:' + Math.min(100, Math.round(e.overall * 10)) + '%"></i></span><span class="lbl">potential</span></div>' +
      '<div class="subs">' + cell("Market", e.scores.market) + cell("Tech", e.scores.tech) + cell("Growth", e.scores.growth) + hint + "</div>" +
      "</summary>" +
      '<div class="detail"><div class="col">' +
      "<div><h4>The twist</h4><p>" + esc(e.twist) + "</p></div><div><h4>Why now</h4><p>" + esc(e.why_now) + "</p></div><div><h4>Who pays</h4><p>" + esc(e.who_pays) + "</p></div>" +
      "<div><h4>The lesson</h4><p>" + esc(e.lesson) + '</p></div><div><h4>Why these scores</h4><ul class="sources">' + reasons + "</ul></div></div>" +
      '<div class="col"><dl><dt>Founded</dt>' + factHtml(e.facts.founded) + "<dt>Based in</dt>" + factHtml(e.facts.based_in) + "<dt>Team</dt>" + factHtml(e.facts.team_size) +
      "<dt>Raised</dt>" + factHtml(e.facts.raised) + "<dt>Growth</dt>" + growthFact + "</dl>" +
      '<div><h4>Where this comes from</h4><ul class="sources">' + srcs + "</ul></div></div></div></details></li>";
  };
  var byName = {}; E.forEach(function (e) { byName[e.id] = e.name; });
  var inds = {}; E.forEach(function (e) { inds[e.industry] = (inds[e.industry] || 0) + 1; });
  var chips = Object.keys(inds).sort(function (a, b) { return inds[b] - inds[a]; }).slice(0, 10).map(function (k) { return '<button type="button" data-ind="' + esc(k) + '">' + esc(k) + "</button>"; }).join("");
  var newCount = E.filter(function (e) { return e.is_new; }).length;
  var trends = (W.trends || []).map(function (t) {
    var names = (t.startup_ids || []).map(function (id) { return byName[id] ? "<span>" + esc(byName[id]) + "</span>" : ""; }).join("");
    var n = (t.startup_ids || []).length;
    return '<li class="trend"><div class="head"><strong>' + esc(t.name) + '</strong><span class="count">' + n + " startup" + (n === 1 ? "" : "s") + "</span></div><p>" + esc(t.sentence) + '</p><div class="names">' + names + "</div></li>"; }).join("");
  var labels = {shut_down: "Shut down", pivoted: "Pivoted", stalled: "Stalled"};
  var strug = (W.struggling || []).map(function (s) {
    return '<li><span class="pill unk">' + (labels[s.what] || esc(s.what)) + '</span><div><span class="name">' + esc(s.name) + '</span> <span class="meta">' + esc(s.sector || "") + " · " + esc(s.country || "") + "</span><p>" + esc(s.why || "") +
      (s.source_url ? ' <a href="' + esc(s.source_url) + '">source</a>' : "") + "</p></div></li>"; }).join("");
  var gaps = (W.gaps || []).map(function (g) { return "<li>" + esc(g) + "</li>"; }).join("");
  var unread = W.unread_sites || [];
  var unreadHtml = unread.length ? '<details class="unread"><summary>Could not read ' + unread.length + " site" + (unread.length === 1 ? "" : "s") + "</summary><ul>" +
    unread.map(function (u) { return '<li><a href="' + esc(u.url) + '">' + esc(u.url) + "</a>: " + esc(u.reason || "") + "</li>"; }).join("") + "</ul></details>" : "";
  var html =
    '<header class="top"><div class="brand"><h1>' + esc(W.title || "Hot Startups") + '</h1><span class="week">Week of ' + esc(nice(W.week_of)) + '</span></div><span class="meta">Updated ' + esc(nice(W.generated)) + "</span></header>" +
    '<section class="card" aria-label="This week in one minute"><h2>This week in one minute</h2><p class="lead">' + esc(W.brief || "First run: the list is still filling up.") + "</p>" +
    '<div class="kpis"><span><b>' + E.length + "</b>on the list</span><span><b>" + newCount + "</b>new this week</span><span><b>" + (W.trends || []).length + "</b>trends</span></div>" +
    (W.mix_text ? '<p class="mix">Where they come from: ' + esc(W.mix_text) + "</p>" : "") + "</section>" +
    '<section class="section" aria-label="Trends"><h2>Ideas that keep coming up</h2>' + (trends ? '<ul class="trend-list">' + trends + "</ul>" : '<p class="empty">No trends yet.</p>') + "</section>" +
    '<section class="section" aria-label="Ideas that are struggling"><h2>Ideas that are struggling</h2>' + (strug ? '<ul class="strug">' + strug + "</ul>" : '<p class="empty">Nothing found this week.</p>') + "</section>" +
    '<section class="gaps" aria-label="Gaps we noticed"><div class="head"><h2>Gaps we noticed</h2><span class="pill unk">Suggestions, not facts</span></div>' + (gaps ? "<ul>" + gaps + "</ul>" : '<p class="empty">Nothing yet.</p>') + "</section>" +
    '<section class="section" aria-label="The list"><h2>The list</h2><div class="toolbar">' +
    '<div class="seg" role="group" aria-label="Show"><button type="button" id="f-all" class="on" data-new="all">All ' + E.length + '</button><button type="button" id="f-new" data-new="new">New this week</button></div>' +
    '<div class="chips" role="group" aria-label="Industry"><button type="button" class="on" data-ind="all">Every industry</button>' + chips + "</div>" +
    '<label class="sort">Sort by <select id="sort"><option value="score">Potential</option><option value="growth">Growth</option><option value="new">Newest</option></select></label></div>' +
    (E.length ? "" : '<p class="empty">Nothing judged yet. The first run fills this in.</p>') +
    '<ol class="startups" id="startups">' + E.map(entryHtml).join("") + "</ol>" +
    '<p class="foot">This week’s run: ' + esc(W.run_line || "") + ". Last successful run: " + esc(nice(W.last_success)) + ". Feeds read: " + (W.feeds_read || 0) + ". Known startups in memory: " + (W.total_known || 0) + ".</p>" + unreadHtml +
    '<p class="foot">Scores run from 0 to 10 and are a rough sorting tool only. Market, tech and growth count equally. When growth is unknown, the score uses the other two and says so. A fact shown without a note was confirmed by two independent sources; otherwise it says who reported it.</p></section>';
  document.getElementById("app").innerHTML = html;

  var list = document.getElementById("startups"); var rows = Array.prototype.slice.call(list.children); var newMode = "all", ind = "all";
  function apply() {
    rows.forEach(function (r) { r.hidden = !((newMode === "all" || r.dataset.new === "1") && (ind === "all" || r.dataset.ind === ind)); });
    var n = 1; rows.forEach(function (r) { if (!r.hidden) r.querySelector(".rank").textContent = n++; });
  }
  function sortBy(key) {
    rows.sort(function (a, b) { if (key === "new") return (b.dataset.new - a.dataset.new) || (b.dataset.score - a.dataset.score); return (b.dataset[key] - a.dataset[key]) || (b.dataset.score - a.dataset.score); });
    rows.forEach(function (r) { list.appendChild(r); }); apply();
  }
  document.querySelectorAll(".seg button").forEach(function (b) { b.addEventListener("click", function () { document.querySelectorAll(".seg button").forEach(function (x) { x.classList.remove("on"); }); b.classList.add("on"); newMode = b.dataset.new; apply(); }); });
  document.querySelectorAll(".chips button[data-ind]").forEach(function (b) { b.addEventListener("click", function () { document.querySelectorAll(".chips button[data-ind]").forEach(function (x) { x.classList.remove("on"); }); b.classList.add("on"); ind = b.dataset.ind; apply(); }); });
  document.getElementById("sort").addEventListener("change", function (e) { sortBy(e.target.value); });
})();
"""


def _fact_data(fv) -> dict:
    from .util import domain_of
    if not isinstance(fv, dict) or fv.get("value") in (None, "", "null"):
        return {"value": None}
    srcs = [u for u in (fv.get("sources") or []) if isinstance(u, str) and u.startswith("http")]
    out = {"value": str(fv["value"]), "confirmed": bool(fv.get("confirmed"))}
    if not out["confirmed"] and srcs:
        out["source"] = srcs[0]
        out["source_domain"] = domain_of(srcs[0]) or "source"
    return out


def _num(v):
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def entry_data(rec: dict, is_new: bool) -> dict:
    """The part of a startup's record the page needs, as plain data for entries.js."""
    e = rec["entry"]
    sc = e.get("scores") or {}
    g = sc.get("growth") or {}
    basis = g.get("basis")
    facts = e.get("facts") or {}
    return {
        "id": rec["id"], "name": rec["name"], "industry": rec.get("industry") or e.get("industry") or "Other", "country": rec.get("country") or e.get("country") or "",
        "is_new": bool(is_new), "overall": e.get("overall") or 0, "website": rec.get("website"),
        "scores": {"market": _num((sc.get("market") or {}).get("score")), "tech": _num((sc.get("tech") or {}).get("score")), "growth": _num(g.get("score"))},
        "reasons": {k: str((sc.get(k) or {}).get("reason") or "") for k in ("market", "tech", "growth")},
        "growth_basis": str(basis) if basis and str(basis) not in ("number", "unknown") else None,
        "idea": e.get("idea") or "", "twist": e.get("twist") or "", "why_now": e.get("why_now") or "", "who_pays": e.get("who_pays") or "", "lesson": e.get("lesson") or "",
        "facts": {k: _fact_data(facts.get(k)) for k in ("founded", "based_in", "team_size", "raised", "growth_signal")},
        "sources": [{"url": s["url"], "title": s.get("title") or s["url"], "date": s.get("date")} for s in (e.get("sources") or [])[:8] if isinstance(s, dict) and s.get("url")],
    }


def page_data(store: Store, week: dict) -> dict:
    startups = store.startups()
    top = [startups[i] for i in week.get("top_ids", []) if i in startups and startups[i].get("entry")]
    new_ids = set(week.get("new_ids", []))
    mix = " · ".join(f"{REGION_NAMES.get(k, k)} {v}" for k, v in sorted((week.get("mix") or {}).items(), key=lambda kv: -kv[1]))
    w = {k: week.get(k) for k in ("run_id", "title", "week_of", "generated", "brief", "trends", "struggling", "gaps", "run_line", "last_success", "feeds_read", "total_known", "unread_sites")}
    w["title"] = w.get("title") or "Hot Startups"
    w["mix_text"] = mix
    return {"week": w, "entries": [entry_data(r, r["id"] in new_ids) for r in top]}


def render_shell(title: str) -> str:
    """The page itself: styles, an empty frame, the data file and the renderer. Small, so republishing stays cheap."""
    return f"""<title>{esc(title)}</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Sora:wght@600;700&family=IBM+Plex+Sans:wght@400;500;600&display=swap">
<style>{CSS}</style>
<div class="wrap" id="app"><p class="empty">Loading the list…</p></div>
<script src="entries.js"></script>
<script>{RENDER_JS}</script>
"""


def write_page(store: Store, week: dict) -> Path:
    """Writes out/index.html (the shell) and out/entries.js (the data). Publish both: index.html as the page,
    entries.js as a supporting file at the same path."""
    import json
    store.out.mkdir(parents=True, exist_ok=True)
    data = page_data(store, week)
    (store.out / "entries.js").write_text("window.HOT = " + json.dumps(data, ensure_ascii=False) + ";\n", encoding="utf-8")
    path = store.out / "index.html"
    path.write_text(render_shell(data["week"]["title"]), encoding="utf-8")
    return path


def render(store: Store, week: dict) -> str:
    """A single self-contained HTML (data inlined) for local viewing and tests."""
    import json
    data = page_data(store, week)
    return render_shell(data["week"]["title"]).replace('<script src="entries.js"></script>', "<script>window.HOT = " + json.dumps(data, ensure_ascii=False).replace("</", "<\\/") + ";</script>")
