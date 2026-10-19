"use client";
import Link from "next/link";
import { useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { FlaskConical, ArrowRight } from "lucide-react";
import { api } from "@/lib/api";
import { useMutation, useResource } from "@/lib/hooks";
import type { Contract, DemoScenario } from "@/lib/types";
import { useAuth } from "./auth";
import { Badge, Button, Loading, Problem } from "./ui";

export function DemoGallery() {
  const result = useResource<DemoScenario[]>("/demo-scenarios"); const action = useMutation();
  const router = useRouter(); const auth = useAuth(); const loading = useRef(false);
  const [partial, setPartial] = useState<Contract | null>(null);
  async function load(scenario: DemoScenario) {
    if (loading.current || partial) return;
    loading.current = true;
    try {
      await action.run(async () => {
        let contract = await api.upload(new File([scenario.text], scenario.filename, { type: "text/plain" }));
        setPartial(contract);
        if (scenario.supports_extraction) contract = await api.extract(contract, "offline_demo");
        if (scenario.conversation_messages) await api.createConversation(contract, scenario.title, scenario.conversation_messages, { reviewer_label: auth?.user.display_name || "Demo operator", note: "Loaded the matching synthetic conversation; sender/date claims are unverified" });
        if (scenario.requested_changes.length) await api.createRequest(contract, scenario.title, scenario.requested_changes, {
          reviewer_label: auth?.user.display_name || "Demo operator", note: "Loaded the matching predefined offline demonstration request",
        });
        return contract;
      }, (contract) => router.push(`/contracts/${contract.id}?tab=${scenario.supports_extraction ? "milestones" : "scope"}`));
    } finally { loading.current = false; }
  }
  return <><div className="page-heading"><div><span className="eyebrow">PREDICTABLE EXAMPLES · HUMAN REVIEW</span><h1>Offline demo scenarios</h1><p>Choose a matching agreement and request. Every assessment is a predefined mocked fixture.</p></div><Badge value="OFFLINE_DEMO">Offline / mocked</Badge></div>
    <div className="notice notice-blue"><FlaskConical size={18} /><p>Loading saves a new synthetic agreement through the upload API, extracts fixture milestones when supported, and prepares its compatible request. Analysis and human approvals remain separate. No invoice is created or sent.</p></div>
    <Problem error={result.error || action.error} onRefresh={result.reload} />
    {partial && action.error && <div className="notice notice-amber"><div><strong>The sample agreement is already saved.</strong><p>A later setup step did not finish. Open this record to continue; loading another copy is disabled.</p><Link className="button button-secondary" href={`/contracts/${partial.id}`}>Open saved sample</Link></div></div>}
    {result.loading ? <Loading label="Loading the backend's supported examples…" /> : <div className="demo-grid">{result.data?.map((scenario) => <article className="panel demo-card" key={scenario.id}><div className="panel-body"><span className="eyebrow">SYNTHETIC · PREDEFINED</span><h2>{scenario.title}</h2><p>{scenario.description}</p><p className="small muted">{scenario.filename} · {scenario.supports_extraction ? "Milestone extraction supported" : "Scope comparison without milestone extraction"}</p>{scenario.conversation_messages && <><h3>Chronological messages</h3><ol>{scenario.conversation_messages.map((item) => <li key={item.sequence}>{item.content}</li>)}</ol><p className="small muted">Manual synthetic sources · individual and cumulative assessment · human review required</p></>}{scenario.requested_changes.length > 0 && <><h3>Compatible requested work</h3><ul>{scenario.requested_changes.map((value) => <li key={value}>{value}</li>)}</ul></>}<details><summary>Preview the original synthetic agreement</summary><pre className="shared-source">{scenario.text}</pre></details><Button disabled={action.busy || !!partial} onClick={() => void load(scenario)}>Load {scenario.title}<ArrowRight size={15} /></Button></div></article>)}</div>}
    <p className="small muted">Arbitrary or edited agreements and requests are not valid offline examples. Use a separately configured and authorized AI provider for general analysis. Your existing agreements are preserved.</p>
  </>;
}
