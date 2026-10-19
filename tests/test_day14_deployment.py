"""Public-profile security and durable recovery, isolated data and no providers."""
from datetime import timedelta
from functools import lru_cache
import os
import hashlib
from pathlib import Path
import secrets
from decimal import Decimal
import sqlite3

from alembic import command
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import build_engine, migration_config
from app.deployment import Deployment, public_origin
from app.main import create_app, get_paypal
from app.resources import AttemptBudget
from app.storage import ChangeRequest, Contract, InvoiceLink, User, utc_now
from scripts.provision_judge import provision, revoke
from scripts.sqlite_backup import backup, restore, inspect_database
from test_milestone_invoices import approve


@pytest.fixture(autouse=True)
def authenticated_legacy_clients():
    # This security suite deliberately uses real anonymous/login/CSRF requests.
    yield


@lru_cache
def password():
    return secrets.token_urlsafe(24)


def login(client, name="judge.one"):
    token = client.get("/api/auth/csrf").json()["csrf_token"]
    result = client.post("/api/auth/login", json={"username": name, "password": password()}, headers={"X-CSRF-Token": token})
    assert result.status_code == 200, result.text
    client.headers["X-CSRF-Token"] = result.json()["csrf_token"]
    return result


@pytest.fixture
def public_config(monkeypatch, tmp_path):
    directory = tmp_path / "volume"
    directory.mkdir()
    (directory / ".scopeshield-volume").write_text("synthetic-volume-identity")
    for name, value in dict(ENV="production", FRONTEND_URL="https://demo.example.test", BACKEND_URL="https://demo.example.test",
                            SECURE_COOKIES="true", DEMO_MODE="true", TRUSTED_PROXIES="127.0.0.1", AI_PROVIDER="disabled",
                            AI_ALLOW_LIVE="false", DATA_DIR=str(directory), STORAGE_ID="synthetic-volume-identity").items():
        monkeypatch.setenv("SCOPESHIELD_" + name, value)
    # Windows temporary directories are not block volumes. Only mount detection is
    # simulated here; the negative mount test below exercises the real rejection.
    monkeypatch.setattr(os.path, "ismount", lambda path: Path(path) == directory)
    return "sqlite:///" + (directory / "public.db").as_posix()


@pytest.fixture
def public_app(public_config):
    app = create_app(storage_url=public_config)
    def forbidden():
        raise AssertionError("No public request may initialize PayPal")
    app.dependency_overrides[get_paypal] = forbidden
    with TestClient(app, base_url="https://demo.example.test", client=("127.0.0.1", 45000)) as client:
        with Session(app.state.engine) as db:
            provision(db, "judge.one", password())
            provision(db, "judge.two", password())
        yield app, client


@pytest.mark.parametrize("origin", ["http://demo.example.com", "https://*.example.com", "https://user:secret@demo.example.com",
    "https://demo.example.com/path", "https://demo.example.com?next=evil", "https://demo.example.com#x", "https://demo.example.com:443",
    "https://127.0.0.1", "https://localhost", "https://demo.local", "https://-bad.example.com", "https://demo.example.com.",
    "https://DEMO.example.com", "//demo.example.com", "https://demo.example.com\\@evil.test"])
def test_public_url_rejects_unsafe_origins(origin):
    with pytest.raises(ValueError):
        public_origin(origin)


def test_valid_origin_and_fail_closed_configuration(public_config, monkeypatch):
    assert public_origin("https://demo.example.test/") == "https://demo.example.test"
    assert Deployment.load().hosts == ["demo.example.test"]
    for key, bad in [("BACKEND_URL", "https://api.example.test"), ("SECURE_COOKIES", "false"),
                     ("DEMO_MODE", "false"), ("AI_PROVIDER", "gemini"), ("AI_ALLOW_LIVE", "true"),
                     ("TRUSTED_PROXIES", "*"), ("TRUSTED_PROXIES", "127.0.0.0/8")]:
        with monkeypatch.context() as scoped:
            scoped.setenv("SCOPESHIELD_" + key, bad)
            with pytest.raises(ValueError): Deployment.load()


def test_storage_rejects_missing_mount_marker_ephemeral_or_restored(public_config, monkeypatch, tmp_path):
    config = Deployment.load()
    config.validate_storage(public_config)
    with monkeypatch.context() as scoped:
        scoped.setattr(os.path, "ismount", lambda _: False)
        with pytest.raises(ValueError): config.validate_storage(public_config)
    for url in ["sqlite:///:memory:", "sqlite:///relative.db", "sqlite:///" + (tmp_path / "elsewhere.db").as_posix(), "postgresql://localhost/test"]:
        with pytest.raises(ValueError): config.validate_storage(url)
    directory = Path(os.environ["SCOPESHIELD_DATA_DIR"])
    (directory / ".scopeshield-volume").write_text("wrong-volume-identity")
    with pytest.raises(ValueError): config.validate_storage(public_config)
    (directory / ".scopeshield-volume").write_text("synthetic-volume-identity")
    (directory / ".restore-pending").write_text("pending")
    with pytest.raises(ValueError): config.validate_storage(public_config)


