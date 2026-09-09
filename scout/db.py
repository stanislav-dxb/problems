"""SQLite storage for Problem Scout."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator

from .config import db_path
from .util import iso, now_utc

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    source        TEXT NOT NULL,
    source_id     TEXT NOT NULL,
    url           TEXT,
    title         TEXT,
    body          TEXT,
    author_handle TEXT,               -- salted hash, never the raw handle
    language      TEXT,
    created_at    TEXT,               -- ISO-8601 UTC, when the post was written
    collected_at  TEXT NOT NULL,      -- ISO-8601 UTC
    raw_json      TEXT,
    UNIQUE (source, source_id)
);
CREATE INDEX IF NOT EXISTS idx_items_created ON items (created_at);
CREATE INDEX IF NOT EXISTS idx_items_source ON items (source);

CREATE TABLE IF NOT EXISTS problems (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id                  INTEGER NOT NULL UNIQUE REFERENCES items(id) ON DELETE CASCADE,
    is_problem               INTEGER NOT NULL DEFAULT 0,
    domain                   TEXT,
    who_has_it               TEXT,
    problem_summary          TEXT,
    pain_score               INTEGER,
    money_mentioned          INTEGER,
    workaround_described     INTEGER,
    solution_requested       INTEGER,
    existing_solutions_named TEXT,    -- JSON list
    language                 TEXT,
    classification_failed    INTEGER NOT NULL DEFAULT 0,
    signal_score             INTEGER,
    classified_by            TEXT,
    cluster_id               INTEGER,
    classified_at            TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_problems_cluster ON problems (cluster_id);
CREATE INDEX IF NOT EXISTS idx_problems_is_problem ON problems (is_problem);

CREATE TABLE IF NOT EXISTS clusters (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    label             TEXT,
    canonical_summary TEXT,
    domain            TEXT,
    item_count        INTEGER NOT NULL DEFAULT 0,
    first_seen        TEXT,
    last_seen         TEXT,
    sources           TEXT,           -- JSON {source: count}
    growth_30d        REAL,
    member_hash       TEXT,           -- sha1 of sorted member item ids
    created_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS scores (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    cluster_id         INTEGER NOT NULL REFERENCES clusters(id) ON DELETE CASCADE,
    volume             REAL, growth REAL, sources REAL, languages REAL,
    pain               REAL, money REAL, demand REAL, workaround REAL,
    triangulation      REAL,
    competition        INTEGER,        -- distinct existing solutions named by members
    existing_solutions TEXT,           -- JSON [[name, count], ...]
    overall_score      REAL,
    scored_at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_scores_cluster ON scores (cluster_id);

CREATE TABLE IF NOT EXISTS reports (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    source        TEXT NOT NULL,        -- worldbank | publisher_rss | arxiv
    publisher     TEXT NOT NULL,
    title         TEXT,
    url           TEXT NOT NULL UNIQUE,
    pdf_url       TEXT,
    published_at  TEXT,
    summary_text  TEXT,
    local_path    TEXT,
    language      TEXT,
    confidence    INTEGER,              -- 1-5 trust in the publisher (config)
    chunks_total  INTEGER DEFAULT 0,
    chunks_kept   INTEGER DEFAULT 0,    -- chunks that passed the keyword prefilter
    fetched_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS report_claims (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id            INTEGER NOT NULL REFERENCES reports(id) ON DELETE CASCADE,
    source_chunk_id      TEXT NOT NULL,
    heading              TEXT,
    is_relevant          INTEGER NOT NULL DEFAULT 1,
    domain               TEXT,
    claim_type           TEXT,
    claim_summary        TEXT,
    numbers              TEXT,          -- JSON list
    geography            TEXT,          -- JSON list
    confidence_in_source INTEGER,
    chunk_excerpt        TEXT,
    created_at           TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_claims_report ON report_claims (report_id);

CREATE TABLE IF NOT EXISTS news_articles (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    feed          TEXT NOT NULL,
    kind          TEXT NOT NULL,        -- press | regulatory | funding | google_news | gdelt
    title         TEXT,
    url           TEXT NOT NULL UNIQUE,
    published_at  TEXT,
    summary_text  TEXT,
    language      TEXT,
    domain_hint   TEXT,
    fetched_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_news_published ON news_articles (published_at);

CREATE TABLE IF NOT EXISTS news_catalysts (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    article_id        INTEGER NOT NULL UNIQUE REFERENCES news_articles(id) ON DELETE CASCADE,
    is_catalyst       INTEGER NOT NULL DEFAULT 0,
    catalyst_type     TEXT,
    domain            TEXT,
    geography         TEXT,             -- JSON list
    summary           TEXT,
    affected_parties  TEXT,
    time_horizon      TEXT,
    catalyst_strength INTEGER,
    numbers           TEXT,             -- JSON list
    evidence_terms    TEXT,             -- JSON list of matched phrases
    created_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS triangulations (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    cluster_id          INTEGER NOT NULL UNIQUE REFERENCES clusters(id) ON DELETE CASCADE,
    pain_evidence       REAL, market_evidence REAL, timing_evidence REAL,
    triangulation_score REAL,
    best_claim_id       INTEGER,
    claim_ids           TEXT,           -- JSON list
    catalyst_ids        TEXT,           -- JSON list
    market_size_source  TEXT,
    why_now             TEXT,
    evidence_gaps       TEXT,
    geography           TEXT,           -- JSON list (union of matched claims/catalysts)
    created_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS hypotheses (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    kind        TEXT NOT NULL,          -- report | news
    ref_id      INTEGER NOT NULL,       -- report_claims.id or news_catalysts.id
    summary     TEXT,
    domain      TEXT,
    geography   TEXT,                   -- JSON list
    strength    INTEGER,
    ask_whom    TEXT,
    source_url  TEXT,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS evaluations (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    cluster_id             INTEGER NOT NULL,       -- not a FK: history survives re-clustering
    member_hash            TEXT,
    member_item_ids        TEXT,                   -- JSON list, for overlap-based reuse
    item_count             INTEGER,
    market_size_estimate   TEXT,
    market_size_reasoning  TEXT,
    market_size_source     TEXT,
    monopoly_potential     INTEGER,
    location_independent   INTEGER,
    runs_without_founder   INTEGER,
    capital_light          INTEGER,
    measurable_90d         INTEGER,
    path_to_1b             TEXT,
    path_to_1b_score       INTEGER,
    what_would_kill_it     TEXT,
    quickest_test          TEXT,
    why_now                TEXT,
    evidence_gaps          TEXT,
    corridor_advantage     TEXT,
    overall_score          REAL,
    model                  TEXT,
    reused_from            INTEGER,                -- evaluations.id this row was copied from
    raw_json               TEXT,
    evaluated_at           TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_eval_cluster ON evaluations (cluster_id);

CREATE TABLE IF NOT EXISTS llm_calls (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    called_at    TEXT NOT NULL,
    stage        TEXT NOT NULL,
    backend      TEXT NOT NULL,
    model        TEXT,
    items        INTEGER,
    prompt_chars INTEGER,
    ok           INTEGER NOT NULL DEFAULT 1,
    duration_ms  INTEGER,
    error        TEXT
);

CREATE TABLE IF NOT EXISTS runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    stage       TEXT NOT NULL,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    notes       TEXT
);
"""


