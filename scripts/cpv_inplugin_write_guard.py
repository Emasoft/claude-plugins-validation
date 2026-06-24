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
from typing import Final, NamedTuple

# The destination-resolves-in-tree decision is shared with the #152 fold so the
# two modules cannot drift. ``_resolve_in_tree`` returns a ``Path`` under
# ``plugin_root`` for a PROVABLE in-plugin destination (incl. the
# ``~/.claude/plugins/data/<slug>/<rest>`` literal fold), or ``None`` for an
# unresolvable / out-of-tree path. ``_fold_to_plugin_root`` is the string-level
# fold used when we want the folded form without requiring the file to exist yet
# (a generated destination may not exist at scan time).
from cpv_persistence_target import _fold_to_plugin_root, _resolve_in_tree


class WriteFinding(NamedTuple):
    """One provable in-plugin script-write occurrence."""

    line_no: int  # 1-based
    message: str  # human-readable finding text


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
    re.compile(r"(?:^|[\s;&|])>>?\s*([^\s;&|<>]+)"),
    # `tee DST` / `tee -a DST`
    re.compile(r"(?:^|[\s;&|])tee\s+(?:-[A-Za-z]+\s+)*([^\s;&|<>]+)"),
    # `sed -i … DST` (edit a file in place). The last token is the file; we
    # capture a `.ext`-bearing token after the `sed -i` marker.
    re.compile(r"\bsed\s+(?:-[A-Za-z]*\s+)*-i[A-Za-z.]*\s+(?:-e\s+\S+\s+|'[^']*'\s+|\"[^\"]*\"\s+)*([^\s;&|<>]+)"),
)

# A heredoc opener whose redirect targets a file: ``cat > DST <<EOF`` /
# ``cat >> DST <<'EOF'`` / ``tee DST <<EOF``. Group 1 is the destination. Used
# to recover the heredoc body (to script-gate it by a written shebang) AND the
# destination path. re2-safe (the delimiter is irrelevant here; the body walk is
# done separately).
_HEREDOC_REDIRECT_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"(?:^|[\s;&|])(?:cat|printf|echo)\b[^\n<>]*>>?\s*([^\s;&|<>]+)\s*<<"),
    re.compile(r"(?:^|[\s;&|])tee\s+(?:-[A-Za-z]+\s+)*([^\s;&|<>]+)\s*<<"),
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


def _destination_in_tree(dst_expr: str, plugin_root: Path) -> bool:
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
    if _resolve_in_tree(raw, plugin_root) is not None:
        return True
    # Stage 2 — a generated destination that may not exist yet. Fold the env /
    # data-dir literal; require the folded path to live under the plugin root.
    folded = _fold_to_plugin_root(raw, plugin_root)
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


def inplugin_script_write_findings(
    content: str,
    rel_path: str,
    plugin_root: Path,
) -> list[WriteFinding]:
    """Scan ``content`` for writes that create / modify a SCRIPT file whose
    destination PROVABLY resolves inside the plugin tree (ROOT or DATA) and are
    NOT verbatim copies. Returns one ``WriteFinding`` per flagged line.

    Lenient fail-safe: a destination that does not provably resolve in-tree
    (``_resolve_in_tree`` / ``_fold_to_plugin_root`` ⇒ ``None``) yields NO
    finding. A verbatim-copy line yields NO finding. A non-script destination
    yields NO finding. Only a PROVABLE in-plugin script GENERATE / EDIT flags.

    ``rel_path`` is the file's plugin-relative path (used only for the finding
    message). ``plugin_root`` is the plugin tree root.
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
            if not _destination_in_tree(dst, plugin_root):
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
            mo = pat.search(line)
            if mo is None:
                continue
            target = mo.group(1)
            if _destination_in_tree(target, plugin_root):
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
        for pat in _PY_WRITE_PATTERNS:
            mo = pat.search(line)
            if mo is None:
                continue
            dst = mo.group(1)
            if not _is_script_destination(dst):
                continue  # non-script write into DATA → ALLOW
            if not _destination_in_tree(dst, plugin_root):
                continue  # lenient — unresolvable / out-of-tree destination
            findings.append(
                WriteFinding(
                    line_no,
                    f"in-plugin script written via Python primitive to '{dst.strip()}' "
                    f"(generate/edit of an unscanned in-plugin script is forbidden; "
                    f"only a verbatim copy is allowed) [{rel_path}]",
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
            dst = mo.group(1)
            if not _is_script_destination(dst):
                continue  # non-script write into DATA → ALLOW
            if not _destination_in_tree(dst, plugin_root):
                continue  # lenient — unresolvable / out-of-tree destination
            findings.append(
                WriteFinding(
                    line_no,
                    f"in-plugin script written via shell redirect to '{dst.strip()}' "
                    f"(generate/edit of an unscanned in-plugin script is forbidden; "
                    f"only a verbatim copy is allowed) [{rel_path}]",
                )
            )
            break

    return findings
