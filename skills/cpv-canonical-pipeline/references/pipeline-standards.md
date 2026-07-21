# Pipeline standards (current)

## Table of Contents

- [Overview](#overview)
- [Whole-repo lint via cpv_lint_engine](#whole-repo-lint-via-cpv_lint_engine)
- [Idempotent publish.py](#idempotent-publishpy)
- [validate_pipeline_script_refs rule](#validate_pipeline_script_refs-rule)
- [Cross-platform scripts — no bash, no jq/sed/awk](#cross-platform-scripts--no-bash-no-jqsedawk)
- [Input sanitization — every script parameter](#input-sanitization--every-script-parameter)
- [Hooks MUST persist state in CLAUDE_PLUGIN_DATA, never CLAUDE_PLUGIN_ROOT](#hooks-must-persist-state-in-claude_plugin_data-never-claude_plugin_root)
- [Hook commands MUST be cross-platform (Python-delegated)](#hook-commands-must-be-cross-platform-python-delegated)
- [PEP 723 scripts MUST be invoked via uv run](#pep-723-scripts-must-be-invoked-via-uv-run)
- [Migrating a legacy plugin](#migrating-a-legacy-plugin)

## Overview

Every plugin scaffolded by generate_plugin_repo.py ships with three
guarantees baked in. The cpv-plugin-fixer-agent agent migrates legacy plugins to
the same guarantees via /cpv-fix-validation.

## Whole-repo lint via cpv_lint_engine

One engine, 15 languages (Python, JS/TS, Rust, Go, Bash, Markdown, YAML,
JSON, TOML, Dockerfile, HTML, CSS, SQL, Lua, R), gitignore-aware,
remote-exec fallback via uvx / bunx / docker. The validate_plugin.py
script calls into the engine — there is no separate lint script.

**Why this matters:** A user reported a creator-agent plugin that
shipped without linting JS/TS/Rust/Bash because each missing local
linter was silently skipped. The unified engine fails-fast and never
silently skips a language.

## Idempotent publish.py

`stage_bump` reads the REMOTE `plugin.json.version` from `origin/master`
as the bump baseline, NOT local. `stage_commit_and_push` skips the
commit when HEAD's subject already matches the expected release commit
AND the working tree is clean, and skips the tag when it already exists
locally. The push always runs.

**Why this matters:** An interrupted publish (push 503, network drop,
hook reject) can be re-run with the same args and resumes from the
failed gate without double-bumping. v2.64.0 was lost during the publish
attempt that prompted this work; the user had to manually skip to
v2.65.0. That class of incident is now structurally impossible.

## validate_pipeline_script_refs rule

The validator scans these surfaces for dangling references to scripts
that no longer exist:

- `.github/workflows/*.{yml,yaml}` — CI / release / notify-marketplace workflows
- `.git/hooks/*` — locally-installed pre-push / pre-commit / post-merge hooks
- `scripts/setup_plugin_pipeline.py` — the PRE_PUSH_HOOK template literal
- `skills/cpv-plugin-validation-skill/references/*` — reference hooks copied into new plugins

Any reference to a non-existent `scripts/<name>.py` emits MAJOR with
the exact `file:line`.

**Why this matters:** Every time a script is renamed or removed,
multiple consumers silently break. v2.65.0 hit this exactly when the
lint script was removed but CI plus the local pre-push hook still
invoked it. The new rule catches the regression at PR or release time
before any push attempts to use the stale ref.

## Cross-platform scripts — no bash, no jq/sed/awk

Every script shipped in a CPV-scaffolded plugin must run identically on
Linux, macOS, AND Windows. Bash scripts (`.sh`) and tools that aren't
natively packaged on Windows (`jq`, `shellcheck`) are publish-blockers.
Python scripts must use cross-platform libraries throughout:

- `pathlib.Path` — NOT `os.path.join` / `os.path.exists` / `os.path.isdir`
- `tempfile.gettempdir()` — NOT a hardcoded `"/tmp/"`
- `shutil.which()` — NOT shell `which`
- `subprocess.run([...], timeout=N)` (a list of args, with a timeout) — NOT `os.system`, and NOT a shell string

Always pass arguments as a list; never invoke a shell to run a command string.

Newly-scaffolded plugins NEVER ship `.sh` files. The cpv-fix-validation
skill's `pipeline-migration.md §3a` documents the conversion recipe for
legacy plugins.

## Input sanitization — every script parameter

Every CLI flag, env-var read, argv element, JSON field, file content,
and gh-API response that flows into a subprocess call, a regex compile,
an SQL query, an HTTP URL, a file path, or a shell argument must be
validated against a strict regex/allowlist BEFORE use. Concrete rules:

- (a) NEVER `shell=True` — always pass `args` as a list.
- (b) repo slugs match `^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$` (canonical
  regex: `set_marketplace_pat.py:REPO_PATTERN`).
- (c) version strings match `^\d+\.\d+\.\d+(?:[-+][\w.]+)?$`.
- (d) plugin names match `^[a-z][a-z0-9-]*$` (kebab-case).
- (e) file paths resolved with `Path(...).resolve()` and checked against
  the plugin root via `relative_to()` to reject `..`-traversal.
- (f) regex patterns from user input go through `re.escape()` before compile.
- (g) URLs from user input have their host validated against an allowlist
  BEFORE passing to `gh api` / `urlopen`.

The validator scans for common unsanitized patterns and emits
MAJOR/CRITICAL when detected.

## Hooks MUST persist state in CLAUDE_PLUGIN_DATA, never CLAUDE_PLUGIN_ROOT

`${CLAUDE_PLUGIN_ROOT}` is REPLACED on every plugin update (~7-day
cleanup); state written there is silently destroyed. Plugins must
persist data in `${CLAUDE_PLUGIN_DATA}` (the
`~/.claude/plugins/data/<plugin-id>/` directory). The `validate_hook`
rule emits CRITICAL on any hook command that writes runtime state under
`${CLAUDE_PLUGIN_ROOT}` (shell redirects, `tee`, `sqlite3`,
package-manager `--prefix`/`--target` flags).

## Hook commands MUST be cross-platform (Python-delegated)

Every hook command in `hooks/hooks.json`, plugin.json's inline `hooks`,
AND agent / skill frontmatter `hooks:` runs on Linux, macOS, AND
Windows. Bash-only constructs (`set -euo pipefail`, `[[ ]]`, `$(<file)`,
process substitution `<(...)`, brace expansion `{a,b,c}`) emit MAJOR.
POSIX-only tools used directly (`jq`, `sed`, `awk`, `shellcheck`) emit
MINOR unless wrapped in `python3 -c "..."` / `bash -c "..."` /
`wsl bash -c "..."`. Recommended: delegate every non-trivial hook to a
Python script bundled under `${CLAUDE_PLUGIN_ROOT}/scripts/`. See
`pipeline-migration.md §3b` for the conversion cheat-sheet.

## PEP 723 scripts MUST be invoked via uv run

When a Python script under `scripts/` declares runtime dependencies via
a PEP 723 inline-script metadata block (`# /// script ... # ///` with a
non-empty `dependencies = [...]`), every reference to that script in
commands / agents / skills / hooks / README / `.mcp.json` / `.lsp.json`
/ `hooks.json` MUST use `uv run scripts/<name>.py <args>` (or
`uv run --with <deps> python scripts/<name>.py`). Bare `python
scripts/<name>.py` IGNORES the metadata block and the script
ImportErrors on the first non-stdlib import at runtime — the plugin
"looks valid" to every static check yet is broken when a user runs it.
CPV's `validate_pep723_invocations` flags every offending invocation as
MAJOR `[RC-PEP723-INVOCATION-001]`; the cpv-codemod `python-to-uv-run`
transform applies the fix in bulk. **When emitting any PEP 723 script
during scaffolding, double-check the README / commands / agents / skills
generated in the same pass so every invocation uses `uv run`.**

## Migrating a legacy plugin

Use the cpv-plugin-fixer-agent agent's pipeline-migration recipe — see the
cpv-fix-validation skill's `references/pipeline-migration.md` for the three
independent migrations:

1. Replace stale `scripts/<name>.py` references in workflows / hooks / templates
2. Consolidate per-language CI lint steps into a single call to the unified engine
3. Add idempotency helpers to publish.py (or regenerate via generate_plugin_repo.py)

Each migration is independently revertable.
