"""Exercise the real service against mocked HTTP responses, including failures."""
from decimal import Decimal

import pytest

from app.paypal import BASE_URL, PayPalAPIError, PayPalConfigurationError, PayPalSandbox, extract_invoice_id
from conftest import BUYER, INVOICE_ID, invoice_body, response


@pytest.mark.parametrize("body,headers,text", [
    ({"id": INVOICE_ID}, {}, None),
    (None, {"Location": f"{BASE_URL}/v2/invoicing/invoices/{INVOICE_ID}"}, ""),
    ({"links": [{"href": f"{BASE_URL}/v2/invoicing/invoices/{INVOICE_ID}"}]}, {}, None),
    ({"id": "invalid", "links": [{"href": f"{BASE_URL}/v2/invoicing/invoices/{INVOICE_ID}"}]}, {}, None),
])
def test_id_response_variants(body, headers, text):
    assert extract_invoice_id(response(201, body, headers=headers, text=text)) == INVOICE_ID


@pytest.mark.parametrize("body", [{}, [], {"id": "../../token"}, {"message": INVOICE_ID}, {"links": [{"href": "https://[invalid"}]}])
def test_missing_id_is_an_error(body):
    with pytest.raises(PayPalAPIError, match="operation failed") as caught:
        extract_invoice_id(response(201, body))
    assert caught.value.name == "INVOICE_ID_MISSING"


def test_create_uses_usd_local_buyer_and_never_sends(service, provider):
    provider.oauth()
    provider.queue(response(201, {"id": INVOICE_ID}))
    assert service.create_invoice(milestone="Test milestone", amount_usd=Decimal("1.00")) == INVOICE_ID
    assert len(provider.calls) == 2
    _, url, options = provider.calls[1]
    assert url == BASE_URL + "/v2/invoicing/invoices"
    assert options["json"]["items"][0]["unit_amount"] == {"currency_code": "USD", "value": "1.00"}
    assert options["json"]["primary_recipients"][0]["billing_info"]["email_address"] == BUYER
    assert all(call[1].startswith(BASE_URL + "/") for call in provider.calls)
    assert all(call[2]["allow_redirects"] is False and call[2]["timeout"] == 25 for call in provider.calls)


def test_token_is_reused_and_expiry_refreshes(service, provider):
    provider.oauth()
    provider.queue(response(body=invoice_body()), response(body=invoice_body()))
    service.get_invoice(INVOICE_ID)
    service.get_invoice(INVOICE_ID)
    assert len(provider.calls) == 3
    service._token_expires_at = 0
    provider.oauth()
    provider.queue(response(body=invoice_body()))
    service.get_invoice(INVOICE_ID)
    assert len(provider.calls) == 5


def test_unauthorized_mutation_is_not_retried(service, provider):
    provider.oauth()
    provider.queue(response(401, {"name": "AUTHENTICATION_FAILURE"}))
    with pytest.raises(PayPalAPIError):
        service.create_invoice(milestone="Test milestone", amount_usd=Decimal("1.00"))
    assert len(provider.calls) == 2
    provider.oauth()
    provider.queue(response(body=invoice_body()))
    service.get_invoice(INVOICE_ID)
    assert len(provider.calls) == 4


@pytest.mark.parametrize("body", [
    {}, [], {"access_token": "dummy_access_token"},
    {"access_token": "", "expires_in": 3600},
    {"access_token": "dummy token", "expires_in": 3600},
    {"access_token": "dummy_access_token", "expires_in": "3600"},
    {"access_token": "dummy_access_token", "expires_in": True},
])
def test_invalid_oauth_is_not_success(service, provider, body):
    provider.queue(response(body=body))
    with pytest.raises(PayPalAPIError):
        service.get_invoice(INVOICE_ID)
    assert len(provider.calls) == 1


def test_get_only_returns_safe_fields_and_authoritative_status(service, provider):
    provider.oauth()
    provider.queue(response(body=invoice_body(status="PAID", access_token="dummy_access_token")))
    result = service.get_invoice(INVOICE_ID)
    assert result == {
        "invoice_id": INVOICE_ID, "status": "PAID", "currency_code": "USD", "total_amount": "1.00",
        "recipient_view_url": "https://www.sandbox.paypal.com/invoice/p/#TEST",
    }
    assert BUYER not in str(result) and "dummy_access_token" not in str(result)


