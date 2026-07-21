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

- [ ] Determine the user's preference: Layout A (hub-and-spoke), Layout B (nested), or Layout C (marketplace-in-plugin)
- [ ] Verify user's choice explicitly — never assume
- [ ] Apply layout-specific template + workflows
- [ ] Populate rich metadata on every marketplace entry
- [ ] Reject git-subdir requests — CPV does not emit it
- [ ] Refactor between layouts only via `cpv-migrate-marketplace-architecture`

## Overview

CPV supports three marketplace layouts. All three are opinionated: each enforces proper release ceremony (tags, CHANGELOG, CI), single-author publishing, and one-way-to-do-it semantics. **No hybrids, no community-monorepos, no mixed layouts** — those patterns degrade discipline and trade the benefits of any single layout for the downsides of all.

The agent must be fluent in all three and follow the user's preference. If the user asks for a fourth option (mixed, hybrid, submodules, git-subdir), the agent should explain that CPV prefers a clean A, B, or C and offer to scaffold one of those instead.

| Aspect | Layout A (Hub-and-Spoke) | Layout B (Nested) | Layout C (Marketplace-in-plugin) |
|---|---|---|---|
| Plugin repos | N independent repos + 1 hub | 1 repo containing N plugins | 1 repo containing 1 plugin (and being its own marketplace) |
| Plugin count | 1+ | 2+ (1 is allowed but A or C is usually better) | EXACTLY 1 |
| Plugin tags | Per-plugin `v1.2.3` | Shared marketplace tag | Shared (one tag covers both manifests) |
| Git history | Isolated per plugin | Single history | Single history |
| CHANGELOG | Per plugin | One for the whole marketplace | Single CHANGELOG |
| marketplace.json source entry | `{"source": "github", "repo": "owner/name"}` | `"./plugins/<name>"` | `"./"` (self-reference) |
| Both manifests in same repo? | No (separate repos) | marketplace.json yes, plugin.json's in subfolders | Yes — both at repo root, version-synced |
| Best for | Many contributors, independent release cadences | Tightly-coupled plugins maintained by one author | One author shipping a single plugin without a separate hub repo |

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

ONE repository serves as BOTH a plugin AND a marketplace. The repo root contains both `.claude-plugin/plugin.json` and `.claude-plugin/marketplace.json`. The marketplace's single entry references the same directory via `source: "./"`. Tag and bump touch both manifests in one atomic commit.

```
my-plugin/                                  (SINGLE repo, both roles)
├── .claude-plugin/
│   ├── plugin.json                         — plugin manifest (the plugin's identity)
│   └── marketplace.json                    — marketplace manifest with one self-entry
├── .github/workflows/
│   ├── ci.yml                              (lint + validate + test)
│   └── release.yml                         (bumps both manifests)
├── cliff.toml
├── scripts/publish.py                      (bumps plugin.json + marketplace.json self-entry)
├── README.md
├── commands/
├── agents/
├── skills/
├── hooks/
└── ...
```

`marketplace.json` plugins[] contents (single entry, self-reference):

```json
{
  "name": "my-plugin",
  "source": "./",
  "version": "1.2.3",
  "description": "What this plugin does",
  "category": "development",
  "author": {"name": "Author Name", "email": "author@example.com"},
  "homepage": "https://github.com/owner/my-plugin",
  "license": "MIT"
}
```

Or as an object form:

```json
{
  "name": "my-plugin",
  "source": {"source": "directory", "path": "./"},
  ...
}
```

### Critical rules for Layout C

1. **Both manifests at repo root** — `.claude-plugin/plugin.json` AND `.claude-plugin/marketplace.json` MUST coexist at the same `.claude-plugin/` directory. CPV emits CRITICAL if either is missing in a Layout C scenario.
2. **`name` MUST match** between `plugin.json.name` and the self-entry's `name` field. CPV's `validate_layout_c_consistency` enforces this.
3. **`source` MUST be `"./"`** (or its object form `{"source": "directory", "path": "./"}`). Any other value is not Layout C — it is something else broken.
4. **`version` MUST be synced** in three places: `plugin.json.version`, `marketplace.json.metadata.version`, and the self-entry's `version`. The standard Layout C `publish.py` bumps all three atomically. CPV's `validate_layout_c_consistency` cross-checks the self-entry's `version` against `plugin.json.version` and emits MINOR on drift.
5. **Single tag per release** — one `vX.Y.Z` tag covers the whole repo (both manifests).
6. **Single CHANGELOG** — `git-cliff` generates one history covering both the plugin's changes and the marketplace registration.
7. **No auto-notify needed** — there is no separate marketplace repo to notify; both manifests live in this repo and are pushed together.

