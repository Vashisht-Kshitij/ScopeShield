# AG Studio release strategy — 19 October 2026

The public candidate is the existing Project Intelligence dashboard and accessible
HTML review workflow. AG Studio is optional local evaluation. Both the default
core build (blank evaluation flag) and strict production profile compile the
disabled component instead of Studio. Production never enables it, even if the
evaluation flag is supplied. Dependencies are resolved by the lockfile for local
development/type checking; proprietary packages are not copied into Git or the
core runtime bundle. A dependency declaration does not relicense the dependency.

The [official installation guidance](https://www.ag-grid.com/studio/react/licence-install/)
permits local testing without a licence and directs production testing to a trial.
The [commercial EULA v40](https://www.ag-grid.com/eula/commercial/) has internal
evaluation terms; neither document establishes permission for this public judge
demo. The sponsor's advertised 45-day trial is not an authorization. No trial,
key, purchase or vendor contact was performed. Written judge/demo/distribution
and Agent Framework coverage through December 15, 2026 remains unverified.

Local evaluation is preserved: set NEXT_PUBLIC_SCOPESHIELD_STUDIO=local-evaluation
and SCOPESHIELD_FRONTEND_PROFILE=local before rebuilding, use loopback only, and
open the studio explicitly. Keep vendor warnings/watermarks. The installed
framework custom runner answers four exact offline queries, with zero tools and
no inference; it is not an autonomous LLM assistant. Day 20's implementation and
demonstration documentation remain available as historical evaluation evidence.

[AG Grid Community](https://www.ag-grid.com/javascript-data-grid/community-vs-enterprise/)
is MIT-licensed and could provide a public sortable/filterable table. However,
the current core already supplies the required project review data and evidence
navigation, so adding a second grid now would duplicate functionality. Community
Grid is not AG Studio, its Agent Framework or linked Studio dashboard widgets;
substituting it would not independently satisfy the
[Studio-focused sponsor challenge](https://paypalaihackathon.devpost.com/details/aggrid).
Recommendation: retain the tested core fallback and preserve the local Studio
integration while requesting a separate rights decision before any judge-facing
Studio build. Do not claim sponsor eligibility is settled.

The maintainer has confirmed original-code MIT authority. Remaining public Studio
blockers: review of Studio screenshots/trademarks and dependency notices; written
vendor/organizer confirmation if Studio is to be accessible to judges; final
submission/video/access verification. These are distinct from Azure approval.
