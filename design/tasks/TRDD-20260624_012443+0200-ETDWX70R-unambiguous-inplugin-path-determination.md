---
trdd-id: ETDWX70R
title: Unambiguous in-plugin vs outside-plugin write-path determination — replace the fail-safe-lenient resolver
column: backburner
created: 2026-06-24T01:24:43+0200
updated: 2026-08-29T15:11:43+0200
current-owner: cpv-main-session
assignee: null
priority: 2
severity: MEDIUM
effort: L
labels: [security, persistence, path-resolution, copy-only-enforcement, next-version]
task-type: spike
parent-trdd: null
npt: []
eht: []
blocked-by: []
relevant-rules: []
release-via: publish
delivery: direct-push
target-branch: master
test-requirements: [unit, lint, typecheck]
audit-requirements: [security-scan]
review-requirements: [code-review]
runtime-targets: [macos, linux]
impacts: []
attempts: 0
last-test-result: not-run
implementation-commits: []
external-refs: ["github.com/Emasoft/claude-plugins-validation/issues/152"]
---

# Unambiguous in-plugin vs outside-plugin write-path determination

## Goal

A path-classification heuristic that decides, with NO ambiguity, whether a plugin
script's file-WRITE destination lands INSIDE the plugin tree (ROOT or DATA) or
OUTSIDE it (the user's project folder, or anywhere else). Every detected write
must resolve to a definite INSIDE or OUTSIDE verdict — no write may pass on an
unresolved / ambiguous destination.

## Why this is needed (the gap the current version accepts ONLY FOR NOW)

The shipping copy-only enforcement (its sibling TRDD this session, shipped
alongside the #152 daemon-source-scan work in TRDD-ETCVNIPC) uses a
fail-safe-LENIENT resolver: it flags a write only when the destination is
PROVABLY in-plugin — a static path literal, or the closed env-fold via
`CLAUDE_PLUGIN_ROOT` / `CLAUDE_PLUGIN_DATA` / the `~/.claude/plugins/data/<slug>/`
literal. A write whose destination is computed at runtime, and therefore not
statically provable, PASSES. That direction was chosen so legitimate
code-generating plugins (whose project-output paths are commonly computed) are
not over-blocked.

The user accepted that lenient direction ONLY FOR THIS VERSION. Its residual gap:
a write whose destination is assembled at runtime to land in-plugin is not caught
(it is unprovable statically, so it passes), so an in-plugin script mutation can
still slip through behind a computed path. The next version must close that gap
WITHOUT re-introducing the over-block problem — i.e. determine INSIDE vs OUTSIDE
even for dynamically-built paths.

## The hard part

General dynamic path construction is not decidable by line-level pattern matching:
a destination can be assembled from variables, function returns, config values, or
external inputs. "No ambiguity" therefore requires analysis stronger than the
current pattern-and-env-fold resolver, and a deliberate choice for the residual
undecidable cases.

## Candidate approaches to evaluate (spike first, then build)

- AST data-flow / taint of the destination expression: track how the path
  variable is constructed; resolve symbolic prefixes to the known plugin-tree
  anchors; emit INSIDE / OUTSIDE / UNRESOLVED per write.
- Constrain-the-input model: REQUIRE in-plugin writes to be expressed only through
  the declared anchors (`CLAUDE_PLUGIN_DATA`) AND only via a verbatim-copy
  primitive; reject any path construction that could reach the plugin tree by any
  other route. Shrinks the decidable surface by contract.
- Abstract interpretation / symbolic resolution of the path expression to a set of
  possible absolute roots; prove containment (or non-containment) in the plugin
  tree; an UNRESOLVED result becomes a hard rejection (flip the fail-safe to
  STRICT) — paired with a supported declaration mechanism so legit project-output
  generators state their output is outside and are not over-blocked.
- Install-time / runtime containment check complementing the static pass: observe
  the actual write destinations during a sandboxed install and confirm any INSIDE
  write is a verbatim copy of an already-scanned source.

## Acceptance criteria

- Every detected write resolves to a definite INSIDE or OUTSIDE verdict; an
  UNRESOLVED destination is never silently allowed.
- An in-plugin write reached via a computed path is caught (closes the current
  residual gap).
- Legitimate plugins that write generated output to the PROJECT folder have a
  supported, unambiguous way to be recognized as OUTSIDE (no over-block).
- Two-sided tests: a computed in-plugin write is flagged; a computed project-output
  write is allowed.

## Attempted measurement — 2026-08-29 — AND WHY IT DOES NOT SETTLE THE QUESTION

Recorded because the negative result is the useful part: **this measurement cannot be
made with the guard's own patterns, and any future attempt must not repeat it.**

What was run: classify every SCRIPT-destination non-copy write that the RC-164 guard's
own `_HEREDOC_REDIRECT_PATTERNS` / `_PY_WRITE_PATTERNS` / `_SHELL_WRITE_PATTERNS`
extract, splitting the guard's single `False ⇒ pass` branch into the two cases it
conflates. Over CPV + **104** installed plugins (105 trees, no sampling cap):

| verdict | count | meaning |
|---|---|---|
| IN_TREE | 57 | destination is a literal that folds in-tree — the guard FLAGS these |
| OUTSIDE | 9 | folds to a concrete path outside the tree — correctly allowed |
| UNRESOLVED | 5 | matched a write pattern but does not fold |

**These are counts of `writes whose destination is a capturable literal token`, NOT of
all writes — and the distinction voids the conclusion I first drew from them.**

Three defects, all confirmed:

1. **The extractor is circular with the resolver.** It can only see a destination that
   appears as a literal in the pattern's capture group. A path assembled at runtime
   (`out = build_path(cfg); out.write_text(body)`) presents no such token, so it is
   invisible to the extractor *and* to the resolver — the same blind spot twice. The
   probe therefore cannot, by construction, count the population this card is about.
2. **The corpus zeros are UNINTERPRETABLE — which is not the same as "the instrument is
   broken", and an earlier revision of this section wrongly said the latter.** 99 of 105
   trees scored `total=0`, and a grep finds hundreds of write-primitive files in those
   same trees (`ai-maestro-janitor` 860, `ai-maestro-plugin` 108, `perfect-skill-suggester`
   103). But the probe only yields a destination when `_is_script_destination(dst)` holds,
   and most of those hits are `.json` state, `.md` reports and `.log` output — for which
   `total=0` is the guard behaving CORRECTLY. The zeros conflate "writes no scripts" with
   "writes no scripts we can detect", and nothing here separates them. Defect 1 is the
   structural argument and stands on its own; this observation cannot corroborate it.
3. A positive control (three planted writes, one per family) does PASS — the extractor
   fires on literal-token shapes. It does not and cannot fire on assembled ones, so the
   control bounds the instrument rather than rescuing the corpus.

**Do NOT cite the table above as evidence that computed in-plugin writes are rare.** The
correct statement is: computed-path writes are UNMEASURED. Measuring them needs an
instrument that does not share the resolver's blind spot (an AST pass over write-sink
call sites, counting destinations by whether they are literal vs assembled) — which is
itself most of the work this card would commission, and is the first thing to build if
the card is ever unparked.

## Decision — 2026-08-29

**Stays `backburner`.** The reason is the UNMEASURED FP population (below), NOT
undecidability.

**Correcting an earlier revision of this section, which was wrong and would have steered
the next reader away from the right approach:** it claimed AST taint and abstract
interpretation "cannot meet acceptance criterion 1 at any effort level" because the
problem is undecidable. That is a misuse of the argument. Undecidability rules out a
resolver that is both SOUND and COMPLETE; it says nothing against a sound-but-INCOMPLETE
one, which is what every static analyser in existence is. Criterion 1 — every write gets a
definite verdict, UNRESOLVED never silently allowed — is trivially satisfiable by resolving
what you can and treating the remainder as REJECT. **Those two approaches are exactly the
right tools**: their value is SHRINKING the unresolved set, i.e. buying down the false
rejects that make the strict flip affordable. Do not dismiss them on theory.

The tractable approach is **constrain-the-input**: require an in-plugin write to be
expressed only through a declared anchor (`CLAUDE_PLUGIN_DATA`) via a verbatim-copy
primitive, and REJECT any other construction that could reach the plugin tree. That
flips the fail-safe to STRICT over a decidable surface, and needs a declaration
mechanism so a legitimate project-output generator states it writes OUTSIDE. It is a
rule, not an analysis engine.

**Acceptance criterion 1 is narrowed** accordingly: "every detected write resolves to
a definite verdict" is achievable only over the constrained grammar. Outside it,
UNRESOLVED must become a hard REJECT — never a silent allow, and never a claim of
having decided.

**Why it stays parked rather than shipping now:** the strict flip is the FP-risk half —
it can over-block a code-generating plugin the day it lands — and the size of the
population it would newly reject is **unknown** (see the measurement section: the number
was not obtained, and the first attempt was void). Shipping a strict reject against an
unmeasured population is how a gate false-blocks a fleet. The RC-164 guard plus the #152
fold already cover every write that provably resolves in-tree, so the exposure is bounded
to the computed-path case.

**Unpark on either trigger:** a real evasion is observed, OR someone builds the AST
literal-vs-assembled census named above — that census is the prerequisite, because it is
the only thing that turns the FP risk of the strict flip from a guess into a number.

## Relationship

- Supersedes the fail-safe-LENIENT resolver in the current-version copy-only
  enforcement (its sibling TRDD this session) — that resolver is "only for now"
  per the user.
- Builds on the resolution primitives in `scripts/cpv_persistence_target.py`
  (`_fold_to_plugin_root` / `_resolve_in_tree` / `_PLUGIN_DATA_LITERAL_RE`).
- Same safety story as the #152 daemon-source-scan discriminator (TRDD-ETCVNIPC):
  what RUNS must be what was SCANNED.
