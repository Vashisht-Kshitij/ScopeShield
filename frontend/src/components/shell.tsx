"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { Activity, ArrowUpRight, BookOpen, CircleHelp, FileCheck2, FolderClosed, LayoutDashboard, PanelLeftClose, ShieldCheck } from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { apiBase } from "@/lib/api";
import { useResource } from "@/lib/hooks";
import { useAuth } from "./auth";
import { ClientPortal } from "./sharing";

const navigation = [
  { href: "/", label: "Overview", icon: LayoutDashboard },
  { href: "/contracts", label: "Contracts", icon: FolderClosed },
  { href: "/scope-changes", label: "Scope changes", icon: FileCheck2 },
  { href: "/activity", label: "Activity", icon: Activity },
  { href: "/demos", label: "Demo scenarios", icon: BookOpen },
];
export function AppShell({ children }: { children: ReactNode }) {
  const auth = useAuth();
  const user = auth?.user;
  const items = user?.role === "client" ? [{ href: "/client", label: "Shared projects", icon: FolderClosed }] : navigation;
  const path = usePathname(); const [menu, setMenu] = useState(false); const health = useResource<{ status: string }>("/health");
  let docsUrl: string | undefined; try { docsUrl = apiBase() + "/docs"; } catch { /* Configuration errors are displayed by the API client. */ }
  const sidebarRef = useRef<HTMLElement>(null); const menuRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (!menu) { if (sidebarRef.current?.contains(document.activeElement)) menuRef.current?.focus(); return; }
    sidebarRef.current?.querySelector<HTMLAnchorElement>("a")?.focus();
    const escape = (event: KeyboardEvent) => { if (event.key === "Escape") setMenu(false); };
    document.addEventListener("keydown", escape); return () => document.removeEventListener("keydown", escape);
  }, [menu]);
  const section = user?.role === "client" ? "Shared projects" : items.find((item) => item.href === "/" ? path === "/" : path.startsWith(item.href))?.label || "Workspace";
  return <div className="app-shell"><a className="skip-link" href="#main-content">Skip to main content</a><aside id="primary-sidebar" ref={sidebarRef} className={`sidebar ${menu ? "sidebar-open" : ""}`}><Link className="brand" href="/" onClick={() => setMenu(false)}><span className="brand-mark"><ShieldCheck size={24} strokeWidth={1.7} /></span><span>ScopeShield<span className="brand-period">.</span></span></Link><div className="workspace-switch"><span className="workspace-avatar">S</span><div><strong>My workspace</strong><span>{auth?.demoMode ? "Synthetic judge workspace" : "Local development"}</span></div><span className="workspace-tag">{auth?.demoMode ? "DEMO" : "LOCAL"}</span></div><span className="nav-label">WORKSPACE</span><nav aria-label="Main navigation">{items.map(({ href, label, icon: Icon }) => {
    const selected = user?.role === "client" || (href === "/" ? path === "/" : path.startsWith(href));
    return <Link key={href} href={href} aria-current={selected ? "page" : undefined} className={`nav-link ${selected ? "active" : ""}`} onClick={() => setMenu(false)}><Icon size={18} strokeWidth={1.7} />{label}{selected && <span className="nav-active-dot" />}</Link>;
  })}</nav><div className="sidebar-bottom"><div className="sidebar-note"><ShieldCheck size={18} /><strong>Every decision, grounded.</strong><p>Review the evidence.<br />Keep control of your scope.</p></div>{!auth?.demoMode && <a className="nav-link subtle" href={docsUrl} target="_blank" rel="noreferrer"><BookOpen size={16} />API documentation<ArrowUpRight size={13} /></a>}<div className="operator"><span className="operator-avatar">LO</span><div><strong>{user?.display_name || "Local operator"}</strong><span>{user?.role || "Local workspace"}</span></div><CircleHelp size={16} /></div></div></aside>{menu && <button className="nav-scrim" aria-label="Close navigation" onClick={() => setMenu(false)} />}<div className="main-shell"><header className="topbar"><div className="breadcrumbs"><button ref={menuRef} className="mobile-menu" aria-expanded={menu} aria-controls="primary-sidebar" aria-label="Toggle navigation" onClick={() => setMenu(!menu)}><PanelLeftClose size={20} /></button><span>Workspace</span><span className="breadcrumb-separator">/</span><strong>{section}</strong></div><div className="topbar-right"><span className={`connection ${health.data ? "online" : ""}`}><i />{health.loading ? "Connecting" : health.data ? "Backend connected" : "Backend unavailable"}</span><span className="topbar-divider" /><span className="environment-pill">{auth?.demoMode ? "Offline demo" : "Sandbox"}</span><span className="top-avatar" aria-hidden="true">{user?.display_name.slice(0, 2).toUpperCase() || "LO"}</span>{auth && <button className="text-button" onClick={() => void auth.logout()}>Sign out</button>}</div></header><main id="main-content" className="main-content">{auth?.demoMode && <div className="notice notice-amber" role="status"><p>Judge demo · Synthetic examples and offline fixtures. Live AI and provider invoice actions are disabled. Begin with Demo scenarios → Full product walkthrough.</p></div>}{user?.role === "client" ? <ClientPortal /> : children}</main><footer className="app-footer"><span>ScopeShield <span className="footer-dot">·</span> Contract intelligence, with human control.</span><span>{auth?.demoMode ? "Judge demo" : "Local workspace"} <span className="footer-dot">·</span> API 0.9</span></footer></div></div>;
}
