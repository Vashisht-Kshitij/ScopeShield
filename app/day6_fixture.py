"""Explicit deterministic mocked scope findings for ONE synthetic agreement."""
import json
from pathlib import Path

from app.extraction import ExtractionError

PATH = Path(__file__).resolve().parents[1] / "examples" / "day6_freelance_agreement.txt"
WALKTHROUGH = PATH.parent / "day10_sandbox_agreement.txt"
SCENARIOS = (
    ("Change the landing page colours during the first revision round.", "IN_SCOPE",
     "Two rounds of text and colour revisions to the landing page are included.", "COVERAGE",
     "The first colour revision round is expressly included.", [], "FULFILL_EXISTING_SCOPE"),
    ("Add an online ticket checkout with payment processing.", "OUT_OF_SCOPE",
     "Online ticket sales, payment processing and shopping cart functionality are excluded from this agreement.",
     "EXCLUSION", "The requested checkout adds expressly excluded functionality.", [], "DRAFT_CHANGE_ORDER"),
    ("Provide weekend telephone launch support for a month.", "AMBIGUOUS",
     "Reasonable launch support is included; the duration and channels of support will be agreed later.",
     "CONTEXT", "The support duration and channels have not been agreed.",
     ["Confirm support hours, duration and channels with the client."], "CLARIFY_WITH_CLIENT"),
)


class OfflineScopeProvider:
    name = "offline_synthetic_scope_fixture"
    live_ai = False

    def compare_scope(self, contract_text: str, requested_changes: list[str]) -> str:
        # Whitespace-only PDF variations are allowed; altered terms are not.
        if " ".join(contract_text.split()) not in {
            " ".join(path.read_text(encoding="utf-8").split()) for path in (PATH, WALKTHROUGH)
        }:
            raise ExtractionError("OFFLINE_DEMO_CONTRACT_MISMATCH", "Unrelated synthetic scope agreement", 422)
        fixtures = {item[0]: item for item in SCENARIOS}
        if any(work not in fixtures for work in requested_changes):
            raise ExtractionError("OFFLINE_DEMO_CONTRACT_MISMATCH", "No fixture for this requested work", 422)
        return json.dumps({"findings": [
            {"ordinal": ordinal, "requested_work": work, "classification": fixtures[work][1],
             "contract_evidence": [{"source_excerpt": fixtures[work][2], "relationship": fixtures[work][3]}],
             "explanation": fixtures[work][4], "uncertainties": fixtures[work][5],
             "recommended_action": fixtures[work][6], "requires_human_review": True}
            for ordinal, work in enumerate(requested_changes, 1)
        ]})
