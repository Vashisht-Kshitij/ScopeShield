"""APIMatic-informed OAuth and invoice regressions; all HTTP is intercepted."""
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier
from types import SimpleNamespace
from uuid import uuid4

import pytest
import requests

import app.paypal as adapter
from app.paypal import BASE_URL, PayPalAPIError
from conftest import INVOICE_ID, invoice_body, response
from test_milestone_invoices import action, api, approve, creation


HTTP_ERRORS = [400, 401, 403, 404, 409, 429, 500]


@pytest.mark.parametrize("lifetime,cache_window", [
    (1, 0.5), (30, 15), (60, 30), (90, 45), (120, 60), (3600, 3540),
])
def test_token_reuse_and_refresh_at_lifetime_appropriate_boundary(
    service, provider, monkeypatch, lifetime, cache_window,
):
    clock = [100.0]
    monkeypatch.setattr(adapter, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    provider.queue(response(body={"access_token": "dummy_short_token", "expires_in": lifetime}))
    provider.queue(response(body=invoice_body()), response(body=invoice_body()))
    assert service.get_invoice(INVOICE_ID)["status"] == "DRAFT"
    clock[0] = 100 + cache_window - 0.01
    service.get_invoice(INVOICE_ID)
    assert sum(url.endswith("/oauth2/token") for _, url, _ in provider.calls) == 1
    clock[0] = 100 + cache_window
    provider.oauth()
    provider.queue(response(body=invoice_body()))
    service.get_invoice(INVOICE_ID)
    assert sum(url.endswith("/oauth2/token") for _, url, _ in provider.calls) == 2
    assert all(method == "GET" for method, url, _ in provider.calls if "/invoicing/" in url)


def test_concurrent_reads_share_one_short_lived_token(service, provider, monkeypatch):
    monkeypatch.setattr(adapter, "time", SimpleNamespace(monotonic=lambda: 100.0))
    provider.queue(response(body={"access_token": "dummy_short_token", "expires_in": 30}))
    provider.queue(*(response(body=invoice_body()) for _ in range(8)))
    ready = Barrier(8)
    def read(_):
        ready.wait(timeout=10)
        return service.get_invoice(INVOICE_ID)
    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(read, range(8)))
    assert all(result["status"] == "DRAFT" for result in results)
    assert len(provider.calls) == 9
    assert sum(url.endswith("/oauth2/token") for _, url, _ in provider.calls) == 1
    assert all(options["headers"]["Authorization"] == "Bearer dummy_short_token"
               for method, _, options in provider.calls if method == "GET")


