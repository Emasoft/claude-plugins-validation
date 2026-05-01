---
name: cpv-validate-plugin
description: Run full validation on a Claude Code plugin
allowed-tools: Read, Bash, Glob, Grep, AskUserQuestion
argument-hint: "<plugin_path_or_name> [--verbose] [--json]"
user-invocable: true
---

# /cpv-validate-plugin Command

Validates a complete Claude Code plugin directory with all components.

## Privacy Check (REQUIRED)

Before running validation, you MUST check for private path detection:

1. **Auto-detect username**:
   ```bash
   uv run python -c "import getpass; print(getpass.getuser())"
   ```

2. **If auto-detection fails**, use `AskUserQuestion` to ask the user:
   > "To detect accidental private path leaks, what is your system username?"

3. **Pass username to validation script** via environment variable:
   ```bash
   CLAUDE_PRIVATE_USERNAMES="username" uv run python scripts/validate_plugin.py ...
   ```

## Usage

```
/cpv-validate-plugin <plugin_path> [options]
```

## Arguments

| Argument | Required | Description |
|----------|----------|-------------|
| `plugin_path_or_name` | Yes | Path to the plugin directory OR just the plugin name for auto-discovery |

### Auto-Discovery

If you provide just a name (e.g., `my-plugin`), the agent will search for it in:
1. Current directory (`./my-plugin/`)
2. OUTPUT_SKILLS folder (`./OUTPUT_SKILLS/my-plugin/`)
3. Local plugins (`./.claude/plugins/my-plugin/`)
4. Global plugins (`~/.claude/plugins/my-plugin/`)
5. Plugin cache (`~/.claude/plugins/cache/*/my-plugin/`)

If multiple matches are found, you'll be asked to choose.

### Typo Tolerance

Names are normalized before searching:
- Converted to lowercase: `My-Plugin` → `my-plugin`
- Underscores become hyphens: `my_plugin` → `my-plugin`

If no exact match is found, fuzzy matching is used (e.g., `cpt-validate` → `cpv-validate`).
**Fuzzy matches always require your confirmation before proceeding.**

## Options

| Option | Description |
|--------|-------------|
| `--strict` | Treat NIT issues as blocking (exit 4) |
| `--verbose` | Show all checks including passed; expands the report-file body |
| `--json` | Output results as JSON (skips the auto-saved report file) |
| `--report PATH` | Write the aggregated report to PATH explicitly |
| `--marketplace-only` | Skip plugin.json requirement for marketplace-only repos |
| `--skip-platform-checks [PLATFORM...]` | Skip platform-specific checks (e.g., `--skip-platform-checks windows`) |

> **Default output is path-only.** Without `--json` or `--report`, `validate_security.py` auto-saves the aggregated report to `$CLAUDE_PROJECT_DIR/reports/security/<TS>-<plugin>.md` and prints **only** the compact summary (counts table + verdict + plugin path + report path). Findings are bucketed by `(level, rule_id)` so each vulnerability TYPE shows its full explanation exactly once with a count and capped file:line list — token-bounded reports, no findings ever silently dropped.
>
> **External scanners always run.** Six external scanners run on every invocation: cc-audit, tirith, trufflehog, gitleaks, semgrep, and the Cisco AI Defense skill-scanner (programmatic-only, no API-key engines). There are no `--no-*` opt-out flags. Each scanner self-skips with an INFO advisory if its source binary cannot be resolved. Env knobs: `CPV_NO_TIRITH_INSTALL=1` disables tirith's auto-install fallback; `CPV_CISCO_SCAN_TIMEOUT_S=<seconds>` overrides the 600s Cisco-scan default. See the project README for the full scanner inventory with source-URL links.

## What Gets Validated

### 1. Plugin Manifest (`.claude-plugin/plugin.json`)
- Required field: `name`
- Recommended fields: `version`, `description`
- Name format validation (kebab-case, lowercase)
- Version format (semver)
- agents array validation (must be array of .md file paths)

### 2. Hooks Configuration (`hooks/hooks.json`)
- JSON structure validation
- Valid event names (PreToolUse, PostToolUse, Stop, etc.)
- Matcher pattern validation (regex syntax)
- Command/prompt hook validation
- Script existence and executable checks
- Script linting (shellcheck, ruff, mypy)

