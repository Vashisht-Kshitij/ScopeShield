"""Independent audit exploits and their remediations; providers are offline only."""
import copy
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import json
from threading import Event

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.day4_fixture import PATH as DAY4_PATH, fixture_output
from app.extraction import ExtractionError, get_extraction_provider, validate_extraction
from app.gemini import DEFAULT_MODEL, GeminiModelRouter
from app.main import create_app, get_paypal
from app.storage import Contract, ContractEvent, InvoiceEvent, InvoiceLink, ManualInvoiceOwner, utc_now
from contract_fixtures import MockExtractionProvider, sample_output, sample_text
from test_contracts import api as contract_api, upload
from test_gemini import ProviderResponse, envelope, http
from test_milestone_invoices import api as invoice_api, approve as approve_milestone, creation
from test_scope_changes import api as scope_api, analyze, body, configured, create, prepare


def validated(data, text=None):
    provider = MockExtractionProvider(data)
    return validate_extraction(provider.output, text or sample_text(), provider=provider, mode="configured")


def router():
    return GeminiModelRouter("dummy_day12_key", models=(DEFAULT_MODEL,), free_tier_models=(DEFAULT_MODEL,),
                             allow_live=True, max_model_calls=1)


def test_astra_legacy_exploit_is_disabled_before_provider_initialization(contract_api):
    app, client = contract_api
    def forbidden():
        pytest.fail("Disabled legacy mutation initialized PayPal/OAuth")
    app.dependency_overrides[get_paypal] = forbidden
    payload = {"milestone": "Unapproved arbitrary work", "amount_usd": "1.00"}
    for _ in range(2):
        result = client.post("/api/invoices", json=payload)
        assert result.status_code == 410
        assert result.json()["detail"]["code"] == "LEGACY_INVOICE_MUTATION_DISABLED"
    with Session(app.state.engine) as session:
        assert all(session.scalar(select(func.count()).select_from(model)) == 0
                   for model in (Contract, InvoiceLink, InvoiceEvent, ManualInvoiceOwner))


def test_legacy_send_cannot_bypass_guarded_flow_or_initialize_provider(invoice_api):
    app, client, cid, paypal = invoice_api
    row = approve_milestone(client, cid)
    link = client.post(f"/api/milestones/{row['id']}/invoice", json=creation(client, row)).json()
    def forbidden():
        pytest.fail("Legacy sending initialized provider")
    app.dependency_overrides[get_paypal] = forbidden
    assert client.post(f"/api/invoices/{link['invoice_id']}/send", json={"confirmed": True}).status_code == 410
    assert len(paypal.creates) == 1
    assert not any(event["action"].startswith("SEND") for event in client.get(f"/api/milestones/{row['id']}/invoice/history").json())


def test_astra_swapped_amounts_with_matching_total_are_removed_and_rejected(http):
    text = DAY4_PATH.read_text(encoding="utf-8")
    data = json.loads(fixture_output())
    a, b = data["milestones"][:2]
    a["amount"], b["amount"] = b["amount"], a["amount"]
    result = validated(data, text)
    assert [row.amount.value for row in result.data.milestones[:2]] == [None, None]
    assert result.totals_match is None
    assert {(issue.field, issue.code) for issue in result.review_issues} >= {
        ("milestones[0].amount", "MILESTONE_EVIDENCE_MISMATCH"),
        ("milestones[1].amount", "MILESTONE_EVIDENCE_MISMATCH"),
    }
    queue, calls = http
    queue.append(ProviderResponse(body=envelope(data)))
    with pytest.raises(ExtractionError, match="source evidence") as caught:
        router().extract(text)
    assert caught.value.code == "AI_UNGROUNDED_OUTPUT" and len(calls) == 1


