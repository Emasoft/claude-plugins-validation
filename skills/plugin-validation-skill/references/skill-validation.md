# Skill Validation Reference

Complete reference for Claude Code skill structure and SKILL.md frontmatter validation.

## Table of Contents

- [1. Skill Directory Structure](#1-skill-directory-structure)
- [2. SKILL.md File Format](#2-skillmd-file-format)
- [3. Frontmatter Fields](#3-frontmatter-fields)
- [4. Claude Code Specific Fields](#4-claude-code-specific-fields)
- [5. Content Best Practices](#5-content-best-practices)
- [6. References Directory](#6-references-directory)
- [7. Common Skill Errors](#7-common-skill-errors)
- [8. Validation Checklist](#8-validation-checklist)

---

## 1. Skill Directory Structure

### Standard Skill Layout

```
my-skill/
├── SKILL.md              # REQUIRED: Main skill file
├── README.md             # Optional: Documentation
├── references/           # Optional: Reference documents
│   ├── topic-one.md
│   ├── topic-two.md
│   └── examples/
│       └── example.md
└── scripts/              # Optional: Utility scripts
    └── helper.py
```

### Placement in Plugin

Skills are directories inside the plugin's skills/ folder:

```
my-plugin/
├── .claude-plugin/
│   └── plugin.json
└── skills/
    ├── skill-one/
    │   ├── SKILL.md
    │   └── references/
    └── skill-two/
        └── SKILL.md
```

---

## 2. SKILL.md File Format

### Basic Structure

```markdown
---
name: my-skill-name
description: Clear description of what this skill teaches and when to use it
---

# My Skill Title

Main content of the skill...

## Section One

Content...

## Section Two

Content...
```

### Frontmatter Requirements

The frontmatter is YAML between `---` delimiters at the top of the file.

**Required:**
- `name`: Skill identifier (kebab-case recommended)
- `description`: What the skill does and when to use it

**Optional:**
- `tags`: Array of categorization tags
- `user-invocable`: Whether users can invoke directly
- `context`: Execution context (Claude Code specific)
- `agent`: Associated agent type (Claude Code specific)

---

## 3. Frontmatter Fields

### Required Fields

| Field | Type | Description | Example |
|-------|------|-------------|---------|
| name | string | Unique skill identifier | `"git-workflow"` |
| description | string | What the skill teaches | `"Git branching and commit best practices"` |

### Optional Fields

| Field | Type | Description | Example |
|-------|------|-------------|---------|
| tags | array | Categorization tags | `["git", "workflow", "version-control"]` |
| user-invocable | boolean | Can users invoke via /skill-name | `true` |
| aliases | array | Alternative names | `["git-flow", "branching"]` |
| version | string | Skill version | `"1.0.0"` |
| author | string | Skill author | `"Developer Name"` |

### Example with All Fields

```yaml
---
name: comprehensive-testing
description: Complete guide to writing unit, integration, and e2e tests with best practices and patterns
tags:
  - testing
  - quality
  - tdd
  - automation
user-invocable: true
aliases:
  - testing
  - test-writing
version: 1.0.0
author: Test Expert
---
```

---

## 4. Claude Code Specific Fields

Claude Code adds several platform-specific frontmatter fields:

### context Field

Specifies the execution context for the skill.

| Value | Description |
|-------|-------------|
| `fork` | Skill runs in a forked/isolated context |

```yaml
---
name: my-skill
description: My skill description
context: fork
---
```

### agent Field

Associates the skill with a specific agent type.

| Value | Description |
|-------|-------------|
| `api-coordinator` | API coordination tasks |
| `test-engineer` | Testing-focused tasks |
| `deploy-agent` | Deployment tasks |
| `debug-specialist` | Debugging tasks |
| `code-reviewer` | Code review tasks |

```yaml
---
name: api-testing
description: API testing skill
agent: test-engineer
---
```

### user-invocable Field

Controls whether users can invoke the skill directly via slash command.

```yaml
---
name: commit
description: Git commit workflow
user-invocable: true
---
```

When `true`, users can type `/commit` to invoke this skill.

### Complete Claude Code Example

```yaml
---
name: tdd-enforcement
description: Enforces TDD workflow - tests must be written before implementation code
tags:
  - testing
  - tdd
  - quality
user-invocable: true
context: fork
agent: test-engineer
---
```

---

## 5. Content Best Practices

### Structure

1. **Start with summary** - Brief overview of what the skill teaches
2. **Table of contents** - For skills with multiple sections
3. **When to use** - Clear guidance on applicability
4. **Step-by-step instructions** - Ordered procedures
5. **Examples** - Concrete usage examples
6. **Troubleshooting** - Common issues and solutions

### Writing Guidelines

- **Be specific** - Agents need explicit instructions
- **Use examples** - Show, don't just tell
- **Progressive disclosure** - Link to references for details
- **No assumptions** - Explain every term and tool
- **Action-oriented** - Focus on what to do, not theory

### Example Content Structure

```markdown
---
name: database-migrations
description: Guide for creating and running database migrations safely
---

# Database Migrations

This skill teaches safe database migration practices.

## Table of Contents

1. [When to Use](#when-to-use)
2. [Creating Migrations](#creating-migrations)
3. [Running Migrations](#running-migrations)
4. [Rollback Procedures](#rollback-procedures)
5. [Troubleshooting](#troubleshooting)

## When to Use

Use this skill when:
- Adding new database tables
- Modifying existing schema
- Seeding data
- Rolling back changes

## Creating Migrations

Step 1: Generate migration file
\`\`\`bash
npx prisma migrate dev --name add_users_table
\`\`\`

Step 2: Edit the migration...

[continues with detailed steps]
```

---

## 6. References Directory

### Purpose

The `references/` directory holds detailed documentation that the SKILL.md links to, enabling progressive disclosure.

### Structure

```
my-skill/
├── SKILL.md
└── references/
    ├── detailed-topic.md
    ├── advanced-patterns.md
    └── troubleshooting.md
```

### Linking from SKILL.md

```markdown
## Configuration

For detailed configuration options, see:
[Configuration Reference](references/configuration.md)
```

### Reference File Format

Each reference file should be standalone:

```markdown
# Configuration Reference

## Table of Contents

- [Basic Options](#basic-options)
- [Advanced Options](#advanced-options)
- [Examples](#examples)

## Basic Options

[detailed content...]
```

### TOC-Driven Progressive Disclosure

Include TOCs in SKILL.md for quick navigation:

```markdown
## Related References

For detailed information, see these references:

**[Configuration Reference](references/configuration.md)**
- 1.1 Basic configuration options
- 1.2 Environment-specific settings
- 1.3 Security configurations

**[Troubleshooting Guide](references/troubleshooting.md)**
- 2.1 Common connection errors
- 2.2 Performance issues
- 2.3 Migration failures
```

---

## 7. Common Skill Errors

### Error: Missing SKILL.md

**Symptom:** Skill directory exists but no SKILL.md

**Fix:** Create SKILL.md with required frontmatter:

```markdown
---
name: my-skill
description: Description of the skill
---

# My Skill

Content here...
```

### Error: Invalid Frontmatter

**Wrong:**
```markdown
name: my-skill
description: Missing delimiters

# Content
```

**Correct:**
```markdown
---
name: my-skill
description: With delimiters
---

# Content
```

### Error: Missing Required Fields

**Wrong:**
```yaml
---
tags:
  - testing
---
```

**Correct:**
```yaml
---
name: testing-skill
description: Required description field
tags:
  - testing
---
```

### Error: Invalid YAML Syntax

**Wrong:**
```yaml
---
name: my-skill
description: Has "unescaped quotes" inside
tags:
- missing indent
---
```

**Correct:**
```yaml
---
name: my-skill
description: Has "properly quoted" inside
tags:
  - proper indent
---
```

### Error: Invalid context Value

**Wrong:**
```yaml
---
name: my-skill
description: Description
context: isolated  # Invalid value
---
```

**Correct:**
```yaml
---
name: my-skill
description: Description
context: fork  # Valid value
---
```

### Error: Invalid agent Value

**Wrong:**
```yaml
---
name: my-skill
description: Description
agent: general  # Invalid value
---
```

**Correct:**
```yaml
---
name: my-skill
description: Description
agent: test-engineer  # Valid value
---
```

---

## 8. Validation Checklist

### Pre-release Skill Checklist

- [ ] SKILL.md exists in skill directory
- [ ] Frontmatter has `---` delimiters
- [ ] Required `name` field present
- [ ] Required `description` field present
- [ ] YAML syntax is valid
- [ ] If `context` present, value is `fork`
- [ ] If `agent` present, value is valid
- [ ] If `user-invocable` present, value is boolean
- [ ] Content is well-structured
- [ ] Examples are included
- [ ] References link correctly
- [ ] No broken internal links

### Validation Command

```bash
# Validate single skill
uv run python scripts/validate_skill.py /path/to/my-skill

# Validate all skills in plugin
for skill in /path/to/plugin/skills/*/; do
  uv run python scripts/validate_skill.py "$skill"
done
```

### OpenSpec Validation

For generic Agent Skills validation:

```bash
skills-ref validate /path/to/my-skill
```

Note: OpenSpec validator may warn about Claude Code specific fields (`context`, `agent`, `user-invocable`). These warnings can be ignored for Claude Code plugins.

---

## Related References

- [Plugin Structure](plugin-structure.md) - Overall plugin layout
- [Hook Validation](hook-validation.md) - Hook configuration
- [MCP Validation](mcp-validation.md) - MCP server setup
