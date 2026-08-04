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

# ── issue #188 — an in-process spawn is not a process spawn ──────────────
# `std::thread::spawn` / `tokio::spawn` / `rayon::spawn` / … start a THREAD or
# an async TASK inside the SAME process: no shell, no `exec`, no child. The
# catalog's `\bspawn\s*\(` is aimed at `subprocess.Popen` / `child_process.spawn`
# / `Command::spawn`, so it matches the bare token with no receiver guard and
# every one of these is a pure FP. It is not cosmetic: the finding lands at
# `medium` -> MINOR, and a MINOR blocks `--strict` (exit 3), so one FP gates a
# release whose CRITICAL and MAJOR counts are both 0 — and the reporting code
# could not be changed to satisfy it without deleting the concurrency proof the
# test exists to make.
#
# PATH-QUALIFIED forms are unambiguous: the crate/module path itself names an
# in-process executor, so the line clears on its own.
_RUST_INPROCESS_SPAWN_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(?:std\s*::\s*)?thread\s*::\s*spawn\s*\("
    r"|\btokio\s*::\s*(?:task\s*::\s*)?spawn(?:_blocking|_local)?\s*\("
    r"|\brayon\s*::\s*spawn(?:_fifo)?\s*\("
    r"|\b(?:async_std|smol|glommio)\s*::\s*(?:task\s*::\s*)?spawn(?:_blocking|_local)?\s*\("
)

