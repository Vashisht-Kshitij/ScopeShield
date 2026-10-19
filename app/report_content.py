"""Pure report projection. Shared by preview and isolated PDF worker; never calls AI."""
from types import SimpleNamespace
import hashlib
import json

from app.evidence import source_occurrence
from app.pdf_provenance import verified_provenance
from app.extraction import validate_extraction, ExtractionError
from app.scope_comparison import validate_comparison
from app.conversations import conversation_digest


def shown(value):
    if value is None: return "Not recorded"
    if isinstance(value, (list, dict)): return json.dumps(value, ensure_ascii=True, sort_keys=True)
    return str(value)


def quote_reference(text, evidence, provenance=None):
    if not isinstance(evidence, dict): return {"verified": False, "reason": "Missing evidence"}
    quote = evidence.get("source_excerpt")
    if not isinstance(quote, str) or not quote.strip(): return {"verified": False, "reason": "Quotation not supplied"}
    start, end = evidence.get("source_start"), evidence.get("source_end")
    match = source_occurrence(text, quote, start, end)
    if match is None: return {"verified": False, "reason": "Quotation/range absent, mismatched or non-unique"}
    start, end = match.start(), match.end()
    pages = [p["page"] for p in (provenance or {}).get("pages", []) if max(start, p["start"]) < min(end, p["end"])]
    return {"verified": True, "quote": text[start:end], "start": start, "end": end, "physical_pages": pages}


def origin(value):
    if not value: return "No analysis recorded"
    if value.get("live_ai") is True and value.get("mode") == "configured":
        return "Recorded live-provider path (not independently attested by this export)"
    if value.get("live_ai") is False:
        return "OFFLINE / SIMULATED analysis - not a live AI result"
    return "UNRESOLVED provider origin; no live or offline provenance confirmed"


def corrections(before, after, path=""):
    """Exact leaf differences from saved audit snapshots, without duplicating history."""
    if before == after: return
    if isinstance(before, dict) and isinstance(after, dict):
        for key in sorted(set(before) | set(after)):
            yield from corrections(before.get(key), after.get(key), f"{path}.{key}" if path else key)
    elif isinstance(before, list) and isinstance(after, list) and len(before) == len(after):
        for i, (left, right) in enumerate(zip(before, after)):
            yield from corrections(left, right, f"{path}[{i}]")
    else:
        yield path, before, after


def contract_facts(c):
    if not c.get("extraction"): return {}
    try:
        return validate_extraction(json.dumps(c["extraction"]["data"]), c["text"], provider=SimpleNamespace(name="report_recheck", live_ai=False), mode="offline_demo").data.model_dump(mode="json")
    except (ExtractionError, ValueError, TypeError, KeyError): return {}


def milestone_approved(s, m):
    fields = ("name", "description", "amount", "currency", "payment_conditions", "deadline", "deliverables", "acceptance_criteria", "status", "revision", "has_human_edits", "reviewed_at", "reviewer_label")
    current = {key: m[key] for key in fields}
    if current["reviewed_at"]: current["reviewed_at"] = current["reviewed_at"].removesuffix("+00:00")
    return m["status"] == "APPROVED" and any(e["milestone_id"] == m["id"] and e["action"] == "APPROVED" and e["revision"] == m["revision"] and e["after"] == current for e in s["reviews"])


def scope_assessment(s, c, r):
    analysis, messages = r["analysis"], r["conversation_messages"]
    if not analysis: return None, False
    bound = c["sha256"] == r["contract_sha256"] and hashlib.sha256(c["text"].encode()).hexdigest() == r["contract_text_sha256"]
    if messages: bound = bound and analysis.get("conversation_sha256") == conversation_digest(messages)
    validated = None
    try:
        if bound:
            validated = validate_comparison(json.dumps({"findings": analysis["findings"]}), c["text"], r["requested_changes"], provider=SimpleNamespace(name="report_recheck", live_ai=False), mode="offline_demo", conversation=messages)
    except (ExtractionError, ValueError, TypeError, KeyError): pass
    reviewed = r["status"] == "APPROVED" and any(e["change_request_id"] == r["id"] and e["action"] == "ANALYSIS_APPROVED" and e["after"].get("request", {}).get("analysis") == analysis and e["after"].get("request", {}).get("analysis_revision") == r["analysis_revision"] for e in s["scope_events"])
    return validated, reviewed


