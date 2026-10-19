// Matches FastAPI 0.9 schemas. Monetary values remain decimal strings.
export type ReviewStatus = "DRAFT" | "REVIEW_REQUIRED" | "APPROVED" | "REJECTED";
export type Classification = "IN_SCOPE" | "OUT_OF_SCOPE" | "AMBIGUOUS";
export type Mode = "configured" | "offline_demo";
export interface Evidence<T = string> { value: T | null; source_excerpt: string | null; source_start: number | null; source_end: number | null }
export interface Quote { source_excerpt: string; source_start?: number | null; source_end?: number | null; relationship?: "COVERAGE" | "EXCLUSION" | "LIMIT" | "CONTEXT" | "CONFLICT" }
export interface ExtractedMilestone {
  name: Evidence; amount: Evidence; currency: Evidence; payment_conditions: Evidence; deadline: Evidence;
  deliverables: Evidence[]; acceptance_criteria: Evidence[];
}
export interface Milestone {
  id: string; ordinal: number; contract_id: string; name: string | null; description: string | null;
  amount: string | null; currency: string | null; payment_conditions: string | null; deadline: string | null;
  deliverables: string[]; acceptance_criteria: string[]; status: ReviewStatus; evidence: ExtractedMilestone;
  revision: number; contract_review_revision: number; has_human_edits: boolean;
  updated_at: string; reviewed_at: string | null; reviewer_label: string | null;
}
export interface Extraction {
  provider: string; model: string | null; mode: Mode; live_ai: boolean;
  requires_human_review: true; milestone_total: string | null; totals_match: boolean | null;
  review_issues: { field: string; code: string }[];
  data: { project_title: Evidence; currency: Evidence; total_value: Evidence; deliverables: Evidence[]; milestones: ExtractedMilestone[] };
}
export interface Contract {
  original_pdf_available?: boolean; pdf_provenance?: PdfProvenance | null;
  ai_input_eligible?: boolean; ai_input_message?: string;
  id: string; project_id: string; filename: string; media_type: string; size_bytes: number; sha256: string; text: string;
  page_count: number | null; status: "UPLOADED" | "PROCESSING" | "REVIEW_REQUIRED" | "FAILED";
  error_code: string | null; created_at: string; updated_at: string; extraction: Extraction | null;
  milestones: Milestone[]; review_revision: number;
}
export interface PdfProvenance {
  method: "pypdf-physical-pages-v1"; text_sha256: string;
  document_sha256?: string;
  pages: { page: number; start: number; end: number }[];
}
export interface ContractSummary {
  id: string; project_id: string; title: string; filename: string; status: Contract["status"];
  created_at: string; updated_at: string; review_revision: number; milestone_count: number;
  pending_milestones: number; change_request_count: number; extraction_provider: string | null;
  extraction_mode: Mode | null; live_ai: boolean | null;
}
export interface ContractMilestones {
  summary: { declared_total: string | null; milestone_total: string | null; active_total: string | null;
    approved_total: string | null; currency: string | null; discrepancies: string[]; contract_review_revision: number };
  milestones: Milestone[];
}
export interface Project { id: string; title: string | null; currency: string | null; total_value: string | null; status: string; created_at: string; contract_ids: string[] }
export interface Finding {
  ordinal: number; requested_work: string; classification: Classification; explanation: string;
  contract_evidence: (Quote & { relationship: NonNullable<Quote["relationship"]> })[];
  uncertainties: string[];
  recommended_action: "FULFILL_EXISTING_SCOPE" | "DRAFT_CHANGE_ORDER" | "CLARIFY_WITH_CLIENT";
  requires_human_review: true;
  message_evidence?: MessageExcerpt[];
  change_types?: ("DELIVERABLE" | "DEADLINE" | "MAINTENANCE" | "REVISIONS" | "OTHER")[];
  conversation_effect?: "INDIVIDUAL" | "REPEATED" | "CONFLICTING" | "CUMULATIVE" | null;
}
export interface ConversationMessageInput {
  id?: string; sequence: number; sender: string; role: "client" | "freelancer" | "other";
  content: string; sent_at: string | null;
}
export interface ConversationMessage extends ConversationMessageInput { id: string }
export interface MessageExcerpt { message_id: string; source_excerpt: string; source_start: number | null; source_end: number | null }
export interface Analysis {
  findings: Finding[]; provider: string; model: string | null; mode: Mode; live_ai: boolean;
  requires_human_review: true; review_flags: string[]; edited_by_human: boolean;
}
export interface ChangeRequest {
  id: string; project_id: string; contract_id: string; title: string; requested_changes: string[];
  contract_sha256: string; contract_text_sha256: string;
  status: "PENDING" | "ANALYZING" | "REVIEW_REQUIRED" | "APPROVED" | "REJECTED" | "CANCELLED";
  revision: number; analysis_revision: number | null; analysis: Analysis | null; error_code: string | null;
  created_at: string; updated_at: string; reviewed_at: string | null; reviewer_label: string | null; change_order_id: string | null;
  conversation_messages?: ConversationMessage[] | null;
  message_accounting?: { message_id: string; sequence: number; duplicate_of_message_id: string | null; counts_as_individual: boolean }[];
}
export interface OrderTerms { proposed_deliverables: string[]; proposed_price: string | null; currency: string | null; proposed_deadline: string | null }
export interface ChangeOrder extends OrderTerms {
  id: string; change_request_id: string; project_id: string; contract_id: string; contract_filename: string;
  supporting_evidence: Quote[]; uncertainties: string[]; status: "REVIEW_REQUIRED" | "APPROVED" | "REJECTED";
  revision: number; request_revision: number; analysis_revision: number; terms_origin: "human_entered";
  requires_human_review: true; created_at: string; updated_at: string; reviewed_at: string | null; reviewer_label: string | null;
}
export interface Activity {
  id: string; entity_id: string; kind: string; action: string; created_at: string; revision: number | null;
  reviewer_label: string | null; note: string | null; details: Record<string, unknown>;
}
export interface ReviewContext { reviewer_label: string; note: string }
export interface ReviewDecision extends ReviewContext { confirmed: true; acknowledge_review_flags: boolean }
export interface MilestoneChanges {
  name: string | null; description: string | null; amount: string | null; currency: string | null;
  payment_conditions: string | null; deadline: string | null; deliverables: string[]; acceptance_criteria: string[];
}

