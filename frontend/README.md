# ScopeShield frontend

The current clean-checkout instructions are in ../INSTALL_GUIDE.md. Use Node
24.15+ in the 24.x line, pnpm 11.25.0 and the pinned lockfile. The supported local
production builder is `pnpm exec next build --webpack`.

The UI is Next.js 16 / React 19 / TypeScript. Cookie sessions, owner authorization
and human approvals are enforced by FastAPI. There is no standalone mocked public
backend, default password or public signup. API/UI hostnames must match for the
SameSite cookie. Local defaults use 127.0.0.1:3000 and 127.0.0.1:8000.

Local core build:

```powershell
pnpm install --frozen-lockfile
$env:SCOPESHIELD_FRONTEND_PROFILE='local'
$env:NEXT_PUBLIC_API_BASE_URL='http://127.0.0.1:8000'
$env:NEXT_PUBLIC_FRONTEND_URL='http://127.0.0.1:3000'
$env:NEXT_PUBLIC_SCOPESHIELD_STUDIO=''
$env:NEXT_TELEMETRY_DISABLED='1'
pnpm exec next build --webpack
pnpm start
```

These shell values take precedence over existing local public settings; preserve
existing .env.local files and never place secrets in NEXT_PUBLIC variables.
`pnpm dev` is available for development on the same port; do not run it with
`pnpm start` simultaneously. Rebuild after changing public build settings.

For the separately prepared future public deployment, build with production,
API base `same-origin` and an exact HTTPS frontend origin. Never point a browser
at a public port 8000/8001; Caddy proxies the private API. No deployment is
currently authorized.

Original PDF binaries are retained for accepted new uploads. New page metadata
is bound to the document hash; older/unverified maps use extracted-text citations
without page claims. Unicode offsets count code points. Invalid supplied ranges
are not silently repaired, and repeated quotes require a valid occurrence.

The public core dashboard, review queue, evidence viewer and report downloads
remain functional without Studio. With the flag blank, its proprietary runtime
is excluded at build time. Local evaluation requires an explicit
`NEXT_PUBLIC_SCOPESHIELD_STUDIO=local-evaluation` rebuild and loopback host.
Strict production always excludes Studio. No licence or trial has been activated.
See ../AG_STUDIO_RELEASE_STRATEGY.md and THIRD_PARTY_NOTICES.md.

Studio's documented custom runner has four offline fixed questions and zero
financial/mutation tools. It does not call an LLM. Simulated PAID, historical
sandbox status, local CREATED and actual payment remain different concepts.
Historical Gemini and real sandbox checks were separate runs; new cumulative
conversation accuracy has not been verified by a fresh live-provider evaluation.

Checks: `pnpm exec vitest run --maxWorkers=1`, `pnpm typecheck`, explicit Webpack
builds, then the root's isolated Chrome/HTTPS runners. Never use their live flags
without separate authorization. Accessibility tests cover the exercised views,
not every assistive technology. Current release checks and limitations are in
../DAY24_RELEASE_READINESS.md. The Day 24 Cedar Chrome test uses the existing UI;
no Studio licence or original-workstation tool cache is required.
