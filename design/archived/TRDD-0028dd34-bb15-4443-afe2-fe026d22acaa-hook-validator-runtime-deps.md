---
trdd-id: 0028dd34
title: TRDD-0028dd34 — Hook validator runtime-dependency blind spots
column: complete
updated: 2026-08-25T17:25:05+0200
---

# TRDD-0028dd34 — Hook validator runtime-dependency blind spots

**TRDD ID:** `0028dd34-bb15-4443-afe2-fe026d22acaa`
**Filename:** `design/tasks/TRDD-0028dd34-bb15-4443-afe2-fe026d22acaa-hook-validator-runtime-deps.md`
**Tracked in:** this repo (design/tasks/ is git-tracked)

**Status:** Done (2026-05-10) — tokenizer, extractor, PEP 723 detection, sys.exit module-scope detector, runtime-dep reconciliation matrix, antipattern checks (unset VIRTUAL_ENV combo + module-scope sys.exit), uvx-non-substitute callout, and umbrella-validator wiring all shipped across commits 1ea1e40 → 56d2da4. Final TDD coverage gap closed 2026-05-10: added `test_uv_run_script_pep723_partial_covers_other_only_flagged` for the §6.5 matrix entry "uv run --script + imports pycozo + PEP 723 declares `requests` only → MAJOR pinpointing the missing dep" (also covers §6.4 malformed-block indirect path). Hook test suite: 234 passed.
**Owner:** Emasoft
**Area:** `scripts/validate_hook.py`
**Trigger incident:** Perfect Skill Suggester (PSS) shipped v3.1.0 with a broken hook that `sys.exit`-ed at every `UserPromptSubmit` because `python3 pss_hook.py` could not resolve the `pycozo` import. CPV's `cpv-validate-plugin` passed the plugin cleanly. The user flagged this as an unacceptable false-negative.

## 1. The failing PSS hooks.json (verbatim, v3.1.0)

```jsonc
{
  "description": "Perfect Skill Suggester - AI-powered skill activation with 88%+ accuracy",
  "hooks": {
    "UserPromptSubmit": [{ "hooks": [{
      "type": "command",
      "command": "unset VIRTUAL_ENV; python3 \"${CLAUDE_PLUGIN_ROOT}/scripts/pss_hook.py\"",
      "timeout": 10,
      "statusMessage": "Analyzing skill triggers..."
    }]}],
    "SessionStart": [{ "matcher": "startup|resume", "hooks": [{
      "type": "command",
      "command": "unset VIRTUAL_ENV; python3 \"${CLAUDE_PLUGIN_ROOT}/scripts/pss_hook.py\" --warm-index &",
      "timeout": 5
    }]}],
    "PostCompact": [{ "hooks": [{
      "type": "command",
      "command": "unset VIRTUAL_ENV; python3 \"${CLAUDE_PLUGIN_ROOT}/scripts/pss_hook.py\" --post-compact",
      "timeout": 5
    }]}]
  }
}
```

The referenced `scripts/pss_hook.py` imports `scripts/pss_cozodb.py`, which imports `pycozo.client.Client`. System `python3` has no `pycozo` installed, so module load raises `ImportError`, and `pss_cozodb.py` converts that into `sys.exit("ERROR: pycozo is required...")` at import time — aborting the hook process with a user-visible error on every prompt.

The fix in PSS v3.1.1: switch the hook command to `uv run --quiet --script "${CLAUDE_PLUGIN_ROOT}/scripts/pss_hook.py"` and add PEP 723 inline metadata declaring `pycozo[embedded]>=0.7.6` at the top of `pss_hook.py`.

## 2. Why CPV didn't catch this

Empirically traced against `scripts/validate_hook.py` (line numbers refer to HEAD at the time of this TRDD):

### 2.1. `extract_script_path` is structurally broken for the most common case

`extract_script_path` (line 316) reads `command.split()[0]` and returns it as the script path only when it ends with a lintable extension. For an interpreter-style invocation like `python3 foo.py`, the first token is `"python3"` (no extension) → returns `None` → **the referenced script is never loaded and never linted**. Verified by direct unit call:

