"use client";

import { useState } from "react";

import { post } from "@/lib/api";

import { useMutation, useResource } from "@/lib/hooks";

import { money, dateTime } from "@/lib/format";

import type { Quote } from "@/lib/types";

import { Badge, Button, Empty, EvidenceQuote, Field, Loading, Modal, Problem, Success } from "./ui";

import type { SessionUser } from "./auth";



type ResponseRecord = { id: string; client_id: string; order_revision: number; decision: string; note: string; created_at: string };

type SharedOrder = { id: string; revision: number; proposed_deliverables: string[]; proposed_price: string | null; currency: string | null; proposed_deadline: string | null; supporting_evidence: Quote[]; uncertainties: string[]; provenance: { provider: string; mode: string; live_ai: boolean }; responses: ResponseRecord[] };

type SharedProject = { id: string; title: string; contracts: { id: string; filename: string; text: string; approved_milestones: { name: string; amount: string | null; currency: string | null; deliverables: string[] }[]; orders: SharedOrder[] }[] };



export function SharingPanel({ projectId }: { projectId: string }) {

  const result = useResource<SessionUser[]>(`/projects/${projectId}/sharing`);

  const [username, setUsername] = useState(""); const [confirmed, setConfirmed] = useState(false); const action = useMutation();

  return <details className="panel sharing-panel"><summary>Client access to this project</summary><div className="panel-body"><p>Sharing includes all current and future agreements in this project, their stored source text, approved milestones and internally approved change orders. Private review history, raw AI output and invoice information stay in the freelancer workspace.</p><Problem error={result.error || action.error} /><Success message={action.success} />{result.data?.map((client) => <div className="sharing-row" key={client.id}><span>{client.username}</span><Button variant="secondary" disabled={action.busy} onClick={() => void action.run(() => post(`/projects/${projectId}/sharing`, { username: client.username, enabled: false, confirmed: true }), result.reload, "Client access revoked.")}>Revoke access</Button></div>)}<form onSubmit={(event) => { event.preventDefault(); void action.run(() => post(`/projects/${projectId}/sharing`, { username, enabled: true, confirmed: true }), () => { result.reload(); setUsername(""); setConfirmed(false); }, "Project shared with the selected client."); }}><Field label="Client username" htmlFor="share-username"><input id="share-username" required minLength={3} maxLength={100} value={username} onChange={(event) => setUsername(event.target.value)} autoComplete="off" /></Field><label className="checkbox-row"><input type="checkbox" required checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} /><span>I reviewed the project contents and intend to share them with this client.</span></label><Button disabled={action.busy || !confirmed}>Share project</Button></form></div></details>;

}



export function ClientResponses({ orderId }: { orderId: string }) {

  const result = useResource<SharedOrder>(`/change-orders/${orderId}/client-responses`);

  return <section className="client-responses"><div className="panel-heading"><h3>Client responses</h3><Button variant="secondary" onClick={result.reload}>Refresh responses</Button></div><p className="small muted">An internally approved order is available to clients with project access. Responses are separate records and never trigger invoicing.</p><Problem error={result.error} />{result.data?.responses.length ? result.data.responses.map((row) => <div className="notice notice-blue" key={row.id}><div><strong>{row.decision} · order revision {row.order_revision}</strong><p>{row.note}</p><span className="small">Client {row.client_id} · {dateTime(row.created_at)}</span></div></div>) : <p className="muted">No client response recorded.</p>}</section>;

}



