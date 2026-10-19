# Install and rehearse ScopeShield locally

This guide uses synthetic data, fixed offline AI answers and an in-memory PayPal
adapter. It needs no provider account, commercial Studio licence, cloud resource
or paid service. No invoice from this walkthrough exists at PayPal.

## Requirements

On Windows, use a short checkout path such as C:\src\ScopeShield. Deeply nested
folders can exceed Windows/tool path limits during dependency installation or
Webpack output. The checkout must also permit tools to write generated files.

- Python 3.11 or 3.12, with venv/pip available.
- Node 24.15 or later in the 24.x line; pnpm **11.25.0** on PATH.
- Git; a modern browser. Automated QA additionally requires Google Chrome.
- Disk space for dependencies, a Next.js build and disposable synthetic databases.
  A future 4 GiB host is a candidate, not a locally measured production guarantee.

Install the pinned pnpm version through your Node version manager or
`npm install --global pnpm@11.25.0` if your development environment permits it.
Check `python --version` (or `python3 --version`), `node --version` and
`pnpm --version`. No Codex installation, original workstation path, Azure CLI,
private key or APIMatic runtime service is needed.

The approved public repository URL is **pending**. After publication is separately
authorized, clone that exact URL into a new directory; until then use the prepared
local source ZIP or a local clone. Do not copy an existing .env, .local, database,
node_modules or virtual environment into the new checkout.

## 1. Install Python dependencies

From the new repository root, Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --only-binary=:all: -r requirements-day5.lock.txt
.\.venv\Scripts\python.exe -m pip check
```

Linux/macOS shell:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --only-binary=:all: -r requirements-day5.lock.txt
.venv/bin/python -m pip check
```

The requirements are version pinned, not a platform-complete locally hash-locked
wheel set. Use the official PyPI index and preserve download integrity checks.
If a download hash differs, stop. Independently verify/re-download the exact
artifact against official PyPI metadata; never disable verification or change the
expected hash to match unexplained bytes. Day 24 encountered and safely recovered
from one such download failure; see the readiness report.

The Day 24 test used fresh Windows environments. POSIX commands are provided, but
Linux/macOS execution was not established by that Windows test. A07 remains open.

## 2. Create an individual, empty synthetic workspace

PowerShell:

```powershell
.\.venv\Scripts\python.exe -B -m scripts.day24_demo create --username demo.local
```

POSIX:

```bash
.venv/bin/python -B -m scripts.day24_demo create --username demo.local
```

Choose an individual 12–128 character password at the hidden prompts. No default
password is supplied or printed. The command creates a **new UUID-named directory**
under .local/day24, applies migrations, creates one freelancer account and prints
the workspace path. It creates zero projects, approvals, invoices or AI usage.
It cannot select or reset an existing database. Save the printed workspace path.

For an optional **already completed, scripted simulation**, add `--complete` to a
new create command. That explicitly scripts review annotations, a pending USD 300
proposal, one USD 500 MOCK draft and two synthetic PDFs. These are rehearsal
decisions, not actual client acceptance, model inference or payment. The default
empty workspace is the one to use when recording the manual workflow.

Each reviewer can provision their own username/workspace. There is no signup,
shared privileged password or public judge account in these instructions.

## 3. Start the guarded backend

Replace WORKSPACE_PRINTED_BY_CREATE with the exact new directory, preserving quotes:

```powershell
.\.venv\Scripts\python.exe -B -m scripts.day24_demo serve --workspace "WORKSPACE_PRINTED_BY_CREATE"
```

```bash
.venv/bin/python -B -m scripts.day24_demo serve --workspace "WORKSPACE_PRINTED_BY_CREATE"
```

Keep that terminal open. This entry point binds 127.0.0.1:8000, runs one worker,
forces disabled live AI, blocks external provider HTTP and substitutes only the
existing PayPal mock. It refuses unmarked workspaces and non-mock invoice history.
It does not modify the normal application entry point or deployment profile.
Mock DRAFT/SENT records rehydrate after restart; no PAID state is synthesized.
Do not expose this development entry point through a public tunnel or host.

## 4. Install and build the core frontend

In a second terminal, from frontend/:

```powershell
pnpm install --frozen-lockfile --ignore-scripts
$env:SCOPESHIELD_FRONTEND_PROFILE='local'
$env:NEXT_PUBLIC_API_BASE_URL='http://127.0.0.1:8000'
$env:NEXT_PUBLIC_FRONTEND_URL='http://127.0.0.1:3000'
$env:NEXT_PUBLIC_SCOPESHIELD_STUDIO=''
$env:NEXT_TELEMETRY_DISABLED='1'
pnpm exec next build --webpack
pnpm start
```

```bash
pnpm install --frozen-lockfile --ignore-scripts
export SCOPESHIELD_FRONTEND_PROFILE=local
export NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8000
export NEXT_PUBLIC_FRONTEND_URL=http://127.0.0.1:3000
export NEXT_PUBLIC_SCOPESHIELD_STUDIO=
export NEXT_TELEMETRY_DISABLED=1
pnpm exec next build --webpack
pnpm start
```

Visit **http://127.0.0.1:3000** and sign in. Use 127.0.0.1 for both API and UI.
The frontend start script also binds loopback. Do not run dev/start concurrently.
Rebuild when public build variables change. The blank Studio flag excludes its
runtime; installation/typechecking still resolves separately licensed commercial
dependencies. Their rights are not granted by ScopeShield's MIT licence.

## 5. Follow the Cedar walkthrough

