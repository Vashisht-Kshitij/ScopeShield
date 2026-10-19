"""Adversarial tests use unmodified TestClient and real sessions, not test auth overrides."""
from datetime import timedelta
from functools import lru_cache
import secrets
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import COOKIE, csrf_token, digest, hash_password, verify_password
from app.main import create_app, get_paypal
from app.storage import AuthSession, ClientDecision, Contract, ManualInvoiceOwner, Project, SecurityEvent, User, utc_now
from test_scope_changes import upload, create, analyze, approve, prepare, body, SCENARIOS


@lru_cache
def credentials():
    password = secrets.token_urlsafe(24)
    return password, hash_password(password)


def login(client, username="owner.one"):
    token = client.get("/api/auth/csrf").json()["csrf_token"]
    result = client.post("/api/auth/login", headers={"X-CSRF-Token": token}, json={"username": username, "password": credentials()[0]})
    assert result.status_code == 200
    client.headers["X-CSRF-Token"] = result.json()["csrf_token"]
    return result


@pytest.fixture
def secured(tmp_path):
    app = create_app(storage_url="sqlite:///" + (tmp_path / "auth.db").as_posix())
    def forbidden(): raise AssertionError("Authorization must reject before provider initialization")
    app.dependency_overrides[get_paypal] = forbidden
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        with Session(app.state.engine) as session:
            for name, role in [("owner.one", "freelancer"), ("owner.two", "freelancer"), ("client.one", "client"), ("client.two", "client")]:
                session.add(User(username=name, display_name=name, role=role, password_hash=credentials()[1]))
            session.commit()
        yield app, client


def full_order(client):
    contract = upload(client)
    request = create(client, contract, [SCENARIOS[1][0]])
    request = approve(client, analyze(client, request).json())
    order = prepare(client, request, proposed_price="125.10", currency="USD").json()
    return contract, request, order


def approve_order(client, order):
    result = client.post(f"/api/change-orders/{order['id']}/approve", json=body(order["request_revision"], expected_order_revision=order["revision"], confirmed=True, acknowledge_review_flags=True))
    assert result.status_code == 200, result.text
    return result.json()


def share(client, contract, enabled=True):
    result = client.post(f"/api/projects/{contract['project_id']}/sharing", json={"username": "client.one", "enabled": enabled, "confirmed": True})
    assert result.status_code == 200


def test_all_workspace_routes_require_authentication_before_validation_or_providers(secured):
    app, client = secured
    checked = 0
    for path, operations in app.openapi()["paths"].items():
        if not path.startswith("/api/") or path.startswith("/api/auth/"): continue
        for method in operations:
            if method not in {"get", "post", "patch", "delete"}: continue
            target = path
            for parameter in ("project_id", "contract_id", "milestone_id", "request_id", "order_id"):
                target = target.replace("{" + parameter + "}", str(uuid4()))
            target = target.replace("{invoice_id}", "INV2-UNKNOWN-1234")
            result = client.request(method, target)
            assert result.status_code == 401, (method, path, result.status_code)
            checked += 1
    assert checked >= 45


def test_password_hash_salts_and_verification():
    password, encoded = credentials()
    assert password not in encoded and encoded.startswith("scrypt$131072$8$1$")
    second = hash_password(password)
    assert second != encoded and verify_password(password, second)
    assert not verify_password("Incorrect password value", encoded)
    with pytest.raises(ValueError): hash_password("short")


def test_login_csrf_required_and_unknown_account_is_generic(secured):
    _, client = secured
    payload = {"username": "unknown.user", "password": "Incorrect password value"}
    assert client.post("/api/auth/login", json=payload).status_code == 403
    token = client.get("/api/auth/csrf").json()["csrf_token"]
    response = client.post("/api/auth/login", json=payload, headers={"X-CSRF-Token": token})
    assert response.status_code == 401 and response.json()["detail"] == "Username or password is incorrect"
    assert payload["password"] not in response.text


def test_session_cookie_rotation_logout_and_database_hash_only(secured):
    app, client = secured
    result = login(client)
    cookie = result.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=strict" in cookie and "Max-Age=28800" in cookie
    token = client.cookies[COOKIE]
    with Session(app.state.engine) as session:
        assert session.get(AuthSession, digest(token)) is not None
        assert session.get(AuthSession, token) is None
    assert client.get("/api/auth/me").headers["cache-control"] == "no-store"
    login(client)
    with Session(app.state.engine) as session: assert session.get(AuthSession, digest(token)) is None
    new_token = client.cookies[COOKIE]
    assert client.post("/api/auth/logout").status_code == 200
    assert client.get("/api/contracts").status_code == 401
    client.cookies.set(COOKIE, new_token)
    assert client.get("/api/contracts").status_code == 401


