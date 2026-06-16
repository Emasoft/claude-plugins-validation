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

# ── issue #124 — additional Rust-idiom discriminators ────────────────────
# A `Command::new(...)` process builder (class 6 — SHELL_EXEC `spawn(`).
_RUST_COMMAND_NEW_RE: Final[re.Pattern[str]] = re.compile(r"\bCommand::new\s*\(")
# `Command::new("<shell>")` — a shell-PROGRAM literal target. This is the
# dangerous form (`sh -c …`) that must KEEP firing; direct-exec of any other
# fixed/variable program is the documented FP.
_RUST_SHELL_PROGRAM_RE: Final[re.Pattern[str]] = re.compile(
    r"""Command::new\s*\(\s*[\"'](?:/(?:usr/)?bin/)?"""
    r"""(?:sh|bash|zsh|dash|ash|ksh|fish|cmd|cmd\.exe|powershell|powershell\.exe|pwsh)[\"']"""
)
# An inline-shell flag `-c` / `/c` (quoted) anywhere on the line — turns a
# `Command` into a shell invocation regardless of which program; keep firing.
_RUST_SHELL_FLAG_RE: Final[re.Pattern[str]] = re.compile(r"""[\"'](?:-c|/c)[\"']""")
# A genuine Rust env-write to a reserved CLAUDE_* var (class 4 — the real
# poisoning shape, distinct from PRINTING the var name in a help string).
_RUST_ENV_WRITE_RE: Final[re.Pattern[str]] = re.compile(r"\b(?:std::)?env::set_var\s*\(")
# Rust logging / formatting macros — a string-literal argument here is a
# LOG/FORMAT sink (printing text), never a write or an executed instruction.
_RUST_FMT_MACRO_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(?:eprintln|println|eprint|print|format|write|writeln|panic|todo|"
    r"unimplemented|unreachable|debug|info|warn|error|trace|log)\s*!"
)
# The Rust `regex` crate (RE2-style — linear-time, NO backtracking). Catastrophic
# ReDoS is impossible by construction (class 5).
_RUST_REGEX_CRATE_RE: Final[re.Pattern[str]] = re.compile(
    r"\bRegex(?:Builder|Set)?::new\b|\bregex::[A-Za-z]|\bregex!\s*\(|\bRegexBuilder\b|\bRegexSet\b"
)
# Backtracking regex engines available in Rust — these CAN ReDoS, so a
# REGEX_DOS match in a file importing one MUST keep firing (class 5 caveat).
_RUST_BACKTRACK_REGEX_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(?:fancy_regex|onig|onig_sys|pcre|pcre2)\b"
)
# CROSS_TOOL_ACCESS weak member (class 3) — the bare `full_context` /
# `context_window` substring that fires on any identifier so named. The STRONG
# members (conversation_history / system_prompt / call_tool / get*Messages) are
# DIFFERENT match texts and keep firing as their own matches.
_CROSS_TOOL_WEAK_RE: Final[re.Pattern[str]] = re.compile(r"^(?:full_context|context_window)$", re.IGNORECASE)


def _classify_shell_exec(line: str, match: str) -> ContextVerdict:
    """SHELL_EXEC: the issue-#71 `eval` FP plus the issue-#124 direct-exec
    `Command::new(<non-shell>)…spawn()` FP."""
    m = (match or "").lower()
    # issue #71 — an `eval`-identifier match (no real-exec on the line).
    if "eval" in m:
        if _RUST_REAL_EXEC_RE.search(line):
            return "unknown"
        if _RUST_EVAL_IDENT_RE.search(line):
            return "safe_literal"
        return "unknown"
    # issue #124 class 6 — a `spawn`/`output`/`status` match. Clear ONLY a
    # direct exec of a non-shell program with NO inline-shell flag on the line.
    # FN-safe: `Command::new("sh").arg("-c")…` has a shell-program literal AND a
    # `-c` flag → both gates fail → FIRES; `Command::new(prog).arg("-c")…` has a
    # `-c` flag → FIRES (shell form regardless of program); a multi-line builder
    # whose `spawn()` line lacks `Command::new(` is not cleared (conservative).
    if not _RUST_COMMAND_NEW_RE.search(line):
        return "unknown"
    if _RUST_SHELL_PROGRAM_RE.search(line) or _RUST_SHELL_FLAG_RE.search(line):
        return "unknown"
    return "safe_literal"


