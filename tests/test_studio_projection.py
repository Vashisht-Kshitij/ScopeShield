"""Studio reuses the owner-only snapshot; no new authority or provider operations."""
from copy import deepcopy
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.storage import Milestone, Project, User
from test_scope_changes import api, body, upload
from test_evidence_pack import domain_state
from test_project_intelligence import projection


def test_studio_rows_keep_exact_terms_current_approval_and_original_evidence(api):
    app, client, _ = api
    c = upload(client, Path("examples/day5_sandbox_agreement.txt").read_bytes())
    c = client.post(f"/api/contracts/{c['id']}/extract", json={"mode": "offline_demo"}).json()
    m = c["milestones"][0]
    result = client.post(f"/api/milestones/{m['id']}/approve", json=body(m["revision"], expected_contract_revision=c["review_revision"], confirmed=True, acknowledge_review_flags=True))
    assert result.status_code == 200
    before = domain_state(app)
    first = projection(client, c["project_id"])
    assert domain_state(app) == before
    rows = first["intelligence"]["milestone_rows"]
    assert len(rows) == 3 and len({r["id"] for r in rows}) == 3
    assert sum(r["human_reviewed"] for r in rows) == 1
    row = next(r for r in rows if r["id"] == m["id"])
    assert row["amount"] == "1.00" and row["evidence"]
    for r in rows:
        assert r["contract_id"] == c["id"]
        for e in r["evidence"]:
            assert e["verified"] and c["text"][e["start"]:e["end"]] == e["quote"]
            assert not e["physical_pages"]  # Text source never receives invented pages.
    with Session(app.state.engine) as db:
        db.get(Milestone, m["id"]).name = "Human-edited saved terms"
        db.commit()
    changed = projection(client, c["project_id"])
    row = next(r for r in changed["intelligence"]["milestone_rows"] if r["id"] == m["id"])
    assert not row["human_reviewed"] and row["name"] == "Human-edited saved terms"
    assert all("Human-edited saved terms" not in e["quote"] for e in row["evidence"])
    assert changed["source_revision"] != first["source_revision"]


def test_studio_misassociated_and_fabricated_milestone_evidence_is_excluded(api):
    app, client, _ = api
    c = upload(client, Path("examples/day5_sandbox_agreement.txt").read_bytes())
    c = client.post(f"/api/contracts/{c['id']}/extract", json={"mode": "offline_demo"}).json()
    m = c["milestones"][0]
    with Session(app.state.engine) as db:
        item = db.get(Milestone, m["id"])
        raw = deepcopy(item.evidence)
        raw["amount"]["source_excerpt"] = "Invented amount"
        item.evidence = raw
        db.commit()
    row = next(r for r in projection(client, c["project_id"])["intelligence"]["milestone_rows"] if r["id"] == m["id"])
    assert all(e["field"] != "amount" for e in row["evidence"])


@pytest.mark.parametrize("role,status", [("anonymous", 401), ("foreign", 404), ("shared_client", 403)])
def test_studio_snapshot_denies_cross_project_and_client_access(api, role, status):
    app, client, c = api
    if role == "anonymous":
        client.cookies.clear()
    else:
        with Session(app.state.engine) as db:
            user = db.scalar(select(User).where(User.username == "domain.test"))
            if role == "shared_client":
                from app.storage import ProjectShare
                user.role = "client"
                db.add(ProjectShare(project_id=c["project_id"], client_id=user.id))
            else:
                other = User(username="studio.other", display_name="Other", role="freelancer", password_hash=user.password_hash)
                db.add(other); db.flush(); db.get(Project, c["project_id"]).owner_id = other.id
            db.commit()
    result = client.get(f"/api/projects/{c['project_id']}/evidence-pack")
    assert result.status_code == status
    assert "milestone_rows" not in result.text and "intelligence" not in result.json()
