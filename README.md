# ScopeShield

**Agree on change. Approve each step.**

ScopeShield helps freelancers compare a contract with an evolving client
conversation, inspect grounded evidence, and prepare a change order for human
review. Approved milestones can enter a separate PayPal sandbox invoice workflow.
It does not authenticate imported messages, decide legal obligations, price work
with AI, or create/send invoices automatically.

## Run the offline demo

See [publication notes](PUBLICATION_NOTES.md) for this corrected release candidate.
Follow [INSTALL_GUIDE.md](INSTALL_GUIDE.md) in a new checkout. It covers Windows
and POSIX shells, Python 3.11/3.12, Node 24.15+ in the 24.x line and pnpm 11.25.0.
The core application needs no commercial Studio licence or provider credentials.

The main Cedar walkthrough uses one synthetic USD 1,500 agreement, three USD 500
milestones and five ordered messages. An individual account and new isolated
workspace are created through hidden password prompts. The guarded demo binds
loopback, disables live AI and replaces PayPal with an explicit in-memory mock.
No invoice from that run exists at PayPal. Do not run live smoke scripts as setup.

- [Three-minute narration](DEMO_SCRIPT.md)
- [Exact actions, messages and expected results](DEMO_STORYBOARD.md)
- [Submission draft](DEVPOST_SUBMISSION_DRAFT.md)
- [Release readiness and actual verification](DAY24_RELEASE_READINESS.md)
- [Publication approval checklist](RELEASE_CHECKLIST.md)

Loading a fixed offline example is not inference for arbitrary input. Altered
contracts/messages are rejected by the fixture. The ordinary application retains
its Gemini integration, but real requests require separately reviewed keys,
project quota/billing access and explicit live authorization. The walkthrough
makes no new Gemini or PayPal request.

## Architecture

Next.js / React / TypeScript calls FastAPI through owner-scoped cookie sessions
and CSRF-protected routes. SQLite stores original agreements, retained PDFs,
reviews, chronological messages, grounded findings, change-order proposals,
audit records, durable AI budgets and invoice reservations. Alembic head is
0009_conversations. Human edits retain the original AI output and invalidate
stale conclusions/approval context.

New PDF page maps bind exact extracted-text ranges to the original binary hash.
Repeated and overlapping quotes need a validated occurrence or longer unique
text. Invalid supplied ranges are never silently repaired. Legacy maps without
the binding use truthful extracted-text evidence with pages unverified; there is
no OCR, visual-coordinate, signature or legal-authenticity claim.

The Project Intelligence Dashboard and Evidence Pack derive complete bounded
snapshots. The two-page brief and full appendix both carry private source JSON;
a short PDF is not a sanitized public report. Only synthetic screenshots are
prepared for this release. No real client data belongs in Git.

## Safety and resource limits

No public signup or shared privileged account exists. Owners control access;
clients cannot approve freelancer decisions. Password/session/CSRF, optimistic
revisions, durable model budgets and single-process enforcement remain in place.

Uploads are limited to 5 MiB, 50 PDF pages and 100,000 extracted characters.
Conversations allow 19 messages plus a cumulative finding. Original PDFs retain
64 MiB/account and 512 MiB/application limits. SQLite is capped at 1 GiB,
including text/history. Encrypted backup transfer permits 1 GiB + 1 MiB and uses
bounded chunks; real target-host recovery remains unverified.

Project snapshots allow 10 agreements, 30 requests, 100 milestones, 500 rows per
audit category and 2 MiB of sources. Larger catalogs remain browseable, but no
partial aggregate is labelled complete. See the installation guide for safe
capacity, revision, authorization and offline-mismatch troubleshooting.

PayPal is sandbox only. Draft creation and sending are separate confirmed
actions with durable reservations and reconciliation. Scope AI cannot trigger
financial actions, and there is no automatic change-order-to-invoice bridge.
Mock DRAFT/PAID, cached local state, provider-checked sandbox status and actual
payment are distinct. No savings, recovered revenue or production settlement is
claimed. Public production disables live provider operations.

## Licence and sponsors

[LICENSE](LICENSE) applies the maintainer-authorized MIT grant to original
ScopeShield source the maintainer is entitled to license. Separate
[third-party notices](THIRD_PARTY_NOTICES.md) apply to dependencies and assets;
MIT does not relicense commercial software or third-party fonts.

The core dashboard runs without initializing Studio. Blank-flag and strict
production builds exclude its runtime. Explicit local evaluation is preserved;
its installed commercial dependencies retain their own terms. No trial is
active, and public judge/demo/distribution rights remain unresolved. Do not
substitute the core dashboard for Studio and claim sponsor completion.

APIMatic's PayPal Context Plugin contributed development guidance to the OAuth
cache fix; it is not an application runtime or a generated Invoicing SDK.
[Sponsor evidence and limits](docs/sponsors/USAGE_EVIDENCE.md) distinguish those
claims. Eligibility is decided by the organizers, not this README.

## Release status

This is local release preparation. The reviewed source ZIP omits Git history,
historical QA media, exported PDFs and workstation reports; the full local
checkout/history/data remain preserved. Dependency installs still fetch upstream
packages under their own licences. No proprietary runtime is vendored in source.

Public repository and video URLs are pending. No push, public repository creation,
video upload, Azure provisioning or deployment is authorized. Azure's separate
candidate remains one Indonesia Central VM, two P4 disks, SSH closed and private
storage. Fresh credit/cost, what-if, permissions and A07 host acceptance checks
are required before separate deployment approval. Account records and private
hosting budgets are excluded from this public source candidate.
