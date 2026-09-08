from scout import db as dbm
from scout.sources.base import make_item, scrub
from scout.util import hash_handle


def _item(source="hn", sid="1", body="x" * 100):
    return make_item(source, sid, "https://example.com/1", "title", body, "someone", 1_700_000_000,
                     {"objectID": sid, "author": "someone"})


def test_duplicate_source_id_is_ignored(conn):
    assert dbm.insert_items(conn, [_item(sid="42")]) == (1, 0)
    assert dbm.insert_items(conn, [_item(sid="42", body="different body " * 10)]) == (0, 1)
    assert conn.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 1


def test_same_id_in_different_sources_is_kept(conn):
    inserted, skipped = dbm.insert_items(conn, [_item(source="hn", sid="7"), _item(source="reddit", sid="7")])
    assert (inserted, skipped) == (2, 0)


def test_duplicates_within_one_batch(conn):
    batch = [_item(sid="a"), _item(sid="a"), _item(sid="b")]
    assert dbm.insert_items(conn, batch) == (2, 1)


def test_items_missing_ids_are_skipped(conn):
    bad = _item(sid="")
    assert dbm.insert_items(conn, [bad]) == (0, 1)


def test_author_handle_is_hashed_and_raw_json_scrubbed(conn):
    it = _item(sid="9")
    assert it["author_handle"] == hash_handle("someone")
    assert it["author_handle"] != "someone" and len(it["author_handle"]) == 16
    assert "author" not in it["raw_json"] and it["raw_json"]["objectID"] == "9"
    dbm.insert_items(conn, [it])
    row = conn.execute("SELECT author_handle, raw_json FROM items").fetchone()
    assert "someone" not in row["author_handle"] and "someone" not in row["raw_json"]


def test_scrub_is_recursive():
    raw = {"user": {"name": "x"}, "data": [{"author": "y", "score": 3}], "kept": 1}
    assert scrub(raw) == {"data": [{"score": 3}], "kept": 1}


def test_unclassified_items_excludes_classified(conn):
    dbm.insert_items(conn, [_item(sid="1"), _item(sid="2")])
    ids = [r["id"] for r in dbm.unclassified_items(conn)]
    assert len(ids) == 2
    dbm.upsert_problem(conn, {"item_id": ids[0], "is_problem": 1, "problem_summary": "s", "classified_at": "t"})
    assert [r["id"] for r in dbm.unclassified_items(conn)] == [ids[1]]
    dbm.upsert_problem(conn, {"item_id": ids[1], "is_problem": 0, "classification_failed": 1, "classified_at": "t"})
    assert dbm.unclassified_items(conn) == []
    assert [r["id"] for r in dbm.unclassified_items(conn, retry_failed=True)] == [ids[1]]
