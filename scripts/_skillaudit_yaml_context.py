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

ContextVerdict = Literal["safe_doc", "safe_schema", "code_fence_neutral", "suspect", "unknown"]

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
                if m_run.group("inline").strip():
                    # Inline run: 'echo hi' — only the run: line itself
                    # belongs.
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

    for lineno, raw in enumerate(source.splitlines(), 1):
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
    lines = source.splitlines()
    if not (0 <= line_idx < len(lines)):
        return "unknown"

    line = lines[line_idx]

    # GitHub Actions workflow path? Known-safe CI install patterns are
    # demoted (not suppressed) because they ARE executed code, just in
    # an ephemeral CI runner. The user's agents triage these.
    if _is_inside_workflow_run(file_path):
        if _line_is_in_run_block(lines, line_idx):
            if _has_known_safe_ci_pattern(line):
                return "code_fence_neutral"
            # In a run: block but not a known-safe pattern — let the
            # heuristic chain decide.
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
