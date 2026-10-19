"""One coherent synthetic walkthrough; reuses Day 10 terms and Day 6 outcomes."""
import json

from app.day6_fixture import SCENARIOS, WALKTHROUGH
from app.conversation_fixture import finding
from app.conversations import requested_work
from app.extraction import ExtractionError, normalize


def scenario():
    return {"id": "project-intelligence", "title": "Project intelligence walkthrough",
            "description": "One synthetic USD 3 agreement, three milestones and chronological requests. Review decisions and any simulation remain explicit.",
            "filename": WALKTHROUGH.name, "text": WALKTHROUGH.read_text(encoding="utf-8"), "supports_extraction": True,
            "requested_changes": [], "mode": "offline_demo", "live_ai": False,
            "conversation_messages": [{"sequence": i, "sender": "Alex (synthetic client)", "role": "client", "content": item[0], "sent_at": None} for i, item in enumerate(SCENARIOS, 1)]}


def compare(text, messages):
    fields = ("sequence", "sender", "role", "content", "sent_at")
    if normalize(text) != normalize(scenario()["text"]) or [{k: m[k] for k in fields} for m in messages] != scenario()["conversation_messages"]:
        raise ExtractionError("OFFLINE_DEMO_CONTRACT_MISMATCH", "Use the unchanged project intelligence example", 422)
    works = requested_work(messages)
    findings = [finding(i, works[i-1], item[1], item[4], item[2], item[3], [messages[i-1]], ["REVISIONS" if i == 1 else "DELIVERABLE" if i == 2 else "MAINTENANCE"], "INDIVIDUAL") for i, item in enumerate(SCENARIOS, 1)]
    findings.append(finding(4, works[-1], "AMBIGUOUS", "The conversation mixes included revisions, excluded checkout work and unspecified support. Negotiate the excluded work and clarify support separately.", SCENARIOS[2][2], "CONTEXT", messages, ["DELIVERABLE", "REVISIONS", "MAINTENANCE"], "CUMULATIVE"))
    return json.dumps({"findings": findings})