### 3. Skills (All skills in `skills/` directory)
- Uses comprehensive validator (190+ rules)
- Nixtla strict mode enabled
- Auto-enables 8+1 Pillars for lang-*/convert-* skills
- Validates frontmatter, structure, content quality

### 4. Agents (All `.md` files in `agents/`)
- YAML frontmatter validation
- Required and known fields
- Name format and description quality
- Tool and model validation

### 5. MCP Servers (`.mcp.json` or inline `mcpServers`)
- Transport type validation (stdio, http, sse)
- Required fields per transport type
- Path and environment variable validation
- Security checks (hardcoded credentials)

### 6. Scripts (All scripts in `scripts/`)
- Shebang validation
- Executable permission check
- Python linting (ruff check)
- Python type checking (mypy)
- Bash linting (shellcheck)

### 7. Directory Structure
- Required: `.claude-plugin/` with `plugin.json`
- Optional: `commands/`, `agents/`, `skills/`, `hooks/`, `scripts/`
- README.md and LICENSE presence

## Examples

### Basic Plugin Validation

```
/cpv-validate-plugin ./my-plugin/
```

### Verbose Output

```
/cpv-validate-plugin ./my-plugin/ --verbose
```

### JSON for CI/CD

```
/cpv-validate-plugin ./my-plugin/ --json
```

## Output

With `--report`, the full detailed output is saved to file and only a compact summary is printed:
```
Plugin Validation: PASS
  CRITICAL:0 | MAJOR:0 | MINOR:2 | PASSED:155
  Report: reports/validate_plugin/20260421_183012+0200-my-plugin.md
```

## Exit Codes

| Code | Meaning |
|------|---------|
| 0 | All checks passed |
| 1 | CRITICAL issues (plugin won't work) |
| 2 | MAJOR issues (significant problems) |
| 3 | MINOR issues (may affect UX) |
| 4 | NIT issues found (only in --strict mode) |

## Severity Levels

| Severity | Behavior |
|----------|----------|
| CRITICAL | Always blocks (exit 1) |
| MAJOR | Always blocks (exit 2) |
| MINOR | Always blocks (exit 3) |
| NIT | Blocks only in `--strict` mode (exit 4) |
| WARNING | Never blocks, always reported (security advisories, best practices) |

## Execution

> **Report location (mandatory):** every report lands at `$MAIN_ROOT/reports/validate_plugin/<YYYYMMDD_HHMMSS±HHMM>-<slug>.md`. `$MAIN_ROOT` is the **main-repo root** — never a linked worktree's own path. Per-component subfolder + local-time+GMT-offset timestamp. Both `reports/` and `reports_dev/` are gitignored (reports routinely contain private data: absolute paths, source snippets, validation output).

Canonical shell prologue (resolves the main-repo root correctly from any worktree):

```bash
if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  MAIN_ROOT="$(git worktree list | head -n1 | awk '{print $1}')"
else
  MAIN_ROOT="${CLAUDE_PROJECT_DIR:-$PWD}"
fi
REPORT_DIR="$MAIN_ROOT/reports/validate_plugin"
mkdir -p "$REPORT_DIR"
REPORT_FILE="$REPORT_DIR/$(date +%Y%m%d_%H%M%S%z)-$(basename "$PLUGIN_PATH").md"
```

When running from the CPV plugin directory (has pyproject.toml with pyyaml):
```bash
uv run python scripts/validate_plugin.py "$PLUGIN_PATH" $OPTIONS --report "$REPORT_FILE"
```

When running from another plugin's directory (no pyproject.toml), use `--with` to provide pyyaml:
```bash
uv run --with pyyaml python scripts/validate_plugin.py . $OPTIONS --report "$REPORT_FILE"
```

## Related Commands

- `/cpv-validate` — Interactive validation (agent asks what to validate)
- `/cpv-validate-skill` — Single skill validation
- `/cpv-fix-validation` — Fix issues from a validation report
- `/cpv-semantic-validation` — Deep semantic analysis (uses opus)
