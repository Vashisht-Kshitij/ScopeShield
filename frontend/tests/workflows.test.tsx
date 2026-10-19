import { render, screen, within, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi } from "vitest";
import { AppShell } from "@/components/shell";
import { Catalog } from "@/components/catalog";
import { UploadDialog } from "@/components/upload";
import { MilestoneEditor } from "@/components/milestones";
import { FindingCard, AnalysisEditor, ScopePanel } from "@/components/scope";
import { OrderEditor } from "@/components/orders";
import { AuditTimeline, SourcePanel } from "@/components/ui";
import { contract, milestone, finding, changeRequest, summary, jsonResponse, source } from "./fixtures";
import { routerPush } from "./setup";

describe("navigation and API states", () => {
  it("provides real navigation, current-page state and backend connection status", async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse({ status: "ok" }));
    render(<AppShell><h1>Workspace test</h1></AppShell>);
    expect(screen.getByRole("link", { name: "Contracts" })).toHaveAttribute("href", "/contracts");
    expect(screen.getByRole("link", { name: "Contracts" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("link", { name: "Scope changes" })).toHaveAttribute("href", "/scope-changes");
    expect(await screen.findByText("Backend connected")).toBeInTheDocument();
  });
  it("renders persisted catalog data and filters by name", async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse([summary]));
    render(<Catalog />);
    expect(await screen.findByText("Synthetic website")).toBeInTheDocument();
    await userEvent.type(screen.getByRole("textbox", { name: "Search agreements" }), "not present");
    expect(screen.getByText("No matching agreements")).toBeInTheDocument();
  });
  it("shows loading and meaningful backend failure without inventing records", async () => {
    let resolve!: (value: Response) => void;
    vi.mocked(fetch).mockImplementation(() => new Promise((done) => { resolve = done; }));
    render(<Catalog />);
    expect(screen.getByRole("status")).toHaveTextContent("Loading your workspace");
    resolve(jsonResponse({ detail: "Local contract storage is unavailable" }, 503));
    expect(await screen.findByRole("alert")).toHaveTextContent("Local contract storage is unavailable");
    expect(screen.queryByText("Synthetic website")).not.toBeInTheDocument();
  });
});

describe("contract uploads", () => {
  it("sends a selected file as multipart to the actual upload route", async () => {
    vi.mocked(fetch).mockImplementation(async (url) => String(url).endsWith("/projects?limit=100") ? jsonResponse([]) : jsonResponse(contract, 201));
    render(<UploadDialog />);
    await userEvent.click(screen.getByRole("button", { name: "Upload agreement" }));
    const dialog = screen.getByRole("dialog");
    const file = new File(["Synthetic agreement"], "synthetic.txt", { type: "text/plain" });
    await userEvent.upload(within(dialog).getByLabelText("Agreement file"), file);
    await userEvent.click(within(dialog).getByRole("button", { name: "Upload agreement" }));
    await waitFor(() => expect(routerPush).toHaveBeenCalledWith("/contracts/contract-1"));
    const call = vi.mocked(fetch).mock.calls.find(([url, init]) => String(url).endsWith("/api/contracts") && init?.method === "POST");
    expect(call?.[1]?.body).toBeInstanceOf(FormData);
    expect((call?.[1]?.body as FormData).get("file")).toBe(file);
  });
  it("rejects empty uploads before calling the backend", async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse([]));
    render(<UploadDialog />);
    await userEvent.click(screen.getByRole("button", { name: "Upload agreement" }));
    await userEvent.upload(screen.getByLabelText("Agreement file"), new File([], "empty.txt", { type: "text/plain" }));
    expect(screen.getByRole("alert")).toHaveTextContent("non-empty PDF or UTF-8 text");
    expect(within(screen.getByRole("dialog")).getByRole("button", { name: "Upload agreement" })).toBeDisabled();
    expect(vi.mocked(fetch).mock.calls.some(([, options]) => options?.method === "POST")).toBe(false);
  });
});