[DEMO_STORYBOARD.md](DEMO_STORYBOARD.md) gives the exact UI actions.
Upload examples/day24_cedar_agreement.txt and explicitly choose offline extraction.
For manual conversation entry, copy the five messages exactly from
app/day24_fixture.py or the storyboard, with sender `Alex (synthetic client)`, role
client and blank dates. Fixed fixtures refuse changed contracts/messages rather
than inventing answers. The catalog's Cedar scenario is an alternative one-click
setup; it creates a new agreement/request, so do not also load it into the same
one-project recording workspace after manual upload.

Human review remains real application interaction. The USD 300 proposal is an
explicit fictional human-entered example, not an AI estimate or agreed amount.
Only one approved USD 500 milestone is used for the mocked invoice. No email,
provider invoice or payment is created, and the change order remains unapproved.
Evidence Pack PDFs contain full JSON attachments; even the executive brief is
not a sanitized public summary. Keep all real customer reports private.

## Migrations and ordinary development

The create command already applies Alembic head **0009_conversations**. To inspect
or repeat migrations, stop the backend, explicitly set SCOPESHIELD_DATABASE_URL
to the new workspace's demo.db, then run the commands below with your venv Python:

```text
python -m alembic current
python -m alembic upgrade head
python -m alembic check
```

Use an absolute SQLite URL (`sqlite:////absolute/path/demo.db` on POSIX,
`sqlite:///C:/absolute/path/demo.db` on Windows). Never point these commands at
an existing customer/workstation database during setup. No downgrade is needed.

.env.offline.example contains credential-free optional development settings. The
guarded CLI supplies its own database and safe provider settings without requiring
an .env file. Never overwrite an existing environment file. To create ordinary
individual accounts outside the rehearsal, the operator CLI is
`python -m scripts.manage_users create --username NAME --role freelancer`, with
the backend stopped and an explicitly chosen database. Role changes/signup are
not public endpoints.

## Optional real providers — separate authorization required

Do not enable these for this rehearsal. Stop the guarded demo first and use a
separate, explicitly chosen sandbox workspace and normal `app.main:app` entry point
only after the operator approves the exact requests and financial amounts.

| Capability | Local configuration to prepare; disabled until approved |
| --- | --- |
| Gemini | GEMINI_API_KEY; SCOPESHIELD_AI_PROVIDER=gemini; metadata-verified SCOPESHIELD_AI_MODELS and project-confirmed SCOPESHIELD_AI_FREE_TIER_MODELS. Keep SCOPESHIELD_AI_ALLOW_LIVE=false until separate quota/billing/request approval. Durable request/token/concurrency limits still apply. |
| PayPal sandbox | PAYPAL_CLIENT_ID, PAYPAL_CLIENT_SECRET and PAYPAL_SANDBOX_BUYER_EMAIL from sandbox accounts only. Never place them in NEXT_PUBLIC variables or Git. The adapter is sandbox only; there is no production-payment switch. Preview, create and send require distinct authorized human actions. |

Do not infer API credits from a chat subscription, student hosting credits or a
model name. Do not run preserved live smoke/diagnostic scripts as installation
checks. Historical live verification is summarized in the submission draft;
it does not authorize another request or establish Cedar live-model accuracy.

## Security, limits and troubleshooting

| Symptom | Safe action |
| --- | --- |
| Login/CSRF/403 | Use the same hostname and documented ports; sign in again after logout/account switches. Do not disable CSRF, cookies or owner checks. |
| Port already in use | Stop your previous local server. Do not terminate unrelated processes or add workers. |
| Offline mismatch/422 | Use the exact Cedar fixture and five messages. Arbitrary-input inference is unavailable offline. |
| Revision/409 | Reload current records and review again. Never bypass revisions or use new keys to duplicate an invoice. |
| Budget/429 | Wait for the documented window or request operator review. Do not raise budgets to force inference. |
| Storage/507 | Preserve data and backups; ask the operator to review capacity. Never delete evidence to make a demo pass. |
| Build/tool missing | Install the documented runtime versions. QA locates pnpm on PATH; SCOPESHIELD_PNPM can name an explicit executable. No original Codex cache is required. |
| HTTPS QA tool missing | Install OpenSSL separately or set SCOPESHIELD_OPENSSL. This creates only disposable loopback certificates, not public HTTPS. |
| Studio absent | Expected for the core build. Do not activate a trial or enable a public evaluation build. |

Uploads: 5 MiB, 50 PDF pages, 100,000 extracted characters. Conversations: at most
19 ordered messages plus one cumulative finding. Original PDFs: 64 MiB/account,
512 MiB/application; SQLite: 1 GiB total. Reports/analytics are complete bounded
snapshots: 10 agreements, 30 requests, 100 milestones, 500 rows per audit category,
2 MiB source data. These ceilings are not pagination or unlimited analytics.
Live input budgets may be lower. Backups stream encrypted data in 4 MiB chunks;
the 1 GiB + 1 MiB envelope does not prove real cloud recovery.

## Offline verification

From the root with the venv Python: `python -B -m pytest tests -q`.
From frontend/: `pnpm exec vitest run --maxWorkers=1` and `pnpm typecheck`.
For Chrome, stop the demo servers and build the blank-flag local core with API
http://127.0.0.1:8001, then run from the root:

```text
python -B -m scripts.run_browser_tests --day24 --day10 --day18
```

Use `--day24-only` additionally for a focused Cedar rehearsal. The runner creates
new disposable accounts/databases; no passwords are arguments or screenshots.
It rejects combining Day 24 with live-provider flags. Build settings for strict
HTTPS QA are in the readiness report; local TLS checks do not close A07.

Stop servers with Ctrl+C. Keep the printed workspace for restart; create another
new workspace for another take. No reset/delete command is provided.
