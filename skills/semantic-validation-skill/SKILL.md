---
name: semantic-validation-skill
description: "Deep AI semantic validation for skills/agents. Use when checking triggering, clarity, examples. Used dynamically via the-skills-menu (TRDD-478d9687). 10x token cost."
tags:
  - validation
  - semantic
  - quality
  - skills
user-invocable: false
---

# Semantic Validation Skill

Deep AI analysis. Opus 1M, ~10-50× tokens of script validation.

## Overview

7 quality pillars + 3 conditional security pillars → Semantic Grade (A-F).

**HARD SEPARATION — USER CHOICE.** Programmatic (`/cpv-validate-plugin`, 0 tokens) catches 95%+ via regex and surfaces CANDIDATES via INFO — never escalates. Semantic (this skill, opt-in via `/cpv-semantic-validation`, thousands–millions tokens) handles the 5% residue: ambiguous injection, shadow features, subtle MCP-description injection. Most projects publish-ready with programmatic alone. Never default-on.

## Prerequisites

- Python 3.12+ with `pyyaml`, `uv` package manager, target skill/agent path

## Instructions

1. Run baseline via launcher (alias `skill`) — see [references/launcher-invocation.md](references/launcher-invocation.md). Direct invocation refused.
   > The one-liner · Why the launcher is mandatory · Direct invocation (development only)
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

Opt-in only. Baseline first. Write report to disk; return grade + filepath.

## Conditional Pillar: Channel MCP Server Source-Code Security

Runs only when `plugin.json.channels` is non-empty AND plugin ships MCP server source.

Load [channel-source-security](references/channel-source-security.md):
- Why This Pillar Exists
- Scope
- Source-Language Support
- Deterministic Prefilter Helper
- Rule 1 — Inbound Sender Gating (CRITICAL)
- Rule 2 — Permission-Relay Capability Gate (CRITICAL)
- Rule 3 — Room/Chat-ID-Only Gating (MAJOR)
- Rule 4 — PASSED
- Opus Prompt Template
- Integration With the A-F Rubric
- Report Format

## Conditional Pillar: AI Content Layer Threats (4 categorical files)

19 threat categories from a 38-repo survey. Most have programmatic siblings; these are the LLM-judgment supplement.

Load [prompt-injection-rules](references/prompt-injection-rules.md):
- CAT-01 Direct prompt injection (instruction override)
- CAT-02 Conditional / time-bomb injection
- CAT-03 Coercive authority / urgency bypass
- CAT-04 Identity hijack (DAN, jailbreak modes)
- CAT-05 System prompt impersonation
- CAT-11 Psychological manipulation
- CAT-13 Anthropic / system-admin impersonation
- CAT-14 IMPORTANT-tag / bracket amplification

Load [concealment-and-multilingual-rules](references/concealment-and-multilingual-rules.md):
- CAT-06 Concealment — markdown comments, HTML, collapsible sections
- CAT-07 Multilingual injection
- CAT-17 Hidden HTML comment with action verbs
- CAT-18 CSS-hidden / collapsible-section injection
- CAT-19 Whitespace-padding / visual-deception

Load [mcp-and-capability-rules](references/mcp-and-capability-rules.md):
- CAT-08 MCP tool-description prompt injection
- CAT-09 MCP tool-name shadowing
- CAT-10 Capability mismatch / shadow features

Load [exfil-and-autonomy-rules](references/exfil-and-autonomy-rules.md):
- CAT-12 Social engineering credential prompt
- CAT-15 Markdown image beacon (silent exfiltration)
- CAT-16 "Don't ask user" autonomy abuse (removes HITL)

## Conditional Pillar: Truly-agent-class RCs (RC-49 partial + RC-77)

Per "code first if accuracy permits", 5 of 7 RCs reclassified to programmatic. Only RC-49 partial + RC-77 remain.

Load [agent-rule-checks](references/agent-rule-checks.md):
- Re-evaluation table (which RCs need LLM, which moved to programmatic)
- RULE: RC-49 (partial agent-class) · RULE: RC-77 (truly agent-class)
- LLM evaluation prompts · Aggregating into A-F rubric · Token-economy compliance
- Independent operation modes · Implementation status · Source citations

## Resources

- Full criteria/rubrics/format: `skills/fix-validation/references/skill-semantic-validation.md`. Cheap counterparts: `skill-validation-skill`, `plugin-validation-skill`.

## Validation Checklist

Copy this checklist and track your progress:

- [ ] User opt-in confirmed
- [ ] Baseline script validation
- [ ] Evaluate pillars + conditionals
- [ ] Review A-F grade
- [ ] Report to `$MAIN_ROOT/reports/semantic-validator/`