describe("milestone review and revisions", () => {
  it("saves human edits with exact decimal strings and both displayed revisions", async () => {
    const refresh = vi.fn(); vi.mocked(fetch).mockResolvedValue(jsonResponse({ ...milestone, amount: "120.10", revision: 3 }));
    render(<MilestoneEditor row={milestone} contract={contract} onRefresh={refresh} onSelectEvidence={vi.fn()} />);
    const amount = screen.getByLabelText("Amount"); await userEvent.clear(amount); await userEvent.type(amount, "120.10");
    await userEvent.type(screen.getByLabelText("Reviewer name"), "Test reviewer"); await userEvent.type(screen.getByLabelText("Review note"), "Checked the original terms");
    expect(screen.getByRole("button", { name: "Approve milestone" })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "Save changes for review" }));
    await waitFor(() => expect(refresh).toHaveBeenCalledOnce());
    const payload = JSON.parse(vi.mocked(fetch).mock.calls[0][1]?.body as string);
    expect(payload).toMatchObject({ expected_revision: 2, expected_contract_revision: 1, changes: { amount: "120.10" } });
  });
  it("blocks invalid monetary input in the browser", async () => {
    render(<MilestoneEditor row={milestone} contract={contract} onRefresh={vi.fn()} onSelectEvidence={vi.fn()} />);
    await userEvent.clear(screen.getByLabelText("Amount")); await userEvent.type(screen.getByLabelText("Amount"), "1.001");
    await userEvent.type(screen.getByLabelText("Reviewer name"), "Reviewer"); await userEvent.type(screen.getByLabelText("Review note"), "Check source");
    await userEvent.click(screen.getByRole("button", { name: "Save changes for review" }));
    expect(screen.getByRole("alert")).toHaveTextContent("at most two decimal places"); expect(fetch).not.toHaveBeenCalled();
  });
  it("shows stale conflicts, preserves local edits and refreshes only on request", async () => {
    const refresh = vi.fn(); vi.mocked(fetch).mockResolvedValue(jsonResponse({ detail: "Stale milestone review revision" }, 409));
    render(<MilestoneEditor row={milestone} contract={contract} onRefresh={refresh} onSelectEvidence={vi.fn()} />);
    await userEvent.clear(screen.getByLabelText("Description")); await userEvent.type(screen.getByLabelText("Description"), "Unsaved reviewed text");
    await userEvent.type(screen.getByLabelText("Reviewer name"), "Reviewer"); await userEvent.type(screen.getByLabelText("Review note"), "Checked source");
    await userEvent.click(screen.getByRole("button", { name: "Save changes for review" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Stale milestone review revision");
    expect(screen.getByLabelText("Description")).toHaveValue("Unsaved reviewed text"); expect(refresh).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "Refresh latest record" })); expect(refresh).toHaveBeenCalledOnce();
  });
  it("requires a confirmation dialog and review-flag acknowledgement to approve", async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse({ ...milestone, status: "APPROVED" }));
    render(<MilestoneEditor row={milestone} contract={contract} onRefresh={vi.fn()} onSelectEvidence={vi.fn()} />);
    await userEvent.click(screen.getByRole("button", { name: "Approve milestone" })); const dialog = screen.getByRole("dialog");
    await userEvent.type(within(dialog).getByLabelText("Reviewer name"), "Reviewer"); await userEvent.type(within(dialog).getByLabelText("Review note"), "Reviewed source and flags");
    await userEvent.click(within(dialog).getByRole("checkbox")); await userEvent.click(within(dialog).getByRole("button", { name: "Confirm approve milestone" }));
    await waitFor(() => expect(fetch).toHaveBeenCalled());
    expect(JSON.parse(vi.mocked(fetch).mock.calls[0][1]?.body as string)).toMatchObject({ expected_revision: 2, confirmed: true, acknowledge_review_flags: true });
    expect(String(vi.mocked(fetch).mock.calls[0][0])).toContain("/milestones/milestone-1/approve");
  });
});

