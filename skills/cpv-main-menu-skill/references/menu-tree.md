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
┏━━━┳━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ # ┃ Option               ┃ What it does                                           ┃
┡━━━╇━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ 1 │ <option name>        │ <one-line description>                                 │
├───┼──────────────────────┼────────────────────────────────────────────────────────┤
│ 2 │ <option name>        │ <one-line description>                                 │
├───┼──────────────────────┼────────────────────────────────────────────────────────┤
│ … │                      │                                                        │
├───┼──────────────────────┼────────────────────────────────────────────────────────┤
│ A │ Ask the agent        │ Let the agent suggest the best next action right now   │
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

### 3.0a Path-source mini-menu (MANDATORY before every path-required leaf)

Claude Code's interactive UI does NOT let the user submit an empty
response — they cannot just "press Enter" to accept a default. So every
leaf that needs a path / name / URL MUST first print a small mini-menu
and route based on the user's number.

The mini-menu is **context-aware**: row 1 is always the most likely
choice for what $PWD looks like RIGHT NOW. The orchestrator inspects the
current directory before drawing the menu and picks the right shape from
the cases below.

#### Detection (run before drawing the menu)

```
1. Layout C       — root has BOTH .claude-plugin/plugin.json
                    AND .claude-plugin/marketplace.json
2. Marketplace    — root has ONLY .claude-plugin/marketplace.json
3. Plugin         — root has ONLY .claude-plugin/plugin.json
4. Multi-plugin   — root has N >= 2 immediate subdirs each containing
                    .claude-plugin/plugin.json (sibling-plugin layout)
5. Plain folder   — none of the above
```

#### Case 1 — Layout C (nested marketplace-in-plugin)

```
┏━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ # ┃ What to scan                                                               ┃
┡━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ 1 │ Whole repo (marketplace AND its bundled plugin together)                   │
│ 2 │ Just the plugin part of this repo                                          │
│ 3 │ Just the marketplace part of this repo                                     │
│ 4 │ Type a different path / name / URL                                         │
│ A │ Ask the agent for a recommendation                                         │
│ 0 │ Cancel / Exit                                                              │
└───┴────────────────────────────────────────────────────────────────────────────┘
```

#### Case 2 — Marketplace only

```
┏━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ # ┃ What to scan                                                               ┃
┡━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ 1 │ This marketplace AND every plugin it lists                                 │
│ 2 │ Just the marketplace listing (skip the plugins)                            │
│ 3 │ Type a different path / name / URL                                         │
│ A │ Ask the agent for a recommendation                                         │
│ 0 │ Cancel / Exit                                                              │
└───┴────────────────────────────────────────────────────────────────────────────┘
```

#### Case 3 — Plugin only (most common)

```
┏━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ # ┃ What to scan                                                               ┃
┡━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ 1 │ This plugin (the one in the current folder)                                │
│ 2 │ Type a different path / name / URL                                         │
│ A │ Ask the agent for a recommendation                                         │
│ 0 │ Cancel / Exit                                                              │
└───┴────────────────────────────────────────────────────────────────────────────┘
```

#### Case 4 — Multi-plugin project (N >= 2 sibling plugin subdirs)

The orchestrator lists the plugin names it found, capped at the first 6
for readability — `(plus K more)` if more than 6.

```
┏━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ # ┃ What to scan                                                               ┃
┡━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ 1 │ All 4 plugins under this folder (alpha, beta, gamma, delta)                │
│ 2 │ Pick just one of them                                                      │
│ 3 │ Type a different path / name / URL                                         │
│ A │ Ask the agent for a recommendation                                         │
│ 0 │ Cancel / Exit                                                              │
└───┴────────────────────────────────────────────────────────────────────────────┘
```

#### Case 5 — Plain folder (default fallback)

```
┏━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ # ┃ What to scan                                                               ┃
┡━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ 1 │ Treat the current folder as the target                                     │
│ 2 │ Type a different path / name / URL                                         │
│ A │ Ask the agent for a recommendation                                         │
│ 0 │ Cancel / Exit                                                              │
└───┴────────────────────────────────────────────────────────────────────────────┘
```

#### Routing (applies to every case)

  - The number that maps to "this project / this plugin / this folder"
    sets `TARGET=$(pwd)` (or, for Layout C and Multi-plugin cases, the
    derived sub-target). The orchestrator continues with that path.
  - The number labelled "Type a different path / name / URL" asks
    `Enter the path / name / URL:` as a plain-text prompt. The user MUST
    type at least one character. Capture as `TARGET`.
  - `0` → `Cancelled — no actions taken.` and stop.

Always put the most-likely choice on row 1 — that lets the user pick the
common path with a single keystroke.

For leaves that need MULTIPLE path-shaped inputs (e.g. report path AND
plugin path), repeat the mini-menu once per input.

When the orchestrator references `TARGET` in execution snippets below,
that's the value captured by this mini-menu.

---

### 3.0b "Ask the agent" shortcut (MANDATORY — present on EVERY menu)

Every menu in §3.0, §3.1, …, §3.10, and the §3.0a path-source mini-menus
MUST include a row labeled `A` immediately before the `0 — Cancel / Exit`
row. The row reads:

```
│ A │ Ask the agent                                   │ Free-form chat with an Opus agent — paste logs, ask questions, get a plan │
```

(Exact column widths vary per menu; the orchestrator pads with trailing
spaces so the row matches the table's content-row width.)

**Routing for `A` — free-form chat handoff (MUST NOT print a menu)**:

When the user picks `A`, the menu agent IMMEDIATELY hands control to a
fresh Opus sub-agent (dispatched via the `Agent` tool with
`subagent_type: general-purpose` and `model: opus`). The sub-agent
receives a single prompt that bundles:

1. The user's recent context (last command, last validation report
   path, last visible error block, current $PWD, layout-detection
   result, any unresolved findings under `reports/` or `design/tasks/`).
