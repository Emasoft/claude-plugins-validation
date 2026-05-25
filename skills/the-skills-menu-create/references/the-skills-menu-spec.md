# the-skills-menu-create — universal skill-discovery method specification

## Table of Contents

- [Purpose](#purpose)
- [Invocation examples](#invocation-examples)
- [What this skill must do](#what-this-skill-must-do)
- [Target resolution](#target-resolution)
- [Plugin detection](#plugin-detection)
- [Agent discovery](#agent-discovery)
- [Skill discovery](#skill-discovery)
- [Generated skill name + path](#generated-skill-name--path)
- [Generated frontmatter](#generated-frontmatter)
- [Generated content](#generated-content)
- [Plugin namespace detection](#plugin-namespace-detection)
- [Agent frontmatter rewrite rule](#agent-frontmatter-rewrite-rule)
- [Agent body instruction rule](#agent-body-instruction-rule)
- [Skill independence review](#skill-independence-review)
- [Safety rules](#safety-rules)
- [Verification](#verification)
- [Final report](#final-report)
- [Expected result](#expected-result)

## Purpose

Convert a Claude Code plugin (or comparable AI-harness plugin) from
**static agent skill assignment** to **the-skills-menu method** —
agents declare only one preloaded skill (`the-skills-menu`) and pick
operational skills dynamically at runtime via the `Skill()` tool.

This skill creates the `the-skills-menu` skill inside the target
plugin and rewrites all of its agents.

After this skill runs, every agent in the target plugin must:

1. have only `the-skills-menu` in its frontmatter `skills:` list;
2. contain the mandatory dynamic skill-loading instruction in its body;
3. rely on the generated `the-skills-menu` skill to discover and load
   operational skills dynamically.

---

## Invocation examples

```text
/the-skills-menu-create create a skills menu from my-example-plugin
/the-skills-menu-create create a skills menu from the plugin project in ~/code/projects/my-scraping-plugin/
/the-skills-menu-create create a menu from my-example-plugin contained in github.com/example-org/example-marketplace
/the-skills-menu-create scaffold the menu for the plugin at /tmp/scratch/my-plugin
```

---

## What this skill must do

When invoked:

1. resolve the target plugin location (see "Target resolution");
2. clone or open the plugin project;
3. inspect the plugin structure;
4. discover all agents in the plugin;
5. discover all skills in the plugin;
6. generate the `the-skills-menu/SKILL.md`;
7. add the generated skill to the plugin;
8. rewrite every agent's frontmatter so its `skills:` list contains
   exactly one entry: `the-skills-menu`;
9. add the mandatory dynamic-loading instruction to every agent body;
10. verify the result;
11. report exactly what changed.

---

## Target resolution

The invocation may identify the target plugin in several ways. Try
them **in this priority order**:

| # | Source | Example |
|---|--------|---------|
| 1 | Explicit Git URL or owner/repo slug | `<owner>/<repo>`, `github.com/<owner>/<repo>`, `https://git.example.com/<owner>/<repo>.git` |
| 2 | Explicit local filesystem path | `~/code/my-plugin/`, `./plugins/my-plugin`, `/abs/path/to/plugin` |
| 3 | Plugin-inside-marketplace expression | `from the example-plugin in github.com/apple/example-marketplace` |
| 4 | Bare plugin name to search | `example-plugin` |

For (1) clone the repository into a temporary or working directory
unless the repository already exists locally. For (3) clone or open
the marketplace first, then locate the plugin inside it. For (4)
search the current workspace + known plugin directories + marketplace
directories; prefer exact directory-name matches; if multiple
candidates are found, choose the most likely plugin based on plugin
metadata and report the ambiguity, asking the user to select.

Do not silently modify multiple plugins unless the user explicitly
requested that.

---

## Plugin detection

A directory may be treated as a plugin root if it contains:

- a manifest file `.claude-plugin/plugin.json` (note the leading dot);
- an `agents/` directory;
- a `skills/` directory.

Detect the common directory shapes:

```text
my-plugin/
  .claude-plugin/
    plugin.json
  agents/
  skills/
```

If multiple subfolders with different layouts exist, inspect them and
choose the one containing the actual plugin agents and skills. If
more than one plugin is found, ask the user to pick one.

---

## Agent discovery

Find every agent definition file in the plugin.

Agent files have:

- location: `<plugin-root>/agents/*.md`;
- YAML frontmatter.

A file is an agent file **only if both conditions hold** (not either-or):

1. it lives in the `agents/` directory of the target plugin;
2. it has YAML frontmatter.

**No agents found.** If `agents/` is absent or contains zero
frontmatter-bearing `.md` files, there is nothing to rewrite. Do NOT
fail — the plugin may be skill-only (skills invoked directly by the
user, no agents). Still generate `the-skills-menu/SKILL.md` as a
discoverability catalog, then report `0 agents migrated — this plugin
ships no agents; the catalog was generated for reference only`. Skip
the agent-frontmatter and agent-body steps entirely.

Always ignore directories such as:

```text
.git/  node_modules/  vendor/  dist/  build/  .cache/  __pycache__/  .venv/  venv/  bin/  .trashcan/
```

Do not modify files outside the target plugin.

---

## Skill discovery

Find every skill definition file in the plugin.

Skills can be:

- folder-based: `<plugin-root>/skills/<skill-name>/SKILL.md` (canonical);
- a packed `.skill` archive (folder zipped into a single file) — treat
  as a future extension; in v1 of this method, primary support is
  folder-based skills.

A skill is identified by:

- location: inside the plugin's `skills/` directory tree;
- file name `SKILL.md` with YAML frontmatter.

A skill folder may contain additional documentation (scripts/,
examples/, templates/, references/) — these are part of the skill
and stay with it.

**Do NOT list `the-skills-menu` itself as an entry inside its own
catalog** — recursive self-reference is meaningless. The catalog
only lists the OTHER skills the agent might invoke.

**No skills found.** If `skills/` is absent or contains only the
`the-skills-menu` folder itself (i.e. no OTHER skills to catalog),
the migration is a no-op for skill discovery. Still generate the
catalog with both canonical sections, each carrying its
"No … skills were discovered" placeholder line, and report
`0 operational skills indexed — nothing for agents to load
dynamically yet`. Do not error.

---

## Generated skill name + path

The generated skill must be named:

```text
the-skills-menu
```

It must be placed at:

```text
<plugin-root>/skills/the-skills-menu/SKILL.md
```

(Claude Code REQUIRES the file name `SKILL.md` — no other name is
recognised by the harness.)

---

## Generated frontmatter

The generated SKILL.md must start with frontmatter equivalent to:

```yaml
---
name: the-skills-menu
description: "Dynamic skill menu for this plugin. Teaches agents which skills are available, when to use them, and how to load them with the Skill() tool. Use when an agent needs to pick a downstream skill at runtime. Used by every agent in this plugin via the-skills-menu method."
user-invocable: false
allowed-tools: Read
---
```

If the target plugin uses additional required skill metadata fields
(e.g. `when_to_use:`, `tags:`), preserve compatibility with that
format.

---

## Generated content

### Required structural sections (validator compatibility)

The generated `the-skills-menu/SKILL.md` is a real skill and MUST pass
the target harness's own skill validator. CPV's validator (and the
Anthropic Agent-Skills / Nixtla-strict checks it mirrors) require every
skill body to carry these sections, in addition to the two catalog
sections below:

- `## Overview` — one-paragraph statement of what the catalog is for.
- `## Prerequisites` — e.g. "the calling agent has `Skill` in its `tools:` list".
- `## Instructions` — the numbered steps an agent follows to pick + load a skill.
- `## Output` — what the catalog itself returns (nothing; the chosen downstream skill produces output).
- `## Error Handling` — unknown-skill-name, double-load, advisory-only legacy descriptions.
- `## Examples` — at least one `Skill({skill: "...", args: "..."})` invocation.
- `## Resources` — pointer to the per-skill reference table when the catalog is large.

A catalog that ships ONLY `## Standalone Skills` + `## Plugin Skills`
(and skips the structural sections above) will be flagged
MAJOR/MINOR by the validator and block the migrated plugin's publish.
Generate the structural sections too — synthesise conservative,
accurate text from the plugin's actual skills.

If the catalog grows past the skill-body budget (CPV: 5,000 **tokens** —
the runtime keeps only ~5,000 tokens of a skill body after auto-compaction,
so anything beyond that is silently dropped), move the full per-skill table
into `skills/the-skills-menu/references/skills-catalog.md` and keep a
domain-grouped summary table in `SKILL.md` (progressive disclosure),
exactly as CPV's own catalog does. The budget is **token-based and
non-negotiable** — there is no per-plugin override (the old
`cpv.max_chars` / `cpv.max_lines` / `cpv.skill_size_severity` keys were
removed in TRDD-021250b5); the only fix for an oversized body is the
progressive-disclosure split.

### Catalog sections

The body then lists available skills under exactly two canonical
sections:

```markdown
## Standalone Skills

(skills installed at user/local/project scope outside the target
plugin's namespace — listed here so the agent knows they exist
and can invoke them via their bare name)

## Plugin Skills

(skills that belong to the target plugin — invoked with the plugin
namespace prefix because the harness disambiguates by namespace)
```

### Standalone Skills section

For each standalone skill add a markdown entry:

```markdown
### skill-name

**Use when:** ...

**Do not use when:** ...

**Load with:** `Skill({skill: "skill-name", args: "..."})`

**Inputs:** ...

**Outputs:** ...

**Dependencies:** ...
```

If no standalone skills are discovered, include:

```markdown
No standalone skills were discovered for this plugin.
```

### Plugin Skills section

For each plugin skill add a markdown entry:

```markdown
### plugin-name:skill-name

**Use when:** ...

**Do not use when:** ...

**Load with:** `Skill({skill: "plugin-name:skill-name", args: "..."})`

**Inputs:** ...

**Outputs:** ...

**Dependencies:** ...
```

The `Load with:` line MUST use Claude Code's actual `Skill` tool syntax
(`Skill({skill: "...", args: "..."})`).

If detailed usage information cannot be inferred from the source skill,
synthesise a conservative first version from:

- the skill filename;
- the skill frontmatter `name` + `description`;
- top-level headings in the skill body;
- obvious keywords in the skill content.

Do not hallucinate precise behaviour. If uncertain, add:

```markdown
Usage details could not be fully inferred from the skill file.
Inspect the skill before relying on it for destructive operations.
```

---

## Plugin namespace detection

Determine the plugin namespace from the best available source,
in priority order:

1. `.claude-plugin/plugin.json` → `name` field;
2. package / plugin metadata (`pyproject.toml` / `package.json`);
3. repository name;
4. plugin root directory name.

Normalise the namespace according to harness conventions (lowercase,
kebab-case).

If the namespace cannot be determined confidently, use the plugin
directory name and report that assumption.

---

## Agent frontmatter rewrite rule

Every discovered agent file must be rewritten so its frontmatter
`skills:` list contains exactly one entry:

```yaml
skills:
  - the-skills-menu
```

The skill is referenced WITHOUT a namespace prefix because the agent
and the skill live in the same plugin. (Cross-plugin references need
the prefix; same-plugin references do not.)

All previously listed skills must be removed from the agent frontmatter.

Do not remove unrelated frontmatter fields.

Before:

```yaml
---
name: fix-agent
description: Fixes plugin issues.
skills:
  - validation
  - security-scan
  - publish-pipeline
model: sonnet
---
```

After:

```yaml
---
name: fix-agent
description: Fixes plugin issues.
skills:
  - the-skills-menu
model: sonnet
---
```

Preserve every other field unchanged (name, description, model, tools,
permissions, routing metadata).

---

## Agent body instruction rule

Every discovered agent body must contain this exact instruction near
the start of the body:

```text
You must load the skills you need dynamically. Use the Skill() tool to load them. Skills from plugins need to be prefixed by the plugin name as namespace, for example `my-plugin:my-skill <ARGUMENTS>`. Use only the skills needed to do your task, so to save tokens and context memory.
```

**Placement:** insert it as the FIRST body paragraph AFTER the agent's
opening `# Title` H1 heading (if the body starts with one), not before
it — a paragraph above the H1 reads as a stray preamble and some
renderers mis-handle it. If the body has no leading H1, insert the
instruction as the very first body line after the frontmatter. Leave a
blank line before and after so it renders as its own paragraph.

If the instruction already exists verbatim, do not duplicate it.

If a non-identical instruction exists that is semantically equivalent
AND includes all five required points — (a) dynamic loading, (b)
`Skill()` tool, (c) plugin namespace prefix, (d) loading only needed
skills, (e) token/context saving — keep the existing text.

Otherwise replace or supplement it with the exact required instruction.

---

## Skill independence review

After generating the menu, inspect discovered skill files for signs
that they assume a specific agent will call them.

Flag suspicious phrases such as:

- "the validator agent"
- "the doctor agent"
- "the migration agent"
- "the publisher agent"
- "the security agent"
- "already checked"
- "already loaded"
- "already prepared"
- "as described in the agent"

Do not automatically rewrite those skill files unless the user
requested full migration cleanup.

Instead, report which skill files may need manual review.

If the user requested full migration cleanup, update those skills so
prerequisites and responsibilities are explicit inside the skill
itself.

---

## Safety rules

Before modifying files:

1. detect whether the directory is a Git repository;
2. check whether there are uncommitted changes;
3. report if the working tree is dirty;
4. avoid overwriting unrelated user changes;
5. rely on Git diff visibility (no extra backups needed when the
   plugin is in Git).

If the working tree is dirty and the harness policy forbids
modifications, ask the user before editing.

When editing:

- never modify files outside the target plugin;
- never modify dependency directories;
- never modify `.git`;
- never delete existing skills;
- never delete existing agents;
- never remove unrelated agent metadata;
- never blindly overwrite an existing `the-skills-menu/SKILL.md` without
  inspecting it first (it may be a hand-curated catalog).

If `the-skills-menu` already exists, decide by content shape:

- **Already a catalog** (has both `## Standalone Skills` and
  `## Plugin Skills` headings) → refresh it in place: re-derive the two
  catalog sections from the current skill set, preserve hand-written
  prose in the structural sections (`## Overview`, `## Instructions`,
  etc.). Do not create a duplicate.
- **Not a catalog** (a hand-curated skill that merely shares the name)
  → never silently clobber. Copy it to a sibling backup file
  (SKILL.md.bak in the same folder), then ask the user whether to
  overwrite (backup kept), merge by hand, or abort. Report which
  choice was taken.

---

## Verification

After editing, verify all of:

- every agent has YAML frontmatter;
- every agent frontmatter has exactly one entry in `skills:`;
- that entry is `the-skills-menu`;
- every agent body contains the mandatory dynamic-loading instruction;
- `<plugin-root>/skills/the-skills-menu/SKILL.md` exists;
- the file contains a `## Standalone Skills` heading;
- the file contains a `## Plugin Skills` heading;
- every plugin-skill entry uses the detected plugin namespace;
- no agent still lists old operational skills in its frontmatter.

If possible, produce a diff summary.

---

## Final report

```markdown
## Skills Menu Creation Report

**Target plugin:** ...
**Plugin namespace:** ...
**Plugin path:** ...

### Created or updated
- ...

### Agents updated
- ...

### Skills indexed
- ...

### Potential manual review needed
- ...

### Verification
- [x] the-skills-menu exists at <plugin-root>/skills/the-skills-menu/SKILL.md
- [x] all agent frontmatters use only the-skills-menu
- [x] all agents contain the dynamic-loading instruction
- [x] plugin skills are namespaced in the catalog
```

If any step failed, report:

- what failed;
- why it failed;
- which files were left unchanged;
- what the user should do next.

---

## Expected result

After successful execution:

```text
<plugin-root>/skills/the-skills-menu/SKILL.md
```

…exists, and every agent in the plugin has frontmatter shaped like:

```yaml
---
name: example-agent
description: Example agent.
skills:
  - the-skills-menu
---
```

…with this instruction at the start of the body:

```text
You must load the skills you need dynamically. Use the Skill() tool to load them. Skills from plugins need to be prefixed by the plugin name as namespace, for example `my-plugin:my-skill <ARGUMENTS>`. Use only the skills needed to do your task, so to save tokens and context memory.
```

The plugin's agents can then dynamically choose operational skills
from the generated `the-skills-menu` instead of loading large static
skill lists from frontmatter.
