"""World Bank Documents & Reports API (public, no key): search.worldbank.org/api/v2/wds"""
from __future__ import annotations

import logging

from ...util import clean_text, parse_dt, since_dt
from ..base import http_client, request_json

log = logging.getLogger("scout.reports.worldbank")
NAME = "worldbank"
API = "https://search.worldbank.org/api/v2/wds"
EXCLUDE_DOCTY = ("procurement", "appraisal", "implementation", "agreement", "disbursement", "audit",
                 "environmental", "resettlement", "loan", "grant", "financial statement", "board report",
                 "monitoring", "supervision", "status", "letter", "memorandum of", "plan")


def describe(since_days: int, cfg: dict) -> list[str]:
    w = cfg.get("reports", {}).get("worldbank", {})
    return [f"worldbank: {len(w.get('queries', []))} queries x {w.get('rows', 20)} newest documents, since {since_days}d"]


def fetch(since_days: int, cfg: dict) -> list[dict]:
    w = cfg.get("reports", {}).get("worldbank", {})
    since = since_dt(since_days)
    out: list[dict] = []
    seen: set[str] = set()
    with http_client() as client:
        for q in w.get("queries", []) or []:
            params = {"format": "json", "qterm": q, "rows": int(w.get("rows", 20)), "srt": "docdt", "order": "desc",
                      "fl": "display_title,pdfurl,url,docdt,abstracts,docty,count,lang"}
            try:
                data = request_json(client, "GET", API, params=params, sleep=0.5)
            except Exception as e:  # noqa: BLE001
                log.warning("worldbank %r: %s", q, e)
                continue
            for key, d in (data.get("documents") or {}).items():
                if key == "facets" or not isinstance(d, dict):
                    continue
                url = d.get("url") or d.get("url_friendly_title")
                if not url or url in seen:
                    continue
                docty = (d.get("docty") or "").lower()
                if any(x in docty for x in EXCLUDE_DOCTY):
                    continue
                dt = parse_dt(d.get("docdt"))
                if dt and dt < since:
                    continue
                seen.add(url)
                abstract = d.get("abstracts") or {}
                abstract = abstract.get("cdata!") if isinstance(abstract, dict) else abstract
                out.append({"source": NAME, "publisher": "World Bank", "title": clean_text(d.get("display_title")),
                            "url": url, "pdf_url": d.get("pdfurl"), "published_at": dt.isoformat() if dt else None,
                            "summary_text": clean_text(abstract)[:3000], "language": (d.get("lang") or "")[:2].lower() or None,
                            "confidence": int(w.get("confidence", 4)), "meta": {"docty": d.get("docty"),
                                                                                  "country": d.get("count"), "query": q}})
    log.info("worldbank: %d reports", len(out))
    return out
