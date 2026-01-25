# Valid Test Plugin

A complete valid plugin structure for testing the validation system.

## Description

This plugin serves as a fixture for testing the claude-plugins-validation validators.
It contains all required components in their correct structure.

## Installation

```bash
claude plugin install valid-test-plugin
```

## Usage

This plugin is used for testing purposes only. It demonstrates the correct
structure and format for Claude Code plugins.

## Structure

```
valid_plugin/
  .claude-plugin/
    plugin.json       # Valid plugin manifest
  agents/
    test-agent.md     # Valid agent with frontmatter
  skills/
    test-skill/
      SKILL.md        # Valid skill with frontmatter
  README.md           # This file
```

## License

MIT
