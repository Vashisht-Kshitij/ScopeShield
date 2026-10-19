"""No cloud/guest execution. Local disposable schema and release compatibility only."""
from pathlib import Path
from decimal import Decimal
from datetime import timedelta
import hashlib
from uuid import uuid4
from alembic import command
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from app.database import build_engine, initialize_database, migration_config
from deploy.azure.host import manage
from scripts.sqlite_backup import backup, restore, inspect_database


def test_release_head_and_populated_previous_schema_upgrade_restore(tmp_path, monkeypatch):
    from app.storage import User, Project, Contract, ContractDocument, Milestone, ChangeRequest, ScopeEvent, AuthSession, utc_now
    from app.pdf_provenance import page_provenance
    from app.conversations import requested_work
    from contract_fixtures import pdf_bytes
    assert manage.release_schema() == "0009_conversations"
    path = tmp_path / "synthetic.db"; url = "sqlite:///" + path.as_posix()
    engine = build_engine(url)
    with engine.begin() as connection:
        cfg = migration_config(); cfg.attributes["connection"] = connection
        command.upgrade(cfg, "0008_pdf_provenance")
    source = "Synthetic migration agreement: one deliverable for USD 1.00."
    original = pdf_bytes(source)
    with Session(engine) as db:
        user = User(username="migration.synthetic", display_name="Synthetic migration owner", role="freelancer", password_hash="invalid-unusable-fixture-hash")
        db.add(user); db.flush()
        project = Project(title="Synthetic migration project", owner_id=user.id, status="DRAFT")
        db.add(project); db.flush()
        contract = Contract(project_id=project.id, filename="synthetic.pdf", media_type="application/pdf", size_bytes=len(original), sha256=hashlib.sha256(original).hexdigest(), text=source, page_count=1, status="UPLOADED")
        db.add(contract); db.flush()
        db.add(ContractDocument(contract_id=contract.id, data=original, provenance=page_provenance([source])))
        db.add(Milestone(contract_id=contract.id, ordinal=1, name="Migration candidate", amount=Decimal("1.00"), currency="USD", deliverables=["One synthetic deliverable"], acceptance_criteria=[], evidence={}, status="DRAFT"))
        db.add(AuthSession(token_hash="1"*64, user_id=user.id, expires_at=utc_now()+timedelta(days=1)))
        db.commit(); uid, cid, pid = user.id, contract.id, project.id
    calls = []
    monkeypatch.setattr(manage, "configuration", lambda: (None, url, path))
    monkeypatch.setattr(manage, "backup", lambda **kwargs: calls.append(kwargs))
    assert manage.migrate() == {"schema": "0009_conversations", "integrity": "ok"}
    assert calls == [{"upload": True}]
    assert manage.migrate()["schema"] == "0009_conversations"
    messages = [{"id": str(uuid4()), "sequence": 1, "sender": "Synthetic client", "role": "client", "sent_at": None, "content": "  Exact café source\nwith emoji 😀.  "}]
    with Session(engine) as db:
        request = ChangeRequest(project_id=pid, contract_id=cid, contract_sha256=hashlib.sha256(original).hexdigest(), contract_text_sha256=hashlib.sha256(source.encode()).hexdigest(), title="Synthetic stored conversation", requested_changes=requested_work(messages), conversation_messages=messages, status="PENDING")
        db.add(request); db.flush()
        db.add(ScopeEvent(actor_id=uid, change_request_id=request.id, revision=1, action="REQUEST_CREATED", reviewer_label="Synthetic owner", note="Migration preservation fixture", before={}, after={"request": {"conversation_messages": messages}}))
        db.commit(); rid = request.id
    engine.dispose()
    copy = tmp_path / "backup.db"; backup(path, copy)
    recovered = tmp_path / "recovery.db"; restore(copy, recovered)
    assert inspect_database(recovered) == "0009_conversations"
    assert (tmp_path / ".restore-pending").is_file()
    restored = build_engine("sqlite:///"+recovered.as_posix())
    try:
        with Session(restored) as db:
            assert db.get(Project, pid).owner_id == uid
            assert db.get(ContractDocument, cid).data == original
            assert db.get(ChangeRequest, rid).conversation_messages == messages
            assert db.scalar(select(Milestone.amount)) == Decimal("1.00")
            assert db.scalar(select(ScopeEvent)).after["request"]["conversation_messages"] == messages
            assert db.scalar(select(func.count()).select_from(AuthSession)) == 0
    finally: restored.dispose()


def test_rollback_requires_compatibility_before_stop_backup_or_switch():
    script = Path("deploy/azure/host/ops.sh").read_text()
    rollback = script.split("rollback)\n", 1)[1].split("export-backup)", 1)[0]
    assert rollback.index('compatible --release "$prior"') < rollback.index("systemctl stop") < rollback.index('"$helper" backup') < rollback.index("ln -s")
    assert "0008_pdf_provenance.py" not in rollback


def test_old_candidate_head_is_detected_without_executing_it(tmp_path):
    versions = tmp_path / "migrations" / "versions"; versions.mkdir(parents=True)
    (tmp_path / "alembic.ini").write_text("[alembic]\n")
    (versions / "old.py").write_text("revision='0008_pdf_provenance'\ndown_revision=None\n")
    assert manage.release_schema(tmp_path) != manage.release_schema()