@pytest.mark.parametrize("field", ["payment_conditions", "deadline", "deliverables", "acceptance_criteria"])
def test_cross_milestone_obligations_and_dates_are_not_borrowed(field):
    data = sample_output()
    data["milestones"][0][field] = copy.deepcopy(data["milestones"][1][field])
    result = validated(data)
    fact = getattr(result.data.milestones[0], field)
    assert all(item.value is None for item in fact) if isinstance(fact, list) else fact.value is None
    assert any(issue.code == "MILESTONE_EVIDENCE_MISMATCH" for issue in result.review_issues)


def test_identical_amounts_bind_to_each_own_section_even_when_output_is_reordered():
    data = sample_output()
    data["milestones"].reverse()
    result = validated(data)
    assert result.totals_match is True
    assert len({row.amount.source_start for row in result.data.milestones}) == 3
    assert [row.name.value for row in result.data.milestones] == [row["name"]["value"] for row in data["milestones"]]
    for row in result.data.milestones:
        assert row.name.source_start < row.amount.source_start
        assert sample_text()[row.amount.source_start:row.amount.source_end] == row.amount.source_excerpt


def test_source_milestone_order_does_not_determine_the_model_row_association():
    paragraphs = sample_text().split("\n\n")
    clauses = [part for part in paragraphs if part.startswith("Milestone ")]
    text = "\n\n".join([part for part in paragraphs if not part.startswith("Milestone ")] + clauses[::-1])
    result = validated(sample_output(), text)
    assert result.totals_match is True
    assert result.data.milestones[0].name.source_start > result.data.milestones[2].name.source_start
    for row in result.data.milestones:
        assert text[row.amount.source_start:row.amount.source_end] == row.amount.source_excerpt


def test_explicit_shared_payment_clause_and_global_currency_are_allowed():
    data = sample_output()
    text = "\n".join(line for line in sample_text().splitlines() if not line.startswith("Payment condition:"))
    shared = "All milestones: Payment condition: Due after written acceptance."
    text += "\n\n" + shared
    for row in data["milestones"]:
        row["payment_conditions"] = {"value": "Due after written acceptance", "source_excerpt": shared}
    result = validated(data, text)
    assert result.totals_match and all(row.payment_conditions.value is not None for row in result.data.milestones)
    assert len({row.currency.source_start for row in result.data.milestones}) == 1
    assert not any(issue.code.endswith(("MISMATCH", "UNCERTAIN")) for issue in result.review_issues)


def test_unidentifiable_heading_requires_manual_correction_without_invented_association():
    text = sample_text().replace("Milestone 1:", "Phase 1:").replace("Milestone 2:", "Phase 2:").replace("Milestone 3:", "Phase 3:")
    data = sample_output()
    for i, row in enumerate(data["milestones"], 1):
        row["name"]["source_excerpt"] = row["name"]["source_excerpt"].replace(f"Milestone {i}:", f"Phase {i}:")
    result = validated(data, text)
    assert all(row.name.value is None and row.amount.value is None for row in result.data.milestones)
    assert any(issue.code == "MILESTONE_ASSOCIATION_UNCERTAIN" for issue in result.review_issues)
    assert result.requires_human_review


def test_heading_number_is_not_a_fee_even_inside_the_correct_section():
    data = sample_output()
    data["milestones"][0]["amount"] = {"value": "1", "source_excerpt": data["milestones"][0]["name"]["source_excerpt"]}
    assert validated(data).data.milestones[0].amount.value is None


def test_named_alpha_beta_swap_is_not_saved_as_a_matching_payment_schedule():
    data = sample_output()
    data["milestones"] = data["milestones"][:2]
    text = sample_text().split("\n\nMilestone 3:")[0].replace("1,500.00", "300.00")
    data["total_value"] = {"value": "300", "source_excerpt": "Total project value: USD 300.00."}
    for row, name, amount in zip(data["milestones"], ["Alpha", "Beta"], ["100.00", "200.00"]):
        heading = f"Milestone: {name}."
        text = text.replace(row["name"]["source_excerpt"], heading)
        text = text.replace("Amount: USD 500.00.", f"Amount: USD {amount}.", 1)
        row["name"] = {"value": name, "source_excerpt": heading}
        row["amount"] = {"value": amount, "source_excerpt": f"Amount: USD {amount}."}
    assert validated(data, text).totals_match is True
    data["milestones"][0]["amount"], data["milestones"][1]["amount"] = data["milestones"][1]["amount"], data["milestones"][0]["amount"]
    result = validated(data, text)
    assert all(row.amount.value is None for row in result.data.milestones)
    assert result.totals_match is None


