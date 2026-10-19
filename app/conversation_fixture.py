"""Three exact, synthetic, offline conversations. No inference for arbitrary input."""
import json
from pathlib import Path
from app.conversations import requested_work
from app.extraction import ExtractionError, normalize

PATH = Path(__file__).resolve().parents[1] / "examples/day17_studio_agreement.txt"
COVERAGE = "Two rounds of text and colour revisions to the landing page are included."
LIMIT = "Additional sections and a content management system are excluded from the agreed deliverables."
MAINTENANCE = "Ongoing maintenance, hosting administration and weekend support are excluded."
DEADLINE = "The agreed delivery date is 2026-11-20; an earlier delivery requires a separately agreed schedule."
CONTEXT = "Defects against the agreed acceptance criteria will be corrected during acceptance review."


def message(content, sent_at=None):
    return {"sender": "Alex (synthetic client)", "role": "client", "content": content, "sent_at": sent_at}


SCENARIOS = [
    {"id": "conversation-revisions", "title": "A · Mostly included revisions",
     "description": "Text and colour feedback stays within the agreement; a repeated request must not become another charge.",
     "messages": [message("Please change the landing page button colour during the first revision round.", "2026-11-05"),
                  message("Please update the headline wording in the same revision round."),
                  message("Please change the landing page button colour during the first revision round.")],
     "classifications": ["IN_SCOPE", "IN_SCOPE", "IN_SCOPE"], "clauses": [COVERAGE] * 3,
     "relationships": ["COVERAGE"] * 3, "types": [["REVISIONS"]] * 3,
     "effects": ["INDIVIDUAL", "INDIVIDUAL", "REPEATED"], "cumulative": "IN_SCOPE",
     "cumulative_clause": COVERAGE, "cumulative_relationship": "COVERAGE",
     "cumulative_explanation": "Taken together, the messages ask for included text and colour revisions. The repeated colour request is not an additional deliverable."},
    {"id": "conversation-expansion", "title": "B · Gradual scope expansion",
     "description": "Small additions accumulate into extra sections, an editing system, an accelerated schedule and ongoing work.",
     "messages": [message("Could you also add a small FAQ section?"),
                  message("Please give our team a content management system to edit the page."),
                  message("Can you deliver on 2026-11-12 instead?"),
                  message("Please keep the page updated after launch and cover weekend support.")],
     "classifications": ["OUT_OF_SCOPE"] * 4, "clauses": [LIMIT, LIMIT, DEADLINE, MAINTENANCE],
     "relationships": ["EXCLUSION", "EXCLUSION", "LIMIT", "EXCLUSION"],
     "types": [["DELIVERABLE"], ["DELIVERABLE"], ["DEADLINE"], ["MAINTENANCE"]],
     "effects": ["INDIVIDUAL"] * 4, "cumulative": "OUT_OF_SCOPE",
     "cumulative_clause": LIMIT, "cumulative_relationship": "EXCLUSION",
     "cumulative_explanation": "The conversation adds excluded deliverables and services while requesting an earlier schedule. Together these require a separately reviewed scope and schedule agreement."},
    {"id": "conversation-ambiguous", "title": "C · Clarify before agreeing",
     "description": "Conflicting design direction and vague polishing/support language require negotiation rather than an automatic decision.",
     "messages": [message("Keep the approved visual direction and just polish the page."),
                  message("Actually, replace the whole visual direction before launch."),
                  message("Could you look after any little issues once we launch?")],
     "classifications": ["AMBIGUOUS"] * 3, "clauses": [COVERAGE, COVERAGE, CONTEXT],
     "relationships": ["CONTEXT"] * 3, "types": [["REVISIONS"], ["REVISIONS"], ["MAINTENANCE"]],
     "effects": ["INDIVIDUAL", "CONFLICTING", "INDIVIDUAL"], "cumulative": "AMBIGUOUS",
     "cumulative_clause": CONTEXT, "cumulative_relationship": "CONTEXT",
     "cumulative_explanation": "Conflicting direction and unspecified post-launch assistance do not establish an agreed new obligation. Clarify the requested outcome and acceptance boundary."},
]


def catalog():
    return [{"id": item["id"], "title": item["title"], "description": item["description"],
             "filename": PATH.name, "text": PATH.read_text(encoding="utf-8"),
             "supports_extraction": False, "requested_changes": [], "mode": "offline_demo", "live_ai": False,
             "conversation_messages": [row | {"sequence": index} for index, row in enumerate(item["messages"], 1)]}
            for item in SCENARIOS]


def compare(contract_text, messages):
    if normalize(contract_text) != normalize(PATH.read_text(encoding="utf-8")):
        raise ExtractionError("OFFLINE_DEMO_CONTRACT_MISMATCH", "Choose the matching synthetic conversation agreement", 422)
    data = [{key: item[key] for key in ("sender", "role", "content", "sent_at")} for item in messages]
    scenario = next((item for item in SCENARIOS if item["messages"] == data), None)
    if scenario is None or [item["sequence"] for item in messages] != list(range(1, len(messages) + 1)):
        raise ExtractionError("OFFLINE_DEMO_CONTRACT_MISMATCH", "Choose an unchanged predefined conversation", 422)
    work = requested_work(messages)
    findings = []
    for index, item in enumerate(messages):
        classification = scenario["classifications"][index]
        effect = scenario["effects"][index]
        evidence = [item] if effect == "INDIVIDUAL" else [messages[0], item]
        findings.append(finding(index + 1, work[index], classification,
            "The request is assessed against the quoted original clause and the earlier conversation context.",
            scenario["clauses"][index], scenario["relationships"][index], evidence,
            scenario["types"][index], effect))
    findings.append(finding(len(messages) + 1, work[-1], scenario["cumulative"],
        scenario["cumulative_explanation"], scenario["cumulative_clause"], scenario["cumulative_relationship"],
        messages, sorted({kind for row in scenario["types"] for kind in row}), "CUMULATIVE"))
    return json.dumps({"findings": findings})


def finding(ordinal, work, classification, explanation, clause, relationship, messages, types, effect):
    return {"ordinal": ordinal, "requested_work": work, "classification": classification,
            "explanation": explanation,
            "contract_evidence": [{"source_excerpt": clause, "relationship": relationship}],
            "message_evidence": [{"message_id": item["id"], "source_excerpt": item["content"]} for item in messages],
            "change_types": types, "conversation_effect": effect,
            "uncertainties": ["Confirm the intended deliverables and obligations with the client."] if classification == "AMBIGUOUS" else [],
            "recommended_action": {"IN_SCOPE": "FULFILL_EXISTING_SCOPE", "OUT_OF_SCOPE": "DRAFT_CHANGE_ORDER",
                                   "AMBIGUOUS": "CLARIFY_WITH_CLIENT"}[classification], "requires_human_review": True}