def order_approved(s, o):
    fields = ("id", "change_request_id", "analysis_revision", "proposed_deliverables", "proposed_price", "currency", "proposed_deadline", "supporting_evidence", "status", "revision")
    return o["status"] == "APPROVED" and any(e["action"] == "ORDER_APPROVED" and e.get("change_order_id") == o["id"] and all(e["after"].get("change_order", {}).get(key) == o[key] for key in fields) for e in s["scope_events"])


def document(s):
    """Content blocks, not HTML. Unverified records remain explicitly unresolved."""
    warnings = {"Private project data: distribute this downloaded copy deliberately.",
                "Sandbox records do not establish real payment or legal enforceability."}
    if any("SCRIPTED OFFLINE SIMULATION" in e.get("note", "") for key in ("reviews", "scope_events", "invoice_events") for e in s[key]):
        warnings.add("SYNTHETIC demonstration: scripted review events simulate human workflow; no real consent or provider invoice exists.")
    sections = []
    def section(title):
        value = {"title": title, "blocks": []}; sections.append(value); return value["blocks"]
    def line(blocks, label, value): blocks.append({"kind": "text", "text": f"{label}: {shown(value)}"})
    def quote(blocks, text, evidence, provenance=None, label="Contract source"):
        ref = quote_reference(text, evidence, provenance)
        if not ref["verified"]:
            line(blocks, "UNRESOLVED evidence", ref["reason"]); warnings.add("Some quotations or findings could not be reverified; review unresolved evidence.")
            return False
        pages = ("; message-local source (no PDF page provenance)" if label.startswith("Message ") else
                 f"; physical original PDF page(s) {', '.join(map(str, ref['physical_pages']))}" if ref["physical_pages"] else "; original PDF page unverified")
        line(blocks, label, f"Unicode code-point range [{ref['start']}, {ref['end']}){pages}")
        blocks.append({"kind": "quote", "text": ref["quote"]})
        return True
    p = s["project"]
    b = section("01 / Project snapshot")
    for key in ("title", "id", "status", "created_at"): line(b, "Project " + key, p[key])
    line(b, "Generation time (UTC)", s["generated_at"])
    line(b, "Report format", s["format"])
    line(b, "Source revision SHA-256", s["source_revision"])
    line(b, "Project metadata monetary terms (not independently contract-verified)", f"{shown(p['currency'])} {shown(p['total_value'])}")
    b.append({"kind": "text", "text": "This is a read-only negotiation record. Exporting approves nothing, sends nothing and does not refresh provider status. Missing approvals and unknown amounts remain unknown."})
    documents = {d["contract_id"]: d["provenance"] for d in s["documents"]}
    for c in s["contracts"]:
        b = section("02 / Agreement - " + c["filename"])
        for key in ("id", "sha256", "media_type", "size_bytes", "page_count", "status", "review_revision", "error_code"): line(b, key, c[key])
        extraction = c["extraction"]
        line(b, "Extraction origin", origin(extraction))
        if extraction and not extraction.get("live_ai"): warnings.add("Includes offline or simulated AI results; these demonstrate workflow only.")
        provenance = verified_provenance(c["text"], c["page_count"], documents.get(c["id"]), document_sha256=c["sha256"]) if c["media_type"] == "application/pdf" else None
        if c["media_type"] == "application/pdf" and provenance is None:
            warnings.add("Original PDF page provenance is unavailable for one or more agreements; only extracted-text ranges are cited.")
        line(b, "Original PDF provenance", "Validated physical-page spans in stored extraction; not visual coordinates" if provenance else "Unavailable or invalid; only extracted-text ranges are cited")
        data = contract_facts(c)
        if extraction and not data:
            warnings.add("Contract extraction could not be revalidated; its terms remain unresolved.")
        for key in ("project_title", "currency", "total_value"):
            fact = data.get(key, {})
            line(b, "Rechecked source candidate " + key, fact.get("value"))
            if fact.get("value") is not None: quote(b, c["text"], fact, provenance)
        for m in sorted((m for m in s["milestones"] if m["contract_id"] == c["id"]), key=lambda m: m["ordinal"]):
            b = section("03 / Payment milestone - " + (m["name"] or "Unnamed"))
            for key in ("id", "ordinal", "status", "revision", "has_human_edits", "reviewer_label", "reviewed_at", "amount", "currency", "payment_conditions", "deadline", "deliverables", "acceptance_criteria", "description"): line(b, "Saved " + key, m[key])
            confirmed = milestone_approved(s, m)
            if not confirmed: warnings.add("Includes milestone candidates without a confirmed current approval.")
            line(b, "Human approval record", "Current revision matches the recorded freelancer approval; not client consent" if confirmed else "No matching current approval confirmed by this export")
            # Reuse extraction's milestone association and monetary verification.
            grounded = {}
            if extraction:
                try:
                    raw = {**extraction["data"], "milestones": [m["evidence"]]}
                    checked = validate_extraction(json.dumps(raw), c["text"], provider=SimpleNamespace(name="report_recheck", live_ai=False), mode="offline_demo")
                    grounded = checked.data.milestones[0].model_dump(mode="json")
                except (ExtractionError, ValueError, TypeError, KeyError, IndexError): pass
            for key in ("name", "amount", "currency", "payment_conditions", "deadline", "deliverables", "acceptance_criteria"):
                facts = grounded.get(key)
                facts = facts if isinstance(facts, list) else [facts]
                for fact in facts:
                    if not fact or fact.get("value") is None:
                        line(b, "UNRESOLVED candidate " + key, "No associated source fact verified; saved human terms above are not source quotations")
                        warnings.add("Some milestone fields lack reverified source evidence; inspect saved human terms separately.")
                    else:
                        line(b, "Original candidate " + key, fact["value"])
                        quote(b, c["text"], fact, provenance)
        for r in (r for r in s["requests"] if r["contract_id"] == c["id"]):
            b = section("04 / Conversation and scope - " + r["title"])
            for key in ("id", "status", "revision", "analysis_revision", "reviewer_label", "reviewed_at", "error_code"): line(b, "Request " + key, r[key])
            messages = r["conversation_messages"]
            if messages:
                b.append({"kind": "text", "text": "Manually entered sources. Sender, role and supplied date are unauthenticated claims; explicit sequence controls order. Repeated text is retained and must not be double counted."})
                for message in sorted(messages, key=lambda item: item["sequence"]):
                    line(b, "Message", f"{message['sequence']} / {message['id']} / {message['sender']} / role {message['role']} / supplied date {shown(message.get('sent_at'))}")
                    b.append({"kind": "quote", "text": message["content"]})
            else:
                for i, work in enumerate(r["requested_changes"], 1): line(b, f"Legacy requested work {i} (manual source)", work)
            analysis = r["analysis"]
            if not analysis:
                warnings.add("Some requests have no completed analysis; no classification is inferred.")
                line(b, "Analysis", "Not recorded; no classification inferred"); continue
            line(b, "Analysis origin", origin(analysis))
            for key in ("provider", "model", "mode", "edited_by_human", "review_flags"): line(b, "Recorded " + key, analysis.get(key))
            if not analysis.get("live_ai"): warnings.add("Includes offline or simulated AI results; these demonstrate workflow only.")
            validated, reviewed = scope_assessment(s, c, r)
            if not reviewed: warnings.add("Includes unapproved, rejected or pending scope interpretations; no agreement is asserted.")
            if any(f.get("classification") == "AMBIGUOUS" or f.get("uncertainties") for f in analysis.get("findings", [])):
                warnings.add("Some scope findings remain ambiguous or have unanswered questions.")
            line(b, "Review disposition", "Human-reviewed conclusion (freelancer approval recorded); not client agreement" if reviewed else "UNAPPROVED / rejected / pending conclusion: not an agreed obligation")
            if validated is None:
                warnings.add("Some scope analysis could not be revalidated; recorded conclusions remain unresolved.")
            for i, f in enumerate(analysis.get("findings", [])):
                good = validated and i < len(validated.findings) and validated.findings[i].classification == f.get("classification")
                line(b, "Cumulative finding" if f.get("conversation_effect") == "CUMULATIVE" else "Individual finding", f"{f.get('ordinal')} / {shown(f.get('conversation_effect'))} / recorded {f.get('classification')} / {'grounding rechecked' if good else 'UNRESOLVED grounding'}")
                for key in ("requested_work", "change_types", "explanation", "uncertainties", "recommended_action"): line(b, "Recorded interpretation " + key, f.get(key))
                for evidence in f.get("contract_evidence", []): quote(b, c["text"], evidence, provenance)
                by_id = {m["id"]: m for m in messages or []}
                for evidence in f.get("message_evidence", []):
                    source = by_id.get(evidence.get("message_id"))
                    quote(b, source["content"] if source else "", evidence, label="Message " + shown(evidence.get("message_id")))
            if r["original_ai_output"] is not None:
                line(b, "Preserved original AI output SHA-256 (current result may be human-edited)", hashlib.sha256(json.dumps(r["original_ai_output"], sort_keys=True).encode()).hexdigest())
    b = section("05 / Change-order proposals and decisions")
    if not s["orders"]: line(b, "Orders", "None recorded; no terms inferred")
    for o in s["orders"]:
        approved = order_approved(s, o)
        if o["status"] != "APPROVED" or not approved: warnings.add("Includes proposed or unconfirmed change-order terms.")
        line(b, "Order disposition", "Freelancer APPROVED terms; client response separately recorded below" if o["status"] == "APPROVED" and approved else "PROPOSED / " + o["status"] + " - current approval not confirmed by this export")
        for key in ("id", "change_request_id", "analysis_revision", "revision", "proposed_deliverables", "proposed_price", "currency", "proposed_deadline", "reviewer_label", "reviewed_at"): line(b, key, o[key])
        for decision in (d for d in s["client_decisions"] if d["order_id"] == o["id"]):
            line(b, "Recorded client account response (revision-specific)", f"{decision['decision']} / order revision {decision['order_revision']} / {decision['created_at']} / actor {decision['client_id']} / {decision['note']}")
        if not any(d["order_id"] == o["id"] for d in s["client_decisions"]): line(b, "Client consent", "No client response recorded")
    b = section("06 / Sandbox invoice history")
    if not s["invoices"]: line(b, "Invoices", "None recorded; illustrative dashboard history is excluded")
    for invoice in s["invoices"]:
        mode = invoice["snapshot"].get("preview", {}).get("provider_mode")
        simulated = mode == "mocked_paypal_sandbox"
        sandbox = mode == "paypal_sandbox"
        label = "SIMULATED - NO REAL PAYMENT / OFFLINE / SIMULATED provider state" if simulated else "Stored PayPal SANDBOX record" if sandbox else "UNRESOLVED provider provenance"
        if simulated: b.append({"kind": "notice", "text": "SIMULATED - NO REAL PAYMENT"})
        line(b, "Origin", label)
        if simulated: warnings.add("Includes simulated invoice states; these are not PayPal-verified events.")
        if not sandbox and not simulated: warnings.add("Some invoice provider provenance is unknown; no provider verification is claimed.")
        line(b, "Local creation state (not payment)", invoice["state"])
        line(b, "Recorded provider status (separate from local state)", invoice["paypal_status"])
        for key in ("id", "milestone_id", "milestone_revision", "contract_review_revision", "invoice_number", "invoice_id", "state", "amount", "currency", "paypal_status", "error_code", "revision", "last_checked_at"): line(b, "Saved " + key, invoice[key])
        milestone = next((m for m in s["milestones"] if m["id"] == invoice["milestone_id"]), None)
        if not milestone or milestone["revision"] != invoice["milestone_revision"] or milestone["status"] != "APPROVED":
            line(b, "UNRESOLVED approval association", "Invoice reference does not establish a matching current milestone approval")
            warnings.add("Some invoice references do not match a currently approved milestone revision.")
        matching_check = any(e["link_id"] == invoice["id"] and e["action"] == "STATUS_SYNCED" and e["data"].get("invoice_id") == invoice["invoice_id"] and e["data"].get("paypal_status") == invoice["paypal_status"] for e in s["invoice_events"])
        line(b, "Provider verification", "Recorded sandbox verification as of last_checked_at, corroborated by saved status event; no fresh provider request" if sandbox and matching_check and invoice["verified"] and invoice["last_checked_at"] else "Not independently provider-verified by this report")
        line(b, "Payment meaning", "DRAFT is unsent; send acceptance is not payment. SENT/UNPAID is not paid. PAID, if recorded, is a sandbox status only, never real-money settlement.")
        sends = [e for e in s["invoice_events"] if e["link_id"] == invoice["id"] and e["action"].startswith("SEND_")]
        for event in sends: line(b, "Recorded send event", f"{event['action']} / {event['created_at']}")
    b = section("07 / Chronological audit and human corrections")
    people = {a["id"]: a for a in s["actors"]}
    events = [(kind, e) for kind, key in (("contract", "contract_events"), ("milestone", "reviews"), ("scope", "scope_events"), ("invoice", "invoice_events"), ("client response", "client_decisions"), ("project security", "security_events")) for e in s.get(key, [])]
    events.sort(key=lambda pair: (pair[1]["created_at"], pair[1].get("revision", pair[1].get("data", {}).get("event_sequence", 0)), pair[1]["id"]))
    if not events: line(b, "Audit", "No events recorded; no history reconstructed")
    for kind, e in events:
        actor_id = e.get("actor_id") or e.get("client_id")
        person = people.get(actor_id)
        line(b, f"{e['created_at']} / {kind}", e.get("action") or e.get("decision"))
        line(b, "Event / actor", f"{e['id']} / {actor_id or 'Actor not recorded'} / current display name {person['display_name'] if person else 'Unknown actor'} / current account role {person['role'] if person else 'unknown'}")
        references = {key: e[key] for key in ("contract_id", "milestone_id", "change_request_id", "change_order_id", "order_id", "link_id", "resource_id") if e.get(key)}
        if references: line(b, "Related records", references)
        for key in ("reviewer_label", "note", "revision", "acknowledged_issues"):
            if key in e: line(b, key + " (recorded)", e[key])
        if e.get("action", "").endswith("EDITED"):
            # Exact saved before/after changes; not a reconstructed source or consent.
            before, after = e.get("before", {}), e.get("after", {})
            for key, left, right in corrections(before, after):
                line(b, "Correction before " + key, left); line(b, "Correction after " + key, right)
    b = section("08 / Methodology and limitations")
    for text in [
        "This report projects persisted records in one SQLite read snapshot. Source revision hashes the included records and their audit history, excluding generation time. It is a reproducibility reference, not a digital signature or tamper-proof attestation.",
        "Source quotations use exact stored text and Unicode code-point offsets [start, end). Legacy quotes are located only if exact and unique. Existing lexical milestone association and scope grounding validators run again; they do not prove semantic entailment or legal interpretation.",
        "Physical original PDF pages appear only with valid hash-bound physical-page spans. They describe extraction provenance, not visual coordinates, printed page labels, OCR accuracy or the author's reading order. Missing mappings remain unverified. Message ranges refer only to their identified message, never original PDF pages.",
        "Unsupported font glyphs, bidirectional/complex-script characters and control characters are displayed as explicit [U+XXXX] escapes rather than missing glyph boxes or misleading text. Literal backslashes are escaped. Line breaks and wrapping are presentation only. The attached UTF-8 project-snapshot.json retains exact source text and quotations; treat it as private data, not executable content.",
        "Human edits and approvals describe recorded application actions by account actors. Manual conversations and sender/date claims are not authenticated imports. Freelancer approval does not establish client agreement. Repeated/conflicting requests and ambiguous obligations require negotiation.",
        "AI outputs remain interpretations. Offline fixtures simulate workflow, while a recorded configured/live flag describes the stored provider path and is not external attestation. No AI or payment service is contacted during export. Unknown prices stay unknown; no savings or new monetary estimates are calculated.",
        "Invoice references are milestone-linked sandbox records, separate from change orders. Provider status is historical as of the saved check time. Sandbox PAID or a simulated payment state never proves real payment. No invoices or approval records are created by export.",
        "Limits: ten agreements, 100 milestones, 30 scope requests/orders, 500 events per audit type, 2 MiB captured data, 160 PDF pages, 8 MiB output, one PDF job shared with ingestion, and 25 seconds wall time. Oversized reports fail explicitly rather than silently omitting records. Downloaded copies are not automatically revoked when sharing changes.",
    ]: b.append({"kind": "text", "text": text})
    return {"sections": sections, "warnings": sorted(warnings)}
