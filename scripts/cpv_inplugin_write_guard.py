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

import os
import re
from pathlib import Path
from typing import TYPE_CHECKING, Final, NamedTuple

from cpv_surface_class import is_documentation_only_path

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


# Path-shape gate for the SHELL ``chmod`` capture (TRDD-RU0POO65). The capture is
# "the next whitespace-delimited token", and the fold resolves any relative bare
# word against the plugin root — so prose ("NOT chmod +x (unlike …", "chmod +x
# it", "chmod +x 12 scripts", "`chmod +x script.sh`") folded "in-tree" and fired
# a blocking RC-164. A shell word only counts as a chmod TARGET when it can be a
# path: every char is a path char (a backtick / paren / comma means prose or a
# command substitution — unresolvable either way), AND it carries path evidence:
# a `/`, a `$`/`~` anchor the fold decides on, a dotted suffix, or it names a
# file that already exists in the plugin tree. re2-safe.
_CHMOD_PATH_TOKEN_RE: Final[re.Pattern[str]] = re.compile(r"^[\w.@+~${}/-]+$")
# A `{` NOT opening a `${VAR}` is a template placeholder (a Python f-string in a
# message such as "run: chmod +x scripts/{name}"), never one literal path.
_BARE_BRACE_RE: Final[re.Pattern[str]] = re.compile(r"(?:^|[^$])\{")
_DOTTED_SUFFIX_RE: Final[re.Pattern[str]] = re.compile(r"[^./]\.[A-Za-z0-9]{1,8}$")


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


def _skip_cmdsub(line: str, i: int) -> int:
    """Index just past the balanced ``$( … )`` span that opens at ``line[i]``."""
    depth = 1
    i += 2
    n = len(line)
    while i < n and depth:
        if line[i] == "(":
            depth += 1
        elif line[i] == ")":
            depth -= 1
        i += 1
    return i


def _dest_token(line: str, match: re.Match[str], group: int = 1) -> str:
    """The full destination token for ``match`` group ``group``.

    A bare `[^\\s…]+` capture stops at the first space, so a QUOTED destination
    containing a command substitution — `> "$(dirname "$0")/gen.sh"` — is
    truncated to `"$(dirname`. When the capture opens a quote, rescan the raw
    line from the capture start for the matching close quote, skipping `$(…)`
    spans (whose own inner quotes are not delimiters). Linear, no backtracking.

    An UNQUOTED word holding a command substitution with a space inside —
    `> public/p/$(cat .token)/feed.xml` — was truncated the same way, to
    `public/p/$(cat`, which invented a residual-var TAIL and fired a T2
    "unresolved" finding on a write whose real file name is the literal
    non-script `feed.xml` (census, eins78 README). The word is therefore
    extended across balanced `$( … )` spans up to the first unquoted separator.
    """
    token = match.group(group)
    start = match.start(group)
    n = len(line)
    if not token:
        return token
    if token[0] not in "\"'":
        if "$(" not in token:
            return token
        i = start
        while i < n:
            if line.startswith("$(", i):
                i = _skip_cmdsub(line, i)
                continue
            if line[i] in " \t;&|<>)":
                break
            i += 1
        return line[start:i]
    quote = token[0]
    i = start + 1
    while i < n:
        if line.startswith("$(", i):
            i = _skip_cmdsub(line, i)
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


# Shell positional / special parameters: `$1`…`$9`, `${10}`, `$@`, `$*`, `$#`,
# `$?`, `$$`, `$!`, `$-` (class D). The shared `_RESIDUAL_VAR_RE` recognises only
# NAMED variables, so `> "$1/review.md"` survived the fold as the literal
# relative path `$1/review.md` and was reported as landing in the plugin root.
# A positional is a caller-supplied value — exactly as unknown as any `$VAR`.
# Kept guard-local on purpose: the persistence fold owns its own residual set.
# The brace form also covers `${1:-default}` / `${1#prefix}` / `${#}`: any
# expansion whose parameter is positional or special is caller-supplied.
_POSITIONAL_PARAM_RE: Final[re.Pattern[str]] = re.compile(r"\$\{?[1-9@*#?!$-]")
# `$0` / `$BASH_SOURCE` name the RUNNING SHELL SCRIPT. `_fold_self_path` folds
# them to the scanned file — correct only when the scanned file IS that shell
# script. In a `.md` shell fence, a Python/JS source, or a JS comment
# (`cost > $0`, census: llm-externalizer security_scan.test.ts) the token does
# not name the scanned file at all, so there it is as unknown as `$1`.
_SELF_PARAM_RE: Final[re.Pattern[str]] = re.compile(r"\$\{?(?:0\b|BASH_SOURCE)")
_SHELL_SCRIPT_SUFFIXES: Final[frozenset[str]] = frozenset({".sh", ".bash", ".zsh", ".ksh"})


