"""Source identity/order validation layered over the existing scope validator."""
import hashlib
import json
import re
from uuid import uuid4

from fastapi import HTTPException
from app.conversation_schemas import CUMULATIVE_WORK
from app.evidence import source_matches
from app.extraction import ExtractionError


def requested_work(messages):
    return [message["content"].strip() for message in messages] + [CUMULATIVE_WORK]


def conversation_digest(messages):
    return hashlib.sha256(json.dumps(messages, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def message_accounting(messages, findings):
    """Exact same-role/content duplicates; shared citations never merge requests."""
    canonical, result = {}, []
    effects = {(f['ordinal'] if isinstance(f, dict) else f.ordinal):
               (f.get('conversation_effect') if isinstance(f, dict) else f.conversation_effect) for f in findings}
    for message in messages:
        key = (message['role'], message['content'])
        duplicate = canonical.get(key)
        canonical.setdefault(key, message['id'])
        result.append({'message_id': message['id'], 'sequence': message['sequence'],
            'duplicate_of_message_id': duplicate,
            'counts_as_individual': duplicate is None and effects.get(message['sequence']) != 'REPEATED'})
    return result


def save_sources(payload, previous=None):
    supplied = payload.conversation_messages
    if supplied is None:
        if previous is not None:
            raise HTTPException(409, "A conversation must be edited as messages; source kind cannot change")
        return payload.requested_changes, None
    if previous is None and any(item.id is not None for item in supplied):
        raise HTTPException(422, "New message identifiers are assigned by the server")
    old_ids = {item["id"] for item in previous or []}
    if any(item.id is not None and str(item.id) not in old_ids for item in supplied):
        raise HTTPException(422, "Message identifier does not belong to this conversation")
    messages = [item.model_dump(mode="json") | {"id": str(item.id or uuid4())} for item in supplied]
    return requested_work(messages), messages


def validate_message_sources(result, messages):
    """Require current-message association and genuine cumulative, ordered sources.

    Source verification cannot prove semantic entailment or sender authenticity.
    """
    def invalid():
        raise ExtractionError("AI_INVALID_OUTPUT", "Conversation output has missing or misattributed message evidence")

    by_id = {item["id"]: item for item in messages}
    flags = {"MANUALLY_ENTERED_CONVERSATION_UNVERIFIED", "MESSAGE_ORDER_SUPPLIED_BY_FREELANCER"}
    for finding in result.findings:
        cumulative = finding.ordinal == len(messages) + 1
        if (not finding.change_types or len(set(finding.change_types)) != len(finding.change_types)
                or finding.conversation_effect is None
                or (finding.conversation_effect == "CUMULATIVE") != cumulative):
            invalid()
        if any(re.search(r"\b(?:savings?|saved|save|worth)\b", value, re.I)
               for value in [finding.explanation, *finding.uncertainties]):
            raise ExtractionError("AI_INVALID_OUTPUT", "Generated savings/value claims are not allowed")
        cited = set()
        for evidence in finding.message_evidence:
            message = by_id.get(str(evidence.message_id))
            if message is None or (not cumulative and message["sequence"] > finding.ordinal):
                invalid()
            matches = source_matches(message["content"], evidence.source_excerpt)
            if not matches:
                invalid()
            if len(matches) == 1:
                match = matches[0]
            else:
                # Repeated text needs a verified range or a longer unambiguous quote.
                match = next((item for item in matches if item.start() == evidence.source_start
                              and item.end() == evidence.source_end), None)
                if match is None:
                    invalid()
            evidence.source_start, evidence.source_end = match.start(), match.end()
            evidence.source_excerpt = message["content"][match.start():match.end()]
            cited.add(message["id"])
        if cumulative:
            if len(cited) < min(2, len(messages)):
                invalid()
            if not any(by_id[identity]["role"] == "client" for identity in cited):
                finding.uncertainties = list(dict.fromkeys([*finding.uncertainties,
                    "The cited messages are not labeled as client requests; confirm who requested the work."]))[:20]
        else:
            current = messages[finding.ordinal - 1]
            if current["id"] not in cited:
                invalid()
            if finding.conversation_effect in {"REPEATED", "CONFLICTING"} and len(cited) < 2:
                invalid()
            if any(item["content"] == current["content"] and item["role"] == current["role"]
                   for item in messages[:finding.ordinal - 1]):
                flags.add(f"MESSAGE_{finding.ordinal}:EXACT_DUPLICATE_DO_NOT_DOUBLE_COUNT")
            if current["role"] != "client":
                finding.uncertainties = list(dict.fromkeys([*finding.uncertainties,
                    "This message is not labeled as a client request; confirm who requested the work."]))[:20]
                flags.add(f"MESSAGE_{finding.ordinal}:NOT_A_CLIENT_REQUEST")
    if any(item["sent_at"] is None for item in messages):
        flags.add("SOME_MESSAGE_DATES_NOT_SUPPLIED")
    return flags
