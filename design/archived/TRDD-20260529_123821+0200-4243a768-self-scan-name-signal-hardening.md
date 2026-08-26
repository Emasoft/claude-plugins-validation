---
trdd-id: 4243a768-a7b8-4bba-96a6-143a88100f85
title: Self-scan name-signal hardening + dead-code removal of the *_self_scan_eligible helpers
column: cancelled
created: 2026-05-29T12:38:21+0200
updated: 2026-08-26T04:30:06+0200
---

<!-- markdownlint-disable-next-line MD025 -->
# TRDD-4243a768 — Self-scan name-signal hardening + dead-code removal

**Filename:** `design/tasks/TRDD-20260529_123821+0200-4243a768-self-scan-name-signal-hardening.md`
**Tracked in:** this repo (design/tasks/ is git-tracked)

## ⏵ STATE — READ THIS FIRST ON RESUME (authoritative; supersedes the body) — 2026-08-26

**Both items are settled. Item 1 is SHIPPED; Item 2's premise is FALSE and its
cleanup is REFUSED on security grounds — do not re-attempt it.**

**Item 1 — DONE (already in the tree).** The name-signal hardening this card
asks for exists: `validate_security._CPV_IS_RUNNING_CPV` (`:1228`, set in
`_set_cpv_self_scan` at `:1413`) is a PATH comparison — true only when the
scanned plugin root IS the running validator's own directory — and `:1800`
gates the pattern-source branch on it. A spoofed `plugin.json` name cannot
satisfy a path identity, so the card's acceptance ("a third-party plugin shaped
like CPV does NOT flip self-scan mode") holds. The comment block at `:1780-1798`
documents exactly this, including the pre-RT3 hole it replaced.

**Scope of that verification, stated honestly:** the GATE was verified by
reading its assignment — a path comparison cannot be satisfied by a spoofed
manifest name, which is sound on the code. The card's stated acceptance also
asks for *two-sided tests* (genuine-CPV → mode on; lookalike → mode off), and
those were NOT written here. CLAUDE.md's v2.145.0 and v4.0.0 entries already
describe this gate as shipped and non-spoofable, so the conclusion is
well-supported — but if anyone wants the acceptance criterion satisfied
literally, the missing artifact is that test pair, not a code change.

**Item 2 — REFUSED, premise falsified.** The card asserts that after
TRDD-b8c6d04f "neither … has a live caller". That is **false as of this
verification**: `validate_security.py:1803` contains

```python
if not _is_self_scan_eligible(file_path):
    return False
```

inside `cpv_self_scan_skip_line`, and the comment at `:1789` names it
**defense-in-depth** — it NARROWS the skip to files that look like
CPV-internal pattern sources. Deleting it would therefore **widen a self-scan
exemption**, i.e. weaken a security gate to satisfy a cleanup card. That is
refused outright per the never-suppress/never-relax rule.

