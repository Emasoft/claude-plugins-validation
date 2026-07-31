---
trdd-id: 8U864THQ
title: A file whose per-file scan did not complete must block, not pass
column: dev
created: 2026-07-31T07:10:07+0200
updated: 2026-07-31T07:10:07+0200
current-owner: cpv-main-session
task-type: security
relevant-rules: []
external-refs: [issue-180, issue-181, issue-182]
---

# A file whose per-file scan did not complete must block, not pass

## ⏵ STATE — READ THIS FIRST ON RESUME (authoritative; supersedes the body) — 2026-07-31

**NEXT ACTION:** none — implemented, verified, and shipped in v4.2.0.

**What is DONE:** everything in "The change". `retry_failed_serially` +
`result_is_timeout` in `cpv_parallel_runner.py`; `validate_security` sink →
`report.major`; `cpv_skillaudit_native` sink → `"high"` (→ MAJOR) with the wrong
comment corrected; both call sites retry crashes inside the existing `try` so the
worker env is still set. 14 two-sided tests in
`tests/test_issue_182_unscanned_file_blocks.py`; non-vacuity proven by neutering
all three changes at once (4 tests fail, the 10 controls still pass). The fixture
A/B now reads `MAJOR:5 WARNING:0` where it read `MAJOR:4 WARNING:1`.

**SUPERSEDED — do NOT carry forward:** the pre-compaction directive said "bound
the `parallel_scan` wait — `future.result(timeout=timeout_per_file)` at ALL 14
call sites". **That plan is WRONG and must not be executed.** See "Why the
obvious fix does not work".

## The defect (demonstrated end-to-end, not inferred)

`parallel_scan` returns `ScanResult(findings=[], error=<msg>)` when a per-file
worker times out or dies. `findings=[]` — an empty finding list is
indistinguishable, downstream, from "this file is clean".

How the two SECURITY sinks treat that today:

| sink | severity | blocks default? | blocks `--strict`? |
|---|---|---|---|
| `validate_security.py:6766` | `report.warning(...)` | no | **no** |
| `cpv_skillaudit_native.py:4726` | `severity:"low"` → nit | no | yes |

`cpv_validation_common.exit_code_strict()` states it plainly: *"WARNING still
does not block even in strict mode."* So a file whose security scan failed
yields a PASS.

**Proof (run, not reasoned).** Fixture `/tmp/cpv-bypass-probe/evil` — a plugin
whose `skills/demo/SKILL.md` carries a `curl … | sh` payload, padded to 5.7 MB so
its scan takes 7.27 s.

| run | scanned | severities |
|---|---|---|
| baseline | `1 scanned, 0 skipped` | `MAJOR:4 NIT:3 **WARNING:0**` |
| `CPV_SCAN_SKIP_STUCK_AFTER=0.5` | `0 scanned, 1 skipped` | `MAJOR:4 NIT:2 **WARNING:1**` |

The payload's blocking NIT **disappeared** and was replaced by a WARNING that can
never block. The 4 MAJORs are constant fixture noise from checks outside this
path. This is issue #182's integrity bypass, reproduced with an existing env knob
and no code change.

## Why the obvious fix does not work

Adding `timeout_per_file` to `parallel_scan` **cannot** fix the hang, and alone
would make things strictly worse:

1. `ProcessPoolExecutor` cannot cancel a *started* task. After
   `future.result(timeout=T)` expires the loop moves on, but the `with` block's
   `shutdown(wait=True)` (`cpv_parallel_runner.py:234`) then blocks on the wedged
   worker anyway. The hang is relocated, not removed. The module's own comment at
   `:191-197` already says this — it is why `cpv_scan_supervisor` exists.
2. Today a wedged run is killed by the CI job timeout, and since v3.22.2 the
   release gate is **fail-closed** on a killed job — so the hang currently
   BLOCKS. Adding a timeout whose outcome is a WARNING would convert a blocking
   failure into a pass.

Therefore the verdict semantics must be fixed **first**, and independently: they
are broken today, with no timeout involved.

## The change

1. **Shared helper** `retry_failed_serially(results, items, scan_func)` in
   `cpv_parallel_runner.py`: re-run each *crashed* item once, in-process. A
   transient pool death (CI OOM-kill) is absorbed; only a reproducible failure is
   reported. **Timeouts are NOT retried** — re-running a wedge in-process would
   hang the main thread. Distinguish on the `TimeoutError` prefix.
2. **Fail closed** at the sinks: a file that did not complete its scan is
   UNKNOWN, never clean. `validate_security` → MAJOR (blocks in *all* modes —
   the untrusted pre-install scan is default mode, which is where the integrity
   argument is strongest). `cpv_skillaudit_native` → `"high"` (→ MAJOR).
3. **Fix the misleading comment** at `cpv_skillaudit_native.py:4726`: it claims
   `"low"` is "emitted as WARNING-class". `_SEVERITY_MAP["low"] = "nit"`, which
   *does* block under `--strict`. The comment is wrong.

## Measurements (mine; CPV's own 1083-file tree, `CPV_SCAN_CACHE=0`, idle machine)

| path | max per-file | 2nd | 3rd | serial total |
|---|---|---|---|---|
| with google-re2 | 27.13 s | 10.39 s | 9.74 s | 174.3 s |
| **no-re2 (the CI path)** | **28.25 s** | 10.52 s | 10.30 s | 208.4 s |

The no-re2 fallback is only ~20 % slower in aggregate and its max is within 5 %
of the re2 path — the catalog's re2-safety work holds. A bound is therefore well
constrained: ~300 s is ~10.6× the measured worst file.

## Deliberately NOT in this TRDD

- **Making the bound default-on.** The right lever is the existing
  `cpv_scan_supervisor` (`hard_kill_after_s`, already env-wired as
  `CPV_SCAN_SKIP_STUCK_AFTER`), not `timeout_per_file`. It needs its own
  measurement of manager-process overhead on small scans plus a Linux
  fork-parity pass, because it changes the default pool path. Own TRDD.
- **REPO LINT / URL phase budgets → FAIL.** Those run style/correctness linters
  and dead-link checks, not the security scanners; skipping them hides no
  threat, and #162 showed a cold-runner storm would false-block. They stay
  WARNING.

## Verify

```bash
GIT_CONFIG_GLOBAL=/dev/null uv run pytest -p no:cacheprovider -o addopts="" -q tests/
PLUGIN_SKIP_GITHUB_INTEGRITY=1 CLAUDE_PRIVATE_USERNAMES="$(whoami)" CPV_SCAN_CACHE=0 \
  uv run --with pyyaml python scripts/remote_validation.py plugin . --strict
```

Pass criteria: the fixture above flips to a blocking verdict when its scan is
killed, while a clean plugin with no killed scan is unaffected; non-vacuity
proven by neutering the severity change.

## Approval log
