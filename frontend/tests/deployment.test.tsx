import { act, render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { apiBase, request } from "@/lib/api";
import { SourcePanel, verifiedRange } from "@/components/ui";
import { ActivityPage, Catalog } from "@/components/catalog";
import { ScopePanel } from "@/components/scope";
import { LoginScreen } from "@/components/auth";
import { contract, changeRequest, jsonResponse, summary } from "./fixtures";

describe("HTTPS deployment and Unicode provenance", () => {
  it("uses relative same-origin API requests with cookies", async () => {
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "same-origin");
    vi.mocked(fetch).mockResolvedValue(jsonResponse([]));
    await request("/contracts");
    expect(fetch).toHaveBeenCalledWith("/api/contracts", expect.objectContaining({ credentials: "include" }));
  });
  it("accepts an explicitly matching HTTPS origin and rejects a different backend", () => {
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "https://demo.example.test");
    vi.stubEnv("NEXT_PUBLIC_FRONTEND_URL", "https://demo.example.test");
    expect(apiBase()).toBe("https://demo.example.test");
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "https://evil.example.test");
    expect(apiBase).toThrow();
  });
  it.each(["😀", "😀🧪", "😀\r\n"])("locates the second repeated quotation after %s using code-point offsets", (prefix) => {
    const text = `${prefix} First: USD 1.00.\nSecond: USD 1.00.`;
    const excerpt = "USD 1.00.";
    const utf16 = text.lastIndexOf(excerpt);
    const start = Array.from(text.slice(0, utf16)).length;
    const quote = { source_excerpt: excerpt, source_start: start, source_end: start + excerpt.length };
    expect(verifiedRange(text, quote)).toEqual([utf16, utf16 + excerpt.length]);
    render(<SourcePanel text={text} filename="synthetic.pdf" selected={quote} />);
    const mark = document.querySelector("mark")!;
    expect(mark.previousSibling?.textContent).toBe(text.slice(0, utf16));
    expect(screen.getByRole("heading", { name: "Extracted agreement text" })).toBeInTheDocument();
    expect(screen.getByText(/PDF layout, signatures and original pages are not retained/)).toBeInTheDocument();
  });
  it("never guesses between repeated matches with missing or stale offsets", () => {
    const text = "😀 USD 1.00. and USD 1.00.";
    expect(verifiedRange(text, { source_excerpt: "USD 1.00." })).toBeNull();
    expect(verifiedRange(text, { source_excerpt: "USD 1.00.", source_start: 1, source_end: 10 })).toBeNull();
  });
  it("explains individual judge access and the first walkthrough", () => {
    render(<LoginScreen onLogin={vi.fn()} />);
    expect(screen.getByText(/individually supplied account/)).toHaveTextContent("Full product walkthrough");
    expect(screen.queryByRole("link", { name: /sign up/i })).not.toBeInTheDocument();
  });
});

