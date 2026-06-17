---
trdd-id: 933592ac-98f0-498c-9e7c-54742acaa76c
title: Fixer/detector hardening — amvcp field report (htmlhint FP, doc-context NITs, TOC catch-22, fixer-agent robustness)
column: dev
created: 2026-06-17T21:34:14+0200
updated: 2026-06-17T21:48:17+0200
current-owner: claude-plugins-validation
assignee: claude-plugins-validation
priority: 2
severity: MEDIUM
effort: XL
labels: [false-positive, fixer-agent, devitalizer, toc-contract, lint-engine, skillaudit]
task-type: bugfix
parent-trdd: null
npt: []
eht: []
blocked-by: []
relevant-rules: []
release-via: publish
delivery: direct-push
target-branch: master
test-requirements: [unit, integration, lint, typecheck]
audit-requirements: []
review-requirements: []
impacts: [public-api]
external-refs: ["github.com/Emasoft/claude-plugins-validation/issues/132"]
---

# TRDD-933592ac — Fixer/detector hardening (amvcp field report)

## ⏵ STATE — READ THIS FIRST ON RESUME (authoritative) — 2026-06-17

**Trigger:** the user reported that CPV's fixer/devitalizer agents "fail to fix
validation issues, crash, exhaust context, leave a corrupt state." Field report
(another plugin's Claude) at
`reports_dev/20260617_121158+0200-cpv-scan-issues-for-fixer-and-devitalizer.md`.

**Verification done (user chose "re-verify first"):** cloned amvcp
(the amvcp working tree under `~/Code`, source READ-ONLY) to
`/tmp/amvcp-clone`, checked out the report's commit `4d96866` IN THE CLONE, ran
the real `remote_validation.py plugin … --strict`. Faithful reproduction:
**0 CRITICAL · 0 MAJOR · 40 MINOR · 13 NIT · 12 WARNING** (EXIT 3). Raw report:
`/tmp/amvcp-val.txt`. The report's claims were OVERSTATED — verify each before
fixing (claim-verification): several were already fixed on 2.129.0, one was my
own `example.com` (RFC 2606) false-alarm.

**Verified root cause of the agent failures:** the fixer is told to reach
0/0/0/0, but ~19 findings are CPV-side and UNFIXABLE by the plugin (10 htmlhint
banner FP + 8 doc-example demoted-NIT + 1 defensible REGEX_DOS) PLUS a catch-22
TOC contract with an unrecognized embed format. So the agent thrashes on the
unfixable (context exhaustion) and mangles correct code trying to "fix"
detector-FPs (corrupt state). Thesis CONFIRMED.

**PROGRESS:**
- **A1 DONE** (uncommitted→committing): `cpv_lint_engine._lint_html` now (a) strips
  the `Config loaded:` banner + `Scanned N file…` summary before building findings,
  (b) strips ANSI color escapes, (c) takes the first 20 REAL lines so genuine errors
  aren't crowded out. FN-safe (a real htmlhint tag-pair error still surfaces).
  Verified end-to-end on amvcp: 10 banner MINORs → 0, ANSI → 0, MINOR 40→33.
- **CACHE-REV BUG found + fixed (the bigger win):** the lint cache key
  (`_build_cache_key`) was keyed on file-content + EXTERNAL-tool-version only, NOT
  CPV's own engine code — so ANY lint-engine fix was MASKED for warm-cache users
  after a CPV upgrade (and fed agents stale findings). Fixed by folding a
  `_LINT_ENGINE_CODE_REV` (sha256 of `cpv_lint_engine.py`) into the key. Proven
  end-to-end: a re-validate with NO cache clear re-linted (val3 had ANSI, val4
  didn't). +5 tests; lint-engine + cache suites 293 pass.

**NEXT ACTION:** A2 (doc-context suppressors for the 8 demoted-NIT doc examples),
B1/B2 (TOC converge + dual embed format), B3/B4/B5 (fixer-agent hardening) — delegate
to opus agents, central-verify each (own probe + full serial suite + self-validate
`--strict`, per the NIT-catch lesson — not just pytest). Ship incrementally.

## Verified findings (amvcp@4d96866)

| Item | Count | Finding | Verdict | Owner |
|---|---|---|---|---|
| A1 | 10 MINOR | `htmlhint: Config loaded: .htmlhintrc` | REAL FP — banner dumped as findings on non-zero exit (`cpv_lint_engine.py:1535`) | CPV detector |
| A2 | 8 NIT | skillaudit on doc examples (SHELL_EXEC/CMD_INJECTION/REGEX_DOS/INSECURE_CRYPTO/OBFUSCATION) in `references/*.md` + 2 in SKILL.md bodies; "demoted, needs review" | likely FP — demoted-NIT blocks `--strict`, no plugin-side fix | CPV detector + policy |
| B1/B2 | ~21 MINOR | TOC-embed (`N/M headings embedded`); 18 show `0/N` on `amvcp-code-syntax/SKILL.md` | REAL — embed format not recognized (B2) + catch-22 (B1) | CPV detector + fixer |
| A4 | 2 NIT | EXFIL_COVERT on `amvcp-runtime.js:2538/:2545` (sendBeacon/fetch to a VARIABLE endpoint) | mostly WAD — can't prove the variable local; conservative demote defensible; escape = audit-consent sentinel | CPV (minor) |
| A3 | 1 MINOR | REGEX_DOS on `new RegExp('(?:'+alt.join('\|')+')')` | defensible-conservative, NOT a clear FP (can't statically prove the joined alternation safe; catastrophic `(x+)+` sibling fires identically) | CPV (low) |

**NOT CPV's (do not touch):** 4 mypy "Returning Any" MINORs, 4 own-quality
WARNINGs (no-checklist, dead URL 404, no dep-installer hook), 8
RC-PIPELINE-DRIFT WARNINGs (non-blocking; the ci/release/notify ones correctly
show the v2.128.0 "AHEAD of canon — do NOT downgrade" message).

## Plan (priority order; each FN-safe two-sided, central-verified, shipped)

1. **A1 — htmlhint banner strip** (`cpv_lint_engine._lint_html`): filter
   `Config loaded:` + `Scanned N file…` summary lines BEFORE converting stdout
   to findings, then take the first 20 REAL lines (so genuine error lines aren't
   crowded out by banners). FN-safe: real htmlhint errors still become MINORs.
   Clears 10 MINOR. Zero judgment — do first. (#132)
2. **A2 — doc-context suppressors** for the 8 remaining doc-example shapes
   (SHELL_EXEC/CMD_INJECTION/REGEX_DOS/INSECURE_CRYPTO/OBFUSCATION in
   `references/*.md`; the 2 CMD_INJECTION in SKILL.md bodies need per-line
   inspection — instruction-loadable surface, so verify they are DISPLAYED not
   INSTRUCTED). Same pattern as #76/#83: suppress provably-inert displayed code
   to `safe_literal` (full, non-blocking), NOT a policy relax. Two-sided: the
   doc example clears AND a real executable of the same rule still fires.
3. **B1/B2 — TOC contract**: make the embed counter accept the nested-`-`-list
   form (not only the `>`-blockquote-with-`·` form), DOCUMENT the accepted
   format, and make the contract converge (treat the two halves — ref-TOC
   section + SKILL.md embed — as ONE atomic fix) instead of a catch-22.
   Detector (`validate_toc_embedding`) + fixer-agent prompt.
4. **B3/B4/B5 — plugin-fixer hardening**: post-move invariant (destination ref
   non-empty AND `words(SKILL+refs) >= words@HEAD`); strict file-scope (only the
   files named in findings); NEVER delete a contract-satisfying section;
   exclusive-tree / owned-worktree isolation for long runs.
5. **A3/A4 — small/defensible**: A4 document the audit-consent-sentinel escape;
   A3 leave conservative unless a safe linearity check (flat alternation, no
   trailing quantifier) is clearly FN-safe.

## Decomposition (delegate to opus agents; disjoint files)

- **W1 (A1)** — `cpv_lint_engine.py` `_lint_html` + a test. Small; inline or 1 agent.
- **W2 (A2)** — `_skillaudit_markdown_context.py` (+ the SKILL.md-body cases) doc-context discriminators + tests. The big detection-accuracy piece.
- **W3 (B1/B2)** — `cpv_validation_common.py` `validate_toc_embedding` (accept both embed shapes; converge) + the fixer/skill prompt docs + tests.
- **W4 (B3/B4/B5)** — `agents/plugin-fixer.md` + `skills/fix-validation` + `skills/canonical-pipeline` (post-move invariant, strict scope, no contract-satisfying deletion, worktree isolation). Prompt/logic hardening + any verifier-script support.

Each: I write the precise spec, delegate to a `model: opus` agent, then
central-adversarial-verify (own probe + full serial suite + self-validate
`--strict`) before shipping. Ship incrementally (A1 first).

## Notes / gotchas

- Verification fixtures must use a REAL external domain, not `example.com`
  (RFC 2606 reserved → scanner correctly ignores it; gave me a false FN alarm).
- A2's 2 SKILL.md-body CMD_INJECTION findings are on an instruction-loadable
  surface — do NOT blanket-suppress; confirm they are displayed code, else they
  stay (a SKILL.md that INSTRUCTS an agent to run a command is not a doc example).
- amvcp is ANOTHER project — never edit it; only validate (read-only).