### When NOT to use Layout C

- **More than one plugin in scope** — Layout C is strictly single-plugin. Use B or A for multi-plugin sets.
- **The marketplace will host plugins from other authors** — Layout C requires the marketplace to be entirely the plugin's own. Use A.
- **You want the marketplace listing to be discoverable separately** — Layout C couples discovery to one specific plugin, so the marketplace shows ONLY that plugin. Use A or B if you want a curated multi-plugin catalog.

## How Claude Code updates plugins in each layout

Claude Code's marketplace refresh follows this flow for any layout:

1. User installs a marketplace: `claude plugin marketplace add <source>`
2. Claude Code clones/fetches the marketplace source and reads `.claude-plugin/marketplace.json`
3. For each plugin entry in `plugins: [...]`:
   - **Layout A**: fetch the plugin's OWN source (the `{"source": "github", ...}` reference) → read that repo's `.claude-plugin/plugin.json` → show that version
   - **Layout B**: resolve the relative `"./plugins/<name>"` inside the ALREADY-FETCHED marketplace repo → read `plugins/<name>/.claude-plugin/plugin.json` → show that version
   - **Layout C**: resolve `"./"` to the marketplace repo's own root → read the SAME repo's root `.claude-plugin/plugin.json` → show that version
4. When the user runs `/reload-plugins` or Claude Code refreshes, the same flow re-runs and picks up new versions

The key insight: **in all three layouts, Claude Code ultimately reads `plugin.json` to get the plugin's version.** In Layout A it finds that file in the plugin's own repo; in Layout B it finds it inside a subdirectory of the marketplace repo; in Layout C it finds it at the marketplace repo's own root. All three work — they differ only in WHERE that `plugin.json` lives relative to the marketplace.

**For version updates to propagate**:
- **Layout A**: push a new tag on the plugin's OWN repo → Claude Code refreshes the marketplace → for the changed plugin, it re-fetches the plugin repo → picks up the new `plugin.json` version
- **Layout B**: edit `plugins/<name>/plugin.json`, push a new tag on the MARKETPLACE repo → Claude Code refreshes the marketplace → re-reads `plugins/<name>/plugin.json` → picks up the new version
- **Layout C**: edit BOTH `plugin.json` and `marketplace.json` self-entry version (the standard publish.py does this atomically), push a new tag on the SINGLE repo → Claude Code refreshes the marketplace → re-reads the root `plugin.json` → picks up the new version

In Layout B and C, the marketplace repo's tag is what triggers Claude Code's refresh, but the actual plugin version shown to users is the one in `plugin.json` (Layout B: nested subfolder; Layout C: repo root).

## When to choose which

**Choose Layout A (hub-and-spoke) when:**
- Multiple contributors who maintain different plugins independently
- Plugins have very different release cadences
- You want each plugin to have its own GitHub issue tracker, PRs, stars, etc.
- You want `git blame` and `git log` to stay per-plugin
- You want a curated multi-plugin catalog under one marketplace name

**Choose Layout B (nested single-repo) when:**
- A single author/team maintains all plugins together
- Plugins are tightly coupled (share scripts, templates, references)
- You want one atomic release that touches multiple plugins at once
- You want one pull request to update many plugins in lock-step
- Simpler onboarding: `git clone` one repo and you have everything

**Choose Layout C (marketplace-in-plugin) when:**
- You are publishing exactly ONE plugin and don't have other plugins to ship alongside it
- You want minimum repo overhead: no separate marketplace repo, no notify chain, no `MARKETPLACE_PAT`
- You want users to do `claude plugin marketplace add <owner>/<repo>` then `claude plugin install <name>@<repo>` against the SAME repo
- You're OK with a future migration to Layout A or B if you ever need to add a second plugin

**Default suggestions by the agent:**
- N == 1 plugin to ship → suggest **Layout C** first, then Layout A as the alternative.
- N >= 2 plugins to ship → suggest **Layout A** first (independent release cadences), then Layout B.
- The user has a strong preference → follow it without argument.

