---
trdd-id: NS1XJNPH
title: Dead validator constants and monitors coverage gaps
column: backburner
created: 2026-09-24T13:23:18+0200
updated: 2026-09-29T02:16:25+0200
current-owner: emanuelesabetta
created-by: emanuelesabetta
task-type: bugfix
min-approval-requirement: none
assignee: emanuelesabetta
mandate: true
mandated-by: none
approved: true
approval-judge: emanuelesabetta
approval-datetime: 2026-09-24T13:23:18+0200
---

# Dead validator constants and monitors coverage gaps

KNOWN_SETTINGS_KEYS (cc_scope_rules.py) and OPTIONAL_MARKETPLACE_TOP_LEVEL_FIELDS (validate_marketplace.py) are defined but read by no validator (verified by grep 2026-09-24), so the settings 'typo detector' and the marketplace top-level unknown-field check described in past release notes do not exist; wiring them could newly flag working files, so it needs its own measurement first. Also: monitors are validated only when declared in plugin.json -- the default monitors/monitors.json and experimental.monitors are not walked by validate_monitors_entries.

## Approval log

- 2026-09-24T13:23:18+0200 — MANDATE issued by emanuelesabetta (min-approval-requirement: none). Pre-approved: issuer authority >= required approver. No approval request was sent.

## Additional findings

fixture_grid_generator emits inline plugin.json hooks with no nested "hooks" array -- CPV's own 5 audit fixture-grid plugins went newly CRITICAL under the v5.19.0 inline-hooks checker (TRDD-Q3CPZL0X); fix the generator, not the 5 fixtures.
A hook file referenced by a non-default path string in plugin.json is not validated.
DISPOSITION 2026-09-29: item 'fixture_grid_generator emits inline hooks with no nested array' — FIXED in commit 8cfa987d (verified this session: generator source carries the nested matcher-block shape; all five hook fixtures CRITICAL=0 through the real strict validator on a minimal repro). Residual, NOT closed by that commit: materialized fixtures 13-17 still draw MAJORs — 13 the relative-hook-path finding (see the new defect appended below), 14-16 'manifest but no content' (the content check appears not to count inline hooks declared in plugin.json — separate suspected validator FP, uncarded), 15 absolute /bin/true in mcpServers (fixture-authored, cosmetic), plus advisory no-gitignore/no-CI rows. NEW DEFECT (this session, via repro): extract_script_paths' direct branch keeps relative hook tokens CWD-relative, so ./hooks/pre.sh with the file present at <plugin_root>/hooks/pre.sh fires 'Script not found' MAJOR. Fix layer under review (see next append); NS1XJNPH item 4 (non-default hooks path) amended mid-flight with a containment rule: paths that escape plugin_root are never read or linted.
FIX DIRECTION SETTLED (review round 2, 2026-09-29): the relative-token defect fixes at TWO layers, not by joining plugin_root — CC runs plugin hooks with cwd=PROJECT dir, so a bare relative token is UNRESOLVABLE, not MISSING: the validator suppresses the not-found MAJOR for unresolvable-relative tokens (the existing relative-path MINOR already names the real problem; resolvable roots keep the MAJOR), and the fixture generator emits ${CLAUDE_PLUGIN_ROOT}/hooks/pre.sh so fixtures model correct authoring. Joining plugin_root would have false-cleared a runtime-broken hook — the FN direction that matters.
