---
name: the-skills-menu-create
description: Migrate any Claude Code plugin to the-skills-menu method — agents declare only one preloaded skill (the-skills-menu) and pick operational skills dynamically via the Skill() tool instead of preloading static lists. Accepts a local plugin path, a Git URL, an owner/repo slug, a "plugin-in-marketplace" expression, or a bare plugin name to search. Works on ANY plugin, not just CPV.
argument-hint: "<plugin-path-or-url-or-name> [--full-cleanup] [--force-dirty]"
user-invocable: true
---

# /the-skills-menu-create — universal skill-discovery migrator

Convert a Claude Code plugin from static `skills: [...]` frontmatter
preloads to **the-skills-menu method**. After the migration runs,
every agent in the target plugin has only `the-skills-menu` in its
preload list and picks operational skills dynamically via the
`Skill()` tool.

This command is **plugin-agnostic** — it migrates any Claude Code
plugin you point it at (a local path, a Git URL, a marketplace + plugin
name expression, or a bare plugin name to search). The migration source
of truth is the
[`the-skills-menu-create`](../skills/the-skills-menu-create/SKILL.md)
skill bundled in this plugin; the full canonical spec lives at
[`the-skills-menu-spec.md`](../skills/the-skills-menu-create/references/the-skills-menu-spec.md).

## Step 0 — Resolve the target

The argument can be any of:

| # | Form | Example |
|---|------|---------|
| 1 | Explicit Git URL | `https://github.com/<owner>/<repo>.git` |
| 2 | Owner/repo slug | `<owner>/<repo>` |
| 3 | Local filesystem path | `~/code/my-plugin/`, `/abs/path/to/plugin` |
| 4 | Plugin-in-marketplace expression | `from the my-plugin in github.com/<owner>/<marketplace>` |
| 5 | Bare plugin name | `my-plugin` (searches workspace + known marketplace caches) |

If no argument was supplied, ask the user plain-text:

```text
Which plugin should I migrate to the-skills-menu method?
Give me a local path, a Git URL, or a plugin name.
```

## Step 1 — Dispatch the skill

Invoke the backing skill, forwarding the user's **entire** argument
string verbatim — the target spec AND any trailing flags
(`--full-cleanup`, `--force-dirty`). The skill parses the flags
itself; dropping them silently changes the migration behaviour.

```text
Skill({skill: "claude-plugins-validation:the-skills-menu-create", args: "<full-argument-string-including-any-flags>"})
```

Example — a path with both flags must arrive at the skill intact:

```text
Skill({skill: "claude-plugins-validation:the-skills-menu-create", args: "~/code/my-plugin/ --full-cleanup --force-dirty"})
```

The skill takes care of:

1. Resolving the target plugin (clone or open).
2. Detecting the plugin shape (`.claude-plugin/plugin.json` + `agents/` + `skills/`).
3. Discovering agents in `<plugin-root>/agents/*.md` and skills in `<plugin-root>/skills/*/SKILL.md`.
4. Generating (or updating) `<plugin-root>/skills/the-skills-menu/SKILL.md` with the canonical `## Standalone Skills` and `## Plugin Skills` sections.
5. Rewriting every agent's frontmatter `skills:` list to exactly `[the-skills-menu]`.
6. Inserting the mandatory dynamic-loading instruction at the start of every agent body.
7. Reviewing skill bodies for agent-coupled phrases ("the doctor agent", "already prepared", etc.) and flagging them for manual review.
8. Producing a migration report with the diff summary.

## Step 2 — Report

Forward the skill's report verbatim. The report includes:

- The target plugin path + detected namespace.
- The catalog skill that was created or updated.
- Each agent that was modified, with its old skill list and the new
  one-line list.
- Any skill flagged for manual review (agent-coupled phrasing).
- A verification checklist.

## Step 3 — Done

Do not enter a post-action menu. The migration is a one-shot operation.
If the user wants to validate the migrated plugin, they can run
`/cpv-batch-validate <plugin-path>` directly, or open `/cpv-main-menu`
for the full validate / doctor / fix navigation. (There is no
standalone `/cpv-validate-plugin` or `/cpv-doctor` slash command after
the v2.90.0 menu unification — validation and the health-check are
reached through those two entry points.)

## Flags

| # | Flag | Effect |
|---|------|--------|
| 1 | `--full-cleanup` | Also rewrite agent-coupled skill bodies to be agent-agnostic. Off by default — risky for skills that genuinely rely on a specific caller's contract. |
| 2 | `--force-dirty` | Allow migration even when the target plugin's working tree has uncommitted changes. Off by default; the skill refuses dirty trees so the user keeps Git diff visibility. |

## See also

- `skills/the-skills-menu-create/SKILL.md` — backing skill.
- `skills/the-skills-menu-create/references/the-skills-menu-spec.md` — canonical spec (target resolution, frontmatter shape, body shape, safety rules).
- `skills/the-skills-menu/SKILL.md` — the catalog skill installed by the migration.
- TRDD-9dd64dbf — canonical rename + universal migrator design (`design/tasks/TRDD-20260519_162841+0200-9dd64dbf-the-skills-menu-canonical-method.md`).
