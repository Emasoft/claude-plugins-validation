# TRDD-b8dd7f6b — Menu visibility + orchestrator latency fix (v2.89.4)

**TRDD ID:** `b8dd7f6b-ee72-479d-85d2-cc282f499abb`
**Filename:** `design/tasks/TRDD-b8dd7f6b-ee72-479d-85d2-cc282f499abb-menu-visibility-fix.md`
**Tracked in:** this repo (design/tasks/ is git-tracked)
**Status:** Done

## Context (the user's symptom)

`/cpv-doctor` is broken end-to-end:

1. **Menu invisible.** The slash-command body calls `scripts/format_menu.py` via Bash. The menu is generated correctly — but Claude Code's UI only renders the LLM's prose text output, **not Bash tool stdout**. So the user types `/cpv-doctor`, sees "Ran 1 shell command", and… nothing. The menu was generated and immediately discarded.
2. **Says Opus, claims Haiku.** The slash command's frontmatter `model: haiku` does not appear to switch the model when the command is invoked from an Opus session. The orchestrator turn runs on Opus, yet prints a banner claiming "Menu rendering is currently haiku (this turn only)" — a lie.
3. **Slow + auto-compacts.** Each invocation: (a) loads the 248-line command body, (b) calls Bash with a heredoc + Python startup + JSON parsing + format rendering — ~27s wall, (c) re-loads the 5 global rule files (~635 lines), (d) burns Opus tokens. A few cycles trigger auto-compact.

User feedback: *"the menu does not appear at all. and consumes the whole context triggering the compact! fix this mess!"*

## Root cause (architectural)

The v2.89.3 pattern hinges on a false assumption: that calling `format_menu.py` via Bash will print the menu to the user. **Bash stdout goes to the LLM as a tool result, NOT to the user's UI.** The instruction "Print the helper's stdout verbatim to the user" was easy to miss — and even when followed, the round trip costs ~27s of wall time per menu.

Secondary issues:
- The model: haiku frontmatter isn't a hard guarantee. Whether Claude Code respects it on slash-command dispatch is platform-dependent.
- The 248-line body has lots of redundant prose explaining the architecture inline.

## Fix

### Phase A — pre-render the first-contact menu

`/cpv-doctor` has a FIXED 24-row menu — there's no reason to regenerate it on every invocation. Pre-render it ONCE with `format_menu.py` (correct display-column widths), then embed the literal Unicode-box-drawn text directly in the slash-command body. The orchestrator's instruction becomes: "copy this block VERBATIM into your text output, then wait for the user's reply." **Zero Bash. Zero latency. Always visible.**

### Phase B — iron-clad "copy stdout into text" for dynamic menus

The other 3 orchestrators (`/cpv-fix-validation`, `/cpv-fix-marketplace-validation`, `/cpv-cache-optimize`) auto-discover rows at runtime — they MUST keep calling `format_menu.py`. But the instruction is now reinforced at every Bash call:

> **CRITICAL: After running the Bash block, copy its stdout VERBATIM into your text message.** Bash output is invisible to the user — without copying it, the menu never appears.

The post-scan menus in `/cpv-doctor` also use this pattern (they need disabled-row dropping based on actual finding counts).

### Phase C — replace the "currently haiku" lie with a tip

Old banner:
> Session model: <X>. Menu rendering is currently haiku (this turn only). For cheaper navigation across every menu step, run /model haiku once.

New banner (one line, honest):
> Tip: run `/model haiku` once for cheaper menu navigation across this session.

### Phase D — replace summary-helper bash with literal text

The post-scan summary (`format_menu.py summary`) was another wasted Bash round-trip. Replaced with an inline literal table the orchestrator can fill with `<C> <M> <n> <t> <w> <VERDICT>` and print as text. Saves one Bash call per scan cycle.

### Phase E — trim the command bodies

Removed the duplicated "Critical rules" block, the verbose "You are the menu orchestrator" preamble (replaced with a 2-line "HARD RULES" list), and the architecture-history sections (kept only a 4-line "Architecture notes" footer).

## Files changed

- `commands/cpv-doctor.md` — first menu pre-rendered, summary inlined, body cut from 248 → 202 lines
- `commands/cpv-fix-validation.md` — banner replaced, iron-clad copy-stdout instructions added, body cut from 172 → 163 lines
- `commands/cpv-fix-marketplace-validation.md` — same pattern, body cut from 178 → 171 lines
- `commands/cpv-cache-optimize.md` — same pattern, body cut from 182 → 175 lines
- `tests/test_menu_visibility.py` — NEW regression tests: (a) cpv-doctor's first-contact menu is embedded as literal text in the body; (b) every `format_menu.py` Bash call in an orchestrator body has a "copy stdout" directive within 25 lines.

## What stays the same (intentionally)

- The `format_menu.py` helper itself — still needed for dynamic menus (disabled-row dropping + renumbering with correct display-column widths).
- The opus work agents (`cpv-doctor-agent`, `plugin-fixer`, `marketplace-fixer`, `cache-optimizer-agent`) — unchanged.
- The Step 1-6 orchestrator flow.
- The `model: haiku` frontmatter — kept as best-effort; the banner now honestly suggests `/model haiku` as a user opt-in.

## Acceptance criteria

- [x] `/cpv-doctor` first menu appears immediately in chat (no Bash round-trip)
- [x] First menu render is < 100ms (no Python interpreter startup)
- [x] Post-scan menus continue to drop disabled rows + renumber correctly via `format_menu.py`
- [x] All 38 `test_agent_model_tiers.py` tests still pass (format_menu.py still referenced, /model haiku still mentioned, etc.)
- [x] All 37 `test_format_menu.py` tests still pass (the helper itself is unchanged)
- [x] New regression test pins the pre-rendered-first-menu pattern
- [x] New regression test pins the iron-clad "copy stdout" directive

## Why this is correct architectural debt repayment

The v2.89.3 pattern was elegant in theory but failed in practice on the one thing that matters: **the user actually sees the menu**. We now have a hybrid pattern that's pragmatic:

- **Static menus → pre-rendered text** (fast, visible, no Bash)
- **Dynamic menus → format_menu.py + explicit copy-stdout directive** (still correct display-width math, still drops disabled rows, but the instruction is loud enough to actually be followed)

The lesson for future orchestrator design: **anything destined for the user MUST appear in the LLM's text output. Tool stdout is invisible.** This invariant is now documented in the test that pins the pattern.
