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

JS = """
  (function () {
    var list = document.getElementById('startups'); if (!list) return;
    var rows = Array.prototype.slice.call(list.children); var newMode = 'all', ind = 'all';
    function apply() {
      rows.forEach(function (r) { r.hidden = !((newMode === 'all' || r.dataset.new === '1') && (ind === 'all' || r.dataset.ind === ind)); });
      var n = 1; rows.forEach(function (r) { if (!r.hidden) r.querySelector('.rank').textContent = n++; });
    }
    function sortBy(key) {
      rows.sort(function (a, b) {
        if (key === 'new') return (b.dataset.new - a.dataset.new) || (b.dataset.score - a.dataset.score);
        return (b.dataset[key] - a.dataset[key]) || (b.dataset.score - a.dataset.score);
      });
      rows.forEach(function (r) { list.appendChild(r); }); apply();
    }
    document.querySelectorAll('.seg button').forEach(function (b) { b.addEventListener('click', function () {
      document.querySelectorAll('.seg button').forEach(function (x) { x.classList.remove('on'); }); b.classList.add('on'); newMode = b.dataset.new; apply(); }); });
    document.querySelectorAll('.chips button[data-ind]').forEach(function (b) { b.addEventListener('click', function () {
      document.querySelectorAll('.chips button[data-ind]').forEach(function (x) { x.classList.remove('on'); }); b.classList.add('on'); ind = b.dataset.ind; apply(); }); });
    var sel = document.getElementById('sort'); if (sel) sel.addEventListener('change', function (e) { sortBy(e.target.value); });
  })();
"""


def _nice_date(iso: str) -> str:
    try:
        from datetime import date
        d = date.fromisoformat(iso[:10])
        return d.strftime("%-d %B %Y")
    except Exception:
        return iso


def _fact(fv) -> str:
    if not isinstance(fv, dict) or fv.get("value") in (None, "", "null"):
        return "<dd>unknown</dd>"
    v = esc(str(fv["value"]))
    srcs = [u for u in (fv.get("sources") or []) if isinstance(u, str) and u.startswith("http")]
    if fv.get("confirmed"):
        return f"<dd>{v}</dd>"
    if srcs:
        from .util import domain_of
        return f'<dd>{v} <small>reported by <a href="{esc(srcs[0])}">{esc(domain_of(srcs[0]) or "source")}</a></small></dd>'
    return f"<dd>{v} <small>unverified</small></dd>"


def _score_cell(label: str, sc: dict | None) -> str:
    sc = sc or {}
    v = sc.get("score")
    if v is None:
        return f"<span>{label}<b class=\"q\">?</b></span>"
    try:
        return f"<span>{label}<b>{float(v):g}</b></span>"
    except (TypeError, ValueError):
        return f"<span>{label}<b class=\"q\">?</b></span>"


