# Licence scope and third-party materials

The root MIT licence is prepared for original ScopeShield application source,
tests, infrastructure templates and original documentation. It does not grant
rights in dependencies, third-party assets, trademarks, private customer content
or software supplied under separate terms. The maintainer has confirmed individual
development with AI assistance and authorized MIT for original code they are
entitled to license. No third-party rights have been inferred from a file merely
being present in this checkout.

| Material | Treatment |
| --- | --- |
| AG Studio / React wrapper 3.0.0; embedded Grid/Charts Enterprise | Commercial dependencies, excluded from the root MIT grant. Their source/binaries and licence keys are not vendored. See frontend/THIRD_PARTY_NOTICES.md. Default-core and strict production builds exclude the Studio runtime; explicit loopback evaluation is separate. |
| DejaVu fonts in app/assets/fonts | Retain LICENSE-DejaVu.txt alongside the font files; the font licence includes upstream Bitstream terms. Do not claim the fonts are ScopeShield MIT source. |
| Installed Python/Node dependencies | Retain upstream copyright/licence notices when distributing them. requirements-day5.lock.txt and frontend/pnpm-lock.yaml are dependency manifests, not relicensing grants. No node_modules or virtual environment is part of the source release. |
| Synthetic PDFs and QA screenshots | Project-generated demonstration artifacts, not client records or proof of payment. Screenshots may contain third-party UI/trademarks; public use of Studio screenshots still requires the unresolved vendor/organizer review. |
| APIMatic PayPal Context Plugin | Development-time context, not a generated SDK or application runtime. Its separate MIT metadata does not cover AG Studio or proprietary third-party SDK source. |
| age, Bicep, Azure CLI and test binaries | Not vendored; ignored local tools or separately installed target prerequisites. Their own terms apply. Never publish .local, tool binaries, private identities or cloud account caches. |

No copied proprietary library implementation was found among tracked application
sources during the Day 23 inventory. This is a source inventory, not a warranty of
ownership or a complete legal review of every historical asset. Public release
still requires final publication approval and a candidate secret-and-asset review.
The maintainer's original-code MIT confirmation is recorded in PUBLICATION_NOTES.md. Do not publish the existing user-supplied untracked live
provider smoke scripts as part of a blanket add operation.

The Day 24 public-source preview includes only four newly captured **core** UI
screenshots and a sanitized sponsor-usage explanation. Historical Studio media,
generated report PDFs, local diagnostics and Git history are omitted from that
preview; the original local files/history are preserved. This packaging choice
does not authorize publication or grant rights to omitted commercial material.
