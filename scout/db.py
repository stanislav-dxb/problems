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
    competition        INTEGER,        -- distinct existing solutions named by members
    existing_solutions TEXT,           -- JSON [[name, count], ...]
    overall_score      REAL,
    scored_at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_scores_cluster ON scores (cluster_id);

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
    conn = sqlite3.connect(p)
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
    for legacy in ("evaluations", "api_calls"):
        conn.execute(f"DROP TABLE IF EXISTS {legacy}")
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
              "signal_score"):
        row.setdefault(k, None)
    conn.execute(
        "INSERT INTO problems (item_id, is_problem, domain, who_has_it, problem_summary, pain_score, "
        "money_mentioned, workaround_described, solution_requested, existing_solutions_named, "
        "language, classification_failed, signal_score, classified_at) VALUES (:item_id, :is_problem, :domain, "
        ":who_has_it, :problem_summary, :pain_score, :money_mentioned, :workaround_described, "
        ":solution_requested, :existing_solutions_named, :language, :classification_failed, :signal_score, "
        ":classified_at) ON CONFLICT(item_id) DO UPDATE SET is_problem=excluded.is_problem, "
        "domain=excluded.domain, who_has_it=excluded.who_has_it, problem_summary=excluded.problem_summary, "
        "pain_score=excluded.pain_score, money_mentioned=excluded.money_mentioned, "
        "workaround_described=excluded.workaround_described, solution_requested=excluded.solution_requested, "
        "existing_solutions_named=excluded.existing_solutions_named, language=excluded.language, "
        "classification_failed=excluded.classification_failed, signal_score=excluded.signal_score, "
        "classified_at=excluded.classified_at",
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
    conn.execute("DELETE FROM clusters")
    conn.execute("UPDATE problems SET cluster_id = NULL")


def insert_cluster(conn: sqlite3.Connection, row: dict[str, Any]) -> int:
    row = dict(row)
    if isinstance(row.get("sources"), (dict, list)):
        row["sources"] = json.dumps(row["sources"], ensure_ascii=False)
    row.setdefault("created_at", iso(now_utc()))
    row.setdefault("member_hash", None)
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
    for k in ("volume", "growth", "sources", "languages", "pain", "money", "demand", "workaround",
              "competition", "existing_solutions", "overall_score"):
        row.setdefault(k, None)
    cur = conn.execute(
        "INSERT INTO scores (cluster_id, volume, growth, sources, languages, pain, money, demand, workaround, "
        "competition, existing_solutions, overall_score, scored_at) VALUES (:cluster_id, :volume, :growth, "
        ":sources, :languages, :pain, :money, :demand, :workaround, :competition, :existing_solutions, "
        ":overall_score, :scored_at)",
        row,
    )
    return int(cur.lastrowid)


def scores_by_cluster(conn: sqlite3.Connection) -> dict[int, sqlite3.Row]:
    return {int(r["cluster_id"]): r for r in conn.execute("SELECT * FROM scores")}


def start_run(conn: sqlite3.Connection, stage: str) -> int:
    cur = conn.execute("INSERT INTO runs (stage, started_at) VALUES (?, ?)", (stage, iso(now_utc())))
    return int(cur.lastrowid)


def finish_run(conn: sqlite3.Connection, run_id: int, notes: str | None = None) -> None:
    conn.execute("UPDATE runs SET finished_at = ?, notes = ? WHERE id = ?",
                 (iso(now_utc()), notes, run_id))