def test_https_cookie_host_origin_csrf_and_no_redirects(public_app):
    app, client = public_app
    preflight = client.get("/api/auth/csrf")
    cookie = preflight.headers["set-cookie"].lower()
    assert "secure" in cookie and "httponly" in cookie and "samesite=strict" in cookie and "domain=" not in cookie
    result = login(client)
    assert result.json()["demo_mode"] is True
    assert "secure" in result.headers["set-cookie"].lower()
    for host in ["evil.example.test", "demo.example.test.evil.test", "demo.example.test:8443", "www.demo.example.test"]:
        denied = client.get("/health", headers={"Host": host}, follow_redirects=False)
        assert denied.status_code == 400 and "location" not in denied.headers
    for origin in ["https://evil.example.test", "null", "http://demo.example.test", "https://demo.example.test.evil.test"]:
        response = client.post("/api/auth/logout", json={}, headers={"Origin": origin})
        assert response.status_code == 403
        assert "access-control-allow-origin" not in response.headers
    response = client.post("/api/auth/logout", json={}, headers={"Origin": "https://demo.example.test", "X-CSRF-Token": "wrong"})
    assert response.status_code == 403
    assert client.get("/api/auth/me").status_code == 200
    response = client.post("/api/auth/logout", json={}, headers={"Origin": "https://demo.example.test"})
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["strict-transport-security"] == "max-age=31536000"
    assert response.headers["x-frame-options"] == "DENY"
    assert client.get("/api/auth/me").status_code == 401
    assert client.get("/api/auth/me/", follow_redirects=False).status_code == 404


def test_proxy_assertions_require_exact_socket_peer_and_single_ip(public_app):
    app, client = public_app
    good = {"X-Forwarded-For": "192.0.2.25", "X-Forwarded-Proto": "https"}
    response = client.get("http://demo.example.test/health", headers=good)
    assert response.status_code == 200
    assert client.get("http://demo.example.test/health").status_code == 400
    for headers in [dict(good, Forwarded="for=192.0.2.25"), dict(good, **{"X-Real-IP": "192.0.2.25"}),
                    dict(good, **{"X-Forwarded-Host": "evil.test"}), dict(good, **{"X-Forwarded-For": "192.0.2.25, 127.0.0.1"}),
                    dict(good, **{"X-Forwarded-Proto": "http"}), {"X-Forwarded-For": "192.0.2.25"}]:
        assert client.get("/health", headers=headers).status_code == 400
    with TestClient(app, base_url="https://demo.example.test", client=("192.0.2.99", 55000)) as untrusted:
        assert untrusted.get("/health", headers=good).status_code == 400


def test_public_judge_isolation_provider_gates_and_diagnostics(public_app, monkeypatch):
    app, client = public_app
    assert client.get("/api/contracts").status_code == 401
    login(client)
    own = client.get("/api/contracts").json()
    assert len(own) == 1
    cid = own[0]["id"]
    # An accidental runtime provider opt-in still cannot bypass the public profile.
    monkeypatch.setenv("SCOPESHIELD_AI_PROVIDER", "gemini")
    monkeypatch.setenv("SCOPESHIELD_AI_ALLOW_LIVE", "true")
    result = client.post(f"/api/contracts/{cid}/extract", json={"mode": "configured"})
    assert result.status_code == 403
    assert client.get(f"/api/contracts/{cid}").json()["status"] == "UPLOADED"
    result = client.post(f"/api/contracts/{cid}/extract", json={"mode": "offline_demo"})
    assert result.status_code == 200 and result.json()["extraction"]["live_ai"] is False
    milestone = approve(client, cid)
    for method, path in [("GET", f"/api/milestones/{milestone['id']}/invoice/preview"),
                         ("POST", f"/api/milestones/{milestone['id']}/invoice"),
                         ("POST", f"/api/milestones/{milestone['id']}/invoice/send"),
                         ("POST", "/api/invoices"), ("GET", "/api/invoices/INV2-NOTREAL-1234")]:
        assert client.request(method, path, json={} if method == "POST" else None).status_code == 403
    for path in ["/docs", "/redoc", "/openapi.json", "/.env", "/.local/scopeshield.db", "/.git/config"]:
        response = client.get(path)
        assert response.status_code == 404
        assert "Traceback" not in response.text and str(Path.cwd()) not in response.text
    with Session(app.state.engine) as db:
        assert db.scalar(select(InvoiceLink)) is None
    # Real database pagination, including a 101st saved request and newest-first selection.
    with Session(app.state.engine) as db:
        contract = db.get(Contract, cid)
        for index in range(101):
            db.add(ChangeRequest(project_id=contract.project_id, contract_id=cid, contract_sha256=contract.sha256,
                contract_text_sha256=hashlib.sha256(contract.text.encode()).hexdigest(),
                title=f"Synthetic request {index:03}", requested_changes=["Synthetic additional work"],
                created_at=utc_now() + timedelta(seconds=index)))
        db.commit()
        with pytest.raises(ValueError): provision(db, "judge.one", password())
    first = client.get(f"/api/contracts/{cid}/change-requests?limit=100&newest=true").json()
    older = client.get(f"/api/contracts/{cid}/change-requests?limit=100&newest=true&offset=100").json()
    assert len(first) == 100 and first[0]["title"] == "Synthetic request 100"
    assert len(older) == 1 and older[0]["title"] == "Synthetic request 000"
    target = first[0]
    denied = client.post(f"/api/change-requests/{target['id']}/analyze", json={
        "mode": "configured", "expected_revision": target["revision"], "reviewer_label": "Synthetic judge", "note": "No live request permitted"})
    assert denied.status_code == 403
    assert client.get(f"/api/change-requests/{target['id']}").json()["status"] == "PENDING"
    login(client, "judge.two")
    assert len(client.get("/api/contracts").json()) == 1
    assert client.get(f"/api/contracts/{cid}").status_code == 404
    with Session(app.state.engine) as db:
        revoke(db, "judge.two")
    assert client.get("/api/auth/me").status_code == 401


