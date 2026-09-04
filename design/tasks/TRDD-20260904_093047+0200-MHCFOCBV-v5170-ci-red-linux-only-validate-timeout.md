---
trdd-id: MHCFOCBV
title: v5.17.0 shipped with red CI because the REPO LINT phase can outlive the 120s subprocess timeout of the test that spawns it
column: todo
created: 2026-09-04T09:30:47+0200
updated: 2026-09-04T11:11:24+0200
current-owner: cpv-main-session
task-type: bugfix
priority: high
severity: major
min-approval-requirement: none
relevant-rules: []
---

# v5.17.0 released with CI red — the REPO LINT phase outlived a 120 s test budget

> **The FILENAME still reads `…-linux-only-validate-timeout`.** TRDD filenames are
> not renamed on retitle, so that stale framing survives permanently in every
> `find`/`grep` hit. It is wrong: the cause was reproduced on macOS. Do not
> re-open a Linux investigation on the strength of the path.

## ⏵ STATE — READ THIS FIRST ON RESUME (authoritative; supersedes the body) — 2026-09-04

**THE COSTLY PHASE IS IDENTIFIED AND THE TEST IS FIXED (2026-09-04, on macOS —
no Docker needed). The MECHANISM INSIDE that phase is NOT established, and
`9e7d2c1e` is NEITHER convicted NOR exonerated.** Read the three subsections
below in that order; an earlier draft of this block over-claimed both and was
corrected by adversarial review before commit.

**NEXT ACTION:** confirm CI goes green on the fix commit — from the job
conclusion, never from a publish exit code. Then close. The Gate-14 finding at
the bottom has moved to its own card, TRDD-4VROKH40.

### SUPERSEDED by this block

Four parts of the body below are stale and must not be acted on:

1. The frontmatter title's "Linux-only" — **wrong**; reproduced on macOS.
2. "**NEXT ACTION:** reproduce on Linux (Docker …)" — **unnecessary**; the phase
   was isolated on macOS from the tool's own per-phase timings.
3. The "not reproduced outside CI" residual paragraph and the "do NOT propose a
   sixth mechanism" warning — **obsolete**, same reason.
4. "Every macOS configuration tried runs the same call in ~1 s" — **false**. It
   runs in ~1 s only once its per-fixture-path scanner-cache entry is warm; a
   cold path measured 30–49 s.
5. The **timestamp table** in "What consumed the time" (`07:08:14` → `07:10:14`
   → `07:10:15`) and its "what the timestamps do NOT prove" framing — not
   wrong, but **superseded** by the two-run green/red table below, which
   answers the question that table declined to answer. Do not redo its
   arithmetic.
6. "deterministic across two runs" in "The failure" — still true, but its
   original reading (*"deterministic, therefore not a variance story"*) is
   **superseded**: both CI runs are cold, so 2-for-2 is what a reproducible
   cold cost looks like, not proof of a code change.

The Notes section's "do not fix this by raising `timeout=120`" still stands and
was honoured.

### What IS established

`validate_plugin.py` prints per-phase timings. On a cold run of this test's exact
fixture, **one phase is the entire runtime and every other phase is 0.0–0.2 s**:
`run_lint_engine [w09]` — 30.2 s of a 30.5 s run. That is a direct reading of the
tool's own instrument, not an inference.

**The CI logs show the same asymmetry, and rule out a local-ordering artifact.**
On BOTH runs the cheap sibling executes FIRST and stays cheap, and this test is
expensive immediately after:

| run | commit | sibling `…skips_gitignored_input_dev` | this test |
|---|---|---|---|
| green, shard 2 | `c441703c` (v5.16.2) | 0.5 s | **11.4 s** |
| red, shard 2 | `f1882af9` (v5.17.0) | 0.5 s | **120.2 s — TimeoutExpired** |

