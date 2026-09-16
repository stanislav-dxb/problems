"""Everything the program remembers lives in data/ as plain JSON files, one startup per file."""
from __future__ import annotations

from pathlib import Path

from .util import domain_of, normalize_name, now_iso, read_json, slugify, today, write_json


class Store:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.data = self.root / "data"
        self.startups_dir = self.data / "startups"
        self.runs_dir = self.data / "runs"
        self.weeks_dir = self.data / "weeks"
        self.work = self.root / "work"
        self.cache = self.root / "cache"
        self.out = self.root / "out"
        self._startups: dict[str, dict] | None = None

    def init(self) -> None:
        for d in (self.startups_dir, self.runs_dir, self.weeks_dir, self.work, self.cache, self.out):
            d.mkdir(parents=True, exist_ok=True)
        if not (self.data / "seen_links.json").exists():
            write_json(self.data / "seen_links.json", {})

    # ---- startups -------------------------------------------------------
    def startups(self) -> dict[str, dict]:
        if self._startups is None:
            self._startups = {}
            if self.startups_dir.exists():
                for p in sorted(self.startups_dir.glob("*.json")):
                    rec = read_json(p)
                    if rec and rec.get("id"):
                        self._startups[rec["id"]] = rec
        return self._startups

    def save_startup(self, rec: dict) -> None:
        rec["updated"] = now_iso()
        self.startups()[rec["id"]] = rec
        write_json(self.startups_dir / f"{rec['id']}.json", rec)

    def find(self, name: str | None = None, url: str | None = None) -> dict | None:
        """Match by website domain first, then by normalized name or alias."""
        dom = domain_of(url) if url else None
        key = normalize_name(name) if name else ""
        for rec in self.startups().values():
            if dom and rec.get("domain") == dom:
                return rec
        if key:
            for rec in self.startups().values():
                if rec.get("name_key") == key or key in rec.get("alias_keys", []):
                    return rec
        return None

    def new_startup(self, name: str, url: str | None = None) -> dict:
        base = slugify(domain_of(url) or name) if (url and domain_of(url)) else slugify(name)
        sid, n = base, 2
        while sid in self.startups():
            sid = f"{base}-{n}"
            n += 1
        rec = {
            "id": sid,
            "name": name.strip(),
            "name_key": normalize_name(name),
            "aliases": [],
            "alias_keys": [],
            "website": None,
            "domain": None,
            "country": None,
            "region": None,
            "industry": None,
            "status": "candidate",
            "first_seen": today(),
            "last_checked": None,
            "mentions": [],
            "research": {"searches": 0, "pages": 0, "queries_done": []},
            "entry": None,
            "score_history": [],
            "drop_reason": None,
        }
        return rec

    def set_website(self, rec: dict, url: str | None) -> None:
        from .util import looks_like_company_site
        if url and looks_like_company_site(url) and not rec.get("website"):
            rec["website"] = url
            rec["domain"] = domain_of(url)

    def add_alias(self, rec: dict, name: str) -> None:
        key = normalize_name(name)
        if key and key != rec.get("name_key") and key not in rec.get("alias_keys", []):
            rec.setdefault("aliases", []).append(name.strip())
            rec.setdefault("alias_keys", []).append(key)

    # ---- runs -----------------------------------------------------------
    def runs(self) -> list[dict]:
        out = []
        if self.runs_dir.exists():
            for p in sorted(self.runs_dir.glob("*.json")):
                r = read_json(p)
                if r and r.get("id"):
                    out.append(r)
        out.sort(key=lambda r: r.get("number", 0))
        return out

    def save_run(self, run: dict) -> None:
        run["updated"] = now_iso()
        write_json(self.runs_dir / f"{run['id']}.json", run)

    def latest_run(self) -> dict | None:
        runs = self.runs()
        return runs[-1] if runs else None

    # ---- weeks (what the page shows) -----------------------------------
    def save_week(self, week: dict) -> None:
        write_json(self.weeks_dir / f"{week['run_id']}.json", week)
        write_json(self.weeks_dir / "latest.json", week)

    def latest_week(self) -> dict | None:
        return read_json(self.weeks_dir / "latest.json")

    # ---- seen feed links ------------------------------------------------
    def seen_links(self) -> dict:
        return read_json(self.data / "seen_links.json", {}) or {}

    def save_seen_links(self, seen: dict) -> None:
        write_json(self.data / "seen_links.json", seen)