def connect(path: str | None = None) -> sqlite3.Connection:
    p = path or db_path()
    if p != ":memory:":
        Path(p).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(p, check_same_thread=False, timeout=30)  # worker threads only log through a lock
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    """Bring databases created by earlier versions up to the current schema."""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(problems)")}
    if "signal_score" not in cols:
        conn.execute("ALTER TABLE problems ADD COLUMN signal_score INTEGER")
    if "classified_by" not in cols:
        conn.execute("ALTER TABLE problems ADD COLUMN classified_by TEXT")
    scols = {r["name"] for r in conn.execute("PRAGMA table_info(scores)")}
    if "triangulation" not in scols:
        conn.execute("ALTER TABLE scores ADD COLUMN triangulation REAL")
    ecols = {r["name"] for r in conn.execute("PRAGMA table_info(evaluations)")}
    if ecols and "corridor_advantage" not in ecols:  # pre-addendum evaluations table: recreate
        conn.execute("DROP TABLE evaluations")
        conn.executescript(SCHEMA)
    conn.execute("DROP TABLE IF EXISTS api_calls")
    conn.commit()


@contextmanager
def session(path: str | None = None) -> Iterator[sqlite3.Connection]:
    conn = connect(path)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------- items

ITEM_FIELDS = ("source", "source_id", "url", "title", "body", "author_handle", "language",
               "created_at", "collected_at", "raw_json")


