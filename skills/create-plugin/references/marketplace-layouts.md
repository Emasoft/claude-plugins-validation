# Marketplace Layouts — Two Supported Architectures

## Table of Contents

- [Overview](#overview)
- [Layout A — Hub-and-Spoke (separate repos)](#layout-a--hub-and-spoke-separate-repos)
- [Layout B — Nested single-repo (monorepo)](#layout-b--nested-single-repo-monorepo)
- [How Claude Code updates plugins in each layout](#how-claude-code-updates-plugins-in-each-layout)
- [When to choose which](#when-to-choose-which)
- [Refactoring between layouts](#refactoring-between-layouts)
- [Agent behavior summary](#agent-behavior-summary)

## Overview

CPV supports two marketplace layouts. Both make plugins installable and updatable by Claude Code. The agent must be fluent in both and must follow the user's preference.

| Aspect | Layout A (Hub-and-Spoke) | Layout B (Nested) |
|---|---|---|
| Plugin repos | N independent repos | 1 repo containing N plugins |
| Plugin tags | Per-plugin `v1.2.3` | Shared marketplace tag |
| Git history | Isolated per plugin | Single history |
| CHANGELOG | Per plugin | One for the whole marketplace |
| marketplace.json source entry | `{"source": "github", "repo": "owner/name"}` | `"./plugins/<name>"` |
| Best for | Many contributors, independent release cadences | Tightly-coupled plugins maintained by one author |

## Layout A — Hub-and-Spoke (separate repos)

Each plugin lives in its own repository (or local folder with its own git). The marketplace repo only holds `marketplace.json` + CI workflows and references each plugin via a `github` / `url` / `git-subdir` source.

```
my-marketplace/                       (HUB repo)
├── .claude-plugin/
│   └── marketplace.json              — references plugins by source
├── .github/workflows/validate.yml
├── README.md
└── scripts/sync_versions.py          (optional)

my-plugin-a/                          (independent repo, own tags)
├── .claude-plugin/plugin.json
├── skills/
├── git-cliff CHANGELOG.md
└── ...

my-plugin-b/                          (independent repo, own tags)
├── .claude-plugin/plugin.json
└── ...
```

`marketplace.json` entry format:

```json
{
  "name": "my-plugin-a",
  "source": {"source": "github", "repo": "my-org/my-plugin-a"}
}
```

**Canonical example**: `Emasoft/emasoft-plugins`.

## Layout B — Nested single-repo (monorepo)

All plugins live as subdirectories inside the marketplace repository. The whole repo is bumped/tagged as one unit.

```
my-marketplace/                       (SINGLE repo)
├── .claude-plugin/
│   └── marketplace.json              — references local paths
├── .github/workflows/
│   ├── validate.yml
│   └── release.yml                   (bumps the whole repo)
├── cliff.toml                        (one CHANGELOG for all plugins)
├── scripts/publish.py
├── README.md
└── plugins/
    ├── my-plugin-a/                  (just a subfolder)
    │   ├── .claude-plugin/plugin.json
    │   └── skills/
    └── my-plugin-b/
        ├── .claude-plugin/plugin.json
        └── ...
```

`marketplace.json` entry format (relative path from marketplace repo root):

```json
{
  "name": "my-plugin-a",
  "source": "./plugins/my-plugin-a"
}
```

Or equivalently as an object:

```json
{
  "name": "my-plugin-a",
  "source": {"source": "directory", "path": "./plugins/my-plugin-a"}
}
```

### Critical rules for Layout B

1. **Each subfolder MUST have its own `.claude-plugin/plugin.json`** with a `version` field. Claude Code reads this file to determine the plugin version shown to users.
2. **Bumping a plugin** means editing BOTH the root-level `marketplace.json` metadata (optional, if you track versions there) AND the per-plugin `plugin.json` inside the subfolder. The repo is then tagged as a whole.
3. **Validate every subfolder** with `validate_plugin.py <plugins/my-plugin-a>` before committing.
4. **Shared CI**: one workflow in `.github/workflows/` runs `validate_plugin.py` against every subfolder. A failure in one plugin fails the whole repo.
5. **Shared publish pipeline**: `scripts/publish.py` at the repo root bumps the repo version, then iterates over each `plugins/*/plugin.json` to update any plugins that changed since the last tag.

## How Claude Code updates plugins in each layout

Claude Code's marketplace refresh follows this flow for any layout:

1. User installs a marketplace: `claude plugin marketplace add <source>`
2. Claude Code clones/fetches the marketplace source and reads `.claude-plugin/marketplace.json`
3. For each plugin entry in `plugins: [...]`:
   - **Layout A**: fetch the plugin's OWN source (the `{"source": "github", ...}` reference) → read that repo's `.claude-plugin/plugin.json` → show that version
   - **Layout B**: resolve the relative `"./plugins/<name>"` inside the ALREADY-FETCHED marketplace repo → read `plugins/<name>/.claude-plugin/plugin.json` → show that version
4. When the user runs `/reload-plugins` or Claude Code refreshes, the same flow re-runs and picks up new versions

The key insight: **in both layouts, Claude Code ultimately reads `plugin.json` to get the plugin's version.** In Layout A it finds that file in the plugin's own repo; in Layout B it finds it inside a subdirectory of the marketplace repo. Both work.

**For version updates to propagate**:
- **Layout A**: push a new tag on the plugin's OWN repo → Claude Code refreshes the marketplace → for the changed plugin, it re-fetches the plugin repo → picks up the new `plugin.json` version
- **Layout B**: edit `plugins/<name>/plugin.json`, push a new tag on the MARKETPLACE repo → Claude Code refreshes the marketplace → re-reads `plugins/<name>/plugin.json` → picks up the new version

In Layout B, the marketplace repo's tag and version are what triggers Claude Code's refresh, but the actual plugin version shown to users is the one in the nested `plugin.json`.

## When to choose which

**Choose Layout A (hub-and-spoke) when:**
- Multiple contributors who maintain different plugins independently
- Plugins have very different release cadences
- You want each plugin to have its own GitHub issue tracker, PRs, stars, etc.
- You want `git blame` and `git log` to stay per-plugin

**Choose Layout B (nested single-repo) when:**
- A single author/team maintains all plugins together
- Plugins are tightly coupled (share scripts, templates, references)
- You want one atomic release that touches multiple plugins at once
- You want one pull request to update many plugins in lock-step
- Simpler onboarding: `git clone` one repo and you have everything

**The agent defaults to suggesting Layout A** (hub-and-spoke) when creating a new marketplace, because it's the more scalable pattern for multi-author projects. But if the user explicitly prefers Layout B, the agent must support it fully without argument.

## Refactoring between layouts

### From Layout B → Layout A (splitting a monorepo)

When the user works on a Layout B marketplace and decides to migrate to Layout A:

1. Scan: `find <mkt-root>/plugins -name plugin.json`
2. For each nested plugin:
   ```bash
   git subtree split --prefix=plugins/<name> -b extract-<name>
   gh repo create <owner>/<name> --public
   git push <owner>/<name> extract-<name>:main
   cd /tmp && git clone <owner>/<name> && cd <name>
   git tag v$(jq -r .version .claude-plugin/plugin.json)
   git push --tags
   ```
3. Remove the nested plugin from the marketplace repo:
   ```bash
   git rm -rf plugins/<name>
   ```
4. Rewrite the marketplace.json entry:
   ```json
   {"name": "<name>", "source": {"source": "github", "repo": "<owner>/<name>"}}
   ```
5. Commit, tag, push the marketplace repo.

### From Layout A → Layout B (merging into a monorepo)

Rarely recommended, but supported:

1. Add each plugin as a subtree under `plugins/`:
   ```bash
   git subtree add --prefix=plugins/<name> <plugin-repo-url> main
   ```
2. Update `marketplace.json` entries to use relative paths `"./plugins/<name>"`
3. Archive the old plugin repos (or keep them in read-only mode)
4. Bump and tag the marketplace repo.

## Agent behavior summary

| Scenario | Agent action |
|---|---|
| User asks to create a new marketplace | Suggest Layout A (hub-and-spoke) first. If user prefers Layout B, scaffold the nested structure without argument. |
| User works on an existing Layout A marketplace | Follow Layout A conventions. No refactor prompts. |
| User works on an existing Layout B marketplace | Follow Layout B conventions. Proactively ask ONCE whether they want to migrate to Layout A, explain tradeoffs, and respect the answer. |
| User asks to add a plugin to a Layout A marketplace | Use `--link-plugin` with a `github` or `url` source entry. |
| User asks to add a plugin to a Layout B marketplace | Create the subfolder under `plugins/<name>`, populate `plugin.json`, add the local-path source entry to `marketplace.json`. |
| Validation | CPV validators accept both layouts. `validate_marketplace.py` recognizes relative-path sources; `validate_plugin.py` works on any plugin root regardless of where it lives. |

**The agent must be fluent in both layouts and must follow the user's preference.**
