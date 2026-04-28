# Interrogation Playbook

## Table of Contents

- [Purpose](#purpose)
- [Target layout selection](#target-layout-selection)
- [GitHub owner and visibility (Layout A only)](#github-owner-and-visibility-layout-a-only)
- [Primary author consolidation (Layout B only)](#primary-author-consolidation-layout-b-only)
- [Per-plugin metadata](#per-plugin-metadata)
- [Guest contributor handling](#guest-contributor-handling)
- [Final confirmation](#final-confirmation)

## Checklist

- [ ] Target layout chosen (A, B, or C) via AskUserQuestion — user's words recorded
- [ ] Layout A: GitHub owner + visibility gathered
- [ ] Layout B: primary author + email consolidated
- [ ] Layout C: confirmed exactly one plugin in scope
- [ ] Per-plugin metadata gathered (category, homepage, author, license)
- [ ] Guest contributors handled (preserve or reassign)
- [ ] Final confirmation from user BEFORE any destructive migration step

## Purpose

Exact `AskUserQuestion` prompts the agent must use to gather user preferences before running any Layout A, B, or C migration. Do not invent defaults silently — every user decision is recorded in the migration log.

## Target layout selection

ALWAYS the first question. Never pick for the user. **Layout C is offered ONLY when exactly one plugin is in scope** — multiple plugins disqualify it (the audit reports plugin count; Layout C requires count == 1).

When N == 1:

```
AskUserQuestion:
  question: >
    This repo packages exactly one plugin. CPV supports three clean
    layouts. Which do you prefer?

    1. Layout A (hub-and-spoke) — give the plugin its own GitHub repo
       and host it in a SEPARATE marketplace repo (one I create or one
       you already own). Best when this plugin will eventually live
       alongside others under the same marketplace.

    2. Layout C (marketplace-in-plugin / self-referential) — keep
       everything in this single repo. Add `.claude-plugin/marketplace.json`
       (or `plugin.json`) so the repo is BOTH a plugin AND a marketplace.
       Users `claude plugin marketplace add <owner>/<repo>` then install.
       Best when this is the only plugin and you want minimum repo
       overhead. Cannot host more plugins later without migrating.

    3. Cancel — leave everything as-is and document the decision.
  options: ["Layout A", "Layout C", "Cancel"]
```

When N >= 2:

```
AskUserQuestion:
  question: >
    This marketplace does not match CPV's opinionated layouts. I can
    convert it to one of two clean architectures. Which do you prefer?

    1. Layout A (hub-and-spoke) — I will git subtree split each nested
       plugin into its own new GitHub repo (preserving per-plugin
       history), create one repo per plugin, tag each at its current
       version, and rewrite marketplace.json to use github sources.
       Best when plugins have independent release cadences or different
       owners.

    2. Layout B (nested-with-discipline) — I will keep the nested layout
       but add the missing CPV discipline: scripts/publish.py, cliff.toml,
       CI workflow running validate_plugin.py on every subfolder,
       CHANGELOG.md via git-cliff, and consolidated single-author
       authorship. No subtree surgery. Best when all plugins are
       tightly coupled and you want one atomic release.

    3. Cancel — leave everything as-is and document the decision.
  options: ["Layout A", "Layout B", "Cancel"]
```

If Cancel: write the migration log with the decision and exit.

If user chose Layout C: skip the GitHub owner / primary author consolidation / per-plugin loops below — Layout C has exactly one plugin and one author. Jump straight to the per-plugin metadata section (category, homepage, license) for the single plugin, then to final confirmation. The Layout C migration is described in `layout-c-migration.md`.

## GitHub owner and visibility (Layout A only)

```
AskUserQuestion:
  question: >
    I will create one new GitHub repository per plugin. Under which
    owner? (I will default to your `git config user.name` — "{current}")
  default: "{git_config_user_name}"
```

```
AskUserQuestion:
  question: >
    Should the new plugin repositories be public or private?
  options: ["public", "private"]
```

```
AskUserQuestion:
  question: >
    Should I push each new repo to GitHub immediately after creating it,
    or stage everything locally first so you can review before the push?
  options: ["push immediately", "stage locally first"]
```

## Primary author consolidation (Layout B only)

Only asked if the pre-migration audit detected mixed authorship.

```
AskUserQuestion:
  question: >
    I found {N} different authors across the existing plugins:
      - {author_1}
      - {author_2}
      - ...

    CPV is a single-author workflow. Who is the primary author of this
    marketplace going forward? (I will rewrite every plugin.json and
    marketplace.json `author` field to this value. Guest contributors
    will be preserved in CONTRIBUTORS.md.)
  default: "{git_config_user_name}"
```

## Per-plugin metadata

Asked once per plugin, in a loop.

For category:

```
AskUserQuestion:
  question: >
    What category should "{plugin_name}" be listed under in marketplace.json?
  options:
    - development
    - security
    - ai-ml
    - infrastructure
    - documentation
    - data
    - devops
    - testing
    - utilities
    - other
```

For homepage:

```
AskUserQuestion:
  question: >
    Homepage URL for "{plugin_name}"? (leave blank to default to
    {github_repo_url})
  default: "{github_repo_url}"
```

For license:

```
AskUserQuestion:
  question: >
    License for "{plugin_name}"?
  options: ["MIT", "Apache-2.0", "GPL-3.0", "BSD-3-Clause", "ISC", "other"]
  default: "MIT"
```

If the user picks "other" for license, a follow-up free-text question.

## Guest contributor handling

Only asked for Layout B if primary author consolidation was needed.

```
AskUserQuestion:
  question: >
    Should I create a CONTRIBUTORS.md at the marketplace root listing
    the {N} guest contributors I detected in the original files?
  options: ["yes, create CONTRIBUTORS.md", "no, drop the attributions"]
```

## Final confirmation

Before running any destructive operation:

```
AskUserQuestion:
  question: >
    Ready to execute the migration plan below:

    - Target layout: {A, B, or C}
    - Plugins to migrate: {N} ({list})
    - GitHub owner: {owner}
    - Primary author: {author}
    - Push: {immediate or staged}

    This will perform git subtree splits, create {N} repositories,
    rewrite marketplace.json, and commit to the current repo. No
    history is rewritten; all operations are forward-only commits
    or additive file creation.

    Proceed?
  options: ["Yes, migrate", "No, cancel"]
```

If No: write the log and exit cleanly.

## After each question

Record the user's answer in `$MAIN_ROOT/reports/migrate-marketplace-architecture/<YYYYMMDD_HHMMSS±HHMM>-<slug>.md` at the main-repo root (first entry of `git worktree list` — never a linked worktree) under a timestamped entry so the full decision trail is auditable.

If the user refuses to answer any required question, cancel the migration — never pick silent defaults for fields that affect git operations or public repo creation.
