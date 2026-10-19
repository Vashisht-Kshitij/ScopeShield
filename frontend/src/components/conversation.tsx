"use client";
import { useRef, useState } from "react";
import { ArrowDown, ArrowUp, MessageSquareText, Plus, Trash2 } from "lucide-react";
import { api } from "@/lib/api";
import { useMutation } from "@/lib/hooks";
import { humanize } from "@/lib/format";
import type { ChangeRequest, Contract, ConversationMessage, ConversationMessageInput, MessageExcerpt, ReviewContext } from "@/lib/types";
import { Button, Field, Modal, Problem, ReviewFields } from "./ui";

type Draft = ConversationMessageInput & { editorKey: string };
const blank = (editorKey: string): Draft => ({ editorKey, sequence: 1, sender: "", role: "client", content: "", sent_at: null });

export function ConversationDialog({ contract, row, onSaved }: { contract: Contract; row?: ChangeRequest; onSaved: (saved: ChangeRequest) => void }) {
  const [open, setOpen] = useState(false);
  const [title, setTitle] = useState(row?.title || "");
  const [messages, setMessages] = useState<Draft[]>(() => row?.conversation_messages?.map((item) => ({ ...item, editorKey: item.id })) || [blank("new-0")]);
  const nextKey = useRef(messages.length);
  const [context, setContext] = useState<ReviewContext>({ reviewer_label: "", note: "" });
  const [validation, setValidation] = useState("");
  const action = useMutation();
  function update(index: number, value: Partial<Draft>) { setMessages((old) => old.map((item, position) => position === index ? { ...item, ...value } : item)); }
  function move(index: number, direction: number) {
    setMessages((old) => { const changed = [...old]; [changed[index], changed[index + direction]] = [changed[index + direction], changed[index]]; return changed; });
  }
  return <><Button variant="secondary" onClick={() => setOpen(true)}><MessageSquareText size={16} />{row ? "Edit conversation" : "Add conversation"}</Button>
    <Modal wide open={open} onOpenChange={(value) => { if (!action.busy) setOpen(value); }} title={row ? "Edit conversation sources" : "Review a client conversation"}
      description="Enter messages in the order you want reviewed. Exact source text is saved; sender labels and dates are supplied by you and are not authenticated.">
      <form onSubmit={(event) => {
        event.preventDefault();
        if (messages.some((item) => item.content.trim().length < 3 || item.content.length > 2000 || !item.sender.trim())) { setValidation("Each message needs a sender and 3–2,000 characters of source text."); return; }
        const sources = messages.map(({ editorKey: _key, ...item }, index) => ({ ...item, sequence: index + 1 }));
        setValidation("");
        void action.run(() => row ? api.editConversation(row, title, sources, context) : api.createConversation(contract, title, sources, context), (saved) => { setOpen(false); onSaved(saved); });
      }}>
        <Field label="Conversation title" htmlFor="conversation-title"><input id="conversation-title" required maxLength={200} value={title} onChange={(event) => setTitle(event.target.value)} placeholder="For example, launch-week client feedback" /></Field>
        <div className="notice notice-blue"><p>Manual sources · dates are optional · sequence controls order. Review up to 19 messages per conversation. No messages are sent to clients.</p></div>
        <div className="conversation-editor">{messages.map((item, index) => <fieldset className="conversation-draft" key={item.editorKey} disabled={action.busy}>
          <legend>Message {index + 1}</legend>
          <div className="conversation-fields">
            <Field label={`Sender ${index + 1}`} htmlFor={`message-sender-${index}`}><input id={`message-sender-${index}`} required maxLength={80} value={item.sender} onChange={(event) => update(index, { sender: event.target.value })} /></Field>
            <Field label={`Role ${index + 1}`} htmlFor={`message-role-${index}`}><select id={`message-role-${index}`} value={item.role} onChange={(event) => update(index, { role: event.target.value as Draft["role"] })}><option value="client">Client</option><option value="freelancer">Freelancer</option><option value="other">Other / unknown</option></select></Field>
            <Field label={`Supplied date ${index + 1} (optional)`} htmlFor={`message-date-${index}`}><input id={`message-date-${index}`} maxLength={40} value={item.sent_at || ""} onChange={(event) => update(index, { sent_at: event.target.value || null })} placeholder="YYYY-MM-DD or ISO timestamp" /></Field>
          </div>
          <Field label={`Message text ${index + 1}`} htmlFor={`message-text-${index}`}><textarea id={`message-text-${index}`} required minLength={3} maxLength={2000} rows={4} value={item.content} onChange={(event) => update(index, { content: event.target.value })} /></Field>
          <div className="button-row"><Button type="button" variant="ghost" aria-label={`Move message ${index + 1} earlier`} disabled={index === 0} onClick={() => move(index, -1)}><ArrowUp size={14} />Earlier</Button><Button type="button" variant="ghost" aria-label={`Move message ${index + 1} later`} disabled={index === messages.length - 1} onClick={() => move(index, 1)}><ArrowDown size={14} />Later</Button><Button type="button" variant="ghost" aria-label={`Remove message ${index + 1}`} disabled={messages.length === 1} onClick={() => setMessages((old) => old.filter((_, position) => position !== index))}><Trash2 size={14} />Remove</Button><span className="small muted">{item.content.length}/2,000 characters</span></div>
        </fieldset>)}</div>
        <Button type="button" variant="secondary" disabled={action.busy || messages.length >= 19} onClick={() => setMessages((old) => [...old, blank(`new-${nextKey.current++}`)])}><Plus size={15} />Add another message</Button>
        {row && <p className="small muted">Saving source edits or a new order resets the current assessment. Earlier sources and results remain in audit history. Existing message IDs are preserved.</p>}
        <ReviewFields value={context} onChange={setContext} prefix="conversation" />
        {validation && <p role="alert" className="validation-error">{validation}</p>}<Problem error={action.error} />
        <div className="dialog-actions"><Button type="button" variant="secondary" disabled={action.busy} onClick={() => setOpen(false)}>Back</Button><Button disabled={action.busy}>{action.busy ? "Saving…" : row ? "Save conversation and reset analysis" : "Save conversation"}</Button></div>
      </form>
    </Modal></>;
}

