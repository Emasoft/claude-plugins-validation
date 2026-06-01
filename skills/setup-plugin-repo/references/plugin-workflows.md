# Plugin Repository -- GitHub Workflows

## Table of Contents

- [ci.yml -- Consolidated CI (lint + validate + test)](#ciyml----consolidated-ci-lint--validate--test)
- [release.yml -- GitHub Release on Tag](#releaseyml----github-release-on-tag)
- [notify-marketplace.yml -- Marketplace Notification](#notify-marketplaceyml----marketplace-notification)
- [Placeholder Reference](#placeholder-reference)
- [Setup Instructions](#setup-instructions)

> For plugins with compiled binaries, see [`plugin-binary-builds.md`](plugin-binary-builds.md) for the `build-binaries.yml` cross-compilation workflow and CI build step patterns.
>
> **v2.12.32 consolidation**: the old separate `validate.yml` was merged into `ci.yml` as three parallel jobs (`lint`, `validate`, `test`). GitHub reports their check-run names as bare `Lint`, `Validate`, `Test` (the job's `name:` field — **not** `workflow / job` format) and those are what `cpv-setup-branch-rules` enforces on the default branch.

## Checklist

- [ ] Copy `ci.yml` into `.github/workflows/` (with lint + validate + test jobs)
- [ ] Copy `release.yml` for tag-triggered releases
- [ ] Copy `notify-marketplace.yml` if publishing to a marketplace
- [ ] Replace all `<placeholder-for-*>` tokens
- [ ] Apply branch-rules via `cpv-setup-branch-rules <owner>/<repo>`

---

## ci.yml -- Consolidated CI (lint + validate + test)

Runs on every push/PR to the default branch and on merge-queue events. Three parallel jobs each emit their own required status check context, which `cpv-setup-branch-rules` then requires on PRs.

```yaml
name: CI

on:
  push:
    branches: [<placeholder-for-default-branch>]
  pull_request:
    branches: [<placeholder-for-default-branch>]
  merge_group:

permissions:
  contents: read

concurrency:
  group: ${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: true

jobs:
  lint:
    name: Lint
    runs-on: ubuntu-latest
    permissions:
      contents: read
      issues: write
      pull-requests: write
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - name: Mega-Linter
        uses: oxsecurity/megalinter@v8
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
          VALIDATE_ALL_CODEBASE: false

  validate:
    name: Validate
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
        # Plain `uv sync` — the pyproject.toml template keeps dev tooling in
        # [project].dependencies, so there is no `dev` extra. `--extra dev` here
        # would abort CI with "Extra `dev` is not defined". Add it back only if
        # you split the tooling into [project.optional-dependencies.dev].
        run: uv sync

      - name: Run plugin validation (remote CPV, --strict)
        # Fetches CPV from GitHub via uvx so downstream plugins do not need
        # to vendor scripts/validate_plugin.py. Matches publish.py's local
        # gate so CI and local gate agree. Issue #11.
        run: |
          set +e
          uvx --from git+https://github.com/Emasoft/claude-plugins-validation \
              --with pyyaml \
              cpv-remote-validate plugin . --strict
          exit_code=$?
          set -e
          if [ $exit_code -eq 0 ]; then
            echo "Validation passed"
            exit 0
          elif [ $exit_code -ge 5 ]; then
            echo "Only WARNING-level findings (exit $exit_code) — advisory, not blocking"
            exit 0
          else
            echo "::error::Validation failed (exit $exit_code: CRITICAL/MAJOR/MINOR/NIT)"
            exit $exit_code
          fi

  test:
    name: Test
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Install uv
        uses: astral-sh/setup-uv@v4

      - name: Set up Python
        run: uv python install 3.12

      - name: Install dependencies
        # Plain `uv sync` — the pyproject.toml template keeps dev tooling in
        # [project].dependencies, so there is no `dev` extra. `--extra dev` here
        # would abort CI with "Extra `dev` is not defined". Add it back only if
        # you split the tooling into [project.optional-dependencies.dev].
        run: uv sync

      - name: Run tests
        run: |
          if [ -d "tests" ] && ls tests/test_*.py 1>/dev/null 2>&1; then
            uv run pytest tests/ -v
          else
            echo "No test files found, skipping"
          fi
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

      - name: Run full plugin validation (remote CPV, --strict)
        # Fetches CPV from GitHub — downstream plugins don't vendor the validator.
        # --strict blocks on CRITICAL(1)/MAJOR(2)/MINOR(3)/NIT(4); WARNING(5+) advisory.
        run: |
          set +e
          uvx --from git+https://github.com/Emasoft/claude-plugins-validation \
              --with pyyaml \
              cpv-remote-validate plugin . --strict \
              > validation-report.txt 2>&1
          exit_code=$?
          set -e
          cat validation-report.txt
          if [ $exit_code -ge 1 ] && [ $exit_code -le 4 ]; then
            echo "::error::Validation failed with exit code $exit_code (CRITICAL/MAJOR/MINOR/NIT)"
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
   - `ci.yml` -- consolidated CI on every push/PR: `lint`, `validate`, `test` jobs
   - `release.yml` -- creates GitHub Releases on version tags
   - `notify-marketplace.yml` -- notifies your marketplace repo on plugin changes

   > **Note**: there is no separate `validate.yml` anymore (removed in v2.12.32). The old validate job is now the `validate` job inside `ci.yml`, and its GitHub check-run name is **`Validate`** (the bare `jobs.validate.name:` field — NOT `CI / Validate`). See `cpv-setup-branch-rules` command for enforcing this check as a required status on PRs.

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
