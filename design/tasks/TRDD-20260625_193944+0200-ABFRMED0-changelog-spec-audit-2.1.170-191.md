---
trdd-id: ABFRMED0
title: Audit CPV spec validators against Claude Code changelog v2.1.170 → v2.1.191 — fix genuine gaps
column: published
created: 2026-06-25T19:39:44+0200
updated: 2026-06-25T21:34:39+0200
current-owner: cpv-main-session
assignee: cpv-main-session
priority: 1
severity: MEDIUM
effort: M
labels: [spec-audit, changelog, false-positive, skill-frontmatter, hooks]
task-type: bugfix
parent-trdd: null
npt: []
eht: []
blocked-by: []
relevant-rules: []
release-via: publish
delivery: direct-push
target-branch: master
merge-strategy: squash
test-requirements: [unit, lint, typecheck]
audit-requirements: [security-scan]
review-requirements: [code-review]
runtime-targets: [macos, linux]
impacts: []
external-refs: ["https://code.claude.com/docs/en/changelog.md", "https://code.claude.com/docs/en/skills.md", "https://code.claude.com/docs/en/hooks.md"]
attempts: 1
implementation-commits: [436a686]
published-version: 2.149.0
published-at: 2026-06-25T21:34:39+0200
---

# TRDD-ABFRMED0 — CPV spec audit vs Claude Code changelog v2.1.170 → v2.1.191

## ⏵ STATE — READ THIS FIRST ON RESUME (authoritative) — 2026-06-25

**User directive (verbatim):** "you should read the issues opened on the plugin
github repo. Also, there were big changes to claude code, impacting the whole
plugins and extensions ecosystem. You must read all the updates in the changelog
since version v2.1.170 onward to v2.1.191 and update the plugin accordingly."

**FACT 1 — CPV GitHub issues: ZERO open** (verified `gh issue list --state open`
→ 0; the 12 most-recent #142–#153 are all CLOSED). Nothing to triage there.

**FACT 2 — CPV is ALREADY well-maintained against this range** (verified, NOT
assumed): `VALID_TOOLS` (cpv_validation_common.py:572) correctly EXCLUDES
`TeamCreate`/`TeamDelete` per v2.1.178 with a detailed note (L611-614);
`validate_hook.py:141` too; hook events list (validate_hook.py:67-105) is
comprehensive through v2.1.121+. So this is a TARGETED AUDIT (verify each
spec-affecting item is handled; fix only genuine gaps), NOT a stale-plugin
rewrite.

**SPEC-AFFECTING changelog items in range (the audit surface):**
- v2.1.178 — `TeamCreate`/`TeamDelete` tools REMOVED → CPV `VALID_TOOLS` ✓ handled.
- v2.1.186 — skill frontmatter `display-name`/`default-enabled`/`fallback`/`metadata.*`
  "now accept kebab/snake/camelCase" → **CONFLICT** (see GAP-1).
- v2.1.191 — comma-separated hook matchers (`"Bash,PowerShell"`) now fire → **GAP-2**.
- v2.1.178/186/172 — permission-rule param syntax `Tool(param:value)`,
  `Agent(type)`/`Agent(x,y)`, `WebFetch(domain:*.example.com)` → AUDIT-B.
- ~14 new settings keys + env vars (sandbox.credentials, disableBundledSkills,
  respondToBashCommands, teammateMode:iterm2, enforceAvailableModels,
  footerLinksRegexes, wheelScrollAccelerationEnabled, attribution.sessionUrl,
  sandbox.allowAppleEvents, CLAUDE_CLIENT_PRESENCE_FILE,
  CLAUDE_CODE_MCP_TOOL_IDLE_TIMEOUT, CLAUDE_CODE_RETRY_WATCHDOG,
  CLAUDE_CODE_DISABLE_BUNDLED_SKILLS, CLAUDE_CODE_SAFE_MODE) → AUDIT-B.
- v2.1.178 — nested `.claude/skills` `<dir>:<name>` naming → AUDIT-B.
- v2.1.169 — `post-session` lifecycle hook (SELF-HOSTED RUNNER context, likely
  NOT a settings.json hook event) → AUDIT-B confirm.

**GROUNDED FINDINGS (verified from source):**

- **GAP-1 (skill frontmatter, v2.1.186) — NEEDS CROSS-DOC ADJUDICATION.**
  `validate_skill.py:147` warns "Unknown frontmatter field '<key>' (may be ignored
  by CLI)" (WARNING) for any key not in `SKILL_FRONTMATTER_FIELDS`
  (cpv_validation_common.py:1176-1193, 17 fields). That 17-field list MATCHES the
  authoritative skills.md frontmatter reference (skills.md L208-242) EXACTLY — and
  skills.md does NOT document `display-name`/`default-enabled`/`fallback`/`metadata`.
  But changelog v2.1.186 explicitly calls them "skill frontmatter" keys the runtime
  now accepts in 3 casings. → CONFLICT: changelog-accepts vs skills.md-undocumented.
  DO NOT blind-add. AUDIT-A must adjudicate (are they skill keys? agent/command/
  plugin keys the changelog conflated? what is the precise, non-over-broadening CPV
  fix + the 3-casing handling?).

