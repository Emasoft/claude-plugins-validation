---
trdd-id: 9dd64dbf-b716-4069-bbab-99505417c3bc
title: the-skills-menu — canonical rename of skills-index + universal migrator skill
status: in-progress
created: 2026-05-19T16:28:41+0200
updated: 2026-05-19T16:28:41+0200
---

<!-- markdownlint-disable-next-line MD025 -->
# TRDD-9dd64dbf — the-skills-menu canonical method

**Filename:** `design/tasks/TRDD-20260519_162841+0200-9dd64dbf-the-skills-menu-canonical-method.md`
**Tracked in:** this repo (`design/tasks/` is git-tracked)

## Origin

User request (verbatim, 2026-05-19):

> I created a standardized method called `the-skills-menu` method, that
> apply the paradigm to all agents and plugins. The menu is created by
> this skill: `/Users/emanuelesabetta/Code/CLAUDE-PLUGIN-VALIDATION/docs_dev/the-skills-menu-create.md`.
> Check it for errors and possible improvements, and integrate it into
> the plugin (even if it is more universal, and would work for all
> plugins)

Generalises and standardises the v2.93.0 `skills-index` pattern
(TRDD-478d9687) under the canonical name **the-skills-menu**, with
two canonical sections (`## Standalone Skills`, `## Plugin Skills`)
and an exact mandated dynamic-loading instruction.

## What changed

### Catalog skill renamed

- `skills/skills-index/` → `skills/the-skills-menu/` (folder + SKILL.md
  + `references/skills-catalog.md` carried over via `git mv`).
- Frontmatter `name:` updated to `the-skills-menu`.
- Body restructured to expose exactly two canonical sections:
  `## Standalone Skills` and `## Plugin Skills`.

### Universal migrator skill added

- New `skills/the-skills-menu-create/SKILL.md` (`user-invocable: true`).
  Converts any Claude Code plugin to the-skills-menu method.
