# Error-to-Fix Index

Maps each CPV validator to its fix reference guide with section numbers.

## Table of Contents
- Plugin Structure
- Skill Structure
- Hooks
- Security
- Encoding
- MCP
- Enterprise
- Rules
- LSP
- Cross-References
- Scoring
- Marketplace
- Documentation
- Agent
- Command
- Marketplace Pipeline
- Skill (Basic)

## Plugin Structure

**validate_plugin.py** → [plugin-structure-fixes.md](plugin-structure-fixes.md)

Manifest (plugin.json) → §1 · Directories → §2 · Commands → §3 · Agents → §4 · Hooks → §5 · MCP → §6 · Scripts → §7 · Cross-platform → §8 · README/LICENSE → §10

## Skill Structure

**validate_skill_comprehensive.py** → [skill-fixes.md](skill-fixes.md)

Structure → §1 · Frontmatter → §2 · Name → §3 · Description → §4 · Token budget → §5 · Required sections (strict) → §6 · Reference files → §7

## Hooks

**validate_hook.py** → [hook-fixes.md](hook-fixes.md)

JSON structure → §1 · Event types → §2 · Matchers → §3 · Hook types → §4 · Command/Prompt/Agent hooks → §5-7 · Timeouts → §8 · Scripts → §9-10

## Security

**validate_security.py** → [security-fixes.md](security-fixes.md)

Injection → §2 · Path traversal → §3 · Secrets → §4 · Paths → §5 · Permissions → §7

## Encoding

**validate_encoding.py** → [encoding-fixes.md](encoding-fixes.md)

UTF-8 → §2 · BOM → §3 · JSON unicode → §4 · Line endings → §6-8

## MCP

**validate_mcp.py** → [mcp-fixes.md](mcp-fixes.md)

Config → §1 · Servers → §2 · Transport → §3-5 · Env vars → §6 · OAuth → §11

## Enterprise

**validate_enterprise.py** → [enterprise-fixes.md](enterprise-fixes.md)

Plugin/path → §1 · Skills → §2 · Metadata → §3 · Author/License → §4-5

## Rules

**validate_rules.py** → [rules-fixes.md](rules-fixes.md)

Directory → §1 · Encoding → §2 · Content → §3 · Frontmatter → §4

## LSP

**validate_lsp.py** → [lsp-fixes.md](lsp-fixes.md)

Config → §1 · Structure → §2 · Command → §4 · Filetypes → §7

## Cross-References

**validate_xref.py** → [xref-fixes.md](xref-fixes.md)

Agent refs → §2 · Subagent_type → §3 · Version sync → §4 · Skill refs → §6

## Scoring

**validate_scoring.py** → [scoring-fixes.md](scoring-fixes.md)

Crash messages → §4 · Low scores → §6

## Marketplace

**validate_marketplace.py** → [marketplace-fixes.md](marketplace-fixes.md)

marketplace.json → §1 · Plugin entries → §2 · Pipeline → §5

## Documentation

**validate_documentation.py** → [documentation-fixes.md](documentation-fixes.md)

README → §1-2 · Links → §3 · CHANGELOG → §4 · Headings → §5

## Agent

**validate_agent.py** → [plugin-structure-fixes.md](plugin-structure-fixes.md)

Agent markdown files → structure, frontmatter, description, allowed-tools

## Command

**validate_command.py** → [plugin-structure-fixes.md](plugin-structure-fixes.md)

Command markdown files → structure, frontmatter, name, argument-hint

## Marketplace Pipeline

**validate_marketplace_pipeline.py** → [marketplace-fixes.md](marketplace-fixes.md)

Publishing pipeline → notify-marketplace.yml, publish.py, pre-push hook, PAT secrets

## Skill (Basic)

**validate_skill.py** → [skill-fixes.md](skill-fixes.md)

Basic skill validation → structure, frontmatter, name matching, directories