2. The exact menu the user was looking at when they picked `A`.
3. **An explicit instruction to enter free-form chat — NOT to print a
   numbered menu, NOT to call `AskUserQuestion`, NOT to return to the
   parent menu after one turn.**

The Opus sub-agent's first message is open-ended, e.g.:

```
I'm here to help. What's going on? Paste any error logs, validation
reports, gh run output, or describe the issue in your own words. I'll
read it, propose a concrete plan, and wait for your approval before
running anything.
```

After that, the sub-agent stays in **multi-turn dialog mode**:

- Reads pasted error blocks / log dumps verbatim.
- Asks clarifying questions when the situation is ambiguous (in plain
  text, NOT a menu).
- Once it understands the problem, prints a concrete plan in this form:

  ```
  Plan:
    1. <step 1 — concrete action, file, command>
    2. <step 2 — …>
    3. <step 3 — …>

  Reply `ok` / `yes` / `go` to execute, or tell me what to change.
  ```

- **Waits for the user's free-form reply.** Only on explicit approval
  (`yes` / `ok` / `go` / `approved` / similar) does it execute the plan
  via the appropriate CPV launcher (`remote_validation.py <alias>`,
  `add_component.py`, etc.).
- After execution, prints a 3-line summary + report path, then asks
  `Anything else?` and continues the dialog.
- The user ends the chat by typing `done`, `exit`, `bye`, `0`, or
  `back to menu` — only then does the Opus sub-agent return control
  to the Haiku menu agent, which then prints the §3.99 "do something
  else?" table.

