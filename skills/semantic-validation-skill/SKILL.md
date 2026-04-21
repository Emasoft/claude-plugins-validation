---
name: semantic-validation-skill
description: "Deep AI semantic validation for skills/agents. Use when checking triggering, clarity, examples. Loaded by semantic-validator agent. 10x token cost."
tags:
  - validation
  - semantic
  - quality
  - skills
user-invocable: false
allowed-tools: Read, Bash(uv*), Bash(python*), Glob, Grep
---

# Semantic Validation Skill

Deep semantic analysis of skill and agent quality — things that automated scripts cannot check.

## Overview

This skill performs AI-driven evaluation of:
- Description triggering effectiveness (will it fire at the right time?)
- Instruction clarity and completeness
- Example quality (realistic, complete, covers failure paths)
- Workflow completeness (start/end, branches, exit conditions)
- Technical quality (exit codes, error handling, terminology)
- Progressive disclosure effectiveness
- Output pattern documentation
- **Channel MCP server source-code security** (sender-gating, prompt-injection prevention — runs only when `plugin.json.channels` is non-empty)

**Cost**: Uses opus model. Only invoke when you need deep quality analysis beyond what scripts provide.

This produces a **Semantic Grade (A-F)**, complementary to the **Syntactic Score (0-100)** from script validation. The two systems are independent — run syntactic validation first (cheap), then semantic validation only when needed.

## Prerequisites

- Python 3.12+ with `pyyaml` installed
- `uv` package manager
- Skill or agent directory to analyze

## Instructions

1. Navigate to the claude-plugins-validation directory
2. Run the command: `/cpv-semantic-validation <skill_or_agent_path>`
3. Baseline script validation: `uv run python scripts/validate_skill_comprehensive.py "<skill_path>" --strict --report "$MAIN_ROOT/reports/validate_skill/$(date +%Y%m%d_%H%M%S%z)-baseline.md"`
4. The agent reads the actual SKILL.md and agent .md files
5. The agent evaluates 7 core semantic criteria plus 1 conditional pillar (Channel MCP Server Source-Code Security — only when `plugin.json.channels` is non-empty)
6. Review the grade (A-F) and report at `$MAIN_ROOT/reports/semantic-validator/<ts±tz>-<slug>.md`

## Output

- **Grade**: A-F letter grade based on semantic quality
- **Criteria Results**: Pass/Partial/Fail for each of 7 core criteria, plus Pass/Partial/Fail/N/A for the conditional Channel Source Security pillar
- **Report File**: `$MAIN_ROOT/reports/semantic-validator/<ts±tz>-<slug>.md` (main-repo root via `git worktree list | head -n1`). Per-component subfolder + local+GMT-offset timestamp. Both `reports/` and `reports_dev/` gitignored.

## Error Handling

- If script validation fails with CRITICAL issues, semantic analysis is skipped — fix structural issues first.
- If the skill path is invalid, the agent reports the error and exits.

## Examples

### Example 1: Validate a Skill Semantically

```
/cpv-semantic-validation ./skills/my-skill/
```

### Example 2: Validate an Agent

```
/cpv-semantic-validation ./agents/my-agent.md
```

## Token Optimization

- **Explicit opt-in only** — never run automatically. Opus, 10x cost.
- **Run script baseline first** — syntactic check catches 90% of issues.
- **Read only the target files** — not the entire plugin tree.
- **Write full report to file** — return only grade + filepath.
- **Prefer LLM Externalizer MCP** (`chat`, `code_task`) for file reads. Pass paths via `input_files_paths`.

## Pillar: Channel MCP Server Source-Code Security

Runs only when `plugin.json.channels` is non-empty AND the plugin ships MCP server source code. Skip entirely for plugins with no channels.

Two attack vectors only an LLM reading the server source can catch:

1. Ungated inbound messages — no sender-ID allowlist => prompt-injection vector.
2. Permission-relay without sender gating — `capabilities.experimental['claude/channel/permission']` declared without sender check => external senders can approve destructive tool calls.

Load [channel-source-security](references/channel-source-security.md):
- Why This Pillar Exists
- Workflow
- Rule 1 — Sender-ID allowlist (CRITICAL)
- Rule 2 — Permission-relay gating (CRITICAL)
- Rule 3 — Chat-ID-only gating (MAJOR)
- Rule 4 — Fully gated (PASSED)
- Example vulnerable code
- Example safe code
- Opus prompt template
- Rubric contribution

## Resources

- Semantic Validation Criteria — see `skills/fix-validation/references/skill-semantic-validation.md` for full criteria, rubrics, report format
- `skill-validation-skill` — Script-based validation (cheap, fast)
- `plugin-validation-skill` — Full plugin validation

## Validation Checklist
Copy this checklist and track your progress:
- [ ] Confirm explicit user opt-in
- [ ] Run semantic validation on target skill
- [ ] Review A-F grade and per-criterion scores
- [ ] Address failing criteria
- [ ] If `plugin.json.channels` is non-empty, run the Channel MCP Server Source-Code Security pillar against every referenced MCP server source file
