"""Demonstrate the synthetic $1,500 agreement over the real local HTTP API.

Offline fixture only: no AI subscription/API credentials, PayPal calls, or invoices.
Run again with --contract-id to retrieve the same result after restarting the server.
"""
import argparse
import json
from pathlib import Path
from urllib.parse import urlparse
from uuid import UUID

import requests

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--contract-id", type=UUID, help="Retrieve an already saved demo result; no upload or re-extraction")
    args = parser.parse_args()
    parsed = urlparse(args.base_url)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"} or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
        parser.error("Use only a local HTTP base URL")
    base = args.base_url.rstrip("/")
    records = []

    def request(step, method, path, expected=200, **kwargs):
        response = requests.request(method, base + path, timeout=60, allow_redirects=False, **kwargs)
        try:
            body = response.json()
        except ValueError:
            raise SystemExit(f"{step}: local server returned a non-JSON response") from None
        records.append({"step": step, "http_status": response.status_code, "body": body})
        print(f"{step}: HTTP {response.status_code}", flush=True)
        if response.status_code != expected:
            raise SystemExit(f"{step} failed; no automatic retry. Inspect the local API response")
        return body

    request("health", "GET", "/health")
    if args.contract_id:
        contract_id = str(args.contract_id)
    else:
        with (ROOT / "examples" / "freelance_agreement.txt").open("rb") as stream:
            uploaded = request("upload", "POST", "/api/contracts", expected=201,
                               files={"file": ("freelance_agreement.txt", stream, "text/plain")})
        contract_id = uploaded["id"]
        print(f"Extracted upload text: {len(uploaded['text'])} characters", flush=True)
        request("offline_demo_extraction", "POST", f"/api/contracts/{contract_id}/extract", json={"mode": "offline_demo"})
    saved = request("retrieve", "GET", f"/api/contracts/{contract_id}")
    extraction = saved["extraction"]
    if not extraction or extraction["mode"] != "offline_demo" or extraction["live_ai"] or extraction["totals_match"] is not True:
        raise SystemExit("Expected an explicitly labeled, validated offline demo result")
    if extraction["data"]["total_value"]["value"] != "1500.00" or len(saved["milestones"]) != 3:
        raise SystemExit("Unexpected synthetic agreement amounts or milestone count")
    request("project", "GET", f"/api/projects/{saved['project_id']}")
    for milestone in saved["milestones"]:
        print(json.dumps({
            "milestone": milestone["name"], "amount": milestone["amount"],
            "currency": milestone["currency"], "deadline": milestone["deadline"],
            "status": milestone["status"],
            "amount_source_excerpt": milestone["evidence"]["amount"]["source_excerpt"],
            "payment_source_excerpt": milestone["evidence"]["payment_conditions"]["source_excerpt"],
        }), flush=True)
    output = ROOT / ".local" / ("day3_restart_demo.json" if args.contract_id else "day3_demo.json")
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(records, indent=2), encoding="utf-8")
    print(f"Contract ID: {contract_id}")
    print("PASS: persistent offline synthetic fixture; no live AI or financial action")
    print("Full HTTP response evidence saved locally in " + str(output))


if __name__ == "__main__":
    main()
