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
3. The agent runs cheap script validation first as a baseline:
   ```bash
   uv run python scripts/validate_skill_comprehensive.py "<skill_path>" --strict --report docs_dev/validate_semantic_baseline_YYYYMMDD.md
   ```
4. The agent reads the actual SKILL.md and agent .md files
5. The agent evaluates 7 core semantic criteria plus 1 conditional pillar (Channel MCP Server Source-Code Security — only when `plugin.json.channels` is non-empty)
6. Review the grade (A-F) and report at `docs_dev/semantic_validation_YYYYMMDD.md`

## Output

- **Grade**: A-F letter grade based on semantic quality
- **Criteria Results**: Pass/Partial/Fail for each of 7 core criteria, plus Pass/Partial/Fail/N/A for the conditional Channel Source Security pillar
- **Report File**: Full analysis saved to `docs_dev/semantic_validation_YYYYMMDD.md`

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

- **Explicit opt-in only** — never run automatically. Uses opus (10x cost).
- **Run script baseline first** — the cheap syntactic check catches 90% of issues.
- **Read only the target SKILL.md/agent .md** — not the entire plugin tree.
- **Write full report to file** — return only grade + filepath.
- **Prefer LLM Externalizer MCP** (`chat`, `code_task`) for file reading — save opus context for grading. Pass paths via `input_files_paths`.

## Pillar: Channel MCP Server Source-Code Security

**When it runs**: only when the target plugin's `plugin.json` contains a non-empty `channels` array AND ships MCP server source code referenced by `mcpServers.<server>.command` / `args`. Skip entirely for plugins with no channels.

**Why it matters**: `channels-reference.md` (Claude Code v2.1.80+) lets MCP servers forward inbound messages (Telegram, Discord, iMessage, etc.) to Claude. Two attack vectors exist that CPV's syntactic validators cannot catch — only an LLM reading the server source can:

1. **Ungated inbound messages.** Without a sender-ID allowlist, anyone who can reach the transport can inject arbitrary prompts into the Claude session.
2. **Permission-relay without sender gating.** A server declaring `capabilities.experimental['claude/channel/permission']` without sender gating lets any external sender approve destructive tool calls.

**Workflow**:
1. Read `plugin.json`. If `channels` is empty or missing, skip this pillar entirely (record "Skipped" in the report).
2. Resolve each MCP server's entry-point source file from `mcpServers.<server>.args[0]`. Prefer `src/*.ts` / `src/*.py` over bundled `dist/` artifacts.
3. Load the source (TypeScript, JavaScript, or Python — other languages emit an INFO note recommending manual review).
4. Evaluate four rules against the source:
   - **Rule 1 (CRITICAL)** — Sender-ID allowlist check (`message.from.id`, `message.author.id`, `message.sender`, or equivalent) before every forward call to `mcp.notification('notifications/claude/channel', ...)`. Missing => CRITICAL. Naïve (always-true guard, empty allowlist, truthy-only check) => MAJOR.
   - **Rule 2 (CRITICAL)** — If `capabilities.experimental['claude/channel/permission']` is declared, the permission handler MUST also gate on sender ID. Missing => CRITICAL.
   - **Rule 3 (MAJOR)** — Chat-ID-only gating (e.g. `msg.chat.id === ALLOWED_CHAT_ID` with no sender-ID compare) => MAJOR.
   - **Rule 4 (PASSED)** — Rules 1-3 all satisfied => PASSED with the file:line of the gating check.
5. Quote the exact `<file>:<line>` for every finding. Do not infer safety from variable names alone.
6. Write findings into the **Channel Source Security** section of the semantic report.

**Rubric contribution** (folds into the A-F grade per the existing table):

| Finding | Rubric effect |
|---------|---------------|
| No channels in plugin.json | N/A (pillar skipped — no grade impact) |
| Rule 4 PASSED | Pass |
| Rule 3 MAJOR only | Partial |
| Rule 1 MAJOR only (naïve gating) | Partial |
| Rule 1 CRITICAL (no gating) | Fail |
| Rule 2 CRITICAL (permission cap ungated) | Fail |

**Detailed rules, example code (vulnerable + safe), and the opus prompt template** are in `references/channel-source-security.md`. Load that reference when you begin the pillar.

## Resources

- Semantic Validation Criteria — see `skills/fix-validation/references/skill-semantic-validation.md` for full criteria, rubrics, report format
- Channel Source Security — see `references/channel-source-security.md` for the full rules, example vulnerable/safe patterns, and the opus prompt template
- `skill-validation-skill` — Script-based validation (cheap, fast)
- `plugin-validation-skill` — Full plugin validation

## Validation Checklist
Copy this checklist and track your progress:
- [ ] Confirm explicit user opt-in
- [ ] Run semantic validation on target skill
- [ ] Review A-F grade and per-criterion scores
- [ ] Address failing criteria
- [ ] If `plugin.json.channels` is non-empty, run the Channel MCP Server Source-Code Security pillar against every referenced MCP server source file
