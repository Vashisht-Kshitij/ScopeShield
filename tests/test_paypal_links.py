"""Verify the official sandbox payload and strict read-only reconciliation."""
import copy
from decimal import Decimal
from uuid import uuid4

import pytest

from app.paypal import PayPalAPIError
from conftest import BUYER, INVOICE_ID, invoice_body, response

NUMBER = "SS-" + "a" * 22
NAME = "Postcard sketch"
DESCRIPTION = "USD 1.00 is due after written sketch acceptance"


def full_invoice(**changes):
    return invoice_body(detail={"invoice_number": NUMBER}, items=[{
        "name": NAME, "description": DESCRIPTION, "quantity": "1",
        "unit_amount": {"currency_code": "USD", "value": "1.00"},
    }], **changes)


def expected():
    return {"invoice_number": NUMBER, "name": NAME, "description": DESCRIPTION, "amount": Decimal("1.00")}


def test_durable_create_sends_request_id_unique_reference_and_exact_reviewed_fields(service, provider):
    provider.oauth()
    provider.queue(response(201, {"id": INVOICE_ID}))
    request_id = str(uuid4())
    assert service.create_invoice(milestone=NAME, description=DESCRIPTION, amount_usd=Decimal("1.00"),
                                  invoice_number=NUMBER, request_id=request_id) == INVOICE_ID
    method, url, options = provider.calls[-1]
    assert method == "POST" and url.endswith("/v2/invoicing/invoices")
    assert options["headers"]["PayPal-Request-Id"] == request_id
    assert options["json"]["detail"]["invoice_number"] == NUMBER
    assert options["json"]["items"][0]["description"] == DESCRIPTION
    assert options["json"]["items"][0]["unit_amount"]["value"] == "1.00"


def test_invoice_details_are_verified_without_exposing_recipient(service, provider):
    provider.oauth()
    provider.queue(response(body=full_invoice()))
    result = service.verify_milestone_invoice(INVOICE_ID, **expected())
    assert result["status"] == "DRAFT" and result["total_amount"] == "1.00"
    assert BUYER not in str(result)


def test_linked_send_rechecks_complete_facts_before_the_only_send_post(service, provider):
    provider.oauth()
    provider.queue(response(body=full_invoice()), response(204, {}))
    service.send_milestone_invoice(INVOICE_ID, **expected(), recipient_email=BUYER)
    posts = [call for call in provider.calls if call[0] == "POST" and call[1].endswith("/send")]
    assert len(posts) == 1 and posts[0][2]["json"]["send_to_recipient"] is True


def test_changed_recipient_snapshot_blocks_send(service, provider):
    provider.oauth(); provider.queue(response(body=full_invoice()))
    with pytest.raises(PayPalAPIError, match="PayPal sandbox operation failed"):
        service.send_milestone_invoice(INVOICE_ID, **expected(), recipient_email="different@personal.example.com")
    assert not any(call[1].endswith("/send") for call in provider.calls)


@pytest.mark.parametrize("field", ["recipient", "reference", "name", "description", "quantity", "unit_amount", "total", "currency", "extra_recipient", "extra_item", "nan"])
def test_reconciliation_rejects_invoices_with_conflicting_snapshot_facts(service, provider, field):
    body = full_invoice()
    item = body["items"][0]
    if field == "recipient": body["primary_recipients"][0]["billing_info"]["email_address"] = "other@example.com"
    if field == "reference": body["detail"]["invoice_number"] = "wrong-reference"
    if field == "name": item["name"] = "Different milestone"
    if field == "description": item["description"] = "Unreviewed terms"
    if field == "quantity": item["quantity"] = "2"
    if field == "unit_amount": item["unit_amount"]["value"] = "2.00"
    if field == "total": body["amount"]["value"] = "2.00"
    if field == "currency": body["amount"]["currency_code"] = "EUR"
    if field == "extra_recipient": body["additional_recipients"] = ["other@example.com"]
    if field == "extra_item": body["items"].append(copy.deepcopy(item))
    if field == "nan": item["unit_amount"]["value"] = "NaN"
    provider.oauth()
    provider.queue(response(body=body))
    with pytest.raises(PayPalAPIError, match="operation failed") as caught:
        service.verify_milestone_invoice(INVOICE_ID, **expected())
    assert caught.value.name == "INVOICE_LINK_MISMATCH"


def test_read_only_search_finds_and_verifies_exact_reference(service, provider):
    provider.oauth()
    provider.queue(response(body={"items": [{"id": INVOICE_ID}], "total_pages": 1}), response(body=full_invoice()))
    result = service.find_milestone_invoice(**expected())
    assert result["invoice_id"] == INVOICE_ID
    method, url, options = provider.calls[1]
    assert method == "POST" and url.endswith("/v2/invoicing/search-invoices")
    assert options["json"] == {"invoice_number": NUMBER}
    assert all(not url.endswith("/v2/invoicing/invoices") for _, url, _ in provider.calls)


def test_empty_search_does_not_create_invoice(service, provider):
    provider.oauth()
    provider.queue(response(body={"items": [], "total_pages": 0}))
    assert service.find_milestone_invoice(**expected()) is None
    assert len(provider.calls) == 2


@pytest.mark.parametrize("body", [{}, {"items": [] , "total_pages": 2}, {"items": [], "links": [{"rel": "next"}]}, {"items": [{"id": "invalid"}]}, {"items": [], "links": 17}])
def test_incomplete_or_malformed_search_cannot_report_safe_absence(service, provider, body):
    provider.oauth()
    provider.queue(response(body=body))
    with pytest.raises(PayPalAPIError):
        service.find_milestone_invoice(**expected())


def test_multiple_exact_matches_require_manual_investigation(service, provider):
    other_id = "INV2-SECOND-1234-5678-ABCD"
    provider.oauth()
    provider.queue(response(body={"items": [{"id": INVOICE_ID}, {"id": other_id}], "total_pages": 1}),
                   response(body=full_invoice()), response(body=full_invoice(id=other_id)))
    with pytest.raises(PayPalAPIError) as caught:
        service.find_milestone_invoice(**expected())
    assert caught.value.name == "MULTIPLE_INVOICE_MATCHES"
