# ScopeShield — recording storyboard and repeatable dataset

Use the blank-flag core build and guarded backend in [INSTALL_GUIDE.md](INSTALL_GUIDE.md).
Create an **empty** new workspace for every take; retain old takes. Do not use
--complete for footage presented as manual review. That option is an explicitly
scripted fallback for inspecting the completed synthetic records.

## Dataset and expected outcomes

| Record | Fixed synthetic content / expected state |
| --- | --- |
| Project | Cedar community launch page; one agreement, USD 1,500.00 total |
| Milestones | Wireframe / responsive page / source handover; USD 500.00 each; explicit dates and acceptance clauses |
| Review | Approve only the first milestone after reading the source; two remain awaiting review |
| Conversation | Five manually supplied client messages below; exact source text and stable IDs; blank dates |
| Initial fixture analysis | One individual IN_SCOPE, four OUT_OF_SCOPE, cumulative OUT_OF_SCOPE; offline simulation |
| Human correction | FAQ becomes AMBIGUOUS; overall cumulative conclusion becomes AMBIGUOUS; original AI output retained |
| Change order | Human-entered CMS proposal, USD 300.00, REVIEW_REQUIRED; no automatic approval or invoice bridge |
| Invoice | One USD 500.00 mocked sandbox DRAFT associated with the approved milestone; no send/payment |
| Dashboard | Original agreement unchanged; proposal separate from approved amounts; simulated invoice visibly distinct |
| Exports | Executive brief and complete appendix include full private source JSON; fixture only, no real client records |

Use role **client**, sender **Alex (synthetic client)** for every message. Copy
exactly, retain this order, and leave date fields blank:

1. `Please update the headline wording during the first revision round.`
2. `Could you also add a small FAQ section?`
3. `Please give our team a content management system to edit the page.`
4. `Can you deliver on 2026-11-12 instead?`
5. `Please keep the page updated after launch and cover weekend support.`

All dates in the contract/request text are fictional scenario facts. Blank message
date fields stay null; sequence provides order. Manual import does not authenticate
the sender. Altered text deliberately fails the offline fixture match.

## Screen-by-screen main cut

| Time | Screen and exact actions | Visible proof / transition |
| --- | --- | --- |
| 0:00–0:20 | Sign in before capture. Start at Overview with zero projects. Introduce Cedar's small-request problem. | Synthetic/offline/mock lower-third; product title. Cut to upload. Never capture password entry. |
| 0:20–0:32 | Upload agreement → choose examples/day24_cedar_agreement.txt → Upload agreement. Extract agreement → Offline demo → Run extraction. | Three USD 500 rows, total USD 1,500; offline extraction label. Typing/loading cut only. |
| 0:32–0:55 | Select wireframe. Locate its amount/payment evidence. Enter reviewer name/note; Approve milestone → acknowledge → Confirm approve milestone. | Exact source highlight, Approved status, remaining two review-required. Do not describe application approval as actual client acceptance. |
| 0:55–1:10 | Scope changes → Add conversation. Title “Synthetic Cedar launch conversation”. Add the five messages above and reviewer context → Save conversation. | Saved exact text, order and unauthenticated/manual label. Use an honest jump cut for repetitive typing. |
| 1:10–1:30 | Reviewer name/note → Analyze scope in offline mode. Inspect one excluded-contract clause and its own message. Scroll to “What changed across the conversation”. | Five individual findings plus initial cumulative OUT_OF_SCOPE; offline label stays visible. |
| 1:30–1:43 | Edit assessment. Change finding 2 (FAQ) to AMBIGUOUS. Explanation: “Clarify whether FAQ means an additional section or replacement content.” Uncertainty: “Confirm whether an existing section would be replaced.” Add reviewer context → Save reviewed assessment. | Human edited; cumulative AMBIGUOUS/clarification flag. This is the correction, not a hidden fixture rerun. |
| 1:43–1:55 | Approve assessment with explicit acknowledgement. Proposed additional deliverables: “SYNTHETIC PROPOSAL: CMS editor; FAQ, dates and support remain separate negotiation items.” Proposed price 300.00, USD. Prepare review draft. | Draft change order / REVIEW_REQUIRED; leave it unapproved. Caption: fictional human-entered proposal, not AI pricing. |
| 1:55–2:08 | Return to agreement → Invoices → Preview invoice draft. Inspect USD 500 and fictional sandbox recipient. Add reviewer context, partial-total acknowledgement and discrepancy explanation. | Mock fixture warning; USD 1,000 remains unapproved. No invoice exists before this explicit step. |
| 2:08–2:20 | Check separate creation confirmation → Create sandbox draft. | INV2-MOCK prefix, PayPal status DRAFT, mock verification label. Do not send or open provider links. Cut to overview. |
| 2:20–2:30 | Project overview. Inspect review queue, individual/cumulative classifications, proposal and invoice grouping. | USD 1,500 original value, simulation warning; no paid/recovered-revenue claim. |
| 2:30–2:40 | Evidence Pack → Download executive summary. Open the downloaded synthetic PDF locally. | Brief and source JSON disclosure. Crop unrelated browser tabs/download history. |
| 2:40–2:58 | Return to clean product overview and closing title. | Separate historical provider verification from this simulation; repository/access pending approval. End including fade by 2:58. |

