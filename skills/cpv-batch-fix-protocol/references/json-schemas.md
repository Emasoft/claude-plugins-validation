# Batch-fix protocol — JSON schemas

## Table of Contents

- [1. Top-level index (`index.json`)](#1-top-level-index-indexjson)
- [2. Per-shard manifest (`shard-K.json`)](#2-per-shard-manifest-shard-kjson)
- [3. Per-shard status (`shard-K.status.json`)](#3-per-shard-status-shard-kstatusjson)
- [Schema version bumps](#schema-version-bumps)

The protocol uses **three** JSON file shapes in one session directory.
Session-dir convention:
`${TMPDIR:-/tmp}/cpv-batch/<YYYYMMDD_HHMMSS±HHMM>/`.

## 1. Top-level index (`index.json`)

Written by `cpv_batch_planner.py`. Read by `/cpv-batch-fix` to know how
many shards to dispatch and at what parallelism cap.

```json
{
  "schema_version": 2,
  "created_at": "2026-05-19T11:50:00+0200",
  "plugin_path": "/abs/path/to/plugin",
  "report_source": "validate_plugin.py(...)" ,
  "shard_count": 6,
  "max_parallel": 6,
  "shard_size": 30,
  "total_findings": 155,
  "counts_by_severity": {"CRITICAL": 5, "MAJOR": 50, "MINOR": 100, "NIT": 0, "WARNING": 0},
  "shards": [
    {
      "shard_id": 1,
      "scope_count": 6,
      "file_count": 6,
      "finding_count": 30,
      "manifest_path": "/tmp/cpv-batch/<ts>/shard-1.json",
      "status_path": "/tmp/cpv-batch/<ts>/shard-1.status.json"
    }
  ]
}
```

Note: `file_count` is a v1-compat alias for `scope_count`.

## 2. Per-shard manifest (`shard-K.json`)

Written by `cpv_batch_planner.py`. Read by `cpv-plugin-fixer-agent` in
`batch_shard` mode. **Each SCOPE appears in exactly one shard** —
two shards never edit the same scope concurrently.

Schema v2 introduces scope-based ownership (`scope_path` +
`scope_kind`) instead of v1's single-file rule. The shard agent has
refactoring rights INSIDE a `skill_dir` scope: it may split an
oversized SKILL.md into multiple smaller focused skills, create new
sibling skill directories with prefix-matched names, externalise
content into `references/*.md`, etc. For `scope_kind: "file"`
scopes, only that single file may be edited.

```json
{
  "schema_version": 2,
  "shard_id": 1,
  "shard_of": 6,
  "plugin_path": "/abs/path/to/plugin",
  "report_source": "validate_plugin.py(...)",
  "max_findings": 30,
  "status_path": "/tmp/cpv-batch/<ts>/shard-1.status.json",
  "scopes": [
    {
      "scope_path": "skills/example-skill/",
      "scope_kind": "skill_dir",
      "finding_count": 5,
      "findings": [
        {"level": "MAJOR", "message": "Required section missing: '## Overview' (Nixtla strict mode)", "file": "skills/example-skill/SKILL.md", "line": null}
      ]
    },
    {
      "scope_path": "agents/some-agent.md",
      "scope_kind": "file",
      "finding_count": 2,
      "findings": [
        {"level": "MINOR", "message": "Missing tools list", "file": "agents/some-agent.md", "line": 4}
      ]
    }
  ],
  "files": [
    {
      "path": "skills/example-skill/",
      "scope_kind": "skill_dir",
      "finding_count": 5,
      "findings": [
        {"level": "MAJOR", "message": "Required section missing: '## Overview' (Nixtla strict mode)", "file": "skills/example-skill/SKILL.md", "line": null}
      ]
    }
  ]
}
```

The `files` field is a v1-compat alias for `scopes` with the same
data (so a v1-only reader still sees something useful). New consumers
MUST read `scopes`.

### Scope-derivation rules

| # | Finding path | Scope path | Scope kind |
|---|--------------|------------|------------|
| 1 | `skills/foo/SKILL.md` | `skills/foo/` | `skill_dir` |
| 2 | `skills/foo/references/X.md` | `skills/foo/` | `skill_dir` |
| 3 | `skills/bar/SKILL.md` | `skills/bar/` | `skill_dir` |
| 4 | `agents/bar.md` | `agents/bar.md` | `file` |
| 5 | `commands/baz.md` | `commands/baz.md` | `file` |
| 6 | `README.md` | `README.md` | `file` |
| 7 | `.claude-plugin/plugin.json` | `.claude-plugin/plugin.json` | `file` |

## 3. Per-shard status (`shard-K.status.json`)

Written by `cpv-plugin-fixer-agent` (batch_shard mode). Read by
`cpv_batch_aggregator.py`. **Atomic writes only** — never partial.
Each per-file fix completes its status update before moving to the
next file, so a crash mid-shard leaves a useful checkpoint for a
follow-up `/cpv-batch-fix` run.

```json
{
  "schema_version": 1,
  "shard_id": 1,
  "started_at": "2026-05-19T11:50:00+0200",
  "finished_at": "2026-05-19T11:54:23+0200",
  "fixed": 29,
  "failed": 1,
  "remaining": 0,
  "agent_exit_reason": "clean",
  "per_file": [
    {
      "path": "skills/example-skill/SKILL.md",
      "fixed_count": 5,
      "remaining_count": 0,
      "errors": []
    }
  ]
}
```

| Field | Type | Notes |
|-------|------|-------|
| `shard_id` | int | Matches the manifest's `shard_id` |
| `started_at` | ISO 8601 + TZ | Set when the shard agent first writes status |
| `finished_at` | ISO 8601 + TZ | Set only on clean/partial/error exit; empty while running |
| `fixed` | int | Total findings successfully fixed across all files in the shard |
| `failed` | int | Findings that the agent attempted but couldn't fix |
| `remaining` | int | Findings the agent never got to (e.g. maxTurns hit) |
| `agent_exit_reason` | enum | `clean` = `fixed == total`; `partial` = ran out; `error` = unrecoverable |
| `per_file[]` | array | One entry per file in the manifest, with per-file fix counts and error list |

## Schema version bumps

When changing any of the three schemas, bump `schema_version` and
update both:

- `scripts/cpv_batch_planner.py:SCHEMA_VERSION`
- `scripts/cpv_batch_aggregator.py:SCHEMA_VERSION`

Plus the tests in:

- `tests/test_cpv_batch_planner.py`
- `tests/test_cpv_batch_aggregator.py`