@pytest.mark.parametrize("elapsed,accepted", [(20, True), (30, False), (31, False)])
def test_slow_token_fetch_does_not_extend_declared_lifetime(service, monkeypatch, elapsed, accepted):
    clock, calls = [100.0], []
    monkeypatch.setattr(adapter, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    def request(method, url, **options):
        calls.append((method, url))
        if url.endswith("/oauth2/token"):
            clock[0] += elapsed
            return response(body={"access_token": "dummy_slow_token", "expires_in": 30})
        assert accepted and method == "GET"
        return response(body=invoice_body())
    monkeypatch.setattr(requests, "request", request)
    if accepted:
        assert service.get_invoice(INVOICE_ID)["status"] == "DRAFT"
        # The early-refresh deadline already passed: the next action fetches again.
        service.get_invoice(INVOICE_ID)
        assert sum(url.endswith("/oauth2/token") for _, url in calls) == 2
    else:
        with pytest.raises(PayPalAPIError) as caught:
            service.get_invoice(INVOICE_ID)
        assert caught.value.name == "OAUTH_TOKEN_EXPIRED"
        assert caught.value.operation == "oauth"
        assert calls == [("POST", BASE_URL + "/v1/oauth2/token")]


@pytest.mark.parametrize("status", HTTP_ERRORS)
@pytest.mark.parametrize("operation", ["read", "create", "send", "search"])
def test_provider_errors_never_retry_or_report_success(service, provider, operation, status):
    provider.oauth()
    if operation == "send":
        provider.queue(response(body=invoice_body()))
    provider.queue(response(status, {"name": "PROVIDER_REJECTED", "debug_id": "abcd1234",
                                     "message": "dummy_client_secret dummy_access_token"}))
    facts = dict(invoice_number="SS-" + "a" * 22, name="Sketch", description="Reviewed",
                 amount=Decimal("1.00"), currency="USD", recipient_email=service.recipient_email)
    with pytest.raises(PayPalAPIError) as caught:
        if operation == "read":
            service.get_invoice(INVOICE_ID)
        elif operation == "create":
            service.create_invoice(milestone="Sketch", amount_usd=Decimal("1.00"), request_id=str(uuid4()))
        elif operation == "send":
            service.send_invoice(INVOICE_ID)
        else:
            service.find_milestone_invoice(**facts)
    error = caught.value
    assert error.upstream_status == status
    assert error.http_status == (404 if status == 404 and operation == "read" else 502)
    assert error.debug_id == "abcd1234"
    assert "dummy_client_secret" not in str(vars(error))
    assert "dummy_access_token" not in str(vars(error))
    assert len(provider.calls) == (3 if operation == "send" else 2)
    assert all(url.startswith(BASE_URL + "/") and options["allow_redirects"] is False
               for _, url, options in provider.calls)


@pytest.mark.parametrize("status", HTTP_ERRORS)
def test_oauth_failure_never_sends_an_invoice_request(service, provider, status):
    provider.queue(response(status, {"error": "invalid_client", "error_description": "dummy_client_secret"}))
    with pytest.raises(PayPalAPIError) as caught:
        service.get_invoice(INVOICE_ID)
    assert caught.value.operation == "oauth" and caught.value.http_status == 502
    assert len(provider.calls) == 1 and provider.calls[0][1].endswith("/oauth2/token")
    assert "dummy_client_secret" not in str(vars(caught.value))


@pytest.mark.parametrize("failure,http_status", [
    (requests.ConnectionError("dummy_client_secret"), 502),
    (requests.ConnectTimeout("dummy_client_secret"), 504),
    (requests.ReadTimeout("dummy_client_secret"), 504),
])
def test_network_failures_never_retry_a_create(service, provider, failure, http_status):
    provider.oauth()
    provider.queue(failure)
    with pytest.raises(PayPalAPIError) as caught:
        service.create_invoice(milestone="Sketch", amount_usd=Decimal("1.00"))
    assert caught.value.http_status == http_status
    assert "dummy_client_secret" not in str(vars(caught.value))
    assert len(provider.calls) == 2


@pytest.mark.parametrize("status", HTTP_ERRORS)
def test_http_rejection_retains_durable_claim_and_read_only_recovery(api, status):
    _, client, cid, paypal = api
    row = approve(client, cid)
    body = creation(client, row)
    path = f"/api/milestones/{row['id']}/invoice"
    paypal.create_error = PayPalAPIError("PROVIDER_REJECTED", operation="create_invoice", upstream_status=status)
    assert client.post(path, json=body).status_code == 502
    link = client.get(path).json()
    assert link["state"] == "RECONCILIATION_REQUIRED" and link["verified"] is False
    assert client.post(path, json=body).status_code == 409
    assert client.post(path, json={**body, "idempotency_key": str(uuid4())}).status_code == 409
    paypal.create_error = None
    paypal.search_missing = True
    recovered = client.post(path + "/reconcile", json=action(link))
    assert recovered.status_code == 200
    assert recovered.json()["state"] == "RECONCILIATION_REQUIRED"
    assert recovered.json()["paypal_status"] is None
    assert len(paypal.creates) == 1 and len(paypal.searches) == 1 and not paypal.reads


def test_failed_sync_keeps_historical_status_without_claiming_new_verification(api):
    _, client, cid, paypal = api
    row = approve(client, cid)
    path = f"/api/milestones/{row['id']}/invoice"
    link = client.post(path, json=creation(client, row)).json()
    assert link["paypal_status"] == "DRAFT" and link["verified"] is True
    paypal.status = "PAID"  # A mock provider value, never a real payment assertion.
    paypal.read_error = PayPalAPIError("PROVIDER_REJECTED", operation="get_invoice", upstream_status=429)
    assert client.post(path + "/sync", json=action(link)).status_code == 502
    stored = client.get(path).json()
    assert stored["paypal_status"] == "DRAFT" and stored["verified"] is False
    assert stored["last_checked_at"] == link["last_checked_at"]
    assert stored["error_code"] == "PROVIDER_REJECTED" and len(paypal.creates) == 1
    # Pure local reads never contact the provider or repair the failed verification.
    read_count = len(paypal.reads)
    client.get(path)
    client.get(path + "/history")
    assert len(paypal.reads) == read_count


def test_401_read_fails_once_then_next_action_reauthenticates(service, provider):
    provider.oauth()
    provider.queue(response(401, {"name": "AUTHENTICATION_FAILURE"}))
    with pytest.raises(PayPalAPIError):
        service.get_invoice(INVOICE_ID)
    assert len(provider.calls) == 2
    provider.oauth()
    provider.queue(response(body=invoice_body()))
    assert service.get_invoice(INVOICE_ID)["status"] == "DRAFT"
    assert len(provider.calls) == 4
