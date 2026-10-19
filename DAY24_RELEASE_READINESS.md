# Day 24 — public source and hackathon demo preparation

> Publication-audit correction: this release copy excludes six historical Azure
> planning/account artifacts that the original Day 24 ZIP incorrectly included.
> Its legacy parser test now imports the application parser instead of an omitted
> smoke script. PUBLICATION_NOTES.md and RELEASE_MANIFEST.json describe the current
> candidate; original-ZIP exclusion claims below are superseded where applicable.
> Maintainer MIT authority has since been confirmed; publication is still pending.
> The development repository and its historical reports remain unchanged.

19 October 2026. Verified baseline **c542b2a**; local branch
**codex/day24-release-demo**. No public repository, push, deployment, Azure change,
live Gemini/PayPal/storage request, trial activation or billing change is authorized
or performed. Existing user data, historical artifacts and supplied reference
scripts are preserved.

## Publication audit and proposed source boundary

The working tree initially contained only the two supplied untracked Gemini
reference scripts. They were hashed for preservation, not read for credentials
or added to Git. Source-pattern scanning covers tracked/current source and
**647 distinct historical text blobs** from the baseline history. There were no
unresolved matches or oversized skips. Three exact marker-only test literals
were reviewed; they contain no private-key body. The direct publication audit
excluded ignored environments, actual user keys, cloud account caches and
untracked live reference scripts.
This bounded pattern scan is not a universal guarantee that no secret exists.

Additional history checks found no tracked environment/database/private-key/
runtime paths or real subscription-path/public-key match. Five identifier-like
occurrences were manually reviewed in unit-test revisions: all are all-zero or
zero-filled dummy GUIDs, not account identifiers. Values are not printed in the
audit receipts. No cloud account, credential or access-token inspection was used.

The three tracked PDF blobs were inspected, including their JSON attachments.
All six embedded source-contract texts match versioned examples, and attached
email domains are reserved example domains. They are synthetic report artifacts,
not evidence of real customers or payment. Labels alone were not taken as proof.
They and historical QA media remain intact locally but are omitted from the
proposed public-source export, avoiding accidental publication of private report
formats or unresolved Studio screenshots.

The source preview is an **allowlisted ZIP without Git history**. It includes
application/tests/migrations/examples, source deployment tooling, locked manifests,
MIT/notices, current installation/demo/submission documents, sanitized sponsor
evidence and four new synthetic core screenshots. It excludes old daily reports,
verification/diagnostic artifacts, historical screenshots and report PDFs. It is
not a history rewrite, GitHub repository or authorized release. The original
checkout and all historical evidence remain available locally.

`scripts/public_source.py` rejects private/runtime paths, links, disguised SQLite
files and unresolved secret patterns; emits source-tree/file hashes and omissions;
and marks publication_approved=false. It never calls GitHub. The full development
history has been scanned but is **not** the proposed public payload. Publishing it
instead requires a separate complete asset/privacy/rights review.

## Licence and ownership

The standard MIT grant is prepared for original ScopeShield code. The maintainer
still must confirm authority over starter/upgrade source, contributions, product
artwork and documentation. The preparation task does not supply that legal
assertion. Third-party dependencies, fonts, commercial tools and trademarks are
excluded from the original-code grant. DejaVu/Bitstream terms remain beside the
font. No vendored Studio code, package binaries or commercial key was found in
tracked application source; declaration in a lockfile does not grant redistribution.

Blank-flag core and strict production exclude Studio runtime. Commercial packages
are still resolved for development/typechecking under their separate terms.
No trial is active. Public Studio judge/demo/media/distribution rights and sponsor
qualification remain unresolved. The new public preview contains core screenshots
only. [Sponsor evidence](docs/sponsors/USAGE_EVIDENCE.md) distinguishes local Studio
evaluation and APIMatic development guidance from actual runtime capabilities.

## Clean installation and portability

[INSTALL_GUIDE.md](INSTALL_GUIDE.md) is canonical for fresh Windows/POSIX setup,
individual hidden-password provisioning, migrations, offline/mock execution,
optional separately authorized providers, resource limits and troubleshooting.
No desktop path, copied database, licence key or original virtual environment is
required. QA runners now find installed pnpm on PATH or an explicit executable
instead of silently assuming the original Codex Windows cache.

A new local clone at c542b2a received a fresh venv and fresh node_modules/store.
The Node installation fetched the frozen 179-package set from the official registry
with lifecycle scripts disabled. Python dependencies were fetched from official
PyPI with binary-only installs. The first attempt stopped on a greenlet wheel
checksum mismatch; no mismatched wheel was installed. Official JSON metadata
identified the pinned Windows wheel and a fresh isolated download matched its
published SHA-256. Only that verified wheel was installed, then the **unchanged**
lockfile completed and `pip check` reported no broken requirements. No expected
hash, dependency version or security check was relaxed.

