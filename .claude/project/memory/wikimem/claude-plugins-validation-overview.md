---
name: claude-plugins-validation-overview
description: "how does CPV work — what claude-plugins-validation is, how a plugin gets validated / security-scanned / fixed / published, and where the deeper pages are"
ocd: 2026-07-25
lmd: 2026-09-03
metadata:
  node_type: memory
  type: project
  tier: hub
  functionality: claude-plugins-validation-overview
  globs: ["scripts/**", "skills/**", "agents/**", "commands/**", "hooks/**", "tests/**"]
publish-globally: false
---
**CPV (`claude-plugins-validation`) is a UNIVERSAL quality gate for Claude Code plugins
and marketplaces.** It answers one question — *is this plugin correct, safe, and
publishable?* — for any plugin, including one that is not installed, has no marketplace,
and lives only as a pre-publish source tree. It is deliberately **not** tied to any
particular ecosystem: it must work standalone, so no validator may gate on an install
slug, a marketplace entry, or a path.

**How the pieces fit.** `scripts/` holds the engine: per-artifact validators (plugin,
skill, agent, command, hook, marketplace, MCP, …) over a shared core
(`cpv_validation_common.py`), plus the security surface — a taint engine, an `RC-NN`
detector-rule catalog under `scripts/rules/`, and several external scanners. Findings
carry a severity, and the severity contract is load-bearing: **CRITICAL / MAJOR / MINOR
always block; NIT additionally blocks under `--strict`; WARNING never blocks in any
mode.** Users reach all of it through *agents*, not raw skills — `commands/` exposes a
single menu plus batch entry points, `agents/` are the workers those menus dispatch, and
`skills/` are the procedures the workers load on demand.

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
