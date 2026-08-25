---
trdd-id: QK4M8T2R
title: cpv-agent self-heals broken agent definitions at launch instead of a manual batch fix
column: complete
created: 2026-07-26T16:10:27+0200
updated: 2026-07-26T16:10:27+0200
current-owner: cpv-session
task-type: feature
scope: project
release-via: publish
relevant-rules: []
implementation-commits: []
---

# cpv-agent self-heals broken agent definitions at launch instead of a manual batch fix

## Problem

Two classes of agent-definition defect fail **completely silently**:

- **A dead MCP grant.** `tools: mcp__chrome-devtools-*` (single separator) matches no real
  tool id — those are `mcp__chrome-devtools__<tool>` — so every one of that server's tools
  stays denied. The agent simply cannot act, with no error anywhere.
- **A duplicated top-level frontmatter key.** `tools:` written twice parses cleanly; YAML
  keeps the LAST occurrence and silently discards the first list.

Neither surfaces at dispatch time, so they rot indefinitely.

v3.18.0 shipped the detectors for these (D1 dup-key, D2 MCP grant, D3 shell fence, D6
`tools` ∩ `disallowedTools`). But **nothing ran them automatically** — they only fired if a
human remembered to point `validate_agent.py` at an agents directory. A detector nobody
invokes is equivalent to no detector.

The originally-planned response was a one-off batch fix of the 33 user-scope agents. The
user rejected that shape outright: *"leave each agent to fix itself running the cpv plugin
agent fixer. do not do it. just improve the cpv-agent to detect the issue and update the
agents if necessary when the cpv-agent is launched."* A batch fix repairs one corpus once;
a launch-time preflight repairs every corpus, forever, including plugins that do not exist
yet.

## Verified facts (measured before building)

- ✓ `validate_agent.py <dir> --json --strict` already supports directory scanning with
  per-agent counts and exit codes.
- ✓ The detectors genuinely FIRE — proven against the pre-fix backup
  `~/.claude/agents_backup_20260724_155849+0200`: exit 2, **4 blocking** agents
  (`data-visualization-specialist` dup key, `gs-researcher` + `pm-researcher` dead MCP
  grant, `python-test-writer` hardcoded path).
- ✓ The live corpus is genuinely CLEAN — 33 scanned, 0 blocking — and independently
  corroborated by grepping the known-bad patterns directly (absent live, present in the
  backup). So the clean verdict is real, **not** a vacuous green. A prior session had
  already fixed those 33.
- ✓ `exit_code` / `exit_code_strict()` in `cpv_validation_common.py` is the canonical
  severity policy (WARNING never blocks; NIT blocks only under strict).
- ✓ Cost: 0.37s wall for 33 agents — cheap enough to run unconditionally.

## Design

`scripts/cpv_agent_preflight.py` — deterministic, zero model tokens:

- `resolve_agent_dirs(target, home)` returns the dirs in scope: the target plugin's
  `agents/`, that project's `.claude/agents/`, and the user-scope `~/.claude/agents/`.
  De-duplicated by **resolved** path, because a duplicate would be handed to the fixer
  twice. Dirs that do not exist or hold no `.md` are skipped, not errors.
- `preflight(dirs, strict)` reuses `validate_agents_directory` and `exit_code_strict()`.
  **The severity policy is not forked** — a second copy would drift from the first.
- Exit `0` CLEAN / `1` FINDINGS / `2` ERROR.

`agents/cpv-agent.md` gains **Step 0**, before the work it was dispatched for: run the
script; on `1`, dispatch `cpv-plugin-fixer-agent` on exactly the listed paths (passing the
findings so it need not re-scan), re-check, then continue; on `2`, report UNKNOWN and
continue.

## Why the cure is not worse than the disease

An auto-repair that runs on every launch is intrusive by construction, so it is bounded:

1. **Only BLOCKING severities reach the fixer.** INFO/advisory findings are excluded —
   auto-"fixing" advisories would rewrite the user's agent files on every unrelated launch.
2. **Never hand-edit** — repairs go through `cpv-plugin-fixer-agent` (Rule 1).
3. **No loop.** A second identical result reports `[BLOCKED]` and the dispatched task
   proceeds; the preflight never holds the user's actual request hostage.
4. **Silent when clean**, which is the overwhelmingly common case.
5. **Exit 2 is UNKNOWN, never "clean"** — the [[lesson-cannot-check-is-not-clean]] rule.

## Test plan (two-sided throughout)

Every detection test is paired with a silence test, because a preflight that fires on a
clean corpus is a worse defect than one that misses: it would edit the user's files on
every launch. 16 tests — scope resolution (user/project/plugin, missing dirs, no-`.md`
dirs, de-duplication), detection vs silence, advisory-never-blocks, per-dir scan coverage,
all four exit paths, JSON parseability, and a contract test that `cpv-agent`'s body
actually invokes the script (a preflight nothing calls is dead code).

## Verification

- 16/16 new tests pass; `ruff` clean; `mypy` — no issues in 133 source files.
- End-to-end against the two real corpora: known-bad backup → `FINDINGS`, 4 blocking, with
  actionable root-cause messages; live corpus → `CLEAN`, 33 scanned, 0.37s.

## Notes

Supersedes Track 2 of the `compiled-munching-pebble` plan (batch-fix the 33 agents), which
is now unnecessary: that corpus is already clean, and any future breakage is repaired by
the launch preflight. Track 1 of that plan shipped as v3.18.0 — this TRDD is what makes
Track 1's detectors actually run.
