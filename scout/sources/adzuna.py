"""Adzuna job search API (free tier). OFF by default.

Job titles and descriptions are a signal for what companies still pay people to do manually.
Requires ADZUNA_APP_ID / ADZUNA_APP_KEY. Indeed and LinkedIn have no open API and are not used.
"""
from __future__ import annotations

import logging

import httpx

from ..config import env
from .base import SourceUnavailable, http_client, long_enough, make_item, request_json

log = logging.getLogger("scout.sources.adzuna")
NAME = "adzuna"
API = "https://api.adzuna.com/v1/api/jobs"


def available(cfg: dict) -> tuple[bool, str]:
    if not cfg["sources"]["adzuna"].get("enabled"):
        return False, "disabled in config"
    if not (env("ADZUNA_APP_ID") and env("ADZUNA_APP_KEY")):
        return False, "ADZUNA_APP_ID/KEY not set"
    return True, "Adzuna API"


def describe(since_days: int, cfg: dict) -> list[str]:
    scfg = cfg["sources"]["adzuna"]
    return [f"adzuna: {len(scfg.get('queries') or [])} queries x {len(scfg.get('countries') or [])} "
            f"countries, {scfg.get('results_per_query', 50)} results each, max_days_old={since_days}"]


def fetch(since_days: int, cfg: dict) -> list[dict]:
    ok, why = available(cfg)
    if not ok:
        raise SourceUnavailable(f"adzuna: {why}")
    scfg = cfg["sources"]["adzuna"]
    app_id, app_key = env("ADZUNA_APP_ID"), env("ADZUNA_APP_KEY")
    per = int(scfg.get("results_per_query", 50))
    min_chars = int(scfg.get("min_body_chars", 80))
    out: list[dict] = []
    seen: set[str] = set()
    with http_client() as client:
        for country in scfg.get("countries") or ["gb"]:
            for q in scfg.get("queries") or []:
                try:
                    data = request_json(client, "GET", f"{API}/{country}/search/1",
                                        params={"app_id": app_id, "app_key": app_key, "what": q,
                                                "results_per_page": min(per, 50), "max_days_old": since_days,
                                                "content-type": "application/json"}, sleep=0.5)
                except httpx.HTTPStatusError as e:
                    if e.response.status_code in (401, 403):
                        raise SourceUnavailable(f"adzuna: HTTP {e.response.status_code} (bad keys?)") from e
                    log.warning("adzuna: %s/%r -> HTTP %d", country, q, e.response.status_code)
                    continue
                for r in data.get("results", []):
                    rid = str(r.get("id"))
                    if rid in seen or not long_enough(r.get("description"), min_chars):
                        continue
                    seen.add(rid)
                    raw = {"id": rid, "country": country, "query": q, "category": (r.get("category") or {}).get("label"),
                           "location": (r.get("location") or {}).get("display_name"),
                           "salary_min": r.get("salary_min"), "salary_max": r.get("salary_max"),
                           "contract_time": r.get("contract_time")}
                    out.append(make_item(NAME, rid, r.get("redirect_url"), r.get("title"), r.get("description"),
                                         None, r.get("created"), raw))
    log.info("adzuna: %d items", len(out))
    return out
