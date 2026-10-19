"""Durable admission accounting; one enforced backend process per SQLite database.

Counters never contain source text, IP addresses, usernames, keys or provider bodies.
File locks, not a configurable worker-count assertion, enforce the supported topology.
"""
from contextlib import contextmanager
from datetime import timedelta
import hashlib
import os
from pathlib import Path
import threading
from uuid import uuid4

from dotenv import dotenv_values
from fastapi import HTTPException
from sqlalchemy import delete, select, update
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.exc import SQLAlchemyError

from app.database import ROOT
from app.storage import AIUsage, ResourceCounter, utc_now


def setting(name, default, minimum=1, maximum=1_000_000_000):
    raw = os.environ.get("SCOPESHIELD_" + name)
    if raw is None:
        raw = dotenv_values(ROOT / ".env").get("SCOPESHIELD_" + name, str(default))
    try:
        value = int(raw)
        if not minimum <= value <= maximum:
            raise ValueError
        return value
    except (TypeError, ValueError):
        raise HTTPException(503, {"code": "RESOURCE_CONFIGURATION_INVALID", "message": "Resource limits are invalid; ask the operator to check configuration"}) from None


def denied(code="RATE_LIMITED", retry=60):
    return HTTPException(429, {"code": code, "message": "Usage limit reached. Wait before trying again; saved results remain available.",
                               "retry_after_seconds": retry}, headers={"Retry-After": str(retry)})


@contextmanager
def transaction(engine):
    try:
        with engine.connect() as connection:
            connection.exec_driver_sql("PRAGMA busy_timeout=1000")
            try:
                connection.exec_driver_sql("BEGIN IMMEDIATE")
                yield connection
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
            finally:
                connection.exec_driver_sql("PRAGMA busy_timeout=30000")
    except SQLAlchemyError:
        raise HTTPException(503, {"code": "RESOURCE_STORAGE_BUSY", "message": "Usage accounting is busy. No new provider request was authorized; refresh the saved record before retrying."}, headers={"Retry-After": "2"}) from None


def reserve(connection, key, amount, limit, reset_at, *, now=None):
    now = now or utc_now()
    connection.execute(delete(ResourceCounter).where(ResourceCounter.key == key, ResourceCounter.reset_at <= now))
    statement = insert(ResourceCounter).values(key=key, used=amount, reset_at=reset_at)
    statement = statement.on_conflict_do_update(index_elements=[ResourceCounter.key],
        set_={"used": ResourceCounter.used + amount}, where=ResourceCounter.used + amount <= limit)
    if amount > limit or connection.execute(statement).rowcount != 1:
        raise denied(retry=max(1, int((reset_at - now).total_seconds()) + 1))


def rate(engine, category, identity, limit, seconds=60):
    now = utc_now()
    # Fixed UTC windows, shared by every route/resource ID in this category.
    epoch = int(now.timestamp())
    reset = now + timedelta(seconds=seconds - epoch % seconds)
    key = category + ":" + hashlib.sha256(identity.encode()).hexdigest()
    with transaction(engine) as connection:
        reserve(connection, key, 1, limit, reset, now=now)
        connection.execute(delete(ResourceCounter).where(ResourceCounter.reset_at < now - timedelta(days=2)))


class Runtime:
    def __init__(self, engine):
        self.engine = engine
        self.slots = {name: threading.BoundedSemaphore(setting(name.upper() + "_CONCURRENCY", count, maximum=16))
                      for name, count in (("ai", 2), ("pdf", 1), ("password", 2), ("body", 4))}
        self.user_slots = set()
        self.mutex = threading.Lock()

    @contextmanager
    def slot(self, category, user=None):
        semaphore = self.slots[category]
        if not semaphore.acquire(blocking=False):
            raise denied("RESOURCE_BUSY", 5)
        acquired = False
        try:
            with self.mutex:
                if user is not None and user in self.user_slots:
                    raise denied("AI_CONCURRENCY_LIMIT", 5)
                if user is not None:
                    self.user_slots.add(user)
                    acquired = True
            yield
        finally:
            if acquired:
                with self.mutex:
                    self.user_slots.remove(user)
            semaphore.release()


_instances = {}
_instance_mutex = threading.Lock()


