"""One separately authorized synthetic request through authenticated upload/extraction.

No billing/account API, retry, fallback or PayPal capability. The explicit flag may
be used only after the human confirms this project's existing Free tier and approves
ONE request. Output contains synthetic results and sanitized metadata, never keys.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import secrets
import requests

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.auth import hash_password
from app.extraction import get_extraction_provider
from app.gemini import configured_provider
from app.main import create_app
from app.storage import User

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "frontend/public/samples/day4_freelance_agreement.txt"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--approved-free-tier-once", action="store_true")
    args = parser.parse_args()
    if not args.approved_free_tier_once:
        parser.error("Explicit approval of one request on the existing Free tier is required")
    result_path = ROOT / ".local/day8/gemini-result.json"
    marker = ROOT / ".local/day8/gemini-attempt.lock"
    marker.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation prevents accidental reruns, even after failed inference.
    with marker.open("x") as handle:
        handle.write(datetime.now(timezone.utc).isoformat())
    os.environ.update(SCOPESHIELD_AI_PROVIDER="gemini", SCOPESHIELD_AI_MODEL="gemini-2.5-flash-lite",
                      SCOPESHIELD_AI_ALLOW_LIVE="true", SCOPESHIELD_AI_MAX_ATTEMPTS="1")
    provider = configured_provider()
    app = create_app(storage_url="sqlite:///" + (ROOT / ".local/day8_live.db").as_posix())
    app.dependency_overrides[get_extraction_provider] = lambda: provider
    result = dict(model="gemini-2.5-flash-lite", max_attempts=1, synthetic_only=True, free_tier_confirmed_by_user=True)
    original_request = requests.request
    result["provider_requests"] = 0
    def one_request(method, url, **kwargs):
        if method != "POST" or url != "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-lite:generateContent" or result["provider_requests"]:
            raise AssertionError("Only one authorized Gemini generation request is permitted")
        result["provider_requests"] += 1
        upstream = original_request(method, url, **kwargs)
        result["upstream_http_status"] = upstream.status_code
        return upstream
    requests.request = one_request
    try:
        with TestClient(app, base_url="http://127.0.0.1:8001") as client:
            password = secrets.token_urlsafe(32)
            with Session(app.state.engine) as session:
                session.add(User(username="live.synthetic", display_name="Synthetic verification", password_hash=hash_password(password), role="freelancer"))
                session.commit()
            preflight = client.get("/api/auth/csrf").json()
            login = client.post("/api/auth/login", headers={"X-CSRF-Token": preflight["csrf_token"]}, json={"username": "live.synthetic", "password": password})
            del password
            login.raise_for_status()
            client.headers["X-CSRF-Token"] = login.json()["csrf_token"]
            uploaded = client.post("/api/contracts", files={"file": (SOURCE.name, SOURCE.read_bytes(), "text/plain")})
            uploaded.raise_for_status()
            contract_id = uploaded.json()["id"]
            response = client.post(f"/api/contracts/{contract_id}/extract", json={"mode": "configured"})
            result.update(http_status=response.status_code, body=response.json(), usage=getattr(provider, "last_usage", None), response_model=getattr(provider, "response_model", None))
            if response.is_success:
                body = response.json()
                source = body["text"]
                checks = []
                def walk(value):
                    if isinstance(value, dict):
                        if value.get("source_excerpt"):
                            checks.append(source[value.get("source_start", 0):value.get("source_end", 0)] == value["source_excerpt"])
                        for child in value.values(): walk(child)
                    elif isinstance(value, list):
                        for child in value: walk(child)
                walk(body["extraction"]["data"])
                result.update(validated_schema=True, evidence_count=len(checks), all_saved_offsets_match=all(checks), all_milestones_require_review=all(row["status"] == "REVIEW_REQUIRED" for row in body["milestones"]))
            client.post("/api/auth/logout")
    finally:
        requests.request = original_request
        os.environ["SCOPESHIELD_AI_ALLOW_LIVE"] = "false"
        result_path.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    print(json.dumps({key: value for key, value in result.items() if key != "body"}, indent=2))


if __name__ == "__main__":
    main()
