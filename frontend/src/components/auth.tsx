"use client";
import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { LockKeyhole, ShieldCheck } from "lucide-react";
import { ApiError, post, request, setCsrfToken } from "@/lib/api";
import { Button, Field, Loading, Problem } from "./ui";

export interface SessionUser { id: string; username: string; display_name: string; role: "freelancer" | "client" }
interface SessionInfo { user: SessionUser; csrf_token: string; demo_mode?: boolean }
const AuthContext = createContext<{ user: SessionUser; logout: () => Promise<void>; demoMode?: boolean } | null>(null);
export const useAuth = () => useContext(AuthContext);
function notifySessionChanged() {
  if (typeof BroadcastChannel !== "undefined") {
    const channel = new BroadcastChannel("scopeshield-session");
    channel.postMessage("changed"); channel.close();
  }
}

export function AuthBoundary({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<SessionUser | null>(null); const [loading, setLoading] = useState(true);
  const [demoMode, setDemoMode] = useState(false);
  const [error, setError] = useState<Error | null>(null); const [version, setVersion] = useState(0);
  function signedIn(info: SessionInfo) { setCsrfToken(info.csrf_token); setDemoMode(!!info.demo_mode); setUser(info.user); setError(null); }
  useEffect(() => {
    if (typeof BroadcastChannel === "undefined") return;
    const channel = new BroadcastChannel("scopeshield-session");
    channel.onmessage = () => { setUser(null); setCsrfToken(""); setVersion((old) => old + 1); };
    return () => channel.close();
  }, []);
  useEffect(() => {
    let alive = true; setLoading(true);
    request<SessionInfo>("/auth/me").then((info) => { if (alive) signedIn(info); }).catch((failure) => {
      if (alive) { setUser(null); setCsrfToken(""); setError(failure instanceof ApiError && failure.status === 401 ? null : failure); }
    }).finally(() => { if (alive) setLoading(false); });
    const expired = () => { setUser(null); setCsrfToken(""); };
    window.addEventListener("scopeshield-session-expired", expired);
    return () => { alive = false; window.removeEventListener("scopeshield-session-expired", expired); };
  }, [version]);
  if (loading) return <main className="auth-screen"><Loading label="Checking your local session…" /></main>;
  if (!user) return <LoginScreen onLogin={(info) => { signedIn(info); notifySessionChanged(); }} connectionError={error} onRetry={() => setVersion(version + 1)} />;
  async function logout() {
    try { await post("/auth/logout", {}); setUser(null); setCsrfToken(""); notifySessionChanged(); }
    catch (failure) {
      if (failure instanceof ApiError && failure.status === 401) { setUser(null); setCsrfToken(""); notifySessionChanged(); }
      else setError(failure as Error);
    }
  }
  return <AuthContext.Provider value={{ user, logout, demoMode }}><Problem error={error} />{children}</AuthContext.Provider>;
}

export function LoginScreen({ onLogin, connectionError = null, onRetry }: { onLogin: (info: SessionInfo) => void; connectionError?: Error | null; onRetry?: () => void }) {
  const [username, setUsername] = useState(""); const [password, setPassword] = useState("");
  const [error, setError] = useState<Error | null>(null); const [busy, setBusy] = useState(false);
  return <main className="auth-screen"><section className="auth-card" aria-labelledby="login-heading"><div className="auth-brand"><ShieldCheck size={28} />ScopeShield<span>.</span></div><span className="eyebrow">TRACEABLE CONTRACT DECISIONS</span><h1 id="login-heading">Welcome back</h1><p className="muted">Turn contracts into reviewed milestones, compare scope changes and prepare PayPal sandbox invoices.</p><Problem error={connectionError} onRefresh={onRetry} /><Problem error={error} /><form onSubmit={async (event) => {
    event.preventDefault(); setBusy(true); setError(null);
    try {
      const preflight = await request<{ csrf_token: string; demo_mode?: boolean }>("/auth/csrf");
      const info = await request<SessionInfo>("/auth/login", { method: "POST", headers: { "X-CSRF-Token": preflight.csrf_token }, body: JSON.stringify({ username, password }) });
      setPassword(""); onLogin(info);
    } catch (failure) { setError(failure as Error); } finally { setBusy(false); }
  }}><Field label="Username" htmlFor="login-username"><input id="login-username" autoComplete="username" autoCapitalize="none" spellCheck={false} required minLength={3} maxLength={100} value={username} onChange={(event) => setUsername(event.target.value)} /></Field><Field label="Password" htmlFor="login-password"><input id="login-password" type="password" autoComplete="current-password" required maxLength={128} value={password} onChange={(event) => setPassword(event.target.value)} /></Field><Button disabled={busy || !!connectionError}><LockKeyhole size={16} />{busy ? "Signing in…" : "Sign in"}</Button></form><div className="auth-footnote"><LockKeyhole size={15} /><p>Judges: use your individually supplied account, then open Demo scenarios → Full product walkthrough. Ask the event operator for access or a password reset. There is no public signup. Sessions expire after eight hours. Use synthetic documents only in the public demo.</p></div></section></main>;
}
