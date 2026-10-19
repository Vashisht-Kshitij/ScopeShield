"""Actual local HTTP demo; exactly one explicit USD 1 sandbox draft, never send/pay.

Default creates a new offline synthetic contract, approves one row and previews.
--create-draft explicitly performs creation. --contract-id retrieves only, unless
--create-draft is specified for a previously prepared, unlinked approved contract.
"""
import argparse
import json
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlparse
from uuid import UUID, uuid4

import requests

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract-id", type=UUID)
    parser.add_argument("--create-draft", action="store_true")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    args = parser.parse_args()
    parsed = urlparse(args.base_url)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"} or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
        parser.error("Use a loopback HTTP base URL only")
    output = ROOT / ".local" / ("day5_restart_demo.json" if args.contract_id and not args.create_draft else "day5_demo.json")
    output.parent.mkdir(exist_ok=True)
    records = []
    base = args.base_url.rstrip("/")

    def request(step, method, path, expected=200, **kwargs):
        result = requests.request(method, base + path, timeout=100, allow_redirects=False, **kwargs)
        body = result.json()
        records.append({"step": step, "http_status": result.status_code, "body": body})
        output.write_text(json.dumps(records, indent=2), encoding="utf-8")
        print(f"{step}: HTTP {result.status_code}", flush=True)
        if result.status_code != expected:
            raise SystemExit(f"{step} failed; inspect local response evidence. No create retry or alternate extraction was performed")
        return body

    def context(row):
        return {"expected_revision": row["revision"], "expected_contract_revision": row["contract_review_revision"],
                "reviewer_label": "Authorized Day 5 synthetic demo", "note": "Explicit sandbox demonstration requested by the local operator"}

    request("health", "GET", "/health")
    if args.contract_id:
        cid = str(args.contract_id)
    else:
        sample = ROOT / "examples" / "day5_sandbox_agreement.txt"
        with sample.open("rb") as stream:
            saved = request("upload", "POST", "/api/contracts", expected=201, files={"file": (sample.name, stream, "text/plain")})
        cid = saved["id"]
        request("offline_extraction", "POST", f"/api/contracts/{cid}/extract", json={"mode": "offline_demo"})
    contract = request("contract", "GET", f"/api/contracts/{cid}")
    rows = contract["milestones"]
    assert len(rows) == 3 and all(Decimal(row["amount"]) == Decimal("1.00") for row in rows)
    row = rows[0]
    if row["status"] != "APPROVED" and (not args.contract_id or args.create_draft):
        row = request("approve_first", "POST", f"/api/milestones/{row['id']}/approve",
                      json={**context(row), "confirmed": True, "acknowledge_review_flags": True})
    prefix = f"/api/milestones/{row['id']}/invoice"
    view = request("preview", "GET", prefix + "/preview")
    assert Decimal(view["amount"]) == Decimal("1.00") and view["currency"] == "USD"
    if args.create_draft:
        body = {**context(row), "contract_id": cid, "idempotency_key": str(uuid4()), "preview_digest": view["preview_digest"],
                "confirmed": True, "acknowledge_discrepancies": True,
                "discrepancy_note": "Partial sandbox billing: only the USD 1 sketch is approved for this single draft; the other USD 2 remains unapproved. No send or payment is authorized"}
        link = request("create_one_draft", "POST", prefix, expected=201, json=body)
        assert link["paypal_status"] == "DRAFT" and link["verified"] is True
        replay = request("idempotent_replay", "POST", prefix, json=body)
        assert replay["invoice_id"] == link["invoice_id"]
        # Demonstrate the rejection path without approving another row.
        other = request("current_milestones", "GET", f"/api/contracts/{cid}/milestones")["milestones"][1]
        assert other["status"] == "REVIEW_REQUIRED"
        request("unapproved_creation_blocked", "POST", f"/api/milestones/{other['id']}/invoice", expected=409,
                json={**context(other), "contract_id": cid, "idempotency_key": str(uuid4()), "preview_digest": "a" * 64, "confirmed": True})
    elif not args.contract_id:
        print("Prepared and previewed only; no PayPal invoice was created")
        print("Contract ID: " + cid)
        return
    linked = request("linked_invoice", "GET", prefix)
    actual = request("real_paypal_read", "GET", "/api/invoices/" + linked["invoice_id"])
    assert actual["status"] == "DRAFT" and Decimal(actual["total_amount"]) == Decimal("1.00")
    history = request("invoice_history", "GET", prefix + "/history")
    print(json.dumps({"contract_id": cid, "milestone_id": row["id"], "invoice_id": linked["invoice_id"],
                      "paypal_status": actual["status"], "amount": actual["total_amount"], "provider": contract["extraction"]["provider"],
                      "live_ai": contract["extraction"]["live_ai"], "audit_actions": [item["action"] for item in history]}, indent=2))
    print("PASS: one persisted USD 1 sandbox draft; explicit creation, duplicate protection, no send/payment")
    print("Contract ID: " + cid)
    print("Local HTTP evidence: " + str(output))


if __name__ == "__main__":
    main()
