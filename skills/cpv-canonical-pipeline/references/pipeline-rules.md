# Pipeline Rules — Mandatory for ALL Plugin Operations

## Table of Contents

- [Pre-Push Hook: The Quality Gate](#pre-push-hook-the-quality-gate)
- [Fix-All Mandate](#fix-all-mandate)
- [Running CPV Scripts](#running-cpv-scripts)
- [Processing Validation Output](#processing-validation-output)
- [GitHub Secrets](#github-secrets)
- [CI Workflow Dependencies](#ci-workflow-dependencies)
- [Marketplace Notification](#marketplace-notification)
- [All Scripts Are Python](#all-scripts-are-python)
- [Binary Plugins](#binary-plugins)
- [README Requirements](#readme-requirements)
- [Pre-Publish Local Dry-Run](#pre-publish-local-dry-run)
- [Post-Push CI Verification](#post-push-ci-verification)
- [Generated-Pipeline Reliability Contract](#generated-pipeline-reliability-contract-v21340)
- [Mega-Linter Configuration](#mega-linter-configuration)
- [Common Fixes Reference](#common-fixes-reference)

## Checklist

- [ ] Pre-push hook installed and enforcing --strict
- [ ] All findings above WARNING fixed before push
- [ ] CPV scripts invoked with `uv run --with pyyaml python`
- [ ] GitHub secrets set via `set_marketplace_pat.py` (never pipe to `gh secret set`)
- [ ] Plugin scripts are all Python (no ad-hoc shell)
- [ ] README has install/update/uninstall sections
- [ ] Dry-run passes before the first real publish
- [ ] Generated CI pins the CPV ref (not HEAD) and the required `Test` aggregate-gate job is present
- [ ] "Done" = green CI watched to green, not "files generated"

These rules MUST be followed by every agent, command, and skill that creates, publishes, standardizes, or fixes a plugin repository.

## Pre-Push Hook: The Quality Gate

The pre-push hook is the **keystone of the entire pipeline**. It runs 4 gates in sequence and blocks the push if ANY gate fails:

| Gate | What It Does | Blocks On |
|------|-------------|-----------|
| 1. Version bump | Compares local vs remote version | Same version (must bump) |
| 2. Lint | `ruff check scripts/ tests/` | Any lint error |
| 3. Validate | `validate_plugin.py . --strict` | CRITICAL, MAJOR, MINOR, NIT (exit codes 1-4) |
| 4. Tests | `pytest tests/ -q` | Any test failure |

**Only WARNING/INFO/PASSED findings pass through** — those map to exit
code 0 under both `--strict` and non-strict. CRITICAL/MAJOR/MINOR/NIT
map to exit codes 1-4 (NIT blocks only under `--strict`) and fail the
gate. There is no exit code 5; WARNING never blocks.

## Fix-All Mandate

Before publishing or pushing a plugin, ALL CRITICAL, MAJOR, MINOR, and NIT issues MUST be fixed. The workflow is:

```
validate --strict → fix issues → re-validate → repeat until only WARNINGs remain → then proceed
```

Never skip this loop. Never publish with unfixed issues. The pre-push hook will block you anyway.

## Running CPV Scripts

Always use this exact command form when running CPV scripts from outside the CPV project:
```bash
uv run --with pyyaml python "${CLAUDE_PLUGIN_ROOT}/scripts/<script>.py" <args>
```
Without `--with pyyaml`, you get `ModuleNotFoundError: No module named 'yaml'`.

## Processing Validation Output

Always strip ANSI color codes and use macOS-compatible grep:
```bash
... 2>&1 | sed 's/\x1b\[[0-9;]*m//g'
```
Use `grep -oE` (extended regex), NOT `grep -oP` (Perl regex — unavailable on macOS).

## GitHub Secrets

Always use `--body` flag:
```bash
gh secret set MARKETPLACE_PAT --repo <owner>/<repo> --body "$MARKETPLACE_PAT"
```
Piping via `echo | gh secret set` does NOT work reliably.

Check if the env var exists first: `test -n "$MARKETPLACE_PAT"`

## CI Workflow Dependencies

CI workflows MUST use `uv sync --extra dev` (NOT just `uv sync`).
ruff, pytest, mypy, pyyaml are in `[project.optional-dependencies] dev`.
Without `--extra dev`, ALL CI runs fail with "Failed to spawn: ruff".

## Marketplace Notification

After `standardize_plugin.py --fix` generates `notify-marketplace.yml`, ALWAYS update:
- `MARKETPLACE_OWNER` → actual marketplace owner (e.g., `Emasoft`)
- `MARKETPLACE_REPO` → actual marketplace repo name (e.g., `emasoft-plugins`)

The generated values are placeholders that will NOT work.

## All Scripts Are Python

The plugin repo pipeline is always Python:
- Pre-push hook: Python script
- publish.py: Python script
- setup_git_hooks.py: Python script
- All validation scripts: Python

GitHub YAML workflows ONLY:
- Run read-only checks (lint, validate, test)
- Create releases (attach artifacts)
- Send notifications (repository_dispatch)
- NEVER commit code, create PRs, or modify the repo

## Binary Plugins

Compiled sources live in `src/<component>/`. Pre-compiled binaries in `src/<component>/bin/`.
Compilation happens **locally** via `publish.py` — NOT on GitHub CI.
`build-binaries.yml` is a FALLBACK only for CI-only environments.

## README Requirements

README MUST include:
- Badge markers: `<!--BADGES-START-->` / `<!--BADGES-END-->`
- Components table (auto-generated from commands/agents/skills/hooks)
- Install section (marketplace + GitHub + manual)
- Uninstall section
- Update section
- Troubleshooting section (3 required topics: hook path not found, old version after update, restart required)

## Pre-Publish Local Dry-Run

Before the first push, ALWAYS verify the generated pipeline works locally:
1. Run the pre-push hook: `echo "" | uv run python git-hooks/pre-push`
2. Run publish.py dry-run: `uv run python scripts/publish.py --dry-run`
Both must complete without crashes. This catches import errors, missing deps, and template bugs.

## Post-Push CI Verification

Since v5.1.1 `publish.py` does this automatically as its `[post-release]` stage
(see Reliability Contract item 7) — so on a canon pipeline the check below is a
BACKSTOP, not the primary mechanism. Run it whenever the publish reported
`UNVERIFIED` (no `gh`, no runs found, or the wait expired), or after any push
that did not go through `publish.py`:

```bash
gh run list --repo <owner>/<plugin-name> --limit 5
```

If any workflow failed, investigate with `gh run view <id> --log-failed | head -30`.
Fix the cause on the plugin side and publish a follow-up patch. Do NOT leave
failing CI as the final state, and NEVER mute a check to make it green.

**Why this is not optional even though the publish "succeeded":** the release
push bypasses the branch ruleset's required status checks (that bypass is what
lets a scripted release run at all), so a green publish says nothing about CI.
The release is already public by the time CI reports — which is exactly why a
red result means "ship a follow-up patch", not "the publish failed".

## Generated-Pipeline Reliability Contract (v2.134.0)

The generator applies six reliability fixes so a scaffolded/upgraded plugin's first GitHub CI run is GREEN — and its release is actually installable — rather than the user's failure notification. The skill's guidance MUST match what the generator emits:

1. **CI pins the CPV ref** — the generated `ci.yml`/`release.yml`/`publish.py` pin the CPV install to an explicit **`@v<ver>` git tag** (the `git+` install URL carries a `@v<ver>` suffix), NOT CPV HEAD. So a new CPV release never silently red-lights a downstream plugin. The upgrade flow updates the pin DELIBERATELY (and re-runs CI to green) rather than letting every plugin track HEAD.
2. **Validate steps skip the live integrity fetch + carry a real timeout** — every CPV-validate step sets `env: { PLUGIN_SKIP_GITHUB_INTEGRITY: "1" }` and a `timeout-minutes`. On a fresh-checkout runner the local manifest already matches the code, so the `raw.githubusercontent.com` anchor adds latency/hang risk but no security. **Never add `CLAUDE_PRIVATE_USERNAMES` to a CI step** (issue #140): that env names the usernames CPV treats as PRIVATE, so seeding it with the public `${{ github.repository_owner }}` makes CPV flag every `github.com/<owner>/` URL + the owner no-reply email as a CRITICAL private-path leak and fails the validate job under `--strict`. A CI runner has no developer local-username to protect — the `CLAUDE_PRIVATE_USERNAMES="$(whoami)"` form is for LOCAL scans only.
3. **The `test` matrix is fronted by an aggregate gate job named exactly `Test`** (`needs: [test]`, succeeds only if the matrix passed). A bare required `Test` context against a matrix that reports `Test (ubuntu-latest)` / `Test (macos-latest)` is NEVER satisfied → PRs stuck pending forever; the aggregate job satisfies the required branch context.
4. **`notify-marketplace` no-ops when `MARKETPLACE_PAT` is absent** — the job is guarded so a repo without the secret does not surface a red associated workflow on the release.
5. **"Done" = green CI, not "files generated"** — the creator/fixer agents PUBLISH then watch every required run with `gh run watch <run-id> --exit-status`, treating a red run as the next fix iteration (read failing job → fix the cause on the plugin side → re-publish → re-watch; `gh run rerun --failed` for transient infra; NEVER mute a check). See `agents/cpv-plugin-creator-agent.md` "CI-green guarantee phase" and `agents/cpv-plugin-fixer-agent.md` §7d.
6. **`publish.py` pushes the dependency-resolver tag `{name}--v{version}`** (DOUBLE hyphen, CC 2.1.110) alongside the release tag `v{version}`, in the SAME atomic push (one `git push origin HEAD v{version} {name}--v{version}` argv) so a release can never ship with one ref and not the other. A version-constrained plugin dependency resolves **only** against `{plugin-name}--v{version}`: without that tag, `claude plugin install` on a dependent fails with `no-matching-tag` and the dependent is DISABLED — a release that looks fine and is silently un-dependable. The generator has emitted this stage since v2.156.0; since v2.158.0 `standardize --fix` also MIGRATES an **existing** `publish.py` that lacks it, injecting only that stage (idempotent; a `publish.py` too customized to migrate safely is left byte-identical and reported, never half-migrated). Do NOT hand-roll `claude plugin tag --push` into a pipeline: it is the wrong layer, and `claude plugin tag <name>` takes a **path**, not a tag name — it silently creates nothing.

7. **`publish.py` VERIFIES CI is green on the released commit** (v5.1.1, `stage_verify_ci_green`, printed as `[post-release]`). This closes a real hole rather than adding ceremony: the release push targets the default branch directly and the maintainer role holds `bypass_mode: always` on the branch ruleset, so GitHub prints `Bypassed rule violations … required status checks are expected` and lets it through. That bypass is deliberate — it is what makes a scripted release possible at all — but it means **the required status checks never actually gate a release**: the tag, the GitHub release and the marketplace notification are all public before CI has said a word. Point 5 above was the only thing covering that gap, and it lives in agent PROSE, which is skippable — the identical defect v2.157.0 fixed one gate earlier when `ci-preflight` was wired in as Gate 3b. The stage **never aborts the publish**, and that asymmetry is deliberate rather than a weak gate: by the time it runs the release is already published, so a non-zero exit could not un-ship anything — it would only discard the report that is the whole point. A RED result is surfaced as a loud notice naming the failing runs plus the exact `gh run view --log-failed` command, which is what feeds point 5's fix→re-publish loop. **"Cannot check" is never reported as green** (no `gh`, no runs found, a timeout → `UNVERIFIED` with the reason), and `skipped`/`neutral` conclusions are not failures — a dormant optional workflow reports `skipped` on every release and treating that as RED would cry wolf every time. The generator emits it for new plugins; **`standardize --fix` MIGRATES an existing `publish.py`** that lacks it (idempotent, on ANY `--fix` — NOT gated behind `--force-templates`, because the plugins that need it most are the ones with a customized `publish.py` they cannot safely force-overwrite). The migration is **all-or-nothing**: the stage, its call site and the top-level `import time` are one unit, since landing the call without the function is a `NameError` at publish time in someone else's repo, and landing the function without the call is dead code that silently keeps the gap open while *looking* migrated. A `publish.py` too customized to migrate safely is left **byte-identical** and reported, never half-migrated.

## Mega-Linter Configuration

The `.mega-linter.yml` config must include:
- `COPYPASTE_JSCPD_ARGUMENTS: "--threshold 5"` — 0% is too strict for plugin repos
- `REPOSITORY_CHECKOV_ARGUMENTS: "--skip-check CKV2_GHA_1"` — flags missing top-level workflow permissions, but we set permissions per-job
- `.gitignore` must include `megalinter-reports/` and `mega-linter.log`

**`--force-templates` MERGES `.mega-linter.yml`; it does not overwrite it (issue #165, v2.158.0).** The plugin's file is the BASE — only the canon keys it LACKS are appended, so its values, key order, and the comment paragraphs justifying them survive verbatim. On a key both declare, **the plugin's value is KEPT** and reported (`[merge] kept YOUR value for <key> (canon differs; reconcile by hand if you did not customize it)`). This is what a custom-KEY detector cannot see: the real divergence is a custom **value inside a key canon also declares** — e.g. an author extending canon's `REPOSITORY_CHECKOV_ARGUMENTS` to `"--skip-check CKV2_GHA_1,CKV_DOCKER_2"` because every Dockerfile they ship is an ephemeral run-once container with no HEALTHCHECK. A blind overwrite deleted that suppression and their rationale comment, and their build went red. Do NOT "restore canon" by hand over a kept value without asking the author why it diverges.

**Canon JSON configs merge the other way round.** For `*.json` canon files the CANON is the base — canon wins on a key canon declares (that is the point of `--force-templates`) — and the plugin's OWN extra keys are carried over and reported (`[merge] preserved custom key(s): …`). JSON has no comments, so nothing else can be lost. A plugin that must diverge on a canon-declared JSON key marks it with `cpv.pipeline.intentional_divergence` instead.

## Copy-paste Gate Parity: `.jscpd.json` (issue #143)

The local pre-push gate (`publish.py --gate`) now runs a **jscpd copy-paste
check at parity with CI's Mega-Linter `COPYPASTE_JSCPD`** — so duplication over
the threshold is caught LOCALLY, before the version bump / tag / push / release,
not only on CI after the release is already tagged (the pre-#143 failure mode:
`publish.py` exits 0, tags + releases, then the CI Lint job fails on jscpd).

- **`.jscpd.json` is the single source of truth** for the threshold and the
  ignore globs, read by BOTH the local gate and CI (jscpd auto-discovers
  `.jscpd.json` at the repo root). Its `threshold: 5` matches
  `COPYPASTE_JSCPD_ARGUMENTS: "--threshold 5"`, and its `ignore` globs mirror
  `.mega-linter.yml`'s `FILTER_REGEX_EXCLUDE` (the `*_dev/` submodules,
  `**/fixtures/**`, vendored trees). Tune duplication policy in ONE place and
  both gates stay in lock-step.
- **Graceful degradation.** The local gate needs Node/npx to run jscpd. If
  neither is available it DEGRADES to a non-blocking WARNING and never
  false-blocks a push — but CI's Mega-Linter still enforces the check, so a
  green local gate does NOT guarantee green CI for the copy-paste dimension
  unless Node/npx is installed locally. Install Node/npx for full local parity.
- **Provisioning on upgrade.** `standardize --fix` CREATES `.jscpd.json` (the
  canonical threshold-5 config) when ABSENT and LEAVES an existing one untouched;
  only `--force-templates` refreshes it, and since v2.158.0 that refresh MERGES —
  canon wins on the keys canon declares, the plugin's own extra keys are preserved
  and reported. The audit path only WARNs about a missing `.jscpd.json` or a stale
  `publish.py`.

## Common Fixes Reference

| Issue | Fix |
|-------|-----|
| SKILL.md missing sections | Add: Overview, Prerequisites, Instructions (numbered), Output, Error Handling, Examples, Resources |
| .gitignore gaps | Append missing patterns: __pycache__/, .venv/, .env, dist/, build/, .coverage, .pytest_cache/, .ruff_cache/, node_modules/, *_dev/, reports/ |
| Missing README badges | Add `<!--BADGES-START-->` block with CI, Version, License, Validation badges |
| Missing LICENSE | Create MIT LICENSE file |
| Script not executable | `chmod +x <script>` |
| Ruff lint errors | `uv run ruff check --fix scripts/` then manually fix remaining |
| Missing author.email | Add `"email": "nnn+user@users.noreply.github.com"` to plugin.json |
| Absolute paths | Replace with `${CLAUDE_PLUGIN_ROOT}` or document as intentional system binaries |
