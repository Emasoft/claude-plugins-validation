---
trdd-id: 8W80DZHI
title: Investigate and complete the marketplace README-table canon rollout to ai-maestro-plugins and emasoft-plugins
column: live_auditing
status: tasked
created: 2026-09-24T18:14:48+0200
updated: 2026-09-24T18:14:48+0200
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

# Investigate and complete the marketplace README-table canon rollout to ai-maestro-plugins and emasoft-plugins

## User directive (verbatim, 2026-09-24)

> ah yes, i remember. I instructed you to improve the cpv publish canon so that every user can publish his own plugin and can also create its own marketplace, and that pipeline must automate the updating of new plugins the user decides to add to its own marketplace, or even multiple marketplaces, and this automation ensure not only updating the manifest of both plugin and marketplace but also the workflows and the readme with the latest versions of the plugins added to it. in other words, this is a feature of the cpv plugin publish agent, not something specific to ai-maestro. it is a functionality that any user that installed the cpv plugin can use to publish his own plugins and marketplaced just asking to the cpv agent. The ai-maestro is an example i asked you to test, so the canon pipeline of all plugins published in the Emasoft/ai-maestro-plugins should be updated to implement this new pipeline with auto updating readmes, and also marketplaces with auto updating readmes. all publishing can be automated by the new cpv canonical pipeline (that include some scripts helpers like publish.py, some yaml workflows, some git hooks, some readme templates, etc.) and that any user can deply on github. I asked to update both the ai-maestro-plugins marketplace and the emasoft-plugins marketplace to the new version of the cpv publishing canon pipeline, but somehow this was interrupted halfway and only a partial update of the ai-maestro-plugins marketplace was done, and the emasoft-plugins marketplace was still not updated to the latest pipeline. But this is not cpv plugin development. its cpv plugin use. of course the fact that the cpv plugin failed halfway suggest that maybe there are some bugs in the plugin skills used by the cpv agent to publish plugins and marketplaces. So you must investigate. But the cpv plugin itself is only published on the emasoft-plugins marketplace. not anywhere else.

## Context

Prior work: TRDD-4EE90MC1 and TRDD-FK9Y6NCL (archived, published in v5.18.0). The only external action taken was PR #18 on Emasoft/ai-maestro-plugins (open, unmerged). emasoft-plugins was never updated (TRDD-4EE90MC1 notes it still carries the date-stamped renderer).

## Phase 1 (this card): read-only investigation

Measure both marketplaces and their plugins against the current canon, find why the rollout stopped halfway, and name concrete defects in the CPV skills/agents/scripts that drive a marketplace + plugin migration. Report path goes in the STATE block when done.

## Approval log

- 2026-09-24T18:14:48+0200 — MANDATE issued by emanuelesabetta (min-approval-requirement: none). Pre-approved: issuer authority >= required approver. No approval request was sent.