The allowlisted source preview was extracted into another new directory. It
received its own frontend installation from the freshly fetched isolated store,
a core Webpack build, a complete CLI/API rehearsal using the fresh Python venv,
and an actual Chrome Cedar workflow. Login, evidence preview and migrations
through 0009_conversations passed without schema drift. The rehearsal persisted
one project, three milestones, one pending order, one mock invoice and zero AI
usage. Restart readback did not create or send another invoice. No original
.venv/node_modules was copied. A fresh Windows install
does not prove Ubuntu/macOS execution, production memory, cloud availability or
A07 acceptance. Version pins are not a platform-complete independently signed SBOM.

## Coherent demonstration and truth labels

The Cedar fixture extends the existing extraction/comparison catalog with one
exact synthetic agreement and five exact messages. It does not add a second AI
pipeline. Existing schema, source, monetary association and review validators run
unchanged. Changed inputs fail offline matching, rather than receiving invented
answers. Dates on imported messages remain null; order and stable IDs persist.

Default CLI creation makes an empty, new marked workspace and one individually
passworded freelancer account. Its server is loopback-only, one worker, blocked
external provider HTTP and the existing explicit PayPal mock. Non-mock invoice
history is refused. The optional --complete flag explicitly scripts synthetic
review and a mock draft through actual auth/CSRF/revision APIs; it is not real
client consent and is not the manual footage path.

The rehearsed flow is upload → three grounded USD 500 milestones → five messages
→ initial cumulative expansion → human FAQ ambiguity correction → cumulative
clarification → reviewed assessment → **unapproved** human-entered USD 300 CMS
proposal → separately confirmed USD 500 mock invoice → dashboard → source-backed
Evidence Pack. Original agreement value remains USD 1,500. No send or payment occurs.
The complete/brief PDFs retain the same full sources, with independent export
timestamps; neither is a sanitized public summary.

| Evidence | What is established | What is not established |
| --- | --- | --- |
| Day 24 offline AI | Fixed fixture, real validators/persistence/UI, exact evidence and human correction | General inference, new live Gemini result, cumulative-model accuracy |
| Earlier real Gemini runs | Authorized extraction and separate single-request scope comparison, recorded in historical Day 10 evidence | A successful single uninterrupted run using both real providers, or Cedar cumulative evaluation |
| Day 24 PayPal mock | Real application approval/reservation/UI with in-memory DRAFT, one create, zero sends | Any invoice/email/payment at PayPal |
| Earlier real PayPal run | Separately authorized USD 1 sandbox draft and provider-read verification | Sending, payment, production settlement or the new USD 500 example |
| Local security/build/Chrome checks | Behavior in exercised offline environments | Public host safety, full accessibility coverage, legal interpretation or measured customer impact |

Historical real-provider evidence remains local and is not automatically copied
into public media. Never display actual invoice/account identifiers to make the
offline recording seem live. No new provider verification was requested here.

## Video, submission and media

[DEMO_SCRIPT.md](DEMO_SCRIPT.md) provides 333 words of English narration targeting
**2:58**, including fades; timing still requires a recorded read-through.
[DEMO_STORYBOARD.md](DEMO_STORYBOARD.md) provides exact actions, values, transitions,
labels and fallback still/clip instructions. The four prepared screenshots are
actual signed-in Chrome captures from the isolated source-export rehearsal using
only synthetic data and the core build. Both generated executive-brief pages
were rendered and visually inspected for readable layout and truthful labels.
No finished video or fallback motion clip has been recorded or uploaded.

[DEVPOST_SUBMISSION_DRAFT.md](DEVPOST_SUBMISSION_DRAFT.md) includes the problem,
architecture, AI/PayPal boundaries, sponsor evidence, challenges, accomplishments,
future work, media checklist and explicitly pending repository/video/access links.
No public URL, eligibility decision, customer statistic or verified revenue is invented.

