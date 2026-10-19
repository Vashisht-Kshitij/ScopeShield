"""Historical records stay intact, and account/ownership changes use local CLI only."""
from uuid import uuid4
from alembic import command
import pytest
from sqlalchemy import MetaData, select, text
from sqlalchemy.orm import Session

from app.database import build_engine, initialize_database, migration_config
from app.storage import Project, User, utc_now
from scripts import manage_users
from test_auth import credentials


def test_populated_day7_upgrade_preserves_original_columns_and_quarantines_legacy(tmp_path):
    engine = build_engine("sqlite:///" + (tmp_path / "legacy.db").as_posix())
    config = migration_config()
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "0005_contract_activity")
    old = MetaData(); old.reflect(engine)
    project_id, contract_id = str(uuid4()), str(uuid4())
    with engine.begin() as connection:
        connection.execute(old.tables["projects"].insert().values(id=project_id, title="Historical synthetic agreement", currency="USD", total_value="0.30", status="DRAFT", created_at=utc_now()))
        connection.execute(old.tables["contracts"].insert().values(id=contract_id, project_id=project_id, filename="historical.txt", media_type="text/plain", size_bytes=9, sha256="a"*64, text="Synthetic", status="UPLOADED", review_revision=0, created_at=utc_now(), updated_at=utc_now()))
    def snapshot():
        with engine.connect() as connection:
            return {name: [dict(row) for row in connection.execute(select(table)).mappings()] for name, table in old.tables.items() if name != "alembic_version"}
    before = snapshot()
    initialize_database(engine); initialize_database(engine)
    assert snapshot() == before
    with engine.begin() as connection:
        assert connection.scalar(text("SELECT owner_id FROM projects")) is None
        assert list(connection.execute(text("PRAGMA foreign_key_check"))) == []
        config.attributes["connection"] = connection
        command.check(config)
        command.downgrade(config, "0005_contract_activity")
        command.upgrade(config, "head")
    assert snapshot() == before
    engine.dispose()


def test_local_seed_assigns_only_explicit_legacy_project_and_prints_no_password(tmp_path, monkeypatch, capsys):
    url = "sqlite:///" + (tmp_path / "cli.db").as_posix()
    monkeypatch.setenv("SCOPESHIELD_DATABASE_URL", url)
    monkeypatch.setattr(manage_users, "getpass", lambda prompt: credentials()[0])
    monkeypatch.setattr("sys.argv", ["manage_users", "create", "--username", "operator.local", "--role", "freelancer"])
    manage_users.main()
    engine = build_engine(url)
    with Session(engine) as session:
        first, second = Project(), Project()
        session.add_all([first, second]); session.commit(); project_id, second_id = first.id, second.id
    monkeypatch.setattr("sys.argv", ["manage_users", "assign-legacy", "--username", "operator.local", "--project-id", project_id])
    manage_users.main()
    with Session(engine) as session:
        user = session.scalar(select(User))
        assert session.get(Project, project_id).owner_id == user.id
        assert session.get(Project, second_id).owner_id is None
    with pytest.raises(SystemExit): manage_users.main()  # no reassignment of an owned project
    output = capsys.readouterr()
    assert credentials()[0] not in output.out + output.err
    with engine.begin() as connection:
        config = migration_config(); config.attributes["connection"] = connection
        with pytest.raises(RuntimeError, match="accounts, ownership"):
            command.downgrade(config, "0005_contract_activity")
    engine.dispose()
