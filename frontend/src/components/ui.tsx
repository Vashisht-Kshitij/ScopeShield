"use client";
import { useEffect, useRef, useState, type ButtonHTMLAttributes, type ReactNode } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import Link from "next/link";
import { AlertCircle, ArrowUpRight, Check, CircleCheck, FileText, LoaderCircle, RefreshCw, X } from "lucide-react";
import { ApiError, apiBase } from "@/lib/api";
import { humanize, dateTime } from "@/lib/format";
import type { Activity, Contract, PdfProvenance, Quote, ReviewContext, ReviewDecision } from "@/lib/types";

export function Button({ variant = "primary", className = "", children, ...props }: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "secondary" | "ghost" | "danger" }) {
  return <button className={`button button-${variant} ${className}`} {...props}>{children}</button>;
}
export function Badge({ value, children }: { value: string; children?: ReactNode }) {
  const tone = ["APPROVED", "IN_SCOPE", "CONNECTED"].includes(value) ? "green" : ["REJECTED", "OUT_OF_SCOPE", "FAILED"].includes(value) ? "red" : ["REVIEW_REQUIRED", "AMBIGUOUS", "PENDING"].includes(value) ? "amber" : "neutral";
  return <span className={`badge badge-${tone}`}><span className="badge-dot" />{children || humanize(value)}</span>;
}
export function Loading({ label = "Loading your workspace…" }: { label?: string }) {
  return <div className="loading-state" role="status"><LoaderCircle size={22} className="spin" /><p>{label}</p><div className="skeleton" /><div className="skeleton short" /></div>;
}
export function Empty({ title, description, children }: { title: string; description: string; children?: ReactNode }) {
  return <div className="empty-state"><div className="empty-icon"><FileText size={23} /></div><h3>{title}</h3><p>{description}</p>{children}</div>;
}
export function Problem({ error, onRefresh }: { error: Error | null; onRefresh?: () => void }) {
  if (!error) return null;
  const conflict = error instanceof ApiError && error.status === 409;
  const offline = error instanceof ApiError && error.code === "OFFLINE_DEMO_CONTRACT_MISMATCH";
  return <div className={`notice ${conflict || offline ? "notice-amber" : "notice-red"}`} role="alert"><AlertCircle size={18} /><div><strong>{offline ? "Choose a compatible offline example" : conflict ? "Review the latest record before continuing" : "This action could not be completed"}</strong><p>{offline ? "Offline mode supports only predefined agreements and their exact sample requests. Your agreement and saved request are preserved." : error.message}</p>{error instanceof ApiError && error.code && <code>{error.code}</code>}{offline && <Link className="button button-secondary" href="/demos">Choose a compatible sample</Link>}{conflict && <p>Your edit has not overwritten saved data. Refreshing replaces the form with the latest saved version.</p>}{onRefresh && <Button type="button" variant="secondary" onClick={onRefresh}><RefreshCw size={14} />{conflict ? "Refresh latest record" : "Try loading again"}</Button>}</div></div>;
}
export function Success({ message }: { message: string }) { return message ? <div role="status" className="notice notice-green"><CircleCheck size={17} /><span>{message}</span></div> : null; }
export function Field({ label, htmlFor, hint, children }: { label: string; htmlFor: string; hint?: string; children: ReactNode }) {
  return <div className="field"><label htmlFor={htmlFor}>{label}</label>{children}{hint && <span className="field-hint">{hint}</span>}</div>;
}
export function ReviewFields({ value, onChange, prefix = "review" }: { value: ReviewContext; onChange: (value: ReviewContext) => void; prefix?: string }) {
  return <div className="review-fields"><Field label="Reviewer name" htmlFor={`${prefix}-name`} hint="Saved as a review label. Your signed-in account is also recorded."><input id={`${prefix}-name`} required minLength={3} maxLength={100} value={value.reviewer_label} onChange={(event) => onChange({ ...value, reviewer_label: event.target.value })} placeholder="Your name or operator label" autoComplete="off" /></Field><Field label="Review note" htmlFor={`${prefix}-note`} hint="Saved with the action in the audit history."><textarea id={`${prefix}-note`} required minLength={3} maxLength={1000} value={value.note} onChange={(event) => onChange({ ...value, note: event.target.value })} placeholder="What did you review or change?" rows={2} /></Field></div>;
}
export function Modal({ open, onOpenChange, title, description, children, wide = false }: { open: boolean; onOpenChange: (value: boolean) => void; title: string; description: string; children: ReactNode; wide?: boolean }) {
  return <Dialog.Root open={open} onOpenChange={onOpenChange}><Dialog.Portal><Dialog.Overlay className="dialog-overlay" /><Dialog.Content className={`dialog-content ${wide ? "dialog-wide" : ""}`}><Dialog.Title className="dialog-title">{title}</Dialog.Title><Dialog.Description className="dialog-description">{description}</Dialog.Description><Dialog.Close className="dialog-close" aria-label="Close dialog"><X size={20} /></Dialog.Close>{children}</Dialog.Content></Dialog.Portal></Dialog.Root>;
}
export function ConfirmReview({ label, description, onConfirm, destructive = false, acknowledge = true, disabled = false, details, error }: {
  label: string; description: string; onConfirm: (context: ReviewDecision) => Promise<boolean>; destructive?: boolean;
  acknowledge?: boolean; disabled?: boolean; details?: ReactNode; error?: Error | null;
}) {
  const [open, setOpen] = useState(false); const [busy, setBusy] = useState(false);
  const [context, setContext] = useState<ReviewContext>({ reviewer_label: "", note: "" }); const [ack, setAck] = useState(false);
  return <><Button variant={destructive ? "danger" : "primary"} disabled={disabled} onClick={() => setOpen(true)}>{destructive ? <X size={15} /> : <Check size={15} />}{label}</Button><Modal open={open} onOpenChange={(value) => { if (!busy) setOpen(value); }} title={label} description={description}><form onSubmit={async (event) => { event.preventDefault(); setBusy(true); try { if (await onConfirm({ ...context, confirmed: true, acknowledge_review_flags: ack })) setOpen(false); } finally { setBusy(false); } }}>{details}<Problem error={error || null} /><ReviewFields value={context} onChange={setContext} prefix={`confirm-${label.replaceAll(" ", "-")}`} />{acknowledge && <label className="checkbox-row"><input type="checkbox" required checked={ack} onChange={(event) => setAck(event.target.checked)} /><span>I reviewed the source evidence, current values, uncertainties and provenance flags.</span></label>}<p className="small muted">This records an internal human review. It does not create or send an invoice.</p><div className="dialog-actions"><Button type="button" variant="secondary" disabled={busy} onClick={() => setOpen(false)}>Back</Button><Button variant={destructive ? "danger" : "primary"} disabled={busy}>{busy && <LoaderCircle size={15} className="spin" />}{busy ? "Saving decision…" : `Confirm ${label.toLowerCase()}`}</Button></div></form></Modal></>;
}
export function verifiedRange(text: string, quote: Quote): [number, number] | null {
  const { source_start: start, source_end: end, source_excerpt: excerpt } = quote;
  const index = text.indexOf(excerpt);
  const unique: [number, number] | null = excerpt && index >= 0 && text.indexOf(excerpt, index + 1) < 0 ? [index, index + excerpt.length] : null;
  // API offsets count Unicode code points (Python); DOM slicing counts UTF-16 units.
  if (start != null || end != null) {
    if (start == null || end == null || !Number.isSafeInteger(start) || !Number.isSafeInteger(end) || start < 0 || end <= start) return unique;
    const points = Array.from(text);
    if (end > points.length) return unique;
    const left = points.slice(0, start).join("").length;
    const right = left + points.slice(start, end).join("").length;
    return text.slice(left, right) === excerpt ? [left, right] : unique;
  }
  return unique;
}
export function EvidenceQuote({ quote, text, onSelect }: { quote: Quote; text: string; onSelect?: (quote: Quote) => void }) {
  const verified = verifiedRange(text, quote);
  return <div className="evidence-quote"><div className="evidence-label"><span>{verified ? <><CircleCheck size={13} /> Verified source excerpt</> : <><AlertCircle size={13} /> Unverified excerpt</>}</span>{quote.relationship && <small>{humanize(quote.relationship)}</small>}</div><blockquote>{quote.source_excerpt}</blockquote>{verified && onSelect && <button type="button" className="text-button" onClick={() => onSelect(quote)}>Locate in agreement <ArrowUpRight size={13} /></button>}</div>;
}
export function originalPages(text: string, quote: Quote, provenance?: PdfProvenance | null): number[] {
  const { source_start: start, source_end: end } = quote;
  if (!provenance || provenance.method !== "pypdf-physical-pages-v1" || start == null || end == null ||
      !Number.isSafeInteger(start) || !Number.isSafeInteger(end) || start < 0 || end <= start) return [];
  const points = Array.from(text);
  if (end > points.length || points.slice(start, end).join("") !== quote.source_excerpt) return [];
  return provenance.pages.filter((span) => span.start < end && span.end > start && span.end > span.start).map((span) => span.page);
}
export function SourcePanel({ text, filename, selected, contract }: { text: string; filename: string; selected: Quote | null; contract?: Contract }) {
  const range = selected ? verifiedRange(text, selected) : null;
  const pages = selected ? originalPages(text, selected, contract?.pdf_provenance) : [];
  const paper = useRef<HTMLDivElement>(null); const highlight = useRef<HTMLElement>(null);
  function reveal() {
    const panel = paper.current; const mark = highlight.current;
    if (panel && mark) panel.scrollTo?.({ top: Math.max(0, panel.scrollTop + mark.getBoundingClientRect().top - panel.getBoundingClientRect().top - panel.clientHeight / 3), behavior: "smooth" });
  }
  useEffect(reveal, [selected, text]);
  return <aside className="source-panel" aria-label="Agreement source"><div className="source-heading"><div className="document-icon"><FileText size={18} /></div><div><h3>Extracted agreement text</h3><p>{filename}</p></div><span className="eyebrow">SOURCE</span></div>{range && <div className="source-found"><CircleCheck size={14} /> Verified excerpt highlighted <a href="#source-highlight" onClick={(event) => { event.preventDefault(); reveal(); highlight.current?.focus({ preventScroll: true }); }}>Jump to excerpt</a></div>}{selected && <p className="source-footnote">{pages.length ? `Original PDF physical page${pages.length > 1 ? "s" : ""}: ${pages.join(", ")}` : "Original PDF page provenance unavailable for this excerpt."}</p>}{contract?.original_pdf_available && <p className="source-footnote"><a href={`${apiBase()}/api/contracts/${contract.id}/original`}>Download original PDF</a></p>}<div ref={paper} className="source-paper" role="region" tabIndex={0} aria-label="Extracted agreement text text">{range ? <>{text.slice(0, range[0])}<mark ref={highlight} tabIndex={-1} id="source-highlight">{text.slice(range[0], range[1])}</mark>{text.slice(range[1])}</> : text}</div><div className="source-footnote">Stored extracted text · Human edits do not change this text. {contract?.original_pdf_available ? "Physical page indexes identify extraction pages, not printed page labels or visual coordinates. The page reference locates the selected extracted occurrence. Repeated quotations do not identify a unique clause. Reading order and signatures are not verified; inspect all contexts in the downloaded original." : "PDF layout, signatures and original pages are not retained."}</div></aside>;
}
export function DataTable({ headers, children, caption }: { headers: string[]; children: ReactNode; caption: string }) {
  return <div className="table-scroll"><table><caption className="sr-only">{caption}</caption><thead><tr>{headers.map((header) => <th scope="col" key={header}>{header}</th>)}</tr></thead><tbody>{children}</tbody></table></div>;
}
export function AuditTimeline({ events }: { events: Activity[] }) {
  if (!events.length) return <Empty title="No activity recorded" description="Saved actions for this agreement will appear here." />;
  return <ol className="timeline">{events.map((event) => <li key={event.id}><span className={`timeline-dot ${event.action.includes("APPROVED") || event.action.includes("SAVED") ? "done" : ""}`} /><div className="timeline-main"><div className="timeline-title"><strong>{humanize(event.action)}</strong>{event.revision != null && <span className="revision">Revision {event.revision}</span>}</div><p>{event.note || (event.kind === "contract" ? (event.action === "CONTRACT_UPLOADED" ? "Agreement stored in the local workspace." : "Recorded by the contract extraction service.") : "Saved backend event.")}</p><div className="timeline-meta"><time dateTime={event.created_at}>{dateTime(event.created_at)}</time>{event.reviewer_label && <span>by {event.reviewer_label}</span>}<span>{humanize(event.kind)}</span></div>{Object.keys(event.details).length > 0 && <details><summary>View recorded details</summary><pre>{JSON.stringify(event.details, null, 2)}</pre></details>}</div></li>)}</ol>;
}
