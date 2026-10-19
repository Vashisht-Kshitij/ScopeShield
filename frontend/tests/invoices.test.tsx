import { render, screen, within, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi } from "vitest";
import { InvoiceWorkspace, MilestoneInvoice, sandboxInvoiceUrl } from "@/components/invoices";
import { DemoGallery } from "@/components/demos";
import { Problem } from "@/components/ui";
import { ApiError } from "@/lib/api";
import type { InvoiceLink, InvoicePreview } from "@/lib/types";
import { contract, milestone, jsonResponse } from "./fixtures";
import { routerPush } from "./setup";

const approved = { ...milestone, status: "APPROVED" as const };
const preview: InvoicePreview = { environment: "paypal_sandbox", contract_id: contract.id, milestone_id: milestone.id,
  milestone_revision: milestone.revision, contract_review_revision: milestone.contract_review_revision,
  name: "Synthetic reviewed work", description: "Approved original deliverables", amount: "1.00", currency: "USD",
  declared_total: "3.00", approved_total: "1.00", discrepancies: ["APPROVED_TOTAL_DIFFERS_FROM_CONTRACT"], review_flags: [],
  preview_digest: "a".repeat(64), existing_link_id: null, recipient_email: "buyer@personal.example.com", provider_mode: "mocked_paypal_sandbox" };
const draft: InvoiceLink = { environment: "paypal_sandbox", id: "link-1", milestone_id: milestone.id, contract_id: contract.id,
  milestone_revision: milestone.revision, contract_review_revision: milestone.contract_review_revision, invoice_number: "SS-SYNTHETIC",
  state: "CREATED", invoice_id: "INV2-MOCK-1234", paypal_status: "DRAFT", amount: "1.00", currency: "USD", verified: true,
  error_code: null, revision: 3, snapshot: { preview }, created_at: contract.created_at, updated_at: contract.updated_at,
  last_checked_at: contract.updated_at, recipient_view_url: null, recipient_email: preview.recipient_email,
  provider_mode: "mocked_paypal_sandbox", send_state: "NOT_REQUESTED" };

function backend(initial: InvoiceLink | null = null, createFailure = false) {
  let saved = initial;
  vi.mocked(fetch).mockImplementation(async (url, init) => {
    const path = String(url);
    if (path.endsWith("/preview")) return jsonResponse(preview);
    if (path.endsWith("/history")) return jsonResponse([]);
    if (init?.method === "POST") {
      if (path.endsWith("/send")) { saved = { ...draft, revision: 6, send_state: "ACCEPTED", paypal_status: "SENT" }; return jsonResponse(saved); }
      if (path.endsWith("/sync") || path.endsWith("/reconcile")) { saved = { ...(saved || draft), revision: 4 }; return jsonResponse(saved); }
      saved = createFailure ? { ...draft, invoice_id: null, state: "RECONCILIATION_REQUIRED", verified: false, error_code: "PAYPAL_TIMEOUT" } : draft;
      return createFailure ? jsonResponse({ detail: { message: "A durable attempt is saved. Reconcile; do not retry creation" } }, 504) : jsonResponse(saved, 201);
    }
    return saved ? jsonResponse(saved) : jsonResponse({ detail: "No invoice attempt is linked" }, 404);
  });
}
function posts(suffix: string) { return vi.mocked(fetch).mock.calls.filter(([url, init]) => init?.method === "POST" && String(url).endsWith(suffix)); }
async function previewDialog() {
  await userEvent.click(await screen.findByRole("button", { name: "Preview invoice draft" }));
  const dialog = await screen.findByRole("dialog");
  await userEvent.type(within(dialog).getByLabelText("Reviewer name"), "Synthetic reviewer");
  await userEvent.type(within(dialog).getByLabelText("Review note"), "Reviewed original source and intended draft");
  return dialog;
}
async function confirmDraft(dialog: HTMLElement) {
  await userEvent.click(within(dialog).getByRole("checkbox", { name: "I reviewed the discrepancies and intend this exact partial draft." }));
  await userEvent.type(within(dialog).getByLabelText("Discrepancy explanation"), "Only the reviewed first milestone is being invoiced");
  await userEvent.click(within(dialog).getByRole("checkbox", { name: "I confirm creating one sandbox draft for this milestone and recipient." }));
  await userEvent.click(within(dialog).getByRole("button", { name: "Create sandbox draft" }));
}

