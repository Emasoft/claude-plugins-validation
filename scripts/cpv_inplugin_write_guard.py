#!/usr/bin/env python3
"""Copy-only in-plugin-write guard (issue #152, TRDD-Z2HKVTUE).

The #152 daemon-source-scan fold (``cpv_persistence_target.py``) scans the
IN-TREE source ``plugin_root/<rest>`` and clears it. That is sound ONLY IF the
file staged at ``~/.claude/plugins/data/<slug>/<rest>`` is a VERBATIM COPY of
it. Without an active check, a plugin could GENERATE a different staged file at
install time — what RUNS would not be what was SCANNED. This guard is that
check.

**THE RULE:** a plugin may COPY a shipped, already-scanned script into the
plugin DATA folder, but may NOT generate / template / string-substitute / edit
a script that lands INSIDE the plugin tree — and "inside" means BOTH the plugin
ROOT tree AND the DATA tree (``~/.claude/plugins/data/<slug>/…``). Only
CPV-scanned scripts are trusted; a post-install in-plugin mutation produces an
unscanned script that then runs, with no CPV pass to re-scan it.

**THE QUIRK (do NOT over-block):** a plugin's legitimate FUNCTION is often to
generate / edit scripts. The discriminator is the DESTINATION, not the act:
output written OUTSIDE the plugin (into the user's PROJECT folder, evaluated by
that project's own Claude) is ALLOWED; only writes that create / modify a
script INSIDE the plugin tree are flagged.

A plugin source line is FLAGGED (CRITICAL) when it performs a WRITE that:

1. creates / modifies a SCRIPT / SOURCE file (the script-file gate), AND
2. whose destination PROVABLY resolves INSIDE the plugin tree (ROOT or DATA)
   via ``cpv_persistence_target._resolve_in_tree`` / ``_fold_to_plugin_root``
   (a ``Path`` under ``plugin_root``, incl. the data-dir literal fold; ``None``
   ⇒ not provable ⇒ PASS), AND
3. is NOT a verbatim COPY of an in-tree source (the copy carve-out).

**THE FAIL-SAFE (lenient, this version):** flag only PROVABLE in-plugin writes
— a destination that statically resolves, or env-folds via
``CLAUDE_PLUGIN_ROOT`` / ``CLAUDE_PLUGIN_DATA`` / the
``~/.claude/plugins/data/<slug>/`` literal, into the plugin tree. A dynamic /
unresolvable destination PASSES (so a legitimate code-generation plugin
computing project paths is not over-flagged). The residual gap (a computed
in-plugin path slips) is accepted for now and tracked by TRDD-ETDWX70R.

Self-scan-clean: this analyzer's own write-primitive needles live in
ALL-CAPS ``*_PATTERNS`` ``Final`` collections (the pattern-source shape) so
CPV's own self-scan reads them as rule DATA and does not self-flag — the same
``_CPV_IS_RUNNING_CPV``-gated pattern-source skip the persistence module uses.
FN-safe: that skip is gated to CPV's OWN hash-pinned source, so a real
in-plugin script write in a THIRD-PARTY plugin still FLAGS.

All regexes here are **re2-safe** (no lookbehind / lookahead) — CI runs without
google-re2.

The resolution helpers are REUSED from ``cpv_persistence_target.py`` — the
destination-resolves-in-tree decision is the SAME one the #152 fold makes, so
the two modules cannot drift.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, Final, NamedTuple

if TYPE_CHECKING:  # pragma: no cover - type-checking only (runtime import cycle)
    from cpv_write_sink_ast import AstWriteSink

# The destination-resolves-in-tree decision is shared with the #152 fold so the
# two modules cannot drift. ``_resolve_in_tree`` returns a ``Path`` under
# ``plugin_root`` for a PROVABLE in-plugin destination (incl. the
# ``~/.claude/plugins/data/<slug>/<rest>`` literal fold), or ``None`` for an
# unresolvable / out-of-tree path. ``_fold_to_plugin_root`` is the string-level
# fold used when we want the folded form without requiring the file to exist yet
# (a generated destination may not exist at scan time).
from cpv_persistence_target import (
    _RESIDUAL_VAR_RE,
    _fold_to_plugin_root,
    _resolve_in_tree,
)


class WriteFinding(NamedTuple):
    """One in-plugin write occurrence, with its severity TIER.

    TRDD-ETDWX70R replaced the single blocking verdict with three tiers:

    * ``critical`` — T1: the destination FOLDS into the plugin tree and its tail
      carries a script suffix (or a shebang / ``chmod +x`` proves it is a
      script). Today's severity, unchanged.
    * ``major`` — T2 ``RC-164-UNRESOLVED``: the destination's PREFIX folds into
      the plugin tree but its tail is NOT a literal, so the fold cannot decide
      whether a script lands in-tree. Blocking for BOTH anchor kinds (ROOT and
      DATA): the #152 daemon fold scans the in-tree source and is sound only if
      the staged DATA file is a verbatim copy, so a non-blocking DATA tier would
      re-open that hole.
    * ``warning`` — reserved, non-blocking.
    * ``info`` — T3: the prefix is unresolvable (a hoisted / parameter-anchored
      root the fold cannot place) but the tail IS a script. ONE aggregate per
      file, never per site — a per-site flood trains readers to discount RC-164.

    ``tier`` carries a default so existing constructors keep working.
    """

    line_no: int  # 1-based
    message: str  # human-readable finding text
    tier: str = "critical"  # critical | major | warning | info


# ────────────────────────────────────────────────────────────────────────
# Script-file gate (§ "Script-file gate")
# ────────────────────────────────────────────────────────────────────────

# A destination with one of these suffixes is a SCRIPT / SOURCE file. A write
# of a NON-script file (``.json`` / ``.log`` / ``.cache`` / …) into the DATA
# dir is ALLOWED — DATA is the plugin's blessed writable home. Lowercased
# comparison; the leading dot is included.
_SCRIPT_EXTENSIONS: Final[frozenset[str]] = frozenset(
    {
        ".py",
        ".pyw",
        ".sh",
        ".bash",
        ".zsh",
        ".ksh",
        ".js",
        ".mjs",
        ".cjs",
        ".ts",
        ".tsx",
        ".rb",
        ".pl",
        ".pm",
        ".lua",
        ".ps1",
        ".psm1",
        ".bat",
        ".cmd",
        ".php",
        ".r",
        ".jl",
        ".applescript",
        ".scpt",
    }
)

# A shebang written INTO a destination makes it executable regardless of its
# extension — a heredoc body starting ``#!/usr/bin/env python3`` / ``#!/bin/sh``
# is a script even if the file is ``daemon`` or ``run`` with no suffix. re2-safe.
_SHEBANG_BODY_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"^#!\s*\S*/(?:env\s+)?(?:ba|z|k)?sh\b"),
    re.compile(r"^#!\s*\S*/(?:env\s+)?python\d?\b"),
    re.compile(r"^#!\s*\S*/(?:env\s+)?(?:perl|ruby|node|nodejs|php|lua)\d?\b"),
    re.compile(r"^#!\s*\S+"),  # any other interpreter shebang
)

# A ``chmod +x`` / ``chmod 7xx`` on a path makes that path executable — an
# explicit signal the written file is a runnable script even with no suffix.
# Group 1 is the target path token. re2-safe.
_CHMOD_EXEC_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"\bchmod\s+(?:-[A-Za-z]+\s+)*(?:\+x|a\+x|u\+x|[0-7]*[1357][0-7]*)\s+(\S+)"),
    re.compile(r"\bos\.chmod\s*\(\s*([^,]+),"),  # os.chmod(path, 0o755)
)


def _is_script_destination(dst: str) -> bool:
    """True iff ``dst`` has a recognised script / source-file suffix."""
    suffix = Path(dst.strip().strip("'\"")).suffix.lower()
    return suffix in _SCRIPT_EXTENSIONS


def _body_starts_with_shebang(body: str) -> bool:
    """True iff the first non-blank line of ``body`` is a shebang."""
    for raw in body.split("\n"):
        line = raw.strip()
        if not line:
            continue
        return any(p.search(line) for p in _SHEBANG_BODY_PATTERNS)
    return False


# ────────────────────────────────────────────────────────────────────────
# Copy carve-out (§ "Copy carve-out (ALLOW)")
# ────────────────────────────────────────────────────────────────────────

# A verbatim COPY of an in-tree source into the plugin tree is exactly what the
# rule PERMITS (the copied file was already CPV-scanned). These primitives copy
# bytes WITHOUT transforming them: ``shutil.copy/copy2/copyfile/copytree``,
# shell ``cp``, ``install`` (no ``-e``/edit transform), and the Python
# read-then-write idiom ``dst.write_bytes(src.read_bytes())`` /
# ``dst.write_text(src.read_text())`` — a destination fed VERBATIM from another
# file's read is a copy, not a generate, so the user's "copy a shipped script
# into DATA" allowance must not over-flag it. Their presence on a line means the
# line is a copy — ALLOW it. (v1 does not yet verify the read SOURCE is itself
# in-tree; a copy-from-external residual is tracked by TRDD-ETDWX70R, exactly as
# for ``cp``/``shutil.copy``.) re2-safe (no lookaround; the ``.*?`` is line-scoped).
_COPY_PRIMITIVE_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"\bshutil\.copy(?:file|tree|2|)\s*\("),
    re.compile(r"(?:^|[\s;&|])cp\s+"),
    re.compile(r"(?:^|[\s;&|])install\s+"),
    re.compile(r"\.write_bytes\s*\(.*?\.read_bytes\s*\("),
    re.compile(r"\.write_text\s*\(.*?\.read_text\s*\("),
)


def _line_is_copy(line: str) -> bool:
    """True iff ``line`` performs a verbatim-copy primitive (ALLOW)."""
    return any(p.search(line) for p in _COPY_PRIMITIVE_PATTERNS)


# ────────────────────────────────────────────────────────────────────────
# Write-primitive detection (§ "Write primitives detected")
# ────────────────────────────────────────────────────────────────────────

# Python file-creation / mutation primitives. Group 1 is the destination path
# expression (a quoted literal, a ``str``-valued expression, or a bare/dotted
# name). The destination is then FOLDED + resolved against the plugin root;
# a non-literal that does NOT fold (a ``$VAR`` / computed path) yields ``None``
# ⇒ PASS (lenient). re2-safe throughout.
_PY_WRITE_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    # open(DST, "w"|"a"|"x"|"w+"|"wb"|…) — a write/append/create mode.
    re.compile(
        r"(?:^|[^.\w])open\s*\(\s*([^,)]+?)\s*,\s*['\"][rwax+bt]*[wax][rwax+bt]*['\"]"
    ),
    # Path(DST).write_text(...) / .write_bytes(...)
    re.compile(r"(?:^|[^.\w])Path\s*\(\s*([^)]+?)\s*\)\s*\.\s*write_(?:text|bytes)\s*\("),
    # DST_VAR.write_text(...) / .write_bytes(...) — a Path object in a variable;
    # group 1 is the variable name (resolved as a bare name — folds to None
    # unless it is literally a plugin-root token, which is the lenient default).
    re.compile(r"(?:^|[^.\w])([A-Za-z_][\w.]*)\s*\.\s*write_(?:text|bytes)\s*\("),
    # os.open(DST, …O_WRONLY|O_CREAT…) — low-level write-mode open.
    re.compile(r"(?:^|[^.\w])os\.open\s*\(\s*([^,)]+?)\s*,"),
)

# Shell redirection / write primitives. Group 1 is the destination path token.
# ``> DST`` / ``>> DST`` / ``tee DST`` / ``sed -i … DST`` / heredoc opener
# ``cat … > DST``. The ``2>`` / ``&>`` fd-redirect forms are NOT matched as a
# plain ``> file`` (handled by requiring the ``>`` to be preceded by whitespace
# or line-start, not a digit). re2-safe.
_SHELL_WRITE_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    # `> DST` / `>> DST` — a redirect to a file path. The char BEFORE `>` is a
    # space, line-start, or `;`/`&`/`|` (NOT a digit → excludes `2>`/`1>`).
    # A QUOTED destination holding SPACES (`> "$(dirname "$0")/gen.sh"`) is
    # recovered by `_dest_token` below, which rescans the line from the capture
    # start with a tiny linear scanner. Doing it in the regex would need an
    # ambiguous `(?:\$\(…\)|[^"])*` alternation — a backtracking hazard on the
    # no-re2 path — so the SCANNER, not the pattern, owns nesting.
    re.compile(r"(?:^|[\s;&|])>>?\s*([^\s;&|<>]+)"),
    # `tee DST` / `tee -a DST`
    re.compile(r"(?:^|[\s;&|])tee\s+(?:-[A-Za-z]+\s+)*([^\s;&|<>]+)"),
    # `sed -i … DST` (edit a file in place). The last token is the file; we
    # capture a `.ext`-bearing token after the `sed -i` marker.
    re.compile(r"\bsed\s+(?:-[A-Za-z]*\s+)*-i[A-Za-z.]*\s+(?:-e\s+\S+\s+|'[^']*'\s+|\"[^\"]*\"\s+)*([^\s;&|<>]+)"),
)


def _dest_token(line: str, match: re.Match[str]) -> str:
    """The full destination token for ``match`` group 1.

    A bare `[^\\s…]+` capture stops at the first space, so a QUOTED destination
    containing a command substitution — `> "$(dirname "$0")/gen.sh"` — is
    truncated to `"$(dirname`. When the capture opens a quote, rescan the raw
    line from the capture start for the matching close quote, skipping `$(…)`
    spans (whose own inner quotes are not delimiters). Linear, no backtracking.
    """
    token = match.group(1)
    if not token or token[0] not in "\"'":
        return token
    quote = token[0]
    start = match.start(1)
    i = start + 1
    n = len(line)
    while i < n:
        if line.startswith("$(", i):
            depth = 1
            i += 2
            while i < n and depth:
                if line[i] == "(":
                    depth += 1
                elif line[i] == ")":
                    depth -= 1
                i += 1
            continue
        if line[i] == quote:
            return line[start : i + 1]
        i += 1
    return line[start:]

# A heredoc opener whose redirect targets a file: ``cat > DST <<EOF`` /
# ``cat >> DST <<'EOF'`` / ``tee DST <<EOF``. Group 1 is the destination. Used
# to recover the heredoc body (to script-gate it by a written shebang) AND the
# destination path. re2-safe (the delimiter is irrelevant here; the body walk is
# done separately).
# The destination capture spans SPACES (up to the `<<`) so a quoted
# `"$(dirname "$0")/gen.sh"` is recovered whole — the `[^<]` class keeps the
# match from crossing the heredoc opener, and the lazy quantifier is anchored by
# the mandatory `<<`, so there is no backtracking blow-up.
_HEREDOC_REDIRECT_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"(?:^|[\s;&|])(?:cat|printf|echo)\b[^\n<>]*>>?\s*([^\s<][^<]*?)\s*<<"),
    re.compile(r"(?:^|[\s;&|])tee\s+(?:-[A-Za-z]+\s+)*([^\s<][^<]*?)\s*<<"),
)

# The heredoc-body delimiter opener (to walk to the body's first line for the
# shebang script-gate). re2-safe — the delimiter may be unquoted, single-, or
# double-quoted (enumerated alternation, NO backreference).
_HEREDOC_OPEN_RE: Final[re.Pattern[str]] = re.compile(
    r"<<-?\s*(?:'([A-Za-z_]\w*)'|\"([A-Za-z_]\w*)\"|([A-Za-z_]\w*))"
)


def _heredoc_body_after(lines: list[str], opener_idx: int) -> str | None:
    """Return the heredoc body whose opener is on ``lines[opener_idx]``, or
    ``None`` if the opener has no recognised delimiter / is unterminated."""
    mo = _HEREDOC_OPEN_RE.search(lines[opener_idx])
    if mo is None:
        return None
    delim = mo.group(1) or mo.group(2) or mo.group(3)
    body: list[str] = []
    for nxt in lines[opener_idx + 1 :]:
        if nxt.strip() == delim:
            return "\n".join(body)
        body.append(nxt)
    return None  # unterminated → fail-safe (no body)


# ────────────────────────────────────────────────────────────────────────
# Resolution glue
# ────────────────────────────────────────────────────────────────────────


def _tail_has_script_suffix(tail: str) -> bool:
    """True iff a destination's trailing literal FRAGMENT carries a script
    suffix.

    Reuses ``_is_script_destination`` (never a second extension set — it already
    lowercases, so ``.PY`` is covered). A bare fragment such as ``".py"`` (from
    ``name + ".py"``) would read as a dot-FILE with no suffix, so a stem is
    prefixed before the test: the fragment is a SUFFIX of a longer name whose
    stem the attacker moved into a variable.
    """
    frag = tail.strip().strip("'\"").rsplit("/", 1)[-1]
    if not frag:
        return False
    return _is_script_destination("_" + frag)


def _destination_in_tree(
    dst_expr: str, plugin_root: Path, self_path: str | None = None
) -> bool:
    """True iff ``dst_expr`` PROVABLY resolves inside the plugin tree.

    Two-stage, both reusing ``cpv_persistence_target`` so the in-tree decision
    cannot drift from the #152 fold:

    * If the destination EXISTS already (an in-place edit of a shipped file),
      ``_resolve_in_tree`` confirms it is a regular file under the root.
    * Otherwise (a GENERATED file that does not exist yet) ``_fold_to_plugin_root``
      folds the env / data-dir literal to a concrete path string; we then
      require that folded path to be under the plugin root.

    A destination that does NOT fold (a ``$VAR`` / ``~`` / computed path, or a
    path outside the tree) yields FALSE ⇒ the caller PASSES (lenient fail-safe).
    """
    raw = dst_expr.strip().strip("'\"")
    if not raw:
        return False
    # Stage 1 — an existing in-tree regular file (an in-place edit).
    if _resolve_in_tree(raw, plugin_root, self_path) is not None:
        return True
    # Stage 2 — a generated destination that may not exist yet. Fold the env /
    # data-dir literal; require the folded path to live under the plugin root.
    folded = _fold_to_plugin_root(raw, plugin_root, self_path)
    if folded is None:
        return False
    try:
        p = Path(folded)
        if not p.is_absolute():
            p = plugin_root / p
        root_real = plugin_root.resolve()
        # Resolve only the EXISTING ancestor so a not-yet-created leaf still
        # resolves; ``Path.resolve()`` tolerates a missing tail.
        real = p.resolve()
        real.relative_to(root_real)
    except (OSError, ValueError, RuntimeError):
        return False
    return True


# ────────────────────────────────────────────────────────────────────────
# Public scan
# ────────────────────────────────────────────────────────────────────────


_PY_SOURCE_SUFFIXES: Final[frozenset[str]] = frozenset({".py", ".pyw"})

_NAME_CAPTURE_RE: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z_][\w.]*$")
_QUOTED_LITERAL_RE: Final[re.Pattern[str]] = re.compile(r"'([^']*)'|\"([^\"]*)\"")
# Wrapper calls that may legitimately precede a literal ROOT without making the
# prefix unknown; anything else identifier-shaped means an unresolvable root.
_WRAPPER_CALL_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(?:Path|PurePath|PosixPath|str|open|os\.fspath|os\.path\.join)\s*\("
)
_BARE_IDENTIFIER_RE: Final[re.Pattern[str]] = re.compile(r"[A-Za-z_]\w*")
_FENCE_MARKER_RE: Final[re.Pattern[str]] = re.compile(r"^\s*(?:```|~~~)")


def _fence_bounds(lines: list[str], idx: int, rel_path: str) -> tuple[int, int]:
    """The `[lo, hi)` line range a name lookup may search.

    In markdown the SAME variable can be rebound across fences, so a lookup is
    bounded to the fence holding the write; every other file type searches whole.
    """
    if not rel_path.lower().endswith((".md", ".markdown")):
        return 0, len(lines)
    lo = 0
    open_fence = False
    for i, line in enumerate(lines):
        if _FENCE_MARKER_RE.match(line):
            if open_fence:
                if lo <= idx < i:
                    return lo, i
                open_fence = False
            else:
                open_fence = True
                lo = i + 1
    return (lo, len(lines)) if open_fence and lo <= idx else (0, len(lines))


def _resolve_name_destination(
    lines: list[str], lo: int, hi: int, before_idx: int, name: str
) -> str | None:
    """Reduce ``NAME``'s last assignment before ``before_idx`` to a path candidate.

    This is the fix for the DEAD script-gate: pattern 3 (`VAR.write_text(...)`)
    captures the VARIABLE NAME, so `_is_script_destination` was testing a bare
    identifier and could never see a suffix. The candidate is the RHS's quoted
    string literals joined by `/` — enough for `VAR = "$CLAUDE_PLUGIN_DATA/x.py"`
    and for `VAR = Path(__file__).parent / "hook.py"`. An RHS with no literal is
    UNRESOLVABLE and yields ``None`` ⇒ the caller emits NOTHING, because a bare
    unresolvable name carries no in-tree evidence at all and blocking every
    `p.write_text(...)` in a doc fence would be a mass over-block.
    """
    pattern = re.compile(r"^\s*" + re.escape(name) + r"\s*(?::[^=]+)?=\s*(.+?)\s*$")
    rhs: str | None = None
    for i in range(max(lo, 0), min(before_idx, hi)):
        mo = pattern.match(lines[i])
        if mo is not None:
            rhs = mo.group(1)
    if rhs is None:
        return None
    first_quote = min(
        (i for i in (rhs.find("'"), rhs.find('"')) if i >= 0),
        default=-1,
    )
    if first_quote < 0:
        return None
    # An IDENTIFIER before the first literal is an unknown ROOT (`tmp_path /
    # "x.py"`). Keeping only the literals would manufacture a bare relative
    # path, which the fold then resolves against the plugin root and reports as
    # in-tree — an in-tree claim the expression never supports. Same rule the
    # AST path enforces via `has_unknown_prefix`; only wrapper calls may precede.
    head = _WRAPPER_CALL_RE.sub("", rhs[:first_quote])
    if _BARE_IDENTIFIER_RE.search(head):
        return None
    parts = [(a or b) for a, b in _QUOTED_LITERAL_RE.findall(rhs) if (a or b)]
    parts = [p.strip("/") for p in parts if p.strip("/")]
    if not parts:
        return None
    return "/".join(parts)


def _regex_tier(
    dst: str, plugin_root: Path, self_path: str
) -> str | None:
    """Tier a REGEX-path destination, or ``None`` for no finding.

    ``critical`` — the whole destination folds in-tree AND is a script.
    ``major``   — the destination's HEAD folds in-tree but a residual ``$VAR``
                  remains in its TAIL, so the fold cannot say whether a script
                  lands in-tree. Without this a bash generator writing
                  ``"$CLAUDE_PLUGIN_DATA/$name"`` sidesteps the gate entirely
                  (the whole path fails to fold, so today it is silent).
    """
    # The script-ness test runs on the FOLDED path too: `> "$0"` carries no
    # suffix of its own, but folds to this very script — a self-rewrite, and a
    # script by definition. Judging only the raw token missed it entirely.
    folded = _fold_to_plugin_root(dst, plugin_root, self_path)
    is_script = _is_script_destination(dst) or (
        folded is not None and _is_script_destination(folded)
    )
    if is_script and _destination_in_tree(dst, plugin_root, self_path):
        return "critical"
    raw = dst.strip().strip("'\"")
    if "/" not in raw:
        return None
    head, tail = raw.rsplit("/", 1)
    if not tail or not _RESIDUAL_VAR_RE.search(tail):
        return None
    return "major" if _destination_in_tree(head, plugin_root, self_path) else None


def inplugin_script_write_findings(
    content: str,
    rel_path: str,
    plugin_root: Path,
) -> list[WriteFinding]:
    """Scan ``content`` for writes that create / modify a SCRIPT file inside the
    plugin tree (ROOT or DATA) and are NOT verbatim copies.

    Dispatch (TRDD-ETDWX70R): a ``.py`` file that PARSES has its PYTHON write
    primitives judged by the AST path ONLY — those are the sole overlap between
    the two paths, so suppressing exactly them is what "no regex double-report"
    buys. The SHELL surface of the same file (a heredoc, a ``sed -i``, a ``>``
    redirect, a ``chmod +x`` — all reachable from Python through
    ``os.system`` / ``subprocess``) is NOT visible to the AST walk, so the regex
    path still runs over it; dropping it would be a straight false negative
    against the pre-TRDD behaviour. A ``SyntaxError`` falls back to the full
    regex path (fail-closed, the RC-70 idiom); ``.md`` / ``.sh`` / anything else
    uses the full regex path, which now folds the script's own location too.

    ``rel_path`` is the file's plugin-relative path — the finding message AND
    the ``self_path`` the ``$0`` / ``__file__`` fold resolves against.
    """
    if Path(rel_path).suffix.lower() in _PY_SOURCE_SUFFIXES:
        from cpv_write_sink_ast import collect_ast_write_sinks  # local: import cycle

        sinks = collect_ast_write_sinks(content, rel_path)
        if sinks is not None:
            findings = _ast_path_findings(sinks, rel_path, plugin_root)
            seen = {f.line_no for f in findings}
            findings.extend(
                f
                for f in _regex_path_findings(
                    content, rel_path, plugin_root, include_py_patterns=False
                )
                if f.line_no not in seen
            )
            return findings

    return _regex_path_findings(content, rel_path, plugin_root)


def _regex_path_findings(
    content: str,
    rel_path: str,
    plugin_root: Path,
    include_py_patterns: bool = True,
) -> list[WriteFinding]:
    """The line-oriented scan (heredoc, chmod, Python primitives, shell writes).

    ``include_py_patterns=False`` drops ONLY ``_PY_WRITE_PATTERNS`` — used when
    the AST path already judged this file's Python writes, so the shell surface
    embedded in it is still scanned without double-reporting.
    """
    findings: list[WriteFinding] = []
    lines = content.split("\n")

    for idx, line in enumerate(lines):
        line_no = idx + 1
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        # Copy carve-out FIRST — a verbatim copy is always ALLOWED, even when
        # its destination is in-plugin (that is the rule's whole point).
        if _line_is_copy(line):
            continue

        # ── Heredoc writes (recover BOTH the destination and the body so a
        # body-shebang can satisfy the script-gate for an extension-less file).
        heredoc_hit = False
        for pat in _HEREDOC_REDIRECT_PATTERNS:
            mo = pat.search(line)
            if mo is None:
                continue
            dst = mo.group(1)
            if not _destination_in_tree(dst, plugin_root, rel_path):
                continue  # lenient — unresolvable / out-of-tree destination
            body = _heredoc_body_after(lines, idx)
            is_script = _is_script_destination(dst) or (
                body is not None and _body_starts_with_shebang(body)
            )
            if is_script:
                findings.append(
                    WriteFinding(
                        line_no,
                        f"in-plugin script generated via heredoc into '{dst}' "
                        f"(generate/edit of an unscanned in-plugin script is "
                        f"forbidden; only a verbatim copy is allowed) [{rel_path}]",
                    )
                )
                heredoc_hit = True
            break
        if heredoc_hit:
            continue

        # ── chmod +x on an in-plugin path makes that path a runnable script.
        chmod_hit = False
        for pat in _CHMOD_EXEC_PATTERNS:
            # ``os.chmod`` is a PYTHON primitive — part of the overlap the AST
            # path already judges (and judges better: it reads the mode bits,
            # where this pattern flags any mode at all). The shell ``chmod +x``
            # form stays, because a shell command inside a string is invisible
            # to the AST walk.
            if not include_py_patterns and "os\\.chmod" in pat.pattern:
                continue
            mo = pat.search(line)
            if mo is None:
                continue
            target = mo.group(1)
            if _destination_in_tree(target, plugin_root, rel_path):
                findings.append(
                    WriteFinding(
                        line_no,
                        f"in-plugin path '{target.strip()}' made executable "
                        f"(chmod +x) — marks an in-plugin script as runnable "
                        f"[{rel_path}]",
                    )
                )
                chmod_hit = True
            break
        if chmod_hit:
            continue

        # ── Python write primitives.
        py_hit = False
        for pat in _PY_WRITE_PATTERNS if include_py_patterns else ():
            mo = pat.search(line)
            if mo is None:
                continue
            dst = mo.group(1)
            if _NAME_CAPTURE_RE.match(dst.strip()):
                # Pattern 3 captures a VARIABLE NAME (`VAR.write_text(...)`), so
                # the script-gate below was reading a bare identifier and could
                # never fire. Resolve the name to its literal tail first.
                lo, hi = _fence_bounds(lines, idx, rel_path)
                resolved = _resolve_name_destination(lines, lo, hi, idx, dst.strip())
                if resolved is None:
                    continue  # unresolvable name → no in-tree evidence → PASS
                dst = resolved
            tier = _regex_tier(dst, plugin_root, rel_path)
            if tier is None:
                continue
            findings.append(
                WriteFinding(
                    line_no,
                    f"in-plugin script written via Python primitive to '{dst.strip()}' "
                    f"(generate/edit of an unscanned in-plugin script is forbidden; "
                    f"only a verbatim copy is allowed) [{rel_path}]"
                    if tier == "critical"
                    else f"in-plugin write to '{dst.strip()}' has an UNRESOLVED tail under "
                    f"an in-plugin prefix — the fold cannot prove whether a script "
                    f"lands inside the plugin tree [{rel_path}]",
                    tier,
                )
            )
            py_hit = True
            break
        if py_hit:
            continue

        # ── Shell write primitives (`> DST`, `>> DST`, `tee DST`, `sed -i`).
        for pat in _SHELL_WRITE_PATTERNS:
            mo = pat.search(line)
            if mo is None:
                continue
            dst = _dest_token(line, mo)
            tier = _regex_tier(dst, plugin_root, rel_path)
            if tier is None:
                continue
            findings.append(
                WriteFinding(
                    line_no,
                    f"in-plugin script written via shell redirect to '{dst.strip()}' "
                    f"(generate/edit of an unscanned in-plugin script is forbidden; "
                    f"only a verbatim copy is allowed) [{rel_path}]"
                    if tier == "critical"
                    else f"in-plugin write to '{dst.strip()}' has an UNRESOLVED tail under "
                    f"an in-plugin prefix — the fold cannot prove whether a script "
                    f"lands inside the plugin tree [{rel_path}]",
                    tier,
                )
            )
            break

    return findings


# ────────────────────────────────────────────────────────────────────────
# AST path — tiering (TRDD-ETDWX70R)
# ────────────────────────────────────────────────────────────────────────


def _ast_path_findings(
    sinks: list["AstWriteSink"], rel_path: str, plugin_root: Path
) -> list[WriteFinding]:
    """Tier every AST write sink; aggregate the T3 sites into ONE info."""
    findings: list[WriteFinding] = []
    seen_lines: set[int] = set()
    unresolved_sites: list[int] = []

    for sink in sinks:
        if sink.copy_idiom:
            continue
        rendered = sink.rendered
        is_script = sink.script_evidence is not None or (
            rendered.literal_tail is not None
            and _tail_has_script_suffix(rendered.literal_tail)
        )
        if rendered.has_unknown_prefix:
            # No placeable root — the destination can be neither proven in-tree
            # nor proven out of it, so it can only ever be the T3 advisory.
            if is_script:
                unresolved_sites.append(sink.line_no)
            continue
        foldable = rendered.foldable
        in_tree = bool(foldable.strip()) and _destination_in_tree(
            foldable, plugin_root, rel_path
        )
        if in_tree:
            if is_script:
                tier = "critical"
                why = (
                    "shebang body"
                    if sink.script_evidence == "shebang"
                    else "chmod +x" if sink.script_evidence == "chmod" else "script suffix"
                )
                message = (
                    f"in-plugin script written via {sink.sink} to "
                    f"'{sink.dest_text[:120]}' ({why}; generate/edit of an unscanned "
                    f"in-plugin script is forbidden; only a verbatim copy is allowed) "
                    f"[{rel_path}]"
                )
            elif rendered.literal_tail is None:
                # T2 — the PREFIX lands in-plugin but the final component is
                # computed, so the fold cannot say whether a script lands in the
                # tree. Blocking for BOTH anchor kinds: a non-blocking DATA tier
                # would re-open the #152 staged-daemon hole.
                tier = "major"
                message = (
                    f"in-plugin write via {sink.sink} to '{sink.dest_text[:120]}' has an "
                    f"UNRESOLVED final component under an in-plugin prefix — the fold "
                    f"cannot prove whether a script lands inside the plugin tree "
                    f"[{rel_path}]"
                )
            else:
                continue  # literal, non-script tail → today's verdict (nothing)
            if sink.line_no in seen_lines:
                continue
            seen_lines.add(sink.line_no)
            findings.append(WriteFinding(sink.line_no, message, tier))

    if unresolved_sites:
        ordered = sorted(set(unresolved_sites))
        shown = ", ".join(str(n) for n in ordered[:3])
        more = "" if len(ordered) <= 3 else f", … (+{len(ordered) - 3} more)"
        findings.append(
            WriteFinding(
                ordered[0],
                f"{len(ordered)} script write(s) in this file are anchored to a root "
                f"the fold cannot place (a hoisted or parameter-anchored path), so "
                f"CPV cannot decide whether they land inside the plugin tree — "
                f"line(s) {shown}{more}. Advisory only: re-express the destination "
                f"through ${{CLAUDE_PLUGIN_DATA}} or a literal path to make it "
                f"decidable [{rel_path}]",
                "info",
            )
        )

    return findings
