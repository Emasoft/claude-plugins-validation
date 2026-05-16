---
name: cpv-cache-optimize
description: Audit AND fix prompt-cache invalidation patterns (CA-01..CA-06) — interactive cache-optimizer
allowed-tools: Bash, Read, Agent, Skill
argument-hint: "<plugin_or_project_path_or_report> [--broader]"
user-invocable: true
---

# /cpv-cache-optimize

You are the menu orchestrator. Pick a target (or accept a path argument), dispatch the opus `cache-optimizer-agent` work agent to run audit → fix → re-validate, render the result, loop until the user exits.

Unlike `/cpv-validate-cache` (audit only), this command runs the full **validate → fix → re-validate loop**.

This command body runs in the main session — whatever model the user is on (opus by default). Dynamic menu rendering is offloaded to the `cpv-format-menu` fork-skill, which spawns a fresh haiku subagent for the render itself.

**HARD RULES — do not violate:**

1. **Print every menu in your TEXT OUTPUT, verbatim.** Tool stdout (Bash) and Skill tool results are INVISIBLE to the user — only your prose text reaches the UI. When you invoke `cpv-format-menu` you MUST copy its text result into your next text message.
2. **Never use `AskUserQuestion`.** Ask via plain text only.

If the user invoked `/cpv-cache-optimize <path>` with a path argument (optionally with `--broader`), skip to **Step 4 — Dispatch**:

- Path is a `.md` file under `validate_cache/` or `cache/` → `mode: from_report`
- Path is a folder → `mode: audit_then_fix` (or `audit_then_fix_broader` if `--broader`)

## Step 1 — print this banner in your TEXT output

```text
Tip: run `/model haiku` once for cheaper menu navigation across this session.
```

## Step 2 — auto-discover recent reports + render the menu

Discover the most recent cache-audit reports:

```bash
MAIN_ROOT="$(git worktree list 2>/dev/null | head -n1 | awk '{print $1}')"
[ -z "$MAIN_ROOT" ] && MAIN_ROOT="${CLAUDE_PROJECT_DIR:-$PWD}"
find "$MAIN_ROOT/reports/validate_cache" -maxdepth 1 -type f -name '*.md' 2>/dev/null \
  | sort -r | head -n 7
```

Build the row JSON (up to 7 discovered reports + 2 fixed rows + cancel) and write the spec to disk, then invoke the `cpv-format-menu` fork-skill:

```bash
cat > /tmp/cpv-cache-optimize-report-list-spec.json <<EOF
{
  "header": "Cache audit — what to optimize?",
  "rows": [
    {"key": "1", "action_id": "report_1",       "label": "<relative path> (<age>)", "disabled": <true|false>},
    {"key": "2", "action_id": "report_2",       "label": "<relative path> (<age>)", "disabled": <true|false>},
    {"key": "3", "action_id": "report_3",       "label": "<relative path> (<age>)", "disabled": <true|false>},
    {"key": "4", "action_id": "report_4",       "label": "<relative path> (<age>)", "disabled": <true|false>},
    {"key": "5", "action_id": "report_5",       "label": "<relative path> (<age>)", "disabled": <true|false>},
    {"key": "6", "action_id": "report_6",       "label": "<relative path> (<age>)", "disabled": <true|false>},
    {"key": "7", "action_id": "report_7",       "label": "<relative path> (<age>)", "disabled": <true|false>},
    {"key": "8", "action_id": "audit_then_fix", "label": "Audit + optimize a path (CA-01..CA-06 audit, then fix loop)"},
    {"key": "9", "action_id": "audit_broader",  "label": "Broader mode (path) — beyond CA-01..CA-06 to maximise cache hit rate"},
    {"key": "0", "action_id": "cancel",         "label": "Cancel / Exit"}
  ],
  "footer": "Type a number to choose:"
}
EOF
```

