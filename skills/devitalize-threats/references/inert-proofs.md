# Inert-Proofs — scanner-side discriminators map

## Table of Contents

- [Raw-string signature](#1-raw-string-signature-r)
- [safe_literal verdict](#2-safe_literal--re-pattern-literal-classifier-verdict)
- [Exec in style/markup](#3-_style_lang_inert_exec_rules--exec-mentions-in-non-executing-stylemarkup-languages)
- [Placeholder secret](#4-placeholder-secret-your_api_token-token-api_token)
- [Defanged illustration](#5-defanged-doc-illustration-text-fence-elided-pipe-dropped-url)
- [Doc-only suppression](#6-doc-only-path-suppression-_doc_only_basenames)
- [Removal / nominalization](#7-removal--nominalization-no-matching-token-remains)
- [Quick reference](#quick-reference--inert-form-to-discriminator)
- [What does NOT count as inert (hidden ≠ absent)](#what-does-not-count-as-inert-hidden--absent)

What the unchanged scanner accepts as "provably data", and the exact
mechanism behind each. A transform is only done when one of these
discriminators makes the rule stop firing *because the shape is inert* —
never because a finding was muted.

> All BEFORE / target shapes referenced here are described in their
> already-inert form (raw strings, placeholders, elided pipes). This file
> documents the discriminators; it is not a payload.

---

## 1. Raw-string signature (`r"..."`)

**Mechanism:** `validate_security._match_inside_raw_string` +
`_DETECTOR_SIGNATURE_SKIP_RULES` (RC-46, RC-87). Skillaudit:
`_match_inside_re_pattern_literal` and the `safe_literal` context verdict.

**Why it proves inert:** a raw-string literal is the *regex convention*. A
real CLI argument or IP is a normal string (`"--no-sandbox"`,
`"10.0.0.1"`); it is never written `r"--no-sandbox"`. So a dangerous-looking
token *inside* a raw string is, by construction, a detector needle being
compared against scanned content — there is no call site that spreads it
into argv or a shell.

**Used by:** T1 (detection-pattern / signature lines).

**Discriminator boundary:** only fires for the rules in
`_DETECTOR_SIGNATURE_SKIP_RULES`. If the raw string is genuinely spread
into a sink (`subprocess.run([tool, *SIGS])`), the bytes differ from a
real argument and the call breaks — that case is load-bearing, not inert.

---

## 2. `safe_literal` / re-pattern-literal classifier verdict

**Mechanism:** skillaudit's `_context_classifier_verdict` /
`_match_inside_string_literal_token` (column-precise tokenize) — a
dangerous token that exists *only* inside a quoted string literal, with no
live call, is classified as data.

**Why it proves inert:** the token is provably data when a live form of the
construct exists *only* as a quoted string and never as an executed call.
Example precedent: `verify=False` *inside a quoted string* is a description
of the flag, not a live `verify=False` kwarg, so INSECURE_TLS is suppressed
there (`_match_inside_string_literal_token`).

**Important limit:** this is *not* universal across execution-class rules.
CMD_INJECTION / SHELL_EXEC are **flow-sensitive** — a string *can* be a
payload to a sink — so those are *demoted, not dropped* inside a string
literal (a string may still flow to a `subprocess(shell=True)`). Only
provably-data constructs (a live `verify=False` exists ONLY as code, never
as a quoted string) get the full suppression.

**Used by:** T1 (alongside the raw-string proof).

---

## 3. `_STYLE_LANG_INERT_EXEC_RULES` — exec mentions in non-executing style/markup languages

**Mechanism:** execution-class rules are skipped/demoted when the match
lands inside a non-executing surface — a CSS / SCSS comment, an AppleScript
comment, a markdown comment-context. A style/markup language does not run
the matched token.

**Why it proves inert:** a comment in a stylesheet or a markup language is
never executed; an exec-shaped token there cannot reach a sink.

**Used by:** T6 (when the backtick / command token lives in a markup
comment) and as background for why doc surfaces differ from code surfaces.

---

## 4. Placeholder secret (`<YOUR_API_TOKEN>`, `<TOKEN>`, `${API_TOKEN}`)

**Mechanism:** secret detectors key on entropy + known prefix signatures.
An angle-bracket placeholder or a bare `${VAR}` reference matches no
entropy / prefix signature, so HARDCODED_SECRET / trufflehog do not fire.

**Why it proves inert:** the token is self-evidently a placeholder; it
carries no credential.

**Used by:** T3 (credentials in docs).

**Reverse-case guard:** if trufflehog *verifies* a value as a live secret,
the placeholder rewrite would *hide* a genuine leak. The devitalizer MUST
refuse and escalate "rotate + purge git history" — devitalization is a
shape rewrite, not a leak remediation.

---

## 5. Defanged doc illustration (`text` fence, elided pipe, dropped URL)

**Mechanism:** SUPPLY_CHAIN / CMD_INJECTION key on the *token shape* of a
remote fetch piped into a shell. A `text`-fenced line with the pipe elided
(`... | bash`) and the URL removed contains no such token.

**Why it proves inert:** there is no pipe-to-shell token, no real URL, and
the fence language is non-executable. A markdown file never executes
anyway; removing the shape clears the token-shape rule.

**Used by:** T2 (B1 defanged illustration), T7 (defanged literal reference).

**Note vs already-cleared cases:** CPV already suppresses
`CLAUDE_CLI_UNAUTHORIZED_INSTALL` in `.md`. Only the *generic*
pipe-to-shell SUPPLY_CHAIN class needs this transform — check the
`rule_id` first.

---

## 6. Doc-only path suppression (`_DOC_ONLY_BASENAMES`)

**Mechanism:** for some rules, a match in a doc-only basename
(`_DOC_ONLY_BASENAMES`) is suppressed because the file is documentation,
not a loadable / executable surface.

**Why it proves inert:** the surface cannot execute or be loaded as an
instruction, so the matched token has no sink.

**Used by:** T2 / T3 / T6 / T8 (doc-surface transforms), as the reason docs
differ from code.

**CRITICAL caveat — demote is NOT clear under `--strict`:** skillaudit
*demotes* several EXECUTION-class matches in `references/*.md`
(safe_doc EXECUTION-class -> "demote") to NIT rather than fully
suppressing — and `validate_plugin.py --strict` exits `EXIT_NIT`, i.e. it
**blocks on NITs**. So relocating a threat token from `SKILL.md` into a
`references/*.md` file does NOT clear the gate; it converts a CRITICAL into
a *blocking NIT*. The references surface is agent-reachable, so
execution-class matches there demote rather than fully suppress. The
transform must change the **shape** so the rule does not fire at all (or
fires-then-skips as one of forms 1–5 above), not merely move the token to a
demoting surface. This is the line between "the threat is gone" and "the
threat is quieter but still blocks publish."

---

## 7. Removal / nominalization (no matching token remains)

**Mechanism:** none needed — the rule cannot match a token that is no
longer present. A dead sink call removed; an imperative-to-the-agent
sentence reworded into a description of behavior; a destructive-verb pileup
replaced by one accurate nominal statement.

**Why it proves inert:** the matching token is gone. For code, the sink
call expression no longer exists (taint flow has no sink); for prose, the
imperative / verb-cluster the INTENT rule keyed on no longer exists.

**Used by:** T4 (dead-sink removal), T7 (nominalize imperative), T8
(de-stack verbs).

---

## Quick reference — inert form to discriminator

| Inert form | Discriminator(s) | Applies to |
|------------|------------------|-----------|
| (A) raw-string signature | 1 raw-string, 2 safe_literal | T1 |
| (B) defanged illustration | 5 defanged doc, 6 doc-only | T2, T7 |
| (C) allow-map dispatch | (taint sanitizer — sink removed) + 7 | T5, T9, T4 |
| (D) placeholder | 4 placeholder secret | T3 |
| (D) removal / nominalization | 7 removal | T4, T7, T8 |

**The single governing fact:** the only acceptable clear is the SAME
unchanged scanner, run at the SAME `--strict`, no longer firing *because
the shape is now provably data* — and a demote-to-NIT is **not** a clear,
because `--strict` blocks on NITs.

---

## What does NOT count as inert (hidden ≠ absent)

Inertness means an execution-critical piece is **absent** from the
shipped bytes, not merely out of sight. None of the following clears the
bar — each leaves the construct *runtime-reconstructable*, so the threat
is hidden, not gone:

- **Encoded payload decoded at runtime** — a base64 / hex / charcode
  string the plugin decodes at load or on demand. The decoder plus the
  blob *is* the payload; nothing was removed.
- **Fetched-then-assembled payload** — the dangerous string is pulled
  from the network (or a sibling file) and assembled at runtime. The
  shipped bytes still know how to rebuild it.
- **Regenerated by a code path** — the token is reconstructed by string
  concatenation, formatting, or any generator the plugin runs. A
  reachable code path that re-emits the token is the token.
- **Compiled to a binary** — moving the executable shape into a `.pyc`,
  a native object, or any opaque artifact hides the source but ships a
  runnable form; the execution-critical piece is present, not absent.
- **Merely relocated to a demoting surface** — moving a threat token
  from `SKILL.md` into `references/*.md` only converts a CRITICAL into a
  *blocking NIT* under `--strict` (see "Doc-only path suppression"
  above); the token is still shipped. Relocation is not removal.

The test is reassembly: if any runtime path could restore the
execution-critical piece from the shipped bytes alone, the construct is
hidden, not inert. Removal must make that reassembly impossible.