def _has_unplaceable_param(raw: str, self_path: str | None) -> bool:
    """True iff ``raw`` holds a shell parameter the fold cannot place."""
    if _POSITIONAL_PARAM_RE.search(raw):
        return True
    return self_path is None and _SELF_PARAM_RE.search(raw) is not None


def _is_shell_script(rel_path: str, lines: list[str]) -> bool:
    """True iff the scanned file is itself a shell script (so `$0` is its path)."""
    suffix = Path(rel_path).suffix.lower()
    if suffix in _SHELL_SCRIPT_SUFFIXES:
        return True
    return suffix == "" and bool(lines) and _SHEBANG_BODY_PATTERNS[0].search(lines[0].strip()) is not None


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
    if not raw or _has_unplaceable_param(raw, self_path):
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
    return _literal_path_candidate(rhs)


def _literal_path_candidate(rhs: str) -> str | None:
    """Reduce a Python path EXPRESSION to its quoted literals joined by `/`.

    ``None`` when the expression has no literal, or an identifier precedes its
    first literal (an unknown ROOT) — see ``_resolve_name_destination``.
    """
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


def _chmod_target(
    raw: str,
    is_os_chmod: bool,
    lines: list[str],
    idx: int,
    rel_path: str,
    plugin_root: Path,
    self_path: str | None,
    written: set[str],
) -> str | None:
    """The chmod target as a path candidate, or ``None`` when it is not one.

    ``os.chmod(NAME, …)`` captures a Python VARIABLE: resolve it to the literal
    it was bound to, exactly as the ``VAR.write_text`` path does. Any other
    ``os.chmod`` argument is reduced to its quoted literals the same way; an
    unbound name, a ``sys.argv[1]`` subscript, or a ``build_path()`` call has
    no placeable root and yields ``None`` — the caller records it for the T3
    advisory (the v5.17.0 tier contract), never a blocking verdict. Before, a
    non-name argument was folded as if its SOURCE TEXT were a relative path, so
    ``build_path()`` "landed in-tree" and fired CRITICAL.

    A SHELL chmod word must pass the path-shape gate above. A bare word is a
    path when that file already exists in the tree OR when this script already
    WROTE it (``written``): ``printf '…' > hook`` then ``chmod +x hook`` is the
    classic generate-then-mark-runnable pair, and 9795a4c0's bare-word gate had
    silenced the chmod half of it. Prose (``chmod +x it``) has no prior write.
    """
    if is_os_chmod:
        tok = raw.strip()
        if not _NAME_CAPTURE_RE.match(tok):
            return _literal_path_candidate(tok)
        lo, hi = _fence_bounds(lines, idx, rel_path)
        return _resolve_name_destination(lines, lo, hi, idx, tok)
    # Trailing shell separators glued to the word (`chmod +x a.sh;`) are not
    # part of the path.
    tok = raw.strip().rstrip(";&|,").strip("'\"")
    if not tok or not _CHMOD_PATH_TOKEN_RE.match(tok) or _BARE_BRACE_RE.search(tok):
        return None
    if "/" in tok or tok[0] in "$~" or _DOTTED_SUFFIX_RE.search(tok) or tok in written:
        return tok
    return tok if _resolve_in_tree(tok, plugin_root, self_path) is not None else None


def _unresolved(text: str, plugin_root: Path, self_path: str | None) -> bool:
    """True iff the fold cannot turn ``text`` into a concrete path."""
    return _has_unplaceable_param(text, self_path) or (
        _fold_to_plugin_root(text, plugin_root, self_path) is None
    )


