---
trdd-id: 1VU6Y5MS
title: A per-linter spawn timeout equal to its caller's timeout makes the linter's own graceful degradation unreachable
column: todo
created: 2026-09-04T10:56:49+0200
updated: 2026-09-04T11:28:59+0200
current-owner: cpv-main-session
task-type: bugfix
min-approval-requirement: none
relevant-rules: []
---

# Lint tool resolution in run_lint_engine may be an unbounded cold-run stall

## ⏵ STATE — READ THIS FIRST ON RESUME (authoritative; supersedes the body) — 2026-09-04

**THE CARD'S TITLE AND ORIGINAL FRAMING ARE WRONG — corrected 2026-09-04 after
review, before any work started. Read this before the body.**

The title says "tool resolution … unbounded". **Resolution is not where the time
goes, and it is not unbounded.** Read first-hand:

- `_resolve` (`cpv_lint_engine.py:272`) → `resolve_tool_command`
  (`cpv_validation_common.py:184-206`) → `resolve_tool()` + `detect_executors()` +
  `choose_best()`. That chain **returns an argv prefix** (e.g.
  `["npx", "markdownlint-cli2"]`). It probes with `shutil.which`. It fetches
  nothing and spawns nothing.
- The npx/bunx package fetch happens later, when that argv is **spawned** —
  inside `lint_markdown` via `_run_linter(..., timeout=120)`
  (`cpv_lint_engine.py:1402`).

So "should RESOLUTION get its own timeout" is unanswerable as posed: it is a
`which` probe. **The real question this card owns is the nested-deadline defect:**

> `lint_markdown`'s spawn timeout is **120 s**, and the test that spawns
> `validate_plugin.py` also allows **120 s**. Equal budgets, outer clock starting
> first ⇒ the inner deadline is unreachable ⇒ markdownlint's own graceful path
> (`report.warning("markdownlint timed out — skipping markdown lint")`,
> `:1403-1405`) is **dead code** from any such caller. A per-linter budget must be
> strictly smaller than any caller's outer timeout or its handler can never run.

**Hypothesis B is DEAD, measured.** The competing explanation — that `9e7d2c1e`
slowed the gitignore-filtered walk via its `cpv_validation_common.py` changes —
was tested directly: `detect_languages()` on the same fixture, n=50 per arm,
median **13.364 ms** (HEAD) vs **13.624 ms** (`9e7d2c1e^`). The walk is ~13 ms at
both commits, three orders of magnitude below the phase cost. Do not re-open it.

**Step 1 of the verification below is ALREADY DONE, and it did NOT kill
hypothesis A** (2026-09-04, this machine):

- `command -v markdownlint-cli2` → **not on PATH**. So `_resolve`'s
  `shutil.which` fallback cannot be what satisfied it, and the tool nonetheless
  ran (it emitted a real MD047 finding) — meaning it came through the
  uvx/bunx/npx/docker path.
- an npx cache (`~/.npm/_npx/`) exists and `markdownlint` appears in it. (An
  earlier draft said "3 of 42 entries name markdownlint" — that count is doing
  evidentiary work it cannot support: a `package.json` *naming* markdownlint
  includes packages that merely depend on it, and no mtime was checked, so the
  three may be unrelated and months old.)

That is consistent with A but still not proof of WHERE the seconds go: a cached
`_npx` entry should make resolution cheap, yet cold runs still measured 30–74 s.
So either the cache is not being hit, or the cost is downstream of resolution.
**Do not close this card on the two facts above** — they narrow the search, they
do not end it. The remaining steps stand:

**NEXT ACTION (verification, MUST run before any fix):**

1. ~~`command -v markdownlint-cli2`~~ — **DONE, see above: not on PATH.** If it had resolved to a locally-installed binary,
   the "cold npx fetch" hypothesis below is DEAD; the 30-49 s is spent elsewhere and
   must be re-diagnosed from scratch (profile `run_lint_engine` directly, e.g.
   `python -X importtime` or manual `time.monotonic()` bracketing around
   `_resolve`/`resolve_tool_command` vs the actual linter subprocess call).
2. If markdownlint-cli2 is NOT local: snapshot `~/.npm/_cacache` and/or
   `~/.npm/_npx` mtimes, run `scripts/validate_plugin.py` cold against the same
   5-file toy fixture, snapshot again, and confirm a package fetch actually
   happened in that window (new/updated cache entries, or an observed `npx`
   child process via a `ps` snapshot per
   `~/.claude/rules/never-tail-on-error-messages.md`-adjacent snapshot discipline
   — never `pgrep`/`ps | grep` directly).
3. Only once (1)-(2) establish where the time actually goes, decide whether tool
   RESOLUTION (`_resolve`/`resolve_tool_command` in
   `scripts/cpv_lint_engine.py`) needs its own bounded timeout, separate from the
   existing per-linter spawn timeouts (60/120/180 s,
   `PLUGIN_REPO_LINT_TIMEOUT`/`_effective_timeout`) and the aggregate phase
   budget (`_DEFAULT_PHASE_TIMEOUT = 600.0 s`,
   `PLUGIN_REPO_LINT_PHASE_TIMEOUT`).

## The problem