describe("approved milestone invoices", () => {
  it("offers no financial controls when no milestone is approved", () => {
    render(<InvoiceWorkspace contract={contract} onRefresh={vi.fn()} />);
    expect(screen.getByText("Approve a milestone first")).toBeInTheDocument();
    expect(fetch).not.toHaveBeenCalled();
  });
  it("previews exact values and recipient before confirmed, discrepancy-aware creation", async () => {
    backend(); render(<MilestoneInvoice row={approved} contract={contract} onRefresh={vi.fn()} />);
    const dialog = await previewDialog();
    expect(within(dialog).getByText(preview.recipient_email)).toBeInTheDocument();
    expect(within(dialog).getByText("USD 1.00")).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Create sandbox draft" })).toBeDisabled();
    expect(posts("/invoice")).toHaveLength(0);
    await confirmDraft(dialog);
    expect(await screen.findByText(draft.invoice_id!)).toBeInTheDocument();
    const payload = JSON.parse(posts("/invoice")[0][1]?.body as string);
    expect(payload).toMatchObject({ preview_digest: preview.preview_digest, expected_revision: preview.milestone_revision,
      expected_contract_revision: preview.contract_review_revision, confirmed: true, acknowledge_discrepancies: true });
    expect(payload.idempotency_key).toMatch(/^[a-f0-9-]{36}$/);
    expect(payload).not.toHaveProperty("amount"); expect(payload).not.toHaveProperty("recipient_email");
    expect(posts("/send")).toHaveLength(0);
  });
  it("recovers the saved uncertain attempt without repeating draft creation", async () => {
    backend(null, true); render(<MilestoneInvoice row={approved} contract={contract} onRefresh={vi.fn()} />);
    await confirmDraft(await previewDialog());
    expect(await screen.findByText(/Not returned — reconcile/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Create sandbox draft" })).not.toBeInTheDocument();
    await userEvent.click(screen.getByText("Read-only recovery and reconciliation"));
    await userEvent.click(screen.getByRole("button", { name: "Reconcile saved attempt" }));
    await waitFor(() => expect(posts("/reconcile")).toHaveLength(1));
    expect(posts("/invoice")).toHaveLength(1); expect(posts("/send")).toHaveLength(0);
  });
  it("requires a second confirmation before sending and displays the actual returned status", async () => {
    backend(draft); render(<MilestoneInvoice row={approved} contract={contract} onRefresh={vi.fn()} />);
    await userEvent.click(await screen.findByRole("button", { name: "Review sending email" }));
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText(draft.recipient_email!)).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Confirm sending sandbox email" })).toBeDisabled();
    expect(posts("/send")).toHaveLength(0);
    await userEvent.type(within(dialog).getByLabelText("Reviewer name"), "Synthetic reviewer");
    await userEvent.type(within(dialog).getByLabelText("Review note"), "Separate explicit mocked send decision");
    await userEvent.click(within(dialog).getByRole("checkbox"));
    await userEvent.click(within(dialog).getByRole("button", { name: "Confirm sending sandbox email" }));
    expect(await screen.findByText("PayPal status: SENT")).toBeInTheDocument();
    expect(JSON.parse(posts("/send")[0][1]?.body as string)).toMatchObject({ expected_link_revision: 3, expected_invoice_id: draft.invoice_id, confirmed: true });
    expect(screen.getByRole("button", { name: "Review sending email" })).toBeDisabled();
    expect(posts("/invoice")).toHaveLength(0);
  });
  it("refreshes provider status explicitly without creating or sending an invoice", async () => {
    backend(draft); render(<MilestoneInvoice row={approved} contract={contract} onRefresh={vi.fn()} />);
    await screen.findByText(draft.invoice_id!);
    expect(vi.mocked(fetch).mock.calls.every(([, init]) => !init?.method || init.method === "GET")).toBe(true);
    await userEvent.click(screen.getByRole("button", { name: "Refresh PayPal status" }));
    await waitFor(() => expect(posts("/sync")).toHaveLength(1));
    expect(JSON.parse(posts("/sync")[0][1]?.body as string).expected_link_revision).toBe(3);
    expect(posts("/invoice")).toHaveLength(0); expect(posts("/send")).toHaveLength(0);
  });
  it.each(["javascript:alert(1)", "https://www.paypal.com/invoice/x", "https://sandbox.paypal.com.evil.example/x", "https://user@www.sandbox.paypal.com/x"])("rejects unsafe provider links %s", (url) => {
    expect(sandboxInvoiceUrl(url)).toBeNull();
  });
});

describe("offline example recovery", () => {
  it("offers a useful recovery link without pretending an unsupported contract is valid", () => {
    render(<Problem error={new ApiError(422, "Mismatch", "OFFLINE_DEMO_CONTRACT_MISMATCH")} />);
    expect(screen.getByRole("alert")).toHaveTextContent("Your agreement and saved request are preserved");
    expect(screen.getByRole("link", { name: "Choose a compatible sample" })).toHaveAttribute("href", "/demos");
  });
  it("keeps a partially loaded agreement accessible instead of duplicating uploads", async () => {
    const scenario = { id: "sample", title: "Compatible sample", filename: "sample.txt", description: "Predefined example", text: "Synthetic fixture", supports_extraction: true, requested_changes: [], mode: "offline_demo", live_ai: false };
    vi.mocked(fetch).mockImplementation(async (url, init) => String(url).endsWith("/demo-scenarios") ? jsonResponse([scenario]) : init?.body instanceof FormData ? jsonResponse(contract, 201) : jsonResponse({ detail: "Extraction fixture unavailable" }, 503));
    render(<DemoGallery />);
    await userEvent.click(await screen.findByRole("button", { name: "Load Compatible sample" }));
    expect(await screen.findByRole("link", { name: "Open saved sample" })).toHaveAttribute("href", `/contracts/${contract.id}`);
    expect(screen.getByRole("button", { name: "Load Compatible sample" })).toBeDisabled();
    expect(routerPush).not.toHaveBeenCalled();
    expect(vi.mocked(fetch).mock.calls.filter(([, init]) => init?.body instanceof FormData)).toHaveLength(1);
  });
});
