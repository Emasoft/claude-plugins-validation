# CPV Main-Menu Tree (claude-menu-system Stop-hook edition)

## Table of Contents

- [Shell prologue](#shell-prologue)
- [Menu-spec rendering rules](#menu-spec-rendering-rules)
- [Fixed-key routing contract](#fixed-key-routing-contract)
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
# Allowlist the dev's own username so the privacy scan does not false-positive
# on local paths under /Users/<me>/ or /home/<me>/.
export CLAUDE_PRIVATE_USERNAMES="$(whoami)"
mkdir -p "$MAIN_ROOT/reports/<component>"
REPORT_FILE="$MAIN_ROOT/reports/<component>/$TS-$SLUG.md"
```

Every leaf inherits the exported `CLAUDE_PRIVATE_USERNAMES` from this prologue,
so individual launcher leaves do not need to set it again.

## Menu-spec rendering rules

Every menu in CPV is rendered by the `claude-menu-system` plugin's
`Stop` / `SubagentStop` / `StopFailure` hook (`menu_emit.py`). The
orchestrator queues a spec via `scripts/print_menu.py` and ENDS its
turn. The hook then prints the rendered menu via the hook JSON
`systemMessage` field — so the menu is shown to the user but NEVER
enters the agent's transcript or prompt cache. Zero token cost
regardless of menu size; no subagent fork.

`print_menu.py` sends the MINIMUM inline data per menu (TRDD-ef3fc7d8):
FIXED menus (static rows, identical every run) are shipped as JSON
files in `skills/cpv-main-menu-skill/skill-menus/NN-<slug>.json` and
queued with just an index — `print_menu.py fixed NN`; DYNAMIC menus
(rows vary at runtime — discovered plugins / paths) send only the
bare list of entries — `print_menu.py dynamic '<entries>'` — and the
engine sorts them alphabetically, numbers them `1..N`, and
auto-appends the standard `P`/`A`/`B`/`M`/`0` footer.

**NEVER print menu tables inline in the orchestrator's response.**
**NEVER use `AskUserQuestion` for menu navigation.** All menus are
queued via `print_menu.py` and emitted post-turn by the Stop hook via
`systemMessage`.

### Canonical spec shape

Every queued spec has the shape below. FIXED menus store this verbatim
in `skill-menus/NN-<slug>.json`; DYNAMIC menus have `print_menu.py`
assemble it from the entries the orchestrator supplies:

```json
{
  "spec_version": 1,
  "mode": "menu",
  "plugin": "cpv",
  "slug": "<unique-per-menu>",
  "header": "<short prompt>",
  "rows": [
    {"key": "1", "action_id": "...", "label": "..."},
    {"key": "2", "action_id": "...", "label": "..."},
    {"key": "A", "action_id": "ask", "label": "Ask the agent — free-form chat with an Opus sub-agent"},
    {"key": "B", "action_id": "back", "label": "Back — return to the previous menu"},
    {"key": "0", "action_id": "cancel", "label": "Cancel / Exit"}
  ],
  "footer": "Type a key:"
}
```

Per-menu rules:

- **`A — Ask the agent`** appears in EVERY menu (between dynamic rows
  and the navigation rows). Picking `A` dispatches a fresh Opus
  sub-agent for free-form chat (see §3.0b below).
- **`B — Back`** appears in every sub-menu. The top-level menu omits
  it. (Some legacy sections may still use digit `9` for Back — the
  letter `B` is preferred because it never collides with multi-digit
  option numbers in long menus.)
- **`0 — Cancel / Exit`** appears in EVERY menu as the last row.
- Reserved navigation letters: `M` Main, `B` Back, `X`/`0` Exit.
  Action letters are mnemonics (`V` Validate, `F` Fix, `D` Diagnose,
  …) — fixed at skill-design time per menu and documented in this
  file's per-menu fixed key→action maps.

### Queue invocation (every orchestrator turn that needs a menu)

Export the skill-menus dir once, then queue the menu with `print_menu.py`
— ONE Bash tool call, no Write/Edit tool, no intermediate tempfile, no
chat text. The orchestrator never types the spec JSON inline.

**FIXED menu** — send only the index `NN` (the JSON lives in
`skill-menus/NN-<slug>.json`):

```bash
export CPV_SKILL_MENUS_DIR="${CLAUDE_PLUGIN_ROOT}/skills/cpv-main-menu-skill/skill-menus"
python "${CLAUDE_PLUGIN_ROOT}/scripts/print_menu.py" fixed NN >/dev/null 2>&1
```

**DYNAMIC menu** — send only the bare list of detected entries (the
engine sorts them alphabetically, numbers them `1..N`, and auto-appends
`P` type-a-path, any `extra_options`, then `A`/`B`/`M`/`0`):

```bash
export CPV_SKILL_MENUS_DIR="${CLAUDE_PLUGIN_ROOT}/skills/cpv-main-menu-skill/skill-menus"
python "${CLAUDE_PLUGIN_ROOT}/scripts/print_menu.py" dynamic '["entry-a","entry-b"]' >/dev/null 2>&1
```

For a dynamic menu that needs extra letter options or a custom
header/footer/slug, pass an object instead of a bare array:
`dynamic '{"slug":"...","entries":[...],"extra_options":[{"key":"S","action_id":"scan_all","label":"..."}]}'`.

Then END THE TURN IMMEDIATELY. **Emit ZERO chat text after the Bash
call** — no "menu queued", no "Stop hook will emit", no commentary
of any kind. The menu IS the entire user-visible output for this
turn. The `>/dev/null 2>&1` suppresses the queue-path stdout so nothing
appears between your last text and the post-turn menu emission.

Using the Write or Edit tool to first create the spec file is
forbidden — it produces a visible `Write(/tmp/...)` diff panel
before the menu, which is exactly the pollution this design avoids.

`print_menu.py` defaults `renumber: false`, so the caller's keys are
kept verbatim. The orchestrator routes the user's next-turn key from
the FIXED map documented in this file — it never inspects the
rendered menu to interpret a key.

## Fixed-key routing contract

Two namespaces that never collide:

- **Numbers `1..N`** — DYNAMIC list, ordered alphabetically. The Nth
  number is always the Nth alpha-sorted dynamic item the agent built.
  N varies run-to-run (e.g. 4 plugins under a multi-plugin workspace).
- **Letters** — FIXED actions. Each letter is permanently bound to
  one action across every CPV menu. An action that doesn't apply
  right now is OMITTED (its row is not printed); its letter NEVER
  reassigns and no other key reletters.

The "Single source of truth" invariant: the per-menu key→action map
in this file is the SOLE reference the orchestrator uses to interpret
a typed key. The agent NEVER reads back which rows were rendered.

### Project-type auto-detection (helper for path-accepting leaves)

Whenever a Validate / Fix / Cache / Security leaf accepts a path, the
orchestrator MUST first probe the path to decide what it is:

```bash
TARGET="<path-argument>"
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
leaf that needs a path / name / URL MUST first queue a small mini-menu
via `print_menu.py` and route based on the user's key.

The mini-menu is **context-aware**: key `1` is always the most likely
choice for what $PWD looks like RIGHT NOW. The orchestrator inspects
the current directory before queueing the menu and picks the right
shape from the cases below.

#### Detection (run before queueing the menu)

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

Spec rows (slug `path-source-layoutc`):

- `1` → action_id `whole_repo` — "Whole repo (marketplace AND its bundled plugin together)"
- `2` → action_id `plugin_part` — "Just the plugin part of this repo"
- `3` → action_id `mkt_part`    — "Just the marketplace part of this repo"
- `T` → action_id `type_path`   — "Type a different path / name / URL"
- `A` → action_id `ask`         — "Ask the agent for a recommendation"
- `0` → action_id `cancel`      — "Cancel / Exit"

Queue the spec via `print_menu.py fixed 1` and end the turn. NEVER print
the menu inline; CMS Stop hook emits via `systemMessage`.

```bash
export CPV_SKILL_MENUS_DIR="${CLAUDE_PLUGIN_ROOT}/skills/cpv-main-menu-skill/skill-menus"
python "${CLAUDE_PLUGIN_ROOT}/scripts/print_menu.py" fixed 1 >/dev/null 2>&1
```

#### Case 2 — Marketplace only

Spec rows (slug `path-source-mkt`):

- `1` → action_id `mkt_plus_all` — "This marketplace AND every plugin it lists"
- `2` → action_id `mkt_only`     — "Just the marketplace listing (skip the plugins)"
- `T` → action_id `type_path`    — "Type a different path / name / URL"
- `A` → action_id `ask`          — "Ask the agent for a recommendation"
- `0` → action_id `cancel`       — "Cancel / Exit"

Queue the spec via `print_menu.py fixed 2` and end the turn.

```bash
export CPV_SKILL_MENUS_DIR="${CLAUDE_PLUGIN_ROOT}/skills/cpv-main-menu-skill/skill-menus"
python "${CLAUDE_PLUGIN_ROOT}/scripts/print_menu.py" fixed 2 >/dev/null 2>&1
```

#### Case 3 — Plugin only (most common)

Spec rows (slug `path-source-plugin`):

- `1` → action_id `this_plugin` — "This plugin (the one in the current folder)"
- `T` → action_id `type_path`   — "Type a different path / name / URL"
- `A` → action_id `ask`         — "Ask the agent for a recommendation"
- `0` → action_id `cancel`      — "Cancel / Exit"

Queue the spec via `print_menu.py fixed 3` and end the turn.

```bash
export CPV_SKILL_MENUS_DIR="${CLAUDE_PLUGIN_ROOT}/skills/cpv-main-menu-skill/skill-menus"
python "${CLAUDE_PLUGIN_ROOT}/scripts/print_menu.py" fixed 3 >/dev/null 2>&1
```

#### Case 4 — Multi-plugin project (N >= 2 sibling plugin subdirs)

This is a **DYNAMIC** menu — the rows vary at runtime (the discovered
plugin names). Per the fixed-key contract, the per-plugin DYNAMIC list
uses numbers `1..N` (alpha-sorted). The orchestrator builds ONLY the
entries (the discovered plugins); `print_menu.py dynamic` sorts them
alphabetically, numbers them `1..N`, and AUTO-APPENDS the standard
fixed footer (`P` type-a-path, then the `S` Scan-all extra option, then
`A` ask, `B` back, `M` main, `0` exit).

Fixed key→action map (slug `path-source-multi`):

- `1..N` → dynamic — one row per discovered plugin (entry `action_id`
  is the plugin name; alpha-sorted)
- `P`    → action_id `type_path` — "Type a path explicitly" (auto-appended)
- `S`    → action_id `scan_all`  — "Scan ALL plugins under this folder" (extra_option)
- `A`    → action_id `ask`       — "Ask" (auto-appended)
- `B`    → action_id `back`      — "Back" (auto-appended)
- `M`    → action_id `main`      — "Main menu" (auto-appended)
- `0`    → action_id `exit`      — "Exit" (auto-appended)

Build the entries as a JSON array (one per discovered plugin) and queue
via `print_menu.py dynamic`, passing the `S` Scan-all row as an
`extra_option`, then end the turn:

```bash
export CPV_SKILL_MENUS_DIR="${CLAUDE_PLUGIN_ROOT}/skills/cpv-main-menu-skill/skill-menus"
python "${CLAUDE_PLUGIN_ROOT}/scripts/print_menu.py" dynamic \
  '{"slug":"path-source-multi","entries":["<plugin-a>","<plugin-b>","<plugin-c>"],"extra_options":[{"key":"S","action_id":"scan_all","label":"Scan ALL plugins under this folder"}]}' \
  >/dev/null 2>&1
```

(Replace the `entries` array with the actual discovered plugin names —
`print_menu.py` alpha-sorts and numbers them. The `P`/`A`/`B`/`M`/`0`
rows are auto-appended; do NOT add them by hand.)

#### Case 5 — Plain folder (default fallback)

Spec rows (slug `path-source-plain`):

- `1` → action_id `current_folder` — "Treat the current folder as the target"
- `T` → action_id `type_path`      — "Type a different path / name / URL"
- `A` → action_id `ask`            — "Ask the agent for a recommendation"
- `0` → action_id `cancel`         — "Cancel / Exit"

Queue the spec via `print_menu.py fixed 4` and end the turn.

```bash
export CPV_SKILL_MENUS_DIR="${CLAUDE_PLUGIN_ROOT}/skills/cpv-main-menu-skill/skill-menus"
python "${CLAUDE_PLUGIN_ROOT}/scripts/print_menu.py" fixed 4 >/dev/null 2>&1
```

#### Routing (applies to every case)

  - The key that maps to "this project / this plugin / this folder"
    sets `TARGET=$(pwd)` (or, for Layout C and Multi-plugin cases, the
    derived sub-target). The orchestrator continues with that path.
  - The "Type a different path / name / URL" row triggers a plain-text
    prompt: `Enter the path / name / URL:`. The user MUST type at
    least one character. Capture as `TARGET`. This row is key `T` in the
    FIXED mini-menus (Cases 1/2/3/5) and key `P` in the DYNAMIC
    multi-plugin mini-menu (Case 4, where `print_menu.py dynamic`
    auto-appends `P` for type-a-path).
  - `0` → `Cancelled — no actions taken.` and stop.

Always put the most-likely choice on key `1` — that lets the user
pick the common path with a single keystroke.

For leaves that need MULTIPLE path-shaped inputs (e.g. report path AND
plugin path), queue the mini-menu spec once per input.

When the orchestrator references `TARGET` in execution snippets below,
that's the value captured by this mini-menu.

---

### 3.0b "Ask the agent" shortcut (MANDATORY — present on EVERY menu)

Every menu spec in §3.0, §3.1, …, §3.10, and the §3.0a path-source
mini-menus MUST include a row with `"key": "A"` immediately before the
`"key": "0"` cancel row:

```json
{"key": "A", "action_id": "ask", "label": "Ask the agent — free-form chat with an Opus agent — paste logs, ask questions, get a plan"}
```

The Stop hook renders the row at its natural column width; CMS handles
padding. NEVER duplicate-render the menu inline; CMS Stop hook emits
via `systemMessage`.

**Routing for `A` — IMMEDIATE suggestion + free-form chat (NO greeting,
NO menu, NO AskUserQuestion)**:

Picking `A` already IS the user's request to chat. Do NOT ask
"what can I help you with?" — that wastes a turn. Instead the menu
agent IMMEDIATELY hands control to a fresh Opus sub-agent (dispatched
via the `Agent` tool with `subagent_type: cpv-spark-agent` and
`model: opus`). The sub-agent's FIRST output is its analysis +
suggestion — not a question, not a greeting, not a menu.

The sub-agent receives a single prompt that bundles:

1. The user's recent context (last command, last validation report
   path, last visible error block, current $PWD, layout-detection
   result, any unresolved findings under `reports/` or `design/tasks/`).
2. The exact menu the user was looking at when they picked `A`.
3. The most recent ~20 messages from the parent conversation (so the
   sub-agent can see error blocks and `gh run` output the user pasted
   into the orchestrator before picking `A`).
4. An explicit instruction: **first message MUST be the analysis +
   suggestion (or plan). NOT a greeting. NOT a question. NOT a menu.**

The sub-agent's first message looks like:

```
Looking at your situation:
  - <observation 1 from the recent context>
  - <observation 2>
  - <inferred problem in one line>

Suggestion: <concrete recommendation>.
Plan:
  1. <step>
  2. <step>
  3. <step>

Reply `ok` / `yes` / `go` to execute, or tell me what to change.
```

Then the sub-agent stays in **multi-turn dialog mode**:

- Reads the user's free-form reply (could be `yes`, `no, do X instead`,
  a pasted log, a clarifying question, etc.).
- Adjusts the plan based on the reply.
- Asks plain-text follow-up questions ONLY when genuinely needed —
  never as a substitute for the initial suggestion.
- Waits for explicit approval (`yes` / `ok` / `go` / `approved` /
  similar) before running anything.
- Routes the approved action through the standard CPV launcher
  (`remote_validation.py <alias>`, `add_component.py`, etc.).
- After execution, prints a 3-line summary + report path, then asks
  `Anything else?` and continues the dialog.
- Ends the chat ONLY when the user types `done`, `exit`, `bye`, `0`,
  or `back to menu` — at that point the sub-agent returns
  `Returning to menu.` and the cpv-main-menu agent queues the §3.99
  "do something else?" spec via `print_menu.py` (Stop hook emits it
  post-turn).

**Critical rules** (encode in every menu agent's prompt):

- The sub-agent's FIRST message MUST contain a concrete suggestion
  derived from the context — never an open-ended "what would you like
  to do?" question.
- NEVER queue a menu spec inside the chat. The user is in free-form
  dialog; menus defeat the purpose.
- NEVER call `AskUserQuestion`. Multi-choice prompts also defeat the
  purpose; the user must be able to paste arbitrary text.
- NEVER auto-execute. Wait for explicit text approval (preserves
  Rule 1 — no proactive project work).
- DO accept multi-line user input verbatim (50-line log dumps are one
  conversational turn).
- DO route concrete actions through CPV launchers — never improvise a
  one-off bash command when a CPV recipe exists.

---

### 3.0 Top-level menu (8 categories + Help + Ask + Cancel)

v2.90.0 (TRDD-c50531c2 — menu unification): canonical 8-category
top-level structure. Each category is a verb the user actually wants
to do. Local-vs-GitHub becomes a sub-leaf choice, not a top-level
category. AI-graded quality (semantic) is a leaf inside Diagnose, not
its own root. Cache optimization gets its own root because it's a
distinct workflow (audit → refactor for cache-friendliness) from
generic Fix.

Fixed key→action map for the top-level menu (the orchestrator routes
the typed key from THIS table; the rendered menu is presentation only):

| Key | Action ID      | Label shown to user                                                                |
|-----|----------------|-------------------------------------------------------------------------------------|
| 1   | validate       | Validate — Check that a plugin / marketplace / component is well-formed             |
| 2   | fix            | Fix — Auto-fix issues that a previous validation found                              |
| 3   | cache_optimize | Optimize for Cache — Prompt-cache invalidation audit + cache-aware refactor (CA-01..CA-07) |
| 4   | diagnose       | Diagnose — Deep audit + AI-graded quality review (semantic, opus, on request)       |
| 5   | update         | Update — Upgrade plugin to latest canonical pipeline standard                       |
| 6   | create         | Create — Scaffold plugin, marketplace, skill, agent, command, hook, MCP server      |
| 7   | publish        | Publish & Migrate — Branch rules, link to marketplace, publish, migrate marketplace |
| 8   | manage         | Manage — List installed plugins, install / update / enable / disable / doctor       |
| H   | help           | Help / About — Show the menu overview, list of commands, version                    |
| A   | ask            | Ask the agent — Let the agent suggest the best next action right now                |
| 0   | cancel         | Cancel / Exit — Stop without doing anything                                         |

Queue the menu spec via `print_menu.py fixed 5` and END THE TURN.
NEVER print the menu inline; CMS Stop hook emits via `systemMessage`:

```bash
export CPV_SKILL_MENUS_DIR="${CLAUDE_PLUGIN_ROOT}/skills/cpv-main-menu-skill/skill-menus"
python "${CLAUDE_PLUGIN_ROOT}/scripts/print_menu.py" fixed 5 >/dev/null 2>&1
```

END THE TURN.

Note that the top-level menu has NO `B — Back` row (it IS the parent
of every other menu). All sub-menus include `B — Back`.

### Sub-menu mapping (v2.90.1 — physically renumbered)

The 8 top-level rows route DIRECTLY to the sub-menu sections in this
file. Section numbers now match the top-level row numbers — no
indirection, no "+§3.Y as sub-leaves" hedging.

| Top row              | Sub-menu section                                                       |
|----------------------|------------------------------------------------------------------------|
| 1 Validate           | §3.1 (Validate sub-menu — nested; sub-menus group related actions)     |
| 2 Fix                | §3.2 (Fix sub-menu)                                                    |
| 3 Optimize for Cache | §3.3 (Cache sub-menu — promoted from drill-in to top-level)            |
| 4 Diagnose           | §3.4 (Diagnose sub-menu — deep audit + semantic AI grading)            |
| 5 Update             | §3.5 (Update sub-menu — upgrade to current pipeline standard)          |
| 6 Create             | §3.6 (Create sub-menu)                                                 |
| 7 Publish & Migrate  | §3.7 (Publish & Migrate sub-menu — branch rules, link, publish, migrate)|
| 8 Manage             | §3.8 (Manage sub-menu)                                                 |

The orchestrator looks up the sub-menu section in the table above based
on the user's top-row pick, then queues that section's spec via
`print_menu.py` and ends the turn.

---

### Post-validate flow (applies to every leaf in §3.1)

After a leaf anywhere in §3.1 (including the nested §3.1.6 From-GitHub
leaves) finishes and the report is on disk, the orchestrator MUST queue
the §3.10 post-validate fix menu spec (NEVER the generic §3.99 "do
something else" spec). This is non-negotiable: the user always gets
the explicit "fix N or end" choice after a validation, never just
"what's next?".

### 3.1 Validate sub-menu (nested)

> **Sizing rule:** group related actions into sub-menus where it aids
> discovery; a leaf menu MAY exceed 7 rows when the actions are a fixed
> enumeration (e.g. the per-scanner security rows).

When the user reaches this menu, the orchestrator queues this Level-1
spec via `print_menu.py`. Every option that takes a path triggers the **project-type
auto-detection** (see "Project-type auto-detection" above) BEFORE
invoking the underlying validator.

The **path-source mini-menu (§3.0a)** is invoked for every leaf that
needs a path — its row 1 ("Current project folder $PWD") is the
one-keystroke shortcut for "validate the project I'm currently in".

#### Level 1 — Validate sub-menu (top)

Fixed key→action map (slug `validate`):

| Key | Action ID    | Label shown to user                                                                          |
|-----|--------------|----------------------------------------------------------------------------------------------|
| 1   | val_plugin   | Plugin (full audit) — Run every check we have on a whole plugin folder                       |
| 2   | val_component| Single component — Skill / agent / command / hook / MCP / LSP / output-style / rule          |
| 3   | val_mkt      | Marketplace — Local folder / GitHub / any git URL / inline settings.json                     |
| 4   | val_scope    | Scope — Project-scope (git-tracked) / Local-scope (not in git)                               |
| 5   | val_quality  | Specific quality check — Security / cache / xref / docs / encoding / lint / other            |
| 6   | val_github   | From GitHub — Plugin or marketplace pulled from a GitHub repo (tmp clone + cleanup)          |
| 7   | val_batch    | Batch / fleet (v2.101.0) — Multiple plugins (marketplace / list / @listfile / scope)         |
| A   | ask          | Ask the agent for a recommendation                                                           |
| B   | back         | Back — Go back to the top-level menu                                                         |
| 0   | cancel       | Cancel / Exit — Stop without doing anything                                                  |

Queue the spec via `print_menu.py fixed 6` and end the turn. NEVER
print the menu inline; CMS Stop hook emits via `systemMessage`:

```bash
export CPV_SKILL_MENUS_DIR="${CLAUDE_PLUGIN_ROOT}/skills/cpv-main-menu-skill/skill-menus"
python "${CLAUDE_PLUGIN_ROOT}/scripts/print_menu.py" fixed 6 >/dev/null 2>&1
```

END THE TURN.

All leaves below FIRST run the project-type detection (see top of file)
on the resolved path, then drill in. Per-leaf recipes:

#### 3.1.1 Plugin (full audit)

- **arg-prompt**: `Path to the plugin? (e.g. ~/Code/my-plugin/ — or just the plugin name for auto-discovery)`
- **execution**:
  ```bash
  CLAUDE_PRIVATE_USERNAMES="$(whoami)" uv run --with pyyaml \
    python "$LAUNCHER" plugin "$TARGET_PATH" --report "$MAIN_ROOT/reports/validate_plugin/$TS-$SLUG.md"
  ```

#### 3.1.2 Single component — Level-2 sub-menu

Fixed key→action map (slug `validate-component`):

| Key | Action ID    | Label shown to user                                                          |
|-----|--------------|-------------------------------------------------------------------------------|
| 1   | comp_skill   | SKILL.md — Header (frontmatter), structure, and content rules                 |
| 2   | comp_agent   | Agent .md — Header, model, tools, examples (2+ <example> blocks)              |
| 3   | comp_command | Command .md — Header, target agent, tool allowlist, argument hint             |
| 4   | comp_hook    | Hook (hooks.json) — hooks.json layout + event names + the scripts the hook calls |
| 5   | comp_mcp     | MCP server — setup (transport, env vars, security checks)                     |
| 6   | comp_lsp     | LSP server — Language-server setup in plugin.json                             |
| 7   | comp_style   | Output-style or Rule file — Output-style files or .claude/rules/*.md          |
| A   | ask          | Ask the agent                                                                 |
| B   | back         | Back — Go back to the Validate menu                                           |
| 0   | cancel       | Cancel / Exit                                                                 |

Queue the spec via `print_menu.py fixed 7` and end the turn. NEVER
print the menu inline; CMS Stop hook emits via `systemMessage`:

```bash
export CPV_SKILL_MENUS_DIR="${CLAUDE_PLUGIN_ROOT}/skills/cpv-main-menu-skill/skill-menus"
python "${CLAUDE_PLUGIN_ROOT}/scripts/print_menu.py" fixed 7 >/dev/null 2>&1
```

END THE TURN.

##### 3.1.2.1 SKILL.md

- **arg-prompt**: `Path to the skill directory? (e.g. ./skills/my-skill/)`
- **execution**:
  ```bash
  CLAUDE_PRIVATE_USERNAMES="$(whoami)" uv run --with pyyaml \
    python "$LAUNCHER" skill "$TARGET_PATH" --report "$MAIN_ROOT/reports/validate_skill/$TS-$SLUG.md"
  ```

##### 3.1.2.2 Agent .md

- **arg-prompt**: `Path to the agent .md file? (e.g. ./agents/my-agent.md)`
- **execution**:
  ```bash
  uv run --with pyyaml python "$LAUNCHER" agent "$TARGET_PATH" \
    --report "$MAIN_ROOT/reports/validate_agent/$TS-$SLUG.md"
  ```

##### 3.1.2.3 Command .md

- **arg-prompt**: `Path to the command .md file? (e.g. ./commands/my-command.md)`
- **execution**:
  ```bash
  uv run --with pyyaml python "$LAUNCHER" command "$TARGET_PATH" \
    --report "$MAIN_ROOT/reports/validate_command/$TS-$SLUG.md"
  ```

##### 3.1.2.4 Hook (hooks.json)

- **arg-prompt**: `Path to hooks.json (or to the plugin root containing hooks/hooks.json)?`
- **execution**:
  ```bash
  uv run --with pyyaml python "$LAUNCHER" hook "$TARGET_PATH" \
    --report "$MAIN_ROOT/reports/validate_hook/$TS-$SLUG.md"
  ```

##### 3.1.2.5 MCP server

- **arg-prompt**: `Path to the plugin (or to .mcp.json directly)?`
- **execution**:
  ```bash
  uv run --with pyyaml python "$LAUNCHER" mcp "$TARGET_PATH" \
    --report "$MAIN_ROOT/reports/validate_mcp/$TS-$SLUG.md"
  ```

##### 3.1.2.6 LSP server

- **arg-prompt**: `Path to the plugin?`
- **execution**:
  ```bash
  uv run --with pyyaml python "$LAUNCHER" lsp "$TARGET_PATH" \
    --report "$MAIN_ROOT/reports/validate_lsp/$TS-$SLUG.md"
  ```

##### 3.1.2.7 Output-style or Rule file — Level-3 sub-menu

Fixed key→action map (slug `validate-style-or-rule`):

| Key | Action ID  | Label shown to user                                                    |
|-----|------------|-------------------------------------------------------------------------|
| 1   | style_file | Output-style — Output-style files in .claude/output-styles/             |
| 2   | rule_file  | Rule file — Rule-file headers and content (.claude/rules/*.md)          |
| A   | ask        | Ask the agent                                                           |
| B   | back       | Back — Go back to the Single-component menu                             |
| 0   | cancel     | Cancel / Exit                                                           |

Queue the spec via `print_menu.py fixed 8` and end the turn. NEVER
print the menu inline; CMS Stop hook emits via `systemMessage`:

```bash
export CPV_SKILL_MENUS_DIR="${CLAUDE_PLUGIN_ROOT}/skills/cpv-main-menu-skill/skill-menus"
python "${CLAUDE_PLUGIN_ROOT}/scripts/print_menu.py" fixed 8 >/dev/null 2>&1
```

END THE TURN.

###### 3.1.2.7.1 Output-style

- **arg-prompt**: `Path to the project root? (validates .claude/output-styles/*.md frontmatter via the project-scope alias)`
- **execution**:
  ```bash
  uv run --with pyyaml python "$LAUNCHER" project-scope "$TARGET_PATH" \
    --report "$MAIN_ROOT/reports/validate_project_scope/$TS-$SLUG.md"
  ```
- **note**: there is no dedicated `validate_output_style.py`; output-style
  files are checked as part of `project-scope` validation.

###### 3.1.2.7.2 Rule file (Cursor-style .md rule files)

- **arg-prompt**: `Path to the plugin (or directly to a .claude/rules/ folder)?`
- **execution**:
  ```bash
  uv run --with pyyaml python "$LAUNCHER" rules "$TARGET_PATH" \
    --report "$MAIN_ROOT/reports/validate_rules/$TS-$SLUG.md"
  ```

#### 3.1.3 Marketplace — Level-2 sub-menu

Fixed key→action map (slug `validate-marketplace`):

| Key | Action ID  | Label shown to user                                                     |
|-----|------------|--------------------------------------------------------------------------|
| 1   | mkt_local  | Local folder — A marketplace folder on this machine                      |
| 2   | mkt_github | GitHub (owner/repo) — Clone a marketplace from a GitHub repo             |
| 3   | mkt_giturl | Any git URL — GitLab / Bitbucket / SSH / self-hosted                     |
| 4   | mkt_inline | Inline (settings.json) — Marketplace blocks pasted directly              |
| A   | ask        | Ask the agent                                                            |
| B   | back       | Back — Go back to the Validate menu                                      |
| 0   | cancel     | Cancel / Exit                                                            |

Queue the spec via `print_menu.py fixed 9` and end the turn. NEVER
print the menu inline; CMS Stop hook emits via `systemMessage`:

```bash
export CPV_SKILL_MENUS_DIR="${CLAUDE_PLUGIN_ROOT}/skills/cpv-main-menu-skill/skill-menus"
python "${CLAUDE_PLUGIN_ROOT}/scripts/print_menu.py" fixed 9 >/dev/null 2>&1
```

END THE TURN.

##### 3.1.3.1 Local folder

- **arg-prompt**: `Path to the marketplace folder? (containing .claude-plugin/marketplace.json)`
- **execution**:
  ```bash
  uv run --with pyyaml python "$LAUNCHER" marketplace "$TARGET_PATH" --strict \
    --report "$MAIN_ROOT/reports/validate_marketplace/$TS-$SLUG.md"
  ```

##### 3.1.3.2 GitHub (owner/repo)

- **arg-prompt**: `GitHub spec? (owner/repo or https://github.com/owner/repo)`
- **execution**:
  ```bash
  uv run --with pyyaml python "$LAUNCHER" github --marketplace "$REPO" \
    --report "$MAIN_ROOT/reports/validate_github_marketplace/$TS-$(echo "$REPO" | tr '/' '_').md"
  ```

##### 3.1.3.3 Any git URL (gitlab/bitbucket/self-hosted/SSH)

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

##### 3.1.3.4 Inline (settings.json — extraKnownMarketplaces)

- **arg-prompt**: `Path to settings.json containing the inline marketplaces block?`
- **execution**:
  ```bash
  uv run --with pyyaml python "$LAUNCHER" settings-marketplace "$TARGET_PATH" --strict \
    --report "$MAIN_ROOT/reports/validate_settings_marketplace/$TS-$SLUG.md"
  ```

#### 3.1.4 Scope — Level-2 sub-menu

Fixed key→action map (slug `validate-scope`):

| Key | Action ID    | Label shown to user                                                     |
|-----|--------------|--------------------------------------------------------------------------|
| 1   | scope_project| Project-scope (git-tracked) — Files in .claude/ that ARE checked into git |
| 2   | scope_local  | Local-scope (not in git) — settings.local.json + ~/.claude.json state    |
| A   | ask          | Ask the agent                                                            |
| B   | back         | Back — Go back to the Validate menu                                      |
| 0   | cancel       | Cancel / Exit                                                            |

Queue the spec via `print_menu.py fixed 10` and end the turn. NEVER
print the menu inline; CMS Stop hook emits via `systemMessage`:

```bash
export CPV_SKILL_MENUS_DIR="${CLAUDE_PLUGIN_ROOT}/skills/cpv-main-menu-skill/skill-menus"
python "${CLAUDE_PLUGIN_ROOT}/scripts/print_menu.py" fixed 10 >/dev/null 2>&1
```

END THE TURN.

##### 3.1.4.1 Project-scope (git-tracked)

- **arg-prompt**: `Path to the project root? (validates git-tracked elements: settings.json, agents/skills/commands/rules/output-styles, .mcp.json)`
- **execution**:
  ```bash
  uv run --with pyyaml python "$LAUNCHER" project-scope "$TARGET_PATH" \
    --report "$MAIN_ROOT/reports/validate_project_scope/$TS-$SLUG.md"
  ```

##### 3.1.4.2 Local-scope (not in git)

- **arg-prompt**: `Path to the project root? (validates non-git-tracked elements: settings.local.json, gitignored components, ~/.claude.json per-project state)`
- **execution**:
  ```bash
  uv run --with pyyaml python "$LAUNCHER" local-scope "$TARGET_PATH" \
    --report "$MAIN_ROOT/reports/validate_local_scope/$TS-$SLUG.md"
  ```

#### 3.1.5 Specific quality check — Level-2 sub-menu

Fixed key→action map (slug `validate-quality`):

| Key | Action ID  | Label shown to user                                                          |
|-----|------------|-------------------------------------------------------------------------------|
| 1   | q_security | Security — Security scanners + rule packs (drills into §3.16)                |
| 2   | q_cache    | Cache patterns — Prompt-cache invalidation audit (dispatches to §3.3)        |
| 3   | q_xref     | Cross-references (xref) — Stale references between agents/skills/commands    |
| 4   | q_docs     | Documentation — README + doc structure rules                                 |
| 5   | q_encoding | File encoding — UTF-8, BOM marker, line endings on every .md/.json/.yaml     |
| 6   | q_lint     | Lint scripts (ruff / mypy / shellcheck) — Run linters on every script        |
| 7   | q_other    | Other (enterprise/scoring/telem) — Compliance / scoring / telemetry hazards  |
| A   | ask        | Ask the agent                                                                |
| B   | back       | Back — Go back to the Validate menu                                          |
| 0   | cancel     | Cancel / Exit                                                                |

Queue the spec via `print_menu.py fixed 11` and end the turn. NEVER
print the menu inline; CMS Stop hook emits via `systemMessage`:

```bash
export CPV_SKILL_MENUS_DIR="${CLAUDE_PLUGIN_ROOT}/skills/cpv-main-menu-skill/skill-menus"
python "${CLAUDE_PLUGIN_ROOT}/scripts/print_menu.py" fixed 11 >/dev/null 2>&1
```

END THE TURN.

##### 3.1.5.1 Security — drill into sub-menu

See §3.16 below.

##### 3.1.5.2 Cache patterns — dispatch to §3.3 Optimize for Cache

See §3.3 below. This leaf is a convenience entry — the user can also reach
the same workflow directly from top-level row 3.

##### 3.1.5.3 Cross-references (xref)

- **arg-prompt**: `Path to the plugin?`
- **execution**:
  ```bash
  uv run --with pyyaml python "$LAUNCHER" xref "$TARGET_PATH" \
    --report "$MAIN_ROOT/reports/validate_xref/$TS-$SLUG.md"
  ```

##### 3.1.5.4 Documentation

- **arg-prompt**: `Path to the plugin?`
- **execution**:
  ```bash
  uv run --with pyyaml python "$LAUNCHER" docs "$TARGET_PATH" \
    --report "$MAIN_ROOT/reports/validate_documentation/$TS-$SLUG.md"
  ```

##### 3.1.5.5 File encoding

- **arg-prompt**: `Path to the plugin?`
- **execution**:
  ```bash
  uv run --with pyyaml python "$LAUNCHER" encoding "$TARGET_PATH" \
    --report "$MAIN_ROOT/reports/validate_encoding/$TS-$SLUG.md"
  ```

##### 3.1.5.6 Lint pass (ruff / mypy / shellcheck)

- **arg-prompt**: `Path to the plugin?`
- **execution**:
  ```bash
  uv run --with pyyaml python "$LAUNCHER" lint "$TARGET_PATH" \
    --report "$MAIN_ROOT/reports/lint/$TS-$SLUG.md"
  ```

##### 3.1.5.7 Other (enterprise / scoring / telemetry) — Level-3 sub-menu

Fixed key→action map (slug `validate-other`):

| Key | Action ID    | Label shown to user                                                          |
|-----|--------------|-------------------------------------------------------------------------------|
| 1   | enterprise   | Enterprise — Compliance / governance / IT-managed-settings rules              |
| 2   | scoring      | Scoring self-check — Verify CPV's own pass / fail / severity logic still works|
| 3   | telemetry    | Telemetry hazards (risky env vars) — PLUGIN_SEED_DIR / SHELL_PREFIX / etc.   |
| A   | ask          | Ask the agent                                                                 |
| B   | back         | Back — Go back to the Specific-quality-check menu                             |
| 0   | cancel       | Cancel / Exit                                                                 |

Queue the spec via `print_menu.py fixed 12` and end the turn. NEVER
print the menu inline; CMS Stop hook emits via `systemMessage`:

```bash
export CPV_SKILL_MENUS_DIR="${CLAUDE_PLUGIN_ROOT}/skills/cpv-main-menu-skill/skill-menus"
python "${CLAUDE_PLUGIN_ROOT}/scripts/print_menu.py" fixed 12 >/dev/null 2>&1
```

END THE TURN.

###### 3.1.5.7.1 Enterprise

- **arg-prompt**: `Path to the plugin?`
- **execution**:
  ```bash
  uv run --with pyyaml python "$LAUNCHER" enterprise "$TARGET_PATH" \
    --report "$MAIN_ROOT/reports/validate_enterprise/$TS-$SLUG.md"
  ```

###### 3.1.5.7.2 Scoring self-check

- **arg-prompt**: `Path to the plugin?`
- **execution**:
  ```bash
  uv run --with pyyaml python "$LAUNCHER" scoring "$TARGET_PATH" \
    --report "$MAIN_ROOT/reports/validate_scoring/$TS-$SLUG.md"
  ```

###### 3.1.5.7.3 Telemetry hazards

- **arg-prompt**: `Path to the plugin?`
- **execution**:
  ```bash
  uv run --with pyyaml python "$LAUNCHER" telemetry "$TARGET_PATH" \
    --report "$MAIN_ROOT/reports/validate_telemetry/$TS-$SLUG.md"
  ```

#### 3.1.6 From GitHub — Level-2 sub-menu

Fixed key→action map (slug `validate-github`):

| Key | Action ID  | Label shown to user                                                  |
|-----|------------|-----------------------------------------------------------------------|
| 1   | gh_plugin  | Plugin (owner/repo) — Check a plugin from a GitHub repo               |
| 2   | gh_mkt     | Marketplace (owner/repo) — Check a marketplace from a GitHub repo     |
| A   | ask        | Ask the agent                                                         |
| B   | back       | Back — Go back to the Validate menu                                   |
| 0   | cancel     | Cancel / Exit                                                         |

Queue the spec via `print_menu.py fixed 13` and end the turn. NEVER
print the menu inline; CMS Stop hook emits via `systemMessage`:

```bash
export CPV_SKILL_MENUS_DIR="${CLAUDE_PLUGIN_ROOT}/skills/cpv-main-menu-skill/skill-menus"
python "${CLAUDE_PLUGIN_ROOT}/scripts/print_menu.py" fixed 13 >/dev/null 2>&1
```

END THE TURN.

##### 3.1.6.1 Plugin from GitHub

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

##### 3.1.6.2 Marketplace from GitHub

- **arg-prompts**: same as 3.1.6.1
- **execution**:
  ```bash
  CLAUDE_PRIVATE_USERNAMES="$(whoami)" uv run --with pyyaml \
    python "$LAUNCHER" github --marketplace "$REPO" --report "$MAIN_ROOT/reports/validate_github_marketplace/$TS-$(echo "$REPO" | tr '/' '_').md"
  ```

#### 3.1.7 Batch / fleet — Level-2 sub-menu (v2.101.0)

The fleet-scale layer added in TRDD-3dcbb37c + TRDD-a175f78d. Each leaf
fans N parallel subagent dispatches out of ONE main-session message
(the only place the Agent tool can parallelise per Anthropic spec).
Inputs are universal: a single plugin path / URL, marketplace path /
URL, comma-separated list, `@/path/to/list.txt`, single skill folder,
skill pack, OR mixed marketplace.json entries. Resolved by
`scripts/cpv_marketplace_input.py`; planned + status-tabled by
`scripts/cpv_batch_orchestrator.py`.

Fixed key→action map (slug `validate-batch`):

| Key | Action ID         | Label shown to user                                                                                |
|-----|-------------------|-----------------------------------------------------------------------------------------------------|
| 1   | batch_validate    | Validate (read-only, fan-out) — /cpv-batch-validate (cpv-plugin-validator-agent batch_validate)              |
| 2   | batch_security    | Security audit (5 ext. scanners) — /cpv-batch-security-audit (cpv-plugin-validator-agent batch_security)     |
| 3   | batch_cache_audit | Caching audit (CA-01..CA-07) — /cpv-batch-caching-audit (cpv-cache-optimizer-agent batch_audit)        |
| 4   | batch_cache_opt   | Caching optimize (audit + fix) — /cpv-batch-caching-optimize (cpv-cache-optimizer-agent batch_fix)     |
| 5   | batch_fix         | Fix (per-plugin) — /cpv-batch-fix (cpv-plugin-fixer-agent batch_per_plugin)                                  |
| 6   | batch_val_fix     | Validate + fix (same-turn) — /cpv-batch-validate-and-fix (cpv-plugin-fixer-agent same_turn_validate_fix)     |
| 7   | batch_full        | Full scan + fix (same-turn) — /cpv-batch-full-scan-and-fix (cpv-plugin-fixer-agent same_turn_full)           |
| 8   | batch_scope       | Scope-aware doctor (LOCAL only) — /cpv-batch-scope-diagnose (cpv-doctor-agent batch_scope_diagnose)|
| A   | ask               | Ask the agent                                                                                       |
| B   | back              | Back — Go back to the Validate menu                                                                |
| 0   | cancel            | Cancel / Exit                                                                                       |

Queue the spec via `print_menu.py fixed 14` and end the turn. NEVER
print the menu inline; CMS Stop hook emits via `systemMessage`:

```bash
export CPV_SKILL_MENUS_DIR="${CLAUDE_PLUGIN_ROOT}/skills/cpv-main-menu-skill/skill-menus"
python "${CLAUDE_PLUGIN_ROOT}/scripts/print_menu.py" fixed 14 >/dev/null 2>&1
```

END THE TURN.

The path-source mini-menu (§3.0a) is skipped for these leaves —
the underlying skills run their own universal-input parser. Just
prompt for the input string.

##### 3.1.7.1 Batch validate

- **arg-prompt**: `Input? (single plugin path/URL, marketplace path/URL, comma-list, @listfile, skill folder, or skill pack)`
- **execution**: invoke the skill via the Skill tool — `Skill({skill: "claude-plugins-validation:cpv-batch-validate", args: "<input>"})`. The skill resolves the input, plans shards (≤8 parallel by default, cap 16), and the main session fans out the cpv-plugin-validator-agent dispatches in one message.

##### 3.1.7.2 Batch security audit

- **arg-prompt**: `Input? (same shapes as 3.1.7.1)`
- **execution**: `Skill({skill: "claude-plugins-validation:cpv-batch-security-audit", args: "<input>"})`.

##### 3.1.7.3 Batch caching audit

- **arg-prompt**: `Input? (same shapes as 3.1.7.1)`
- **execution**: `Skill({skill: "claude-plugins-validation:cpv-batch-caching-audit", args: "<input>"})`.

##### 3.1.7.4 Batch caching optimize

- **arg-prompt**: `Input? (same shapes as 3.1.7.1)`
- **execution**: `Skill({skill: "claude-plugins-validation:cpv-batch-caching-optimize", args: "<input>"})`. Includes both audit AND fix passes in priority order.

##### 3.1.7.5 Batch fix (per-plugin)

- **arg-prompt**: `Input? (same shapes as 3.1.7.1)`
- **execution**: `Skill({skill: "claude-plugins-validation:cpv-batch-fix", args: "<input>"})`. One cpv-plugin-fixer-agent subagent per plugin in `batch_per_plugin` mode.

##### 3.1.7.6 Batch validate + fix (same-turn)

- **arg-prompt**: `Input? (same shapes as 3.1.7.1)`
- **execution**: `Skill({skill: "claude-plugins-validation:cpv-batch-validate-and-fix", args: "<input>"})`. Single-pass per-file read; scans + verifies FPs via llm-externalizer file-range syntax + fixes in one turn. ~3-5× cheaper than separate passes.

##### 3.1.7.7 Batch full scan + fix (same-turn)

- **arg-prompt**: `Input? (same shapes as 3.1.7.1)`
- **execution**: `Skill({skill: "claude-plugins-validation:cpv-batch-full-scan-and-fix", args: "<input>"})`. Combined validate + security + caching audit + caching optimize + fix in one turn per plugin.

##### 3.1.7.8 Scope-aware doctor — Level-3 sub-menu

Fixed key→action map (slug `validate-batch-scope`):

| Key | Action ID         | Label shown to user                                                                |
|-----|-------------------|-------------------------------------------------------------------------------------|
| 1   | scope_diagnose    | Diagnose (read-only) — /cpv-batch-scope-diagnose (surface issues across scopes)    |
| 2   | scope_fix         | Fix (apply fixes) — /cpv-batch-scope-fix (fix issues a prior diagnose surfaced)    |
| 3   | scope_diag_fix    | Diagnose + fix (same-turn) — /cpv-batch-scope-diagnose-and-fix                     |
| A   | ask               | Ask the agent                                                                       |
| B   | back              | Back — Go back to the Batch / fleet menu                                            |
| 0   | cancel            | Cancel / Exit                                                                       |

Queue the spec via `print_menu.py fixed 15` and end the turn. NEVER
print the menu inline; CMS Stop hook emits via `systemMessage`:

```bash
export CPV_SKILL_MENUS_DIR="${CLAUDE_PLUGIN_ROOT}/skills/cpv-main-menu-skill/skill-menus"
python "${CLAUDE_PLUGIN_ROOT}/scripts/print_menu.py" fixed 15 >/dev/null 2>&1
```

END THE TURN.

- **arg-prompts** (in order for ALL three leaves):
  1. `Project folder list? (single path / comma-separated / @listfile — LOCAL only, URLs rejected with CRITICAL)`
  2. `Scope? (full / user / project / local — default: full)`
- **execution**:
  - 3.1.7.8.1 → `Skill({skill: "claude-plugins-validation:cpv-batch-scope-diagnose", args: "<paths> --scope <scope>"})`
  - 3.1.7.8.2 → `Skill({skill: "claude-plugins-validation:cpv-batch-scope-fix", args: "<paths> --scope <scope>"})`
  - 3.1.7.8.3 → `Skill({skill: "claude-plugins-validation:cpv-batch-scope-diagnose-and-fix", args: "<paths> --scope <scope>"})`
- **note**: URL inputs are rejected per TRDD-a175f78d §1 — the doctor needs `~/.claude/` filesystem access which a URL cannot represent.

---

### 3.2 Fix sub-menu

For SINGLE-plugin fixes (the common case), use leaves 1-5 below. For
FLEET / MARKETPLACE-scale fixes (TRDD-3dcbb37c, v2.101.0) — many
plugins in parallel — see §3.1.7 Batch / fleet (rows 5-7 cover
batch-fix, batch-validate-and-fix, batch-full-scan-and-fix). The
existing in-flight auto-batch on §3.2.1 step 4 still handles
single-plugin findings counts > safe-ceiling — that's a different
shape than fleet operations.

Fixed key→action map (slug `fix`):

| Key | Action ID      | Label shown to user                                                                          |
|-----|----------------|----------------------------------------------------------------------------------------------|
| 1   | fix_plugin     | Fix plugin issues — From a report file OR a plugin folder (cpv-plugin-fixer-agent)                      |
| 2   | fix_mkt        | Fix marketplace issues — From a report file OR a marketplace folder                           |
| 3   | fix_cache      | Optimize prompt cache — Audit + auto-fix the cache patterns                                   |
| 4   | fix_devitalize | Devitalize security threats — Convert flagged execution-class code into provably-inert data (cpv-plugin-devitalizer-agent); never suppresses a rule, flags load-bearing code |
| 5   | fix_leaks      | Prevent leaks & harden — Redact exposed secrets (runtime-read the needed ones) + implement missing safeguards (cpv-plugin-leaks-preventer-agent); never suppresses a rule, flags what it can't safely fix |
| 6   | fix_batch      | Batch fix (fleet) — Drill into §3.1.7 Batch / fleet                                           |
| A   | ask            | Ask the agent                                                                                 |
| B   | back           | Back — Go back to the top-level menu                                                          |
| 0   | cancel         | Cancel / Exit                                                                                 |

Queue the spec via `print_menu.py fixed 16` and end the turn. NEVER
print the menu inline; CMS Stop hook emits via `systemMessage`:

```bash
export CPV_SKILL_MENUS_DIR="${CLAUDE_PLUGIN_ROOT}/skills/cpv-main-menu-skill/skill-menus"
python "${CLAUDE_PLUGIN_ROOT}/scripts/print_menu.py" fixed 16 >/dev/null 2>&1
```

END THE TURN.

#### 3.2.1 Fix plugin findings

- **arg-prompt**: `Path to a validation report .md file OR a plugin directory?`
- **execution (TRDD-14cc93a6 — runtime routing; v2.98.0 — lowered thresholds + auto-batch)**:
  1. Quick-triage: run `validate_plugin.py --json --no-color <path>` and parse `counts.critical + counts.major + counts.minor` from stdout. Time-budget: ≤60 s. (Skip if the user passed an already-existing `.json` report — read its `counts` directly.)
  2. **If `total_findings == 0`** → reply `Plugin is already clean. ✓` and return to the post-action menu.
  3. **If `total_findings ≤ 20`** → dispatch the **cpv-plugin-fixer-agent agent** with the path. (The single-agent fix loop is sized for opus 200K context with v2.98.0 safe-ceiling ~15-25; raise this threshold proportionally if `cpv-plugin-fixer-agent.model` is upgraded to a 1M variant — then the ceiling is ~50-75.)
  4. **If `total_findings > 20`** → DON'T dispatch cpv-plugin-fixer-agent (it would die mid-loop on a context exhaust). AUTO-DISPATCH the batch protocol from the main session:
     - Reply with: `This plugin has <N> findings — exceeds single-agent safe-ceiling. Auto-dispatching batch protocol (<shard_count> shards × <shard_size> findings).`
     - Run `python3 scripts/cpv_batch_planner.py <path> --shard-size 15` via Bash (zero LLM cost; emits index.json + per-shard manifests).
     - In the SAME main-session message, fan out N parallel `cpv-plugin-fixer-agent` Agent calls in `batch_shard` mode — one per shard, each given its own shard manifest path. This is the ONLY place the Agent tool can parallelise (per Anthropic spec: subagents cannot spawn subagents).
     - After all shards return, run `python3 scripts/cpv_batch_aggregator.py <session-dir>` via Bash and surface the consolidated outcome.
     - The user no longer has to type `/cpv-batch-fix` manually — the menu does the dispatch end-to-end.
  5. **If a dispatched cpv-plugin-fixer-agent returns a line starting with `[BATCH_REQUIRED]`** (the fixer detected the threshold itself), parse the `plugin-root=<P>` token and route to step 4 with that path.
  6. After the chosen workflow returns, surface the one-line summary verbatim and route to the post-action menu.

#### 3.2.2 Fix marketplace findings

- **arg-prompt**: `Path to a marketplace validation report OR a marketplace directory?`
- **execution**: dispatch the **cpv-marketplace-fixer-agent agent**. Handles mechanical fixes AND architectural migrations (Layout A↔B↔C).

#### 3.2.3 Cache optimize

- **arg-prompts** (in order):
  1. `Path to plugin or project root?`
  2. `Also do broader cache-aware refactoring? (yes/no — --broader invokes Phase 4)`
- **execution**: dispatch the **cpv-cache-optimizer-agent** with the path and `--broader` flag if requested.

#### 3.2.4 Devitalize security threats

- **arg-prompt**: `Path to a security report .md file OR a plugin directory?`
- **execution**: dispatch the **cpv-plugin-devitalizer-agent agent** (`model: opus` for the security reasoning) with the path. It scans with `validate_security` + native skillaudit, then converts each flagged execution-class finding into provably-inert data — passing the security gate by neutralizing the code's shape, NEVER by suppressing a rule or relaxing `--strict`. Load-bearing code (live shell-exec, real installers, genuine code-execution features, verified leaked secrets) is FLAGGED to the user, not silently broken. The agent runs a scan → classify → minimal-transform → re-scan-to-prove-inert loop until the scan is clean or only load-bearing findings remain flagged, then returns a before/after report path.

#### 3.2.5 Prevent leaks & harden

- **arg-prompt**: `Path to a security report .md file OR a plugin directory?`
- **execution**: dispatch the **cpv-plugin-leaks-preventer-agent agent** (`model: opus` for the security reasoning) with the path. It scans with `validate_security` + native skillaudit, then redacts every exposed secret (runtime-reading the genuinely-needed ones from env / exported vars / GitHub vars / OS keychain) and implements the missing safeguards (safe config parse, input sanitization, launch/deploy params, prompt-injection pre-scan) — passing the gate by removing leaks and adding safeguards, NEVER by suppressing a rule or relaxing `--strict`. A verified live committed secret is FLAGGED to rotate + purge history, not silently edited; anything that cannot be safely fixed is FLAGGED, never broken. The agent runs a scan → classify → minimal redact/harden → re-scan-to-prove-clean loop until the scan is clean or only flagged findings remain, then returns a before/after report path.

#### 3.2.6 Batch fix (fleet)

This row is a routing shortcut, not a separate workflow. When the user picks `6`, the orchestrator MUST jump to §3.1.7 Batch / fleet so the user can pick which batch variant they actually want (validate-only / fix-only / same-turn validate+fix / same-turn full scan+fix). No path prompt here — the batch sub-menu has its own input prompt accepting all universal shapes (single / marketplace / list / @listfile / mixed).

---

### 3.6 Create sub-menu

Fixed key→action map (slug `create`). Numbers stay in declaration order
because every leaf is a distinct scaffold operation (NOT a dynamic
alpha-sorted list — they are FIXED actions, but they happen to use
digit keys here because there are 10 of them and we want a sequential
mnemonic; cf. §3.8 Manage which mixes letter actions with high-digit
actions):

| Key | Action ID         | Label shown to user                                                                |
|-----|-------------------|-------------------------------------------------------------------------------------|
| 1   | new_plugin        | New plugin (latest pipeline standard) — Fresh plugin repo                          |
| 2   | new_mkt           | New marketplace — Fresh marketplace repo from scratch (Layout A/B/C)               |
| 3   | new_skill         | New skill (in existing plugin) — Add skills/<name>/SKILL.md                         |
| 4   | new_agent         | New agent (in existing plugin) — Add agents/<name>.md                               |
| 5   | new_command       | New slash command (in existing plugin) — Add commands/<name>.md                     |
| 6   | new_hook          | New hook (in existing plugin) — Append entry to hooks/hooks.json                    |
| 7   | new_mcp           | New MCP server (in existing plugin) — Register server in .mcp.json                  |
| 8   | pack_components   | Pack components into a plugin (multi-select)                                        |
| 9   | add_deps          | Add dependencies (existing plugin) — --add NAME[@MKT[@VER]] OR --from PATH-OR-URL  |
| 10  | impl_skills_menu  | Implement cpv-the-skills-menu method (existing) — Decouple skills from agents          |
| A   | ask               | Ask the agent                                                                       |
| B   | back              | Back — Go back to the top-level menu                                                |
| 0   | cancel            | Cancel / Exit                                                                       |

Queue the spec via `print_menu.py fixed 17` and end the turn. NEVER
print the menu inline; CMS Stop hook emits via `systemMessage`:

```bash
export CPV_SKILL_MENUS_DIR="${CLAUDE_PLUGIN_ROOT}/skills/cpv-main-menu-skill/skill-menus"
python "${CLAUDE_PLUGIN_ROOT}/scripts/print_menu.py" fixed 17 >/dev/null 2>&1
```

END THE TURN.

#### 3.6.1 Scaffold a new plugin

- **arg-prompts** (in order):
  1. `Plugin name?`
  2. `Target directory?`
  3. `Layout (A=hub-and-spoke / B=nested monorepo / C=marketplace-in-plugin self-referential)?`
- **execution**: dispatch the **cpv-plugin-creator-agent agent** with the answers. Newly-scaffolded plugins ship with current pipeline standards baked in (idempotent publish.py, cpv_lint_engine, pathlib-only Python, sanitized inputs, validate_pipeline_script_refs rule, no `.sh` scripts).
- **post-execution**: ALWAYS auto-dispatch the **cpv-plugin-diagnoser-agent agent** on the just-scaffolded plugin path. If the diagnosis returns 0 CRITICAL/MAJOR/MINOR, print `✓ Scaffold passes diagnose-plugin clean.` and queue the §3.99 spec via `print_menu.py`. Otherwise let the diagnoser queue its follow-up menu spec so the user can pick a fix path.

#### 3.6.2 Scaffold a new marketplace

- **arg-prompts** (in order):
  1. `Marketplace name?`
  2. `Target directory?`
  3. `Owner GitHub username?`
- **execution**: dispatch the **cpv-plugin-creator-agent agent** in marketplace mode.

#### 3.6.3 Add a skill to an existing plugin

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

#### 3.6.4 Add an agent to an existing plugin

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

#### 3.6.5 Add a slash command to an existing plugin

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

#### 3.6.6 Add a hook to an existing plugin

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

#### 3.6.7 Add an MCP server to an existing plugin

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

#### 3.6.8 Pack components into a new plugin (multi-select)

The recovery path for "Phase 0 plugin-shape detection refused" — converts a folder of standalone components (skill / agent / command / hook / mcp / lsp / monitor / output-style) into a real installable plugin. Also useful for rolling components from disparate projects into a single shared plugin.

- **arg-prompts** (in order):
  1. `Source folder containing components?`
  2. `Target directory for the new plugin?`
  3. `Plugin name (kebab-case, lowercase)?`
  4. `One-line description?`
  5. `Author name?`
  6. `Author email?`
  7. `GitHub owner (optional, leave blank if not publishing)?`

- **discovery + selection** (BEFORE scaffolding):
  ```bash
  uv run --with pyyaml python "${CLAUDE_PLUGIN_ROOT}/scripts/cpv_pack_components.py" \
      "$SOURCE" --list-only
  ```
  The script prints a table grouping discovered components by type. The menu agent then offers a multi-select prompt:
  ```
  Discovered N components in <SOURCE>:
    skill:  my-skill, other-skill
    agent:  my-agent
    command: my-cmd
    hook:    hooks
    mcp:     mcp
    lsp:     lsp
    monitor: monitors
    output-style: casual

  Pick which to pack into the new plugin:
    a) Pack ALL discovered components (recommended)
    b) Pick by type (e.g. "skill,agent" includes everything in those types)
    c) Pick by name (e.g. "my-skill,my-agent,my-cmd")
    d) Cancel — leave the directory untouched
  Type a / b / c / d:
  ```
  Translate the user's answer into `--all` or `--include type=name,name [...]` flags.

- **execution**:
  ```bash
  uv run --with pyyaml python "${CLAUDE_PLUGIN_ROOT}/scripts/cpv_pack_components.py" \
      "$SOURCE" "$TARGET" \
      --name "$NAME" --description "$DESC" \
      --author "$AUTHOR" --author-email "$AUTHOR_EMAIL" \
      --github-owner "$GITHUB_OWNER" \
      "${INCLUDE_FLAGS[@]}"
  ```

- **post-execution**: ALWAYS auto-dispatch the **cpv-plugin-diagnoser-agent agent** on `$TARGET`. If the diagnose returns 0 CRITICAL/MAJOR, print `✓ Pack passes diagnose-plugin clean.` and queue the §3.99 spec via `print_menu.py`. Otherwise let the diagnoser queue its follow-up menu spec so the user can pick a fix path.

- **JSON / remote-API mode**: append `--json` to make `cpv_pack_components.py` emit a single JSON object on stdout instead of human prose — used when the menu is driven by an external orchestrator.

- **shape-detection escape hatch**: when Phase 0 detection has refused (per `skills/cpv-plugin-validation-skill/references/shape-detection.md`), this menu entry is the recommended remedy for option 1 ("Wrap into a NEW plugin") of the hard-refusal protocol.

#### 3.6.9 Add dependencies (existing plugin)

Adds plugin dependencies to a target plugin's `plugin.json::dependencies` array per [plugin-dependencies.md](https://code.claude.com/docs/en/plugin-dependencies.md). Two input modes that can be combined; the engine deduplicates by name with last-write-wins, sorts the result, and writes atomically.

- **arg-prompts** (in order):
  1. `Target plugin path?` (the plugin whose dependencies you're editing)
  2. `Add specs (comma-separated, blank to skip)?` — each spec is one of:
     - `name` (bare-string entry — auto-tracks latest, WARN on validate)
     - `name@marketplace` (explicit marketplace, no version pin)
     - `name@marketplace@version` (full pin — recommended)
     - `name@@version` (version-pinned, no marketplace override)
  3. `Copy-from sources (comma-separated paths/URLs, blank to skip)?` — each source is either a local plugin folder or a git URL (`https://`, `git+https://`, `ssh://`, `git@host:owner/repo`). Cloned shallowly to a tmp dir; deps merged in.

- **execution**:
  ```bash
  uv run python "${CLAUDE_PLUGIN_ROOT}/scripts/add_dependencies.py" "$TARGET" \
      --add NAME[@MARKETPLACE[@VERSION]]   # repeat for each
      --from PATH-OR-URL                   # repeat for each
  ```

- **dry-run**: append `--dry-run` to print the merged array on stdout WITHOUT writing. Always offer this BEFORE the real write so the user reviews. After confirmation, re-run without `--dry-run`.

- **rollback safety**: the engine writes a `.bak` next to plugin.json BEFORE the atomic rename. If the post-write `validate_plugin --strict` produces ANY new CRITICAL/MAJOR finding, the `.bak` is restored and the user sees `ROLLBACK: merged dependencies introduced N new blocking findings`.

- **typical recipes**:
  ```bash
  # Pin dev-browser to ~1.2.0 (recommended — auto-tracks 1.2.x patches)
  add_dependencies.py /path/to/my-plugin --add dev-browser@@~1.2.0

  # Cross-marketplace pin
  add_dependencies.py /path/to/my-plugin --add audit-logger@acme-shared@^2.0

  # "Add the same dependencies that other-plugin requires"
  add_dependencies.py /path/to/my-plugin --from /path/to/other-plugin

  # Copy from a remote plugin's git URL
  add_dependencies.py /path/to/my-plugin \
      --from https://github.com/Emasoft/dev-browser-plugin
  ```

- **post-execution**: auto-run `validate_plugin --strict` on the target. If unversioned bare-string deps emit `WARNING [RC-DEP-VERSION-001]`, surface them and offer to convert to pinned via a follow-up `--add` invocation.

#### 3.6.10 Implement cpv-the-skills-menu method (existing plugin)

Decouple skills from agents in any Claude Code plugin. After this leaf
runs, every agent in the target plugin declares only `skills: [cpv-the-skills-menu]`
and picks operational skills dynamically via the `Skill()` tool at runtime.
Works on ANY plugin (CPV, other people's plugins, your own).

- **path-source**: per §3.0a, but extended — accepts a local plugin
  path, a Git URL (`https://github.com/<owner>/<repo>.git` or
  `<owner>/<repo>` slug), a "plugin-in-marketplace" expression (`from
  the X in github.com/<owner>/<marketplace>`), or a bare plugin name
  to search.
- **arg-prompts** (in order):
  1. `Target plugin (path, Git URL, owner/repo, or plugin name)?`
  2. `Full cleanup? Rewrite agent-coupled skill bodies to be agent-agnostic? (y/N — risky for skills with deliberate caller contracts)`
- **execution**:

  ```text
  Skill({skill: "claude-plugins-validation:cpv-the-skills-menu-create", args: "<target> [--full-cleanup]"})
  ```

- **post-execution**: forward the migration report verbatim. Then
  offer §3.99 ("do something else / done").
- **see also**: `commands/cpv-the-skills-menu-create.md` exposes the same
  flow as the `/cpv-the-skills-menu-create` slash command for users who
  prefer typing the command directly. The bundled
  `cpv-the-skills-menu-create` skill is the migration source of truth.

---

### 3.8 Manage sub-menu

Fixed key→action map (slug `manage`). Note: row numbers preserve the
historical IDs that the per-leaf recipe sections (§3.8.1 through
§3.8.13) reference — the gap between row 8 and row 10 reflects that
the legacy §3.8.9 leaf was removed; the IDs were NOT re-sequenced
(per the fixed-key contract — letters/numbers never reassign):

| Key | Action ID         | Label shown to user                                                              |
|-----|-------------------|-----------------------------------------------------------------------------------|
| 1   | mgr_list          | List installed plugins — Show every plugin Claude Code knows about               |
| 2   | mgr_install       | Install / update / enable / off — Hand off to the cpv-plugin-manager-agent agent           |
| 3   | mgr_doctor        | Health check — Look for problems in registry, settings, and cache                 |
| 4   | mgr_scanners      | Install external scanners — Install all the security scanners CPV uses           |
| 5   | mgr_prune         | Prune old cached plugin versions — Free disk space                                |
| 6   | mgr_publish       | Bump version + publish — Bump patch / minor / major and run the publish pipeline  |
| 7   | mgr_version       | Show CPV version — Read the version from .claude-plugin/plugin.json              |
| 8   | mgr_readme        | Refresh plugin README — Re-build the auto-generated README sections               |
| 10  | mgr_standardize   | Standardize plugin — Re-write the plugin's publish.py + CI + retry helpers       |
| 11  | mgr_add_component | Add component — Add a new skill / agent / command / hook / mcp to a plugin       |
| 12  | mgr_strip_dev     | Move tests to a sub-repo — Move tests/ into a separate git submodule (PSS)        |
| 13  | mgr_migrate_mkt   | Migrate marketplace.json — Normalize old source.url entries, detect dead 404s    |
| A   | ask               | Ask the agent                                                                     |
| B   | back              | Back — Go back to the top-level menu                                              |
| 0   | cancel            | Cancel / Exit                                                                     |

Queue the spec via `print_menu.py fixed 18` and end the turn. NEVER
print the menu inline; CMS Stop hook emits via `systemMessage`:

```bash
export CPV_SKILL_MENUS_DIR="${CLAUDE_PLUGIN_ROOT}/skills/cpv-main-menu-skill/skill-menus"
python "${CLAUDE_PLUGIN_ROOT}/scripts/print_menu.py" fixed 18 >/dev/null 2>&1
```

END THE TURN.

#### 3.8.1 List installed plugins

- **execution**:
  ```bash
  uv run --with pyyaml python "$LAUNCHER" registry --list
  ```

#### 3.8.2 Install / update / enable / disable

- **execution**: dispatch the **cpv-plugin-manager-agent agent**. The agent queues its own First Contact menu spec via `print_menu.py` (Stop hook emits it) asking what operation to do.

#### 3.8.3 Doctor (health check)

- **arg-prompts** (in order):
  1. `Quick sub-second triage instead of full sweep? (y/N — passes --quick)`
  2. `Verbose output? (yes/no)`
  3. `Auto-fix orphaned entries? (yes/no — passes --fix)`
- **execution**:
  ```bash
  uv run --with pyyaml python "$LAUNCHER" doctor [--quick] [--verbose] [--fix]
  ```
- **note**: `--quick` is `manage_doctor.py`'s sub-second triage mode (skip the
  full sweep). It is mutually informative with the §3.7 migration table's
  row-3/row-20 mention that the default Doctor run is the full per-plugin sweep
  and `--quick` skips it.

#### 3.8.4 Install all external scanners

- **arg-prompt**: `This will install cc-audit, tirith, trufflehog, semgrep, Cisco AI Defense skill-scanner, fclones, AND the optional google-re2 accelerator via brew/snap/pipx/cargo (silent, idempotent, per-platform). (SkillAudit is NOT installed here — it is a native in-process Python check that always runs, no external tool needed.) Proceed? (yes/no)`
- **execution**: ALWAYS confirm first, then:
  ```bash
  uv run python "${CLAUDE_PLUGIN_ROOT}/scripts/manage_doctor.py" --install-scanners
  ```
- **note**: This is the ONLY direct invocation of `manage_doctor.py` for this leaf — `--install-scanners` is a one-shot bootstrap that doesn't need the launcher's environment isolation.

#### 3.8.5 Prune old plugin cache versions (v2.48)

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

#### 3.8.6 Bump version + publish (current plugin)

- **arg-prompt**: `Bump type? (patch / minor / major)`
- **execution** (TRDD-bbff5bc5: publish.py is the canonical entry point):
  ```bash
  uv run python "${CLAUDE_PLUGIN_ROOT}/scripts/publish.py" --$BUMP_TYPE
  ```
- **note**: This runs the FULL pipeline — bump + manifest refresh +
  CHANGELOG + commit + push + GitHub release. For a local-only bump
  without push, the user should call `bump_version.py` directly (it's
  now a thin wrapper around `publish.bump_semver`).

#### 3.8.7 Show CPV version

- **execution**:
  ```bash
  cat "${CLAUDE_PLUGIN_ROOT}/.claude-plugin/plugin.json" | python3 -c 'import json,sys; print(json.load(sys.stdin)["version"])'
  ```

#### 3.8.8 Refresh README AUTO-COMPONENTS (Phase 5, v2.57.0+)

- **path-source**: per §3.0a (its row 1 = "current project folder $PWD")
- **execution**:
  ```bash
  uv run python "${CLAUDE_PLUGIN_ROOT}/scripts/refresh_readme.py" "$TARGET"
  ```
- **note**: Adds `<!-- BEGIN AUTO-COMPONENTS -->` block on first run;
  subsequent runs preserve placement and only update the body.

#### 3.8.10 Standardize plugin (force-templates) (Phase 2, v2.55.0+)

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

#### 3.8.11 Add component (Phase 10, v2.61.0+)

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

#### 3.8.12 Strip dev parts (submodule) (Phase 2, v2.52.0+)

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

#### 3.8.13 Migrate marketplace (source.url → source.repo) (Phase 2.6, v2.59.0+)

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

### 3.4 Diagnose sub-menu

The big-picture entry for any existing plugin. Goes beyond §3.1.1
(structure-only) by ALSO running all 5 external scanners + pipeline-staleness
checks + cross-platform compliance + marketplace registration probe +
cached-vs-GitHub sync probe + branch-rules + Claude action audit. Includes
the AI-graded semantic quality review (Opus, opt-in only — EXPENSIVE).

For SCOPE-AWARE diagnostics across a LIST of project folders (user /
project / local / full scope) — TRDD-a175f78d, v2.101.0 — use §3.1.7 row
8 (Scope-aware doctor sub-menu). That entry rejects URL inputs by design
because the doctor needs `~/.claude/` filesystem access. The single-plugin
diagnose flow below uses local-cache state implicitly so URLs work there.

Fixed key→action map (slug `diagnose`):

| Key | Action ID    | Label shown to user                                                                          |
|-----|--------------|-----------------------------------------------------------------------------------------------|
| 1   | diag_plugin  | Diagnose plugin (deep audit) — Full audit + structured report + follow-up menu               |
| 2   | diag_critical| Apply CRITICAL fixes only — Fix only publish-blockers + security blockers                    |
| 3   | diag_major   | Apply MAJOR + CRITICAL fixes — Fix everything that blocks publishing or is non-cross-platform|
| 4   | diag_sync    | Sync cached install with GitHub — Compare cache version to latest tag                        |
| 5   | diag_register| Check + fix marketplace registration — Verify listed; offer to register / create / re-register|
| 6   | diag_branch  | Audit branch rules + Claude action setup — ruleset / bypass actors / action pin / secrets    |
| 7   | diag_xplat   | Cross-platform audit — Quick scan for `.sh` / os.path / shell=True / bash hook constructs   |
| 8   | diag_semantic| AI-graded semantic review (opus, EXPENSIVE) — Opus A-F quality review (10-50× normal cost)   |
| A   | ask          | Ask the agent                                                                                 |
| B   | back         | Back — Go back to the top-level menu                                                          |
| 0   | cancel       | Cancel / Exit                                                                                 |

Queue the spec via `print_menu.py fixed 19` and end the turn. NEVER
print the menu inline; CMS Stop hook emits via `systemMessage`:

```bash
export CPV_SKILL_MENUS_DIR="${CLAUDE_PLUGIN_ROOT}/skills/cpv-main-menu-skill/skill-menus"
python "${CLAUDE_PLUGIN_ROOT}/scripts/print_menu.py" fixed 19 >/dev/null 2>&1
```

END THE TURN.

#### 3.4.1 Diagnose plugin

- **path-source**: per §3.0a (its row 1 = "current project folder $PWD")
- **execution**: dispatch the **cpv-plugin-diagnoser-agent agent** with the path. The agent runs phases 1–7 (validate, security with all scanners, pipeline staleness, cross-platform, marketplace registration, branch+actions, sync), writes the structured report, then queues its own follow-up menu spec via `print_menu.py` (keys `1`-`7` + `0`).
- **Phase 0 escape hatch**: when the diagnoser's Phase 0 plugin-shape detection refuses (per `skills/cpv-plugin-validation-skill/references/shape-detection.md`), the diagnoser MUST redirect to §3.6.8 (Pack components into a new plugin) so the user can multi-select components and convert them into a real installable plugin. NEVER auto-scaffold around the wrong shape.

#### 3.4.2 Apply CRITICAL fixes only

- **path-source**: per §3.0a
- **execution**: dispatch the **cpv-plugin-fixer-agent agent** with `min_severity=CRITICAL`.

#### 3.4.3 Apply MAJOR + CRITICAL fixes

- **path-source**: per §3.0a
- **execution**: dispatch the **cpv-plugin-fixer-agent agent** with `min_severity=MAJOR`.

#### 3.4.4 Sync cached install with GitHub

- **arg-prompts** (in order):
  1. `Plugin name (or path under ~/.claude/plugins/cache/)?`
  2. (after detection prints `cached: vX.Y.Z, latest: vA.B.C, N versions behind`) — `Run \`claude plugin update <name>@<marketplace>\` now? (yes/no)`
- **execution**:
  ```bash
  uv run --with pyyaml python "${CLAUDE_PLUGIN_ROOT}/scripts/manage_doctor.py" --check-cache-sync "<plugin-name>"
  # if user says yes:
  claude plugin update <name>@<marketplace>
  ```

#### 3.4.5 Check + fix marketplace registration

- **path-source**: per §3.0a
- **execution**: dispatch the **cpv-plugin-diagnoser-agent agent** in marketplace-only mode → if not registered, dispatch the **cpv-plugin-creator-agent agent** in orphan-plugin marketplace-onboarding mode (4-path menu: A=existing marketplace, B=new local marketplace, C=new GitHub marketplace, D=existing GitHub marketplace).

#### 3.4.6 Audit branch rules + Claude action setup

- **arg-prompt**: `Owner/repo slug to audit (or "auto" to detect from origin)?`
- **execution**: dispatch the **cpv-plugin-diagnoser-agent agent** in branch-rules-only mode (Phase 6.5). Findings include: ruleset state, bypass actors, Claude action version pin, missing secrets. After the audit prints, offer:
  - 1: re-apply cpv-branch-rules ruleset → invoke the `cpv-setup-plugin-repo` skill (`setup-branch-rules-generic` recipe) with `<owner>/<repo>`
  - 2: pin Claude action to latest SHA via pinact
  - 3: surface secret-setup instructions for `ANTHROPIC_API_KEY` / `CLAUDE_CODE_OAUTH_TOKEN`
  - 0: end

#### 3.4.7 Cross-platform audit

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
- **post**: queue the §3.99 "do something else?" spec via `print_menu.py` and end the turn.

#### 3.4.8 AI-graded semantic review (opus, EXPENSIVE)

- **arg-prompts** (in order):
  1. `Semantic validation uses Opus with 1M context at max effort. Cost: ~10-50× normal. Proceed? (yes/no)`
  2. (only if yes) `Path to skill or agent or whole plugin?`
- **execution**: dispatch the **cpv-semantic-validator-agent agent** with the path. The agent itself runs the syntactic baseline first then the semantic pass.

---

### 3.5 Update sub-menu

Standalone top-level for the "upgrade this plugin to the current canonical
pipeline standard" workflow — applies ALL pipeline-migration steps (§1–§5
from `skills/cpv-fix-validation/references/pipeline-migration.md`): bash → Python,
os.path → pathlib, idempotent publish.py, sanitized inputs, no `.sh` scripts,
canonical CI workflows, etc.

Fixed key→action map (slug `update`):

| Key | Action ID    | Label shown to user                                                                          |
|-----|--------------|-----------------------------------------------------------------------------------------------|
| 1   | upd_upgrade  | Upgrade plugin to current pipeline standard — Apply ALL pipeline-migration steps (§1–§5)     |
| A   | ask          | Ask the agent                                                                                 |
| B   | back         | Back — Go back to the top-level menu                                                          |
| 0   | cancel       | Cancel / Exit                                                                                 |

Queue the spec via `print_menu.py fixed 20` and end the turn. NEVER
print the menu inline; CMS Stop hook emits via `systemMessage`:

```bash
export CPV_SKILL_MENUS_DIR="${CLAUDE_PLUGIN_ROOT}/skills/cpv-main-menu-skill/skill-menus"
python "${CLAUDE_PLUGIN_ROOT}/scripts/print_menu.py" fixed 20 >/dev/null 2>&1
```

END THE TURN.

#### 3.5.1 Upgrade to current pipeline standard

- **path-source**: per §3.0a
- **execution**: dispatch the **cpv-plugin-fixer-agent agent** with the path AND the prompt: `Apply pipeline-migration §1–§5 from skills/cpv-fix-validation/references/pipeline-migration.md. min_severity=WARNING (fix everything).`
- **v2.98.0 auto-batch**: when the dispatched cpv-plugin-fixer-agent returns a line starting with `[BATCH_REQUIRED]` (the migration uncovered more findings than fit in single-agent context), the menu orchestrator parses the `plugin-root=<P>` token and AUTO-DISPATCHES the batch protocol — same flow as §3.2.1 step 4 (planner → N parallel shard-fixers in one main-session message → aggregator). The user does NOT see a manual `/cpv-batch-fix` prompt; the upgrade flow handles it end-to-end. The ceiling is the per-model safe-ceiling (15-25 for bare opus/sonnet, 50-75 for [1m] variants).
- **Phase 0 escape hatch**: same rule as §3.4.1 — if shape detection refuses, redirect to §3.6.8 instead of upgrading the wrong shape.

---

### 3.7 Publish & Migrate sub-menu

Covers branch protection, marketplace linking, publishing, and
marketplace-layout migrations (Layout A ↔ B ↔ C). Replaces the former
"GitHub setup" menu, with the cpv-migrate-marketplace-architecture leaf
folded in for one-stop access to the publish workflow.

> **Migration note (v2.90.0 — TRDD-c50531c2):** the previous §3.7
> "Doctor (deep diagnostic) — 22-option menu" was deleted. It was a
> second main menu with options that mostly duplicated §3.1 and §3.8
> (Manage). The 22 Doctor options are now reached from:
>
> | Doctor row                                          | New home in main menu                                  |
> |-----------------------------------------------------|--------------------------------------------------------|
> | 1, 2 (specific plugin / current folder)             | §3.1.1 Plugin (full) — auto-detects $PWD               |
> | 3 (all installed plugins, deep scan)                | §3.8.3 Doctor (health check) — default full per-plugin sweep (no flag; `--quick` skips it) |
> | 4, 5 (GitHub plugin / marketplace)                  | §3.1.6.1, §3.1.6.2                                     |
> | 6 (local marketplace)                               | §3.1.3.1                                               |
> | 7, 8 (local / project scope)                        | §3.1.4.2, §3.1.4.1                                     |
> | 9 (user scope)                                      | §3.1.4.2 (Local-scope) with target `~/.claude/`        |
> | 10–13, 15, 16 (skill/agent/hook/MCP/output-style/LSP)| §3.1.2.1, §3.1.2.2, §3.1.2.4, §3.1.2.5, §3.1.2.7.1, §3.1.2.6 |
> | 14 (specific monitor)                               | no dedicated monitor validator — monitor configs are checked as part of `project-scope` validation (§3.1.4) |
> | 17 (cache cleanup)                                  | §3.8.5 Prune old plugin cache versions                 |
> | 18 (install scanners)                               | §3.8.4 Install all external scanners                   |
> | 19 (auto-fix orphans)                               | §3.8.3 Doctor (health check) → answer "yes" to "Auto-fix orphaned entries?" (passes `--fix`) |
> | 20 (quick health check)                             | §3.8.3 Doctor (health check)                           |
> | 21 (dependency tree)                                | not migrated — closest is §3.6.9 Add dependencies (no standalone dependency-tree view in the new menu) |
> | 22 (add dependency)                                 | §3.6.9 Add dependencies                                |

Fixed key→action map (slug `publish`):

| Key | Action ID    | Label shown to user                                                                          |
|-----|--------------|-----------------------------------------------------------------------------------------------|
| 1   | pub_branch_here| Protect this repo's branches — Apply branch-protection rules to the repo of `origin`        |
| 2   | pub_branch_any | Protect another repo's branches — Apply branch-protection rules to any owner/repo           |
| 3   | pub_link     | Link a plugin to a marketplace — Register a plugin in a marketplace's plugin list             |
| 4   | pub_publish  | Publish plugin to its marketplace — Run the full publish pipeline (bump + push + release)    |
| 5   | pub_migrate  | Migrate marketplace layout (A ↔ B ↔ C) — Convert marketplace.json layout                     |
| A   | ask          | Ask the agent                                                                                 |
| B   | back         | Back — Go back to the top-level menu                                                          |
| 0   | cancel       | Cancel / Exit                                                                                 |

Queue the spec via `print_menu.py fixed 21` and end the turn. NEVER
print the menu inline; CMS Stop hook emits via `systemMessage`:

```bash
export CPV_SKILL_MENUS_DIR="${CLAUDE_PLUGIN_ROOT}/skills/cpv-main-menu-skill/skill-menus"
python "${CLAUDE_PLUGIN_ROOT}/scripts/print_menu.py" fixed 21 >/dev/null 2>&1
```

END THE TURN.

#### 3.7.1 Branch protection (current repo)

- **execution**: invokes the `cpv-setup-plugin-repo` skill (`setup-branch-rules` recipe) inline (no extra prompts — uses the current `git remote get-url origin`).

#### 3.7.2 Branch protection (generic owner/repo)

- **arg-prompt**: `Owner/repo slug?`
- **execution**: invokes the `cpv-setup-plugin-repo` skill (`setup-branch-rules-generic` recipe) inline with the slug.

#### 3.7.3 Link plugin to a marketplace

- **arg-prompts** (in order):
  1. `Plugin repo (owner/repo)?`
  2. `Marketplace repo (owner/repo)?`
- **execution**: invokes the `cpv-link-plugin-marketplace` skill inline with the answers.

#### 3.7.4 Publish plugin to its marketplace

- **arg-prompt**: `Bump type? (patch / minor / major)`
- **execution**: invokes the `cpv-publish-to-marketplace` skill inline. Runs the
  full publish pipeline — bump, manifest refresh, CHANGELOG, commit, push,
  GitHub release, marketplace notify.
  ```bash
  uv run python "${CLAUDE_PLUGIN_ROOT}/scripts/publish.py" --$BUMP_TYPE
  ```
- **note**: Same canonical entry point as §3.8.6. Listed here for proximity
  to the linking + branch-rules workflow most plugin authors run together
  before their first publish.

#### 3.7.5 Migrate marketplace layout (A ↔ B ↔ C)

- **path-source**: per §3.0a (Layout-C detection recommended — see §3.0a Case 1)
- **arg-prompts** (in order):
  1. `Source layout? (A=hub-and-spoke / B=nested monorepo / C=marketplace-in-plugin)`
  2. `Target layout? (A / B / C)`
  3. `Run in --check mode first? (yes/no — recommended yes)`
- **execution**: invokes the `cpv-migrate-marketplace-architecture` skill with
  the source + target layout codes. The skill handles all three layout
  conversion paths idempotently.
- **note**: For source.url → source.repo normalization on a single
  marketplace.json file (not a full layout migration), use §3.8.13 instead.

---

### 3.10H Help / About sub-menu (letter `H` shortcut on top-level)

v2.90.0 (TRDD-c50531c2 — menu unification): Help/About is reached via
the letter `H` shortcut on the top-level menu (alongside `A` for "Ask
the agent" and `0` for Cancel). The 8 functional categories take rows
1-8; H/A/0 are letters so they never collide with multi-digit option
numbers anywhere else in the tree.

Fixed key→action map (slug `help`):

| Key | Action ID    | Label shown to user                                                      |
|-----|--------------|---------------------------------------------------------------------------|
| 1   | help_top     | Show the top-level menu — Re-queue the main menu (the 8 categories)       |
| 2   | help_list    | List every CPV command — Print the name + description of every /cpv-* cmd |
| 3   | help_version | Show CPV version — Read the version from .claude-plugin/plugin.json       |
| A   | ask          | Ask the agent                                                             |
| B   | back         | Back — Go back to the top-level menu                                      |
| 0   | cancel       | Cancel / Exit                                                             |

Queue the spec via `print_menu.py fixed 22` and end the turn. NEVER
print the menu inline; CMS Stop hook emits via `systemMessage`:

```bash
export CPV_SKILL_MENUS_DIR="${CLAUDE_PLUGIN_ROOT}/skills/cpv-main-menu-skill/skill-menus"
python "${CLAUDE_PLUGIN_ROOT}/scripts/print_menu.py" fixed 22 >/dev/null 2>&1
```

END THE TURN.

#### 3.10H.1 Category overview

- **execution**: re-print the 3.0 top-level menu table.

#### 3.10H.2 List every CPV command

- **execution**:
  ```bash
  for f in "${CLAUDE_PLUGIN_ROOT}"/commands/cpv-*.md; do
    name=$(basename "$f" .md)
    desc=$(awk '/^description:/{sub(/^description:[[:space:]]*/, ""); print; exit}' "$f")
    printf "%-42s %s\n" "/$name" "$desc"
  done
  ```

#### 3.10H.3 Show CPV plugin version

- **execution**: same as §3.8.7.

---

### 3.99 End-of-leaf "do something else?" table (NON-validate flows)

After a Create / Manage / Publish-&-Migrate / Help leaf finishes, queue
this 2-row menu spec via `print_menu.py` and end the turn. NEVER print
the menu inline; CMS Stop hook emits via `systemMessage`.

Fixed key→action map (slug `done`):

| Key | Action ID  | Label shown to user                                            |
|-----|------------|-----------------------------------------------------------------|
| 1   | go_main    | Do something else — Go back to the top-level menu               |
| A   | ask        | Ask the agent                                                   |
| 0   | done       | Done (exit) — Reply `Done.` and stop                            |

```bash
export CPV_SKILL_MENUS_DIR="${CLAUDE_PLUGIN_ROOT}/skills/cpv-main-menu-skill/skill-menus"
python "${CLAUDE_PLUGIN_ROOT}/scripts/print_menu.py" fixed 23 >/dev/null 2>&1
```

END THE TURN.

---

### 3.16 Security sub-menu (drilled into from §3.1.5.1)

Fixed key→action map (slug `security`):

| Key | Action ID    | Label shown to user                                                                          |
|-----|--------------|-----------------------------------------------------------------------------------------------|
| 1   | sec_full     | Single plugin (full security pass) — All rule packs + 5 external scanners                    |
| 2   | sec_github   | Single plugin from GitHub URL — Auto-clone github.com URL → scan → cleanup                   |
| 3   | sec_giturl   | Single plugin from arbitrary git URL — gitlab / SSH / self-hosted → scan → cleanup           |
| 4   | sec_archive  | Single plugin from local archive (.zip/.tar.gz) — Extract → scan → cleanup                   |
| 5   | sec_mkt      | Marketplace (every plugin, tree-scan-once) — fclones-dedup + scanners once + bucket          |
| 6   | sec_loose    | Loose / flat skill pack (--loose) — Skip the .claude-plugin/ precondition                    |
| 7   | sec_ccaudit  | Single scanner only (cc-audit)                                                               |
| 8   | sec_tirith   | Single scanner only (tirith)                                                                 |
| 9   | sec_trufflehog| Single scanner only (trufflehog) — secret scanner (--concurrency on, gitleaks dropped)      |
| 10  | sec_semgrep  | Single scanner only (semgrep) — with p/security-audit + p/secrets rule packs                 |
| 11  | sec_cisco    | Single scanner only (Cisco AI Defense) — programmatic engines, no API key needed             |
| 12  | sec_telemetry| Telemetry hazards only — PLUGIN_SEED_DIR / SHELL_PREFIX / OTEL_LOG_RAW_API_BODIES            |
| A   | ask          | Ask the agent                                                                                 |
| B   | back         | Back — Go back to the Validate sub-menu                                                       |
| 0   | cancel       | Cancel / Exit                                                                                 |

Queue the spec via `print_menu.py fixed 24` and end the turn. NEVER
print the menu inline; CMS Stop hook emits via `systemMessage`:

```bash
export CPV_SKILL_MENUS_DIR="${CLAUDE_PLUGIN_ROOT}/skills/cpv-main-menu-skill/skill-menus"
python "${CLAUDE_PLUGIN_ROOT}/scripts/print_menu.py" fixed 24 >/dev/null 2>&1
```

END THE TURN.

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
- **execution**: clone-then-scan with retry-loop (see §3.1.3.3).

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

#### 3.16.7..3.16.11 Per-scanner focus (rows cc-audit / tirith / trufflehog / semgrep / Cisco)

NOTE: there is no per-scanner isolation flag — these rows run the SAME full
multi-scanner pass; the choice only labels the report filename. Tell the user
this when they pick one.

- **arg-prompts** (in order): `Path to the plugin?`
- **execution** (identical for all five rows — substitute `<scanner>` only in
  the report filename):
  ```bash
  uv run --with pyyaml python "$LAUNCHER" security "$TARGET_PATH" \
    --report "$MAIN_ROOT/reports/validate_security/$TS-<scanner>-$SLUG.md"
  ```
- **note**: there is **no single-scanner isolation flag** — by design the
  external scanners are not opt-out and `validate_security` ALWAYS runs the
  full pass (all rule packs + every available external scanner). These five
  rows therefore run the SAME full scan; they exist only to label the report
  by the scanner the user cares about. To read one scanner's results, open
  the report and look at that scanner's rows (each finding is prefixed with
  its scanner name, e.g. `cc-audit:`, `trufflehog:`, `semgrep:`). Do NOT pass
  a `--only-scanner`/`--cc-audit`/`--no-semgrep`-style flag: none exist, and
  `validate_security` uses `parse_args()` so an unrecognized flag aborts the
  run with a non-zero exit instead of degrading gracefully.

#### 3.16.12 Telemetry hazards only

See §3.1.5.7.3 — same recipe.

---

### 3.3 Optimize for Cache sub-menu

Promoted from drill-in to top-level in v2.90.0: prompt-cache invalidation
audit + cache-aware refactor (CA-01..CA-07) is a distinct workflow from
generic Fix (§3.2), with its own audit + optimize loop.

Since v2.102.0 every CA-01..CA-07 finding is a **WARNING** — the cache audit
is advisory (cost/latency optimization), never publish-blocking. CA-04 covers
a `model:` frontmatter pin on ANY component (agents, commands AND skills);
`model: inherit` is exempt. To fix the findings, pick row 2 (auto-fix) or use
the §3.10 post-validate fix menu with "Fix ALL (incl. WARNING)".

Also reachable from §3.1.5.2 (Specific quality check → Cache patterns)
for users who arrive via the Validate menu.

For FLEET / MARKETPLACE-scale caching audits + optimizations
(TRDD-3dcbb37c, v2.101.0), use §3.1.7 rows 3 (`/cpv-batch-caching-audit`)
and 4 (`/cpv-batch-caching-optimize`) instead — they fan out N
parallel cpv-cache-optimizer-agent dispatches in one main-session message.

Fixed key→action map (slug `cache`):

| Key | Action ID    | Label shown to user                                                                          |
|-----|--------------|-----------------------------------------------------------------------------------------------|
| 1   | cache_audit  | Audit only (CA-01..CA-07) — Pure read-only audit, produces report with per-rule findings     |
| 2   | cache_fix    | Audit + auto-fix (loop) — Dispatch cpv-cache-optimizer-agent to fix CA-01..CA-07 in priority     |
| 3   | cache_broader| Audit + broader cache-aware refactoring — Audit + fix + Phase 4 (CLAUDE.md split, etc.)      |
| 4   | cache_project| Audit project root (not a plugin) — Scans .claude/ + CLAUDE.md (no .claude-plugin/ required) |
| A   | ask          | Ask the agent                                                                                 |
| B   | back         | Back — Go back to the top-level menu                                                          |
| 0   | cancel       | Cancel / Exit                                                                                 |

Queue the spec via `print_menu.py fixed 25` and end the turn. NEVER
print the menu inline; CMS Stop hook emits via `systemMessage`:

```bash
export CPV_SKILL_MENUS_DIR="${CLAUDE_PLUGIN_ROOT}/skills/cpv-main-menu-skill/skill-menus"
python "${CLAUDE_PLUGIN_ROOT}/scripts/print_menu.py" fixed 25 >/dev/null 2>&1
```

END THE TURN.

#### 3.3.1 Audit only

- **arg-prompt**: `Path to plugin or project root?`
- **execution**:
  ```bash
  uv run --with pyyaml python "$LAUNCHER" cache "$TARGET_PATH" \
    --report "$MAIN_ROOT/reports/validate_cache/$TS-$SLUG.md"
  ```

#### 3.3.2 Audit + auto-fix

- **arg-prompt**: `Path to plugin or project root?`
- **execution**: dispatch the **cpv-cache-optimizer-agent** with the path. The
  agent runs Phase 1 (audit) → Phase 2 (fix) → Phase 3 (re-validate)
  internally.

#### 3.3.3 Audit + broader refactoring

- **arg-prompt**: `Path to plugin or project root?`
- **execution**: dispatch the **cpv-cache-optimizer-agent** with the path AND
  the explicit `broader` keyword in the prompt. The agent runs Phase 1-3
  and THEN Phase 4 (CLAUDE.md split, dynamic-content migration, etc.).

#### 3.3.4 Project root (not a plugin)

- Same recipe as 3.3.1 — the validator auto-handles project vs plugin
  trees and skips the `.claude-plugin/` precondition when not present.

> Note: `validate_cache` has no `--strict` mode — every CA finding is a
> WARNING and WARNING never blocks (even under `--strict` elsewhere), so a
> strict cache gate would be a no-op. The audit is advisory by design.

---

### 3.10 Post-validate fix menu (MANDATORY after every Validate leaf, including §3.1.6 From-GitHub)

This table replaces the generic §3.99 for ALL validate flows. It MUST be printed
unconditionally after a validate leaf finishes — even when the validation
verdict is PASS / VALID — so the user always has the explicit option to
end OR to fix any residual WARNINGs they care about.

If the validation finished completely clean (CRITICAL=0 MAJOR=0 MINOR=0
NIT=0 WARNING=0), still queue the spec. Keys `1`-`5` will simply find
nothing to fix when dispatched, and the fixer will exit clean — but the
user always sees the menu and is never auto-deflected.

Fixed key→action map (slug `post-validate`):

| Key | Action ID    | Label shown to user                                                            | Severities the fixer will touch  |
|-----|--------------|---------------------------------------------------------------------------------|----------------------------------|
| 1   | fix_all      | Fix ALL issues (incl. WARNING) — Dispatch fixer on every finding in the report  | CRITICAL+MAJOR+MINOR+NIT+WARNING |
| 2   | fix_nit      | Fix NIT and higher — Skip WARNING-only findings                                 | CRITICAL+MAJOR+MINOR+NIT         |
| 3   | fix_minor    | Fix MINOR and higher — Skip NIT and WARNING                                     | CRITICAL+MAJOR+MINOR             |
| 4   | fix_major    | Fix MAJOR and higher — Only fix the publish-blockers (and CRITICALs)            | CRITICAL+MAJOR                   |
| 5   | fix_critical | Fix CRITICAL only — Strictest mode (loaders/security blockers and nothing else) | CRITICAL                         |
| A   | ask          | Ask the agent                                                                   | —                                |
| 0   | end          | End — Done; exit without running the fixer                                      | —                                |

Queue the spec via `print_menu.py fixed 26` and end the turn. NEVER
print the menu inline; CMS Stop hook emits via `systemMessage`:

```bash
export CPV_SKILL_MENUS_DIR="${CLAUDE_PLUGIN_ROOT}/skills/cpv-main-menu-skill/skill-menus"
python "${CLAUDE_PLUGIN_ROOT}/scripts/print_menu.py" fixed 26 >/dev/null 2>&1
```

END THE TURN.

#### 3.10.1 Dispatching the fixer with a minimum severity

When the user picks rows 1-5, dispatch the **cpv-plugin-fixer-agent agent** (or, for
marketplace reports, the **cpv-marketplace-fixer-agent agent**; for cache reports,
the **cpv-cache-optimizer-agent**) with the report path and a `min_severity`
parameter. The agent honours the filter by skipping fixes for any finding
whose severity is BELOW the threshold.

| Row | `min_severity` value to pass | Agent prompt template |
|-----|-------------------------------|----------------------|
| 1 | `WARNING` | `Fix every finding in <REPORT_PATH>. min_severity=WARNING (fix everything including WARNINGs).` |
| 2 | `NIT` | `Fix findings in <REPORT_PATH>. min_severity=NIT (skip WARNING-only).` |
| 3 | `MINOR` | `Fix findings in <REPORT_PATH>. min_severity=MINOR (skip NIT and WARNING).` |
| 4 | `MAJOR` | `Fix findings in <REPORT_PATH>. min_severity=MAJOR (publish-blockers only).` |
| 5 | `CRITICAL` | `Fix findings in <REPORT_PATH>. min_severity=CRITICAL (strictest — only loader/security blockers).` |

After the fixer agent returns, queue the §3.99 "do something else?"
spec via `print_menu.py` (keys: `1` Do something else, `A` Ask, `0`
Done) and end the turn.

If the user picks `0` (End) → reply `Done.` and stop.

---

## Etiquette and error handling

### Cancel / Exit semantics

At ANY menu level, picking `0` (Cancel / Exit) → the orchestrator MUST:

1. Stop all further menu prompts.
2. Reply with exactly ONE line: `Cancelled — no actions taken.`
3. Not run any bash, not write any reports, not modify any files.

### Back semantics

In a sub-menu, picking `B` / `b` (Back) → re-queue the PARENT menu's
spec via `print_menu.py` and end the turn (typically the §3.0 top-level
spec). At the top-level menu there is no `B` row. Some legacy
sub-menus may still use `9` for Back where there is no collision risk
— both `B` and a numeric Back row work, but `B` is preferred for any
menu with more than 9 options.

### Argument-prompt etiquette

- ALWAYS ask required arguments as a single plain-text line — NEVER use AskUserQuestion.
- Example: `Path to the plugin to validate? (e.g. ~/Code/my-plugin/)`
- If the user provides an invalid path → re-ask with a hint, do not abort.
- If the user replies `0` or `cancel` or `exit` at the argument prompt → treat the same as a top-level Cancel.
- For paths, ALWAYS resolve `~` to `$HOME` and expand environment variables before invoking bash.

### Key-parsing rules

- Strip surrounding whitespace from the user's reply.
- Accept letters `B` / `b` (Back), `A` / `a` (Ask the agent or Scan-all
  in detection sub-tables), `T` / `t` (Type a different path),
  `S` / `s` (Scan-all in multi-plugin path-source mini-menu),
  `H` / `h` (Help — top-level only), `M` / `m` (Main, reserved) and
  `X` / `x` (Exit, reserved) — case-insensitive — before falling
  through to integer parsing.
- Take the FIRST integer found in the reply (so `1` and `1.` and `1)` all
  match key `1`; `12` matches key `12`, NOT key `1`).
- If the user types text not starting with a digit/letter but matching
  an option label (case-insensitive substring match on the `label`
  field of the menu's fixed key→action map), accept it and resolve to
  the matching `action_id`.
- Otherwise: ask `Invalid choice. Pick a key from the menu (or B for back, 0 to cancel).` and RE-QUEUE the SAME sub-menu spec via `print_menu.py` (do not jump back to top-level).

### Error handling

- If `${CLAUDE_PLUGIN_ROOT}` is unset → abort with:
  > "CPV plugin not installed in this session. Install via
  > `/plugin install claude-plugins-validation@emasoft-plugins`."
- If a launcher invocation exits non-zero → surface stderr verbatim, then re-queue the SAME sub-menu spec via `print_menu.py` so the user can retry with different arguments.
- If `print_menu.py` exits with `MenuSystemUnavailable` (claude-menu-system not installed) → surface the install hint verbatim and stop. There is NO inline fallback renderer (TRDD-4de479a0, no-legacy rule).

### Token budget

- Never paste a full report into the response. Always return the report-file path and a 3-line summary (verdict + counts + path).
- Do not load `references/menu-tree.md` repeatedly — the orchestrator reads it once at session start.
- Use the launcher invocation table (above) verbatim — do not generate alternative bash spellings.
- NEVER print menu tables inline. Every menu is queued via `print_menu.py`
  and emitted post-turn by the claude-menu-system Stop hook through
  `systemMessage`, which keeps the menu out of the agent transcript
  and prompt cache.
