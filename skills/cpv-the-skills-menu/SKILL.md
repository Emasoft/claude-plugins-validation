---
name: cpv-the-skills-menu
description: "Agent-facing à-la-carte menu of every claude-plugins-validation (CPV) skill, agent, and script: classifies a plugin-quality request — validate, security-scan, fix, cache-optimize, create, publish, marketplace, manage, semantic-grade — and routes it to the right tool. Use when a request mentions CPV, the CPV skills menu, or validating / fixing / publishing / scanning / cache-optimizing a plugin and the exact tool is unchosen. Trigger with 'read the CPV skills menu', 'use CPV to <task>', or Skill(claude-plugins-validation:cpv-the-skills-menu). Also the runtime catalog CPV agents consult (TRDD-478d9687)."
user-invocable: true
---

# cpv-the-skills-menu — universal CPV router + catalog

## Overview

CPV has **two menus for two audiences**: humans get `/cpv-main-menu` — a real
interactive numbered menu rendered post-turn by the `claude-menu-system` Stop
hook (zero token cost); **agents and any routing Claude get THIS skill** — not a
rendered menu, just a plain readable document offering **every CPV skill, agent
and script à la carte**. Read it, pick what the task needs, invoke it.

It is also the runtime catalog CPV's own agents load skills from on
demand (TRDD-478d9687), for one plugin or a whole fleet.

## Instructions

How to route a free-form request, autonomously:

