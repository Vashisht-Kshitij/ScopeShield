# ScopeShield — publication approval checklist

**Preparation only. No public release, repository creation, push, video upload,
submission or deployment is authorized.** Check this against the final local
commit and ZIP manifest; do not publish the entire working directory.

## Prepared locally

- [x] Inspect baseline c542b2a, preserve existing data/reference scripts and use a
  separate Day 24 branch.
- [x] Scan tracked source and historical text for secret patterns; review cloud
  identifier matches without exposing values. Test GUIDs are synthetic fixtures.
- [x] Inspect tracked/historical PDF attachments: all source contract texts match
  versioned synthetic examples; no non-example email domain was found in them.
- [x] Keep .env, databases, private keys, cloud caches, runtime packages and local
  diagnostics out of source; strengthen ignore patterns.
- [x] Prepare a fail-closed, allowlisted local source ZIP without Git history,
  historical QA screenshots, exported report PDFs or workstation reports.
- [x] Prepare cross-platform installation guidance, individual account provisioning,
  a fixed coherent offline dataset, video narration/storyboard and Devpost draft.
- [x] Preserve single-process, auth/CSRF/ownership, human approvals, exact money,
  durable budgets/reservations and truthful simulation/provenance labels.

Actual final test/install results and their limitations are in
[DAY24_RELEASE_READINESS.md](DAY24_RELEASE_READINESS.md). These checkboxes describe
prepared controls; they are not a declaration that publication is approved.

## Human ownership and legal sign-off — still required

- [x] Maintainer confirmed individual development with AI assistance and authorized
  MIT for original source they are entitled to license. No other contributor
  permissions were reported missing. Third-party materials retain separate terms.
- [ ] Confirm the four new core screenshots are approved for public use; inspect
  captions, simulated identifiers, example recipient and UI branding.
- [ ] Keep the DejaVu/Bitstream font licence and all applicable upstream notices.
  Dependencies retain their own terms; no blanket redistribution grant is assumed.
- [ ] Review dependency declarations/lockfiles and the distinction between a core
  runtime without Studio and development installs that still fetch commercial
  packages. MIT does not grant Studio source/binary/media rights.
- [ ] If pursuing public Studio judging/media, obtain written vendor/organizer
  terms covering the exact use and judging period. No trial or purchase is approved.

## Public source candidate review — before approval

1. Build the preview from the reviewed staged tree with
   `python -B -m scripts.public_source --staged`, or from a final commit with
   `python -B -m scripts.public_source --revision COMMIT`.
   These commands create only a new ignored local ZIP; neither contacts GitHub.
2. Inspect release-source-manifest.json, ZIP SHA-256, included paths and omissions.
   Confirm no .git/history, ignored state, private reports, binaries or keys.
   The font is a deliberately retained licensed asset, not proprietary Studio code.
3. Independently scan the final candidate and inspect screenshots, examples and
   every exported attachment intended for publication. Pattern scans are not a
   mathematical proof that no secret exists.
4. Confirm the clean installation/rehearsal results apply to the same source tree
   and pinned dependency locks. Do not substitute the original .venv/node_modules.
5. Choose the proposed **fresh-history public repository from this source export**.
   The full development checkout/history stays local. Publishing existing history
   instead requires a separate review of the omitted reports/media and permissions.
   No history rewrite/deletion is performed or implicitly authorized.
6. Approve the exact repository name, owner/visibility, source ZIP/commit and MIT
   scope. Do not use blanket `git add .` on this original workspace; its two
   supplied live Gemini scripts remain untracked and excluded.

## Main video and Devpost — separate publication approval

- [ ] Record the manual Cedar flow from a new empty workspace, with visible
  synthetic/offline/mock labels and no login, client data or account settings.
- [ ] Read the narration aloud and export **under 180 seconds**, including fades.
  Target 2:58; review every frame and audio track before upload.
- [ ] Verify that the pending USD 300 proposal is described as human-entered,
  not agreed revenue, automated pricing, savings or an approved change order.
- [ ] Verify that the USD 500 INV2-MOCK draft is never described as an actual
  PayPal invoice, sent email, verified payment or completed live-provider flow.
- [ ] Recheck the organizer's current rules/form, participant eligibility and IP
  rights; approve the final English text, test instructions and media rights.
- [ ] Replace repository/video/revision placeholders only after approved creation
  and verification. No deployed URL is available or invented.
- [ ] Decide sponsor entries separately. APIMatic evidence is development-context
  usage; public Studio eligibility/rights remain unresolved.
- [ ] If an access account is requested, supply individual isolated access by an
  approved private method. Never put a shared privileged password in public docs.

The [official rules](https://paypalaihackathon.devpost.com/rules) currently list
the submission deadline as **November 12, 2026, 12:00 PM PST** and judging through
**December 15, 2026, 8:00 AM PST**. Reconfirm before submitting; preparing drafts
does not enter the competition. Any hosted access must remain available through
the judging end. Local build access remains an option.

## Azure remains a separate NO-GO

No source/media approval authorizes infrastructure. Preserve Indonesia Central,
one backend process, two P4 disks, SSH closed, restricted private Blob access,
sandbox-only PayPal and disabled public live-provider mutations. Before creation:
refresh student credits/protection, eligibility/capacity assumptions, regional
costs including maximum backup retention, effective permissions, source-bound
what-if and A07 target-host acceptance. Hosting budgets and subscription evidence
remain private; no subscription upgrade or billing change is approved.

## Exact approval boundary

After final local verification, present the final commit, source ZIP/hash, omitted
paths, ownership/licence decisions, destination and media/submission drafts.
Ask for explicit approval **before** creating a public repository, adding/pushing
to a public remote, uploading video/screenshots, or submitting Devpost. Each action
must stay within that approval. No such action is part of Day 24 preparation.

If anything sensitive is discovered later, stop publication. Keep evidence private,
obtain provider/operator review for revocation and a separately approved history
sanitization plan. Removing a working-tree file alone does not remove Git history.