export function ClientPortal() {

  const projects = useResource<{ id: string; title: string }[]>("/client/projects");

  const [selected, setSelected] = useState(""); const [version, setVersion] = useState(0);

  const id = selected || projects.data?.[0]?.id; const detail = useResource<SharedProject>(id ? `/client/projects/${id}` : null, version);

  return <><div className="page-heading"><div><span className="eyebrow">CLIENT WORKSPACE</span><h1>Shared projects</h1><p className="muted">Review shared agreements and respond to proposed additional work.</p></div><Button variant="secondary" onClick={() => { projects.reload(); setVersion(version + 1); }}>Refresh projects</Button></div><Problem error={projects.error || detail.error} onRefresh={() => { projects.reload(); setVersion(version + 1); }} />{projects.loading ? <Loading label="Loading shared projects…" /> : !projects.data?.length ? <Empty title="No projects shared yet" description="Your freelancer can grant access to a project from its agreement workspace." /> : <><Field label="Shared project" htmlFor="client-project"><select id="client-project" value={id} onChange={(event) => setSelected(event.target.value)}>{projects.data.map((row) => <option key={row.id} value={row.id}>{row.title}</option>)}</select></Field>{detail.loading ? <Loading label="Loading permitted project information…" /> : detail.data?.contracts.map((contract) => <section className="panel client-contract" key={contract.id}><div className="panel-heading"><h2>{contract.filename}</h2></div><div className="panel-body"><details><summary>Read shared agreement</summary><pre className="shared-source">{contract.text}</pre></details><h3>Approved milestones</h3>{contract.approved_milestones.length ? contract.approved_milestones.map((row, index) => <p key={index}>{row.name} · {money(row.amount, row.currency)}</p>) : <p className="muted">No approved milestones shared yet.</p>}<h3>Proposed change orders</h3>{!contract.orders.length && <p className="muted">No internally approved orders available.</p>}{contract.orders.map((order) => <ClientOrder key={order.id} order={order} text={contract.text} onRefresh={() => setVersion(version + 1)} />)}</div></section>)}</>}</>;

}



function ClientOrder({ order, text, onRefresh }: { order: SharedOrder; text: string; onRefresh: () => void }) {

  const [decision, setDecision] = useState<"APPROVED" | "REJECTED" | null>(null); const [note, setNote] = useState(""); const [confirmed, setConfirmed] = useState(false); const action = useMutation();

  return <article className="client-order"><p className="small muted">Order {order.id.slice(0, 8)} · revision {order.revision}</p><ul>{order.proposed_deliverables.map((value, index) => <li key={index}>{value}</li>)}</ul><p className="small muted">{order.provenance.live_ai ? "Live provider assessment" : "Offline / mocked assessment"} · Terms entered by a human</p><p>Proposed price: <strong>{money(order.proposed_price, order.currency)}</strong> · Proposed deadline: {order.proposed_deadline || "Not proposed"}</p>{order.supporting_evidence.map((quote, index) => <EvidenceQuote key={index} quote={quote} text={text} />)}{order.uncertainties.length > 0 && <div className="notice notice-amber"><div><strong>Questions still to resolve</strong><ul>{order.uncertainties.map((question, index) => <li key={index}>{question}</li>)}</ul></div></div>}{order.responses.length ? order.responses.map((row) => <div key={row.id}><Badge value={row.decision} /><p>{row.note}</p><time>{dateTime(row.created_at)}</time></div>) : <div className="button-row"><Button onClick={() => setDecision("APPROVED")}>Approve proposed order</Button><Button variant="danger" onClick={() => setDecision("REJECTED")}>Reject proposed order</Button></div>}<Modal open={!!decision} onOpenChange={(open) => { if (!action.busy && !open) setDecision(null); }} title={decision === "APPROVED" ? "Approve proposed order" : "Reject proposed order"} description="Your response is recorded against these exact proposed terms. This does not create an invoice or execute a payment."><form onSubmit={(event) => { event.preventDefault(); void action.run(() => post(`/client/change-orders/${order.id}/respond`, { expected_order_revision: order.revision, decision, note, confirmed: true }), () => { setDecision(null); onRefresh(); }); }}><Problem error={action.error} onRefresh={onRefresh} /><Field label="Response note" htmlFor="client-note"><textarea id="client-note" required minLength={3} maxLength={1000} value={note} onChange={(event) => setNote(event.target.value)} /></Field><label className="checkbox-row"><input type="checkbox" required checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} /><span>I reviewed the proposed work, price, deadline and source evidence.</span></label><Button disabled={action.busy || !confirmed}>Confirm response</Button></form></Modal></article>;

}