- New `skills/the-skills-menu-create/references/the-skills-menu-spec.md`
  with full TOC (18 headings), no YAML frontmatter (reference files
  don't carry frontmatter).
- Accepts target plugin as Git URL, local path,
  plugin-in-marketplace expression, or bare plugin name.
- Mandatory dynamic-loading instruction is the EXACT verbatim text
  from the user's spec — required in every migrated agent body.

### All 11 agents updated

`agents/cache-optimizer-agent.md`, `cpv-doctor-agent.md`,
`cpv-main-menu-agent.md`, `marketplace-fixer.md`, `plugin-creator.md`,
`plugin-diagnoser.md`, `plugin-fixer.md`, `plugin-manager.md`,
`plugin-validator.md`, `semantic-validator.md`, `skill-validation-agent.md`:

- Frontmatter `skills: [skills-index]` → `skills: [the-skills-menu]`.
- Body's dynamic-loading line replaced with the canonical instruction
  text from the spec.

### All 32 skill descriptions updated

`Used dynamically via skills-index (TRDD-478d9687)` →
`Used dynamically via the-skills-menu (TRDD-478d9687)`.

### Validator regex extended

`scripts/validate_skill_comprehensive.py`:

- Renamed `RE_USED_VIA_SKILLS_INDEX` → `RE_USED_VIA_THE_SKILLS_MENU`.
- Pattern now accepts both old and new names so a plugin midway
  through migration validates cleanly:
  `r"(?:[Uu]sed|[Ll]oaded)\s+(?:dynamically\s+)?(?:by\s+\S+\s+)?via\s+(?:the-skills-menu|skills-index)"`

### Tests updated

- `tests/test_consolidation_v211.py` — `skills-index` →
  `the-skills-menu` references; added exemption for
  `the-skills-menu-create` in `test_all_skills_are_non_user_invocable`
  (this skill must stay user-invocable so authors can trigger
  `/the-skills-menu-create` against a target plugin).
- `tests/test_batch_fix_v291.py` — same rename.
- `tests/test_marketplace_authoring_contract.py` — same rename.

## Design decisions

| # | Decision | Rationale |
|---|----------|-----------|
| 1 | Backwards-compat validator regex accepts both names | A mid-migration plugin doesn't fail validation while skill descriptions are being updated batch-by-batch. |
| 2 | Same-plugin skills referenced WITHOUT namespace prefix in `skills:` list | The harness disambiguates by plugin scope; only cross-plugin references need the `plugin:skill` form. Confirmed against the spec's §Agent frontmatter rewrite rule. |
| 3 | `the-skills-menu-create` is `user-invocable: true` | It's a migration tool that operates on OTHER plugins, not on the plugin that contains it. Test exemption added to make the architectural rule explicit. |
| 4 | Reference file `the-skills-menu-spec.md` has no YAML frontmatter | Reference files inside `references/` never carry frontmatter — only SKILL.md does. Matches every other reference file in the plugin. |
| 5 | Placeholder URLs use `<owner>/<repo>` and `git.example.com` | Avoids 404 against the dead-URL detector while still showing the intended shape. The earlier draft used `apple/ios-development-plugin` which 404s. |
| 6 | TOC list link "Generated frontmatter" instead of "Generated `the-skills-menu` frontmatter" | The shorter form keeps the embedded TOC blob in SKILL.md under the size limit AND avoids the backtick-in-anchor compatibility risk. |

## Anthropic prompt-cache compatibility (CA-01..CA-06)

Audited the new method against every rule in
`skills/cache-validation-skill/references/ca-rules.md`:

| Rule | Result | Reason |
|------|--------|--------|
| CA-01 (dynamic placeholders) | ✓ no conflict | Method touches `skills:` list, not byte-substituted content. |
| CA-02 (hooks writing cached files) | ✓ no conflict | Method doesn't add hooks. |
| CA-03 (hooks flipping permissions/MCP) | ✓ no conflict | No permission churn. |
| CA-04 (`model:` in SKILL.md) | ✓ verified | Neither `the-skills-menu/SKILL.md` nor `the-skills-menu-create/SKILL.md` declares `model:`. |
| CA-05 (unbounded hook output) | ✓ no conflict | No hooks added. |
| CA-06 (compaction hook prefix) | ✓ no conflict | No hooks added. |

The new method is in fact cache-FRIENDLIER than the previous static
`skills: [...]` because:

1. Agent prefix becomes byte-stable (one fixed entry: `the-skills-menu`).
2. The same catalog skill is preloaded by all agents → shared cache slot.
3. Runtime `Skill()` invocations land in conversation history, NOT in
   the cached system-prompt prefix — they can't invalidate the cache.

One trade-off documented for future readers:

- Old method: editing a skill that 3 agents list invalidates only
  those 3 agent prefixes.
- New method: editing the catalog (or adding a new plugin skill that
  rotates the catalog body) invalidates every agent's prefix.

This is once-per-plugin-update churn, not per-session — strictly
better than the previous per-session churn from variable skill lists.

## Verification

- `validate_skill_comprehensive.py skills/the-skills-menu-create` →
  100.0/100, 0/0/0.
- `validate_plugin.py .` with `PLUGIN_SKIP_GITHUB_INTEGRITY=1` →
  0/0/0/0/0.
- `pytest -n auto` → 5389 passed, 1 skipped, 0 failed.

## Open questions / future work

- Move the spec into `references/the-skills-menu-spec.md` of the
  `the-skills-menu` (catalog) skill instead of the `-create` skill?
  Currently the spec lives next to the migrator because authors
  reading the migrator are the primary audience.
- Surface `the-skills-menu-create` from the CPV main menu under
  "Create / Convert plugins" once the v2.94.0 dust settles.

## Suggestions for the next revision

- Consider auto-discovering standalone (user/local/project-scope)
  skills the plugin's agents reference, instead of relying on the
  user to list them manually. Currently the migrator only flags
  same-plugin skills.