def test_duplicate_heading_names_are_uncertain_instead_of_assigned_by_output_order():
    data = sample_output()
    text = sample_text()
    for row in data["milestones"]:
        text = text.replace(row["name"]["source_excerpt"], "Milestone: Delivery.")
        row["name"] = {"value": "Delivery", "source_excerpt": "Milestone: Delivery."}
    result = validated(data, text)
    assert all(row.name.value is None and row.amount.value is None for row in result.data.milestones)
    assert any(issue.code == "MILESTONE_ASSOCIATION_UNCERTAIN" for issue in result.review_issues)


def test_currency_in_another_milestone_is_not_a_project_wide_declaration():
    data = sample_output()
    text = sample_text().replace("Amount: USD 500.00.", "Amount: EUR 500.00.", 1)
    data["milestones"][1]["currency"] = {"value": "EUR", "source_excerpt": "Amount: EUR 500.00."}
    result = validated(data, text)
    assert result.data.milestones[1].currency.value is None
    assert any(issue.field == "milestones[1].currency" and issue.code == "MILESTONE_EVIDENCE_MISMATCH" for issue in result.review_issues)


def test_project_currency_cannot_override_an_explicit_milestone_currency():
    text = sample_text().replace("Amount: USD 500.00.", "Amount: USD 500.00.\nCurrency: EUR.", 1)
    result = validated(sample_output(), text)
    assert result.data.milestones[0].currency.value is None
    assert any(issue.field == "milestones[0].currency" and issue.code == "MILESTONE_EVIDENCE_MISMATCH" for issue in result.review_issues)


def test_mentioning_another_milestone_as_a_prerequisite_does_not_reassign_the_obligation():
    data = sample_output()
    quote = "Payment condition: Due after Milestone 1 is accepted."
    text = sample_text().replace(data["milestones"][1]["payment_conditions"]["source_excerpt"], quote)
    data["milestones"][0]["payment_conditions"] = {"value": "Due after Milestone 1 is accepted", "source_excerpt": quote}
    result = validated(data, text)
    assert result.data.milestones[0].payment_conditions.value is None
    assert any(issue.field == "milestones[0].payment_conditions" and issue.code == "MILESTONE_EVIDENCE_MISMATCH" for issue in result.review_issues)


def test_unassociated_paragraph_layout_is_saved_as_null_for_manual_correction(http):
    data = sample_output()
    text = sample_text().replace("\nAmount:", "\n\nAmount:")
    http[0].append(ProviderResponse(body=envelope(data)))
    adapter = router()
    raw = adapter.extract(text)
    result = validate_extraction(raw, text, provider=adapter, mode="configured")
    assert all(row.amount.value is None for row in result.data.milestones)
    assert any(issue.code == "MILESTONE_ASSOCIATION_UNCERTAIN" for issue in result.review_issues)
    assert result.requires_human_review and len(http[1]) == 1


def test_crlf_shared_clause_does_not_make_an_unrelated_preamble_fee_universal():
    data = sample_output()
    shared = "All milestones: Payment condition: Due after written acceptance."
    text = "Administrative deposit: USD 25.00.\n\n" + sample_text() + "\n\n" + shared
    data["milestones"][0]["amount"] = {"value": "25", "source_excerpt": "Administrative deposit: USD 25.00."}
    result = validated(data, text.replace("\n", "\r\n"))
    assert result.data.milestones[0].amount.value is None
    assert any(issue.field == "milestones[0].amount" and issue.code == "MILESTONE_ASSOCIATION_UNCERTAIN" for issue in result.review_issues)