That reading needs the shard to be single-process, and four independent signals
say it is: both logs' step header is `Run uv run pytest tests/ --splits 4 --group
2 -v` (no `-n`), `pytest.ini`'s `addopts` is only `-v --tb=short`,
`.github/workflows/ci.yml:110` states it in words, and neither log contains a
`gw<N>` prefix. Circumstantial rather than proof — a `PYTEST_ADDOPTS` in the job
env would not appear in any of them — but the 4-way split is pytest-split across
four JOBS, which is orthogonal to xdist workers inside one job, so xdist being
merely loaded-and-unused is the ordinary reading.

The differentiator between this test and its siblings is that its fixture is the
only one carrying a **non-gitignored `.md`** (`unexpected_root/stuff.md`,
required by its own assertion), which makes `markdown` a detected language and
gives REPO LINT real work to do. The cold run's output confirms both halves:
`Detected languages: json, markdown` and a markdownlint MD047 NIT against that
exact file.

The structural defect, and the sole justification the fix needs — **corrected
2026-09-04 after review; an earlier draft named the wrong budget.** It is NOT
the 600 s aggregate phase budget (`_DEFAULT_PHASE_TIMEOUT`) that binds: that one
never gets near. The binding number is `lint_markdown`'s own spawn timeout,
**`timeout=120` at `scripts/cpv_lint_engine.py:1402` — exactly EQUAL to the
test's `timeout=120`**, not smaller.

Two equal deadlines nested, with the outer clock starting first (fixture build,
four `git` spawns, interpreter startup, every earlier phase), means the inner one
is **unreachable**. So markdownlint's own graceful degradation —

```python
except subprocess.TimeoutExpired:
    report.warning("markdownlint timed out — skipping markdown lint")
    return True                      # cpv_lint_engine.py:1403-1405
