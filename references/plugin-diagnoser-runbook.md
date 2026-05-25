# Plugin Diagnoser — full runbook

Detailed step-by-step bodies for the `plugin-diagnoser` agent. The agent
body keeps every phase HEADER plus a 1–3 line summary; the verbose detail
(per-check tables, severity rules, the Phase 9 render recipe, and the
Phase 10 dispatch table) lives here. Read the matching section before
executing a phase.

## Table of Contents

- [Phase 1 — Structural validation](#phase-1--structural-validation)
- [Phase 2 — Security audit (all scanners)](#phase-2--security-audit-all-scanners)
- [Phase 3 — Pipeline-staleness audit](#phase-3--pipeline-staleness-audit)
- [Phase 4 — Cross-platform compliance](#phase-4--cross-platform-compliance)
- [Phase 5 — Marketplace registration check](#phase-5--marketplace-registration-check)
- [Phase 6 — Cached-vs-GitHub sync check](#phase-6--cached-vs-github-sync-check)
- [Phase 6.5 — Branch rules + GitHub Actions hygiene](#phase-65--branch-rules--github-actions-hygiene)
- [Phase 6.7 — Persistent-data-folder + bundled-deps audit](#phase-67--persistent-data-folder--bundled-deps-audit)
- [Phase 7 — Missing / duplicated parts](#phase-7--missing--duplicated-parts)
- [Phase 8 — Write report](#phase-8--write-report)
- [Phase 9 — Follow-up menu (render recipe)](#phase-9--follow-up-menu-render-recipe)
- [Phase 10 — Dispatch on user choice](#phase-10--dispatch-on-user-choice)

## Phase 1 — Structural validation

Run `validate_plugin --strict` via the launcher. Capture the report path
and severity counts. This is the structure baseline.

## Phase 2 — Security audit (all scanners)

Run `validate_security` with all 5 external scanners enabled:
cc-audit, tirith, trufflehog, semgrep, Cisco AI Defense skill-scanner.
On Linux/macOS the scanners auto-install if missing (per
`cpv_install_scanners.py`). On Windows, missing scanners get downgraded
to WARNING.

## Phase 3 — Pipeline-staleness audit

The detection signals + fix recipes for every section below live in
[pipeline-migration.md](../skills/fix-validation/references/pipeline-migration.md)
> §0 — Detect canonical pipeline drift via RC-PIPELINE-DRIFT-001 · §0b — Remove legacy pipeline scripts via RC-LEGACY-PIPELINE-001 · §1 — Fix dangling script references · §2 — Migrate to whole-repo lint via cpv_lint_engine · §3 — Cross-platform Python — bash to Python, os.path to pathlib · §4 — Make publish.py idempotent — interrupted-publish recovery · §5 — Sanitize every script-input parameter against injection

Read that doc and run its per-section detection commands. Two sections are
validator-driven and surfaced from `validate_plugin.py --strict` output:

| Check | How |
|---|---|
| §0 Canonical pipeline drift | surface every `[RC-PIPELINE-DRIFT-001]` finding. Fix path: `/cpv-upgrade-plugin` (dispatches plugin-fixer with `--force-templates`). |
| §0b Legacy pipeline scripts | surface every `[RC-LEGACY-PIPELINE-001]` MINOR finding (bump_version.py, release.sh, lint.sh, compute_hashes.py, …). Fix path: same `/cpv-upgrade-plugin` flow auto-moves them to `scripts_dev/` (files MOVED, never deleted). |
| §3a/§3b/§3c Cross-platform Python | run the §3 detection commands from pipeline-migration.md (shipped `.sh` scripts, bash hook commands via `check_hook_command_cross_platform`, non-pathlib `os.path`/`shell=True`/`/tmp/`/`os.system`). |
| §4 Non-idempotent publish.py | run the §4 presence check from pipeline-migration.md (`_read_remote_version`/`_infer_bump_type`/`_git_porcelain_clean` helpers present = current standard). |
| §5 Unsanitized inputs | per §5: flag argparse flags / env-var reads flowing into `subprocess` / `re.compile` / `urlopen` without an intermediate regex check. |

## Phase 4 — Cross-platform compliance

Cross-platform stacks are Python + Node.js/TypeScript ONLY; classify each
detected language for the report's cross-platform row (apply the §3
"bash → Python is NOT universal" exclusions from pipeline-migration.md before
recommending any conversion):

| Detected language | Cross-platform? | Action |
|---|---|---|
| Python (with pathlib + no `shell=True`) | ✓ | OK |
| TypeScript / JavaScript / Node.js | ✓ | OK |
| Bash / sh | ✗ | Flag — convert to Python via §3a |
| Ruby / Perl / PHP / R | ✗ | Flag — convert or document Windows requirement |
| Go / Rust binaries | platform-conditional | OK if `build-binaries.yml` covers all 3 OS targets |

## Phase 5 — Marketplace registration check

- Check the plugin name against `~/.claude/plugins/known_marketplaces.json`'s registered plugins.
- If a `notify-marketplace.yml` exists in `.github/workflows/`, parse the target marketplace owner/repo.
- Probe: `gh api repos/<owner>/<marketplace>/contents/.claude-plugin/marketplace.json` to verify the plugin is listed.
- Probe: `gh secret list --repo <owner>/<plugin>` to verify `MARKETPLACE_PAT` exists.
- Report any mismatch (plugin not listed / wrong source / no PAT / no notify workflow).

## Phase 6 — Cached-vs-GitHub sync check

When the plugin path is under `~/.claude/plugins/cache/`, parse the
version from `plugin.json` and compare to:
- The latest tag from `gh api repos/<owner>/<plugin>/releases/latest`.

If the cache is older than the latest release, report the gap with
the exact `claude plugin update <name>@<marketplace>` command.

## Phase 6.5 — Branch rules + GitHub Actions hygiene

For BOTH the plugin repo AND its marketplace repo (when one is found),
check:

| Check | How |
|---|---|
| Branch protection ruleset present | `gh api repos/<owner>/<repo>/rulesets` — list all; verify the `cpv-branch-rules` (or equivalent) ruleset is `enforcement: active` and targets the default branch. |
| Required status checks | The ruleset's `required_status_checks` array MUST include the actual check-run names CI emits (compare to `gh api repos/<owner>/<repo>/commits/HEAD/check-runs`). Mismatched names mean the rule never blocks merges. |
| Bypass actors not over-privileged | The `bypass_actors` list MUST contain ONLY admin role + a small allowlist (Dependabot, Renovate, plugin-author bots). Flag any user-account bypass actors as a SECURITY issue. |
| Bot conflicts | Detect two bots both with auto-merge permissions on overlapping PRs (e.g. dependabot + renovate without conflict resolution). Flag MAJOR. |
| `MARKETPLACE_PAT` secret present | Probe `gh api repos/<owner>/<plugin>/actions/secrets --jq '.secrets[].name'` — emit MAJOR if `MARKETPLACE_PAT` is missing AND the plugin has `.github/workflows/notify-marketplace.yml`. The remediation flow (Phase 10 row 6) reads `$PAT_MARKETPLACE` (or `$MARKETPLACE_PAT` for back-compat); when neither env var is set, the doctor asks the user `Which env var holds your PAT? (e.g. PAT_MARKETPLACE, GITHUB_PAT)` and passes the answer to `set_marketplace_pat.py --env-var <NAME>`. The script feeds the PAT to `gh secret set` via stdin (`--body-file -`) — never argv — so the value never leaks via `/proc/<pid>/cmdline`. |
| `MARKETPLACE_PAT` scopes | Cannot probe scopes server-side; if the last `notify-marketplace.yml` run failed with "Bad credentials" emit MAJOR with the rotate-PAT hint. |
| Other CI hygiene | Last 5 workflow runs status (`gh run list --limit 5 --json status,conclusion,name,workflowName`); flag any workflow whose last 3 runs all failed (likely broken). Flag workflows that have NEVER run (probably misconfigured trigger). Compare each `.github/workflows/*.yml` against `actions/checkout` / `setup-python` / `setup-node` latest versions and flag MINOR if a major version is behind. |
| Claude action present | Look for `.github/workflows/*.yml` containing `anthropics/claude-code-action@`. If found, parse the version pin and compare to `gh api repos/anthropics/claude-code-action/releases/latest`. |
| Claude action up-to-date | If the pinned version is more than 2 minor versions behind latest, emit MINOR with the upgrade command. If the action is unpinned (no SHA pin), emit MAJOR (security — tag rewrite vector). |
| Claude action setup complete | If the action is referenced but `secrets.ANTHROPIC_API_KEY` (or `secrets.CLAUDE_CODE_OAUTH_TOKEN` for the OAuth flow) is not set on the repo, emit MAJOR. |
| Claude action YAML hygiene | Validate the workflow's `permissions:` block (must be `contents: read` minimum + only the scopes the action documents); flag overly-broad scopes. |

For the marketplace repo, repeat all checks above PLUS:
- Verify `update-submodules.yml` (or `update-plugins.yml`) receiver workflow exists and listens for `repository_dispatch: plugin-updated`.
- Verify the receiver workflow has `permissions: contents: write, pull-requests: write` (or runs as a PR bot with the right scopes).

Severity rules:
- Bypass actor that is a user account (not a bot/role) → CRITICAL.
- Unpinned third-party action → MAJOR.
- Outdated Claude action by ≥3 minor versions → MAJOR.
- Outdated Claude action by 1–2 minor versions → MINOR.
- Missing required status checks → MAJOR (rule is non-enforcing).
- `MARKETPLACE_PAT` missing on a plugin that ships `notify-marketplace.yml` → MAJOR.

## Phase 6.7 — Persistent-data-folder + bundled-deps audit

Plugins must use `${CLAUDE_PLUGIN_DATA}` for runtime mutable state
(installed deps, caches, generated files). `${CLAUDE_PLUGIN_ROOT}`
is replaced wholesale on every plugin update — anything written there
is lost. Reference: <https://code.claude.com/docs/en/plugins-reference>.

| Check | How |
|---|---|
| Bundled `node_modules/` shipped at plugin root | List `<plugin-root>/node_modules` — if it exists AND `.git` is absent (= packaged install, not dev checkout), emit MAJOR. Already-on-disk validator: `validate_plugin.py` line ~2918. The fix is a SessionStart hook that runs `npm install --prefix "$CLAUDE_PLUGIN_DATA"` once per session. |
| Bundled `.venv/`, `venv/`, `vendor/`, `__pypackages__/` | Same rule as node_modules — language-agnostic. MAJOR. |
| `package.json` / `package-lock.json` present without a SessionStart hook | Grep `hooks/hooks.json` for an `event: SessionStart` block whose `command` invokes `npm ci`, `npm install`, `pnpm install`, `bun install`, or `yarn install` AND targets `$CLAUDE_PLUGIN_DATA`. If absent, emit WARNING with the canonical hook recipe (see plugins-reference pointer below). |
| `pyproject.toml` / `requirements.txt` present without a SessionStart hook | Same — look for `pip install --target $CLAUDE_PLUGIN_DATA/...` or `uv sync --project $CLAUDE_PLUGIN_DATA/...`. Emit WARNING. |
| `Cargo.toml` / `go.mod` present without a SessionStart hook | Same — `cargo build --target-dir $CLAUDE_PLUGIN_DATA/...` or `go install GOPATH=$CLAUDE_PLUGIN_DATA/go ...`. Emit WARNING. |
| Code references `${CLAUDE_PLUGIN_ROOT}/node_modules/` | Grep `scripts/`, `hooks/`, agents, skills, commands for the literal substring `${CLAUDE_PLUGIN_ROOT}/node_modules`. Emit MAJOR — must be `${CLAUDE_PLUGIN_DATA}/node_modules`. Same rule for `.venv`, `venv`, `vendor`. |
| Code writes to `${CLAUDE_PLUGIN_ROOT}/...` for mutable state | Grep for `>` / `Path(...).write_text` / `open(..., "w")` / `fs.writeFileSync` whose target path starts with `${CLAUDE_PLUGIN_ROOT}/`. The validator already catches this in `validate_hook.py::check_hook_command_cross_platform` (CRITICAL `_PD_HOOK_WRITE_ROOT_RE`). Surface those CRITICALs verbatim in the report. |

The canonical SessionStart install-hook recipe (the `diff`-guarded
`npm install` into `${CLAUDE_PLUGIN_DATA}`, plus the `NODE_PATH` wiring and
the Python/uv equivalent) lives in the "Environment variables" section of
[plugins-reference](../skills/plugin-validation-skill/references/plugins-reference.md).
Quote that snippet into the report when a manifest declares deps but ships no
installer hook — do not invent a different recipe.

Severity rules (Phase 6.7):
- Bundled `node_modules/` (or any other dep dir) inside packaged install → MAJOR.
- Code references `${CLAUDE_PLUGIN_ROOT}/<dep-dir>/` → MAJOR.
- Code writes mutable state to `${CLAUDE_PLUGIN_ROOT}/...` → CRITICAL (already a CPV CRITICAL via `validate_hook`).
- Manifest declares deps but no SessionStart installer hook → WARNING (advisory; some plugins legitimately bundle small `.js` shims that aren't `node_modules`).

## Phase 7 — Missing / duplicated parts

Scan for:
- Same skill name in two folders.
- Same MCP server in `.mcp.json` AND inline `plugin.json:mcpServers`.
- Same LSP server name in two sources.
- Hook handler script that doesn't exist on disk.
- Agent / skill / command file with empty frontmatter.

## Phase 8 — Write report

Write a structured Markdown report to:
`$MAIN_ROOT/reports/plugin-diagnoser/<YYYYMMDD_HHMMSS±HHMM>-<plugin-name>.md`

The report has 8 sections (one per phase) plus a top-of-document
summary table:

| Phase | Severity | Findings |
|---|---|---|
| Structure | CRITICAL=… MAJOR=… MINOR=… NIT=… | … |
| Security  | CRITICAL=… MAJOR=… | scanner counts + which scanner caught what |
| Pipeline  | MAJOR=… (per §) | which migrations apply |
| Cross-platform | MAJOR=… MINOR=… | bash/sh count, posix-tool count, os.path count |
| Marketplace | n/a or MAJOR | registered? notify wired? PAT set? |
| Branch+Actions | CRITICAL/MAJOR/MINOR | branch ruleset, bypass actors, Claude action version |
| Sync | n/a or INFO | cache version vs latest tag |
| Duplicates | MAJOR=… | duplicate counts |

## Phase 9 — Follow-up menu (render recipe)

After writing the report, render this menu via the claude-menu-system bridge
(`scripts/cpv_menu.py`) and end the turn immediately. The user's next-turn
reply is routed through the FIXED letter→action map below — NEVER inspect the
rendered menu to decide what a key means.

**Fixed letter→action map (immutable, per TRDD-4de479a0 FIXED-KEY contract):**

| Key | action_id            | Label                                                                                       |
|-----|----------------------|---------------------------------------------------------------------------------------------|
| F   | full_upgrade         | Full upgrade to current standards (every migration that applies, including WARNINGs)        |
| C   | critical_only        | Apply only CRITICAL fixes (publish-blockers + security blockers)                            |
| J   | major_plus_critical  | Apply MAJOR + CRITICAL (publish-blockers, security, cross-platform issues)                  |
| R   | register_marketplace | Register / create marketplace for this plugin                                               |
| S   | sync_cache           | Sync cache from GitHub (run `claude plugin update`)                                         |
| G   | github_branch_rules  | Fix branch rules + Claude action setup (server-side ruleset, bypass actors, action version) |
| D   | rediagnose           | Re-diagnose after manual fixes                                                              |
| 0   | end                  | End                                                                                         |

Letter rationale (first free letter of the action name): `F` **F**ull,
`C` **C**RITICAL, `J` ma**J**or (`M` is the reserved Main-menu nav key),
`R` **R**egister, `S` **S**ync, `G` **G**ithub branch rules (`B` is the
reserved Back nav key), `D` re-**D**iagnose, `0` the CMS end key. `M`/`B`/`X`
are globally reserved for Main/Back/Exit and never assigned here.

**Render recipe (Bash, in the agent body):**

```bash
PLUGIN_DIAGNOSER_PHASE9_SPEC=$(mktemp -t plugin-diagnoser-phase9-spec.XXXXXX.json)
cat > "$PLUGIN_DIAGNOSER_PHASE9_SPEC" <<'JSON'
{
  "spec_version": 1,
  "mode": "menu",
  "plugin": "plugin-diagnoser",
  "slug": "phase9-followup",
  "header": "Diagnosis complete — pick a follow-up action",
  "rows": [
    {"key": "F", "action_id": "full_upgrade",         "label": "Full upgrade to current standards (every migration that applies, including WARNINGs)"},
    {"key": "C", "action_id": "critical_only",        "label": "Apply only CRITICAL fixes (publish-blockers + security blockers)"},
    {"key": "J", "action_id": "major_plus_critical",  "label": "Apply MAJOR + CRITICAL (publish-blockers, security, cross-platform issues)"},
    {"key": "R", "action_id": "register_marketplace", "label": "Register / create marketplace for this plugin"},
    {"key": "S", "action_id": "sync_cache",           "label": "Sync cache from GitHub (run claude plugin update)"},
    {"key": "G", "action_id": "github_branch_rules",  "label": "Fix branch rules + Claude action setup (ruleset, bypass actors, action version pin)"},
    {"key": "D", "action_id": "rediagnose",           "label": "Re-diagnose after manual fixes"},
    {"key": "0", "action_id": "end",                  "label": "End"}
  ],
  "footer": "Type a key:"
}
JSON
uv run python "${CLAUDE_PLUGIN_ROOT}/scripts/cpv_menu.py" "$PLUGIN_DIAGNOSER_PHASE9_SPEC"
```

End the turn immediately after this call. NEVER print this menu inline.

## Phase 10 — Dispatch on user choice

- **F (`full_upgrade`)** → dispatch **plugin-fixer** with `min_severity=WARNING` AND a prompt that explicitly asks for pipeline-migration §1–§5 to run first.
- **C (`critical_only`)** → dispatch **plugin-fixer** with `min_severity=CRITICAL` AND the same pipeline-migration §1–§5 prompt.
- **J (`major_plus_critical`)** → dispatch **plugin-fixer** with `min_severity=MAJOR` AND the same pipeline-migration §1–§5 prompt.
- **R (`register_marketplace`)** → dispatch **plugin-creator** in marketplace-mode (orphan plugin path) — interactive interrogation about marketplace target.
- **S (`sync_cache`)** → ask the user "Run `claude plugin update <name>@<marketplace>` now? (yes/no)". On yes, run it. On no, print the command for the user to copy.
- **G (`github_branch_rules`)** → fix branch rules + Claude action + secrets:
  - **(a)** confirm the user wants to (re)apply the `cpv-branch-rules` ruleset → run `cpv-setup-branch-rules-generic <owner>/<repo>` interactively;
  - **(b)** if Claude action is unpinned or outdated, propose the SHA-pinned latest version via pinact;
  - **(c)** if `ANTHROPIC_API_KEY` / `CLAUDE_CODE_OAUTH_TOKEN` is missing, instruct the user to set it (never automate secret creation — interactive only);
  - **(d) `MARKETPLACE_PAT` setup (when missing on a plugin shipping `notify-marketplace.yml`)**:
    1. Try `os.environ.get("PAT_MARKETPLACE")` → if set, skip to step 4.
    2. Try `os.environ.get("MARKETPLACE_PAT")` (legacy fallback) → if set, skip to step 4.
    3. Ask the user **in plain text** (NEVER `AskUserQuestion`):
       `Which environment variable holds your GitHub PAT? (e.g. PAT_MARKETPLACE, GITHUB_PAT, GH_PAT)`. Read the answer; if blank, abort the (d) sub-step with a clear message and continue to (a)/(b)/(c).
    4. Run:
       ```bash
       uv run python "${CLAUDE_PLUGIN_ROOT}/scripts/set_marketplace_pat.py" \
         --env-var "<NAME>" "<owner>/<plugin>"
       ```
       The helper reads `os.environ[<NAME>]` and feeds the value to
       `gh secret set MARKETPLACE_PAT --repo <owner>/<plugin> --body-file -` via stdin —
       the PAT never appears in argv (no `/proc/<pid>/cmdline` leak), never in stderr/stdout
       (only the byte-length is printed), and trailing newlines from copy-paste are
       rejected up-front.
    5. On exit code 0 → secret set + verified; on non-zero → surface stderr verbatim
       and re-print the diagnoser's Phase 9 menu so the user can pick a different action.
- **D (`rediagnose`)** → re-run this whole agent (recursive on the same path).
- **0 (`end`)** → reply `Done.` and stop.
