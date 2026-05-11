# Coverage-Surface Audit — CPV vs `claude plugin validate`

**TRDD:** b4c6cbe7
**Generated:** 2026-05-11
**Fixtures audited:** 30


## 0. Executive summary — top gap categories

Each row aggregates `cli_only` findings by topic-fingerprint, so a single category that fires on N fixtures shows up once with `count=N`. These are the strongest child-TRDD candidates — highest severity at the top, then highest count.

| Rank | Severity | Topic | Count | Example fixtures |
|---:|---|---|---:|---|
| 1 | `CRITICAL` | `field:hooks` | 5 | `13-hook-pretooluse-command`, `14-hook-stop-prompt`, `15-hook-sessionstart-mcp-tool`, …+2 |
| 2 | `CRITICAL` | `field:plugins.0.source` | 3 | `05-marketplace-source-relative-path`, `08-marketplace-source-git-subdir`, `12-layout-c-marketplace-in-plugin` |
| 3 | `CRITICAL` | `field:lspservers` | 2 | `27-lspserver-basic`, `28-lspserver-malformed` |
| 4 | `CRITICAL` | `field:name` | 1 | `02-missing-name` |
| 5 | `CRITICAL` | `unknown:cpv` | 1 | `04-unknown-root-key` |
| 6 | `CRITICAL` | `field:mcpservers` | 1 | `26-mcpserver-no-command` |
| 7 | `CRITICAL` | `field:monitors` | 1 | `29-monitors-field` |
| 8 | `CRITICAL` | `unknown:outputstyle` | 1 | `30-outputstyle-field` |
| 9 | `WARNING` | `description-no-description-provided-adding-a-description-helps-users-understand-` | 12 | `01-bare-minimum-valid`, `03-invalid-semver`, `10-layout-a-plugin-only`, …+9 |
| 10 | `WARNING` | `author-no-author-information-provided-consider-adding-author-details-for-plugin-` | 12 | `01-bare-minimum-valid`, `03-invalid-semver`, `10-layout-a-plugin-only`, …+9 |

## 1. Summary counts

| Metric | Count |
|---|---|
| Fixtures | 30 |
| CLI-only findings (CPV gaps) | 43 |
| CPV-only findings (extensions or false positives) | 256 |
| Findings agreed on (both flagged) | 0 |

## 2. Per-fixture diff matrix

| Fixture | CLI exit | CPV exit | CLI-only | CPV-only | Both |
|---|---:|---:|---:|---:|---:|
| `01-bare-minimum-valid` | 0 | 2 | 2 | 8 | 0 |
| `02-missing-name` | 1 | 1 | 1 | 9 | 0 |
| `03-invalid-semver` | 0 | 2 | 2 | 9 | 0 |
| `04-unknown-root-key` | 1 | 2 | 1 | 8 | 0 |
| `05-marketplace-source-relative-path` | 1 | 2 | 1 | 9 | 0 |
| `06-marketplace-source-github` | 0 | 2 | 1 | 9 | 0 |
| `07-marketplace-source-url` | 0 | 2 | 1 | 9 | 0 |
| `08-marketplace-source-git-subdir` | 1 | 2 | 1 | 9 | 0 |
| `09-marketplace-source-npm` | 0 | 2 | 1 | 9 | 0 |
| `10-layout-a-plugin-only` | 0 | 2 | 2 | 8 | 0 |
| `11-layout-b-monorepo-plugin` | 0 | 2 | 2 | 8 | 0 |
| `12-layout-c-marketplace-in-plugin` | 1 | 2 | 1 | 9 | 0 |
| `13-hook-pretooluse-command` | 1 | 2 | 1 | 8 | 0 |
| `14-hook-stop-prompt` | 1 | 2 | 1 | 8 | 0 |
| `15-hook-sessionstart-mcp-tool` | 1 | 2 | 1 | 9 | 0 |
| `16-hook-userpromptsubmit-http` | 1 | 2 | 1 | 8 | 0 |
| `17-hook-precompact-agent` | 1 | 2 | 1 | 7 | 0 |
| `18-agent-frontmatter-valid` | 0 | 2 | 2 | 7 | 0 |
| `19-agent-frontmatter-missing-name` | 0 | 1 | 2 | 8 | 0 |
| `20-agent-frontmatter-hex-color` | 0 | 2 | 2 | 7 | 0 |
| `21-skill-valid` | 0 | 2 | 2 | 9 | 0 |
| `22-skill-undeclared-named-arg` | 0 | 2 | 2 | 9 | 0 |
| `23-skill-paths-shape` | 0 | 2 | 2 | 9 | 0 |
| `24-mcpserver-stdio` | 0 | 2 | 2 | 9 | 0 |
| `25-mcpserver-http` | 0 | 2 | 2 | 9 | 0 |
| `26-mcpserver-no-command` | 1 | 1 | 1 | 9 | 0 |
| `27-lspserver-basic` | 1 | 2 | 1 | 9 | 0 |
| `28-lspserver-malformed` | 1 | 2 | 1 | 8 | 0 |
| `29-monitors-field` | 1 | 2 | 2 | 10 | 0 |
| `30-outputstyle-field` | 1 | 2 | 1 | 9 | 0 |

