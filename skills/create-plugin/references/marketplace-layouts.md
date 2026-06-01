# Marketplace Layouts — Three Supported Architectures

## Table of Contents

- [Overview](#overview)
- [Layout A — Hub-and-Spoke (separate repos)](#layout-a--hub-and-spoke-separate-repos)
- [Layout B — Nested single-repo (monorepo)](#layout-b--nested-single-repo-monorepo)
- [Layout C — Marketplace-in-plugin (self-referential single repo)](#layout-c--marketplace-in-plugin-self-referential-single-repo)
- [How Claude Code updates plugins in each layout](#how-claude-code-updates-plugins-in-each-layout)
- [When to choose which](#when-to-choose-which)
- [Rich metadata fields (author, homepage, license, category)](#rich-metadata-fields-author-homepage-license-category)
- [Why CPV does not use git-subdir](#why-cpv-does-not-use-git-subdir)
- [Encountering a non-CPV marketplace](#encountering-a-non-cpv-marketplace)
- [Refactoring between layouts](#refactoring-between-layouts)
- [Agent behavior summary](#agent-behavior-summary)

## Checklist

- [ ] Pick a layout: A (hub-and-spoke) for independently-versioned plugins, B (nested) for monorepos, C (marketplace-in-plugin) for a single self-referential repo
- [ ] Verify the choice with the user — never unilateral
- [ ] Apply layout-specific template (separate plugin repos + hub OR nested subdirs OR self-referential)
- [ ] Populate rich metadata (author, homepage, license, category) in every marketplace entry
- [ ] Reject git-subdir requests — CPV does not emit it
- [ ] For refactor between layouts, load `migrate-marketplace-architecture` skill
- [ ] **Layout C only**: ensure plugin.json's `name` appears in marketplace.json's `plugins[]` (self-reference)

## Overview

CPV supports three marketplace layouts. All three enforce proper release ceremony (tags, CHANGELOG, CI), single-author publishing, and one-way-to-do-it semantics within each layout.

**Layouts A and B are the canonical separated forms.** Layout C is a legitimate "marketplace-in-plugin" pattern where the same root directory serves both roles — a single repo that *is* a plugin AND also publishes a marketplace listing itself (and optionally siblings) as installable plugins. Useful when you want to ship one self-contained extension that participates in marketplace discovery without splitting into two repos.

**Avoid:** mixed/community-monorepos that don't fit any of A/B/C, git-subdir, or split definitions where the plugin and marketplace manifests disagree about the plugin's identity.

The agent must be fluent in all three and follow the user's preference. If the user asks for a fourth option (submodules, git-subdir, etc.), the agent should explain that CPV prefers a clean A, B, or C and offer to scaffold one of those instead.

| Aspect | Layout A (Hub-and-Spoke) | Layout B (Nested) |
|---|---|---|
| Plugin repos | N independent repos | 1 repo containing N plugins |
| Plugin tags | Per-plugin `v1.2.3` | Shared marketplace tag |
| Git history | Isolated per plugin | Single history |
| CHANGELOG | Per plugin | One for the whole marketplace |
| marketplace.json source entry | `{"source": "github", "repo": "owner/name"}` | `"./plugins/<name>"` |
| Best for | Many contributors, independent release cadences | Tightly-coupled plugins maintained by one author |

## Layout A — Hub-and-Spoke (separate repos)

Each plugin lives in its own repository (or local folder with its own git). The marketplace repo only holds `marketplace.json` + CI workflows and references each plugin via a `github` or `url` source.

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

## Layout C — Marketplace-in-plugin (self-referential single repo)

A single repository that **is itself a plugin** AND **also publishes a marketplace** listing that same plugin (and optionally sibling plugins or external dependencies). The same root directory holds both manifests; the marketplace's `plugins[]` array contains an entry whose `name` matches the plugin's own `name` and whose `source` is a relative `./` reference.

This is the right shape when you want a single self-contained extension to participate in marketplace discovery without splitting into two repositories. Common use cases:

- A team utility plugin that other teams should install via `claude plugin marketplace add <repo>` followed by `claude plugin install <name>@<marketplace>` — same flow as third-party plugins, no "first install the plugin, then add the marketplace" two-step.
- An internal organisation extension that wants to declare additional sibling plugins in its own marketplace (e.g. `core` plugin + `extras` plugin shipped together).
- A demo/showcase repository where the plugin itself serves as living documentation for how to register a marketplace.

```
my-extension/                          (SINGLE repo — plugin + marketplace)
├── .claude-plugin/
│   ├── plugin.json                    — defines THIS repo as a plugin
│   └── marketplace.json               — declares `my-extension` (same name) as one of its plugins
├── agents/
├── commands/
├── hooks/
├── skills/
├── .github/workflows/validate.yml     — runs both validate_plugin AND validate_marketplace
├── README.md
└── CHANGELOG.md
```

`marketplace.json` self-reference (the plugin's own entry):

```json
{
  "name": "my-extension",
  "owner": {"name": "Author Name", "email": "..."},
  "plugins": [
    {
      "name": "my-extension",
      "source": "./",
      "description": "...",
      "version": "1.0.0",
      "category": "...",
      "tags": ["..."]
    }
  ]
}
```

`plugin.json` (no special marker — looks like a standard plugin manifest):

```json
{
  "name": "my-extension",
  "version": "1.0.0",
  "description": "...",
  "author": {"name": "Author Name"}
}
```

### Critical rules for Layout C

1. **Manifests MUST agree on the plugin name.** `plugin.json` `name` and the matching marketplace.json `plugins[].name` MUST be identical. Any drift breaks `claude plugin install <name>@<marketplace>` resolution. CPV cross-validates this.
2. **Marketplace `plugins[].source` for the self-reference MUST be `"./"`** (relative-path source pointing at the same root). Other source types (github, url) would create a duplicate clone of the same repo at install time.
3. **Versioning is unified** — bump `plugin.json.version` AND keep `marketplace.json.plugins[<self>].version` in sync (CPV warns when they diverge). One git tag covers both manifests.
4. **CI runs both validators**: `validate_plugin.py .` AND `validate_marketplace.py .` against the same root. The shared validate.yml workflow must succeed for both.
5. **Sibling plugins (optional)** — if the marketplace also lists other plugins (different name from the repo), each sibling needs its own subdirectory with its own `.claude-plugin/plugin.json`. That hybrid is essentially Layout B + a self-reference. Use Layout B with no self-reference unless you genuinely need the marketplace-in-plugin semantic.
6. **Single CHANGELOG** at the repo root covers both surfaces.
7. **`plugin.json` and `marketplace.json` MUST have matching `version` fields** when both declare one. Checked by CPV cross-validation.

### When NOT to use Layout C

- Multiple plugins, different release cadences → use Layout A.
- Multiple plugins, single release cadence, no self-publishing requirement → use Layout B.
- Only Layout C when the *primary* shape is a single plugin that wants to be marketplace-installable from its own repo.

## How Claude Code updates plugins in each layout

Claude Code's marketplace refresh follows this flow for any layout:

1. User installs a marketplace: `claude plugin marketplace add <source>`
2. Claude Code clones/fetches the marketplace source and reads `.claude-plugin/marketplace.json`
3. For each plugin entry in `plugins: [...]`:
   - **Layout A**: fetch the plugin's OWN source (the `{"source": "github", ...}` reference) → read that repo's `.claude-plugin/plugin.json` → show that version
   - **Layout B**: resolve the relative `"./plugins/<name>"` inside the ALREADY-FETCHED marketplace repo → read `plugins/<name>/.claude-plugin/plugin.json` → show that version
   - **Layout C**: resolve the self-reference `"./"` against the ALREADY-FETCHED repo root → read that root's `.claude-plugin/plugin.json` → show that version (same directory-resolution path as Layout B, just pointing at the repo root instead of a subfolder)
4. When the user runs `/reload-plugins` or Claude Code refreshes, the same flow re-runs and picks up new versions

The key insight: **in all three layouts, Claude Code ultimately reads `plugin.json` to get the plugin's version.** In Layout A it finds that file in the plugin's own repo; in Layout B it finds it inside a subdirectory of the marketplace repo; in Layout C it finds it at the marketplace repo's own root. All three work.

**For version updates to propagate**:
- **Layout A**: push a new tag on the plugin's OWN repo → Claude Code refreshes the marketplace → for the changed plugin, it re-fetches the plugin repo → picks up the new `plugin.json` version
- **Layout B**: edit `plugins/<name>/plugin.json`, push a new tag on the MARKETPLACE repo → Claude Code refreshes the marketplace → re-reads `plugins/<name>/plugin.json` → picks up the new version
- **Layout C**: edit the root `.claude-plugin/plugin.json` (and keep `marketplace.json.plugins[<self>].version` in sync), push a new tag on the repo → Claude Code refreshes → re-reads the root `plugin.json` → picks up the new version

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

**Choose Layout C (marketplace-in-plugin) when:**
- The *primary* shape is a single self-contained plugin that should also be marketplace-installable from its own repo (no separate hub repo)
- You want the same `claude plugin marketplace add <repo>` + `claude plugin install <name>@<marketplace>` flow third-party plugins use, without a two-step setup
- See the "When NOT to use Layout C" subsection above for the cases that should fall back to A or B instead

**The agent defaults to suggesting Layout A** (hub-and-spoke) when creating a new marketplace, because it's the more scalable pattern for multi-author projects. But if the user explicitly prefers Layout B, the agent must support it fully without argument.

## Rich metadata fields (author, homepage, license, category)

CPV `marketplace.json` entries should carry rich per-plugin metadata even though Claude Code does not require them. They make browsing the catalog much better and let users pick plugins without cloning each one.

Recommended per-plugin fields in `marketplace.json`:

| Field | Type | Purpose |
|---|---|---|
| `name` | string | Required. Plugin install key. |
| `source` | string or object | Required. Layout A: `{"source": "github", "repo": "..."}`. Layout B: `"./plugins/<name>"`. |
| `description` | string | One-line summary shown in catalog listings. |
| `version` | string | Semver. For Layout B this should mirror the nested `plugin.json` version. |
| `author` | object | `{"name": "...", "email": "...", "url": "..."}`. CAN differ from marketplace owner when a plugin is the work of a contributor — CPV is a single-author workflow, but a user may credit guest contributors. |
| `homepage` | string | Plugin-specific URL (docs, marketing page, tutorial). |
| `license` | string | SPDX identifier (MIT, Apache-2.0, etc.). Can differ from marketplace license per-plugin. |
| `category` | string | Free-text tag for grouping. CPV does not enforce a taxonomy, but consistent categories (`development`, `security`, `ai-ml`, `infrastructure`, `documentation`, etc.) make listings browsable. |

### Agent behavior — interrogate the user for metadata

When creating or standardizing a marketplace, the agent MUST use `AskUserQuestion` to gather these fields for each plugin rather than leaving them blank. Example interaction flow:

1. For each plugin being added, ask: "What category should `<plugin-name>` be listed under? (examples: development, security, ai-ml, infrastructure, documentation, data, devops, other)"
2. Ask: "What is the plugin's homepage URL? (leave blank to default to the GitHub repo URL)"
3. Ask: "Is this plugin authored by you, or by a guest contributor? If guest, give me their name + email/URL."
4. Ask: "What license applies to this plugin? (MIT, Apache-2.0, GPL-3.0, or other)"

These answers are injected into the `marketplace.json` entry before validation.

Do NOT invent values silently. If the user declines to specify a field, the agent may omit it — but the agent must have asked.

## Why CPV does not use git-subdir

Claude Code supports a `git-subdir` source type that pulls a plugin from a subdirectory of an external repository. CPV's validators accept it (it is a valid Claude Code type) but the creation and standardization workflows never emit it, and the agent should explain why when asked:

- **When the plugin is the whole repo** → use Layout A with `{"source": "github", "repo": "owner/name"}`. Gives independent versions, tags, releases, CI, and a clean git history. Strictly better than git-subdir.
- **When the plugin belongs with sibling plugins** → use Layout B with `"./plugins/<name>"`. The marketplace owns the code and tags the repo as a whole. Strictly better than git-subdir.
- **git-subdir is only useful when you do NOT control the plugin's home repo** but want to expose one of its subdirectories as a plugin. That is never CPV's use case — CPV's user always publishes their own plugins, so they always control the layout of the source repo.

The downsides of git-subdir:

1. No version isolation — the plugin subdirectory's version is not reflected in any tag you can pin to.
2. No CI isolation — a commit to the upstream repo that breaks the plugin subdirectory will not be caught by any CPV pipeline.
3. No rollback — if a new commit breaks things, you can't `git revert` just the subdirectory.
4. Forces Claude Code to shallow-clone a foreign repo and extract only one directory, which is slow and fragile.
5. Creates an awkward middle-ground between Layout A and Layout B that complicates the mental model.

**Rule**: if a user asks the agent to add a plugin via git-subdir, the agent should push back and ask whether the plugin could be moved to its own repo (Layout A) or copied into the marketplace under `plugins/<name>/` (Layout B). Only proceed with git-subdir if the user insists and the plugin genuinely lives in a foreign repo they do not control.

## Encountering a non-CPV marketplace

If the agent is asked to validate or standardize a marketplace that does not follow CPV's layouts — for example, a community monorepo with per-plugin independent version cadences, no git tags, no CHANGELOG, no CI, and mixed authorship (the wshobson/agents pattern) — the agent should:

1. Run the normal validators and report the findings.
2. Then emit an explicit RECOMMENDATION that the user migrate the repo to one of CPV's two clean layouts. Present the tradeoffs:
   - **Migrate to Layout A**: `git subtree split --prefix=plugins/<name>` for each plugin, one `gh repo create` per plugin, one independent tag per plugin, then rewrite `marketplace.json` entries to use github sources. Gives each plugin its own versions, tags, releases, CI, rollback, and independent git history.
   - **Migrate to Layout B**: keep the nested layout but add the missing pieces — CI workflows that run `validate_plugin.py` on every subfolder, a root `scripts/publish.py` + `cliff.toml` that tags the marketplace repo atomically, a `CHANGELOG.md` at root, and the rich per-plugin metadata (`author`, `homepage`, `license`, `category`) in every `marketplace.json` entry.
3. If the user declines, validate what they have and document the gaps explicitly in the report so they understand the risks.

CPV's two layouts are better than the "structurally-B-but-missing-everything" pattern because they enforce release ceremony, atomic rollback, automated CI, and clear single-author accountability. The agent should be confident in this recommendation rather than humble.

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