**Critical rules for the Ask-the-agent flow** (encode these in every
menu agent's prompt):

- NEVER print a numbered menu after picking `A`. The user is now in
  free-form chat — menus would defeat the purpose.
- NEVER call `AskUserQuestion`. Multi-choice prompts also defeat the
  purpose; the user must be able to paste arbitrary text (error logs,
  multi-line snippets, file contents).
- NEVER auto-execute a plan. Wait for explicit text approval. This
  preserves Rule 1 (no proactive project work).
- DO accept multi-line user input. The user may paste a 50-line log
  dump and that's a single conversational turn.
- DO read the most recent ~20 messages from the parent conversation
  for context (the menu agent passes them through in the dispatch
  prompt) — this is where "the gh run failed" context lives.
- DO route the eventual concrete action through CPV's standard
  launchers (validators, fixers, scaffolders) — never improvise a
  one-off bash command when a CPV recipe exists.

If the user types nothing for several turns or types something the
agent doesn't understand, the agent asks ONE clarifying question in
plain text — never falls back to a menu. The orchestrator's menu only
re-appears when the user explicitly says they're done.

---

### 3.0 Top-level menu (10 categories + Cancel)

```
┏━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ # ┃ Category                 ┃ What it does                                                          ┃
┡━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ 1 │ Validate                 │ Check that a plugin or marketplace is well-formed                     │
│ 2 │ Validate from GitHub     │ Check a plugin or marketplace hosted on GitHub (no local clone needed)│
│ 3 │ Fix                      │ Auto-fix issues that a previous validation found                      │
│ 4 │ Create                   │ Scaffold plugin, marketplace, skill, agent, command, hook, MCP server │
│ 5 │ Manage                   │ List installed plugins, install / update, health-check, bump version  │
│ 6 │ Diagnose & Upgrade       │ Deep audit + upgrade existing plugin to latest pipeline (recommended) │
│ 7 │ GitHub setup             │ Branch-protection rules, link plugin to marketplace                   │
│ 8 │ Deep semantic analysis   │ AI-graded quality review (slow + expensive — confirms cost first)     │
│ 9 │ Help / About             │ Show the menu overview, list of commands, version                     │
│ A │ Ask the agent            │ Let the agent suggest the best next action right now                  │
│ 0 │ Cancel / Exit            │ Stop without doing anything                                           │
└───┴──────────────────────────┴───────────────────────────────────────────────────────────────────────┘
Type a number (or A to ask the agent) to choose:
```

---

### Post-validate flow (applies to every leaf in §3.1 and §3.2)

After a leaf in §3.1 or §3.2 finishes and the report is on disk, the
orchestrator MUST print the §3.10 post-validate fix menu (NEVER the
generic §3.99 "do something else" table). This is non-negotiable: the user
always gets the explicit "fix N or end" choice after a validation, never
just "what's next?".

### 3.1 Validate sub-menu (24 explicit choices + Back + Cancel)

When the user reaches this menu, the orchestrator first prints this
table. Every option that takes a path triggers the **project-type
auto-detection** (see "Project-type auto-detection" above) BEFORE
invoking the underlying validator.

The **path-source mini-menu (§3.0a)** is invoked for every leaf that
needs a path — its row 1 ("Current project folder $PWD") is the
one-keystroke shortcut for "validate the project I'm currently in".

```
┏━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ #  ┃ What to validate                                ┃ What it does                                                                          ┃
┡━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│  1 │ Whole plugin (every check we have)              │ Run all 17 checks on a plugin folder                                                  │
├────┼─────────────────────────────────────────────────┼───────────────────────────────────────────────────────────────────────────────────────┤
│  2 │ One SKILL.md file                               │ Header (frontmatter), structure, and content rules                                    │
├────┼─────────────────────────────────────────────────┼───────────────────────────────────────────────────────────────────────────────────────┤
│  3 │ One agent .md file                              │ Header, model, tools, examples (2+ <example> blocks)                                  │
├────┼─────────────────────────────────────────────────┼───────────────────────────────────────────────────────────────────────────────────────┤
│  4 │ One command .md file                            │ Header, target agent, tool allowlist, argument hint                                   │
├────┼─────────────────────────────────────────────────┼───────────────────────────────────────────────────────────────────────────────────────┤
│  5 │ Hook                                            │ hooks.json layout + event names + the scripts the hook calls                          │
├────┼─────────────────────────────────────────────────┼───────────────────────────────────────────────────────────────────────────────────────┤
│  6 │ MCP server                                      │ MCP server setup (transport, env vars, security checks)                               │
├────┼─────────────────────────────────────────────────┼───────────────────────────────────────────────────────────────────────────────────────┤
│  7 │ LSP server                                      │ Language-server setup in plugin.json                                                  │
├────┼─────────────────────────────────────────────────┼───────────────────────────────────────────────────────────────────────────────────────┤
│  8 │ Output-style                                    │ Output-style files in .claude/output-styles/                                          │
├────┼─────────────────────────────────────────────────┼───────────────────────────────────────────────────────────────────────────────────────┤
│  9 │ Rule files (.claude/rules/*.md)                 │ Rule-file headers and content                                                         │
├────┼─────────────────────────────────────────────────┼───────────────────────────────────────────────────────────────────────────────────────┤
│ 10 │ Marketplace — local folder                      │ A marketplace folder on this machine                                                  │
├────┼─────────────────────────────────────────────────┼───────────────────────────────────────────────────────────────────────────────────────┤
│ 11 │ Marketplace — GitHub (owner/repo)               │ A marketplace from a GitHub repo (cloned to a tmp dir, then deleted)                  │
├────┼─────────────────────────────────────────────────┼───────────────────────────────────────────────────────────────────────────────────────┤
│ 12 │ Marketplace — any git URL                       │ A marketplace from any git URL (GitLab, Bitbucket, SSH, self-hosted)                  │
├────┼─────────────────────────────────────────────────┼───────────────────────────────────────────────────────────────────────────────────────┤
│ 13 │ Inline marketplaces in settings.json            │ Marketplace blocks pasted directly into Claude Code's settings.json                   │
├────┼─────────────────────────────────────────────────┼───────────────────────────────────────────────────────────────────────────────────────┤
│ 14 │ Project-level Claude config (git-tracked)       │ Files in .claude/ that ARE checked into git (settings.json, agents, …)                │
├────┼─────────────────────────────────────────────────┼───────────────────────────────────────────────────────────────────────────────────────┤
│ 15 │ Local-only Claude config (not in git)           │ settings.local.json + ~/.claude.json per-project state (not in git)                   │
├────┼─────────────────────────────────────────────────┼───────────────────────────────────────────────────────────────────────────────────────┤
│ 16 │ Security checks (sub-menu)                      │ Open the security-only sub-menu (one scanner, marketplace-wide, etc.)                 │
├────┼─────────────────────────────────────────────────┼───────────────────────────────────────────────────────────────────────────────────────┤
│ 17 │ Prompt-cache checks (sub-menu)                  │ Open the cache-pattern sub-menu (cache-friendly refactor, etc.)                       │
├────┼─────────────────────────────────────────────────┼───────────────────────────────────────────────────────────────────────────────────────┤
│ 18 │ Broken links between files                      │ Stale references between agents / skills / commands and their reference files         │
├────┼─────────────────────────────────────────────────┼───────────────────────────────────────────────────────────────────────────────────────┤
│ 19 │ Documentation                                   │ README + doc structure rules                                                          │
├────┼─────────────────────────────────────────────────┼───────────────────────────────────────────────────────────────────────────────────────┤
│ 20 │ File encoding                                   │ UTF-8, BOM marker, line endings on every .md / .json / .yaml file                     │
├────┼─────────────────────────────────────────────────┼───────────────────────────────────────────────────────────────────────────────────────┤
│ 21 │ Enterprise                                      │ Compliance / governance / IT-managed-settings rules                                   │
├────┼─────────────────────────────────────────────────┼───────────────────────────────────────────────────────────────────────────────────────┤
│ 22 │ Self-check the scoring system                   │ Verify CPV's own pass / fail / severity logic still works                             │
├────┼─────────────────────────────────────────────────┼───────────────────────────────────────────────────────────────────────────────────────┤
│ 23 │ Lint scripts (ruff / mypy / shellcheck)         │ Run linters on every Python and Bash script the plugin ships                          │
├────┼─────────────────────────────────────────────────┼───────────────────────────────────────────────────────────────────────────────────────┤
│ 24 │ Risky env-var usage                             │ Telemetry-leak rules (PLUGIN_SEED_DIR, SHELL_PREFIX, OTEL_LOG_RAW_API_BODIES, …)      │
├────┼─────────────────────────────────────────────────┼───────────────────────────────────────────────────────────────────────────────────────┤
│  A │ Ask the agent for a recommendation              │ Let the agent suggest the best next action right now                                  │
├────┼─────────────────────────────────────────────────┼───────────────────────────────────────────────────────────────────────────────────────┤
│  B │ Back                                            │ Go back to the top-level menu                                                         │
├────┼─────────────────────────────────────────────────┼───────────────────────────────────────────────────────────────────────────────────────┤
│  0 │ Cancel / Exit                                   │ Stop without doing anything                                                           │
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
┏━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ # ┃ Source                    ┃ What it does                                                          ┃
┡━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ 1 │ Plugin from GitHub        │ Check a plugin from a GitHub repo (cloned to a tmp dir, then deleted) │
│ 2 │ Marketplace from GitHub   │ Check a marketplace from a GitHub repo (tmp clone + cleanup)          │
│ 9 │ Back                      │ Go back to the top-level menu                                         │
│ A │ Ask the agent             │ Let the agent suggest the best next action right now                  │
│ 0 │ Cancel / Exit             │ Stop without doing anything                                           │
└───┴───────────────────────────┴───────────────────────────────────────────────────────────────────────┘
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
┏━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ # ┃ Operation                ┃ What it does                                                       ┃
┡━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ 1 │ Fix plugin issues        │ From a report file OR a plugin folder (uses the plugin-fixer agent)│
│ 2 │ Fix marketplace issues   │ From a report file OR a marketplace folder (uses marketplace-fixer)│
│ 3 │ Optimize prompt cache    │ Audit + auto-fix the cache patterns (uses cache-optimizer-agent)   │
│ 9 │ Back                     │ Go back to the top-level menu                                      │
│ A │ Ask the agent            │ Let the agent suggest the best next action right now               │
│ 0 │ Cancel / Exit            │ Stop without doing anything                                        │
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
┏━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ # ┃ Scaffold                                        ┃ What it does                                                                               ┃
┡━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ 1 │ New plugin (latest pipeline standard)           │ Fresh plugin repo with idempotent publish.py + cpv_lint_engine + pathlib + sanitized inputs│
│ 2 │ New marketplace                                 │ Fresh marketplace repo from scratch (Layout A/B/C — interactive)                           │
│ 3 │ New skill (in existing plugin)                  │ Add skills/<name>/SKILL.md — valid frontmatter + auto-refresh README                       │
│ 4 │ New agent (in existing plugin)                  │ Add agents/<name>.md — valid frontmatter + tools whitelist + auto-refresh README           │
│ 5 │ New slash command (in existing plugin)          │ Add commands/<name>.md — valid frontmatter + auto-refresh README                           │
│ 6 │ New hook (in existing plugin)                   │ Append hook entry to hooks/hooks.json — cross-platform-aware (bash-isms rejected)          │
│ 7 │ New MCP server (in existing plugin)             │ Register server in .mcp.json — stdio default, HTTP via flag, server-name uniqueness check  │
│ 9 │ Back                                            │ Go back to the top-level menu                                                              │
│ A │ Ask the agent                                   │ Let the agent suggest the best next action right now                                       │
│ 0 │ Cancel / Exit                                   │ Stop without doing anything                                                                │
└───┴─────────────────────────────────────────────────┴────────────────────────────────────────────────────────────────────────────────────────────┘
Type a number to choose:
```

#### 3.4.1 Scaffold a new plugin

- **arg-prompts** (in order):
  1. `Plugin name?`
  2. `Target directory?`
  3. `Layout (A=hub-and-spoke / B=nested monorepo / C=marketplace-in-plugin self-referential)?`
- **execution**: dispatch the **plugin-creator agent** with the answers. Newly-scaffolded plugins ship with current pipeline standards baked in (idempotent publish.py, cpv_lint_engine, pathlib-only Python, sanitized inputs, validate_pipeline_script_refs rule, no `.sh` scripts).
- **post-execution**: ALWAYS auto-dispatch the **plugin-diagnoser agent** on the just-scaffolded plugin path. If the diagnosis returns 0 CRITICAL/MAJOR/MINOR, print `✓ Scaffold passes diagnose-plugin clean.` and proceed to §3.99. Otherwise print the diagnoser's follow-up menu so the user can pick a fix path.

#### 3.4.2 Scaffold a new marketplace

- **arg-prompts** (in order):
  1. `Marketplace name?`
  2. `Target directory?`
  3. `Owner GitHub username?`
- **execution**: dispatch the **plugin-creator agent** in marketplace mode.

#### 3.4.3 Add a skill to an existing plugin

- **path-source**: per §3.0a (row 1 = current project folder)
- **arg-prompts** (in order):
  1. `Skill name (kebab-case)?`
  2. `One-line description (what does the skill do)?`
- **execution**:
  ```bash
  uv run "${CLAUDE_PLUGIN_ROOT}/scripts/add_component.py" "$PLUGIN_PATH" \
    --type skill --name "$NAME" --description "$DESC"
  uv run "${CLAUDE_PLUGIN_ROOT}/scripts/refresh_readme.py" "$PLUGIN_PATH"
  ```
- **post-execution**: auto-run `validate_plugin --strict` on the parent plugin so any drift is caught immediately. Print summary + report path.

#### 3.4.4 Add an agent to an existing plugin

- **path-source**: per §3.0a
- **arg-prompts** (in order):
  1. `Agent name (kebab-case)?`
  2. `One-line description?`
  3. `Tools whitelist (comma-separated, e.g. "Read, Bash, Grep" — leave blank for default sonnet+all)?`
- **execution**:
  ```bash
  uv run "${CLAUDE_PLUGIN_ROOT}/scripts/add_component.py" "$PLUGIN_PATH" \
    --type agent --name "$NAME" --description "$DESC" --tools "$TOOLS"
  uv run "${CLAUDE_PLUGIN_ROOT}/scripts/refresh_readme.py" "$PLUGIN_PATH"
  ```
- **post-execution**: auto-run `validate_plugin --strict`.

#### 3.4.5 Add a slash command to an existing plugin

- **path-source**: per §3.0a
- **arg-prompts** (in order):
  1. `Command name (kebab-case)?`
  2. `One-line description?`
  3. `Allowed tools (e.g. "Bash(uv:*)" — leave blank to allow all)?`
- **execution**:
  ```bash
  uv run "${CLAUDE_PLUGIN_ROOT}/scripts/add_component.py" "$PLUGIN_PATH" \
    --type command --name "$NAME" --description "$DESC" --allowed-tools "$TOOLS"
  uv run "${CLAUDE_PLUGIN_ROOT}/scripts/refresh_readme.py" "$PLUGIN_PATH"
  ```
- **post-execution**: auto-run `validate_plugin --strict`.

#### 3.4.6 Add a hook to an existing plugin

- **path-source**: per §3.0a
- **arg-prompts** (in order):
  1. `Hook event (PreToolUse, PostToolUse, SessionStart, Stop, …)?`
  2. `Command (cross-platform — recommend `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/<your-hook>.py"`)?`
- **pre-execution**: validate the command via `check_hook_command_cross_platform`. If it contains bash-only constructs (`set -euo pipefail`, `[[ ]]`, `$(<file)`, process substitution, brace expansion) OR POSIX-only tools (`jq`, `sed`, `awk`, `shellcheck`) — REJECT with the conversion-cheat-sheet from `pipeline-migration.md §3b` and re-prompt for the command.
- **execution**:
  ```bash
  uv run "${CLAUDE_PLUGIN_ROOT}/scripts/add_component.py" "$PLUGIN_PATH" \
    --type hook --event "$EVENT" --command "$COMMAND"
  ```
- **post-execution**: auto-run `validate_plugin --strict` (will re-validate the new hook command).

#### 3.4.7 Add an MCP server to an existing plugin

- **path-source**: per §3.0a
- **arg-prompts** (in order):
  1. `Server name (must be unique within plugin; cannot be "workspace")?`
  2. `Command (must invoke a cross-platform runtime: `node`, `python3`, `uv run`, `npx` — bare `./script.sh` rejected)?`
  3. `Transport (1=stdio default, 2=http)?`
  4. (only if http) `HTTP URL?`
- **pre-execution**: reject the reserved server name `workspace`. Reject command starting with bare `./` or `*.sh` (cross-platform requirement).
- **execution**:
  ```bash
  # stdio
  uv run "${CLAUDE_PLUGIN_ROOT}/scripts/add_component.py" "$PLUGIN_PATH" \
    --type mcp --name "$NAME" --command "$COMMAND"
  # http
  uv run "${CLAUDE_PLUGIN_ROOT}/scripts/add_component.py" "$PLUGIN_PATH" \
    --type mcp --name "$NAME" --http-url "$URL"
  ```
- **post-execution**: auto-run `validate_plugin --strict` AND `validate_mcp --strict` (catches cross-source server-name shadowing).

---

### 3.5 Manage sub-menu

```
┏━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ # ┃ Operation                         ┃ What it does                                                    ┃
┡━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ 1 │ List installed plugins            │ Show every plugin Claude Code knows about                       │
│ 2 │ Install / update / enable / off   │ Hand off to the plugin-manager agent (it asks what you want)    │
│ 3 │ Health check                      │ Look for problems in the plugin registry, settings, and cache   │
│ 4 │ Install external scanners         │ Install all the security scanners CPV uses (cc-audit, etc.)     │
│ 5 │ Prune old cached plugin versions  │ Free disk space — keep the active version, delete older ones    │
│ 6 │ Bump version + publish            │ Bump patch / minor / major and run the publish pipeline         │
│ 7 │ Show CPV version                  │ Read the version from .claude-plugin/plugin.json                │
│ 8 │ Refresh plugin README             │ Re-build the plugin's auto-generated README sections            │
│10 │ Standardize plugin                │ Re-write the plugin's publish.py + CI + retry helpers           │
│11 │ Add component                     │ Add a new skill / agent / command / hook / mcp to a plugin      │
│12 │ Move tests to a sub-repo          │ Move tests/ into a separate git submodule (PSS pattern)         │
│13 │ Migrate marketplace.json          │ Normalize old source.url entries and detect dead 404 entries    │
│ 9 │ Back                              │ Go back to the top-level menu                                   │
│ A │ Ask the agent                     │ Let the agent suggest the best next action right now            │
│ 0 │ Cancel / Exit                     │ Stop without doing anything                                     │
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

- **path-source**: per §3.0a (its row 1 = "current project folder $PWD")
- **execution**:
  ```bash
  uv run python "${CLAUDE_PLUGIN_ROOT}/scripts/refresh_readme.py" "$TARGET"
  ```
- **note**: Adds `<!-- BEGIN AUTO-COMPONENTS -->` block on first run;
  subsequent runs preserve placement and only update the body.

#### 3.5.10 Standardize plugin (force-templates) (Phase 2, v2.55.0+)

- **path-source**: per §3.0a (its row 1 = "current project folder $PWD")
- **arg-prompt**: `Run in --check mode first? (yes/no — recommended yes)`
- **execution** (check mode):
  ```bash
  uv run --with pyyaml python "${CLAUDE_PLUGIN_ROOT}/scripts/remote_validation.py" \
    standardize "$TARGET" --fix --dry-run --force-templates
  ```
- **execution** (apply):
  ```bash
  uv run --with pyyaml python "${CLAUDE_PLUGIN_ROOT}/scripts/remote_validation.py" \
    standardize "$TARGET" --fix --force-templates
  ```
- **note**: OVERWRITES infrastructure files (publish.py, ci/release/notify
  workflows, retry helpers, pre-push hook, cliff.toml, .mega-linter.yml)
  with the canonical CPV templates. Backs up each existing copy to
  `<file>.bak`. README, pyproject.toml, .gitignore are NEVER force-written.

#### 3.5.11 Add component (Phase 10, v2.61.0+)

- **path-source**: per §3.0a (its row 1 = "current project folder $PWD")
- **arg-prompts** (in order, after the path-source mini-menu):
  1. `Component type? (skill / agent / command / hook / mcp)`
  2. `Name? (for skill/agent/command/mcp)`
  3. `Description?`
  4. `(if hook)` `Event name? (e.g. PreToolUse, Stop)` and `Command to run?`
  5. `(if mcp)` `Stdio command OR HTTP URL?`
- **execution**:
  ```bash
  uv run python "${CLAUDE_PLUGIN_ROOT}/scripts/add_component.py" "$TARGET" \
    --type "$TYPE" --name "$NAME" --description "$DESC" [--allowed-tools ...]
  ```

#### 3.5.12 Strip dev parts (submodule) (Phase 2, v2.52.0+)

- **path-source**: per §3.0a (its row 1 = "current project folder $PWD")
- **arg-prompt**: `Mode? (dry-run / check / auto)`
- **execution** (dry-run, no destructive ops):
  ```bash
  uv run python "${CLAUDE_PLUGIN_ROOT}/scripts/cpv_strip_dev.py" "$TARGET" --dry-run
  ```
- **execution** (auto — DESTRUCTIVE):
  ```bash
  uv run python "${CLAUDE_PLUGIN_ROOT}/scripts/cpv_strip_dev.py" "$TARGET" --auto
  ```
- **note**: --auto creates a `<owner>/<plugin>-tests` private GitHub repo,
  filters its history into the new repo, replaces the tests/ dir with a
  submodule mount. Idempotent state machine resumes crashed runs.
  ALWAYS run --dry-run first.

#### 3.5.13 Migrate marketplace (source.url → source.repo) (Phase 2.6, v2.59.0+)

- **path-source**: per §3.0a (row 2 prompt text: `Enter the marketplace
  root path (containing .claude-plugin/marketplace.json):`)
- **arg-prompt**: `Run in --check mode first? (yes/no — recommended yes)`
- **execution** (check mode — exits 1 if migrations would change file):
  ```bash
  uv run python "${CLAUDE_PLUGIN_ROOT}/scripts/migrate_marketplace.py" "$TARGET" --check
  ```
- **execution** (apply atomically):
  ```bash
  uv run python "${CLAUDE_PLUGIN_ROOT}/scripts/migrate_marketplace.py" "$TARGET"
  ```
- **note**: Probes each plugin entry's GitHub repo via `gh api` (retry-wrapped).
  Dead 404 entries are surfaced but NOT removed automatically — user decides.

---

### 3.6 Diagnose & Upgrade sub-menu

The big-picture entry for any existing plugin. Goes beyond `/cpv-validate-plugin`
(structure-only) by ALSO running all 5 external scanners + pipeline-staleness
checks + cross-platform compliance + marketplace registration probe +
cached-vs-GitHub sync probe + branch-rules + Claude action audit.

```
┏━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ # ┃ Operation                                         ┃ What it does                                                                                              ┃
┡━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ 1 │ Diagnose plugin (deep audit)                      │ Full audit + structured report + follow-up menu (offers upgrade / register / sync / fix)                  │
│ 2 │ Upgrade plugin to current pipeline standard       │ Apply ALL pipeline-migration steps (§1–§5) — bash → Python, os.path → pathlib, idempotent publish.py, etc.│
│ 3 │ Apply CRITICAL fixes only                         │ Fix only publish-blockers + security blockers — leaves MAJOR/MINOR/NIT findings for later                 │
│ 4 │ Apply MAJOR + CRITICAL fixes                      │ Fix everything that blocks publishing or is non-cross-platform — leaves MINOR/NIT/WARNING for later       │
│ 5 │ Sync cached install with GitHub                   │ Compare cache version to latest tag; offer to run `claude plugin update`                                  │
│ 6 │ Check + fix marketplace registration              │ Verify plugin is listed in a marketplace; offer to register / create / re-register                        │
│ 7 │ Audit branch rules + Claude action setup          │ Check ruleset, bypass actors, Claude action version pin, missing secrets — offer to fix                   │
│ 8 │ Cross-platform audit                              │ Quick scan for `.sh` scripts, `os.path`, hardcoded `/tmp/`, `shell=True`, bash hook constructs, jq/sed/awk│
│ 9 │ Back                                              │ Go back to the top-level menu                                                                             │
│ A │ Ask the agent                                     │ Let the agent suggest the best next action right now                                                      │
│ 0 │ Cancel / Exit                                     │ Stop without doing anything                                                                               │
└───┴───────────────────────────────────────────────────┴───────────────────────────────────────────────────────────────────────────────────────────────────────────┘
Type a number to choose:
```

#### 3.6.1 Diagnose plugin

- **path-source**: per §3.0a (its row 1 = "current project folder $PWD")
- **execution**: dispatch the **plugin-diagnoser agent** with the path. The agent runs phases 1–7 (validate, security with all scanners, pipeline staleness, cross-platform, marketplace registration, branch+actions, sync), writes the structured report, then prints its own follow-up menu (rows 1–7 + 0).

#### 3.6.2 Upgrade to current pipeline standard

- **path-source**: per §3.0a
- **execution**: dispatch the **plugin-fixer agent** with the path AND the prompt: `Apply pipeline-migration §1–§5 from skills/fix-validation/references/pipeline-migration.md. min_severity=WARNING (fix everything).`

#### 3.6.3 Apply CRITICAL fixes only

- **path-source**: per §3.0a
- **execution**: dispatch the **plugin-fixer agent** with `min_severity=CRITICAL`.

#### 3.6.4 Apply MAJOR + CRITICAL fixes

- **path-source**: per §3.0a
- **execution**: dispatch the **plugin-fixer agent** with `min_severity=MAJOR`.

#### 3.6.5 Sync cached install with GitHub

- **arg-prompts** (in order):
  1. `Plugin name (or path under ~/.claude/plugins/cache/)?`
  2. (after detection prints `cached: vX.Y.Z, latest: vA.B.C, N versions behind`) — `Run \`claude plugin update <name>@<marketplace>\` now? (yes/no)`
- **execution**:
  ```bash
  uv run --with pyyaml python "${CLAUDE_PLUGIN_ROOT}/scripts/manage_doctor.py" --check-cache-sync "<plugin-name>"
  # if user says yes:
  claude plugin update <name>@<marketplace>
  ```

#### 3.6.6 Check + fix marketplace registration

- **path-source**: per §3.0a
- **execution**: dispatch the **plugin-diagnoser agent** in marketplace-only mode → if not registered, dispatch the **plugin-creator agent** in orphan-plugin marketplace-onboarding mode (4-path menu: A=existing marketplace, B=new local marketplace, C=new GitHub marketplace, D=existing GitHub marketplace).

#### 3.6.7 Audit branch rules + Claude action setup

- **arg-prompt**: `Owner/repo slug to audit (or "auto" to detect from origin)?`
- **execution**: dispatch the **plugin-diagnoser agent** in branch-rules-only mode (Phase 6.5). Findings include: ruleset state, bypass actors, Claude action version pin, missing secrets. After the audit prints, offer:
  - 1: re-apply cpv-branch-rules ruleset → `/cpv-setup-branch-rules-generic <owner>/<repo>`
  - 2: pin Claude action to latest SHA via pinact
  - 3: surface secret-setup instructions for `ANTHROPIC_API_KEY` / `CLAUDE_CODE_OAUTH_TOKEN`
  - 0: end

#### 3.6.8 Cross-platform audit

- **path-source**: per §3.0a
- **execution**:
  ```bash
  cd <plugin-path>
  echo "=== Bash/shell scripts (should only be in scripts_dev/ — anything in scripts/ blocks Windows users) ==="
  find . -name "*.sh" -not -path "./.git/*" -not -path "./scripts_dev/*"
  echo
  echo "=== os.path / hardcoded /tmp/ / shell=True / os.system in Python scripts ==="
  grep -rnE "os\.path\.|shell=True|\"/tmp/|os\.system|os\.geteuid" scripts/ --include="*.py" 2>/dev/null
  echo
  echo "=== Bash-only hook commands ==="
  uv run --with pyyaml python "${CLAUDE_PLUGIN_ROOT}/scripts/remote_validation.py" hook . --strict 2>&1 | grep -E "bash-only|POSIX-only" || echo "(none)"
  ```
- **post**: print the §3.99 "do something else?" 2-row table.

---

### 3.7 GitHub setup sub-menu

```
┏━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ # ┃ Operation                            ┃ What it does                                              ┃
┡━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ 1 │ Protect this repo's branches         │ Apply branch-protection rules to the repo of `origin`     │
│ 2 │ Protect another repo's branches      │ Apply branch-protection rules to any owner/repo           │
│ 3 │ Link a plugin to a marketplace       │ Register a plugin in a marketplace's plugin list          │
│ 9 │ Back                                 │ Go back to the top-level menu                             │
│ A │ Ask the agent                        │ Let the agent suggest the best next action right now      │
│ 0 │ Cancel / Exit                        │ Stop without doing anything                               │
└───┴──────────────────────────────────────┴───────────────────────────────────────────────────────────┘
Type a number to choose:
```

#### 3.7.1 Branch protection (current repo)

- **execution**: invokes `/cpv-setup-branch-rules` workflow inline (no extra prompts — uses the current `git remote get-url origin`).

#### 3.7.2 Branch protection (generic owner/repo)

- **arg-prompt**: `Owner/repo slug?`
- **execution**: invokes `/cpv-setup-branch-rules-generic` workflow inline with the slug.

#### 3.7.3 Link plugin to a marketplace

- **arg-prompts** (in order):
  1. `Plugin repo (owner/repo)?`
  2. `Marketplace repo (owner/repo)?`
- **execution**: invokes `/cpv-link-plugin` workflow inline with the answers.

---

### 3.8 Deep semantic analysis (opus, EXPENSIVE)

```
┏━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ # ┃ Operation                  ┃ What it does                                                     ┃ Cost                    ┃
┡━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ 1 │ Confirm + run on a path    │ AI-graded quality review (A-F) of a skill / agent / whole plugin │ 10-50x a normal scan    │
│ 9 │ Back                       │ Go back to the top-level menu                                    │ —                       │
│ A │ Ask the agent              │ Let the agent suggest the best next action right now             │ —                       │
│ 0 │ Cancel / Exit              │ Stop without doing anything                                      │ —                       │
└───┴────────────────────────────┴──────────────────────────────────────────────────────────────────┴─────────────────────────┘
Type a number to choose:
```

#### 3.8.1 Confirm + run

- **arg-prompts** (in order):
  1. `Semantic validation uses Opus with 1M context at max effort. Cost: ~10-50× normal. Proceed? (yes/no)`
  2. (only if yes) `Path to skill or agent or whole plugin?`
- **execution**: dispatch the **semantic-validator agent** with the path. The agent itself runs the syntactic baseline first then the semantic pass.

---

### 3.9 Help / About sub-menu

```
┏━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ # ┃ Help topic                          ┃ What it shows                                             ┃
┡━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ 1 │ Show the top-level menu             │ Re-print the main menu (the 8 categories)                 │
│ 2 │ List every CPV command              │ Print the name + description of every /cpv-* command      │
│ 3 │ Show CPV version                    │ Read the version from .claude-plugin/plugin.json          │
│ 9 │ Back                                │ Go back to the top-level menu                             │
│ A │ Ask the agent                       │ Let the agent suggest the best next action right now      │
│ 0 │ Cancel / Exit                       │ Stop without doing anything                               │
└───┴─────────────────────────────────────┴───────────────────────────────────────────────────────────┘
Type a number to choose:
```

#### 3.9.1 Category overview

- **execution**: re-print the 3.0 top-level menu table.

#### 3.9.2 List every CPV command

- **execution**:
  ```bash
  for f in "${CLAUDE_PLUGIN_ROOT}"/commands/cpv-*.md; do
    name=$(basename "$f" .md)
    desc=$(awk '/^description:/{sub(/^description:[[:space:]]*/, ""); print; exit}' "$f")
    printf "%-42s %s\n" "/$name" "$desc"
  done
  ```

#### 3.9.3 Show CPV plugin version

- **execution**: same as 3.5.7.

---

### 3.99 End-of-leaf "do something else?" table (NON-validate flows)

After a Create / Manage / GitHub-setup / Help leaf finishes, print this 2-row
table and wait for the user's number:

```
┏━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ # ┃ Next                        ┃ What it does                                                   ┃
┡━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ 1 │ Do something else           │ Go back to the top-level menu                                  │
│ A │ Ask the agent               │ Let the agent suggest the best next action right now           │
│ 0 │ Done (exit)                 │ Reply `Done.` and stop                                         │
└───┴─────────────────────────────┴────────────────────────────────────────────────────────────────┘
Type a number to choose:
```

---

### 3.16 Security sub-menu (drilled into from §3.1.16)

```
┏━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ #  ┃ Security scan target                            ┃ What it does                                                                                       ┃
┡━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│  1 │ Single plugin (full security pass)              │ All in-process rule packs + 5 external scanners (cc-audit, tirith, trufflehog, semgrep, Cisco)     │
├────┼─────────────────────────────────────────────────┼────────────────────────────────────────────────────────────────────────────────────────────────────┤
│  2 │ Single plugin from GitHub URL                   │ Auto-clone github.com URL → security pass → cleanup (v2.48 direct URL ingestion)                   │
├────┼─────────────────────────────────────────────────┼────────────────────────────────────────────────────────────────────────────────────────────────────┤
│  3 │ Single plugin from arbitrary git URL            │ git clone any URL (gitlab/SSH/self-hosted) → security pass → cleanup                               │
├────┼─────────────────────────────────────────────────┼────────────────────────────────────────────────────────────────────────────────────────────────────┤
│  4 │ Single plugin from local archive (.zip/.tar.gz) │ Extract → security pass → cleanup (v2.48 archive ingestion)                                        │
├────┼─────────────────────────────────────────────────┼────────────────────────────────────────────────────────────────────────────────────────────────────┤
│  5 │ Marketplace (every plugin, tree-scan-once)      │ v2.48 architecture: stage all plugins, fclones-dedup, run scanners ONCE, bucket per-plugin         │
├────┼─────────────────────────────────────────────────┼────────────────────────────────────────────────────────────────────────────────────────────────────┤
│  6 │ Loose / flat skill pack (--loose)               │ Skip the .claude-plugin/ precondition for SKILL_*.md packs                                         │
├────┼─────────────────────────────────────────────────┼────────────────────────────────────────────────────────────────────────────────────────────────────┤
│  7 │ Single scanner only (cc-audit)                  │ Only cc-audit (skip tirith/trufflehog/semgrep/Cisco/internal)                                      │
├────┼─────────────────────────────────────────────────┼────────────────────────────────────────────────────────────────────────────────────────────────────┤
│  8 │ Single scanner only (tirith)                    │ Only tirith policy engine                                                                          │
├────┼─────────────────────────────────────────────────┼────────────────────────────────────────────────────────────────────────────────────────────────────┤
│  9 │ Single scanner only (trufflehog)                │ Only trufflehog secret scanner (--concurrency on, gitleaks dropped in v2.48)                       │
├────┼─────────────────────────────────────────────────┼────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ 10 │ Single scanner only (semgrep)                   │ Only semgrep with p/security-audit + p/secrets rule packs                                          │
├────┼─────────────────────────────────────────────────┼────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ 11 │ Single scanner only (Cisco AI Defense)          │ Only the Cisco AI Defense skill-scanner (programmatic engines, no API key needed)                  │
├────┼─────────────────────────────────────────────────┼────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ 12 │ Telemetry hazards only                          │ Per-plugin env-var leak rules (PLUGIN_SEED_DIR, SHELL_PREFIX, OTEL_LOG_RAW_API_BODIES=file:*…)     │
├────┼─────────────────────────────────────────────────┼────────────────────────────────────────────────────────────────────────────────────────────────────┤
│  A │ Ask the agent for a recommendation              │ Let the agent suggest the best next action right now                                               │
├────┼─────────────────────────────────────────────────┼────────────────────────────────────────────────────────────────────────────────────────────────────┤
│  B │ Back                                            │ Go back to the Validate sub-menu                                                                   │
├────┼─────────────────────────────────────────────────┼────────────────────────────────────────────────────────────────────────────────────────────────────┤
│  0 │ Cancel / Exit                                   │ Stop without doing anything                                                                        │
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
┏━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ # ┃ Cache action                                  ┃ What it does                                                                                     ┃
┡━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
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
│ A │ Ask the agent for a recommendation            │ Let the agent suggest the best next action right now                                             │
├───┼───────────────────────────────────────────────┼──────────────────────────────────────────────────────────────────────────────────────────────────┤
│ B │ Back                                          │ Go back to the Validate sub-menu                                                                 │
├───┼───────────────────────────────────────────────┼──────────────────────────────────────────────────────────────────────────────────────────────────┤
│ 0 │ Cancel / Exit                                 │ Stop without doing anything                                                                      │
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
┏━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ # ┃ Action                                          ┃ What it does                                                          ┃ Severities the fixer will touch  ┃
┡━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ 1 │ Fix ALL issues (incl. WARNING)                  │ Dispatch the cpv-fixer agent on every finding in the report           │ CRITICAL+MAJOR+MINOR+NIT+WARNING │
│ 2 │ Fix NIT and higher                              │ Skip WARNING-only findings                                            │ CRITICAL+MAJOR+MINOR+NIT         │
│ 3 │ Fix MINOR and higher                            │ Skip NIT and WARNING                                                  │ CRITICAL+MAJOR+MINOR             │
│ 4 │ Fix MAJOR and higher                            │ Only fix the publish-blockers (and CRITICALs)                         │ CRITICAL+MAJOR                   │
│ 5 │ Fix CRITICAL only                               │ Strictest mode — fix the loaders/security blockers and nothing else   │ CRITICAL                         │
│ A │ Ask the agent                                   │ Let the agent suggest the best next action right now                  │ —                                │
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