- **GAP-2 (hook matcher comma, v2.1.191) — CONFIRMED, fix ready.**
  `validate_hook.py:701` `_check_matcher_values` does `re.split(r"[|()]", matcher)`
  — splits on `|`, `(`, `)` but NOT comma. So a v2.1.191-valid `"Bash,PowerShell"`
  becomes ONE unknown token → spurious `report.info(...)` (L708, INFO = non-blocking
  FP). Fix: add `,` to the split class → `r"[|(),]"`. Low severity (INFO) but a real
  correctness FP; trivial + TDD-able.

- **NOT-A-BUG — CANONICAL_TOOLS (cpv_tool_permission_match.py:44-87).** Still lists
  `TeamCreate`/`TeamDelete`, BUT used ONLY to build the `ToolName(` detection regex
  (`_TOOL_NAMES_FOR_RE`, L115) — NOT as a validity allowlist (that's VALID_TOOLS,
  already correct). Detection-only retention is harmless/defensible. Optional: a
  one-line clarity comment. The `_skillaudit_json_context.py:157` regex likewise
  retains them for detection — harmless.

**AUDITS COMPLETE (2026-06-25) — both reports under reports/cpv-changelog-audit/:**
- AUDIT-A (`*-audit-A-skill-frontmatter-keys.md`): all 4 keys ARE runtime-accepted
  skill frontmatter (changelog v2.1.186 authoritative; skills.md table lags — docs
  lag, not a conflict). Surfaced an INTERNAL CPV inconsistency: comprehensive
  validator already accepts `metadata`, basic doesn't. Gave exact §6 fix (helper +
  4 keys + casing).
- AUDIT-B (`*-audit-B-remaining-items.md`): EVERYTHING ELSE HANDLED or OUT-OF-SCOPE.
  permission-rule param syntax (no CPV rule-string parser), ~14 new settings keys
  (`KNOWN_SETTINGS_KEYS` is unconsumed dead code; plugin-settings allowlist correctly
  scoped to 4 keys), new env vars (only `${VAR}` SKILL.md refs trip it; these are
  CLI vars), nested `.claude/skills` `<dir>:<name>` (CPV validates plugin skills
  only), `post-session` (self-hosted-runner hook, NOT in hooks.md, correct to flag
  as unknown) — all need NO change. One housekeeping gap: stale tool-name regex.

**FINALIZED FIX SET (3 file-disjoint fixes — ALL false-positive reductions; NO gate
relaxed, NO rule suppressed):**
- **FIX-A (v2.1.186 skill frontmatter) — MEDIUM, main fix.** Per AUDIT-A §6: add
  `display-name`/`default-enabled`/`fallback`/`metadata` to `SKILL_FRONTMATTER_FIELDS`
  (cpv_validation_common.py:1176) + `_CASING_TOLERANT_SKILL_KEYS` + `_to_kebab()` +
  `is_known_skill_frontmatter_key()`; route validate_skill.py:147 +
  validate_skill_comprehensive.py unknown-field path + validate_command.py:211
  through the helper (commands share the skill field set). Closes the
  validate_skill↔comprehensive `metadata` disagreement. Files: cpv_validation_common.py,
  validate_skill.py, validate_skill_comprehensive.py, validate_command.py. ALSO verify
  AUDIT-A §6.6 (plugin.json top-level `metadata` may WARN as unknown manifest field —
  fix if real, per never-defer).
- **FIX-B (v2.1.191 hook comma matcher) — LOW (INFO FP), trivial.** validate_hook.py:701
  `_check_matcher_values` `re.split(r"[|()]", matcher)` → `r"[|(),]"` so `"Bash,PowerShell"`
  validates each tool (was a spurious INFO). File: validate_hook.py.