1. **Classify** the request into exactly one row of the
   [Intent → Action table](#intent--action-table). When the request is
   broad ("check my plugin"), default to **validate** first, then offer
   to fix.
2. **Execute** the mapped action — load the skill with `Skill()`, run
   the `uvx` script, or dispatch the specialist agent. **Chain** when the
   table says so (e.g. validate → fix).
3. **Never hand-edit to fix findings.** CPV ships fixer agents
   (`cpv-plugin-fixer-agent`, `cpv-marketplace-fixer-agent`, `cpv-cache-optimizer-agent`) that
   know the per-rule remediation recipes; hand-rolling one drifts from the rules.
4. **Fleet → batch.** For more than one plugin (a marketplace, a list, an
   `@listfile`), prefer the `/cpv-batch-*` family — it fans out parallel
   workers from one message.
5. **Surface** the downstream result (severity counts + report path), or
   chain into the next step.

## Intent → Action table

Every "Claude Code" cell runs in a session with CPV installed. Every
"Standalone (uvx)" cell runs from a terminal with **no install** (needs
[uv](https://docs.astral.sh/uv/); prefix with
`uvx --from git+https://github.com/Emasoft/claude-plugins-validation --with pyyaml cpv-remote-validate`).

| # | I want to… | Do this (Claude Code) | Standalone (uvx) |
|---|---|---|---|
| 1 | **Validate a whole plugin** (structure, hooks, skills, security, compat, quality — 20 validators) | Dispatch `cpv-plugin-validator-agent`, or `Skill(claude-plugins-validation:cpv-plugin-validation-skill)` | `… plugin /path` |
| 2 | **Validate one skill** (SKILL.md frontmatter, sections, description quality) | Dispatch `cpv-skill-validation-agent`, or `Skill(claude-plugins-validation:cpv-skill-validation-skill)` | `… skill /path --strict` |
| 3 | **Validate an agent / command / hook / MCP / LSP** | Dispatch `cpv-plugin-validator-agent` (covers all components) | `… agent /path` · `… command /path` · `… hook /path` · `… mcp /path` · `… lsp /path` |
| 4 | **Security-scan a plugin** (5 external scanners — trufflehog, cc-audit, tirith, semgrep, Cisco — + native skillaudit) | Dispatch `cpv-plugin-validator-agent` (runs the full security pipeline), or fleet-wide `Skill(claude-plugins-validation:cpv-batch-security-audit)` | `… security /path` |
| 5 | **Security-scan BEFORE installing** an untrusted plugin / skill / marketplace | `/cpv-pre-install-scan <target>` (sandboxed; never writes to the plugin cache) | `… security <github-url-or-path>` |
| 5b | **Full READ-ONLY scan of one folder or repo** (structure, rules, security, leaks, cache; no fixes) | `/cpv-validate-plugin-folder [path-or-url]`, or `Skill({skill: "claude-plugins-validation:cpv-validate-plugin-folder"})` | `… plugin /path` + `… security /path` |
| 6 | **Fix validation findings in a plugin** (mechanical per-rule remediation) — **do NOT hand-edit** | Dispatch `cpv-plugin-fixer-agent` (validate → fix loop), or fleet-wide `/cpv-batch-fix` · `/cpv-batch-validate-and-fix` | — (fixing needs write access; run in Claude Code) |
| 7 | **Fix marketplace findings / migrate marketplace layout** (A ⇄ B ⇄ C) | Dispatch `cpv-marketplace-fixer-agent`, or `Skill(claude-plugins-validation:cpv-migrate-marketplace-architecture)` | — |
| 8 | **Devitalize security threats** (rewrite execution-class code to provably-inert data; never suppresses a rule) | Dispatch `cpv-plugin-devitalizer-agent` (scan → devitalize → re-scan; flags load-bearing code) | — (needs write access) |
| 9 | **Prevent leaks & harden a plugin** (redact / runtime-read secrets, add missing safeguards; never suppresses a rule) | Dispatch `cpv-plugin-leaks-preventer-agent` (scan → redact/harden → re-scan) | — (needs write access) |
| 10 | **Optimize prompt cache** (CA-01..CA-07) | Audit: `Skill(claude-plugins-validation:cpv-cache-validation-skill)` or `/cpv-batch-caching-audit`. Fix: dispatch `cpv-cache-optimizer-agent` or `/cpv-batch-caching-optimize` | `… cache /path` (audit only) |
| 11 | **Create a new plugin / marketplace / skill / agent / command / hook / MCP** | Dispatch `cpv-plugin-creator-agent`, or `Skill(claude-plugins-validation:cpv-create-plugin)` · `Skill(…:cpv-scaffold-skill)` · `…:cpv-scaffold-agent` · `…:cpv-scaffold-command` · `…:cpv-add-hook` · `…:cpv-register-mcp` | — |
| 12 | **Publish a plugin to GitHub + add it to a marketplace** | Dispatch `cpv-plugin-creator-agent` (scaffolds repo + CI/CD + publishes), or chain `Skill(…:cpv-setup-plugin-repo)` → `Skill(…:cpv-setup-github-marketplace)` → `Skill(…:cpv-link-plugin-marketplace)` → `Skill(…:cpv-publish-to-marketplace)` | — |
| 13 | **Bring an old plugin up to the current CPV pipeline standard** | Dispatch `cpv-plugin-creator-agent`, or `Skill(claude-plugins-validation:cpv-standardize-plugin)` / `Skill(…:cpv-canonical-pipeline)` | `… standardize /path` |
| 14 | **Manage installed plugins** (install / update / enable / disable / list / search / health-check) | Dispatch `cpv-plugin-manager-agent`, or `Skill(claude-plugins-validation:cpv-plugin-management)` | `… doctor` (health-check only) |
| 15 | **Deep diagnostic** (all scanners + pipeline-staleness + cross-platform + marketplace-registration + cache-sync) | Dispatch `cpv-plugin-diagnoser-agent`; for `.claude/` scope (user / project / local) `/cpv-batch-scope-diagnose` | `… doctor` |
| 16 | **AI-grade quality** (descriptions that won't trigger, unclear instructions, workflows with no exit) — **expensive, opt-in** | Dispatch `cpv-semantic-validator-agent` (warns about 10–50× token cost first) | — (needs Opus) |
| 17 | **Do the same op across many plugins** (a marketplace / list / `@listfile`) | The `/cpv-batch-*` family — validate, security-audit, caching-audit/optimize, fix, validate-and-fix, full-scan-and-fix, scope-diagnose/fix | most aliases accept a `--marketplace <spec>` |
| 18 | **Just show me an interactive numbered menu** | `/cpv-main-menu` (human picks a number; zero-token Stop-hook render) | — |
| 19 | **Hand the whole free-form request to one autonomous worker** (isolated context; keeps yours clean, returns a report path) | `Agent(subagent_type: "cpv-agent", prompt: "<request, verbatim>")` | — |
| 20 | **Migrate a compiled-component plugin to ship ONLY the binary** — create the separate PUBLIC source repo, extract the source, ship `bin/` only (the `RC-SHIP-BINARY-ONLY` remediation) | Dispatch `cpv-plugin-fixer-agent`, or `Skill(claude-plugins-validation:cpv-strip-dev-submodules)`; recipe: `cpv-fix-validation/references/ship-binary-only-fixes.md`. **Confirm with the user before creating a PUBLIC repo.** | — (needs write access + gh auth) |
| 21 | **ONE agent + the skills it REACHES** — validate, security-scan, convert, or cost-compare variants | `agent --closure` · `agent-security` · `convert_agent.py --to <mode>` · `agent-eval` | same |

## Scripts à la carte (no install, no tokens)

Pick one raw validator for a focused check: pass the alias as the first
arg to `cpv-remote-validate` (standalone) or `remote_validation.py
<alias> <target>` (in the plugin cache). The aliases are the ones in the
**Standalone (uvx)** column above — `plugin` · `skill` · `agent` ·
`command` · `hook` · `mcp` · `lsp` · `rules` · `security` · `telemetry` ·
`scoring` · `docs` · `encoding` · `xref` · `enterprise` · `cache` ·
`marketplace` · `settings-marketplace` · `local-scope` · `project-scope`
· `standardize` · `doctor` · `lint`. Full help: `cpv-remote-validate
--help`. Management (lifecycle, no validation) is reached via the
`cpv-plugin-manager-agent` or `Skill(claude-plugins-validation:cpv-plugin-management)`.

## Prerequisites

- The Skill tool is available (CPV agents declare no `tools:` field, so inherit it; a main-session Claude has it natively).
- A clear task statement (intent + target) so the right row can be picked.

## Plugin Skills (full catalog)

All entries are invoked as `Skill({skill: "claude-plugins-validation:<name>"})`;
per-skill inputs and return contracts are in
[skills-catalog](references/skills-catalog.md).

| # | Domain | Skills |
|---|--------|--------|
| 1 | Validate / diagnose | `cpv-plugin-validation-skill`, `cpv-skill-validation-skill`, `cpv-cache-validation-skill`, `cpv-semantic-validation-skill` |
| 2 | Fix / migrate | `cpv-fix-validation`, `cpv-fix-marketplace-validation`, `cpv-migrate-marketplace-architecture`, `cpv-canonical-pipeline`, `cpv-batch-fix-protocol`, `cpv-deterministic-codemod`, `cpv-marketplace-authoring-contract`, `cpv-devitalize-threats`, `cpv-harden-and-redact` |
| 3 | Scaffold / build | `cpv-standardize-plugin`, `cpv-create-plugin`, `cpv-setup-plugin-repo`, `cpv-setup-github-marketplace`, `cpv-setup-marketplace-auto-notification`, `cpv-link-plugin-marketplace`, `cpv-pack-components`, `cpv-add-component-to-plugin`, `cpv-add-dependency`, `cpv-add-hook`, `cpv-register-mcp`, `cpv-scaffold-agent`, `cpv-scaffold-command`, `cpv-scaffold-skill`, `cpv-create-mono-agent`, `cpv-create-micro-agents-workflow` |
| 4 | Publish / release | `cpv-strip-dev-submodules`, `cpv-refresh-readme`, `cpv-bump-version`, `cpv-show-version`, `cpv-publish-to-marketplace` |
| 5 | Routing / UX | `cpv-plugin-management`, `cpv-main-menu-skill`, `cpv-the-skills-menu-create` |
| 6 | Batch / fleet (TRDD-3dcbb37c) | `cpv-batch-validate`, `cpv-batch-security-audit`, `cpv-batch-caching-audit`, `cpv-batch-caching-optimize`, `cpv-batch-fix`, `cpv-batch-validate-and-fix`, `cpv-batch-full-scan-and-fix` |
| 7 | Scope-aware diagnostics (TRDD-a175f78d) | `cpv-batch-scope-diagnose`, `cpv-batch-scope-fix`, `cpv-batch-scope-diagnose-and-fix` |
| _ | _ | `verification-before-completion` — Iron Law — no completion claim without fresh verification evidence. |

## Agents (specialist workers)

Dispatch any row below with the Agent tool, passing the agent's name as
the `subagent_type`. The `cpv-agent` agent is the general router; the rest are
specialists it can dispatch directly.

| Agent | Use it to… |
|---|---|
| `cpv-agent` | Route + execute any free-form CPV request autonomously |
| `cpv-plugin-validator-agent` | Run validators, return severity reports (incl. security) |
| `cpv-skill-validation-agent` | Validate a single skill |
| `cpv-plugin-fixer-agent` | Fix plugin findings (per-rule remediation) |
| `cpv-marketplace-fixer-agent` | Fix marketplace findings + migrate layout |
| `cpv-plugin-devitalizer-agent` | Rewrite execution-class findings to provably-inert data (never suppresses the gate) |
| `cpv-plugin-leaks-preventer-agent` | Redact / runtime-read secrets + add missing safeguards (never suppresses the gate) |
| `cpv-cache-optimizer-agent` | Apply CA-01..CA-07 cache fixes |
| `cpv-plugin-creator-agent` | Scaffold plugins/marketplaces, publish to GitHub |
| `cpv-plugin-manager-agent` | Plugin lifecycle (install/update/enable/disable/doctor) |
| `cpv-plugin-diagnoser-agent` | Deep diagnostic (all scanners + staleness + sync) |
| `cpv-doctor-agent` | Scope-aware `.claude/` diagnosis + fix |
| `cpv-semantic-validator-agent` | AI quality grade (expensive, opt-in) |
| `cpv-spark-agent` | One bounded edit / file creation (lightweight) |

## Invocation rules

- **Namespace skills.** Always `claude-plugins-validation:<name>`.
- **One skill at a time.** Don't load another until the first returns.
- **Don't re-implement.** If a script or agent does the job, use it.
- A skill description saying "Loaded by `<agent>`" is advisory — any agent or routing Claude can invoke any skill.

## Output

This menu returns nothing — the chosen downstream tool produces the output:

- Validation / security → severity counts + report path.
- Fix → `[DONE]` / `[BLOCKED]` / `[BATCH_REQUIRED]` one-line summary.
- Scaffold / create → list of created files.
- Publish → published version + release URL.

## Error Handling

- **Unknown skill name** → the `Skill` tool errors; re-check it against the [Plugin Skills](#plugin-skills-full-catalog) table (CPV skills are always namespaced `claude-plugins-validation:<name>`).
- **Ambiguous intent** (fits no row, or several) → default to **validate** first, report findings, then offer the matching fix/optimize/publish step.
- **Standalone script refuses to run** from the plugin cache → blocked by design; use `cpv-remote-validate <alias> <target>` or `remote_validation.py <alias> <target>`.
- **No write access** (URL / read-only target) → fix/scaffold/publish rows cannot run; fall back to a read-only row (validate / security-scan / cache-audit) and report.

## Examples

```yaml
# "use CPV to security-scan my plugin"
Agent(subagent_type: "cpv-plugin-validator-agent",
      prompt: "Run the full security pipeline on /path/to/plugin; report severity counts + report path.")

# "fix the validation errors" — NEVER hand-edit; dispatch the fixer
Agent(subagent_type: "cpv-plugin-fixer-agent",
      prompt: "Validate /path/to/plugin, verify false positives, fix every real finding, re-validate clean.")
```

## Resources

- [skills-catalog](references/skills-catalog.md) — full per-skill table with inputs + return contracts.
  > Validation / diagnostic skills · Fix / migration skills · Scaffold / build skills · Publish / release skills · Routing / UX skills · Batch / fleet skills · Scope-aware diagnostics · Invocation pattern
