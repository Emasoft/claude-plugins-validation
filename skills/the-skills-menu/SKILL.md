---
name: the-skills-menu
description: "Agent-facing à-la-carte menu of every claude-plugins-validation (CPV) skill, agent, and script: classifies a plugin-quality request — validate, security-scan, fix, cache-optimize, create, publish, marketplace, manage, semantic-grade — and routes it to the right tool. Use when a request mentions CPV, the CPV skills menu, or validating / fixing / publishing / scanning / cache-optimizing a plugin and the exact tool is unchosen. Trigger with 'read the CPV skills menu', 'use CPV to <task>', or Skill(claude-plugins-validation:the-skills-menu). Also the runtime catalog CPV agents consult (TRDD-478d9687)."
user-invocable: true
---

# the-skills-menu — universal CPV router + catalog

## Overview

CPV has **two menus for two audiences**:

- **Humans** get `/cpv-main-menu` — a real, interactive numbered menu
  rendered post-turn by the `claude-menu-system` Stop hook (zero token
  cost). Type a number, navigate, pick.
- **Agents (and any routing Claude) get THIS skill** — not a rendered
  menu, just a plain readable document that offers **every CPV skill,
  agent, and script à la carte**. Read it, pick what the task needs,
  invoke it. No menu chrome, no round-trips, no token cost beyond
  reading the page.

This is that agent-facing menu. Read it, classify the user's request,
and execute the mapped action — no need to remember script names, agent
names, or flags.

It serves two audiences with one document:

