"""Read-only project metrics from the report snapshot, without provider requests.

Money is Decimal and partitioned by currency. Neither invoices nor change orders
are added to milestone allocations. Payment status cannot establish a real balance.
"""
from collections import Counter
from decimal import Decimal
import json
from types import SimpleNamespace
from pydantic import TypeAdapter, ValidationError

from app.contract_schemas import MoneyValue
from app.review_schemas import SUPPORTED_CURRENCIES
from app.conversations import message_accounting

from app.report_content import (contract_facts, milestone_approved, order_approved,
                                origin, quote_reference, scope_assessment)

MONEY = TypeAdapter(MoneyValue)


def milestone_record(s, m, c):
    """Saved terms and separately rechecked original candidates; never conflate them."""
    from app.extraction import ExtractionError, validate_extraction
    evidence = []
    try:
        raw = {**c["extraction"]["data"], "milestones": [m["evidence"]]}
        checked = validate_extraction(json.dumps(raw), c["text"], provider=SimpleNamespace(name="report_recheck", live_ai=False), mode="offline_demo")
        candidate = checked.data.milestones[0].model_dump(mode="json")
        for key in ("name", "amount", "currency", "payment_conditions", "deadline", "deliverables", "acceptance_criteria"):
            facts = candidate.get(key)
            for fact in facts if isinstance(facts, list) else [facts]:
                if fact and fact.get("value") is not None:
                    ref = quote_reference(c["text"], fact)
                    if ref["verified"]:
                        evidence.append({**ref, "source": c["filename"], "kind": "Original contract candidate", "field": key})
    except (ExtractionError, ValueError, TypeError, KeyError, IndexError):
        pass
    return {"id": m["id"], "contract_id": m["contract_id"], "ordinal": m["ordinal"], "name": m["name"],
            "status": m["status"], "human_reviewed": milestone_approved(s, m), "revision": m["revision"],
            "amount": m["amount"], "currency": m["currency"], "deadline": m["deadline"],
            "has_human_edits": m["has_human_edits"], "origin": origin(c["extraction"]), "evidence": evidence}


def unique(rows):
    return list({row["id"]: row for row in rows}.values())


def money_totals(rows, field="amount"):
    totals, unknown = {}, 0
    for row in unique(rows):
        try:
            amount, currency = MONEY.validate_python(row.get(field)), row.get("currency")
            if not isinstance(currency, str) or currency not in SUPPORTED_CURRENCIES:
                raise ValueError
            totals[currency] = totals.get(currency, Decimal(0)) + amount
        except (ValidationError, ValueError, TypeError): unknown += 1
    return {"amounts": [{"currency": c, "value": format(v, "f")} for c, v in sorted(totals.items())], "unknown_terms": unknown}


def invoice_record(s, invoice):
    mode = invoice["snapshot"].get("preview", {}).get("provider_mode")
    kind = {"mocked_paypal_sandbox": "SIMULATED", "paypal_sandbox": "HISTORICAL_SANDBOX"}.get(mode, "UNRESOLVED")
    checked = kind == "HISTORICAL_SANDBOX" and invoice["verified"] and bool(invoice["last_checked_at"]) and any(
        e["link_id"] == invoice["id"] and e["action"] == "STATUS_SYNCED" and e["data"].get("invoice_id") == invoice["invoice_id"] and e["data"].get("paypal_status") == invoice["paypal_status"] for e in s["invoice_events"])
    return {"id": invoice["id"], "contract_id": invoice["contract_id"], "milestone_id": invoice["milestone_id"],
            "origin": kind, "local_state": invoice["state"], "provider_status": invoice["paypal_status"] or "NOT_RECORDED",
            "historical_check_corroborated": bool(checked), "last_checked_at": invoice["last_checked_at"],
            "amount": invoice["amount"], "currency": invoice["currency"],
            "notice": "SIMULATED - NO REAL PAYMENT" if kind == "SIMULATED" else "Historical sandbox status; no real payment" if kind == "HISTORICAL_SANDBOX" else "Provider origin unresolved; no payment verified"}


