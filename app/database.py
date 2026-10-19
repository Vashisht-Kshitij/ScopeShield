"""SQLAlchemy engine/session setup and versioned Alembic initialization."""
from __future__ import annotations

import os
from decimal import Decimal
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Numeric, String, create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session
from sqlalchemy.types import TypeDecorator
from fastapi import Request
from dotenv import dotenv_values
from app.storage_limits import MAX_DATABASE_BYTES

ROOT = Path(__file__).resolve().parents[1]


class Base(DeclarativeBase):
    pass


class ExactMoney(TypeDecorator):
    """SQLite uses decimal text; PostgreSQL uses NUMERIC, never a SQLite float."""
    impl = String(40)
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "sqlite":
            return dialect.type_descriptor(String(40))
        return dialect.type_descriptor(Numeric(18, 2, asdecimal=True))

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if not isinstance(value, Decimal) or not value.is_finite():
            raise ValueError("Money must be a finite Decimal")
        if value != value.quantize(Decimal("0.01")) or not 0 < value < Decimal("10000000000000000"):
            raise ValueError("Money must be positive with at most two decimal places")
        return format(value, ".2f") if dialect.name == "sqlite" else value

    def process_result_value(self, value, dialect):
        return Decimal(value) if value is not None else None


def database_url() -> str:
    configured = os.environ.get("SCOPESHIELD_DATABASE_URL")
    if not configured:
        configured = dotenv_values(ROOT / ".env").get("SCOPESHIELD_DATABASE_URL")
    return configured or "sqlite:///" + (ROOT / ".local" / "scopeshield.db").as_posix()


def build_engine(url: str) -> Engine:
    options = {"echo": False, "hide_parameters": True}
    if url.startswith("sqlite:"):
        options["connect_args"] = {"check_same_thread": False, "timeout": 30}
        from sqlalchemy.engine import make_url
        path = make_url(url).database
        if path and path != ":memory:":
            Path(path).resolve().parent.mkdir(parents=True, exist_ok=True)
        if not path or path == ":memory:":
            from sqlalchemy.pool import StaticPool
            options["poolclass"] = StaticPool
    engine = create_engine(url, **options)
    if engine.dialect.name == "sqlite":
        @event.listens_for(engine, "connect")
        def sqlite_pragmas(connection, record):
            cursor = connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            page_size = cursor.execute("PRAGMA page_size").fetchone()[0]
            pages = MAX_DATABASE_BYTES // page_size
            actual = cursor.execute(f"PRAGMA max_page_count={pages}").fetchone()[0]
            if actual > pages:
                cursor.close()
                raise RuntimeError("Database exceeds the recoverable 1 GiB limit; preserve it for operator review")
            cursor.close()
    return engine


def migration_config() -> Config:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    return config


def initialize_database(engine: Engine) -> None:
    config = migration_config()
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")


def get_db(request: Request):
    with Session(request.app.state.engine, expire_on_commit=False) as session:
        @event.listens_for(session, "before_flush")
        def record_actor(db, flush_context, instances):
            user = db.info.get("actor")
            if user:
                from app.storage import ContractEvent, InvoiceEvent, ReviewEvent, ScopeEvent
                for row in db.new:
                    if isinstance(row, (ContractEvent, InvoiceEvent, ReviewEvent, ScopeEvent)):
                        row.actor_id = user.id
        yield session