Review annotations can use “Synthetic release reviewer” and “Synthetic rehearsal
decision; not real client acceptance or payment.” Do not type an actual judge's
identity into a sample approval. Do not claim real customer validation.

## Rehearsal, screenshots and fallback material

Automated reproduction (after the 8001 core build):
`python -B -m scripts.run_browser_tests --day24 --day24-only --day10`.
The final regression run omits --day24-only to exercise the wider core suite.
Passwords are generated in memory; trace/video/login screenshots stay off. The
runner stores results under a new .local/day24/run-* and a Day 24 latest pointer.

Four actual, signed-in synthetic core screenshots are prepared for review:

- [Grounded contract review](docs/day24/contract-review.png)
- [Human-corrected conversation](docs/day24/conversation-review.png)
- [Mocked invoice draft](docs/day24/mock-invoice.png)
- [Project intelligence overview](docs/day24/project-overview.png)

| Failure while recording | Honest fallback |
| --- | --- |
| Network/provider unavailable | Expected offline operation needs neither Gemini nor PayPal. Never switch to live mode during a take. |
| Long typing/loading | Use separately recorded footage from another fresh synthetic workspace; label edited timing. Preserve the same terms and sequence. |
| UI/server failure | Use a reviewed core screenshot above or restart the same marked workspace. Do not imply a still is a running interaction. Re-record the failed action before final publication. |
| PDF viewer unavailable | Show the real download and Evidence Pack preview. A fallback PDF from --complete is synthetic scripted output; label it as such. |
| Historical real-service clip unavailable | Use a caption saying the verification was separate; show no fabricated Gemini output, PayPal page or payment receipt. |
| Studio unavailable/licensing unresolved | Use the core dashboard in the main cut. Omit the optional Studio sponsor clip rather than substituting a basic table and claiming sponsor completion. |

No fallback clip has been recorded yet. The assets above are stills, not video.
Before export, capture short clean clips for each segment, check there are no
credentials/client tabs or notifications, and listen to a timed narration read.

## Recording and publication checklist

- English, legible cursor/text, no music or imagery without rights, no clipped labels.
- Persistent synthetic/offline/mock caption; keep native provider warnings visible.
- No browser sign-in, API consoles, shell environment dumps or real invoice IDs.
- No recovery/savings/customer-impact statistic without measured evidence.
- Verify the exported file is shorter than 180 seconds, including intro/fades.
- Video URL, public repository, account access and final submission remain pending.
  Do not upload, publish or submit this draft without separate approval.
