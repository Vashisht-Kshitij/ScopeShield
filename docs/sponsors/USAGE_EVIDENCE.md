# Sponsor-tool usage — sanitized evidence for review

This document contains no account identifier, credential, plugin code or licence
key. It describes actual implementation and its limits; sponsor eligibility is
not confirmed. No trial, purchase, benefit claim or vendor contact is authorized.

## AG Grid / AG Studio

The [sponsor challenge](https://paypalaihackathon.devpost.com/details/aggrid)
emphasizes a polished Studio dashboard, custom widgets/layout/theming and trying
Studio Agent Framework. A basic Grid or the core HTML table is not equivalent.

- Pinned AG Studio/React wrapper **3.0.0**, with the actual dashboard implementation
  in frontend/src/components/studio-workspace.tsx and projection in
  frontend/src/lib/studio-data.ts.
- Persisted owner-authorized project data feeds milestone/invoice grids, charts,
  linked filtering and a custom contract/conversation evidence widget.
- The documented Agent Framework custom runner accepts four exact offline queries
  with zero mutation tools. It is not a Gemini/LLM assistant or autonomous finance
  agent. The fourth query asks for scope-expansion candidates, not proven new
  deliverables inferred from every out-of-scope classification.
- Tests: frontend/tests/studio.test.tsx, tests/test_studio_projection.py and
  frontend/e2e/studio.spec.ts. Local evaluation was exercised previously; the
  Day 24 main demo is deliberately the core build.

[Local-evaluation guidance](https://www.ag-grid.com/studio/react/licence-install/)
does not settle public judging/distribution permission under the
[commercial EULA](https://www.ag-grid.com/eula/commercial/). No 45-day trial has
been activated. Blank-flag and strict builds exclude Studio runtime; dependencies
are declared for development under separate terms, not relicensed or vendored.
Historical Studio screenshots are excluded from the public source preview.

Before a Studio sponsor clip is public: obtain written rights covering judge
access, video/screenshots, source/dependency distribution and Agent Framework;
confirm coverage through December 15; then separately approve the exact clip/build.
Keep the core dashboard available. AG Grid Community is MIT but would duplicate
the existing table and would not demonstrate the same Studio integration.

## APIMatic PayPal Context Plugin

The [sponsor page](https://paypalaihackathon.devpost.com/details/apimatic)
describes using its PayPal context in coding agents. Day 21 installed the official
PayPal Context Plugin **0.4.1** and used its Python authentication/resilience guidance
to inspect the existing direct-REST adapter. This is a development-time tool.

Actual contribution, recorded locally in commit **ab52e2d**:

- Useful short-lived tokens no longer expire immediately merely because their
  lifetime is at most 60 seconds.
- Deadline calculation starts before acquisition; a response arriving after its
  declared lifetime is rejected instead of extending token validity.
- Concurrent token acquisition is serialized and cached against a monotonic clock.
- No new automatic 401 retry or financial-action retry was added.

Inspect app/paypal.py and run tests/test_paypal_day21.py. The mocked regressions
cover short/malformed/expired tokens, request delay, changing credentials,
concurrency and invalid-response behavior; no real token is needed to demonstrate
the fix. Durable invoice reservations and explicit actions remain independent.

The reviewed plugin source commit was
97bda162cba34576d8633b910fb4f37167812bf8. The historical installation compared all
36 installed plugin files with that pinned source. The website version label had
lagged the installed manifest. Neither plugin source nor private installation
configuration is copied into this public preview. The local development commit
is provenance, not a promise that a future fresh-history public repository will
contain that same historical Git object.

Do not claim a generated Invoicing SDK, APIMatic runtime, new real PayPal result
or guaranteed prize qualification. The separate sponsor demonstration can show
this explanation, current code and passing mocked tests. Any organizer evidence
request requiring additional installation receipts needs a separately reviewed,
redacted export; do not display account settings or authentication material.