describe("scope assessment and original source", () => {
  it.each(["IN_SCOPE", "OUT_OF_SCOPE", "AMBIGUOUS"] as const)("renders %s classification and verified evidence", (classification) => {
    render(<FindingCard finding={{ ...finding, classification }} text={source} onSelectEvidence={vi.fn()} />);
    expect(screen.getByText(classification.toLowerCase().replaceAll("_", " ").replace(/^./, (value) => value.toUpperCase()))).toBeInTheDocument();
    expect(screen.getByText("Verified source excerpt")).toBeInTheDocument(); expect(screen.getByText("Online payments are excluded.")).toBeInTheDocument();
  });
  it("highlights the actual source slice and does not trust forged offsets", () => {
    render(<SourcePanel text={source} filename="synthetic.txt" selected={{ ...finding.contract_evidence[0], source_start: 0, source_end: 1 }} />);
    expect(document.querySelector("mark")).toHaveTextContent("Online payments are excluded.");
  });
  it("lets keyboard users focus a verified source excerpt without leaving the page", async () => {
    render(<SourcePanel text={source} filename="synthetic.txt" selected={finding.contract_evidence[0]} />);
    await userEvent.click(screen.getByRole("link", { name: "Jump to excerpt" }));
    expect(document.querySelector("mark")).toHaveFocus();
  });
  it("labels nonmatching evidence unverified instead of fabricating a match", () => {
    render(<FindingCard finding={{ ...finding, contract_evidence: [{ source_excerpt: "Fabricated clause", relationship: "COVERAGE" }] }} text={source} onSelectEvidence={vi.fn()} />);
    expect(screen.getByText("Unverified excerpt")).toBeInTheDocument(); expect(screen.queryByRole("button", { name: /Locate in agreement/ })).not.toBeInTheDocument();
  });
  it("allows human edits while preserving the complete findings schema", async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse(changeRequest));
    render(<AnalysisEditor row={{ ...changeRequest, status: "REVIEW_REQUIRED" }} onRefresh={vi.fn()} />);
    await userEvent.click(screen.getByRole("button", { name: "Edit assessment" }));
    await userEvent.clear(screen.getByLabelText("Explanation")); await userEvent.type(screen.getByLabelText("Explanation"), "The explicit exclusion requires a separate review.");
    await userEvent.type(screen.getByLabelText("Reviewer name"), "Reviewer"); await userEvent.type(screen.getByLabelText("Review note"), "Checked the original exclusion");
    await userEvent.click(screen.getByRole("button", { name: "Save reviewed assessment" }));
    await waitFor(() => expect(fetch).toHaveBeenCalled());
    const body = JSON.parse(vi.mocked(fetch).mock.calls[0][1]?.body as string);
    expect(body.expected_revision).toBe(4); expect(body.analysis.findings[0]).toMatchObject({ ordinal: 1, requested_work: finding.requested_work, requires_human_review: true });
  });
  it("shows unavailable configured AI and preserves the saved request in the view", async () => {
    vi.mocked(fetch).mockImplementation(async (url) => String(url).endsWith("/analyze")
      ? jsonResponse({ detail: { code: "AI_PROVIDER_UNAVAILABLE", message: "Scope analysis failed; the request is preserved" } }, 503)
      : jsonResponse([{ ...changeRequest, status: "PENDING", analysis: null }]));
    render(<ScopePanel contract={contract} version={0} onRefresh={vi.fn()} onSelectEvidence={vi.fn()} />);
    await screen.findByRole("button", { name: "Analyze scope" });
    await userEvent.selectOptions(screen.getByLabelText("Analysis provider"), "configured");
    await userEvent.type(screen.getByLabelText("Reviewer name"), "Reviewer"); await userEvent.type(screen.getByLabelText("Review note"), "Check provider failure");
    await userEvent.click(screen.getByRole("button", { name: "Analyze scope" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("AI_PROVIDER_UNAVAILABLE");
    expect(screen.getByRole("heading", { name: "Add checkout" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Try loading again" })).toBeInTheDocument();
  });
});

describe("change orders and recorded history", () => {
  it("leaves price and deadline empty and sends only explicitly entered human terms", async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse({ id: "order-1" }, 201));
    render(<OrderEditor row={changeRequest} contract={contract} order={null} onRefresh={vi.fn()} onSelectEvidence={vi.fn()} />);
    expect(screen.getByLabelText("Proposed price (optional)")).toHaveValue(""); expect(screen.getByLabelText("Proposed deadline (optional)")).toHaveValue("");
    await userEvent.type(screen.getByLabelText("Proposed price (optional)"), "200.10");
    await userEvent.type(screen.getByLabelText("Proposed deadline (optional)"), "2026-12-01");
    await userEvent.type(screen.getByLabelText("Reviewer name"), "Reviewer"); await userEvent.type(screen.getByLabelText("Review note"), "Human proposed the additional terms");
    await userEvent.click(screen.getByRole("button", { name: "Prepare review draft" }));
    await waitFor(() => expect(fetch).toHaveBeenCalled());
    const payload = JSON.parse(vi.mocked(fetch).mock.calls[0][1]?.body as string);
    expect(payload).toMatchObject({ expected_revision: 4, terms: { proposed_price: "200.10", currency: "USD", proposed_deadline: "2026-12-01" } });
    expect(String(vi.mocked(fetch).mock.calls[0][0])).not.toContain("invoice");
  });
  it("renders only supplied backend audit events with exact revisions and notes", () => {
    render(<AuditTimeline events={[{ id: "event-1", entity_id: "milestone-1", kind: "milestone", action: "MILESTONE_APPROVED", created_at: contract.created_at, revision: 3, reviewer_label: "Reviewer", note: "Verified the original clause", details: { status: "APPROVED" } }]} />);
    expect(screen.getByText("Milestone approved")).toBeInTheDocument(); expect(screen.getByText("Revision 3")).toBeInTheDocument();
    expect(screen.getByText("Verified the original clause")).toBeInTheDocument(); expect(screen.queryByText("Invoice created")).not.toBeInTheDocument();
  });
  it("edits a draft using the order revision and current parent revision", async () => {
    const order = { id: "order-1", change_request_id: "request-1", project_id: contract.project_id, contract_id: contract.id,
      contract_filename: contract.filename, proposed_deliverables: [finding.requested_work], proposed_price: "200.00", currency: "USD", proposed_deadline: null,
      supporting_evidence: finding.contract_evidence, uncertainties: [], status: "REVIEW_REQUIRED" as const,
      revision: 2, request_revision: 6, analysis_revision: 3, terms_origin: "human_entered" as const,
      requires_human_review: true as const, created_at: contract.created_at, updated_at: contract.updated_at, reviewed_at: null, reviewer_label: null };
    vi.mocked(fetch).mockImplementation(async (url) => String(url).endsWith("/client-responses") ? jsonResponse({ ...order, responses: [] }) : jsonResponse({ ...order, proposed_price: "225.10", revision: 3, request_revision: 7 }));
    render(<OrderEditor row={changeRequest} contract={contract} order={order} onRefresh={vi.fn()} onSelectEvidence={vi.fn()} />);
    await userEvent.clear(screen.getByLabelText("Proposed price (optional)")); await userEvent.type(screen.getByLabelText("Proposed price (optional)"), "225.10");
    expect(screen.getByRole("button", { name: "Approve change order" })).toBeDisabled();
    await userEvent.type(screen.getByLabelText("Reviewer name"), "Reviewer"); await userEvent.type(screen.getByLabelText("Review note"), "Revised proposed human price");
    await userEvent.click(screen.getByRole("button", { name: "Save draft changes" }));
    await waitFor(() => expect(fetch).toHaveBeenCalled());
    const options = vi.mocked(fetch).mock.calls.find(([, init]) => init?.method === "PATCH")?.[1];
    expect(options?.method).toBe("PATCH"); expect(JSON.parse(options?.body as string)).toMatchObject({ expected_revision: 6, expected_order_revision: 2, terms: { proposed_price: "225.10" } });
  });
});
