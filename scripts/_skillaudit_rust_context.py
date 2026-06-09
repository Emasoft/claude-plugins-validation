#!/usr/bin/env python3
"""Rust context classifier for SkillAudit (issue #71).

Given a Rust (``.rs``) source file plus the line index a SkillAudit regex
matched against, decide whether the match is a genuine threat or a Rust
idiom the cross-language regexes mis-fire on.

The single FP class this classifier addresses is the ``SHELL_EXEC`` rule's
``\\beval\\s*\\(`` pattern firing on Rust ``eval`` identifiers:

    pub fn eval(&self, lc: &LineCtx) -> bool   # fn eval(  — definition
    Expr::Leaf(q) => q.eval(lc)                # .eval(    — method call
    Pred::eval(&p, lc)                         # ::eval(   — path call

Rust has **no** runtime code-eval and **no** shell-eval builtin (unlike
Python's ``eval()`` / JS's ``eval()`` / shell ``eval``). Every ``eval(``
in Rust source is therefore a user-defined function or method call, never
shell execution. Suppressing these matches hides nothing: real Rust shell
execution is ``std::process::Command::new(...)`` followed by ``.spawn()``
/ ``.output()`` / ``.status()`` / ``.exec()`` — none of which contain the
substring ``eval`` — and those continue to fire via the ``SHELL_EXEC``
``\\bspawn\\s*\\(`` pattern and the taint engine, INDEPENDENTLY of this
classifier (issue #71 repro line 9:
``std::process::Command::new("sh").arg("-c").spawn()`` still fires).

Conservative by construction:

* Only ``SHELL_EXEC`` matches whose matched text is an ``eval`` token are
  ever suppressed. Every other rule / match falls through to ``unknown``
  so the existing heuristic chain runs unchanged.
* Defense in depth: if the SAME source line ALSO carries a real
  process-spawn indicator (``Command::new``, ``std::process``,
  ``.spawn(``, ``.output(``, ``.status(``, ``.exec(``), the match is NOT
  suppressed — the line fires. So an obfuscation like
  ``let _ = eval(); Command::new("sh")`` stays visible.
* The classifier never parses Rust syntax (that would need a tree-sitter
  / ``syn`` parser); it uses tight line-window regex heuristics, matching
  the precedent set by the Python (v2.101.0) and TypeScript (issue #39)
  classifiers. Patterns are re2-safe (no lookbehind / lookahead) so they
  run identically with and without ``google-re2`` installed.
"""

from __future__ import annotations

import re
from typing import Final, Literal

ContextVerdict = Literal["safe_literal", "unknown"]

# A genuine Rust process-spawn / shell-exec indicator. When this appears on
# the SAME line as an `eval` match, do NOT suppress — let the line fire.
_RUST_REAL_EXEC_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(?:Command::new|std::process|process::Command)\b"
    r"|\.\s*(?:spawn|output|status|exec)\s*\("
)

# An `eval` identifier in method (`.eval(`), path (`::eval(`), definition
# (`fn eval(`), or bare-call (`eval(`) position — the Rust shapes the
# SHELL_EXEC `\beval\s*\(` pattern mis-fires on.
_RUST_EVAL_IDENT_RE: Final[re.Pattern[str]] = re.compile(
    r"\bfn\s+eval\b"  # fn eval(...)        — definition
    r"|\.\s*eval\s*\("  # receiver.eval(...)  — method call
    r"|::\s*eval\s*\("  # Path::eval(...)     — path call
    r"|\beval\s*\("  # eval(...)           — free call
)


def classify(
    file_path: str,
    content: str,
    line_idx: int,
    match: str,
    rule_id: str,
) -> ContextVerdict:
    """Classify a SkillAudit match on a Rust source line.

    Returns ``"safe_literal"`` only for the SHELL_EXEC ``eval``-identifier
    FP (issue #71); ``"unknown"`` for everything else so the regular
    heuristic chain decides (the iron rule — "better safe than sorry" —
    stays in force).
    """
    if not file_path.lower().endswith(".rs"):
        return "unknown"

    # The only Rust FP this classifier owns is the SHELL_EXEC `eval(` match.
    if rule_id != "SHELL_EXEC":
        return "unknown"

    # Only an `eval`-token match is in scope. A `spawn(` / `Command` match
    # (the real-exec form, e.g. line 9 of the issue #71 repro) carries no
    # `eval` substring, so it falls through and fires.
    if "eval" not in (match or "").lower():
        return "unknown"

    lines = content.splitlines()
    if not (0 <= line_idx < len(lines)):
        return "unknown"
    line = lines[line_idx]

    # Defense in depth: a real process-spawn indicator on the same line
    # overrides the eval-suppression — keep the finding.
    if _RUST_REAL_EXEC_RE.search(line):
        return "unknown"

    # Confirm the match really is an `eval` identifier in method / path /
    # definition / free-call position before suppressing. If the eval token
    # is in some unrecognised position, stay conservative and let it fire.
    if _RUST_EVAL_IDENT_RE.search(line):
        return "safe_literal"

    return "unknown"