@pytest.mark.parametrize("classification,expected", [("IN_SCOPE", 502), ("OUT_OF_SCOPE", 502), ("AMBIGUOUS", 200)])
def test_configured_scope_distinguishes_honest_absence_from_unsupported_claims(scope_api, http, monkeypatch, classification, expected):
    app, client, contract = scope_api
    work = "Provide telephone support"
    row = create(client, contract, [work])
    data = {"findings": [{"ordinal": 1, "requested_work": work, "classification": classification,
        "explanation": "The agreement does not settle this requested service.", "contract_evidence": [],
        "uncertainties": ["Coverage remains unresolved."], "recommended_action": "CLARIFY_WITH_CLIENT",
        "requires_human_review": True}]}
    queue, calls = http
    queue.append(ProviderResponse(body=envelope(data)))
    configured(monkeypatch, router())
    response = analyze(client, row, "configured")
    assert response.status_code == expected and len(calls) == 1
    if expected == 200:
        saved = response.json()
        assert saved["status"] == "REVIEW_REQUIRED"
        finding = saved["analysis"]["findings"][0]
        assert finding["classification"] == "AMBIGUOUS" and finding["contract_evidence"] == []
        assert finding["requires_human_review"] and finding["recommended_action"] == "CLARIFY_WITH_CLIENT"
        assert "CHANGE_1:NO_RELEVANT_CLAUSE_CITED" in saved["analysis"]["review_flags"]
        assert prepare(client, saved, proposed_price="1.00", currency="USD").status_code == 409
        with Session(app.state.engine) as session:
            assert session.scalar(select(func.count()).select_from(InvoiceLink)) == 0


def test_ambiguous_classification_does_not_excuse_fabricated_evidence(http):
    from test_scope_changes import output, PATH, SCENARIOS
    data = output()
    data["findings"][0].update(classification="AMBIGUOUS", uncertainties=["Needs clarification."],
        contract_evidence=[{"source_excerpt": "Invented telephone entitlement.", "relationship": "CONTEXT"}])
    http[0].append(ProviderResponse(body=envelope(data)))
    with pytest.raises(ExtractionError) as caught:
        router().compare_scope(PATH.read_text(encoding="utf-8"), [SCENARIOS[0][0]])
    assert caught.value.code == "AI_UNGROUNDED_OUTPUT" and len(http[1]) == 1


class FailedProvider:
    name, live_ai = "offline_failure", False
    def extract(self, text):
        raise ExtractionError("AI_TIMEOUT", "Synthetic offline timeout", 504)


def test_failed_reextraction_preserves_review_and_invoice_eligibility_across_restart(invoice_api):
    app, client, cid, paypal = invoice_api
    before = client.get(f"/api/contracts/{cid}").json()
    app.dependency_overrides[get_extraction_provider] = FailedProvider
    assert client.post(f"/api/contracts/{cid}/extract", json={"mode": "configured", "force": True}).status_code == 504
    after = client.get(f"/api/contracts/{cid}").json()
    assert after["status"] == "REVIEW_REQUIRED" and after["error_code"] == "AI_TIMEOUT"
    assert after["extraction"] == before["extraction"] and after["original_ai_output"] == before["original_ai_output"]
    assert after["milestones"] == before["milestones"]
    assert all(row["status"] == "REVIEW_REQUIRED" for row in after["milestones"])
    assert client.post(f"/api/contracts/{cid}/extract", json={"mode": "offline_demo"}).json() == after
    row = approve_milestone(client, cid)
    assert client.get(f"/api/milestones/{row['id']}/invoice/preview").status_code == 200
    history = client.get(f"/api/contracts/{cid}/activity").json()
    with Session(app.state.engine) as session:
        failure = session.scalar(select(ContractEvent).where(ContractEvent.contract_id == cid, ContractEvent.action == "EXTRACTION_FAILED"))
        assert failure.data["retained_validated_extraction"] and failure.actor_id
    restarted = create_app(storage_url=str(app.state.engine.url))
    restarted.dependency_overrides[get_paypal] = lambda: paypal
    with TestClient(restarted, base_url="http://127.0.0.1:8000") as again:
        assert again.get(f"/api/contracts/{cid}").json()["milestones"][0]["status"] == "APPROVED"
        assert again.get(f"/api/milestones/{row['id']}/invoice/preview").status_code == 200
        assert again.get(f"/api/contracts/{cid}/activity").json() == history
    assert not paypal.creates


