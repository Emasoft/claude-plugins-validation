# TRDD-79638eb6-bf72-4627-ab35-984ca5255041 — Project Drift + Submodule + Lockfile Detection

**TRDD ID:** `79638eb6-bf72-4627-ab35-984ca5255041`
**Filename:** `design/tasks/TRDD-79638eb6-bf72-4627-ab35-984ca5255041-drift-detection-autodetect.md`
**Tracked in:** this repo (design/tasks/ is git-tracked)

**Status:** Done (2026-05-10) — implementation already shipped in v2.38.x.
This commit adds the missing test coverage (92 tests) for all four parts:
detect_languages (34 tests), detect_lockfiles (26 tests), and the wired-in
validate_submodule_containment / validate_project_languages /
validate_lockfiles / standardize_plugin.audit_drift functions (32 tests).
All 4605 suite-wide tests pass.
**Priority:** LOW
**Effort:** MEDIUM
**Source:** `docs_dev/cpv_workflow_audit_20260412.md` sections B2/B4/B5/B7 / C7/C8/C9/C10

## Problem

CPV has no awareness of:

1. **Project drift** — plugin.json `tools` field vs actual usage in code
2. **Git submodule containment** — plugin inside a parent repo's submodule
3. **Multi-language config files** — only pyproject.toml / Cargo.toml /
   go.mod recognized; no package.json, deno.json, mix.exs, pom.xml, etc.
4. **Lockfile presence/drift** — no scan for uv.lock / pnpm-lock.yaml /
   Cargo.lock / etc.

## Scope

### Part 1 — Language detection helper

New module: `scripts/detect_language.py`

```python
def detect_languages(plugin_root: Path) -> list[str]:
    """Return a sorted list of detected languages.

    Detection order (first match wins for primary, all matches returned):
    - pyproject.toml / setup.py / requirements.txt → 'python'
    - package.json → 'js' (or 'ts' if tsconfig.json or type=module+.ts files)
    - deno.json / deno.jsonc → 'deno'
    - Cargo.toml → 'rust'
    - go.mod → 'go'
    - mix.exs → 'elixir'
    - Gemfile → 'ruby'
    - pom.xml / build.gradle[.kts] → 'java' or 'kotlin'
    - pubspec.yaml → 'dart'
    - CMakeLists.txt / Makefile → 'c' or 'cpp' (via .c/.cpp file presence)
    """
```

Used by: `validate_plugin.py` linting step, `standardize_plugin.py` CI audit,
`smart_exec.py` linter selection.

### Part 2 — Lockfile detection

New module: `scripts/detect_lockfiles.py`

```python
LOCKFILES = {
    'uv.lock': 'python',
    'poetry.lock': 'python',
    'Pipfile.lock': 'python',
    'package-lock.json': 'js',
    'pnpm-lock.yaml': 'js',
    'yarn.lock': 'js',
    'bun.lockb': 'js',
    'deno.lock': 'deno',
    'Cargo.lock': 'rust',
    'go.sum': 'go',
    'Gemfile.lock': 'ruby',
    'mix.lock': 'elixir',
}

def detect_lockfiles(plugin_root: Path) -> dict[str, str]:
    """Return {lockfile_path: language} for each lockfile found."""
```

Used by `validate_plugin.py` to emit:
- NIT: lockfile present but language not detected (orphan lockfile)
- NIT: language detected but no lockfile (binary distribution risk)
- WARNING: lockfile in .gitignore (deps won't be pinned in CI)

### Part 3 — Submodule detection

In `validate_plugin.py` startup:

```python
def is_plugin_in_submodule(plugin_root: Path) -> str | None:
    """Returns parent repo path if plugin_root is inside a git submodule.

    Walks up the parent chain looking for .gitmodules that lists this path.
    Returns the parent repo root on match, None otherwise.
    """
```

Emit INFO on match: `"Plugin is a submodule of <parent_repo>. CI on the
parent repo will not run this plugin's pipeline automatically."`

### Part 4 — Project drift check

New function in `standardize_plugin.py::audit_drift`:

```python
def audit_drift(plugin_root: Path, report: AuditReport) -> None:
    """Cross-check plugin.json declarations against language config.

    Checks:
    1. Tools in plugin.json `agents[*].tools` should not reference tools
       missing from the installed Claude Code (can't really check) — skip.
    2. Dependencies in pyproject.toml `[project.dependencies]` should be
       imported somewhere in scripts/ or hooks/.
    3. MCP servers declared in plugin.json should have a command that's
       resolvable (or at least the referenced package is in deps).
    4. Skills declared with `allowed-tools: [Read, Write]` should have an
       actual need for Write (heuristic: body contains "file" references).
    """
```

Flag as MINOR for deps-not-used, NIT for declared-but-unused skill tools.

## Success criteria

- [ ] `scripts/detect_language.py` returns correct language for each
      fixture in `tests/fixtures/`
- [ ] `standardize_plugin.py` CI audit uses the correct linter based on
      detected language (not hardcoded to Python)
- [ ] `validate_plugin.py` emits INFO when plugin is in a submodule
- [ ] `audit_drift` catches a test fixture with `requests` in pyproject
      but not imported anywhere
- [ ] All existing tests still pass

## Out of scope

- Fixing the drift (only detecting)
- Auto-adding missing deps
- Lockfile regeneration
