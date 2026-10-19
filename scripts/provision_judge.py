"""Provision one named, isolated judge workspace; no HTTP, AI or invoice operation.

Run in an interactive operator terminal with the backend stopped. Passwords are
hidden, never command arguments. Repeated names refuse instead of resetting data.
"""
import argparse
from getpass import getpass

from sqlalchemy import select, delete
from sqlalchemy.orm import Session

from app.auth import hash_password, normalize_username
from app.database import ROOT, build_engine, database_url, initialize_database
from app.deployment import Deployment
from app.ingestion import ingest
from app.resources import single_instance
from app.storage import AuthSession, Contract, ContractEvent, Project, SecurityEvent, User


def prepare_demo(session, user, project, contract):
    """Validated offline snapshots only. No approval, invoice, or provider history."""
    import json
    from app.day6_fixture import OfflineScopeProvider, SCENARIOS
    from app.extraction import OfflineDemoProvider, validate_extraction
    from app.scope_comparison import validate_comparison
    from app.scope_changes import snapshot, text_digest
    from app.storage import ChangeRequest, Milestone, ScopeEvent
    provider = OfflineDemoProvider()
    raw = provider.extract(contract.text)
    result = validate_extraction(raw, contract.text, provider=provider, mode="offline_demo")
    contract.status, contract.extraction = "REVIEW_REQUIRED", result.model_dump(mode="json")
    contract.original_ai_output, contract.original_output_kind = json.loads(raw), "provider_raw"
    project.currency, project.total_value, project.status = result.data.currency.value, result.data.total_value.value, "REVIEW_REQUIRED"
    for ordinal, row in enumerate(result.data.milestones, 1):
        session.add(Milestone(contract_id=contract.id, ordinal=ordinal, name=row.name.value,
            amount=row.amount.value, currency=row.currency.value, payment_conditions=row.payment_conditions.value,
            deadline=row.deadline.value, deliverables=[v.value for v in row.deliverables if v.value is not None],
            acceptance_criteria=[v.value for v in row.acceptance_criteria if v.value is not None],
            evidence=row.model_dump(mode="json"), status="REVIEW_REQUIRED"))
    session.add(ContractEvent(contract_id=contract.id, actor_id=user.id, action="EXTRACTION_SAVED",
        data={"event_sequence": 2, "synthetic_judge_seed": True, "mode": "offline_demo", "provider": provider.name, "live_ai": False}))
    scope_provider = OfflineScopeProvider()
    changes = [row[0] for row in SCENARIOS]
    scope_raw = scope_provider.compare_scope(contract.text, changes)
    analysis = validate_comparison(scope_raw, contract.text, changes, provider=scope_provider, mode="offline_demo")
    request = ChangeRequest(project_id=project.id, contract_id=contract.id, contract_sha256=contract.sha256,
        contract_text_sha256=text_digest(contract.text), title="Synthetic launch change requests · offline simulation",
        requested_changes=changes, status="REVIEW_REQUIRED", revision=1, analysis_revision=1,
        analysis=analysis.model_dump(mode="json"), original_ai_output=json.loads(scope_raw))
    session.add(request)
    session.flush()
    session.add(ScopeEvent(change_request_id=request.id, actor_id=user.id, revision=1, action="OFFLINE_DEMO_SEEDED",
        reviewer_label="Operator synthetic setup", note="Predefined offline simulation; human review remains required.",
        before={}, after=snapshot(session, request)))


def provision(session, username, password, *, prepared=False):
    username = normalize_username(username)
    if session.scalar(select(User).where(User.username == username)):
        raise ValueError("Account already exists; use password reset or provision a new named account")
    document = ingest("day10_sandbox_agreement.txt", "text/plain", (ROOT / "examples/day10_sandbox_agreement.txt").read_bytes())
    user = User(username=username, display_name=username, role="freelancer", password_hash=hash_password(password))
    session.add(user)
    session.flush()
    project = Project(owner_id=user.id, title="Judge synthetic walkthrough")
    session.add(project)
    session.flush()
    contract = Contract(project_id=project.id, filename=document.filename, media_type=document.media_type,
                        size_bytes=document.size_bytes, sha256=document.sha256, text=document.text, page_count=None)
    session.add(contract)
    session.flush()
    session.add_all([ContractEvent(contract_id=contract.id, actor_id=user.id, action="CONTRACT_UPLOADED", data={"event_sequence": 1, "synthetic_judge_seed": True}),
                     SecurityEvent(actor_id=user.id, action="JUDGE_ACCOUNT_PROVISIONED", resource_id=project.id)])
    if prepared:
        prepare_demo(session, user, project, contract)
    session.commit()
    return user.id


def revoke(session, username):
    user = session.scalar(select(User).where(User.username == normalize_username(username)))
    if not user:
        raise ValueError("Account not found")
    user.active = False
    session.execute(delete(AuthSession).where(AuthSession.user_id == user.id))
    session.add(SecurityEvent(actor_id=user.id, action="ACCOUNT_REVOKED"))
    session.commit()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["create", "revoke"])
    parser.add_argument("--username", required=True)
    parser.add_argument("--prepare-demo", action="store_true", help="Seed validated offline milestone/scope candidates; never human approvals")
    args = parser.parse_args()
    config, url = Deployment.load(), database_url()
    config.validate_storage(url)
    engine = build_engine(url)
    try:
        with single_instance(engine):
            initialize_database(engine)
            with Session(engine) as session:
                if args.action == "revoke":
                    revoke(session, args.username)
                else:
                    password = getpass("Individual judge password (12–128 characters, hidden): ")
                    if password != getpass("Confirm password (hidden): "):
                        raise ValueError("Passwords do not match")
                    provision(session, args.username, password, prepared=args.prepare_demo)
                    del password
        print("Judge account operation completed. No credentials printed; no provider calls.")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