```

— is dead code when called through a test with a 120 s budget. The phase cannot
degrade gracefully; the caller just dies first. That is a sharper defect than
"the inner budget is bigger than the outer", and it is worth fixing on its own
merits: **a per-linter budget should be strictly smaller than any caller's outer
timeout, or its own timeout handler can never run.** Tracked on TRDD-1VU6Y5MS.

### What is NOT established — do not repeat these as fact

- **The mechanism inside `run_lint_engine`.** A cold `npx`/`bunx` fetch of
  `markdownlint-cli2` is a plausible story, but it was never observed — it was
  inferred from the wording of a missing-tool WARNING string. `resolve_tool_command`
  was never read, no `npx` process was observed, no package cache was checked.
  One `command -v markdownlint-cli2` plus an `~/.npm/_npx` before/after would
  settle it.
- **Whether `9e7d2c1e` contributed.** The HEAD-vs-parent timings taken here
  (30.2/49.2 vs 35.5/40.3) are n=2 per arm, unpaired, ordered, on a busy machine,
  and the parent ran from a fresh detached worktree whose untracked/ignored state
  differs — so they neither convict nor exonerate. Concluding "indistinguishable"
  from that would be the exact n=3 error corrected on TRDD-21ES7XEX the same day.
  What the CI logs DO show is that the cost moved 11.4 s → 120.2 s across the
  window, so *something* changed or the underlying cost is wildly variable. The
  commit touches no lint-engine file (`cpv_lint_engine.py`, `smart_exec.py`, and
  the markdownlint configs are all untouched) but does add ~1400 tests to the
  shard, which changes contention. **The untouched-files list is NOT
  exculpatory:** `lint_repo`'s banner reads "gitignore-filtered", and that walk
  routes through `GitignoreFilter` / `is_path_gitignored` in
  `scripts/cpv_validation_common.py` — which the commit DOES change (+222
  lines). "Did not touch the lint engine" is fully compatible with "changed the
  lint engine's behaviour".
  **Discriminating measurement, COMPLETE — and it does NOT settle the CI
  question. Read the limitation before citing the numbers.** Paired cold runs, a
  FRESH fixture path each time (so every run is genuinely cold), alternating
  HEAD / `9e7d2c1e^`, n=4 per arm, reading only the `run_lint_engine` phase:

  | pair | HEAD | `9e7d2c1e^` | HEAD − base |
  |---|---|---|---|
  | 1 | 41.6 s | 44.5 s | −2.9 s |
  | 2 | 74.4 s | 67.3 s | +7.1 s |
  | 3 | 43.2 s | 39.9 s | +3.3 s |
  | 4 | 47.4 s | 47.0 s | +0.4 s |

  No HEAD-only signal: the arms interleave, and the within-arm spread
  (41.6 → 74.4) dwarfs every pairwise difference.

  **The limitation of that table:** all eight runs landed in the 40–74 s regime,
  in BOTH arms, while the CI green run was **11.4 s**. This machine never
  reproduced the cheap regime, so a whole-phase comparison here cannot resolve an
  effect measured against an 11.4 s baseline — an 8 s absolute regression would
  be 70 % of CI's baseline and statistically invisible against a 57 s local mean.

  **So the phase timing was abandoned for a direct measurement of the suspect
  function, and THAT is decisive.** The only way `9e7d2c1e` could have touched
  this phase is through `cpv_validation_common`'s gitignore helpers, which reach
  it via `detect_languages()` — an in-process, deterministic, network-free walk.
  Timed directly on the same fixture, n=50 per arm:

  | | median | mean | min |
  |---|---|---|---|
  | HEAD | 13.364 ms | 13.257 ms | 11.476 ms |
  | `9e7d2c1e^` | 13.624 ms | 13.666 ms | 12.332 ms |

  **Hypothesis B is dead.** The gitignore-filtered walk is ~13 ms at both
  commits — three orders of magnitude below the 30–74 s phase cost, and
  indistinguishable between them. `9e7d2c1e` did not slow the walk, so it cannot
  be the source of the 11.4 s → 120 s move through that path. The remaining cost
  is downstream of the walk (tool resolution or the linter spawn) and belongs to
  TRDD-1VU6Y5MS.

  **And the whole-shard comparison closes the rest.** Both archived shard-2 logs
  were parsed into per-test wall-clock gaps. They are the same run except for one
  test:

  | | green `c441703c` | red `f1882af9` |
  |---|---|---|
  | result lines on the shard | 3207 | 3245 |
  | position of the failing test | #3195 | #3191 |
  | **that test** | **11.4 s** | **120.2 s** |
  | 2nd slowest (`test_killed_run_identifies_the_stuck_phase`) | 8.0 s | 8.0 s |
  | 3rd (`…queue_semaphore_deadlock…`) | 7.1 s | 7.3 s |
  | 4th (`test_unarmed_child_really_hangs`) | 6.0 s | 6.0 s |
  | 5th/6th (`…timeout_per_file…`) | 5.2 / 5.2 s | 5.2 / 5.2 s |
  | 7th (`…wedged_file_is_killed…`) | 2.7 s | 2.7 s |

  Every other test on a 3200-test shard is within noise, at the same position.
  This kills the two remaining environmental explanations as well: it is **not**
  a slower runner (everything else would move) and **not** shard
  re-partitioning shifting who pays a first-run cost (composition and ordering
  are effectively unchanged, and the test sits at the same index).

  **Conclusion: nothing in the window regressed.** The single varying component
  is the markdownlint spawn inside that one test, whose cost is dominated by an
  external, network-dependent package fetch (`markdownlint-cli2` is not on PATH;
  it resolves through the npx/bunx fallback). A 10× swing in that between two
  runs two days apart is ordinary for a network fetch and extraordinary for
  compiled-in behaviour. `9e7d2c1e` is cleared — not by a null result, but by two
  positive measurements: the walk is 13 ms at both commits, and the other 3200
  tests are unchanged.

### The fix

`tests/test_issue_37_gitignore_walkers.py` sets `PLUGIN_SKIP_REPO_LINT=1`
alongside the `PLUGIN_SKIP_GITHUB_INTEGRITY=1` it already set — at **one** spawn
site: `test_rc_nonstd_dir_still_fires_for_non_gitignored_dirs`, the only test in
the file whose fixture has a non-gitignored `.md`. That env var is not a new
escape hatch: it is the documented issue-#148 opt-out, whose own docstring names
"a cold runner" as the case it exists for.

The other two spawn sites deliberately keep linting. A first pass applied the
flag to all three; that was reverted. Both of those tests carry **negative**
assertions (`"RC-NONSTD-DIR-001" not in output`; no `[CRITICAL]` line mentions
`INPUT_DEV/`), and silencing a phase shrinks the haystack a negative assertion
searches. Neither ever paid the cost (0.58–0.65 s), so the flag bought nothing
and risked a vacuous pass — in particular on
`test_no_critical_finding_references_gitignored_path`, which is the named
acceptance signature for issue #37.

Checked, so the remaining vacuity risk is bounded: `cpv_lint_engine.py` contains
**zero** `.critical(` calls and **zero** `RC-NONSTD-DIR-001` references, so the
lint phase cannot be the emitter either assertion is watching for. The file's
positive control is the fixed test itself — it asserts RC-NONSTD-DIR-001 MUST
fire, and it still passes with the phase skipped.

Measured after the fix: the failing test 16.1 s → **0.64 s**; the file
18.5 s → 3.6 s. **12 passed** (11 originally, +1 for the new
`TestLintWalkerHonoursGitignore`, which is one test making two observations on
one fixture rather than two tests — the negative half must be guarded by the
positive half in the same run, on the same root, or it can pass vacuously).

**Not swept, and the stated reason matters:** 44 other test files spawn
`validate_plugin.py` without this opt-out. The predicate that selects the ones
at risk is **static, not temporal** — does the fixture write a non-gitignored
file in a language whose linter is expensive to resolve? A local `--durations`
run cannot answer it: after the first run everything is warm and every such test
reports ~0.6 s, so a clean local table would be a false negative, not a clean
bill of health. Grep the fixtures instead.

## The failure

One test, one shard, **deterministic across two runs** (initial + `gh run rerun --failed`):

```
tests/test_issue_37_gitignore_walkers.py::TestNonstdDirRuleHonoursGitignore::test_rc_nonstd_dir_still_fires_for_non_gitignored_dirs
subprocess.TimeoutExpired  —  timeout=120
```

It shells out to `uv run python scripts/validate_plugin.py <fixture> --strict`
with `cwd=REPO` and `PLUGIN_SKIP_GITHUB_INTEGRITY=1`. Shard 2 result:
`1 failed, 3361 passed, 19 skipped`. Lint, Validate and shards 1/3/4 are green.

### What consumed the time — and what the timestamps do NOT prove

**The direct evidence is the `subprocess.TimeoutExpired` exception itself**: it
proves the `uv run … validate_plugin.py` subprocess ran the full 120 s. Cite
that, not the log arithmetic.

The shard-2 timestamps are context, not a duration measurement:

| # | Time | Event |
|---|---|---|
| 1 | `07:08:14.7178` | sibling `test_rc_nonstd_dir_is_not_flagged_when_gitignored` finishes (same 120 s budget, PASSES) |
| 2 | `07:10:14.8393` | this test finishes — 120.1 s later |
| 3 | `07:10:15.3134` | next test finishes, milliseconds again |

Neighbouring tests run in **milliseconds**, so the runner was not globally slow.
But pytest emits its line when a test *finishes*, so the 120.1 s gap is
`setup + fixture build + subprocess + teardown` — and `_build_repro_fixture`
spawns `git init`, `git add`, `git commit`, plus the failing test's extra
`git add`, before the timed subprocess starts. That the gap lands near 120 s is
a coincidence of magnitude, not a measurement of the subprocess.

**The sibling comparison is NOT controlled.** The failing test differs in TWO
ways, not one: `unexpected_root/` is not gitignored, AND the failing test alone
creates that directory, writes `stuff.md` into it, and runs an extra `git add`.
The gitignored sibling never creates the directory at all. So "the slow path is
the RC-NONSTD-DIR-001 firing branch" is the leading hypothesis, not an
established fact — the Linux repro must separate those two variables.

## Blame is established by elimination, not by reproduction

`c441703c` **is** the v5.16.2 release commit and its CI was **green** (as were
the three runs before it, all 2026-09-02). The first red is `f1882af9`.

The elimination set was VERIFIED against the commit graph, not inferred from a
branch-filtered CI listing (`git merge-base --is-ancestor c441703c f1882af9` →
`ANCESTOR OK`). It contains **four** commits, not the two first claimed:

| # | Commit | Contents | Code? |
|---|---|---|---|
| 1 | `057d469d` | docs: archive TRDD-UW4CQ64E — CLAUDE.md, one TRDD, two manifests | no |
| 2 | `9e7d2c1e` | RC-164 fold + CC spec sync 2.1.258-260 | **yes** |
| 3 | `1f8c8496` | TRDD card + hash manifests | no |
| 4 | `f1882af9` | `chore(release): v5.17.0` — bump, CHANGELOG, README, manifests, and a one-line `__version__` edit in `scripts/cpv_skillaudit_native.py` | one line |

`057d469d` was **never CI-tested** — it sat unpushed, which is what the
unexplained `git rev-list --left-right --count origin/master...master` → `0 2`
was reporting. It is docs + manifests only, verified by `git show --stat`.

The release commit is not a pure version bump, but its only code change is
`__version__ = "5.16.2"` → `"5.17.0"`. That is not automatically inert — the
skillaudit cache is keyed on `(content, catalog, __version__, ext)`, so a bump
invalidates it — but a cold cache was measured at 0 s (hypothesis 5), which
rules out cache-miss *cost*.

So of the four, only `9e7d2c1e` carries substantive code, and blame rests
there. **This is still elimination, not reproduction.** If the Docker repro
comes back green at `9e7d2c1e`, do not conclude "no bug" — widen to the whole
range and to the non-Linux residual above.

## Five hypotheses, all REFUTED by measurement (do not re-test these)

Fixture rebuilt to match `_build_repro_fixture` + the failing test's extra
`unexpected_root/` + `git add`. All runs `PLUGIN_SKIP_GITHUB_INTEGRITY=1`,
`cwd` = repo root, macOS:

| # | Hypothesis | Measurement | Verdict |
|---|---|---|---|
| 1 | The RC-164 AST write-sink pass slowed the validator | HEAD ×3: **1 s, 1 s, 0 s** | refuted |
| 2 | Missing `google-re2` → catastrophic backtracking | re2 import shadowed ×2: **0 s, 1 s** | refuted |
| 3 | `uv` environment resolution consumed the 120 s | no resolve/install/download lines anywhere in the shard log | unsupported |
| 4 | The test or its `timeout=120` was recently tightened | `timeout=120` dates to `e33756b3`; file untouched since `48a87e1e` | refuted |
| 5 | Cold skillaudit cache (warm locally, cold on CI) | `CPV_SCAN_CACHE=0`: **0 s** | refuted |

Also checked and **not** the variable: `43a62328` ("external scanners always
run") is already an ancestor of the previous *green* release; and hiding the
locally-installed `~/.local/bin/tirith` from `PATH` still gives a 1 s run
(`validate_plugin.py` does not invoke the external scanners — that is
`validate_security.py`).

**Instrumentation note worth keeping:** hypothesis 2's first control only proved
the stub file raises `ImportError`, not that `PYTHONPATH` survives `uv run`.
Re-run without the `sys.path.insert` crutch, it did propagate and re2 was
genuinely absent. Without that second check the "refuted" would have been
worthless — a proxy read standing in for the thing.

## Acceptance criteria

- [~] **OBSOLETE — no Linux run needed.** (Original: "the failure is reproduced
      on Linux (Docker, no `google-re2`)".) The cost was attributed to a NAMED
      phase, `run_lint_engine`, on macOS from the tool's own per-phase lines —
      which is what this criterion actually wanted.
- [x] The costly phase is stated as a measured fact — `run_lint_engine`, 30.2 s
      of a 30.5 s run, every other phase 0.0–0.2 s.
- [ ] **The MECHANISM inside that phase is stated as a measured fact, not a
      hypothesis.** Still open — tracked on TRDD-1VU6Y5MS.
- [x] **Did `9e7d2c1e` slow the gitignore-filtered walk?** **No.**
      `detect_languages()` timed directly, n=50/arm: 13.364 ms (HEAD) vs
      13.624 ms (`9e7d2c1e^`). The walk is ~13 ms at both commits, three orders
      of magnitude below the phase cost. That was the only path by which the
      commit could reach this phase, so it is cleared for THIS mechanism.
- [x] **What made the same test 11.4 s on one CI run and 120 s on the next?**
      An external, network-dependent cost in the markdownlint spawn — not a
      regression. Proven by elimination on positive evidence, not a null result:
      the walk is 13 ms at both commits (n=50/arm), and a full per-test gap
      comparison of both shard-2 logs shows every other test on a 3200-test
      shard unchanged at the same position. **No shipped regression.** The
      remaining design question (a per-linter budget that can never degrade
      gracefully, and an unpriced first-run fetch) is TRDD-1VU6Y5MS's.
- [x] A fix lands whose non-vacuity is mutation-proven: reverting
      `PLUGIN_SKIP_REPO_LINT=1` restores the cost (16.1 s warm / 30–49 s cold,
      vs 0.64 s with it) — measured both ways.
- [x] The coverage the fix would otherwise have removed is replaced by a
      DIRECT assertion. `TestLintWalkerHonoursGitignore` now asserts on
      `detect_languages()` — in-process, no linter spawned — that the lint
      walker sees a tracked `.md` and never one under gitignored `INPUT_DEV/`.
      That property was previously only proven incidentally, by unasserted
      subprocess output.
- [ ] CI is green on the fix commit — verified from the job conclusion, never
      from a publish exit code.
- [ ] A patch release ships with green CI.

## Second finding — which post-push gates are advisory, and which are fatal?

`publish.py` exited **0** on a run whose own Gate 14 printed
`✗ CI IS RED on the released commit f1882af9: CI=failure`. Gate 14 runs after
Gate 12 has already pushed, so it cannot un-ship the release.

The real question is not "should Gate 14 exit non-zero" but **which post-push
gates are advisory and which are fatal** — answered once, for all of them. The
evidence that this is currently unanswered rather than deliberate: Gate 15
(install smoke) ran and PASSED *after* Gate 14 printed red, so the pipeline
already treats post-push gates as advisory in practice, without saying so.

Whatever the answer, the exit code must not report success for a release known
to be red — an automated caller reading `0` as healthy is how this one got past
review. Decide whether this becomes its own card.

**Resolved 2026-09-04: it did.** This finding now lives on its own card,
**TRDD-4VROKH40** (`design/tasks/TRDD-20260904_104137+0200-4VROKH40-gate-14-prints-ci-red-but-exits-zero.md`).
Do not work it here; MHCFOCBV closes on the timeout alone.

## Notes

Do not "fix" this by raising `timeout=120`. The budget is not the defect; a
call that takes under a second on one platform and over two minutes on another
is the defect, and raising the timeout would hide it while leaving every real
user of the non-gitignored-dir path on the slow branch.
