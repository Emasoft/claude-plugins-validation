---
name: devitalize-threats
description: >
  Transformation catalog for converting flagged execution-class security
  findings into provably-inert data — so a plugin passes CPV's security
  gate by neutralizing the code's executable shape, never by suppressing
  a rule or relaxing --strict. Maps each threat shape (live os.system /
  subprocess(shell=True), pipe-to-shell install docs, eval/exec of a
  string, backtick command substitution, hardcoded bearer tokens in docs,
  detection-pattern signatures) to a before/after rewrite plus the
  inert-proof the scanner recognizes. Use when a plugin's security scan
  reports execution-class findings that must be cleared by making the
  code provably inert rather than by suppressing rules. Used dynamically
  via the-skills-menu by the plugin-devitalizer agent.
user-invocable: false
---

# Devitalize Threats — execution-shape to inert-data transformation catalog

## Overview

Devitalization rewrites the *shape* of a flagged construct so the threat
itself disappears — not the finding. A construct stops firing because it
is now provably data that can never reach an execution sink, verified by
the same unchanged scanner at the same `--strict` level.

The one rule: **provably-passive or don't touch it.** Never mute a rule,
never add an ignore/skip flag, never weaken `--strict`, never edit a CPV
validator. The only acceptable "clear" is the scanner no longer firing
*because the shape is now inert* — not because it was silenced.

This is the author-side counterpart to CPV's scanner-side inert-proof
discriminators. The scanner already auto-clears a few shapes it can
mechanically prove harmless (raw-string regex signatures, defanged doc
illustrations, placeholder secrets). Devitalization rewrites the
plugin's source so a flagged construct *matches one of those shapes*, or
is removed/nominalized so no rule fires at all.

## Prerequisites

- A security report from `validate_security.py` + native skillaudit
  (the plugin-devitalizer agent produces it via the isolation launcher).
- The flagged file open, with each finding's `file:line` + `rule_id` +
  matched span.

## The two questions before any transform (decision gate)

- **Q1 — Is this code LOAD-BEARING?** Does live behavior depend on this
  construct *executing*, reachable from any entry point (hooks.json,
  command/agent dispatch, dynamic import, glob loader, re-export, test)?
- **Q2 — Can the flagged construct be expressed as data with NO path to
  a sink?** A detector needle, a doc illustration, a dead example, or a
  dynamic value constrained to a finite known set.

Routing (full table in the load-bearing-triage reference, linked under Resources):

