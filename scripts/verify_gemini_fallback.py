"""Explicitly approved, single-run live extraction with a GLOBAL <=3 POST budget.

Do not run the approved flags until the human confirms the named models' project
Free-tier allowances and this exact plan. No SDK retry, PayPal, billing, second AI
workflow, response fixture or automatic rerun. Source/request are synthetic files.
"""
import argparse
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import secrets
import time
from uuid import uuid4

from fastapi.testclient import TestClient
import requests
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth import hash_password
from app.database import build_engine
from app.dependencies import get_paypal
from app.extraction import get_extraction_provider
from app.gemini import API_BASE, VERIFIED_MODELS, configured_provider
from app.storage import Contract, InvoiceLink, InvoiceEvent, ManualInvoiceOwner, ReviewEvent, User

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "examples/gemini_fallback_agreement.txt"
SCOPE = ROOT / "examples/gemini_fallback_scope_request.json"
OUTPUT = ROOT / ".local/gemini-fallback"


def save(path, data):
    with path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2)
        handle.flush()
        os.fsync(handle.fileno())


class InferenceBudget:
    """Forward the actual HTTP call only after committing this one-model intent."""
    def __init__(self, models, max_calls, journal, original):
        self.models, self.max_calls, self.journal, self.original = models, max_calls, journal, original
        self.attempts, self.blocked_requests = [], 0

    def request(self, session, method, url, **kwargs):
        model = next((model for model in self.models if url == f"{API_BASE}/{model}:generateContent"), None)
        if (method.upper() != "POST" or model is None or len(self.attempts) >= self.max_calls
                or any(row["model"] == model for row in self.attempts)
                or model != self.models[len(self.attempts)]):
            self.blocked_requests += 1
            raise AssertionError("Request outside the approved model/order/one-attempt budget was blocked")
        row = dict(model=model, started_at=datetime.now(timezone.utc).isoformat(), http_status=None, elapsed_ms=None)
        self.attempts.append(row)
        save(self.journal, {"attempts": self.attempts})
        started = time.monotonic()
        try:
            response = self.original(session, method, url, **kwargs)
            row["http_status"] = response.status_code
            return response
        finally:
            row["elapsed_ms"] = round((time.monotonic() - started) * 1000)
            save(self.journal, {"attempts": self.attempts})


def verify_result(body):
    extraction, source = body["extraction"], body["text"]
    rows = extraction["data"]["milestones"]
    issues = extraction["review_issues"]
    evidence = []
    def walk(value):
        if isinstance(value, dict):
            if "value" in value and value["value"] is not None:
                start, end = value.get("source_start"), value.get("source_end")
                evidence.append(isinstance(start, int) and isinstance(end, int)
                                and source[start:end] == value.get("source_excerpt"))
            for child in value.values(): walk(child)
        elif isinstance(value, list):
            for child in value: walk(child)
    walk(extraction["data"])
    third = rows[2] if len(rows) == 3 else None
    checks = {
        "live_provider": extraction["live_ai"] is True and extraction["mode"] == "configured" and extraction["provider"] == "google_gemini",
        "currency_usd": extraction["data"]["currency"]["value"] == "USD" and all(row["currency"]["value"] == "USD" for row in rows),
        "three_exact_milestones": len(rows) == 3 and all(row["amount"]["value"] is not None for row in rows)
            and [Decimal(row["amount"]["value"]) for row in rows] == [Decimal("60"), Decimal("90"), Decimal("150")],
        "exact_total": extraction["data"]["total_value"]["value"] is not None
            and Decimal(extraction["data"]["total_value"]["value"]) == Decimal("300") and extraction["totals_match"] is True,
        "all_source_offsets_match": bool(evidence) and all(evidence),
        "ambiguous_deadline_not_invented": bool(third and third["deadline"]["value"] is None),
        "ambiguous_condition_flagged": any(item["field"] in {"milestones[2].payment_conditions", "milestones[2].acceptance_criteria", "milestones[2].acceptance_criteria[0]"}
            and item["code"] in {"NOT_STATED", "PROVIDER_REQUESTED_REVIEW"} for item in issues),
        "human_approval_required": extraction["requires_human_review"] is True and body["status"] == "REVIEW_REQUIRED"
            and all(row["status"] == "REVIEW_REQUIRED" for row in body["milestones"]),
    }
    return {"checks": checks, "all_checks_pass": all(checks.values()), "verified_evidence_fields": len(evidence)}


