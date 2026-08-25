---
trdd-id: 71e68ab5-a74f-4f1b-b180-11a003bd6371
title: Batch-fix scalability — parallel-shard protocol for 300+ findings
column: complete
created: 2026-05-19T11:40:50+0200
updated: 2026-08-25T17:25:05+0200
---

<!-- markdownlint-disable-next-line MD025 -->
# TRDD-71e68ab5 — Batch-fix scalability via main-session-coordinated parallel sharding

**Filename:** `design/tasks/TRDD-20260519_114050+0200-71e68ab5-batch-fix-parallel-sharding.md`
**Tracked in:** this repo (`design/tasks/` is git-tracked)

## Origin (provenance)

User reports that on plugins with 300+ validation findings (typical
for plugins with many skills), spawning multiple `cpv-doctor-agent` or
`plugin-fixer` agents in parallel from the main session results in
each agent silently dying mid-task because the per-agent context
window is exhausted. The size of that window is per-model — bare
`opus` / `sonnet` = 200K tokens, the `opus[1m]` / `sonnet[1m]`
variants = 1M, future Claude models may differ — and model quality
degrades noticeably above ~50% utilisation, so the safe ceiling is
significantly lower than the raw context size. The single-agent
design assumes a small plugin fits comfortably in its model's safe
window, which breaks down at the 300-finding scale regardless of
which model is chosen.

Compounding the problem: `plugin-fixer.md:626` claims "for very large
batches (10+ files), parallel subagents are allowed, one per file" —
a factual error inherited from a pre-v2.89.0 mental model.
Subagents **cannot spawn subagents** per the Anthropic spec, so the
"orchestrating fixer" pattern has been silently no-op since v2.89.0.

Full audit + initial design report:
`reports/batch-fix-design/20260519_102837+0200-audit-and-design.md`.

## Problem statement

CPV's fix pipeline does not scale beyond ~50 findings per agent
because:

| # | Failure mode | Evidence |
|---|--------------|----------|
| 1 | One agent owns the entire validate→fix→revalidate loop | `agents/plugin-fixer.md` |
| 2 | Per-finding round-trip costs ~3-5K tokens; 300 findings exceeds even the 1M-context `opus[1m]`/`sonnet[1m]` *safe-utilisation* threshold (~50% = 500K), and is multiples-over the 200K of bare `opus`/`sonnet` | math: 300 × 5K = 1.5M >> 500K safe / 200K default |
| 3 | `plugin-fixer.md:626` promises an impossible "parallel subagents" pattern | line 626 |
| 4 | `cpv-doctor-agent` has `maxTurns: 30` — far below big-plugin needs | `agents/cpv-doctor-agent.md:25` |
| 5 | Main session cannot drive 300 sequential `Agent()` calls without burning its own context | architectural |

## Goal

Add a **three-tier batch-fix protocol** that:

1. Slices findings into shards using a Python planner (zero-LLM cost).
2. Dispatches N shard-fixer agents in parallel **from the main session in a single message** (the only place parallel `Agent()` calls are allowed per Anthropic spec).
3. Aggregates per-shard status files into one consolidated report using a Python aggregator (zero-LLM cost).
4. Runs the existing `deterministic-codemod` first to absorb the mechanical 30-60% of findings at zero LLM cost.

Main-session token cost: ~3-4K total (paths only, no report bodies).

## Out of scope

| # | Item | Reason |
|---|------|--------|
| 1 | Cross-agent state sharing during a run | Each shard is isolated — that's the entire point of the design |
| 2 | Auto-detection of optimal shard size from findings density and `plugin-fixer.model` | Default 30 is sufficient for bare `opus`/`sonnet` (200K); user/orchestrator tunes via `--shard-size` (raise to ~100-150 for the `[1m]` variants). Future TRDD may add planner-side model-aware tuning |
| 3 | Agent-pooling / connection reuse | Anthropic API handles that |
| 4 | Worktree isolation per shard | Possible later optimization (TRDD-future); not required for v1 |
| 5 | Live progress streaming | The Stop hook in `claude-menu-system` can do this in a follow-up; v1 returns aggregate summary on completion |