**The eligibility check is NOT redundant with the `_CPV_IS_RUNNING_CPV` gate
above it** — the two test different objects. `_CPV_IS_RUNNING_CPV` is a
per-RUN boolean about WHICH PLUGIN is being scanned;
`_is_self_scan_eligible(file_path)` is per-FILE. A per-run flag cannot subsume
a per-file filter: delete the call and `is_pattern_source_line` would be
consulted on EVERY file of the running CPV during an in-tree edit, not just its
pattern sources — strictly wider, and the comment at `:1780` states the rule
that forbids exactly that ("keys on attacker-controllable signals … must
NEVER be consulted on a file that a third-party plugin could supply").

Verified first-hand at the source, not from the delegated report: the helper is
defined at `:1891`, called at `:1803`. The
`TestSelfScanEligibilityLockstep` invariant in `test_audit_caches_token.py` is
**live and correct**, not obsolete — evidenced by that test RUNNING AND PASSING
in this session's 51-passed batch, not by the mirror comments in
`_plugin_compute_hashes.py` (a comment documents intent, never agreement). A
delegated worker attempted the deletion, hit the same wall, and reverted
cleanly (confirmed: `git status --porcelain` on all six candidate files is
empty). Report:
`reports/board-drain-impl/20260826_042844+0200-4243a768-impl.md`.

**Why the card was wrong (verified):** it conflates `cpv_self_scan_skip` (fully
SHA-gated, no eligibility call) with `cpv_self_scan_skip_line` (the
in-tree-edit path, which DOES call it). No origin story is offered for how the
error arose — that would be speculation about an author's intent, and the
conflation stands on its own without one.

**NEXT ACTION:** none. Item 1 needs no work; Item 2 must not be done. If a
future maintainer still finds the helper confusing, the correct fix is a
clarifying comment at `:1891` — never removal.

**Column — why `cancelled`, and why NOT `testing` or `complete`.** Two wrong
answers were tried before this one; both were untrue columns of opposite kinds.

- **`testing` claimed an activity nobody performs** — nothing here is being
  verified, because Item 1 needed no change and Item 2 must not be done.
- **`complete` claimed a delivery nobody performed** — this card shipped
  NOTHING. Item 1 was already in the tree before the card was worked, and Item
  2 was explicitly REFUSED. Marking it done would also FREEZE an
  archive-eligible record that still names spec-required work, forcing the next
  maintainer to open a new card to do work this one already specifies.
- **`cancelled` is the honest value**: the work is withdrawn — Item 2 is
  permanently declined on security grounds, and Item 1 requires nothing from
  this card. This comes from the rule's own definition (`trdd-approval-tiers`:
  *"cancelled — the work is withdrawn, no longer wanted"*), not from counting
  sibling cards. The in-repo precedent TRDD-V7K2QF8M was then read in full to
  confirm the shape matches: it was attempted, shipped, went RED, was reverted
  and abandoned — its own STATE even planned `failed` first. Different route,
  same category, because `failed` is *retryable and open* and "giving up on a
  failed TRDD = cancel → archived".
- **NOT `refused`**, deliberately: that value describes a PROPOSAL declined at
  intake, which lives in `design/refused/` and never entered the pipeline. This
  card was approved, sat in `design/tasks/`, and was worked. Declining one ITEM
  inside an approved card is not the same shape.
- **NOT a split** (Item 1 → its own `completed` card, Item 2 → cancelled): a
  `completed` card for Item 1 would repeat the `complete` error one level down,
  because Item 1 was shipped by OTHER work, not by this card. The objection to
  the split is that it launders the same false delivery claim into a new
  record — not that it costs more.

**REAL FOLLOW-UP WORK THIS CARD LEAVES BEHIND — do not lose it.** Item 1's
acceptance criterion asks for **two-sided tests** (genuine-CPV → self-scan mode
ON; a third-party lookalike → mode OFF). Those were **never written**. The gate
mechanism was verified by reading `_CPV_IS_RUNNING_CPV`'s path-identity
assignment, which is sound evidence about the gate but is NOT that test pair.
Anyone wanting the acceptance criterion satisfied literally should open a fresh
card for those two tests — the code needs no change, only coverage.

Security-hygiene follow-up to [[exhaustive-sha-self-recognition]]
(TRDD-b8c6d04f). Two independent items; both are defense-in-depth /
cleanup, NOT functional security holes (the SHA gate already makes a skip
require a hash match, and spoofing is defeated by `_set_cpv_self_scan`'s
GitHub-anchored trust).

## Item 1 — harden the `is_cpv_self_scan` name-signal (Category-C)

### Background

`is_cpv_self_scan` is the cheap name/signature gate that flips the
validator into self-scan MODE. The actual per-file skip is SHA-gated
(`cpv_self_scan_skip` — only a manifest hash match skips), and spoofing is
defeated upstream by `_set_cpv_self_scan` (trusts the local manifest ONLY
for the running, GitHub-verified CPV; otherwise fetches the canonical
manifest from GitHub and REFUSES to skip if it can't reach it). So the
name-signal cannot, by itself, grant a skip.

### The residual concern

The name-signal is still a *mode* trigger derived from path/name shape. A
plugin that mimics CPV's directory shape (a `scripts/validate_*.py`, a
`.claude-plugin/plugin.json` with `name == "claude-plugins-validation"`,
etc.) can flip CPV into self-scan MODE for ITS OWN tree. Even though no
file gets skipped without a SHA match, mode-flipping is an unnecessary
attack surface and a source of confusion.

### Proposed hardening (deterministic, no opt-out)