def classify(
    file_path: str,
    content: str,
    line_idx: int,
    match: str,
    rule_id: str,
) -> ContextVerdict:
    """Classify a SkillAudit match on a Rust source line.

    Returns ``"safe_literal"`` for a Rust-idiom FP the cross-language regexes
    mis-fire on (issue #71 SHELL_EXEC `eval`; issue #124 classes 1/3/4/5/6);
    ``"unknown"`` for everything else so the regular heuristic chain decides
    (the iron rule — "better safe than sorry" — stays in force).

    NOTE on INDIRECT_PROMPT_INJECT (issue #124 class 2): deliberately NOT cleared
    here. It is an INTENT-class rule (the v2.126.24 protected set) whose benign
    log-string shape (`debug!("…corrected prompt: {}", v)`) is collision-shaped
    with a real injection (`debug!("corrected system prompt: <override>")`);
    weakening it risks the prompt-injection invariant, so a legitimate log string
    is resolved by rephrasing, not by a classifier clear.
    """
    if not file_path.lower().endswith(".rs"):
        return "unknown"

    lines = content.splitlines()
    if not (0 <= line_idx < len(lines)):
        return "unknown"
    line = lines[line_idx]

    if rule_id == "SHELL_EXEC":
        return _classify_shell_exec(line, match)

    # Class 1 — PROTOTYPE_POLLUTION is a JS/TS-only vulnerability class (it needs
    # a mutable `Object.prototype` / dynamic property assignment). Rust has no
    # prototypes and no `Object`; `Vec::extend`/`HashMap::extend` are typed,
    # bounds-checked appends. The rule is categorically inapplicable to Rust, so
    # a blanket per-language clear is FN-safe by construction (the JS `__proto__`
    # / `req.body` siblings are `.js`/`.ts` files → never reach this classifier).
    if rule_id == "PROTOTYPE_POLLUTION":
        return "safe_literal"

    # Class 5 — REGEX_DOS. Rust's `regex` crate is a finite-automaton (RE2-style)
    # engine with guaranteed linear-time matching and NO backtracking, so
    # catastrophic-backtracking ReDoS is impossible by construction. Clear a
    # nested-quantifier match ONLY when the line uses the `regex`-crate API AND
    # the file does NOT import a BACKTRACKING engine (fancy_regex / onig / pcre),
    # which genuinely can ReDoS and must keep firing.
    if rule_id == "REGEX_DOS":
        if _RUST_BACKTRACK_REGEX_RE.search(content):
            return "unknown"
        if _RUST_REGEX_CRATE_RE.search(line):
            return "safe_literal"
        return "unknown"

    # Class 3 — CROSS_TOOL_ACCESS. Only the bare `full_context` / `context_window`
    # sub-pattern is weak — it fires on any identifier so named (here PSS's
    # in-process scoring variable `full_context_text`). Clear ONLY that member;
    # the STRONG sub-patterns (conversation_history / system_prompt / call_tool /
    # get*Messages) are different match texts and keep firing as their own matches
    # even in Rust, so a genuine cross-tool read is still caught.
    if rule_id == "CROSS_TOOL_ACCESS":
        if _CROSS_TOOL_WEAK_RE.match((match or "").strip()):
            return "safe_literal"
        return "unknown"

    # Class 4 — CLAUDE_RESERVED_ENV_POISON. The rule's own description says
    # reading/printing a reserved var is normal; the FP is the `\bsetx?\s+` Windows
    # `set` sub-pattern collapsing the English imperative "Set" in a help string
    # (`eprintln!("… Set CLAUDE_PLUGIN_ROOT …")`). Clear ONLY a print/format-macro
    # line that does NOT also perform a genuine env write. FN-safe: a real Rust
    # write `env::set_var("CLAUDE_*", …)` (now a catalog pattern) is not a bare
    # print macro → not cleared → FIRES; the Python/shell/Node write siblings are
    # other languages and keep firing.
    if rule_id == "CLAUDE_RESERVED_ENV_POISON":
        if _RUST_FMT_MACRO_RE.search(line) and not _RUST_ENV_WRITE_RE.search(line):
            return "safe_literal"
        return "unknown"

    return "unknown"