def insert_items(conn: sqlite3.Connection, items: Iterable[dict[str, Any]]) -> tuple[int, int]:
    """Insert items, ignoring duplicates on (source, source_id). Returns (inserted, skipped)."""
    inserted = skipped = 0
    now = iso(now_utc())
    for it in items:
        row = {k: it.get(k) for k in ITEM_FIELDS}
        row["collected_at"] = row.get("collected_at") or now
        if isinstance(row.get("raw_json"), (dict, list)):
            row["raw_json"] = json.dumps(row["raw_json"], ensure_ascii=False, default=str)
        if not row["source"] or row["source_id"] in (None, ""):
            skipped += 1
            continue
        row["source_id"] = str(row["source_id"])
        cur = conn.execute(
            "INSERT OR IGNORE INTO items (source, source_id, url, title, body, author_handle, "
            "language, created_at, collected_at, raw_json) VALUES "
            "(:source, :source_id, :url, :title, :body, :author_handle, :language, :created_at, "
            ":collected_at, :raw_json)",
            row,
        )
        if cur.rowcount == 1:
            inserted += 1
        else:
            skipped += 1
    return inserted, skipped


def unclassified_items(conn: sqlite3.Connection, limit: int | None = None,
                       retry_failed: bool = False) -> list[sqlite3.Row]:
    sql = (
        "SELECT i.* FROM items i LEFT JOIN problems p ON p.item_id = i.id "
        "WHERE p.id IS NULL" + (" OR p.classification_failed = 1" if retry_failed else "") +
        " ORDER BY i.created_at DESC"
    )
    if limit:
        sql += f" LIMIT {int(limit)}"
    return list(conn.execute(sql))


def upsert_problem(conn: sqlite3.Connection, row: dict[str, Any]) -> None:
    row = dict(row)
    if isinstance(row.get("existing_solutions_named"), (list, dict)):
        row["existing_solutions_named"] = json.dumps(row["existing_solutions_named"], ensure_ascii=False)
    row.setdefault("classified_at", iso(now_utc()))
    row.setdefault("classification_failed", 0)
    for k in ("domain", "who_has_it", "problem_summary", "pain_score", "money_mentioned",
              "workaround_described", "solution_requested", "existing_solutions_named", "language",
              "signal_score", "classified_by"):
        row.setdefault(k, None)
    conn.execute(
        "INSERT INTO problems (item_id, is_problem, domain, who_has_it, problem_summary, pain_score, "
        "money_mentioned, workaround_described, solution_requested, existing_solutions_named, "
        "language, classification_failed, signal_score, classified_by, classified_at) VALUES (:item_id, :is_problem, "
        ":domain, :who_has_it, :problem_summary, :pain_score, :money_mentioned, :workaround_described, "
        ":solution_requested, :existing_solutions_named, :language, :classification_failed, :signal_score, "
        ":classified_by, :classified_at) ON CONFLICT(item_id) DO UPDATE SET is_problem=excluded.is_problem, "
        "domain=excluded.domain, who_has_it=excluded.who_has_it, problem_summary=excluded.problem_summary, "
        "pain_score=excluded.pain_score, money_mentioned=excluded.money_mentioned, "
        "workaround_described=excluded.workaround_described, solution_requested=excluded.solution_requested, "
        "existing_solutions_named=excluded.existing_solutions_named, language=excluded.language, "
        "classification_failed=excluded.classification_failed, signal_score=excluded.signal_score, "
        "classified_by=excluded.classified_by, classified_at=excluded.classified_at",
        row,
    )