export interface InvoicePreview {
  environment: "paypal_sandbox"; contract_id: string; milestone_id: string; milestone_revision: number;
  contract_review_revision: number; name: string; description: string; amount: string; currency: "USD";
  declared_total: string; approved_total: string; discrepancies: string[]; review_flags: string[];
  preview_digest: string; existing_link_id: string | null; recipient_email: string;
  provider_mode: "paypal_sandbox" | "mocked_paypal_sandbox";
}
export interface InvoiceLink {
  environment: "paypal_sandbox"; id: string; milestone_id: string; contract_id: string;
  milestone_revision: number; contract_review_revision: number; invoice_number: string;
  state: "CREATING" | "CREATED" | "RECONCILIATION_REQUIRED"; invoice_id: string | null;
  paypal_status: string | null; amount: string; currency: string; verified: boolean; error_code: string | null;
  revision: number; snapshot: { preview?: InvoicePreview; discrepancy_note?: string | null; [key: string]: unknown };
  created_at: string; updated_at: string; last_checked_at: string | null; recipient_view_url: string | null;
  recipient_email: string | null; provider_mode: InvoicePreview["provider_mode"];
  send_state: "NOT_REQUESTED" | "REQUESTED" | "ACCEPTED" | "UNCERTAIN";
}
export interface InvoiceEvent {
  id: string; milestone_id: string; link_id: string | null; action: string;
  reviewer_label: string; note: string; data: Record<string, unknown>; created_at: string;
}
export interface DemoScenario {
  id: string; title: string; description: string; filename: string; text: string;
  supports_extraction: boolean; requested_changes: string[]; mode: "offline_demo"; live_ai: false;
  conversation_messages?: ConversationMessageInput[];
}
