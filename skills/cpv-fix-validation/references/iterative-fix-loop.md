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
- [ ] Run validation with `--strict --json`, build the compact ledger (`cpv_fix_ledger.py build`), read the LEDGER not the full report
- [ ] Auto-apply the MECH set first (`cpv_codemod.py apply --json … --apply`, zero LLM), then fix the INTEL residual fix-as-you-go (one file at a time, read once, fix in the same turn)
- [ ] Re-validate AFTER every batch (never chain speculative fixes); read the DELTA ledger, never a fresh full report
- [ ] Evaluate every remaining WARNING against the publish-blocker rules
- [ ] Fix publish-blocker WARNINGs; leave truly-advisory WARNINGs with per-entry justification
- [ ] Stop when findings empty AND no blocking warnings (CONVERGED), OR escalate when the finding set RECURS vs **any** prior iteration (oscillation — tracked deterministically by `scripts/cpv_fix_loop_state.py`, not just vs N-1). NO fixed iteration cap.
- [ ] **For migration runs only (`/cpv-upgrade-plugin`)**: run `run_all_checks` from `references/canonical-pipeline-migration-checklist.md` — every BLOCKER + MAJOR must pass.
- [ ] **For migration runs only**: run `uv run python scripts/publish.py --print-gates` then `--dry-run` then `--patch`, then `gh run watch <run-id> --exit-status` on the resulting tag (and on the marketplace tag if Layout C / Layout A).
- [ ] Write the iteration-by-iteration fix log to `$MAIN_ROOT/reports/cpv-plugin-fixer-agent/<YYYYMMDD_HHMMSS±HHMM>-<slug>.md` (at the **main-repo root** — first entry of `git worktree list`, never a linked worktree; both `reports/` and `reports_dev/` gitignored). NEVER write to `docs_dev/`, the worktree-local `reports/`, or any other path.
- [ ] Return one-line summary to caller

## Why a loop

Fixes often cascade. Adding `"type": "number"` to a `userConfig` entry can expose a MINOR that was masked by the missing-field check. Running `standardize_plugin.py --fix` creates new files that must themselves be validated. A single validate-then-fix pass is insufficient — the fixer must re-validate after every batch of changes and continue fixing until the report is fully clean.

This reference defines the loop both fixer agents (cpv-plugin-fixer-agent, cpv-marketplace-fixer-agent) run. The same algorithm applies to plugins and marketplaces; only the validator command differs.

## Algorithm

**The loop's CONTROL FLOW is a BEHAVIOUR owned by the agent prompts** —
`agents/cpv-plugin-fixer-agent.md` and `agents/cpv-marketplace-fixer-agent.md` run validate→fix→
re-validate from their OWN prompt and do not load this skill to learn HOW to loop.
This file is the SUPPORTING DATA that loop consults: the WARNING-evaluation rules,
the migration-only step detail (7c/7d), and the output contract, all below. The
shape the agent's behaviour follows:

1. **Validate → ledger** (`--strict --json > <findings.json>`, then `cpv_fix_ledger.py build`
   → compact by-file `<ledger.txt>`; `cpv_fix_loop_state.py record` for the deterministic verdict).
   Read the LEDGER, never the full `.md` report (see "Compact ledger + fix-as-you-go" below).
2. **`PROGRESS`** → **MECH first** (`cpv_codemod.py apply --json … --apply` clears the
   `fixable:true` set at zero LLM cost), then **INTEL fix-as-you-go**: one file at a time, read
   only the ledger's line ranges, fix ALL of that file's findings in the same turn, never re-read;
   the recipe is inline (`suggestion`), open `plugin-error-index.md` once per rule-TYPE; re-validate
   (the next ledger is the DELTA).
3. **`CONVERGED`** (blocking set empty) → evaluate WARNINGs (rules below); fix
   publish-blockers; then the mandatory final verify (+ migration 7c/7d when dispatched
   for an upgrade — and publish-until-CI-green).
4. **`CYCLE`** → the standard fix is FUTILE (it oscillates against another finding);
   do NOT repeat it — switch to the DEEPER plugin-side remediation that breaks the root
   tension (canonical case: the TOC catch-22 → `skill-fixes.md` §8 Fix B, MERGE the
   reference file's headings into fewer broad chapters so the TOC fits AND stays under
   the body cap), and keep looping. Return `[BLOCKED]` ONLY if the SAME cycle recurs
   after that deeper remediation.

