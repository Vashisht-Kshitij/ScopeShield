"use client";
import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";
import { AgStudio, AgStudioProvider, createWidgets, type AgRegistry, type AgWidgetDefinition } from "ag-studio-react";
import { AgStudioAiModule, createAiHarness, studioTheme, type AgAiHarnessSetup, type AgDataSourcesDefinition, type AgDefaultWidgetDefinition, type AgReportState, type AgStudioApi, type AgWidgetData, type AgWidgetDataFormat, type AgWidgetFieldReference, type AgWidgetParams } from "ag-studio";
import { studioAgent } from "@/lib/studio-agent";
import { STUDIO_PROMPTS, studioSnapshot, type StudioSnapshot } from "@/lib/studio-data";
import type { Intelligence } from "./project-intelligence";
import { Button } from "./ui";

type EvidenceData = AgWidgetData<{ records: AgWidgetFieldReference[] }, AgWidgetDataFormat<unknown>> & { type: "evidence-review" };
type EvidenceDef = AgWidgetDefinition<"evidence-review", EvidenceData>;
interface ScopeRegistry extends AgRegistry { widgets: readonly (AgDefaultWidgetDefinition | EvidenceDef)[] }
const fieldIds = ["id", "contract_id", "agreement", "kind", "title", "review", "classification", "origin", "amount", "local_state", "provider_status", "evidence_count", "count", "tab"];
const refs = (...ids: string[]) => ids.map((id) => ({ id: `records.${id}` }));
const title = (text: string) => ({ title: { enabled: true, text }, crossFilter: "filter" as const });

/** Uses Studio's filtered data API, not an independent unfiltered copy of the queue. */
export function EvidenceReview(params: AgWidgetParams<EvidenceData, unknown, StudioSnapshot>) {
  const [ids, setIds] = useState<string[]>([]); const [selected, setSelected] = useState(""); const [error, setError] = useState(false);
  useEffect(() => {
    const abort = new AbortController();
    const fields = params.dataMapping.records || [];
    params.widgetApi.setDisplayState("loading"); setError(false);
    params.widgetApi.getData({ fields }, { signal: abort.signal, maxRows: 500, maxBytes: 2 * 1024 * 1024 }).then(({ results }) => {
      if (abort.signal.aborted) return;
      const key = fields.find((f) => f.fieldId === "records.id")?.key;
      const next = key ? results.rows.map((r) => String(r[key])).filter((id) => !!params.context.details[id]) : [];
      setIds(next); setSelected((old) => next.includes(old) ? old : next[0] || "");
      params.widgetApi.setDisplayState(next.length ? "displayed" : "noData");
    }).catch(() => { if (!abort.signal.aborted) { setIds([]); setSelected(""); setError(true); params.widgetApi.setDisplayState("displayed"); } });
    return () => abort.abort();
  }, [params]);
  const rows = params.context.rows.filter((r) => ids.includes(r.id));
  const detail = params.context.details[selected]; const row = rows.find((r) => r.id === selected);
  return <div className="studio-evidence" aria-label="Filtered evidence review">
    {error ? <p role="status">Evidence view unavailable. Use the project overview to continue review.</p> : <>
      <p className="small muted" role="status">{rows.length} linked record(s) after Studio filters. Selecting a record here does not approve it.</p>
      <label>Inspect evidence for<select value={selected} onChange={(e) => setSelected(e.target.value)}>{rows.map((r) => <option key={r.id} value={r.id}>{r.kind} · {r.title}</option>)}</select></label>
      <div className="studio-evidence-actions"><Button variant="secondary" disabled={!selected} onClick={() => { const field = params.dataMapping.records?.find((f) => f.fieldId === "records.id"); if (field) params.widgetApi.toggleCrossFilter({ group: 0, type: "value", field, value: selected }); }}>Cross-filter selected record</Button><Button variant="secondary" onClick={() => params.widgetApi.resetCrossFilter()}>Clear evidence cross-filter</Button></div>
      {detail && row && <><div className="studio-evidence-heading"><strong>{row.review}</strong><span>{row.origin}</span></div><p>{detail.explanation}</p>
        {detail.evidence.length ? detail.evidence.map((e, index) => <figure key={index}><blockquote>{e.quote}</blockquote><figcaption>{e.kind} · {e.source} · extracted-text characters {e.start}–{e.end}{e.field ? ` · ${e.field}` : ""}</figcaption></figure>) : <p>No rechecked quotations attached to this record. No citation is inferred.</p>}
        <Link className="button button-secondary" href={detail.href}>Open saved record for human review</Link>
      </>}
    </>}
  </div>;
}

