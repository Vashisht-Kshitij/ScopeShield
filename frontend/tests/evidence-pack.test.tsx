import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { EvidencePack, type EvidenceSummary } from "@/components/evidence-pack";
import { api } from "@/lib/api";
import { jsonResponse } from "./fixtures";

const summary: EvidenceSummary = { project_id: "project-one", source_revision: "a".repeat(64), generated_at: "2026-10-17T12:00:00Z",
  counts: { contracts: 1, milestones: 3, requests: 2, orders: 1, invoices: 0 },
  warnings: ["Includes offline or simulated AI results", "Sandbox records do not establish real payment"], sections: ["01 / Project snapshot", "04 / Conversation and scope"] };
afterEach(() => vi.restoreAllMocks());
describe("Evidence Pack", () => {
  it("previews all project records and warnings without generating a PDF automatically", async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse(summary)); render(<EvidencePack projectId="project-one" />);
    expect(await screen.findByText("Inside your report")).toBeVisible();
    expect(screen.getByText("Includes offline or simulated AI results")).toBeVisible();
    expect(fetch).toHaveBeenCalledOnce();
    expect(screen.getByRole("button", { name: "Download Evidence Pack PDF" })).toBeEnabled();
  });
  it("requires an explicit download and binds it to the preview revision with credentials", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(summary)).mockResolvedValueOnce(new Response("%PDF-fixture", { headers: { "Content-Type": "application/pdf" } }));
    const create = vi.fn(() => "blob:test-private"); const revoke = vi.fn();
    vi.stubGlobal("URL", Object.assign(URL, { createObjectURL: create, revokeObjectURL: revoke }));
    const clicked = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
    render(<EvidencePack projectId="project-one" />);
    await userEvent.click(await screen.findByRole("button", { name: "Download Evidence Pack PDF" }));
    await waitFor(() => expect(clicked).toHaveBeenCalledOnce());
    expect(fetch).toHaveBeenLastCalledWith(expect.stringContaining(`expected_revision=${summary.source_revision}`), expect.objectContaining({ credentials: "include", cache: "no-store" }));
    expect(create).toHaveBeenCalledOnce();
    await waitFor(() => expect(revoke).toHaveBeenCalledWith("blob:test-private"), { timeout: 2000 });
  });
  it("shows a changed-project conflict and offers a fresh summary", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(summary)).mockResolvedValueOnce(jsonResponse({ detail: { code: "REPORT_REVISION_CHANGED", message: "Project changed after the preview. Refresh the report summary before downloading." } }, 409));
    render(<EvidencePack projectId="project-one" />); await userEvent.click(await screen.findByRole("button", { name: "Download Evidence Pack PDF" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Project changed after the preview");
    vi.mocked(fetch).mockResolvedValue(jsonResponse(summary)); fireEvent.click(screen.getByRole("button", { name: "Refresh summary" }));
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(3));
  });
  it("escapes malicious preview text", async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse({ ...summary, warnings: ['<img src=x onerror="alert(1)">'] }));
    const { container } = render(<EvidencePack projectId="project-one" />);
    expect(await screen.findByText('<img src=x onerror="alert(1)">')).toBeVisible(); expect(container.querySelector("img")).toBeNull();
  });
  it.each([401, 403, 404])("does not expose a download on authorization error %s", async (status) => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse({ detail: "Access denied" }, status)); render(<EvidencePack projectId="foreign" />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Access denied");
    expect(screen.queryByRole("button", { name: "Download Evidence Pack PDF" })).toBeNull();
  });
  it("rejects a non-PDF successful response instead of downloading unsafe content", async () => {
    vi.mocked(fetch).mockResolvedValue(new Response("<script>bad</script>", { headers: { "Content-Type": "text/html" } }));
    await expect(api.evidencePdf("project", "a".repeat(64))).rejects.toThrow("did not return a PDF");
  });
});
