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
- [Custom-folder declarations in plugin.json](#custom-folder-declarations-in-pluginjson)
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
  1. Wrap this <skill / agent / commands> into a NEW plugin (I will
     ask for plugin name + scaffolding details).
  2. ADD this <skill / agent / commands> into an existing plugin's
     correct folder (I will ask for the existing plugin path).
  3. Cancel — I will leave the directory untouched.

Which option?
```

The agent then waits for the user's plain-text reply. NEVER use
AskUserQuestion. NEVER auto-pick option 1.

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

- A folder/file appears at root and is NOT declared in
  `cpv.allow_root_dirs` → CRITICAL "non-standard root entry".
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

## Custom-folder declarations in plugin.json

A plugin MAY use folders outside the standard list, but EVERY
non-standard folder at root MUST be declared:

```json
{
  "name": "my-plugin",
  "cpv": {
    "allow_root_dirs": ["fixtures/", "examples/", "data/"]
  }
}
```

Without the declaration, CPV emits CRITICAL. The rule exists because
silent custom layouts are the #1 source of "the plugin published but
installs to nothing" — the install pipeline only ever knows about
the standard component dirs.

The same rule applies to non-standard FILES at root: any file that
isn't `LICENSE` / `CHANGELOG.md` / `README.md` / `.gitignore` /
`pyproject.toml` / `package.json` / `Cargo.toml` / `go.mod` /
`.python-version` / `.markdownlint.json` / `.mega-linter.yml` /
`cliff.toml` / `settings.json` / `.mcp.json` / `.lsp.json` MUST be
covered by `cpv.allow_root_files` or it triggers MAJOR.

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
8. Every non-standard root entry is in `cpv.allow_root_dirs` /
   `cpv.allow_root_files`.
9. Every cross-reference to another agent/skill in this plugin uses
   the `<plugin-name>:<component>` form (no bare name).
10. Manifest's `marketplace`, `repository`, `homepage` URLs point at
    plausible GitHub URLs (or are absent).

If any of 1–10 fails, surface as a CRITICAL or MAJOR finding —
do NOT proceed to fixes that assume the shape is correct.
