"""Physical-page spans in stored extracted text; never visual coordinates or labels."""
import hashlib

METHOD = "pypdf-physical-pages-v1"


def text_hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def page_provenance(pages):
    raw = "\n\n".join(pages)
    text = raw.strip()
    left = len(raw) - len(raw.lstrip())
    spans, offset = [], 0
    for number, page in enumerate(pages, 1):
        start = min(len(text), max(0, offset - left))
        end = min(len(text), max(0, offset + len(page) - left))
        spans.append({"page": number, "start": start, "end": end})
        offset += len(page) + 2
    return {"method": METHOD, "text_sha256": text_hash(text), "pages": spans}


def verified_provenance(text, page_count, value, *, document_sha256=None):
    """Missing/invalid metadata yields no page claim, including legacy records."""
    if not isinstance(value, dict) or value.get("method") != METHOD or value.get("text_sha256") != text_hash(text):
        return None
    if document_sha256 is not None and value.get("document_sha256") != document_sha256:
        return None
    pages = value.get("pages")
    if not isinstance(pages, list) or not 1 <= (page_count or 0) <= 50 or len(pages) != page_count:
        return None
    previous = 0
    for number, span in enumerate(pages, 1):
        if not isinstance(span, dict) or set(span) != {"page", "start", "end"}:
            return None
        if any(type(span[key]) is not int for key in span):
            return None
        if span["page"] != number or not previous <= span["start"] <= span["end"] <= len(text):
            return None
        # Only join whitespace may fall outside a page. Never silently omit content.
        if text[previous:span["start"]].strip():
            return None
        previous = span["end"]
    return value if not text[previous:].strip() else None
