---
trdd-id: b0c85371-37bf-430b-baf7-9f51562c533f
title: SkillSpector deferred cherry-picks — proposals needing a judgment call
status: completed
created: 2026-06-01T21:48:19+0200
updated: 2026-06-01T23:22:51+0200
---

# TRDD-b0c85371 — SkillSpector deferred cherry-picks (proposals)

## ⏵ IMPLEMENTED (2026-06-01, v2.118.0) — "do everything deferred"

The maintainer greenlit implementing all deferred items. Each was built in its
DEFENSIBLE, FP-resistant form (the reason each was deferred), with two-sided tests
in `tests/test_skillspector_port_phase2.py` (29 tests):

1. **TR1 → TR3** (catch-all skill-description baiting): NOT the FP-prone hook-matcher
   form. Added `_CATCHALL_TRIGGER_RE` advisory **WARNING** in
   `validate_skill.validate_description_field` — anchored catch-all phrases only
   ("use this for everything", "on every request", "whenever the user says anything").
   Scoped descriptions ("for any Python task") do NOT fire.
2. **LP2** (wildcard/blanket permission): the `allow:["*"]` shape is dubious Claude
   Code syntax (NOT added — would be a dead check). Implemented the REAL gap:
   `defaultMode: "bypassPermissions"` in a plugin-shipped settings file (RC-62 only
   reads plugin.json) → WARNING in BOTH `_flag_permissions_default_mode[_local]`.
3. **AST7** (taint-gated dynamic getattr): `_check_dynamic_getattr` in
   `cpv_taint_engine.py` — `getattr/setattr/delattr(obj, <tainted name>)` fires
   RC-73/74; a LITERAL attr name never fires (taint-gated, not regex).
4. **MP2** (context-stuffing): `_detect_repeated_token_padding` detector
   (`CONTEXT_STUFFING`, medium/evasion) using SkillSpector's exact lookahead+backref
   regex — legal in a Python DETECTOR (the re2 limit was only for the catalog). The
   `(?!\2)` guard + a pure-punctuation exclusion mean separators / wide table rows
   never fire.
5. **PRIVILEGE_ESC** += `\bpkexec\b`, `\bdoas\s` (re2-safe).
6. **REVERSE_SHELL** += PowerShell `New-Object …TCPClient`, `/dev/tcp/` redirect
   variants, Ruby `TCPSocket.new…exec` (re2-safe). mkfifo + PHP fsockopen were
   already covered.

re2_compatibility.json regenerated (527→532 patterns via the enhanced
`scripts_dev/regen_re2_compat.py` existing-rule-extension path). All shipped in
v2.118.0.

---

## (Original proposal text below — kept for the rationale that drove the defensible forms)

**Filename:** `design/proposals/TRDD-20260601_214819+0200-b0c85371-skillspector-deferred-checks.md`
**Tracked in:** this repo (design/proposals/ is git-tracked)

## Why these are PROPOSALS, not implemented

The parent task (TRDD-de582146) shipped the two SkillSpector ideas that were
unambiguously *missing + valuable + FP-resistant* (`INSECURE_TLS`,
`CREDENTIAL_DISCOVERY`). Deeper investigation of CPV's actual code showed the
remaining cherry-picks are each a JUDGMENT CALL — implementing them naively
would add false-positive noise or redundancy, i.e. reimplement them *badly*.
They are recorded here with verified rationale so the maintainer decides.

The gap-analysis report (`reports/skillspector-eval/CATALOG-GAP-ANALYSIS.md`)
tier-A'd TR1/LP2/AST7; the code below OVERRIDES that tiering with evidence the
report didn't reach.

## Deferred items