def _regex_tier(
    dst: str, plugin_root: Path, self_path: str | None
) -> str | None:
    """Tier a REGEX-path destination, or ``None`` for no finding.

    ``critical`` — the whole destination folds in-tree AND is a script.
    ``major``   — the longest resolvable PREFIX folds in-tree but a later
                  component is unresolved, so the fold cannot say whether a
                  script lands in-tree. Without this a bash generator writing
                  ``"$CLAUDE_PLUGIN_DATA/$name"`` sidesteps the gate entirely
                  (the whole path fails to fold, so it would be silent).
                  When the FINAL component is a literal non-script name
                  (``…/$(cat .token)/feed.xml``) the written file is provably
                  not a script whatever the middle resolves to → no finding.
    """
    # The script-ness test runs on the FOLDED path too: `> "$0"` carries no
    # suffix of its own, but folds to this very script — a self-rewrite, and a
    # script by definition. Judging only the raw token missed it entirely.
    folded = _fold_to_plugin_root(dst, plugin_root, self_path)
    is_script = _is_script_destination(dst) or (
        folded is not None
        and (
            _is_script_destination(folded)
            # An extension-less shell script (`hooks/run`) rewriting itself via
            # `$0` is a script by definition too — only a SHELL script reaches
            # here with a non-None ``self_path``.
            or (self_path is not None and Path(folded) == plugin_root / self_path)
        )
    )
    if is_script and _destination_in_tree(dst, plugin_root, self_path):
        return "critical"
    raw = dst.strip().strip("'\"")
    if "/" not in raw or not _unresolved(raw, plugin_root, self_path):
        return None
    parts = raw.split("/")
    last = parts[-1]
    if not last or (
        not _unresolved(last, plugin_root, self_path) and not _tail_has_script_suffix(last)
    ):
        return None
    head = ""
    for k in range(1, len(parts)):
        candidate = "/".join(parts[:k]) or "/"  # a leading "" is the fs root
        if _unresolved(candidate, plugin_root, self_path):
            break
        head = candidate
    if not head:
        return None
    return "major" if _destination_in_tree(head, plugin_root, self_path) else None


# ────────────────────────────────────────────────────────────────────────
# Per-line context: markdown scopes (classes A / C) and shell cwd (class B)
# ────────────────────────────────────────────────────────────────────────

_DATA_ONLY_SUFFIXES: Final[frozenset[str]] = frozenset({".jsonl", ".ndjson"})
_BLOCKQUOTE_RE: Final[re.Pattern[str]] = re.compile(r"^\s*(?:>[ \t]?)+")
_FENCE_OPEN_RE: Final[re.Pattern[str]] = re.compile(r"^\s*(?:```|~~~)\s*([^\s`{]*)")
# Fence info strings whose body is SHELL — the only fences where a `cd` moves
# the working directory the next line writes into.
_SHELL_FENCE_LANGS: Final[frozenset[str]] = frozenset(
    {"bash", "sh", "shell", "zsh", "ksh", "console", "shell-session", "shellsession", "terminal"}
)


class _LineCtx(NamedTuple):
    scope: int  # changes at every fence boundary — per-scope state resets
    marker: bool  # the fence line itself
    prose: bool  # markdown text OUTSIDE any fence
    shell: bool  # shell semantics apply (a shell script / a shell-tagged fence)


def _line_contexts(lines: list[str], rel_path: str, shell_file: bool) -> list[_LineCtx]:
    """Classify every line; a non-markdown file is one code scope."""
    if not rel_path.lower().endswith((".md", ".markdown")):
        return [_LineCtx(0, False, False, shell_file)] * len(lines)
    out: list[_LineCtx] = []
    scope = 0
    lang: str | None = None  # None = outside a fence; "" = an untagged fence
    for line in lines:
        mo = _FENCE_OPEN_RE.match(line)
        if mo is not None:
            scope += 1
            lang = mo.group(1).lower() if lang is None else None
            out.append(_LineCtx(scope, True, False, False))
        elif lang is None:
            out.append(_LineCtx(scope, False, True, False))
        else:
            out.append(_LineCtx(scope, False, False, lang in _SHELL_FENCE_LANGS))
    return out


def _heredoc_delimiters(line: str) -> list[str]:
    """Delimiters of the heredocs a shell line opens (``<<<`` herestrings excluded)."""
    delims: list[str] = []
    for mo in _HEREDOC_OPEN_RE.finditer(line):
        start = mo.start()
        if line[start + 2 : start + 3] == "<" or (start and line[start - 1] == "<"):
            continue
        delims.append(mo.group(1) or mo.group(2) or mo.group(3))
    return delims


def _bare(dst: str) -> str:
    return dst.strip().strip("'\"")


