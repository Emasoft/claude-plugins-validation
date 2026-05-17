# TRDD-c50531c2 — Menu unification: single user-facing command

**TRDD ID:** `c50531c2-1bc0-4a94-96e2-e2f63f85833c`
**Filename:** `design/tasks/TRDD-c50531c2-menu-unification.md`
**Tracked in:** this repo (design/tasks/ is git-tracked)
**Status:** In progress
**Target release:** v2.90.0 (BREAKING)
**Owner:** main session
**Created:** 2026-05-17

## User request (verbatim)

> simplify the whole menu system. unify the menus of validation, fixes,
> upgrade, doctor, creation, etc. Only one single command:
> cpv-main-menu . No other commands with menus. audit, verify, test,
> then commit and publish.

Follow-up clarifications:

> just to be clear: the user can only see the cpv-main-menu, but this
> does not mean that the other skills cannot be kept as user-invocable:
> false. You can still have many skills to divide the sub menus, if you
> need to, but the user must not be overwhelmed. only one menu, from
> which the user can do everything.
>
> the slash commands must not be deleted but converted in skills that
> have 'user-invocable: false'. unless they are redundant, of course.
>
> the ones with a menu must be either become part of the main menu or
> become skills invoked by the agents without menus.
>
> there are currently 3 main-menus de-facto... those must be merged into
> one main-menu
>
> one is called main-menu.. another is called doctor.. another has a
> name that i don't remember.. those are all root menus with options
> not available in the other 2..  instead we need to unify everything
> into one menu...
>
> the main menu top categories must be:
> 1. Validate
> 2. Fix
> 3. Optimize for Cache
> 4. Diagnose
> 5. Update
> 6. Create
> 7. Publish & Migrate
> 8. Manage

## Design

**One user-visible slash command:** `/cpv-main-menu`. Every other
slash command is deleted (when redundant with an existing skill) or
converted to a `user-invocable: false` skill (when its content is
unique). Skills do **not** render their own menus — the only menu is
in `cpv-main-menu-skill`. Skills that need yes/no confirmation may
still use `AskUserQuestion` for bounded (≤4 option) prompts.

### Architecture

```
/cpv-main-menu  (the only command)
       ↓
cpv-main-menu-agent  (haiku, renders the menu via cpv-format-menu skill)
       ↓
cpv-main-menu-skill  (routes the user's pick to the right work surface)
       ↓
work skills (user-invocable: false)  +  work agents (Agent tool dispatch)
       ↓
python scripts/  (the actual validators / fixers / etc.)
```

## Audit summary (`/tmp/cpv-cmd-skill-map.md`)

37 user-facing commands beyond `cpv-main-menu`. The classification:

| Bucket | Count |
|---|---:|
| **DELETE-REDUNDANT** (content already covered by an existing skill) | 23 |
| **CONVERT-TO-SKILL** (content unique → new `user-invocable: false` skill) | 14 |

### DELETE-REDUNDANT (23)

All 10 menu-bearing commands are in this bucket. Their menu logic is
already present in `cpv-main-menu-skill/references/menu-tree.md` (§3.1
through §3.17). Their work logic is in the corresponding work-skill.

| Command | Covered by |
|---|---|
| cpv-validate-plugin | plugin-validation-skill |
| cpv-validate-skill | skill-validation-skill |
| cpv-validate-local-scope | plugin-validation-skill |
| cpv-validate-project-scope | plugin-validation-skill |
| cpv-validate-cache | cache-validation-skill |
| cpv-validate-github-plugin | plugin-validation-skill |
| cpv-validate-github-marketplace | plugin-validation-skill |
| cpv-validate-settings-marketplace | plugin-validation-skill |
| cpv-validate-telemetry | plugin-validation-skill |
| cpv-fix-validation | fix-validation |
| cpv-fix-marketplace-validation | fix-marketplace-validation |
| cpv-semantic-validation | semantic-validation-skill |
| cpv-create | create-plugin |
| cpv-list-plugins | plugin-management |
| cpv-manage | plugin-management |
| cpv-diagnose-plugin | plugin-management |
| cpv-validate | cpv-main-menu-skill (menu) |
| cpv-doctor | cpv-main-menu-skill (menu) |
| cpv-cache-optimize | cpv-main-menu-skill (menu) |
| cpv-upgrade-plugin | standardize-plugin |
| cpv-setup-branch-rules | setup-plugin-repo |
| cpv-setup-branch-rules-generic | setup-plugin-repo |
| cpv-migrate-marketplace | migrate-marketplace-architecture |

### CONVERT-TO-SKILL (14)

| Command | New skill |
|---|---|
| cpv-add-component | add-component-to-plugin |
| cpv-add-dependency | add-dependency |
| cpv-bump-version | bump-version |
| cpv-codemod | deterministic-codemod |
| cpv-create-agent | scaffold-agent |
| cpv-create-command | scaffold-command |
| cpv-create-hook | add-hook |
| cpv-create-mcp | register-mcp |
| cpv-create-skill | scaffold-skill |
| cpv-link-plugin | link-plugin-marketplace |
| cpv-pack-components | pack-components |
| cpv-refresh-readme | refresh-readme |
| cpv-strip-dev-parts | strip-dev-submodules |
| cpv-version | show-version |

Each new skill keeps the command body verbatim, replaces the command
frontmatter (`description:`, `argument-hint:`, `allowed-tools:`) with
skill frontmatter (`description:`, `when_to_use:`, `user-invocable:
false`, `allowed-tools:`), and is referenced from the appropriate
`cpv-main-menu-skill` leaf recipe.

## Verification

Acceptance criteria for v2.90.0:

- [ ] `ls commands/*.md | wc -l` returns exactly **1** (`cpv-main-menu.md`)
- [ ] `ls skills/*/SKILL.md | wc -l` returns **32** (18 existing + 14 new)
- [ ] Every `skills/*/SKILL.md` has `user-invocable: false`
- [ ] `python3 scripts/validate_plugin.py .` returns 0/0/0/0
- [ ] `uv run pytest -n auto` is green
- [ ] `uv run ruff check .` is clean

## Migration for end users

Old:                                          New:
```
/cpv-validate-plugin ~/Code/foo   →   /cpv-main-menu → 1 (Validate) → 1 (Plugin) → ~/Code/foo
/cpv-fix-validation report.md     →   /cpv-main-menu → 3 (Fix) → 1 (Fix plugin) → report.md
/cpv-doctor                       →   /cpv-main-menu → 6 (Diagnose & Upgrade)
... etc                           →   /cpv-main-menu → <category> → <leaf>
```

Per the user: "the user must not be overwhelmed. only one menu, from
which the user can do everything."
