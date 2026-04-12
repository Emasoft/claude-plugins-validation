# Layout B Migration (Nested-with-Discipline)

## Table of Contents

- [Pre-Flight Checks](#pre-flight-checks)
- [Scaffold publish.py](#scaffold-publishpy)
- [Scaffold cliff.toml](#scaffold-clifftoml)
- [Scaffold validate.yml](#scaffold-validateyml)
- [Generate CHANGELOG.md](#generate-changelogmd)
- [Consolidate Authorship](#consolidate-authorship)
- [Preserve Guest Contributors](#preserve-guest-contributors)
- [Single Atomic Commit](#single-atomic-commit)
- [Tag the Marketplace](#tag-the-marketplace)
- [Verification](#verification)
- [Rollback Recipe](#rollback-recipe)

---

## Purpose

Full step-by-step procedure for adding CPV release discipline to a nested
marketplace WITHOUT splitting it into separate repos. Keeps the original
directory layout (plugins as subdirectories under `plugins/`) and adds the
missing canonical files: `scripts/publish.py`, `cliff.toml`,
`.github/workflows/validate.yml`, `CHANGELOG.md`, and optionally
`CONTRIBUTORS.md`.

Loaded by the `migrate-marketplace-architecture` skill when the user selects
Layout B in the interrogation playbook. Never proceed until the pre-migration
audit verdict is READY.

## Pre-Flight Checks

1. **Clean working tree** — every file created here becomes part of a single
   atomic commit. Dirty trees cause accidental bundling.

   ```bash
   [ -z "$(git status --porcelain)" ] \
     || { echo "BLOCKER: working tree is dirty"; exit 1; }
   ```

2. **Record current marketplace version** from
   `.claude-plugin/marketplace.json::metadata.version`. The final step tags
   the marketplace at `v<version>`.

3. **Confirm still-nested state** — the pre-migration audit must have marked
   zero `already_migrated` plugins. Layout B is meaningful only when every
   plugin lives as a subdirectory in the same repo.

4. **Default branch detection**

   ```bash
   DEFAULT_BRANCH=$(git remote show origin | awk '/HEAD branch/ {print $NF}')
   ```

## Scaffold publish.py

Copy the CPV `scripts/publish.py` template from
`generate_plugin_repo.py::PUBLISH_PY_TEMPLATE` into `scripts/publish.py`,
adapted to a marketplace root: the bump loop iterates every
`plugins/*/.claude-plugin/plugin.json` instead of bumping a single plugin.json.

Required behavior:

- `--patch` / `--minor` / `--major` flags bump every plugin in lockstep.
- Bumps `metadata.version` in `marketplace.json` to the same value.
- Runs `git-cliff --tag v<new-version> -o CHANGELOG.md`.
- Runs the full CPV validation suite against every plugin subdir.
- Stages `plugins/*/.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json`,
  and `CHANGELOG.md` only (never `-A` / `--all`).
- Commits with message `chore: release v<new-version>`.
- Tags `v<new-version>` and pushes tags.

The template in `generate_plugin_repo.py` is the source of truth — copy it
verbatim and modify only the iteration loop.

## Scaffold cliff.toml

Copy the CPV `cliff.toml` template from the claude-plugins-validation repo
root. This file drives `git-cliff` and must not be hand-written — the CPV
template is tuned to match the commit message conventions enforced by the
pre-push hook.

```bash
cp "$CLAUDE_PLUGIN_ROOT/cliff.toml" ./cliff.toml
```

No edits to the copied file. Release notes inherit the exact same format as
CPV core.

## Scaffold validate.yml

Create `.github/workflows/validate.yml` that runs on every push and pull
request. It must:

- Install `uv` and Python 3.12.
- Run `uv run ruff check .`.
- Run `uv run mypy scripts/`.
- Loop over every `plugins/<name>/` directory and run
  `uv run --with pyyaml python scripts/validate_plugin.py plugins/<name> --strict`.
- Run `uv run --with pyyaml python scripts/validate_marketplace.py . --strict`.
- Run `uv run pytest tests/ -q` if a `tests/` directory exists.
- Fail the job on any non-WARNING finding.

Example skeleton:

```yaml
name: validate
on:
  push:
    branches: [main]
  pull_request:
    branches: [main]
jobs:
  validate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v4
      - run: uv python install 3.12
      - run: uv run ruff check .
      - run: uv run mypy scripts/
      - run: |
          for d in plugins/*/; do
            uv run --with pyyaml python scripts/validate_plugin.py "$d" --strict
          done
      - run: uv run --with pyyaml python scripts/validate_marketplace.py . --strict
```

## Generate CHANGELOG.md

Initial CHANGELOG is produced with `git-cliff` using the current marketplace
version recorded during the pre-flight checks:

```bash
git-cliff --tag "v$current_version" -o CHANGELOG.md
```

This builds the file from existing commits — the marketplace's real history
becomes the foundation for every future release. Never hand-write the initial
CHANGELOG.

## Consolidate Authorship

If the pre-migration audit detected mixed authorship AND the interrogation
playbook captured a primary author, rewrite every `author` field in the repo
to that value. Use `jq` and the `sponge` pattern to avoid partial writes.

```bash
primary="$user_supplied_primary_author"
jq --arg a "$primary" '.metadata.author = $a' .claude-plugin/marketplace.json \
  > tmp.json && mv tmp.json .claude-plugin/marketplace.json
for p in plugins/*/.claude-plugin/plugin.json; do
  jq --arg a "$primary" '.author = $a' "$p" > "$p.tmp" && mv "$p.tmp" "$p"
done
```

Never rewrite any field other than `author` during this step — consolidation
is about authorship only. Category, license, homepage changes belong to the
interrogation playbook and its per-plugin questions.

## Preserve Guest Contributors

If the user opted to keep the previous author attributions, create
`CONTRIBUTORS.md` at the repo root listing the guest contributors captured
from the original `plugin.json` files.

```markdown
# Contributors

This marketplace was originally built with contributions from:

- Original Author A (plugins: alpha, beta)
- Original Author B (plugin: gamma)

Primary maintainer going forward: <primary-author>
```

If the user declined (chose "drop the attributions"), skip this file — the
decision is already recorded in the migration log.

## Single Atomic Commit

Stage ALL the new files together and commit once. Layout B's entire discipline
upgrade must land as one reviewable change, not a scattered series of commits.

```bash
git add scripts/publish.py cliff.toml .github/workflows/validate.yml \
        CHANGELOG.md .claude-plugin/marketplace.json plugins/*/.claude-plugin/plugin.json
[ -f CONTRIBUTORS.md ] && git add CONTRIBUTORS.md
git commit -m "feat: add CPV release discipline"
```

Never use `git add -A` or `git add .` — stage specific files by name to avoid
accidentally catching unrelated untracked files.

## Tag the Marketplace

Tag the commit at the current `metadata.version` captured in the pre-flight
checks. The tag itself is the release; `git-cliff` has already recorded it in
CHANGELOG.md.

```bash
git tag "v$current_version" -m "CPV discipline adopted"
git push origin "$DEFAULT_BRANCH" --tags
```

No force-push. No history rewrite. The tag is forward-only.

## Verification

Run `validate_marketplace.py --strict` against the repo and confirm zero
architecture warnings in the report:

```bash
uv run --with pyyaml python scripts/validate_marketplace.py . --strict \
  2>&1 | tee "docs_dev/layout-b-verify_$(date -u +%Y%m%d).log"
```

Also run the per-plugin validator against every subdirectory so the report
surfaces any leftover MAJOR findings in individual plugins:

```bash
for d in plugins/*/; do
  uv run --with pyyaml python scripts/validate_plugin.py "$d" --strict || break
done
```

Fix every non-WARNING finding before declaring the migration complete.

## Rollback Recipe

Layout B is fully additive, so rollback is simple:

- If the commit has not been pushed: `git reset --soft HEAD~1` (move the
  scaffolded files back to the staging area, review them, and either edit or
  `git restore --staged .` to unstage without losing content).
- If the commit has been pushed: open a revert commit (`git revert <hash>`)
  rather than force-pushing. Layout B never rewrites history.
- Never delete `CHANGELOG.md` or `cliff.toml` without the user's explicit
  approval — the user may want to keep them even if the rest of the
  discipline upgrade is rolled back.

Record every rollback decision in
`docs_dev/migration-log_<marketplace>_<date>.md` under a fresh timestamped
entry.
