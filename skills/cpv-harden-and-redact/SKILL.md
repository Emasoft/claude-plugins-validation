---
name: cpv-harden-and-redact
description: >
  Catalog for fixing security-scan LEAK and MISSING-SAFEGUARD findings
  without loss of functionality — redacting secrets (runtime-reading the
  needed ones from env / GitHub vars / OS keychain) and implementing
  missing safeguards: safe config parsing, input sanitization, correct
  launch/deploy parameters, safe file loading, and a by-code-only
  prompt-injection pre-scan. Maps each finding to a before/after rewrite
  plus a re-scan. Use when a security scan reports leaked secrets or
  missing safeguards to redact or harden rather than suppress. Loaded via
  cpv-the-skills-menu by the cpv-plugin-leaks-preventer-agent.
user-invocable: false
---

# Harden and Redact — leak-redaction and safeguard-hardening catalog

## Overview

This catalog rewrites two classes of security finding so the threat
itself disappears — not the finding. A **leak** is removed (or the value
moved to a runtime read), and a **missing safeguard** is implemented, both
verified by the same unchanged scanner at the same `--strict` level.

The one rule: **remove the leak / add the safeguard, or flag it — never
weaken the scan.** Never mute a rule, never add an ignore/skip flag, never
relax `--strict`, never edit a CPV validator. The only acceptable "clear"
is the scanner no longer firing *because the secret is gone* or *because
the safeguard now exists* — not because it was silenced.

This is the leaks-and-hardening counterpart to the `cpv-devitalize-threats`
catalog. Devitalize neutralizes the *executable shape* of a shipped
threat; cpv-harden-and-redact removes *exposed data* and adds *missing
defenses*. A plugin can need both — the two are complementary, not
overlapping (full bucket map in the parent spec; the live report's
`rule_id` decides which catalog a finding belongs to).

## Prerequisites

- A security report from `validate_security.py` + native skillaudit
  (the cpv-plugin-leaks-preventer-agent agent produces it via the isolation
  launcher).
- The flagged file open, with each finding's `file:line` + `rule_id` +
  matched span + bucket (B leak / C safeguard / both).

## The decision gate (per finding)

Run these questions in order before any edit. The gate decides redact vs
runtime-read vs harden vs flag.

- **Q1 — is this a SECRET (leak) or a MISSING SAFEGUARD?**
  A leak is a credential, token, key, webhook, or private path committed
  in source. A missing safeguard is an absent defense on a path that
  processes data — TLS off, no SSRF guard, an unsafe parser, no input
  sanitization, no prompt-injection pre-scan.
- **Q2 (leak) — is the secret needed at runtime?**
  No → redact to an obvious placeholder (the value was never needed).
  Yes → redact the literal AND replace it with a runtime read from the
  environment (env var / exported shell var / GitHub repo+Actions
  variable / OS keychain), with a fail-fast when unset.
- **Q2 (safeguard) — can the safeguard be added without changing
  behavior?**
  Yes → apply the least-invasive safe form (safe parser, sanitizer,
  containment, correct launch params). No / ambiguous → FLAG, do not
  rewrite.
- **Q3 (leak reverse-case) — is the secret already committed AND live?**
  If an external scanner verifies the value as live, redacting the
  working-tree file is NOT enough — the value is already in git history.
  FLAG it: rotate the credential and purge git history. Do not silently
  edit a file to hide a real leak.

**Ambiguous-case rule:** when a finding cannot be proven a benign example,
or a path cannot be proven free of untrusted input, treat it as real and
FLAG it. A false "this was just an example" that leaks a live credential,
or a "this never sees untrusted input" that ships a real hole, is far
worse than a flagged finding the user resolves by hand.

## Instructions

1. Read the live security report; for each finding note `file:line`,
   `rule_id`, the matched span, and its bucket (B leak / C safeguard /
   both). Fix only what actually fires.
2. Run the decision gate (Q1 leak or safeguard? Q2 needed at runtime? /
   safely fixable? Q3 already committed and live?) and classify the
   finding.
3. If the finding is a public example, a path with no untrusted input, an
   intentional test fixture, or a verified-live committed secret, stop —
   FLAG it to the user with the precise remediation choice (rotate + purge
   for a live secret) instead of rewriting it.
