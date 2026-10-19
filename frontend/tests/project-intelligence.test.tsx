import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { ProjectIntelligence, ProjectSelector, type Intelligence } from "@/components/project-intelligence";
import { EvidencePack } from "@/components/evidence-pack";
import { jsonResponse } from "./fixtures";
import { Catalog } from "@/components/catalog";

const data: Intelligence = {
  project_title: "Synthetic project", contracts: [{ contract_id: "c1", name: "Synthetic agreement", value: "3.00", currency: "USD", origin: "OFFLINE / SIMULATED" }],
  milestones: { total: 3, approved: 1, awaiting_review: 2, rejected: 0, approved_value: { amounts: [{ currency: "USD", value: "1.00" }, { currency: "EUR", value: "0.30" }], unknown_terms: 1 }, awaiting_value: { amounts: [{ currency: "USD", value: "2.00" }], unknown_terms: 0 } },
  outstanding: { value: null, reason: "Cannot determine real balance from sandbox history" },
  classifications: { IN_SCOPE: 0, OUT_OF_SCOPE: 1, AMBIGUOUS: 0, UNRESOLVED: 0 },
  requests: [{ id: "r1", contract_id: "c1", title: "Synthetic expansion", status: "REVIEW_REQUIRED", classification: "OUT_OF_SCOPE", human_reviewed: false, origin: "OFFLINE / SIMULATED", cumulative_classification: "OUT_OF_SCOPE" }],
  orders: [{ id: "o1", contract_id: "c1", status: "REVIEW_REQUIRED", freelancer_approved: false, price: "75.00", currency: "USD", client_decision: "Freelancer approval pending", current_responses: {}, stale_responses: 1 }],
  order_counts: { approved: 0, proposed: 1, rejected: 0 },
  invoice_groups: [{ origin: "SIMULATED", local_state: "CREATED", provider_status: "PAID", count: 1 }],
  issues: [{ contract_id: "c1", tab: "scope", title: "Review scope", reason: "Clarify evidence" }], recent_events: [{ id: "e1", kind: "Invoice", action: "SIMULATED_HISTORY_IMPORTED", created_at: "2026-10-18T12:00:00Z", simulation: true }], event_count: 1, simulated: true,
  next_action: { label: "Review milestone candidates", contract_id: "c1", tab: "milestones", reason: "Two await review" }, methodology: "No provider is contacted",
};
const preview = { project_id: "p1", source_revision: "a".repeat(64), generated_at: "2026-10-18T12:00:00Z", intelligence: data, counts: { contracts: 1, milestones: 3, requests: 1, orders: 1, invoices: 1 }, warnings: ["Private data"], sections: ["01 / Project snapshot"] };

