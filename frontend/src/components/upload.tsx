"use client";
import { useState } from "react";
import { useRouter } from "next/navigation";
import { FileUp, FlaskConical, LoaderCircle, UploadCloud } from "lucide-react";
import { api } from "@/lib/api";
import { useMutation, useResource } from "@/lib/hooks";
import type { Project } from "@/lib/types";
import { Button, Field, Modal, Problem } from "./ui";

export function UploadDialog({ label = "Upload agreement", onUploaded, disabled = false }: { label?: string; onUploaded?: () => void; disabled?: boolean }) {
  const [open, setOpen] = useState(false); const [file, setFile] = useState<File | null>(null); const [project, setProject] = useState(""); const [validation, setValidation] = useState("");
  const [projectOffset, setProjectOffset] = useState(0);
  const action = useMutation(); const router = useRouter(); const projects = useResource<Project[]>(open ? `/projects?limit=100${projectOffset ? `&offset=${projectOffset}` : ""}` : null);
  function choose(value: File | null) {
    setValidation(""); setFile(value);
    if (value && (!/\.(txt|pdf)$/i.test(value.name) || value.size > 5 * 1024 * 1024 || value.size === 0)) {
      setValidation("Choose a non-empty PDF or UTF-8 text file up to 5 MB."); setFile(null);
    }
  }
  async function upload(selected: File) {
    await action.run(() => api.upload(selected, project || undefined), (contract) => {
      setOpen(false); onUploaded?.(); router.push(`/contracts/${contract.id}`);
    });
  }
  return <><Button disabled={disabled} title={disabled ? "Wait for the project evidence check to finish" : undefined} onClick={() => setOpen(true)}><FileUp size={16} />{label}</Button><Modal open={open} onOpenChange={(value) => { if (!action.busy) setOpen(value); }} title="Add an agreement" description="Upload a contract to review its deliverables and compare future requests."><form onSubmit={(event) => { event.preventDefault(); if (file) void upload(file); }}><div className="upload-zone" onDragOver={(event) => event.preventDefault()} onDrop={(event) => { event.preventDefault(); choose(event.dataTransfer.files[0] || null); }}><UploadCloud size={29} /><strong>{file ? file.name : "Choose a file or drop it here"}</strong><span>PDF or UTF-8 text · Up to 5 MB</span><input aria-label="Agreement file" type="file" accept=".txt,.pdf,text/plain,application/pdf" onChange={(event) => choose(event.target.files?.[0] || null)} /></div>{validation && <p role="alert" className="validation-error">{validation}</p>}<Field label="Project" htmlFor="upload-project" hint="Choose an existing project to upload a new agreement version."><select id="upload-project" value={project} onChange={(event) => setProject(event.target.value)}><option value="">Create a new project</option>{projects.data?.map((row) => <option value={row.id} key={row.id}>{row.title || `Untitled project · ${row.id.slice(0, 8)}`}</option>)}</select></Field><div className="button-row"><Button type="button" variant="secondary" disabled={!projectOffset || projects.loading} onClick={() => { setProject(""); setProjectOffset(Math.max(0, projectOffset - 100)); }}>Previous projects</Button><span>Project page {projectOffset / 100 + 1}</span><Button type="button" variant="secondary" disabled={projects.loading || (projects.data?.length || 0) < 100} onClick={() => { setProject(""); setProjectOffset(projectOffset + 100); }}>Next projects</Button></div><Problem error={projects.error} onRefresh={projects.reload} /><Problem error={action.error} /><div className="notice notice-blue"><FileUp size={17} /><p>Uploads support 100,000 text characters. Configured AI supports at most 20,000 characters and a 32,000-byte complete prompt; eligibility is shown after upload. Extraction is separate and requires human review.</p></div><div className="dialog-actions"><Button variant="secondary" type="button" onClick={() => setOpen(false)} disabled={action.busy}>Cancel</Button><Button disabled={!file || action.busy}>{action.busy ? <LoaderCircle size={16} className="spin" /> : <FileUp size={16} />}{action.busy ? "Uploading…" : "Upload agreement"}</Button></div></form><div className="sample-section"><span>Exploring ScopeShield?</span><p>Start with a clearly labelled synthetic agreement. It is saved through the real upload API.</p><div className="button-row">{[{ name: "Milestone review sample", file: "day4_freelance_agreement.txt" }, { name: "Scope change sample", file: "day6_freelance_agreement.txt" }].map((sample) => <Button key={sample.file} type="button" variant="secondary" disabled={action.busy} onClick={async () => {
      await action.run(async () => { const response = await fetch(`/samples/${sample.file}`); if (!response.ok) throw new Error("The bundled sample could not be loaded."); return api.upload(new File([await response.text()], sample.file, { type: "text/plain" }), project || undefined); }, (contract) => { setOpen(false); router.push(`/contracts/${contract.id}`); onUploaded?.(); });
    }}><FlaskConical size={14} />{sample.name}</Button>)}</div></div></Modal></>;
}