def _rebase(dst: str, cwd: Path | None, root_n: Path) -> str | None:
    """Re-anchor a RELATIVE destination on the shell's current directory.

    Class B: a relative path is relative to the cwd, and the fold resolves every
    relative path against the plugin root — right only while the cwd IS the
    root. After ``cd "$WORK"`` / ``cd "$(mktemp -d)"`` / ``cd "$1"`` the cwd is
    unknown (``None``) and a relative write cannot be placed → ``None`` (no
    finding, the lenient rule). An anchored destination (``/…``, ``$VAR``,
    ``$0``, ``~``) does not depend on the cwd and is returned unchanged.
    """
    raw = _bare(dst)
    if not raw or raw[0] in "/$~`":
        return dst
    if cwd is None:
        return None
    return dst if cwd == root_n else str(cwd / raw)


# `cd` / `pushd` / `popd` in COMMAND position: line start, or after `;`, `&`,
# `|`, `(`, `{`, or a `then` / `do` / `else` keyword. Group 2 is the target.
_CD_RE: Final[re.Pattern[str]] = re.compile(
    r"(?:^|[;&|({]|\b(?:then|do|else)\b)\s*(cd|pushd|popd)\b"
    r"(?:[ \t]+(?:-[LPe@]+[ \t]+|--[ \t]+)*([^\s;&|)]+))?"
)


_ASSIGN_RE: Final[re.Pattern[str]] = re.compile(
    r"^\s*(?:(?:export|local|readonly|declare(?:\s+-\w+)*)\s+)?([A-Za-z_]\w*)=([^\s;&|]+)"
)
_VAR_REF_RE: Final[re.Pattern[str]] = re.compile(r"\$\{([A-Za-z_]\w*)\}|\$([A-Za-z_]\w*)")
_ENV_DEFAULT_RE: Final[re.Pattern[str]] = re.compile(
    r"\$\{(CLAUDE_PLUGIN_ROOT|CLAUDE_PLUGIN_DATA):?[-=][^}]*\}"
)
_CD_PWD_RE: Final[re.Pattern[str]] = re.compile(
    r"^\$\(\s*cd\s+(.+?)\s*(?:&&|;)\s*pwd(?:\s+-P)?\s*\)$"
)


def _quoted_at(line: str, pos: int) -> bool:
    """Line-local quote state before ``pos``. Used only to IGNORE a quoted `cd`
    (``echo "then cd /tmp"``): a misread can only keep the cwd at the plugin
    root, i.e. judge MORE writes — never fewer."""
    quote = ""
    i = 0
    while i < pos:
        ch = line[i]
        if ch == "\\" and quote != "'":
            i += 2
            continue
        if quote:
            if ch == quote:
                quote = ""
        elif ch in "'\"":
            quote = ch
        i += 1
    return bool(quote)


