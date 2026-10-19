"""Explicit offline scenario catalog; no generated answers for arbitrary input."""
import json
from pathlib import Path

from fastapi import APIRouter
from app.day6_fixture import OfflineScopeProvider, SCENARIOS
from app.extraction import ExtractionError, normalize

ROOT = Path(__file__).resolve().parents[1]
PHOTO = ROOT / "examples/day4_freelance_agreement.txt"
PHOTO_WORK = "Add a product launch video."
router = APIRouter(prefix="/api", tags=["Offline demonstrations"])


@router.get("/demo-scenarios")
def scenarios():
    definitions = [
        ("full-walkthrough", "Full product walkthrough", "One synthetic agreement: three USD 1 milestones, all three scope outcomes, human review and a separate invoice preview.", "day10_sandbox_agreement.txt", True, [row[0] for row in SCENARIOS]),
        ("sandbox-milestones", "Milestone approval and invoice", "Three synthetic USD 1 milestones. Review a milestone, then preview a sandbox draft.", "day5_sandbox_agreement.txt", True, []),
        ("photography", "Complete photography workflow", "Review milestones, then assess a proposed video. The offline answer is deliberately ambiguous.", "day4_freelance_agreement.txt", True, [PHOTO_WORK]),
        ("scope-comparison", "Three scope outcomes", "Compare included revisions, excluded checkout work and ambiguous support against the matching agreement.", "day6_freelance_agreement.txt", False, [row[0] for row in SCENARIOS]),
    ]
    from app.conversation_fixture import catalog
    from app.day19_fixture import scenario
    from app.day24_fixture import scenario as release_scenario
    return [dict(id=key, title=title, description=description, filename=filename,
        text=(ROOT / "examples" / filename).read_text(encoding="utf-8"), supports_extraction=extract,
        requested_changes=work, live_ai=False, mode="offline_demo")
        for key, title, description, filename, extract, work in definitions] + catalog() + [scenario(), release_scenario()]


class OfflineScenarioProvider:
    name = "offline_synthetic_scope_fixture"
    live_ai = False

    def compare_conversation(self, contract_text, conversation):
        from app.day24_fixture import PATH as CEDAR_PATH, compare as cedar_compare
        if normalize(contract_text) == normalize(CEDAR_PATH.read_text(encoding="utf-8")):
            return cedar_compare(contract_text, conversation)
        from app.day6_fixture import WALKTHROUGH
        if normalize(contract_text) == normalize(WALKTHROUGH.read_text(encoding="utf-8")):
            from app.day19_fixture import compare
            return compare(contract_text, conversation)
        from app.conversation_fixture import compare
        return compare(contract_text, conversation)

    def compare_scope(self, contract_text: str, requested_changes: list[str]) -> str:
        if normalize(contract_text) != normalize(PHOTO.read_text(encoding="utf-8")):
            return OfflineScopeProvider().compare_scope(contract_text, requested_changes)
        if requested_changes != [PHOTO_WORK]:
            raise ExtractionError("OFFLINE_DEMO_CONTRACT_MISMATCH", "Choose a compatible predefined offline scenario", 422)
        return json.dumps({"findings": [{"ordinal": 1, "requested_work": PHOTO_WORK, "classification": "AMBIGUOUS",
            "explanation": "The agreement lists photography deliverables; confirm the proposed video scope separately.",
            "contract_evidence": [{"source_excerpt": "Changes beyond these deliverables require separately agreed scope and fees.", "relationship": "CONTEXT"}],
            "uncertainties": ["Confirm video deliverables and acceptance criteria with the client."],
            "recommended_action": "CLARIFY_WITH_CLIENT", "requires_human_review": True}]})
