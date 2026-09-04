---
trdd-id: MHCFOCBV
title: v5.17.0 shipped with red CI - one Linux-only 120s validate_plugin timeout in the non-gitignored-dir branch
column: todo
created: 2026-09-04T09:30:47+0200
updated: 2026-09-04T09:30:47+0200
current-owner: cpv-main-session
task-type: bugfix
priority: high
severity: major
min-approval-requirement: none
relevant-rules: []
---

# v5.17.0 released with CI red — a Linux-only 120 s timeout

## ⏵ STATE — READ THIS FIRST ON RESUME (authoritative; supersedes the body) — 2026-09-04

v5.17.0 is **published and installing cleanly**, but CI is **RED** on the
released commit `f1882af9`. The release went out anyway because `publish.py`
Gate 14 observes CI *after* the push and does not block on it — `PUBLISH2_EXIT=0`
and `✗ CI IS RED` are both true of the same run. That gate behaviour is a second
finding, recorded at the bottom.

**NEXT ACTION:** reproduce on Linux (Docker, `ubuntu` + `uv`, no `google-re2`).
Every macOS configuration tried runs the same call in ~1 s, so the residual is
**"not reproduced outside CI"** — which includes Linux, but also the runner's
disk, `$HOME`, git version, the absence of a warm `~/.local/bin/tirith`, the
pytest-split shard's working directory, and any workflow env var not replicated
here. Docker is the cheapest way to test the largest chunk of that residual;
**a green Docker run does not close the question.** Do NOT propose a sixth
mechanism before a Linux run exists.

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

- [ ] The failure is reproduced on Linux (Docker, no `google-re2`), with the
      per-phase `[cpv-phase] DONE <name> <N>s` lines captured so the 120 s is
      attributed to a NAMED phase rather than inferred.
- [ ] The mechanism is stated as a measured fact, not a hypothesis.
- [ ] A fix lands whose non-vacuity is mutation-proven: reverting it makes the
      test fail again.
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

## Notes

Do not "fix" this by raising `timeout=120`. The budget is not the defect; a
call that takes under a second on one platform and over two minutes on another
is the defect, and raising the timeout would hide it while leaving every real
user of the non-gitignored-dir path on the slow branch.