- **FIX-C (skillaudit tool regex) — LOW housekeeping.** _skillaudit_json_context.py:153
  `_CLAUDE_CODE_TOOL_GLOB_RE` sources its tool-name alternation from
  `cpv_tool_permission_match.CANONICAL_TOOLS | set(TOOL_ALIASES)` (SSOT; adds
  Monitor/PowerShell/etc. so their `permissions.*` globs are suppressed, not
  content-scanned → rare FP gone). + clarity comment on CANONICAL_TOOLS explaining
  the deliberate VALID_TOOLS(validity)-vs-CANONICAL_TOOLS(detection-breadth)
  divergence. Files: _skillaudit_json_context.py, cpv_tool_permission_match.py.

**NOT CHANGED (verified safe):** CANONICAL_TOOLS retains TeamCreate/TeamDelete on
PURPOSE (detection/suppression breadth; harmless — removing could un-suppress a
stale `TeamCreate(...)` glob → FP). Only a clarity comment is added.

**NEXT ACTION:** Dispatch 3 parallel examine+fix spark agents (file-disjoint, TDD
two-sided) for FIX-A/B/C. Then central-verify (read every diff + own two-sided
probes through the REAL validators) + ruff + mypy + cache-cold self-validate
0/0/0/0 + run the gate. Then update CLAUDE.md + README + help + the LOCAL
`cpv-spec-reference` memory note. REGEN `.cpv-self-hashes.json` LAST (after ALL .py
and CLAUDE.md/TRDD edits). Then publish.py + watch CI green.

**FIX SET COMPLETE + VERIFIED (2026-06-25 ~21:00) — inline completion after the
agent batch hit a session/rate limit; partial edits were KEPT (correct) and
finished by hand:**
- **FIX-A DONE** — `is_known_skill_frontmatter_key` + `_to_kebab` +
  `_CASING_TOLERANT_SKILL_KEYS` + 4 keys in cpv_validation_common.py; WIRED into
  validate_skill.py:147, validate_skill_comprehensive.py:1565 (non-strict branch),
  validate_command.py:211 (OR-fallback, preserves command-local fields). Basic↔
  comprehensive now agree on `metadata`.
- **FIX-B DONE** — validate_hook.py `re.split(r"[|(),]")`.
- **FIX-C DONE** — _skillaudit_json_context.py SSOT regex (cycle-safe import,
  shape preserved) + CANONICAL_TOOLS clarity comment in cpv_tool_permission_match.py.
  Import-cycle check: OK (no cycle).
- **§6.6 — NO CHANGE (verified).** plugin.json top-level `metadata` is NOT a
  documented spec field (plugins-reference lists it only in prose; its
  "Unrecognized fields" section calls a foreign-ecosystem `metadata` tolerated-
  but-unrecognized). validate_plugin's `Unknown manifest field 'metadata'` WARNING
  (non-blocking, "consider documenting it") is therefore CORRECT — distinct from
  the SKILL-frontmatter `metadata` (v2.1.186) which IS now a spec key. Existing
  behavior is right.

**VERIFICATION (all green):** 22 NEW two-sided tests pass (11 FIX-A + 5 FIX-B +
6 FIX-C); helper correct on 16 hand-checked cases; ruff + mypy clean on all 7
changed scripts; 67-file / 2415-test regression on every test importing a changed
module — ALL PASS, zero regression.

**DONE — SHIPPED v2.149.0, CI GREEN (feature commit 436a686 → release a764510,
2026-06-25).** CLAUDE.md + self-hash manifest updated; fixed 2 blocking markdown
NITs (MD004 in this TRDD + a pre-existing MD018 in the v2.148.0 35BN0TEI TRDD);
cache-cold (`CPV_SCAN_CACHE=0`) strict self-validate 0/0/0/0; publish.py --minor
bumped 2.148.0→2.149.0; CI (Lint 38s + Validate 3m30s + Test 7m46s serial/no-re2)
GREEN, Release 10m48s GREEN, Notify Marketplace GREEN, PyPI skipped (dormant).
Release: https://github.com/Emasoft/claude-plugins-validation/releases/tag/v2.149.0

**Durable artifacts:** scratchpad `cc-changelog.md` / `cc-skills.md` / `cc-hooks.md`
(fetched live); audit reports under reports/cpv-changelog-audit/.

## Verification gates
- Every fix is TWO-SIDED tested (fires on the real case, silent on the inverse).
- NO gate relaxed; NO rule suppressed. GAP fixes REMOVE false positives only.
- ruff + mypy clean; cache-cold (`CPV_SCAN_CACHE=0`) self-validate 0/0/0/0.
- CPV's own CI + Release green after publish.
