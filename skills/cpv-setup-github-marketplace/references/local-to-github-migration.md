# Local → GitHub Marketplace Migration

## Table of Contents

- [Scenario](#scenario)

- [Detect the starting state](#detect-the-starting-state)
- [Four migration paths](#four-migration-paths)
- [Path 1: Lift-and-shift the whole marketplace to GitHub as Layout B](#path-1-lift-and-shift-the-whole-marketplace-to-github-as-layout-b)
- [Path 2: Split every plugin into its own repo + create a Layout A hub from the local folder](#path-2-split-every-plugin-into-its-own-repo--create-a-layout-a-hub-from-the-local-folder)
- [Path 3: Ship ONE plugin to its own GitHub repo + keep the local marketplace for dev](#path-3-ship-one-plugin-to-its-own-github-repo--keep-the-local-marketplace-for-dev)
- [Path 4: Ship ONE plugin to an EXISTING third-party GitHub marketplace](#path-4-ship-one-plugin-to-an-existing-third-party-github-marketplace)
- [Gotchas](#gotchas)
- [Post-migration verification](#post-migration-verification)
- [User instructions template](#user-instructions-template)

## Scenario

A plugin is under development inside a **local** marketplace folder (a directory with `marketplace.json` that the user registered via `claude plugin marketplace add <path>` or `--plugin-dir`). The user wants to publish it to GitHub — either alongside its whole host marketplace or by splitting it into its own repo and linking it somewhere public.

## Checklist

- [ ] Detect starting state (local marketplace, no GitHub remote, relative-path sources)
- [ ] Ask the user which of the 4 paths fits (never decide for them)
- [ ] Execute the chosen path end-to-end
- [ ] Run post-migration verification (remote validate + marketplace.json check)
- [ ] Emit final user-runnable install commands verbatim

## Detect the starting state

Before planning, the agent must confirm it's looking at a local marketplace. Markers:

1. **The plugin's parent directory (or an ancestor within 3 levels) contains `marketplace.json` or `.claude-plugin/marketplace.json`** — run `find <plugin-parent> -maxdepth 4 -name marketplace.json`. If found, that's the host marketplace.
2. **The host marketplace directory is NOT a git repo, OR is a git repo without a GitHub remote** — `git -C <host> remote -v` returns nothing, or `git -C <host> status` says "not a git repository".
3. **The plugin entries in `marketplace.json` use relative-path sources** (`"source": "./plugins/<name>"` or `"./<name>"`), not GitHub URLs.
4. **`claude plugin marketplace list` shows the marketplace with a `file://` or directory source** (if the user has registered it).

If all four markers are true, you are in a local-dev marketplace scenario and must run the migration workflow below.

## Four migration paths

Ask the user — do NOT choose for them — with `AskUserQuestion`:

> Your plugin is in a local-only marketplace. How do you want to publish it?
>
> 1. **Layout B: push the whole marketplace as one GitHub repo** — plugins stay as subfolders, one tag per release. Simplest migration. Good for tightly-coupled plugin sets maintained by one team.
> 2. **Layout A: split every plugin into its own GitHub repo, and publish the marketplace as a hub** — each plugin lives independently with its own versioning and CI. Best for independently-versioned plugins or plugins that external users may fork.
> 3. **Ship ONLY this plugin to its own GitHub repo; keep the local marketplace for dev** — the local marketplace keeps its relative-path entries, and you add a second entry that points at the GitHub repo. Good when only one plugin is "production-ready" and the rest are still being developed.
> 4. **Ship ONLY this plugin to its own GitHub repo; register it in a different, already-existing GitHub marketplace** — useful when publishing into a community hub you do not own.

## Path 1: Lift-and-shift the whole marketplace to GitHub as Layout B

**Preconditions**: user owns `<owner>/<marketplace-repo>` on GitHub (create if absent), marketplace layout is already Layout B (`./plugins/<name>` relative sources) OR the agent is authorized to migrate Layout A → Layout B via `cpv-marketplace-fixer-agent`.

**Steps**:

1. Validate every plugin: loop `validate_plugin.py --strict` over each subfolder. Fix anything non-WARNING via the `cpv-plugin-fixer-agent` agent.
2. Validate the marketplace: `validate_marketplace.py --strict`. Fix via `cpv-marketplace-fixer-agent` if needed.
3. If the marketplace dir is not yet a git repo: `git init`, set user.name/email, add a `.gitignore` that excludes `node_modules`, `.venv`, `__pycache__`, `_dev` folders.
4. Install CI/CD templates: `generate_marketplace_repo.py --layout B --in-place <dir>` (or, if --in-place is not supported, scaffold separately and merge). Ensure workflows for lint + validate + test are present.
5. Create GitHub repo: `gh repo create <owner>/<marketplace-repo> --public --source . --push`.
6. Install the Layout-B publish pipeline from `cpv-canonical-pipeline` skill (one `scripts/publish.py`, one `cliff.toml`, one `CHANGELOG.md` at marketplace root).
7. Push, tag `v0.1.0`, create first GitHub release via `scripts/publish.py --minor`.
8. **Do NOT remove the local registration** — the user may still want to dev locally. Inform them they can `claude plugin marketplace remove <local>` if they want to switch over.

**Outcome**: one GitHub repo holding everything, single tag/release/CHANGELOG. `marketplace.json` entries remain `./plugins/<name>` — they now resolve inside the checked-out repo.

## Path 2: Split every plugin into its own repo + create a Layout A hub from the local folder

**Preconditions**: user owns a GitHub org/user namespace. Each plugin subfolder will become its own repo under `<owner>/<plugin-name>`.

**Steps** (repeat steps 1–6 once per plugin):

1. For each plugin subfolder: validate (`cpv-plugin-fixer-agent` if needed), git init inside the plugin subfolder, apply `generate_plugin_repo.py` templates for CI/CD + publish pipeline.
2. Create its GitHub repo: `gh repo create <owner>/<plugin-name> --public --source <plugin-subfolder> --push`.
3. Install pre-push hooks: `uv run python scripts/publish.py --install-hook`.
4. Run first publish: `uv run python scripts/publish.py --minor` to create `v0.1.0` tag + release.
5. Apply branch-rules: `cpv-setup-branch-rules <owner>/<plugin-name>`.
6. After all plugins have GitHub repos, rewrite `marketplace.json`: replace each `"source": "./plugins/<name>"` entry with `{"source": "github", "repo": "<owner>/<plugin-name>"}`. Keep `version`, `description`, `author`, `category`, `license`, `keywords`, `homepage`.
7. Turn the marketplace folder into a Layout A hub repo: `git init` at the marketplace root, install Layout-A templates from `generate_marketplace_repo.py --layout A`, commit, `gh repo create <owner>/<marketplace-repo> --public --source . --push`.
8. Wire per-plugin auto-notify: for each plugin, install `notify-marketplace.yml` targeting the marketplace repo AND set `MARKETPLACE_PAT` via `scripts/set_marketplace_pat.py <owner>/<plugin> <owner>/<marketplace>`. This is the setup the `cpv-setup-marketplace-auto-notification` skill automates — load it for this phase.
9. Apply marketplace branch-rules: `cpv-setup-branch-rules <owner>/<marketplace-repo>`.

**Outcome**: N+1 GitHub repos (N plugins + 1 hub marketplace). Each plugin has independent versioning; the marketplace repo's `marketplace.json` is auto-updated via GitHub Actions dispatch whenever any plugin pushes.

## Path 3: Ship ONE plugin to its own GitHub repo + keep the local marketplace for dev

**Preconditions**: user wants to test production install while keeping development loop fast.

**Steps**:

1. Validate the plugin, fix to clean, apply `generate_plugin_repo.py` templates.
2. `git init` inside the plugin subfolder (if not already), commit.
3. `gh repo create <owner>/<plugin-name> --public --source <subfolder> --push`.
4. Install hooks + publish pipeline inside the plugin repo. First publish: `scripts/publish.py --minor` → `v0.1.0`.
5. **In the LOCAL marketplace's `marketplace.json`**, add a SECOND plugin entry (do not remove the relative-path one — give it a different name or mark the local one with a `-dev` suffix):
   ```json
   {
     "name": "my-plugin",
     "source": { "source": "github", "repo": "<owner>/<plugin-name>" },
     "version": "0.1.0"
   },
   {
     "name": "my-plugin-dev",
     "source": "./plugins/my-plugin",
     "version": "0.1.0-dev"
   }
   ```
6. The user can now install the stable one from GitHub via `claude plugin install my-plugin@<local-marketplace>` and still iterate on `my-plugin-dev` locally.
7. The local marketplace stays where it is — no GitHub repo for it. Remind the user it is not shareable in this state.

**Outcome**: one plugin published, one still local-only, both reachable from the same local marketplace.

## Path 4: Ship ONE plugin to an EXISTING third-party GitHub marketplace

**Preconditions**: user has write access (direct or via PR) to `<target-owner>/<target-marketplace>`. If they only have read access, the workflow becomes a pull request against that repo's `marketplace.json`.

**Steps**:

1. Validate target marketplace: `cpv-remote-validate marketplace <target-owner>/<target-marketplace> --strict`. If it has CRITICAL/MAJOR findings, warn the user — they will need the maintainer to clean it before a new plugin can be added.
2. Apply steps 1–4 from Path 3 (validate, fix, git init, gh repo create, first publish). Now the plugin is at `<owner>/<plugin-name>` on GitHub with tag `v0.1.0`.
3. Clone the target marketplace: `gh repo clone <target-owner>/<target-marketplace> /tmp/target-market`.
4. Add the plugin entry to its `marketplace.json` (or `.claude-plugin/marketplace.json` for Layout B) with full metadata gathered from the user via `AskUserQuestion`: `name`, `source={github, repo=<owner>/<plugin-name>}`, `version`, `description`, `category`, `author`, `license`, `keywords`, `homepage`.
5. Validate: `validate_marketplace.py --strict /tmp/target-market`. If clean, commit.
6. If user has write access: push directly. Otherwise: `gh pr create --title "Add <plugin-name>" --body "..."` and tell the user a PR has been opened.
7. Configure per-plugin auto-notify on THIS plugin repo targeting the target marketplace: `scripts/set_marketplace_pat.py <owner>/<plugin-name> <target-owner>/<target-marketplace>`. Requires the target maintainer to have already set up `plugin-updated` repository_dispatch receiver on their end.

**Outcome**: plugin is on GitHub, registered in a public community marketplace. The user's original local marketplace is untouched.

## Gotchas

- **Never run `git add -A`** in a directory that may contain nested `.git` folders, uncommitted test data, or cached venvs. Use explicit `git add <file1> <file2>`.
- **`gh repo create --source <dir>`** will refuse if `<dir>` has no commits yet — init + commit first.
- **Sensitive files in the local marketplace** — scan for `.env`, `credentials.json`, private PATs before pushing. If the marketplace was local-only, these may have been committed loosely. Run `trufflehog` or equivalent before the first push.
- **Large binary artifacts** (compiled outputs, downloaded models) — if the plugin bundles a binary, use `src/<component>/bin/` + GitHub release attachments, not git LFS, per CPV conventions.
- **Path 3's dual-entry requirement** — some tools may complain about duplicate plugin `name`s across entries. Use distinct names (`my-plugin` and `my-plugin-dev`) to keep them routable.
- **`marketplace.json` schema** — Layout A entries use `{"source": "github", "repo": "..."}`; Layout B entries use the bare string `"./plugins/<name>"`. The CPV validator rejects `"file"` as a per-plugin source type (settings-level only). Don't try to use `file://` URLs in marketplace.json.

## Post-migration verification

After any path, run this checklist BEFORE declaring success:

1. `cpv-remote-validate plugin <owner>/<plugin-name> --strict` → zero above-WARNING.
2. `cpv-remote-validate marketplace <owner>/<marketplace-repo> --strict` → zero above-WARNING.
3. `gh api repos/<owner>/<marketplace-repo>/contents/.claude-plugin/marketplace.json` (or `marketplace.json` for Layout A) — decoded content must list the new plugin with the correct version + source.
4. For Paths 2 and 4: trigger a dummy patch publish on the plugin repo, watch the marketplace repo's Actions tab, confirm `update-plugin-version` workflow runs green and commits back to the marketplace.
5. Emit the final user instructions (see template below).

## User instructions template

At the end of ANY migration path, emit this to the user verbatim (substitute placeholders):

```
✓ Migration complete.

Plugin repo:       https://github.com/<owner>/<plugin-name>
Marketplace repo:  https://github.com/<owner>/<marketplace-repo>   (or: <target-owner>/<target-marketplace>)
Registered as:     <plugin-name>@<marketplace-name> (version <X.Y.Z>)

To install it, run these commands yourself:

  # FIRST TIME — add the marketplace to Claude Code
  claude plugin marketplace add <owner>/<marketplace-repo>

  # Refresh to pull the latest marketplace.json
  claude plugin marketplace update <marketplace-name>

  # Install — pick ONE scope
  claude plugin install <plugin-name>@<marketplace-name> --scope user      # personal, all projects
  claude plugin install <plugin-name>@<marketplace-name> --scope project   # team, committed to this repo
  claude plugin install <plugin-name>@<marketplace-name> --scope local     # personal, this project only

  # Confirm
  claude plugin list

If you still want to run the plugin from the LOCAL marketplace for dev (Path 3 only), keep it
registered with:
  claude plugin marketplace add <local-marketplace-dir>
```

If the user chose Path 4 and could only open a PR (not merge directly), add: "Your plugin will be installable after the maintainer merges PR <URL>."
