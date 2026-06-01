# Plugin-shape detection — Phase 0

This document is the canonical Phase 0 rule for every CPV agent that
validates, fixes, migrates, upgrades, or scaffolds a Claude Code
plugin. It exists because CPV agents have repeatedly mis-classified
input directories — wrapping standalone skills, single agents, or
plain folders into "plugins" that publish but install to nothing.

## Table of Contents

- [Why this rule exists](#why-this-rule-exists)
- [Detection table — root-folder signals to verdict](#detection-table--root-folder-signals-to-verdict)
- [Hard refusal protocol](#hard-refusal-protocol)
- [Standard plugin layout](#standard-plugin-layout)
- [Path-variable rules — ${CLAUDE_PLUGIN_ROOT} vs ${CLAUDE_PLUGIN_DATA}](#path-variable-rules--claude_plugin_root-vs-claude_plugin_data)
- [Custom non-standard root entries](#custom-non-standard-root-entries)
- [Common mis-classification patterns](#common-mis-classification-patterns)
- [Verifier: ten checks before marking as plugin](#verifier-ten-checks-before-marking-as-plugin)

---

## Why this rule exists

A real incident: a directory containing a `SKILL.md` at root, a
`references/` folder with `.md` files, and `.md` content using
`./scripts/` and `~/.claude/skills/...` paths was processed by the
CPV doctor + fixer + creator agents. They:

1. Did not notice `SKILL.md` at the root signalled "this is a skill".
2. Did not notice `references/` signalled "this is a skill's
   embedded references".
3. Wrote a `plugin.json`, scaffolded a marketplace, added the
   publish pipeline, and shipped to GitHub.
4. The user installed the plugin — and got an empty shell. The skill
   content lived in the wrong shape; nothing loaded.

The agents must NEVER again proceed past Phase 0 without verifying
the directory IS a plugin. If it is anything else (skill, agent,
single command, plain folder), the agents must ABORT and ASK the
user what they actually want.

---

## Detection table — root-folder signals to verdict

| Signal at directory root | Verdict | Action |
|---|---|---|
| `.claude-plugin/plugin.json` is present and parses as JSON | IS a plugin | Proceed with normal validation / fix / upgrade flow. |
| `SKILL.md` at root + NO `.claude-plugin/plugin.json` | IS a single skill | ABORT. Ask: *"This looks like a standalone skill, not a plugin. Wrap it into a NEW plugin (skills/<this>/SKILL.md, plus optional commands), or ADD it to an existing plugin's `skills/` folder?"* |
| `agents/<name>.md` only + NO `.claude-plugin/plugin.json` + no other plugin components | IS a single agent | ABORT. Ask: *"This looks like a standalone agent. Wrap it into a NEW plugin or ADD it to an existing plugin's `agents/` folder?"* |
| `commands/` only + NO `.claude-plugin/plugin.json` | IS a commands folder | ABORT. Ask: *"This looks like loose commands. Wrap them into a NEW plugin or ADD them to an existing plugin's `commands/` folder?"* |
| `marketplace.json` at root and NO `plugin.json` | IS a marketplace | Route to validate_marketplace, NOT validate_plugin. |
| `plugins/<name>/` subdirectories with their own `plugin.json` files | IS a Layout B nested marketplace | Route to validate_marketplace; iterate plugins/<name>/. |
| BOTH `.claude-plugin/plugin.json` AND `.claude-plugin/marketplace.json` at root | IS a Layout C marketplace-in-plugin | Validate as both — name + version sync mandatory. |
| Plain folder with no recognised plugin / skill / agent / command markers | UNKNOWN | ABORT. Do NOT scaffold a plugin from arbitrary files. Ask the user what the folder is. |

The "ABORT and ask" verdicts are NON-NEGOTIABLE. The agents must NOT
silently scaffold plugin metadata to make the verdict come out as
"plugin".

## Hard refusal protocol

When Phase 0 returns anything other than "IS a plugin" / "IS a
marketplace" / "IS a Layout C marketplace-in-plugin", the agent
returns this exact response shape (replace placeholders):

```
[BLOCKED — Phase 0 plugin-shape detection]

The directory at <PATH> does NOT match the plugin shape. Detected
signals:
  - <signal 1>
  - <signal 2>
  - …

Verdict: <e.g. "single skill", "single agent", "unknown folder">.

I refuse to scaffold plugin metadata around this — that would
produce an empty install (no plugin components would actually load).

Pick one:
  1. Wrap this <skill / agent / commands> into a NEW plugin — I will
     run the multi-select packer (scripts/cpv_pack_components.py) so
     you can choose which detected components to include and where to
     write the new plugin (see menu §3.4.8).
  2. ADD this <skill / agent / commands> into an existing plugin's
     correct folder (I will ask for the existing plugin path).
  3. Cancel — I will leave the directory untouched.

Which option?
```

The agent then waits for the user's plain-text reply. NEVER use
AskUserQuestion. NEVER auto-pick option 1.

When the user picks option 1, the agent invokes:
```bash
uv run --with pyyaml python "${CLAUDE_PLUGIN_ROOT}/scripts/cpv_pack_components.py" \
    "<source>" --list-only
```
to enumerate detected components, presents the per-type list, asks the
user which to include, then re-invokes the script with `--all` or
`--include type=name,name [...]` flags to actually pack the selected
subset into a new plugin shape. Exit codes 0/1/2/3/4/5 are the same as
the menu §3.4.8 flow — the agent must report them to the user verbatim
on failure.

## Standard plugin layout

The official layout (verbatim from
[plugins-reference.md](plugins-reference.md) §"Plugin directory
structure"):

```
my-plugin/
├── .claude-plugin/
│   └── plugin.json             # MANDATORY for "is a plugin"
├── skills/
│   └── <skill-name>/
│       └── SKILL.md            # one folder per skill
├── commands/                   # flat .md files
│   └── <command>.md
├── agents/
│   └── <agent>.md
├── output-styles/
│   └── <style>.md
├── themes/
│   └── <theme>.json
├── monitors/
│   └── monitors.json
├── hooks/
│   └── hooks.json              # main hook config
├── bin/                        # PATH-added executables
│   └── <executable>
├── settings.json               # default settings
├── .mcp.json                   # MCP server definitions
├── .lsp.json                   # LSP server configurations
├── scripts/                    # hook + utility scripts
├── LICENSE
└── CHANGELOG.md
```

Top-level files at plugin root that AREN'T in this list are
non-standard. Two failure modes apply:

- A folder appears at root and is NOT recognised by CPV (not a known
  component dir, not referenced by the manifest, not gitignored, not a
  vendoring/submodule dir) → MAJOR `[RC-NONSTD-DIR-001]`
  "non-standard directory". CPV no longer honors a plugin-declared
  `cpv.allow_root_dirs` opt-out (removed in TRDD-02e1672b — a plugin
  must not be able to self-exempt). See [Custom non-standard root
  entries](#custom-non-standard-root-entries) for how to legitimise one.
- A standard folder is MISSING for components the manifest
  references → CRITICAL "promised component not found".

A `CLAUDE.md` at plugin root is NOT loaded as project context. Plugin
authors must put instructions in skills, agents, or hooks.

## Path-variable rules — ${CLAUDE_PLUGIN_ROOT} vs ${CLAUDE_PLUGIN_DATA}

`${CLAUDE_PLUGIN_ROOT}`: absolute path to the plugin's installation
directory. Substituted inline anywhere it appears in skill / agent /
hook / monitor / MCP / LSP content. Use for bundled scripts, binaries,
configs. **EPHEMERAL — wiped on every plugin update.** Never write
state here.

`${CLAUDE_PLUGIN_DATA}`: persistent directory for plugin state that
**survives updates**. Use for installed dependencies (`node_modules`,
Python venvs), generated code, caches. Resolves to
`~/.claude/plugins/data/<id>/` where `<id>` is the plugin id with
non-`[a-zA-Z0-9_-]` chars replaced by `-`.

Forbidden path forms in skill / agent / hook / command / config
content:

| Forbidden form | Why | Correct form |
|---|---|---|
| `./scripts/<name>` | Relative paths break under the cache install — the cwd is not the plugin root. | `${CLAUDE_PLUGIN_ROOT}/scripts/<name>` |
| `./references/<name>` | Same. References resolve via SKILL.md's location, but for cross-references use the env var. | `${CLAUDE_PLUGIN_ROOT}/skills/<this>/references/<name>` |
| `~/.claude/skills/<name>` | Talks to the user's global skills, not the plugin's. | Bundle the skill in the plugin's `skills/` folder. |
| `~/.claude/plugins/cache/...` | Pinning to the cache path is brittle and version-locked. | `${CLAUDE_PLUGIN_ROOT}/...` |
| `${CLAUDE_PLUGIN_ROOT}/node_modules/` | ROOT is wiped on every update; deps installed there are lost. | `${CLAUDE_PLUGIN_DATA}/node_modules/` + a SessionStart installer hook. |

CPV emits MAJOR `[RC-DATA-WRONG-ROOT-001]` for the cache-relative
case and `[RC-PATH-RELATIVE-001]` (planned) for `./` / `~/` forms.

## Custom non-standard root entries

A plugin MAY use folders outside the standard list, but a non-standard
folder at root is reported as MAJOR `[RC-NONSTD-DIR-001]` unless CPV
can recognise it as legitimate.

> **The `cpv.allow_root_dirs` / `cpv.allow_root_files` self-declaration
> opt-outs were REMOVED (TRDD-02e1672b).** A plugin must not be able to
> exempt its own directories from CPV's checks — a malicious author
> could otherwise self-allow arbitrary content. A `cpv.allow_root_dirs`
> key still present in plugin.json now emits a one-release deprecation
> WARNING (`[RC-DEPRECATED-OPTOUT]`) and is IGNORED. `cpv.allow_root_files`
> was never honored.

CPV recognises a non-standard root folder as legitimate (no MAJOR) when
ANY of these hold — these are CPV's OWN logic, not author-controlled:

1. It is a built-in known component dir (skills/agents/commands/hooks/
   scripts/monitors/themes/output-styles/bin, plus `.claude-plugin/`).
2. A manifest entry references it via `${CLAUDE_PLUGIN_ROOT}/<dir>/...`
   (MCP, LSP, hook, or monitor command). The manifest reference is the
   self-documentation that the folder is intentional.
3. It is gitignored — the plugin excludes it from distribution, so it
   cannot cause an empty install (research material, local builds,
   fixtures the publish pipeline never ships).
4. It is a vendoring/submodule root (`external/`, `vendor/`,
   `third_party/`, `node_modules/`, anything listed in `.gitmodules`,
   or a subdirectory named after the plugin itself).

So the correct way to legitimise a custom folder is to pick one of the
four above — reference it from the manifest if a component uses it, or
gitignore it if it is dev-only — NOT to declare an allow-list. The rule
exists because silent custom layouts are the #1 source of "the plugin
published but installs to nothing" — the install pipeline only ever
loads from the standard component dirs.

## Common mis-classification patterns

These are the signal patterns the agents have historically fumbled:

1. **SKILL.md at root** → SKILL, not plugin. The plugin equivalent
   would be `skills/<name>/SKILL.md` nested.
2. **`description:` in frontmatter that doesn't match the parent
   folder name** → suggests the file was written for a different
   plugin / skill. ABORT until rectified.
3. **References to other skills via `~/.claude/skills/<other>/SKILL.md`**
   → suggests the user expected to ship multiple skills in ONE
   directory; the only legitimate form is one skill per
   `skills/<name>/` folder inside a plugin. ABORT and ask.
4. **Commands invoking `npx`, `pip install`, `cargo build` with no
   `--prefix` / `--target` / `--target-dir` pointing at
   `${CLAUDE_PLUGIN_DATA}`** → install destination is wrong; the
   bundled deps will land in the user's cwd or global env, not the
   plugin's data dir.
5. **Agents / skills referenced by bare name (`my-agent`) instead of
   `plugin-name:my-agent`** → cross-plugin invocation will fail
   silently. The validator MUST flag this.

## Verifier: ten checks before marking as plugin

Even when `.claude-plugin/plugin.json` is present, run these
sanity checks before declaring the directory "definitely a plugin":

1. `plugin.json` parses as valid JSON.
2. `plugin.json::name` is non-empty and matches `[a-z][a-z0-9-]*`.
3. `plugin.json::version` matches semver.
4. At least ONE component dir exists (skills/ OR agents/ OR
   commands/ OR hooks/ OR monitors/ OR has .mcp.json OR .lsp.json).
5. SKILL.md at root is ABSENT (would mean someone is mixing skill +
   plugin shapes).
6. No `references/<name>.md` at root (skill-reference shape).
7. No path inside skill/agent/command/hook content uses `./`, `~/`,
   or absolute non-`${CLAUDE_PLUGIN_ROOT}` paths to local files.
8. Every non-standard root folder is legitimised by CPV's own logic —
   a known component dir, a manifest reference, a `.gitignore` entry, or
   a vendoring/submodule root (see [Custom non-standard root
   entries](#custom-non-standard-root-entries)); a plugin-declared
   `cpv.allow_root_dirs` allow-list is NOT honored (removed,
   TRDD-02e1672b).
9. Every cross-reference to another agent/skill in this plugin uses
   the `<plugin-name>:<component>` form (no bare name).
10. Manifest's `marketplace`, `repository`, `homepage` URLs point at
    plausible GitHub URLs (or are absent).

If any of 1–10 fails, surface as a CRITICAL or MAJOR finding —
do NOT proceed to fixes that assume the shape is correct.