4. Otherwise pick the least-invasive fix and look up the matching recipe:
   redact to a placeholder (B), runtime-read (B1–B4), relativize a path
   (B5), or add the one safeguard (C1–C5).
5. Apply one minimal edit to the flagged span only — never batch-rewrite a
   file blind.
6. Re-run the same scanner at the same `--strict` level; confirm the
   finding is gone AND no new finding appeared.
7. Record the outcome (finding → bucket → recipe applied OR FLAGGED +
   reason → re-scan result) and close the report with the two-perspective
   view. Note any execution-class findings still present and point at the
   `cpv-plugin-devitalizer-agent` agent.

## PART B — secrets: redact / runtime-read (summary)

Full recipes — BEFORE / AFTER / WHY-SAFE / VERIFY / NO-FUNCTIONALITY-LOSS,
per file kind — live in the redact-recipes reference (linked under
Resources). Summary:

| # | Leak shape | Fix | One-line action |
|---|------------|-----|-----------------|
| B1 | Literal API key / token in source | runtime-read | Remove the literal; read it from the environment with a fail-fast when unset |
| B2 | Secret needed in a GitHub Action | GitHub vars | Reference a repo secret / a repo+Actions variable; never inline the value |
| B3 | Secret in a committed `.env` / config file | env hygiene | Remove from the committed file, gitignore it, document the var in an example file with a placeholder |
| B4 | Secret stored in the OS keychain | keychain read | Read it at runtime from the platform keychain, fail-fast on miss |
| B5 | Leaked private path / username | relativize | Replace the absolute path with a plugin-root or home-relative reference |
| B6 | Verified-live committed secret | ROTATE + purge | Refuse the redact-only path; flag rotate the credential and purge git history |

The B contract: **redact the literal**, and if the value is genuinely
needed at runtime, **read it at runtime** from the environment. If a
secret is already committed and an external scanner verifies it live,
**rotate + purge history** — redaction alone leaves it in the history.

## PART C — safeguards: harden (summary)

Full recipes — BEFORE / AFTER / WHY-SAFE / VERIFY / NO-FUNCTIONALITY-LOSS
— live in the harden-recipes reference (linked under Resources). Summary:

| # | Missing safeguard | Fix | One-line action |
|---|-------------------|-----|-----------------|
| C1 | Unsafe launch / deploy / network params | safe params | TLS verification on; SSRF allow-list + private-IP block; DNS-rebind host pin; subprocess argv list with shell disabled |
| C2 | Unsafe config parse | safe parser | The safe loader per format — YAML safe-load, toml, size-capped json, cfg/ini, plist, entity-disabled XML |
| C3 | Missing input sanitization | sanitize | Parameterized SQL; output-encode for XSS; bounded regex + length cap for ReDoS; allow-map for dynamic dispatch |
| C4 | Unsafe file load | safe load | No untrusted deserialization; path containment under a base directory |
| C5 | No prompt-injection pre-scan | pre-read guard | A by-code-only scan that runs BEFORE any agent reads untrusted content, neutralizing or refusing on injection markers |

**C5 — the by-code-only prompt-injection pre-scan (the user-emphasised
item):** a PURE-CODE (no-LLM) scan that runs BEFORE any agent reads
untrusted content. It scans the to-be-read content for the same injection
markers CPV's own prompt-injection rules catch, then either neutralizes
the markers or refuses-and-flags. Wiring it as a pre-read guard hardens
the plugin's OWN agent/skill flow against indirect prompt injection — the
plugin defends itself, it does not merely pass the scan. Because the
guard runs first, no agent ever reads raw untrusted content. Full recipe
(with the marker set and the pre-read wiring) is in the harden-recipes
reference, section C5.

Each recipe carries a **NO-FUNCTIONALITY-LOSS** note: a runtime read must
fail-fast (not silently no-op) when unset; a safe parser must accept the
same valid inputs; a sanitizer must not reject legitimate data; a
containment check must not block legitimate paths.

## Cross-cutting rules

1. **Check the `rule_id` and file kind first.** Several shapes are
   *already* cleared on certain surfaces (a context-suppressed write in a
   Dockerfile, a placeholder in a doc). Read the live report; fix only
   what actually fires — don't churn an already-passing line.