export function initialStudioState(compact = false): AgReportState<ScopeRegistry> {
  const positions = compact ? {
    review: { xTrack: 0, yTrack: 0, xSpan: 12, ySpan: 5 }, classes: { xTrack: 0, yTrack: 5, xSpan: 12, ySpan: 5 },
    records: { xTrack: 0, yTrack: 10, xSpan: 12, ySpan: 7 }, evidence: { xTrack: 0, yTrack: 17, xSpan: 12, ySpan: 10 },
  } : {
    review: { xTrack: 0, yTrack: 0, xSpan: 6, ySpan: 5 }, classes: { xTrack: 6, yTrack: 0, xSpan: 6, ySpan: 5 },
    records: { xTrack: 0, yTrack: 5, xSpan: 12, ySpan: 7 }, evidence: { xTrack: 0, yTrack: 12, xSpan: 12, ySpan: 10 },
  };
  return { selectedPageId: "scope", pages: [{ id: "scope", layout: { columns: 12, rowHeight: 52, minWidth: 600, pagePadding: 14, widgetPadding: 12, widgetBorderEnabled: true, widgetBorderRadius: 12, widgetBorderColor: "#dce5e9", widgetBackgroundColor: "#ffffff", backgroundColor: "#f3f7f8" }, widgets: {
    review: { type: "bar-chart-grouped", format: title("Human review · record count"), dataMapping: { categoryKey: refs("review"), valueKey: [{ id: "records.count", aggregation: "sum" }] } },
    classes: { type: "bar-chart-grouped", format: title("Scope classifications · request count"), dataMapping: { categoryKey: refs("classification"), valueKey: [{ id: "records.count", aggregation: "sum" }] } },
    records: { type: "grid", format: title("Milestones, requests & sandbox history"), dataMapping: { cols: refs("title", "kind", "review", "classification", "amount", "origin", "local_state", "provider_status", "agreement", "id") }, sort: [{ field: { id: "records.title" }, direction: "asc" }] },
    evidence: { type: "evidence-review", format: title("Evidence desk · inspect before negotiating"), dataMapping: { records: refs("id") } },
  }, widgetLayout: positions, filter: { widget: { classes: [{ field: { id: "records.kind" }, model: { operator: "equals", value: "Scope request" } }] } } }], panels: { filters: { collapsed: true }, edit: { collapsed: true }, data: { collapsed: true }, ai: { collapsed: true } } };
}

const theme = studioTheme.withParams({ accentColor: "#087f8c", backgroundColor: "#f3f7f8", foregroundColor: "#172c3d", fontFamily: "Arial, sans-serif", borderRadius: 8, fontSize: 13, studioFormLabelColor: "#344f60" });
const widgets = createWidgets<ScopeRegistry>({ additionalTypes: [{
  id: "evidence-review", label: "ScopeShield evidence desk", comp: EvidenceReview,
  form: (p) => p.createDefaults({ dataMappingItems: [] }),
  dataMapping: { records: { type: "fieldset", supportedRoles: ["category"], required: true } },
  defaultSize: { width: 800, height: 520 }, minSize: { width: 320, height: 240 },
}], menu: [{ label: "ScopeShield", widgetIds: ["evidence-review", "grid", "bar-chart-grouped", "value"] }] });

