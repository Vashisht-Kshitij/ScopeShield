"""API behavior, validation, and safe upstream failures without external calls."""
import pytest
import requests
from fastapi.testclient import TestClient

from app.main import app, get_paypal
from app.paypal import PayPalAPIError, PayPalConfigurationError
from conftest import INVOICE_ID, invoice_body, response


class FakePayPal:
    def __init__(self):
        self.calls = []
        self.status = "DRAFT"

    def create_invoice(self, *, milestone, amount_usd):
        self.calls.append(("create", milestone, amount_usd))
        return INVOICE_ID

    def get_invoice(self, invoice_id):
        self.calls.append(("get", invoice_id))
        return {"invoice_id": invoice_id, "status": self.status, "currency_code": "USD", "total_amount": "1.00"}

    def send_invoice(self, invoice_id):
        self.calls.append(("send", invoice_id))
        self.status = "SENT"


@pytest.fixture
def fake():
    return FakePayPal()


@pytest.fixture
def client(fake):
    app.dependency_overrides[get_paypal] = lambda: fake
    try:
        with TestClient(app, base_url="http://127.0.0.1") as test_client:
            from sqlalchemy import select
            from sqlalchemy.orm import Session
            from app.storage import User, ManualInvoiceOwner
            with Session(app.state.engine) as session:
                owner = session.scalar(select(User).where(User.username == "domain.test"))
                session.add(ManualInvoiceOwner(invoice_id=INVOICE_ID, owner_id=owner.id))
                session.commit()
            yield test_client
    finally:
        app.dependency_overrides.clear()


def test_health_does_not_need_paypal():
    def unavailable():
        raise AssertionError("Health must not initialize the PayPal service")
    app.dependency_overrides[get_paypal] = unavailable
    try:
        with TestClient(app, base_url="http://127.0.0.1") as client:
            result = client.get("/health")
        assert result.status_code == 200
        assert result.json() == {"status": "ok", "environment": "paypal_sandbox"}
    finally:
        app.dependency_overrides.clear()


def test_legacy_mutations_are_disabled_and_owned_reads_remain(client, fake):
    from sqlalchemy.orm import Session
    from app.storage import ManualInvoiceOwner
    with Session(app.state.engine) as session:
        session.delete(session.get(ManualInvoiceOwner, INVOICE_ID)); session.commit()
    created = client.post("/api/invoices", json={"milestone": "  Test milestone  ", "amount_usd": "1.00"})
    assert created.status_code == 410
    assert created.json()["detail"]["code"] == "LEGACY_INVOICE_MUTATION_DISABLED"
    assert fake.calls == []
    # Historical ownership receipts are not created by a disabled mutation.
    assert client.get(f"/api/invoices/{INVOICE_ID}").status_code == 404
    with Session(app.state.engine) as session:
        from sqlalchemy import select
        from app.storage import User
        owner = session.scalar(select(User).where(User.username == "domain.test"))
        session.add(ManualInvoiceOwner(invoice_id=INVOICE_ID, owner_id=owner.id)); session.commit()
    assert client.get(f"/api/invoices/{INVOICE_ID}").json()["status"] == "DRAFT"
    sent = client.post(f"/api/invoices/{INVOICE_ID}/send", json={"confirmed": True})
    assert sent.status_code == 410
    assert [call for call in fake.calls if call[0] in {"create", "send"}] == []


@pytest.mark.parametrize("payload", [
    {}, {"milestone": "Test milestone"}, {"amount_usd": "1.00"},
    {"milestone": "Hi", "amount_usd": "1.00"},
    {"milestone": "   ", "amount_usd": "1.00"},
    {"milestone": "x" * 121, "amount_usd": "1.00"},
    {"milestone": None, "amount_usd": "1.00"},
    *[{"milestone": "Test milestone", "amount_usd": amount} for amount in
      ["0", "-1", "1000.01", "1.999", "NaN", "Infinity", "abc", None, True]],
    {"milestone": "Test milestone", "amount_usd": "1.00", "buyer_email": "unapproved@example.com"},
    {"milestone": "Test milestone", "amount_usd": "1.00", "status": "PAID"},
])
def test_invalid_create_does_not_call_provider(client, fake, payload):
    assert client.post("/api/invoices", json=payload).status_code == 410
    assert fake.calls == []


@pytest.mark.parametrize("suffix", ["", "/send"])
@pytest.mark.parametrize("invoice_id", ["bad-id", "INV2-x", "INV2----", "INV2-" + "A" * 90])
def test_invalid_invoice_id(client, fake, suffix, invoice_id):
    result = client.request("POST" if suffix else "GET", f"/api/invoices/{invoice_id}{suffix}")
    assert result.status_code == 422
    assert fake.calls == []


