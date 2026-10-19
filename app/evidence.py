"""Contiguous source matching with whitespace-only quotation normalization."""
import re


def source_matches(text: str, excerpt: str | None):
    """Return every exact/whitespace-only occurrence, ignoring supplied offsets."""
    if not excerpt or not excerpt.split():
        return []
    # Escape every non-whitespace character. No case/punctuation rewriting,
    # fuzzy matching, ellipsis expansion, word skipping or invented quotations.
    parts = excerpt.split()
    pattern = r"\s+".join(re.escape(part) for part in parts)
    # A literal prefix such as "USD 30" is not evidence for 30 when the source
    # says "USD 300". Quotes must not cut into a word or number at either edge.
    if re.match(r"\w", parts[0][0]):
        pattern = r"(?<!\w)" + pattern
    if re.match(r"\w", parts[-1][-1]):
        pattern += r"(?!\w)"
    if parts[0][0].isdigit():
        pattern = r"(?<!\d[.,])" + pattern
    if parts[-1][-1].isdigit():
        pattern += r"(?![.,]\d)"
    elif re.search(r"\d[.,]$", parts[-1]):
        pattern += r"(?!\d)"
    # Advancing by match.end() would miss overlapping quotations, e.g. two
    # occurrences of "very very" in "very very very", falsely proving uniqueness.
    compiled = re.compile(pattern)
    matches, position = [], 0
    while match := compiled.search(text, position):
        matches.append(match)
        position = match.start() + 1
    return matches


def source_match(text: str, excerpt: str | None, *, anchor: int | None = None):
    matches = source_matches(text, excerpt)
    return next((match for match in matches if anchor is None or match.start() >= anchor),
                matches[0] if matches else None)


def source_occurrence(text: str, excerpt: str | None, start=None, end=None):
    """Select a verified occurrence; never repair inconsistent supplied offsets.

    Python code-point offsets are authoritative only after exact slice validation.
    Whitespace-only normalization remains available for a unique offset-free quote.
    """
    matches = source_matches(text, excerpt)
    if start is not None or end is not None:
        if (type(start) is not int or type(end) is not int
                or not 0 <= start < end <= len(text) or text[start:end] != excerpt):
            return None
        return next((m for m in matches if (m.start(), m.end()) == (start, end)), None)
    return matches[0] if len(matches) == 1 else None