def test_restart_backup_restore_preserves_reviews_usage_reservations_and_sessions(public_app, public_config, tmp_path):
    app, client = public_app
    login(client)
    cid = client.get("/api/contracts").json()[0]["id"]
    assert client.post(f"/api/contracts/{cid}/extract", json={"mode": "offline_demo"}).status_code == 200
    milestone = approve(client, cid)
    with Session(app.state.engine) as db:
        user = db.scalar(select(User).where(User.username == "judge.one"))
        AttemptBudget(app.state.engine, user.id).start("synthetic-no-network", 10, 20)
        # Seed an uncertain reservation directly; no provider or create route used.
        db.add(InvoiceLink(milestone_id=milestone["id"], contract_id=cid, idempotency_key=secrets.token_hex(16),
            request_fingerprint="a" * 64, invoice_number="SS-SYNTHETIC-RESTORE", snapshot={"name": "Synthetic reservation", "description": "No invoice exists"},
            amount=Decimal("1.00"), currency="USD", milestone_revision=milestone["revision"],
            contract_review_revision=milestone["contract_review_revision"], state="CREATING"))
        db.commit()
    saved = client.get(f"/api/contracts/{cid}").json()
    archive = tmp_path / "backup.db"
    assert backup(Path(app.state.engine.url.database), archive)["integrity"] == "ok"
    with TestClient(create_app(storage_url=public_config), base_url="https://demo.example.test") as restarted:
        restarted.cookies.update(client.cookies)
        assert restarted.get("/api/auth/me").status_code == 200
        assert restarted.get(f"/api/contracts/{cid}").json() == saved
    target_dir = tmp_path / "recovery"
    target_dir.mkdir()
    target = target_dir / "restored.db"
    assert restore(archive, target)["quarantined"] is True
    assert (target_dir / ".restore-pending").exists()
    with sqlite3.connect(target) as db:
        assert db.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 2
        assert db.execute("SELECT COUNT(*) FROM auth_sessions").fetchone()[0] == 0
        assert db.execute("SELECT status FROM milestones WHERE id=?", (milestone["id"],)).fetchone()[0] == "APPROVED"
        assert db.execute("SELECT state FROM invoice_links").fetchone()[0] == "CREATING"
        assert db.execute("SELECT COUNT(*) FROM ai_usage").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM resource_counters").fetchone()[0] > 0
        assert db.execute("SELECT COUNT(*) FROM review_events").fetchone()[0] > 0
    with pytest.raises(ValueError): restore(archive, target)
    with pytest.raises(FileExistsError): backup(Path(app.state.engine.url.database), archive)
    config = migration_config()
    engine = build_engine("sqlite:///" + target.as_posix())
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
        command.check(config)
    engine.dispose()
    assert inspect_database(target) == "0009_conversations"


def test_public_conversation_uses_offline_review_and_blocks_live_provider_before_resolution(public_app, monkeypatch):
    from app.conversation_fixture import catalog
    app, client = public_app
    login(client)
    example = catalog()[1]
    response = client.post("/api/contracts", files={"file": (example["filename"], example["text"].encode(), "text/plain")})
    assert response.status_code == 201
    contract = response.json()
    label = {"reviewer_label": "Synthetic judge", "note": "Review the synthetic conversation sources"}
    response = client.post(f"/api/contracts/{contract['id']}/change-requests", json={**label,
        "project_id": contract["project_id"], "title": example["title"], "conversation_messages": example["conversation_messages"]})
    assert response.status_code == 201
    row = response.json()
    def forbidden(): raise AssertionError("Public conversations must not initialize a live provider")
    monkeypatch.setattr("app.scope_changes.get_scope_provider", forbidden)
    path = f"/api/change-requests/{row['id']}/analyze"
    assert client.post(path, json={**label, "expected_revision": row["revision"], "mode": "configured"}).status_code == 403
    result = client.post(path, json={**label, "expected_revision": row["revision"], "mode": "offline_demo"})
    assert result.status_code == 200 and result.json()["analysis"]["live_ai"] is False
    assert result.json()["status"] == "REVIEW_REQUIRED"
