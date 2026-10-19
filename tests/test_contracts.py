"""Upload security, persisted extraction, processing recovery, and regressions."""
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.extraction import get_extraction_provider
from app.ingestion import MAX_REQUEST_BYTES, MAX_UPLOAD_BYTES
from app.main import create_app, get_paypal
from app.storage import Contract, Milestone, Project, utc_now
from contract_fixtures import MockExtractionProvider, pdf_bytes, sample_output, sample_text


@pytest.fixture
def api(tmp_path):
    application = create_app(storage_url="sqlite:///" + (tmp_path / "contracts.db").as_posix())
    def forbidden_paypal():
        raise AssertionError("Contract processing must never call PayPal")
    application.dependency_overrides[get_paypal] = forbidden_paypal
    with TestClient(application, base_url="http://127.0.0.1:8000") as client:
        yield application, client


def upload(client, content=None, *, name="agreement.txt", media_type="text/plain", project_id=None):
    data = {"project_id": project_id} if project_id else None
    return client.post("/api/contracts", files={"file": (name, (content if content is not None else sample_text().encode()), media_type)}, data=data)


def offline(client, contract_id, **extra):
    return client.post(f"/api/contracts/{contract_id}/extract", json={"mode": "offline_demo", **extra})


def test_text_upload_and_retrieve_persisted_text_metadata(api):
    application, client = api
    created = upload(client)
    assert created.status_code == 201
    body = created.json()
    assert body["text"] == sample_text() and body["status"] == "UPLOADED"
    assert body["extraction"] is None and body["milestones"] == []
    assert len(body["sha256"]) == 64 and body["page_count"] is None
    assert client.get(f"/api/contracts/{body['id']}").json() == body
    project = client.get(f"/api/projects/{body['project_id']}").json()
    assert project["contract_ids"] == [body["id"]] and project["title"] is None
    assert project["total_value"] is None and project["status"] == "DRAFT"


def test_valid_pdf_text_ingestion_and_fixture_extraction(api):
    _, client = api
    created = upload(client, pdf_bytes(sample_text()), name="agreement.pdf", media_type="application/pdf")
    assert created.status_code == 201
    assert created.json()["page_count"] == 1
    assert "Total project value: USD 1,500.00." in created.json()["text"]
    extracted = offline(client, created.json()["id"])
    assert extracted.status_code == 200
    assert extracted.json()["extraction"]["totals_match"] is True


@pytest.mark.parametrize("content,name,media,status,code", [
    (b"", "empty.txt", "text/plain", 400, "EMPTY_UPLOAD"),
    (b" \n\t ", "empty.txt", "text/plain", 400, "EMPTY_CONTRACT_TEXT"),
    (b"malicious executable", "contract.exe", "application/octet-stream", 415, "UNSUPPORTED_FILE_TYPE"),
    (b"normal text", "contract.txt", "image/jpeg", 415, "UNSUPPORTED_FILE_TYPE"),
    (b"not a PDF", "contract.pdf", "application/pdf", 400, "INVALID_PDF"),
    (b"%PDF-1.7\nmalformed", "contract.pdf", "application/pdf", 400, "INVALID_PDF"),
    (b"%PDF-1.7\n", "contract.txt", "text/plain", 400, "INVALID_TEXT_FILE"),
    (b"\x00binary", "contract.txt", "text/plain", 400, "INVALID_TEXT_FILE"),
    (b"\xff\xfe", "contract.txt", "text/plain", 400, "INVALID_TEXT_ENCODING"),
    (b"x" * 100001, "contract.txt", "text/plain", 413, "CONTRACT_TEXT_TOO_LONG"),
    (b"text", "x" * 181 + ".txt", "text/plain", 400, "INVALID_FILENAME"),
], ids=["empty", "whitespace", "extension", "mime", "not_pdf", "malformed_pdf", "pdf_as_text", "binary", "encoding", "text_limit", "filename_limit"])
def test_invalid_uploads_create_no_persistent_rows(api, content, name, media, status, code):
    application, client = api
    result = upload(client, content, name=name, media_type=media)
    assert result.status_code == status
    assert result.json()["detail"]["code"] == code
    with Session(application.state.engine) as session:
        assert session.scalar(select(func.count()).select_from(Contract)) == 0
        assert session.scalar(select(func.count()).select_from(Project)) == 0


