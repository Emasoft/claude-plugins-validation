# Plugin Validation Skill

Comprehensive validation skill for Claude Code plugins, marketplaces, hooks, skills, and MCP servers.

## Usage

This skill teaches Claude how to validate all components of Claude Code plugins. Invoke it when you need guidance on:

- Plugin structure and manifest validation
- Hook configuration and script requirements
- Skill structure and frontmatter
- MCP server configuration
- Marketplace setup and plugin distribution

## Files

| File | Description |
|------|-------------|
| `SKILL.md` | Main skill with quick start and overview |
| `references/plugin-structure.md` | Plugin directory structure and manifest |
| `references/hook-validation.md` | Hook events, matchers, and scripts |
| `references/skill-validation.md` | SKILL.md format and frontmatter |
| `references/mcp-validation.md` | MCP server configuration |
| `references/marketplace-validation.md` | Marketplace setup and publishing |

## Quick Reference

### Validation Commands

```bash
# Full plugin validation
uv run python scripts/validate_plugin.py /path/to/plugin --verbose

# Component-specific validation
uv run python scripts/validate_skill.py /path/to/skill
uv run python scripts/validate_hook.py /path/to/hooks.json
uv run python scripts/validate_mcp.py /path/to/plugin
uv run python scripts/validate_marketplace.py /path/to/marketplace
```

### Exit Codes

| Code | Meaning |
|------|---------|
| 0 | All passed |
| 1 | Critical issues |
| 2 | Major issues |
| 3 | Minor issues |

## Key Rules

1. **Components at ROOT**: Place commands/, agents/, skills/, hooks/ at plugin root
2. **Path variables**: Always use `${CLAUDE_PLUGIN_ROOT}` for plugin paths
3. **Naming**: Use kebab-case for names, semver for versions
4. **Agent format**: `agents` field must be array of .md file paths
5. **Hook auto-load**: Standard hooks/hooks.json auto-loads
