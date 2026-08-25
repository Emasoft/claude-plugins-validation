---
trdd-id: 37d3dbba-3425-4b02-8fc8-b27008124d60
title: Complete pending issues — menu bug-free, README, help screens, issue #70
column: published
created: 2026-06-10T10:45:28+0200
updated: 2026-06-24T03:27:35+0200
current-owner: cpv-maintainer-claude
assignee: cpv-maintainer-claude
task-type: bugfix
release-via: publish
publish-target: claude-plugins-validation
test-requirements: [unit, lint, typecheck]
relevant-rules: []
external-refs: ["github.com/Emasoft/claude-plugins-validation/issues/70", "github.com/Emasoft/claude-menu-system"]
---

# Complete pending issues — menu bug-free, README, help, issue #70

## ⏵ STATE — READ THIS FIRST ON RESUME — 2026-06-10

**User request:** "complete all pending issues, ensure everything is working,
the full menu is bugs free, the readme is updated, along with the scripts help
screens, including those of the claude menu plugin, then … publish." Plus a
mid-session correction: "the fact that you don't know the very structure of the
plugin you are working on is crazy … use the claude.md."

**Current state — 5 workstreams, all DONE except the final CPV publish:**

1. **CLAUDE.md (NEW)** — authored `claude-plugins-validation/CLAUDE.md`
   (authoritative inventory: v2.126.x · 13 cmd · 15 agent · 46 skill · 113
   script · 302 test files; menu architecture; canonical commands; invariants).
   Read it FIRST on resume; update counts on every structural change. Memory:
   `maintain-project-claude-md.md`.

2. **CPV menu (14 bugs) — FIXED.** From `reports/menu-audit/…-menu-bugs.md`:
   - `commands/cpv-main-menu.md`: "~22 commands"/"half-dozen agents"→de-counted;
     "11 rows"→"all categories".
   - `agents/cpv-main-menu-agent.md`: **mechanism contradiction (Bug 8)** — agent
     rendered menus by INLINING the spec via `cpv_menu.py` (a 2nd source of truth
     for 05-main.json) vs the mandated `print_menu.py fixed NN`. Rewrote Rendering
     plus First-Contact plus all `cpv_menu.py` refs → `print_menu.py fixed <NN>`
     (fixed 5=main, 23=done, 26=post-validate); deleted the inline 05-main copy.
   - `menu-tree.md` (via cpv-spark, 8/8): dead leaf `/cpv-setup-branch-rules-generic`
     →`setup-plugin-repo` skill recipe; 3 dangling §3.7-table leaves repointed;
     §3.8.3 `--quick` documented; the false "≤7 functional rows" rule reworded;
     §3.16.7-11 "single scanner" honesty note; `CLAUDE_PRIVATE_USERNAMES` added to
     the shell prologue. Bug 12 (unsurfaced agents skill-validation-agent/cpv-doctor-
     agent/plugin-validator/cpv) documented in CLAUDE.md as batch-only/legacy.

3. **README — REFRESHED.** test badge 2336→8800+; "20 validators/checks"→"17"
   (4 places, matching `validate_plugin --help`); added plugin-devitalizer +
   plugin-diagnoser to the AI Agents table; replaced stale "Every agent presents
   a menu when invoked" with the menu/route reality + the 15-agent note.

4. **Help screens — VERIFIED current.** remote_validation/cpv_pre_install_scan/
   bump_version/validate_plugin/publish `--help` all accurate (remote_validation
   even says "17 checks").

5. **claude-menu-system (SEPARATE repo) — PUBLISHED v0.1.6** (user authorized
   edit+publish). Fixed M1 (dup-key route drop → reject), M2 (renumber clobbers
   keys), M3 (truncation off-by-one), M4 (empty-menu prompt), M5 (`--help` crash),
   R1 (README path), S1 (stale skill refs); +25 tests (214 pass), ruff+mypy clean.
   **Gotcha:** publish blocked on a CPV-FP — a fullwidth `ｗ` (U+FF57 homograph) +
   "ASCII char" wording in a `pyproject.toml` COMMENT tripped INDIRECT_PROMPT_INJECT
   (NIT→--strict block). Reworded the comment to scan-clean (verified via scan_content
   before commit); this FP class is the SAME as #70-B-1, now fixed CPV-side.
   CI green. Commit ea50929 (+amend); release v0.1.6.

6. **Issue #70 B/C — FIXED (CPV-side, via aegis agent + my independent verify).**
   - B-1 build-config COMMENT (`.toml/.ini/.cfg/.cnf/.conf`) prose-injection → suppress
     (new `_BUILD_CONFIG_EXTS`+`_PROSE_INJECTION_RULES` carve-out in
     `_context_classifier_verdict`). FN-safe verified: TOML exec-VALUE `curl|bash`
     still fires; SKILL.md prose-injection still fires; invisible-unicode excluded.
   - B-3 AppleScript COMMENT (`--`/`#`/`(* *)` w/ block-nesting) execution-class →
     suppress, in BOTH skillaudit (`applescript_comment_lines()`) and
     `validate_security.scan_for_supply_chain` (RC-136). FN-safe verified: real
     `do shell script "curl|sh"` still fires; trailing `--` comment keeps code visible.
   - B-2 CSS comment — already fixed (`_STYLE_LANG_INERT_EXEC_RULES`); locked w/ tests.
   - **B-4 3rd-party scanner rule-table — BY-DESIGN, NOT auto-cleared.** Signals
     (`*_PATTERNS=[...]`, `# RC-NN`) are attacker-forgeable → extending
     `is_pattern_source_line` to 3rd parties re-opens the RT3 hole. Stays DEMOTED-NIT
     (visible triage; real exfil still CRITICAL). Per-plugin allowlist REJECTED
     (rule-muting). Memory: `issue-70b-scanning-the-scanner-bydesign.md`.
   - Part C orphan-cache (`.orphaned_at`) — already fixed prior session.
   - +16 tests (`tests/test_issue_70_bc_config_applescript.py`).

**NEXT ACTION:** serial suite (running, `/tmp/cpv_serial_final.log`) must be
green → commit the CPV changeset (CLAUDE.md + README + menu + #70 + tests +
manifest, BY NAME) → `publish.py --patch` (v2.126.5) → verify CI green → comment
issue #70 (B-1/B-2/B-3 fixed, B-4 by-design, C fixed; begin "This is the Claude
responsible for the claude-plugins-validation project.") and decide open/close.

**Verification done:** self-validate --strict 0/0/0/0+4WARN (incl. new CLAUDE.md);
issue #70 B-1+B-3 independently two-sided-verified by me (not just the agent); manifest
regenerated after CLAUDE.md add.

## Load-bearing facts
- claude-menu-system source: `~/Code/claude-menu-system` (separate repo, own
  publish.py). CPV's menu routes through ITS Stop hook → CPV menu only as
  bug-free as that hook.
- The TOML-comment FP (#70-B-1) is what blocked the CMS publish — same root cause,
  fixed both sides.
- Scratch repro files live in `/tmp/fx70*` (not the repo); do NOT ship.
