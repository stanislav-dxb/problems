"""PDF download, text extraction (pymupdf) and chunking with headings preserved."""
from __future__ import annotations

import hashlib
import logging
import re
from pathlib import Path

from ..base import http_client

log = logging.getLogger("scout.reports.pdf")


def download_pdf(url: str, dest_dir: str | Path, max_mb: int = 25) -> Path | None:
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    path = dest / (hashlib.sha1(url.encode()).hexdigest()[:16] + ".pdf")
    if path.exists():
        return path
    try:
        with http_client(timeout=120, headers={"Accept": "application/pdf,*/*"}) as client, client.stream("GET", url) as r:
            r.raise_for_status()
            size = 0
            with open(path, "wb") as f:
                for chunk in r.iter_bytes():
                    size += len(chunk)
                    if size > max_mb * 1024 * 1024:
                        raise ValueError(f"larger than {max_mb} MB")
                    f.write(chunk)
        with open(path, "rb") as f:
            if f.read(5) != b"%PDF-":
                raise ValueError("not a PDF")
        return path
    except Exception as e:  # noqa: BLE001
        log.info("pdf skipped %s: %s", url, e)
        path.unlink(missing_ok=True)
        return None


def extract_text(path: str | Path, max_pages: int = 400) -> str:
    import pymupdf as fitz  # lazy: heavy import
    parts = []
    with fitz.open(str(path)) as doc:
        for i, page in enumerate(doc):
            if i >= max_pages:
                break
            parts.append(page.get_text("text"))
    return "\n".join(parts)


_HEADING_RE = re.compile(r"^(?:\d+(?:\.\d+)*\s+)?[A-Z][^.!?]{3,80}$")


def chunk_text(text: str, target_chars: int = 8000) -> list[dict]:
    """~2,000-token chunks split on blank-line paragraph boundaries; each carries the last heading seen.
    Oversized blocks (PDF pages without blank lines) are hard-split on line boundaries."""
    paras: list[str] = []
    for block in re.split(r"\n\s*\n", text or ""):
        block = block.strip()
        while len(block) > 2 * target_chars:
            cut = block.rfind("\n", 0, target_chars)
            cut = cut if cut > 0 else target_chars
            paras.append(block[:cut].strip())
            block = block[cut:].strip()
        if block:
            paras.append(block)
    chunks: list[dict] = []
    buf: list[str] = []
    size = 0
    heading = None
    chunk_heading = None
    for p in paras:
        line = p.splitlines()[0].strip()
        if len(line) <= 90 and _HEADING_RE.match(line) and not line.endswith((".", ",")):
            heading = line
        if not buf:
            chunk_heading = heading
        buf.append(p)
        size += len(p)
        if size >= target_chars:
            chunks.append({"chunk_id": f"c{len(chunks) + 1}", "heading": chunk_heading, "text": "\n\n".join(buf)})
            buf, size = [], 0
    if buf:
        chunks.append({"chunk_id": f"c{len(chunks) + 1}", "heading": chunk_heading, "text": "\n\n".join(buf)})
    return chunks