- Require MULTIPLE corroborating signals before `is_cpv_self_scan` returns
  True (name + a structural fingerprint, e.g. the presence of the two
  manifest files AND a `scripts/validate_security.py` whose module-level
  symbol set matches a known fingerprint), rather than any single
  name/path match.
- Consider tying the MODE flip to the same GitHub-anchored identity check
  `_set_cpv_self_scan` already performs, so mode and skip share one trust
  root.
- Keep it fail-closed: ambiguous identity → NOT self-scan → scan everything.

### Acceptance

- A third-party plugin shaped like CPV does NOT flip self-scan mode.
- The running, GitHub-verified CPV still self-scans cleanly (0/0/0/0).
- Two-sided tests (genuine-CPV → mode on; lookalike → mode off).

## Item 2 — remove the dead `*_self_scan_eligible` helpers

### Background

After TRDD-b8c6d04f changes 1+2, neither
`_plugin_compute_hashes.is_self_scan_eligible` nor
`validate_security._is_self_scan_eligible` has a live caller —
`compute_manifest` hashes ALL git-tracked files and `cpv_self_scan_skip`
is fully SHA-gated. They survive only as:

- a re-export in the legacy `compute_cpv_self_hashes.py` shim, and
- the "lockstep" audit invariant in `tests/test_audit_caches_token.py`
  (`TestSelfScanEligibilityLockstep`, cache #3) plus surface assertions in
  `test_audit_integrity_nits.py` and `test_legacy_integrity_fallback.py`.

The lockstep invariant ("the two implementations must agree") is now
**obsolete** — neither drives the skip decision, so they no longer need to
agree.

### Proposed cleanup

1. Delete `_is_self_scan_eligible` from `validate_security.py` and
   `is_self_scan_eligible` from `_plugin_compute_hashes.py`.
2. Drop the re-export from `compute_cpv_self_hashes.py` `__all__` + import.
   (Or retire the whole shim — its own removal was deferred from v2.53.0;
   evaluate doing it here.)
3. Delete `TestSelfScanEligibilityLockstep` (cache #3) from
   `test_audit_caches_token.py`; remove the surface assertions in
   `test_audit_integrity_nits.py` / `test_legacy_integrity_fallback.py`.
4. Grep for every other reference (comments in both modules that say "stay
   in lockstep with …") and update them.

### Risk / verify

- These functions look like they gate self-scan but do not — leaving them
  is a security-readability hazard a future maintainer could trip on. That
  is the motivation; there is no runtime risk in leaving OR removing them.
- After removal: full suite green, CPV self-scan 0/0/0/0, manifest still
  exhaustive (the removal changes only `.py` content → regen manifest).

## Why deferred from TRDD-b8c6d04f

Both items are hygiene / defense-in-depth, not functional security gaps
(verified: `cpv_self_scan_skip` is SHA-gated and ignores the name-signal
for the actual skip). The exhaustive-SHA TRDD's acceptance criteria are met
without them. Splitting them out keeps that TRDD's diff focused and avoids a
multi-file audit-test teardown at the tail of a long working session.

## Approval log

- 2026-08-26T04:58:22+0200 — CANCELLED by the project's own Claude under the
  USER's standing directive ("complete all pending tasks and TRDDs… decide
  yourself, base decisions on verified facts"). Reason: Item 1 was already
  shipped by other work and needs nothing from this card; Item 2's specified
  cleanup is permanently REFUSED because deleting
  `validate_security._is_self_scan_eligible` would widen a self-scan exemption
  (it is a per-FILE narrowing the per-RUN `_CPV_IS_RUNNING_CPV` gate cannot
  subsume). Nothing was delivered BY this card, so `completed` would be false.
  **Surfaced to the USER for veto in the same session.** Scope of the rule,
  stated precisely: `trdd-approval-tiers` §"Archival protocol" (:275-278)
  REQUIRES this Approval log entry for a `cancelled` card — that is directly on
  point and was genuinely missing. The further judgement that cancellation is
  *approval-worthy* is an ANALOGY, not rule text: `manager-approval-defaults`
  lists `<any> → failed` as non-exempt ("abandoning a TRDD; permanent
  decision") and names the risk "Agent silently abandons a TRDD" (:290), and
  cancellation abandons spec'd work permanently in the same way — but the
  non-exempt list does not name `cancelled` literally. Recorded as inference so
  a later reader does not cite this card as proof of a rule that does not say
  it.
