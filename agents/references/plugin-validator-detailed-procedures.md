# Plugin Validator - Detailed Procedures

This reference contains detailed verification checklists, CI procedures, and troubleshooting guides for the plugin-validator agent.

## Table of Contents

1. [Auto-Detection and Auto-Installation](#auto-detection-and-auto-installation-behavior)
2. [Verification Checklists](#verification-checklists)
3. [GitHub CI Verification](#github-ci-verification)
4. [Complete Validation Checklist](#complete-validation-checklist)
5. [Troubleshooting Guide](#troubleshooting-guide)
6. [Advanced Examples](#advanced-examples)

---

## AUTO-DETECTION AND AUTO-INSTALLATION BEHAVIOR

### Language Detection Process

The pre-push hook automatically detects languages by:
1. Scanning for language-specific files (*.py, *.js, *.ts, *.sh, *.go, *.rs, *.md, *.json, *.yaml)
2. Only installing/running linters for detected languages
3. Skipping languages not present in the project

### Auto-Installation Matrix (DETAILED)

| Language | Linter | Installation Command | Config Files |
|----------|--------|---------------------|--------------|
| Python | ruff | `pip install ruff` or `uv pip install ruff` | pyproject.toml, ruff.toml |
| Python | mypy | `pip install mypy` | mypy.ini, pyproject.toml |
| JavaScript | eslint | `npm install eslint` (local) | .eslintrc, eslint.config.js |
| TypeScript | eslint | `npm install eslint @typescript-eslint/parser` | eslint.config.js |
| Shell | shellcheck | `brew install shellcheck` (macOS) | (none) |
| Go | gofmt/go vet | (built-in with Go) | go.mod |
| Rust | cargo fmt/clippy | (built-in with Rust) | Cargo.toml |
| Markdown | markdownlint | `npm install markdownlint-cli` | .markdownlint.json |
| JSON | prettier | `npm install prettier` | .prettierrc |
| YAML | yamllint | `pip install yamllint` | .yamllint.yaml |

---

## VERIFICATION CHECKLISTS

### Verification Checklist: Auto-Detection

```
□ A.1 Python detection: Look for "Detected: Python" in output
□ A.2 JavaScript detection: Look for "Detected: JavaScript" in output
□ A.3 TypeScript detection: Look for "Detected: TypeScript" in output
□ A.4 Shell detection: Look for "Detected: Shell" in output
□ A.5 Go detection: Look for "Detected: Go" in output
□ A.6 Rust detection: Look for "Detected: Rust" in output
□ A.7 Markdown detection: Look for "Detected: Markdown" in output
□ A.8 JSON detection: Always runs (validate_plugin.py uses JSON)
□ A.9 YAML detection: Look for "Detected: YAML" in output
```

### Verification Checklist: Auto-Installation

```
□ B.1 Check for "Installing missing linters..." message
□ B.2 Python: Verify ruff installed: which ruff
□ B.3 Python: Verify mypy installed: which mypy
□ B.4 JavaScript: Check for node_modules/.bin/eslint
□ B.5 Shell: Verify shellcheck installed: which shellcheck
□ B.6 Markdown: Check for node_modules/.bin/markdownlint or global
□ B.7 YAML: Verify yamllint installed: which yamllint
```

### Verification Checklist: Lint Execution

```
□ C.1 Python lint verification:
      □ ruff check --fix runs (auto-fixes what it can)
      □ ruff format runs (after check)
      □ mypy runs with --ignore-missing-imports
      □ Check for unresolved issues in output

□ C.2 JavaScript lint verification:
      □ eslint --fix runs (if config exists)
      □ Check for .eslintrc, .eslintrc.js, eslint.config.js

□ C.3 Shell lint verification:
      □ shellcheck -x runs on each .sh file
      □ Issues reported but don't auto-fix

□ C.4 Go lint verification:
      □ gofmt -w runs (auto-fixes formatting)
      □ go vet runs (reports issues)

□ C.5 Rust lint verification:
      □ cargo fmt runs (auto-fixes formatting)
      □ cargo clippy runs (reports issues)

□ C.6 Markdown lint verification:
      □ markdownlint --fix runs (auto-fixes formatting issues)
      □ markdownlint runs again to verify (reports remaining issues)

□ C.7 JSON lint verification:
      □ Python json.load() validates syntax (always runs)
      □ prettier --write --parser json runs (if prettier available)

□ C.8 YAML lint verification:
      □ yamllint -d relaxed --format parsable runs
      □ Errors ([ERROR]) block push, warnings ([WARNING]) don't
```

### Verification Checklist: Auto-Fix Loop

```
□ D.1 Check iteration counter: "--- Iteration 1/5 ---"
□ D.2 Verify file modification detection: "Files modified by auto-fix, committing..."
□ D.3 Verify auto-commit: "chore: Auto-fix lint/format issues (iteration N)"
□ D.4 Check loop restart: "Restarting validation cycle..."
□ D.5 Final outcome must be one of:
      □ "✔ VALIDATION PASSED - Push allowed"
      □ "✘ LINT ISSUES CANNOT BE AUTO-FIXED - Push blocked"
      □ "✘ VALIDATION FAILED - Push blocked"
      □ "✘ MAX ITERATIONS REACHED (5) - Push blocked"
```

---

## GITHUB CI VERIFICATION

### Step 1: List Recent Workflow Runs

```bash
gh run list --repo OWNER/REPO --limit 10
gh run list --repo OWNER/REPO --workflow validate.yml --limit 5
```

### Step 2: Check Run Status

```bash
gh run view RUN_ID --repo OWNER/REPO
gh run view RUN_ID --repo OWNER/REPO --log
gh run view RUN_ID --repo OWNER/REPO --log-failed
```

### Step 3: Analyze Failures

```bash
gh run download RUN_ID --repo OWNER/REPO --dir ./ci-logs
gh run view RUN_ID --repo OWNER/REPO --job JOB_ID --log
```

### Step 4: Re-run Failed Workflows

```bash
gh run rerun RUN_ID --repo OWNER/REPO --failed
gh run rerun RUN_ID --repo OWNER/REPO
```

### Expected CI Workflow Structure

```yaml
name: Plugin Validation
on:
  push:
    branches: [main, master]
  pull_request:
    branches: [main, master]

jobs:
  validate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          submodules: recursive
      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'
      - run: pip install ruff mypy pyyaml types-PyYAML
      - run: python scripts/validate_plugin.py . --verbose
```

---

## COMPLETE VALIDATION CHECKLIST

### Phase 1: Pre-Flight Checks
```
□ 1.1 Navigate to project root
□ 1.2 Verify git repository exists: ls -la .git/
□ 1.3 Check for .claude-plugin/plugin.json: cat .claude-plugin/plugin.json
```

### Phase 2: Plugin Structure Validation
```
□ 2.1 Run validator: uv run python scripts/validate_plugin.py . --verbose
□ 2.2 Verify exit code 0 (or 3 for MINOR-only)
□ 2.3 Check for CRITICAL issues (must fix)
□ 2.4 Check for MAJOR issues (should fix)
□ 2.5 Review MINOR warnings
```

### Phase 3: Hook Configuration Validation
```
□ 3.1 Check hooks/hooks.json exists and is valid JSON
□ 3.2 Verify event types are valid
□ 3.3 Check script paths use ${CLAUDE_PLUGIN_ROOT}
□ 3.4 Verify scripts are executable
```

### Phase 4: Git Hooks Installation
```
□ 4.1 Run: bash scripts/setup_git_hooks.sh
□ 4.2 Verify .git/hooks/pre-commit exists and is executable
□ 4.3 Verify .git/hooks/pre-push exists and is executable
□ 4.4 For submodules: check .git/modules/*/hooks/
```

### Phase 5: CI/CD Pipeline Validation
```
□ 5.1 Check .github/workflows/ directory exists
□ 5.2 Verify workflow YAML files are valid
□ 5.3 Check for validate step in workflows
□ 5.4 Verify submodules: recursive checkout
```

### Phase 6: Linter Configuration
```
□ 6.1 Check pyproject.toml for ruff config
□ 6.2 Verify line-length settings (88 for format, 120 for check)
□ 6.3 Check mypy configuration
□ 6.4 Verify .gitignore excludes cache files
```

### Phase 7: GitHub CI Verification
```
□ 7.1 Check recent workflow runs: gh run list --limit 5
□ 7.2 Verify last run passed
□ 7.3 If failed, analyze logs
□ 7.4 Re-run if needed
```

### Phase 8: End-to-End Test
```
□ 8.1 Make a test change to a file
□ 8.2 Stage and commit: git add -A && git commit -m "test: Pipeline test"
□ 8.3 Attempt push: git push
□ 8.4 Verify pre-push hook runs
□ 8.5 Verify push succeeds or blocks appropriately
□ 8.6 Check GitHub Actions run triggered
□ 8.7 Verify CI passes
```

---

## TROUBLESHOOTING GUIDE

### Pre-Push Hook Issues

**Issue: "Push blocked but I can't see why"**
```bash
python3 .git/hooks/pre-push
uv run python scripts/validate_plugin.py . --verbose
```

**Issue: "Unfixable lint issues remain"**
```bash
ruff check . --select=E,F,W
# Fix manually, then retry
git add -A && git commit -m "fix: Manual lint fixes" && git push
```

**Issue: "Type errors found"**
```bash
mypy --ignore-missing-imports .
# Add type annotations: issues: list[tuple[str, str]] = []
```

**Issue: "Max iterations reached"**
```bash
git push --no-verify  # Bypass (use with caution!)
ruff check . --fix && ruff format . && mypy .
git add -A && git commit -m "fix: Manual fixes" && git push
```

### Git Hook Installation Issues

**Issue: "Hooks not firing"**
```bash
chmod +x .git/hooks/pre-commit .git/hooks/pre-push
```

**Issue: "Hooks exist but have old content"**
```bash
rm .git/hooks/pre-commit .git/hooks/pre-push
uv run python scripts/setup_plugin_pipeline.py . --fix
```

### GitHub CI Issues

**Issue: "CI passes but local push blocked"**
```bash
# Update CI workflow to match local validator
cp templates/github-workflows/validate-marketplace.yml .github/workflows/validate.yml
```

---

## ADVANCED EXAMPLES

### Example: Full Marketplace Pipeline Validation

```bash
# Validate plugin with focus on pipeline integrity
uv run python scripts/validate_marketplace_pipeline.py /path/to/marketplace --verbose

# Output interpretation:
# ✓ Marketplace structure valid
# ✓ update-submodules.yml workflow present and correct
# ✓ Plugin A: notify-marketplace.yml present
# ✓ Plugin B: notify-marketplace.yml present
# ✗ Plugin C: Missing notify-marketplace.yml workflow
#
# Total Score: 97/100 (Grade: A)
```

### Example: Setup Pipeline for New Marketplace

```bash
uv run python scripts/validate_marketplace_pipeline.py /path/to/marketplace --verbose
uv run python scripts/setup_marketplace_automation.py /path/to/marketplace --setup-all
# Test with small change and push
```

### Example: Add New Plugin to Existing Marketplace

```bash
git submodule add https://github.com/OWNER/new-plugin.git new-plugin
# Update marketplace.json
mkdir -p new-plugin/.github/workflows
cp templates/github-workflows/notify-marketplace.yml new-plugin/.github/workflows/
gh secret set MARKETPLACE_PAT --repo OWNER/new-plugin
git add .gitmodules new-plugin .claude-plugin/marketplace.json
git commit -m "feat: Add new-plugin to marketplace"
git push
uv run python scripts/validate_marketplace_pipeline.py . --verbose
```