2. **Least-invasive fix.** Redact to a placeholder before anything; add
   the single missing safeguard rather than refactoring the file; preserve
   the same valid inputs and outputs.
3. **One finding, one minimal edit, one re-scan.** Never batch-rewrite a
   file blind; fix the specific flagged span, re-scan, confirm the finding
   is gone AND no new finding appeared.
4. **Never lie about behavior.** Describe what a runtime read or a safe
   parser does precisely; do not claim a fix preserves behavior when it
   silently changes it.

## When hardening / redacting is WRONG (do not break real plugins)

Some findings are real but must NOT be rewritten:

- A "secret" that is genuinely a **public example** value (a published
  sample key, a documented test fixture) — confirm it is public, then
  leave it as an obvious placeholder; do not invent a runtime read for a
  value nobody uses.
- A "missing safeguard" on a path that **never sees untrusted input** (a
  parser that only reads the plugin's own bundled, trusted config) — adding
  a heavy guard there is churn, not security; flag the reasoning.
- An **intentional insecure-by-design test fixture** (a fixture that
  deliberately ships an unsafe shape so the plugin's own tests can detect
  it) — flag it; rewriting it would break the test.
- A **verified-live committed secret** — flag rotate + purge history;
  editing the working-tree file would hide a genuine leak.

In every such case, FLAG the finding with the precise remediation choice
instead of rewriting it. Silently breaking a feature, or hiding a real
leak, to clear a finding is the single worst outcome and is forbidden.

## Output

A per-finding before/after diff and a report listing: finding → bucket
(B/C/both) → fix applied (redact / runtime-read / harden: recipe) OR
"FLAGGED: reason" (intended / public-example / verified-live → rotate +
purge) → re-scan result. The report ends with a two-perspective close
(what a perfectionist would still object to vs what a pragmatist
accepts). Any execution-class (devitalize) findings still present are
noted with a pointer to the `cpv-plugin-devitalizer-agent` agent — the two jobs are
complementary.

## Error Handling

- **Finding still fires after the fix.** The leak was relocated, not
  removed, or the safeguard does not actually break the flagged path.
  Re-read the recipe's WHY-SAFE and change the shape, not the location.
- **A new finding appears after the edit.** Revert the edit and re-apply
  more narrowly; never leave a net-new finding behind.
- **Cannot prove a value is a public example, or a path is free of
  untrusted input.** Treat it as real and flag it — a false "just an
  example" or "never untrusted" is worse than a flagged finding resolved
  by hand.
- **An external scanner verifies a value as a live secret.** Refuse the
  redact-only rewrite and escalate "rotate the credential and purge git
  history" — editing the file would hide a genuine leak.

## Examples

- Hardcoded API key needed at runtime.
  - Input: a credential literal in source that the plugin actually uses.
  - Output: bucket B; recipe B1 — remove the literal and read the value
    at runtime from the environment with a fail-fast when unset; re-scan
    finds no credential literal (full recipe B1).
- Unsafe config parse on user-supplied input.
  - Input: a finding on a full YAML loader reading a file the user
    supplies.
  - Output: bucket C; recipe C2 — switch to the safe YAML loader, which
    accepts the same documents but constructs no arbitrary objects;
    re-scan finds no unsafe-parse shape (full recipe C2).
- Verified-live committed token.
  - Input: an external scanner verifies a committed token as a live
    secret.
  - Output: bucket B reverse-case; recipe B6 — do NOT just edit the file;
    FLAG it: rotate the credential and purge git history. Redacting the
    working tree leaves the secret in history.

## Resources

- [redact-recipes](references/redact-recipes.md) — the full Part B
  per-file-kind redaction and runtime-read recipes.
  > B1 env-read · B2 GitHub vars · B3 .env hygiene · B4 OS keychain · B5 private-path · B6 rotate + purge · Per-file-kind table · Cross-cutting rules
- [harden-recipes](references/harden-recipes.md) — the full Part C
  safeguard-hardening recipes.
  > C1 launch / deploy params · C2 safe config parse · C3 input sanitization · C4 safe file load · C5 prompt-injection pre-scan · Cross-cutting rules