def _entry_html(rank: int, rec: dict, is_new: bool) -> str:
    e = rec["entry"]
    sc = e.get("scores") or {}
    overall = e.get("overall") or 0
    g = (sc.get("growth") or {})
    growth_unknown = g.get("score") is None
    ind = esc(rec.get("industry") or e.get("industry") or "Other")
    country = esc(rec.get("country") or e.get("country") or "")
    pills = ""
    if is_new:
        pills += '<span class="pill new">New</span>'
    if growth_unknown:
        pills += '<span class="pill unk">Growth unknown</span>'
    hint = '<span class="hint">score uses market and tech only</span>' if growth_unknown else ""
    basis = g.get("basis")
    basis_note = f' <small>({esc(str(basis))})</small>' if basis and str(basis) not in ("number", "unknown") else ""
    facts = e.get("facts") or {}
    growth_fact = facts.get("growth_signal")
    sources = ""
    for s in (e.get("sources") or [])[:8]:
        if isinstance(s, dict) and s.get("url"):
            t = esc(s.get("title") or s["url"])
            d = f" <small>{esc(str(s.get('date') or ''))}</small>" if s.get("date") else ""
            sources += f'<li><a href="{esc(s["url"])}">{t}</a>{d}</li>'
    website = f'<li><a href="{esc(rec["website"])}">Company website</a></li>' if rec.get("website") else ""
    reasons = "".join(f"<li><b>{k.title()}</b>: {esc(str((sc.get(k) or {}).get('reason') or ''))}</li>" for k in ("market", "tech", "growth"))
    try:
        growth_num = float(g.get("score")) if g.get("score") is not None else -1
    except (TypeError, ValueError):
        growth_num = -1
    return f"""
      <li class="row" data-new="{1 if is_new else 0}" data-ind="{ind}" data-score="{overall}" data-growth="{growth_num}">
        <details>
          <summary>
            <span class="rank">{rank}</span>
            <div class="who"><span class="name">{esc(rec["name"])}</span><span class="meta">{ind} · {country}</span>{pills}</div>
            <p class="idea">{esc(e.get("idea") or "")}</p>
            <div class="score"><span class="num">{overall:g}</span><span class="meter"><i style="width:{min(100, int(round(overall * 10)))}%"></i></span><span class="lbl">potential</span></div>
            <div class="subs">{_score_cell("Market", sc.get("market"))}{_score_cell("Tech", sc.get("tech"))}{_score_cell("Growth", sc.get("growth"))}{hint}</div>
          </summary>
          <div class="detail">
            <div class="col">
              <div><h4>The twist</h4><p>{esc(e.get("twist") or "")}</p></div>
              <div><h4>Why now</h4><p>{esc(e.get("why_now") or "")}</p></div>
              <div><h4>Who pays</h4><p>{esc(e.get("who_pays") or "")}</p></div>
              <div><h4>The lesson</h4><p>{esc(e.get("lesson") or "")}</p></div>
              <div><h4>Why these scores</h4><ul class="sources">{reasons}</ul></div>
            </div>
            <div class="col">
              <dl>
                <dt>Founded</dt>{_fact(facts.get("founded"))}
                <dt>Based in</dt>{_fact(facts.get("based_in"))}
                <dt>Team</dt>{_fact(facts.get("team_size"))}
                <dt>Raised</dt>{_fact(facts.get("raised"))}
                <dt>Growth</dt>{_fact(growth_fact)[:-5] + basis_note + "</dd>"}
              </dl>
              <div><h4>Where this comes from</h4><ul class="sources">{website}{sources}</ul></div>
            </div>
          </div>
        </details>
      </li>"""