@pytest.mark.parametrize("content_size", [MAX_UPLOAD_BYTES + 1, MAX_REQUEST_BYTES + 1])
def test_oversized_files_are_rejected(api, content_size):
    application, client = api
    result = upload(client, b"x" * content_size)
    assert result.status_code == 413
    with Session(application.state.engine) as session:
        assert session.scalar(select(func.count()).select_from(Contract)) == 0


def test_streamed_multipart_request_is_bounded_without_content_length(api):
    _, client = api
    def content():
        yield b'--boundary\r\nContent-Disposition: form-data; name="file"; filename="huge.txt"\r\nContent-Type: text/plain\r\n\r\n'
        for _ in range(6):
            yield b"x" * (1024 * 1024)
        yield b"\r\n--boundary--\r\n"
    result = client.post("/api/contracts", headers={"Content-Type": "multipart/form-data; boundary=boundary"}, content=content())
    assert result.status_code == 413


def test_pdf_without_text_has_clear_scanned_pdf_error(api):
    _, client = api
    result = upload(client, pdf_bytes(), name="scan.pdf", media_type="application/pdf")
    assert result.status_code == 422 and result.json()["detail"]["code"] == "PDF_HAS_NO_TEXT"
    assert "OCR" in result.json()["detail"]["message"]


def test_encrypted_and_over_page_limit_pdfs(api):
    _, client = api
    encrypted = upload(client, pdf_bytes("Synthetic text", password="test-only-password"), name="encrypted.pdf", media_type="application/pdf")
    assert encrypted.status_code == 422 and encrypted.json()["detail"]["code"] == "ENCRYPTED_PDF"
    large = upload(client, pdf_bytes(pages=51), name="large.pdf", media_type="application/pdf")
    assert large.status_code == 413 and large.json()["detail"]["code"] == "PDF_TOO_MANY_PAGES"


def test_filename_is_metadata_and_path_traversal_is_removed(api):
    _, client = api
    result = upload(client, name="../../unsafe\\agreement.txt", media_type="application/octet-stream")
    assert result.status_code == 201 and result.json()["filename"] == "agreement.txt"


def test_text_bom_and_unicode_are_supported(api):
    _, client = api
    result = upload(client, "\ufeffSynthetic café agreement".encode())
    assert result.status_code == 201 and result.json()["text"] == "Synthetic café agreement"


def test_offline_extraction_saves_milestones_in_review_state_and_never_paypal(api):
    application, client = api
    created = upload(client).json()
    result = offline(client, created["id"])
    assert result.status_code == 200
    body = result.json()
    assert body["status"] == "REVIEW_REQUIRED" and body["error_code"] is None
    assert body["extraction"]["provider"] == "offline_synthetic_fixture"
    assert body["extraction"]["live_ai"] is False
    assert body["extraction"]["mode"] == "offline_demo"
    assert [item["amount"] for item in body["milestones"]] == ["500.00"] * 3
    assert all(item["status"] == "REVIEW_REQUIRED" for item in body["milestones"])
    assert [item["ordinal"] for item in body["milestones"]] == [1, 2, 3]
    assert client.get(f"/api/contracts/{created['id']}").json() == body
    project = client.get(f"/api/projects/{created['project_id']}").json()
    assert project["total_value"] == "1500.00" and project["status"] == "REVIEW_REQUIRED"
    with Session(application.state.engine) as session:
        amounts = session.scalars(select(Milestone.amount).order_by(Milestone.ordinal)).all()
        assert amounts == [Decimal("500.00")] * 3
        assert session.execute(text("SELECT typeof(amount), amount FROM milestones")).all() == [("text", "500.00")] * 3