It is FIX-AND-LOOP-UNTIL-VALIDATION-PASSES: CPV's rules are never relaxed to converge —
the scanned plugin changes to comply. The oscillation guard
(`scripts/cpv_fix_loop_state.py`) is the deterministic terminator — see "Termination
and safety".

Key properties:
- Re-validate after EVERY batch, not once at the end. Validator output changes as fixes land — a finding that seemed low-priority may upgrade once a blocking issue clears.
- Fix in priority order within a single batch, but always re-validate before the next batch — don't chain speculative fixes based on stale reports.
- Stop when the report is clean of findings AND free of publish-blocking warnings.

## Compact ledger + fix-as-you-go

The inner validate→fix loop used to ingest the FULL `.md` report every iteration and read the
per-error recipe once per finding — a top token sink (cost ≈ turns × per-turn-context: every raw
report rides forward and is re-charged on each later turn). The ledger + fix-as-you-go discipline
removes that (TRDD-GVMOKJBB); it changes HOW the loop reads and applies findings and relaxes NO gate.

- **The ledger IS the finding surface.** `cpv_fix_ledger.py build --json <findings.json> --out
  <ledger.json> --text <ledger.txt>` reshapes the validator JSON into a COMPACT by-file view: each
  finding is `L<line> <LEVEL> [<category>] <suggestion>`, grouped by file, split into a `mech`
  bucket (`fixable:true`, auto-fixed by codemod) and an `intel` bucket (needs the model), with each
  WARNING pre-tagged `BLOCKING`/`advisory` (same rule as this file's categories — the agent never
  re-reads the 40-line table). Read `<ledger.txt>`; NEVER re-ingest the full report.
- **MECH before INTEL.** `cpv_codemod.py apply --json <findings.json> --apply` deterministically
  clears the `mech` set (idempotent, per-file backup, skips vendored) at ZERO model cost. Run it
  first so the model only ever works the INTEL residual.
- **Read once, fix in the same turn.** For each file in the `intel` bucket, read ONLY the ledger's
  line ranges (`tldr slice`/`Read` offset+limit — never the whole file), apply ALL of that file's
  fixes in one turn (`fastedit` for a symbol body, else `Edit`), then never re-read it. File-centric,
  not finding-centric — a file with 5 findings is read once, not five times.
- **Delta re-validate.** After a pass, re-validate → a NEW (smaller) ledger; the loop-state guard
  records the signature as before. Read the delta ledger, never a fresh full report.
- **Optional pinpoint (large files, imprecise lines).** When a finding's `line` is `null`/coarse and
  the file is large, you MAY use FREE-mode `llm-externalizer` to locate the exact span — but only
  when its tools are present; otherwise use the ledger line directly. Never a hard dependency (the
  ledger already carries file+line for nearly every finding).

## Entry points — plugin path vs report path

The fixer accepts EITHER a plugin/marketplace path OR a pre-existing validation report path.

1. **Path ends in `.md` or `.json` and the file exists AND contains CPV-style severity markers (`[MAJOR]`, `[CRITICAL]`, `SUMMARY: CRITICAL=…`)** → treat as a report. Read the findings and enter the loop at fix_batch with the existing report; on re-validate, generate a NEW report.
2. **Path is a directory** → treat as the target. Run validation first, then enter the loop.
3. **Path is missing / ambiguous** → apply the Path Resolution Protocol (see cpv-plugin-creator-agent agent). For the fixer, this typically means asking the user which plugin/marketplace they meant among the candidates.

The old contract ("fixer never validates") is superseded by this one: the fixer owns the full loop. Validation is not a separate agent step when a fix is underway.

## Termination and safety

