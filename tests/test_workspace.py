"""Read-only UI catalogs, accurate activity, and exact loopback CORS rules."""
import pytest
from fastapi.testclient import TestClient

from app.main import create_app, get_paypal
from test_scope_changes import LABEL, SCENARIOS, analyze, approve, body, create, prepare
from test_contracts import upload, offline


@pytest.fixture
def workspace(tmp_path):
    app = create_app(storage_url="sqlite:///" + (tmp_path / "workspace.db").as_posix())
    def forbidden(): raise AssertionError("Workspace reads must never call PayPal")
    app.dependency_overrides[get_paypal] = forbidden
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        yield app, client


def test_catalog_lists_persisted_versions_projects_counts_and_no_contract_text(workspace):
    _, client = workspace
    assert client.get("/api/contracts").json() == [] and client.get("/api/projects").json() == []
    contract = upload(client).json()
    extracted = offline(client, contract["id"]).json()
    second = upload(client, b"New synthetic version", project_id=contract["project_id"]).json()
    rows = client.get("/api/contracts").json()
    assert [row["id"] for row in rows] == [second["id"], contract["id"]]
    assert rows[1]["milestone_count"] == 3 and rows[1]["pending_milestones"] == 3
    assert rows[1]["extraction_provider"] == "offline_synthetic_fixture" and rows[1]["live_ai"] is False
    assert "text" not in rows[1] and "extraction" not in rows[1]
    assert client.get("/api/contracts?offset=1&limit=1").json() == [rows[1]]
    assert len(client.get(f"/api/contracts?project_id={contract['project_id']}").json()) == 2
    project = client.get("/api/projects").json()[0]
    assert set(project["contract_ids"]) == {contract["id"], second["id"]}
    assert project["total_value"] == "1500.00"