# A SCOPED spawn (`s.spawn(...)` inside `thread::scope(|s| …)`). The receiver
# alone proves NOTHING — `cmd.spawn()` on a `Command` is a real process spawn —
# so this shape is cleared only with positive evidence of an enclosing scope.
_RUST_SCOPED_SPAWN_RE: Final[re.Pattern[str]] = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*\s*\.\s*spawn\s*\(")

# The scope openers that legitimise a scoped `s.spawn(` above.
_RUST_SCOPE_OPENER_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(?:std\s*::\s*)?thread\s*::\s*scope\s*\("
    r"|\bcrossbeam\s*::\s*(?:thread\s*::\s*)?scope\s*\("
    r"|\brayon\s*::\s*(?:scope|scope_fifo|in_place_scope)\s*\("
)

# A real PROCESS indicator other than `Command::new` — a line carrying one is
# never cleared as in-process, whatever else it looks like.
_RUST_PROCESS_PATH_RE: Final[re.Pattern[str]] = re.compile(r"\bstd\s*::\s*process\b|\bprocess\s*::\s*Command\b")
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

# ── issue #124 (reopened) — multi-line look-back support ─────────────────
# Real Rust code spreads a construct across several lines, so the token that
# proves a match safe (the `eprintln!(` opener, the `Regex::new(` call, the
# `Command::new(` builder head) sits on a PRIOR line, not the flagged one.
# These helpers walk a small window back up from the flagged line to find the
# enclosing construct. The window is bounded (a handful of non-blank lines) so
# the cost stays line-local-ish and an unrelated earlier construct cannot leak
# in. re2-safe: plain `re.search` on already-compiled patterns, no parsing.
#
# Max non-blank lines to walk back when associating a flagged line with the
# construct that opened it. Real print-macro args / builder chains / multi-line
# `Regex::new(` calls in the wild span 2-6 lines; 8 is a comfortable ceiling
# that still refuses to reach an unrelated statement far above.
_RUST_LOOKBACK_MAX: Final[int] = 8
# A line that OPENS a print/format macro call whose argument list is not closed
# on the same line (the trailing `(` with nothing balancing it) — e.g.
# ``eprintln!(`` on its own line, the string args following below.
_RUST_FMT_MACRO_OPEN_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(?:eprintln|println|eprint|print|format|write|writeln|panic|todo|"
    r"unimplemented|unreachable|debug|info|warn|error|trace|log)\s*!\s*\("
)
# A builder-chain continuation line — begins (after indentation) with a method
# call `.foo(` / `.foo` (e.g. `.stdin(...)`, `.stdout(...)`, `.arg(...)`). Used
# to walk UP a `Command::new(...)` builder chain to its `Command::new(` head.
_RUST_BUILDER_CONT_RE: Final[re.Pattern[str]] = re.compile(r"^\s*\.\s*[A-Za-z_]")


def _rust_macro_call_span_has_no_env_write(lines: list[str], line_idx: int) -> bool:
    """C4 (multi-line): True iff the flagged line is INSIDE the argument list of
    a print/format macro call whose opener is on a prior line, and NO genuine
    ``env::set_var`` write appears anywhere in that macro-call span.

    A single-line macro (``eprintln!("… Set CLAUDE_* …")``) is handled by the
    caller's existing ``_RUST_FMT_MACRO_RE`` check; this covers the case where
    ``eprintln!(`` is on line N and the flagged ``CLAUDE_*`` env-name sits on a
    string-CONTINUATION line N+k. Walks back up to ``_RUST_LOOKBACK_MAX``
    non-blank lines for a macro opener; if found, the macro span is the opener
    line through the flagged line, and an ``env::set_var`` ANYWHERE in that span
    disqualifies (a real poisoning write, not a printed reference).
    """
    seen = 0
    for j in range(line_idx, -1, -1):
        text = lines[j]
        if not text.strip():
            continue
        if _RUST_FMT_MACRO_OPEN_RE.search(text):
            # Found the macro opener at line j. The macro-call span is j..line_idx
            # inclusive. A genuine env write anywhere in that span keeps it firing.
            for k in range(j, line_idx + 1):
                if _RUST_ENV_WRITE_RE.search(lines[k]):
                    return False
            return True
        seen += 1
        if seen >= _RUST_LOOKBACK_MAX:
            break
    return False


def _rust_regex_crate_call_above(lines: list[str], line_idx: int) -> bool:
    """C5 (multi-line): True iff the flagged pattern line is the string argument
    of a ``Regex::new(`` / ``RegexBuilder`` call whose call site is on a prior
    line. Walks back up to ``_RUST_LOOKBACK_MAX`` non-blank lines for the
    ``regex``-crate API call site.

    The whole-file BACKTRACKING-engine guard (``fancy_regex`` / ``onig`` /
    ``pcre``) is applied by the caller BEFORE this helper, so a file importing a
    backtracking engine never reaches here — only the linear RE2-style ``regex``
    crate is cleared.
    """
    seen = 0
    for j in range(line_idx, -1, -1):
        text = lines[j]
        if not text.strip():
            continue
        if _RUST_REGEX_CRATE_RE.search(text):
            return True
        seen += 1
        if seen >= _RUST_LOOKBACK_MAX:
            break
    return False


def _rust_command_chain_is_direct_exec(lines: list[str], line_idx: int) -> bool:
    """C6 (multi-line): True iff the flagged ``.spawn()`` / ``.output()`` /
    ``.status()`` terminates a ``Command::new(<non-shell program>)`` builder
    chain that carries NO inline-shell flag (``-c`` / ``/c``) and NO shell
    program (``sh`` / ``bash`` / ``cmd`` / ``powershell`` / …) ANYWHERE in the
    chain. Walks UP the builder chain (continuation lines beginning with ``.``)
    to the ``Command::new(`` head, then inspects every line of the chain.

    FN-safe: a ``Command::new("sh")\\n.arg("-c")\\n.spawn()`` chain has a shell
    program literal AND a ``-c`` flag → disqualified → keeps firing; a
    ``Command::new(prog).arg("-c").spawn()`` chain has a ``-c`` flag → keeps
    firing regardless of program. A chain whose head is not found within the
    look-back window is NOT cleared (returns False → falls through to firing).
    """
    # 1. Walk up to the Command::new( head. The flagged line and the lines above
    #    it (while they look like builder continuations) belong to one chain.
    head_idx: int | None = None
    seen = 0
    for j in range(line_idx, -1, -1):
        text = lines[j]
        if not text.strip():
            continue
        if _RUST_COMMAND_NEW_RE.search(text):
            head_idx = j
            break
        # Only keep walking up while the line is a builder continuation; if we
        # hit a non-continuation, non-Command line first, this `.spawn()` is not
        # part of a recognisable Command chain → bail (conservative).
        if not _RUST_BUILDER_CONT_RE.search(text):
            return False
        seen += 1
        if seen >= _RUST_LOOKBACK_MAX:
            return False
    if head_idx is None:
        return False
    # 2. Inspect every line of the chain (head through the flagged line). A shell
    #    program literal OR an inline-shell flag ANYWHERE keeps it firing.
    for k in range(head_idx, line_idx + 1):
        if _RUST_SHELL_PROGRAM_RE.search(lines[k]) or _RUST_SHELL_FLAG_RE.search(lines[k]):
            return False
    return True


def _rust_spawn_is_in_process(lines: list[str], line_idx: int) -> bool:
    """issue #188: True iff the flagged ``spawn(`` starts a THREAD or an async
    TASK in THIS process rather than a child process.

    Two shapes, deliberately held to different standards of proof:

    1. PATH-QUALIFIED (``std::thread::spawn(``, ``tokio::spawn(``,
       ``rayon::spawn(``, ``async_std::task::spawn(``, …) — the path names an
       in-process executor, so the line clears on its own.
    2. SCOPED HANDLE (``s.spawn(...)`` inside ``thread::scope(|s| …)``) — the
       receiver proves NOTHING on its own, because ``cmd.spawn()`` on a
       ``Command`` is a genuine process spawn. Cleared ONLY when a bounded
       look-back finds a scope opener, and never if it finds a ``Command``
       builder first.

    FN-safe in both shapes: a line carrying a real process indicator
    (``Command::new``, ``std::process``, ``process::Command``) is never
    cleared, so ``Command::new("sh").arg("-c").spawn()`` keeps firing.
    """
    line = lines[line_idx]
    # A real process builder on the flagged line itself always wins.
    if _RUST_COMMAND_NEW_RE.search(line) or _RUST_PROCESS_PATH_RE.search(line):
        return False
    # Shape 1 — unambiguous path-qualified in-process executor.
    if _RUST_INPROCESS_SPAWN_RE.search(line):
        return True
    # Shape 2 — a scoped handle needs positive evidence of its scope.
    if not _RUST_SCOPED_SPAWN_RE.search(line):
        return False
    for j in range(line_idx, max(-1, line_idx - _RUST_LOOKBACK_MAX - 1), -1):
        text = lines[j]
        # A Command builder in the window means this `.spawn()` may terminate a
        # process chain — decline and let the #124 chain logic decide.
        if _RUST_COMMAND_NEW_RE.search(text) or _RUST_PROCESS_PATH_RE.search(text):
            return False
        if _RUST_SCOPE_OPENER_RE.search(text):
            return True
    return False


def _classify_shell_exec(lines: list[str], line_idx: int, match: str) -> ContextVerdict:
    """SHELL_EXEC: the issue-#71 `eval` FP plus the issue-#124 direct-exec
    `Command::new(<non-shell>)…spawn()` FP (single- AND multi-line)."""
    line = lines[line_idx]
    m = (match or "").lower()
    # issue #71 — an `eval`-identifier match (no real-exec on the line).
    if "eval" in m:
        if _RUST_REAL_EXEC_RE.search(line):
            return "unknown"
        if _RUST_EVAL_IDENT_RE.search(line):
            return "safe_literal"
        return "unknown"
    # issue #188 — an in-process thread/async-task spawn is not a process spawn.
    # Checked BEFORE the #124 Command logic: these shapes have no `Command::new`
    # head at all, so the chain walker below would never reach them.
    if "spawn" in m and _rust_spawn_is_in_process(lines, line_idx):
        return "safe_literal"
    # issue #124 class 6 — a `spawn`/`output`/`status` match. Clear ONLY a direct
    # exec of a non-shell program with NO inline-shell flag in the builder chain.
    # FN-safe: `Command::new("sh").arg("-c")…` has a shell-program literal AND a
    # `-c` flag → FIRES; `Command::new(prog).arg("-c")…` has a `-c` flag → FIRES
    # (shell form regardless of program).
    #
    # Single-line shape (`Command::new(...)…spawn()` all on one line): decide off
    # the flagged line directly. Multi-line shape (issue #124 reopened — the
    # `.spawn()` is several lines down a builder chain whose `Command::new(` head
    # is on a prior line): walk the chain. A chain whose head can't be located is
    # NOT cleared (conservative → falls through to firing).
    if _RUST_COMMAND_NEW_RE.search(line):
        if _RUST_SHELL_PROGRAM_RE.search(line) or _RUST_SHELL_FLAG_RE.search(line):
            return "unknown"
        return "safe_literal"
    if _rust_command_chain_is_direct_exec(lines, line_idx):
        return "safe_literal"
    return "unknown"


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
        return _classify_shell_exec(lines, line_idx, match)

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
        # Single-line shape (`Regex::new(r"…")` on one line) OR multi-line shape
        # (issue #124 reopened — `Regex::new(` on a prior line, the flagged
        # pattern on a string-continuation line below). The whole-file
        # backtracking-engine guard above already gates this, so only the linear
        # RE2-style `regex` crate reaches the clear.
        if _RUST_REGEX_CRATE_RE.search(line) or _rust_regex_crate_call_above(lines, line_idx):
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
        # Single-line shape — the print macro and the env-name are on the flagged
        # line (`eprintln!("… Set CLAUDE_* …")`), no env write on the line.
        if _RUST_FMT_MACRO_RE.search(line) and not _RUST_ENV_WRITE_RE.search(line):
            return "safe_literal"
        # Multi-line shape (issue #124 reopened) — the `eprintln!(` opener is on a
        # prior line and the flagged `CLAUDE_*` env-name sits on a string-
        # continuation line with no macro token of its own. Resolve the enclosing
        # macro-call span and clear only when NO env::set_var write is in it.
        if _rust_macro_call_span_has_no_env_write(lines, line_idx):
            return "safe_literal"
        return "unknown"

    return "unknown"
