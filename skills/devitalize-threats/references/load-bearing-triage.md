# Load-Bearing Triage — dead-vs-live decision procedure

## Table of Contents

- [The two gate questions](#the-two-gate-questions)
- [The classification table](#the-classification-table)
- [The ambiguous-case rule](#the-ambiguous-case-rule-load-bearing)
- [What a FLAGGED entry must contain](#what-a-flagged-entry-must-contain)
- [Why a flag beats a broken transform](#why-a-flag-beats-a-broken-transform)
- [Two-perspective close](#two-perspective-close-report-ending)

Before any transform, classify the flagged construct. Devitalization is
only legitimate when the construct can be proven *not* to be load-bearing
live behavior. When it cannot, the finding is **flagged to the user**, not
rewritten. Silently breaking a feature to clear a finding is the single
worst outcome and is forbidden.

This mirrors plugin-fixer Guardrail 1 ("never blindly purge dead code")
and Guardrail 2 ("don't over-convert"), applied to security shapes.

---

## The two gate questions

- **Q1 — Is this code LOAD-BEARING?** Does live behavior depend on this
  construct *executing*, reachable from any entry point?
- **Q2 — Can the flagged construct be expressed as data with NO path to a
  sink?**

Reachability (for Q1) must be checked against EVERY entry point, with the
same rigor as plugin-fixer Guardrail 1:

- `hooks/hooks.json` (and any `settings.json` `hooks` key)
- slash command dispatch (`commands/*.md`)
- agent dispatch (`agents/*.md`, `subagent_type:` references)
- dynamic import / `importlib` / `__import__`
- glob loader / plugin auto-discovery
- re-export / barrel file
- test files and mocks

If a caller exists from *any* of these, the construct is **live**.

---

## The classification table

| Class | Test (must hold) | Action |
|-------|------------------|--------|
| **Documentation** | host file is a non-loadable doc surface (`.md` in `references/` / `docs/`, README) AND the construct is illustrative prose / fence | Devitalize (B / D) — always safe; docs never execute |
| **Detector signature** | the literal is a *needle compared against scanned content*, defined in a rules / patterns / signatures table, never spread into argv / a sink | Devitalize to raw-string (A) |
| **Dead code** | the enclosing function / block is provably unreachable from EVERY entry point (see list above) — same rigor as Guardrail 1 | Devitalize (C / D) or remove the dead sink |
| **Live + constrainable** | reachable, but the dynamic input has a *finite known* value set | Devitalize to allow-map dispatch (C) — also hardens it |
| **Live + irreducible** | reachable AND the feature *is* the execution (REPL, sandbox runner, real installer, genuine shell orchestration) | **DO NOT devitalize.** Flag to user. |
| **Real leaked secret** | trufflehog *verifies* the credential as live | **DO NOT just edit the doc.** Flag: rotate + purge history. |
| **Ambiguous** | cannot prove dead, cannot prove the value set is finite, unsure if load-bearing | **DO NOT devitalize.** Flag to user with the specific uncertainty. |

---

## The ambiguous-case rule (load-bearing)

**When in doubt, treat the construct as live / load-bearing — flag, don't
transform.** A false "this was dead" that breaks a feature is far worse
than a flagged finding the user resolves manually. The devitalizer never
guesses that something is safe to neutralize; it either *proves* the
construct inert-able (one of the first four rows) or it flags.

Specifically, flag (do not transform) when ANY of these holds:

- Reachability cannot be ruled out from every entry point.
- The dynamic value set cannot be shown finite and known.
- The construct is the plugin's intended execution feature.
- A secret is verified live (rotate + purge, never hide).
- The imperative prose is a *legitimate* instruction the plugin's own
  agent depends on (T7 load-bearing case).

---

## What a FLAGGED entry must contain

For each flagged finding, the report records — not an edit — but an
actionable choice for the user:

- the `file:line` + `rule_id` + matched span,
- the class (live-irreducible / real-secret / ambiguous + the specific
  uncertainty),
- the precise remediation the user chooses between, e.g.:
  - live shell-exec: "harden it (argv-list + `shell=False`, validated
    input) **or** accept the finding — the devitalizer will not silently
    break working behavior",
  - real secret: "rotate the credential and purge it from git history;
    editing the doc would hide a genuine leak",
  - intentional code-execution feature: "this cannot be devitalized
    without removing the feature; accept the finding or redesign".

---

## Why a flag beats a broken transform

- A flagged finding costs the user a manual decision; a broken transform
  costs them a silently regressed plugin discovered later in production.
- The devitalizer's entire value is conditional on never breaking working
  plugins — a single silent feature break destroys trust in every clear
  it produces.
- The user owns genuine security decisions (keep a real installer, run a
  real REPL); the devitalizer surfaces them precisely instead of making
  them invisibly.

---

## Two-perspective close (report ending)

Every devitalization report ends with both views so the user decides the
tradeoff:

- **Perfectionist:** the remaining flagged-not-devitalized findings and
  *why* each is load-bearing (so nothing looks "missed").
- **Pragmatist:** the plugin now passes the gate honestly, with the
  irreducible items surfaced for an explicit human decision (so the user
  knows the gate-pass is real, not a mute).