def preflight(models, max_calls, metadata):
    if models not in {VERIFIED_MODELS[:2], VERIFIED_MODELS} or max_calls != len(models) or max_calls > 3:
        raise ValueError("Approve the exact two-model priority, or all three including optional Flash")
    checked = datetime.fromisoformat(metadata["checked_at"])
    age = (datetime.now(timezone.utc) - checked).total_seconds()
    if not 0 <= age < 86400 or metadata["api_base"] != API_BASE or metadata["list_http_status"] != 200 or not metadata["list_complete"]:
        raise ValueError("Fresh complete official v1beta model metadata is required")
    for model in models:
        row = metadata["models"][model]
        if row["http_status"] != 200 or "generateContent" not in row["methods"] or "models/" + model not in metadata["generate_content_models"]:
            raise ValueError("An approved model lacks compatible metadata")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--approved", action="store_true")
    parser.add_argument("--approved-free-tier-models", default="")
    parser.add_argument("--approved-max-calls", type=int, choices=[2, 3])
    args = parser.parse_args()
    if not args.approved or not args.approved_free_tier_models or args.approved_max_calls is None:
        parser.error("Human approval, confirmed Free-tier models and the global call cap are required")
    models = tuple(args.approved_free_tier_models.split(","))
    metadata = json.loads((OUTPUT / "model-metadata.json").read_text(encoding="utf-8"))
    preflight(models, args.approved_max_calls, metadata)
    source, scope = SOURCE.read_bytes(), json.loads(SCOPE.read_text(encoding="utf-8"))
    result = dict(started_at=datetime.now(timezone.utc).isoformat(), approved_models=models,
        global_call_cap=args.approved_max_calls, synthetic_source_sha256=hashlib.sha256(source).hexdigest(),
        source_bytes=len(source), scope_request_sha256=hashlib.sha256(SCOPE.read_bytes()).hexdigest(),
        sdk_retries=0, per_model_attempts=1, no_paypal=True, live_scope_analysis_attempted=False)
    settings = {"SCOPESHIELD_AI_PROVIDER": "gemini", "SCOPESHIELD_AI_MODELS": ",".join(models),
        "SCOPESHIELD_AI_FREE_TIER_MODELS": ",".join(models), "SCOPESHIELD_AI_ALLOW_LIVE": "true",
        "SCOPESHIELD_AI_MAX_MODEL_CALLS": str(args.approved_max_calls)}
    before = {name: os.environ.get(name) for name in settings}
    original = requests.sessions.Session.request
    budget = InferenceBudget(models, args.approved_max_calls, OUTPUT / "live-attempts.json", original)
    # Exclusive marker survives failure/crash and prohibits a second invocation.
    with (OUTPUT / "live-authorization.lock").open("x", encoding="utf-8") as handle:
        json.dump(result, handle); handle.flush(); os.fsync(handle.fileno())
    password = secrets.token_urlsafe(32)
    database = "sqlite:///" + (OUTPUT / ("live-" + uuid4().hex + ".db")).as_posix()
    result["database_filename"] = database.rsplit("/", 1)[-1]
    provider = None
    try:
        os.environ.update(settings)
        provider = configured_provider()
        from app.main import create_app
        app = create_app(storage_url=database)
        app.dependency_overrides[get_extraction_provider] = lambda: provider
        def forbid_paypal(): raise AssertionError("Live AI verification cannot initialize PayPal")
        app.dependency_overrides[get_paypal] = forbid_paypal
        requests.sessions.Session.request = lambda session, method, url, **kwargs: budget.request(session, method, url, **kwargs)
        with TestClient(app, base_url="http://127.0.0.1:8001") as client:
            with Session(app.state.engine) as session:
                session.add(User(username="live.fallback", display_name="Synthetic verification", role="freelancer", password_hash=hash_password(password)))
                session.commit()
            csrf = client.get("/api/auth/csrf").json()["csrf_token"]
            login = client.post("/api/auth/login", headers={"X-CSRF-Token": csrf}, json={"username": "live.fallback", "password": password})
            login.raise_for_status(); del password
            client.headers["X-CSRF-Token"] = login.json()["csrf_token"]
            uploaded = client.post("/api/contracts", files={"file": (SOURCE.name, source, "text/plain")})
            uploaded.raise_for_status(); contract = uploaded.json(); result["contract_id"] = contract["id"]
            request = client.post(f"/api/contracts/{contract['id']}/change-requests", json={
                **scope, "project_id": contract["project_id"], "reviewer_label": "Synthetic verification", "note": "Prepared synthetic request; no scope inference authorized in this extraction test"})
            request.raise_for_status(); result["scope_request_status"] = request.json()["status"]
            response = client.post(f"/api/contracts/{contract['id']}/extract", json={"mode": "configured"})
            result.update(application_http_status=response.status_code, application_body=response.json())
            if response.is_success:
                result.update(verify_result(response.json()))
                saved = client.get(f"/api/contracts/{contract['id']}"); saved.raise_for_status()
                result["api_read_matches"] = saved.json()["extraction"] == response.json()["extraction"]
            client.post("/api/auth/logout")
        engine = build_engine(database)
        with Session(engine) as session:
            stored = session.get(Contract, result["contract_id"])
            result["persisted_in_fresh_connection"] = bool(stored.extraction and stored.extraction == result["application_body"].get("extraction"))
            result["unapproved_milestones"] = [row.status for row in stored.milestones]
            result["financial_and_review_counts"] = {model.__tablename__: session.scalar(select(func.count()).select_from(model))
                for model in (InvoiceLink, InvoiceEvent, ManualInvoiceOwner, ReviewEvent)}
        engine.dispose()
    except Exception as exc:
        # Error class is diagnostic; arbitrary exception text may contain secrets.
        result["verification_error_type"] = type(exc).__name__
    finally:
        requests.sessions.Session.request = original
        for name, value in before.items():
            if value is None: os.environ.pop(name, None)
            else: os.environ[name] = value
        result.update(inference_calls=len(budget.attempts), network_attempts=budget.attempts,
            blocked_requests=budget.blocked_requests, model_attempts=getattr(provider, "last_attempts", []),
            selected_model=getattr(provider, "model", None), response_model=getattr(provider, "response_model", None),
            token_usage=getattr(provider, "last_usage", None), completed_at=datetime.now(timezone.utc).isoformat(),
            process_settings_restored=all(os.environ.get(name) == value for name, value in before.items()))
        save(OUTPUT / "live-result.json", result)
    print(json.dumps({key: value for key, value in result.items() if key != "application_body"}, indent=2))
    return 0 if result.get("all_checks_pass") and result.get("persisted_in_fresh_connection") else 1


if __name__ == "__main__": raise SystemExit(main())