@pytest.mark.parametrize("origin", ["http://127.0.0.1:3000", "http://localhost:3000"])
def test_exact_local_frontend_can_preflight_and_mutate(workspace, origin):
    _, client = workspace
    result = client.options("/api/contracts", headers={"Origin": origin, "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "content-type"})
    assert result.status_code == 200 and result.headers["access-control-allow-origin"] == origin
    assert result.headers["access-control-allow-credentials"] == "true"
    uploaded = client.post("/api/contracts", headers={"Origin": origin}, files={"file": ("local.txt", b"Synthetic local terms", "text/plain")})
    assert uploaded.status_code == 201 and uploaded.headers["access-control-allow-origin"] == origin


@pytest.mark.parametrize("origin", ["https://foreign.example", "http://localhost:3001", "http://127.0.0.1:3000.evil.example", "null", "https://localhost:3000"])
def test_other_origins_cannot_mutate_or_receive_cors_permission(workspace, origin):
    _, client = workspace
    assert client.post("/api/contracts", headers={"Origin": origin}).status_code == 403
    preflight = client.options("/api/contracts", headers={"Origin": origin, "Access-Control-Request-Method": "POST"})
    assert preflight.status_code == 400 and "access-control-allow-origin" not in preflight.headers


def test_timeline_records_exact_upload_and_extraction_events_not_guessed_times(workspace):
    _, client = workspace
    contract = upload(client).json()
    path = f"/api/contracts/{contract['id']}/activity"
    events = client.get(path).json()
    assert len(events) == 1 and events[0]["action"] == "CONTRACT_UPLOADED"
    assert events[0]["created_at"] == contract["created_at"]
    assert offline(client, contract["id"]).status_code == 200
    events = client.get(path).json()
    assert [event["action"] for event in reversed(events)] == ["CONTRACT_UPLOADED", "EXTRACTION_STARTED", "EXTRACTION_SAVED"]
    assert events[0]["details"]["live_ai"] is False and events[0]["details"]["mode"] == "offline_demo"
    # Cached extraction must not create a new provider-request event.
    assert offline(client, contract["id"]).status_code == 200 and client.get(path).json() == events
    assert client.get(path + "?offset=1&limit=1").json() == [events[1]]


@pytest.mark.parametrize("legacy", [False, True])
def test_extraction_audit_order_survives_equal_timestamps_and_pagination(workspace, legacy):
    from datetime import datetime
    from sqlalchemy import select
    from sqlalchemy.orm import Session
    from app.storage import ContractEvent
    app, client = workspace; contract = upload(client).json()
    assert offline(client, contract["id"]).status_code == 200
    instant = datetime(2026, 10, 15)
    with Session(app.state.engine) as session:
        for event in session.scalars(select(ContractEvent).where(ContractEvent.contract_id == contract["id"])):
            event.created_at = instant
            if legacy: event.data = {key: value for key, value in event.data.items() if key != "event_sequence"}
        session.commit()
    path = f"/api/contracts/{contract['id']}/activity"
    events = client.get(path).json()
    assert [event["action"] for event in events] == ["EXTRACTION_SAVED", "EXTRACTION_STARTED", "CONTRACT_UPLOADED"]
    assert len({event["created_at"] for event in events}) == 1
    assert client.get(path + "?offset=1&limit=1").json() == [events[1]]


def test_failed_extraction_keeps_sanitized_activity(workspace):
    _, client = workspace
    contract = upload(client).json()
    assert client.post(f"/api/contracts/{contract['id']}/extract", json={"mode": "configured"}).status_code == 503
    events = client.get(f"/api/contracts/{contract['id']}/activity").json()
    assert events[0]["action"] == "EXTRACTION_FAILED"
    assert events[0]["details"]["error_code"] == "AI_PROVIDER_UNAVAILABLE"
    assert "message" not in events[0]["details"]


def test_timeline_combines_real_milestone_and_scope_order_revisions(workspace):
    app, client = workspace
    contract = upload(client).json()
    extracted = offline(client, contract["id"]).json()
    milestone = extracted["milestones"][0]
    assert client.patch(f"/api/milestones/{milestone['id']}", json={**LABEL, "expected_revision": 1,
        "expected_contract_revision": 0, "changes": {"description": "Reviewed synthetic description"}}).status_code == 200
    # Comparison uses stored original text; use a proper Day 6 fixture contract.
    from app.day6_fixture import PATH
    scope_contract = upload(client, PATH.read_bytes()).json()
    row = approve(client, analyze(client, create(client, scope_contract, [SCENARIOS[1][0]])).json())
    order = prepare(client, row).json()
    from datetime import datetime
    from sqlalchemy import select
    from sqlalchemy.orm import Session
    from app.storage import ContractEvent, ScopeEvent
    with Session(app.state.engine) as session:
        for event in session.scalars(select(ContractEvent).where(ContractEvent.contract_id == scope_contract["id"])):
            event.created_at = datetime(2026, 10, 15)
        for event in session.scalars(select(ScopeEvent).where(ScopeEvent.change_request_id == row["id"])):
            event.created_at = datetime(2026, 10, 15)
        session.commit()
    events = client.get(f"/api/contracts/{scope_contract['id']}/activity").json()
    assert events[0]["action"] == "ORDER_PREPARED" and events[0]["revision"] == order["request_revision"]
    assert {event["action"] for event in events} == {"CONTRACT_UPLOADED", "REQUEST_CREATED", "ANALYSIS_STARTED", "ANALYSIS_SAVED", "ANALYSIS_APPROVED", "ORDER_PREPARED"}
    assert client.get(f"/api/contracts/{scope_contract['id']}/activity?limit=1").json() == [events[0]]
    milestone_event = client.get(f"/api/contracts/{contract['id']}/activity").json()[0]
    assert milestone_event["action"] == "MILESTONE_EDITED" and milestone_event["revision"] == 2
    assert milestone_event["details"]["changes"]["description"]["after"] == "Reviewed synthetic description"
    assert all(event["kind"] != "invoice" for event in events)


def test_workspace_activity_survives_restart(workspace):
    app, client = workspace
    contract = upload(client).json()
    offline(client, contract["id"])
    path = f"/api/contracts/{contract['id']}/activity"
    before = client.get(path).json()
    with TestClient(create_app(storage_url=str(app.state.engine.url)), base_url="http://127.0.0.1:8000") as restarted:
        assert restarted.get(path).json() == before