- **NO hardcoded iteration cap.** Most small plugins converge in 1-2 iterations, but plugins with hundreds of findings legitimately need 20+ iterations. Let the loop run until convergence (empty finding set) or oscillation (next bullet). The agent decides when to stop — not a magic number.
- **NO hardcoded per-iteration timeout.** Some fixes (e.g. running `gh run watch` on a tag) legitimately take many minutes. Use judgement: if a single iteration runs absurdly long with no progress, surface that to the user with the partial state — but do not let an arbitrary `300s` ceiling kill a legitimate long-running step.
- **Full-history oscillation guard (THE termination check) — `scripts/cpv_fix_loop_state.py`.** A `record` call each iteration hashes the finding multiset and compares it against **every** prior iteration (not just N-1). A repeat = `CYCLE`. The old single-step "same as N-1" guard MISSED multi-step cycles: the TOC-embed catch-22 oscillates over two iterations (embed-verbatim → over-cap MAJOR → shrink → TOC MINOR returns → A,B,A,B…), so consecutive iterations always differ and the single-step guard NEVER fired → the loop ran forever and the agent exhausted its context (the field report this loop hardening fixes). The state file lives on disk, so detection survives the very context-exhaustion that was the failure mode — the agent does not have to remember 20+ prior signatures itself. Reset it once at loop start.
- **`CYCLE` ≠ give up — it means "switch strategy".** The fixer's job is to FIX UNTIL VALIDATION PASSES, all on the scanned-plugin side. A `CYCLE` says the fix you keep applying is futile because it pulls against another finding; STOP repeating it and apply the DEEPER plugin-side remediation that resolves the root tension (the canonical case: TOC catch-22 → `skill-fixes.md` §8 Fix B, MERGE the reference file's headings into fewer broad chapters so the TOC fits *and* stays under the body cap). Return `[BLOCKED]` ONLY when the SAME cycle recurs *after* the deeper remediation was applied — i.e. no plugin-side fix can break it and a human/CPV decision is genuinely needed. The finite finding space guarantees the loop still terminates (pigeonhole) — no magic number required.
- **Never disable/suppress rules to converge.** The goal is a genuinely clean report. Lowering severity, adding ignores, or patching the validator to skip a rule is never a valid fix — the plugin changes to comply, never CPV.
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
5. **Next steps**: if clean → "ready to publish, run `scripts/publish.py`"; if blocked → "these findings need human decisions: …"; if escalated → "loop stopped after iteration N because the finding set was identical to the previous iteration (oscillation) — need human review of …".
6. **For cpv-canonical-pipeline migration runs only** (`/cpv-upgrade-plugin`): the Unicode-bordered table from `run_all_checks` (the 87-check matrix from `references/canonical-pipeline-migration-checklist.md`) AND the `gh run` URL of the green CI run on the resulting tag (and on the marketplace tag if Layout C / Layout A registered). Without both, the migration is `[PARTIAL]`, NOT `[DONE]`. See `agents/cpv-plugin-fixer-agent.md` § "Pre-completion verification (REQUIRED)" for the exact bash commands. Closes [issue #21 ask #1](https://github.com/Emasoft/claude-plugins-validation/issues/21).

## Migration runs — extra steps after step 7

When the agent was dispatched for a cpv-canonical-pipeline migration, the
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
- **Step 7d — Real publish, then LOOP UNTIL CI IS GREEN.** Run
  `uv run python scripts/publish.py --patch` then
  `gh run watch <run-id> --exit-status` on the workflow run triggered by
  the resulting tag push (repeat for the marketplace tag if Layout A /
  Layout C). **A red CI run is NOT a stopping point — it is the next fix
  iteration.** See "Publish / upgrade — loop until CI green" below: read the
  failing job, fix the *cause* on the plugin side, re-publish, re-watch,
  until every required run is green. `[PARTIAL]` is returned ONLY when the
  set of failing CI jobs *oscillates* (a fix is not landing — same
  `cpv_fix_loop_state.py` guard), never on the first red.

The migration agent never silently `--force-templates` when checks fail.
Instead, surface the per-CHECK failure list to the user and ask them to
choose: (a) fix manually, (b) re-run with `--force-templates` (with
explicit warning that hand-tuned customisations to canonical files will be
overwritten), or (c) abort. See `agents/cpv-plugin-fixer-agent.md`'s "Pre-completion
verification (REQUIRED)" section for the full decision matrix.

## Publish / upgrade — loop until CI green

The same "loop until it passes" discipline that governs the validate→fix loop
governs **publishing** and **upgrading**: a release is not done when the tag is
pushed — it is done when **every required GitHub CI/CD run on that tag is
green**. Whoever runs `publish.py` (the cpv-plugin-fixer-agent migration path, the
`/cpv-upgrade-plugin` upgrade flow, any cpv-canonical-pipeline publish) owns this
loop and does not return `[DONE]` on a red or pending run.

This is a BEHAVIOUR owned by the agent that runs `publish.py` —
`agents/cpv-plugin-fixer-agent.md` §7d (migration / `/cpv-upgrade-plugin` path) and
`agents/cpv-marketplace-fixer-agent.md` own the loop; this section is the supporting policy
the agent applies. The shape: publish (`publish.py --patch`) → `gh run watch
<run-id> --exit-status` → on a red run, read the failing job (`gh run view`), fix
the CAUSE on the plugin side, re-publish, re-watch — until every required run is
green. The set of FAILING CI jobs is tracked with `scripts/cpv_fix_loop_state.py`
exactly like a finding set (a second `--state` file), so a non-landing CI fix
*oscillates* → `[PARTIAL]` with the `gh run view` URL, never an infinite spin.

Rules that keep this honest:
- **Fix the cause, never the symptom.** A red CI job is fixed by correcting what
  it caught (a failing test, a lint error, a type error, a missing workflow
  permission), on the plugin side — never by deleting the job, marking it
  `continue-on-error`, or `--force-templates`-ing over a hand-tuned workflow.
- **Each iteration re-publishes a real bump.** `publish.py --patch` is idempotent
  for an interrupted publish (pipeline-migration §4) but a genuine *new* attempt
  bumps the patch — that is correct: every attempt is a real, auditable release
  attempt, and the loop stops as soon as one is green.
- **Oscillation = a fix that is not landing.** If the identical set of CI jobs
  fails after you "fixed" it, the fix did not address the cause (or the failure
  is environmental/flaky — surface that). `cpv_fix_loop_state.py` reports `CYCLE`
  and the loop returns `[PARTIAL]` with the evidence — it never spins forever.
- **GitHub transient failures retry, they don't count as a fix-cycle.** A network
  timeout / runner-provisioning error is re-run (`gh run rerun <id> --failed`),
  not "fixed" — only a genuine job failure enters the fix branch.

### Token discipline — the part that keeps a CI-green loop from burning millions (TRDD-DZS5K34A)

A migration / CI-green loop that ingests RAW `publish.py` + CI output every cycle
burned **16-25M tokens** per run in the field. Three mandatory disciplines stop that
(all gate-neutral — they change HOW the loop reads output, never WHAT it enforces):

- **(A) Lean output capture — NEVER pipe raw `publish.py` / `pytest` / `gh run
  watch|view` into context.** Those emit the WHOLE suite + every gate + full CI logs,
  and (cost ≈ turns × per-turn-context) each raw dump rides forward and is re-charged
  on every later turn. **Redirect to a file, read back ONLY the failure summary:**
  ```bash
  uv run python scripts/publish.py --patch > /tmp/cpv-publish.log 2>&1; rc=$?
  # rc!=0 → read ONLY the failed gate + failing test names, NOT the whole suite:
  grep -nE "FAILED|^E |AssertionError|Gate [0-9].*(FAIL|BLOCK)|ERROR" /tmp/cpv-publish.log | head -40
  gh run view <run-id> --log-failed > /tmp/cpv-ci.log 2>&1   # failed-step logs ONLY
  grep -nE "FAIL|Error|Traceback" /tmp/cpv-ci.log | head -40
  ```
  Drill into one failure by `grep`-ing the log FILE — never by re-emitting it.
- **(C) Verify the fix LOCALLY before you re-publish.** A red CI job → reproduce it
  locally first: run the SPECIFIC failing test (`uv run python -m pytest <path>::<test>
  -x -q > /tmp/cpv-test.log 2>&1`, read only the summary) + a lean-captured `validate
  --strict`. Only when the local repro is GREEN do you spend another full
  `publish.py --patch` + `gh run watch` cycle. Re-publishing on every speculative edit
  multiplies the burn for nothing.
- **(B) Bound the expensive cycles with `--stall-window` on the CI state file.** The
  CI loop's second `cpv_fix_loop_state.py` state file opts into the non-progress guard:
  `record --state <ci-loopstate.json> --findings <ci-jobs.json> --stall-window 5`. A
  `CYCLE` (exact-repeat failing set) OR a `STALLED` (no new-best failing-job count for
  5 consecutive real release+CI cycles, exit 3) → return `[PARTIAL]` with the `gh run
  view` URL. STALLED catches the **churning** failing set the exact-repeat guard
  misses (each cycle a slightly different failing test → never an exact repeat → would
  otherwise loop forever). This is a PROGRESS gate on the *costly* publish cycles, NOT
  an iteration cap on the cheap inner validate→fix loop — that loop keeps NO
  `--stall-window` (a count can plateau there while the set productively churns).
