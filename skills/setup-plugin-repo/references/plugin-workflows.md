# Plugin Repository -- GitHub Workflows

## Table of Contents

- [ci.yml -- Continuous Integration](#ciyml----continuous-integration)
- [release.yml -- GitHub Release on Tag](#releaseyml----github-release-on-tag)
- [validate.yml -- Plugin Validation](#validateyml----plugin-validation)
- [notify-marketplace.yml -- Marketplace Notification](#notify-marketplaceyml----marketplace-notification)
- [Placeholder Reference](#placeholder-reference)
- [Setup Instructions](#setup-instructions)

> For plugins with compiled binaries, see [`plugin-binary-builds.md`](plugin-binary-builds.md) for the `build-binaries.yml` cross-compilation workflow and CI build step patterns.

---

## ci.yml -- Continuous Integration

Runs on every push and PR to the main branch. Lints source files, validates the plugin structure, and runs the test suite.

```yaml
name: CI

on:
  push:
    branches: [<placeholder-for-default-branch>]
  pull_request:
    branches: [<placeholder-for-default-branch>]

jobs:
  validate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Install uv
        uses: astral-sh/setup-uv@v4

      - name: Set up Python
        run: uv python install 3.12

      - name: Install dependencies
        run: uv sync

      - name: Lint all source files (read-only)
        run: uv run python scripts/lint_files.py .

      - name: Run plugin validation
        run: uv run python scripts/validate_plugin.py .

      - name: Run tests
        run: uv run pytest tests/ -v
```

---

## release.yml -- GitHub Release on Tag

Triggered when a semver tag (`v*.*.*`) is pushed. Runs full validation, tests, linting, and type checking. If validation passes, generates a changelog from commit history and creates a GitHub Release with the validation report attached.

```yaml
name: Release

on:
  push:
    tags:
      - 'v*.*.*'

jobs:
  release:
    runs-on: ubuntu-latest
    permissions:
      contents: write
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - name: Install uv
        uses: astral-sh/setup-uv@v4

      - name: Set up Python
        run: uv python install 3.12

      - name: Install dependencies
        run: uv sync

      - name: Run full plugin validation
        run: |
          set +e
          uv run python scripts/validate_plugin.py . --verbose > validation-report.txt 2>&1
          exit_code=$?
          set -e
          cat validation-report.txt
          # Fail release on critical (1) or major (2) issues
          if [ $exit_code -le 2 ] && [ $exit_code -ge 1 ]; then
            echo "::error::Validation failed with exit code $exit_code (critical/major issues found)"
            exit $exit_code
          fi

      - name: Run tests
        run: uv run pytest tests/ -v

      - name: Lint Python scripts
        run: uv run ruff check scripts/

      - name: Type check
        run: uv run mypy scripts/ --ignore-missing-imports

      - name: Generate changelog
        id: changelog
        run: |
          # Use git-cliff if available, fall back to git log
          if command -v git-cliff &> /dev/null; then
            git-cliff --latest --strip header > changelog.txt
          elif pipx run git-cliff --latest --strip header > changelog.txt 2>/dev/null; then
            true  # pipx fallback succeeded
          else
            # Fallback: generate from git log
            PREV_TAG=$(git describe --tags --abbrev=0 HEAD^ 2>/dev/null || echo "")
            if [ -z "$PREV_TAG" ]; then
              git log --pretty=format:"- %s (%h)" HEAD > changelog.txt
            else
              git log --pretty=format:"- %s (%h)" ${PREV_TAG}..HEAD > changelog.txt
            fi
          fi
          echo "changelog_file=changelog.txt" >> $GITHUB_OUTPUT

      - name: Create GitHub Release
        uses: softprops/action-gh-release@v3
        with:
          body_path: changelog.txt
          files: |
            validation-report.txt
          generate_release_notes: true
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
```

---

## validate.yml -- Plugin Validation

Runs on every push and PR to the main branch. Discovers the validator script location (supports both in-repo and submodule layouts), lints source files, validates the plugin, and runs ruff lint checks.

```yaml
name: Plugin Validation

on:
  push:
    branches: [<placeholder-for-default-branch>]
  pull_request:
    branches: [<placeholder-for-default-branch>]

jobs:
  validate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          submodules: recursive

      - name: Install uv
        uses: astral-sh/setup-uv@v4

      - name: Set up Python
        run: uv python install 3.12

      - name: Install dependencies
        run: uv sync

      - name: Find validator
        id: find-validator
        run: |
          if [ -f "scripts/validate_plugin.py" ]; then
            echo "validator=scripts/validate_plugin.py" >> $GITHUB_OUTPUT
          elif [ -f "<placeholder-for-validation-submodule-path>/scripts/validate_plugin.py" ]; then
            echo "validator=<placeholder-for-validation-submodule-path>/scripts/validate_plugin.py" >> $GITHUB_OUTPUT
          else
            echo "validator=" >> $GITHUB_OUTPUT
          fi

      - name: Lint all source files (read-only)
        run: uv run python scripts/lint_files.py .

      - name: Validate plugin(s)
        if: steps.find-validator.outputs.validator != ''
        run: |
          set +e  # Don't exit on error - we need to capture exit code
          uv run python ${{ steps.find-validator.outputs.validator }} . --verbose
          exit_code=$?
          set -e  # Re-enable exit on error
          # Exit codes: 0=pass, 1=critical, 2=major, 3=minor
          # Strict mode: ALL non-zero exit codes block
          if [ $exit_code -eq 0 ]; then
            echo "Validation passed"
            exit 0
          else
            echo "Validation failed (exit code: $exit_code)"
            exit $exit_code
          fi

      - name: Lint Python files
        run: |
          # Run ruff check but don't fail CI - just report issues
          # The validate_plugin.py already checks lint via mypy
          uv run ruff check scripts/ --select=E,F,W --ignore=E501 || echo "Lint issues found (non-blocking)"
```

---

## notify-marketplace.yml -- Marketplace Notification

**CRITICAL for the push protocol.** Triggers a `repository_dispatch` event on your marketplace repository whenever plugin source files change on the main branch. This is how the marketplace stays in sync with individual plugin repos.

**Requires a Personal Access Token (PAT) -- see Setup Instructions below.**

```yaml
# Notify marketplace repo when this plugin is updated
# Place this in each plugin repo: .github/workflows/notify-marketplace.yml
#
# SETUP INSTRUCTIONS:
# 1. Create a Personal Access Token (PAT):
#    - Go to GitHub Settings -> Developer settings -> Personal access tokens -> Tokens (classic)
#    - Click "Generate new token (classic)"
#    - Give it a descriptive name like "Plugin Marketplace Notification"
#    - Select scope: 'repo' (Full control of private repositories)
#    - Click "Generate token" and COPY the token (you won't see it again!)
#
# 2. Add the PAT as a secret to THIS plugin repository:
#    - Go to your plugin repo -> Settings -> Secrets and variables -> Actions
#    - Click "New repository secret"
#    - Name: MARKETPLACE_PAT
#    - Value: paste the PAT you copied in step 1
#    - Click "Add secret"
#
# 3. Update the MARKETPLACE_OWNER and MARKETPLACE_REPO variables below:
#    - MARKETPLACE_OWNER: Your GitHub username or org that owns the marketplace repo
#    - MARKETPLACE_REPO: The name of your marketplace repository
#
# IMPORTANT: Do NOT use GITHUB_TOKEN - it lacks permissions to trigger workflows in other repos!

name: Notify Marketplace

on:
  push:
    branches: [<placeholder-for-default-branch>]
    paths:
      - '.claude-plugin/plugin.json'
      - 'hooks/**'
      - 'commands/**'
      - 'agents/**'
      - 'skills/**'
      - 'scripts/**'

env:
  # REQUIRED: Update these values to point to your marketplace repository
  # Example: If your marketplace is at https://github.com/johndoe/my-claude-plugins
  #   MARKETPLACE_OWNER: 'johndoe'
  #   MARKETPLACE_REPO: 'my-claude-plugins'
  MARKETPLACE_OWNER: '<placeholder-for-marketplace-owner>'
  MARKETPLACE_REPO: '<placeholder-for-marketplace-repo-name>'

jobs:
  notify:
    runs-on: ubuntu-latest
    steps:
      - name: Get plugin info
        id: plugin
        run: |
          echo "name=${{ github.event.repository.name }}" >> $GITHUB_OUTPUT
          echo "ref=${{ github.sha }}" >> $GITHUB_OUTPUT

      - name: Trigger marketplace update
        uses: peter-evans/repository-dispatch@v4
        with:
          token: ${{ secrets.MARKETPLACE_PAT }}
          repository: ${{ env.MARKETPLACE_OWNER }}/${{ env.MARKETPLACE_REPO }}
          event-type: plugin-updated
          client-payload: |
            {
              "plugin": "${{ steps.plugin.outputs.name }}",
              "ref": "${{ steps.plugin.outputs.ref }}",
              "source_repo": "${{ github.repository }}",
              "triggered_by": "${{ github.actor }}"
            }

      - name: Summary
        run: |
          echo "## Marketplace Notification" >> $GITHUB_STEP_SUMMARY
          echo "" >> $GITHUB_STEP_SUMMARY
          echo "Triggered update in ${{ env.MARKETPLACE_OWNER }}/${{ env.MARKETPLACE_REPO }}" >> $GITHUB_STEP_SUMMARY
          echo "" >> $GITHUB_STEP_SUMMARY
          echo "- Plugin: ${{ steps.plugin.outputs.name }}" >> $GITHUB_STEP_SUMMARY
          echo "- Commit: ${{ steps.plugin.outputs.ref }}" >> $GITHUB_STEP_SUMMARY
```

---

## Placeholder Reference

| Placeholder | Description | Example Value |
|---|---|---|
| `<placeholder-for-default-branch>` | Your repository's default branch name | `main` |
| `<placeholder-for-validation-submodule-path>` | Path to the CPV validation submodule (if used) | `my-validation-plugin` |
| `<placeholder-for-marketplace-owner>` | GitHub username or org that owns the marketplace repo | `my-org` |
| `<placeholder-for-marketplace-repo-name>` | Name of your marketplace repository | `my-plugins-marketplace` |

**Secrets required:**

| Secret | Used By | Description |
|---|---|---|
| `MARKETPLACE_PAT` | `notify-marketplace.yml` | Personal Access Token with `repo` scope -- needed to trigger `repository_dispatch` in the marketplace repo. **Do NOT use `GITHUB_TOKEN`** -- it cannot trigger workflows in other repos. |
| `GITHUB_TOKEN` | `release.yml` | Automatically provided by GitHub Actions -- no setup needed. Used to create releases. |

---

## Setup Instructions

1. **Create the workflows directory** in your plugin repo:
   ```bash
   mkdir -p .github/workflows
   ```

2. **Copy each workflow file** from the templates above into `.github/workflows/`:
   - `ci.yml` -- basic CI on every push/PR
   - `release.yml` -- creates GitHub Releases on version tags
   - `validate.yml` -- runs plugin validation with CPV
   - `notify-marketplace.yml` -- notifies your marketplace repo on plugin changes

3. **Replace all `<placeholder-for-...>` values** with your actual values (see Placeholder Reference above).

4. **Create the `MARKETPLACE_PAT` secret**:
   - Go to GitHub Settings -> Developer settings -> Personal access tokens -> Tokens (classic)
   - Generate a new token with `repo` scope
   - Go to your plugin repo -> Settings -> Secrets and variables -> Actions
   - Add a new secret named `MARKETPLACE_PAT` with the token value

5. **Commit and push** the workflow files:
   ```bash
   git add .github/workflows/
   git commit -m "Add GitHub Actions workflows for CI, release, validation, and marketplace notification"
   git push
   ```

6. **Verify** by checking the Actions tab in your GitHub repository -- the CI and validate workflows should trigger on the push.
