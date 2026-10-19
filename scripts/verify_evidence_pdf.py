"""Read a generated report and recheck attachment integrity and exact source quotes."""
from io import BytesIO
import hashlib
import json
from pathlib import Path

from pypdf import PdfReader
from app.evidence_pack import digest
from app.report_content import document


def verify(path):
    data = Path(path).read_bytes()
    reader = PdfReader(BytesIO(data), strict=True)
    assert 1 <= len(reader.pages) <= 160 and not reader.is_encrypted
    if reader.metadata.title == "ScopeShield Executive Summary": assert len(reader.pages) <= 2
    assert set(reader.attachments) == {"project-snapshot.json"}
    snapshot = json.loads(reader.attachments["project-snapshot.json"][0])
    original = dict(snapshot)
    revision = original.pop("source_revision"); original.pop("generated_at")
    assert digest(original) == revision
    assert "/OpenAction" not in reader.trailer["/Root"]
    names = reader.trailer["/Root"].get("/Names", {})
    assert "/JavaScript" not in names
    for i, page in enumerate(reader.pages, 1):
        text = page.extract_text()
        assert text.strip() and f"Page {i}" in text and not page.get("/Annots")
    sources = [c["text"] for c in snapshot["contracts"]] + [m["content"] for r in snapshot["requests"] for m in r["conversation_messages"] or []]
    content = document(snapshot)
    quotes = [b["text"] for s in content["sections"] for b in s["blocks"] if b["kind"] == "quote"]
    assert all(any(quote in source for source in sources) for quote in quotes)
    return {"pdf_sha256": hashlib.sha256(data).hexdigest(), "source_revision": revision,
            "pages": len(reader.pages), "exact_source_blocks": len(quotes),
            "view": "executive" if reader.metadata.title == "ScopeShield Executive Summary" else "complete",
            "source_blocks_count_basis": "all revalidated snapshot quotations; not the selected printed-brief count", "active_content": False,
            "attachment_hash_verified": True}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument("pdf")
    print(json.dumps(verify(parser.parse_args().pdf)))
