import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { originalPages, SourcePanel } from "@/components/ui";
import type { PdfProvenance } from "@/lib/types";
import { contract } from "./fixtures";
import { jsonResponse } from "./fixtures";
import { AuthBoundary } from "@/components/auth";
import { InvoiceWorkspace } from "@/components/invoices";

const text = "😀 First: USD 100.\n\nSecond: USD 100.";
const second = Array.from(text.slice(0, text.lastIndexOf("USD"))).length;
const boundary = Array.from(text.slice(0, text.indexOf("Second"))).length;
const provenance: PdfProvenance = { method: "pypdf-physical-pages-v1", text_sha256: "verified-by-server",
  pages: [{ page: 1, start: 0, end: boundary - 2 }, { page: 2, start: boundary, end: Array.from(text).length }] };

describe("physical PDF provenance", () => {
  it("shows simulation history to judges without requesting any provider endpoint", async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse({ user: { id: "judge", username: "individual.judge", display_name: "Judge", role: "freelancer" }, csrf_token: "test", demo_mode: true }));
    render(<AuthBoundary><InvoiceWorkspace contract={contract} onRefresh={vi.fn()} /></AuthBoundary>);
    expect(await screen.findByRole("heading", { name: "Simulated invoice history" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Invoice demonstration", level: 2 })).toBeInTheDocument();
    expect(screen.getByText("Paid example · SIMULATED")).toBeInTheDocument();
    expect(screen.getByText(/No PayPal invoice, email or payment exists/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Preview invoice draft" })).not.toBeInTheDocument();
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(vi.mocked(fetch).mock.calls[0][0]).toEqual(expect.stringContaining("/api/auth/me"));
  });
  it("uses the exact second occurrence after an emoji and displays its physical page", () => {
    const quote = { source_excerpt: "USD 100.", source_start: second, source_end: second + 8 };
    expect(originalPages(text, quote, provenance)).toEqual([2]);
    render(<SourcePanel text={text} filename="contract.pdf" selected={quote} contract={{ ...contract, original_pdf_available: true, pdf_provenance: provenance }} />);
    expect(screen.getByText("Original PDF physical page: 2")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Download original PDF" })).toHaveAttribute("href", expect.stringContaining(`/api/contracts/${contract.id}/original`));
    expect(screen.getByText(/not printed page labels or visual coordinates/)).toBeInTheDocument();
    expect(screen.getByText(/Repeated quotations do not identify a unique clause/)).toBeInTheDocument();
  });
  it("lists both physical pages when an exact quotation crosses their boundary", () => {
    const quote = { source_excerpt: text, source_start: 0, source_end: Array.from(text).length };
    expect(originalPages(text, quote, provenance)).toEqual([1, 2]);
  });
  it.each([
    { source_excerpt: "USD 100." },
    { source_excerpt: "USD 100.", source_start: second + 1, source_end: second + 9 },
    { source_excerpt: "invented clause", source_start: 0, source_end: 15 },
    { source_excerpt: "USD 100.", source_start: 0.5, source_end: 8.5 },
  ])("never turns a missing, stale or invalid source range into a page citation", (quote) => {
    expect(originalPages(text, quote, provenance)).toEqual([]);
  });
  it("preserves extracted-text evidence with an explicit fallback on legacy records", () => {
    const quote = { source_excerpt: "USD 100.", source_start: second, source_end: second + 8 };
    render(<SourcePanel text={text} filename="legacy.pdf" selected={quote} contract={contract} />);
    expect(screen.getByText("Original PDF page provenance unavailable for this excerpt.")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Download original PDF" })).not.toBeInTheDocument();
    expect(document.querySelector("mark")).toHaveTextContent("USD 100.");
  });
});
