"""Run the three explicit mocked scenarios in an isolated persistent local DB.

Uses real FastAPI routing in-process, not live inference or an external HTTP server.
Run --verify-only in another process to verify the saved responses after restart.
"""
import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import requests
from fastapi.testclient import TestClient

from app.day6_fixture import PATH, SCENARIOS
from app.dependencies import get_paypal
from app.main import create_app

EVIDENCE = ROOT / ".local" / "day6_demo.json"
DATABASE = ROOT / ".local" / "day6_demo.db"
LABEL = {"reviewer_label": "Offline demo operator", "note": "Save explicitly mocked synthetic scope evidence"}


def forbidden(*args, **kwargs):
    raise RuntimeError("Day 6 demonstration prohibits all external provider calls")


def checked(response, status=200):
    if response.status_code != status:
        raise RuntimeError(f"Unexpected local API response: {response.status_code} {response.text}")
    return response.json()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify-only", action="store_true", help="Read saved results only; create nothing")
    args = parser.parse_args()
    # Process-only gates plus HTTP denial. Local .env and running servers are untouched.
    os.environ["SCOPESHIELD_AI_PROVIDER"] = "disabled"
    os.environ["SCOPESHIELD_AI_ALLOW_LIVE"] = "false"
    requests.sessions.Session.request = forbidden
    application = create_app(storage_url="sqlite:///" + DATABASE.as_posix())
    application.dependency_overrides[get_paypal] = forbidden
    with TestClient(application, base_url="http://127.0.0.1:8000") as client:
        if args.verify_only:
            saved = json.loads(EVIDENCE.read_text(encoding="utf-8"))
            assert checked(client.get(f"/api/contracts/{saved['contract']['id']}")) == saved["contract"]
            for scenario in saved["scenarios"]:
                path = f"/api/change-requests/{scenario['request']['id']}"
                assert checked(client.get(path)) == scenario["request"]
                assert checked(client.get(path + "/history")) == scenario["history"]
            print("PASS: all three saved requests, grounded analyses and histories match in a new process.")
            return
        contract = checked(client.post("/api/contracts", files={"file": (PATH.name, PATH.read_bytes(), "text/plain")}), 201)
        scenarios = []
        print("MOCKED RESPONSES: deterministic offline fixtures; live AI = false; external calls = 0.")
        print(f"Contract: {contract['id']} (isolated .local/day6_demo.db)")
        for scenario_name, fixture in zip(("A", "B", "C"), SCENARIOS):
            request = checked(client.post(f"/api/contracts/{contract['id']}/change-requests", json={
                **LABEL, "project_id": contract["project_id"], "title": "Scenario " + scenario_name,
                "requested_changes": [fixture[0]],
            }), 201)
            path = f"/api/change-requests/{request['id']}"
            saved = checked(client.post(path + "/analyze", json={**LABEL, "expected_revision": request["revision"], "mode": "offline_demo"}))
            finding = saved["analysis"]["findings"][0]
            assert finding["classification"] == fixture[1]
            assert saved["status"] == "REVIEW_REQUIRED" and saved["analysis"]["live_ai"] is False
            for excerpt in finding["contract_evidence"]:
                assert contract["text"][excerpt["source_start"]:excerpt["source_end"]] == excerpt["source_excerpt"]
            assert checked(client.get(path)) == saved
            history = checked(client.get(path + "/history"))
            scenarios.append({"name": scenario_name, "request": saved, "history": history})
            print(f"{scenario_name}: {finding['classification']} | {saved['status']} | {finding['recommended_action']}")
            print("  Evidence: " + finding["contract_evidence"][0]["source_excerpt"])
        EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
        EVIDENCE.write_text(json.dumps({"mocked": True, "live_ai": False, "external_calls": 0,
                                       "contract": contract, "scenarios": scenarios}, indent=2) + "\n", encoding="utf-8")
        print("Saved full results to ignored .local/day6_demo.json. Run again with --verify-only.")


if __name__ == "__main__":
    main()
