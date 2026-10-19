"use client";
import { Component, lazy, Suspense, useEffect, useState, type ReactNode } from "react";
import type { Intelligence } from "./project-intelligence";
import { localStudioAllowed } from "@/lib/studio-data";
import { Button } from "./ui";

// Compile-time branch keeps the commercial runtime out of strict production builds.
const Studio = lazy(() => process.env.NEXT_PUBLIC_SCOPESHIELD_FRONTEND_PROFILE !== "production"
  && process.env.NEXT_PUBLIC_SCOPESHIELD_STUDIO === "local-evaluation"
  ? import("./studio-workspace") : import("./studio-disabled"));
export class StudioBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  render() { return this.state.failed ? <p role="status">AG Studio is unavailable. The complete project overview above remains available.</p> : this.props.children; }
}
export function StudioGate({ data }: { data: Intelligence }) {
  const [allowed, setAllowed] = useState(false); const [open, setOpen] = useState(false);
  useEffect(() => setAllowed(localStudioAllowed(process.env.NEXT_PUBLIC_SCOPESHIELD_STUDIO, window.location.hostname, process.env.NEXT_PUBLIC_SCOPESHIELD_FRONTEND_PROFILE)), []);
  if (!allowed) return null;
  return <section className="studio-gate" aria-label="AG Studio local evaluation">
    <div className="studio-heading"><div><span className="eyebrow">EXPLORE YOUR PROJECT</span><h3>Scope intelligence studio</h3><p>Linked analytics, grounded evidence and a read-only query assistant.</p></div><Button variant="secondary" onClick={() => setOpen(!open)}>{open ? "Close analytics studio" : "Open analytics studio"}</Button></div>
    <p className="small muted">Local AG Studio evaluation · no trial activated · public demo disabled pending licence approval. No approvals or provider requests.</p>
    {open && <StudioBoundary><Suspense fallback={<p role="status">Loading AG Studio…</p>}><Studio data={data} /></Suspense></StudioBoundary>}
  </section>;
}
