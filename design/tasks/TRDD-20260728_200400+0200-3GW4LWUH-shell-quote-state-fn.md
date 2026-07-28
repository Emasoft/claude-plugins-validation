---
trdd-id: 3GW4LWUH
title: Shell quote-state — one FP report, two security false negatives
column: complete
created: 2026-07-28T20:04:00+0200
updated: 2026-07-28T20:04:00+0200
current-owner: cpv-main
task-type: security
scope: project
relevant-rules: [4]
external-refs: ["#180"]
---

# Shell quote-state — one FP report, two security false negatives

## ⏵ STATE — READ THIS FIRST ON RESUME (authoritative) — 2026-07-28

Shipped. Three defects, one root cause: **nothing tracked shell quote state**,
so "is this text inert?" was answered line-locally and by the wrong property.

| # | Shape | Before | After |
|---|---|---|---|
| 1 | backtick in a genuine `run:` `#` comment | NIT (blocks `--strict`) | cleared |
| 2 | `echo "$(curl … \| sh)"` in a `.sh` | **fully suppressed** | CRITICAL |
| 3 | `#` on a line inside a double quote opened EARLIER | suppressed / NIT | visible |

**NEXT ACTION: none.** Guarded by `tests/test_issue_180_shell_comment_quote_state.py`
(29 tests, two-sided).

## How it was found

The reporter on #180 asked a question rather than filing a claim: *"if the
canon-emitted validate block contains backticks inside its `run:` comments,
every consumer inherits this NIT."* Probing that FP two-sidedly is what
surfaced defects 2 and 3 — neither was reported, and defect 2 is the serious
one.

CPV's own workflows carry backticks in `run:` comments and self-validate
clean, which could have read as "already handled". It is not: the reporter's
long line was cleared **by accident**, by an unrelated multi-backtick-span
helper (#88) that needs ≥2 spans. Their one-span sibling fell through to NIT.
The rule was never principled — which is exactly why probing beat inspecting.

## The defects

**1 — FP.** A `run:` body IS shell, so a `#` comment in it is documentation
the shell never executes. The shell classifier already knew this for `.sh`;
the YAML classifier never reached that rule. Explaining a shell change in a
comment naturally means markdown inline code, so the collision is easy to
walk into.

**2 — FN, the serious one.** `_match_inside_shell_echo_string` suppressed any
execution-class match inside a quoted argument to `echo`/`printf`/`cat`,
**without distinguishing the quote style**. Single quotes are literal; double
quotes are not — `$(...)` and backticks are substituted *before* the display
command is invoked. So `echo "$(curl … | sh)"` executes the pipeline exactly
like the bare form, and was reported as nothing at all while the bare form is
CRITICAL. Any payload could be hidden by wrapping it in `echo "…"`.

**3 — FN.** `_is_shell_comment_line` matches `^\s*#`. A `#` only opens a
comment when the line *starts* outside a string; with a double quote still
open from an earlier line it is ordinary string content and the backticks
beside it run.

## The fix

One primitive — `_shell_quote_state_at_line_start(lines, start, target)` —
lexing forward and returning `normal` / `sq` / `dq` / `unknown`.

`unknown` is deliberately a **distinct** value, not folded into `normal`: a
heredoc body is not lexed like ordinary shell, and a new suppression must
require positively-`normal`, so an unmodelled construct can never be mistaken
for proof of inertness.

Direction of each change is deliberate:

- defects 2 and 3 are pure **tightenings** — they only *remove* suppression in
  the provably-live case, so they cannot regress an existing FP fix. Only `dq`
  is declined; `sq` and `unknown` keep their historical verdict.
- defect 1 is the only **new** suppression, so it carries the strictest gate:
  a genuine comment AND a positively-`normal` state AND execution-class only.

## Rejected

- **Catalog-level narrowing of the backtick pattern.** Backticks in a comment
  and backticks in a live substitution are byte-identical; only context
  separates them. A pattern tweak would trade the FP for an FN.
- **Declining the whole `echo` body when it contains any substitution.** This
  is what I shipped FIRST, on the reasoning that attribution would suppress
  the substitution's own findings. That reasoning was **false** — those
  matches lie INSIDE the span, so they are never the ones suppressed — and
  measuring the coarse form found two new FPs: a printed `sudo` beside a
  benign `$(ls | wc -l)` drew a CRITICAL, and `$(( … ))` arithmetic (which
  runs no command) read as a substitution. Replaced with span attribution,
  which is both more precise and equally FN-safe.
- **Exempting `.github/workflows/`.** Workflow YAML is execution config; the
  module already says so and refuses the JSON-metadata model for it.

## Scope guard

The clear is execution-class only. `_SHELL_EXECUTION_CLASS_RULES` excludes
every prose-vector rule, so `PROMPT_INJECT` / `INDIRECT_PROMPT_INJECT` /
`A2A_*` in a comment stay visible — an agent reading a workflow still sees
comment text. Pinned by
`test_prompt_injection_in_a_comment_stays_visible`.

## Lesson

A one-span and a three-span version of the same comment got different
verdicts, and the three-span one looked correct. **When a report says "this
shape is fine on your side", test the shape's smallest member** — the passing
case may be passing for an unrelated reason. Probing the FP is what found the
FN; inspecting the diff would not have.

Second lesson, from my own first attempt: **a security tightening still needs
its FP side measured.** I declined the whole `echo` body on a rationale that
sounded careful ("over-reporting is the safe direction") and was factually
wrong about what would be suppressed. Probing five ordinary display strings
found two regressions in under a minute. Fail-safe is the right default when
you genuinely cannot attribute — not a substitute for checking whether you
can.

Related: `lesson-verify-fp-scope-before-category-fix`,
`lesson-review-competing-fix-with-real-scanner`.