def test_no_live_provider_returns_503_and_persisted_failed_state(api):
    _, client = api
    contract_id = upload(client).json()["id"]
    result = client.post(f"/api/contracts/{contract_id}/extract")
    assert result.status_code == 503 and result.json()["detail"]["code"] == "AI_PROVIDER_UNAVAILABLE"
    saved = client.get(f"/api/contracts/{contract_id}").json()
    assert saved["status"] == "FAILED" and saved["extraction"] is None
    assert saved["error_code"] == "AI_PROVIDER_UNAVAILABLE"
    assert offline(client, contract_id).status_code == 200


def test_provider_schema_failure_is_safe_and_retryable(api):
    application, client = api
    contract_id = upload(client).json()["id"]
    provider = MockExtractionProvider('{"sensitive":"dummy_secret_to_hide"}')
    application.dependency_overrides[get_extraction_provider] = lambda: provider
    result = client.post(f"/api/contracts/{contract_id}/extract", json={"mode": "configured"})
    assert result.status_code == 502 and "dummy_secret_to_hide" not in result.text
    assert client.get(f"/api/contracts/{contract_id}").json()["error_code"] == "AI_INVALID_OUTPUT"
    provider.output = MockExtractionProvider(sample_output()).output
    assert client.post(f"/api/contracts/{contract_id}/extract", json={"mode": "configured"}).status_code == 200


def test_provider_exception_is_sanitized(api):
    application, client = api
    class BrokenProvider:
        name, live_ai = "mock_broken", False
        def extract(self, text):
            raise RuntimeError("dummy_secret_to_hide " + text)
    application.dependency_overrides[get_extraction_provider] = lambda: BrokenProvider()
    contract_id = upload(client).json()["id"]
    result = client.post(f"/api/contracts/{contract_id}/extract")
    assert result.status_code == 502 and "dummy_secret_to_hide" not in result.text
    assert sample_text() not in result.text


def test_arbitrary_contract_does_not_get_fixture_facts(api):
    _, client = api
    contract_id = upload(client, b"An unrelated agreement with no fee or dates").json()["id"]
    result = offline(client, contract_id)
    assert result.status_code == 422 and result.json()["detail"]["code"] == "OFFLINE_DEMO_CONTRACT_MISMATCH"
    assert client.get(f"/api/contracts/{contract_id}").json()["milestones"] == []


def test_cached_and_forced_extraction_do_not_duplicate_milestones(api):
    application, client = api
    provider = MockExtractionProvider(sample_output())
    application.dependency_overrides[get_extraction_provider] = lambda: provider
    contract_id = upload(client).json()["id"]
    path = f"/api/contracts/{contract_id}/extract"
    first = client.post(path, json={"mode": "configured"}).json()
    assert client.post(path, json={"mode": "configured"}).json() == first
    assert len(provider.calls) == 1
    assert client.post(path, json={"mode": "configured", "force": True}).status_code == 200
    assert len(provider.calls) == 2
    with Session(application.state.engine) as session:
        assert session.scalar(select(func.count()).select_from(Milestone)) == 3


def test_failed_reprocessing_preserves_previous_validated_snapshot(api):
    application, client = api
    contract_id = upload(client).json()["id"]
    saved = offline(client, contract_id).json()
    result = client.post(f"/api/contracts/{contract_id}/extract", json={"mode": "configured", "force": True})
    assert result.status_code == 503
    after = client.get(f"/api/contracts/{contract_id}").json()
    assert after["extraction"] == saved["extraction"] and after["milestones"] == saved["milestones"]
    assert after["status"] == "REVIEW_REQUIRED"
    assert after["error_code"] == "AI_PROVIDER_UNAVAILABLE"


def test_active_processing_is_not_claimed_twice_and_stale_work_can_recover(api):
    application, client = api
    contract_id = upload(client).json()["id"]
    with Session(application.state.engine) as session:
        row = session.get(Contract, contract_id)
        row.status, row.processing_started_at, row.processing_token = "PROCESSING", utc_now(), str(uuid4())
        session.commit()
    assert offline(client, contract_id).status_code == 409
    with Session(application.state.engine) as session:
        session.get(Contract, contract_id).processing_started_at = utc_now() - timedelta(minutes=6)
        session.commit()
    assert offline(client, contract_id).status_code == 200