## Design

### Architecture

```
USER
  │
  ▼
/cpv-batch-fix <plugin-path>
  │
  ▼
[main session — Phase A — minimal token spend]
  │
  ├─ Bash: python3 scripts/cpv_batch_planner.py <plugin-path>
  │    │
  │    ▼
  │  reads validation report, applies deterministic-codemod first,
  │  groups residual findings by file, splits into N shards of ~30,
  │  writes /tmp/cpv-batch/<TS>/shard-{1..N}.json + index.json
  │
  ▼
[main session — Phase B — ONE message, N Agent() calls in parallel]
  │
  ├─ Agent(plugin-fixer, mode=batch_shard, shard=/tmp/.../shard-1.json) ─┐
  ├─ Agent(plugin-fixer, mode=batch_shard, shard=/tmp/.../shard-2.json) ─┤
  ├─ Agent(plugin-fixer, mode=batch_shard, shard=/tmp/.../shard-3.json) ─┼─ parallel
  └─ ...                                                                  ─┘
  │
  ▼
[each shard agent — fresh context (size = `plugin-fixer.model` window: 200K bare opus/sonnet, 1M for the [1m] variants) — owns ~30 findings (calibrated for the bare-opus default; raise --shard-size for 1M models)]
  │
  ▼
[main session — Phase C]
  │
  ├─ Bash: python3 scripts/cpv_batch_aggregator.py /tmp/cpv-batch/<TS>/
  │    │
  │    ▼
  │  reads all shard-{N}.status.json, writes consolidated report
  │
  ▼
USER sees: "DONE: 327 → 0 (deterministic: 141, LLM: 186, failures: 0). Report: ..."
```

### Components

| # | Path | Purpose |
|---|------|---------|
| 1 | `scripts/cpv_batch_planner.py` | Read report → group by file → run codemod → shard residual → write manifests |
| 2 | `scripts/cpv_batch_aggregator.py` | Read shard-status files → write consolidated report |
| 3 | `commands/cpv-batch-fix.md` | Main-session orchestrator slash command |
| 4 | `agents/plugin-fixer.md` — new `batch_shard` mode | Reads ONE shard manifest, fixes only its findings, writes status |
| 5 | `skills/batch-fix-protocol/SKILL.md` | Schema reference for shard manifest + status JSON |
| 6 | `tests/test_cpv_batch_planner.py` | Coverage for planner |
| 7 | `tests/test_cpv_batch_aggregator.py` | Coverage for aggregator |
| 8 | `tests/test_cpv_batch_fix_integration.py` | E2E test from a fixture validation report |

### Shard manifest schema (`shard-K.json`)

```json
{
  "schema_version": 1,
  "shard_id": 1,
  "shard_of": 6,
  "plugin_path": "/abs/path/to/plugin",
  "report_path": "/abs/path/to/source-report.md",
  "files": [
    {
      "path": "/abs/path/to/plugin/skills/foo/SKILL.md",
      "findings": [
        {
          "severity": "MAJOR",
          "rule": "RC-...",
          "message": "...",
          "line": 12
        }
      ]
    }
  ],
  "status_path": "/tmp/cpv-batch/<TS>/shard-1.status.json",
  "max_findings": 30,
  "deterministic_already_applied": true
}
```

### Shard status schema (`shard-K.status.json`)

```json
{
  "schema_version": 1,
  "shard_id": 1,
  "started_at": "2026-05-19T11:50:00+0200",
  "finished_at": "2026-05-19T11:54:23+0200",
  "fixed": 29,
  "failed": 1,
  "remaining": 0,
  "per_file": [
    {"path": "...", "fixed_count": 3, "remaining_count": 0, "errors": []}
  ],
  "agent_exit_reason": "clean" | "maxTurns" | "error"
}
```

### Bug fixes shipped alongside (because they bite us regardless)

