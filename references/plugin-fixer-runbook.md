# Plugin-fixer runbook — extracted detail

Detailed, load-on-demand reference for the `plugin-fixer` agent. The agent
body (`agents/plugin-fixer.md`) keeps the core triage / loop / completion-gate
contract inline and points here for the long step-by-step procedures.

## Table of Contents

- [1. Pre-completion verification (REQUIRED, migration runs)](#1-pre-completion-verification-required-migration-runs)
- [2. Pipeline migration to current standards (legacy plugin upgrade)](#2-pipeline-migration-to-current-standards-legacy-plugin-upgrade)
- [3. Special class: runtime-dep and invocation hook issues (TRDD-0028dd34)](#3-special-class-runtime-dep-and-invocation-hook-issues-trdd-0028dd34)
- [4. CRITICAL: Never improvise `gh secret set`](#4-critical-never-improvise-gh-secret-set)
- [5. MCP bundling & empirical loading footguns](#5-mcp-bundling--empirical-loading-footguns)
- [6. Marketplace upstream cross-check gate (TRDD-c0ee9543 Phase F)](#6-marketplace-upstream-cross-check-gate-trdd-c0ee9543-phase-f)
- [7. RC-GHOST-DISPATCH-* (TRDD-25b9be90 — ghost-agent dispatch)](#7-rc-ghost-dispatch--trdd-25b9be90--ghost-agent-dispatch)
- [8. Optional `min_severity` parameter (post-validate menu integration)](#8-optional-min_severity-parameter-post-validate-menu-integration)
- [9. Input handling (post-menu dispatch — NO First Contact menu)](#9-input-handling-post-menu-dispatch--no-first-contact-menu)
- [10. Fix Guides & routing](#10-fix-guides--routing)
- [11. Preservation guardrails — detail](#11-preservation-guardrails--detail)
- [12. Phase 0.5 triage — evidence + safe-ceiling detail](#12-phase-05-triage--evidence--safe-ceiling-detail)
- [13. Launcher aliases (remote_validation.py)](#13-launcher-aliases-remote_validationpy)

## 1. Pre-completion verification (REQUIRED, migration runs)

**Mandatory for every canonical-pipeline migration run.** Skipping any step
violates the Migration exit contract ([issue #21 ask #1](https://github.com/Emasoft/claude-plugins-validation/issues/21)).
The authoritative reference is the 82-check matrix in
[`references/canonical-pipeline-migration-checklist.md`](canonical-pipeline-migration-checklist.md)
— 82 checks across 16 categories (workflow YAML integrity, Python source
quality, hook shape, publish.py, plugin.json, .gitignore, CPV self-validate,
canonical-template parity, tests, git state, smoke-test publish, marketplace,
notification chain, hooks.json, MCP servers, docs & changelog). Read that file
in full before running step 7c the first time on any plugin. Do NOT reproduce
the matrix here or in the agent body — it lives in that one file only.

Run these, in order, with `cwd` = plugin root:

1. **Run the 82-check matrix.** Extract `run_all_checks` from the checklist
   (`awk '/^### run_all_checks$/,/^### END_RUN_ALL$/' "$CHECKLIST" | sed '1d;$d' > /tmp/run_all_checks.sh`),
   `source` it (plus the plugin's `run_migration_checks.sh` under `scripts/`
   if present), then `run_all_checks "$PWD"`. It writes a Unicode-bordered
   table to `$MAIN_ROOT/reports/canonical-pipeline-migration/<ts±tz>-run-all.md`
   and **exits 0 only if every BLOCKER + MAJOR passes**. If run_all_checks
   does NOT exit 0: print `[PARTIAL]`, surface the failed CHECK-NN list with
   file:line, and STOP — do NOT proceed to publish, do NOT silently
   `--force-templates`.
2. **Smoke-test publish (zero side-effects):** `uv run python scripts/publish.py --print-gates`
   then `--dry-run` (both exit 0 if argparse + imports + full pipeline parse).
   Catches "publish.py exists but is broken" failures the validator cannot see.
3. **Real publish + CI watch (the actual exit gate):** `uv run python scripts/publish.py --patch`
   (bumps + commits + pushes the tag), capture the tag, then
   `gh run watch <run_id> --exit-status`. On non-zero, print `[PARTIAL]` with
   the failing job's log URL and exit.
4. **Conditional marketplace gate:** if `.claude-plugin/marketplace.json` is at
   the plugin root (Layout C), the same publish.py already bumped both
   manifests → one tag covers both. Otherwise (Layout A) locate the upstream
   marketplace via plugin.json:repository or the registered list, cd there, and
   repeat steps 2 + 3.

SUCCESS for a migration requires step 7 clean AND **run_all_checks returns exit
0** AND `gh run watch` success on every tag.

**Do NOT silently `--force-templates` when checks fail.** Present the per-CHECK
failures and ask the user (via AskUserQuestion — **never auto-pick**):

| Option | What happens |
|--------|--------------|
| (a) Fix manually | Surface the exact CHECK-NN failures with file:line; wait for the user to fix and re-invoke. |
| (b) Re-run with `--force-templates` | Rerun `standardize_plugin.py . --fix --force-templates`, re-enter the loop. **EXPLICIT WARNING required:** hand-tuned customisations to canonical files (publish.py, ci.yml, pre-push, cliff.toml) will be **overwritten/lost**. Show a `git diff` preview of every drifted file FIRST. |
| (c) Abort | Return `[PARTIAL]` with the run-all log path; leave the plugin as-is (no rollback). |

Show the run_all_checks Unicode-bordered table to the user as part of the
completion report — it is the source of truth; do not summarise it in prose.

## 2. Pipeline migration to current standards (legacy plugin upgrade)

When the user asks "fix/upgrade the pipeline" / "match the latest CPV pipeline",
load `fix-validation`'s `pipeline-migration.md` reference (linked with its full
TOC in Guardrail 2 of the agent body) and apply its independent, revertable
migrations: §1 stale script refs (→ cpv_lint_engine in CI), §2 whole-repo lint
via cpv_lint_engine, §3a/§3b/§3c bash→Python scripts/hook-commands/os.path→pathlib,
§3 idempotent publish.py (the 5 `_read_remote_version`-style helpers), §5
sanitize every input parameter (boundary regex; reject traversal/unsafe URLs;
NEVER `shell=True`). The full detection signals + fix tables live in that
reference — read the relevant section, do not reproduce it here. This is the
legacy validator-only check — the migration is **NOT complete** until §1
(Pre-completion verification) above also passes.

## 3. Special class: runtime-dep and invocation hook issues (TRDD-0028dd34)

A finding whose message references runtime-dep / PEP 723 / venv /
module-scope-`sys.exit` / `unset VIRTUAL_ENV` / HTTP-hook-timeout /
`..`-escapes-root phrasing is a RUNTIME-DEP issue, fixed by changing the
INVOCATION method, NOT the script's logic. Read **hook-fixes.md §13** (a
subsection per diagnostic + §13.9 edge-case matrix) for the exact
trigger-phrase list and recipes. Critical rule: **preserve the hook's effective
behavior** — don't delete it, don't mute with `|| true`/`2>/dev/null`, don't
strip third-party imports unless a genuine stdlib alternative exists. The fix is
almost always one of: (1) change the command to `uv run --quiet --script` + add
a `# /// script` PEP 723 block; (2) add a SessionStart hook that sets up
`${CLAUDE_PLUGIN_DATA}/.venv`; (3) move a module-scope `sys.exit` into
`if __name__ == '__main__':` or raise `ImportError`; (4) add `"async": true` to
an HTTP hook on a latency-sensitive event. Never substitute `uvx` for
`uv run --script` — `uvx` cannot target a local `.py` (§13.1).

## 4. CRITICAL: Never improvise `gh secret set`

If a fix touches `MARKETPLACE_PAT` (rare for plugin-scope — usually routed to
marketplace-fixer), use the helper `scripts/set_marketplace_pat.py` (it never
prints the token, so it cannot leak into the transcript, shell history, or
logs): `uv run python scripts/set_marketplace_pat.py OWNER/repo-a OWNER/repo-b`.
Manual fallback ONLY if the helper is unavailable — value via `--body`/`-b`,
never stdin/pipe: `gh secret set MARKETPLACE_PAT --repo OWNER/REPO --body "$MARKETPLACE_PAT" >/dev/null`.
Reject on sight (they inject a trailing newline → `Bad credentials`/401):
`echo ... | gh secret set`, `gh secret set ... <<< ...`, `printf ... | gh secret set`,
any stdin-driven form without `--body`/`-b`.

## 5. MCP bundling & empirical loading footguns

When adding/relocating bundled MCP server executables, prefer **`servers/`** at
the plugin root ([official docs](https://code.claude.com/docs/en/plugins-reference#mcp-servers));
reference as `${CLAUDE_PLUGIN_ROOT}/servers/<name>` — never bare relative paths;
never relocate a working server with a predefined path (`bin/`, `src/servers/`).
Server/LSP names must be unique across all declaration sources (`.mcp.json`,
inline `plugin.json:mcpServers`, path-string `mcpServers`); on a
`"declared in both"` MAJOR remove the duplicate from one source (prefer inline
`plugin.json`).

For the silent-failure loading footguns `claude plugin validate` does NOT catch
— `Field 'agents' contains folder path`, `Field 'hooks' points to
'./hooks/hooks.json' ... DISABLES this plugin's MCP servers`, `mcpServers`
pointing at auto-discovered `.mcp.json`, cross-source duplicate MCP/LSP servers
— apply the recipes in the `fix-validation` references `plugin-structure-fixes.md`,
`mcp-fixes.md`, and `lsp-fixes.md`. Full empirical evidence (13 scenarios,
debug-log excerpts, runtime probes) is in `empirical-loading-bugs.md`.

## 6. Marketplace upstream cross-check gate (TRDD-c0ee9543 Phase F)

This gate is part of the agent body's **Completion gate**. When the plugin is
registered in any marketplace.json (sibling Layout A hub, Layout C
self-marketplace, Layout B parent monorepo), ALSO run:

```bash
uv run python scripts/validate_marketplace.py <marketplace-path> --strict
```

and confirm exit 0 with no `RC-MKPL-NAME-MISMATCH`, `RC-MKPL-UNKNOWN-FIELD`, or
`RC-MKPL-UNKNOWN-SOURCE-FIELD` — these three block install (2026-05-11
ai-maestro-visual-communicator-plugin incident: mismatched name → "not found";
unknown field → `claude plugin validate` rejects the entry). For any surviving
RC-MKPL-* MAJOR, apply §1/§3/§4 of
[marketplace-upstream-drift.md](../skills/fix-validation/references/marketplace-upstream-drift.md):

> 1. Name mismatch — RC-MKPL-NAME-MISMATCH · 2. Version drift — RC-MKPL-VERSION-DRIFT · 3. Unknown entry field — RC-MKPL-UNKNOWN-FIELD · 4. Unknown source sub-field — RC-MKPL-UNKNOWN-SOURCE-FIELD · 5. Source unreachable — RC-MKPL-UPSTREAM-UNREACHABLE · 6. Description / author / keywords drift — RC-MKPL-METADATA-DRIFT · 7. Per-batch bulk align — consolidated marketplace patch · 8. Opt-out flags — when drift IS intentional

If the drift is intentional (brand-vs-canonical alias), add
`"_cpv_skip_upstream_check": true` — but ONLY after asking the user to confirm
the alias is documented in the README. **Agent-introduced drift WITHOUT user
confirmation is forbidden** (TRDD-c0ee9543 §9): the gate must distinguish
user-blessed drift (opt-out present) from agent-introduced drift (no opt-out)
and refuse to ship the latter. Full code table:
[marketplace-error-index.md §1.1](../skills/fix-validation/references/marketplace-error-index.md#11-rc-mkpl-upstream-cross-validation-codes-v2810).

## 7. RC-GHOST-DISPATCH-* (TRDD-25b9be90 — ghost-agent dispatch)

- **001 (CRITICAL)** — `Task()`/`subagent_type:` literal names a non-existent
  agent. Fix: correct the name to one that exists (plugin `agents/`, a built-in
  `general-purpose`/`Explore`/`Plan`/`statusline-setup`, or — for user-scope
  content — `~/.claude/agents/`), OR delete the dispatch if no longer needed.
  NEVER suppress without fixing — the bug is silent at runtime.
- **002 (MINOR)** — dynamic `subagent_type=<var>`. Informational; leave in place
  when the dispatch is intentionally dynamic.
- **003 (NIT)** — cross-plugin `<other-plugin>:<agent>` reference. Not
  statically verifiable; leave unless the target plugin was removed.

Full resolution algorithm + built-in allow-list:
[references/finding-codes.md](finding-codes.md).

## 8. Optional `min_severity` parameter (post-validate menu integration)

When the prompt includes a line like `min_severity=MAJOR (publish-blockers
only).`, filter findings BEFORE fixing: skip any below the threshold. Ranking
(high→low): `CRITICAL`(5, loader/security blockers), `MAJOR`(4, publish-blockers),
`MINOR`(3, quality), `NIT`(2, cosmetic), `WARNING`(1, advisory). Accepts
`WARNING`/`NIT`/`MINOR`/`MAJOR`/`CRITICAL`. No `min_severity` → default: fix every
CRITICAL/MAJOR/MINOR/NIT, evaluate WARNINGs. After a filtered run, the report
MUST list (1) findings fixed per severity, (2) findings SKIPPED below threshold,
(3) the threshold applied — so a follow-up run at a lower threshold picks up the
residue without re-validating.

## 9. Input handling (post-menu dispatch — NO First Contact menu)

This agent is dispatched after the user picked a target via the menu; it does
NOT render a menu (that belongs to the dispatching menu). The prompt carries a
`<context>` block:

```
<context>
source: cpv-fix-validation menu
user_choice: <integer or "manual">
target_path: <absolute path to a report .md OR a plugin folder>
optional_min_severity: <if forwarded>
</context>
```

Detect the `target_path` kind:

- `.md`/`.json` that exists and contains CPV severity markers (`[MAJOR]`,
  `SUMMARY: CRITICAL=`) → **report mode**: pick up the findings, fix, re-validate
  the plugin the report points at.
- Directory → **plugin mode**: run the Path Resolution Protocol (parent/skill/`.claude/`/cache
  folders, typos, missing git), confirm the resolved root via a plain-text
  question (NEVER AskUserQuestion), then enter the loop.
- Missing/invalid → offer candidates from the parent directory.

If invoked DIRECTLY (no `<context>`, no path), return one line asking the caller
to invoke `/cpv-fix-validation` so the menu handles path discovery. Do NOT render
a menu yourself.

## 10. Fix Guides & routing

This agent fixes **plugin-level** issues only. Route each finding:

- **Plugin mechanical fixes** (CRITICAL/MAJOR/MINOR/NIT on plugin files —
  missing fields, malformed JSON, typos, encoding, stale refs, hooks, metadata)
  → `fix-validation` skill's `plugin-error-index.md` (covers
  `validate_plugin/skill*/hook/agent/command/mcp/lsp/security/rules/xref/settings_marketplace/documentation/encoding/enterprise/scoring`).
  Read only the relevant index section, then open the specific fix reference it
  points to; never load whole reference files. Apply Edit operations.
- **Marketplace findings** (any `validate_marketplace.py`/`validate_marketplace_pipeline.py`
  report, or `category: architecture`) → STOP and redirect: "This report
  contains marketplace-level findings. I only fix plugin issues. Please invoke
  the **marketplace-fixer** agent (via `/cpv-fix-marketplace-validation <report>`)
  — it handles mechanical marketplace fixes AND architectural Layout A ↔ B
  migration." Do NOT attempt marketplace fixes or migrations here.

For RC-MKPL-* findings see the `marketplace-upstream-drift.md` reference (8
sections covering every RC-MKPL-* code + the opt-out matrix; its full TOC is in
§6 above).

## 11. Preservation guardrails — detail

Two destructive shortcuts are FORBIDDEN; both caused real damage on prior runs.
The agent body keeps the headings + core directives inline; the full decision
procedure is here.

**Guardrail 1 — never blindly purge "dead" code or "orphan" .md files.** Before
deleting any script/function/.md the validator flagged as unreferenced, ask in
order: (1) **Truly redundant?** Re-read it in full; a regex "no callers"
detector misses dynamic imports, `hooks/hooks.json` references, glob loaders, and
.md references. If you cannot prove it unreachable from EVERY entry point
(publish, hooks, agents, commands, MCP, validators), it is NOT safely dead.
(2) **Just misplaced?** Often the file should live elsewhere (a script in a
skill's `scripts/`, an agent in `agents/`, an MCP stub in `servers/<name>/`) —
suggest relocation, not deletion. (3) **Could it become a feature with
adaptation?** If useful but ill-fitting, propose adapting it and ASK before
deleting. The same three questions apply to .md files (a "dead" doc may be an
intentional draft/TODO/vendor copy).

**Guardrail 2 — bash → Python conversion is NOT universal.** The migration in
[pipeline-migration §3](../skills/fix-validation/references/pipeline-migration.md)
is the DEFAULT for canonical pipeline files (publish.py, pre-push hook, CI
workflows), NOT for every bash file. Before converting any `.sh`, check:
(1) **Bash-specific tooling** (here-docs, `set -o pipefail`, `trap`,
`while IFS= read -r`, process substitution, named pipes) — a Python rewrite
loses functionality or balloons; leave it with a `# windows-incompatible`
comment the validator recognises. (2) **Bash-teaching skills/examples** — keep
`.sh` examples intact; the "bash hook constructs" rule targets HOOK COMMANDS,
not code fenced inside .md docs. (3) **Plugin author intent** — if the
README/CHANGELOG markets a bash-tooling plugin, surface bash as INFO, not MAJOR.
When unclear, return `[BLOCKED]` and ASK — never "convert everything just in
case".

`pipeline-migration.md` §-TOC: §0 — Detect canonical pipeline drift via
RC-PIPELINE-DRIFT-001 · §0b — Remove legacy pipeline scripts via
RC-LEGACY-PIPELINE-001 · §1 — Fix dangling script references · §2 — Migrate to
whole-repo lint via cpv_lint_engine · §3 — Cross-platform Python — bash to
Python, os.path to pathlib · §4 — Make publish.py idempotent —
interrupted-publish recovery · §5 — Sanitize every script-input parameter
against injection.

## 12. Phase 0.5 triage — evidence + safe-ceiling detail

The agent body keeps the Phase 0.5 routing table + the `[BATCH_REQUIRED]` exit
block inline. This section holds the evidence-gathering command and the full
safe-ceiling derivation table.

**Step 1 — Gather evidence** (one validate call via the launcher):

```bash
CLAUDE_PRIVATE_USERNAMES="$(whoami)" uv run --with pyyaml \
  python "${CLAUDE_PLUGIN_ROOT}/scripts/remote_validation.py" \
  plugin <plugin-root> --json --strict > /tmp/triage-report.json
```

Compute `total_findings = counts.critical + counts.major + counts.minor + counts.nit`
and the per-level `severity_mix`. **NIT must be included**: the triage call
above is `--strict`, under which NIT blocks (`validate_plugin.py` returns
`EXIT_NIT`=4 via `exit_code_strict`), and the completion gate requires `NIT=0`.
Excluding NIT here would compute `total_findings=0` for a NIT-only plugin,
route it to Situation 1, and return `[DONE] clean` with blocking NITs unfixed.
Only WARNING is excluded (it never blocks, even under `--strict`).

**Step 2 — Compute the safe-ceiling** from this agent's `model:` frontmatter
(absent = inherits the session model's window):

| `model:` | Raw window | Safe (~50%) | Findings/run @ 3-5K each |
|----------|-----------|-------------|--------------------------|
| `opus` / `sonnet` (bare) | 200K | ~100K | **15-25** |
| `opus[1m]` / `sonnet[1m]` | 1M | ~500K | **50-75** |
| future models | varies | varies | `(window/2)/per_finding` |

v2.98.0 lowered the ceilings (bare 30-40 → 15-25, 1m 100-150 → 50-75) so batch
mode kicks in earlier, giving each shard fixer more headroom. Override via
`--shard-size` on `/cpv-batch-fix`.

## 13. Launcher aliases (remote_validation.py)

NEVER call `validate_*.py` directly from the cache (the environment-isolation
guard refuses with a "remote location" error). ALWAYS go through the launcher:

```bash
uv run --with pyyaml python "${CLAUDE_PLUGIN_ROOT}/scripts/remote_validation.py" <alias> <args>
```

Valid `<alias>` values: `plugin`, `skill`, `hook`, `agent`, `command`, `mcp`,
`lsp`, `marketplace`, `security`, `cache`, `xref`, `docs`, `encoding`, `rules`,
`enterprise`, `scoring`, `lint`, `local-scope`, `project-scope`.