`scripts/cpv_lint_engine.py::lint_repo` is invoked by `scripts/validate_plugin.py`
as the phase `run_lint_engine`. On a cold run against a 5-file toy plugin fixture
(1 JSON file + 1 markdown file to lint), `run_lint_engine` took **30.2 s** while
every other validation phase took **0.0-0.2 s**. Repeated cold runs measured
**30.2 / 35.5 / 49.2 / 40.3 s** for the same phase, same fixture.

If a plugin author hits this on a cold machine, `validate_plugin.py` stalls for
30-50 s inside one phase with no progress feedback — and it is not yet established
whether the existing timeouts (linter-spawn, phase-aggregate) actually bound
whatever is causing the stall, or only bound a downstream step that isn't where
the time is going.

## Measured evidence (this session, verified)

| # | Fact | Status |
|---|---|---|
| 1 | `run_lint_engine` phase took 30.2/35.5/49.2/40.3 s across 4 cold runs on a 5-file fixture; every other phase took 0.0-0.2 s | VERIFIED (measured this session) |
| 2 | Aggregate phase budget is `_DEFAULT_PHASE_TIMEOUT = 600.0` s, env-overridable via `PLUGIN_REPO_LINT_PHASE_TIMEOUT` | VERIFIED (read from source) |
| 3 | Per-linter spawn timeouts are 60/120/180 s, resolved by `_effective_timeout`, env-overridable via `PLUGIN_REPO_LINT_TIMEOUT` | VERIFIED (read from source) |
| 4 | `_resolve(tool_name)` (~line 272) calls `resolve_tool_command(tool_name)` from smart_exec, falling back to `shutil.which`; its missing-tool warning mentions "install it locally or rely on uvx / bunx / npx / docker fallback" | VERIFIED (read from source) |
| 5 | Detected languages for the fixture were `json, markdown`; markdownlint-cli2 produced a real MD047 finding, so it resolved and ran successfully | VERIFIED (measured this session) |
| 6 | The 30-49 s is spent inside a cold `npx`/`bunx` package fetch during tool resolution | **UNVERIFIED — hypothesis only.** Nobody read `resolve_tool_command`, observed an `npx` child process, or checked a package cache before/after a run. |

## What is NOT established

- Whether tool resolution (as opposed to the linter subprocess itself, or
  something else entirely — dependency scanning, environment probing) is where the
  30-49 s actually goes.
- Whether `resolve_tool_command`/`_resolve` has ANY timeout of its own, or
  whether it is unbounded and the 600 s phase budget is the only thing that would
  ever stop it.
- Whether this reproduces on a warm machine (repeat run immediately after a cold
  one) — if warm runs are fast, that alone strongly supports the cold-fetch
  hypothesis without further instrumentation.

Do not assume the mechanism. This card exists specifically because the mechanism
was NOT verified when discovered — see `TRDD-MHCFOCBV`, the CI-red card from the
same v5.17.0 release investigation, which is where this timing anomaly was first
noticed as a side observation.

## Acceptance criteria

> **NOTE — the criteria below were written under the card's original (wrong)
> framing and are kept for provenance. The three that actually govern are these:**
>
> - [ ] **Make the per-linter spawn budget strictly smaller than any plausible
>       caller's outer timeout**, so `lint_markdown`'s own `TimeoutExpired`
>       handler can run. Today both are 120 s and the handler is unreachable.
> - [ ] **Find where the 30–74 s actually goes inside the spawn.** Resolution is
>       ruled out (it is a `which` probe). Three candidates remain and NONE has
>       been observed — do not assume the first:
>       (a) a cold npx/bunx package **fetch** (network);
>       (b) cold **ESM module resolution** by Node against a cold page cache —
>       entirely local, and `lint_markdown` already runs in a scratch cwd to
>       control module resolution (`cpv_lint_engine.py:1392-1396`);
>       (c) npx **cache validation** of already-present entries.
>       Discriminators, cheapest first: run the slow path with the network
>       disabled (kills (a) if still slow); diff `~/.npm/_npx` mtimes across a
>       slow run; snapshot the process table (never `pgrep`/`ps | grep`) for an
>       `npx` child. Instrument `_run_linter` to split spawn-setup from lint.
> - [ ] **Decide whether the one-time fetch should be surfaced or pre-warmed**
>       rather than silently charged to whichever caller happens to go first.
>       On CI that caller is arbitrary — it is whichever test the shard split
>       happens to schedule first.

- [x] Verification step 1 (`command -v markdownlint-cli2`) run and result recorded
      as a fact, not inferred. **DONE — not on PATH; see the STATE block.**
- [ ] If local resolution: re-profile `run_lint_engine` directly to find where the
      30-49 s actually goes; update this card's "What is NOT established" section
      with the real answer before any fix is designed.
- [ ] If cold-fetch confirmed: decide whether tool resolution needs its own bounded
      timeout distinct from the linter-spawn and phase-aggregate timeouts, and
      whether a warm/cold difference should be surfaced to the user (progress
      message, or a documented one-time-cost note) rather than silently eaten by
      the phase budget.
- [ ] A warm-vs-cold repeat-run comparison is recorded (same fixture, same
      machine, back-to-back) to test whether this is purely a first-run cost.
- [ ] Whatever fix (if any) is decided lands with a test that reproduces the
      slow path deterministically (e.g. by clearing the relevant cache dir) and
      demonstrates the chosen bound actually triggers.
