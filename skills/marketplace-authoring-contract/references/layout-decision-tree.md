# Layout Decision Tree

## Table of Contents

- [The Decision Tree](#the-decision-tree)
- [Layout A — Hub and Spoke](#layout-a--hub-and-spoke)
- [Layout B — Nested Monorepo](#layout-b--nested-monorepo)
- [Layout C — Self-Marketplace-in-Plugin](#layout-c--self-marketplace-in-plugin)
- [When to Migrate Between Layouts](#when-to-migrate-between-layouts)
- [Disqualifying Conditions](#disqualifying-conditions)
- [Wizard Question Flow](#wizard-question-flow)
- [Cross References](#cross-references)

## Checklist

- [ ] Plugin count determined first (1 vs 2+)
- [ ] For 2+, repo location asked (same vs different)
- [ ] Layout pinned BEFORE drafting any marketplace.json entry
- [ ] No mixing of source types within one marketplace (e.g. some `github`, some `relative-path`)
- [ ] User confirms the layout before scaffolding fires

## The Decision Tree

```
Q1: How many plugins will live in this marketplace?
├── 1 plugin only?  → Layout C (self-marketplace-in-plugin), single self-entry, source: "./"
└── 2+ plugins
    ├── Are all plugins in the SAME GitHub repo?  → Layout B (nested monorepo), local source: "./plugins/<name>" string (or "directory" dict)
    └── Different repos                            → Layout A (hub-and-spoke), source: github / url
```

The agent walks this tree by asking the user, in order. No layout is chosen before both Q1 and (when relevant) Q2 are answered. No layout is inferred from "the directory we happen to be in" — Layout C and Layout B can look identical on disk if you only look at file presence.

## Layout A — Hub and Spoke

**Shape on disk:**
- 1 marketplace repo: `mkpl/` containing `.claude-plugin/marketplace.json` + README.
- N plugin repos: `plugin-1/`, `plugin-2/`, …, each with its own `.claude-plugin/plugin.json`.
- Marketplace entries use `source: github` (or `url` / `git`).

**Default for:** multi-plugin sets where plugins evolve independently and may have different maintainers.

**Pros:**
- Plugin-level access control (per-repo collaborators).
- Independent release cadences.
- Doctor diagnostics route to the right repo by source.

**Cons:**
- N + 1 repos to maintain.
- Auto-notification setup (CPV's `setup-marketplace-auto-notification`) needed to keep marketplace.json fresh on plugin releases.
- Cross-repo coordination for breaking changes.

**Marketplace entry example:**
```json
{
  "name": "foo-plugin",
  "source": {
    "source": "github",
    "repo": "owner/foo-plugin"
  },
  "description": "Foo plugin for Claude Code"
}
```

`source` is a NESTED object — `repo` lives INSIDE it. A flat sibling `repo` (`{"source": "github", "repo": "..."}`) is rejected MAJOR `RC-MKPL-UNKNOWN-FIELD`. See [source-shape](source-shape.md#source-github). (No `version` field — see [version-strategy](version-strategy.md).)

## Layout B — Nested Monorepo

**Shape on disk:**
- 1 repo: `mkpl/` containing `.claude-plugin/marketplace.json` + `plugins/foo/`, `plugins/bar/`, …
- Each plugin lives in `plugins/<name>/`, with its own `.claude-plugin/plugin.json`.
- Marketplace entries use a local source pointing at `./plugins/<name>` — the bare relative-path string `"source": "./plugins/<name>"`, or the nested `directory` dict `{"source": "directory", "path": "./plugins/<name>"}`. (There is no `relative-path` dict type.)

**Default for:** multi-plugin sets that share a release cadence, a single maintainer, or shared infrastructure (CI, lint config, scripts).

**Pros:**
- Single repo to maintain.
- Atomic releases across plugins (publish.py bumps all together).
- No auto-notification wiring needed — the marketplace and the plugins move together.
- Shared CI / lint / scripts.

**Cons:**
- No plugin-level access control (one repo's collaborators see everything).
- All plugins share the marketplace's git history.
- Migration to Layout A (split plugins out) is non-trivial (subtree splits).

**Marketplace entry example:**
```json
{
  "name": "foo-plugin",
  "version": "1.0.0",
  "source": "./plugins/foo-plugin"
}
```

The local source is the bare relative-path STRING `"./plugins/foo-plugin"` — there is NO `relative-path` dict type, and a top-level sibling `path` field is rejected. The equivalent nested form is the `directory` dict `{"source": "directory", "path": "./plugins/foo-plugin"}`. See [source-shape](source-shape.md#source-relative-path).

(`version` REQUIRED — local source — see [version-strategy](version-strategy.md).)

## Layout C — Self-Marketplace-in-Plugin

**Shape on disk:**
- 1 repo: contains BOTH `.claude-plugin/plugin.json` AND `.claude-plugin/marketplace.json` at root.
- Marketplace entry uses `source: "./"`.
- The marketplace's `plugins[]` has exactly ONE entry, pointing at the repo itself.

**Default for:** single-plugin repos that want install-by-marketplace (`claude plugin install plugin@mkpl`) without managing a separate marketplace repo.

**Pros:**
- One repo, zero coordination.
- publish.py atomically bumps plugin.json.version + marketplace.json.metadata.version + plugins[0].version.
- Install via `claude plugin install foo@github:owner/foo` works directly (the resolver detects marketplace.json at root).

**Cons:**
- Only works for single-plugin scenarios.
- Adding a SECOND plugin forces a migration to Layout A or B.

**Marketplace entry example:**
```json
{
  "name": "foo-plugin",
  "version": "1.0.0",
  "source": "./",
  "description": "Foo plugin for Claude Code"
}
```

The `"./"` is a literal — see [source-shape](source-shape.md) and PIT-004 in [common-pitfalls](common-pitfalls.md).

## When to Migrate Between Layouts

Layout migrations are tracked in detail in `skills/migrate-marketplace-architecture/`. The decision points:

| From | To | Trigger |
|---|---|---|
| C | A | Adding a second plugin and keeping them in separate repos |
| C | B | Adding a second plugin in the same monorepo |
| A | B | Consolidating to a single maintainer / single release cadence |
| B | A | Splitting plugins out to give each independent release cadence |
| Anything → C | (impossible if multi-plugin) | Layout C requires N=1 |

Never migrate without running CPV's pre-migration audit (the migrate-marketplace-architecture skill's first step).

## Disqualifying Conditions

Layout C is the ONLY layout with a hard disqualifier:

> Layout C requires exactly one plugin in scope. Multiple plugins → must use Layout A or Layout B.

The migrate-marketplace audit reports plugin count; offer Layout C only when count == 1.

Layout A and Layout B do not have hard disqualifiers — both accept any plugin count ≥ 1 (Layout C is just a more focused shape for the N=1 case).

## Wizard Question Flow

When the user asks "create a marketplace" via plugin-creator:

```
Question 1 (AskUserQuestion):
  "How many plugins will live in this marketplace?"
  - "One — make it Layout C (self-marketplace-in-plugin)"
  - "Two or more"

If "one": go to source-shape Layout C.

If "two or more":

Question 2 (AskUserQuestion):
  "Will all plugins live in the SAME GitHub repository, or in
  separate repositories?"
  - "Same repo — Layout B (nested monorepo)"
  - "Separate repos — Layout A (hub-and-spoke)"

Layout pinned. Proceed to per-plugin questions (name, description,
source URL) AFTER fetching upstream plugin.json for each — see
preflight-recipe.md.
```

The agent NEVER picks the layout silently. NEVER infers from the directory layout it sees. NEVER asks question 2 before question 1.

## Cross References

- [name-canonicalisation](name-canonicalisation.md) — applies to ALL layouts
- [version-strategy](version-strategy.md) — version field rules differ per layout class (local vs remote)
- [source-shape](source-shape.md) — per-source-type field allowlists
- [preflight-recipe](preflight-recipe.md) — preflight checks after layout selection
- `skills/migrate-marketplace-architecture/` — sister skill for transitioning between layouts
