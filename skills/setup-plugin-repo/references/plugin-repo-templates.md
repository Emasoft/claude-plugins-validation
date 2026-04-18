# Plugin Repository Templates

## Table of Contents
- [plugin.json Template](#pluginjson-template)
- [pyproject.toml Template](#pyprojecttoml-template)
- [.gitignore Template](#gitignore-template)
- [README.md Template](#readmemd-template)
- [Placeholder Reference](#placeholder-reference)

## Checklist

- [ ] Copy plugin.json template to `.claude-plugin/plugin.json`
- [ ] Copy pyproject.toml template to repo root
- [ ] Copy .gitignore template to repo root
- [ ] Copy README.md template to repo root
- [ ] Replace every `<placeholder-for-*>` with real values
- [ ] Run `validate_plugin.py --strict` to confirm

---

## plugin.json Template

Place this file at `.claude-plugin/plugin.json` in the repository root.

```json
{
  "name": "<placeholder-for-plugin-name>",
  "version": "<placeholder-for-plugin-version>",
  "description": "<placeholder-for-plugin-description>",
  "author": {
    "name": "<placeholder-for-plugin-author-name>",
    "email": "<placeholder-for-author-email>"
  },
  "homepage": "https://github.com/<placeholder-for-github-repo-owner>/<placeholder-for-plugin-github-repo>",
  "repository": "https://github.com/<placeholder-for-github-repo-owner>/<placeholder-for-plugin-github-repo>",
  "license": "<placeholder-for-license-type>",
  "keywords": [
    <placeholder-for-keywords>
  ]
}
```

### Notes
- `name` must match the repository name exactly.
- `version` must follow semantic versioning (e.g. `1.0.0`).
- `keywords` is a comma-separated list of quoted strings, e.g. `"validation", "plugins", "linting"`.
- `homepage` and `repository` should point to the GitHub repo URL.

---

## pyproject.toml Template

Place this file at the repository root. Only the `[project]` section is templated; adjust `[tool.*]` sections to match your project's tooling needs.

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["scripts"]

[project]
name = "<placeholder-for-plugin-name>"
version = "<placeholder-for-plugin-version>"
description = "<placeholder-for-plugin-description>"
readme = "README.md"
requires-python = ">=<placeholder-for-python-version>"
dependencies = [
    "mypy>=1.19.1",
    "pytest>=8.0.0",
    "pytest-cov>=4.1.0",
    "pyyaml>=6.0.3",
    "ruff>=0.14.14",
    "types-pyyaml>=6.0.12",
]

[tool.ruff]
line-length = 120

[tool.ruff.lint]
select = ["E", "F", "W", "I"]
ignore = ["E501"]

[tool.ruff.lint.per-file-ignores]
"tests/*.py" = ["E402"]

[tool.mypy]
python_version = "<placeholder-for-python-version>"
warn_return_any = true
warn_unused_configs = true

[tool.pyright]
pythonVersion = "<placeholder-for-python-version>"
extraPaths = ["scripts", "tests"]
reportMissingImports = "warning"
typeCheckingMode = "basic"
```

### Notes
- Replace `packages = ["scripts"]` with the actual package directory for your plugin.
- Adjust `dependencies` to include only what your plugin needs.
- `<placeholder-for-python-version>` should be a version string like `3.12` (without the `>=` prefix, which is already included in the template where needed).

---

## .gitignore Template

Place this file at the repository root.

```gitignore
# Python
__pycache__/
*.py[cod]
*$py.class
*.so
.Python
*.egg-info/
.eggs/
dist/
build/
.coverage
.venv/
.pytest_cache/

# Type checking
.mypy_cache/
.dmypy.json
dmypy.json

# Linting
.ruff_cache/

# IDE
.idea/
.vscode/
*.swp
*.swo

# OS
.DS_Store
Thumbs.db

# Environment
.env
.env.*

# Dev folders (NEVER PUBLISH - development artifacts only)
# Wildcard pattern catches all: docs_dev, scripts_dev, tests_dev, samples_dev,
# examples_dev, downloads_dev, libs_dev, builds_dev, etc.
*_dev/

# Node
node_modules/

# Rust
target/
Cargo.lock  # Remove this line for binary plugins (commit Cargo.lock for reproducible builds)
```

### Notes
- The `*_dev/` pattern ignores all development artifact folders (docs_dev, scripts_dev, tests_dev, etc.).
- Add language-specific entries if your plugin uses languages beyond Python (e.g. TypeScript, Go, Rust).

---

## README.md Template

Place this file at the repository root.

````markdown
# <placeholder-for-plugin-name>

[![CI](https://github.com/<placeholder-for-github-repo-owner>/<placeholder-for-plugin-github-repo>/actions/workflows/ci.yml/badge.svg)](https://github.com/<placeholder-for-github-repo-owner>/<placeholder-for-plugin-github-repo>/actions/workflows/ci.yml)
[![Version](https://img.shields.io/badge/version-<placeholder-for-plugin-version>-blue)](https://github.com/<placeholder-for-github-repo-owner>/<placeholder-for-plugin-github-repo>)
[![License](https://img.shields.io/badge/license-<placeholder-for-license-type>-green)](LICENSE)

<placeholder-for-plugin-description>

## Installation

### From Marketplace

```bash
claude plugin install <placeholder-for-plugin-name>@<placeholder-for-marketplace-name>
```

### From GitHub

```bash
gh repo clone <placeholder-for-github-repo-owner>/<placeholder-for-plugin-github-repo>
cd <placeholder-for-plugin-github-repo>
uv venv --python 3.12
source .venv/bin/activate
uv pip install -e .
```

### As a Claude Code Plugin

Add to your Claude Code configuration:

```json
{
  "plugins": [
    "https://github.com/<placeholder-for-github-repo-owner>/<placeholder-for-plugin-github-repo>"
  ]
}
```

## Uninstall

```bash
claude plugin uninstall <placeholder-for-plugin-name>
```

## Update

```bash
claude plugin update <placeholder-for-plugin-name>@<placeholder-for-marketplace-name>
```

## Components

### Commands

<placeholder-for-commands-table>

### Agents

<placeholder-for-agents-table>

### Skills

<placeholder-for-skills-table>

### Hooks

<placeholder-for-hooks-description>

## Usage

<placeholder-for-usage-instructions>

## Development

### Prerequisites

- Python >= <placeholder-for-python-version>
- [uv](https://docs.astral.sh/uv/) package manager

### Setup

```bash
uv venv --python <placeholder-for-python-version>
source .venv/bin/activate
uv pip install -e ".[dev]"
```

### Testing

```bash
uv run pytest tests/ -v
```

### Linting & Formatting

```bash
uv run ruff check scripts/ tests/
uv run ruff format scripts/ tests/
uv run mypy scripts/
```

### Release

```bash
uv run python scripts/publish.py --patch    # 1.0.0 → 1.0.1
uv run python scripts/publish.py --minor    # 1.0.0 → 1.1.0
```

## Project Structure

```
<placeholder-for-plugin-github-repo>/
├── .claude-plugin/
│   └── plugin.json          # Plugin manifest
├── commands/                # Slash commands (*.md)
├── agents/                  # Agent definitions (*.md)
├── skills/                  # Skill directories (SKILL.md)
├── hooks/                   # Hook configurations
├── scripts/                 # Python scripts (pipeline, validators)
├── tests/                   # Test suite
├── git-hooks/pre-push       # Quality gate (thin delegator to publish.py --gate)
├── .github/workflows/       # CI/CD (ci, release, validate, notify)
├── cliff.toml               # git-cliff changelog config
├── pyproject.toml           # Project configuration
├── README.md                # This file
├── CHANGELOG.md             # Auto-generated changelog
├── LICENSE                  # License file
└── .gitignore               # Git ignore rules
```

## Troubleshooting

### Hook path not found
If you get "can't open file" errors from hooks, reinstall them:
```bash
uv run python scripts/setup_git_hooks.py
```

### Old version after update
Claude Code may cache the old version. Restart Claude Code to pick up changes:
```bash
# Close and reopen Claude Code
```

### Restart required after update
After updating the plugin, restart Claude Code to reload all components.

## Marketplace

This plugin is available on the [<placeholder-for-marketplace-name> marketplace](https://github.com/<placeholder-for-github-repo-owner>/<placeholder-for-marketplace-repo-name>).

## License

This project is licensed under the <placeholder-for-license-type> License. See [LICENSE](LICENSE) for details.

## Author

**<placeholder-for-plugin-author-name>** - [GitHub](https://github.com/<placeholder-for-github-repo-owner>)
````

### Notes
- The README uses standard sections expected by the Claude Code plugin ecosystem.
- The agent MUST fill `<placeholder-for-commands-table>` by scanning `commands/*.md` frontmatter and generating a markdown table with `| Command | Description |` columns.
- The agent MUST fill `<placeholder-for-agents-table>` by scanning `agents/*.md` frontmatter and generating a markdown table with `| Agent | Description |` columns.
- The agent MUST fill `<placeholder-for-skills-table>` by scanning `skills/*/SKILL.md` frontmatter and generating a markdown table with `| Skill | Description |` columns.
- The agent MUST fill `<placeholder-for-hooks-description>` by reading `hooks/hooks.json` (if present) and listing each hook event and its purpose.
- The agent MUST fill `<placeholder-for-usage-instructions>` with actual usage examples showing how to invoke the plugin's commands, activate its agents, and use its skills.
- The Troubleshooting section includes the 3 required topics: hook path not found, old version after update, restart required.

---

## Placeholder Reference

| Placeholder | Description | Example Value |
|---|---|---|
| `<placeholder-for-plugin-name>` | Plugin package name (lowercase, hyphens allowed) | `my-awesome-plugin` |
| `<placeholder-for-plugin-version>` | Semantic version string | `1.0.0` |
| `<placeholder-for-plugin-description>` | One-line description of the plugin | `A plugin that validates Claude Code configurations` |
| `<placeholder-for-plugin-author-name>` | Author's display name or GitHub username | `my-org` |
| `<placeholder-for-author-email>` | Author's email (can use GitHub noreply) | `user@example.com` |
| `<placeholder-for-github-repo-owner>` | GitHub account or organization name | `my-org` |
| `<placeholder-for-plugin-github-repo>` | GitHub repository name (usually matches plugin name) | `my-awesome-plugin` |
| `<placeholder-for-license-type>` | SPDX license identifier | `MIT` |
| `<placeholder-for-keywords>` | Comma-separated quoted keyword strings | `"validation", "plugins", "linting"` |
| `<placeholder-for-python-version>` | Minimum Python version (digits and dots only) | `3.12` |
| `<placeholder-for-marketplace-name>` | Marketplace name for install commands | `my-marketplace` |
| `<placeholder-for-marketplace-repo-name>` | Marketplace GitHub repository name | `my-marketplace` |
| `<placeholder-for-commands-table>` | Auto-generated: scan `commands/*.md` frontmatter | `\| /cmd \| desc \|` table |
| `<placeholder-for-agents-table>` | Auto-generated: scan `agents/*.md` frontmatter | `\| agent \| desc \|` table |
| `<placeholder-for-skills-table>` | Auto-generated: scan `skills/*/SKILL.md` frontmatter | `\| skill \| desc \|` table |
| `<placeholder-for-hooks-description>` | Auto-generated: read `hooks/hooks.json` events | Hook event list |
| `<placeholder-for-usage-instructions>` | Agent writes actual usage examples | Command invocations |
