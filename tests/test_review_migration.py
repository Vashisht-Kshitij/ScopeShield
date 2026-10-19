"""Upgrade real Day 3-shaped records without losing extraction or Decimal values."""
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic import command

from app.database import build_engine, initialize_database, migration_config
from app.extraction import validate_extraction
from app.storage import utc_now
from contract_fixtures import MockExtractionProvider, sample_output, sample_text


def test_day3_data_migrates_to_review_baseline_without_losing_facts(tmp_path):
    engine = build_engine("sqlite:///" + (tmp_path / "old.db").as_posix())
    cfg = migration_config()
    with engine.begin() as connection:
        cfg.attributes["connection"] = connection
        command.upgrade(cfg, "0001_contract_storage")
    metadata = sa.MetaData()
    metadata.reflect(engine)
    project_id, contract_id, milestone_id = [str(uuid4()) for _ in range(3)]
    raw = MockExtractionProvider(sample_output())
    extracted = validate_extraction(raw.output, sample_text(), provider=raw, mode="configured").model_dump(mode="json")
    evidence = extracted["data"]["milestones"][0]
    now = utc_now()
    with engine.begin() as connection:
        connection.execute(metadata.tables["projects"].insert().values(id=project_id, title="Website Redesign", currency="USD", total_value="1500.00", status="REVIEW_REQUIRED", created_at=now))
        connection.execute(metadata.tables["contracts"].insert().values(
            id=contract_id, project_id=project_id, filename="old.txt", media_type="text/plain", size_bytes=10,
            sha256="a" * 64, text=sample_text(), status="REVIEW_REQUIRED", extraction=extracted,
            created_at=now, updated_at=now,
        ))
        connection.execute(metadata.tables["milestones"].insert().values(
            id=milestone_id, contract_id=contract_id, ordinal=1, name="Discovery and requirements",
            amount="500.00", currency="USD", payment_conditions=evidence["payment_conditions"]["value"],
            deliverables=[item["value"] for item in evidence["deliverables"]],
            acceptance_criteria=[item["value"] for item in evidence["acceptance_criteria"]], evidence=evidence, status="REVIEW_REQUIRED",
        ))
    initialize_database(engine)
    metadata = sa.MetaData()
    metadata.reflect(engine)
    with engine.connect() as connection:
        contract = connection.execute(sa.select(metadata.tables["contracts"])).mappings().one()
        milestone = connection.execute(sa.select(metadata.tables["milestones"])).mappings().one()
        assert contract["extraction"] == extracted
        assert contract["original_ai_output"] == extracted["data"] and contract["original_output_kind"] == "legacy_validated"
        assert contract["review_revision"] == 0
        assert milestone["id"] == milestone_id and milestone["amount"] == "500.00"
        assert milestone["revision"] == 1 and milestone["has_human_edits"] is False
        assert milestone["status"] == "REVIEW_REQUIRED" and milestone["reviewed_at"] is None
    engine.dispose()
