"""Conservative source-section association for untrusted milestone facts.

Sections come from the document, never the output order or provider offsets.
Unrecognizable/ambiguous headings require manual correction. A literal quote in
another section is not evidence for this milestone, even when totals match.
"""
from dataclasses import dataclass
from decimal import Decimal
import re

from app.evidence import source_matches


HEADING = re.compile(r"^[ \t]*(?:\#{1,6}[ \t]+)?Milestone(?:[ \t]+(?:\d+|[IVXLCDM]+))?[ \t]*[:.\-–—][ \t]*", re.I | re.M)
REFERENCE = re.compile(r"\bmilestone[ \t]+(\d+|[IVXLCDM]+)\b", re.I)
UNIVERSAL = re.compile(r"\b(?:all|each|every)\s+(?:(?:project|payment)\s+)?milestones?\b", re.I)
GLOBAL_CURRENCY = re.compile(r"\bcurrency\s*:|\ball\s+(?:project\s+)?(?:fees|payments)\b|\btotal(?:\s+project)?\s+(?:price|value|fees?)\b", re.I)
TARGETED_CLAUSE = re.compile(r"^\s*Milestone\s+(?P<reference>\d+|[IVXLCDM]+)\s+(?P<field>currency|amount|fee|price|payment condition|deadline|deliverable|acceptance criterion)\s*:", re.I)
TARGET_FIELDS = {"currency": {"currency"}, "amount": {"amount", "fee", "price"},
                 "payment_conditions": {"payment condition"}, "deadline": {"deadline"},
                 "deliverables": {"deliverable"}, "acceptance_criteria": {"acceptance criterion"}}
PARAGRAPH_BREAK = re.compile(r"\r?\n[ \t]*\r?\n")


def monetary_value(value: Decimal, excerpt: str, currency: str | None) -> bool:
    """A heading number or deadline is not a monetary amount."""
    code = re.escape(currency) if currency else r"[A-Z]{3}"
    number = r"(?P<amount>(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)(?![\w,%]|\.\d|[-/]\d)"
    prefix = rf"(?:\b{code}\s*|[$€£₹]\s*|(?i:\b(?:amount|fee|price|payment|cost|instalment|installment)\s*[:=]?\s*)(?:{code}\s*)?)"
    return any(Decimal(match.group("amount").replace(",", "")) == value
               for match in re.finditer(prefix + number, excerpt))


def currency_consistent(value: str, text: str, section: "Section") -> bool:
    # A project-wide currency cannot silently override an explicit local one.
    declared = []
    for line in text[section.start:section.end].splitlines():
        targeted = TARGETED_CLAUSE.match(line)
        if targeted and targeted.group("reference").casefold() != section.reference:
            continue
        declared.extend(re.findall(r"(?i:\bcurrency\s*:)\s*([A-Z]{3})\b", line))
    return not declared or set(declared) == {value}


@dataclass(frozen=True)
class Section:
    start: int
    heading_end: int
    end: int
    reference: str | None


def sections(text: str) -> list[Section]:
    headings = list(HEADING.finditer(text))
    result = []
    for i, heading in enumerate(headings):
        end = headings[i + 1].start() if i + 1 < len(headings) else len(text)
        # A blank paragraph ends the milestone's own clause block. Shared terms
        # after it need an explicit universal/targeted association below.
        paragraph = PARAGRAPH_BREAK.search(text[heading.start():end])
        if paragraph:
            end = heading.start() + paragraph.start()
        line_end = text.find("\n", heading.end(), end)
        reference = REFERENCE.search(heading.group())
        result.append(Section(heading.start(), line_end if line_end != -1 else end, end,
                              reference.group(1).casefold() if reference else None))
    return result


def section_for_name(text: str, fact, candidates: list[Section]) -> Section | None:
    if fact.value is None:
        return None
    normalized = " ".join(str(fact.value).split()).casefold()
    found = []
    for section in candidates:
        heading = text[section.start:section.heading_end]
        if normalized not in " ".join(heading.split()).casefold():
            continue
        if any(match.start() < section.heading_end and match.end() > section.start
               for match in source_matches(text, fact.source_excerpt)):
            found.append(section)
    return found[0] if len(found) == 1 else None


def associated_match(text: str, excerpt: str | None, section: Section, candidates: list[Section], field: str):
    matches = source_matches(text, excerpt)
    for match in matches:
        if section.start <= match.start() and match.end() <= section.end:
            return match
    for match in matches:
        quote = text[match.start():match.end()]
        references = {ref.casefold() for ref in REFERENCE.findall(quote)}
        targeted = TARGETED_CLAUSE.match(quote)
        explicit_target = (targeted and targeted.group("reference").casefold() == section.reference
                           and targeted.group("field").casefold() in TARGET_FIELDS.get(field, set()))
        # Merely mentioning another milestone as a prerequisite does not assign
        # this clause's obligation to it. Only an explicit field assignment can
        # override section locality.
        if any(other.start <= match.start() < other.end for other in candidates) and not explicit_target:
            continue
        if references:
            if explicit_target and references == {section.reference}:
                return match
            continue
        # Use the complete paragraph for applicability; a model may quote only
        # "USD" or "Written acceptance" from an explicit shared clause.
        before, after = 0, len(text)
        for separator in PARAGRAPH_BREAK.finditer(text):
            if separator.end() <= match.start():
                before = separator.end()
            elif separator.start() >= match.end():
                after = separator.start()
                break
        paragraph = text[before:after]
        if UNIVERSAL.search(paragraph):
            return match
        if field == "currency" and match.end() <= candidates[0].start and GLOBAL_CURRENCY.search(paragraph):
            return match
    return None


def association_failure(text: str, excerpt: str | None, section: Section, candidates: list[Section]) -> str:
    """Distinguish a wrong source milestone from a layout we cannot associate."""
    for match in source_matches(text, excerpt):
        if any(other != section and other.start <= match.start() < other.end for other in candidates):
            return "MILESTONE_EVIDENCE_MISMATCH"
        targeted = TARGETED_CLAUSE.match(text[match.start():match.end()])
        if targeted and targeted.group("reference").casefold() != section.reference:
            return "MILESTONE_EVIDENCE_MISMATCH"
    return "MILESTONE_ASSOCIATION_UNCERTAIN"
