import type { ChangeRequest, Contract, ContractSummary, Finding, Milestone } from "@/lib/types";

export const source = "Project: Synthetic website\nScope: One responsive page.\nAcceptance: The signup form validates email addresses.\nRevisions: Two text revisions are included.\nExclusion: Online payments are excluded.";
const evidence = (value: string) => ({ value, source_excerpt: value, source_start: source.indexOf(value), source_end: source.indexOf(value) + value.length });
export const milestone: Milestone = {
  id: "milestone-1", ordinal: 1, contract_id: "contract-1", name: "Page delivery", description: "Reviewed page",
  amount: "100.00", currency: "USD", payment_conditions: "After written acceptance", deadline: null,
  deliverables: ["One responsive page."], acceptance_criteria: ["The signup form validates email addresses."],
  status: "REVIEW_REQUIRED", revision: 2, contract_review_revision: 1, has_human_edits: false,
  updated_at: "2026-10-14T10:00:00Z", reviewed_at: null, reviewer_label: null,
  evidence: { name: evidence("Page delivery"), amount: evidence("100.00"), currency: evidence("USD"),
    payment_conditions: evidence("After written acceptance"), deadline: { value: null, source_excerpt: null, source_start: null, source_end: null },
    deliverables: [evidence("One responsive page.")], acceptance_criteria: [evidence("The signup form validates email addresses.")] },
};
export const contract: Contract = { id: "contract-1", project_id: "project-1", filename: "synthetic.txt", media_type: "text/plain", size_bytes: source.length,
  sha256: "a".repeat(64), text: source, page_count: null, status: "REVIEW_REQUIRED", error_code: null,
  created_at: "2026-10-14T09:00:00Z", updated_at: "2026-10-14T10:00:00Z", review_revision: 1,
  milestones: [milestone], extraction: null };
export const summary: ContractSummary = { id: contract.id, project_id: contract.project_id, title: "Synthetic website", filename: contract.filename,
  status: contract.status, created_at: contract.created_at, updated_at: contract.updated_at, review_revision: 1,
  milestone_count: 1, pending_milestones: 1, change_request_count: 0, extraction_provider: "mocked_test_provider", extraction_mode: "offline_demo", live_ai: false };
export const finding: Finding = { ordinal: 1, requested_work: "Add an online checkout", classification: "OUT_OF_SCOPE",
  explanation: "The agreement explicitly excludes online payments.", contract_evidence: [{ source_excerpt: "Online payments are excluded.", relationship: "EXCLUSION", source_start: source.indexOf("Online payments"), source_end: source.length }],
  uncertainties: [], recommended_action: "DRAFT_CHANGE_ORDER", requires_human_review: true };
export const changeRequest: ChangeRequest = { id: "request-1", project_id: contract.project_id, contract_id: contract.id, title: "Add checkout", requested_changes: [finding.requested_work],
  contract_sha256: contract.sha256, contract_text_sha256: "b".repeat(64), status: "APPROVED", revision: 4, analysis_revision: 3,
  analysis: { findings: [finding], provider: "mocked_test_provider", model: null, mode: "offline_demo", live_ai: false, requires_human_review: true, review_flags: ["OFFLINE_FIXTURE_NOT_LIVE_AI"], edited_by_human: false },
  error_code: null, created_at: contract.created_at, updated_at: contract.updated_at, reviewed_at: contract.updated_at, reviewer_label: "Test reviewer", change_order_id: null };
export const jsonResponse = (data: unknown, status = 200) => ({ ok: status >= 200 && status < 300, status, headers: new Headers(), json: async () => data }) as Response;