export default function StudioWorkspace({ data }: { data: Intelligence }) {
  const snapshot = useMemo(() => studioSnapshot(data), [data]);
  const api = useRef<AgStudioApi<ScopeRegistry> | null>(null); const container = useRef<HTMLDivElement>(null);
  const [compact, setCompact] = useState(false); const [mode, setMode] = useState<"view" | "edit">("view");
  const [painted, setPainted] = useState(false);
  const initialState = useMemo(() => initialStudioState(compact), [compact]);
  const panels = useMemo(() => ({ edit: { right: ["filters", "ai"] as ("filters" | "ai")[] }, view: { right: ["filters"] as "filters"[] } }), []);
  const [kind, setKind] = useState(""); const [agreement, setAgreement] = useState(""); const [failed, setFailed] = useState(false); const [ready, setReady] = useState(false);
  useEffect(() => { let previous = false; const observer = new ResizeObserver(([entry]) => { const next = entry.contentRect.width < 900; if (next !== previous) { previous = next; setCompact(next); setReady(false); setKind(""); setAgreement(""); } }); if (container.current) observer.observe(container.current); return () => observer.disconnect(); }, []);
  useEffect(() => { if (ready) return; const timer = setTimeout(() => setFailed(true), 30_000); return () => clearTimeout(timer); }, [ready, compact]);
  const engineData = useMemo<AgDataSourcesDefinition<ScopeRegistry>>(() => ({ sources: [{ id: "records", name: "Authorized project records", data: snapshot.rows.map((row) => ({ ...row })), fields: fieldIds.map((id) => ({ id, name: id.replaceAll("_", " "), format: ["count", "evidence_count"].includes(id) ? "integerFormat" : "textFormat", hide: ["contract_id", "tab"].includes(id), editable: false })) }], options: { fanout: { execute: "prevent" } }, description: "One record per milestone, scope request, or sandbox invoice. Only count is additive. Monetary display strings must never be summed or treated as real settlement." }), [snapshot]);
  const ai = useMemo<AgAiHarnessSetup>(() => ({ api: studioApi }) => createAiHarness(studioApi, () => ({ agents: [studioAgent(snapshot)], primary: "scopeshield-read-only", promptStarters: STUDIO_PROMPTS.map((p) => ({ label: p, prompt: p })) })), [snapshot]);
  function filter(nextKind: string, nextAgreement: string) {
    setKind(nextKind); setAgreement(nextAgreement);
    const current = api.current?.getState(); if (!current) return;
    api.current!.setState({ ...current, pages: current.pages.map((page) => ({ ...page, crossFilter: { values: {} }, filter: { ...page.filter, page: [ ...(nextKind ? [{ field: { id: "records.kind" }, model: { operator: "equals" as const, value: nextKind } }] : []), ...(nextAgreement ? [{ field: { id: "records.contract_id" }, model: { operator: "equals" as const, value: nextAgreement } }] : []) ] } })) });
  }
  function assistant() {
    setMode("edit"); const state = api.current?.getState();
    if (state) api.current!.setState({ ...state, panels: { ...state.panels, ai: { collapsed: false, width: 360 }, filters: { collapsed: true }, edit: { collapsed: true }, data: { collapsed: true } } });
  }
  return <div ref={container} className="studio-workspace">
    <div className="studio-toolbar"><div className="studio-filter-group" role="group" aria-label="Record type filter">{["", "Milestone", "Scope request", "Sandbox invoice"].map((value) => <Button key={value} variant={kind === value ? "primary" : "secondary"} aria-pressed={kind === value} disabled={!ready} onClick={() => filter(value, agreement)}>{value || "All records"}</Button>)}</div><label>Agreement<select aria-label="Studio agreement filter" value={agreement} onChange={(e) => filter(kind, e.target.value)} disabled={!ready}><option value="">All agreements</option>{data.contracts.map((c) => <option key={c.contract_id} value={c.contract_id}>{c.name}</option>)}</select></label><Button variant="ghost" disabled={!ready} onClick={() => filter("", "")}>Reset filters</Button><Button variant="secondary" disabled={!ready} onClick={assistant}>Open offline query assistant</Button><Button variant="ghost" disabled={!ready} onClick={() => { setMode(mode === "view" ? "edit" : "view"); }}>{mode === "view" ? "Arrange layout" : "Finish arranging"}</Button></div>
    <p className="small muted">Click a chart bar to cross-filter linked records and evidence. Sort grid columns with their headers. Arrange layout changes only this browser session. Record counts across types are not deliverable or payment totals.</p>
    <p className="studio-query-notice"><strong>OFFLINE RULE-BASED QUERIES — NOT LIVE AI</strong> · Assistant answers four questions over this project’s full snapshot. Sandbox and simulated invoice states never establish real payment.</p>
    {snapshot.unavailable ? <p role="status">Analytics fields are missing. Refresh the project or use the complete overview above.</p> : failed ? <p role="status">AG Studio could not initialize. The complete project overview above remains available.</p> : <div className="studio-scroll" tabIndex={0} role="region" aria-label="Scrollable analytics canvas"><div className="studio-canvas" data-studio-ready={ready ? "true" : "false"} data-studio-rendered={painted ? "true" : "false"}>
      <AgStudioProvider modules={[AgStudioAiModule]}><AgStudio<ScopeRegistry> key={compact ? "compact" : "wide"} data={engineData} initialState={initialState} context={snapshot} widgets={widgets} theme={theme} loadThemeGoogleFonts={false} mode={mode} ai={ai} panels={panels} enableFilterEditingInViewMode onApiReady={(event) => { api.current = event.api as AgStudioApi<ScopeRegistry>; }} onStudioReady={() => setReady(true)} onRenderStateChanged={(event) => setPainted(event.renderState === "rendered")} onErrorRaised={(event) => { if (event.fatal) setFailed(true); }} /></AgStudioProvider>
    </div></div>}
  </div>;
}
