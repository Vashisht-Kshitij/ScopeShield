"""Adversarial admission, resource isolation and failure tests. No external HTTP."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from io import BytesIO
import json
import subprocess
import sys
from threading import Event

from fastapi import HTTPException
import pytest
import requests
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.database import build_engine
from app.extraction import ExtractionError, get_extraction_provider
from app.gemini import DEFAULT_MODELS, GeminiModelRouter
from app.ingestion import ingest, UploadBodyLimit
from app.resources import AttemptBudget, rate
from app.storage import AIUsage, Contract, InvoiceLink, ResourceCounter, User, utc_now
from contract_fixtures import pdf_bytes, sample_text
from test_contracts import api, upload
from test_gemini import http, ProviderResponse, envelope
from test_scope_changes import api as scope_api, analyze, create, configured, output


def router(two=False):
    models = DEFAULT_MODELS if two else DEFAULT_MODELS[:1]
    return GeminiModelRouter("dummy_day13_key", models=models, free_tier_models=models, allow_live=True, max_model_calls=len(models))


def enable(app, two=False):
    app.dependency_overrides[get_extraction_provider] = lambda: router(two)


def extract(client, cid):
    return client.post(f"/api/contracts/{cid}/extract", json={"mode": "configured", "force": True})


def owner(app):
    with Session(app.state.engine) as session:
        return session.scalar(select(User.id))


@pytest.mark.parametrize("status,code", [(400, "AI_REQUEST_INVALID"), (403, "AI_AUTH_FAILED"),
    (404, "AI_MODEL_UNAVAILABLE"), (429, "AI_QUOTA_EXCEEDED"), (500, "AI_PROVIDER_FAILED"), (503, "AI_MODELS_UNAVAILABLE")])
def test_provider_errors_charge_failed_attempt_and_preserve_snapshot(api, http, status, code):
    app, client = api
    cid = upload(client).json()["id"]
    before = client.post(f"/api/contracts/{cid}/extract", json={"mode": "offline_demo"}).json()
    enable(app)
    http[0].append(ProviderResponse(status, body={"error": {"message": "secret source raw stack"}}))
    failed = extract(client, cid)
    assert failed.status_code in {429, 502, 503}
    assert failed.json()["detail"]["code"] == code and "secret source" not in failed.text
    assert len(http[1]) == 1
    saved = client.get(f"/api/contracts/{cid}").json()
    assert saved["status"] == "REVIEW_REQUIRED" and saved["milestones"] == before["milestones"]
    with Session(app.state.engine) as session:
        usage = session.scalar(select(AIUsage))
        assert usage.outcome == "FAILED" and usage.http_status == status and usage.reserved_output == 8192
        assert session.scalar(select(func.count()).select_from(InvoiceLink)) == 0


@pytest.mark.parametrize("failure,code", [(requests.Timeout(), "AI_TIMEOUT"), (requests.ConnectionError(), "AI_PROVIDER_FAILED")])
def test_uncertain_transport_is_charged_without_fallback(api, http, failure, code):
    app, client = api
    enable(app, two=True)
    cid = upload(client).json()["id"]
    http[0].append(failure)
    assert extract(client, cid).json()["detail"]["code"] == code
    assert len(http[1]) == 1
    with Session(app.state.engine) as session:
        usage = session.scalar(select(AIUsage))
        assert usage.outcome == "FAILED" and usage.error_code == code


def test_unexpected_transport_exception_is_failed_and_sanitized(api, http):
    app, client = api
    enable(app, two=True)
    cid = upload(client).json()["id"]
    http[0].append(RuntimeError("provider secret body"))
    result = extract(client, cid)
    assert result.status_code == 502 and "provider secret body" not in result.text
    assert len(http[1]) == 1
    with Session(app.state.engine) as session:
        usage = session.scalar(select(AIUsage))
        assert usage.outcome == "FAILED" and usage.error_code == "AI_PROVIDER_FAILED"


def test_invalid_ai_input_setting_does_not_hide_saved_agreement(api, monkeypatch):
    _, client = api
    cid = upload(client).json()["id"]
    monkeypatch.setenv("SCOPESHIELD_AI_MAX_INPUT_TOKENS", "invalid")
    saved = client.get(f"/api/contracts/{cid}")
    assert saved.status_code == 200 and saved.json()["ai_input_eligible"] is False
    assert "settings are invalid" in saved.json()["ai_input_message"]
    assert extract(client, cid).status_code == 503


def test_daily_budget_blocks_force_and_fallback_without_another_post(api, http, monkeypatch):
    app, client = api
    monkeypatch.setenv("SCOPESHIELD_AI_USER_DAILY_REQUESTS", "1")
    enable(app, two=True)
    cid = upload(client).json()["id"]
    http[0].append(ProviderResponse(503, body={"error": {}}))
    first = extract(client, cid)
    assert first.status_code == 429 and first.json()["detail"]["code"] == "AI_BUDGET_EXCEEDED"
    assert int(first.headers["retry-after"]) > 0 and len(http[1]) == 1
    for _ in range(2):
        assert extract(client, cid).status_code == 429
    assert len(http[1]) == 1
    with Session(app.state.engine) as session:
        assert session.scalar(select(func.count()).select_from(AIUsage)) == 1
        assert session.get(ResourceCounter, "ai:requests:app").used == 1


def test_success_metadata_and_failed_schema_are_both_accounted(api, http):
    app, client = api
    enable(app)
    cid = upload(client).json()["id"]
    response = envelope()
    response["usageMetadata"] = {"promptTokenCount": 100, "candidatesTokenCount": 20, "totalTokenCount": 120}
    http[0].append(ProviderResponse(body=response))
    assert extract(client, cid).status_code == 200
    http[0].append(ProviderResponse(body=envelope({"unexpected": "not the required schema"})))
    assert extract(client, cid).status_code == 502
    with Session(app.state.engine) as session:
        rows = session.scalars(select(AIUsage).order_by(AIUsage.created_at)).all()
        assert [row.outcome for row in rows] == ["SUCCEEDED", "FAILED"]
        assert rows[0].reported_usage["totalTokenCount"] == 120
        assert rows[1].error_code == "AI_INVALID_OUTPUT"
        assert session.get(ResourceCounter, "ai:requests:app").used == 2


def test_provider_quota_cooldown_is_durable_and_no_fallback(api, http):
    app, client = api
    enable(app, two=True)
    cid = upload(client).json()["id"]
    http[0].append(ProviderResponse(429, body={"error": {}}))
    assert extract(client, cid).status_code == 429
    again = extract(client, cid)
    assert again.json()["detail"]["code"] == "AI_QUOTA_COOLDOWN" and len(http[1]) == 1
    with Session(app.state.engine) as session:
        assert session.get(ResourceCounter, "ai:cooldown").reset_at > utc_now()


@pytest.mark.parametrize("limit", ["SCOPESHIELD_AI_APP_DAILY_REQUESTS", "SCOPESHIELD_AI_USER_DAILY_REQUESTS", "SCOPESHIELD_AI_USER_DAILY_TOKENS"])
def test_atomic_last_allowance_across_independent_connections(api, monkeypatch, limit):
    app, _ = api
    monkeypatch.setenv(limit, "200" if limit.endswith("TOKENS") else "1")
    uid = owner(app)
    def attempt(_):
        engine = build_engine(str(app.state.engine.url))
        try:
            return AttemptBudget(engine, uid).start(DEFAULT_MODELS[0], 100, 100)
        except ExtractionError as exc:
            assert exc.code == "AI_BUDGET_EXCEEDED"
            return None
        finally:
            engine.dispose()
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(attempt, range(8)))
    assert sum(item is not None for item in results) == 1
    with Session(app.state.engine) as session:
        assert session.get(ResourceCounter, "ai:requests:app").used == 1
        assert session.scalar(select(func.count()).select_from(AIUsage)) == 1


def test_app_budget_shared_between_accounts_and_failed_reservation_rolls_back(api, monkeypatch):
    app, _ = api
    uid = owner(app)
    with Session(app.state.engine) as session:
        other = User(username="other.budget", display_name="Other", role="freelancer", password_hash="unused")
        session.add(other)
        session.commit()
        other_id = other.id
    monkeypatch.setenv("SCOPESHIELD_AI_APP_DAILY_REQUESTS", "1")
    AttemptBudget(app.state.engine, uid).start(DEFAULT_MODELS[0], 100, 100)
    with pytest.raises(ExtractionError):
        AttemptBudget(app.state.engine, other_id).start(DEFAULT_MODELS[0], 100, 100)
    with Session(app.state.engine) as session:
        assert session.get(ResourceCounter, "ai:requests:" + other_id) is None


def test_output_and_input_reservations_checked_before_network(api, http, monkeypatch):
    app, client = api
    enable(app)
    cid = upload(client).json()["id"]
    monkeypatch.setenv("SCOPESHIELD_AI_MAX_INPUT_TOKENS", "1")
    assert extract(client, cid).status_code == 413 and not http[1]
    assert client.get(f"/api/contracts/{cid}").json()["status"] == "UPLOADED"
    monkeypatch.setenv("SCOPESHIELD_AI_MAX_INPUT_TOKENS", "32768")
    monkeypatch.setenv("SCOPESHIELD_AI_MAX_OUTPUT_TOKENS", "100")
    http[0].append(ProviderResponse(body=envelope()))
    assert extract(client, cid).status_code == 200
    assert http[1][0][2]["json"]["generationConfig"]["maxOutputTokens"] == 100


def test_scope_and_extraction_share_same_account_allowance(scope_api, http, monkeypatch):
    app, client, contract = scope_api
    monkeypatch.setenv("SCOPESHIELD_AI_USER_DAILY_REQUESTS", "1")
    configured(monkeypatch, router())
    row = create(client, contract)
    http[0].append(ProviderResponse(body=envelope(output())))
    assert analyze(client, row, "configured").status_code == 200
    enable(app)
    assert extract(client, contract["id"]).status_code == 429
    assert len(http[1]) == 1


def test_busy_ai_rejects_same_user_on_other_contract_before_network(api, http, monkeypatch):
    app, client = api
    enable(app)
    ids = [upload(client).json()["id"] for _ in range(2)]
    entered, release = Event(), Event()
    def request(*args, **kwargs):
        entered.set()
        assert release.wait(15)
        return ProviderResponse(body=envelope())
    monkeypatch.setattr(requests, "request", request)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(extract, client, ids[0])
        try:
            assert entered.wait(15)
            response = extract(client, ids[1])
            assert response.status_code == 429 and response.json()["detail"]["code"] == "AI_CONCURRENCY_LIMIT"
        finally:
            release.set()
        assert pending.result().status_code == 200
    assert not app.state.resources.user_slots


def test_second_backend_process_is_rejected_and_budget_survives_restart(api):
    app, _ = api
    AttemptBudget(app.state.engine, owner(app)).start(DEFAULT_MODELS[0], 100, 100)
    code = "from app.database import build_engine; from app.resources import single_instance; import sys; e=build_engine(sys.argv[1]);\nwith single_instance(e): print('unexpected second instance')"
    result = subprocess.run([sys.executable, "-B", "-c", code, str(app.state.engine.url)], capture_output=True, timeout=30)
    assert result.returncode != 0 and b"backend already owns" in result.stderr
    # A new engine models a fresh process's durable accounting view.
    engine = build_engine(str(app.state.engine.url))
    try:
        with Session(engine) as session:
            assert session.get(ResourceCounter, "ai:requests:app").used == 1
            assert session.scalar(select(AIUsage)).outcome == "STARTED"
    finally:
        engine.dispose()


def test_database_contention_fails_closed_without_attempt(api):
    app, _ = api
    uid = owner(app)
    with app.state.engine.connect() as lock:
        lock.exec_driver_sql("BEGIN IMMEDIATE")
        with pytest.raises(ExtractionError) as caught:
            AttemptBudget(app.state.engine, uid).start(DEFAULT_MODELS[0], 100, 100)
        assert caught.value.code == "AI_BUDGET_UNAVAILABLE"
        lock.rollback()
    with Session(app.state.engine) as session:
        assert session.scalar(select(func.count()).select_from(AIUsage)) == 0


def test_authenticated_operation_limit_cannot_be_reset_by_resource_or_headers(api, monkeypatch):
    app, client = api
    monkeypatch.setenv("SCOPESHIELD_RATE_EXTRACT", "1")
    ids = [upload(client).json()["id"] for _ in range(2)]
    assert client.post(f"/api/contracts/{ids[0]}/extract", json={"mode": "offline_demo"}).status_code == 200
    blocked = client.post(f"/api/contracts/{ids[1]}/extract", json={"mode": "offline_demo"},
        headers={"X-User-Id": "new-user", "X-Request-Id": "new-operation", "Origin": "http://127.0.0.1:3000"})
    assert blocked.status_code == 429 and blocked.headers["retry-after"]
    assert "Retry-After" in blocked.headers["access-control-expose-headers"]
    assert client.get(f"/api/contracts/{ids[0]}").status_code == 200


@pytest.mark.parametrize("declared", [True, False])
def test_large_anonymous_json_rejected_before_validation(api, declared):
    _, client = api
    payload = b'{"password":"' + b"x" * (2 * 1024 * 1024) + b'"}'
    data = payload if declared else iter([payload[:100], payload[100:]])
    response = client.post("/api/auth/login", content=data, headers={"Content-Type": "application/json"})
    assert response.status_code == 413 and response.json()["detail"]["code"] == "REQUEST_TOO_LARGE"


def test_ingress_ignores_forwarded_ip_and_limits_unauthenticated_reads(api, monkeypatch):
    app, client = api
    # Exercise a rollover after fixture sign-in, then hold the explicit current
    # window fixed. A legitimate minute reset must not make this assertion flaky.
    current_window = utc_now() + timedelta(seconds=61)
    monkeypatch.setattr("app.resources.utc_now", lambda: current_window)
    assert client.get("/api/auth/csrf").status_code == 200
    monkeypatch.setenv("SCOPESHIELD_RATE_INGRESS", "1")
    # The direct client's explicitly primed window has one admitted request.
    for ip in ("1.2.3.4", "5.6.7.8"):
        response = client.get("/api/auth/csrf", headers={"X-Forwarded-For": ip})
        assert response.status_code == 400 and response.json()["detail"]["code"] == "PROXY_HEADERS_UNSUPPORTED"
    assert client.get("/api/auth/csrf").status_code == 429


def test_catalog_queries_share_account_admission_before_loading_results(api, monkeypatch):
    _, client = api
    monkeypatch.setenv("SCOPESHIELD_RATE_QUERY", "1")
    assert client.get("/api/contracts").status_code == 200
    assert client.get("/api/projects?offset=0&limit=100").status_code == 429


def test_concurrent_auth_reads_do_not_deadlock_a_small_connection_pool(api, monkeypatch):
    import app.auth as auth
    from threading import Barrier
    from sqlalchemy import create_engine, event
    app, client = api
    original = app.state.engine
    limited = create_engine(str(original.url), pool_size=2, max_overflow=0, pool_timeout=3,
                            connect_args={"check_same_thread": False}, hide_parameters=True)
    event.listen(limited, "connect", lambda connection, _: connection.execute("PRAGMA foreign_keys=ON"))
    both_read = Barrier(2, timeout=5)
    authenticate = auth.current_user
    def synchronize_auth(request, session):
        user = authenticate(request, session)
        # Hold checked-out connections after cursors close, rather than holding a
        # live SQLite read cursor that would itself block the other ingress writer.
        both_read.wait()
        return user
    monkeypatch.setattr(auth, "current_user", synchronize_auth)
    app.state.engine = limited
    try:
        with ThreadPoolExecutor(max_workers=8) as pool:
            responses = list(pool.map(lambda _: client.get("/api/contracts"), range(8)))
        assert [response.status_code for response in responses] == [200] * 8
    finally:
        app.state.engine = original
        limited.dispose()


def test_daily_token_limit_rejects_without_partial_counter_or_http(api, http, monkeypatch):
    app, client = api
    enable(app)
    monkeypatch.setenv("SCOPESHIELD_AI_APP_DAILY_TOKENS", "1")
    cid = upload(client).json()["id"]
    assert extract(client, cid).status_code == 429 and not http[1]
    with Session(app.state.engine) as session:
        assert session.get(ResourceCounter, "ai:requests:app") is None
        assert session.scalar(select(func.count()).select_from(AIUsage)) == 0


def test_authorized_availability_fallback_reserves_both_attempts(api, http):
    app, client = api
    enable(app, two=True)
    cid = upload(client).json()["id"]
    http[0].extend([ProviderResponse(503, body={"error": {}}), ProviderResponse(body=envelope())])
    assert extract(client, cid).status_code == 200 and len(http[1]) == 2
    with Session(app.state.engine) as session:
        assert session.get(ResourceCounter, "ai:requests:app").used == 2
        assert [row.outcome for row in session.scalars(select(AIUsage).order_by(AIUsage.created_at))] == ["FAILED", "SUCCEEDED"]


def test_disconnect_during_body_never_enters_parser_or_mutates(api):
    app, _ = api
    called, sent = [], []
    async def inner(*args): called.append(True)
    queue = [{"type": "http.request", "body": b'{"partial":', "more_body": True}, {"type": "http.disconnect"}]
    async def receive(): return queue.pop(0)
    async def send(item): sent.append(item)
    asyncio.run(UploadBodyLimit(inner)({"type": "http", "method": "POST", "path": "/api/auth/login", "headers": [], "app": app}, receive, send))
    assert not called and not sent
    with Session(app.state.engine) as session:
        assert session.scalar(select(func.count()).select_from(Contract)) == 0


def test_pdf_expansion_bomb_fails_without_contract_rows(api):
    from pypdf import PdfReader, PdfWriter
    from pypdf.generic import DecodedStreamObject, NameObject
    app, client = api
    writer = PdfWriter()
    page = writer.add_page(PdfReader(BytesIO(pdf_bytes("Small text"))).pages[0])
    stream = DecodedStreamObject()
    stream.set_data(b" " * (9 * 1024 * 1024))
    page[NameObject("/Contents")] = writer._add_object(stream.flate_encode())
    result = BytesIO()
    writer.write(result)
    response = upload(client, result.getvalue(), name="expanded.pdf", media_type="application/pdf")
    assert response.status_code == 413 and response.json()["detail"]["code"] == "PDF_EXPANDED_LIMIT"
    with Session(app.state.engine) as session:
        assert session.scalar(select(func.count()).select_from(Contract)) == 0


def test_pdf_timeout_kills_and_reaps_worker(monkeypatch):
    real = subprocess.Popen
    children = []
    def stalled(*args, **kwargs):
        child = real([sys.executable, "-I", "-c", "import time; time.sleep(60)"], **kwargs)
        children.append(child)
        return child
    monkeypatch.setenv("SCOPESHIELD_PDF_TIMEOUT_SECONDS", "1")
    monkeypatch.setattr("app.ingestion.subprocess.Popen", stalled)
    with pytest.raises(HTTPException) as caught:
        ingest("slow.pdf", "application/pdf", b"%PDF-1.7\n")
    assert caught.value.detail["code"] == "PDF_PROCESSING_TIMEOUT"
    assert children[0].poll() is not None


def test_bounded_pdf_transfer_preserves_supported_non_bmp_text(monkeypatch):
    real = subprocess.Popen
    def unicode_result(*args, **kwargs):
        # Simulate a valid parser result at the declared text bound; JSON surrogate
        # escaping must not accidentally impose a smaller Unicode document limit.
        return real([sys.executable, "-I", "-c", "import json; print(json.dumps({'text': chr(0x1f600)*90000, 'page_count': 1}))"], **kwargs)
    monkeypatch.setattr("app.ingestion.subprocess.Popen", unicode_result)
    document = ingest("unicode.pdf", "application/pdf", b"%PDF-1.7\n")
    assert len(document.text) == 90000 and document.text == chr(0x1f600) * 90000


def test_worker_memory_ceiling_is_enforced():
    from pathlib import Path
    path = Path(__file__).resolve().parents[1] / "app" / "pdf_worker.py"
    code = "import runpy,sys; worker=runpy.run_path(sys.argv[1]); job=worker['memory_limit']();\ntry: data=bytearray(600*1024*1024)\nexcept MemoryError: print('bounded')"
    result = subprocess.run([sys.executable, "-I", "-c", code, str(path)], capture_output=True, timeout=30)
    assert result.returncode == 0 and result.stdout.strip() == b"bounded"


def test_password_checks_have_a_global_concurrency_cap(api, monkeypatch):
    from threading import Lock
    app, client = api
    bootstrap = client.get("/api/auth/csrf").json()["csrf_token"]
    entered, release, mutex = Event(), Event(), Lock()
    count = []
    def verify(*args):
        with mutex:
            count.append(True)
            if len(count) == 2:
                entered.set()
        assert release.wait(15)
        return False
    monkeypatch.setattr("app.auth.verify_password", verify)
    def attempt():
        return client.post("/api/auth/login", json={"username": "domain.test", "password": "wrong"}, headers={"X-CSRF-Token": bootstrap})
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = [pool.submit(attempt) for _ in range(2)]
        try:
            assert entered.wait(15)
            assert attempt().status_code == 429
            assert len(count) == 2
        finally:
            release.set()
        assert [item.result().status_code for item in pending] == [401, 401]


def test_global_slots_bound_different_accounts_and_release_on_exception(api):
    app, _ = api
    with app.state.resources.slot("ai", "first"):
        with app.state.resources.slot("ai", "second"):
            with pytest.raises(HTTPException) as caught:
                with app.state.resources.slot("ai", "third"):
                    pytest.fail("Global AI limit bypassed")
            assert caught.value.status_code == 429
    assert not app.state.resources.user_slots


def test_process_crash_keeps_usage_and_processing_claim_and_releases_instance_lock(tmp_path):
    from app.resources import single_instance
    path = tmp_path / "crashed.db"
    url = "sqlite:///" + path.as_posix()
    code = '''
import os,sys
from app.database import build_engine, initialize_database
from app.resources import single_instance, AttemptBudget
from app.storage import User, Project, Contract, utc_now
from sqlalchemy.orm import Session
engine=build_engine(sys.argv[1])
with single_instance(engine):
    initialize_database(engine)
    with Session(engine) as session:
        user=User(username="crash.test",display_name="Crash",role="freelancer",password_hash="unused")
        session.add(user); session.flush()
        project=Project(owner_id=user.id); session.add(project); session.flush()
        session.add(Contract(project_id=project.id,filename="crash.txt",media_type="text/plain",size_bytes=4,sha256="a"*64,text="text",status="PROCESSING",processing_token="crashed-operation",processing_started_at=utc_now()))
        session.commit(); uid=user.id
    AttemptBudget(engine,uid).start("gemini-3.5-flash-lite",100,100)
    os._exit(17)
'''
    result = subprocess.run([sys.executable, "-B", "-c", code, url], capture_output=True, timeout=30)
    assert result.returncode == 17
    engine = build_engine(url)
    try:
        with single_instance(engine):
            with Session(engine) as session:
                assert session.scalar(select(Contract)).status == "PROCESSING"
                assert session.scalar(select(AIUsage)).outcome == "STARTED"
                assert session.get(ResourceCounter, "ai:requests:app").used == 1
                assert session.scalar(select(func.count()).select_from(InvoiceLink)) == 0
    finally:
        engine.dispose()


def test_upload_reports_configured_ai_ineligibility_without_truncation(api, http):
    app, client = api
    enable(app)
    uploaded = upload(client, b"x" * 25000)
    assert uploaded.status_code == 201
    contract = uploaded.json()
    assert contract["ai_input_eligible"] is False and len(contract["text"]) == 25000
    response = extract(client, contract["id"])
    assert response.status_code == 413 and not http[1]
    assert client.get(f"/api/contracts/{contract['id']}").json()["status"] == "UPLOADED"


def test_socket_disconnect_after_admission_keeps_one_durable_result(api, monkeypatch):
    import socket
    import threading
    import time
    import uvicorn
    from app.main import create_app
    app, client = api
    cid = upload(client).json()["id"]
    entered, release = Event(), Event()
    calls = []
    def provider_request(*args, **kwargs):
        calls.append(True)
        entered.set()
        assert release.wait(15)
        return ProviderResponse(body=envelope())
    monkeypatch.setattr(requests, "request", provider_request)
    application = create_app(storage_url=str(app.state.engine.url))
    enable(application)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(application, host="127.0.0.1", port=port, log_level="critical", access_log=False, proxy_headers=False))
    worker = threading.Thread(target=lambda: server.run(sockets=[listener]), daemon=True)
    worker.start()
    try:
        deadline = time.monotonic() + 15
        while not server.started and worker.is_alive() and time.monotonic() < deadline:
            time.sleep(.01)
        assert server.started
        body = json.dumps({"mode": "configured"}).encode()
        cookie = "; ".join(f"{key}={value}" for key, value in client.cookies.items())
        with socket.create_connection(("127.0.0.1", port), timeout=5) as connection:
            headers = f"POST /api/contracts/{cid}/extract HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nCookie: {cookie}\r\nX-CSRF-Token: {client._test_csrf}\r\nContent-Type: application/json\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n"
            connection.sendall(headers.encode() + body)
            assert entered.wait(10)
            connection.shutdown(socket.SHUT_RDWR)
        release.set()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            with Session(app.state.engine) as session:
                contract = session.get(Contract, cid)
                if contract.status == "REVIEW_REQUIRED":
                    assert contract.extraction["live_ai"] is True  # Mocked HTTP through the actual configured adapter.
                    assert session.scalar(select(AIUsage)).outcome == "SUCCEEDED"
                    break
            time.sleep(.02)
        else:
            pytest.fail("Disconnected operation did not persist its completed result")
        assert len(calls) == 1
    finally:
        release.set()
        server.should_exit = True
        worker.join(10)
        listener.close()
        assert not worker.is_alive()