## Rich metadata fields (author, homepage, license, category)

CPV `marketplace.json` entries should carry rich per-plugin metadata even though Claude Code does not require them. They make browsing the catalog much better and let users pick plugins without cloning each one.

Recommended per-plugin fields in `marketplace.json`:

| Field | Type | Purpose |
|---|---|---|
| `name` | string | Required. Plugin install key. Layout C: MUST equal `plugin.json.name`. |
| `source` | string or object | Required. Layout A: `{"source": "github", "repo": "..."}`. Layout B: `"./plugins/<name>"`. Layout C: `"./"` or `{"source": "directory", "path": "./"}`. |
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

- **When the plugin is the whole repo and there's a separate marketplace** → use Layout A with `{"source": "github", "repo": "owner/name"}`. Gives independent versions, tags, releases, CI, and a clean git history. Strictly better than git-subdir.
- **When the plugin is the whole repo and serves as its OWN marketplace** → use Layout C with `"./"`. One repo, two manifests, no separate marketplace.
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

### From Layout A → Layout C (collapsing one plugin into self-marketplace)

Only valid when the marketplace contains exactly ONE plugin AND that plugin is owned by the same author as the marketplace.

1. In the plugin repo, add `.claude-plugin/marketplace.json` mirroring the plugin's identity (see `cpv-migrate-marketplace-architecture` → `layout-c-migration.md`).
2. Add the self-entry with `source: "./"`, name + version matching `plugin.json`.
3. Update the plugin's publish.py to bump both manifests atomically.
4. Archive (do not delete) the old marketplace repo, or keep it as a redirect README.
5. Tell users to switch their `claude plugin marketplace add` command to point at the plugin repo instead.

### From Layout C → Layout A (when the plugin grows companions)

Required when a Layout C repo wants to ship a SECOND plugin (Layout C is single-plugin only).

1. Create a new marketplace repo (`gh repo create <owner>/<plugin-name>-marketplace`).
2. Move the marketplace.json into it with both plugins as github sources.
3. Strip the marketplace.json from the original plugin repo (it is now Layout A's spoke).
4. Set up the auto-notify chain (`cpv-setup-marketplace-auto-notification`).
5. Tell users to switch their `claude plugin marketplace add` to point at the new marketplace repo.

### From Layout B → Layout C (collapsing a single-plugin monorepo)

Valid only when Layout B contains exactly ONE plugin under `plugins/<name>/`.

1. Move the plugin contents from `plugins/<name>/` to the repo root (`git mv`).
2. Rewrite the marketplace.json self-entry to `source: "./"`.
3. Verify name + version sync between root `plugin.json` and the self-entry.
4. Commit, tag, push.

## Agent behavior summary

| Scenario | Agent action |
|---|---|
| User asks to create a new marketplace, multiple plugins | Suggest Layout A (hub-and-spoke) first. If user prefers Layout B, scaffold the nested structure without argument. |
| User asks to publish a single plugin | Suggest Layout C (marketplace-in-plugin) first — minimum repo overhead. Layout A is the alternative if the user expects more plugins to follow. |
| User works on an existing Layout A marketplace | Follow Layout A conventions. No refactor prompts. |
| User works on an existing Layout B marketplace | Follow Layout B conventions. Proactively ask ONCE whether they want to migrate to Layout A, explain tradeoffs, and respect the answer. |
| User works on an existing Layout C repo | Follow Layout C conventions. Maintain version sync between both manifests in every commit. Reject attempts to add a SECOND plugin (suggest migration to A or B). |
| User asks to add a plugin to a Layout A marketplace | Use `--link-plugin` with a `github` or `url` source entry. |
| User asks to add a plugin to a Layout B marketplace | Create the subfolder under `plugins/<name>`, populate `plugin.json`, add the local-path source entry to `marketplace.json`. |
| User asks to add a SECOND plugin to a Layout C repo | Refuse — Layout C is single-plugin. Offer migration to Layout A (split into separate plugin repo + new marketplace hub) or Layout B (turn the existing repo into a multi-plugin monorepo). |
| Validation | CPV validators accept all three layouts. `validate_marketplace.py` recognizes relative-path sources including `"./"`; `validate_plugin.py` works on any plugin root. CPV's `validate_layout_c_consistency` cross-checks name and version sync when both manifests coexist. |

**The agent must be fluent in all three layouts and must follow the user's preference.**