class _ShellCwd:
    """The working directory of a shell scope, line by line (class B).

    Block scoping is by INDENTATION: a `cd` on an indented line (a function
    body, a subshell block, an `if` arm) holds only until a line indented LESS
    than it. For a function body that is exactly right; for an `if` arm it
    reverts the cwd to the outer value early — which judges MORE writes, never
    fewer. A `(cd …)` / `$(cd …)` subshell on one line moves the cwd for the
    rest of THAT line only.
    """

    def __init__(self, root_n: Path) -> None:
        self.root_n = root_n
        self.frames: list[tuple[int, Path | None]] = [(-1, root_n)]
        self.dirstack: list[Path | None] = []
        # `NAME=value` assignments seen so far. A `cd "$SCRIPT_DIR"` whose
        # variable was set to `$(cd "$(dirname "$0")" && pwd)` is a cd INTO the
        # plugin — without this every such script would lose its cwd and every
        # later relative in-plugin write would go silent (a false negative).
        self.vars: dict[str, str] = {}

    def current(self) -> Path | None:
        return self.frames[-1][1]

    def _expand(self, raw: str) -> str:
        """Substitute known `$NAME` / `${NAME}` values (bounded depth)."""
        for _ in range(4):
            new = _VAR_REF_RE.sub(lambda m: self.vars.get(m.group(1) or m.group(2), m.group(0)), raw)
            if new == raw:
                break
            raw = new
        # `$(cd DIR && pwd)` IS `DIR` (the idiom that canonicalises a path);
        # unwrapping it lets `$(dirname "$0")/..` reach the self-path fold.
        wrapped = _CD_PWD_RE.match(raw)
        if wrapped is not None:
            raw = _bare(wrapped.group(1))
        # `${CLAUDE_PLUGIN_ROOT:-fallback}` is the plugin root whenever it runs
        # under Claude Code — fold it like the bare variable.
        return _ENV_DEFAULT_RE.sub(r"${\1}", raw)

    def _target(
        self, target: str | None, cur: Path | None, plugin_root: Path, self_path: str | None
    ) -> Path | None:
        raw = self._expand(_bare(target or ""))
        if not raw or raw == "-":
            return None  # bare `cd` → $HOME; `cd -` → the previous dir
        if raw[0] in "/$~`":
            if _has_unplaceable_param(raw, self_path):
                return None
            folded = _fold_to_plugin_root(raw, plugin_root, self_path)
            if folded is None:
                return None
            p = Path(folded)
            return Path(os.path.normpath(p if p.is_absolute() else self.root_n / p))
        return None if cur is None else Path(os.path.normpath(cur / raw))

    def advance(
        self, line: str, plugin_root: Path, self_path: str | None
    ) -> tuple[Path | None, list[tuple[int, Path | None]]]:
        """(cwd at the start of ``line``, [(column, cwd after each cd)])."""
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            return self.current(), []
        indent = len(line) - len(line.lstrip())
        while len(self.frames) > 1 and self.frames[-1][0] > indent:
            self.frames.pop()
        assign = _ASSIGN_RE.match(line)
        if assign is not None:
            self.vars[assign.group(1)] = _bare(_dest_token(line, assign, 2))
        before = cur = self.current()
        persist: Path | None = before
        changed = False
        events: list[tuple[int, Path | None]] = []
        for mo in _CD_RE.finditer(line):
            pos = mo.start(1)
            if _quoted_at(line, pos):
                continue
            verb = mo.group(1)
            if verb == "popd":
                new = self.dirstack.pop() if self.dirstack else None
            else:
                target = _dest_token(line, mo, 2) if mo.group(2) else None
                new = self._target(target, cur, plugin_root, self_path)
                if verb == "pushd":
                    self.dirstack.append(cur)
            cur = new
            events.append((pos, cur))
            if not line[:pos].rstrip().endswith("("):
                persist, changed = cur, True
        if changed:
            if self.frames[-1][0] == indent:
                self.frames[-1] = (indent, persist)
            else:
                self.frames.append((indent, persist))
        return before, events


