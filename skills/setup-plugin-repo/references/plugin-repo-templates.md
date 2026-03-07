# Plugin Repository Templates

## Table of Contents
- [plugin.json Template](#pluginjson-template)
- [pyproject.toml Template](#pyprojecttoml-template)
- [.gitignore Template](#gitignore-template)
- [README.md Template](#readmemd-template)
- [Placeholder Reference](#placeholder-reference)

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
Cargo.lock
```

### Notes
- The `*_dev/` pattern ignores all development artifact folders (docs_dev, scripts_dev, tests_dev, etc.).
- Add language-specific entries if your plugin uses languages beyond Python (e.g. TypeScript, Go, Rust).

---

## README.md Template

Place this file at the repository root.

````markdown
# <placeholder-for-plugin-name>

<placeholder-for-plugin-description>

## Installation

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

## Usage

```bash
# Run the plugin
uv run python scripts/main.py --help
```

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

## Project Structure

```
<placeholder-for-plugin-github-repo>/
├── .claude-plugin/
│   └── plugin.json          # Plugin manifest
├── scripts/                 # Plugin source code
├── tests/                   # Test suite
├── pyproject.toml           # Project configuration
├── README.md                # This file
├── LICENSE                  # License file
└── .gitignore               # Git ignore rules
```

## License

This project is licensed under the <placeholder-for-license-type> License. See [LICENSE](LICENSE) for details.

## Author

**<placeholder-for-plugin-author-name>** - [GitHub](https://github.com/<placeholder-for-github-repo-owner>)
````

### Notes
- The README uses standard sections expected by the Claude Code plugin ecosystem.
- Adjust the "Usage" section to reflect your plugin's actual entry point and commands.
- The "Project Structure" tree should be updated as your plugin grows.

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