it("renders exact server totals, unknown balance and separate local/simulated provider states", async () => {
  vi.mocked(fetch).mockResolvedValue(jsonResponse(preview)); render(<ProjectIntelligence projectId="p1" />);
  expect(await screen.findByRole("heading", { name: "Synthetic project" })).toBeVisible();
  expect(screen.getByText("USD 1.00 / EUR 0.30")).toBeVisible();
  expect(screen.getByText("Not determined", { exact: true })).toBeVisible();
  expect(screen.getAllByText("SIMULATED — NO REAL PAYMENT")).toHaveLength(2);
  expect(screen.getByText("Local creation state: CREATED")).toBeVisible();
  expect(screen.getByText("1 record(s) · provider PAID")).toBeVisible();
  expect(screen.getByText("Proposed terms — not agreed")).toBeVisible();
  expect(screen.getByRole("link", { name: /Continue review/ })).toHaveAttribute("href", "/contracts/c1?tab=milestones");
  expect(fetch).toHaveBeenCalledOnce();
});
it.each([401, 403, 404])("shows no private metrics or actions after access denial %s", async (status) => {
  vi.mocked(fetch).mockResolvedValue(jsonResponse({ detail: "Access denied" }, status)); render(<ProjectIntelligence projectId="foreign" />);
  expect(await screen.findByRole("alert")).toHaveTextContent("Access denied");
  expect(screen.queryByText("USD 3.00")).toBeNull(); expect(screen.queryByRole("link", { name: /Continue review/ })).toBeNull();
});
it("does not merge multiple agreement versions or execute title markup", async () => {
  vi.mocked(fetch).mockResolvedValue(jsonResponse({ ...preview, intelligence: { ...data, project_title: '<img src=x onerror="bad()">', contracts: [...data.contracts, { ...data.contracts[0], contract_id: "c2" }] } }));
  const { container } = render(<ProjectIntelligence projectId="p1" />);
  expect(await screen.findByText("2 separate agreements")).toBeVisible();
  expect(screen.getByText("Values are listed below; versions are never summed")).toBeVisible(); expect(container.querySelector("img")).toBeNull();
});
it("selects the owner's project and does not silently switch back while paging", async () => {
  vi.mocked(fetch).mockImplementation(async (input) => String(input).includes("evidence-pack") ? jsonResponse(preview) : jsonResponse([{ id: "p1", title: "Synthetic project", contract_ids: ["c1"] }, { id: "p2", title: "Second project", contract_ids: [] }]));
  render(<ProjectSelector />); expect(await screen.findByRole("heading", { name: "Synthetic project" })).toBeVisible();
  await userEvent.selectOptions(screen.getByLabelText("Project overview"), "p2");
  await waitFor(() => expect(fetch).toHaveBeenLastCalledWith(expect.stringContaining("/projects/p2/evidence-pack"), expect.objectContaining({ credentials: "include" })));
});
it("downloads the executive summary only after an explicit click with snapshot revision", async () => {
  vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(preview)).mockResolvedValueOnce(new Response("%PDF-fixture", { headers: { "Content-Type": "application/pdf" } }));
  const create = vi.fn(() => "blob:private");
  vi.stubGlobal("URL", class extends URL { static createObjectURL = create; static revokeObjectURL = vi.fn(); });
  const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
  render(<EvidencePack projectId="p1" />);
  await userEvent.click(await screen.findByRole("button", { name: "Download executive summary" }));
  await waitFor(() => expect(click).toHaveBeenCalledOnce());
  expect(fetch).toHaveBeenLastCalledWith(expect.stringContaining(`expected_revision=${preview.source_revision}&executive=true`), expect.objectContaining({ credentials: "include", cache: "no-store" }));
  expect(create).toHaveBeenCalledOnce();
});

it("waits for the bounded overview evidence check before enabling upload", async () => {
  let finish!: (response: Response) => void;
  const pending = new Promise<Response>((resolve) => { finish = resolve; });
  vi.mocked(fetch).mockImplementation(async (input) => String(input).includes("evidence-pack") ? pending : String(input).includes("/projects?") ? jsonResponse([{ id: "p1", title: "Synthetic project", contract_ids: ["c1"] }]) : jsonResponse([]));
  render(<Catalog overview />);
  const upload = screen.getByRole("button", { name: "Upload agreement" });
  expect(upload).toBeDisabled();
  await waitFor(() => expect(fetch).toHaveBeenCalledWith(expect.stringContaining("evidence-pack"), expect.anything()));
  finish(jsonResponse(preview));
  await waitFor(() => expect(upload).toBeEnabled());
});

it("warns about export capacity and never replaces an oversized project's summary with partial totals", async () => {
  vi.mocked(fetch).mockImplementation(async (input) => String(input).includes("evidence-pack")
    ? jsonResponse({ detail: { code: "REPORT_TOO_LARGE", message: "Project exceeds report record limits; no partial report was generated." } }, 422)
    : jsonResponse([{ id: "p1", title: "Large synthetic project", contract_ids: Array.from({ length: 11 }, (_, i) => `c${i}`) }]));
  render(<ProjectSelector />);
  expect(await screen.findByText(/11 agreements in this project/)).toBeVisible();
  expect(await screen.findByRole("alert")).toHaveTextContent("no partial report");
  expect(screen.queryByText("USD 1.00 / EUR 0.30")).toBeNull();
  expect(screen.getByText(/Larger projects remain browseable in Contracts/)).toBeVisible();
});