@pytest.mark.parametrize("body", [
    [], {}, invoice_body(id="INV2-DIFFERENT"), invoice_body(status=None),
    invoice_body(status={}), invoice_body(status="UNKNOWN"),
])
def test_malformed_invoice_is_an_error(service, provider, body):
    provider.oauth()
    provider.queue(response(body=body))
    with pytest.raises(PayPalAPIError):
        service.get_invoice(INVOICE_ID)


@pytest.mark.parametrize("url", [
    "https://www.paypal.com/invoice/p/#LIVE", "http://www.sandbox.paypal.com/invoice/p/#TEST",
    "https://evil.example.com/invoice/p/#TEST", "https://www.sandbox.paypal.com.evil.example.com/test",
    "https://dummy_access_token@www.sandbox.paypal.com/test", "https://[invalid",
])
def test_unsafe_recipient_urls_are_omitted(service, provider, url):
    provider.oauth()
    provider.queue(response(body=invoice_body(detail={"metadata": {"recipient_view_url": url}})))
    assert service.get_invoice(INVOICE_ID)["recipient_view_url"] is None


def test_recipient_url_fallback_and_null_metadata(service, provider):
    provider.oauth()
    provider.queue(response(body=invoice_body(detail=None, links=[{"rel": "recipient-view", "href": "https://www.sandbox.paypal.com/invoice/p/#TEST"}])))
    assert service.get_invoice(INVOICE_ID)["recipient_view_url"].endswith("#TEST")


@pytest.mark.parametrize("status", [200, 202, 204])
def test_explicit_send_has_guarded_recipient_and_never_marks_paid(service, provider, status):
    provider.oauth()
    provider.queue(response(body=invoice_body()), response(status, text=""))
    assert service.send_invoice(INVOICE_ID) is None
    method, url, options = provider.calls[-1]
    assert method == "POST" and url.endswith(f"/{INVOICE_ID}/send")
    assert options["json"] == {"send_to_recipient": True, "send_to_invoicer": False}
    assert all("/payments" not in call[1] for call in provider.calls)


@pytest.mark.parametrize("body", [
    invoice_body(primary_recipients=[]),
    invoice_body(primary_recipients=[{"billing_info": {"email_address": "another@example.com"}}]),
    invoice_body(primary_recipients=[{"billing_info": {"email_address": BUYER}}, {"billing_info": {"email_address": BUYER}}]),
    invoice_body(primary_recipients=[{"billing_info": None}]),
    invoice_body(additional_recipients=["another@example.com"]),
    invoice_body(status="PAID"), invoice_body(status="SENT"),
])
def test_send_preflight_blocks_unapproved_recipients_and_nondrafts(service, provider, body):
    provider.oauth()
    provider.queue(response(body=body))
    with pytest.raises(PayPalAPIError) as caught:
        service.send_invoice(INVOICE_ID)
    assert caught.value.http_status == 409
    assert len(provider.calls) == 2


def test_invalid_service_id_does_not_authenticate(service, provider):
    with pytest.raises(PayPalAPIError):
        service.get_invoice("../../oauth/token")
    assert provider.calls == []


def test_configuration_loads_project_file_without_printing_values(monkeypatch, tmp_path):
    from pathlib import Path
    import app.paypal as module
    monkeypatch.chdir(tmp_path)
    seen = []
    def local_values(path):
        seen.append(path)
        return {"PAYPAL_CLIENT_ID": "local_id", "PAYPAL_CLIENT_SECRET": "local_secret", "PAYPAL_SANDBOX_BUYER_EMAIL": BUYER}
    monkeypatch.setattr(module, "dotenv_values", local_values)
    for name in ("PAYPAL_CLIENT_ID", "PAYPAL_CLIENT_SECRET", "PAYPAL_SANDBOX_BUYER_EMAIL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PAYPAL_CLIENT_ID", "override_id")
    service = PayPalSandbox.from_environment()
    assert service._client_id == "override_id"
    assert service._client_secret == "local_secret"
    assert seen[0] == Path(module.__file__).resolve().parents[1] / ".env"


@pytest.mark.parametrize("client_id,secret,buyer", [
    ("", "dummy_secret", BUYER), ("dummy_id", "", BUYER),
    ("dummy_id", "dummy_secret", ""), ("dummy_id", "dummy_secret", "buyer@example.com"),
    ("dummy_id", "dummy_secret", "invalid-email"),
])
def test_bad_configuration_is_safe(client_id, secret, buyer):
    with pytest.raises(PayPalConfigurationError) as caught:
        PayPalSandbox(client_id=client_id, client_secret=secret, buyer_email=buyer)
    assert "dummy_secret" not in str(caught.value)
