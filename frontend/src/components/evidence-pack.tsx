"use client";
import { Download, FileCheck2, RefreshCw } from "lucide-react";
import { api } from "@/lib/api";
import { useMutation, useResource } from "@/lib/hooks";
import { dateTime } from "@/lib/format";
import { Button, Loading, Problem, Success } from "./ui";

export interface EvidenceSummary {
  project_id: string; source_revision: string; generated_at: string;
  counts: Record<"contracts" | "milestones" | "requests" | "orders" | "invoices", number>;
  warnings: string[]; sections: string[];
}
export function EvidencePack({ projectId, version = 0 }: { projectId: string; version?: number }) {
  const preview = useResource<EvidenceSummary>(`/projects/${projectId}/evidence-pack`, version);
  const action = useMutation(); const summary = preview.data;
  async function download(executive = false) {
    if (!summary) return;
    await action.run(async () => {
      const blob = await api.evidencePdf(projectId, summary.source_revision, executive);
      const url = URL.createObjectURL(blob);
      try {
        const link = document.createElement("a"); link.href = url;
        link.download = `scopeshield-${executive ? "summary" : "evidence"}-${summary.source_revision.slice(0, 12)}.pdf`;
        link.click();
      } finally { setTimeout(() => URL.revokeObjectURL(url), 1000); }
    }, undefined, "PDF download prepared. This private copy contains the exact source snapshot; share it deliberately.");
  }
  return <section className="panel evidence-pack"><div className="panel-heading"><div><span className="eyebrow">A TRACEABLE BASIS FOR NEGOTIATION</span><h2><FileCheck2 size={20} /> Evidence Pack</h2><p>One private report across every agreement in this project.</p></div><Button variant="secondary" disabled={action.busy || preview.loading} onClick={preview.reload}><RefreshCw size={15} />Refresh summary</Button></div><div className="panel-body"><p>Bring original quotations, chronological messages, reviewed findings, proposed terms and saved sandbox invoice history into a readable PDF. Generating a report does not approve work or contact AI or payment providers.</p><Problem error={preview.error || action.error} onRefresh={preview.reload} /><Success message={action.success} />{preview.loading ? <Loading label="Checking project evidence…" /> : summary && <><div className="contract-stats">{Object.entries(summary.counts).map(([label, count]) => <div key={label}><span>{label.toUpperCase()}</span><strong>{count}</strong></div>)}</div><div className="notice notice-amber"><div><strong>Review before sharing</strong><ul>{summary.warnings.map((warning) => <li key={warning}>{warning}</li>)}</ul></div></div><div className="evidence-summary"><div><h3>Inside your report</h3><ol>{summary.sections.map((title, index) => <li key={`${index}-${title}`}>{title.replace(/^\d+ \/ /, "")}</li>)}</ol></div><div><h3>Snapshot integrity</h3><p>Checked {dateTime(summary.generated_at)}</p><p className="small muted">Revision <code className="evidence-revision">{summary.source_revision}</code></p><p>If the project changes, refresh this summary before downloading. Unverified evidence remains unresolved. Manual messages do not authenticate a sender or date.</p><p className="small muted">The PDF includes an exact JSON source snapshot. Common Latin punctuation and currency symbols use embedded fonts. Unsupported scripts and characters use explicit Unicode escapes in the printed view. Access is limited to the owning freelancer; client sharing does not grant this export.</p></div></div><div className="button-row"><Button disabled={action.busy} onClick={() => void download(true)}><Download size={16} />Download executive summary</Button><Button variant="secondary" disabled={action.busy} onClick={() => void download()}><Download size={16} />{action.busy ? "Preparing PDF…" : "Download Evidence Pack PDF"}</Button></div><p className="small muted">Executive summary: two pages with selected exact quotations, review progress and next actions. Complete pack: all evidence, sources, audit history and technical references. Both include a private JSON source snapshot tied to the preview revision.</p></>}</div></section>;
}
