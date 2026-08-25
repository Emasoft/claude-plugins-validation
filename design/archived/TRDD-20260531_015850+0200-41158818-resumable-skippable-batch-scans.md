---
trdd-id: 41158818-f114-4dfb-af29-fc6b8fcacf95
title: Resumable + skippable batch scans — progress, stuck-detection, notifications (issue #56)
column: complete
created: 2026-05-31T01:58:50+0200
updated: 2026-08-25T17:25:16+0200
---

> **IMPLEMENTED 2026-05-31 (v2.111.0)** in `scripts/cpv_scan_supervisor.py`
> (same module as the #52 hard-kill — they are one supervisor). See
> [[TRDD-25e57b01]] for the kill mechanism.

<!-- markdownlint-disable-next-line MD025 -->
# TRDD-41158818 — Resumable + skippable batch scans (#56)

**Filename:** `design/tasks/TRDD-20260531_015850+0200-41158818-resumable-skippable-batch-scans.md`
**Tracked in:** this repo (design/tasks/ is git-tracked)

GitHub issue #56 asked for operator-grade supervision of long scans over large
corpora: per-element progress, stuck-element detection, skip-stuck, per-file
resume/inspect, and notifications. All five shipped on top of the #52 killable
supervisor.

## What shipped

1. **Per-file progress** — `supervised_scan` emits `start` / `finish` events
   (index / total / verdict / duration / worker) to an `on_event` callback;
   `stderr_progress_printer()` renders the `[cpv-scan wN] start i/n …` shape,
   gated by `CPV_SCAN_PROGRESS`.
2. **Stuck-element detection** — a `stuck_warn` event (default threshold
   `hard_kill_after_s / 2`) fires once per file that runs long, carrying path /
   index / elapsed.
3. **Skip-stuck** — `hard_kill_after_s` (the #52 SIGKILL) records the file
   `TIMED_OUT` and respawns the worker so the rest of the batch finishes.
4. **Resume / inspect** — per-file verdicts append to a JSONL sidecar as they
   are produced (so a killed run loses nothing); a small `<state>.json` pointer
   is rewritten each tick. `resume=True` skips files already recorded (index +
   path must both match); `inspect_state(path)` is a read-only snapshot for
   `--inspect`-style monitoring.
5. **Notification hook** — `notify` callback invoked when a file goes stuck;
   `default_notifier` does macOS `osascript` / Linux `notify-send` / a
   non-blocking write to `~/.cpv/notify.fifo`.

## Operator surface (CPV-idiomatic env knobs)

`scan_all_files` resolves these when the matching kwarg is unset, so ANY scan
path (validate_plugin, `scan_one_target`, …) gains supervision with no code
change: `CPV_SCAN_SKIP_STUCK_AFTER`, `CPV_SCAN_STUCK_WARN_AFTER`,
`CPV_SCAN_STATE`, `CPV_SCAN_RESUME`, `CPV_NOTIFY_ON_STUCK`, and (rich-progress
on the supervisor path) `CPV_SCAN_PROGRESS`. Explicit kwargs always win; unset
env keeps the legacy lean path. The reporter's at-scale `--scan-list` pipeline
builds directly on the `supervised_scan` Python API (one killable worker per
target).

## Tests

`tests/test_issue_52_hard_timeout_kill.py` — progress events, stuck-warn
(fires once, notifier called), resume-skips-completed, inspect snapshot, and
the env-knob path (`CPV_SCAN_SKIP_STUCK_AFTER` / `CPV_SCAN_STATE` route through
the supervisor and complete on a clean plugin).

## Acceptance

- Per-file progress, stuck-warn, skip-stuck, resume, inspect, notify all
  functional and tested. CPV self-scan 0/0/0/0; full suite green.

## Approval log

- 2026-08-25T17:25:16+0200 — CLOSED as complete by the CPV session (board drain; authority delegated by USER 2026-08-25). SHIPPED v2.111.0 — inspect_state/on_event/stuck_warn_after_s live (batch_ae)