| Class | Action |
|-------|--------|
| Documentation (non-loadable doc surface, illustrative) | Devitalize — docs never execute |
| Detector signature (needle compared to content, never spread to a sink) | Devitalize to raw-string |
| Dead code (provably unreachable from EVERY entry point) | Devitalize or remove the dead sink |
| Live + constrainable (reachable, finite known value set) | Devitalize to allow-map dispatch (also hardens) |
| Live + irreducible (the feature IS the execution) | DO NOT devitalize — flag to user |
| Real leaked secret (verified live by trufflehog) | DO NOT just edit the doc — flag: rotate + purge |
| Ambiguous (can't prove dead/finite/non-load-bearing) | DO NOT devitalize — flag to user |

**Ambiguous-case rule:** when in doubt, treat as live/load-bearing.
A false "this was dead" that breaks a feature is far worse than a
flagged finding the user resolves by hand.

## The four inert forms

Every transform moves the construct into one of four shapes:

- **(A) Raw-string signature** in a clearly-named rules/patterns table —
  the scanner proves it is a detector needle, not a live argument
  (`_match_inside_raw_string`, `_DETECTOR_SIGNATURE_SKIP_RULES`,
  skillaudit `safe_literal` / re-pattern-literal verdict).
- **(B) Annotated non-runnable illustration** — defanged: broken pipe,
  placeholder URL, `text` fence instead of `bash`. No executable token
  the rule matches remains.
- **(C) Data constant + dispatch on a fixed allow-map** — the dynamic
  string never reaches `exec`/`eval`/shell; only a vetted in-repo literal
  runs. This is also a genuine security improvement.
- **(D) Removal / nominalization** — the matching token is gone (dead
  sink removed; imperative prose reworded to a description).

## Instructions

1. Read the live security report; for each finding note `file:line`,
   `rule_id`, and the matched span. Transform only what actually fires.
2. Run the decision gate (Q1 load-bearing? Q2 expressible as data?) and
   classify the finding via the routing table above.
3. If the class is live-irreducible, a real leaked secret, or ambiguous,
   stop — flag it to the user with the precise remediation choice
   instead of rewriting it.
4. Otherwise pick the least-invasive inert form (A raw-string, B
   defanged illustration, C allow-map dispatch, D removal /
   nominalization) and look up the matching T# recipe.
5. Apply one minimal edit to the flagged span only — never batch-rewrite
   a file blind.
6. Re-run the same scanner at the same `--strict` level; confirm the
   finding is gone AND no new finding appeared.
7. Record the outcome (finding → class → T# applied OR FLAGGED + reason
   → re-scan result) and close the report with the two-perspective view.

## Transformation catalog (T1–T9)

The full per-shape recipes — BEFORE / AFTER / WHY-INERT / VERIFY /
SAFE-vs-BREAKS — live in the transform-catalog reference (linked under
Resources). Summary:

| # | Threat shape | Inert form | One-line transform |
|---|--------------|-----------|--------------------|
| T1 | Detection-pattern / signature lines | A | Raw-string the needles in a DATA-only table |
| T2 | Doc pipe-to-shell install one-liner | B | Defang (text fence, elide pipe, drop URL) or split download/review/run |
| T3 | Hardcoded bearer token / credential in docs | D | Replace with an obvious `<YOUR_API_TOKEN>` placeholder |
| T4 | Live shell-exec sink that is DEAD / EXAMPLE code | C/D | Remove the dead sink; keep teaching content as comment + data |
| T5 | `eval` / `exec` of a string | C | Fixed allow-map dispatch on vetted callables |
| T6 | Backtick command-substitution in prose | D | Quote as English ("the id command"), not a shell token |
| T7 | "The agent MUST execute …" prose | D | Nominalize: describe what happens, drop the imperative |
| T8 | Destructive-verb enumeration | D | One accurate non-stacked statement |
| T9 | Dynamic `setattr`/`getattr` attribute name | C | Constrain to a fixed allow-set before the sink |

## Inert-proof recognition (what the scanner accepts as "provably data")

The scanner-side discriminators a transform must satisfy — mapped to the
exact mechanism — are in the inert-proofs reference (linked under
Resources). Key points:

- A raw-string `r"..."` literal is the inert-proof for detector
  signatures; a real CLI arg is a normal string, never raw.
- A placeholder secret matches no entropy/prefix detector.
- **Demote is NOT clear under `--strict`.** Skillaudit *demotes* several
  execution-class matches in `references/*.md` to NIT rather than fully
  suppressing — and `--strict` blocks on NITs. Merely relocating a threat
  token from `SKILL.md` into a reference does not clear the gate; it turns
  a CRITICAL into a *blocking NIT*. Transform the **shape** so the rule
  does not fire (or fires-then-skips-as-inert), never just relocate.

## Cross-cutting rules

1. **Check the `rule_id` and file kind first.** Several shapes are
   *already* cleared on certain surfaces. Read the live report; transform
   only what actually fires — don't churn an already-passing line.
2. **Prefer the least-invasive inert form** that clears the finding.
   Forms A and C are also security improvements (preferred when the
   construct is real-but-constrainable); B and D-removal are for
   documentation and dead code.
3. **One finding, one minimal edit, one re-scan.** Never batch-rewrite a
   file blind; transform the specific flagged span, re-scan, confirm the
   finding is gone AND no new finding appeared.

## When devitalizing is WRONG (do not break real plugins)

Live install scripts, real network clients, genuine subprocess
orchestration, intentional code-execution features (a REPL, a sandbox
runner), and verified leaked secrets are **load-bearing or real** — they
are FLAGGED to the user with a precise remediation choice, never silently
rewritten. Silently breaking a feature to clear a finding is the single
worst outcome and is forbidden. See the load-bearing-triage reference
(linked under Resources).

## Output

A per-finding before/after diff and a report listing: finding -> class ->
transform applied (T#) OR "FLAGGED: load-bearing, reason" -> re-scan
result. The report ends with a two-perspective close (what a
perfectionist would still object to vs what a pragmatist accepts).

## Examples

- Detector-signature finding.
  - Input: RC-46 fires on a scanner's own needles stored in a rules table.
  - Output: class detector-signature; Form A — express the needles as
    raw-string literals in a DATA-only table; re-scan skips them as
    detector signatures (recipe T1).
- Docs install one-liner.
  - Input: a docs page shows a pipe-to-shell install line (SUPPLY_CHAIN).
  - Output: class documentation; Form B — defang to a `text` fence with
    the pipe elided and the URL dropped, or split into download → review
    → run; re-scan finds no pipe-to-shell token (recipe T2).
- Reachable shell-exec sink.
  - Input: a live shell-exec sink reachable from a hook.
  - Output: class live-irreducible; do NOT rewrite — flag to the user
    (harden with an argv list + `shell=False`, validated input, or
    accept the finding). The devitalizer never silently breaks behavior.

## Error Handling

- **Finding still fires after the transform.** The shape was not made
  inert (often a demote-to-NIT relocation rather than a real shape
  change, which `--strict` still blocks on). Re-read the recipe's
  WHY-INERT and change the shape, not the location.
- **A new finding appears after the edit.** Revert the edit and
  re-transform the original span more narrowly; never leave a net-new
  finding behind.
- **Cannot prove the construct dead, finite, or non-load-bearing.** Treat
  it as live and flag it to the user — a false "this was dead" that
  breaks a feature is worse than a flagged finding resolved by hand.
- **trufflehog verifies a value as a live secret.** Refuse the
  placeholder rewrite and escalate "rotate the credential and purge git
  history" — editing the doc would hide a genuine leak.

## Resources

- [transform-catalog](references/transform-catalog.md) — the full T1–T9 per-shape recipes.
  > T1 Signature lines · T2 Install one-liner · T3 Credential in docs · T4 Dead exec sink · T5 String eval/exec · T6 Backtick in prose · T7 Instruction prose · T8 Verb enumeration · T9 Dynamic attribute · Cross-cutting rules
- [inert-proofs](references/inert-proofs.md) — the scanner-side discriminators map.
  > Raw-string signature · safe_literal verdict · Exec in style/markup · Placeholder secret · Defanged illustration · Doc-only suppression · Removal / nominalization · Quick reference
- [load-bearing-triage](references/load-bearing-triage.md) — the dead-vs-live
  decision procedure and the ambiguous-then-flag rule.
  > The two gate questions · The classification table · The ambiguous-case rule · What a FLAGGED entry must contain · Why a flag beats a broken transform · Two-perspective close