| command | returned path |
|---|---|
| `unset VIRTUAL_ENV; python3 "${CLAUDE_PLUGIN_ROOT}/scripts/pss_hook.py"` | `None` |
| `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/pss_hook.py"` | `None` |
| `uv run --quiet --script "${CLAUDE_PLUGIN_ROOT}/scripts/pss_hook.py"` | `None` |
| `"${CLAUDE_PLUGIN_ROOT}/scripts/run.sh"` (original test case) | `scripts/run.sh` ✓ |

This means the python/JS lint paths in `validate_hook.py` are essentially dead code for every real-world hook.

### 2.2. No shell compound-command parsing

`;`, `&&`, `||`, `|` are ignored. In `unset VIRTUAL_ENV; python3 …`, `parts[0]` is `"unset"` and the extractor never reaches `python3 …`.

### 2.3. `mypy --ignore-missing-imports`

`lint_python_script` (line 458) runs `mypy --ignore-missing-imports`. The flag silently swallows exactly the failure mode that killed PSS (`pycozo` not resolvable). Even if extraction had worked, mypy would have reported nothing.

### 2.4. No runtime-dependency reconciliation

Nothing correlates:
- the third-party imports in a referenced script, against
- the hook's invocation method (plain `python3` vs `uv run --script` vs a `${CLAUDE_PLUGIN_DATA}/.venv` produced by a SessionStart setup hook).

The three valid resolution patterns (PEP 723 + `uv run --script`; `${CLAUDE_PLUGIN_DATA}/.venv` + SessionStart install hook; stdlib-only) are never checked.

### 2.5. No detection of antipattern env manipulation

`unset VIRTUAL_ENV` (explicit in the PSS bug), `unset PYTHONPATH`, etc. are red flags — the author is deliberately fighting the runtime. CPV has no awareness of these patterns.

### 2.6. No check for `sys.exit()` / `raise SystemExit` at module scope

`pss_cozodb.py` had `sys.exit("ERROR: pycozo is required...")` at module top level. Any script that imports `pss_cozodb` takes that process down. This pattern is especially lethal in hook scripts where a `SystemExit` at import time becomes a user-visible failure on every prompt. Not checked.

## 3. Root cause (one sentence)

`validate_hook.py` treats a hook command as an opaque string whose "script" is always `command.split()[0]`, so it can neither find the actual script in real-world compound/interpreter invocations nor reason about whether that script's imports will resolve at runtime.

## 4. Design

### 4.1. New helper: `_tokenize_hook_command(command: str) -> list[list[str]]`