def test_unknown_contract_project_and_invalid_ids(api):
    _, client = api
    for method, path in [("GET", "/api/contracts/"), ("POST", "/api/contracts/")]:
        suffix = "/extract" if method == "POST" else ""
        assert client.request(method, path + str(uuid4()) + suffix).status_code == 404
        assert client.request(method, path + "invalid" + suffix).status_code == 422
    assert client.get(f"/api/projects/{uuid4()}").status_code == 404
    assert upload(client, project_id=str(uuid4())).status_code == 404


def test_contracts_can_share_a_project(api):
    _, client = api
    first = upload(client).json()
    second = upload(client, b"A second synthetic agreement", project_id=first["project_id"]).json()
    assert second["project_id"] == first["project_id"]
    project = client.get(f"/api/projects/{first['project_id']}").json()
    assert set(project["contract_ids"]) == {first["id"], second["id"]}


def test_foreign_origin_cannot_upload_or_extract(api):
    _, client = api
    result = client.post("/api/contracts", files={"file": ("contract.txt", b"text", "text/plain")}, headers={"Origin": "https://foreign.example.com"})
    assert result.status_code == 403


def test_contract_contents_are_not_logged(api, caplog):
    _, client = api
    text_marker = b"Synthetic confidential marker that must not be logged"
    assert upload(client, text_marker).status_code == 201
    assert text_marker.decode() not in caplog.text


def test_persistence_across_full_application_lifespans(tmp_path):
    url = "sqlite:///" + (tmp_path / "restart.db").as_posix()
    with TestClient(create_app(storage_url=url), base_url="http://127.0.0.1:8000") as first:
        created = upload(first).json()
        saved = offline(first, created["id"]).json()
    with TestClient(create_app(storage_url=url), base_url="http://127.0.0.1:8000") as restarted:
        retrieved = restarted.get(f"/api/contracts/{created['id']}")
        assert retrieved.status_code == 200 and retrieved.json() == saved
        assert restarted.get("/health").status_code == 200


def test_versioned_schema_is_idempotent_and_foreign_keys_are_enabled(api):
    application, _ = api
    from app.database import initialize_database
    initialize_database(application.state.engine)
    with Session(application.state.engine) as session:
        assert session.scalar(text("SELECT version_num FROM alembic_version")) == "0009_conversations"
        assert session.scalar(text("PRAGMA foreign_keys")) == 1


def test_superseded_extraction_cannot_overwrite_new_processing_attempt(api):
    application, client = api
    contract_id = upload(client).json()["id"]
    current_token = str(uuid4())
    class SupersededProvider(MockExtractionProvider):
        def extract(self, source_text):
            with Session(application.state.engine) as session:
                row = session.get(Contract, contract_id)
                row.processing_token = current_token
                session.commit()
            return super().extract(source_text)
    application.dependency_overrides[get_extraction_provider] = lambda: SupersededProvider(sample_output())
    result = client.post(f"/api/contracts/{contract_id}/extract")
    assert result.status_code == 409
    with Session(application.state.engine) as session:
        row = session.get(Contract, contract_id)
        assert row.processing_token == current_token and row.extraction is None
        assert session.scalar(select(func.count()).select_from(Milestone)) == 0


def test_provider_typed_error_cannot_leak_secrets_or_choose_success_status(api):
    application, client = api
    from app.extraction import ExtractionError
    class UnsafeErrorProvider:
        name, live_ai = "mock_unsafe_error", False
        def extract(self, text):
            raise ExtractionError("AI_INVALID_OUTPUT", "dummy_secret_to_hide " + text, 200)
    application.dependency_overrides[get_extraction_provider] = lambda: UnsafeErrorProvider()
    contract_id = upload(client).json()["id"]
    result = client.post(f"/api/contracts/{contract_id}/extract")
    assert result.status_code == 502
    assert "dummy_secret_to_hide" not in result.text and sample_text() not in result.text
