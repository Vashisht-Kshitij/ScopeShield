import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ConversationDialog, ConversationEvidence, ConversationTimeline } from "@/components/conversation";
import { OrderEditor } from "@/components/orders";
import { AnalysisEditor, ScopePanel } from "@/components/scope";
import type { ChangeRequest, ConversationMessage, Finding } from "@/lib/types";
import { changeRequest, contract, finding, jsonResponse } from "./fixtures";

const messages: ConversationMessage[] = [
  { id: "message-one", sequence: 1, role: "client", sender: "Synthetic client", content: "Please revise the colour.", sent_at: null },
  { id: "message-two", sequence: 2, role: "client", sender: "Synthetic client", content: "Please add another section.", sent_at: "2026-11-06" },
];
const individual: Finding = { ...finding, requested_work: messages[0].content, change_types: ["REVISIONS"], conversation_effect: "INDIVIDUAL",
  message_evidence: [{ message_id: messages[0].id, source_excerpt: messages[0].content, source_start: 0, source_end: Array.from(messages[0].content).length }] };
const cumulative: Finding = { ...individual, ordinal: 3, requested_work: "Review the cumulative effect of this ordered conversation.", conversation_effect: "CUMULATIVE" };
const row: ChangeRequest = { ...changeRequest, status: "REVIEW_REQUIRED", conversation_messages: messages, requested_changes: [...messages.map((item) => item.content), cumulative.requested_work],
  analysis: { ...changeRequest.analysis!, findings: [individual, { ...individual, ordinal: 2, requested_work: messages[1].content }, cumulative] } };

async function review() {
  await userEvent.type(screen.getByLabelText("Reviewer name"), "Synthetic reviewer");
  await userEvent.type(screen.getByLabelText("Review note"), "Checked exact conversation sources");
}

describe("manual conversation entry", () => {
  it("saves multiple exact messages with explicit order and no invented date or ID", async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse(row, 201));
    const saved = vi.fn(); render(<ConversationDialog contract={contract} onSaved={saved} />);
    await userEvent.click(screen.getByRole("button", { name: "Add conversation" }));
    await userEvent.type(screen.getByLabelText("Conversation title"), "Launch discussion");
    await userEvent.type(screen.getByLabelText("Sender 1"), "Client");
    const exact = "  Please revise\n  the café headline 👩🏽‍💻.  ";
    fireEvent.change(screen.getByLabelText("Message text 1"), { target: { value: exact } });
    await userEvent.click(screen.getByRole("button", { name: "Add another message" }));
    await userEvent.type(screen.getByLabelText("Sender 2"), "Client");
    fireEvent.change(screen.getByLabelText("Message text 2"), { target: { value: exact } });
    await review(); await userEvent.click(screen.getByRole("button", { name: "Save conversation" }));
    await waitFor(() => expect(saved).toHaveBeenCalledOnce());
    const [url, options] = vi.mocked(fetch).mock.calls[0];
    expect(String(url)).toContain(`/contracts/${contract.id}/change-requests`);
    const payload = JSON.parse(options!.body as string);
    expect(payload.requested_changes).toBeUndefined();
    expect(payload.conversation_messages).toEqual([1, 2].map((sequence) => ({ sequence, sender: "Client", role: "client", content: exact, sent_at: null })));
  });

  it("reorders existing messages while preserving stable IDs and sends the displayed revision", async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse(row));
    render(<ConversationDialog contract={contract} row={row} onSaved={vi.fn()} />);
    await userEvent.click(screen.getByRole("button", { name: "Edit conversation" }));
    await userEvent.click(screen.getByRole("button", { name: "Move message 2 earlier" }));
    expect(screen.getByLabelText("Message text 1")).toHaveValue(messages[1].content);
    await review(); await userEvent.click(screen.getByRole("button", { name: "Save conversation and reset analysis" }));
    await waitFor(() => expect(fetch).toHaveBeenCalledOnce());
    const payload = JSON.parse(vi.mocked(fetch).mock.calls[0][1]!.body as string);
    expect(payload.expected_revision).toBe(row.revision);
    expect(payload.conversation_messages.map((item: ConversationMessage) => [item.id, item.sequence])).toEqual([["message-two", 1], ["message-one", 2]]);
  });

  it("preserves unsaved text when the backend rejects a stale revision", async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse({ detail: "Stale change request revision" }, 409));
    const saved = vi.fn(); render(<ConversationDialog contract={contract} row={row} onSaved={saved} />);
    await userEvent.click(screen.getByRole("button", { name: "Edit conversation" }));
    fireEvent.change(screen.getByLabelText("Message text 1"), { target: { value: "My unsaved source correction" } });
    await review(); await userEvent.click(screen.getByRole("button", { name: "Save conversation and reset analysis" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Stale change request revision");
    expect(screen.getByLabelText("Message text 1")).toHaveValue("My unsaved source correction");
    expect(saved).not.toHaveBeenCalled();
  });
});

