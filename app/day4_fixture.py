"""Explicitly offline fixture for the new synthetic approval-workflow demonstration."""
import json
from pathlib import Path

PATH = Path(__file__).resolve().parents[1] / "examples" / "day4_freelance_agreement.txt"


def fixture_output(path: Path = PATH) -> str:
    text = path.read_text(encoding="utf-8").strip()
    lines = text.splitlines()
    def fact(value, source):
        return {"value": value, "source_excerpt": source}
    def line(prefix):
        return next(item for item in lines if item.startswith(prefix))
    currency = fact("USD", line("Currency:"))
    result = {
        "project_title": fact(line("Project:").split(": ", 1)[1].rstrip("."), line("Project:")),
        "currency": currency, "total_value": fact(line("Total project value:").split("USD ", 1)[1].rstrip(".").replace(",", ""), line("Total project value:")),
        "deliverables": [], "milestones": [],
        "review_required_fields": [] if path.name == "day10_sandbox_agreement.txt" else ["milestones[2].payment_conditions", "milestones[2].acceptance_criteria", "milestones[2].deadline"],
    }
    for paragraph in text.split("\n\n"):
        if not paragraph.startswith("Milestone "):
            continue
        fields = paragraph.splitlines()
        def value(prefix):
            source = next(item for item in fields if item.startswith(prefix))
            return fact(source.split(": ", 1)[1].rstrip("."), source)
        deadline = value("Deadline:")
        if deadline["value"] == "To be agreed":
            deadline = {"value": None, "source_excerpt": None}
        amount = next(item for item in fields if item.startswith("Amount:"))
        milestone = {
            "name": fact(fields[0].split(": ", 1)[1].rstrip("."), fields[0]),
            "amount": fact(amount.split("USD ", 1)[1].rstrip("."), amount),
            "currency": currency, "payment_conditions": value("Payment condition:"),
            "deadline": deadline, "deliverables": [value("Deliverable:")],
            "acceptance_criteria": [value("Acceptance criterion:")],
        }
        result["milestones"].append(milestone)
        result["deliverables"].extend(milestone["deliverables"])
    return json.dumps(result)