Now invoke the Skill tool to render the menu (forks to haiku, returns rendered text):

```
Skill({
  skill: "claude-plugins-validation:cpv-format-menu",
  args: "menu /tmp/cpv-cache-optimize-report-list-spec.json /tmp/cpv-cache-optimize-report-list-map.json"
})
```

**COPY THE SKILL TOOL'S TEXT RESULT VERBATIM INTO YOUR TEXT RESPONSE.** Skill tool output (like Bash stdout) is INVISIBLE to the user — without echoing the result into your prose the menu never appears in the UI.

## Step 3 — route the user's reply

Look up `action_id` from `/tmp/cpv-cache-optimize-report-list-map.json`:

| action_id | Action |
|---|---|
| `cancel` | Reply EXACTLY: `Cancelled — no actions taken.` and stop. |
| `report_N` | Resolve to the matching report path; dispatch in `mode: from_report`. |
| `audit_then_fix` | Ask plain-text: `Path to plugin or project root?`. Wait. Dispatch in `mode: audit_then_fix`. |
| `audit_broader` | Ask plain-text: `Path to plugin or project root?`. Wait. Dispatch in `mode: audit_then_fix_broader`. |
| any plain-text path | Treat as the work agent's input — dispatch in `mode: audit_then_fix`. |

## Step 4 — dispatch the work agent

```
subagent_type: cache-optimizer-agent
description: "Cache optimizer dispatched from /cpv-cache-optimize"
prompt: |
  <context>
  source: /cpv-cache-optimize main-session menu
  user_choice: <the rendered key the user picked, OR "manual" / "argument">
  mode: <from_report | audit_then_fix | audit_then_fix_broader>
  target_path: <absolute path to a plugin/project folder OR an existing cache-audit report .md>
  </context>

  Run Phases 1-3 (or 1-4 for broader mode) per your authoritative algorithm.
  Return ONE line:

    Cache optimize: <N> issues addressed, <verdict> after re-validate (report: <abs-path>)

  DO NOT render menus yourself.
```

## Step 5 — after the agent returns, render results IN YOUR TEXT OUTPUT

Print this summary block VERBATIM in your text response:

```text
┏━━━━━━━━━━┳━━━━━━━━━━┳━━━━━━━━━┳━━━━━━━━━┳━━━━━━━━━━━┳━━━━━━━━━┓
┃ CRITICAL ┃   MAJOR  ┃  MINOR  ┃   NIT   ┃  WARNING  ┃ VERDICT ┃
┡━━━━━━━━━━╇━━━━━━━━━━╇━━━━━━━━━╇━━━━━━━━━╇━━━━━━━━━━━╇━━━━━━━━━┩
│    <C>   │    <M>   │   <n>   │   <t>   │    <w>    │ <VERD>  │
└──────────┴──────────┴─────────┴─────────┴───────────┴─────────┘
Report: <abs-path>
```

Then render the post-optimize menu — compute `total = C + M + n`, write the spec to disk, and invoke the `cpv-format-menu` fork-skill:

```bash
cat > /tmp/cpv-cache-optimize-postopt-spec.json <<EOF
{
  "header": "What now?",
  "rows": [
    {"key": "1", "action_id": "fix_all_remaining", "label": "Fix ALL remaining findings (<total> total)", "disabled": <true|false>},
    {"key": "2", "action_id": "broader_refactor",  "label": "Run broader mode on the same target (Phase 4 refactor)"},
    {"key": "3", "action_id": "reaudit",           "label": "Re-audit (run validate_cache fresh, no fixes)"},
    {"key": "4", "action_id": "another_target",    "label": "Optimize another target"},
    {"key": "5", "action_id": "open_report",       "label": "Open the final report (returns the path)"},
    {"key": "6", "action_id": "done",              "label": "Skip / done"},
    {"key": "0", "action_id": "exit",              "label": "Exit"}
  ],
  "footer": "Type a number to choose:"
}
EOF
```