| # | Fix | File |
|---|-----|------|
| 1 | Remove the "parallel subagents are allowed" claim | `agents/plugin-fixer.md:626` |
| 2 | Bump doctor `maxTurns` 30 → 100 | `agents/cpv-doctor-agent.md:25` |
| 3 | Doctor auto-recommends `/cpv-batch-fix` when findings > 100 | `agents/cpv-doctor-agent.md` body |

## Test plan

| # | Test file | What it pins |
|---|-----------|--------------|
| 1 | `test_cpv_batch_planner.py` | Shards size; codemod-first ordering; manifests well-formed; idempotent re-runs |
| 2 | `test_cpv_batch_aggregator.py` | Merges N statuses; preserves per-finding evidence; counts add up |
| 3 | `test_cpv_batch_fix_integration.py` | End-to-end on a fixture plugin with 60+ findings → 2 shards → both finish → aggregator merges → final count zero |
| 4 | `test_plugin_fixer_batch_shard_mode.py` | Plugin-fixer in batch_shard mode reads ONE manifest, doesn't browse outside it, exits with status JSON |
| 5 | `test_plugin_fixer_no_subagent_claim.py` | Regression-lock: `plugin-fixer.md` MUST NOT claim subagents are allowed |
| 6 | `test_cpv_doctor_maxturns.py` | Doctor maxTurns ≥ 100 |
| 7 | `test_cpv_doctor_recommends_batch.py` | Doctor body recommends `/cpv-batch-fix` for big plugins |

Target: 25-40 new tests, 0 regressions on the existing ~5300 tests.

## Severity rationale

This is a **MAJOR architectural** addition, not a bug fix. The
in-flight bug fixes (the `:626` stale claim, doctor maxTurns) are
MINOR but they're shipped with the new feature so they're not lost
in a follow-up.

## Acceptance criteria

- [ ] `scripts/cpv_batch_planner.py` implemented
- [ ] `scripts/cpv_batch_aggregator.py` implemented
- [ ] `commands/cpv-batch-fix.md` slash command works end-to-end
- [ ] `plugin-fixer.md` has `batch_shard` mode
- [ ] `plugin-fixer.md:626` no longer claims subagents are allowed
- [ ] `cpv-doctor-agent.md` maxTurns ≥ 100, recommends batch mode for big plugins
- [ ] `skills/batch-fix-protocol/SKILL.md` documents the schema
- [ ] All new tests pass; full suite still green; self-scan 0/0/0/0
- [ ] Documentation updated in MEMORY.md + README

## Risks + mitigations

| # | Risk | Mitigation |
|---|------|------------|
| 1 | Two shards both edit the same file (rare — planner groups by file) | Planner enforces: each file belongs to exactly one shard |
| 2 | Anthropic API rate-limits when N>8 parallel agents | Planner caps default parallelism at 8 |
| 3 | Shard agent dies mid-shard | Status JSON is checkpointed per finding; re-run picks up residue |
| 4 | Validation report format changes | Planner reads structured JSON sidecar (already produced by validate_plugin.py per v2.89.3) |
| 5 | Deterministic codemod has a bug → wrongly auto-fixes a finding | Existing per-file backup in `.cpv-codemod-backup/<ts>/`; codemod is opt-out via `--no-codemod` flag |
| 6 | User reads stale "subagents allowed" guidance from a cached `plugin-fixer.md` | Bug fix in §3c lands in this same release, so cache update fixes both at once |

## Phases

| # | Phase | Deliverables |
|---|-------|--------------|
| 1 | Foundation | Planner + aggregator + schemas + bug fixes + tests |
| 2 | Orchestration | Slash command + plugin-fixer `batch_shard` mode + integration test |
| 3 | Documentation | Skill reference + README/MEMORY updates |
| 4 | Validation | Full test suite green + self-scan green + version bump + push |

Phases 1-4 in this session per user direction "yes do it".

## Approval log

- 2026-08-25T17:25:05+0200 — CLOSED as complete by the CPV session (board drain; authority delegated by USER 2026-08-25). SHIPPED v2.91.0 — cpv_batch_planner.py + cpv-batch-* commands live (batch_ab)