Split `command` into a sequence of simple-command token lists, separated by shell compound operators (`;`, `&&`, `||`, `|`, `&` at end). Handle single/double quoting correctly so `"${CLAUDE_PLUGIN_ROOT}/scripts/foo.py"` is a single token. Strip trailing `&` (background) from the final token. Implementation: a small hand-rolled tokenizer (shlex-compatible where possible; we can't use `shlex.shlex` directly because we need to preserve `${VAR}` literally).

### 4.2. Rewrite `extract_script_path` → `extract_script_paths` (plural)

For each simple command produced by `_tokenize_hook_command`:
1. Skip leading shell-builtin sequences like `unset VAR`, `export VAR=val`, `cd path` that have no script payload.
2. Detect interpreter form: if `tokens[0]` is in a known interpreter set (`python`, `python3`, `python3.N`, `node`, `deno`, `bun`, `bunx`, `npx`, `uvx`, `uv`, `bash`, `sh`, `zsh`, `ruby`, `perl`, `php`, `env`), walk past known flags (e.g. `uv run --quiet --script`, `env -S`) and take the first non-flag argument as the script candidate.
3. Otherwise treat `tokens[0]` as the script candidate (existing behavior).
4. If the candidate resolves to a file with a lintable extension (or no extension but `#!` shebang), record it as a `ScriptRef` with the original invocation context (`invocation_mode: "interpreter-python" | "uv-run-script" | "venv-python" | "direct" | "node" | "other"`).

Return a list of `ScriptRef` — multiple scripts in one hook command are rare but legal (e.g. `foo.sh && bar.py`).

### 4.3. Backwards compatibility

The existing `extract_script_path(command, plugin_root) -> Path | None` remains, but is reimplemented as `extract_script_paths(...)[0].path if any else None` to preserve the tests at `test_validate_hook.py:245-268`. Callers inside `validate_command_hook` migrate to the plural form.

### 4.4. New helper: `detect_python_third_party_imports(script_path: Path) -> set[str]`

- Parse with `ast.parse(source, filename=str(script_path))`. On `SyntaxError` return `set()` (linting already reports the syntax issue separately).
- Walk top-level `Import`/`ImportFrom` nodes (and those inside `if __name__` blocks), record module root names.
- Filter against `sys.stdlib_module_names` (Python 3.10+; for Python 3.9 fallback, ship a vendored set).
- Also filter against names that match the plugin's own `scripts/*.py` files (intra-plugin imports are fine).
- Return the set of third-party module roots.

### 4.5. New helper: `detect_pep723_deps(script_path: Path) -> list[str] | None`

Parse the `# /// script … # ///` block at the top of the file (PEP 723 official spec). Return the `dependencies` list from the TOML inside, or `None` if no block present. Use stdlib `tomllib` (Python 3.11+). For broader compatibility, parse a simple regex + `tomllib`.

### 4.6. New validator: runtime-dep reconciliation

For each Python `ScriptRef` with non-empty third-party imports, require ONE of:
1. `invocation_mode == "uv-run-script"` AND the script has a PEP 723 block whose `dependencies` cover every detected third-party import root. Emit PASSED on match; MAJOR on missing/incomplete block.
2. `invocation_mode == "venv-python"` (command invokes `${CLAUDE_PLUGIN_DATA}/.venv/bin/python`) AND the plugin has a SessionStart hook that sets up the venv (heuristic: SessionStart command contains `uv venv` or `pip install` targeting `${CLAUDE_PLUGIN_DATA}`). Emit PASSED on match; MINOR if SessionStart setup is missing.
3. No third-party imports at all. Silent pass.

Else: MAJOR — `Hook invokes '{script}' which imports third-party modules {mods} via plain '{interpreter}' — imports will fail at runtime unless resolved via PEP 723 + 'uv run --script' OR a ${CLAUDE_PLUGIN_DATA}/.venv set up by a SessionStart hook.`

### 4.7. Antipattern checks (new)

**`unset VIRTUAL_ENV` — conditional warning (refined 2026-04-17):**

The initial draft warned on every `unset VIRTUAL_ENV` occurrence. That was
too broad. There are three real-world patterns:

| Pattern | Verdict | Why |
|---|---|---|
| `unset VIRTUAL_ENV; python3 foo.py` | **WARN** | The PSS v3.1.0 failure mode. Sheds user venv, falls back to system `python3` with no project deps. |
| `unset VIRTUAL_ENV; uv run --script foo.py` | **OK (silent)** | Defensive belt-and-suspenders. uv respects VIRTUAL_ENV by default and might sync into it; unsetting it forces uv to create its own script-scoped cache venv. |
| `unset VIRTUAL_ENV; ${CLAUDE_PLUGIN_DATA}/.venv/bin/python foo.py` | **OK (silent)** | Direct-invocation of a venv's python resolves `sys.prefix` from the binary path regardless of VIRTUAL_ENV — so unsetting it is redundant but harmless. |

The check therefore fires ONLY when `unset VIRTUAL_ENV` coincides with a
`interpreter-python` ScriptRef in the same command **and no safer-python ref
is present**. Same logic applies to `unset PYTHONPATH`.

**Module-scope `sys.exit` / `raise SystemExit`:**

In `detect_python_third_party_imports` (or a sibling helper), detect top-level
`Expr(Call(func=…))` where `func` is `sys.exit` or `exit`, or top-level
`Raise(SystemExit)`, and the same forms inside top-level `if` blocks.
WARN at MAJOR: `Script '{script}' calls sys.exit() at module scope — the
hook process will be killed at import time if the call path is reached.`

### 4.8. mypy flag review

Keep `--ignore-missing-imports` (removing it floods reports with noise on every plugin that doesn't vendor stubs), but when the new runtime-dep reconciliation check produces its MAJOR, include the missing module list explicitly. The new check is the substantive signal; mypy stays cosmetic.

## 5. Files to change

| File | Change |
|---|---|
| `scripts/validate_hook.py` | Add tokenizer, rewrite extraction, add helpers 4.4–4.5, add reconciliation check 4.6, add antipattern checks 4.7 |
| `tests/test_validate_hook.py` | Add ~12 new tests covering each new behavior + a regression test for the exact PSS v3.1.0 hooks.json |
| `design/tasks/TRDD-0028dd34-…md` | This document (shipped in the same commit as the TRDD creation, separately from the implementation) |

No changes to agent/command/skill files — this is a pure validator-internal refactor. Scoring output stays in the `severity counts + binary VALID/INVALID` format documented in `MEMORY.md`.

## 6. Test plan

### 6.1. Unit: tokenizer

- `unset VIRTUAL_ENV; python3 foo.py` → `[["unset","VIRTUAL_ENV"], ["python3","foo.py"]]`
- `cd /tmp && python3 foo.py` → `[["cd","/tmp"], ["python3","foo.py"]]`
- `python3 "$CLAUDE_PLUGIN_ROOT/scripts/foo.py" --warm-index &` → `[["python3","$CLAUDE_PLUGIN_ROOT/scripts/foo.py","--warm-index"]]` (trailing `&` stripped)
- `source venv/bin/activate && python3 foo.py` → two commands, second is interpreter form

### 6.2. Unit: extraction

- `python3 foo.py` → script=`foo.py`, mode=`interpreter-python`
- `uv run --quiet --script foo.py` → script=`foo.py`, mode=`uv-run-script`
- `${CLAUDE_PLUGIN_DATA}/.venv/bin/python foo.py` → script=`foo.py`, mode=`venv-python`
- `node foo.js` → script=`foo.js`, mode=`node`
- `unset VAR; python3 foo.py` → script=`foo.py`, mode=`interpreter-python`
- `echo hello` → empty

### 6.3. Unit: import detection

- File importing only `os`, `sys`, `json` → empty third-party set
- File importing `pycozo` → `{"pycozo"}`
- File importing `pss_cozodb` (sibling module) → empty when `scripts/pss_cozodb.py` exists in the plugin
- File with syntax error → empty (silent; syntax flagged elsewhere)

### 6.4. Unit: PEP 723 parsing

- File with `# /// script\n# dependencies = ["foo"]\n# ///` → `["foo"]`
- File without the block → `None`
- Malformed block (unbalanced markers, invalid TOML, non-list `dependencies`)
  → `[]` (block present but unusable). No MINOR is surfaced here — this
  case is caught *indirectly* during reconciliation: if the script's imports
  include third-party modules, the empty-dep list fails `uv run --script`
  coverage and the MAJOR "invocation X imports third-party modules but the
  PEP 723 block does not declare them" finding fires downstream. Splitting
  out a dedicated "malformed block" MINOR would report the same failure
  twice for the same root cause, so it is intentionally omitted. Tests:
  `test_detect_pep723_malformed_block_returns_empty` (unit) plus the
  reconciliation matrix entry "`uv run --script foo.py` / imports pycozo /
  covers `other` only" (integration).

### 6.5. Integration: reconciliation matrix

| Hook command | Script imports | PEP 723 block | Expected verdict |
|---|---|---|---|
| `python3 foo.py` | `{pycozo}` | none | MAJOR (runtime ImportError risk) |
| `uv run --script foo.py` | `{pycozo}` | covers pycozo | PASSED |
| `uv run --script foo.py` | `{pycozo}` | covers `other` only | MAJOR (block incomplete) |
| `uv run --script foo.py` | `{pycozo}` | none | MAJOR (no block) |
| `${CLAUDE_PLUGIN_DATA}/.venv/bin/python foo.py` | `{pycozo}` | none | PASSED if plugin has SessionStart venv setup, else MINOR |
| `python3 foo.py` | `{}` (stdlib only) | n/a | PASSED |

### 6.6. Regression

A test fixture containing the exact PSS v3.1.0 `hooks.json` plus a stub `pss_hook.py` importing `pycozo` must produce at least one MAJOR. Without this test, any future regression that silently re-breaks the extractor would go undetected.

## 7. Rollout

1. Phase 1: commit this TRDD.
2. Phase 2: implement 4.1–4.3 (tokenizer + extraction), ship with 6.1/6.2 tests. No behavior change on valid plugins.
3. Phase 3: implement 4.4–4.6 (imports, PEP 723, reconciliation), ship with 6.3/6.4/6.5 tests. New MAJOR report class appears on plugins with the bug.
4. Phase 4: implement 4.7 (antipatterns), ship with tests.
5. Version bump to 2.13.0 (minor — new diagnostic class, no backward-incompatible output changes).
6. Re-validate the top 10 plugins in `emasoft-plugins` marketplace to surface latent instances of the same bug.

All four phases land in one commit sequence for this TRDD.

## 8. Non-goals

- Shell semantic analysis beyond tokenizing simple-commands separated by `; && || |`.
- Validating non-Python hook scripts for runtime deps (JS/TS node_modules resolution is out of scope for now — can be a follow-up TRDD).
- Detecting dynamic imports (`importlib.import_module`, `__import__`) — AST-only, static analysis only.

## 8.5. `uvx` vs `uv run --script` (recommendation-design note)

Published for the avoidance of doubt after a user question during TRDD review:

**The validator recommends `uv run --script`, not `uvx`.** They are NOT
interchangeable:

| Tool | Purpose | Can target a local `.py` file with PEP 723 metadata? |
|---|---|---|
| `uv run --script foo.py` | Run a local script; resolve deps from the script's PEP 723 inline metadata block | **Yes — purpose-built for this.** |
| `uvx pkg` / `uv tool run pkg` | Run an installable PyPI package (or git URL) via its `[project.scripts]` entry-point | **No.** `uvx /path/to/pss_hook.py` tries to resolve `pss_hook.py` as a PyPI package name and fails. There is no `uvx --script` flag. |

Secondary reasons `uv run --script` is the right primitive for hook scripts:

1. **Cache invalidation**: `uv run --script` keys its cache on the script's
   PEP 723 metadata hash. When a plugin update bumps a dependency range
   (e.g. `pycozo>=0.7.6 → >=0.8`), the cache auto-invalidates on the next
   run. `uvx`-installed tools persist across invocations and require
   explicit `uv tool upgrade` to refresh.
2. **No user-visible state**: `uv run --script` operates silently in its
   cache. `uvx` installations show up in `uv tool list` and pollute the
   user's tool namespace — bad for a hook that runs in someone else's
   environment.
3. **Packaging burden**: to use `uvx`, the plugin would have to publish
   the hook as a pip-installable package (PyPI release, pyproject.toml
   with `[project.scripts]` entry-point, version bumps tied to release).
   `uv run --script` needs only a `# /// script` header comment.

The reconciliation MAJOR message for `interpreter-python` mode therefore
explicitly calls out `uvx` as a non-substitute, so plugin authors who
reach for the shorter command don't mistakenly "fix" the warning by
switching to a tool that doesn't support their use case.

## 9. References

- Incident transcript: supplied by user 2026-04-17 in session; preserved in this TRDD's section 1 for posterity.
- Claude Code plugin docs: https://code.claude.com/docs/en/plugins-reference.md — documents the `${CLAUDE_PLUGIN_DATA}` SessionStart install pattern.
- PEP 723 — Inline script metadata: https://peps.python.org/pep-0723/
- uv script runner: https://docs.astral.sh/uv/guides/scripts/#running-a-script-with-dependencies

## Approval log

- 2026-08-25T17:25:05+0200 — CLOSED as complete by the CPV session (board drain; authority delegated by USER 2026-08-25). SHIPPED — extract_script_paths live at validate_hook.py:1270 (triage batch_aa)