def test_validation_does_not_echo_secret_input(client):
    result = client.get("/api/invoices/dummy_secret_to_hide")
    assert result.status_code == 422
    assert "dummy_secret_to_hide" not in result.text


def test_configuration_failure_is_safe(client):
    def unavailable():
        raise PayPalConfigurationError("dummy_secret_to_hide")
    app.dependency_overrides[get_paypal] = unavailable
    result = client.get(f"/api/invoices/{INVOICE_ID}")
    assert result.status_code == 503
    assert "dummy_secret_to_hide" not in result.text


@pytest.mark.parametrize("failure,expected", [
    (response(401, {"error": "invalid_client", "message": "dummy_client_secret"}), 502),
    (response(403, {"name": "NOT_AUTHORIZED", "debug_id": "abcdef0123456", "message": "dummy_client_secret"}), 502),
    (response(422, {"name": "UNPROCESSABLE_ENTITY", "details": [{"value": "dummy_client_secret"}]}), 502),
    (response(429, {"name": "RATE_LIMIT_REACHED"}), 502),
    (response(500, text="dummy_client_secret upstream HTML"), 502),
    (response(500, ["dummy_client_secret"]), 502),
    (response(500, {"name": "dummy_client_secret", "debug_id": "dummy_access_token"}), 502),
    (requests.Timeout("dummy_client_secret"), 504),
    (requests.ConnectionError("dummy_client_secret"), 502),
])
def test_provider_failures_are_not_success_or_secret_leaks(client, service, provider, failure, expected):
    app.dependency_overrides[get_paypal] = lambda: service
    provider.queue(failure)
    result = client.get(f"/api/invoices/{INVOICE_ID}")
    assert result.status_code == expected
    assert "dummy_client_secret" not in result.text
    assert "dummy_access_token" not in result.text
    assert len(provider.calls) == 1
    assert "invoice_id" not in result.json()["detail"]


def test_upstream_not_found(client, service, provider):
    app.dependency_overrides[get_paypal] = lambda: service
    provider.oauth()
    provider.queue(response(404, {"name": "RESOURCE_NOT_FOUND"}))
    result = client.get(f"/api/invoices/{INVOICE_ID}")
    assert result.status_code == 404
    assert result.json()["detail"]["upstream_status"] == 404


@pytest.mark.parametrize("action", ["create", "send"])
def test_disabled_mutations_cannot_reach_a_failing_followup_read(client, fake, monkeypatch, action):
    def failed_read(invoice_id):
        raise PayPalAPIError("PAYPAL_UNAVAILABLE", operation="get_invoice")
    monkeypatch.setattr(fake, "get_invoice", failed_read)
    if action == "create":
        result = client.post("/api/invoices", json={"milestone": "Test milestone", "amount_usd": "1.00"})
    else:
        result = client.post(f"/api/invoices/{INVOICE_ID}/send", json={"confirmed": True})
    assert result.status_code == 410
    assert result.json()["detail"]["code"] == "LEGACY_INVOICE_MUTATION_DISABLED"
    assert fake.calls == []


def test_disabled_send_cannot_reach_provider_or_followup_read(client, fake, monkeypatch):
    def failed_send(invoice_id):
        raise PayPalAPIError("NOT_AUTHORIZED", operation="send_invoice", upstream_status=403)
    monkeypatch.setattr(fake, "send_invoice", failed_send)
    result = client.post(f"/api/invoices/{INVOICE_ID}/send", json={"confirmed": True})
    assert result.status_code == 410
    assert fake.calls == []


def test_swagger_and_openapi(client):
    assert client.get("/docs").status_code == 200
    schema = client.get("/openapi.json").json()
    assert {"/health", "/api/invoices", "/api/invoices/{invoice_id}", "/api/invoices/{invoice_id}/send"} <= set(schema["paths"])


def test_untrusted_host_is_rejected(client, fake):
    assert client.get("/health", headers={"Host": "attacker.example.com"}).status_code == 400
    assert fake.calls == []


@pytest.mark.parametrize("origin", ["https://attacker.example.com", "null"])
def test_foreign_origin_cannot_trigger_send(client, fake, origin):
    result = client.post(f"/api/invoices/{INVOICE_ID}/send", headers={"Origin": origin})
    assert result.status_code == 403
    assert fake.calls == []


def test_swagger_origin_cannot_restore_legacy_draft_creation(client, fake):
    result = client.post("/api/invoices", headers={"Origin": "http://127.0.0.1"}, json={"milestone": "Test milestone", "amount_usd": "1.00"})
    assert result.status_code == 410 and fake.calls == []
