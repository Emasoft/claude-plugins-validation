# Enterprise Compliance — Validation Issues and Fixes

## Table of Contents

- [1. Plugin/Path Level Issues](#1-pluginpath-level-issues)
- [2. Skill File Issues](#2-skill-file-issues)
- [3. Required Metadata: name and description](#3-required-metadata-name-and-description)
- [4. Author Field Issues](#4-author-field-issues)
- [5. License Field Issues](#5-license-field-issues)
- [6. Context Field Issues](#6-context-field-issues)
- [7. Agent Field Issues](#7-agent-field-issues)
- [8. User-Invocable Field Issues](#8-user-invocable-field-issues)
- [9. Tags Field Issues](#9-tags-field-issues)
- [10. Mode Field Issues](#10-mode-field-issues)
- [11. Agent Compliance Issues](#11-agent-compliance-issues)
- [12. Summary/Informational Messages](#12-summaryinformational-messages)

---

Comprehensive remediation guide for all issues detected by `validate_enterprise.py`.

## Checklist

- [ ] Identify the enterprise compliance finding (path, severity, rule)
- [ ] Match to a numbered section below
- [ ] Open the target file and read the relevant section
- [ ] Apply the compliance fix
- [ ] Re-validate with `--strict`

## Overview

The enterprise compliance validator checks skills (in `skills/`) and agents (in `agents/`) for nine compliance rules:

1. Required skill metadata: `name`, `description`, `author`, `license`
2. `context: fork` field valid value
3. `agent` field enterprise agent types
4. `user-invocable` must be boolean
5. `author` field REQUIRED for enterprise compliance
6. `license` field REQUIRED (SPDX identifier)
7. `tags` array RECOMMENDED
8. `mode` field valid values: `read`, `write`, `read-write`
9. All agents must have `name` and `description`

**Strict mode** (`--strict`): All MAJOR issues become CRITICAL.

---

## 1. Plugin/Path Level Issues

### [CRITICAL] Plugin directory not found: {plugin_path}
**Source**: `validate_enterprise.py` — `validate_enterprise_compliance()`
**What it means**: The path passed to the validator does not exist on the filesystem.
**How to fix**:
1. Verify the path is correct: `ls /path/to/plugin/`
2. Ensure the plugin directory was created and not deleted or moved.
3. Pass the correct absolute path to the validator.

---

### [CRITICAL] Path is not a directory: {plugin_path}
**Source**: `validate_enterprise.py` — `validate_enterprise_compliance()`
**What it means**: The path exists but points to a file, not a directory.
**How to fix**:
1. Check the path with `ls -la /path/to/plugin`
2. Pass the plugin root directory, not a file path.

---

## 2. Skill File Issues

### [CRITICAL/MAJOR] SKILL.md not found
**Source**: `validate_enterprise.py` — `validate_skill_compliance()`
**What it means**: A subdirectory inside `skills/` exists but has no `SKILL.md` file. Every skill directory must contain a `SKILL.md` file.
**How to fix**:
1. Create `skills/<skill-name>/SKILL.md`.
2. Add YAML frontmatter at the top with at minimum `name`, `description`, `author`, and `license`.

Example minimal `SKILL.md`:
```markdown
---
name: my-skill
description: Does something useful
author: My Name
license: MIT
---
```

---

### [CRITICAL] Failed to read SKILL.md: {error}
**Source**: `validate_enterprise.py` — `validate_skill_compliance()`
**What it means**: The `SKILL.md` file exists but cannot be read (permissions issue, encoding problem, or I/O error).
**How to fix**:
1. Check file permissions: `ls -la skills/<skill-name>/SKILL.md`
2. Fix read permissions: `chmod 644 skills/<skill-name>/SKILL.md`
3. Ensure the file is valid UTF-8 text.

---

### [CRITICAL/MAJOR] No YAML frontmatter found (required for enterprise compliance)
**Source**: `validate_enterprise.py` — `validate_skill_compliance()`
**What it means**: `SKILL.md` exists but does not start with `---` YAML frontmatter block.
**How to fix**:
1. Add YAML frontmatter as the very first lines of `SKILL.md`:
```markdown
---
name: my-skill
description: Brief description
author: Your Name
license: MIT
---

# My Skill

Rest of the skill documentation...
```
2. The frontmatter block must start at line 1 with `---`.

---

## 3. Required Metadata: name and description

### [CRITICAL/MAJOR] Missing required field: 'name'
**Source**: `validate_enterprise.py` — `validate_required_metadata()`
**What it means**: The YAML frontmatter does not contain a `name` field, which is required for enterprise compliance.
**How to fix**:
1. Add `name` to the frontmatter:
```yaml
---
name: my-skill-name
---
```
2. The name should be a kebab-case string identifying the skill.

---

### [CRITICAL/MAJOR] 'name' must be a string, got {type}
**Source**: `validate_enterprise.py` — `validate_required_metadata()`
**What it means**: The `name` field exists but its value is not a string (e.g., it is a number or boolean).
**How to fix**:
1. Quote the name in YAML to ensure it is a string:
```yaml
name: "my-skill"
```

---

### [CRITICAL/MAJOR] 'name' cannot be empty
**Source**: `validate_enterprise.py` — `validate_required_metadata()`
**What it means**: The `name` field is present but set to an empty string or only whitespace.
**How to fix**:
1. Provide a meaningful non-empty name:
```yaml
name: my-skill
```

---

### [CRITICAL/MAJOR] Missing required field: 'description'
**Source**: `validate_enterprise.py` — `validate_required_metadata()`
**What it means**: The YAML frontmatter does not contain a `description` field.
**How to fix**:
1. Add `description` to the frontmatter:
```yaml
description: "A brief description of what this skill does"
```

---

### [CRITICAL/MAJOR] 'description' must be a string, got {type}
**Source**: `validate_enterprise.py` — `validate_required_metadata()`
**What it means**: The `description` field exists but its value is not a string.
**How to fix**:
1. Ensure the description is a quoted string in YAML:
```yaml
description: "My skill description"
```

---

### [CRITICAL/MAJOR] 'description' cannot be empty
**Source**: `validate_enterprise.py` — `validate_required_metadata()`
**What it means**: The `description` field is present but empty or whitespace-only.
**How to fix**:
1. Provide a meaningful description:
```yaml
description: "Automates deployment to production via CI/CD pipeline"
```

---

## 4. Author Field Issues

### [CRITICAL/MAJOR] Missing required field: 'author' (enterprise compliance requirement)
**Source**: `validate_enterprise.py` — `validate_author_field()`
**What it means**: The `author` field is absent. For enterprise compliance, `author` is required to establish accountability and ownership.
**How to fix**:
1. Add `author` as a string:
```yaml
author: "Jane Doe"
```
2. Or as an object with `name` and optional `email`:
```yaml
author:
  name: "Jane Doe"
  email: "jane@example.com"
```

---

### [CRITICAL/MAJOR] 'author' cannot be empty
**Source**: `validate_enterprise.py` — `validate_author_field()`
**What it means**: The `author` field is a string but empty or whitespace-only.
**How to fix**:
1. Provide a non-empty author name:
```yaml
author: "Jane Doe"
```

---

### [CRITICAL/MAJOR] 'author' object must have 'name' field
**Source**: `validate_enterprise.py` — `validate_author_field()`
**What it means**: The `author` field is a YAML object but is missing the required `name` sub-field.
**How to fix**:
1. Add the `name` field to the author object:
```yaml
author:
  name: "Jane Doe"
  email: "jane@example.com"
```

---

### [CRITICAL/MAJOR] 'author' must be a string or object, got {type}
**Source**: `validate_enterprise.py` — `validate_author_field()`
**What it means**: The `author` field value is neither a string nor a YAML mapping/object (e.g., it is a list or number).
**How to fix**:
1. Use either a plain string or a `name`/`email` object:
```yaml
# String form:
author: "Jane Doe"

# Object form:
author:
  name: "Jane Doe"
  email: "jane@example.com"
```

---

## 5. License Field Issues

### [CRITICAL/MAJOR] Missing required field: 'license' (enterprise compliance requirement)
**Source**: `validate_enterprise.py` — `validate_license_field()`
**What it means**: The `license` field is absent. Enterprise compliance requires every skill to declare its license using an SPDX identifier.
**How to fix**:
1. Add `license` with an SPDX identifier:
```yaml
license: MIT
```
Common SPDX identifiers: `MIT`, `Apache-2.0`, `GPL-3.0`, `BSD-2-Clause`, `ISC`, `Proprietary`, `UNLICENSED`.
Full list: https://spdx.org/licenses/

---

### [CRITICAL/MAJOR] 'license' must be a string (SPDX identifier), got {type}
**Source**: `validate_enterprise.py` — `validate_license_field()`
**What it means**: The `license` field value is not a string.
**How to fix**:
1. Set `license` to a plain string SPDX identifier:
```yaml
license: "Apache-2.0"
```

---

### [CRITICAL/MAJOR] 'license' cannot be empty
**Source**: `validate_enterprise.py` — `validate_license_field()`
**What it means**: The `license` field is set to an empty string.
**How to fix**:
1. Provide a valid SPDX identifier:
```yaml
license: MIT
```

---

### [MINOR] 'license' value '{value}' is not a common SPDX identifier. See https://spdx.org/licenses/ for valid identifiers.
**Source**: `validate_enterprise.py` — `validate_license_field()`
**What it means**: The license string is not in the known SPDX license list. It may still be valid, but is unrecognized. This is a warning, not a blocking error.
**How to fix**:
1. Check https://spdx.org/licenses/ for the exact SPDX identifier for your license.
2. Replace with the correct identifier:
```yaml
license: MIT        # not "mit" or "MIT License"
license: Apache-2.0  # not "Apache 2" or "Apache-2"
```

---

## 6. Context Field Issues

### [INFO] No 'context' field (skill runs in main context)
**Source**: `validate_enterprise.py` — `validate_context_field()`
**What it means**: The `context` field is absent. This is informational — the skill will run in the main Claude context. No action is required unless you want the skill to run in a forked sub-agent.
**How to fix**: No fix required. To run in a forked context, add:
```yaml
context: fork
```

---

### [CRITICAL/MAJOR] 'context' must be a string, got {type}
**Source**: `validate_enterprise.py` — `validate_context_field()`
**What it means**: The `context` field exists but is not a string.
**How to fix**:
1. Set `context` to the string `"fork"` or another valid value:
```yaml
context: fork
```

---

### [CRITICAL/MAJOR] Invalid 'context' value: '{value}'. Valid values: {VALID_CONTEXT_VALUES}
**Source**: `validate_enterprise.py` — `validate_context_field()`
**What it means**: The `context` field contains an unrecognized value.
**How to fix**:
1. Use only the valid context value `fork`:
```yaml
context: fork
```
(Omit the field entirely if you want main-context execution.)

---

## 7. Agent Field Issues

### [INFO] No 'agent' field with context: fork (defaults to general-purpose)
**Source**: `validate_enterprise.py` — `validate_agent_field()`
**What it means**: The skill has `context: fork` but no `agent` field. The forked sub-agent will default to a general-purpose agent. This is informational only.
**How to fix**: No fix required. To specify an enterprise agent type:
```yaml
agent: test-engineer
```
Valid enterprise types: `api-coordinator`, `test-engineer`, `deploy-agent`, `debug-specialist`, `code-reviewer`.

---

### [CRITICAL/MAJOR] 'agent' must be a string, got {type}
**Source**: `validate_enterprise.py` — `validate_agent_field()`
**What it means**: The `agent` field exists but its value is not a string.
**How to fix**:
1. Set `agent` to a valid string agent type:
```yaml
agent: "test-engineer"
```

---

### [MAJOR] 'agent' field has no effect without 'context: fork'
**Source**: `validate_enterprise.py` — `validate_agent_field()`
**What it means**: An `agent` field is present but `context` is not set to `fork`. The `agent` field only has effect when the skill runs in a forked context.
**How to fix**:
1. Either remove the `agent` field if you don't need a forked context, or add `context: fork`:
```yaml
context: fork
agent: test-engineer
```

---

### [MINOR] 'agent' value '{value}' is not a known type. Enterprise types: {...}. Built-in types: {...}
**Source**: `validate_enterprise.py` — `validate_agent_field()` (strict mode only)
**What it means**: The `agent` value is not a recognized enterprise or built-in agent type. Only emitted in `--strict` mode; in normal mode this is logged as INFO.
**How to fix**:
1. Use a recognized enterprise agent type: `api-coordinator`, `test-engineer`, `deploy-agent`, `debug-specialist`, `code-reviewer`.
2. Or use a built-in Claude agent type.
3. If referencing a custom agent defined in `.claude/agents/`, this is expected — the warning can be disregarded.

---

### [INFO] 'agent' value '{value}' may be a custom agent from .claude/agents/
**Source**: `validate_enterprise.py` — `validate_agent_field()` (non-strict mode)
**What it means**: The `agent` value is not a recognized built-in or enterprise type. In non-strict mode, this is informational — it may be a custom agent defined locally.
**How to fix**: No fix required if referencing a valid custom agent. If it is a typo, correct the agent name.

---

## 8. User-Invocable Field Issues

### [CRITICAL/MAJOR] 'user-invocable' must be a boolean (true/false), got {type}
**Source**: `validate_enterprise.py` — `validate_user_invocable_field()`
**What it means**: The `user-invocable` field exists but its value is not a YAML boolean.
**How to fix**:
1. Use YAML boolean literals (no quotes):
```yaml
user-invocable: true
# or
user-invocable: false
```
Do NOT use strings like `"true"` or `"yes"` — these are strings in YAML, not booleans.

---

## 9. Tags Field Issues

### [MINOR] Missing recommended field: 'tags' (helps with skill discovery)
**Source**: `validate_enterprise.py` — `validate_tags_field()`
**What it means**: The `tags` array is absent. Tags are recommended (not required) to improve skill discoverability in enterprise environments.
**How to fix**:
1. Add a `tags` array with relevant keywords:
```yaml
tags:
  - testing
  - automation
  - ci-cd
```

---

### [MINOR] 'tags' should be an array, got {type}
**Source**: `validate_enterprise.py` — `validate_tags_field()`
**What it means**: The `tags` field exists but is not a YAML list/array.
**How to fix**:
1. Change `tags` to a YAML array:
```yaml
tags:
  - testing
  - automation
```

---

### [MINOR] 'tags' array is empty (add tags for better skill discovery)
**Source**: `validate_enterprise.py` — `validate_tags_field()`
**What it means**: The `tags` field is an empty array `[]`. It should contain at least one tag.
**How to fix**:
1. Add relevant tags:
```yaml
tags:
  - deployment
  - enterprise
```

---

### [MINOR] 'tags' array contains non-string values: {invalid_tags}
**Source**: `validate_enterprise.py` — `validate_tags_field()`
**What it means**: One or more elements in the `tags` array are not strings (e.g., numbers or booleans).
**How to fix**:
1. Ensure every tag is a quoted or unquoted string:
```yaml
tags:
  - testing      # OK - string
  - "ci-cd"      # OK - quoted string
  # NOT: - 123   # BAD - number
```

---

## 10. Mode Field Issues

### [CRITICAL/MAJOR] 'mode' must be a string, got {type}
**Source**: `validate_enterprise.py` — `validate_mode_field()`
**What it means**: The `mode` field exists but is not a string value.
**How to fix**:
1. Set `mode` to one of the valid string values:
```yaml
mode: read
```

---

### [CRITICAL/MAJOR] Invalid 'mode' value: '{value}'. Valid values: {'read', 'write', 'read-write'}
**Source**: `validate_enterprise.py` — `validate_mode_field()`
**What it means**: The `mode` field contains an unrecognized value.
**How to fix**:
1. Use one of the three valid mode values:
```yaml
mode: read        # Skill only reads files/resources
mode: write       # Skill can write/modify files
mode: read-write  # Skill can both read and write
```

---

## 11. Agent Compliance Issues

### [CRITICAL] Failed to read agent file: {error}
**Source**: `validate_enterprise.py` — `validate_agent_compliance()`
**What it means**: An agent `.md` file in the `agents/` directory cannot be read (permissions issue or encoding error).
**How to fix**:
1. Check file permissions: `ls -la agents/`
2. Fix read permissions: `chmod 644 agents/<agent-file>.md`
3. Ensure the file is valid UTF-8.

---

### [CRITICAL/MAJOR] No YAML frontmatter found (required for agent compliance)
**Source**: `validate_enterprise.py` — `validate_agent_compliance()`
**What it means**: An agent `.md` file in `agents/` exists but has no YAML frontmatter. Agent files require `name` and `description` in frontmatter.
**How to fix**:
1. Add YAML frontmatter to the agent file:
```markdown
---
name: my-agent
description: "A brief description of this agent's role"
---

# Agent Instructions

...
```

---

### [CRITICAL/MAJOR] Missing required field: 'name' (agent file)
**Source**: `validate_enterprise.py` — `validate_agent_compliance()`
**What it means**: An agent `.md` file has frontmatter but is missing the `name` field.
**How to fix**:
1. Add `name` to the agent frontmatter:
```yaml
---
name: my-agent
description: "Does something"
---
```

---

### [CRITICAL/MAJOR] Missing required field: 'description' (agent file)
**Source**: `validate_enterprise.py` — `validate_agent_compliance()`
**What it means**: An agent `.md` file has frontmatter but is missing the `description` field.
**How to fix**:
1. Add `description` to the agent frontmatter:
```yaml
---
name: my-agent
description: "Performs automated testing for the CI pipeline"
---
```

---

## 12. Summary/Informational Messages

### [MINOR] No skills or agents found to validate
**Source**: `validate_enterprise.py` — `validate_enterprise_compliance()`
**What it means**: Neither a `skills/` directory with skill subdirectories nor an `agents/` directory with `.md` files was found. The validator had nothing to check.
**How to fix**:
1. Ensure skills are placed in `skills/<skill-name>/SKILL.md`.
2. Ensure agents are placed in `agents/<agent-name>.md`.
3. If this is intentional (e.g., the plugin has no skills/agents), this message can be ignored.

---

### [INFO] No skills/ directory found
**Source**: `validate_enterprise.py` — `validate_enterprise_compliance()`
**What it means**: There is no `skills/` subdirectory in the plugin root. Informational only.
**How to fix**: No action needed if the plugin has no skills. To add skills, create `skills/<skill-name>/SKILL.md`.

---

### [INFO] No agents/ directory found
**Source**: `validate_enterprise.py` — `validate_enterprise_compliance()`
**What it means**: There is no `agents/` subdirectory in the plugin root. Informational only.
**How to fix**: No action needed if the plugin has no agents. To add agents, create `agents/<agent-name>.md`.

---

### [INFO] No skills found in skills/ directory
**Source**: `validate_enterprise.py` — `validate_enterprise_compliance()`
**What it means**: The `skills/` directory exists but contains no skill subdirectories. Informational only.
**How to fix**: Add skill subdirectories inside `skills/`, each with a `SKILL.md` file.

---

### [INFO] Found {N} skill(s) in skills/ directory
**Source**: `validate_enterprise.py` — `validate_enterprise_compliance()`
**What it means**: Informational. N skills were discovered and will be validated.
**How to fix**: No action required.

---

### [INFO] No agents found in agents/ directory
**Source**: `validate_enterprise.py` — `validate_enterprise_compliance()`
**What it means**: The `agents/` directory exists but contains no `.md` agent files. Informational only.
**How to fix**: Add agent `.md` files inside the `agents/` directory.

---

### [INFO] Found {N} agent(s) in agents/ directory
**Source**: `validate_enterprise.py` — `validate_enterprise_compliance()`
**What it means**: Informational. N agent files were discovered and will be validated.
**How to fix**: No action required.