### 1. TR1 — overly-broad hook matcher → DO NOT implement as proposed
**Verdict: would be a false positive.** `validate_hook.validate_matcher`
(validate_hook.py:733) DELIBERATELY exempts `None`/`""`/`"*"` as "the
legitimate match-all / absent sentinels". A `*` matcher on
PreToolUse/PostToolUse is the *normal* way to write an observe-all-tools hook
(logging, audit, metrics). Flagging it — even as WARNING, even event-scoped —
fires on every legitimate broad hook. SkillSpector's TR1 targets *skill
triggers* (a skill activating on everything); Claude Code skills have no
equivalent `triggers` list, so there is no clean analog. **If revisited:** the
only defensible signal is a skill whose `description` is a catch-all
("use this for everything") — that is TR3 (prose-heuristic, FP-prone, needs the
markdown classifier's instructional-context guard).

### 2. LP2 — wildcard permission → REDUNDANT
**Verdict: already covered.** `bypassPermissions` / `dangerouslySkipPermissions`
/ `bypass` permission-mode is caught by RC-62 (validate_security.py:5748); the
CLI flag by CLAUDE_CLI_PERMISSION_BYPASS (skillaudit). The only uncovered
sub-shape is a literal `"*"` inside a settings `permissions.allow` array — a
narrow, low-frequency case. **If revisited:** extend RC-62 to also flag
`permissions.allow` containing `"*"`, rather than a new rule.

### 3. AST7 — taint-gated dynamic `getattr` → niche, needs taint-engine work
**Verdict: genuinely absent but low-frequency + non-trivial.** Add `getattr` /
`__getattribute__` as a CONDITIONAL sink in `cpv_taint_engine.py`
(`TAINT_SINKS_*`) that fires only when the 2nd arg is a taint-linked value
(source = `request.*`/`input`/`sys.argv`/`os.environ`). Must be taint-gated —
a bare `getattr(` regex would FP on every defensive `getattr(o, "x", default)`.
Catches `getattr(obj, user_controlled)()` dynamic dispatch. Worth doing when
the taint engine is next touched; not worth a standalone pass.

### 4. MP2 — repeated-token context-stuffing detector → re2-incompatible as written
**Verdict: good structural idea, needs a non-backreference reimplementation.**
SkillSpector's `((\S)(?!\2).{1,19}?)\1{20,}` detects a ≤20-char unit repeated
≥20× (context-window stuffing / DoS padding). It uses a BACKREFERENCE (`\1`,
`\2`) + negative-lookahead which google-re2 rejects, so it cannot go in
skillaudit_patterns.json (the re2 hybrid matcher). **If revisited:** implement
as a small Python structural detector (a `_detect_repeated_token_padding(lines)`
secondary detector, like `_detect_invisible_unicode`) — count max run-length of
a repeated short substring per line; flag > threshold. Low FP (structural, not
prose). CPV has no equivalent.

### 5. PRIVILEGE_ESC extension — `pkexec` / `doas` → cheap, clean, deferred only for batching
**Verdict: clean win, deferred to keep the shipped release focused.** CPV's
PRIVILEGE_ESC has sudo/chmod+s/setuid/setgid/chown-root//etc/shadow but not
`pkexec` (PolicyKit) or `doas` (OpenBSD sudo). Add `\bpkexec\b` and
`\bdoas\s+` (re2-safe, high-signal binary names). Requires the full-re-derivation
re2-compat regen (existing-rule pattern-count change).

### 6. REVERSE_SHELL extension — PowerShell / mkfifo shapes → partial clean win
**Verdict: mkfifo-pipe is clean; bare TCPClient has some FP risk.** CPV's
REVERSE_SHELL (8 patterns) misses `mkfifo … | /bin/sh` (named-pipe reverse
shell — high-signal, add `mkfifo\s+[^\n]*\|\s*(?:/bin/)?(?:ba)?sh`) and the
PowerShell `New-Object System.Net.Sockets.TCPClient` opener (higher FP — legit
PS networking also uses TCPClient; only the full `…TCPClient + GetStream + iex`
chain is conclusive). PHP `fsockopen(..exec(` and Ruby `TCPSocket.new(..exec(`
need cross-construct pairing that re2 can't express in one pattern → skip or
move to a structural detector. **If revisited:** add the mkfifo pattern (clean)
and a paired PowerShell-TCPClient+iex structural check.

## Evidence
- Parent: `design/tasks/TRDD-20260601_212735+0200-de582146-skillspector-cherry-pick-static-checks.md`
- `reports/skillspector-eval/CATALOG-GAP-ANALYSIS.md`, `reports/skillspector-eval/FULL-STATIC-CHECK-CATALOGUE.md`
- Code refs verified: validate_hook.py:733 (TR1), validate_security.py:5748 (RC-62/LP2),
  cpv_skillaudit_native.py:1023 (references/ NOT doc-only — security bypass-fix).