def intelligence(s):
    contracts = {c["id"]: c for c in unique(s["contracts"])}
    terms = []
    for c in contracts.values():
        facts = contract_facts(c)
        value, currency = facts.get("total_value", {}).get("value"), facts.get("currency", {}).get("value")
        # Never substitute project metadata or sum potentially overlapping versions.
        terms.append({"contract_id": c["id"], "name": c["filename"], "value": value, "currency": currency,
                      "origin": origin(c["extraction"]), "evidence": [quote_reference(c["text"], facts[k]) for k in ("total_value", "currency") if facts.get(k, {}).get("value") is not None]})
    milestones = unique(s["milestones"])
    approved = [m for m in milestones if milestone_approved(s, m)]
    pending = [m for m in milestones if m["status"] in {"DRAFT", "REVIEW_REQUIRED"} or (m["status"] == "APPROVED" and not milestone_approved(s, m))]
    separate = len({m["contract_id"] for m in milestones}) > 1
    approved_value, awaiting_value = money_totals(approved), money_totals(pending)
    for total in (approved_value, awaiting_value):
        total["not_combined"] = separate
        if separate: total["amounts"] = []
    milestone_groups = [{"contract_id": cid, "name": contracts[cid]["filename"],
                         "approved_value": money_totals([m for m in approved if m["contract_id"] == cid]),
                         "awaiting_value": money_totals([m for m in pending if m["contract_id"] == cid])} for cid in contracts]
    requests, findings, issues = [], [], []
    for r in unique(s["requests"]):
        c = contracts[r["contract_id"]]
        validated, reviewed = scope_assessment(s, c, r)
        recorded = (r["analysis"] or {}).get("findings", [])
        good = bool(validated and len(validated.findings) == len(recorded) and all(a.classification == b.get("classification") for a, b in zip(validated.findings, recorded)))
        labels = {f["classification"] for f in recorded}
        classification = ("UNRESOLVED" if not good else "AMBIGUOUS" if "AMBIGUOUS" in labels else "OUT_OF_SCOPE" if "OUT_OF_SCOPE" in labels else "IN_SCOPE")
        countable = ({m['sequence'] for m in message_accounting(r['conversation_messages'], validated.findings if validated else [])
                      if m['counts_as_individual']} if r['conversation_messages'] is not None else None)
        item = {"id": r["id"], "contract_id": c["id"], "title": r["title"], "status": r["status"], "classification": classification,
                "human_reviewed": reviewed, "origin": origin(r["analysis"]), "finding_count": len(recorded),
                "individual_classifications": dict(Counter(f["classification"] if good else "UNRESOLVED" for f in recorded
                    if f.get("conversation_effect") not in {"CUMULATIVE", "REPEATED"}
                    and (countable is None or f['ordinal'] in countable))),
                "cumulative_classification": next((f["classification"] for f in recorded if f.get("conversation_effect") == "CUMULATIVE"), None) if good else None}
        requests.append(item)
        active = r["status"] not in {"CANCELLED", "REJECTED"}
        if active and (classification in {"UNRESOLVED", "AMBIGUOUS"} or not reviewed or any(f.get("uncertainties") for f in recorded)):
            issues.append({"contract_id": c["id"], "tab": "scope", "title": r["title"], "reason": "Clarify ambiguous or unresolved evidence" if classification in {"UNRESOLVED", "AMBIGUOUS"} else "Review interpretation and remaining questions"})
        if not good or not active: continue
        by_id = {m["id"]: m for m in r["conversation_messages"] or []}
        # Individual findings precede cumulative ones. A summary selects examples;
        # it never sums messages/cumulative conclusions as separate billable work.
        for f in validated.findings:
            value = f.model_dump(mode="json")
            if value["classification"] == "IN_SCOPE" or value.get("conversation_effect") == "REPEATED": continue
            evidence = []
            for e in value["contract_evidence"]:
                ref = quote_reference(c["text"], e)
                if ref["verified"]: evidence.append({**ref, "source": c["filename"], "kind": "Contract"})
            for e in value.get("message_evidence", []):
                source = by_id.get(e["message_id"])
                ref = quote_reference(source["content"] if source else "", e)
                if ref["verified"]: evidence.append({**ref, "source": f"Message {source['sequence']} / {source['sender']}", "kind": "Message"})
            findings.append({"request_id": r["id"], "contract_id": c["id"], "title": r["title"], "ordinal": value["ordinal"],
                             "classification": value["classification"], "work": value["requested_work"], "explanation": value["explanation"],
                             "human_reviewed": reviewed, "origin": item["origin"], "evidence": evidence})
    orders = []
    for o in unique(s["orders"]):
        confirmed = order_approved(s, o)
        current = [d for d in s["client_decisions"] if d["order_id"] == o["id"] and d["order_revision"] == o["revision"]]
        responses = dict(Counter(d["decision"] for d in current))
        stale = sum(d["order_id"] == o["id"] and d["order_revision"] != o["revision"] for d in s["client_decisions"])
        contract_id = next(r["contract_id"] for r in s["requests"] if r["id"] == o["change_request_id"])
        orders.append({"id": o["id"], "contract_id": contract_id, "status": o["status"], "freelancer_approved": confirmed,
                       "price": o["proposed_price"], "currency": o["currency"], "current_responses": responses, "stale_responses": stale,
                       "client_decision": "Not requested: freelancer approval pending" if not confirmed else "No current response recorded" if not current else "Revision-specific account response recorded; not a signed agreement"})
        if o["status"] != "REJECTED" and (not confirmed or not current or any(d["decision"] != "APPROVED" for d in current)):
            issues.append({"contract_id": contract_id, "tab": "orders", "title": "Change-order terms / client response", "reason": "Freelancer approval pending; price remains proposed" if not confirmed else "Resolve current client decisions; account responses are not legal consent"})
    invoices = [invoice_record(s, i) for i in unique(s["invoices"])]
    groups = Counter((i["origin"], i["local_state"], i["provider_status"]) for i in invoices)
    significant = []
    for kind, key in (("Contract", "contract_events"), ("Milestone", "reviews"), ("Scope", "scope_events"), ("Invoice", "invoice_events"), ("Client response", "client_decisions")):
        for e in s[key]:
            action = e.get("action") or e.get("decision")
            if action == "EXTRACTION_STARTED": continue
            significant.append({"id": e["id"], "kind": kind, "action": action, "created_at": e["created_at"],
                                "simulation": "SCRIPTED OFFLINE SIMULATION" in e.get("note", "") or action == "SIMULATED_HISTORY_IMPORTED"})
    significant.sort(key=lambda e: (e["created_at"], e["id"]), reverse=True)
    first = next(iter(contracts), None)
    if pending:
        next_action = {"label": "Review milestone candidates", "contract_id": pending[0]["contract_id"], "tab": "milestones", "reason": f"{len(pending)} milestones still need a confirmed current review."}
    elif issues:
        next_action = {"label": "Resolve negotiation issues", **issues[0]}
    elif any(not c["extraction"] for c in contracts.values()):
        next_action = {"label": "Review agreement extraction", "contract_id": next(c["id"] for c in contracts.values() if not c["extraction"]), "tab": "milestones", "reason": "Original terms remain unverified."}
    else:
        next_action = {"label": "Review evidence before sharing", "contract_id": first, "tab": "evidence", "reason": "Inspect the private report; downloading approves nothing."}
    return {"project_title": s["project"]["title"], "contracts": terms,
            "milestones": {"total": len(milestones), "approved": len(approved), "awaiting_review": len(pending), "rejected": sum(m["status"] == "REJECTED" for m in milestones),
                           "approved_value": approved_value, "awaiting_value": awaiting_value, "per_agreement": milestone_groups},
            "outstanding": {"value": None, "reason": "Real outstanding balance is not determined. Acceptance/payment conditions are not assessed; sandbox and simulated statuses cannot establish real settlement."},
            "requests": requests, "classifications": {key: sum(r["classification"] == key for r in requests) for key in ("IN_SCOPE", "OUT_OF_SCOPE", "AMBIGUOUS", "UNRESOLVED")},
            "findings": findings, "orders": orders,
            "milestone_rows": [milestone_record(s, m, contracts[m["contract_id"]]) for m in milestones],
            "order_counts": {"approved": sum(o["freelancer_approved"] for o in orders), "proposed": sum(not o["freelancer_approved"] and o["status"] != "REJECTED" for o in orders), "rejected": sum(o["status"] == "REJECTED" for o in orders)},
            "invoices": invoices, "invoice_groups": [{"origin": k[0], "local_state": k[1], "provider_status": k[2], "count": v} for k, v in sorted(groups.items())],
            "issues": issues, "recent_events": significant[:8], "event_count": len(significant), "next_action": next_action,
            "simulated": any(i["origin"] == "SIMULATED" for i in invoices) or any(e["simulation"] for e in significant),
            "methodology": "One snapshot. One count per persisted request/order/invoice/milestone. Potentially overlapping agreement versions and currencies are never financially combined; per-agreement allocations remain separate. Cumulative findings and repeated messages are not additional billable work. Orders and invoices are never added to milestones. No provider is contacted."}