def _cwd_at(
    before: Path | None, events: list[tuple[int, Path | None]], pos: int
) -> Path | None:
    """The shell's cwd at column ``pos``: the last `cd` left of it, else ``before``."""
    here = before
    for cd_pos, after in events:
        if cd_pos < pos:
            here = after
    return here


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
    the ``self_path`` the ``__file__`` fold (AST) and, for a shell script only,
    the ``$0`` fold (regex) resolve against.
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

    Markdown (see ``_line_contexts``): each fence is its own scope, prose outside
    fences is judged by the class A / C rules, and a documentation-only file's
    prose is not scanned at all. Shell scopes (a shell script, a shell-tagged
    fence) additionally track the working directory (class B, ``_ShellCwd``).
    """
    # A JSON Lines file is a stream of DATA records: no Claude Code surface
    # executes it or loads it as instructions, so there is no program whose
    # write RC-164 could be judging. Census: a detector-bench corpus record
    # quoting `chmod +x evil.sh` as test input fired a blocking RC-164.
    if Path(rel_path).suffix.lower() in _DATA_ONLY_SUFFIXES:
        return []
    findings: list[WriteFinding] = []
    lines = content.split("\n")
    self_path = rel_path if _is_shell_script(rel_path, lines) else None
    doc_only = is_documentation_only_path(rel_path)
    contexts = _line_contexts(lines, rel_path, self_path is not None)
    root_n = Path(os.path.normpath(plugin_root))
    unresolved_chmod: list[int] = []
    scope = -1
    cwd = _ShellCwd(root_n)
    written: set[str] = set()
    heredoc_delims: list[str] = []

    for idx, line in enumerate(lines):
        line_no = idx + 1
        ctx = contexts[idx]
        if ctx.scope != scope:
            # A new fence is a new program: its cwd and its written files are
            # its own, never inherited from the fence before it.
            scope = ctx.scope
            cwd = _ShellCwd(root_n)
            written = set()
            heredoc_delims = []
        if ctx.marker:
            continue
        if ctx.prose:
            # Class C (part 1): a documentation-only surface (CHANGELOG,
            # README, docs/) is prose for a HUMAN reader — it is not a program
            # and no agent loads it as instructions, so no sentence in it can
            # perform the write RC-164 judges.
            if doc_only:
                continue
            # Class A: a line-leading `>` in markdown prose is a BLOCKQUOTE
            # marker (`  > resolve_pillar_scripts.sh — …`), never a redirect.
            # Inside a fence the same `>` IS a redirect and is left alone.
            line = _BLOCKQUOTE_RE.sub("", line, count=1)

        # Heredoc bodies are file CONTENT, not commands: a `cd` written into a
        # generated script must not move this script's cwd.
        if heredoc_delims:
            if line.strip() == heredoc_delims[0]:
                heredoc_delims.pop(0)
            cwd_events: list[tuple[int, Path | None]] = []
            cwd_before = cwd.current()
        elif ctx.shell:
            cwd_before, cwd_events = cwd.advance(line, plugin_root, self_path)
            heredoc_delims.extend(_heredoc_delimiters(line))
        else:
            cwd_before, cwd_events = root_n, []

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
            written.add(_bare(dst))
            judged = _rebase(dst, _cwd_at(cwd_before, cwd_events, mo.start(1)), root_n)
            if judged is None or not _destination_in_tree(judged, plugin_root, self_path):
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
        # Class C (part 2): a chmod in markdown PROSE never counts, on any
        # surface. chmod creates no content, so on its own it cannot put an
        # unscanned script in the tree; RC-164 uses it only as script EVIDENCE
        # for a write in the same program — and prose has no program flow. A
        # prose instruction that really generates a script carries the WRITE,
        # which is still judged on an instruction-loadable surface. Inside a
        # fence (and in every non-markdown file) chmod keeps firing.
        chmod_hit = False
        for pat in () if ctx.prose else _CHMOD_EXEC_PATTERNS:
            is_os_chmod = "os\\.chmod" in pat.pattern
            # ``os.chmod`` is a PYTHON primitive — part of the overlap the AST
            # path already judges (and judges better: it reads the mode bits,
            # where this pattern flags any mode at all). The shell ``chmod +x``
            # form stays, because a shell command inside a string is invisible
            # to the AST walk.
            if not include_py_patterns and is_os_chmod:
                continue
            mo = pat.search(line)
            if mo is None:
                continue
            # The generate-then-chmod pairing is trusted only in a SHELL scope:
            # elsewhere (a Python docstring, a JS comment) "> on" and "chmod +x
            # on" are two sentences, not a program writing then marking `on`.
            target = _chmod_target(
                mo.group(1),
                is_os_chmod,
                lines,
                idx,
                rel_path,
                plugin_root,
                self_path,
                written if ctx.shell else set(),
            )
            if target is None:
                if is_os_chmod:
                    unresolved_chmod.append(line_no)  # unplaceable root → T3
                break
            target_judged = _rebase(target, _cwd_at(cwd_before, cwd_events, mo.start()), root_n)
            if target_judged is not None and _destination_in_tree(
                target_judged, plugin_root, self_path
            ):
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
            judged = _rebase(dst, _cwd_at(cwd_before, cwd_events, mo.start(1)), root_n)
            tier = None if judged is None else _regex_tier(judged, plugin_root, self_path)
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
            written.add(_bare(dst))
            judged = _rebase(dst, _cwd_at(cwd_before, cwd_events, mo.start(1)), root_n)
            tier = None if judged is None else _regex_tier(judged, plugin_root, self_path)
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

    if unresolved_chmod:
        findings.append(_t3_aggregate(unresolved_chmod, rel_path))
    return findings


def _t3_aggregate(sites: list[int], rel_path: str) -> WriteFinding:
    """ONE non-blocking T3 advisory for every unplaceable script write in a file."""
    ordered = sorted(set(sites))
    shown = ", ".join(str(n) for n in ordered[:3])
    more = "" if len(ordered) <= 3 else f", … (+{len(ordered) - 3} more)"
    return WriteFinding(
        ordered[0],
        f"{len(ordered)} script write(s) in this file are anchored to a root "
        f"the fold cannot place (a hoisted or parameter-anchored path), so "
        f"CPV cannot decide whether they land inside the plugin tree — "
        f"line(s) {shown}{more}. Advisory only: re-express the destination "
        f"through ${{CLAUDE_PLUGIN_DATA}} or a literal path to make it "
        f"decidable [{rel_path}]",
        "info",
    )


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
        findings.append(_t3_aggregate(unresolved_sites, rel_path))

    return findings
