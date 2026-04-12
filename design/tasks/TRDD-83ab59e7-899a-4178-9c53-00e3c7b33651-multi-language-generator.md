# TRDD-83ab59e7-899a-4178-9c53-00e3c7b33651 — Multi-language Plugin Generator

**TRDD ID:** `83ab59e7-899a-4178-9c53-00e3c7b33651`
**Filename:** `design/tasks/TRDD-83ab59e7-899a-4178-9c53-00e3c7b33651-multi-language-generator.md`
**Tracked in:** this repo (design/tasks/ is git-tracked)

**Status:** Not started
**Priority:** MEDIUM
**Effort:** MEDIUM-LARGE
**Source:** `docs_dev/cpv_workflow_audit_20260412.md` section B4 / C5

## Problem

`scripts/generate_plugin_repo.py` is hardcoded to Python. It emits:

- `pyproject.toml` (no package.json / Cargo.toml / go.mod / deno.json)
- `--python-version` flag (no equivalent for other languages)
- Python-specific `.gitignore` patterns
- CI workflows that run `ruff` + `mypy` (no ESLint / cargo / go vet / deno lint)
- `scripts/publish.py` assuming Python

Plugin authors writing JS/TS/Rust/Go/Deno/Dart plugins either fork the
generator or generate a Python skeleton they must strip.

## Scope

Add a `--language` flag with enum:

```
--language {python|js|ts|rust|go|deno|elixir|ruby|java|kotlin}
```

Default: `python` (preserves current behavior).

### Per-language emissions

| Language | Manifest | Lint | Test | Lockfile | Gitignore template |
|---|---|---|---|---|---|
| python | pyproject.toml | ruff | pytest | uv.lock | python |
| js | package.json | eslint | jest/vitest | package-lock.json | node |
| ts | package.json + tsconfig.json | eslint+tsc | vitest | package-lock.json | node |
| rust | Cargo.toml | cargo clippy | cargo test | Cargo.lock | rust |
| go | go.mod | golangci-lint | go test | go.sum | go |
| deno | deno.json | deno lint | deno test | deno.lock | deno |
| elixir | mix.exs | credo | mix test | mix.lock | elixir |
| ruby | Gemfile | rubocop | rspec | Gemfile.lock | ruby |
| java | pom.xml | checkstyle | junit | n/a | java |
| kotlin | build.gradle.kts | detekt | junit | n/a | kotlin |

### CI workflow template per language

Each `.github/workflows/validate.yml` must run:

1. Language-specific install step (setup-node / setup-go / setup-python / cargo)
2. Language-specific lint step
3. Language-specific test step
4. The CPV validator (language-agnostic)

### publish.py template per language

The publish script bumps the version in the correct manifest:

- python: pyproject.toml `version = "X.Y.Z"` and plugin.json `"version": "X.Y.Z"`
- js/ts: package.json `"version"` and plugin.json
- rust: Cargo.toml `[package] version =` and plugin.json
- go: no version bump in go.mod; use git tag + plugin.json
- deno: deno.json `"version"` and plugin.json

## Detection of existing language

Add `detect_language(plugin_root)` helper returning the list of languages
detected in the plugin root. Used by:

- `generate_plugin_repo.py --language auto` (infer from first existing manifest)
- `validate_plugin.py` to pick which linters to run
- `standardize_plugin.py` to audit correct CI workflow

Files to check (in order):
1. `pyproject.toml` / `setup.py` / `requirements.txt` → python
2. `package.json` → js or ts (check `tsconfig.json` or `"type": "module"`)
3. `deno.json` / `deno.jsonc` → deno
4. `Cargo.toml` → rust
5. `go.mod` → go
6. `mix.exs` → elixir
7. `Gemfile` → ruby
8. `pom.xml` / `build.gradle[.kts]` → java/kotlin
9. `pubspec.yaml` → dart/flutter

## Success criteria

- [ ] `generate_plugin_repo.py --language ts --name my-plugin` creates a
      valid TypeScript plugin that passes `validate_plugin.py`
- [ ] Generated plugins for each language have CI workflows that pass
- [ ] `detect_language()` used in `standardize_plugin.py` picks the right linter
- [ ] All existing Python-plugin generation tests still pass
- [ ] At least one fixture plugin per language exists in `tests/fixtures/`
