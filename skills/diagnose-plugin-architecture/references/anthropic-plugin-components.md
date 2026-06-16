# Runtime-essential plugin components (Anthropic plugins-reference)

This is the canonical **ship-always** list. It is sourced from the
official Anthropic Claude Code **plugins-reference** (the "Plugin
components reference", "Plugin directory structure", and "File locations
reference" sections). A file is **runtime-essential** if it IS one of
these components, BELONGS TO one (a skill's own `references/` / `scripts/`,
an MCP server's referenced `config*.json` / `data/`), OR is referenced via
`${CLAUDE_PLUGIN_ROOT}/<path>` by any of them.

The architecture diagnostic NEVER recommends stripping, removing, or
gitignoring anything on this list. When a file's status is in doubt, it is
classified **ship-always** (FN-safe default).

## Table of Contents

- [Ship-always component table](#ship-always-component-table)
- [The ${CLAUDE_PLUGIN_ROOT} reference rule](#the-claude_plugin_root-reference-rule)
- [Why bin/ ships but its source does not](#why-bin-ships-but-its-source-does-not)
- [Conventional small root files](#conventional-small-root-files)
- [The two path variables that drive the taxonomy](#the-two-path-variables-that-drive-the-taxonomy)

## Ship-always component table

| Component | Default location | Notes |
|---|---|---|
| Manifest | `.claude-plugin/plugin.json` | `+ .claude-plugin/marketplace.json` for marketplaces. The ONLY file inside `.claude-plugin/`. |
| Skills | `skills/<name>/SKILL.md` | `+` its own `references/`, `scripts/`, `assets/`, `templates/`. Or a single root `SKILL.md` for one-skill plugins. |
| Commands | `commands/*.md` | Flat-file skills. Discovered automatically at the plugin root. |
| Agents | `agents/*.md` | Subagent definitions. Discovered automatically at the plugin root. |
| Output styles | `output-styles/` | Or the `outputStyles` manifest path (e.g. `styles/`). |
| Themes | `themes/*.json` | Experimental component. Color theme definitions. |
| Hooks | `hooks/hooks.json` | `+` every script the JSON references via `${CLAUDE_PLUGIN_ROOT}/...`. |
| MCP servers | `.mcp.json` | `+ servers/` executables, `+` any referenced `config*.json` / `data/`. |
| LSP servers | `.lsp.json` | Config ONLY. The LSP binary is installed separately and never shipped. |
| Monitors | `monitors/monitors.json` | Experimental component. `+` every script it references. |
| Executables | `bin/` | The COMPILED binaries, invokable as bare commands on PATH. ALWAYS ship. |
| Settings | `settings.json` | Default plugin settings (`agent` / `subagentStatusLine` keys). |
| Scripts | `scripts/` | Ship IFF referenced by a runtime component. A build-only, never-referenced script is a strip candidate — decide by REFERENCE, not by name. |

`scripts/`, `agents/`, `commands/`, `skills/`, `hooks/`, `.claude-plugin/`,
`.git`, `.gitmodules`, and `templates/` are additionally in the
**`_RESERVED_SRCS`** set of the `cpv_strip_dev.py` engine — they can never
be moved into a strip submodule even if a user mis-configures
`cpv.strip.extract[]`.

## The ${CLAUDE_PLUGIN_ROOT} reference rule

Anything a hook / MCP / LSP / monitor / skill / agent references through
`${CLAUDE_PLUGIN_ROOT}/<path>` is runtime-essential, regardless of where it
lives or what it looks like. The engine resolves the runtime-essential set
by scanning these surfaces for `${CLAUDE_PLUGIN_ROOT}` references and marks
every referenced path ship-always:

- `.claude-plugin/plugin.json` (component path fields + any inline configs)
- `hooks/hooks.json` (every `command` / args)
- `.mcp.json` (`command`, `args`, `env`)
- `.lsp.json` (`command`, `args`, `env`)
- `monitors/monitors.json` (`command`)
- skill bodies and agent bodies (inline `${CLAUDE_PLUGIN_ROOT}` references)

This is why a `scripts/format-code.sh` named in a `PostToolUse` hook is
ship-always, while a `scripts/build_release.py` that nothing references at
runtime is a `DEV_ONLY` strip candidate.

## Why bin/ ships but its source does not

`bin/` holds the compiled, ready-to-run binaries. Claude Code adds `bin/`
to the Bash tool's `PATH`, so those binaries are invokable as bare
commands while the plugin is enabled — they MUST ship.

The SOURCE that builds those binaries (a `rust/` crate, a `Sources/` Swift
tree, `*.go` packages) is a different story: it only PRODUCES `bin/` at
build time and is never executed at runtime. That source is `BUILD_SOURCE`
— a strip candidate. The diagnostic keeps `bin/` ship-always and flags only
the build source.

## Conventional small root files

These conventional root files are ship-always (they are tiny and either
expected by tooling or carry license/changelog/lint config):

`LICENSE`, `CHANGELOG.md`, `README*` (`README.md`, `README.rst`, ...),
`.gitignore`, `.gitmodules`, `.mega-linter.yml`, `.markdownlint.json`,
`cliff.toml`.

A root `CLAUDE.md` is NOT loaded as context by Claude Code (per the
plugins-reference: "A `CLAUDE.md` file at the plugin root is not loaded as
project context"). It is tiny, harmless, and conventional, so the
diagnostic does not flag it as mass — but be aware it does not function as
runtime context the way a project `CLAUDE.md` does.

## The two path variables that drive the taxonomy

The plugins-reference defines two path variables that the strip / lean
remediations build on:

- **`${CLAUDE_PLUGIN_ROOT}`** — the absolute path to the plugin's install
  directory. Used to reference SHIPPED scripts, binaries, and configs. This
  path changes on every plugin update; never write state here.
- **`${CLAUDE_PLUGIN_DATA}`** — a persistent per-plugin directory that
  survives updates (`~/.claude/plugins/data/<id>/`). The plugins-reference
  explicitly recommends it "for installed dependencies such as
  `node_modules` or Python virtual environments, generated code, caches".
  This is the destination for the `RUNTIME_DEP` install-on-first-use
  pattern: a `SessionStart` hook populates `${CLAUDE_PLUGIN_DATA}` instead
  of shipping the dependency tree.

The canonical npm install-on-first-use `SessionStart` hook from the
plugins-reference:

```json
{
  "hooks": {
    "SessionStart": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "diff -q \"${CLAUDE_PLUGIN_ROOT}/package.json\" \"${CLAUDE_PLUGIN_DATA}/package.json\" >/dev/null 2>&1 || (cd \"${CLAUDE_PLUGIN_DATA}\" && cp \"${CLAUDE_PLUGIN_ROOT}/package.json\" . && npm install) || rm -f \"${CLAUDE_PLUGIN_DATA}/package.json\""
          }
        ]
      }
    ]
  }
}
```

The `diff` exits nonzero on first run AND whenever an update changes the
manifest, so the dependency tree installs once and reinstalls only when the
manifest changes. The trailing `rm` makes a failed install retry next
session.
