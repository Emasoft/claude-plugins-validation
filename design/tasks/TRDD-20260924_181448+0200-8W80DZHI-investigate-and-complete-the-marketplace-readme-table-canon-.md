---
trdd-id: 8W80DZHI
title: Investigate why the marketplace README-table canon rollout stopped and what blocks it
column: human_review
status: tasked
created: 2026-09-24T18:14:48+0200
updated: 2026-09-24T18:42:37+0200
current-owner: emanuelesabetta
created-by: emanuelesabetta
task-type: audit
min-approval-requirement: none
assignee: emanuelesabetta
mandate: true
mandated-by: none
approved: true
approval-judge: emanuelesabetta
approval-datetime: 2026-09-24T18:14:48+0200
---

# Investigate why the marketplace README-table canon rollout stopped and what blocks it

## ⏵ STATE — READ THIS FIRST ON RESUME (authoritative; supersedes the body) — 2026-09-24

- Investigation DONE. Report (gitignored): reports/rollout-investigation/20260924_182730+0200-marketplace-canon-rollout.md.
- Verified by the orchestrator (✓): D1 (marketplace fixer's completion gate is validate_marketplace --strict only), D3 (pipeline validator requires top-level version), D5 (setup_marketplace_automation overwrites existing files), D7 (standardize_marketplace emits update-catalog.yml that calls a renderer it does not ship), D9 (notify payload = repo name), web-scenario-tester stale 0.1.3 vs 0.1.7, emasoft-plugins renderer stamps date.today(), claude-plugins-management repo 404 even for the authenticated owner. The other defects (D2, D4, D6, D8, D10-D14), the PR #18 merge-red simulation, and the per-plugin table rest on the agent's report (? INFERRED).
- NEXT ACTION: the user decides — CPV fix order and card split, PR #18 (rebase now vs redo through the fixed agent), multi-marketplace scope, the three quick fixes. Every outward push needs the user's approval.
- Rollout work belongs in NEW cards, one per atomic task; this card closes once they exist.

## User directive (verbatim, 2026-09-24)

> ah yes, i remember. I instructed you to improve the cpv publish canon so that every user can publish his own plugin and can also create its own marketplace, and that pipeline must automate the updating of new plugins the user decides to add to its own marketplace, or even multiple marketplaces, and this automation ensure not only updating the manifest of both plugin and marketplace but also the workflows and the readme with the latest versions of the plugins added to it. in other words, this is a feature of the cpv plugin publish agent, not something specific to ai-maestro. it is a functionality that any user that installed the cpv plugin can use to publish his own plugins and marketplaced just asking to the cpv agent. The ai-maestro is an example i asked you to test, so the canon pipeline of all plugins published in the Emasoft/ai-maestro-plugins should be updated to implement this new pipeline with auto updating readmes, and also marketplaces with auto updating readmes. all publishing can be automated by the new cpv canonical pipeline (that include some scripts helpers like publish.py, some yaml workflows, some git hooks, some readme templates, etc.) and that any user can deply on github. I asked to update both the ai-maestro-plugins marketplace and the emasoft-plugins marketplace to the new version of the cpv publishing canon pipeline, but somehow this was interrupted halfway and only a partial update of the ai-maestro-plugins marketplace was done, and the emasoft-plugins marketplace was still not updated to the latest pipeline. But this is not cpv plugin development. its cpv plugin use. of course the fact that the cpv plugin failed halfway suggest that maybe there are some bugs in the plugin skills used by the cpv agent to publish plugins and marketplaces. So you must investigate. But the cpv plugin itself is only published on the emasoft-plugins marketplace. not anywhere else.

## Context

Prior work: TRDD-4EE90MC1 and TRDD-FK9Y6NCL (archived, published in v5.18.0). The only external action taken was PR #18 on Emasoft/ai-maestro-plugins (open, unmerged). ? INFERRED, not yet checked against the repos: emasoft-plugins was never updated, and TRDD-4EE90MC1 (2026-09-06) noted it still carried the date-stamped renderer. Completing the rollout is NOT in this card's scope: each outward-facing change (per marketplace, per plugin repo) goes in its own card and needs the user's approval.

## Scope: read-only investigation

Measure both marketplaces and their plugins against the current canon, find why the rollout stopped halfway, and name concrete defects in the CPV skills/agents/scripts that drive a marketplace + plugin migration. Report path goes in the STATE block when done.

## Approval log

- 2026-09-24T18:14:48+0200 — MANDATE issued by emanuelesabetta (min-approval-requirement: none). Pre-approved: issuer authority >= required approver. No approval request was sent.

## Findings (2026-09-24)

Report (gitignored): reports/rollout-investigation/20260924_182730+0200-marketplace-canon-rollout.md. Neither marketplace is at the current canon: ai-maestro-plugins main has none of it; PR #18 has all of it but is 10 commits behind and its own --check gate would go red on merge; emasoft-plugins still runs the date-stamped renderer. Only ai-maestro-plugins PR #18 was ever in scope (TRDD-4EE90MC1 item 7); emasoft-plugins and the per-plugin rollout were never planned. cpv-agent cannot do this migration today: 14 defects D1-D14, incl. the fixer's gate never sees the canon (D1), validate_marketplace_pipeline is Layout-B-only and requires top-level version (D2, D3), setup_marketplace_automation overwrites existing workflows with a Layout-B one (D5, spot-verified), the canon notify payload sends the repo name not the plugin name (D9, spot-verified; web-scenario-tester stuck at 0.1.3 vs 0.1.7, spot-verified). One plugin can notify only one marketplace.
User, verbatim (2026-09-24): "claude-plugins-management was an old plugin, now merged into the Emasoft/ai-maestro-plugin". So the emasoft-plugins entry is obsolete (superseded by ai-maestro-plugin, listed in ai-maestro-plugins), not a broken link to a live plugin; the fix is to remove the entry.