def render(store: Store, week: dict) -> str:
    startups = store.startups()
    top = [startups[i] for i in week.get("top_ids", []) if i in startups and startups[i].get("entry")]
    new_ids = set(week.get("new_ids", []))
    title = esc(week.get("title") or "Hot Startups")
    inds: dict[str, int] = {}
    for r in top:
        k = r.get("industry") or (r.get("entry") or {}).get("industry") or "Other"
        inds[k] = inds.get(k, 0) + 1
    chips = "".join(f'<button type="button" data-ind="{esc(k)}">{esc(k)}</button>' for k, _ in sorted(inds.items(), key=lambda kv: -kv[1])[:10])
    mix = " · ".join(f"{REGION_NAMES.get(k, k)} {v}" for k, v in sorted(week.get("mix", {}).items(), key=lambda kv: -kv[1]))
    trends_html = ""
    for tr in week.get("trends", []):
        names = "".join(f"<span>{esc(startups[i]['name'])}</span>" for i in tr.get("startup_ids", []) if i in startups)
        n = len(tr.get("startup_ids", []))
        trends_html += f'<li class="trend"><div class="head"><strong>{esc(tr["name"])}</strong><span class="count">{n} startup{"s" if n != 1 else ""}</span></div><p>{esc(tr.get("sentence") or "")}</p><div class="names">{names}</div></li>'
    strug_html = ""
    labels = {"shut_down": "Shut down", "pivoted": "Pivoted", "stalled": "Stalled"}
    for it in week.get("struggling", []):
        src = f' <a href="{esc(it["source_url"])}">source</a>' if it.get("source_url") else ""
        strug_html += f'<li><span class="pill unk">{labels.get(it["what"], it["what"])}</span><div><span class="name">{esc(it["name"])}</span> <span class="meta">{esc(it.get("sector") or "")} · {esc(it.get("country") or "")}</span><p>{esc(it.get("why") or "")}{src}</p></div></li>'
    gaps_html = "".join(f"<li>{esc(g)}</li>" for g in week.get("gaps", []))
    entries_html = "".join(_entry_html(i + 1, r, r["id"] in new_ids) for i, r in enumerate(top))
    unread = week.get("unread_sites") or []
    unread_html = ""
    if unread:
        items = "".join(f'<li><a href="{esc(u["url"])}">{esc(u["url"])}</a>: {esc(u.get("reason") or "")}</li>' for u in unread)
        unread_html = f'<details class="unread"><summary>Could not read {len(unread)} site{"s" if len(unread) != 1 else ""}</summary><ul>{items}</ul></details>'
    empty = '<p class="empty">Nothing judged yet. The first run fills this in.</p>' if not top else ""
    brief = esc(week.get("brief") or "First run: the list is still filling up.")
    return f"""<title>{title}</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Sora:wght@600;700&family=IBM+Plex+Sans:wght@400;500;600&display=swap">
<style>{CSS}</style>
<div class="wrap">
  <header class="top">
    <div class="brand"><h1>{title}</h1><span class="week">Week of {_nice_date(week.get("week_of", ""))}</span></div>
    <span class="meta">Updated {_nice_date(week.get("generated", ""))}</span>
  </header>

  <section class="card" aria-label="This week in one minute">
    <h2>This week in one minute</h2>
    <p class="lead">{brief}</p>
    <div class="kpis"><span><b>{len(top)}</b>on the list</span><span><b>{len(new_ids)}</b>new this week</span><span><b>{len(week.get("trends", []))}</b>trends</span></div>
    {f'<p class="mix">Where they come from: {esc(mix)}</p>' if mix else ''}
  </section>

  <section class="section" aria-label="Trends">
    <h2>Ideas that keep coming up</h2>
    {f'<ul class="trend-list">{trends_html}</ul>' if trends_html else '<p class="empty">No trends yet.</p>'}
  </section>

  <section class="section" aria-label="Ideas that are struggling">
    <h2>Ideas that are struggling</h2>
    {f'<ul class="strug">{strug_html}</ul>' if strug_html else '<p class="empty">Nothing found this week.</p>'}
  </section>

  <section class="gaps" aria-label="Gaps we noticed">
    <div class="head"><h2>Gaps we noticed</h2><span class="pill unk">Suggestions, not facts</span></div>
    {f'<ul>{gaps_html}</ul>' if gaps_html else '<p class="empty">Nothing yet.</p>'}
  </section>

  <section class="section" aria-label="The list">
    <h2>The list</h2>
    <div class="toolbar">
      <div class="seg" role="group" aria-label="Show"><button type="button" id="f-all" class="on" data-new="all">All {len(top)}</button><button type="button" id="f-new" data-new="new">New this week</button></div>
      <div class="chips" role="group" aria-label="Industry"><button type="button" class="on" data-ind="all">Every industry</button>{chips}</div>
      <label class="sort">Sort by <select id="sort"><option value="score">Potential</option><option value="growth">Growth</option><option value="new">Newest</option></select></label>
    </div>
    {empty}
    <ol class="startups" id="startups">{entries_html}
    </ol>
    <p class="foot">This week's run: {esc(week.get("run_line") or "")}. Last successful run: {_nice_date(week.get("last_success", ""))}. Feeds read: {week.get("feeds_read", 0)}. Known startups in memory: {week.get("total_known", 0)}.</p>
    {unread_html}
    <p class="foot">Scores run from 0 to 10 and are a rough sorting tool only. Market, tech and growth count equally. When growth is unknown, the score uses the other two and says so. A fact shown without a note was confirmed by two independent sources; otherwise it says who reported it.</p>
  </section>
</div>
<script>{JS}</script>
"""


def write_page(store: Store, week: dict) -> Path:
    store.out.mkdir(parents=True, exist_ok=True)
    path = store.out / "index.html"
    path.write_text(render(store, week), encoding="utf-8")
    return path
