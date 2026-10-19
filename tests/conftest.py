"""Offline fixtures: no real network traffic and no use of local credentials."""
import json
from collections import deque

import pytest
import requests

from app.paypal import PayPalSandbox


@pytest.fixture(autouse=True)
def authenticated_legacy_clients(monkeypatch, request):
    """Existing domain tests run through REAL login/CSRF, never a production bypass.

    Security suites deliberately opt out so anonymous/adversarial behavior is tested.
    Accounts exist only in each test's disposable database. Reuse one random password
    hash to avoid spending minutes on account provisioning across 372 domain tests.
    """
    if request.node.path.name in {"test_auth.py", "test_auth_migration.py"}:
        return
    from fastapi.testclient import TestClient
    from sqlalchemy import select
    from sqlalchemy.orm import Session
    from app.auth import hash_password
    from app.storage import User
    import secrets
    password = secrets.token_urlsafe(24)
    # The fixture's KDF is cached across tests without persisting any password.
    if not hasattr(authenticated_legacy_clients, "credentials"):
        authenticated_legacy_clients.credentials = (password, hash_password(password))
    password, encoded = authenticated_legacy_clients.credentials
    original_enter, original_request = TestClient.__enter__, TestClient.request
    def enter(client):
        result = original_enter(client)
        with Session(client.app.state.engine) as session:
            user = session.scalar(select(User).where(User.username == "domain.test"))
            if user is None:
                session.add(User(username="domain.test", display_name="Domain test", role="freelancer", password_hash=encoded))
                session.commit()
        preflight = original_request(client, "GET", "/api/auth/csrf").json()
        logged = original_request(client, "POST", "/api/auth/login", json={"username": "domain.test", "password": password}, headers={"X-CSRF-Token": preflight["csrf_token"]})
        assert logged.status_code == 200
        client._test_csrf = logged.json()["csrf_token"]
        return result
    def authenticated_request(client, method, url, **kwargs):
        if getattr(client, "_test_csrf", None):
            kwargs["headers"] = {"X-CSRF-Token": client._test_csrf, **(kwargs.get("headers") or {})}
        return original_request(client, method, url, **kwargs)
    monkeypatch.setattr(TestClient, "__enter__", enter)
    monkeypatch.setattr(TestClient, "request", authenticated_request)

INVOICE_ID = "INV2-TEST-1234-5678-ABCD"
BUYER = "buyer@personal.example.com"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("Automated tests must never make a real network call")
    monkeypatch.setattr(requests.sessions.Session, "request", blocked)


@pytest.fixture(autouse=True)
def isolated_default_database(monkeypatch, tmp_path):
    monkeypatch.setenv("SCOPESHIELD_DATABASE_URL", "sqlite:///" + (tmp_path / "isolated.db").as_posix())
    monkeypatch.setenv("SCOPESHIELD_AI_PROVIDER", "disabled")


def response(status=200, body=None, *, headers=None, text=None):
    result = requests.Response()
    result.status_code = status
    result.headers.update(headers or {})
    result._content = (text if text is not None else json.dumps(body)).encode()
    return result


def invoice_body(**changes):
    body = {
        "id": INVOICE_ID, "status": "DRAFT",
        "primary_recipients": [{"billing_info": {"email_address": BUYER}}],
        "amount": {"currency_code": "USD", "value": "1.00"},
        "detail": {"metadata": {"recipient_view_url": "https://www.sandbox.paypal.com/invoice/p/#TEST"}},
    }
    body.update(changes)
    return body


@pytest.fixture
def service():
    return PayPalSandbox(client_id="dummy_client_id", client_secret="dummy_client_secret", buyer_email=BUYER)


@pytest.fixture
def provider(monkeypatch):
    """Queue documented mock responses or exceptions, and record requests."""
    queue, calls = deque(), []

    def request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        assert queue, "Unexpected extra PayPal request"
        item = queue.popleft()
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(requests, "request", request)

    class Provider:
        def __init__(self):
            self.calls = calls

        def queue(self, *items):
            queue.extend(items)

        def oauth(self):
            self.queue(response(body={"access_token": "dummy_access_token", "expires_in": 3600}))

    result = Provider()
    yield result
    assert not queue, "A planned provider response was never consumed"
