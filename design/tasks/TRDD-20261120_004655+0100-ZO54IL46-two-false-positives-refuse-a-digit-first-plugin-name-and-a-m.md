---
trdd-id: ZO54IL46
title: Two false positives refuse a digit-first plugin name and a multiple string userConfig list default
column: blocked
status: tasked
created: 2026-11-20T00:46:55+0100
updated: 2026-11-20T02:11:49+0100
current-owner: main-agent@claude-plugins-validation
created-by: main-agent@claude-plugins-validation
task-type: bugfix
min-approval-requirement: none
assignee: main-agent@claude-plugins-validation
mandate: true
mandated-by: none
approved: true
approval-judge: main-agent@claude-plugins-validation
approval-datetime: 2026-11-20T00:46:55+0100
blocked-by: [8OI25ZJ4]
pre-block-column: dev
blocker-probe: [git, ls-remote, --tags, https://github.com/Emasoft/claude-plugins-validation.git, refs/tags/v5.23.2]
blocker-holds-if: not-match:refs/tags/v5.23.2
---

# Two false positives refuse a digit-first plugin name and a multiple string userConfig list default

## Approval log

- 2026-11-20T00:46:55+0100 — MANDATE issued by main-agent@claude-plugins-validation (min-approval-requirement: none). Pre-approved: issuer authority >= required approver. No approval request was sent.

## Symptom

Search words: plugin name must not start with a digit; command name must not start with a digit; userConfig default type (list) does not match declared type (string). CPV --strict refused the plugin 3-pillars-tasks-management (2 CRITICAL, 1 MAJOR) while claude plugin validate passes on it.

## Doc lines relied on

Marketplace manifest reference chapter line 79 (marketplace name: starting with a letter or digit), line 101 (plugin entry name: starting with a letter or digit), lines 468-469 (install error text: must start with a letter or digit).
Plugin manifest reference line 183 (name: no spaces, @, :, path separators; use kebab-case; no first-character rule). Subagents reference lines 318 and 354 (name: no :, not starting with -, at most 256 chars). Skills reference lines 396 and 460-473 (skill and command names come from the frontmatter name or the file or directory name; no character rule). MCP servers reference lines 226 and 1134 (letters, numbers, hyphens, underscores; no first-character rule). No chapter forbids a leading digit for any type validate_component_name checks (plugin, skill, agent, command, mcp-server).
Plugin manifest reference line 455, userConfig table: multiple | No | For string, allows an array of strings.

## Decision

Fix the shared NAME_PATTERN (cpv_validation_common.py) to ^[a-z0-9]+(-[a-z0-9]+)*$ and its two byte-identical mirrors (validate_plugin._PLUGIN_NAME_RE, cpv_dependency_schema._NAME_PATTERN), drop the digit branch of validate_component_name, and update the marketplace messages and the cpv-fix-validation recipes that stated the old regex. Every consumer of the pattern (component names, marketplace name, marketplace plugin entry, settings marketplace names, dependency names) is covered by the marketplace reference lines above. Lowercase, single hyphens and a letter or digit at both ends are unchanged.
userConfig: accept a list of str default when type is string and multiple is true; keep the MAJOR when multiple is absent or false, or when any list item is not a string.
Options plus multiple: an options-bearing entry with multiple: true already draws a MAJOR from _validate_user_config_options, which is correct (plugin manifest reference line 503: options applies to a string field that is not multiple), so the new list-default acceptance cannot hide a real finding there. The guidance docs cpv-fix-validation pipeline-migration and cpv-canonical-pipeline pipeline-standards were also moved to the new regex because authors copy them into input validators.

## Follow-ups not in this change

Scaffolders and helpers still require a letter-first name when CREATING something: add_component.py _NAME_RE, add_dependencies.py _SPEC_NAME_RE, cpv_pack_components.py _NAME_RE, generate_marketplace_repo.py and standardize_marketplace.py KEBAB_RE, and the skill docs cpv-scaffold-skill, cpv-scaffold-agent, cpv-scaffold-command, cpv-add-dependency spec-syntax, cpv-plugin-validation-skill shape-detection. add_dependencies would refuse a dependency on a digit-first plugin.

## Acceptance checklist

- [x] New tests fail before the fix and pass after (digit-first names for plugin, skill, agent, command, mcp-server, marketplace, marketplace plugin entry, dependency; multiple list default)
- [x] Refusal siblings still fire (uppercase, underscore, hyphen shapes after a digit; list default without multiple, with multiple false, with a non-string item)
- [ ] Released through scripts/publish.py with CI and release runs green
- [ ] The released CPV no longer reports the two digit CRITICALs and the userConfig MAJOR on 3-pillars-tasks-management

## Evidence

Before the fix: the 38 selected new or inverted tests ran 30 failed, 8 passed (the 8 are refusal siblings that also hold on the old code). After the fix: 38 passed; the 7 touched test files ran 634 passed. mypy scripts/ --ignore-missing-imports: no issues in 163 files. ruff check scripts/ tests/: clean.

## Release status

2026-11-20: publish.py --patch (run 3, from a fresh clone fast-forwarded to 10a7b025) passed Gates 1-11 and failed at Gate 12. The atomic push did not land: origin master stays ff4ec418, no v5.23.2 tag on GitHub. The integrity audit entry at 01:44:07 (version 5.23.2, root the release clone) reads: no hash manifest for tag v5.23.2 could be fetched. The pre-push hook runs validate_plugin.py --strict; publish.py starts the Gate 12 push with env=os.environ.copy(), with no PLUGIN_INTEGRITY_RELEASE_CANDIDATE, so the hook validator looks for a GitHub tag manifest that cannot exist before the push. A manual simulation (anchor hand-computed at the clone HEAD, no push) passed the integrity check. Fix belongs in publish.py Gate 12 under its own card; not done here.
Correction to the 10a7b025 commit message: it says test_scenario_21_large_marketplace_performance does not touch naming. validate_dependencies imports cpv_dependency_schema, which holds the changed name pattern. Timing (5 solo runs each, machine loaded): base 1.46-1.55 s (4 timed), fix 1.33-1.96 s; inconclusive.
Correction to the entry above: the Gate 12 cause is INFERRED, not observed. publish.py captured the hook output and did not print it; the evidence is one integrity audit refusal at 01:44:07 for version 5.23.2 in the release clone, between Gate 11 and the push failure. Comparison with 5.23.1: its release clone (/private/tmp/cpv-release) logged release-candidate-verified at 02:17:24 and 02:28:11 and no integrity audit entry at all after 02:37:10, while its release commit is 02:39:54, so no CPV validator ran during its push. That suggests the 5.23.1 clone had no CPV pre-push hook installed (the user-global hooksPath holds only a git-lfs hook); this run's clone had the tracked git-hooks/pre-push installed by hand. Unverified hypothesis. Options for the owner: (a) publish.py Gate 12 passes a fresh release-candidate anchor to the push (own card plus test); (b) release from a clone without the CPV hook, as 5.23.1 apparently was; (c) the owner runs the release.
