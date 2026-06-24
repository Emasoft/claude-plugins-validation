# Pipeline migration to current standards

## Table of Contents

- [§0 — Detect canonical pipeline drift via RC-PIPELINE-DRIFT-001](#0--detect-canonical-pipeline-drift-via-rc-pipeline-drift-001)
- [§0b — Remove legacy pipeline scripts via RC-LEGACY-PIPELINE-001](#0b--remove-legacy-pipeline-scripts-via-rc-legacy-pipeline-001)
- [§1 — Fix dangling script references](#1--fix-dangling-script-references)
- [§2 — Migrate to whole-repo lint via cpv_lint_engine](#2--migrate-to-whole-repo-lint-via-cpv_lint_engine)
- [§3 — Cross-platform Python — bash to Python, os.path to pathlib](#3--cross-platform-python-bash--python-ospath--pathlib)
- [§4 — Make publish.py idempotent — interrupted-publish recovery](#4--make-publishpy-idempotent-interrupted-publish-recovery)
- [§5 — Sanitize every script-input parameter against injection](#5--sanitize-every-script-input-parameter-against-injection)
- [§6 — #137-143 CI-parity defects](#6--137-143-ci-parity-defects)

---

## §0 — Detect canonical pipeline drift via `[RC-PIPELINE-DRIFT-001]`

`validate_plugin.py::validate_canonical_pipeline_drift` (v2.66.0) emits one
WARNING **per drifted file** — **`[WARNING] [RC-PIPELINE-DRIFT-001] Plugin
pipeline differs from the canonical CPV standard in <file>. <recommendation>`**
followed by an embedded unified diff (canonical → plugin) — for each of these
files (`validate_plugin.py::_CANONICAL_PIPELINE_FILES`) that drifts from its
canonical `gen_*` template:

```
scripts/publish.py
scripts/cpv_network_resilience.py
git-hooks/pre-push
.github/workflows/ci.yml
.github/workflows/release.yml
.github/workflows/notify-marketplace.yml
cliff.toml
.mega-linter.yml
.markdownlint.json
```

These files are pure infrastructure — plugin authors are NOT expected to
customise them. Drift means the plugin's pipeline lacks something the
current standard provides (idempotent publish.py recovery, sanitized
inputs, cross-platform Python, the latest workflow `permissions: {}` block,
GH Actions retry helpers, etc.).

### Fix recipe (§0)

```bash
# One-shot migration that overwrites every drifted file with the canonical
# template, backing up the previous copy as <file>.bak.
uvx cpv-remote-validate standardize <plugin-path> --fix --force-templates

# Equivalently from inside the plugin:
uv run python scripts/standardize_plugin.py . --fix --force-templates
```

After migration, the WARNING disappears AND every §1–§5 follow-up section
below becomes a no-op for the force-overwritten files (they already comply).
Only files outside `_FORCE_TEMPLATE_FILES` (e.g. user-owned README, custom
hook scripts, project-specific workflows) still need the per-section recipes.

### Verify (§0)

```bash
uv run python scripts/validate_plugin.py . --strict
# expect: no `[RC-PIPELINE-DRIFT-001]` finding
```

If a specific file SHOULD legitimately diverge from the standard (rare —
usually for a plugin with bespoke release infrastructure), document the
divergence in `CHANGELOG.md` and explicitly opt out by editing
`scripts/standardize_plugin.py::_FORCE_TEMPLATE_FILES` in your fork. CPV
itself is auto-skipped (the templates ARE its own files).

How the plugin-fixer auto-migrates a legacy plugin's CI/CD + release
pipeline to the canonical current standards. Three independent
migrations: stale script refs, lint consolidation, and publish.py
idempotency.

---

## §0b — Remove legacy pipeline scripts via `[RC-LEGACY-PIPELINE-001]`

`validate_plugin.py::validate_legacy_pipeline_scripts` (v2.69.0) emits a
**`[MINOR] [RC-LEGACY-PIPELINE-001]`** finding for every known-legacy
pipeline script that survives in the plugin's `scripts/` folder. These
files are obsoleted by `publish.py`'s 14-gate pipeline — keeping them
around invites users to invoke them and skip the canonical gates
(security scans, gh-auth precheck, integrity manifest, idempotent
commit/tag/push, cross-platform Python, etc.).

The legacy list (each entry includes its replacement):

| Legacy file                       | Replaced by                                          |
|-----------------------------------|------------------------------------------------------|
| `scripts/bump_version.py`         | `publish.py --patch / --minor / --major` (Gate 7)    |
| `scripts/release.sh`              | `publish.py` (.sh blocks Windows users)              |
| `scripts/release.py`              | `publish.py`                                         |
| `scripts/publish.sh`              | `publish.py`                                         |
| `scripts/lint.sh`                 | `ci.yml` + `publish.py` Gate 4 (lint)                |
| `scripts/setup-hooks.sh`          | `setup-hooks.py` (cross-platform)                    |
| `scripts/compute_hashes.py`       | `publish.py` Gate 8 (integrity manifest)             |
| `scripts/verify_hashes.py`        | `publish.py` Gate 8 verification                     |
| `scripts/changelog.py`            | `publish.py` Gate 9 (git-cliff)                      |
| `scripts/generate_changelog.py`   | `publish.py` Gate 9                                  |
| `scripts/check_version.py`        | `publish.py` Gate 7                                  |
| `scripts/install.sh`              | `claude plugin install` (no install script needed)   |

### Fix recipe (§0b)

```bash
# Auto-applied alongside --force-templates (the upgrade flow's umbrella):
uvx cpv-remote-validate standardize <plugin-path> --fix --force-templates

# Or as a standalone cleanup (preserves --force-templates state):
uvx cpv-remote-validate standardize <plugin-path> --fix --clean-legacy

# Dry-run preview:
uv run python scripts/standardize_plugin.py . --fix --clean-legacy --dry-run
```

**Preservation guardrail**: legacy scripts are **MOVED to `scripts_dev/`**,
NOT deleted. `scripts_dev/` is gitignored per the user's `.gitignore`
convention so the relocated files won't be committed accidentally. After
verifying the upgrade works, the user can delete `scripts_dev/` contents
in a follow-up commit, OR `git add scripts_dev/<file>` to keep something
tracked.

### Why MINOR (not MAJOR)?

The finding does not block publishing — many plugins keep these around
as hand-written helpers and the user must opt into removal. The fixer
agent's `/cpv-upgrade-plugin` flow is the canonical path; manual users
get the cheat-sheet table above.

### Verify (§0b)

```bash
uv run python scripts/validate_plugin.py . --strict
# expect: no `[RC-LEGACY-PIPELINE-001]` findings

ls scripts_dev/  # the moved legacy files (review before deleting)
```

If a script in the legacy list is genuinely needed by your plugin (rare —
ask "what does publish.py NOT do that this script does?"), document the
need in `CHANGELOG.md` and rename it so it doesn't match the legacy
allowlist (e.g. `scripts/lint.sh` → `scripts/_extra_lint_checks.sh`).
Better still: replace the bash with a cross-platform Python module under
`scripts/` and call it from a publish.py gate.

---

## §1 — Fix dangling script references

CPV's `validate_pipeline_script_refs` rule (added v2.65.1) emits
**`[MAJOR] Dangling reference to scripts/<name>.py — file does not exist`**
with `file:line` for every stale reference in:

- `.github/workflows/*.{yml,yaml}` — CI / release / notify-marketplace workflows
- `.git/hooks/*` — locally-installed pre-push / pre-commit / post-merge hooks
- `scripts/setup_plugin_pipeline.py` — the PRE_PUSH_HOOK template literal
- `skills/plugin-validation-skill/references/*` — reference hooks copied into new plugins

### Fix recipe (§1 )

For each finding `[MAJOR] Dangling reference to scripts/<old>.py — found at <file>:<line>`:

| `<old>` | Action |
|---|---|
| `lint_files.py` | Replace with `cpv_lint_engine.py` (CI workflow line) **or** drop entirely (pre-push hook — `validate_plugin.py` covers it) |
| `lint_validation.py` | Same as `lint_files.py` (older alias) |
| Any other removed script | Read the script's last-known purpose from `git log --diff-filter=D --name-only`. If it had a replacement, swap the reference; if it was deleted with no replacement, remove the call site. |

### Verify (§1 )

```bash
uv run python scripts/validate_plugin.py . --strict
# expect: 0 MAJOR findings from validate_pipeline_script_refs
```

---

## §2 — Migrate to whole-repo lint via `cpv_lint_engine`

If the plugin still has a separate `scripts/lint_files.py`, or its CI
workflow runs ruff/eslint/shellcheck/etc. as separate steps, consolidate
to the unified engine.

### Detection signals (§2 )

| Signal | Severity | Source |
|---|---|---|
| `scripts/lint_files.py` exists | INFO (legacy artefact) | filesystem |
| `.github/workflows/ci.yml` has separate `Ruff check` / `ESLint` / `ShellCheck` steps for project source | INFO | workflow file |
| Pre-push hook calls a per-language linter directly | INFO | `.git/hooks/pre-push` |

### Fix recipe (§2 )

1. **Delete `scripts/lint_files.py`** (always — the engine owns this)
2. **Replace per-language steps in `.github/workflows/ci.yml`**:

```yaml
# Before
- name: Ruff check
  run: uv run ruff check scripts/ tests/
- name: ESLint
  run: bunx eslint .
- name: ShellCheck
  run: shellcheck **/*.sh

# After (single step covers ALL supported languages)
- name: Lint all source files (read-only)
  run: uv run python scripts/cpv_lint_engine.py .
```

3. **Pre-push hook**: drop any direct linter call. The hook calls
   `scripts/validate_plugin.py` which already invokes
   `cpv_lint_engine` internally.

The unified engine supports Python, JS/TS, Rust, Go, Bash, Markdown,
YAML, JSON, TOML, Dockerfile, HTML, CSS, SQL, Lua, R — and uses
`uvx`/`bunx`/`docker` fallback so missing local binaries do NOT silently
skip the language.

### Verify (§2 )

```bash
uv run python scripts/cpv_lint_engine.py . --strict
# expect: every present language reports OK or fails with explicit findings
```

---

## §3 — Cross-platform Python (bash → Python, os.path → pathlib)

This section consolidates three independent migrations: shipped `.sh`
scripts (§3a below), bash-only hook commands (§3b below), and Python
scripts that use `os.path` / `os.system` / hardcoded `/tmp/` /
`shell=True` (§3c below). Apply them in order, then re-validate.

### IMPORTANT — bash → Python is NOT universal

NEVER convert every `.sh` file in the plugin to Python without first
checking each one against this exclusion list:

1. **Bash-specific functionality**: heredocs, `set -o pipefail`, complex
   `trap` handlers, process substitution `<(cmd)`, named pipes, or
   external-tool composition that depends on bash syntax. Python
   rewrite either loses functionality (no equivalent for `<(...)`) or
   balloons in complexity. Leave the file as bash with a comment
   marker the validator recognises.
2. **Bash-teaching skills or examples**: a skill teaching bash, or a
   reference file demonstrating bash patterns, MUST keep its `.sh`
   examples intact. §3b's "bash hook constructs" rule applies to HOOK
   COMMANDS only — NOT to code fenced inside `.md` documentation.
3. **Plugin-author intent**: a plugin marketed as bash tooling
   (README / CHANGELOG explicitly says so) keeps its bash. Surface
   bash files as INFO, not MAJOR, in those plugins.

When uncertain, ASK the user before converting. The fix loop's
`[BLOCKED]` return is the right move when policy is unclear — NEVER
the shortcut of "convert everything just in case".

### §3b — Convert bash hook commands to Python (cross-platform)

Hook commands embedded in `hooks/hooks.json`, in plugin.json's inline
`hooks` block, OR in agent / skill frontmatter `hooks:` field run on
Linux, macOS, AND Windows. Bash-isms that work on POSIX (set -euo
pipefail, `[[ ]]`, process substitution, brace expansion) break on
Windows where the hook runner uses cmd.exe / PowerShell.

### Detection signals (§3b)

`validate_hook.py` (and the same checks invoked from `validate_agent`
and `validate_skill`) emit:

- **MAJOR — bash-only constructs:** `set -e`/`set -euo pipefail`,
  `[[ ]]`, `$(<file)`, `<(...)`/`>(...)` process substitution,
  `{a,b,c}` brace expansion.
- **MINOR — POSIX-only tools used directly:** `jq`, `sed`, `awk`,
  `shellcheck`. Skipped when wrapped in `python3 -c "..."`,
  `bash -c "..."`, or `wsl bash -c "..."` (the user has owned the
  platform decision).

### Fix recipe — delegate to a Python script (§3b)

The cleanest fix is to delegate every non-trivial hook command to a
Python script bundled under `${CLAUDE_PLUGIN_ROOT}/scripts/`. Python
is cross-platform by default and gives access to `subprocess`, `re`,
`json`, `pathlib`, etc. — replacements for bash, jq, sed, awk.

Example before:
```json
{
  "hooks": {
    "PostToolUse": [{
      "hooks": [{
        "type": "command",
        "command": "set -euo pipefail; gh api repos/X | jq '.name' > \"${CLAUDE_PLUGIN_DATA}/last.txt\""
      }]
    }]
  }
}
```

Example after:
```json
{
  "hooks": {
    "PostToolUse": [{
      "hooks": [{
        "type": "command",
        "command": "python3 \"${CLAUDE_PLUGIN_ROOT}/scripts/post_tool_use_hook.py\""
      }]
    }]
  }
}
```

`scripts/post_tool_use_hook.py`:
```python
#!/usr/bin/env python3
import json, os, subprocess
from pathlib import Path

data_dir = Path(os.environ["CLAUDE_PLUGIN_DATA"])
data_dir.mkdir(parents=True, exist_ok=True)
result = subprocess.run(
    ["gh", "api", "repos/X"],
    capture_output=True, text=True, check=True, timeout=30,
)
name = json.loads(result.stdout).get("name", "")
(data_dir / "last.txt").write_text(name + "\n", encoding="utf-8")
```

### Translation cheat-sheet

| Bash | Python equivalent |
|---|---|
| `jq '.name' file.json` | `json.loads(open("file.json").read())["name"]` |
| `sed -i 's/foo/bar/g' f` | `Path(f).write_text(re.sub(r"foo", "bar", Path(f).read_text()))` |
| `awk '{print $1}' f` | `[ln.split()[0] for ln in Path(f).read_text().splitlines()]` |
| `[[ -f X ]]` | `Path("X").is_file()` |
| `set -euo pipefail` | (default Python — uncaught exceptions exit nonzero) |
| `cmd1 \| cmd2` | `subprocess.run(["cmd2"], input=subprocess.check_output(["cmd1"]))` |
| `mktemp -d` | `tempfile.mkdtemp()` |
| `find . -name "*.py"` | `Path(".").rglob("*.py")` |
| `realpath X` | `Path(X).resolve()` |

### Verify (§3b)

```bash
uv run python scripts/validate_plugin.py . --strict
# expect: no MAJOR "bash-only constructs" findings,
#         no MINOR "POSIX-only tool" findings.
```

### §3a — Convert shipped bash/shell scripts to Python (cross-platform)

CPV's pipeline standard is **Python-only** since v2.65.2: every script
shipped to user installs must run identically on Linux, macOS, AND
Windows. Bash scripts violate that contract — `bash`, `jq`, `sed`,
`awk` etc. are not natively packaged on Windows, so a plugin that
ships a `.sh` script breaks for any Windows user.

### Detection signals (§3a)

`validate_plugin.py` emits a WARNING for each `.sh` script in the
plugin tree ("Found N Bash/Shell script(s)"). Treat every such
WARNING as a publish-blocker for cross-platform plugins.

### Fix recipe (§3a)

1. Identify the bash script's purpose — most fall in 4 categories:
   - GitHub API calls via `gh + jq` → Python `subprocess.run` + `json` module (or `gh_with_retry` from `cpv_network_resilience`)
   - File manipulation via `sed/awk/grep` → Python `re` + `pathlib`
   - Process control via `set -euo pipefail` → `subprocess.run(check=True)` + try/except
   - Conditional execution via `if [ -f X ]` → `Path(X).is_file()`

2. Write the Python equivalent. Preserve the same CLI surface (argparse names matching the bash flags) so existing callers in workflows, hooks, and docs work unchanged.

3. Move the bash script to `scripts_dev/` (gitignored, preserved for reference) — do not delete outright; the user may want to compare behaviour later.

4. Update every reference — workflows, docs, README, hooks, skill references — to point at the new `.py`. The `validate_pipeline_script_refs` rule catches missed references.

### Verify (§3a)

```bash
find . -name "*.sh" -not -path "./.git/*" -not -path "./scripts_dev/*"
# expected: nothing in shipped tree

uv run python scripts/validate_plugin.py . --strict
# expected: no "Found N Bash/Shell script(s)" WARNING
```

### §3c — Convert os.path / os.system / hardcoded paths → pathlib (cross-platform Python)

Even when a plugin's scripts are 100% Python, they can still fail on
Windows if they use POSIX-only patterns:

| POSIX-only pattern | Windows breakage | Cross-platform replacement |
|---|---|---|
| `os.path.join(a, b)` | works but inconsistent slashes mixed with `pathlib` | `Path(a) / b` |
| `os.path.isfile(x)` | fragile vs symlinks | `Path(x).is_file()` |
| `os.path.isdir(x)` | same | `Path(x).is_dir()` |
| `os.path.exists(x)` | same | `Path(x).exists()` |
| `os.path.dirname(__file__)` | returns string, mixed with Path | `Path(__file__).parent` |
| `os.path.basename(p)` | string round-trip | `Path(p).name` |
| `os.path.abspath(p)` | string | `Path(p).resolve()` |
| `os.system("cmd")` | `shell=True` security risk + Windows quoting | `subprocess.run([...], check=True, timeout=N)` |
| `"/tmp/foo"` | does not exist on Windows | `tempfile.gettempdir() / "foo"` or `tempfile.mkdtemp()` |
| `"/usr/bin/env python"` shebang only | Windows ignores shebangs | declare `python` interpreter explicitly in CLI invocations |
| `os.geteuid()` | `AttributeError` on Windows | `getattr(os, "geteuid", lambda: 1)()` |
| `subprocess.run(... shell=True)` | quoting differs Windows vs POSIX | always pass `args` as list, never `shell=True` |
| `f.read(n)` looping until 0 bytes | infinite loop on FUSE / sparse | bound the loop `for _ in range(N): ...` |

### Detection signals (§3c)

```bash
# Per-file scan — quick heuristic for legacy patterns
grep -rn "os\\.path\\.join\\|os\\.path\\.exists\\|os\\.path\\.isfile\\|os\\.path\\.isdir\\|os\\.geteuid\\|shell=True\\|\"/tmp/\\|os\\.system" scripts/ --include="*.py" | head
```

The CPV validator does NOT currently emit a finding for `os.path`
usage (it's still legal Python). Treat ANY hit on the grep above as a
fix candidate during a cross-platform audit.

### Fix recipe (mechanical)

1. `import os` + `os.path.X(...)` → `from pathlib import Path` + `Path(...).X()`
2. `os.path.join(a, b, c)` → `Path(a) / b / c`
3. `subprocess.run(... shell=True)` → split args list, drop `shell=True`
4. `os.system("cmd")` → `subprocess.run([...], check=True, timeout=60)`
5. `"/tmp/<name>"` → `Path(tempfile.gettempdir()) / "<name>"` OR `Path(tempfile.mkdtemp(prefix="<name>-"))`
6. Add `timeout=` to every `subprocess.run` (60s default, longer for clones/builds)

### Verify (§3c)

```bash
# After conversion, re-run grep — expect 0 hits in shipped scripts
grep -rn "os\\.path\\.\\|shell=True\\|\"/tmp/\\|os\\.system" scripts/ --include="*.py"

# Run lint + tests
uv run ruff check scripts/
uv run pytest tests/
```

## §4 — Make `publish.py` idempotent (interrupted-publish recovery)

A non-idempotent `publish.py` reads LOCAL `plugin.json.version` as the
bump baseline. When a publish is interrupted between commit+tag and
push (transient network failure, pre-push hook reject, GitHub 503),
the local repo is at the bumped version while origin is one minor
behind. Re-running `publish.py --minor` then DOUBLE-BUMPS — local
went from 2.63.2 → 2.64.0 (interrupted), and the second attempt would
go 2.64.0 → 2.65.0, skipping 2.64.0 entirely. This actually happened
on CPV's own publish.py during the v2.64.0 ship attempt.

### Detection signal

Run:
```bash
grep -E "^def _read_remote_version|^def _infer_bump_type|^def _git_porcelain_clean" scripts/publish.py
```

If the helpers are absent, `publish.py` is non-idempotent and must be
upgraded.

### Fix recipe (§4 )

The simplest path is to regenerate `publish.py` from
`generate_plugin_repo.py`'s `gen_publish_py()` — every newly-scaffolded
plugin since v2.65.1 ships with idempotency baked in.

For a surgical patch (preserves customizations), add these five helpers
before `stage_bump`:

- `_read_remote_version(plugin_root) -> str | None` — reads
  `.claude-plugin/plugin.json` from `origin/master`
- `_infer_bump_type(old, new) -> str | None` — classifies a semver delta
- `_git_porcelain_clean(root) -> bool` — true iff working tree clean
- `_head_commit_message(root) -> str` — HEAD subject line
- `_local_tag_exists(root, tag) -> bool` — local tag presence check

Then modify two stages:

**`stage_bump`** — read REMOTE plugin.json as baseline. If
`current == new_ver`, skip the bump. If `current != remote and current != new_ver`,
refuse and ask for manual intervention.

**`stage_commit_and_push`** — skip the commit when HEAD's subject
already matches `chore: bump version to <new_ver>` AND the tree is
clean. Skip the tag when it already exists locally. The push always runs.

Reference implementation: `scripts/generate_plugin_repo.py:gen_publish_py`
(canonical) and `scripts/publish.py` (CPV's own — same helpers).

### Verify (§4 )

```bash
# Simulate the interrupted-publish state and re-run:
echo '{"version":"X.Y.Z"}' > .claude-plugin/plugin.json   # already bumped
git commit -am "chore: bump version to X.Y.Z"             # already committed
git tag vX.Y.Z                                            # already tagged
# (push would have failed)
uv run python scripts/publish.py --minor
# expect: "Local plugin.json is already at X.Y.Z — skipping bump",
#         "HEAD is already 'chore: bump version to X.Y.Z' — skipping commit",
#         "Tag vX.Y.Z already exists locally — skipping tag step",
#         then push proceeds normally
```

---

## §5 — Sanitize every script-input parameter against injection

Every CLI flag, environment variable, argv element, JSON field, file
content, gh-API response, and incoming HTTP body that a script consumes
MUST be validated against a strict regex/allowlist BEFORE it is used in:

- A subprocess call (shell injection)
- A regex compile (regex injection / ReDoS)
- A file-path operation (path traversal)
- A URL passed to `gh api` / `urlopen` (SSRF)
- An SQL query (SQL injection)
- A shell argument inside `bash -c "..."` (shell injection)

### Detection signals (§5)

```bash
# Per-script grep — quick heuristic for unsanitized-input patterns
grep -rnE 'shell=True|os\.system|subprocess\.run\([^,]*\$\{|re\.compile\([^)]*input\(|urlopen\([^)]*\$\{|f\"[^\"]*\{[^}]*\}[^\"]*\"\s*,?\s*shell=True' \
  scripts/ --include="*.py" | head
```

Also flag every place where an `argparse` argument or env-var read is
passed directly to a subprocess / regex / URL without intermediate
validation.

### Fix recipe (§5)

1. **Define a canonical regex / allowlist for each input type.** Examples
   from CPV's existing scripts (use as-is or adapt):

   ```python
   # set_marketplace_pat.py — canonical repo-slug regex
   REPO_PATTERN = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")

   # Semantic version
   SEMVER_PATTERN = re.compile(r"^\d+\.\d+\.\d+(?:[-+][\w.]+)?$")

   # Plugin / marketplace name (kebab-case, must start with letter)
   NAME_PATTERN = re.compile(r"^[a-z][a-z0-9-]*$")

   # Tag name (vX.Y.Z form only — no arbitrary tag names accepted)
   TAG_PATTERN = re.compile(r"^v\d+\.\d+\.\d+(?:[-+][\w.]+)?$")

   # GitHub host allowlist
   ALLOWED_HOSTS = frozenset({"github.com", "api.github.com", "raw.githubusercontent.com"})
   ```

2. **Validate at the boundary.** As soon as the script reads input
   (argparse, env-var, JSON load), validate it against the regex/allowlist
   AND raise / exit non-zero on mismatch. NEVER pass unvalidated input
   forward.

   ```python
   # WRONG — value flows directly into a subprocess
   def install(repo: str) -> None:
       subprocess.run(["gh", "secret", "set", "X", "--repo", repo, ...])

   # RIGHT — validated at the boundary
   def install(repo: str) -> None:
       if not REPO_PATTERN.match(repo):
           raise ValueError(f"Invalid repo slug: {repo!r}")
       subprocess.run(["gh", "secret", "set", "X", "--repo", repo, ...])
   ```

3. **For paths, resolve + relative_to.** This rejects path traversal
   reliably — `Path("/safe/root").resolve() / "../../etc/passwd"` is
   not enough; you must also verify the resolved result is under the
   safe root.

   ```python
   from pathlib import Path
   def safe_path(user_path: str, root: Path) -> Path:
       resolved = (root / user_path).resolve()
       try:
           resolved.relative_to(root.resolve())
       except ValueError as e:
           raise ValueError(f"Path traversal detected: {user_path!r}") from e
       return resolved
   ```

4. **For regex inputs, escape before compile.** If user input becomes
   part of a regex pattern (e.g. searching for a user-supplied keyword),
   escape it with `re.escape()` first.

5. **For URLs, parse + allowlist host.** Use `urllib.parse.urlparse`
   and check `.scheme in {"https"}` AND `.hostname in ALLOWED_HOSTS`.

6. **NEVER `shell=True`. NEVER `os.system`.** Always pass argv as a
   list to `subprocess.run`. The shell never gets a chance to interpret
   metacharacters.

### Verify (§5)

```bash
# After sanitization, re-run the grep — expect 0 hits in shipped scripts
grep -rnE 'shell=True|os\.system' scripts/ --include="*.py"

# Run the plugin's full security validator
uv run python scripts/validate_plugin.py . --strict

# (Future) CPV will gain a dedicated rule that flags unvalidated
# argparse-to-subprocess flows.
```

---

## §6 — #137-143 CI-parity defects

These six defects all share ONE failure shape: the upgrade passes
`validate_plugin --strict` LOCALLY but FAILS the adopting plugin's GitHub
CI — because `validate_plugin` does NOT run the jscpd / actionlint /
`mypy --strict` / `uv sync --extra dev` gates the generated `ci.yml` Lint
job runs. `generate_plugin_repo.py` and `standardize_plugin.py` already
EMIT the fixed forms; the recipe here is for an agent hand-touching one
of these constructs during a manual upgrade step.

### Local detector (§6)

Run the CI-parity preflight LOCALLY before declaring DONE — it runs the
gates `validate_plugin` skips AND statically detects all six defects
below (CIP-1..6):

```bash
cpv-remote-validate ci-preflight <plugin-path>
# Equivalently from inside the plugin:
uv run python scripts/cpv_ci_preflight.py .
```

Every gate DEGRADES to a non-blocking WARNING when its tool is absent
(never false-blocks); a real defect BLOCKS. A non-crash run is NOT
CI-parity proof on its own — read the per-check verdicts.

### The six defects (§6)

| # | Defect | One-line fix |
|---|--------|--------------|
| CIP-1 | Inverted `CLAUDE_PRIVATE_USERNAMES` env on a CI validate step (set to the repo owner → CPV flags every owner GitHub URL + no-reply email as a private-path leak). | DROP the line from the workflow (a CI runner has no developer local-username to protect); keep only `PLUGIN_SKIP_GITHUB_INTEGRITY=1`. The local `CLAUDE_PRIVATE_USERNAMES="$(whoami)"` scan idiom is a different, correct usage — untouched. |
| CIP-2 | `publish.py` import-fallback shim (`gh_with_retry`/`git_with_retry`) carries only `# type: ignore[no-redef]`, but `mypy --strict` also needs `[misc]` (conditional-variant non-identical-signature rule). | Use `# type: ignore[no-redef, misc]` on the fallback shim (idiomatic import-fallback idiom, NOT a suppression — keep the WHY comment). |
| CIP-3 | `pyproject.toml` lacks a `[project.optional-dependencies].dev` table, so the canon `uv sync --extra dev` fails ("Extra dev is not defined"). | Add `dev = ["pytest", "ruff", "mypy"]` (create-or-augment, format-preserving; refresh the lockfile). `standardize --fix` auto-provisions this. |
| CIP-4 | A superseded standalone `validate.yml` survives after the consolidated `ci.yml` was added; its pre-existing shellcheck SC2086 then fails `ci.yml`'s actionlint Lint job. | Remove the CPV-shipped `validate.yml` (its Validate job is replaced by `ci.yml`'s) and re-point branch protection; safe-delete it to `scripts_dev/superseded-workflows/`. `standardize --fix` removes it (identity-guarded, only when `ci.yml` is present). |
| CIP-5 | The jscpd copy-paste check in `publish.py` Gate 2b and CI's Mega-Linter use divergent ignore globs, so a local pass differs from CI. | Provision a single-source `.jscpd.json` (threshold 5 + ignore globs mirroring `.mega-linter.yml`'s `FILTER_REGEX_EXCLUDE`) auto-discovered by BOTH; never clobber an existing one. `standardize --fix` provisions it. |
| CIP-6 | A `.github/workflows/*.yml` pins `claude-plugins-validation@<ref>` at a non-resolvable ref (`@main`/`@develop`/`@HEAD`/a branch name) — CPV's default branch is `master`, so `uvx --from git+…@main` 404s (`Git operation failed / Updating … (main)`) and the workflow red-CIs forever. A plugin migrated by an OLD CPV (≤v2.137, pre-#139) was pinned `@main` and never re-published, so nothing re-pins it. **This is the DOMINANT downstream CI failure.** | Re-pin to the current `v<semver>` tag or `master` — `standardize --fix` rewrites the stale ref in place (surgical, only the CPV ref), or `--force-templates` regenerates the whole workflow. A valid `master`/`v<semver>`/SHA pin is left untouched. |

### Verify (§6)

```bash
cpv-remote-validate ci-preflight .
# expect: CIP-1..6 PASS (or a non-blocking WARNING when a gate's tool is
#         absent); no BLOCK from any parity gate.
```

---

## Combined verification

After all three migrations, the plugin should:

```bash
uv run python scripts/validate_plugin.py . --strict
# expect: 0 CRITICAL/MAJOR/MINOR/NIT (only WARNINGs allowed)

uv run python scripts/cpv_lint_engine.py .
# expect: every language reports OK

# And the publish.py interrupted-recovery test from §3 should resume cleanly.
```

If any of these still fail after applying the recipes, surface the
remaining findings to the user — do NOT push a half-migrated state.