def test_secure_cookie_setting_and_expiry_and_disabled_account(secured, monkeypatch):
    app, client = secured
    monkeypatch.setenv("SCOPESHIELD_SECURE_COOKIES", "true")
    assert "Secure" in client.get("/api/auth/csrf").headers["set-cookie"]
    monkeypatch.delenv("SCOPESHIELD_SECURE_COOKIES")
    monkeypatch.setattr("app.auth.dotenv_values", lambda *args: {"SCOPESHIELD_SECURE_COOKIES": "true"})
    assert "Secure" in client.get("/api/auth/csrf").headers["set-cookie"]
    monkeypatch.setattr("app.auth.dotenv_values", lambda *args: {"SCOPESHIELD_SECURE_COOKIES": "invalid"})
    assert client.get("/api/auth/csrf").status_code == 503
    monkeypatch.setattr("app.auth.dotenv_values", lambda *args: {})
    login(client)
    with Session(app.state.engine) as session:
        session.get(AuthSession, digest(client.cookies[COOKIE])).expires_at = utc_now() - timedelta(seconds=1)
        session.commit()
    assert client.get("/api/contracts").status_code == 401
    login(client)
    with Session(app.state.engine) as session:
        session.scalar(select(User).where(User.username == "owner.one")).active = False
        session.commit()
    assert client.get("/api/contracts").status_code == 401


@pytest.mark.parametrize("header", [None, "incorrect", ""])
def test_cookie_mutations_require_bound_csrf_even_without_origin(secured, header):
    _, client = secured
    login(client)
    client.headers.pop("X-CSRF-Token")
    result = client.post("/api/contracts", headers={"X-CSRF-Token": header} if header is not None else {}, files={"file": ("safe.txt", b"Synthetic", "text/plain")})
    assert result.status_code == 403
    assert client.get("/api/contracts").json() == []


def test_no_public_registration_role_changes_or_cross_session_csrf(secured):
    _, client = secured
    login(client)
    stale_csrf = client.headers["X-CSRF-Token"]
    login(client, "client.one")
    result = client.post("/api/auth/logout", headers={"X-CSRF-Token": stale_csrf})
    assert result.status_code == 403
    assert client.patch("/api/auth/me", json={"role": "freelancer"}).status_code == 405
    token = client.get("/api/auth/csrf").json()["csrf_token"]
    assert client.post("/api/auth/login", headers={"X-CSRF-Token": token}, json={"username": "client.one", "password": credentials()[0], "role": "freelancer"}).status_code == 422
    assert client.get("/api/auth/me").json()["user"]["role"] == "client"


def test_other_owner_denied_on_every_identifier_route_and_filtered_catalogs(secured):
    app, client = secured
    login(client)
    contract, request, order = full_order(client)
    with Session(app.state.engine) as session:
        owner = session.scalar(select(User).where(User.username == "owner.one"))
        session.add(ManualInvoiceOwner(invoice_id="INV2-Mixed-1234", owner_id=owner.id))
        session.commit()
    ids = {"project_id": contract["project_id"], "contract_id": contract["id"], "request_id": request["id"], "order_id": order["id"], "invoice_id": "INV2-Mixed-1234"}
    # Add a real milestone owned by the first account.
    from contract_fixtures import sample_text
    milestone_contract = client.post("/api/contracts", files={"file": ("sample.txt", sample_text().encode(), "text/plain")}).json()
    extracted = client.post(f"/api/contracts/{milestone_contract['id']}/extract", json={"mode": "offline_demo"}).json()
    ids["milestone_id"] = extracted["milestones"][0]["id"]
    login(client, "owner.two")
    assert client.get("/api/contracts").json() == []
    assert client.get("/api/projects").json() == []
    assert client.get(f"/api/contracts?project_id={contract['project_id']}").json() == []
    checked = 0
    for path, operations in app.openapi()["paths"].items():
        if not path.startswith("/api/") or path.startswith(("/api/auth/", "/api/client/")) or "{" not in path: continue
        target = path
        for key, value in ids.items(): target = target.replace("{" + key + "}", value)
        for method in operations:
            if method not in {"get", "post", "patch", "delete"}: continue
            response = client.request(method, target)
            assert response.status_code == 404, (method, path, response.status_code)
            checked += 1
    assert checked >= 35
    result = client.post("/api/contracts", data={"project_id": contract["project_id"]}, files={"file": ("other.txt", b"Other synthetic agreement", "text/plain")})
    assert result.status_code == 404


