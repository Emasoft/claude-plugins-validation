---
name: cpv-semantic-validation
description: Deep AI-driven semantic validation of skills and agents (uses opus)
allowed-tools: Read, Bash, Glob, Grep, Task, AskUserQuestion
argument-hint: "<skill_or_agent_path>"
agent: semantic-validator
user-invocable: true
---

# /cpv-semantic-validation Command

Deep semantic analysis of skill or agent quality. Evaluates aspects that automated scripts cannot check: description triggering, instruction clarity, example quality, workflow completeness.

**Cost**: Uses opus model — only invoke when you need quality analysis beyond script validation.

## Usage

```
/cpv-semantic-validation <path>
```

## Arguments

| Argument | Required | Description |
|----------|----------|-------------|
| `path` | Yes | Path to skill directory or agent .md file |

## What Gets Evaluated

1. **Description Triggering** — Does the description match real user intents?
2. **Instruction Clarity** — Are instructions unambiguous and complete?
3. **Example Quality** — Are examples realistic with success/failure paths?
4. **Workflow Completeness** — Clear start/end, branches, exit conditions?
5. **Technical Quality** — Exit codes, error handling, terminology consistency
6. **Progressive Disclosure** — Concise SKILL.md with details in references/?
7. **Output Patterns** — Timestamped reports, severity levels documented?

## Output

- Grade: A-F
- Per-criterion: Pass / Partial / Fail
- Report file: `docs_dev/semantic_validation_YYYYMMDD.md`

## Examples

```
/cpv-semantic-validation ./skills/my-skill/
/cpv-semantic-validation ./agents/my-agent.md
```

## Related Commands

- `/cpv-validate-skill` — Script-based validation (cheap, fast)
- `/cpv-validate-plugin` — Full plugin validation
- `/cpv-fix-validation` — Fix issues from a validation report
