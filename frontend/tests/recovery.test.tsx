import { render, screen, within, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi } from "vitest";
import { ContractWorkspace } from "@/components/contract-workspace";
import type { Contract, Extraction } from "@/lib/types";
import { contract, milestone, jsonResponse } from "./fixtures";

const extraction: Extraction = {
  provider: "offline_synthetic_fixture", model: null, mode: "offline_demo", live_ai: false,
  requires_human_review: true, milestone_total: "100.00", totals_match: true, review_issues: [],
  data: { project_title: milestone.evidence.name, currency: milestone.evidence.currency,
    total_value: milestone.evidence.amount, deliverables: milestone.evidence.deliverables, milestones: [milestone.evidence] },
};
function serve(saved: Contract) {
  vi.mocked(fetch).mockImplementation(async (url, init) => {
    const path = String(url);
    if (init?.method === "POST") return jsonResponse({ ...saved, error_code: null });
    if (path.endsWith("/milestones")) return jsonResponse({ summary: { discrepancies: [] }, milestones: saved.milestones });
    if (path.endsWith(`/contracts/${saved.id}`)) return jsonResponse(saved);
    if (path.endsWith("/sharing")) return jsonResponse([]);
    return jsonResponse([]);
  });
}

describe("retained extraction recovery", () => {
  it("explains stored input ineligibility before configured extraction", async () => {
    serve({ ...contract, status: "UPLOADED", extraction: null, milestones: [], ai_input_eligible: false, ai_input_message: "Stored successfully; configured extraction input is too large." });
    render(<ContractWorkspace id={contract.id} />);
    expect(await screen.findByText(/Stored successfully/)).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: "Extract agreement" }));
    await userEvent.selectOptions(screen.getByLabelText("Analysis provider"), "configured");
    expect(screen.getByRole("button", { name: "Run extraction" })).toBeDisabled();
  });
  it("offers an explicit forced retry for unreviewed candidates after a failed attempt", async () => {
    serve({ ...contract, extraction, review_revision: 0, error_code: "AI_TIMEOUT" });
    render(<ContractWorkspace id={contract.id} />);
    await userEvent.click(await screen.findByRole("button", { name: "Retry extraction" }));
    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveTextContent("last valid result is preserved");
    await userEvent.click(within(dialog).getByRole("button", { name: "Run extraction" }));
    await waitFor(() => expect(vi.mocked(fetch).mock.calls.some(([url, init]) =>
      String(url).endsWith("/extract") && init?.method === "POST" &&
      JSON.stringify(JSON.parse(init.body as string)) === JSON.stringify({ mode: "offline_demo", force: true }))).toBe(true));
  });
  it("keeps AI replacement disabled once a human has reviewed the retained milestones", async () => {
    serve({ ...contract, extraction, review_revision: 1, error_code: "AI_TIMEOUT" });
    render(<ContractWorkspace id={contract.id} />);
    expect(await screen.findByRole("button", { name: "Extraction saved" })).toBeDisabled();
    expect(screen.queryByRole("button", { name: "Retry extraction" })).not.toBeInTheDocument();
  });
  it("recovers an older FAILED record without replacing already reviewed content", async () => {
    serve({ ...contract, extraction, status: "FAILED", review_revision: 1, error_code: "AI_TIMEOUT" });
    render(<ContractWorkspace id={contract.id} />);
    await userEvent.click(await screen.findByRole("button", { name: "Recover saved extraction" }));
    await waitFor(() => expect(vi.mocked(fetch).mock.calls.some(([url, init]) =>
      String(url).endsWith("/extract") && init?.method === "POST" &&
      JSON.stringify(JSON.parse(init.body as string)) === JSON.stringify({ mode: "offline_demo" }))).toBe(true));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});