1. **Any Claude** told *"read the CPV skills menu and use whatever you
   need"* — classify the free-form request against the
   [Intent → Action table](#intent--action-table) and run the mapped
   tool directly, or hand the whole job to the `cpv` agent.
2. **CPV's own agents** — the runtime catalog of operational skills
   each agent loads on demand via the `Skill()` tool (TRDD-478d9687).

CPV is a plugin-quality toolkit. It can **validate**, **security-scan**,
**fix**, **optimize prompt-cache**, **create**, **publish to GitHub**,
**wire up a marketplace**, **migrate a marketplace layout**, **manage
installed plugins**, and **AI-grade** plugins, skills, agents, commands,
hooks, and MCP servers — for one plugin or a whole fleet.

## Two execution surfaces

| Surface | When to use it | How |
|---|---|---|
| **Route in this session** | The user is already talking to you and wants the work done here. | Read the [Intent → Action table](#intent--action-table), invoke the mapped agent / skill / script directly. |
| **Dispatch the `cpv` agent** | You want the whole job done autonomously in an isolated context (keeps your main context clean). | `Agent(subagent_type: "cpv", prompt: "<the user's request, verbatim>")` — it reads this menu, routes, executes, and returns a report path. |

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
   (`plugin-fixer`, `marketplace-fixer`, `cache-optimizer-agent`) that
   know the per-rule remediation recipes. Re-implementing a fix or a
   validation by hand wastes effort and drifts from the rules.
4. **Fleet → batch.** For more than one plugin (a marketplace, a list, an
   `@listfile`), prefer the `/cpv-batch-*` family — it fans out parallel
   workers from one message.
5. **Surface** the downstream result (severity counts + report path), or
   chain into the next step.

## Intent → Action table

Every "Claude Code" cell runs inside a session with CPV installed. Every
"Standalone (uvx)" cell runs from a terminal with **no install** (needs
[uv](https://docs.astral.sh/uv/); prefix with
`uvx --from git+https://github.com/Emasoft/claude-plugins-validation --with pyyaml cpv-remote-validate`).

| # | I want to… | Do this (Claude Code) | Standalone (uvx) |
|---|---|---|---|
| 1 | **Validate a whole plugin** (structure, hooks, skills, security, compat, quality — 20 validators) | Dispatch `plugin-validator`, or `Skill(claude-plugins-validation:plugin-validation-skill)` | `… plugin /path` |
| 2 | **Validate one skill** (SKILL.md frontmatter, sections, description quality) | Dispatch `skill-validation-agent`, or `Skill(claude-plugins-validation:skill-validation-skill)` | `… skill /path --strict` |
| 3 | **Validate an agent / command / hook / MCP / LSP** | Dispatch `plugin-validator` (covers all components) | `… agent /path` · `… command /path` · `… hook /path` · `… mcp /path` · `… lsp /path` |
| 4 | **Security-scan a plugin** (5 external scanners — trufflehog, cc-audit, tirith, semgrep, Cisco — + native skillaudit) | Dispatch `plugin-validator` (runs the full security pipeline), or fleet-wide `Skill(claude-plugins-validation:cpv-batch-security-audit)` | `… security /path` |
| 5 | **Security-scan BEFORE installing** an untrusted plugin / skill / marketplace | `/cpv-pre-install-scan <target>` (sandboxed; never writes to the plugin cache) | `… security <github-url-or-path>` |
| 6 | **Fix validation findings in a plugin** (mechanical per-rule remediation) — **do NOT hand-edit** | Dispatch `plugin-fixer` (validate → fix loop), or fleet-wide `/cpv-batch-fix` · `/cpv-batch-validate-and-fix` | — (fixing needs write access; run in Claude Code) |
| 7 | **Fix marketplace findings / migrate marketplace layout** (A ⇄ B ⇄ C) | Dispatch `marketplace-fixer`, or `Skill(claude-plugins-validation:migrate-marketplace-architecture)` | — |
| 8 | **Optimize prompt cache** (CA-01..CA-06 — dynamic placeholders, hook mutations, model-fork, unbounded output) | Audit: `Skill(claude-plugins-validation:cache-validation-skill)` or `/cpv-batch-caching-audit`. Fix: dispatch `cache-optimizer-agent` or `/cpv-batch-caching-optimize` | `… cache /path` (audit only) |
| 9 | **Create a new plugin / marketplace / skill / agent / command / hook / MCP** | Dispatch `plugin-creator`, or `Skill(claude-plugins-validation:create-plugin)` · `Skill(…:scaffold-skill)` · `…:scaffold-agent` · `…:scaffold-command` · `…:add-hook` · `…:register-mcp` | — |
| 10 | **Publish a plugin to GitHub + add it to a marketplace** | Dispatch `plugin-creator` (scaffolds repo + CI/CD + publishes), or chain `Skill(…:setup-plugin-repo)` → `Skill(…:setup-github-marketplace)` → `Skill(…:link-plugin-marketplace)` → `Skill(…:publish-to-marketplace)` | — |
| 11 | **Bring an old plugin up to the current CPV pipeline standard** | Dispatch `plugin-creator`, or `Skill(claude-plugins-validation:standardize-plugin)` / `Skill(…:canonical-pipeline)` | `… standardize /path` |
| 12 | **Manage installed plugins** (install / update / enable / disable / list / search / health-check) | Dispatch `plugin-manager`, or `Skill(claude-plugins-validation:plugin-management)` | `… doctor` (health-check only) |
| 13 | **Deep diagnostic** (all scanners + pipeline-staleness + cross-platform + marketplace-registration + cache-sync) | Dispatch `plugin-diagnoser`; for `.claude/` scope (user / project / local) `/cpv-batch-scope-diagnose` | `… doctor` |
| 14 | **AI-grade quality** (descriptions that won't trigger, unclear instructions, workflows with no exit) — **expensive, opt-in** | Dispatch `semantic-validator` (warns about 10–50× token cost first) | — (needs Opus; run in Claude Code) |
| 15 | **Do the same op across many plugins** (a marketplace / list / `@listfile`) | The `/cpv-batch-*` family — validate, security-audit, caching-audit/optimize, fix, validate-and-fix, full-scan-and-fix, scope-diagnose/fix | most aliases accept a `--marketplace <spec>` |
| 16 | **Just show me an interactive numbered menu** | `/cpv-main-menu` (human picks a number; zero-token Stop-hook render) | — |
| 17 | **Hand the whole free-form request to one autonomous worker** | `Agent(subagent_type: "cpv", prompt: "<request>")` | — |

> **Tip:** make a shell alias for the standalone path —
> `alias cpv='uvx --from git+https://github.com/Emasoft/claude-plugins-validation --with pyyaml cpv-remote-validate'`,
> then just `cpv plugin /path`, `cpv security /path`, `cpv skill /path --strict`.

## Scripts à la carte (no install, no tokens)

Pick one raw validator for a focused check. Pass the alias as the first
arg to `cpv-remote-validate` (standalone) or
`remote_validation.py <alias> <target>` (in the plugin cache). Full help:
`cpv-remote-validate --help`.

- **Whole-plugin:** `plugin` (all 20 + lint) · `standardize` (audit + fix to conventions)
- **Components:** `skill` · `agent` · `command` · `hook` · `mcp` · `lsp` · `rules`
- **Security & supply-chain:** `security` (5 external scanners + skillaudit) · `telemetry` (OTEL risks)
- **Quality & docs:** `scoring` · `docs` · `encoding` · `xref` · `enterprise`
- **Cache:** `cache` (CA-01..CA-06 audit)
- **Marketplace:** `marketplace` (Layouts A/B/C) · `settings-marketplace`
- **End-user `.claude/`:** `local-scope` · `project-scope`
- **Health & lint:** `doctor` (`--install-scanners`) · `lint` (15 languages)

Management (lifecycle, no validation) is reached via the `plugin-manager`
agent or `Skill(claude-plugins-validation:plugin-management)`.

## Prerequisites

- The Skill tool is available (every CPV agent declares no `tools:` field, so it inherits all tools — `Skill` included; a main-session Claude has it natively).
- A clear task statement (intent + target) so the right row can be picked.

## Plugin Skills (full catalog)

All entries below are invoked as
`Skill({skill: "claude-plugins-validation:<name>"})`. See
[skills-catalog](references/skills-catalog.md) for full per-skill inputs
and return contracts.

| # | Domain | Skills |
|---|--------|--------|
| 1 | Validate / diagnose | `plugin-validation-skill`, `skill-validation-skill`, `cache-validation-skill`, `semantic-validation-skill` |
| 2 | Fix / migrate | `fix-validation`, `fix-marketplace-validation`, `migrate-marketplace-architecture`, `canonical-pipeline`, `batch-fix-protocol`, `deterministic-codemod`, `marketplace-authoring-contract` |
| 3 | Scaffold / build | `standardize-plugin`, `create-plugin`, `setup-plugin-repo`, `setup-github-marketplace`, `setup-marketplace-auto-notification`, `link-plugin-marketplace`, `pack-components`, `add-component-to-plugin`, `add-dependency`, `add-hook`, `register-mcp`, `scaffold-agent`, `scaffold-command`, `scaffold-skill` |
| 4 | Publish / release | `strip-dev-submodules`, `refresh-readme`, `bump-version`, `show-version`, `publish-to-marketplace` |
| 5 | Routing / UX | `plugin-management`, `cpv-main-menu-skill`, `the-skills-menu-create` |
| 6 | Batch / fleet (TRDD-3dcbb37c) | `cpv-batch-validate`, `cpv-batch-security-audit`, `cpv-batch-caching-audit`, `cpv-batch-caching-optimize`, `cpv-batch-fix`, `cpv-batch-validate-and-fix`, `cpv-batch-full-scan-and-fix` |
| 7 | Scope-aware diagnostics (TRDD-a175f78d) | `cpv-batch-scope-diagnose`, `cpv-batch-scope-fix`, `cpv-batch-scope-diagnose-and-fix` |

## Agents (specialist workers)

Dispatch any row below with the Agent tool, passing the agent's name as
the `subagent_type`. The `cpv` agent is the general router; the rest are
specialists it can dispatch directly.

| Agent | Use it to… |
|---|---|
| `cpv` | Route + execute any free-form CPV request autonomously |
| `plugin-validator` | Run validators, return severity reports (incl. security) |
| `skill-validation-agent` | Validate a single skill |
| `plugin-fixer` | Fix plugin findings (per-rule remediation) |
| `marketplace-fixer` | Fix marketplace findings + migrate layout |
| `cache-optimizer-agent` | Apply CA-01..CA-06 cache fixes |
| `plugin-creator` | Scaffold plugins/marketplaces, publish to GitHub |
| `plugin-manager` | Plugin lifecycle (install/update/enable/disable/doctor) |
| `plugin-diagnoser` | Deep diagnostic (all scanners + staleness + sync) |
| `cpv-doctor-agent` | Scope-aware `.claude/` diagnosis + fix |
| `semantic-validator` | AI quality grade (expensive, opt-in) |
| `cpv-spark` | One bounded edit / file creation (lightweight) |

## Invocation rules

- **Namespace skills.** Always `Skill({skill: "claude-plugins-validation:<name>"})` — CPV skills are namespaced.
- **One skill at a time.** Don't load another until the first returns; a double-load wastes a round-trip.
- **Don't re-implement.** If a script or agent does the job, use it; never hand-roll a validation or a fix CPV already automates.
- A skill description that still says "Loaded by `<agent>`" is advisory — the the-skills-menu method means any agent (and any routing Claude) can invoke any skill.

## Output

This menu itself returns nothing — the chosen downstream tool produces
the output. Typical shapes:

- Validation / security → severity counts + report path.
- Fix → `[DONE]` / `[BLOCKED]` / `[BATCH_REQUIRED]` one-line summary.
- Scaffold / create → list of created files.
- Publish → published version + release URL.

## Error Handling

- **Unknown skill name** → the `Skill` tool errors out; re-check the name against the [Plugin Skills](#plugin-skills-full-catalog) table (CPV skills are always namespaced `claude-plugins-validation:<name>`).
- **Ambiguous intent** (the request fits no single row, or several) → default to **validate** first, report what was found, then offer the matching fix/optimize/publish step.
- **Standalone script refuses to run** from the plugin cache → that direct invocation is blocked by design; use the launcher `cpv-remote-validate <alias> <target>` or `remote_validation.py <alias> <target>`.
- **No write access** (URL / read-only target) → fix/scaffold/publish rows cannot run; fall back to a read-only row (validate / security-scan / cache-audit) and report.

## Examples

```yaml
# "use CPV to security-scan my plugin"
Agent(subagent_type: "plugin-validator",
      prompt: "Run the full security pipeline on /path/to/plugin; report severity counts + report path.")

# "fix the validation errors" — NEVER hand-edit; dispatch the fixer
Agent(subagent_type: "plugin-fixer",
      prompt: "Validate /path/to/plugin, verify false positives, fix every real finding, re-validate clean.")

# "read the CPV skills menu and use whatever you need" → hand it all to one worker
Agent(subagent_type: "cpv", prompt: "<the user's whole request, verbatim>")
```

## Resources

- [skills-catalog](references/skills-catalog.md) — full per-skill table with inputs + return contracts.
  > Validation / diagnostic skills · Fix / migration skills · Scaffold / build skills · Publish / release skills · Routing / UX skills · Batch / fleet skills · Scope-aware diagnostics · Invocation pattern