describe("conversation review and evidence", () => {
  it("renders exact source text safely and labels dates/senders as unauthenticated", () => {
    const source = { ...messages[0], content: '<img src=x onerror="alert(1)">\nManual source' };
    const { container } = render(<ConversationTimeline messages={[source]} />);
    expect(screen.getByText(/Manually entered, unauthenticated/)).toBeInTheDocument();
    expect(screen.getByText(/Date not supplied/)).toBeInTheDocument();
    expect(container.querySelector("pre")).toHaveTextContent("<img src=x");
    expect(container.querySelector("img")).toBeNull();
  });

  it("verifies Python code-point ranges for Unicode and refuses mismatched message attribution", () => {
    const content = "👩🏽‍💻 Please revise the café headline.";
    const source = { ...messages[0], content };
    const start = Array.from("👩🏽‍💻 ").length;
    const quote = { message_id: source.id, source_excerpt: "Please revise the café headline.", source_start: start, source_end: Array.from(content).length };
    render(<ConversationEvidence messages={[source]} quotes={[quote, { ...quote, message_id: "other-source" }]} />);
    expect(screen.getByText("Verified source text")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Message 1/ })).toHaveAttribute("href", "#message-message-one");
    expect(screen.getByText(/Message quotation is unavailable/)).toBeInTheDocument();
  });

  it("shows cumulative findings within existing scope review with offline provenance", async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse([row]));
    render(<ScopePanel contract={contract} version={0} onRefresh={vi.fn()} onSelectEvidence={vi.fn()} />);
    expect(await screen.findByRole("region", { name: "Saved conversation" })).toBeInTheDocument();
    expect(screen.getByText("What changed across the conversation")).toBeInTheDocument();
    expect(screen.getByText("Offline / mocked response")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Approve assessment" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Prepare review draft" })).not.toBeInTheDocument();
  });

  it("requires human-entered deliverables and leaves all monetary terms blank for conversations", () => {
    render(<OrderEditor row={{ ...row, status: "APPROVED" }} contract={contract} order={null} onRefresh={vi.fn()} onSelectEvidence={vi.fn()} />);
    expect(screen.getByLabelText("Proposed additional deliverables")).toHaveValue("");
    expect(screen.getByLabelText("Proposed price (optional)")).toHaveValue("");
    expect(screen.getByLabelText("Proposed deadline (optional)")).toHaveValue("");
    expect(screen.getByRole("button", { name: "Prepare review draft" })).toBeInTheDocument();
  });

  it("human corrections keep message evidence for backend revalidation", async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse(row));
    render(<AnalysisEditor row={{ ...row, analysis: { ...row.analysis!, findings: [individual] } }} onRefresh={vi.fn()} />);
    await userEvent.click(screen.getByRole("button", { name: "Edit assessment" }));
    const dialog = screen.getByRole("dialog");
    await userEvent.selectOptions(within(dialog).getByLabelText("Classification"), "AMBIGUOUS");
    await review(); await userEvent.click(within(dialog).getByRole("button", { name: "Save reviewed assessment" }));
    await waitFor(() => expect(fetch).toHaveBeenCalledOnce());
    const payload = JSON.parse(vi.mocked(fetch).mock.calls[0][1]!.body as string);
    expect(payload.analysis.findings[0]).toMatchObject({ classification: "AMBIGUOUS", message_evidence: individual.message_evidence });
  });
});
