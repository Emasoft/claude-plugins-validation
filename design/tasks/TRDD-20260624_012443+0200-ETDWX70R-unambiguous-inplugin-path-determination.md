---
trdd-id: ETDWX70R
title: Unambiguous in-plugin vs outside-plugin write-path determination — replace the fail-safe-lenient resolver
column: backburner
created: 2026-06-24T01:24:43+0200
updated: 2026-06-24T01:24:43+0200
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

## Relationship

- Supersedes the fail-safe-LENIENT resolver in the current-version copy-only
  enforcement (its sibling TRDD this session) — that resolver is "only for now"
  per the user.
- Builds on the resolution primitives in `scripts/cpv_persistence_target.py`
  (`_fold_to_plugin_root` / `_resolve_in_tree` / `_PLUGIN_DATA_LITERAL_RE`).
- Same safety story as the #152 daemon-source-scan discriminator (TRDD-ETCVNIPC):
  what RUNS must be what was SCANNED.
