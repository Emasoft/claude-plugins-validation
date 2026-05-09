# Iterative Validate → Fix → Re-validate Loop

## Table of Contents

- [Why a loop](#why-a-loop)
- [Algorithm](#algorithm)
- [Entry points — plugin path vs report path](#entry-points--plugin-path-vs-report-path)
- [Termination and safety](#termination-and-safety)
- [WARNING evaluation rules](#warning-evaluation-rules)
- [Publish-blocking warning categories](#publish-blocking-warning-categories)
- [Truly advisory warnings](#truly-advisory-warnings)
- [Output contract](#output-contract)

## Checklist

Copy this checklist into your fix log and tick each item as you go:

- [ ] Resolve the target (plugin/marketplace path via Path Resolution Protocol, or parse report)
- [ ] Run validation with `--strict`
- [ ] Apply fix batch in priority order (CRITICAL → MAJOR → MINOR → NIT)
- [ ] Re-validate AFTER every batch (never chain speculative fixes)
- [ ] Evaluate every remaining WARNING against the publish-blocker rules
- [ ] Fix publish-blocker WARNINGs; leave truly-advisory WARNINGs with per-entry justification
- [ ] Stop when findings empty AND no blocking warnings, OR escalate at iteration 5 / identical-finding-set
- [ ] **For migration runs only (`/cpv-upgrade-plugin`)**: run `run_all_checks` from `references/canonical-pipeline-migration-checklist.md` — every BLOCKER + MAJOR must pass.
- [ ] **For migration runs only**: run `uv run python scripts/publish.py --print-gates` then `--dry-run` then `--patch`, then `gh run watch <run-id> --exit-status` on the resulting tag (and on the marketplace tag if Layout C / Layout A).
- [ ] Write the iteration-by-iteration fix log to `$MAIN_ROOT/reports/plugin-fixer/<YYYYMMDD_HHMMSS±HHMM>-<slug>.md` (at the **main-repo root** — first entry of `git worktree list`, never a linked worktree; both `reports/` and `reports_dev/` gitignored). NEVER write to `docs_dev/`, the worktree-local `reports/`, or any other path.
- [ ] Return one-line summary to caller

## Why a loop

Fixes often cascade. Adding `"type": "number"` to a `userConfig` entry can expose a MINOR that was masked by the missing-field check. Running `standardize_plugin.py --fix` creates new files that must themselves be validated. A single validate-then-fix pass is insufficient — the fixer must re-validate after every batch of changes and continue fixing until the report is fully clean.

This reference defines the loop both fixer agents (plugin-fixer, marketplace-fixer) run. The same algorithm applies to plugins and marketplaces; only the validator command differs.

## Algorithm

```
iterations = 0
while iterations < MAX_ITERATIONS (default 5):
    iterations += 1
    report = validate(<target>)    # validate_plugin.py --strict OR validate_marketplace.py --strict
    findings = report.filter(severity in {CRITICAL, MAJOR, MINOR, NIT})
    if findings is empty:
        remaining_warnings = report.filter(severity == WARNING)
        blocking_warnings = evaluate_warnings(remaining_warnings)
        if blocking_warnings is empty:
            # Step 7c — migration runs only — Pre-completion verification
            if dispatched_for_migration:
                run_all_rc = run_all_checks(<target>)
                if run_all_rc != 0:
                    return PARTIAL (BLOCKER/MAJOR check failed — see run-all log)
                # Step 7d — migration runs only — Real publish + CI watch
                if publish_dry_run() != 0: return PARTIAL
                tag = publish_patch()                     # bumps + commits + pushes
                if gh_run_watch(tag) != 0: return PARTIAL
                if has_marketplace:
                    if marketplace_publish_and_watch() != 0: return PARTIAL
            return SUCCESS (clean)
        else:
            fix_batch(blocking_warnings)    # blocking warnings must be fixed too
            continue
    fix_batch(findings)                     # CRITICAL → MAJOR → MINOR → NIT, in priority order
if iterations == MAX_ITERATIONS:
    return ESCALATE_TO_USER (loop not converging — human must intervene)
```

Key properties:
- Re-validate after EVERY batch, not once at the end. Validator output changes as fixes land — a finding that seemed low-priority may upgrade once a blocking issue clears.
- Fix in priority order within a single batch, but always re-validate before the next batch — don't chain speculative fixes based on stale reports.
- Stop when the report is clean of findings AND free of publish-blocking warnings.

## Entry points — plugin path vs report path

The fixer accepts EITHER a plugin/marketplace path OR a pre-existing validation report path.

1. **Path ends in `.md` or `.json` and the file exists AND contains CPV-style severity markers (`[MAJOR]`, `[CRITICAL]`, `SUMMARY: CRITICAL=…`)** → treat as a report. Read the findings and enter the loop at fix_batch with the existing report; on re-validate, generate a NEW report.
2. **Path is a directory** → treat as the target. Run validation first, then enter the loop.
3. **Path is missing / ambiguous** → apply the Path Resolution Protocol (see plugin-creator agent). For the fixer, this typically means asking the user which plugin/marketplace they meant among the candidates.

The old contract ("fixer never validates") is superseded by this one: the fixer owns the full loop. Validation is not a separate agent step when a fix is underway.

## Termination and safety

- **Max iterations: 5 by default.** Most plugins converge in 1-2 iterations; 3 is rare; 5 signals either a bug in the fix guide or a cascading-rule problem that needs human review.
- **Per-iteration timeout: 300 seconds.** If a single validate-fix pass runs longer than 5 minutes, abort and ask the user.
- **Identical-finding-set guard:** if iteration N produces the exact same finding set as iteration N-1, there is a fix that is not landing (wrong file, wrong offset, dry-run flag, etc.). Stop and surface the finding to the user — do not keep looping.
- **Never disable/suppress rules to converge.** The goal is a genuinely clean report. Lowering severity, adding ignores, or patching the validator to skip a rule is never a valid fix.
- **Each fix batch commits** (or at minimum stages) changes, so `git status` + `git diff` stays inspectable between iterations. If the fixer crashes mid-loop, the in-progress fixes are not lost.

## WARNING evaluation rules

After the CRITICAL/MAJOR/MINOR/NIT set is empty, evaluate remaining WARNINGs. A WARNING is a publish-blocker if ANY of these hold:

1. The message mentions missing CI infrastructure (`.github/workflows/ci.yml`, `validate.yml`, `update-submodules.yml`, `notify-marketplace.yml`).
2. The message mentions missing publish pipeline files (`scripts/publish.py`, `cliff.toml`, `CHANGELOG.md`, `.git-hooks/pre-push`).
3. The message mentions broken or missing marketplace-integration plumbing — PAT secret not set on a plugin that wants auto-notify, mismatched marketplace owner/repo in `notify-marketplace.yml`, missing dispatch receiver on the marketplace side.
4. The message mentions the plugin's declared `platform:` but warns that platform is not supported (e.g., plugin declares `linux` but all scripts are `.bat` Windows-only).
5. The message references a version mismatch across `plugin.json` ↔ `pyproject.toml` ↔ `__version__` ↔ marketplace entry.
6. The message warns that a dependency in `dependencies[]` targets a non-existent or yanked version — this fails on install.

If a WARNING is a publish-blocker, it goes BACK into the fix_batch. Apply the normal error-to-fix routing.

## Publish-blocking warning categories

Non-exhaustive list. When the WARNING text matches any of these patterns, treat it as a must-fix:

| Pattern in WARNING text | Why it blocks publish |
|---|---|
| `CI workflow not found` / `missing validate.yml` | Without CI, the pre-push hook is the only gate — server-side enforcement is gone. |
| `No pre-push hook installed` | Local edits will push through without validation. |
| `notify-marketplace.yml not found` / `not on default branch` | Publishing won't trigger marketplace sync. |
| `MARKETPLACE_PAT not configured` / `missing repository secret` | Auto-notify dispatch will 401. |
| `update-submodules.yml not found` on marketplace side | Marketplace won't receive dispatches. |
| `Version mismatch: plugin.json=X pyproject.toml=Y` | Release tag + changelog will be wrong. |
| `publish.py not executable` / `chmod +x required` | `publish.py --install-hook` will fail silently. |
| `dependencies[].version not satisfiable` | Install will fail on dependency resolution. |
| `plugin platform declares X but Y-only scripts found` | Claude Code will reject or misbehave on that platform. |
| `marketplace entry version does not match plugin.json version` | Cache mismatch between marketplace and repo. |

## Truly advisory warnings

These warnings are SAFE to leave. The fixer should list them in the final report but not block on them:

- `[WARNING] --skip-platform-checks windows applied` — informational, user opted out.
- `[WARNING] Found N Bash/Shell script(s) — not natively available on Windows` — if the plugin does NOT declare cross-platform support, this is advisory.
- `[WARNING] Language detection: X files detected as <language>` — informational.
- `[WARNING] Lockfile <name> present — consider pruning` — optional cleanup.
- `[WARNING] Optional metadata missing (homepage, keywords, license email)` — purely cosmetic.
- `[WARNING] Submodule advisory: <name> contained within plugin root` — informational containment check.
- `[WARNING] Orphan lockfile detected — no matching <tool> config` — informational only when build is known to be manual.

When in doubt, treat a WARNING as a blocker rather than advisory. The cost of a false positive (agent asks user) is much lower than the cost of a false negative (agent ships a broken plugin).

## Output contract

The final report from the fixer must include:

1. **Loop summary**: `iterations=<N>`, time elapsed, terminal state (`clean` / `blocked` / `escalated` / `partial`).
2. **Findings healed**: list of CRITICAL/MAJOR/MINOR/NIT findings that were fixed, with the commits or Edit operations that fixed them.
3. **Warnings fixed**: list of publish-blocking warnings that were addressed.
4. **Advisory warnings remaining**: the list of truly-advisory warnings, with a one-line explanation per entry of why they are safe to leave. This lets the user audit the judgment.
5. **Next steps**: if clean → "ready to publish, run `scripts/publish.py`"; if blocked → "these findings need human decisions: …"; if escalated → "loop stopped at iteration 5 with unchanged findings — need human review of …".
6. **For canonical-pipeline migration runs only** (`/cpv-upgrade-plugin`): the Unicode-bordered table from `run_all_checks` (the 82-check matrix from `references/canonical-pipeline-migration-checklist.md`) AND the `gh run` URL of the green CI run on the resulting tag (and on the marketplace tag if Layout C / Layout A registered). Without both, the migration is `[PARTIAL]`, NOT `[DONE]`. See `agents/plugin-fixer.md` § "Pre-completion verification (REQUIRED)" for the exact bash commands. Closes [issue #21 ask #1](https://github.com/Emasoft/claude-plugins-validation/issues/21).

## Migration runs — extra steps after step 7

When the agent was dispatched for a canonical-pipeline migration, the
basic loop above is **necessary but not sufficient**. After the regular
loop returns clean (step 7's mandatory final re-validation passes), the
agent MUST also run:

- **Step 7c — Pre-completion verification matrix.** Source `run_all_checks`
  from `references/canonical-pipeline-migration-checklist.md` and execute
  it on the plugin root. Every BLOCKER and MAJOR check must pass. Output
  is a Unicode-bordered Markdown table at
  `$MAIN_ROOT/reports/canonical-pipeline-migration/<ts±tz>-run-all.md`. A
  failed BLOCKER/MAJOR is equivalent to a CRITICAL/MAJOR in
  `validate_plugin.py` — return `[PARTIAL]` (not `[DONE]`).
- **Step 7d — Real publish + `gh run watch`.** Run
  `uv run python scripts/publish.py --patch` then
  `gh run watch <run-id> --exit-status` on the workflow run triggered by
  the resulting tag push. Repeat for the marketplace tag if Layout A
  (separate marketplace repo) or Layout C (single repo with both
  manifests bumped atomically). If either run reports failure, return
  `[PARTIAL]` with the failing job's `gh run view` URL.

The migration agent never silently `--force-templates` when checks fail.
Instead, surface the per-CHECK failure list to the user and ask them to
choose: (a) fix manually, (b) re-run with `--force-templates` (with
explicit warning that hand-tuned customisations to canonical files will be
overwritten), or (c) abort. See `agents/plugin-fixer.md`'s "Pre-completion
verification (REQUIRED)" section for the full decision matrix.
