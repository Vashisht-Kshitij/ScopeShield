"""A deliberately selected two-page brief. Full evidence stays in the complete pack."""


def brief_pages(s, info, display_length=len):
    def compact(text, limit=110):
        text = str(text).replace("\n", " ").replace("\t", " ")
        if display_length(text) <= limit: return text
        while text and display_length(text + "...") > limit: text = text[:-1]
        return text + "..."
    def amount(value):
        if value.get("not_combined"): return "Not combined across potentially overlapping agreements (see complete pack)"
        result = " / ".join(f"{v['currency']} {v['value']}" for v in value["amounts"][:3]) or "Not stated"
        return result + (f" (+{len(value['amounts']) - 3} currencies in complete pack)" if len(value["amounts"]) > 3 else "")
    def block(text, kind="text"): return {"kind": kind, "text": text}
    m, counts = info["milestones"], info["classifications"]
    first = [block("PROJECT BRIEF", "title"), block(compact(info["project_title"], 160), "project"),
             block("ScopeShield / Executive summary / " + s["generated_at"][:10] + " UTC"),
             block("PRIVATE / Contains contract and negotiation information. Share deliberately.")]
    if info["simulated"]: first.append(block("SIMULATED - NO REAL PAYMENT / Includes simulated records; they do not establish real consent or transactions.", "notice"))
    if any("OFFLINE" in r["origin"] for r in info["requests"]) or any("OFFLINE" in c["origin"] for c in info["contracts"]):
        first.append(block("OFFLINE / SIMULATED AI results; no live provider request was made by this report.", "notice"))
    origins = [r["origin"] for r in info["requests"]] + [c["origin"] for c in info["contracts"]]
    first.append(block(f"Stored analysis/extraction origins: {sum('OFFLINE' in o for o in origins)} offline; {sum(o.startswith('Recorded live') for o in origins)} historical live-provider path; {sum('OFFLINE' not in o and not o.startswith('Recorded live') for o in origins)} unknown/unrecorded. Provider metadata is not independent attestation."))
    first.append(block("01 / Agreement and milestone progress", "heading"))
    if not info["contracts"]: first.append(block("No agreement recorded. Original value is unknown."))
    for c in info["contracts"][:3]:
        first.append(block(compact(c["name"], 70) + ": " + ((c["currency"] or "Currency unknown") + " " + str(c["value"]) if c["value"] is not None else "Original value not verified")))
        source = next((q for q in c["evidence"] if q["verified"] and display_length(q["quote"]) <= 120), None)
        if source: first.append(block(source["quote"], "quote"))
    if len(info["contracts"]) > 1: first.append(block("Agreement versions are separate; no combined contract value is inferred."))
    if len(info["contracts"]) > 3: first.append(block(f"{len(info['contracts']) - 3} further agreements are listed in the complete pack."))
    first.extend([block(f"{m['approved']} of {m['total']} milestones have matching current freelancer approvals. {m['awaiting_review']} await review; {m['rejected']} rejected."),
                  block("Approved allocation: " + amount(m["approved_value"]) + f". Unknown monetary terms: {m['approved_value']['unknown_terms']}."),
                  block("Awaiting-review allocation (not agreed): " + amount(m["awaiting_value"]) + f". Unknown terms: {m['awaiting_value']['unknown_terms']}."),
                  block("Outstanding real balance: NOT DETERMINED. Acceptance and payment conditions are not assessed; sandbox states never establish real settlement."),
                  block("02 / Scope and proposed terms", "heading"),
                  block("Requests counted once: " + "; ".join(f"{k.replace('_', ' ').title()} {counts[k]}" for k in counts) + "."),
                  block("Cumulative findings and repeated messages are not extra billable work. Interpretations still require human judgment."),
                  block(f"Change orders: {info['order_counts']['approved']} freelancer-approved; {info['order_counts']['proposed']} proposed/unconfirmed; {info['order_counts']['rejected']} rejected. Freelancer approval is not client agreement.")])
    individual = {}
    for request in info["requests"]:
        for label, count in request["individual_classifications"].items(): individual[label] = individual.get(label, 0) + count
    first.insert(next(i for i, b in enumerate(first) if b["text"].startswith("Cumulative findings")), block("Individual findings (repeated/cumulative excluded): " + ("; ".join(f"{label.replace('_', ' ').title()} {count}" for label, count in sorted(individual.items())) or "None rechecked") + "."))
    for o in info["orders"][:2]:
        first.append(block(("Freelancer-approved terms" if o["freelancer_approved"] else "REJECTED terms - not agreed" if o["status"] == "REJECTED" else "PROPOSED terms - not agreed") + ": " + (f"{o['currency'] or 'Currency unknown'} {o['price']}" if o["price"] is not None else "price not stated") + ". " + o["client_decision"] + (f" / Current responses: {o['current_responses']}" if o["current_responses"] else "") + (f" / {o['stale_responses']} older-revision responses excluded" if o["stale_responses"] else "")))
    if len(info["orders"]) > 2: first.append(block(f"{len(info['orders']) - 2} more orders and all responses are in the complete pack."))
    second = [block("EVIDENCE AND NEXT STEPS", "title"), block("03 / Selected grounded scope examples", "heading")]
    selected = []
    for finding in info["findings"]:
        # Exact complete quotations only. Omit overlong examples explicitly rather
        # than cutting quotations or implying this is the full evidence record.
        contract = next((q for q in finding["evidence"] if q["kind"] == "Contract" and display_length(q["quote"]) <= 180), None)
        message = next((q for q in finding["evidence"] if q["kind"] == "Message" and display_length(q["quote"]) <= 180), None)
        if not contract: continue
        if finding["request_id"] in selected: continue
        selected.append(finding["request_id"])
        second.append(block(compact(finding["title"], 70) + " / " + finding["classification"].replace("_", " ") + " / " + ("Freelancer-reviewed" if finding["human_reviewed"] else "UNAPPROVED interpretation")))
        second.append(block("Recorded interpretation: " + compact(finding["explanation"], 200)))
        second.append(block("Contract / " + compact(contract["source"], 55) + f" / extracted text [{contract['start']}, {contract['end']})"))
        second.append(block(contract["quote"], "quote"))
        if message:
            second.append(block(compact(message["source"], 80) + f" / message-local [{message['start']}, {message['end']})"))
            second.append(block(message["quote"], "quote"))
        if len(selected) == 2: break
    second.append(block(f"Selected {len(selected)} request examples. All findings, explanations, long quotations, physical-page provenance and technical references remain in the complete pack."))
    second.append(block("04 / Recorded sandbox invoices", "heading"))
    if not info["invoice_groups"]: second.append(block("No invoice records. No payment inferred."))
    for group in info["invoice_groups"][:3]:
        second.append(block(f"{group['count']} record(s) / local {group['local_state']} / provider {group['provider_status']} / " + ("SIMULATED - NO REAL PAYMENT" if group["origin"] == "SIMULATED" else group["origin"].replace("_", " "))))
    if len(info["invoice_groups"]) > 3: second.append(block(f"{len(info['invoice_groups']) - 3} additional status groups in complete pack."))
    second.append(block("CREATED is local creation state, not payment. Provider status is stored history, never refreshed here. Even sandbox PAID is not real payment."))
    second.append(block("05 / Unresolved issues and next action", "heading"))
    second.append(block(f"{len(info['issues'])} unresolved review/negotiation items. " + compact(info["next_action"]["label"], 80) + ": " + compact(info["next_action"].get("reason", "Inspect source and recorded decisions."), 140)))
    second.append(block("Method: exact source matching and current audit checks; no semantic or legal guarantee. Manual sender/date claims are unauthenticated. This brief selects examples; read the complete private pack before deciding. Unsupported glyphs use explicit Unicode escapes; the attached private JSON retains exact text."))
    return [first, second]
