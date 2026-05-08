# Pipeline standards (current)

## Table of Contents

- [Overview](#overview)
- [Whole-repo lint via cpv_lint_engine](#whole-repo-lint-via-cpv_lint_engine)
- [Idempotent publish.py](#idempotent-publishpy)
- [validate_pipeline_script_refs rule](#validate_pipeline_script_refs-rule)
- [Migrating a legacy plugin](#migrating-a-legacy-plugin)

## Overview

Every plugin scaffolded by generate_plugin_repo.py ships with three
guarantees baked in. The plugin-fixer agent migrates legacy plugins to
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
- `skills/plugin-validation-skill/references/*` — reference hooks copied into new plugins

Any reference to a non-existent `scripts/<name>.py` emits MAJOR with
the exact `file:line`.

**Why this matters:** Every time a script is renamed or removed,
multiple consumers silently break. v2.65.0 hit this exactly when the
lint script was removed but CI plus the local pre-push hook still
invoked it. The new rule catches the regression at PR or release time
before any push attempts to use the stale ref.

## Migrating a legacy plugin

Use the plugin-fixer agent's pipeline-migration recipe — see the
fix-validation skill's `references/pipeline-migration.md` for the three
independent migrations:

1. Replace stale `scripts/<name>.py` references in workflows / hooks / templates
2. Consolidate per-language CI lint steps into a single call to the unified engine
3. Add idempotency helpers to publish.py (or regenerate via generate_plugin_repo.py)

Each migration is independently revertable.
