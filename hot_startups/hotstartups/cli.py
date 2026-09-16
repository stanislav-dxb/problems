"""Command line: python -m hotstartups <command>. Run `python -m hotstartups --help`."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import tasks
from .config import home, load_config, load_list_sites, load_sources
from .fetch import get, read_feed
from .page import write_page
from .store import Store
from .util import read_json


def _ctx():
    root = home()
    cfg = load_config(root)
    return cfg, load_sources(root), Store(root)


def _out(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def cmd_init(a):
    cfg, src, store = _ctx()
    store.init()
    print(f"ready: {store.root}")


def cmd_run_start(a):
    cfg, src, store = _ctx()
    try:
        run = tasks.start_run(cfg, store, force_first=a.first)
    except tasks.RunError as e:
        print(str(e))
        sys.exit(3)
    _out({"run": run["id"], "number": run["number"], "week_of": run["week_of"], "limits": run["limits"], "first_run": run["first_run"],
          "resumed": run.get("resumed", 0), "phase": run["phase"], "counts": run["counts"]})


def cmd_next(a):
    cfg, src, store = _ctx()
    run = store.latest_run()
    if not run:
        print("no run: use `run start` first")
        sys.exit(3)
    try:
        res = tasks.next_tasks(cfg, src, store, run, batch=a.batch)
    except tasks.RunError as e:
        print(str(e))
        sys.exit(3)
    if a.out:
        Path(a.out).write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
        brief = dict(res)
        if "tasks" in brief:
            brief["tasks"] = [{"ticket": t["ticket"], "type": t["type"], "key": t.get("key"), "query": t.get("query"), "url": t.get("url")} for t in res["tasks"]]
        brief["written_to"] = a.out
        _out(brief)
    else:
        _out(res)


def cmd_submit(a):
    cfg, src, store = _ctx()
    run = store.latest_run()
    if not run:
        print("no run")
        sys.exit(3)
    try:
        result = json.loads(Path(a.file).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"cannot read result file: {e}")
        sys.exit(3)
    try:
        _out(tasks.submit(cfg, src, store, run, a.ticket, result))
    except tasks.RunError as e:
        print(f"rejected: {e}")
        sys.exit(3)


def cmd_status(a):
    cfg, src, store = _ctx()
    run = store.latest_run()
    if not run:
        print("no run yet")
        return
    pending = [t for t, m in run["tickets"].items() if m["status"] == "issued"]
    _out({"run": run["id"], "phase": run["phase"], "finished": run.get("finished"), "counts": run["counts"], "limits": run["limits"],
          "line": tasks.counts_line(run), "pending_tickets": pending, "failures": len(run["failures"]), "new": len(run["new_ids"]), "judged": len(run["judged_ids"]),
          "known_startups": len(store.startups())})


def cmd_finish(a):
    cfg, src, store = _ctx()
    run = store.latest_run()
    if not run:
        print("no run")
        sys.exit(3)
    pending = [t for t, m in run["tickets"].items() if m["status"] == "issued"]
    if pending and not a.force:
        print(f"still pending: {pending}. Submit them or use --force.")
        sys.exit(3)
    week = tasks.finish_run(cfg, store, run)
    path = write_page(store, week)
    out = {"finished": run["finished"], "line": week["run_line"], "on_the_list": len(week["top_ids"]), "new": len(week["new_ids"]), "page": str(path)}
    branch = (cfg.get("git") or {}).get("branch")
    if branch and not a.no_push:
        out["git"] = _save_memory(store, branch, f"Hot Startups run {run['id']}: {week['run_line']}")
    _out(out)
    if out.get("git", {}).get("pushed") is False:
        sys.exit(4)


def _save_memory(store: Store, branch: str, message: str) -> dict:
    """Commit data/, out/ and config.yaml and push them to the branch. The data folder is the program's memory;
    without this push the next run forgets everything this run learned."""
    import subprocess
    repo = store.root.parent
    rel = store.root.name
    def git(*args):
        return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True)
    git("add", f"{rel}/data", f"{rel}/out", f"{rel}/config.yaml")
    if not git("diff", "--cached", "--quiet").returncode == 0:
        c = git("commit", "-q", "-m", message)
        if c.returncode != 0:
            return {"committed": False, "pushed": False, "error": (c.stderr or c.stdout).strip()[:300]}
    else:
        return {"committed": False, "pushed": True, "note": "nothing new to save"}
    for attempt in range(4):
        r = git("push", "-u", "origin", branch)
        if r.returncode == 0:
            return {"committed": True, "pushed": True, "branch": branch}
        if "rejected" in (r.stderr or "") or "fetch first" in (r.stderr or ""):
            git("pull", "--rebase", "origin", branch)
        else:
            import time
            time.sleep(2 ** attempt)
    return {"committed": True, "pushed": False, "error": (r.stderr or r.stdout).strip()[:300]}


def cmd_page(a):
    cfg, src, store = _ctx()
    week = store.latest_week()
    if not week:
        week = {"run_id": "none", "week_of": "", "generated": "", "title": cfg["page"]["title"], "top_ids": [], "new_ids": [], "trends": [], "gaps": [], "struggling": [], "mix": {}}
    print(write_page(store, week))


def cmd_check_sources(a):
    cfg, src, store = _ctx()
    print("list sites:")
    for s in load_list_sites(store.root):
        ok, text, title = get(s["url"])
        print(f"  {'ok  ' if ok and len(text) > 200 else 'FAIL'} {s['url']}  {title[:60] if ok else text[:60]}")
    print("feeds:")
    for region, rdef in src.get("regions", {}).items():
        for f in rdef.get("feeds", []):
            ok, items, err = read_feed(f["url"])
            print(f"  {'ok  ' if ok else 'FAIL'} {region:14} {f['name']:16} {len(items):3} entries  {err}")


def cmd_show(a):
    cfg, src, store = _ctx()
    rec = store.startups().get(a.id) or store.find(name=a.id)
    _out(rec or {"error": "not found"})


def main(argv=None):
    p = argparse.ArgumentParser(prog="hotstartups", description="Hot Startups weekly run")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init", help="create the data folders").set_defaults(fn=cmd_init)
    r = sub.add_parser("run", help="run start | run finish")
    rs = r.add_subparsers(dest="what", required=True)
    s = rs.add_parser("start", help="start (or resume) this week's run")
    s.add_argument("--first", action="store_true", help="use the small first-run limits")
    s.set_defaults(fn=cmd_run_start)
    f = rs.add_parser("finish", help="close the run, write the week file and the page, commit and push the memory")
    f.add_argument("--force", action="store_true")
    f.add_argument("--no-push", action="store_true", help="do not commit and push data/ afterwards")
    f.set_defaults(fn=cmd_finish)
    n = sub.add_parser("next", help="hand out the next tickets")
    n.add_argument("--batch", type=int, default=5)
    n.add_argument("--out", help="write the full tasks JSON here and print only a short summary")
    n.set_defaults(fn=cmd_next)
    sb = sub.add_parser("submit", help="submit a ticket's result file")
    sb.add_argument("ticket")
    sb.add_argument("file")
    sb.set_defaults(fn=cmd_submit)
    sub.add_parser("status", help="counts against limits").set_defaults(fn=cmd_status)
    sub.add_parser("page", help="re-render out/index.html from the latest week").set_defaults(fn=cmd_page)
    sub.add_parser("check-sources", help="test that feeds and list sites can be read").set_defaults(fn=cmd_check_sources)
    sh = sub.add_parser("show", help="print one startup's record")
    sh.add_argument("id")
    sh.set_defaults(fn=cmd_show)
    a = p.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