The [official rules](https://paypalaihackathon.devpost.com/rules), reviewed for
this task, require a detectable open-source licence, public source and a video
under three minutes; a runnable local build can provide access. Current submission
deadline is November 12, 2026 at noon PST; judging ends December 15 at 8 AM PST.
Final participant/IP eligibility, form, media and deadline checks remain human
submission responsibilities. A draft is not an entry.

## Final verification

| Check | Final result and boundary |
| --- | --- |
| Complete backend/security/migration suite | **903 passed**, 937.55 seconds; one upstream Starlette/httpx deprecation warning |
| Focused release/tooling and catalog regressions | **14 passed** plus a separate **4 passed** catalog/tooling run; overlap with the full suite is not added to its count |
| Frontend unit/component suite | **135 passed** across 11 files |
| TypeScript | Passed; production builds also completed their type checks |
| Core Chrome workflow suite | **14 passed, 2 intentional skips**: old Day 19 seeded-project fixture and optional Studio fixture; Cedar independently exercises the core dashboard |
| Fresh source-export Chrome rehearsal | **1 passed** using fresh dependencies; exact manual Cedar flow, human correction, mock draft and Evidence Pack |
| Strict local HTTPS Chrome checks | **2 passed**: real local TLS/proxy, sessions/CSRF, isolated judge access and API restart; Windows mount recognition is simulated, not cloud-volume verification |
| Webpack production builds | Root local core, root strict production and fresh source-export core all passed |
| Commercial runtime exclusion | Core and strict production NFT traces contain **zero** Studio/Grid Enterprise/Charts Enterprise dependency files, including strict production with the evaluation flag supplied |
| Clean dependency installation | Fresh frozen 179-package frontend install; fresh Python environment, verified checksum recovery and successful pip check; no original runtime directory copied |
| Source-export CLI/API and migrations | Passed; head 0009_conversations, no schema drift, authenticated review, exact source/money and restart readback |
| Local infrastructure compatibility | Bicep compile and graph checks passed; **23** unsafe graph mutations rejected; SSH closed, data-disk detach, private storage and four container-scoped roles retained; zero Azure calls |
| Publication scans | **307** current text files and **647** baseline historical text blobs; no unresolved secret-pattern matches or oversized skips; identifiers and all three historical PDF blobs separately reviewed |
| Preservation | **26** protected file hashes unchanged, including original database, both supplied scripts, provider/security code, deployment templates and dependency locks |
| Documentation/media | 27 portable local links resolved; four final Chrome images and both brief pages visually reviewed |

The full backend run initially exposed the catalog test's old eight-scenario
expectation after Cedar became the ninth. The assertion was updated and all nine
fixture ingestions/analyses passed; the final full run passed. An initial new
browser capture raced an assessment reload; waiting for the visible saved
"Human edited" state fixed the test, without changing application behavior.
Early new-test expectation errors and failed-run logs remain in ignored local
artifacts; only the final results above are release evidence.

The tested export came from source tree
`f076f97120feed684a05514538bcb066682abe54`. Before the final commit, all included
application, script, test, migration and frontend source files were compared with
that archive and matched, allowing Git line-ending normalization. Only report
completion and replacement with the final fresh-install screenshots followed.
The final committed export has its own ignored receipt and per-file manifest;
it is not a claim that every historical report is approved for publication.

Local receipts are under .local/day24: backend-verified.xml/log, frontend.log,
typecheck.log, browser-verified.log, clean-browser.log, https.log, build-core.log,
build-strict.log, source-build.log, clean-source-rehearsal.log, pip-check.log,
secret-history.json, secret-source-final.json and preservation.json. These logs
are intentionally excluded from the public source package.

To repeat strict HTTPS QA, stop local demo servers, set frontend build variables
SCOPESHIELD_FRONTEND_PROFILE=production,
NEXT_PUBLIC_API_BASE_URL=same-origin,
NEXT_PUBLIC_FRONTEND_URL=https://demo.example.test and a blank
NEXT_PUBLIC_SCOPESHIELD_STUDIO; run `pnpm exec next build --webpack` in frontend/,
then `python -B -m scripts.run_deployment_tests` from the root using the venv.
Chrome/OpenSSL must be installed; this runner binds only loopback and blocks
external providers. Rebuild with the installation guide's local variables before
the ordinary demo. None of these checks establishes public-host acceptance.

## GO / NO-GO and next approval

**GO for local source/demo review. NO-GO for publication, submission or deployment.**
Local verification and source-package preparation are complete. Before
publication, review the final commit, ZIP/manifest and screenshots, confirm
MIT/contributor/asset authority,
record/review the under-three-minute video and approve the exact public destination
and payload. [RELEASE_CHECKLIST.md](RELEASE_CHECKLIST.md) is the approval gate.

Preferred proposal: a fresh-history public repository from the reviewed source
export, with individual local test instructions and the core-only video. That
proposal is prepared, not executed. Public Studio remains a separate rights gate.
A runnable local build remains the proposed judge-access path; no hosted deployment
exists. Any desired Azure hosting needs a fresh separate approval and all A07 checks. SSH closure,
student credit/cost protections, sandbox-only PayPal and disabled public provider
mutations remain. No history rewrite, key rotation, cleanup of real data, paid
resource, billing change or public action was performed.
