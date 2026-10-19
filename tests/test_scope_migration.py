"""Day 5 contracts, reviews and invoice attempts survive the additive migration."""
from decimal import Decimal

from alembic import command
from sqlalchemy import MetaData, text
from uuid import uuid4
from app.storage import utc_now
from sqlalchemy.orm import Session

from app.database import build_engine, initialize_database, migration_config
from app.storage import Contract, InvoiceEvent, InvoiceLink, Milestone, Project, ReviewEvent


def test_populated_day5_upgrade_preserves_all_prior_records_and_schema_matches(tmp_path):
    engine = build_engine("sqlite:///" + (tmp_path / "day5.db").as_posix())
    config = migration_config()
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "0003_invoice_links")
    # Reflect the historical schema: today's ORM includes ownership/actor columns.
    old = MetaData(); old.reflect(engine)
    project_id, contract_id, milestone_id, review_id, link_id, event_id = [str(uuid4()) for _ in range(6)]
    now = utc_now()
    with engine.begin() as connection:
        connection.execute(old.tables["projects"].insert().values(id=project_id, title="Prior synthetic project", status="DRAFT", created_at=now))
        connection.execute(old.tables["contracts"].insert().values(id=contract_id, project_id=project_id, filename="prior.txt", media_type="text/plain", size_bytes=4, sha256="a"*64, text="Prior synthetic terms", review_revision=1, status="UPLOADED", created_at=now, updated_at=now))
        connection.execute(old.tables["milestones"].insert().values(id=milestone_id, contract_id=contract_id, ordinal=1, name="Prior milestone", amount="0.30", currency="USD", deliverables=["Prior artwork"], acceptance_criteria=["Written review"], evidence={"synthetic": True}, status="APPROVED", revision=2, has_human_edits=False, updated_at=now))
        connection.execute(old.tables["review_events"].insert().values(id=review_id, contract_id=contract_id, milestone_id=milestone_id, revision=2, contract_review_revision=1, action="APPROVED", reviewer_label="Prior operator", note="Prior synthetic approval", before={}, after={"amount":"0.30"}, acknowledged_issues=[], created_at=now))
        connection.execute(old.tables["invoice_links"].insert().values(id=link_id, milestone_id=milestone_id, contract_id=contract_id, milestone_revision=2, contract_review_revision=1, idempotency_key="b"*36, request_fingerprint="c"*64, invoice_number="SS-PRIOR-SYNTHETIC", snapshot={"prior":True}, amount="0.30", currency="USD", state="RECONCILIATION_REQUIRED", verified=False, revision=1, created_at=now, updated_at=now))
        connection.execute(old.tables["invoice_events"].insert().values(id=event_id, milestone_id=milestone_id, link_id=link_id, action="CREATE_REQUESTED", reviewer_label="Prior operator", note="Synthetic attempt only", data={"prior":True}, created_at=now))
    tables = ("projects", "contracts", "milestones", "review_events", "invoice_links", "invoice_events")
    def saved():
        with engine.connect() as connection:
            return {table: [{key: row[key] for key in old.tables[table].columns.keys()} for row in connection.execute(text(f"SELECT * FROM {table}")).mappings()]
                    for table in tables}
    before = saved()
    initialize_database(engine)
    assert saved() == before
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.check(config)
        command.downgrade(config, "0003_invoice_links")
        command.upgrade(config, "head")
    assert saved() == before
    engine.dispose()
