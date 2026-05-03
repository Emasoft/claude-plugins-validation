# CPV Main-Menu Tree (numbered-table edition)

## Table of Contents

- [Shell prologue](#shell-prologue)
- [Table-rendering rules](#table-rendering-rules)
- [Menu definitions](#menu-definitions)
- [Etiquette and error handling](#etiquette-and-error-handling)

## Shell prologue

Every leaf that produces a report uses this shell prologue:

```bash
if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  MAIN_ROOT="$(git worktree list | head -n1 | awk '{print $1}')"
else
  MAIN_ROOT="${CLAUDE_PROJECT_DIR:-$PWD}"
fi
TS="$(date +%Y%m%d_%H%M%S%z)"
SLUG="$(basename "$TARGET_PATH")"
LAUNCHER="${CLAUDE_PLUGIN_ROOT}/scripts/remote_validation.py"
mkdir -p "$MAIN_ROOT/reports/<component>"
REPORT_FILE="$MAIN_ROOT/reports/<component>/$TS-$SLUG.md"
```

## Table-rendering rules

Every menu is rendered as a Unicode box-drawing table. The user picks an
option by typing the number in their next message. NEVER use
`AskUserQuestion` for menu navigation.

### Canonical layout

- **Header row** uses heavy box-drawing characters (`┏━┳━┓` / `┡━╇━┩`).
- **Data rows** use light characters (`│ │ │`).
- **Row separators between EVERY data row** (`├─┼─┤`) — this makes long
  multi-column tables readable. NO exceptions: every row gets a separator
  above and below, even when descriptions are one line.
- **Footer** is a single line below the table: `Type a number to choose:`.
- **Cancel / Exit** is ALWAYS the LAST row, numbered `0`.
- **Back** (sub-menus only) is the second-to-last row, numbered `B` (a
  letter, so it doesn't collide with multi-digit option numbers like
  `9`/`19`/`24` in long menus). Both `0` and `B` are case-insensitive.
- Column widths fit the longest entry; pad with spaces.
- Standard columns: `#` (1-3 chars wide) / `Option` / `What it does`. Add
  a 4th column for `Pros / Cons / Cost / Risk / When to pick` whenever it
  helps the user choose (semantic-validation cost, security scanner
  inventory, etc.).
- Use full-width separators wider than 80 chars when needed; do not
  truncate descriptions to fit a narrow window — the user can scroll.

### Reference template (paste into the agent's output verbatim, then customize)

```
┏━━━┳━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ # ┃ Option               ┃ What it does                                           ┃
┡━━━╇━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ 1 │ <option name>        │ <one-line description>                                 │
├───┼──────────────────────┼────────────────────────────────────────────────────────┤
│ 2 │ <option name>        │ <one-line description>                                 │
├───┼──────────────────────┼────────────────────────────────────────────────────────┤
│ … │                      │                                                        │
├───┼──────────────────────┼────────────────────────────────────────────────────────┤
│ B │ Back                 │ Return to the previous menu                            │
├───┼──────────────────────┼────────────────────────────────────────────────────────┤
│ 0 │ Cancel / Exit        │ Terminate without action                               │
└───┴──────────────────────┴────────────────────────────────────────────────────────┘
Type a number (or B for back, 0 to cancel):
```

For top-level menus (no parent), drop the `B — Back` row but keep `0`.

### Project-type auto-detection (helper for path-accepting leaves)

Whenever a Validate / Fix / Cache / Security leaf accepts a path, the
orchestrator MUST first probe the path to decide what it is:

```bash
TARGET="<user-supplied-path>"
PLUGIN_HERE=0; MULTI_PLUGIN=0; SUBMODULES=0; MARKETPLACE_HERE=0
[ -f "$TARGET/.claude-plugin/plugin.json" ] && PLUGIN_HERE=1
[ -f "$TARGET/.claude-plugin/marketplace.json" ] && MARKETPLACE_HERE=1
# Multi-plugin workspace: 2+ children each containing .claude-plugin/plugin.json
N_CHILD_PLUGINS=$(find "$TARGET" -mindepth 2 -maxdepth 3 -type f -name 'plugin.json' \
  -path '*/.claude-plugin/*' 2>/dev/null | wc -l | tr -d ' ')
[ "$N_CHILD_PLUGINS" -ge 2 ] && MULTI_PLUGIN=1
# Submodules: .gitmodules present at root
[ -f "$TARGET/.gitmodules" ] && SUBMODULES=1
```

Then act based on the flags:

- **Single plugin** (`PLUGIN_HERE=1`, `MULTI_PLUGIN=0`) → proceed with the
  validation directly on `$TARGET`.
- **Single marketplace** (`MARKETPLACE_HERE=1`, `PLUGIN_HERE=0`) → proceed
  with marketplace validation.
- **Layout C** (`PLUGIN_HERE=1` AND `MARKETPLACE_HERE=1`) → tell the user
  this is a marketplace-in-plugin layout and ask which view they want via
  a small numbered table (`1 — As a plugin / 2 — As a marketplace / 0 — Cancel`).
- **Multi-plugin workspace** (`MULTI_PLUGIN=1`) → list the child plugins as
  rows in a numbered table and let the user pick one OR pick `A — Scan all`
  to iterate every child.
- **Has git submodules** (`SUBMODULES=1`) → list the submodule paths in a
  numbered table; let the user pick one or `A — Scan all submodules`. Also
  include a row for "Treat root as a single plugin" (in case the submodules
  are unrelated to the validation goal).
- **None of the above** (no `.claude-plugin/`, no submodules, multiple `*.md`
  files at any depth) → suggest `--loose` mode and offer to run a flat-pack
  scan via `validate_security.py --loose` (v2.48+).

The detection MUST run BEFORE invoking any validator. The user MUST always
have a `0 — Cancel / Exit` option in any sub-table the detection presents.

## Menu definitions

### 3.0 Top-level menu (8 categories + Cancel)

```
┏━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ # ┃ Category                ┃ What it does                                                          ┃
┡━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ 1 │ Validate                │ Run a CPV validator (plugin/skill/cache/marketplace/scope/component)  │
│ 2 │ Validate from GitHub    │ Clone owner/repo to tmpdir, scan, clean up                            │
│ 3 │ Fix                     │ Apply mechanical fixes from a validation report                       │
│ 4 │ Create                  │ Scaffold a new plugin or marketplace from scratch                     │
│ 5 │ Manage                  │ List, install, doctor, install scanners, bump version                 │
│ 6 │ GitHub setup            │ Branch protection rules, link plugin to marketplace                   │
│ 7 │ Deep semantic analysis  │ Opus A-F grading (expensive — confirms cost first)                    │
│ 8 │ Help / About            │ Category overview, command list, CPV version                          │
│ 0 │ Cancel / Exit           │ Terminate without action                                              │
└───┴─────────────────────────┴───────────────────────────────────────────────────────────────────────┘
Type a number to choose:
```

---

### Post-validate flow (applies to every leaf in §3.1 and §3.2)

After a leaf in §3.1 or §3.2 finishes and the report is on disk, the
orchestrator MUST print the §3.10 post-validate fix menu (NEVER the
generic §3.9 "do something else" table). This is non-negotiable: the user
always gets the explicit "fix N or end" choice after a validation, never
just "what's next?".

### 3.1 Validate sub-menu (24 explicit choices + Back + Cancel)

When the user reaches this menu, the orchestrator first prints this
table. Every option that takes a path triggers the **project-type
auto-detection** (see "Project-type auto-detection" above) BEFORE
invoking the underlying validator.

```
┏━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ #  ┃ What to validate                                ┃ What it does                                                                          ┃
┡━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│  1 │ Plugin (full, all 17 sub-validators)            │ Validate every component of a plugin directory                                        │
├────┼─────────────────────────────────────────────────┼───────────────────────────────────────────────────────────────────────────────────────┤
│  2 │ Skill                                           │ Single SKILL.md (frontmatter + structure + 190+ rules)                                │
├────┼─────────────────────────────────────────────────┼───────────────────────────────────────────────────────────────────────────────────────┤
│  3 │ Agent                                           │ Single agent .md (frontmatter, model, tools, examples, 2+ <example> blocks)          │
├────┼─────────────────────────────────────────────────┼───────────────────────────────────────────────────────────────────────────────────────┤
│  4 │ Command                                         │ Single command .md (frontmatter, agent ref, allowed-tools, argument-hint)             │
├────┼─────────────────────────────────────────────────┼───────────────────────────────────────────────────────────────────────────────────────┤
│  5 │ Hook                                            │ hooks.json structure + matchers + 28 valid event names + script linting               │
├────┼─────────────────────────────────────────────────┼───────────────────────────────────────────────────────────────────────────────────────┤
│  6 │ MCP server                                      │ .mcp.json or inline mcpServers (transport, env, security checks)                      │
├────┼─────────────────────────────────────────────────┼───────────────────────────────────────────────────────────────────────────────────────┤
│  7 │ LSP server                                      │ lspServers in plugin.json (binary path, init args, transport)                         │
├────┼─────────────────────────────────────────────────┼───────────────────────────────────────────────────────────────────────────────────────┤
│  8 │ Output-style                                    │ .claude/output-styles/*.md frontmatter (validated via project-scope alias)            │
├────┼─────────────────────────────────────────────────┼───────────────────────────────────────────────────────────────────────────────────────┤
│  9 │ Rule (Cursor-style .md rule files)              │ .claude/rules/*.md frontmatter + content                                              │
├────┼─────────────────────────────────────────────────┼───────────────────────────────────────────────────────────────────────────────────────┤
│ 10 │ Marketplace — LOCAL folder                      │ marketplace.json + plugin entries on disk                                             │
├────┼─────────────────────────────────────────────────┼───────────────────────────────────────────────────────────────────────────────────────┤
│ 11 │ Marketplace — REMOTE GitHub (owner/repo)        │ Clone github:owner/repo with --depth 1, validate, clean up                            │
├────┼─────────────────────────────────────────────────┼───────────────────────────────────────────────────────────────────────────────────────┤
│ 12 │ Marketplace — REMOTE arbitrary git URL          │ git clone any URL (gitlab/bitbucket/self-hosted/SSH/HTTPS), validate, clean up        │
├────┼─────────────────────────────────────────────────┼───────────────────────────────────────────────────────────────────────────────────────┤
│ 13 │ Settings: extraKnownMarketplaces inline         │ The block inside settings.json (different schema from marketplace.json)               │
├────┼─────────────────────────────────────────────────┼───────────────────────────────────────────────────────────────────────────────────────┤
│ 14 │ Project scope (.claude/ git-tracked)            │ settings.json, agents/skills/commands/rules/output-styles, .mcp.json                  │
├────┼─────────────────────────────────────────────────┼───────────────────────────────────────────────────────────────────────────────────────┤
│ 15 │ Local scope (.claude/ non-tracked)              │ settings.local.json, gitignored elements, ~/.claude.json per-project state            │
├────┼─────────────────────────────────────────────────┼───────────────────────────────────────────────────────────────────────────────────────┤
│ 16 │ Security — sub-menu                             │ Drill into security-only scans (full pass, single scanner, marketplace-wide, etc.)    │
├────┼─────────────────────────────────────────────────┼───────────────────────────────────────────────────────────────────────────────────────┤
│ 17 │ Cache — sub-menu                                │ Drill into cache-pattern audits (CA-01..CA-06) and cache-aware refactor              │
├────┼─────────────────────────────────────────────────┼───────────────────────────────────────────────────────────────────────────────────────┤
│ 18 │ Cross-references (xref)                         │ Stale links between agents/skills/commands; broken `references/`                      │
├────┼─────────────────────────────────────────────────┼───────────────────────────────────────────────────────────────────────────────────────┤
│ 19 │ Documentation                                   │ README, frontmatter docs, structure rules                                             │
├────┼─────────────────────────────────────────────────┼───────────────────────────────────────────────────────────────────────────────────────┤
│ 20 │ Encoding                                        │ UTF-8 / BOM / line endings on all .md/.json/.yaml                                     │
├────┼─────────────────────────────────────────────────┼───────────────────────────────────────────────────────────────────────────────────────┤
│ 21 │ Enterprise                                      │ Compliance / governance / managed-settings.d/ schema                                  │
├────┼─────────────────────────────────────────────────┼───────────────────────────────────────────────────────────────────────────────────────┤
│ 22 │ Scoring                                         │ Auto-scoring system check (severity rollups, exit-code wiring)                        │
├────┼─────────────────────────────────────────────────┼───────────────────────────────────────────────────────────────────────────────────────┤
│ 23 │ Lint pass (ruff/mypy/shellcheck)                │ Lint every Python/Bash script in the plugin's scripts/ folder                         │
├────┼─────────────────────────────────────────────────┼───────────────────────────────────────────────────────────────────────────────────────┤
│ 24 │ Telemetry hazards                               │ CRITICAL env-var leak rules (PLUGIN_SEED_DIR, SHELL_PREFIX, …)                        │
├────┼─────────────────────────────────────────────────┼───────────────────────────────────────────────────────────────────────────────────────┤
│  B │ Back                                            │ Return to top-level menu                                                              │
├────┼─────────────────────────────────────────────────┼───────────────────────────────────────────────────────────────────────────────────────┤
│  0 │ Cancel / Exit                                   │ Terminate without action                                                              │
└────┴─────────────────────────────────────────────────┴───────────────────────────────────────────────────────────────────────────────────────┘
Type a number (or B for back, 0 to cancel):
```

All leaves below FIRST run the project-type detection (see top of file)
on the resolved path, then drill in. Per-leaf recipes:

#### 3.1.1 Plugin (full)

- **arg-prompt**: `Path to the plugin? (e.g. ~/Code/my-plugin/ — or just the plugin name for auto-discovery)`
- **execution**:
  ```bash
  CLAUDE_PRIVATE_USERNAMES="$(whoami)" uv run --with pyyaml \
    python "$LAUNCHER" plugin "$TARGET_PATH" --report "$MAIN_ROOT/reports/validate_plugin/$TS-$SLUG.md"
  ```

#### 3.1.2 Skill

- **arg-prompt**: `Path to the skill directory? (e.g. ./skills/my-skill/)`
- **execution**:
  ```bash
  CLAUDE_PRIVATE_USERNAMES="$(whoami)" uv run --with pyyaml \
    python "$LAUNCHER" skill "$TARGET_PATH" --report "$MAIN_ROOT/reports/validate_skill/$TS-$SLUG.md"
  ```

#### 3.1.3 Agent

- **arg-prompt**: `Path to the agent .md file? (e.g. ./agents/my-agent.md)`
- **execution**:
  ```bash
  uv run --with pyyaml python "$LAUNCHER" agent "$TARGET_PATH" \
    --report "$MAIN_ROOT/reports/validate_agent/$TS-$SLUG.md"
  ```

#### 3.1.4 Command

- **arg-prompt**: `Path to the command .md file? (e.g. ./commands/my-command.md)`
- **execution**:
  ```bash
  uv run --with pyyaml python "$LAUNCHER" command "$TARGET_PATH" \
    --report "$MAIN_ROOT/reports/validate_command/$TS-$SLUG.md"
  ```

#### 3.1.5 Hook

- **arg-prompt**: `Path to hooks.json (or to the plugin root containing hooks/hooks.json)?`
- **execution**:
  ```bash
  uv run --with pyyaml python "$LAUNCHER" hook "$TARGET_PATH" \
    --report "$MAIN_ROOT/reports/validate_hook/$TS-$SLUG.md"
  ```

#### 3.1.6 MCP server

- **arg-prompt**: `Path to the plugin (or to .mcp.json directly)?`
- **execution**:
  ```bash
  uv run --with pyyaml python "$LAUNCHER" mcp "$TARGET_PATH" \
    --report "$MAIN_ROOT/reports/validate_mcp/$TS-$SLUG.md"
  ```

#### 3.1.7 LSP server

- **arg-prompt**: `Path to the plugin?`
- **execution**:
  ```bash
  uv run --with pyyaml python "$LAUNCHER" lsp "$TARGET_PATH" \
    --report "$MAIN_ROOT/reports/validate_lsp/$TS-$SLUG.md"
  ```

#### 3.1.8 Output-style

- **arg-prompt**: `Path to the project root? (validates .claude/output-styles/*.md frontmatter via the project-scope alias)`
- **execution**:
  ```bash
  uv run --with pyyaml python "$LAUNCHER" project-scope "$TARGET_PATH" \
    --report "$MAIN_ROOT/reports/validate_project_scope/$TS-$SLUG.md"
  ```
- **note**: there is no dedicated `validate_output_style.py`; output-style
  files are checked as part of `project-scope` validation.

#### 3.1.9 Rule (Cursor-style .md rule files)

- **arg-prompt**: `Path to the plugin (or directly to a .claude/rules/ folder)?`
- **execution**:
  ```bash
  uv run --with pyyaml python "$LAUNCHER" rules "$TARGET_PATH" \
    --report "$MAIN_ROOT/reports/validate_rules/$TS-$SLUG.md"
  ```

#### 3.1.10 Marketplace — LOCAL folder

- **arg-prompt**: `Path to the marketplace folder? (containing .claude-plugin/marketplace.json)`
- **execution**:
  ```bash
  uv run --with pyyaml python "$LAUNCHER" marketplace "$TARGET_PATH" --strict \
    --report "$MAIN_ROOT/reports/validate_marketplace/$TS-$SLUG.md"
  ```

#### 3.1.11 Marketplace — REMOTE GitHub

- **arg-prompt**: `GitHub spec? (owner/repo or https://github.com/owner/repo)`
- **execution**:
  ```bash
  uv run --with pyyaml python "$LAUNCHER" github --marketplace "$REPO" \
    --report "$MAIN_ROOT/reports/validate_github_marketplace/$TS-$(echo "$REPO" | tr '/' '_').md"
  ```

#### 3.1.12 Marketplace — REMOTE arbitrary git URL (gitlab/bitbucket/self-hosted/SSH)

- **arg-prompt**: `Git URL? (any URL git can clone — https://gitlab.example.com/group/repo, git@host:org/repo.git, etc.)`
- **execution**: clone, validate, clean up:
  ```bash
  TMPDIR_X=$(mktemp -d -t cpv-mkt-XXXXXX)
  trap 'rm -rf "$TMPDIR_X"' EXIT
  i=0; until git -c http.lowSpeedLimit=100 -c http.lowSpeedTime=300 clone --depth 1 "$GIT_URL" "$TMPDIR_X/repo"; do
    i=$((i+1)); [ $i -ge 30 ] && exit 1; sleep 6
  done
  uv run --with pyyaml python "$LAUNCHER" marketplace "$TMPDIR_X/repo" --strict \
    --report "$MAIN_ROOT/reports/validate_marketplace/$TS-$(basename "$GIT_URL" .git).md"
  ```
- **note**: respects `~/.claude/rules/github-timeouts.md` retry pattern. The
  temp checkout is cleaned up via the `trap` regardless of validation
  outcome.

#### 3.1.13 Settings: extraKnownMarketplaces inline

- **arg-prompt**: `Path to settings.json containing the inline marketplaces block?`
- **execution**:
  ```bash
  uv run --with pyyaml python "$LAUNCHER" settings-marketplace "$TARGET_PATH" --strict \
    --report "$MAIN_ROOT/reports/validate_settings_marketplace/$TS-$SLUG.md"
  ```

#### 3.1.14 Project scope

- **arg-prompt**: `Path to the project root? (validates git-tracked elements: settings.json, agents/skills/commands/rules/output-styles, .mcp.json)`
- **execution**:
  ```bash
  uv run --with pyyaml python "$LAUNCHER" project-scope "$TARGET_PATH" \
    --report "$MAIN_ROOT/reports/validate_project_scope/$TS-$SLUG.md"
  ```

#### 3.1.15 Local scope

- **arg-prompt**: `Path to the project root? (validates non-git-tracked elements: settings.local.json, gitignored components, ~/.claude.json per-project state)`
- **execution**:
  ```bash
  uv run --with pyyaml python "$LAUNCHER" local-scope "$TARGET_PATH" \
    --report "$MAIN_ROOT/reports/validate_local_scope/$TS-$SLUG.md"
  ```

#### 3.1.16 Security — drill into sub-menu

See §3.16 below.

#### 3.1.17 Cache — drill into sub-menu

See §3.17 below.

#### 3.1.18 Cross-references (xref)

- **arg-prompt**: `Path to the plugin?`
- **execution**:
  ```bash
  uv run --with pyyaml python "$LAUNCHER" xref "$TARGET_PATH" \
    --report "$MAIN_ROOT/reports/validate_xref/$TS-$SLUG.md"
  ```

#### 3.1.19 Documentation

- **arg-prompt**: `Path to the plugin?`
- **execution**:
  ```bash
  uv run --with pyyaml python "$LAUNCHER" docs "$TARGET_PATH" \
    --report "$MAIN_ROOT/reports/validate_documentation/$TS-$SLUG.md"
  ```

#### 3.1.20 Encoding

- **arg-prompt**: `Path to the plugin?`
- **execution**:
  ```bash
  uv run --with pyyaml python "$LAUNCHER" encoding "$TARGET_PATH" \
    --report "$MAIN_ROOT/reports/validate_encoding/$TS-$SLUG.md"
  ```

#### 3.1.21 Enterprise

- **arg-prompt**: `Path to the plugin?`
- **execution**:
  ```bash
  uv run --with pyyaml python "$LAUNCHER" enterprise "$TARGET_PATH" \
    --report "$MAIN_ROOT/reports/validate_enterprise/$TS-$SLUG.md"
  ```

#### 3.1.22 Scoring

- **arg-prompt**: `Path to the plugin?`
- **execution**:
  ```bash
  uv run --with pyyaml python "$LAUNCHER" scoring "$TARGET_PATH" \
    --report "$MAIN_ROOT/reports/validate_scoring/$TS-$SLUG.md"
  ```

#### 3.1.23 Lint pass

- **arg-prompt**: `Path to the plugin?`
- **execution**:
  ```bash
  uv run --with pyyaml python "$LAUNCHER" lint "$TARGET_PATH" \
    --report "$MAIN_ROOT/reports/lint/$TS-$SLUG.md"
  ```

#### 3.1.24 Telemetry hazards

- **arg-prompt**: `Path to the plugin?`
- **execution**:
  ```bash
  uv run --with pyyaml python "$LAUNCHER" telemetry "$TARGET_PATH" \
    --report "$MAIN_ROOT/reports/validate_telemetry/$TS-$SLUG.md"
  ```

---

### 3.2 Validate from GitHub sub-menu

```
┏━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ # ┃ Source                    ┃ What it does                                                      ┃
┡━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ 1 │ Plugin from GitHub        │ Clone owner/repo, validate plugin, clean up                       │
│ 2 │ Marketplace from GitHub   │ Clone owner/repo, validate marketplace, clean up                  │
│ 9 │ Back                      │ Return to top-level menu                                          │
│ 0 │ Cancel / Exit             │ Terminate without action                                          │
└───┴───────────────────────────┴───────────────────────────────────────────────────────────────────┘
Type a number to choose:
```

#### 3.2.1 Plugin from GitHub

- **arg-prompts** (in order):
  1. `GitHub spec? (owner/repo or https://github.com/owner/repo)`
  2. `Run security --audit too? (yes/no)`
- **execution** (without audit):
  ```bash
  CLAUDE_PRIVATE_USERNAMES="$(whoami)" uv run --with pyyaml \
    python "$LAUNCHER" github --plugin "$REPO" --report "$MAIN_ROOT/reports/validate_github_plugin/$TS-$(echo "$REPO" | tr '/' '_').md"
  ```
- **execution** (with audit): append `--audit` before `--report`.
- **fallback** (security-only direct URL ingestion, v2.48+):
  ```bash
  CLAUDE_PRIVATE_USERNAMES="$(whoami)" uv run --with pyyaml \
    python "$LAUNCHER" security "https://github.com/$REPO" --report "$REPORT_FILE"
  ```

#### 3.2.2 Marketplace from GitHub

- **arg-prompts**: same as 3.2.1
- **execution**:
  ```bash
  CLAUDE_PRIVATE_USERNAMES="$(whoami)" uv run --with pyyaml \
    python "$LAUNCHER" github --marketplace "$REPO" --report "$MAIN_ROOT/reports/validate_github_marketplace/$TS-$(echo "$REPO" | tr '/' '_').md"
  ```

---

### 3.3 Fix sub-menu

```
┏━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ # ┃ Operation                ┃ What it does                                                       ┃
┡━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ 1 │ Fix plugin findings      │ From a report file OR a plugin path (uses plugin-fixer agent)      │
│ 2 │ Fix marketplace findings │ From a report OR marketplace path (uses marketplace-fixer agent)   │
│ 3 │ Cache optimize           │ Audit + fix loop for CA-01..CA-06 (uses cache-optimizer-agent)     │
│ 9 │ Back                     │ Return to top-level menu                                           │
│ 0 │ Cancel / Exit            │ Terminate without action                                           │
└───┴──────────────────────────┴────────────────────────────────────────────────────────────────────┘
Type a number to choose:
```

#### 3.3.1 Fix plugin findings

- **arg-prompt**: `Path to a validation report .md file OR a plugin directory?`
- **execution**: dispatch the **plugin-fixer agent** with the path. The agent owns the validate→fix→re-validate loop.

#### 3.3.2 Fix marketplace findings

- **arg-prompt**: `Path to a marketplace validation report OR a marketplace directory?`
- **execution**: dispatch the **marketplace-fixer agent**. Handles mechanical fixes AND architectural migrations (Layout A↔B↔C).

#### 3.3.3 Cache optimize

- **arg-prompts** (in order):
  1. `Path to plugin or project root?`
  2. `Also do broader cache-aware refactoring? (yes/no — --broader invokes Phase 4)`
- **execution**: dispatch the **cache-optimizer-agent** with the path and `--broader` flag if requested.

---

### 3.4 Create sub-menu

```
┏━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ # ┃ Scaffold                    ┃ What it does                                                     ┃
┡━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ 1 │ Scaffold a new plugin       │ Generate full plugin repo (uses plugin-creator agent)            │
│ 2 │ Scaffold a new marketplace  │ Generate marketplace hub (uses plugin-creator agent)             │
│ 9 │ Back                        │ Return to top-level menu                                         │
│ 0 │ Cancel / Exit               │ Terminate without action                                         │
└───┴─────────────────────────────┴──────────────────────────────────────────────────────────────────┘
Type a number to choose:
```

#### 3.4.1 Scaffold a new plugin

- **arg-prompts** (in order):
  1. `Plugin name?`
  2. `Target directory?`
  3. `Layout (A=hub-and-spoke / B=nested monorepo / C=marketplace-in-plugin self-referential)?`
- **execution**: dispatch the **plugin-creator agent** with the answers.

#### 3.4.2 Scaffold a new marketplace

- **arg-prompts** (in order):
  1. `Marketplace name?`
  2. `Target directory?`
  3. `Owner GitHub username?`
- **execution**: dispatch the **plugin-creator agent** in marketplace mode.

---

### 3.5 Manage sub-menu

```
┏━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ # ┃ Operation                         ┃ What it does                                                    ┃
┡━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ 1 │ List installed plugins            │ Show every plugin Claude Code knows about                       │
│ 2 │ Install / update / enable / dis   │ Dispatches plugin-manager agent (its First Contact menu picks)  │
│ 3 │ Doctor (health check)             │ Probe registry, settings, cache for orphans                     │
│ 4 │ Install all external scanners     │ Batch-install cc-audit/tirith/trufflehog/semgrep/Cisco/fclones  │
│ 5 │ Prune old plugin cache versions   │ Free disk space — keep active version, delete older             │
│ 6 │ Bump version + publish            │ patch / minor / major (delegates to publish.py)                 │
│ 7 │ Show CPV version                  │ Read .claude-plugin/plugin.json                                 │
│ 8 │ Refresh README AUTO-COMPONENTS    │ Re-render the plugin README components table from filesystem    │
│10 │ Standardize plugin (force-tpl)    │ Force-overwrite publish.py + CI + retry helpers from canonical  │
│11 │ Add component (skill/agent/cmd)   │ Add new skill/agent/command/hook/mcp to an existing plugin      │
│12 │ Strip dev parts (submodule)       │ Move tests/ to per-plugin git submodule (PSS pattern)           │
│13 │ Migrate marketplace (source.url)  │ Normalize source.url → source.repo + detect dead 404 entries    │
│ 9 │ Back                              │ Return to top-level menu                                        │
│ 0 │ Cancel / Exit                     │ Terminate without action                                        │
└───┴───────────────────────────────────┴─────────────────────────────────────────────────────────────────┘
Type a number to choose:
```

#### 3.5.1 List installed plugins

- **execution**:
  ```bash
  uv run --with pyyaml python "$LAUNCHER" registry --list
  ```

#### 3.5.2 Install / update / enable / disable

- **execution**: dispatch the **plugin-manager agent**. The agent's First Contact menu (also a Unicode table) asks what operation to do.

#### 3.5.3 Doctor (health check)

- **arg-prompts** (in order):
  1. `Verbose output? (yes/no)`
  2. `Auto-fix orphaned entries? (yes/no — passes --fix)`
- **execution**:
  ```bash
  uv run --with pyyaml python "$LAUNCHER" doctor [--verbose] [--fix]
  ```

#### 3.5.4 Install all external scanners

- **arg-prompt**: `This will install cc-audit, tirith, trufflehog, semgrep, Cisco AI Defense skill-scanner, AND fclones via brew/snap/pipx/cargo (silent, idempotent, per-platform). Proceed? (yes/no)`
- **execution**: ALWAYS confirm first, then:
  ```bash
  uv run python "${CLAUDE_PLUGIN_ROOT}/scripts/manage_doctor.py" --install-scanners
  ```
- **note**: This is the ONLY direct invocation of `manage_doctor.py` for this leaf — `--install-scanners` is a one-shot bootstrap that doesn't need the launcher's environment isolation.

#### 3.5.5 Prune old plugin cache versions (v2.48)

- **arg-prompts** (in order):
  1. `First show DRY-RUN preview? (yes/no — recommended yes)`
  2. `Keep how many newest versions per plugin? (default: 1)`
  3. After dry-run preview prints: `Proceed with actual deletion? (yes/no)`
- **execution** (dry-run preview):
  ```bash
  uv run python "${CLAUDE_PLUGIN_ROOT}/scripts/manage_doctor.py" --prune-dry-run --prune-keep $KEEP_N
  ```
- **execution** (actual delete):
  ```bash
  uv run python "${CLAUDE_PLUGIN_ROOT}/scripts/manage_doctor.py" --prune-old-versions --prune-keep $KEEP_N
  ```
- **note**: The active version (whichever Claude Code's `enabledPlugins` references) is always kept, even if older than another cached version.

#### 3.5.6 Bump version + publish (current plugin)

- **arg-prompt**: `Bump type? (patch / minor / major)`
- **execution** (TRDD-bbff5bc5: publish.py is the canonical entry point):
  ```bash
  uv run python "${CLAUDE_PLUGIN_ROOT}/scripts/publish.py" --$BUMP_TYPE
  ```
- **note**: This runs the FULL pipeline — bump + manifest refresh +
  CHANGELOG + commit + push + GitHub release. For a local-only bump
  without push, the user should call `bump_version.py` directly (it's
  now a thin wrapper around `publish.bump_semver`).

#### 3.5.7 Show CPV version

- **execution**:
  ```bash
  cat "${CLAUDE_PLUGIN_ROOT}/.claude-plugin/plugin.json" | python3 -c 'import json,sys; print(json.load(sys.stdin)["version"])'
  ```

#### 3.5.8 Refresh README AUTO-COMPONENTS (Phase 5, v2.57.0+)

- **arg-prompt**: `Plugin path? (default: cwd)`
- **execution**:
  ```bash
  uv run python "${CLAUDE_PLUGIN_ROOT}/scripts/refresh_readme.py" "$PATH"
  ```
- **note**: Adds `<!-- BEGIN AUTO-COMPONENTS -->` block on first run;
  subsequent runs preserve placement and only update the body.

#### 3.5.10 Standardize plugin (force-templates) (Phase 2, v2.55.0+)

- **arg-prompt**: `Plugin path?`
- **arg-prompt**: `Run in --check mode first? (yes/no — recommended yes)`
- **execution** (check mode):
  ```bash
  uv run --with pyyaml python "${CLAUDE_PLUGIN_ROOT}/scripts/remote_validation.py" \
    standardize "$PATH" --fix --dry-run --force-templates
  ```
- **execution** (apply):
  ```bash
  uv run --with pyyaml python "${CLAUDE_PLUGIN_ROOT}/scripts/remote_validation.py" \
    standardize "$PATH" --fix --force-templates
  ```
- **note**: OVERWRITES infrastructure files (publish.py, ci/release/notify
  workflows, retry helpers, pre-push hook, cliff.toml, .mega-linter.yml)
  with the canonical CPV templates. Backs up each existing copy to
  `<file>.bak`. README, pyproject.toml, .gitignore are NEVER force-written.

#### 3.5.11 Add component (Phase 10, v2.61.0+)

- **arg-prompts** (in order):
  1. `Plugin path?`
  2. `Component type? (skill / agent / command / hook / mcp)`
  3. `Name? (for skill/agent/command/mcp)`
  4. `Description?`
  5. `(if hook)` `Event name? (e.g. PreToolUse, Stop)` and `Command to run?`
  6. `(if mcp)` `Stdio command OR HTTP URL?`
- **execution**:
  ```bash
  uv run python "${CLAUDE_PLUGIN_ROOT}/scripts/add_component.py" "$PATH" \
    --type "$TYPE" --name "$NAME" --description "$DESC" [--allowed-tools ...]
  ```

#### 3.5.12 Strip dev parts (submodule) (Phase 2, v2.52.0+)

- **arg-prompts** (in order):
  1. `Plugin path?`
  2. `Mode? (dry-run / check / auto)`
- **execution** (dry-run, no destructive ops):
  ```bash
  uv run python "${CLAUDE_PLUGIN_ROOT}/scripts/cpv_strip_dev.py" "$PATH" --dry-run
  ```
- **execution** (auto — DESTRUCTIVE):
  ```bash
  uv run python "${CLAUDE_PLUGIN_ROOT}/scripts/cpv_strip_dev.py" "$PATH" --auto
  ```
- **note**: --auto creates a `<owner>/<plugin>-tests` private GitHub repo,
  filters its history into the new repo, replaces the tests/ dir with a
  submodule mount. Idempotent state machine resumes crashed runs.
  ALWAYS run --dry-run first.

#### 3.5.13 Migrate marketplace (source.url → source.repo) (Phase 2.6, v2.59.0+)

- **arg-prompts** (in order):
  1. `Marketplace root path? (containing .claude-plugin/marketplace.json)`
  2. `Run in --check mode first? (yes/no — recommended yes)`
- **execution** (check mode — exits 1 if migrations would change file):
  ```bash
  uv run python "${CLAUDE_PLUGIN_ROOT}/scripts/migrate_marketplace.py" "$PATH" --check
  ```
- **execution** (apply atomically):
  ```bash
  uv run python "${CLAUDE_PLUGIN_ROOT}/scripts/migrate_marketplace.py" "$PATH"
  ```
- **note**: Probes each plugin entry's GitHub repo via `gh api` (retry-wrapped).
  Dead 404 entries are surfaced but NOT removed automatically — user decides.

---

### 3.6 GitHub setup sub-menu

```
┏━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ # ┃ Operation                            ┃ What it does                                              ┃
┡━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ 1 │ Branch protection (current repo)     │ Apply rules to the repo of `git remote get-url origin`    │
│ 2 │ Branch protection (generic owner/rp) │ Apply rules to a different owner/repo                     │
│ 3 │ Link plugin to a marketplace         │ Add the plugin to a marketplace's plugin list             │
│ 9 │ Back                                 │ Return to top-level menu                                  │
│ 0 │ Cancel / Exit                        │ Terminate without action                                  │
└───┴──────────────────────────────────────┴───────────────────────────────────────────────────────────┘
Type a number to choose:
```

#### 3.6.1 Branch protection (current repo)

- **execution**: invokes `/cpv-setup-branch-rules` workflow inline (no extra prompts — uses the current `git remote get-url origin`).

#### 3.6.2 Branch protection (generic owner/repo)

- **arg-prompt**: `Owner/repo slug?`
- **execution**: invokes `/cpv-setup-branch-rules-generic` workflow inline with the slug.

#### 3.6.3 Link plugin to a marketplace

- **arg-prompts** (in order):
  1. `Plugin repo (owner/repo)?`
  2. `Marketplace repo (owner/repo)?`
- **execution**: invokes `/cpv-link-plugin` workflow inline with the answers.

---

### 3.7 Deep semantic analysis (opus, EXPENSIVE)

```
┏━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ # ┃ Operation                  ┃ What it does                                                     ┃ Cost                    ┃
┡━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ 1 │ Confirm + run on a path    │ Opus A-F semantic grading of a skill / agent / whole plugin      │ 10-50× normal scan cost │
│ 9 │ Back                       │ Return to top-level menu                                         │ —                       │
│ 0 │ Cancel / Exit              │ Terminate without action                                         │ —                       │
└───┴────────────────────────────┴──────────────────────────────────────────────────────────────────┴─────────────────────────┘
Type a number to choose:
```

#### 3.7.1 Confirm + run

- **arg-prompts** (in order):
  1. `Semantic validation uses Opus with 1M context at max effort. Cost: ~10-50× normal. Proceed? (yes/no)`
  2. (only if yes) `Path to skill or agent or whole plugin?`
- **execution**: dispatch the **semantic-validator agent** with the path. The agent itself runs the syntactic baseline first then the semantic pass.

---

### 3.8 Help / About sub-menu

```
┏━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ # ┃ Help topic                          ┃ What it shows                                             ┃
┡━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ 1 │ Category overview                   │ Re-print the 8-row top-level table                        │
│ 2 │ List every CPV command              │ Walk commands/cpv-*.md and print name + description       │
│ 3 │ Show CPV plugin version             │ Read .claude-plugin/plugin.json                           │
│ 9 │ Back                                │ Return to top-level menu                                  │
│ 0 │ Cancel / Exit                       │ Terminate without action                                  │
└───┴─────────────────────────────────────┴───────────────────────────────────────────────────────────┘
Type a number to choose:
```

#### 3.8.1 Category overview

- **execution**: re-print the 3.0 top-level menu table.

#### 3.8.2 List every CPV command

- **execution**:
  ```bash
  for f in "${CLAUDE_PLUGIN_ROOT}"/commands/cpv-*.md; do
    name=$(basename "$f" .md)
    desc=$(awk '/^description:/{sub(/^description:[[:space:]]*/, ""); print; exit}' "$f")
    printf "%-42s %s\n" "/$name" "$desc"
  done
  ```

#### 3.8.3 Show CPV plugin version

- **execution**: same as 3.5.7.

---

### 3.9 End-of-leaf "do something else?" table (NON-validate flows)

After a Create / Manage / GitHub-setup / Help leaf finishes, print this 2-row
table and wait for the user's number:

```
┏━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ # ┃ Next                        ┃ What it does                                                   ┃
┡━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ 1 │ Do something else           │ Return to top-level menu                                       │
│ 0 │ Done (exit)                 │ Reply `Done.` and stop                                         │
└───┴─────────────────────────────┴────────────────────────────────────────────────────────────────┘
Type a number to choose:
```

---

### 3.16 Security sub-menu (drilled into from §3.1.16)

```
┏━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ #  ┃ Security scan target                            ┃ What it does                                                                                       ┃
┡━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│  1 │ Single plugin (full security pass)              │ All in-process rule packs + 5 external scanners (cc-audit, tirith, trufflehog, semgrep, Cisco)     │
├────┼─────────────────────────────────────────────────┼────────────────────────────────────────────────────────────────────────────────────────────────────┤
│  2 │ Single plugin from GitHub URL                   │ Auto-clone github.com URL → security pass → cleanup (v2.48 direct URL ingestion)                  │
├────┼─────────────────────────────────────────────────┼────────────────────────────────────────────────────────────────────────────────────────────────────┤
│  3 │ Single plugin from arbitrary git URL            │ git clone any URL (gitlab/SSH/self-hosted) → security pass → cleanup                              │
├────┼─────────────────────────────────────────────────┼────────────────────────────────────────────────────────────────────────────────────────────────────┤
│  4 │ Single plugin from local archive (.zip/.tar.gz) │ Extract → security pass → cleanup (v2.48 archive ingestion)                                       │
├────┼─────────────────────────────────────────────────┼────────────────────────────────────────────────────────────────────────────────────────────────────┤
│  5 │ Marketplace (every plugin, tree-scan-once)      │ v2.48 architecture: stage all plugins, fclones-dedup, run scanners ONCE, bucket per-plugin         │
├────┼─────────────────────────────────────────────────┼────────────────────────────────────────────────────────────────────────────────────────────────────┤
│  6 │ Loose / flat skill pack (--loose)               │ Skip the .claude-plugin/ precondition for SKILL_*.md packs                                        │
├────┼─────────────────────────────────────────────────┼────────────────────────────────────────────────────────────────────────────────────────────────────┤
│  7 │ Single scanner only (cc-audit)                  │ Only cc-audit (skip tirith/trufflehog/semgrep/Cisco/internal)                                     │
├────┼─────────────────────────────────────────────────┼────────────────────────────────────────────────────────────────────────────────────────────────────┤
│  8 │ Single scanner only (tirith)                    │ Only tirith policy engine                                                                          │
├────┼─────────────────────────────────────────────────┼────────────────────────────────────────────────────────────────────────────────────────────────────┤
│  9 │ Single scanner only (trufflehog)                │ Only trufflehog secret scanner (--concurrency on, gitleaks dropped in v2.48)                      │
├────┼─────────────────────────────────────────────────┼────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ 10 │ Single scanner only (semgrep)                   │ Only semgrep with p/security-audit + p/secrets rule packs                                         │
├────┼─────────────────────────────────────────────────┼────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ 11 │ Single scanner only (Cisco AI Defense)          │ Only the Cisco AI Defense skill-scanner (programmatic engines, no API key needed)                 │
├────┼─────────────────────────────────────────────────┼────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ 12 │ Telemetry hazards only                          │ Per-plugin env-var leak rules (PLUGIN_SEED_DIR, SHELL_PREFIX, OTEL_LOG_RAW_API_BODIES=file:*…)     │
├────┼─────────────────────────────────────────────────┼────────────────────────────────────────────────────────────────────────────────────────────────────┤
│  B │ Back                                            │ Return to the Validate sub-menu                                                                    │
├────┼─────────────────────────────────────────────────┼────────────────────────────────────────────────────────────────────────────────────────────────────┤
│  0 │ Cancel / Exit                                   │ Terminate without action                                                                           │
└────┴─────────────────────────────────────────────────┴────────────────────────────────────────────────────────────────────────────────────────────────────┘
Type a number (or B for back, 0 to cancel):
```

#### 3.16.1 Single plugin (full security pass)

- **arg-prompt**: `Path to the plugin?`
- **execution**:
  ```bash
  uv run --with pyyaml python "$LAUNCHER" security "$TARGET_PATH" \
    --report "$MAIN_ROOT/reports/validate_security/$TS-$SLUG.md"
  ```

#### 3.16.2 Plugin from github.com URL

- **arg-prompt**: `GitHub URL? (https://github.com/owner/repo or owner/repo)`
- **execution**:
  ```bash
  uv run --with pyyaml python "$LAUNCHER" security "https://github.com/$REPO" \
    --report "$MAIN_ROOT/reports/validate_security/$TS-$(echo "$REPO" | tr '/' '_').md"
  ```

#### 3.16.3 Plugin from arbitrary git URL

- **arg-prompt**: `Git URL? (gitlab.example.com, git@host:org/repo.git, etc.)`
- **execution**: clone-then-scan with retry-loop (see 3.1.12).

#### 3.16.4 Plugin from local archive

- **arg-prompt**: `Path to the .zip / .tar.gz / .tgz / .tar.bz2 / .tar.xz / .tar archive?`
- **execution**:
  ```bash
  uv run --with pyyaml python "$LAUNCHER" security "$ARCHIVE_PATH" \
    --report "$MAIN_ROOT/reports/validate_security/$TS-$(basename "$ARCHIVE_PATH").md"
  ```

#### 3.16.5 Marketplace tree-scan-once

- **arg-prompt**: `Marketplace spec? (local path, github:owner/repo, or arbitrary git URL)`
- **execution**:
  ```bash
  uv run --with pyyaml python "$LAUNCHER" security --marketplace "$SPEC" \
    --report "$MAIN_ROOT/reports/validate_security/$TS-marketplace-$(echo "$SPEC" | tr '/:' '_').md"
  ```

#### 3.16.6 Loose / flat skill pack

- **arg-prompt**: `Path to the flat skill pack? (folder of SKILL_*.md / *.md files without .claude-plugin/)`
- **execution**:
  ```bash
  uv run --with pyyaml python "$LAUNCHER" security "$TARGET_PATH" --loose \
    --report "$MAIN_ROOT/reports/validate_security/$TS-loose-$SLUG.md"
  ```

#### 3.16.7..3.16.11 Single-scanner modes

- **arg-prompts** (in order): `Path to the plugin?`
- **execution** (substitute `<scanner>` with `cc-audit`, `tirith`, `trufflehog`, `semgrep`, or `cisco`):
  ```bash
  uv run --with pyyaml python "$LAUNCHER" security "$TARGET_PATH" \
    --only-scanner <scanner> \
    --report "$MAIN_ROOT/reports/validate_security/$TS-<scanner>-$SLUG.md"
  ```
- **note**: `--only-scanner` is the v2.48 flag to short-circuit the scanner
  matrix; if it doesn't exist on the installed CPV version, fall back to
  the full pass and surface a one-line note that single-scanner isolation
  isn't available on this version.

#### 3.16.12 Telemetry hazards only

See §3.1.24 — same recipe.

---

### 3.17 Cache sub-menu (drilled into from §3.1.17)

```
┏━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ # ┃ Cache action                                  ┃ What it does                                                                                     ┃
┡━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ 1 │ Audit only (CA-01..CA-06)                     │ Pure read-only audit, produces report with per-rule findings                                     │
├───┼───────────────────────────────────────────────┼──────────────────────────────────────────────────────────────────────────────────────────────────┤
│ 2 │ Audit + auto-fix (loop)                       │ Audit, then dispatch cache-optimizer-agent to fix CA-01..CA-06 in priority order                 │
├───┼───────────────────────────────────────────────┼──────────────────────────────────────────────────────────────────────────────────────────────────┤
│ 3 │ Audit + broader cache-aware refactoring       │ Audit, fix CA-01..CA-06, then dispatch Phase 4 broader improvements (CLAUDE.md split, etc.)      │
├───┼───────────────────────────────────────────────┼──────────────────────────────────────────────────────────────────────────────────────────────────┤
│ 4 │ Apply --strict (MINOR + WARNING block too)    │ Same as 1 but exit non-zero when CA-04/05 (MINOR) or CA-06 (WARNING) findings exist              │
├───┼───────────────────────────────────────────────┼──────────────────────────────────────────────────────────────────────────────────────────────────┤
│ 5 │ Audit project root (not a plugin)             │ For project trees: scans .claude/ + CLAUDE.md (no .claude-plugin/ precondition)                  │
├───┼───────────────────────────────────────────────┼──────────────────────────────────────────────────────────────────────────────────────────────────┤
│ B │ Back                                          │ Return to the Validate sub-menu                                                                  │
├───┼───────────────────────────────────────────────┼──────────────────────────────────────────────────────────────────────────────────────────────────┤
│ 0 │ Cancel / Exit                                 │ Terminate without action                                                                         │
└───┴───────────────────────────────────────────────┴──────────────────────────────────────────────────────────────────────────────────────────────────┘
Type a number (or B for back, 0 to cancel):
```

#### 3.17.1 Audit only

- Same as §3.1.3 (legacy numbering — kept for compatibility).
- **execution**:
  ```bash
  uv run --with pyyaml python "$LAUNCHER" cache "$TARGET_PATH" \
    --report "$MAIN_ROOT/reports/validate_cache/$TS-$SLUG.md"
  ```

#### 3.17.2 Audit + auto-fix

- **arg-prompt**: `Path to plugin or project root?`
- **execution**: dispatch the **cache-optimizer-agent** with the path. The
  agent runs Phase 1 (audit) → Phase 2 (fix) → Phase 3 (re-validate)
  internally.

#### 3.17.3 Audit + broader refactoring

- **arg-prompt**: `Path to plugin or project root?`
- **execution**: dispatch the **cache-optimizer-agent** with the path AND
  the explicit `broader` keyword in the prompt. The agent runs Phase 1-3
  and THEN Phase 4 (CLAUDE.md split, dynamic-content migration, etc.).

#### 3.17.4 Strict mode

- **arg-prompt**: `Path to plugin or project root?`
- **execution**:
  ```bash
  uv run --with pyyaml python "$LAUNCHER" cache "$TARGET_PATH" --strict \
    --report "$MAIN_ROOT/reports/validate_cache/$TS-strict-$SLUG.md"
  ```

#### 3.17.5 Project root (not a plugin)

- Same recipe as 3.17.1 — the validator auto-handles project vs plugin
  trees and skips the `.claude-plugin/` precondition when not present.

---

### 3.10 Post-validate fix menu (MANDATORY after every Validate / Validate-from-GitHub leaf)

This table replaces §3.9 for ALL validate flows. It MUST be printed
unconditionally after a validate leaf finishes — even when the validation
verdict is PASS / VALID — so the user always has the explicit option to
end OR to fix any residual WARNINGs they care about.

If the validation finished completely clean (CRITICAL=0 MAJOR=0 MINOR=0
NIT=0 WARNING=0), still print the table. Rows 1-5 will simply find
nothing to fix when dispatched, and the fixer will exit clean — but the
user always sees the menu and is never auto-deflected.

```
┏━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ # ┃ Action                                          ┃ What it does                                                          ┃ Severities the fixer will touch ┃
┡━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ 1 │ Fix ALL issues (incl. WARNING)                  │ Dispatch the cpv-fixer agent on every finding in the report           │ CRITICAL+MAJOR+MINOR+NIT+WARNING │
│ 2 │ Fix NIT and higher                              │ Skip WARNING-only findings                                            │ CRITICAL+MAJOR+MINOR+NIT         │
│ 3 │ Fix MINOR and higher                            │ Skip NIT and WARNING                                                  │ CRITICAL+MAJOR+MINOR             │
│ 4 │ Fix MAJOR and higher                            │ Only fix the publish-blockers (and CRITICALs)                         │ CRITICAL+MAJOR                   │
│ 5 │ Fix CRITICAL only                               │ Strictest mode — fix the loaders/security blockers and nothing else   │ CRITICAL                         │
│ 0 │ End                                             │ Done — exit without running the fixer                                 │ —                                │
└───┴─────────────────────────────────────────────────┴───────────────────────────────────────────────────────────────────────┴──────────────────────────────────┘
Type a number to choose:
```

#### 3.10.1 Dispatching the fixer with a minimum severity

When the user picks rows 1-5, dispatch the **plugin-fixer agent** (or, for
marketplace reports, the **marketplace-fixer agent**; for cache reports,
the **cache-optimizer-agent**) with the report path and a `min_severity`
parameter. The agent honours the filter by skipping fixes for any finding
whose severity is BELOW the threshold.

| Row | `min_severity` value to pass | Agent prompt template |
|-----|-------------------------------|----------------------|
| 1 | `WARNING` | `Fix every finding in <REPORT_PATH>. min_severity=WARNING (fix everything including WARNINGs).` |
| 2 | `NIT` | `Fix findings in <REPORT_PATH>. min_severity=NIT (skip WARNING-only).` |
| 3 | `MINOR` | `Fix findings in <REPORT_PATH>. min_severity=MINOR (skip NIT and WARNING).` |
| 4 | `MAJOR` | `Fix findings in <REPORT_PATH>. min_severity=MAJOR (publish-blockers only).` |
| 5 | `CRITICAL` | `Fix findings in <REPORT_PATH>. min_severity=CRITICAL (strictest — only loader/security blockers).` |

After the fixer agent returns, print the §3.9 "do something else?" 2-row
table (Return to top-level / Done) and wait.

If the user picks `0` (End) → reply `Done.` and stop.

---

## Etiquette and error handling

### Cancel / Exit semantics

At ANY menu level, picking `0` (Cancel / Exit) → the orchestrator MUST:

1. Stop all further menu prompts.
2. Reply with exactly ONE line: `Cancelled — no actions taken.`
3. Not run any bash, not write any reports, not modify any files.

### Back semantics

In a sub-menu, picking `B` / `b` (Back) → re-print the PARENT menu's
table (typically 3.0 top-level). At the top-level menu there is no `B`
row. Some legacy sub-menus may still use `9` for Back where there is no
collision risk — both `B` and a numeric Back row work, but `B` is
preferred for any menu with more than 9 options.

### Argument-prompt etiquette

- ALWAYS ask required arguments as a single plain-text line — NEVER use AskUserQuestion.
- Example: `Path to the plugin to validate? (e.g. ~/Code/my-plugin/)`
- If the user provides an invalid path → re-ask with a hint, do not abort.
- If the user replies `0` or `cancel` or `exit` at the argument prompt → treat the same as a top-level Cancel.
- For paths, ALWAYS resolve `~` to `$HOME` and expand environment variables before invoking bash.

### Number-parsing rules

- Strip surrounding whitespace from the user's reply.
- Accept the literal letters `B` / `b` (Back) and `A` / `a` (Scan-all,
  used in detection sub-tables) — case-insensitive — before falling
  through to integer parsing.
- Take the FIRST integer found in the reply (so `1` and `1.` and `1)` all
  match row 1; `12` matches row 12, NOT row 1).
- If the user types text not starting with a digit/B/A but matching an
  option name (case-insensitive substring match on the `Option` column),
  accept it.
- Otherwise: print `Invalid choice. Pick a number from the table (or B for back, 0 to cancel).` and re-print the SAME table (do not jump back to top-level).

### Error handling

- If `${CLAUDE_PLUGIN_ROOT}` is unset → abort with:
  > "CPV plugin not installed in this session. Install via
  > `/plugin install claude-plugins-validation@emasoft-plugins`."
- If a launcher invocation exits non-zero → surface stderr verbatim, then re-print the SAME sub-menu table so the user can retry with different arguments.

### Token budget

- Never paste a full report into the response. Always return the report-file path and a 3-line summary (verdict + counts + path).
- Do not load `references/menu-tree.md` repeatedly — the orchestrator reads it once at session start.
- Use the launcher invocation table (above) verbatim — do not generate alternative bash spellings.
