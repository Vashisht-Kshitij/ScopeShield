"""One separately approved Gemini call through real loopback HTTP + ScopeShield auth.

Never imports/executes the user's reference scripts. No PayPal, billing, metadata,
fallback or retry. The isolated app uses the normal configured provider and routes.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import socket
import threading
import time
from uuid import uuid4

import httpx
import requests
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session
import uvicorn

from app.auth import hash_password
from app.database import build_engine
from app.contract_schemas import ContractExtraction, ExtractedMilestone, Evidence
from app.dependencies import get_paypal
from app.extraction import ExtractionError, get_extraction_provider, parse_provider_json
from app.gemini import DEFAULT_MODEL, MAX_OUTPUT_TOKENS, MAX_RESPONSE_BYTES, GeminiProvider, configured_provider
from app.main import create_app
from app.storage import Contract, InvoiceEvent, InvoiceLink, ManualInvoiceOwner, ReviewEvent, User
from scripts.verify_gemini_fallback import InferenceBudget, save

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "examples/gemini_verified_contract.txt"
SOURCE_SHA256 = "7a21b4b86d2651687f071b32324c1d093903c8e10db1b4c8e4dcc2a5c0f0a832"
OUTPUT = ROOT / ".local/gemini-rest-integration"


def schema_diagnostic(raw, output_type=ContractExtraction):
    """Offline-only diagnosis; never stores Pydantic input, context or messages."""
    try:
        text = GeminiProvider._extract_text(raw)
    except ExtractionError as exc:
        return {"stage":"provider_envelope", "code":exc.code}
    try:
        data = parse_provider_json(text)
    except (ValueError, TypeError, RecursionError):
        return {"stage":"json", "code":"AI_INVALID_OUTPUT"}
    try:
        output_type.model_validate(data)
    except ValidationError as exc:
        schema=output_type.model_json_schema()
        fields=set(schema.get("properties",{})) | {key for definition in schema.get("$defs",{}).values() for key in definition.get("properties",{})}
        errors = exc.errors(include_input=False, include_context=False, include_url=False)
        return {"stage":"schema", "error_count":len(errors), "errors":[
            {"type":row["type"], "loc":[part if type(part) is int or part in fields else "<unexpected_field>" for part in row["loc"]]}
            for row in errors[:30]]}
    return {"stage":"schema_valid"}


class VerifiedRequestBudget(InferenceBudget):
    def __init__(self,*args,source=SOURCE,source_sha256=SOURCE_SHA256,requested_changes=None,**kwargs):
        super().__init__(*args,**kwargs)
        self.source,self.source_sha256,self.requested_changes=source,source_sha256,requested_changes
        if self.journal.exists():
            self.attempts=json.loads(self.journal.read_text(encoding="utf-8"))["attempts"]

    def request(self, session, method, url, **kwargs):
        payload = kwargs.get("json", {})
        try:
            user = json.loads(payload["contents"][0]["parts"][0]["text"])
            source = user["untrusted_contract_text" if self.requested_changes is None else "untrusted_original_contract"]
            if self.requested_changes is not None and user["untrusted_requested_changes"] != [{"ordinal":i,"requested_work":work} for i,work in enumerate(self.requested_changes,1)]:
                raise ValueError("Unapproved scope request")
            if (source != self.source.read_text(encoding="utf-8").strip()
                    or hashlib.sha256(self.source.read_bytes()).hexdigest() != self.source_sha256
                    or payload["generationConfig"] != {"responseMimeType":"application/json", "maxOutputTokens":MAX_OUTPUT_TOKENS}
                    or "tools" in payload or "toolConfig" in payload):
                raise ValueError("Unapproved payload")
        except (KeyError, TypeError, ValueError):
            self.blocked_requests += 1
            raise AssertionError("Request differs from the approved synthetic JSON-mode plan") from None
        response = super().request(session, method, url, **kwargs)
        original_iter = response.iter_content
        api_key = kwargs.get("headers", {}).get("x-goog-api-key", "")
        def recorded_chunks(*args, **options):
            chunks, size = [], 0
            try:
                for chunk in original_iter(*args, **options):
                    remaining = MAX_RESPONSE_BYTES - size
                    if remaining > 0: chunks.append(chunk[:remaining])
                    size += len(chunk)
                    yield chunk
            finally:
                # Tee the existing bounded stream; no extra read/request, no
                # production logging, no headers. Only this pinned synthetic run.
                raw = b"".join(chunks)
                redacted = raw.replace(api_key.encode(), b"[redacted]") if api_key else raw
                captured = {"http_status":response.status_code, "captured_bytes":len(raw),
                    "truncated":size > MAX_RESPONSE_BYTES, "redaction_truncated":len(redacted)>MAX_RESPONSE_BYTES,
                    "body":redacted[:MAX_RESPONSE_BYTES].decode("utf-8", errors="replace")}
                try:
                    from app.scope_schemas import ScopeComparison
                    captured["validation"] = schema_diagnostic(raw,ContractExtraction if self.requested_changes is None else ScopeComparison) if not captured["truncated"] else {"stage":"size_limit"}
                    save(self.journal.parent/"provider-response.json", captured)
                    self.response_diagnostic = captured["validation"]
                except Exception as exc:
                    # Diagnostics must never alter application validation/results.
                    self.capture_error_type = type(exc).__name__
        response.iter_content = recorded_chunks
        return response


@contextmanager
def local_server(app):
    listener = socket.socket(); listener.bind(("127.0.0.1", 0)); listener.listen(32)
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_config=None, access_log=False))
    thread = threading.Thread(target=lambda: server.run(sockets=[listener]), daemon=True)
    # Allow a bounded cold-start window for the local verification server on
    # Windows. This affects local readiness only, never provider work.
    thread.start(); deadline = time.monotonic() + 45
    try:
        while not server.started:
            if not thread.is_alive() or time.monotonic() > deadline: raise RuntimeError("Isolated app did not start")
            time.sleep(.02)
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True; thread.join(10)
        if thread.is_alive(): server.force_exit = True; thread.join(5)
        listener.close()


def extraction_checks(body):
    extraction, text = body["extraction"], body["text"]
    rows = extraction["data"]["milestones"]
    evidence = []
    def walk(value):
        if isinstance(value, dict):
            if "value" in value and value["value"] is not None:
                start, end = value.get("source_start"), value.get("source_end")
                evidence.append(isinstance(start,int) and isinstance(end,int) and text[start:end] == value.get("source_excerpt"))
            for child in value.values(): walk(child)
        elif isinstance(value,list):
            for child in value: walk(child)
    walk(extraction["data"])
    checks = {
        "usd": extraction["data"]["currency"]["value"] == "USD" and all(row["currency"]["value"] == "USD" for row in rows),
        "total_1500": extraction["data"]["total_value"]["value"] is not None and Decimal(extraction["data"]["total_value"]["value"]) == Decimal("1500"),
        "three_amounts_300_700_500": len(rows)==3 and all(row["amount"]["value"] is not None for row in rows)
            and [Decimal(row["amount"]["value"]) for row in rows] == list(map(Decimal,["300","700","500"])),
        "totals_match": extraction["totals_match"] is True,
        "grounded_source_offsets": bool(evidence) and all(evidence),
        "unknown_deadlines_not_invented": len(rows)==3 and all(row["deadline"]["value"] is None for row in rows),
        "human_review_required": body["status"]=="REVIEW_REQUIRED" and extraction["requires_human_review"] is True
            and len(body["milestones"])==3 and all(row["status"]=="REVIEW_REQUIRED" for row in body["milestones"]),
        "live_model_attribution": extraction["live_ai"] is True and extraction["mode"]=="configured"
            and extraction["provider"]=="google_gemini" and extraction["model"].startswith(DEFAULT_MODEL),
    }
    return {"checks":checks,"all_checks_pass":all(checks.values()),"evidence_fields":len(evidence)}


def exercise_application(provider_factory, output):
    output.mkdir(parents=True, exist_ok=True)
    database = "sqlite:///" + (output / ("application-"+uuid4().hex+".db")).as_posix()
    app = create_app(storage_url=database); observed = {}; result = {"database_filename":database.rsplit("/",1)[-1]}
    def tracked_provider():
        observed["provider"] = provider_factory()
        return observed["provider"]
    def no_paypal(): raise AssertionError("This verification cannot initialize PayPal")
    app.dependency_overrides[get_extraction_provider] = tracked_provider
    app.dependency_overrides[get_paypal] = no_paypal
    with local_server(app) as url, httpx.Client(base_url=url, timeout=110, follow_redirects=False) as client:
        result["http_transport"] = "real loopback HTTP"
        result["anonymous_contract_status"] = client.get("/api/contracts").status_code
        password = secrets.token_urlsafe(32)
        with Session(app.state.engine) as session:
            session.add(User(username="live.verified",display_name="Synthetic verification",role="freelancer",password_hash=hash_password(password)))
            session.commit()
        preflight = client.get("/api/auth/csrf"); preflight.raise_for_status()
        login = client.post("/api/auth/login",headers={"X-CSRF-Token":preflight.json()["csrf_token"]},json={"username":"live.verified","password":password})
        login.raise_for_status(); del password
        client.headers["X-CSRF-Token"] = login.json()["csrf_token"]
        upload = client.post("/api/contracts",files={"file":(SOURCE.name,SOURCE.read_bytes(),"text/plain")})
        upload.raise_for_status(); contract = upload.json(); result["contract_id"] = contract["id"]
        if contract["sha256"] != SOURCE_SHA256 or contract["text"] != SOURCE.read_text(encoding="utf-8").strip():
            raise ValueError("Uploaded source differs from the approved contract")
        response = client.post(f"/api/contracts/{contract['id']}/extract",json={"mode":"configured"})
        result.update(application_http_status=response.status_code,application_body=response.json())
        if response.is_success:
            result.update(extraction_checks(response.json()))
            saved = client.get(f"/api/contracts/{contract['id']}"); saved.raise_for_status()
            result["api_read_matches"] = saved.json()["extraction"] == response.json()["extraction"]
        events = client.get(f"/api/contracts/{contract['id']}/activity"); events.raise_for_status()
        result["audit_actions"] = [row["action"] for row in events.json()]
        client.post("/api/auth/logout").raise_for_status()
        result["after_logout_contract_status"] = client.get("/api/contracts").status_code
    engine = build_engine(database)
    with Session(engine) as session:
        stored = session.get(Contract,result["contract_id"])
        result["persisted_in_fresh_connection"] = bool(stored.extraction and stored.extraction == result["application_body"].get("extraction"))
        result["financial_and_review_counts"] = {model.__tablename__:session.scalar(select(func.count()).select_from(model))
            for model in (InvoiceLink,InvoiceEvent,ManualInvoiceOwner,ReviewEvent)}
    engine.dispose()
    provider = observed.get("provider")
    attempts = getattr(provider,"last_attempts",[])
    last = attempts[-1] if attempts else {}
    result.update(model_attempts=attempts,token_usage=getattr(provider,"last_usage",None) or last.get("token_usage"),
                  selected_model=getattr(provider,"model",None),response_model=getattr(provider,"response_model",None) or last.get("response_model"))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--approved-free-tier-once",action="store_true")
    parser.add_argument("--verification-id", help="Fresh named subdirectory; requires a new explicit one-call authorization")
    args = parser.parse_args()
    if not args.approved_free_tier_once: parser.error("Separate human approval for this one Free-tier call is required")
    if args.verification_id and not re.fullmatch(r"verification-[a-z0-9-]{1,40}", args.verification_id):
        parser.error("Verification ID must be verification- followed by 1-40 lowercase letters, digits or hyphens")
    output = OUTPUT / args.verification_id if args.verification_id else OUTPUT
    source = SOURCE.read_bytes()
    if hashlib.sha256(source).hexdigest()!=SOURCE_SHA256: raise SystemExit("Approved source hash changed; no request permitted")
    output.mkdir(parents=True,exist_ok=True)
    result = dict(started_at=datetime.now(timezone.utc).isoformat(),source_bytes=len(source),source_sha256=SOURCE_SHA256,
                  approved_model=DEFAULT_MODEL,global_call_cap=1,paypal_permitted=False)
    with (output/"authorization.lock").open("x",encoding="utf-8") as handle:
        json.dump(result,handle); handle.flush(); os.fsync(handle.fileno())
    settings = {"SCOPESHIELD_AI_PROVIDER":"gemini","SCOPESHIELD_AI_MODELS":DEFAULT_MODEL,
        "SCOPESHIELD_AI_FREE_TIER_MODELS":DEFAULT_MODEL,"SCOPESHIELD_AI_ALLOW_LIVE":"true","SCOPESHIELD_AI_MAX_MODEL_CALLS":"1"}
    before = {name:os.environ.get(name) for name in settings}
    original = requests.sessions.Session.request
    budget = VerifiedRequestBudget((DEFAULT_MODEL,),1,output/"attempts.json",original)
    try:
        os.environ.update(settings)
        requests.sessions.Session.request = lambda session,method,url,**kwargs: budget.request(session,method,url,**kwargs)
        result.update(exercise_application(configured_provider,output))
    except Exception as exc:
        result["verification_error_type"] = type(exc).__name__
    finally:
        requests.sessions.Session.request = original
        for name,value in before.items():
            if value is None: os.environ.pop(name,None)
            else: os.environ[name]=value
        result.update(inference_calls=len(budget.attempts),network_attempts=budget.attempts,blocked_requests=budget.blocked_requests,
                      response_diagnostic=getattr(budget,"response_diagnostic",None),capture_error_type=getattr(budget,"capture_error_type",None),
                      process_settings_restored=all(os.environ.get(name)==value for name,value in before.items()),
                      completed_at=datetime.now(timezone.utc).isoformat())
        save(output/"result.json",result)
    print(json.dumps({key:value for key,value in result.items() if key!="application_body"},indent=2))
    return 0 if (result.get("all_checks_pass") and result.get("persisted_in_fresh_connection")
        and result.get("api_read_matches") and len(budget.attempts)==1 and budget.attempts[0]["http_status"]==200
        and result["anonymous_contract_status"]==401 and result["after_logout_contract_status"]==401
        and not any(result["financial_and_review_counts"].values())) else 1


if __name__=="__main__": raise SystemExit(main())
