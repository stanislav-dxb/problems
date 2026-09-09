from scout import db as dbm
from scout.claims import ask_whom, classify_chunk, process_report
from scout.sources.reports.pdf import chunk_text, extract_text

TEXT = """1 Executive Summary
The logistics market in the Gulf was valued at $45 billion in 2024 and is expected to grow at a CAGR of 6% to 2030, a clear opportunity.

2 Structural Issues
Customs clearance remains fragmented and largely manual; small forwarders lack a standard data exchange, and
a shortage of skilled staff persists across Saudi Arabia and the UAE.

3 Methodology
This chapter explains how interviews were conducted and how the sample was selected."""


def _pdf(tmp_path):
    import pymupdf as fitz
    path = tmp_path / "fixture.pdf"
    doc = fitz.open()
    for para in TEXT.split("\n\n"):
        page = doc.new_page()
        page.insert_textbox(fitz.Rect(50, 72, 560, 700), para, fontsize=10)  # wraps; insert_text would clip
    doc.save(str(path))
    doc.close()
    return path


def test_pdf_chunk_classify_store(conn, cfg, tmp_path):
    path = _pdf(tmp_path)
    text = extract_text(path)
    assert "Executive Summary" in text and "$45 billion" in text
    cfg["reports"]["chunk_chars"] = 120  # tiny chunks so each section of the fixture is its own chunk
    chunks = chunk_text(text, target_chars=120)
    assert len(chunks) == 3 and chunks[0]["chunk_id"] == "c1"
    assert any((c["heading"] or "").startswith(("1 Executive", "2 Structural", "3 Methodology")) for c in chunks)
    rid = dbm.insert_report(conn, {"source": "test", "publisher": "Test Institute", "title": "Gulf logistics",
                                   "url": "https://example.org/r1", "confidence": 4})
    total, kept = process_report(conn, rid, text, cfg, confidence=4)
    assert total == len(chunks) and 1 <= kept < total  # methodology chunk fails the prefilter
    claims = dbm.relevant_claims(conn)
    types = {c["claim_type"] for c in claims}
    assert "market_size" in types and "structural_gap" in types
    ms = next(c for c in claims if c["claim_type"] == "market_size")
    assert "$45 billion" in ms["numbers"] and "gulf" in ms["geography"] and ms["confidence_in_source"] == 4
    assert dbm.insert_report(conn, {"source": "test", "publisher": "x", "title": "dup", "url": "https://example.org/r1"}) is None
    rep = conn.execute("SELECT chunks_total, chunks_kept FROM reports WHERE id = ?", (rid,)).fetchone()
    assert (rep[0], rep[1]) == (total, kept)


def test_classify_chunk_rules_and_ask_whom():
    assert classify_chunk("Weather was pleasant throughout the quarter.", None, ["gap"], 3) is None
    row = classify_chunk("Rising demand for cold storage in India; the sector remains fragmented.", "Cold chain", ["fragmented"], 3)
    assert row["claim_type"] in ("structural_gap", "demand_shift") and row["geography"] == ["india"]
    assert ask_whom("construction subcontractors") == "a site manager or subcontractor"
    assert ask_whom("something else entirely").startswith("a distributor")
