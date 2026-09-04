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
Every macOS configuration tried runs the same call in ~1 s, so the missing
variable is the platform. Do NOT propose a sixth mechanism before a Linux run
exists.

## The failure

One test, one shard, **deterministic across two runs** (initial + `gh run rerun --failed`):

```
tests/test_issue_37_gitignore_walkers.py::TestNonstdDirRuleHonoursGitignore::test_rc_nonstd_dir_still_fires_for_non_gitignored_dirs
subprocess.TimeoutExpired  —  timeout=120
```

It shells out to `uv run python scripts/validate_plugin.py <fixture> --strict`
with `cwd=REPO` and `PLUGIN_SKIP_GITHUB_INTEGRITY=1`. Shard 2 result:
`1 failed, 3361 passed, 19 skipped`. Lint, Validate and shards 1/3/4 are green.

### The timestamps localize it precisely

From the shard-2 log — neighbouring tests run in **milliseconds**, so the runner
was not globally slow:

| # | Time | Event |
|---|---|---|
| 1 | `07:08:14.7178` | sibling `test_rc_nonstd_dir_is_not_flagged_when_gitignored` finishes (same 120 s budget, PASSES) |
| 2 | `07:10:14.8393` | this test finishes — **120.1 s later, the full timeout** |
| 3 | `07:10:15.3134` | next test finishes, milliseconds again |

The two siblings differ in exactly one way: the failing one's `unexpected_root/`
is **NOT gitignored**, so the walker descends it instead of pruning. The slow
path is therefore inside the RC-NONSTD-DIR-001 *firing* branch.

## Blame is established by elimination, not by reproduction

`c441703c` **is** the v5.16.2 release commit and its CI was **green** (as were
the three runs before it, all 2026-09-02). The first red is `f1882af9`. The only
changes in between are this session's `9e7d2c1e` (RC-164 fold + CC spec sync
2.1.258-260) and `1f8c8496` (a TRDD card + hash manifests). So the regression is
in `9e7d2c1e` — but see the caveat: no local run reproduces it.

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

## Second finding — Gate 14 sees the red and does not stop

`publish.py` exited **0** on a run whose own Gate 14 printed
`✗ CI IS RED on the released commit f1882af9: CI=failure`. Gate 14 runs after
Gate 12 has already pushed, so it cannot un-ship the release — but the exit code
then reports success for a release known to be red. At minimum the exit code
should carry the red, so an automated caller cannot read `0` as healthy. Decide
whether that is a separate card.

## Notes

Do not "fix" this by raising `timeout=120`. The budget is not the defect; a
call that takes under a second on one platform and over two minutes on another
is the defect, and raising the timeout would hide it while leaving every real
user of the non-gitignored-dir path on the slow branch.
