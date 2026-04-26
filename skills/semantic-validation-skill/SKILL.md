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

Deep AI analysis. Opus 1M, ~10-50× tokens of script validation.

## Overview

7 quality pillars (description triggering, clarity, examples, workflow, technical, disclosure, output) + 3 conditional security pillars. Produces Semantic Grade (A-F).

**HARD SEPARATION — USER CHOICE:**

| Layer | Invoke | Tokens | Catches |
|-------|--------|--------|---------|
| **Programmatic** (default) | `/cpv-validate-plugin` | 0 | 95%+ via regex. Surfaces CANDIDATES, emits INFO — never escalates. |
| **Semantic** (this skill, opt-in) | `/cpv-semantic-validation` | thousands–millions | The 5% residue: ambiguous injection, shadow features, subtle MCP-description injection. |

Most projects publish-ready with programmatic alone. Never default-on.

## Prerequisites

- Python 3.12+ with `pyyaml`, `uv` package manager, target skill/agent path

## Instructions

1. Run baseline: `uv run python scripts/validate_skill_comprehensive.py "<path>" --strict --report "$MAIN_ROOT/reports/validate_skill/$(date +%Y%m%d_%H%M%S%z)-baseline.md"`
2. Read SKILL.md / agent .md
3. Evaluate 7 pillars + applicable conditional security pillars
4. Write report → `$MAIN_ROOT/reports/semantic-validator/<YYYYMMDD_HHMMSS±HHMM>-<slug>.md`
5. Return `[DONE] Grade: X. Report: <filepath>`

## Output

- **Grade**: A-F. **Criteria**: Pass/Partial/Fail per pillar. **Report**: `$MAIN_ROOT/reports/semantic-validator/<ts±tz>-<slug>.md`. Both `reports/` and `reports_dev/` gitignored.

## Error Handling

- If script validation fails CRITICAL → semantic skipped. Fix structure first.
- Invalid path → report error, exit.

## Examples

```
/cpv-semantic-validation ./skills/my-skill/
/cpv-semantic-validation ./agents/my-agent.md
```

## Token Optimization

- Opt-in only. Run script baseline first (catches 90%). Read only target files. Write full report to disk; return only grade + filepath. Prefer LLM Externalizer MCP (`chat`, `code_task`) for file reads via `input_files_paths`.

## Conditional Pillar: Channel MCP Server Source-Code Security

Runs only when `plugin.json.channels` is non-empty AND plugin ships MCP server source.

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

## Conditional Pillar: Security Threat Catalog (AI Content Layer)

19 categories from a 38-repo survey. Most have programmatic siblings; this is the LLM-judgment supplement.

Load [security-threat-catalog](references/security-threat-catalog.md):
- CAT-01–19 (19 threat categories below)
- Severity reference · Opus prompt template · A-F rubric integration · Report format · References

## Conditional Pillar: Truly-agent-class RCs (RC-49 partial + RC-77)

Per "code first if accuracy permits", 5 of 7 originally-agent-class RCs reclassified to programmatic. Only RC-49 partial + RC-77 remain truly agent-class.

Load [agent-rule-checks](references/agent-rule-checks.md):
- Re-evaluation table (which RCs need LLM, which moved to programmatic)
- RULE: RC-49 (partial agent-class) · RULE: RC-77 (truly agent-class)
- LLM evaluation prompts · Aggregating into A-F rubric · Token-economy compliance
- Independent operation modes · Implementation status · Source citations

## Resources

- Full criteria, rubrics, report format: `skills/fix-validation/references/skill-semantic-validation.md`
- Cheap counterparts: `skill-validation-skill`, `plugin-validation-skill`

## Validation Checklist

Copy this checklist and track your progress:

- [ ] Explicit user opt-in
- [ ] Baseline script validation first
- [ ] Evaluate 7 pillars + conditionals
- [ ] Review A-F grade
- [ ] Report to `$MAIN_ROOT/reports/semantic-validator/`
