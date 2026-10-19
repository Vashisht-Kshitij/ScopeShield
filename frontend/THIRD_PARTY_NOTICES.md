# AG Studio local evaluation

Portions of this software are copyright 2015-2026 by AG Grid Ltd.

`ag-studio` and `ag-studio-react` 3.0.0 are commercial dependencies, not open-source
components covered by any licence on ScopeShield application code. Their bundled
AG Grid Enterprise and AG Charts Enterprise widgets are used only inside Studio.
No standalone Enterprise grid/chart is instantiated by ScopeShield.

Official documentation permits local testing without a licence:
https://www.ag-grid.com/studio/react/licence-install/

The optional feature defaults off, permits loopback hosts only, and is disabled
in the strict production profile. That profile compiles a disabled component in
place of the Studio runtime. No key, licence bypass, or watermark suppression is
included. No trial has been requested or activated by this work.

Package installation retrieves dependencies through the lockfile. Do not vendor,
relicense, or publish their source, node_modules, or a Studio-enabled bundle as
part of an open-source release. ScopeShield adapter source and tests may be
reviewed independently; installation/use of proprietary dependencies remains
subject to the vendor's terms. Public demo, production testing, redistribution
and judging-period rights must be confirmed separately before enabling a build
for judges. A feature flag is not a commercial licence.

Terms: https://www.ag-grid.com/eula/commercial/
Coverage: https://www.ag-grid.com/studio/react/enterprise-licence/
