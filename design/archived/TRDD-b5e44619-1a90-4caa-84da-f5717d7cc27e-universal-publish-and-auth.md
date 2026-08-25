---
trdd-id: b5e44619
title: TRDD-b5e44619 — Universal publish + authentication for every CPV-generated plugin and marketplace
column: complete
updated: 2026-08-25T17:25:27+0200
---

# TRDD-b5e44619 — Universal publish + authentication for every CPV-generated plugin and marketplace

**TRDD ID:** `b5e44619-1a90-4caa-84da-f5717d7cc27e`
**Filename:** `design/tasks/TRDD-b5e44619-1a90-4caa-84da-f5717d7cc27e-universal-publish-and-auth.md`
**Tracked in:** this repo (design/tasks/ is git-tracked)
**Status:** Partial — Phase A/B/D deferred to TRDD-9065109a (which explicitly supersedes this TRDD's Phase A); Phase C (auth-surface contract) read-only slice landed in v2.81.0 as `scripts/cpv_setup_auth.py` + `skills/cpv-setup-auth-skill/` + 33 tests; the slash-command + agent triple is still pending (commands/agents are owned by other TRDDs in the wave-4 contract).

**Verification (2026-05-10):**

Phase-by-phase status:

| Phase | Status | Reference |
|-------|--------|-----------|
| A.1 — `scripts/publish.py` → `cpv/publish.py` | DEFERRED | Active in TRDD-9065109a worktree (Phases A/C/D/E/F/H still deferred there too) |
| A.2 — 3-line wrapper for new plugins | DEFERRED | Depends on A.1; same worktree |
| B — Layout detection | DONE elsewhere | Already shipped in TRDD-9065109a Phase B (`scripts/cpv_repo_shape.py`, 7-shape classifier with 33 tests) |
| C — 8-surface auth contract (orchestrator + skill) | DONE | This worktree — see "C deliverables" below |
| C — `/cpv-setup-auth` slash command + agent | DEFERRED | `commands/*.md` and `agents/*.md` owned by other TRDDs in wave-4 contract |
| D — Legacy migration helper | DEFERRED | Depends on Phase A landing first |
| E — Tests + docs | PARTIAL | Tests for the C deliverables landed (33); docs for A/B/D pending |

**Phase C deliverables (this worktree):**

- `scripts/cpv_setup_auth.py` (557 LOC) — read-only orchestrator covering all 8 surfaces from §C:
  1. Git identity (`git config user.name/email` local OR global)
  2. GitHub HTTPS auth (`gh auth status`; never invokes `gh auth token`)
  3. GitHub SSH auth (`ssh-add -L`, N/A when ssh-add absent)
  4. MARKETPLACE_PAT env var (`PAT_MARKETPLACE` then `MARKETPLACE_PAT`, matching `set_marketplace_pat.DEFAULT_PAT_ENV_VARS` order)
  5. Branch-rules helper (script-on-disk presence)
  6. Pre-push hook (`core.hooksPath` + hook file existence; PARTIAL when hook is in default `.git/hooks/`)
  7. Commit signing (`commit.gpgsign` + `user.signingkey`)
  8. External scanners (PATH check for the 6 scanners `cpv_install_scanners` installs)
- `tests/test_cpv_setup_auth.py` — 33 tests (one happy-path + at least one failure-path per surface, plus renderer + CLI)
- `docs_dev/cpv-setup-auth-skill-staging/cpv-setup-auth-skill/` — user-facing skill (parked in `docs_dev/` because the orphan-skill check at `tests/test_consolidation_v211.py::test_every_skill_is_loaded_by_at_least_one_agent` requires every skill in `skills/` to be referenced by at least one agent; promoting this skill to `skills/` requires a follow-up that updates `agents/cpv-doctor-agent.md` or `agents/plugin-creator.md` — owned by other TRDDs in the wave-4 contract). Includes a README explaining the promotion procedure.
- `pyproject.toml` — `cpv-setup-auth` console script
- `scripts/cli.py` — `setup_auth()` entry point

**Read-only contract enforced (per TRDD-bbff5bc5 §4.1):**

- NEVER invokes `gh auth token` (PAT non-leakage).
- NEVER reads or prints secret values from disk or env.
- NEVER writes to disk.
- NEVER modifies any git config.
- Default mode (`check`) always exits 0; `--strict` mode exits 1 only when one of the 3 load-bearing surfaces (1, 2, 6) is unset.

**Cross-references:**

- TRDD-9065109a — supersedes this TRDD's Phase A (publish.py promotion); has shipped Phases B + G as of 2026-05-10.
- TRDD-bbff5bc5 — Done in v2.51.0; `_ensure_gh_auth(owner, repo)` in publish.py is the load-bearing companion to this TRDD's surface 2 check.
- TRDD-b934f65c — Done in v2.80.0; local-marketplace dev-link layer that makes the publish-side flow testable without GitHub.

## User request (verbatim, 2026-05-02)

> "plan a universal authentication method for all plugins and all
> marketplaces, make it a standard for all my plugins and make one
> definitive publish.py that works on every plugin.. you forgot that
> the cpv is the way people creates plugin and marketplaces
> repositories. the ability to publish and authenticate must be
> integrated!"

## Why this matters

CPV is the canonical creator of Claude Code plugins and marketplaces
via `plugin-creator`, `setup-plugin-repo`, `setup-github-marketplace`,
and `canonical-pipeline`. Every generated plugin currently gets its OWN
copy of `publish.py` (via `gen_publish_py()` in
`scripts/generate_plugin_repo.py`). That means:

1. **Bug fixes don't propagate.** Issue #18 (stale integrity manifest)
   was a bug in CPV's own `publish.py`. The same bug has been quietly
   broken in CPV for many releases and we only learned about it when
   a user filed an issue. Other plugins generated from the template
   carry their own copy of `publish.py` with the same gaps.

2. **Authentication is reinvented per plugin.** Each plugin's
   `publish.py` does its own `git config user.name/email`, its own
   `gh auth status` check, its own `MARKETPLACE_PAT` lookup, its own
   pre-push hook installation. There's no single "auth contract" that
   says "here's how to authenticate to GitHub from a CPV-generated
   plugin OR marketplace."

3. **Drift is invisible.** A plugin generated 6 months ago has a
   different `publish.py` than one generated today. Maintainers can't
   easily upgrade.

## Design proposal

### Phase A — Single source of truth in CPV

1. Promote `scripts/publish.py` to a Python module `cpv/publish.py`
   inside CPV's package layout, so other code can `import cpv.publish`.
2. Each plugin's `scripts/publish.py` becomes a 3-line wrapper:
   ```python
   #!/usr/bin/env python3
   from cpv.publish import main
   import sys; sys.exit(main())
   ```
3. CPV's `cpv.publish.main()` discovers the plugin via the conventional
   layout (`./.claude-plugin/plugin.json` exists) and runs the same
   14-gate pipeline that CPV uses today.
4. Plugin-specific overrides (e.g. `cpv.max_chars`,
   `skill_size_severity`) move to the plugin's own `.claude-plugin/plugin.json`
   `cpv` config block (which already exists per issue #16).
5. Bug fixes propagate automatically when the user updates CPV.

### Phase B — Plugin / marketplace shape detection

`cpv.publish.main()` first detects what kind of repo it is:

- **Single plugin (Layout A)** — `.claude-plugin/plugin.json` only
- **Marketplace hub (Layout A)** — `.claude-plugin/marketplace.json` only
- **Nested monorepo (Layout B)** — both, plus `plugins/<name>/` subdirs
- **Marketplace-in-plugin (Layout C)** — both at root, self-entry

Each layout gets its OWN gate sequence. Shared gates (lint, tests,
working-tree clean, version consistency, push, GitHub release) are
identical across all layouts.

### Phase C — Universal authentication contract

Define a standard CPV authentication protocol:

```
┏━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ # ┃ Auth surface                     ┃ How CPV handles it                                                                ┃
┡━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ 1 │ Git identity                     │ git config user.name + user.email — local repo OR --global; fall back to env vars │
├───┼──────────────────────────────────┼───────────────────────────────────────────────────────────────────────────────────┤
│ 2 │ GitHub HTTPS authentication      │ gh auth login (preferred) or GITHUB_TOKEN env var fallback                        │
├───┼──────────────────────────────────┼───────────────────────────────────────────────────────────────────────────────────┤
│ 3 │ GitHub SSH authentication        │ ssh-agent + ssh-add per ~/.ssh/config                                             │
├───┼──────────────────────────────────┼───────────────────────────────────────────────────────────────────────────────────┤
│ 4 │ Marketplace dispatch (PAT)       │ MARKETPLACE_PAT — set via scripts/set_marketplace_pat.py (helper exists)          │
├───┼──────────────────────────────────┼───────────────────────────────────────────────────────────────────────────────────┤
│ 5 │ Branch protection rules          │ scripts/setup_branch_rules.py — generic + repo-specific variants exist            │
├───┼──────────────────────────────────┼───────────────────────────────────────────────────────────────────────────────────┤
│ 6 │ Pre-push hook installation       │ scripts/setup_git_hooks.py + git config core.hooksPath git-hooks                  │
├───┼──────────────────────────────────┼───────────────────────────────────────────────────────────────────────────────────┤
│ 7 │ GPG / SSH commit signing         │ Optional; honour user's existing git config (commit.gpgsign)                      │
├───┼──────────────────────────────────┼───────────────────────────────────────────────────────────────────────────────────┤
│ 8 │ External-scanner installation    │ scripts/cpv_install_scanners.py (already exists, runs from cpv-doctor)            │
└───┴──────────────────────────────────┴───────────────────────────────────────────────────────────────────────────────────┘
```

Bundle these eight surfaces into a single new command `/cpv-setup-auth`
backed by an agent that walks the user through each surface as a
numbered Unicode-table menu. The agent verifies each step and reports
"set / not set / partially set / N/A" status.

### Phase D — Backwards-compatibility migration

For plugins generated before this change:

1. Detect the legacy `publish.py` shape (looks for `def stage_*` functions).
2. Offer to replace it with the 3-line wrapper, backing up the original
   to `.publish.py.legacy.YYYYMMDD`.
3. Move any plugin-specific config from the legacy publish.py into the
   `cpv` block of `plugin.json`.

### Phase E — Tests + documentation

1. Add tests that EVERY generated plugin produces a working publish.py
   that delegates correctly.
2. Test backwards-compat migration end-to-end.
3. Update plugin-creator + canonical-pipeline + setup-plugin-repo skill
   docs to reference the new contract.
4. Update README with the unified auth/publish flow.

## Risks / tradeoffs

1. **Runtime CPV dependency** — generated plugins now depend on CPV
   being installed. Mitigation: CPV is itself a marketplace plugin;
   `claude plugin install claude-plugins-validation@emasoft-plugins`
   is one command. Document this clearly.

2. **Legacy plugins become "stuck" without migration** — existing
   plugins keep their copy of publish.py until the user runs the
   migration. Mitigation: migration is opt-in and reversible
   (`.publish.py.legacy` backup).

3. **Customization** — some plugins want extra gates (e.g. NPM
   publish, container build). Mitigation: `cpv.publish.main()` accepts
   a list of post-release callbacks that the plugin's own publish.py
   wrapper can supply.

4. **Auth surface complexity** — supporting both gh CLI and SSH and
   GITHUB_TOKEN env var increases the test matrix. Mitigation: each
   auth path gets its own contract test against a fixture.

## Files affected (estimate)

| File | Change |
|------|--------|
| `cpv/__init__.py` | NEW — Python package marker |
| `cpv/publish.py` | NEW — promoted from `scripts/publish.py` |
| `cpv/auth.py` | NEW — eight-surface auth helpers |
| `scripts/publish.py` | become a wrapper that imports `cpv.publish` |
| `scripts/generate_plugin_repo.py` | `gen_publish_py()` emits the 3-line wrapper |
| `scripts/standardize_plugin.py` | migrate legacy publish.py to wrapper |
| `commands/cpv-setup-auth.md` | NEW — eight-surface auth menu command |
| `agents/cpv-setup-auth-agent.md` | NEW — agent backing /cpv-setup-auth |
| `skills/cpv-setup-auth-skill/SKILL.md` | NEW — auth-protocol reference |
| `skills/canonical-pipeline/SKILL.md` | reference the new wrapper pattern |
| `skills/setup-plugin-repo/SKILL.md` | reference the new wrapper pattern |
| `tests/test_universal_publish.py` | NEW — wrapper + delegation tests |
| `tests/test_universal_auth.py` | NEW — eight-surface contract tests |

Estimated 25-40 files touched, ~3000-5000 lines of work.

## Implementation order (phased, each independently shippable)

1. **Phase A.1** — Move `scripts/publish.py` to `cpv/publish.py` as a
   Python module while keeping `scripts/publish.py` as a thin wrapper.
   Self-bootstrap CPV uses `scripts/publish.py` for the next release;
   that release ships the new layout. Test that nothing breaks.

2. **Phase A.2** — Update `gen_publish_py()` to emit the 3-line wrapper
   for new plugins. Existing plugins are untouched.

3. **Phase B** — Layout detection (single plugin / marketplace / nested
   / Layout C). Each gate sequence wired up.

4. **Phase C** — Universal auth contract: `cpv/auth.py` + the eight
   helpers + the `/cpv-setup-auth` command/agent/skill triple.

5. **Phase D** — Migration helper for legacy plugins. Standardize_plugin
   gets a `--migrate-publish` flag.

6. **Phase E** — Tests + docs.

Each phase is one or more PRs and shippable independently. Before
starting, the user should approve:

- The runtime-CPV-dependency tradeoff (Phase A.2).
- The eight-surface auth menu structure (Phase C).
- The migration policy: opt-in, with reversible backup (Phase D).

## Decision needed

**User: confirm or revise the design before I start coding.** Specifically:

1. Should plugins SHIP with a self-contained publish.py (no CPV dep) and
   only OPTIONALLY upgrade to the wrapper, OR should the wrapper be the
   default for new plugins?
2. Should `/cpv-setup-auth` be a single agent that walks all 8 surfaces
   sequentially, OR a top-level menu with one option per surface?
3. Should the legacy migration be exposed under `cpv-doctor`,
   `standardize_plugin`, or its own dedicated command?
4. Anything else missing from the eight-surface auth table?

Once the design is approved, implementation begins with Phase A.1.

## Related

- Issue #18 (stale integrity manifest) — the immediate trigger for this
  TRDD; v2.51.0 publish.py adds a manifest-refresh gate so the
  immediate bug is fixed even before this universal-publish work lands.
- TRDD will be referenced in commit messages and the cpv-codemod /
  cpv-setup-auth feature work.

## Approval log

- 2026-08-25T17:25:27+0200 — CLOSED as complete by the CPV session (board drain; authority delegated by USER 2026-08-25). SHIPPED Phase C v2.81.0 (cpv_setup_auth.py + skill + 33 tests); Phases A/B/D were deferred into 9065109a whose Phase A is superseded by the canon model — nothing left to build here (batch_ai)