describe("bounded discovery across pages", () => {
  it("waits for the updated request before exposing an editable prepared order", async () => {
    let prepared = false;
    let release!: (response: Response) => void;
    const refreshedRequest = new Promise<Response>((resolve) => { release = resolve; });
    const row = { ...changeRequest, status: "APPROVED", revision: 4 };
    const order = { id: "order-1", change_request_id: row.id, proposed_deliverables: ["Add checkout"], proposed_price: "2.00", currency: "USD",
      proposed_deadline: null, supporting_evidence: [], uncertainties: [], status: "REVIEW_REQUIRED", revision: 1, request_revision: 5,
      analysis_revision: 3, terms_origin: "human_entered", created_at: contract.created_at, updated_at: contract.updated_at };
    vi.mocked(fetch).mockImplementation(async (url, init) => {
      if (String(url).includes("/client-responses")) return jsonResponse({ ...order, responses: [] });
      if (String(url).includes("/contracts/") && String(url).includes("/change-requests?")) return prepared ? refreshedRequest : jsonResponse([row]);
      if (init?.method === "POST") { prepared = true; return jsonResponse(order, 201); }
      return prepared ? jsonResponse(order) : jsonResponse({ detail: "No order" }, 404);
    });
    function RefreshingPanel() {
      const [version, setVersion] = useState(0);
      return <ScopePanel contract={contract} version={version} onRefresh={() => setVersion((old) => old + 1)} onSelectEvidence={vi.fn()} />;
    }
    render(<RefreshingPanel />);
    await screen.findByRole("button", { name: "Prepare review draft" });
    await userEvent.type(screen.getByLabelText("Proposed price (optional)"), "2.00");
    await userEvent.type(screen.getByLabelText("Reviewer name"), "Reviewer");
    await userEvent.type(screen.getByLabelText("Review note"), "Propose synthetic terms");
    await userEvent.click(screen.getByRole("button", { name: "Prepare review draft" }));
    await waitFor(() => expect(vi.mocked(fetch).mock.calls.filter(([url]) => String(url).includes("/change-requests?")).length).toBe(2));
    expect(screen.queryByRole("heading", { name: "Draft change order" })).not.toBeInTheDocument();
    await act(async () => { release(jsonResponse([{ ...row, revision: 5, change_order_id: order.id }])); });
    await screen.findByRole("heading", { name: "Draft change order" });
    await userEvent.clear(screen.getByLabelText("Proposed price (optional)"));
    await userEvent.type(screen.getByLabelText("Proposed price (optional)"), "2.10");
    await userEvent.type(screen.getByLabelText("Reviewer name"), "Reviewer");
    await userEvent.type(screen.getByLabelText("Review note"), "Revise synthetic price");
    expect(screen.getByRole("button", { name: "Save draft changes" })).toBeEnabled();
    expect(screen.getByLabelText("Proposed price (optional)")).toHaveValue("2.10");
  });
  it("labels catalog search as applying to the current page", async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse([summary]));
    render(<Catalog />);
    expect(await screen.findByText("Synthetic website")).toBeInTheDocument();
    expect(screen.getByText(/Search and status filters apply to this page only/)).toBeInTheDocument();
  });
  it("can select an agreement beyond the first hundred in Activity", async () => {
    const page = Array.from({ length: 100 }, (_, index) => ({ ...summary, id: `first-${index}`, title: `Agreement ${index}` }));
    vi.mocked(fetch).mockImplementation(async (url) => String(url).includes("/activity") ? jsonResponse([]) :
      jsonResponse(String(url).includes("offset=100") ? [{ ...summary, id: "after-100", title: "Later agreement" }] : page));
    render(<ActivityPage />);
    await screen.findByRole("option", { name: "Agreement 99" });
    await userEvent.click(screen.getByRole("button", { name: "Next agreements" }));
    expect(await screen.findByRole("option", { name: "Later agreement" })).toBeInTheDocument();
    await waitFor(() => expect(fetch).toHaveBeenCalledWith(expect.stringContaining("/contracts/after-100/activity"), expect.anything()));
  });
  it("can select requests beyond one hundred and starts new saves on the newest page", async () => {
    const page = Array.from({ length: 100 }, (_, index) => ({ ...changeRequest, analysis: null, status: "PENDING" as const, id: `request-${index}`, title: `Request ${index}` }));
    vi.mocked(fetch).mockImplementation(async (url) => jsonResponse(String(url).includes("offset=100") ?
      [{ ...page[0], id: "older", title: "Older saved request" }] : page));
    render(<ScopePanel contract={contract} version={0} onRefresh={vi.fn()} onSelectEvidence={vi.fn()} />);
    await screen.findByRole("option", { name: /Request 99/ });
    await userEvent.click(screen.getByRole("button", { name: "Older requests" }));
    expect(await screen.findByRole("option", { name: /Older saved request/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Newer requests" })).toBeEnabled();
    expect(vi.mocked(fetch).mock.calls.some(([url]) => String(url).includes("newest=true"))).toBe(true);
  });
});
