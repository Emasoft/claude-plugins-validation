# cpv-plugin-creator-agent runbook

Detailed reference loaded by the `cpv-plugin-creator-agent` agent (`agents/cpv-plugin-creator-agent.md`).
The agent body keeps the contracts, gates, and protocols inline; this file holds the
long-form detail — marketplace layouts, the script table, the full publish-as-GitHub-repo
workflow, the empirical loading footguns, the hard-won lessons, the dev-stripping
render recipe, and the gap-detection table. The agent points here wherever that detail
was removed; nothing is orphaned.

## Table of Contents

- [1. Marketplace layouts — three legitimate shapes](#1-marketplace-layouts--three-legitimate-shapes)
- [2. Scripts](#2-scripts)
- [3. Workflow: Publish Plugin as GitHub Repo](#3-workflow-publish-plugin-as-github-repo)
- [4. CRITICAL: Empirical Plugin-Loading Footguns (verified 2026-04-18)](#4-critical-empirical-plugin-loading-footguns-verified-2026-04-18)
- [5. HARD-WON LESSONS](#5-hard-won-lessons)
- [6. Dev-stripping (TRDD-793ac32a — Sprint 2)](#6-dev-stripping-trdd-793ac32a--sprint-2)
- [7. Gap-detection table + delegation rules](#7-gap-detection-table--delegation-rules)
- [8. Path Resolution — skill destination sub-procedure](#8-path-resolution--skill-destination-sub-procedure)
- [9. Phase 0 — wrap / add / cancel detail](#9-phase-0--wrap--add--cancel-detail)
- [10. Additional worked example](#10-additional-worked-example)

## 1. Marketplace layouts — three legitimate shapes

CPV supports three layouts; pick the one matching the user's distribution intent. Full A/B/C templates, the "why no git-subdir" rationale, non-CPV-marketplace migration, and the rich per-plugin metadata fields are EMBEDDED in [marketplace-layouts.md](../skills/cpv-create-plugin/references/marketplace-layouts.md) (loaded by `cpv-create-plugin`).
> Checklist · Overview · Layout A — Hub-and-Spoke (separate repos) · Layout B — Nested single-repo (monorepo) · Layout C — Marketplace-in-plugin (self-referential single repo) · How Claude Code updates plugins in each layout · When to choose which · Rich metadata fields (author, homepage, license, category) · Why CPV does not use git-subdir · Encountering a non-CPV marketplace · Refactoring between layouts · Agent behavior summary

Read it before deciding shape.

- **Layout A (hub-and-spoke)**: each plugin = independent GitHub repo; marketplace holds only `marketplace.json` + CI; entries use `{"source": "github", "repo": "<owner>/<name>"}`.
- **Layout B (nested monorepo)**: all plugins as subfolders; entries use `"./plugins/<name>"`; ONE tag/CHANGELOG/cliff.toml/publish.py + shared CI.
- **Layout C (marketplace-in-plugin / self-referential)**: ONE repo that is BOTH plugin AND marketplace; root has `plugin.json` AND `marketplace.json` with a single self-entry (`"source": "./"`, name matching plugin.json, versions kept in sync).

**Default suggestion:** ONE plugin + no existing marketplace → **C**; multiple plugins → **B** (shared release cycle) or **A** (independent repos). Follow a stated preference. For a 4th option (mixed/submodules/git-subdir) explain CPV discourages hybrids and offer A/B/C. git-subdir: validator accepts it for compat but the creator NEVER emits it.

When asked to validate/standardize a non-CPV marketplace (nested, no tags/CHANGELOG/CI, mixed authorship), run the validators, explain the tradeoffs, and offer migration to A or B.

When creating a marketplace or adding a plugin, use `AskUserQuestion` to gather per-plugin metadata (Category, Homepage URL, Author, License, Description) and inject the answers into the entry before validation — never invent values silently; if the user declines a field, omit it, but ask first.

## 2. Scripts

All at `${CLAUDE_PLUGIN_ROOT}/scripts/`. **VALIDATORS** must always be invoked via the launcher (NEVER directly — environment-isolation guard refuses):

```bash
uv run --with pyyaml python "${CLAUDE_PLUGIN_ROOT}/scripts/remote_validation.py" <alias> <args>
```

**SCAFFOLD/STANDARDIZE/UTILITY scripts** (no environment-isolation guard) can be invoked directly with `uv run python "${CLAUDE_PLUGIN_ROOT}/scripts/<script>" <args>`.

| Script | Purpose | Invocation |
|---|---|---|
| `generate_plugin_repo.py` | Scaffold a complete plugin repo with all standard files | direct |
| `generate_marketplace_repo.py` | Scaffold a marketplace hub repo | direct |
| `standardize_plugin.py` | Audit and fix an existing plugin repo to match standards | launcher: `standardize` |
| `standardize_marketplace.py` | Audit and fix an existing marketplace repo | launcher: `standardize_marketplace` |
| `validate_plugin.py` | Validate plugin passes all 190+ rules | launcher: `plugin` |
| `validate_marketplace.py` | Validate marketplace structure | launcher: `marketplace` |
| `validate_security.py` | Security scan (5 external scanners + AI rules) | launcher: `security` |
| `validate_cache.py` | Prompt-cache invalidation audit (CA-01..CA-07) | launcher: `cache` |
| `manage_github_validate.py` | Validate a GitHub plugin/marketplace without installing | launcher: `github` |
| `bump_version.py` | Bump plugin version (patch/minor/major) | direct |
| `manage_doctor.py` | Health-check + `--install-scanners` for all 5 external scanners + fclones | launcher: `doctor` or direct (`--install-scanners` only) |

## 3. Workflow: Publish Plugin as GitHub Repo

**THE GOLDEN RULE — fix everything BEFORE publishing.** The pre-push hook runs `--strict` and blocks on CRITICAL/MAJOR/MINOR/NIT (only WARNINGs pass). Unfixed issues block the push; fix them FIRST.

This sequence is the agent's choreography. The notify/PAT/publish chain detail lives in the `cpv-publish-to-marketplace` + `cpv-canonical-pipeline` skills; the scaffold pipeline-standards (cross-platform, sanitization, hook→`CLAUDE_PLUGIN_DATA`, PEP 723, idempotent publish.py, script-ref scan) are in `cpv-canonical-pipeline`'s [pipeline-standards.md](../skills/cpv-canonical-pipeline/references/pipeline-standards.md).
> Overview · Whole-repo lint via cpv_lint_engine · Idempotent publish.py · validate_pipeline_script_refs rule · Cross-platform scripts — no bash, no jq/sed/awk · Input sanitization — every script parameter · Hooks MUST persist state in CLAUDE_PLUGIN_DATA, never CLAUDE_PLUGIN_ROOT · Hook commands MUST be cross-platform (Python-delegated) · PEP 723 scripts MUST be invoked via uv run · Migrating a legacy plugin

Load them on demand; don't re-derive here.

1. **Validate** (launcher): `uv run --with pyyaml python "${CLAUDE_PLUGIN_ROOT}/scripts/remote_validation.py" plugin <folder> --strict --verbose`
2. **Standardize** (adds missing files): `… remote_validation.py standardize <folder> --fix`
3. **FIX ALL ISSUES** (CRITICAL → NIT): **always delegate to the `cpv-plugin-fixer-agent` agent** (`/cpv-fix-validation <report-or-plugin-path>`) — never improvise ad-hoc patches. The fixer accepts a report or a plugin path, runs the full validate→fix→re-validate loop, owns the error-to-fix mapping (including runtime-only Zod drift like `userConfig.<key>.type`/`title` the docs omit), and returns only when clean.
4. **Generate README component tables**: scan `commands/*.md`, `agents/*.md`, `skills/*/SKILL.md` frontmatter → tables; add Install/Uninstall/Update/Troubleshooting sections.
5. **Re-validate** (`--strict`): MUST show only WARNINGs — else loop to step 3. **DO NOT skip** — a 0-finding source run is how you confirm the plugin clears Claude Code's install-time manifest schema (releases shipped without this pass hit runtime-only Zod rejections, e.g. ai-maestro-janitor v0.1.2, 2026-04-18).
6. **Compiled binaries** (Cargo.toml/go.mod/Makefile present): sources MUST be in `src/`, binaries in `src/<component>/bin/`; add `build-binaries.yml` only as fallback.
7. **Git init + commit** (if not already a repo).
8. **Create GitHub repo**: `gh repo create <owner>/<name> --public --source . --push`
9. **Install git hooks — FROM INSIDE the newly-scaffolded plugin repo** (NOT CPV itself): `uv run python scripts/publish.py --install-hook`. The `--install-hook`/`--gate` flags live in the `publish.py` that `generate_plugin_repo.py` writes into every new plugin (`gen_publish_py`, `scripts/generate_plugin_repo.py`). CPV's own `scripts/publish.py` is the minimal bump variant and errors with `unrecognized arguments: --install-hook`. Once installed, the pre-push hook delegates to `publish.py --gate` (lint + validate `--strict` + tests; blocks on any non-WARNING).
10. **Determine target marketplace** (MANDATORY — never invent one). Use `AskUserQuestion`: an existing marketplace the user names (verify `gh repo view`); one visible via `claude plugin marketplace list`; or "create a new marketplace" → load `cpv-setup-github-marketplace` and create it BEFORE proceeding (Layout A default for multi-plugin; Layout C when this is the only plugin + single repo wanted; Layout B only on explicit monorepo request).
11. **Verify marketplace is CPV-standard**: `gh repo view <owner>/<marketplace> --json name` + `uvx --from git+https://github.com/Emasoft/claude-plugins-validation --with pyyaml cpv-remote-validate marketplace <owner>/<marketplace> --strict`. Anything above WARNING → route to `cpv-marketplace-fixer-agent` (`/cpv-fix-marketplace-validation <report>`) and wait. Never register into a broken marketplace.
12. **Configure marketplace notification on the plugin repo** (REQUIRED): update notify-marketplace.yml with MARKETPLACE_OWNER/MARKETPLACE_REPO; check `test -n "$MARKETPLACE_PAT"` (if unset ask for a PAT with `repo` scope); **set the secret ONLY via** `uv run python scripts/set_marketplace_pat.py <owner>/<plugin> <owner>/<marketplace>`. FORBIDDEN: `echo "$MARKETPLACE_PAT" | gh secret set …` (pipe stores a trailing newline → Bad credentials at push time).
13. **Final plugin validation + CI-parity preflight** (BEFORE the first publish — `--strict` does NOT mirror CI's Lint job, the #137-143 root cause): run the `--strict` validate (only WARNINGs — else loop to step 3) AND `uv run --with pyyaml python "${CLAUDE_PLUGIN_ROOT}/scripts/remote_validation.py" ci-preflight <plugin-root>` to catch the actionlint / Mega-Linter-`mypy` / jscpd / `uv sync --extra dev` gates plus the CIP-1..5 (#137-143) defect shapes that `--strict` skips. Resolve every non-WARNING ci-preflight finding before step 14; a WARNING (tool absent on this box) degrades and does not block. This is the gate that stops the first publish from RED-CI'ing on an actionlint / mypy / #137-143 class — the same `ci-preflight` `cpv-plugin-fixer-agent` runs in its completion gate.
14. **Run the publish pipeline**: `uv run python scripts/publish.py` (the plugin repo's, not CPV's) — bumps version, creates release, pushes tag, triggers `notify-marketplace.yml`. Wait for the dispatch.
15. **Verify marketplace registration**: poll until the marketplace repo's sync workflow completes AND `marketplace.json` on the default branch lists the plugin at the new version (`gh run watch <run-id>` + `gh api repos/<owner>/<marketplace>/contents/.claude-plugin/marketplace.json`). On failure: `gh run view --log-failed`, fix, re-dispatch. Don't claim success until `marketplace.json` reflects the new version.
16. **Emit final user instructions** — tell the user EXACTLY what to run. Use this template verbatim (substitute placeholders):

    ```
    ✓ Plugin is deployment-ready.

    Plugin repo:       https://github.com/<owner>/<plugin-name>
    Marketplace repo:  https://github.com/<owner>/<marketplace-name>
    Registered as:     <plugin-name>@<marketplace-name> (version <X.Y.Z>)

    To install it, run these commands yourself (the agent will not install plugins for you):

      # 1. Add the marketplace to Claude Code (FIRST TIME ONLY — skip if already added)
      claude plugin marketplace add <owner>/<marketplace-name>

      # 2. Refresh the marketplace cache
      claude plugin marketplace update <marketplace-name>

      # 3. Install the plugin — pick ONE scope:
      claude plugin install <plugin-name>@<marketplace-name> --scope user      # personal, all projects
      claude plugin install <plugin-name>@<marketplace-name> --scope project   # team, committed to this repo
      claude plugin install <plugin-name>@<marketplace-name> --scope local     # personal, this project only (gitignored)

      # 4. (Optional) Confirm it's enabled
      claude plugin list
    ```

    If the plugin was local-only (no GitHub), substitute the plugin/marketplace lines with the `--plugin-dir <path>` equivalent and explain it.

**NEVER** run `claude plugin install`, `claude plugin enable`, or `claude plugin uninstall` yourself — those are user decisions (scope, timing). The agent's job ends at step 16 with the instructions printed. (For which skill/agent handles each step, see the Invocation table in the agent body.)

## 4. CRITICAL: Empirical Plugin-Loading Footguns (verified 2026-04-18)

`claude plugin validate` won't catch these, but they break plugins silently at runtime. The full footgun table (folder-path `agents`, default-file `hooks`/`mcpServers`, MCP/LSP name shadowing) with empirical evidence lives in the `cpv-canonical-pipeline` and `cpv-plugin-validation-skill` skills (their detailed-standard and validation-checklist references). **The simplest safe default for a new plugin** — let CC auto-discover all four, so OMIT the manifest field whenever the file is at its default location:

- `agents/*.md` at plugin root → omit the `agents` field (if you must declare it, use `.md` FILE paths only, NEVER a folder path — CC rejects folder paths with cryptic `Invalid input`)
- `hooks/hooks.json` at plugin root → omit the `hooks` field (pointing it at the default file cascades to DISABLE this plugin's MCP servers via `hook-load-failed`)
- `.mcp.json` / `.lsp.json` at plugin root → omit `mcpServers` / `lspServers`

Only use a manifest field for a NON-default path or to declare additional files. A server name MUST appear in only ONE source — on collision, inline `plugin.json` silently wins and the other declaration is dropped.

## 5. HARD-WON LESSONS

See the **cpv-plugin-management** skill for the full list of 19 hard-won lessons from real publish runs. Key ones to never forget:
- Always `uv run --with pyyaml python` when running CPV scripts from outside the CPV venv
- Always `--body` flag for `gh secret set`
- Always `git config user.name/email` before committing in /tmp directories
- Always run `publish.py --gate` before the first push
- Marketplace entries MUST include `repository` field
- Strip ANSI codes: `| sed 's/\x1b\[[0-9;]*m//g'`

## 6. Dev-stripping (TRDD-793ac32a — Sprint 2)

When creating a NEW plugin from scratch, ask the user whether to enable dev-stripping (default ON for the ~12 MB install-size win; ships only the rc-2 enforcement + rc-3 metadata in plugin.json — actual `--auto` extraction deferred to rc-3). Render the choice via the claude-menu-system bridge (NOT `AskUserQuestion`): the Stop hook emits it post-turn via `systemMessage`, so it costs ZERO tokens and never enters the transcript. NEVER print the menu inline; END THE TURN right after the `cpv_menu.py` call, and resume on the user's next-turn reply using the FIXED letter→action map below.

**Fixed letter→action map (immutable, per TRDD-4de479a0 FIXED-KEY contract — the SOLE reference for routing the reply; never inspect the rendered menu):** `S` = standard (Standard/PSS pattern — one `cpv.strip` entry for `tests/`, default), `L` = legacy (keep everything in MAIN repo, discouraged), `0` = cancel. `M`/`B`/`X` are globally reserved for Main/Back/Exit and never assigned here.

**Render recipe (Bash, in the agent body):**

```bash
PLUGIN_DEV_STRIPPING_SPEC=$(mktemp -t plugin-creator-strip-dev-spec.XXXXXX.json)
cat > "$PLUGIN_DEV_STRIPPING_SPEC" <<'JSON'
{
  "spec_version": 1,
  "mode": "menu",
  "plugin": "cpv-plugin-creator-agent",
  "slug": "dev-stripping",
  "header": "Dev-stripping (TRDD-793ac32a) — default = (S) Standard",
  "rows": [
    {"key": "S", "action_id": "standard", "label": "Standard (PSS pattern) — one cpv.strip entry for tests/"},
    {"key": "L", "action_id": "legacy",   "label": "Legacy — keep everything in MAIN repo (discouraged)"},
    {"key": "0", "action_id": "cancel",   "label": "Cancel"}
  ],
  "footer": "Type a key:"
}
JSON
uv run python "${CLAUDE_PLUGIN_ROOT}/scripts/cpv_menu.py" "$PLUGIN_DEV_STRIPPING_SPEC"
```

`standard` → pass `--strip-dev` (default) to `generate_plugin_repo.py`, which adds the `cpv.strip` block to plugin.json with ONE extract entry (`tests/`, matching PSS's single-submodule pattern); `cpv strip-dev-parts --auto` reads it later. `legacy` → `--no-strip-dev`. No GitHub repos created at scaffold time; add more extract entries by hand for additional heavy dev folders. Default-ON because new plugins start with the right structure, reviewers see the block from day 1, migration is just `cpv strip-dev-parts` later, and there's zero downside (no submodules until opt-in).

## 7. Gap-detection table + delegation rules

Before running any workflow, read the current state and identify what is missing against the 6-point "deployment-ready" definition in the agent body. Then fill each gap — do NOT stop at the first. Typical gaps and how to resolve:

| Detected gap | Resolution |
|---|---|
| Plugin folder has no `.claude-plugin/plugin.json` | Scaffold with `generate_plugin_repo.py` or `cpv-create-plugin` skill |
| Plugin folder is not a git repo | `git init` + initial commit |
| Plugin folder is a git repo but has no GitHub remote | Ask user for `<owner>` → `gh repo create <owner>/<name> --public --source . --push` |
| **Plugin lives inside a LOCAL-ONLY marketplace** (ancestor within 3 levels has `marketplace.json`, no GitHub remote, relative-path sources) and user wants to publish | Load `cpv-setup-github-marketplace` → its "Local → GitHub Migration" reference. Ask which of the **4 migration paths** fits (lift whole marketplace as Layout B / split into own repos + Layout A hub / publish this plugin only + keep local marketplace for dev / publish this plugin only into a different existing GitHub marketplace). Never decide for the user. |
| **ORPHAN plugin — plugin folder the user wants to install but no marketplace around it** (no ancestor `marketplace.json` within 3 levels AND user's goal is installing, not developing) | Load `cpv-setup-github-marketplace` → its "Orphan Plugin Onboarding" reference. The user likely doesn't know plugins REQUIRE a marketplace — EXPLAIN that first, then ask (via `AskUserQuestion`) which of the **4 hosting paths** fits: (A) names an existing marketplace → just emit `marketplace add` + `install`; (B) new LOCAL marketplace (only `marketplace.json` + README); (C) new GitHub marketplace, agent creates fresh; (D) existing GitHub marketplace the user owns (check `~/.claude/plugins/known_marketplaces.json`). C/D require the FULL pipeline (publish.py + CI + pre-push hook + dispatch receiver + per-plugin notify-marketplace.yml + `MARKETPLACE_PAT` + branch rules on both repos) — never skip it; silently-broken sync chains are worse than missing features. |
| Validation fails (CRITICAL/MAJOR/MINOR/NIT) | Delegate to `cpv-plugin-fixer-agent` agent (`/cpv-fix-validation <report-or-plugin-path>` — the fixer accepts either and runs the validate→fix→re-validate loop until clean) |
| No marketplace specified by the user | Use `AskUserQuestion` — list existing marketplaces from `claude plugin marketplace list` output if available, plus "create a new one" |
| Marketplace specified but doesn't exist on GitHub | Load `cpv-setup-github-marketplace` skill and create it (Layout A by default) |
| Marketplace exists but is missing CI/CD + sync workflow | Load `cpv-setup-github-marketplace` skill → "link existing marketplace" phase; or route to `cpv-marketplace-fixer-agent` agent if validation report has `category: architecture` findings |
| Plugin not yet linked in marketplace.json | Load `cpv-publish-to-marketplace` skill → Phase 1 (configure notification) + Phase 3 (publish bumps version + triggers dispatch that adds the entry) |
| PAT secret missing | Ask user, then call `scripts/set_marketplace_pat.py` (NEVER pipe to `gh secret set`) |
| Marketplace sync workflow failed after push | Investigate the run with `gh run view`, fix, rerun. Do NOT declare victory until `marketplace.json` in the marketplace repo shows the new version |

### When to delegate vs. do it yourself

Delegate CPV-fixable findings (schema, frontmatter, missing sections) to `cpv-plugin-fixer-agent` (don't improvise). Hand off marketplace validation failures + Layout A↔B migration to `cpv-marketplace-fixer-agent` (`/cpv-fix-marketplace-validation <report>` — it owns layout migration + per-plugin auto-notify wiring). Everything else (scaffold, git init, gh repo create, linking, CI templates, PAT wiring, running the publish pipeline) → do yourself with the on-demand skills.

## 8. Path Resolution — skill destination sub-procedure

This expands the Path Resolution Protocol check #7 in the agent body (path P has `SKILL.md` but NO `.claude-plugin/plugin.json`). It is a **skill**, not a plugin — users confuse them. STOP and explain the difference: a plugin = bundle + manifest, installed via a marketplace; a skill = a single `SKILL.md` folder, just dropped into a skills folder, no marketplace. Then `AskUserQuestion` for the destination:

1. **User scope** `~/.claude/skills/<name>/`
2. **Project scope** `<root>/.claude/skills/<name>/` (committed)
3. **Local scope** (same path, gitignored)
4. **Wrap into a plugin**

For destinations 1/2/3: copy the folder (or `ln -s` with user consent) there, then run `validate_skill.py` (or route to `cpv-skill-validation-agent`) and report. For destination 4: fresh scaffold — wrap, move the skill to `<plugin-root>/skills/<name>/`, then enter the deploy flow.

If P has `SKILL.md` AND an ancestor ≤3 levels up has `plugin.json`, it is a skill inside plugin `<name>` — ask whether to work on the plugin or just that skill.

Other Path-Resolution notes: `validate_plugin.py` emits most of the resolution hints on stderr, but do the resolution in the agent FIRST so the user answers inside the conversation in one round-trip. For check #5 (path missing) `Glob`/`ls` P's parent for similarly-named + plugin folders. For check #6 (no `.claude-plugin/` but contains plugin subfolders, scanning ≤3 levels, a subfolder counting if it has `.claude-plugin/plugin.json` or `plugin.json`): list candidates and ask which to work on, or treat P as a new marketplace hub; if exactly one candidate, ask a single "did you mean `<candidate>`?".

## 9. Phase 0 — wrap / add / cancel detail

When shape-detection returns "single skill"/"single agent"/"loose commands"/"unknown folder", refuse to wrap and use shape-detection.md's hard-refusal protocol. The three acceptable next steps:

1. **Wrap into a NEW plugin** — invoke `scripts/cpv_pack_components.py` (the multi-select packer) so components land in the correct subfolders: `skills/<name>/SKILL.md`, `agents/<name>.md`, `commands/<name>.md`, `hooks/<file>.json`, `.mcp.json`, `.lsp.json`, `monitors/<file>.json`, `output-styles/<name>.md`. NEVER just drop a `plugin.json` next to misplaced content. The packer's `--list-only` enumerates what's there for a multi-select prompt. Menu: §3.6.8 in `skills/cpv-main-menu-skill/references/menu-tree.md`.
2. **ADD to an existing plugin** — append via `scripts/add_component.py`.
3. **Cancel.**

The canonical plugin layout, manifest schema, env vars, caching rules, and CLI commands are EMBEDDED VERBATIM in [plugins-reference](../skills/cpv-plugin-validation-skill/references/plugins-reference.md). Read it BEFORE deciding the new plugin's shape.

## 10. Additional worked example

A second worked example complementing the one in the agent body (scaffold-from-scratch as Layout C):

```
user: Create a new plugin "lint-checker" from scratch and ship it as Layout C.
assistant: [generate_plugin_repo.py --self-marketplace → plugin.json + self-entry] [validate: clean] [git init + gh repo create --push] [publish.py --install-hook]
✓ Layout C ready. Run `claude plugin marketplace add owner/lint-checker && claude plugin install lint-checker@lint-checker --scope user`.
```
