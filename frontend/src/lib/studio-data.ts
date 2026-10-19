import type { Intelligence } from "@/components/project-intelligence";
import { money } from "./format";

export interface StudioEvidence {
  verified: boolean; quote: string; start: number; end: number;
  source: string; kind: string; field?: string;
}
export interface MilestoneRow {
  id: string; contract_id: string; ordinal: number; name: string; status: string;
  human_reviewed: boolean; revision: number; amount: string | null; currency: string | null;
  deadline: string | null; has_human_edits: boolean; origin: string; evidence: StudioEvidence[];
}
export interface InvoiceRow {
  id: string; contract_id: string; milestone_id: string; origin: string; local_state: string;
  provider_status: string; historical_check_corroborated: boolean; last_checked_at: string | null;
  amount: string | null; currency: string | null; notice: string;
}
export interface FindingRow {
  request_id: string; contract_id: string; title: string; ordinal: number; classification: string;
  work: string; explanation: string; human_reviewed: boolean; origin: string; evidence: StudioEvidence[];
}
export interface AnalyticsRow {
  id: string; contract_id: string; agreement: string; kind: string; title: string; review: string;
  classification: string; origin: string; amount: string; local_state: string; provider_status: string;
  evidence_count: number; count: number; tab: string;
}
export interface AnalyticsDetail { evidence: StudioEvidence[]; explanation: string; href: string; expansionCandidate: boolean }
export interface StudioSnapshot { rows: AnalyticsRow[]; details: Record<string, AnalyticsDetail>; unavailable: boolean }

// No numeric monetary fields enter the engine. Count is the only additive measure.
export function studioSnapshot(data: Intelligence): StudioSnapshot {
  const agreements = new Map(data.contracts.map((c) => [c.contract_id, c.name]));
  const rows: AnalyticsRow[] = []; const details: StudioSnapshot["details"] = {};
  const add = (id: string, cid: string, fields: Partial<AnalyticsRow>, evidence: StudioEvidence[], explanation: string, tab: string) => {
    if (!agreements.has(cid) || rows.some((r) => r.id === id)) return;
    const verified = evidence.filter((e) => e.verified && typeof e.quote === "string" && Number.isInteger(e.start) && e.end > e.start);
    rows.push({ id, contract_id: cid, agreement: agreements.get(cid)!, kind: "Unknown", title: "Unnamed record", review: "Unresolved", classification: "Not applicable", origin: "Unresolved", amount: "Not stated", local_state: "Not applicable", provider_status: "Not applicable", evidence_count: verified.length, count: 1, tab, ...fields });
    details[id] = { evidence: verified, explanation, href: `/contracts/${encodeURIComponent(cid)}?tab=${tab}`, expansionCandidate: false };
  };
  for (const m of data.milestone_rows || []) add(`milestone:${m.id}`, m.contract_id, {
    kind: "Milestone", title: `${m.ordinal}. ${m.name || "Unnamed milestone"}`, review: m.human_reviewed ? "Freelancer approved" : m.status === "REJECTED" ? "Rejected" : "Needs review",
    origin: m.origin, amount: money(m.amount, m.currency),
  }, m.evidence || [], `Saved milestone revision ${m.revision}. ${m.has_human_edits ? "Human-edited terms may differ from the original candidate quotations." : "Quotations describe original extracted candidates."} Approval is not client consent or completed work.`, "milestones");
  for (const r of data.requests) {
    const findings = (data.findings || []).filter((f) => f.request_id === r.id && f.contract_id === r.contract_id);
    const classification = ["IN_SCOPE", "OUT_OF_SCOPE", "AMBIGUOUS", "UNRESOLVED"].includes(r.classification) ? r.classification : "UNRESOLVED";
    add(`request:${r.id}`, r.contract_id, { kind: "Scope request", title: r.title, review: r.status === "REJECTED" || r.status === "CANCELLED" ? "Rejected / cancelled" : r.human_reviewed && classification !== "UNRESOLVED" ? "Freelancer reviewed" : "Needs review", classification, origin: r.origin }, findings.flatMap((f) => f.evidence), findings.map((f) => `${f.work}: ${f.explanation}`).join("\n") || "No rechecked expansion evidence available. Open the saved analysis for the complete record.", "scope");
    if (details[`request:${r.id}`]) details[`request:${r.id}`].expansionCandidate = findings.some((f) => f.classification === "OUT_OF_SCOPE");
  }
  const milestones = new Map((data.milestone_rows || []).map((m) => [m.id, m]));
  for (const i of data.invoices || []) {
    const m = milestones.get(i.milestone_id);
    add(`invoice:${i.id}`, i.contract_id, { kind: "Sandbox invoice", title: m && m.contract_id === i.contract_id ? `Invoice · ${m.name}` : "Invoice · milestone unresolved", review: m?.contract_id === i.contract_id && m.human_reviewed ? "Milestone currently approved" : "Milestone approval unresolved", origin: i.origin, amount: money(i.amount, i.currency), local_state: i.local_state, provider_status: i.provider_status }, [], `${i.notice}. Saved status only; no refresh or real settlement. ${i.historical_check_corroborated ? "Saved sandbox check has matching history." : "No corroborated historical provider check."} Current milestone approval does not prove approval at invoice creation.`, "invoices");
  }
  return { rows, details, unavailable: !Array.isArray(data.milestone_rows) || !Array.isArray(data.invoices) || !Array.isArray(data.findings) };
}

export const STUDIO_PROMPTS = [
  "Which milestones still need review?",
  "Which scope requests remain unresolved?",
  "Show sandbox invoices associated with approved milestones.",
  "Which client requests are scope-expansion candidates?",
] as const;

// Closed, exact query vocabulary. Source text is data, never executable instructions.
export function answerStudioQuestion(snapshot: StudioSnapshot, prompt: string): string {
  const index = STUDIO_PROMPTS.findIndex((p) => p.toLowerCase() === prompt.trim().toLowerCase());
  const prefix = "OFFLINE RULE-BASED QUERY — not live AI. Current project snapshot; all records, independent of dashboard filters. ";
  if (index < 0 || prompt.length > 240) return prefix + "Choose one of the four supported questions. This assistant cannot approve, send, create, pay, change data, or infer new obligations.";
  if (snapshot.unavailable) return prefix + "Required analytics fields are missing. Refresh the project; no conclusion is available.";
  const rows = snapshot.rows.filter((r) => index === 0 ? r.kind === "Milestone" && r.review === "Needs review" : index === 1 ? r.kind === "Scope request" && r.review !== "Rejected / cancelled" && (r.classification === "UNRESOLVED" || r.classification === "AMBIGUOUS" || r.review === "Needs review") : index === 2 ? r.kind === "Sandbox invoice" && r.review === "Milestone currently approved" : r.kind === "Scope request" && r.review !== "Rejected / cancelled" && snapshot.details[r.id]?.expansionCandidate);
  const caveat = index === 2 ? "Sandbox and simulated records never prove real payment; approval is current, not historical. " : index === 3 ? "Out-of-scope classifications are review candidates, not proof of a new deliverable. No new inference is made. " : "Human review remains required. ";
  return prefix + caveat + (rows.length ? `${rows.length} matching record(s):\n` + rows.map((r) => `${r.kind} [${r.id}] — ${r.review}; ${r.classification}; ${r.origin}; provider ${r.provider_status}`).join("\n") : "No matching records in this complete bounded snapshot.");
}

export function localStudioAllowed(flag: string | undefined, hostname: string, profile: string | undefined): boolean {
  return flag === "local-evaluation" && profile !== "production" && ["localhost", "127.0.0.1", "[::1]"].includes(hostname);
}
