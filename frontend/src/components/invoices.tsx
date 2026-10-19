"use client";
import { useEffect, useRef, useState } from "react";
import { FileText, RefreshCw, Send, ShieldCheck } from "lucide-react";
import { api, ApiError } from "@/lib/api";
import { useMutation, useResource } from "@/lib/hooks";
import { dateTime, humanize, money } from "@/lib/format";
import type { Contract, InvoiceEvent, InvoiceLink, InvoicePreview, Milestone, ReviewContext } from "@/lib/types";
import { useAuth } from "./auth";
import { Badge, Button, Empty, Field, Loading, Modal, Problem, ReviewFields, Success } from "./ui";

export function InvoiceWorkspace({ contract, onRefresh }: { contract: Contract; onRefresh: () => void }) {
  const demoMode = useAuth()?.demoMode;
  const rows = contract.milestones.filter((row) => row.status === "APPROVED");
  const [selected, setSelected] = useState("");
  const row = rows.find((item) => item.id === selected) || rows[0];
  if (demoMode) return <div className="panel"><div className="panel-heading"><h2>Invoice demonstration</h2></div><Empty title="Provider invoice actions are disabled in this judge demo" description="Review and approve milestones, compare scope and prepare draft change orders. This synthetic demo does not create invoices, send email or execute payments. The guarded sandbox integration is available only in the separate operator workflow." /><div className="panel-body"><h3>Simulated invoice history</h3><div className="notice notice-amber"><strong>SIMULATED — NO REAL PAYMENT</strong></div><p>Illustrative scenario only · No PayPal invoice, email or payment exists for these examples. These states are separate from your saved approvals and financial records.</p><ol className="timeline">{[
    ["Draft example", "A simulated USD 1.00 draft is prepared after review. No payment is implied."],
    ["Sent example", "Simulated email acceptance illustrates a separate confirmed send. It does not establish delivery."],
    ["Paid example", "Simulated payment state for demonstration only. ScopeShield has not collected or verified a payment."],
  ].map(([title, description]) => <li key={title}><div className="timeline-main"><strong>{title} · SIMULATED</strong><p>{description}</p></div></li>)}</ol><p className="small muted">Historical real Gemini evidence and actual PayPal sandbox draft/read results are documented in the project reports. They are separate runs; this demonstration makes no fresh provider requests. Actual sandbox sending and settlement have not been verified.</p></div></div>;
  if (!row) return <div className="panel"><Empty title="Approve a milestone first" description="Only currently approved milestones can be checked for invoice eligibility. Review the source and approve a complete milestone before continuing." /></div>;
  return <><Field label="Approved milestone to invoice" htmlFor="invoice-milestone"><select id="invoice-milestone" value={row.id} onChange={(event) => setSelected(event.target.value)}>{rows.map((item) => <option value={item.id} key={item.id}>{item.name} · {money(item.amount, item.currency)}</option>)}</select></Field><MilestoneInvoice key={row.id} row={row} contract={contract} onRefresh={onRefresh} /></>;
}

export function sandboxInvoiceUrl(value: string | null): string | null {
  if (!value) return null;
  try { const url = new URL(value); return url.protocol === "https:" && ["www.sandbox.paypal.com", "sandbox.paypal.com"].includes(url.host) && !url.username && !url.password ? url.href : null; }
  catch { return null; }
}