def problems_for_clustering(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return list(conn.execute(
        "SELECT p.id, p.item_id, p.problem_summary, p.domain, p.language, p.pain_score, "
        "i.source, i.created_at, i.url, i.title, i.body FROM problems p JOIN items i ON i.id = p.item_id "
        "WHERE p.is_problem = 1 AND p.classification_failed = 0 AND p.problem_summary IS NOT NULL "
        "AND length(p.problem_summary) > 0 ORDER BY p.id"
    ))


def reset_clusters(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM scores")
    conn.execute("DELETE FROM triangulations")
    conn.execute("DELETE FROM clusters")
    conn.execute("UPDATE problems SET cluster_id = NULL")


def insert_cluster(conn: sqlite3.Connection, row: dict[str, Any]) -> int:
    row = dict(row)
    if isinstance(row.get("sources"), (dict, list)):
        row["sources"] = json.dumps(row["sources"], ensure_ascii=False)
    row.setdefault("created_at", iso(now_utc()))
    for k in ("member_hash", "first_seen", "last_seen", "domain", "growth_30d", "sources"):
        row.setdefault(k, None)
    cur = conn.execute(
        "INSERT INTO clusters (label, canonical_summary, domain, item_count, first_seen, last_seen, "
        "sources, growth_30d, member_hash, created_at) VALUES (:label, :canonical_summary, :domain, "
        ":item_count, :first_seen, :last_seen, :sources, :growth_30d, :member_hash, :created_at)",
        row,
    )
    return int(cur.lastrowid)


def assign_cluster(conn: sqlite3.Connection, cluster_id: int, problem_ids: list[int]) -> None:
    conn.executemany("UPDATE problems SET cluster_id = ? WHERE id = ?",
                     [(cluster_id, pid) for pid in problem_ids])


def cluster_members(conn: sqlite3.Connection, cluster_id: int, limit: int | None = None) -> list[sqlite3.Row]:
    sql = (
        "SELECT p.*, i.source, i.url, i.title, i.body, i.created_at AS item_created_at, "
        "i.language AS item_language FROM problems p JOIN items i ON i.id = p.item_id "
        "WHERE p.cluster_id = ? ORDER BY p.pain_score DESC, i.created_at DESC"
    )
    if limit:
        sql += f" LIMIT {int(limit)}"
    return list(conn.execute(sql, (cluster_id,)))


def insert_score(conn: sqlite3.Connection, row: dict[str, Any]) -> int:
    row = dict(row)
    if isinstance(row.get("existing_solutions"), (list, dict)):
        row["existing_solutions"] = json.dumps(row["existing_solutions"], ensure_ascii=False)
    row.setdefault("scored_at", iso(now_utc()))
    for k in ("volume", "growth", "sources", "languages", "pain", "money", "demand", "workaround", "triangulation",
              "competition", "existing_solutions", "overall_score"):
        row.setdefault(k, None)
    cur = conn.execute(
        "INSERT INTO scores (cluster_id, volume, growth, sources, languages, pain, money, demand, workaround, "
        "triangulation, competition, existing_solutions, overall_score, scored_at) VALUES (:cluster_id, :volume, "
        ":growth, :sources, :languages, :pain, :money, :demand, :workaround, :triangulation, :competition, "
        ":existing_solutions, :overall_score, :scored_at)",
        row,
    )
    return int(cur.lastrowid)


def scores_by_cluster(conn: sqlite3.Connection) -> dict[int, sqlite3.Row]:
    return {int(r["cluster_id"]): r for r in conn.execute("SELECT * FROM scores")}


# ---------------------------------------------------------------- reports / news / triangulation

def _json(v: Any) -> Any:
    return json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v


def insert_report(conn: sqlite3.Connection, row: dict[str, Any]) -> int | None:
    """Insert a report; returns its id, or None if the URL is already stored."""
    row = dict(row)
    row.setdefault("fetched_at", iso(now_utc()))
    for k in ("pdf_url", "published_at", "summary_text", "local_path", "language", "confidence"):
        row.setdefault(k, None)
    cur = conn.execute(
        "INSERT OR IGNORE INTO reports (source, publisher, title, url, pdf_url, published_at, summary_text, "
        "local_path, language, confidence, fetched_at) VALUES (:source, :publisher, :title, :url, :pdf_url, "
        ":published_at, :summary_text, :local_path, :language, :confidence, :fetched_at)", row)
    return int(cur.lastrowid) if cur.rowcount == 1 else None


def report_url_known(conn: sqlite3.Connection, url: str) -> bool:
    return conn.execute("SELECT 1 FROM reports WHERE url = ?", (url,)).fetchone() is not None


def update_report(conn: sqlite3.Connection, report_id: int, **fields: Any) -> None:
    if not fields:
        return
    sets = ", ".join(f"{k} = :{k}" for k in fields)
    conn.execute(f"UPDATE reports SET {sets} WHERE id = :id", {**fields, "id": report_id})


def insert_claim(conn: sqlite3.Connection, row: dict[str, Any]) -> int:
    row = dict(row)
    row["numbers"] = _json(row.get("numbers") or [])
    row["geography"] = _json(row.get("geography") or [])
    row.setdefault("created_at", iso(now_utc()))
    for k in ("heading", "domain", "claim_type", "claim_summary", "confidence_in_source", "chunk_excerpt"):
        row.setdefault(k, None)
    row.setdefault("is_relevant", 1)
    cur = conn.execute(
        "INSERT INTO report_claims (report_id, source_chunk_id, heading, is_relevant, domain, claim_type, "
        "claim_summary, numbers, geography, confidence_in_source, chunk_excerpt, created_at) VALUES (:report_id, "
        ":source_chunk_id, :heading, :is_relevant, :domain, :claim_type, :claim_summary, :numbers, :geography, "
        ":confidence_in_source, :chunk_excerpt, :created_at)", row)
    return int(cur.lastrowid)


def insert_article(conn: sqlite3.Connection, row: dict[str, Any]) -> int | None:
    row = dict(row)
    row.setdefault("fetched_at", iso(now_utc()))
    for k in ("title", "published_at", "summary_text", "language", "domain_hint"):
        row.setdefault(k, None)
    cur = conn.execute(
        "INSERT OR IGNORE INTO news_articles (feed, kind, title, url, published_at, summary_text, language, "
        "domain_hint, fetched_at) VALUES (:feed, :kind, :title, :url, :published_at, :summary_text, :language, "
        ":domain_hint, :fetched_at)", row)
    return int(cur.lastrowid) if cur.rowcount == 1 else None


def insert_catalyst(conn: sqlite3.Connection, row: dict[str, Any]) -> int:
    row = dict(row)
    for k in ("geography", "numbers", "evidence_terms"):
        row[k] = _json(row.get(k) or [])
    row.setdefault("created_at", iso(now_utc()))
    for k in ("catalyst_type", "domain", "summary", "affected_parties", "time_horizon", "catalyst_strength"):
        row.setdefault(k, None)
    cur = conn.execute(
        "INSERT OR REPLACE INTO news_catalysts (article_id, is_catalyst, catalyst_type, domain, geography, summary, "
        "affected_parties, time_horizon, catalyst_strength, numbers, evidence_terms, created_at) VALUES "
        "(:article_id, :is_catalyst, :catalyst_type, :domain, :geography, :summary, :affected_parties, "
        ":time_horizon, :catalyst_strength, :numbers, :evidence_terms, :created_at)", row)
    return int(cur.lastrowid)


def relevant_claims(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return list(conn.execute(
        "SELECT c.*, r.publisher, r.url AS report_url, r.title AS report_title, r.published_at "
        "FROM report_claims c JOIN reports r ON r.id = c.report_id WHERE c.is_relevant = 1 ORDER BY c.id"))


def catalysts(conn: sqlite3.Connection, since: str | None = None) -> list[sqlite3.Row]:
    sql = ("SELECT n.*, a.title, a.url, a.published_at, a.feed, a.kind, a.language FROM news_catalysts n "
           "JOIN news_articles a ON a.id = n.article_id WHERE n.is_catalyst = 1")
    args: tuple = ()
    if since:
        sql += " AND a.published_at >= ?"
        args = (since,)
    return list(conn.execute(sql + " ORDER BY n.catalyst_strength DESC, a.published_at DESC", args))


def insert_triangulation(conn: sqlite3.Connection, row: dict[str, Any]) -> int:
    row = dict(row)
    for k in ("claim_ids", "catalyst_ids", "geography"):
        row[k] = _json(row.get(k) or [])
    row.setdefault("created_at", iso(now_utc()))
    for k in ("best_claim_id", "market_size_source", "why_now", "evidence_gaps"):
        row.setdefault(k, None)
    cur = conn.execute(
        "INSERT OR REPLACE INTO triangulations (cluster_id, pain_evidence, market_evidence, timing_evidence, "
        "triangulation_score, best_claim_id, claim_ids, catalyst_ids, market_size_source, why_now, evidence_gaps, "
        "geography, created_at) VALUES (:cluster_id, :pain_evidence, :market_evidence, :timing_evidence, "
        ":triangulation_score, :best_claim_id, :claim_ids, :catalyst_ids, :market_size_source, :why_now, "
        ":evidence_gaps, :geography, :created_at)", row)
    return int(cur.lastrowid)


def insert_hypothesis(conn: sqlite3.Connection, row: dict[str, Any]) -> int:
    row = dict(row)
    row["geography"] = _json(row.get("geography") or [])
    row.setdefault("created_at", iso(now_utc()))
    for k in ("summary", "domain", "strength", "ask_whom", "source_url"):
        row.setdefault(k, None)
    cur = conn.execute(
        "INSERT INTO hypotheses (kind, ref_id, summary, domain, geography, strength, ask_whom, source_url, created_at) "
        "VALUES (:kind, :ref_id, :summary, :domain, :geography, :strength, :ask_whom, :source_url, :created_at)", row)
    return int(cur.lastrowid)


EVAL_FIELDS = ("member_hash", "member_item_ids", "item_count", "market_size_estimate", "market_size_reasoning",
               "market_size_source", "monopoly_potential", "location_independent", "runs_without_founder",
               "capital_light", "measurable_90d", "path_to_1b", "path_to_1b_score", "what_would_kill_it",
               "quickest_test", "why_now", "evidence_gaps", "corridor_advantage", "overall_score", "model",
               "reused_from", "raw_json")


def insert_evaluation(conn: sqlite3.Connection, row: dict[str, Any]) -> int:
    row = dict(row)
    for k in ("member_item_ids", "raw_json"):
        row[k] = _json(row.get(k))
    row.setdefault("evaluated_at", iso(now_utc()))
    for k in EVAL_FIELDS:
        row.setdefault(k, None)
    cols = ("cluster_id",) + EVAL_FIELDS + ("evaluated_at",)
    cur = conn.execute(f"INSERT INTO evaluations ({', '.join(cols)}) VALUES ({', '.join(':' + c for c in cols)})", row)
    return int(cur.lastrowid)


def latest_evaluations(conn: sqlite3.Connection) -> dict[int, sqlite3.Row]:
    """Most recent evaluation per *current* cluster, keyed by cluster_id."""
    rows = conn.execute(
        "SELECT e.* FROM evaluations e JOIN (SELECT cluster_id, MAX(id) AS mid FROM evaluations GROUP BY cluster_id) m "
        "ON m.mid = e.id JOIN clusters c ON c.id = e.cluster_id")
    return {int(r["cluster_id"]): r for r in rows}


def prior_evaluations(conn: sqlite3.Connection, limit: int = 500) -> list[sqlite3.Row]:
    """Latest evaluations of any earlier cluster (for exact or overlap-based reuse)."""
    return list(conn.execute(
        "SELECT e.* FROM evaluations e JOIN (SELECT member_hash, MAX(id) AS mid FROM evaluations "
        "WHERE reused_from IS NULL GROUP BY member_hash) m ON m.mid = e.id ORDER BY e.id DESC LIMIT ?", (limit,)))


def log_llm_call(conn: sqlite3.Connection, **kw: Any) -> None:
    kw.setdefault("called_at", iso(now_utc()))
    for k in ("model", "items", "prompt_chars", "duration_ms", "error"):
        kw.setdefault(k, None)
    kw.setdefault("ok", 1)
    conn.execute("INSERT INTO llm_calls (called_at, stage, backend, model, items, prompt_chars, ok, duration_ms, error) "
                 "VALUES (:called_at, :stage, :backend, :model, :items, :prompt_chars, :ok, :duration_ms, :error)", kw)


def triangulations_by_cluster(conn: sqlite3.Connection) -> dict[int, sqlite3.Row]:
    return {int(r["cluster_id"]): r for r in conn.execute("SELECT * FROM triangulations")}


def start_run(conn: sqlite3.Connection, stage: str) -> int:
    cur = conn.execute("INSERT INTO runs (stage, started_at) VALUES (?, ?)", (stage, iso(now_utc())))
    return int(cur.lastrowid)


def finish_run(conn: sqlite3.Connection, run_id: int, notes: str | None = None) -> None:
    conn.execute("UPDATE runs SET finished_at = ?, notes = ? WHERE id = ?",
                 (iso(now_utc()), notes, run_id))