def test_old_failed_snapshot_can_be_recovered_without_replacing_reviewed_facts(invoice_api):
    app, client, cid, _ = invoice_api
    row = approve_milestone(client, cid)
    before = client.get(f"/api/contracts/{cid}").json()
    with Session(app.state.engine) as session:
        stored = session.get(Contract, cid)
        stored.status, stored.error_code = "FAILED", "AI_TIMEOUT"
        session.commit()
    recovered = client.post(f"/api/contracts/{cid}/extract", json={"mode": "offline_demo"})
    assert recovered.status_code == 200
    assert recovered.json()["milestones"] == before["milestones"]
    assert recovered.json()["status"] == "REVIEW_REQUIRED"
    assert client.get(f"/api/milestones/{row['id']}/invoice/preview").status_code == 200
    assert any(event["action"] == "EXTRACTION_RECOVERED" for event in client.get(f"/api/contracts/{cid}/activity").json())


@pytest.mark.parametrize("supersede", [False, True])
def test_concurrent_reextraction_failure_cannot_unlock_active_review_or_clobber_recovery(invoice_api, supersede):
    app, client, cid, _ = invoice_api
    entered, release = Event(), Event()
    class BlockingFailure(FailedProvider):
        def extract(self, text):
            entered.set()
            assert release.wait(15), "Test did not release the offline provider"
            return super().extract(text)
    worker_app = create_app(storage_url=str(app.state.engine.url))
    worker_app.dependency_overrides[get_extraction_provider] = BlockingFailure
    def request():
        with TestClient(worker_app, base_url="http://127.0.0.1:8000") as worker:
            return worker.post(f"/api/contracts/{cid}/extract", json={"mode": "configured", "force": True})
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(request)
        try:
            assert entered.wait(15)
            for force in (False, True):
                assert client.post(f"/api/contracts/{cid}/extract", json={"mode": "offline_demo", "force": force}).status_code == 409
            milestone = client.get(f"/api/contracts/{cid}").json()["milestones"][0]
            assert client.post(f"/api/milestones/{milestone['id']}/approve", json={
                "expected_revision": milestone["revision"], "expected_contract_revision": milestone["contract_review_revision"],
                "reviewer_label": "Offline concurrent reviewer", "note": "Review must remain locked", "confirmed": True,
                "acknowledge_review_flags": True}).status_code == 409
            if supersede:
                with Session(app.state.engine) as session:
                    session.get(Contract, cid).processing_started_at = utc_now() - timedelta(minutes=6)
                    session.commit()
                saved = client.post(f"/api/contracts/{cid}/extract", json={"mode": "offline_demo", "force": True})
                assert saved.status_code == 200
        finally:
            release.set()
        assert pending.result(timeout=20).status_code == 504
    current = client.get(f"/api/contracts/{cid}").json()
    assert current["status"] == "REVIEW_REQUIRED"
    assert current["error_code"] == (None if supersede else "AI_TIMEOUT")
    assert all(row["status"] == "REVIEW_REQUIRED" for row in current["milestones"])
    if supersede:
        assert current == saved.json()
        with Session(app.state.engine) as session:
            assert session.scalar(select(func.count()).select_from(ContractEvent).where(
                ContractEvent.contract_id == cid, ContractEvent.action == "EXTRACTION_FAILED")) == 0