export function ConversationTimeline({ messages }: { messages: ConversationMessage[] }) {
  return <section className="conversation-timeline" aria-label="Saved conversation"><div className="panel-heading"><div><span className="eyebrow">CONVERSATION SOURCES</span><h3>{messages.length} messages · supplied order</h3></div><MessageSquareText size={20} /></div>
    <p className="small muted">Manually entered, unauthenticated sources. Sender labels and dates are not verified. Missing dates remain unspecified.</p>
    <ol>{messages.map((item) => <li id={`message-${item.id}`} key={item.id}><div className="conversation-message-meta"><strong>Message {item.sequence} · {item.sender}</strong><span>{humanize(item.role)} · {item.sent_at || "Date not supplied"}</span></div><pre className="conversation-text">{item.content}</pre><details><summary>Source identity</summary><code>{item.id}</code></details></li>)}</ol>
  </section>;
}

export function ConversationEvidence({ quotes, messages }: { quotes: MessageExcerpt[]; messages: ConversationMessage[] }) {
  return <div className="conversation-evidence">{quotes.map((quote, index) => {
    const message = messages.find((item) => item.id === quote.message_id);
    const points = message ? Array.from(message.content) : [];
    const verified = Number.isInteger(quote.source_start) && Number.isInteger(quote.source_end) && quote.source_start! >= 0 && quote.source_end! > quote.source_start! && quote.source_end! <= points.length && points.slice(quote.source_start!, quote.source_end!).join("") === quote.source_excerpt;
    if (!message || !verified) return <p key={index} className="small muted">Message quotation is unavailable for this source revision. Refresh before reviewing.</p>;
    return <figure key={index}><figcaption><a href={`#message-${message.id}`}>Message {message.sequence} · {message.sender}</a><span>Verified source text</span></figcaption><blockquote>{quote.source_excerpt}</blockquote></figure>;
  })}</div>;
}