@contextmanager
def single_instance(engine):
    if engine.dialect.name != "sqlite":
        raise RuntimeError("Resource controls support one backend process with local SQLite only")
    database = engine.url.database
    if not database or database == ":memory:" or engine.url.query:
        raise RuntimeError("Resource controls require a persistent local SQLite file without URI options")
    key = str(Path(database).resolve()) if database and database != ":memory:" else str(id(engine))
    with _instance_mutex:
        if key in _instances:
            entry = _instances[key]
            entry[2] += 1
        else:
            handle = None
            if database and database != ":memory:":
                handle = open(key + ".instance.lock", "a+b")
                try:
                    handle.seek(0)
                    if os.name == "nt":
                        import msvcrt
                        if not handle.read(1):
                            handle.write(b"0")
                            handle.flush()
                        handle.seek(0)
                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError:
                    handle.close()
                    raise RuntimeError("A backend already owns this database. Run exactly one backend process; multiple workers/replicas are unsupported.") from None
            try:
                entry = [Runtime(engine), handle, 1]
            except BaseException:
                if handle:
                    handle.close()
                raise
            _instances[key] = entry
    try:
        yield entry[0]
    finally:
        with _instance_mutex:
            entry[2] -= 1
            if not entry[2]:
                if entry[1]:
                    entry[1].close()
                del _instances[key]


class AttemptBudget:
    def __init__(self, engine, user_id):
        self.engine, self.user_id = engine, user_id

    def start(self, model, input_tokens, output_tokens):
        from app.extraction import ExtractionError
        now = utc_now()
        reset = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        attempt = str(uuid4())
        try:
            with transaction(self.engine) as connection:
                cooldown = connection.execute(select(ResourceCounter.reset_at).where(ResourceCounter.key == "ai:cooldown", ResourceCounter.reset_at > now)).scalar()
                if cooldown:
                    raise denied("AI_QUOTA_COOLDOWN", max(1, int((cooldown - now).total_seconds()) + 1))
                for identity, prefix, requests, tokens in (("app", "AI_APP", 60, 1_000_000), (self.user_id, "AI_USER", 20, 250_000)):
                    reserve(connection, "ai:requests:" + identity, 1, setting(prefix + "_DAILY_REQUESTS", requests, minimum=0), reset, now=now)
                    reserve(connection, "ai:tokens:" + identity, input_tokens + output_tokens, setting(prefix + "_DAILY_TOKENS", tokens, minimum=0), reset, now=now)
                connection.execute(insert(AIUsage).values(id=attempt, user_id=self.user_id, model=model,
                    reserved_input=input_tokens, reserved_output=output_tokens, created_at=now, outcome="STARTED"))
        except HTTPException as exc:
            code = ("AI_QUOTA_COOLDOWN" if isinstance(exc.detail, dict) and exc.detail.get("code") == "AI_QUOTA_COOLDOWN"
                    else "AI_BUDGET_EXCEEDED" if exc.status_code == 429 else "AI_BUDGET_UNAVAILABLE")
            error = ExtractionError(code, "AI admission denied", exc.status_code)
            error.retry_after = int(exc.headers.get("Retry-After", "60")) if exc.headers else 60
            raise error from None
        return attempt

    def finish(self, attempt, metadata):
        with transaction(self.engine) as connection:
            connection.execute(update(AIUsage).where(AIUsage.id == attempt).values(
                completed_at=utc_now(), outcome="FAILED" if metadata.get("error_code") else "SUCCEEDED",
                error_code=metadata.get("error_code"), reported_usage=metadata.get("token_usage"),
                http_status=metadata.get("http_status")))
            if metadata.get("error_code") == "AI_QUOTA_EXCEEDED":
                statement = insert(ResourceCounter).values(key="ai:cooldown", used=1,
                    reset_at=utc_now() + timedelta(seconds=setting("AI_QUOTA_COOLDOWN_SECONDS", 60, maximum=3600)))
                connection.execute(statement.on_conflict_do_update(index_elements=[ResourceCounter.key],
                    set_={"reset_at": statement.excluded.reset_at}))


@contextmanager
def ai_operation(session, provider, runtime):
    if not provider.live_ai:
        yield
        return
    from app.auth import actor
    from app.extraction import ExtractionError
    try:
        with runtime.slot("ai", actor(session).id):
            provider.budget = AttemptBudget(session.get_bind(), actor(session).id)
            yield
    except HTTPException as exc:
        error = ExtractionError("AI_CONCURRENCY_LIMIT" if exc.status_code == 429 else "AI_BUDGET_UNAVAILABLE", "AI work could not be admitted", exc.status_code)
        error.retry_after = int((exc.headers or {}).get("Retry-After", "5"))
        raise error from None


def error_headers(error):
    return {"Retry-After": str(getattr(error, "retry_after", 60))} if error.status_code == 429 else {}