## 3. CLI-only findings (CPV gaps)

Each row here is a candidate child-TRDD: a finding the
official CLI flags that CPV does not currently emit.

### 01-bare-minimum-valid

- `WARNING` description: No description provided. Adding a description helps users understand what your plugin does (.claude-plugin/plugin.json)
- `WARNING` author: No author information provided. Consider adding author details for plugin attribution (.claude-plugin/plugin.json)

### 02-missing-name

- `CRITICAL` name: Invalid input: expected string, received undefined (.claude-plugin/plugin.json)

### 03-invalid-semver

- `WARNING` description: No description provided. Adding a description helps users understand what your plugin does (.claude-plugin/plugin.json)
- `WARNING` author: No author information provided. Consider adding author details for plugin attribution (.claude-plugin/plugin.json)

### 04-unknown-root-key

- `CRITICAL` root: Unrecognized key: "cpv" (.claude-plugin/plugin.json)

### 05-marketplace-source-relative-path

- `CRITICAL` plugins.0.source: Invalid input (.claude-plugin/marketplace.json)

### 06-marketplace-source-github

- `WARNING` description: No marketplace description provided. Adding a description helps users understand what this marketplace offers (.claude-plugin/marketplace.json)

### 07-marketplace-source-url

- `WARNING` description: No marketplace description provided. Adding a description helps users understand what this marketplace offers (.claude-plugin/marketplace.json)

### 08-marketplace-source-git-subdir

- `CRITICAL` plugins.0.source: Invalid input (.claude-plugin/marketplace.json)

### 09-marketplace-source-npm

- `WARNING` description: No marketplace description provided. Adding a description helps users understand what this marketplace offers (.claude-plugin/marketplace.json)

### 10-layout-a-plugin-only

- `WARNING` description: No description provided. Adding a description helps users understand what your plugin does (.claude-plugin/plugin.json)
- `WARNING` author: No author information provided. Consider adding author details for plugin attribution (.claude-plugin/plugin.json)

### 11-layout-b-monorepo-plugin

- `WARNING` description: No description provided. Adding a description helps users understand what your plugin does (.claude-plugin/plugin.json)
- `WARNING` author: No author information provided. Consider adding author details for plugin attribution (.claude-plugin/plugin.json)

### 12-layout-c-marketplace-in-plugin

- `CRITICAL` plugins.0.source: Invalid input (.claude-plugin/marketplace.json)

### 13-hook-pretooluse-command

- `CRITICAL` hooks: Invalid input (.claude-plugin/plugin.json)

### 14-hook-stop-prompt

- `CRITICAL` hooks: Invalid input (.claude-plugin/plugin.json)

### 15-hook-sessionstart-mcp-tool

- `CRITICAL` hooks: Invalid input (.claude-plugin/plugin.json)

### 16-hook-userpromptsubmit-http

- `CRITICAL` hooks: Invalid input (.claude-plugin/plugin.json)

### 17-hook-precompact-agent

- `CRITICAL` hooks: Invalid input (.claude-plugin/plugin.json)

### 18-agent-frontmatter-valid

- `WARNING` description: No description provided. Adding a description helps users understand what your plugin does (.claude-plugin/plugin.json)
- `WARNING` author: No author information provided. Consider adding author details for plugin attribution (.claude-plugin/plugin.json)

### 19-agent-frontmatter-missing-name

- `WARNING` description: No description provided. Adding a description helps users understand what your plugin does (.claude-plugin/plugin.json)
- `WARNING` author: No author information provided. Consider adding author details for plugin attribution (.claude-plugin/plugin.json)

### 20-agent-frontmatter-hex-color

- `WARNING` description: No description provided. Adding a description helps users understand what your plugin does (.claude-plugin/plugin.json)
- `WARNING` author: No author information provided. Consider adding author details for plugin attribution (.claude-plugin/plugin.json)

### 21-skill-valid

- `WARNING` description: No description provided. Adding a description helps users understand what your plugin does (.claude-plugin/plugin.json)
- `WARNING` author: No author information provided. Consider adding author details for plugin attribution (.claude-plugin/plugin.json)

### 22-skill-undeclared-named-arg

- `WARNING` description: No description provided. Adding a description helps users understand what your plugin does (.claude-plugin/plugin.json)
- `WARNING` author: No author information provided. Consider adding author details for plugin attribution (.claude-plugin/plugin.json)

### 23-skill-paths-shape

- `WARNING` description: No description provided. Adding a description helps users understand what your plugin does (.claude-plugin/plugin.json)
- `WARNING` author: No author information provided. Consider adding author details for plugin attribution (.claude-plugin/plugin.json)