def test_unassigned_legacy_project_is_quarantined(secured):
    app, client = secured
    with Session(app.state.engine) as session:
        project = Project()
        session.add(project); session.commit(); project_id = project.id
    login(client)
    assert client.get(f"/api/projects/{project_id}").status_code == 404
    assert client.get("/api/projects").json() == []


def test_client_only_reads_explicit_shared_dto_and_cannot_use_freelancer_routes(secured):
    _, client = secured
    login(client)
    contract, request, order = full_order(client)
    share(client, contract)
    login(client, "client.one")
    assert len(client.get("/api/client/projects").json()) == 1
    detail = client.get(f"/api/client/projects/{contract['project_id']}").json()
    assert detail["contracts"][0]["text"] == contract["text"]
    assert detail["contracts"][0]["orders"] == []  # Internal approval still required.
    assert all(key not in str(detail) for key in ["original_ai_output", "invoice_id", "reviewer_label", "password_hash"])
    for path in [f"/api/contracts/{contract['id']}", f"/api/change-orders/{order['id']}", "/api/contracts", "/api/projects"]:
        assert client.get(path).status_code == 403
    assert client.post(f"/api/change-requests/{request['id']}/analysis/approve", json={}).status_code == 403
    assert client.post("/api/invoices", json={}).status_code == 403
    assert client.post(f"/api/milestones/{uuid4()}/invoice/send", json={"confirmed": True}).status_code == 403
    assert client.get("/api/demo-scenarios").status_code == 403
    login(client, "client.two")
    assert client.get("/api/client/projects").json() == []
    assert client.get(f"/api/client/projects/{contract['project_id']}").status_code == 404


def test_client_response_internal_approval_revision_replay_revoke_and_audit(secured):
    app, client = secured
    login(client)
    contract, _, order = full_order(client)
    share(client, contract)
    login(client, "client.one")
    path = f"/api/client/change-orders/{order['id']}/respond"
    payload = {"expected_order_revision": order["revision"], "decision": "APPROVED", "note": "Reviewed synthetic proposed work", "confirmed": True}
    assert client.post(path, json=payload).status_code == 409
    login(client)
    approved = approve_order(client, order)
    login(client, "client.one")
    assert client.post(path, json=payload).status_code == 409  # stale pre-approval revision
    payload["expected_order_revision"] = approved["revision"]
    assert client.post(path, json={**payload, "confirmed": False}).status_code == 422
    result = client.post(path, json=payload)
    assert result.status_code == 201
    assert client.post(path, json=payload).status_code == 409
    login(client)
    assert client.get(f"/api/change-orders/{order['id']}").json()["status"] == "APPROVED"
    assert len(client.get(f"/api/change-orders/{order['id']}/client-responses").json()["responses"]) == 1
    share(client, contract, False)
    login(client, "client.one")
    assert client.get(f"/api/client/projects/{contract['project_id']}").status_code == 404
    assert client.post(path, json=payload).status_code == 404
    with Session(app.state.engine) as session:
        assert len(list(session.scalars(select(ClientDecision)))) == 1
        assert session.scalar(select(SecurityEvent).where(SecurityEvent.action == "CLIENT_ORDER_APPROVED")) is not None


def test_review_audit_records_authenticated_actor_independently_of_label(secured):
    _, client = secured
    login(client)
    contract = upload(client)
    events = client.get(f"/api/contracts/{contract['id']}/activity").json()
    assert events[0]["details"]["actor_id"] == client.get("/api/auth/me").json()["user"]["id"]


def test_login_throttling_survives_requests_and_wrong_password_never_leaks(secured, monkeypatch):
    _, client = secured
    monkeypatch.setattr("app.auth.verify_password", lambda *args: False)
    token = client.get("/api/auth/csrf").json()["csrf_token"]
    for _ in range(10):
        assert client.post("/api/auth/login", headers={"X-CSRF-Token": token}, json={"username": "owner.one", "password": "incorrect"}).status_code == 401
    result = client.post("/api/auth/login", headers={"X-CSRF-Token": token}, json={"username": "owner.one", "password": "incorrect"})
    assert result.status_code == 429 and result.headers["retry-after"] == "900"
