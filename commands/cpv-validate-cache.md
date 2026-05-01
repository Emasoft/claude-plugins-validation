---
name: cpv-validate-cache
description: Audit a plugin or project for prompt-cache invalidation patterns (CA-01..CA-06)
allowed-tools: Read, Bash, Glob, Grep, AskUserQuestion
argument-hint: "<plugin_or_project_path> [--strict] [--verbose] [--report PATH]"
user-invocable: true
---

# /cpv-validate-cache Command

Runs the **cache-audit validator** (`validate_cache.py`) against a Claude Code plugin OR a project root that uses Claude Code. The validator catches six documented patterns that silently break Anthropic's prompt cache and multiply per-turn API costs by 5-10x:

| Rule | Severity | What it catches |
|---|---|---|
| **CA-01** | MAJOR | `{{TIMESTAMP}}` / `$(date)` / `${RANDOM}` / other dynamic substitutions inside cached content (`CLAUDE.md`, `agents/*.md`, `skills/*/SKILL.md`) |
| **CA-02** | MAJOR | `SessionStart` / `UserPromptSubmit` / `PreCompact` hooks that WRITE to `CLAUDE.md` or `settings.json` |
| **CA-03** | MAJOR | Hook scripts that flip `permissions.allow` / `permissions.deny` / `enabledMcpServers` between turns |
| **CA-04** | MINOR | `SKILL.md model:` frontmatter forcing an in-line model switch (use a dedicated agent instead) |
| **CA-05** | MINOR | Hook scripts running unbounded-output commands (`git status`, `find`, `ls -laR`, `cat <large-file>`) without size caps |
| **CA-06** | WARNING | `PreCompact` / `PostCompact` / `SubagentStart` hooks that don't preserve the cached prefix |

These rules sit on top of the project's general security/structure validators — run this command **in addition to** `/cpv-validate-plugin` for any plugin that ships hooks, skills, or agents.

## Usage

```
/cpv-validate-cache <plugin_or_project_path> [options]
```

## Arguments

| Argument | Required | Description |
|----------|----------|-------------|
| `plugin_or_project_path` | Yes | Path to the plugin directory OR a project root that uses Claude Code (`.claude/` configs are scanned too) |

## Options

| Option | Description |
|---|---|
| `--strict` | Treat MINOR (CA-04, CA-05) and WARNING (CA-06) as blocking failures |
| `--verbose` | Show all checks including PASSED |
| `--report PATH` | Write the full aggregated report to PATH explicitly |
| `--json` | Output as JSON (skips the auto-saved report file) |

> **Default output is path-only.** Without `--json` or `--report`, the script auto-saves to `${CLAUDE_PROJECT_DIR}/reports/cache/<timestamp>-<slug>.md` and prints **only** the compact summary (counts table + verdict + plugin path + report path). Token-bounded so the calling agent never gets flooded.

## Workflow

The command runs:

```bash
uv run python "${CLAUDE_PLUGIN_ROOT:-.}/scripts/validate_cache.py" <plugin_or_project_path>
```

with the report path defaulted to `${CLAUDE_PROJECT_DIR}/reports/cache/$(date +%Y%m%d_%H%M%S%z)-<slug>.md`. The validator walks the tree, applies CA-01..CA-06 in order, and writes a per-rule aggregated report. The summary printed to stdout includes the per-severity counts, the verdict, the plugin path, and the report path.

## Exit Codes

| Code | Meaning |
|---|---|
| `0` | No cache-invalidation issues found |
| `1` | CRITICAL (the cache layer never raises CRITICAL — reserved for the security validator) |
| `2` | MAJOR cache-invalidation found (CA-01 / CA-02 / CA-03) — fix before publish |
| `3` | MINOR cost/latency issue found (CA-04 / CA-05) — recommended to fix |
| `4` | NIT-level only (`--strict` mode) |

## Examples

```
# Standard scan — auto-saves report, prints compact summary
/cpv-validate-cache ./my-plugin/

# Strict mode — MINOR + WARNING also block
/cpv-validate-cache ./my-plugin/ --strict

# Save to a specific path for CI artifacts
/cpv-validate-cache ./my-plugin/ --report /tmp/cache-audit.md

# Audit a project root (not a plugin), including its .claude/ configs
/cpv-validate-cache /path/to/my/project/
```

## Related

- `/cpv-cache-optimize` — Interactive agent that validates AND fixes the issues, plus broader cache-aware improvements to the plugin's skills/agents/commands/CLAUDE.md.
- `/cpv-validate-plugin` — Full plugin validation (structure, security, skills, etc.). Run this in addition for full coverage.
- See `skills/fix-validation/references/cache-fixes.md` for the per-rule fix recipe used by `/cpv-cache-optimize`.

## Reference

The CA-01..CA-06 rule pack is derived from the documented prompt-caching patterns in *"Lessons from Building Claude Code: Prompt Caching Is Everything"* by Thariq Shihipar (Anthropic) and from the open-source [ussumant/cache-audit](https://github.com/ussumant/cache-audit) corpus.