### 24-mcpserver-stdio

- `WARNING` description: No description provided. Adding a description helps users understand what your plugin does (.claude-plugin/plugin.json)
- `WARNING` author: No author information provided. Consider adding author details for plugin attribution (.claude-plugin/plugin.json)

### 25-mcpserver-http

- `WARNING` description: No description provided. Adding a description helps users understand what your plugin does (.claude-plugin/plugin.json)
- `WARNING` author: No author information provided. Consider adding author details for plugin attribution (.claude-plugin/plugin.json)

### 26-mcpserver-no-command

- `CRITICAL` mcpServers: Invalid input (.claude-plugin/plugin.json)

### 27-lspserver-basic

- `CRITICAL` lspServers: Invalid input (.claude-plugin/plugin.json)

### 28-lspserver-malformed

- `CRITICAL` lspServers: Invalid input (.claude-plugin/plugin.json)

### 29-monitors-field

- `CRITICAL` monitors: Invalid input (.claude-plugin/plugin.json)
- `WARNING` monitors: 'monitors' is an experimental component; declare it under 'experimental.monitors' instead of at the top level. Top-level still loads for now but will be removed in a future release. (.claude-plugin/plugin.json)

### 30-outputstyle-field

- `CRITICAL` root: Unrecognized key: "outputStyle" (.claude-plugin/plugin.json)

## 4. CPV-only findings (extensions / false positives)

CPV-only findings represent either: (a) intentional extensions
where CPV enforces stricter rules than CLI, or (b) false
positives that should be silenced. Triage each row.

### 01-bare-minimum-valid