export function MilestoneInvoice({ row, contract, onRefresh }: { row: Milestone; contract: Contract; onRefresh: () => void }) {
  const [version, setVersion] = useState(0); const [historyOpen, setHistoryOpen] = useState(false);
  const linked = useResource<InvoiceLink>(`/milestones/${row.id}/invoice`, version, true);
  const history = useResource<InvoiceEvent[]>(historyOpen ? `/milestones/${row.id}/invoice/history` : null, version);
  const [preview, setPreview] = useState<InvoicePreview | null>(null); const [key, setKey] = useState("");
  const [createOpen, setCreateOpen] = useState(false); const [sendOpen, setSendOpen] = useState(false);
  const [context, setContext] = useState<ReviewContext>({ reviewer_label: "", note: "" });
  const [confirmed, setConfirmed] = useState(false); const [ack, setAck] = useState(false); const [discrepancyNote, setDiscrepancyNote] = useState("");
  const [recoveredId, setRecoveredId] = useState(""); const [attempted, setAttempted] = useState(false);
  const creating = useRef(false); const action = useMutation(); const auth = useAuth();
  const link = linked.data;
  useEffect(() => { if (link) { setCreateOpen(false); if (link.send_state !== "NOT_REQUESTED") setSendOpen(false); } }, [link]);
  function refresh() { setVersion((old) => old + 1); onRefresh(); }
  const readContext = (note: string): ReviewContext => ({ reviewer_label: auth?.user.display_name || "Local reviewer", note });
  async function showPreview() {
    const ok = await action.run(() => api.invoicePreview(row.id), (value) => { if (value.existing_link_id) { refresh(); return; } setPreview(value); setKey(crypto.randomUUID()); setConfirmed(false); setAck(false); setCreateOpen(true); }, "Preview checked; no provider request was made.");
    if (ok) setAttempted(false);
  }
  const providerMode = link?.provider_mode || preview?.provider_mode;
  const url = sandboxInvoiceUrl(link?.recipient_view_url || null);
  return <section className="panel invoice-panel" aria-label="Milestone invoice"><div className="panel-heading"><div><span className="eyebrow">APPROVED WORK · EXPLICIT ACTIONS</span><h2><FileText size={18} />PayPal sandbox invoice</h2></div><Badge value={link?.paypal_status || "APPROVED"} /></div><div className="panel-body">
    <div className="notice notice-blue"><ShieldCheck size={17} /><p>{providerMode === "mocked_paypal_sandbox" ? "SIMULATED — NO REAL PAYMENT. Mocked PayPal responses for automated QA. No invoice or email exists at PayPal." : "PayPal sandbox only. Creating a draft and sending its email are separate confirmed actions. This workspace has no payment-execution control."}</p></div>
    <Problem error={linked.error || action.error} onRefresh={refresh} /><Success message={action.success} />
    {linked.loading && !link ? <Loading label="Checking the saved invoice association…" /> : !link ? <>
      <div className="invoice-facts"><div><span>Approved milestone</span><strong>{row.name}</strong></div><div><span>Approved amount</span><strong>{money(row.amount, row.currency)}</strong></div><div><span>Agreement</span><strong>{contract.filename}</strong></div><div><span>Approval revision</span><strong>{row.revision}</strong></div></div>
      <p>The preview checks current approval, totals and sandbox limits, and shows the configured recipient before creation.</p>
      {attempted && <div className="notice notice-amber"><p>A creation request was submitted. Check the saved attempt before taking another action. An uncertain outcome must be reconciled.</p></div>}
      <div className="button-row"><Button disabled={action.busy || (attempted && !(action.error instanceof ApiError && action.error.status >= 400 && action.error.status < 500))} onClick={() => void showPreview()}>Preview invoice draft</Button><Button variant="secondary" disabled={linked.loading || action.busy} onClick={refresh}>Check saved attempt</Button></div>
    </> : <>
      <div className="invoice-facts"><div><span>PayPal invoice ID</span><strong className="record-identifier">{link.invoice_id || "Not returned — reconcile the saved attempt"}</strong></div><div><span>Invoice amount</span><strong>{money(link.amount, link.currency)}</strong></div><div><span>Sandbox recipient</span><strong>{link.recipient_email || "Not captured in this legacy snapshot"}</strong></div><div><span>Agreement</span><strong>{contract.filename}</strong></div><div><span>Saved approval</span><strong>Milestone revision {link.milestone_revision} · contract revision {link.contract_review_revision}</strong></div><div><span>{providerMode === "mocked_paypal_sandbox" ? "Last checked in mock fixture" : "Last verified at PayPal"}</span><strong>{link.last_checked_at ? dateTime(link.last_checked_at) : "Not yet verified"}</strong></div></div>
      <p><strong>PayPal status: {link.paypal_status || "Unknown"}</strong> · {providerMode === "mocked_paypal_sandbox" ? "Simulated status only — never verified at PayPal" : link.verified ? "Historical sandbox check at the displayed time — no real payment" : "Unverified cached state — refresh or reconcile"}</p><p className="small muted">Local creation state: {link.state} (not payment) · Invoice number {link.invoice_number} · Send: {humanize(link.send_state)}</p>
      {link.error_code && <p className="validation-error">Last operation: {humanize(link.error_code)}. The saved association is retained.</p>}
      {link.send_state !== "NOT_REQUESTED" && <div className="notice notice-amber"><p>A send attempt is recorded. Refresh or reconcile the provider status; this workflow will not send a second email. Provider acceptance does not prove delivery or payment.</p></div>}
      <div className="button-row"><Button variant="secondary" disabled={action.busy || !link.invoice_id} onClick={() => void action.run(() => api.syncInvoice(link, readContext("User requested a read-only PayPal status refresh")), refresh, "Provider status checked; no new invoice was created.") }><RefreshCw size={15} />Refresh PayPal status</Button><Button disabled={action.busy || link.state !== "CREATED" || !link.verified || link.paypal_status !== "DRAFT" || !link.recipient_email || link.send_state !== "NOT_REQUESTED"} onClick={() => { setConfirmed(false); setSendOpen(true); }}><Send size={15} />Review sending email</Button>{url && <a className="button button-secondary" href={url} target="_blank" rel="noreferrer">Open sandbox invoice</a>}</div>
      <details className="invoice-recovery"><summary>Read-only recovery and reconciliation</summary><p>Search the saved invoice number, or verify an invoice ID recovered from PayPal. This never creates or sends an invoice.</p><Field label="Recovered invoice ID (optional)" htmlFor="recovered-invoice"><input id="recovered-invoice" value={recoveredId} maxLength={85} onChange={(event) => setRecoveredId(event.target.value)} placeholder="INV2-…" /></Field><Button variant="secondary" disabled={action.busy} onClick={() => void action.run(() => api.reconcileInvoice(link, readContext("User requested read-only reconciliation of the saved attempt"), recoveredId.trim() || undefined), refresh, "Reconciliation completed. Review the saved state and actual provider status.")}>Reconcile saved attempt</Button></details>
    </>}
    <details className="invoice-history" open={historyOpen} onToggle={(event) => setHistoryOpen(event.currentTarget.open)}><summary>Invoice audit history</summary>{historyOpen && <>{history.loading ? <Loading label="Loading saved invoice events…" /> : <><Problem error={history.error} onRefresh={history.reload} />{!history.data?.length ? <p>No invoice actions recorded.</p> : <ol className="timeline">{history.data.map((event) => <li key={event.id}><div className="timeline-main"><strong>{humanize(event.action)}</strong><p>{event.note}</p><time>{dateTime(event.created_at)}</time><details><summary>Recorded provider and action details</summary><pre>{JSON.stringify(event.data, null, 2)}</pre></details></div></li>)}</ol>}</>}</>}</details>
    <Modal open={createOpen && !link} onOpenChange={(open) => { if (!action.busy) setCreateOpen(open); }} title="Create one sandbox draft" description="Review these exact approved values and recipient. This action creates a draft only; no invoice email is sent.">
      {preview && <form onSubmit={async (event) => { event.preventDefault(); if (creating.current || !confirmed || (preview.discrepancies.length > 0 && !ack)) return; creating.current = true; setAttempted(true);
        try { await action.run(() => api.createInvoice(preview, key, context, preview.discrepancies.length ? discrepancyNote : ""), () => setCreateOpen(false), "Sandbox draft recorded. Sending remains a separate action."); }
        finally { creating.current = false; refresh(); }
      }}><PreviewFacts preview={preview} filename={contract.filename} /><Problem error={action.error} />
        {preview.discrepancies.length > 0 && <div className="notice notice-amber"><div><strong>Review billing discrepancies</strong><p>Contract total {money(preview.declared_total, preview.currency)} · Approved total {money(preview.approved_total, preview.currency)}</p><ul>{preview.discrepancies.map((flag) => <li key={flag}>{humanize(flag)}</li>)}</ul></div></div>}
        <ReviewFields value={context} onChange={setContext} prefix="invoice-create" />
        {preview.discrepancies.length > 0 && <><label className="checkbox-row"><input type="checkbox" required checked={ack} onChange={(event) => setAck(event.target.checked)} /><span>I reviewed the discrepancies and intend this exact partial draft.</span></label><Field label="Discrepancy explanation" htmlFor="invoice-discrepancy"><textarea id="invoice-discrepancy" required minLength={3} maxLength={1000} value={discrepancyNote} onChange={(event) => setDiscrepancyNote(event.target.value)} /></Field></>}
        <label className="checkbox-row"><input type="checkbox" required checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} /><span>I confirm creating one sandbox draft for this milestone and recipient.</span></label><div className="dialog-actions"><Button variant="secondary" type="button" disabled={action.busy} onClick={() => setCreateOpen(false)}>Back</Button><Button disabled={action.busy || !confirmed || (preview.discrepancies.length > 0 && !ack)}>Create sandbox draft</Button></div></form>}
    </Modal>
    <Modal open={sendOpen} onOpenChange={(open) => { if (!action.busy) setSendOpen(open); }} title="Send this sandbox invoice email" description="This is a separate action from draft creation. It sends the selected sandbox invoice to its saved recipient; it does not execute a payment.">
      {link && <form onSubmit={async (event) => { event.preventDefault(); if (!confirmed) return; await action.run(() => api.sendInvoice(link, context), () => setSendOpen(false), "PayPal accepted the send request. Review the actual provider status below."); refresh(); }}>
        <p><strong>{link.invoice_id}</strong> · {money(link.amount, link.currency)}</p><p>Recipient: <strong>{link.recipient_email}</strong></p><p>{contract.filename} · Milestone revision {link.milestone_revision}</p><Problem error={action.error} /><ReviewFields value={context} onChange={setContext} prefix="invoice-send" /><label className="checkbox-row"><input type="checkbox" required checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} /><span>I separately confirm sending this sandbox invoice email to the displayed recipient.</span></label><div className="dialog-actions"><Button type="button" variant="secondary" disabled={action.busy} onClick={() => setSendOpen(false)}>Back</Button><Button disabled={action.busy || !confirmed}>Confirm sending sandbox email</Button></div></form>}
    </Modal>
  </div></section>;
}

function PreviewFacts({ preview, filename }: { preview: InvoicePreview; filename: string }) {
  return <div className="invoice-preview"><Badge value="APPROVED" /><h3>{preview.name}</h3><p className="invoice-preview-amount">{money(preview.amount, preview.currency)}</p><dl><dt>Sandbox recipient</dt><dd>{preview.recipient_email}</dd><dt>Original agreement</dt><dd>{filename}</dd><dt>Approval</dt><dd>Milestone revision {preview.milestone_revision} · contract revision {preview.contract_review_revision}</dd></dl><p>{preview.description}</p>{preview.provider_mode === "mocked_paypal_sandbox" && <p className="notice notice-amber">Mocked PayPal fixture — no real provider action.</p>}</div>;
}
