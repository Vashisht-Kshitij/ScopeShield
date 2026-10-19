"""Run the synthetic review workflow over the actual loopback HTTP API.

Default: explicit offline fixture. --mode live requires --quota-confirmed and
locally configured Gemini credentials/opt-in; it never silently falls back.
--contract-id only retrieves and verifies persisted decisions after a restart.
"""
import argparse
import json
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlparse
from uuid import UUID

import requests

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["offline", "live"], default="offline")
    parser.add_argument("--quota-confirmed", action="store_true", help="Operator has checked this Gemini project's billing tier and active quota")
    parser.add_argument("--contract-id", type=UUID, help="Verify an existing demo; do not upload, extract, or change decisions")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    args = parser.parse_args()
    parsed = urlparse(args.base_url)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"} or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
        parser.error("Use only a local HTTP base URL")
    if args.mode == "live" and not args.contract_id and not args.quota_confirmed:
        parser.error("Live inference requires --quota-confirmed after reviewing the project's actual quota and billing tier")
    base = args.base_url.rstrip("/")
    records = []
    output = ROOT / ".local" / ("day4_restart_demo.json" if args.contract_id else "day4_demo.json")
    output.parent.mkdir(exist_ok=True)

    def request(step, method, path, expected=200, **kwargs):
        result = requests.request(method, base + path, timeout=90, allow_redirects=False, **kwargs)
        try:
            body = result.json()
        except ValueError:
            raise SystemExit(f"{step}: server returned a non-JSON response") from None
        records.append({"step": step, "http_status": result.status_code, "body": body})
        output.write_text(json.dumps(records, indent=2), encoding="utf-8")
        print(f"{step}: HTTP {result.status_code}", flush=True)
        if result.status_code != expected:
            raise SystemExit(f"{step} failed. No retry or automatic offline substitution was made; inspect the saved API response")
        return body

    def state(contract_id):
        return request("milestones", "GET", f"/api/contracts/{contract_id}/milestones")

    def context(row):
        return {"expected_revision": row["revision"], "expected_contract_revision": row["contract_review_revision"],
                "reviewer_label": "Authorized synthetic Day 4 demo", "note": "Synthetic workflow demonstration; sources and discrepancies explicitly reviewed"}

    request("health", "GET", "/health")
    if args.contract_id:
        contract_id = str(args.contract_id)
    else:
        sample = ROOT / "examples" / "day4_freelance_agreement.txt"
        with sample.open("rb") as stream:
            uploaded = request("upload", "POST", "/api/contracts", expected=201, files={"file": (sample.name, stream, "text/plain")})
        contract_id = uploaded["id"]
        extracted = request("extraction", "POST", f"/api/contracts/{contract_id}/extract",
                            json={"mode": "configured" if args.mode == "live" else "offline_demo"})
        if bool(extracted["extraction"]["live_ai"]) != (args.mode == "live"):
            raise SystemExit("Provider provenance does not match the requested demonstration mode")
        rows = state(contract_id)["milestones"]
        if len(rows) != 3:
            raise SystemExit("The new synthetic agreement must produce three milestones before review")
        row = rows[0]
        request("edit_first_milestone", "PATCH", f"/api/milestones/{row['id']}", json={
            **context(row), "changes": {"amount": "650.00", "description": "Planning and shot list with a human-reviewed styling checklist"},
        })
        for index, action in [(0, "approve"), (1, "approve"), (2, "reject")]:
            row = state(contract_id)["milestones"][index]
            request(action + f"_{index + 1}", "POST", f"/api/milestones/{row['id']}/{action}", json={
                **context(row), "confirmed": True, "acknowledge_review_flags": True,
            })
    saved = request("retrieve", "GET", f"/api/contracts/{contract_id}")
    current = state(contract_id)
    history = request("approval_history", "GET", f"/api/contracts/{contract_id}/approval-history")
    assert [row["status"] for row in current["milestones"]] == ["APPROVED", "APPROVED", "REJECTED"]
    assert Decimal(current["milestones"][0]["amount"]) == Decimal("650.00")
    assert Decimal(saved["original_ai_output"]["milestones"][0]["amount"]["value"]) == Decimal("600.00")
    assert [event["action"] for event in history] == ["EDITED", "APPROVED", "APPROVED", "REJECTED"]
    assert "MILESTONE_TOTAL_MISMATCH" in current["summary"]["discrepancies"]
    print(json.dumps({"contract_id": contract_id, "provider": saved["extraction"]["provider"],
                      "model": saved["extraction"]["model"], "live_ai": saved["extraction"]["live_ai"],
                      "milestones": [{"name": row["name"], "amount": row["amount"], "status": row["status"], "revision": row["revision"],
                                      "original_amount_source": row["evidence"]["amount"]["source_excerpt"]} for row in current["milestones"]],
                      "summary": current["summary"], "audit_events": len(history)}, indent=2))
    print("PASS: explicit, persistent synthetic review decisions; no PayPal action")
    print("Contract ID: " + contract_id)
    print("HTTP evidence saved locally in " + str(output))


if __name__ == "__main__":
    main()