- `MINOR` Missing recommended field 'description' in plugin.json (.claude-plugin/plugin.json)
- `MAJOR` Plugin has a manifest but no content — expected at least one of: commands/, skills/, agents/, hooks/, scripts/, .mcp.json, or .lsp.json (.claude-plugin/plugin.json)
- `MINOR` No LICENSE file found
- `MAJOR` No .gitignore file found — cache files, build artifacts, and secrets may be accidentally included in the plugin
- `MINOR` No pre-push hook found (.githooks/pre-push or git-hooks/pre-push) — recommended for quality gates
- `WARNING` No scripts/publish.py found — recommended for release automation
- `WARNING` No cliff.toml found — recommended for automated changelog generation
- `MINOR` No .github/workflows/*.yml found — recommended for CI/CD automation

### 02-missing-name

- `CRITICAL` Missing required field 'name' in plugin.json (.claude-plugin/plugin.json)
- `MINOR` Missing recommended field 'description' in plugin.json (.claude-plugin/plugin.json)
- `MAJOR` Plugin has a manifest but no content — expected at least one of: commands/, skills/, agents/, hooks/, scripts/, .mcp.json, or .lsp.json (.claude-plugin/plugin.json)
- `MINOR` No LICENSE file found
- `MAJOR` No .gitignore file found — cache files, build artifacts, and secrets may be accidentally included in the plugin
- `MINOR` No pre-push hook found (.githooks/pre-push or git-hooks/pre-push) — recommended for quality gates
- `WARNING` No scripts/publish.py found — recommended for release automation
- `WARNING` No cliff.toml found — recommended for automated changelog generation
- `MINOR` No .github/workflows/*.yml found — recommended for CI/CD automation

### 03-invalid-semver

- `MINOR` Missing recommended field 'description' in plugin.json (.claude-plugin/plugin.json)
- `MAJOR` Version must be semver format: not-semver (.claude-plugin/plugin.json)
- `MAJOR` Plugin has a manifest but no content — expected at least one of: commands/, skills/, agents/, hooks/, scripts/, .mcp.json, or .lsp.json (.claude-plugin/plugin.json)
- `MINOR` No LICENSE file found
- `MAJOR` No .gitignore file found — cache files, build artifacts, and secrets may be accidentally included in the plugin
- `MINOR` No pre-push hook found (.githooks/pre-push or git-hooks/pre-push) — recommended for quality gates
- `WARNING` No scripts/publish.py found — recommended for release automation
- `WARNING` No cliff.toml found — recommended for automated changelog generation
- `MINOR` No .github/workflows/*.yml found — recommended for CI/CD automation

### 04-unknown-root-key

- `MINOR` Missing recommended field 'description' in plugin.json (.claude-plugin/plugin.json)
- `MAJOR` Plugin has a manifest but no content — expected at least one of: commands/, skills/, agents/, hooks/, scripts/, .mcp.json, or .lsp.json (.claude-plugin/plugin.json)
- `MINOR` No LICENSE file found
- `MAJOR` No .gitignore file found — cache files, build artifacts, and secrets may be accidentally included in the plugin
- `MINOR` No pre-push hook found (.githooks/pre-push or git-hooks/pre-push) — recommended for quality gates
- `WARNING` No scripts/publish.py found — recommended for release automation
- `WARNING` No cliff.toml found — recommended for automated changelog generation
- `MINOR` No .github/workflows/*.yml found — recommended for CI/CD automation

### 05-marketplace-source-relative-path

- `MINOR` Missing recommended field 'description' in plugin.json (.claude-plugin/plugin.json)
- `MAJOR` Plugin has a manifest but no content — expected at least one of: commands/, skills/, agents/, hooks/, scripts/, .mcp.json, or .lsp.json (.claude-plugin/plugin.json)
- `MAJOR` Layout C: marketplace.json's self-reference for plugin 'fixture-05' has source={'source': './', 'type': 'relative-path'}; must be './' (relative) so install resolves to the same repo. Other source types would re-clone the repository. (.claude-plugin/marketplace.json)
- `MINOR` No LICENSE file found
- `MAJOR` No .gitignore file found — cache files, build artifacts, and secrets may be accidentally included in the plugin
- `MINOR` No pre-push hook found (.githooks/pre-push or git-hooks/pre-push) — recommended for quality gates
- `WARNING` No scripts/publish.py found — recommended for release automation
- `WARNING` No cliff.toml found — recommended for automated changelog generation
- `MINOR` No .github/workflows/*.yml found — recommended for CI/CD automation

### 06-marketplace-source-github

- `MINOR` Missing recommended field 'description' in plugin.json (.claude-plugin/plugin.json)
- `MAJOR` Plugin has a manifest but no content — expected at least one of: commands/, skills/, agents/, hooks/, scripts/, .mcp.json, or .lsp.json (.claude-plugin/plugin.json)
- `MAJOR` Layout C: marketplace.json's self-reference for plugin 'fixture-06' has source={'source': 'github', 'repo': 'Emasoft/fixture-06'}; must be './' (relative) so install resolves to the same repo. Other source types would re-clone the repository. (.claude-plugin/marketplace.json)
- `MINOR` No LICENSE file found
- `MAJOR` No .gitignore file found — cache files, build artifacts, and secrets may be accidentally included in the plugin
- `MINOR` No pre-push hook found (.githooks/pre-push or git-hooks/pre-push) — recommended for quality gates
- `WARNING` No scripts/publish.py found — recommended for release automation
- `WARNING` No cliff.toml found — recommended for automated changelog generation
- `MINOR` No .github/workflows/*.yml found — recommended for CI/CD automation

### 07-marketplace-source-url

- `MINOR` Missing recommended field 'description' in plugin.json (.claude-plugin/plugin.json)
- `MAJOR` Plugin has a manifest but no content — expected at least one of: commands/, skills/, agents/, hooks/, scripts/, .mcp.json, or .lsp.json (.claude-plugin/plugin.json)
- `MAJOR` Layout C: marketplace.json's self-reference for plugin 'fixture-07' has source={'source': 'url', 'url': 'https://example.com/fixture.tgz'}; must be './' (relative) so install resolves to the same repo. Other source types would re-clone the repository. (.claude-plugin/marketplace.json)
- `MINOR` No LICENSE file found
- `MAJOR` No .gitignore file found — cache files, build artifacts, and secrets may be accidentally included in the plugin
- `MINOR` No pre-push hook found (.githooks/pre-push or git-hooks/pre-push) — recommended for quality gates
- `WARNING` No scripts/publish.py found — recommended for release automation
- `WARNING` No cliff.toml found — recommended for automated changelog generation
- `MINOR` No .github/workflows/*.yml found — recommended for CI/CD automation

### 08-marketplace-source-git-subdir

- `MINOR` Missing recommended field 'description' in plugin.json (.claude-plugin/plugin.json)
- `MAJOR` Plugin has a manifest but no content — expected at least one of: commands/, skills/, agents/, hooks/, scripts/, .mcp.json, or .lsp.json (.claude-plugin/plugin.json)
- `MAJOR` Layout C: marketplace.json's self-reference for plugin 'fixture-08' has source={'source': 'git-subdir', 'url': 'https://github.com/Emasoft/monorepo.git', 'subdir': 'plugins/fixture-08'}; must be './' (relative) so install resolves to the same repo. Other source types would re-clone the repository. (.claude-plugin/marketplace.json)
- `MINOR` No LICENSE file found
- `MAJOR` No .gitignore file found — cache files, build artifacts, and secrets may be accidentally included in the plugin
- `MINOR` No pre-push hook found (.githooks/pre-push or git-hooks/pre-push) — recommended for quality gates
- `WARNING` No scripts/publish.py found — recommended for release automation
- `WARNING` No cliff.toml found — recommended for automated changelog generation
- `MINOR` No .github/workflows/*.yml found — recommended for CI/CD automation

### 09-marketplace-source-npm

- `MINOR` Missing recommended field 'description' in plugin.json (.claude-plugin/plugin.json)
- `MAJOR` Plugin has a manifest but no content — expected at least one of: commands/, skills/, agents/, hooks/, scripts/, .mcp.json, or .lsp.json (.claude-plugin/plugin.json)
- `MAJOR` Layout C: marketplace.json's self-reference for plugin 'fixture-09' has source={'source': 'npm', 'package': '@scope/fixture-09'}; must be './' (relative) so install resolves to the same repo. Other source types would re-clone the repository. (.claude-plugin/marketplace.json)
- `MINOR` No LICENSE file found
- `MAJOR` No .gitignore file found — cache files, build artifacts, and secrets may be accidentally included in the plugin
- `MINOR` No pre-push hook found (.githooks/pre-push or git-hooks/pre-push) — recommended for quality gates
- `WARNING` No scripts/publish.py found — recommended for release automation
- `WARNING` No cliff.toml found — recommended for automated changelog generation
- `MINOR` No .github/workflows/*.yml found — recommended for CI/CD automation

### 10-layout-a-plugin-only

- `MINOR` Missing recommended field 'description' in plugin.json (.claude-plugin/plugin.json)
- `MAJOR` Plugin has a manifest but no content — expected at least one of: commands/, skills/, agents/, hooks/, scripts/, .mcp.json, or .lsp.json (.claude-plugin/plugin.json)
- `MINOR` No LICENSE file found
- `MAJOR` No .gitignore file found — cache files, build artifacts, and secrets may be accidentally included in the plugin
- `MINOR` No pre-push hook found (.githooks/pre-push or git-hooks/pre-push) — recommended for quality gates
- `WARNING` No scripts/publish.py found — recommended for release automation
- `WARNING` No cliff.toml found — recommended for automated changelog generation
- `MINOR` No .github/workflows/*.yml found — recommended for CI/CD automation

### 11-layout-b-monorepo-plugin

- `MINOR` Missing recommended field 'description' in plugin.json (.claude-plugin/plugin.json)
- `MAJOR` Plugin has a manifest but no content — expected at least one of: commands/, skills/, agents/, hooks/, scripts/, .mcp.json, or .lsp.json (.claude-plugin/plugin.json)
- `MINOR` No LICENSE file found
- `MAJOR` No .gitignore file found — cache files, build artifacts, and secrets may be accidentally included in the plugin
- `MINOR` No pre-push hook found (.githooks/pre-push or git-hooks/pre-push) — recommended for quality gates
- `WARNING` No scripts/publish.py found — recommended for release automation
- `WARNING` No cliff.toml found — recommended for automated changelog generation
- `MINOR` No .github/workflows/*.yml found — recommended for CI/CD automation

### 12-layout-c-marketplace-in-plugin

- `MINOR` Missing recommended field 'description' in plugin.json (.claude-plugin/plugin.json)
- `MAJOR` Plugin has a manifest but no content — expected at least one of: commands/, skills/, agents/, hooks/, scripts/, .mcp.json, or .lsp.json (.claude-plugin/plugin.json)
- `MAJOR` Layout C: marketplace.json's self-reference for plugin 'fixture-12' has source={'source': './', 'type': 'relative-path'}; must be './' (relative) so install resolves to the same repo. Other source types would re-clone the repository. (.claude-plugin/marketplace.json)
- `MINOR` No LICENSE file found
- `MAJOR` No .gitignore file found — cache files, build artifacts, and secrets may be accidentally included in the plugin
- `MINOR` No pre-push hook found (.githooks/pre-push or git-hooks/pre-push) — recommended for quality gates
- `WARNING` No scripts/publish.py found — recommended for release automation
- `WARNING` No cliff.toml found — recommended for automated changelog generation
- `MINOR` No .github/workflows/*.yml found — recommended for CI/CD automation

### 13-hook-pretooluse-command

- `MINOR` Missing recommended field 'description' in plugin.json (.claude-plugin/plugin.json)
- `MINOR` No LICENSE file found
- `MAJOR` No .gitignore file found — cache files, build artifacts, and secrets may be accidentally included in the plugin
- `WARNING` Found 1 Bash/Shell script(s) (.sh) — only natively available on linux, macos. Not natively available on Windows. Consider providing cross-platform alternatives or documenting requirements.
- `MINOR` No pre-push hook found (.githooks/pre-push or git-hooks/pre-push) — recommended for quality gates
- `WARNING` No scripts/publish.py found — recommended for release automation
- `WARNING` No cliff.toml found — recommended for automated changelog generation
- `MINOR` No .github/workflows/*.yml found — recommended for CI/CD automation

### 14-hook-stop-prompt

- `MINOR` Missing recommended field 'description' in plugin.json (.claude-plugin/plugin.json)
- `MAJOR` Plugin has a manifest but no content — expected at least one of: commands/, skills/, agents/, hooks/, scripts/, .mcp.json, or .lsp.json (.claude-plugin/plugin.json)
- `MINOR` No LICENSE file found
- `MAJOR` No .gitignore file found — cache files, build artifacts, and secrets may be accidentally included in the plugin
- `MINOR` No pre-push hook found (.githooks/pre-push or git-hooks/pre-push) — recommended for quality gates
- `WARNING` No scripts/publish.py found — recommended for release automation
- `WARNING` No cliff.toml found — recommended for automated changelog generation
- `MINOR` No .github/workflows/*.yml found — recommended for CI/CD automation

### 15-hook-sessionstart-mcp-tool

- `MINOR` Missing recommended field 'description' in plugin.json (.claude-plugin/plugin.json)
- `MAJOR` Plugin has a manifest but no content — expected at least one of: commands/, skills/, agents/, hooks/, scripts/, .mcp.json, or .lsp.json (.claude-plugin/plugin.json)
- `MAJOR` Absolute path found in plugin.json:mcpServers:demo-server:command: /bin/true - use ${{CLAUDE_PLUGIN_ROOT}} for portability
- `MINOR` No LICENSE file found
- `MAJOR` No .gitignore file found — cache files, build artifacts, and secrets may be accidentally included in the plugin
- `MINOR` No pre-push hook found (.githooks/pre-push or git-hooks/pre-push) — recommended for quality gates
- `WARNING` No scripts/publish.py found — recommended for release automation
- `WARNING` No cliff.toml found — recommended for automated changelog generation
- `MINOR` No .github/workflows/*.yml found — recommended for CI/CD automation

### 16-hook-userpromptsubmit-http

- `MINOR` Missing recommended field 'description' in plugin.json (.claude-plugin/plugin.json)
- `MAJOR` Plugin has a manifest but no content — expected at least one of: commands/, skills/, agents/, hooks/, scripts/, .mcp.json, or .lsp.json (.claude-plugin/plugin.json)
- `MINOR` No LICENSE file found
- `MAJOR` No .gitignore file found — cache files, build artifacts, and secrets may be accidentally included in the plugin
- `MINOR` No pre-push hook found (.githooks/pre-push or git-hooks/pre-push) — recommended for quality gates
- `WARNING` No scripts/publish.py found — recommended for release automation
- `WARNING` No cliff.toml found — recommended for automated changelog generation
- `MINOR` No .github/workflows/*.yml found — recommended for CI/CD automation

### 17-hook-precompact-agent

- `MINOR` Missing recommended field 'description' in plugin.json (.claude-plugin/plugin.json)
- `MINOR` No LICENSE file found
- `MAJOR` No .gitignore file found — cache files, build artifacts, and secrets may be accidentally included in the plugin
- `MINOR` No pre-push hook found (.githooks/pre-push or git-hooks/pre-push) — recommended for quality gates
- `WARNING` No scripts/publish.py found — recommended for release automation
- `WARNING` No cliff.toml found — recommended for automated changelog generation
- `MINOR` No .github/workflows/*.yml found — recommended for CI/CD automation

### 18-agent-frontmatter-valid

- `MINOR` Missing recommended field 'description' in plugin.json (.claude-plugin/plugin.json)
- `MINOR` No LICENSE file found
- `MAJOR` No .gitignore file found — cache files, build artifacts, and secrets may be accidentally included in the plugin
- `MINOR` No pre-push hook found (.githooks/pre-push or git-hooks/pre-push) — recommended for quality gates
- `WARNING` No scripts/publish.py found — recommended for release automation
- `WARNING` No cliff.toml found — recommended for automated changelog generation
- `MINOR` No .github/workflows/*.yml found — recommended for CI/CD automation

### 19-agent-frontmatter-missing-name

- `MINOR` Missing recommended field 'description' in plugin.json (.claude-plugin/plugin.json)
- `CRITICAL` Missing 'name' in frontmatter (agents/b.md)
- `MINOR` No LICENSE file found
- `MAJOR` No .gitignore file found — cache files, build artifacts, and secrets may be accidentally included in the plugin
- `MINOR` No pre-push hook found (.githooks/pre-push or git-hooks/pre-push) — recommended for quality gates
- `WARNING` No scripts/publish.py found — recommended for release automation
- `WARNING` No cliff.toml found — recommended for automated changelog generation
- `MINOR` No .github/workflows/*.yml found — recommended for CI/CD automation

### 20-agent-frontmatter-hex-color

- `MINOR` Missing recommended field 'description' in plugin.json (.claude-plugin/plugin.json)
- `MINOR` No LICENSE file found
- `MAJOR` No .gitignore file found — cache files, build artifacts, and secrets may be accidentally included in the plugin
- `MINOR` No pre-push hook found (.githooks/pre-push or git-hooks/pre-push) — recommended for quality gates
- `WARNING` No scripts/publish.py found — recommended for release automation
- `WARNING` No cliff.toml found — recommended for automated changelog generation
- `MINOR` No .github/workflows/*.yml found — recommended for CI/CD automation

### 21-skill-valid

- `MINOR` Missing recommended field 'description' in plugin.json (.claude-plugin/plugin.json)
- `MINOR` Field 'skills' points to './skills/' which Claude Code auto-discovers anyway. This declaration is redundant. Remove the field from plugin.json (the default folder is scanned automatically). (.claude-plugin/plugin.json)
- `MAJOR` plugin.json::skills must be a list of paths (got str). CC v2.1.136+ rejects non-list values and the field overrides the default skills/ directory. (.claude-plugin/plugin.json)
- `MINOR` No LICENSE file found
- `MAJOR` No .gitignore file found — cache files, build artifacts, and secrets may be accidentally included in the plugin
- `MINOR` No pre-push hook found (.githooks/pre-push or git-hooks/pre-push) — recommended for quality gates
- `WARNING` No scripts/publish.py found — recommended for release automation
- `WARNING` No cliff.toml found — recommended for automated changelog generation
- `MINOR` No .github/workflows/*.yml found — recommended for CI/CD automation

### 22-skill-undeclared-named-arg

- `MINOR` Missing recommended field 'description' in plugin.json (.claude-plugin/plugin.json)
- `MINOR` Field 'skills' points to './skills/' which Claude Code auto-discovers anyway. This declaration is redundant. Remove the field from plugin.json (the default folder is scanned automatically). (.claude-plugin/plugin.json)
- `MAJOR` plugin.json::skills must be a list of paths (got str). CC v2.1.136+ rejects non-list values and the field overrides the default skills/ directory. (.claude-plugin/plugin.json)
- `MINOR` No LICENSE file found
- `MAJOR` No .gitignore file found — cache files, build artifacts, and secrets may be accidentally included in the plugin
- `MINOR` No pre-push hook found (.githooks/pre-push or git-hooks/pre-push) — recommended for quality gates
- `WARNING` No scripts/publish.py found — recommended for release automation
- `WARNING` No cliff.toml found — recommended for automated changelog generation
- `MINOR` No .github/workflows/*.yml found — recommended for CI/CD automation

### 23-skill-paths-shape

- `MINOR` Missing recommended field 'description' in plugin.json (.claude-plugin/plugin.json)
- `MINOR` Field 'skills' points to './skills/' which Claude Code auto-discovers anyway. This declaration is redundant. Remove the field from plugin.json (the default folder is scanned automatically). (.claude-plugin/plugin.json)
- `MAJOR` plugin.json::skills must be a list of paths (got str). CC v2.1.136+ rejects non-list values and the field overrides the default skills/ directory. (.claude-plugin/plugin.json)
- `MINOR` No LICENSE file found
- `MAJOR` No .gitignore file found — cache files, build artifacts, and secrets may be accidentally included in the plugin
- `MINOR` No pre-push hook found (.githooks/pre-push or git-hooks/pre-push) — recommended for quality gates
- `WARNING` No scripts/publish.py found — recommended for release automation
- `WARNING` No cliff.toml found — recommended for automated changelog generation
- `MINOR` No .github/workflows/*.yml found — recommended for CI/CD automation

### 24-mcpserver-stdio

- `MINOR` Missing recommended field 'description' in plugin.json (.claude-plugin/plugin.json)
- `MAJOR` Plugin has a manifest but no content — expected at least one of: commands/, skills/, agents/, hooks/, scripts/, .mcp.json, or .lsp.json (.claude-plugin/plugin.json)
- `MAJOR` Absolute path found in plugin.json:mcpServers:stdio-srv:command: /usr/bin/true - use ${{CLAUDE_PLUGIN_ROOT}} for portability
- `MINOR` No LICENSE file found
- `MAJOR` No .gitignore file found — cache files, build artifacts, and secrets may be accidentally included in the plugin
- `MINOR` No pre-push hook found (.githooks/pre-push or git-hooks/pre-push) — recommended for quality gates
- `WARNING` No scripts/publish.py found — recommended for release automation
- `WARNING` No cliff.toml found — recommended for automated changelog generation
- `MINOR` No .github/workflows/*.yml found — recommended for CI/CD automation

### 25-mcpserver-http

- `MINOR` Missing recommended field 'description' in plugin.json (.claude-plugin/plugin.json)
- `MAJOR` Plugin has a manifest but no content — expected at least one of: commands/, skills/, agents/, hooks/, scripts/, .mcp.json, or .lsp.json (.claude-plugin/plugin.json)
- `WARNING` Server http-srv connects to remote URL 'https://api.example.com/mcp' — remote MCP servers can access tool results and conversation data. Ensure the server is trusted and uses HTTPS.
- `MINOR` No LICENSE file found
- `MAJOR` No .gitignore file found — cache files, build artifacts, and secrets may be accidentally included in the plugin
- `MINOR` No pre-push hook found (.githooks/pre-push or git-hooks/pre-push) — recommended for quality gates
- `WARNING` No scripts/publish.py found — recommended for release automation
- `WARNING` No cliff.toml found — recommended for automated changelog generation
- `MINOR` No .github/workflows/*.yml found — recommended for CI/CD automation

### 26-mcpserver-no-command

- `MINOR` Missing recommended field 'description' in plugin.json (.claude-plugin/plugin.json)
- `MAJOR` Plugin has a manifest but no content — expected at least one of: commands/, skills/, agents/, hooks/, scripts/, .mcp.json, or .lsp.json (.claude-plugin/plugin.json)
- `CRITICAL` Server broken-srv missing required 'command' field
- `MINOR` No LICENSE file found
- `MAJOR` No .gitignore file found — cache files, build artifacts, and secrets may be accidentally included in the plugin
- `MINOR` No pre-push hook found (.githooks/pre-push or git-hooks/pre-push) — recommended for quality gates
- `WARNING` No scripts/publish.py found — recommended for release automation
- `WARNING` No cliff.toml found — recommended for automated changelog generation
- `MINOR` No .github/workflows/*.yml found — recommended for CI/CD automation

### 27-lspserver-basic

- `MINOR` Missing recommended field 'description' in plugin.json (.claude-plugin/plugin.json)
- `MAJOR` LSP server 'py-lsp' missing required 'extensionToLanguage' field (.claude-plugin/plugin.json)
- `MAJOR` Plugin has a manifest but no content — expected at least one of: commands/, skills/, agents/, hooks/, scripts/, .mcp.json, or .lsp.json (.claude-plugin/plugin.json)
- `MINOR` No LICENSE file found
- `MAJOR` No .gitignore file found — cache files, build artifacts, and secrets may be accidentally included in the plugin
- `MINOR` No pre-push hook found (.githooks/pre-push or git-hooks/pre-push) — recommended for quality gates
- `WARNING` No scripts/publish.py found — recommended for release automation
- `WARNING` No cliff.toml found — recommended for automated changelog generation
- `MINOR` No .github/workflows/*.yml found — recommended for CI/CD automation

### 28-lspserver-malformed

- `MINOR` Missing recommended field 'description' in plugin.json (.claude-plugin/plugin.json)
- `MAJOR` Plugin has a manifest but no content — expected at least one of: commands/, skills/, agents/, hooks/, scripts/, .mcp.json, or .lsp.json (.claude-plugin/plugin.json)
- `MINOR` No LICENSE file found
- `MAJOR` No .gitignore file found — cache files, build artifacts, and secrets may be accidentally included in the plugin
- `MINOR` No pre-push hook found (.githooks/pre-push or git-hooks/pre-push) — recommended for quality gates
- `WARNING` No scripts/publish.py found — recommended for release automation
- `WARNING` No cliff.toml found — recommended for automated changelog generation
- `MINOR` No .github/workflows/*.yml found — recommended for CI/CD automation

### 29-monitors-field

- `MINOR` Missing recommended field 'description' in plugin.json (.claude-plugin/plugin.json)
- `NIT` 'monitors' should be nested under 'experimental: { ... }' per v2.1.129. Top-level still works (claude plugin validate warns). (.claude-plugin/plugin.json)
- `MAJOR` monitors[0] must be an object (plugins-reference.md:268-318) (.claude-plugin/plugin.json)
- `MAJOR` Plugin has a manifest but no content — expected at least one of: commands/, skills/, agents/, hooks/, scripts/, .mcp.json, or .lsp.json (.claude-plugin/plugin.json)
- `MINOR` No LICENSE file found
- `MAJOR` No .gitignore file found — cache files, build artifacts, and secrets may be accidentally included in the plugin
- `MINOR` No pre-push hook found (.githooks/pre-push or git-hooks/pre-push) — recommended for quality gates
- `WARNING` No scripts/publish.py found — recommended for release automation
- `WARNING` No cliff.toml found — recommended for automated changelog generation
- `MINOR` No .github/workflows/*.yml found — recommended for CI/CD automation

### 30-outputstyle-field

- `MINOR` Missing recommended field 'description' in plugin.json (.claude-plugin/plugin.json)
- `WARNING` Unknown manifest field 'outputStyle' — not part of the Claude Code plugin spec. If used by plugin scripts, consider documenting it. (.claude-plugin/plugin.json)
- `MAJOR` Plugin has a manifest but no content — expected at least one of: commands/, skills/, agents/, hooks/, scripts/, .mcp.json, or .lsp.json (.claude-plugin/plugin.json)
- `MINOR` No LICENSE file found
- `MAJOR` No .gitignore file found — cache files, build artifacts, and secrets may be accidentally included in the plugin
- `MINOR` No pre-push hook found (.githooks/pre-push or git-hooks/pre-push) — recommended for quality gates
- `WARNING` No scripts/publish.py found — recommended for release automation
- `WARNING` No cliff.toml found — recommended for automated changelog generation
- `MINOR` No .github/workflows/*.yml found — recommended for CI/CD automation

