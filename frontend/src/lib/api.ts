import type { ChangeOrder, ChangeRequest, Contract, ConversationMessageInput, Finding, InvoiceLink, InvoicePreview, Milestone, MilestoneChanges, Mode, OrderTerms, ReviewContext, ReviewDecision } from "./types";

export class ApiError extends Error {
  constructor(public status: number, message: string, public code?: string) { super(message); this.name = "ApiError"; }
}
let csrfToken = "";
export function setCsrfToken(value: string) { csrfToken = value; }
export function apiBase(): string {
  const value = process.env.NEXT_PUBLIC_API_BASE_URL || "http://127.0.0.1:8000";
  if (value === "same-origin") return "";
  const url = new URL(value);
  const local = url.protocol === "http:" && ["127.0.0.1", "localhost"].includes(url.hostname);
  const publicUrl = url.protocol === "https:" && value === process.env.NEXT_PUBLIC_FRONTEND_URL &&
    /^[a-z0-9-]+(?:\.[a-z0-9-]+)+$/.test(url.hostname) && !url.port;
  if ((!local && !publicUrl) || url.username || url.password || url.pathname !== "/" || url.search || url.hash) {
    throw new Error("Set NEXT_PUBLIC_API_BASE_URL to same-origin, an explicit matching HTTPS frontend origin, or a plain HTTP loopback origin.");
  }
  return url.origin;
}
function detailMessage(detail: unknown): string {
  if (detail && typeof detail === "object" && "code" in detail) {
    const messages: Record<string, string> = {
      AI_BUDGET_EXCEEDED: "The daily AI allowance is exhausted. Try after the next UTC midnight. Saved results remain available; offline mode supports only the bundled examples.",
      AI_QUOTA_COOLDOWN: "AI is cooling down after a provider quota limit. Wait before retrying. Saved results remain available.",
      AI_QUOTA_EXCEEDED: "The provider quota is exhausted. Ask the operator to check allowances; saved results remain available.",
      AI_CONCURRENCY_LIMIT: "AI is busy. Wait a few seconds, then refresh the saved record before retrying.",
      AI_INPUT_TOO_LARGE: "This agreement or scope request exceeds configured AI input limits. Upload a shorter complete agreement or reduce the requested work. The source is saved and is never truncated automatically.",
      AI_MODEL_UNAVAILABLE: "This model is unavailable. Ask the operator to check model access before another attempt.",
      AI_MODELS_UNAVAILABLE: "Configured models are temporarily unavailable. Refresh to see any saved result, then try later.",
      AI_PROVIDER_TEMPORARY: "AI is temporarily unavailable. Saved results remain available; try later.",
      AI_TIMEOUT: "AI timed out. Refresh the saved record before retrying; any previous completed result is preserved.",
      RESOURCE_BUSY: "Processing is busy. Wait a few seconds before retrying; saved results remain available.",
      RATE_LIMITED: "Too many requests. Wait before retrying; saved results remain available.",
      PDF_PROCESSING_TIMEOUT: "PDF processing timed out. Upload a simpler PDF or UTF-8 text.",
      PDF_EXPANDED_LIMIT: "This PDF expands beyond the processing limit. Upload a simpler PDF or UTF-8 text.",
    };
    const message = messages[String(detail.code)];
    if (message) return message;
  }
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) return detail.map((item) => `${item.loc?.slice(1).join(" · ") || "Request"}: ${item.msg || "Invalid value"}`).join(". ");
  if (detail && typeof detail === "object" && "message" in detail) return String(detail.message);
  if (detail && typeof detail === "object" && "provider" in detail && detail.provider === "paypal_sandbox" && "error" in detail) {
    const code = String(detail.error);
    if (code === "PAYPAL_TIMEOUT") return "PayPal did not respond in time. Check the saved attempt and reconcile before repeating a financial action.";
    if (code === "PAYPAL_UNAVAILABLE") return "PayPal sandbox is unavailable. Saved invoice records remain available; refresh or reconcile when the provider recovers.";
    if (["SANDBOX_RECIPIENT_CHANGED", "SANDBOX_RECIPIENT_MISMATCH", "INVOICE_LINK_MISMATCH"].includes(code)) return "The provider invoice or recipient does not match the reviewed draft. Keep the saved attempt and investigate before continuing.";
    return `PayPal sandbox could not complete this action (${code.replaceAll("_", " ").toLowerCase()}). Check the local sandbox configuration and saved invoice history.`;
  }
  return "The local API could not complete this request.";
}
export async function request<T>(path: string, init: RequestInit = {}, binary = false): Promise<T> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 110_000);
  const cancel = () => controller.abort();
  init.signal?.addEventListener("abort", cancel, { once: true });
  try {
    const response = await fetch(`${apiBase()}${path === "/health" ? path : "/api" + path}`, {
      ...init, signal: controller.signal, cache: "no-store", credentials: "include",
      headers: { ...(init.body && !(init.body instanceof FormData) ? { "Content-Type": "application/json" } : {}),
        ...(init.method && init.method !== "GET" && csrfToken ? { "X-CSRF-Token": csrfToken } : {}), ...init.headers },
    });
    if (response.ok && binary) {
      if (!response.headers.get("Content-Type")?.startsWith("application/pdf")) throw new ApiError(502, "The server did not return a PDF.");
      return await response.blob() as T;
    }
    const data = await response.json().catch(() => null);
    if (!response.ok) {
      if (response.status === 401 && !path.startsWith("/auth/") && typeof window !== "undefined") window.dispatchEvent(new Event("scopeshield-session-expired"));
      const retry = response.headers.get("Retry-After");
      const guidance = response.status === 429 && retry && /^\d{1,6}$/.test(retry) ? ` Retry after ${retry} seconds.` : "";
      throw new ApiError(response.status, detailMessage(data?.detail) + guidance, data?.detail?.code || (data?.detail?.provider === "paypal_sandbox" ? data.detail.error : undefined));
    }
    return data as T;
  } catch (error) {
    if (error instanceof ApiError) throw error;
    if (init.signal?.aborted) throw error;
    throw new ApiError(0, controller.signal.aborted
      ? "The request took too long. Refresh the saved record before trying again; the backend may still be processing."
      : error instanceof Error && error.message.startsWith("Set NEXT_PUBLIC") ? error.message
      : "Cannot reach the local backend. Check that FastAPI is running and the API address matches the frontend configuration.");
  } finally { clearTimeout(timer); init.signal?.removeEventListener("abort", cancel); }
}
export const post = <T>(path: string, body: unknown) => request<T>(path, { method: "POST", body: JSON.stringify(body) });
export const patch = <T>(path: string, body: unknown) => request<T>(path, { method: "PATCH", body: JSON.stringify(body) });
export const api = {
  evidencePdf: (projectId: string, revision: string, executive = false) => request<Blob>(`/projects/${projectId}/evidence-pack.pdf?expected_revision=${encodeURIComponent(revision)}${executive ? "&executive=true" : ""}`, {}, true),
  createConversation: (contract: Contract, title: string, messages: ConversationMessageInput[], context: ReviewContext) => post<ChangeRequest>(`/contracts/${contract.id}/change-requests`, {
    ...context, project_id: contract.project_id, title, conversation_messages: messages,
  }),
  editConversation: (row: ChangeRequest, title: string, messages: ConversationMessageInput[], context: ReviewContext) => patch<ChangeRequest>(`/change-requests/${row.id}`, {
    ...context, expected_revision: row.revision, title, conversation_messages: messages,
  }),
  invoicePreview: (milestoneId: string) => request<InvoicePreview>(`/milestones/${milestoneId}/invoice/preview`),
  createInvoice: (preview: InvoicePreview, key: string, context: ReviewContext, discrepancyNote: string) => post<InvoiceLink>(`/milestones/${preview.milestone_id}/invoice`, {
    ...context, contract_id: preview.contract_id, expected_revision: preview.milestone_revision,
    expected_contract_revision: preview.contract_review_revision, preview_digest: preview.preview_digest,
    idempotency_key: key, confirmed: true, acknowledge_discrepancies: !!discrepancyNote,
    discrepancy_note: discrepancyNote || null,
  }),
  syncInvoice: (link: InvoiceLink, context: ReviewContext) => post<InvoiceLink>(`/milestones/${link.milestone_id}/invoice/sync`, { ...context, expected_link_revision: link.revision }),
  reconcileInvoice: (link: InvoiceLink, context: ReviewContext, invoiceId?: string) => post<InvoiceLink>(`/milestones/${link.milestone_id}/invoice/reconcile`, {
    ...context, expected_link_revision: link.revision, ...(invoiceId ? { invoice_id: invoiceId } : {}),
  }),
  sendInvoice: (link: InvoiceLink, context: ReviewContext) => post<InvoiceLink>(`/milestones/${link.milestone_id}/invoice/send`, {
    ...context, expected_link_revision: link.revision, expected_invoice_id: link.invoice_id, confirmed: true,
  }),
  upload: (file: File, projectId?: string) => {
    const form = new FormData(); form.append("file", file); if (projectId) form.append("project_id", projectId);
    return request<Contract>("/contracts", { method: "POST", body: form });
  },
  extract: (contract: Contract, mode: Mode, force = false) => post<Contract>(`/contracts/${contract.id}/extract`, { mode, ...(force ? { force: true } : {}) }),
  editMilestone: (row: Milestone, changes: Partial<MilestoneChanges>, context: ReviewContext) => patch<Milestone>(`/milestones/${row.id}`, {
    ...context, changes, expected_revision: row.revision, expected_contract_revision: row.contract_review_revision,
  }),
  decideMilestone: (row: Milestone, action: "approve" | "reject" | "reopen", context: ReviewDecision) => post<Milestone>(`/milestones/${row.id}/${action}`, {
    ...context, expected_revision: row.revision, expected_contract_revision: row.contract_review_revision,
  }),
  createRequest: (contract: Contract, title: string, work: string[], context: ReviewContext) => post<ChangeRequest>(`/contracts/${contract.id}/change-requests`, {
    ...context, project_id: contract.project_id, title, requested_changes: work,
  }),
  editRequest: (row: ChangeRequest, title: string, work: string[], context: ReviewContext) => patch<ChangeRequest>(`/change-requests/${row.id}`, {
    ...context, expected_revision: row.revision, title, requested_changes: work,
  }),
  analyze: (row: ChangeRequest, mode: Mode, context: ReviewContext) => post<ChangeRequest>(`/change-requests/${row.id}/analyze`, { ...context, expected_revision: row.revision, mode }),
  editAnalysis: (row: ChangeRequest, findings: Finding[], context: ReviewContext) => patch<ChangeRequest>(`/change-requests/${row.id}/analysis`, {
    ...context, expected_revision: row.revision, analysis: { findings },
  }),
  decideRequest: (row: ChangeRequest, action: "approve" | "reject" | "cancel", context: ReviewDecision) => post<ChangeRequest>(
    `/change-requests/${row.id}/${action === "cancel" ? "cancel" : `analysis/${action}`}`, { ...context, expected_revision: row.revision }),
  prepareOrder: (row: ChangeRequest, terms: OrderTerms, context: ReviewContext) => post<ChangeOrder>(`/change-requests/${row.id}/change-order`, {
    ...context, expected_revision: row.revision, terms,
  }),
  editOrder: (row: ChangeOrder, terms: OrderTerms, context: ReviewContext) => patch<ChangeOrder>(`/change-orders/${row.id}`, {
    ...context, expected_revision: row.request_revision, expected_order_revision: row.revision, terms,
  }),
  decideOrder: (row: ChangeOrder, action: "approve" | "reject", context: ReviewDecision) => post<ChangeOrder>(`/change-orders/${row.id}/${action}`, {
    ...context, expected_revision: row.request_revision, expected_order_revision: row.revision,
  }),
};
