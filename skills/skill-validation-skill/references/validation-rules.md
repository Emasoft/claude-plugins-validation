# Complete Skill Validation Rules Reference

This document contains all 190+ validation rules extracted from multiple validation frameworks.

## Table of Contents

- [1. Structure Validation Rules (8 rules)](#1-structure-validation-rules)
- [2. Frontmatter Validation Rules (25 rules)](#2-frontmatter-validation-rules)
- [3. Name Field Validation Rules (12 rules)](#3-name-field-validation-rules)
- [4. Description Quality Rules (15 rules)](#4-description-quality-rules)
- [5. Token Budget Rules (8 rules)](#5-token-budget-rules)
- [6. Required Sections Rules (9 rules)](#6-required-sections-rules)
- [7. Path Format Rules (6 rules)](#7-path-format-rules)
- [8. Resource Reference Rules (8 rules)](#8-resource-reference-rules)
- [9. Allowed-Tools Rules (10 rules)](#9-allowed-tools-rules)

## Checklist

- [ ] Identify the rule category the finding belongs to (1-9)
- [ ] Open the matching section
- [ ] Apply the rule's remediation to SKILL.md
- [ ] Re-run `validate_skill.py --strict`
- [10. 8+1 Pillars Rules (18 rules)](#10-81-pillars-rules)
- [11. Progressive Disclosure Rules (12 rules)](#11-progressive-disclosure-rules)
- [12. Content Quality Rules (15 rules)](#12-content-quality-rules)
- [13. Agent-Specific Rules (22 rules)](#13-agent-specific-rules)

---

## 1. Structure Validation Rules

| Rule ID | Rule | Severity | Source |
|---------|------|----------|--------|
| S01 | Skill path must exist | CRITICAL | OpenSpec |
| S02 | Skill path must be a directory | CRITICAL | OpenSpec |
| S03 | SKILL.md must exist in skill directory | CRITICAL | OpenSpec |
| S04 | SKILL.md should be uppercase (not skill.md) | MINOR | Best Practice |
| S05 | Optional: scripts/ directory for automation | INFO | Claude Code |
| S06 | Optional: references/ directory for deep docs | INFO | Claude Code |
| S07 | Optional: examples/ directory for code samples | INFO | Claude Code |
| S08 | Scripts must be executable (chmod +x) | MAJOR | Claude Code |

---

## 2. Frontmatter Validation Rules

| Rule ID | Rule | Severity | Source |
|---------|------|----------|--------|
| F01 | Frontmatter must start with `---` | CRITICAL | OpenSpec |
| F02 | Frontmatter must end with `---` | CRITICAL | OpenSpec |
| F03 | Frontmatter YAML must be syntactically valid | CRITICAL | OpenSpec |
| F04 | Frontmatter must be a dictionary (not list/scalar) | CRITICAL | OpenSpec |
| F05 | `name` field is required | CRITICAL | OpenSpec |
| F06 | `description` field is required | CRITICAL | OpenSpec |
| F07 | `allowed-tools` field required (enterprise) | MAJOR | Enterprise |
| F08 | `version` field required (enterprise) | MAJOR | Enterprise |
| F09 | `author` field required (enterprise) | MAJOR | Enterprise |
| F10 | `license` field required (enterprise) | MAJOR | Enterprise |
| F11 | Field whitelist: only 6 fields allowed (OpenSpec strict) | MAJOR | OpenSpec |
| F12 | Extended fields allowed in Claude Code mode | INFO | Claude Code |
| F13 | Unknown fields generate warning | INFO | Claude Code |
| F14 | Deprecated fields (`when_to_use`) generate warning | MINOR | Nixtla |
| F15 | `context` field must be "fork" if present | CRITICAL | Claude Code |
| F16 | `agent` field requires `context: fork` | MAJOR | Claude Code |
| F17 | `user-invocable` must be boolean | CRITICAL | Claude Code |
| F18 | `disable-model-invocation` must be boolean | CRITICAL | Claude Code |
| F19 | `model` field must be string | MAJOR | Claude Code |
| F20 | `argument-hint` field must be string | MAJOR | Claude Code |
| F21 | `hooks` field must be object | MAJOR | Claude Code |
| F22 | `metadata` field must be dictionary | MAJOR | OpenSpec |
| F23 | `compatibility` field max 500 chars | MINOR | OpenSpec |
| F24 | Frontmatter size max 12K chars (warning) | MINOR | Nixtla |
| F25 | Frontmatter size max 15K chars (error) | MAJOR | Nixtla |

---

## 3. Name Field Validation Rules

| Rule ID | Rule | Severity | Source |
|---------|------|----------|--------|
| N01 | Name must be a string | CRITICAL | OpenSpec |
| N02 | Name must be non-empty | CRITICAL | OpenSpec |
| N03 | Name max 64 characters | MAJOR | OpenSpec |
| N04 | Name must be lowercase | MAJOR | OpenSpec |
| N05 | Name must be kebab-case (letters, numbers, hyphens) | MAJOR | OpenSpec |
| N06 | Name cannot start with hyphen | MAJOR | OpenSpec |
| N07 | Name cannot end with hyphen | MAJOR | OpenSpec |
| N08 | Name cannot contain consecutive hyphens (`--`) | MAJOR | OpenSpec |
| N09 | Name must match directory name (OpenSpec strict) | MAJOR | OpenSpec |
| N10 | Name mismatch with directory generates warning (Claude Code) | INFO | Claude Code |
| N11 | Reserved words forbidden: "anthropic", "claude" | MAJOR | Enterprise |
| N12 | Unicode NFKC normalization applied | INFO | OpenSpec |

---

## 4. Description Quality Rules

| Rule ID | Rule | Severity | Source |
|---------|------|----------|--------|
| D01 | Description must be a string | MAJOR | OpenSpec |
| D02 | Description must be non-empty | MAJOR | OpenSpec |
| D03 | Description min 20 characters | MINOR | Nixtla |
| D04 | Description max 1024 characters | MAJOR | OpenSpec |
| D05 | Description max 200 chars (recommended) | MINOR | Meta-Skill |
| D06 | Must include "Use when..." phrase (strict mode) | MAJOR | Nixtla |
| D07 | Should include "Trigger with..." phrase (strict mode) | MINOR | Nixtla |
| D08 | No first person ("I can", "I will") | MAJOR | Nixtla |
| D09 | No second person ("You can", "You should") | MAJOR | Nixtla |
| D10 | Third-person voice only | MAJOR | Nixtla |
| D11 | Must be action-oriented (describes capability) | MINOR | Meta-Skill |
| D12 | Must include trigger keywords | MINOR | Meta-Skill |
| D13 | Description should describe WHAT and WHEN | INFO | OpenSpec |
| D14 | Description too vague warning | MINOR | Meta-Skill |
| D15 | Description lists features instead of actions | MINOR | Meta-Skill |

---

## 5. Token Budget Rules

| Rule ID | Rule | Severity | Source |
|---------|------|----------|--------|
| T01 | SKILL.md max 500 lines (warning) | MINOR | Nixtla |
| T02 | SKILL.md max 800 lines (error) | MAJOR | Nixtla |
| T03 | Word count max 3500 (warning) | MINOR | Nixtla |
| T04 | Word count max 5000 (error) | MAJOR | Nixtla |
| T05 | Frontmatter max 12K chars (warning) | MINOR | Nixtla |
| T06 | Frontmatter max 15K chars (error) | MAJOR | Nixtla |
| T07 | Progressive disclosure required for large skills | MAJOR | Meta-Skill |
| T08 | Reference files should be under 100 lines each | MINOR | Meta-Skill |

---

## 6. Required Sections Rules

Applies only in Nixtla strict mode (`--strict`).

| Rule ID | Rule | Severity | Source |
|---------|------|----------|--------|
| RS01 | Title line required (`# ...`) | MAJOR | Nixtla |
| RS02 | `## Overview` section required | MAJOR | Nixtla |
| RS03 | `## Prerequisites` section required | MAJOR | Nixtla |
| RS04 | `## Instructions` section required | MAJOR | Nixtla |
| RS05 | `## Output` section required | MAJOR | Nixtla |
| RS06 | `## Error Handling` section required | MAJOR | Nixtla |
| RS07 | `## Examples` section required | MAJOR | Nixtla |
| RS08 | `## Resources` section required | MAJOR | Nixtla |
| RS09 | Instructions must have numbered step-by-step list | MAJOR | Nixtla |

---

## 7. Path Format Rules

| Rule ID | Rule | Severity | Source |
|---------|------|----------|--------|
| P01 | No absolute paths (`/home/...`) | MAJOR | Nixtla |
| P02 | No OS-specific paths (`/Users/...`) | MAJOR | Nixtla |
| P03 | No Windows paths (`C:\Users\...`) | MAJOR | Nixtla |
| P04 | Use `{baseDir}/...` for skill-relative paths | MAJOR | Nixtla |
| P05 | Use forward slashes only (no backslashes) | MAJOR | Nixtla |
| P06 | Relative paths from skill root | INFO | Meta-Skill |

---

## 8. Resource Reference Rules

| Rule ID | Rule | Severity | Source |
|---------|------|----------|--------|
| R01 | `{baseDir}/scripts/...` references must exist | MAJOR | Nixtla |
| R02 | `{baseDir}/references/...` references must exist | MAJOR | Nixtla |
| R03 | `{baseDir}/assets/...` references must exist | MAJOR | Nixtla |
| R04 | Markdown links to local files must exist | MAJOR | Claude Code |
| R05 | Scripts must be executable | MAJOR | Claude Code |
| R06 | No path traversal (`../..`) beyond skill directory | CRITICAL | Security |
| R07 | Reference files should have TOC at top | MINOR | Meta-Skill |
| R08 | Reference file TOC should use case-oriented headings | INFO | Meta-Skill |

---

## 9. Allowed-Tools Rules

| Rule ID | Rule | Severity | Source |
|---------|------|----------|--------|
| AT01 | `allowed-tools` must be string or list | MAJOR | Claude Code |
| AT02 | Must be CSV string (not YAML array) in strict mode | MAJOR | Nixtla |
| AT03 | Valid tools: Read, Write, Edit, Bash, Glob, Grep, etc. | INFO | Claude Code |
| AT04 | Unscoped `Bash` forbidden in strict mode | MAJOR | Nixtla |
| AT05 | Use scoped Bash: `Bash(git:*)`, `Bash(npm:*)` | INFO | Nixtla |
| AT06 | Over-permissioning warning (>6 tools) | MINOR | Nixtla |
| AT07 | MCP tools format: `mcp__server__tool` | INFO | Claude Code |
| AT08 | Unknown tools generate warning | INFO | Claude Code |
| AT09 | Empty allowed-tools generates warning | MINOR | Claude Code |
| AT10 | Wildcard syntax must be valid | INFO | Claude Code |

---

## 10. 8+1 Pillars Rules

For skills with names starting with `lang-` or `convert-`.

| Rule ID | Rule | Severity | Source |
|---------|------|----------|--------|
| PL01 | Module pillar coverage required | MINOR | Meta-Skill |
| PL02 | Error pillar coverage required | MINOR | Meta-Skill |
| PL03 | Concurrency pillar coverage required | MINOR | Meta-Skill |
| PL04 | Metaprogramming pillar coverage required | MINOR | Meta-Skill |
| PL05 | Zero/Default pillar coverage required | MINOR | Meta-Skill |
| PL06 | Serialization pillar coverage required | MINOR | Meta-Skill |
| PL07 | Build pillar coverage required | MINOR | Meta-Skill |
| PL08 | Testing pillar coverage required | MINOR | Meta-Skill |
| PL09 | Dev Workflow/REPL pillar for REPL-centric languages | MINOR | Meta-Skill |
| PL10 | Full coverage = dedicated section + 3+ keyword matches | INFO | Meta-Skill |
| PL11 | Partial coverage = 2-4 keyword matches | INFO | Meta-Skill |
| PL12 | Missing coverage = 0-1 keyword matches | MINOR | Meta-Skill |
| PL13 | Total score < 50% = incomplete | MAJOR | Meta-Skill |
| PL14 | Total score 50-75% = needs improvement | MINOR | Meta-Skill |
| PL15 | Total score > 75% = good coverage | INFO | Meta-Skill |
| PL16 | REPL-centric languages include 9th pillar | INFO | Meta-Skill |
| PL17 | Gap mitigation required for unavailable pillars | MINOR | Meta-Skill |
| PL18 | Cross-reference to language docs for gaps | INFO | Meta-Skill |

---

## 11. Progressive Disclosure Rules

| Rule ID | Rule | Severity | Source |
|---------|------|----------|--------|
| PD01 | Main SKILL.md should be concise (<500 lines) | MINOR | Meta-Skill |
| PD02 | Detailed content belongs in references/ | INFO | Meta-Skill |
| PD03 | Quick Navigation section recommended | INFO | Meta-Skill |
| PD04 | Reference file TOC embedded in SKILL.md | INFO | Meta-Skill |
| PD05 | TOC entries should describe USE CASES | INFO | Meta-Skill |
| PD06 | File references should be one level deep | MINOR | OpenSpec |
| PD07 | Avoid deeply nested reference chains | MINOR | OpenSpec |
| PD08 | Each reference file should be focused | INFO | OpenSpec |
| PD09 | Domain organization for large skills | INFO | Meta-Skill |
| PD10 | Conditional details with "See also" blocks | INFO | Meta-Skill |
| PD11 | Examples go in examples/ if >20 lines | INFO | Meta-Skill |
| PD12 | Templates go in FORMS.md or templates/ | INFO | Meta-Skill |

---

## 12. Content Quality Rules

| Rule ID | Rule | Severity | Source |
|---------|------|----------|--------|
| CQ01 | Templates provided for structured output | MINOR | Meta-Skill |
| CQ02 | Examples included for complex tasks | MINOR | Meta-Skill |
| CQ03 | Consistent terminology throughout | MINOR | Meta-Skill |
| CQ04 | No time-sensitive information | MINOR | Meta-Skill |
| CQ05 | No specific version numbers that expire | MINOR | Meta-Skill |
| CQ06 | No "As of 2024..." statements | MINOR | Meta-Skill |
| CQ07 | MCP tool format: `mcp__server__tool` | MINOR | Meta-Skill |
| CQ08 | Code blocks have syntax highlighting | INFO | Meta-Skill |
| CQ09 | Runnable examples preferred | MINOR | Meta-Skill |
| CQ10 | Error messages should be helpful | INFO | Meta-Skill |
| CQ11 | Scripts should be self-contained | MINOR | OpenSpec |
| CQ12 | Dependencies documented clearly | MINOR | OpenSpec |
| CQ13 | Edge cases handled gracefully | INFO | OpenSpec |
| CQ14 | Step-by-step instructions numbered | MAJOR | Nixtla |
| CQ15 | Each step has clear action verb | INFO | Nixtla |

---

## 13. Agent-Specific Rules

For agent `.md` files in agents/ directory.

| Rule ID | Rule | Severity | Source |
|---------|------|----------|--------|
| AG01 | File size optimal: 70-150 lines | INFO | Agent-Validator |
| AG02 | File size warning: 150-200 lines | MINOR | Agent-Validator |
| AG03 | File size error: >300 lines | MAJOR | Agent-Validator |
| AG04 | Role clarity required | MAJOR | Agent-Validator |
| AG05 | Expertise domains defined (3-5 recommended) | MINOR | Agent-Validator |
| AG06 | Anti-scope defined (what NOT to do) | MINOR | Agent-Validator |
| AG07 | Workflow structure with numbered steps | MAJOR | Agent-Validator |
| AG08 | Decision points documented | MINOR | Agent-Validator |
| AG09 | "Must Follow" checklist required | MINOR | Agent-Validator |
| AG10 | "Must Avoid" section required | MINOR | Agent-Validator |
| AG11 | Verification steps documented | MINOR | Agent-Validator |
| AG12 | Quality gates defined | MINOR | Agent-Validator |
| AG13 | Success criteria measurable | MINOR | Agent-Validator |
| AG14 | Token efficiency awareness | INFO | Agent-Validator |
| AG15 | Tool usage strategy documented | INFO | Agent-Validator |
| AG16 | Skill leverage documented | INFO | Agent-Validator |
| AG17 | Sub-agent delegation guidance | INFO | Agent-Validator |
| AG18 | Error recovery strategies | MINOR | Agent-Validator |
| AG19 | Edge case awareness | MINOR | Agent-Validator |
| AG20 | Tutor pattern: learning progression required | MINOR | Agent-Validator |
| AG21 | Operator pattern: runbooks required | MINOR | Agent-Validator |
| AG22 | Architect pattern: design workflow required | MINOR | Agent-Validator |

---

## Summary

| Category | Rule Count |
|----------|------------|
| Structure | 8 |
| Frontmatter | 25 |
| Name Field | 12 |
| Description | 15 |
| Token Budget | 8 |
| Required Sections | 9 |
| Path Format | 6 |
| Resource References | 8 |
| Allowed-Tools | 10 |
| 8+1 Pillars | 18 |
| Progressive Disclosure | 12 |
| Content Quality | 15 |
| Agent-Specific | 22 |
| **Total** | **168** |

---

## Rule Sources

| Source | Description |
|--------|-------------|
| **OpenSpec** | AgentSkills Open Specification (skills-ref library) |
| **Claude Code** | Claude Code plugin documentation |
| **Nixtla** | Nixtla Quality Standards (strict mode) |
| **Meta-Skill** | Meta-skill validation framework |
| **Enterprise** | Enterprise plugin standards |
| **Agent-Validator** | Agent validation framework (9 categories) |
| **Security** | Security best practices |
| **Best Practice** | Industry best practices |
