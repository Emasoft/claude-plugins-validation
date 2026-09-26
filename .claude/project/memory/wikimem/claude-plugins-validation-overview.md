---
name: claude-plugins-validation-overview
description: "how does CPV work — what claude-plugins-validation is, how a plugin gets validated / security-scanned / fixed / published, and where the deeper pages are"
ocd: 2026-07-25
lmd: 2026-09-26
metadata:
  node_type: memory
  type: project
  tier: hub
  functionality: claude-plugins-validation-overview
  globs: ["scripts/**", "skills/**", "agents/**", "commands/**", "hooks/**", "tests/**"]
publish-globally: false
---
^W3DQ7KXR [desc:"CPV is a UNIVERSAL quality gate for Claude Code plugins and marketplaces, working standalone on uninstalled plugins; no validator may gate on an install slug, marketplace entry, or path.", keywords: what_is_CPV claude-plugins-validation universal_quality_gate plugin_validator is_this_plugin_correct_safe_publishable validate_uninstalled_plugin no_marketplace_required no_install_slug_gating standalone_validator not_tied_to_ecosystem pre_publish_source_tree scan_plugin_without_installing, type: project, ocd: 2026-07-25, lmd: 2026-09-26]
**CPV (`claude-plugins-validation`) is a UNIVERSAL quality gate for Claude Code plugins
and marketplaces.** It answers one question — *is this plugin correct, safe, and
publishable?* — for any plugin, including one that is not installed, has no marketplace,
and lives only as a pre-publish source tree. It is deliberately **not** tied to any
particular ecosystem: it must work standalone, so no validator may gate on an install
slug, a marketplace entry, or a path.

^T8HN4ZQL [desc:"scripts/ holds the validator engine over a shared core plus the security surface (taint engine, RC-NN rule catalog, external scanners); findings carry a severity contract: CRITICAL/MAJOR/MINOR always block, NIT blocks only under --strict, WARNING never blocks.", keywords: how_CPV_pieces_fit scripts_engine cpv_validation_common.py shared_core taint_engine RC_rules skillaudit rule_catalog external_scanners severity_contract which_severities_block CRITICAL_MAJOR_MINOR_block NIT_blocks_strict_only WARNING_never_blocks remote_validation launcher, type: project, ocd: 2026-07-25, lmd: 2026-09-26]
**How the pieces fit.** `scripts/` holds the engine: per-artifact validators (plugin,
skill, agent, command, hook, marketplace, MCP, …) over a shared core
(`cpv_validation_common.py`), plus the security surface — a taint engine, an `RC-NN`
detector-rule catalog under `scripts/rules/`, and several external scanners. Findings
carry a severity, and the severity contract is load-bearing: **CRITICAL / MAJOR / MINOR
always block; NIT additionally blocks under `--strict`; WARNING never blocks in any
mode.** Users reach all of it through *agents*, not raw skills — `commands/` exposes a
single menu plus batch entry points, `agents/` are the workers those menus dispatch, and
`skills/` are the procedures the workers load on demand.

^P5MR2B6C [desc:"Two invariants govern every CPV change: never call a valid plugin invalid (two-sided testing, misfire-prone advisories stay WARNING), and security is never traded for green (fixes make code provably inert, never suppress a rule); releases only via the canonical publish.py pipeline.", keywords: CPV_invariants never_call_a_valid_plugin_invalid north_star two_sided_testing advisory_misfire_WARNING security_first no_rule_suppression no_strict_relaxation provably_inert canonical_pipeline publish.py_release_gates lint_test_suite_self_validation_before_push, type: project, ocd: 2026-07-25, lmd: 2026-09-26]
**Two invariants govern every change here.** First, the north star: *never call a valid
plugin invalid* — a new detector is only worth shipping if it is two-sidedly tested, and
an advisory that could misfire belongs at WARNING. Second, security is never traded for
green: a finding is cleared by making the code **provably inert**, never by suppressing a
rule or relaxing `--strict`. Releases go out only through the canonical pipeline
(`publish.py`), whose gates run lint, the full test suite, a self-validation of CPV by
CPV, and the plugin's own tests before anything is pushed.

## Parts map

- [[prose-vs-executable-intent-canon]] — when a security rule fires on documentation
  prose: narrow the matcher on a property of the TEXT; never by path exclusion, severity
  re-tier, or grammatical voice. Includes the measure-co-firing-coverage discipline.
- [[agent-prompt-cache-and-context-economy]] — how the prompt cache actually works
  (prefix cache over `tools → system → messages`), what `skills:` frontmatter really
  injects, and which "cache optimizations" are folklore.
- [[agents-have-no-body-limit]] — what does and does not constrain an agent definition's
  size.
- [[agent-skill-closure-and-architectures]] — which skills an agent can actually REACH
  (tool-gated on `Skill`/`disallowedTools`, not the `skills:` list), the two preload
  entries that silently do nothing, and the three canonical architectures
  (ALL-IN-ONE / ONE-FOR-ALL / PLUGIN-OMNI) with the never-inline rule.
- [[hook-event-registration-is-a-two-half-contract]] — a Claude Code spec sync that adds a
  hook event must edit BOTH `VALID_HOOK_EVENTS` and `HOOK_OUTPUT_EVENT_FIELDS`; one
  invariant test is all that couples them, and an empty output schema must be a decision
  with a doc citation, never the `.get(..., frozenset())` default by accident.
- (add component pages here as they are written — the validator core, the security /
  taint surface, the canonical publish pipeline, the menu + agent dispatch architecture)

## Applies to

- (radiates down to this functionality's component and aspect pages; wire the reciprocal
  `## Governed by` on each one as it is added)

## See also

- Git-tracked `CLAUDE.md` at the repo root — the authoritative live inventory (component
  counts, version history, open-issues snapshot). Read it first on resume; this page is
  the *story*, that file is the *state*.
- [[rc164-inplugin-write-guard-fold-tiers]]

## Notes and lessons learned