Now invoke the Skill tool to render the menu (forks to haiku, returns rendered text):

```
Skill({
  skill: "claude-plugins-validation:cpv-format-menu",
  args: "menu /tmp/cpv-cache-optimize-postopt-spec.json /tmp/cpv-cache-optimize-postopt-map.json"
})
```

**COPY THE SKILL TOOL'S TEXT RESULT VERBATIM INTO YOUR TEXT RESPONSE.** Skill tool output (like Bash stdout) is INVISIBLE to the user — without echoing the result into your prose the menu never appears in the UI.

## Step 6 — route the post-optimize pick

| action_id | Action |
|---|---|
| `fix_all_remaining` | Re-dispatch with `mode: from_report` + the report path. |
| `broader_refactor` | Re-dispatch with `mode: audit_then_fix_broader` and the same target. |
| `reaudit` | Suggest `/cpv-validate-cache <target>`. Reply with the command. Stop. |
| `another_target` | Loop back to Step 2. |
| `open_report` | Reply with the absolute report path. Re-print the post-optimize menu. |
| `done` | Reply `Done.` Stop. |
| `exit` | Reply `Exit.` Stop. |

## Phases (run by the work agent)

| # | Phase | What happens |
|---|---|---|
| 1 | **Audit** | Run `validate_cache.py` against the target. Auto-saves the aggregated report. |
| 2 | **Fix** | Apply per-rule fix recipes from `skills/fix-validation/references/cache-fixes.md` in priority order (CA-01..CA-03 MAJOR → CA-04..CA-05 MINOR → CA-06 WARNING). Each batch is its own `fix(cache-CA-NN): ...` commit. |
| 3 | **Re-validate** | Re-run the validator. Iterate until verdict = VALID or a residual issue cannot be safely fixed. |
| 4 | **Broader refactor** *(optional, only in `audit_then_fix_broader` mode)* | Cached-prefix size audit, dynamic-content migration, model-switch audit, CLAUDE.md decomposition, append `## Cache Notes` block. Each material refactor is approved by the user before the edit lands. |

## Output

Report lands at `$MAIN_ROOT/reports/cache/<TS>-<slug>-final.md` at the **main-repo root** (never a linked worktree). `reports/` and `reports_dev/` are gitignored.

## Related

- `/cpv-validate-cache` — Audit only (read-only).
- `/cpv-validate-plugin` — Full plugin validation.
- `/cpv-fix-validation` — Generic plugin-fixer for non-cache validation reports.

## Reference

CA-01..CA-06 rule pack is derived from *"Lessons from Building Claude Code: Prompt Caching Is Everything"* by Thariq Shihipar (Anthropic) and the open-source [ussumant/cache-audit](https://github.com/ussumant/cache-audit) corpus.

## Architecture notes (v2.89.4)

- **Menu rendering is offloaded to the `cpv-format-menu` fork-skill.** The orchestrator writes the JSON spec to `/tmp/cpv-cache-optimize-<purpose>-spec.json`, invokes the Skill tool, and the fork-skill spawns a fresh `general-purpose` subagent on haiku that runs `scripts/format_menu.py`. The orchestrator MUST copy the Skill tool's text result into its prose output — both Bash stdout AND Skill tool results are invisible to the user.
- **Why a fork-skill instead of `model: haiku` on this command?** Per the Claude Code skills doc, `model:` overrides apply "for the rest of the current turn" while keeping the inherited conversation history. A multi-turn orchestrator command body on opus with a 1M-token context cannot safely degrade mid-turn to haiku — the override silently fails. `context: fork` (on the `cpv-format-menu` skill) creates a fresh subagent with no inherited history, so `model: haiku` actually takes effect for the render step alone. The orchestrator turn itself stays on the session model.
- The orchestrator runs in the main session; only the main session can dispatch the opus `cache-optimizer-agent` work agent.
