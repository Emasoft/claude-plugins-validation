#!/usr/bin/env python3
"""INTRINSIC daemon-source-scan persistence discriminator (issue #63).

CPV's two persistence detectors (the skillaudit ``PERSISTENCE`` rule and
``validate_security.py`` RC-39) emit CRITICAL on *any* boot-loaded-daemon
install line. They cannot tell a documented opt-in installer from malware
installing the same mechanism, because the detection is purely textual on
the install line — it never looks at *what the daemon runs*.

This module is the intrinsic test #63 lacked: **resolve the launched program
and scan its actual content**. The public predicate
``persistence_launches_clean_inert_target`` returns ``True`` (→ the caller
DOWNGRADES the finding to non-blocking) iff ALL FOUR ALLOW conditions hold,
else ``False`` (→ STAY CRITICAL):

* **C1 RESOLVABLE** — the launched program is identifiable AND is a regular
  file inside the plugin tree (``resolve_launched_target``).
* **C2 CLEAN** — re-scanning that file with CPV's own ``scan_content`` yields
  no non-suppressed CRITICAL/MAJOR execution/exfil finding.
* **C3 NON-EXPLOITABLE** — the launched file does not dynamically load/exec
  external/mutable code — ``eval``/``exec``/``compile``, computed ``import``,
  ``os.exec*``/``os.spawn*``/``posix_spawn``/``runpy``/file-based dynamic-import,
  pipe-to-interpreter (ratified MAXIMUM-STRICTNESS, issue #152: NO
  ``~/.claude/plugins/`` sandbox exemption — even a self-roll into the plugin's
  own mutable cache disqualifies) — nor accept inputs enabling RCE (listen
  socket, eval-of-stdin/env/argv, HTTP/RPC endpoint, watch-file-and-exec, unsafe
  deserialization of external data) — ``_non_exploitable``.
* **C4 INSTALL LINE CLEAN** — the install line itself carries no separate
  exec/exfil sink beyond the persistence verb — ``_install_line_clean``.

The clear is **INTRINSIC** — computed from the launched code's content/AST,
never from a plugin self-declaration (no ``cpv:allow``, no allowlist, no
manifest baseline; those were correctly refused in #63). It is **FN-safe** —
every persistence-malware shape still BLOCKS. **Default = STAY CRITICAL:**
every helper FAILS-SAFE — any parse error, unreadable target, ambiguous
path, unbounded launcher chain, or uncertainty makes the condition FAIL.

All regexes here are **re2-safe** (no lookbehind/lookahead) — CI runs
without google-re2.
"""

from __future__ import annotations

import configparser
import plistlib
import re
import shlex
from pathlib import Path
from typing import Final, NamedTuple

# ── Bound on the transitive launcher→real-script chain (§7). A launcher
# whose only action is to exec/source another in-tree fixed-path file is
# followed; the moment the chain leaves the tree, becomes unresolvable, or
# exceeds this depth, the predicate FAILS (STAY CRITICAL). An unusually deep
# launcher chain is itself suspicious.
_MAX_CHAIN: Final[int] = 4

# ── C2 boundary. A ``scan_content`` finding disqualifies the launched file
# when it is non-suppressed, non-demoted, CRITICAL/MAJOR (skillaudit
# ``{critical, high}``) AND its category is in this execution/exfil set. We
# intentionally EXCLUDE ``network`` per se (a benign daemon may fetch a
# constant host); a *malicious* network shape is already critical/high under
# ``data_exfiltration``. Category strings verified against the catalog.
_EXEC_EXFIL_CATEGORIES: Final[frozenset[str]] = frozenset(
    {
        "code_execution",
        "data_exfiltration",
        "supply_chain",
        "obfuscation",
        "persistence",
    }
)
_DISQUALIFYING_SEVERITIES: Final[frozenset[str]] = frozenset({"critical", "high"})


class ResolvedTarget(NamedTuple):
    """The concrete in-tree program a persistence mechanism launches (§3)."""

    program_path: Path  # the in-tree file the daemon launches
    argv: list[str]  # full argv if known (an inline-code flag → C1 fail)
    extra_sources: list[Path]  # additional in-tree files referenced (plist working-dir / env scripts)
    mechanism: str  # launchd | systemd | cron | rc | pm2 | windows_run | loginitems


# ────────────────────────────────────────────────────────────────────────
# Path-folding helpers (C1)
# ────────────────────────────────────────────────────────────────────────

# Closed whitelist of env vars we constant-fold to the plugin root ``R``.
# ``$HOME`` is deliberately NOT folded in general — a ``$HOME``-anchored target is
# OUTSIDE the tree → C1 fails. Anything not on this list stays variable → C1
# fails. ``${VAR}`` and ``$VAR`` forms both covered. The ONE ``$HOME`` exception
# is the plugin-data sandbox literal handled by ``_PLUGIN_DATA_LITERAL_RE`` below.
_PLUGIN_ROOT_ENV_NAMES: Final[tuple[str, ...]] = ("CLAUDE_PLUGIN_ROOT", "CLAUDE_PLUGIN_DATA")

# Issue #152 — a hard-coded plugin-data sandbox literal:
# ``<HOME>/.claude/plugins/data/<slug>/<rest>`` (``~`` / ``$HOME`` / ``${HOME}``
# HOME forms). A cross-plugin / detached daemon installer CANNOT use the
# ``${CLAUDE_PLUGIN_DATA}`` env var (it resolves to whichever plugin owns the
# current turn, wrong in a launchd/systemd-spawned process), so it hard-codes its
# own data dir. That dir is the SAME Claude-managed sandbox ``${CLAUDE_PLUGIN_DATA}``
# resolves to, so we fold its ``<rest>`` to the plugin root ``R`` exactly like the
# env var. The ``<slug>`` segment (group-less ``[^/]+``) is consumed UNGATED: CPV
# must validate UNINSTALLED, marketplace-less plugins (which have NO data-dir slug
# to match), and an attacker cannot know a victim's slug before install. FN-safety
# is preserved downstream — the folded ``R/<rest>`` must still be an EXISTING
# in-tree regular file that C2 (clean) + C3 (non-exploitable, incl. the strict
# dynamic-exec block) scan; a malicious or dynamic target still STAYS CRITICAL.
# re2-safe (no lookbehind/lookahead).
_PLUGIN_DATA_LITERAL_RE: Final[re.Pattern[str]] = re.compile(
    r"^(?:~|\$HOME|\$\{HOME\})/\.claude/plugins/data/[^/]+/(.+)$"
)

# ``$PWD`` / ``$(pwd)`` → R. (A daemon's WorkingDirectory or a cron line run
# from the plugin checkout resolves $PWD to the plugin root in practice; the
# fail-safe direction is that a non-folded $VAR escapes the tree anyway.)
_PWD_TOKEN_RE: Final[re.Pattern[str]] = re.compile(r"\$\{?PWD\}?|\$\(pwd\)|`pwd`")

# Any residual ``$VAR`` / ``${VAR}`` after folding → unresolvable → C1 fails.
_RESIDUAL_VAR_RE: Final[re.Pattern[str]] = re.compile(r"\$\{?[A-Za-z_]\w*\}?|\$\(")

# Inline-code flags: a launcher invoking an interpreter with one of these has
# NO scannable file → C1 fails (it is an inline dynamic-exec at the launch
# site). Keyed per interpreter family.
_SHELL_INLINE_FLAGS: Final[frozenset[str]] = frozenset({"-c", "-e", "-E", "-r"})
_PWSH_INLINE_FLAGS: Final[frozenset[str]] = frozenset(
    {"-command", "-c", "-encodedcommand", "-e", "-ec"}
)
_CMD_INLINE_FLAGS: Final[frozenset[str]] = frozenset({"/c", "/k"})
_INTERPRETER_NAMES: Final[frozenset[str]] = frozenset(
    {"sh", "bash", "zsh", "dash", "ksh", "python", "python3", "perl", "ruby", "node", "nodejs", "php"}
)


def _strip_program_name(token: str) -> str:
    """Basename without directory, lowercased, ``.exe`` stripped."""
    base = token.replace("\\", "/").rsplit("/", 1)[-1].lower()
    if base.endswith(".exe"):
        base = base[:-4]
    return base


def _argv_has_inline_code_flag(argv: list[str]) -> bool:
    """True iff ``argv`` invokes an interpreter with an inline-code flag
    (``python -c``, ``bash -c``, ``powershell -Command``, ``cmd /c`` …) —
    there is no file to scan, so C1 must FAIL. FAILS-SAFE: an unrecognised
    interpreter with a ``-c``-like flag is treated as inline too."""
    if not argv:
        return False
    prog = _strip_program_name(argv[0])
    flags = {a.lower() for a in argv[1:] if a.startswith("-") or a.startswith("/")}
    if prog in {"powershell", "pwsh"}:
        return bool(flags & _PWSH_INLINE_FLAGS)
    if prog in {"cmd"}:
        return bool(flags & _CMD_INLINE_FLAGS)
    if prog in _INTERPRETER_NAMES:
        return bool(flags & _SHELL_INLINE_FLAGS)
    # Defence-in-depth: any program given a bare ``-c``/``-e`` AND a following
    # non-flag string is suspicious of inline code → fail safe.
    return False


def _interp_script_target(argv: list[str]) -> str | None:
    """The SCANNABLE program in ``argv``. When ``argv[0]`` is a known interpreter
    (``python3``/``bash``/``node``/…) the launched program is the SCRIPT — the
    first non-flag token after it — NOT the interpreter binary (which is never
    in-tree). When the program is launched directly (no interpreter wrapper)
    ``argv[0]`` itself is the target. Returns ``None`` when an interpreter is
    given ONLY flags (an inline ``-c`` code launch — there is no file to scan;
    ``resolve_launched_target`` independently rejects that via
    ``_argv_has_inline_code_flag``). re2-safe (pure-Python token walk)."""
    if not argv:
        return None
    prog0 = _strip_program_name(argv[0])
    if prog0 in _INTERPRETER_NAMES or prog0 in {"powershell", "pwsh"}:
        for a in argv[1:]:
            if not a.startswith("-"):
                return a
        return None  # interpreter + only flags → inline code → no scannable file
    return argv[0]


def _fold_to_plugin_root(raw: str, plugin_root: Path) -> str | None:
    """Constant-fold the closed env whitelist in ``raw`` to ``plugin_root``.

    Returns a concrete path string, or ``None`` when a residual ``$VAR`` (or a
    NON-sandbox ``$HOME``) remains — i.e. the path is unresolvable and C1 must
    FAIL. The ONE folded ``$HOME``/``~`` form is the plugin-data sandbox literal
    ``<HOME>/.claude/plugins/data/<slug>/<rest>`` → ``R/<rest>`` (issue #152). A
    bare relative path (``scripts/d.py``) is resolved relative to ``plugin_root``.
    """
    s = raw.strip().strip("'\"")
    if not s:
        return None
    root = str(plugin_root)
    # Fold the plugin-root env vars (``${VAR}`` and ``$VAR``) → R.
    for name in _PLUGIN_ROOT_ENV_NAMES:
        s = s.replace("${" + name + "}", root).replace("$" + name, root)
    # Fold $PWD / $(pwd) / `pwd` → R.
    s = _PWD_TOKEN_RE.sub(root, s)
    # Fold the plugin-data sandbox literal ``<HOME>/.claude/plugins/data/<slug>/<rest>``
    # → ``R/<rest>`` (issue #152) — the ONE $HOME/~ form that IS in-tree, because
    # that sandbox dir holds the plugin's OWN staged files (the same dir
    # ``${CLAUDE_PLUGIN_DATA}`` resolves to). Evaluate the FULL path: the <slug>
    # wildcard segment is consumed, the <rest> is resolved under R and still
    # C2/C3-scanned. Fall through to the residual-var / tilde guards so a <rest>
    # that itself carries a computed $VAR is still rejected.
    mo = _PLUGIN_DATA_LITERAL_RE.match(s)
    if mo is not None:
        s = str(plugin_root / mo.group(1))
    # Any residual variable (a NON-sandbox $HOME, or a $VAR in the folded <rest>)
    # → unresolvable.
    if _RESIDUAL_VAR_RE.search(s):
        return None
    # Tilde is a $HOME alias → out of tree → fail (the sandbox tilde was already
    # folded above; any OTHER ``~`` path is genuinely out of tree).
    if s.startswith("~"):
        return None
    return s


def _resolve_in_tree(raw: str, plugin_root: Path) -> Path | None:
    """Fold ``raw`` then require it to be an EXISTING REGULAR FILE whose
    realpath is UNDER the plugin root's realpath. Symlink resolution that
    leaves the tree → ``None``. Any failure → ``None`` (C1 fails)."""
    folded = _fold_to_plugin_root(raw, plugin_root)
    if folded is None:
        return None
    try:
        p = Path(folded)
        if not p.is_absolute():
            p = plugin_root / p
        real = p.resolve()
        root_real = plugin_root.resolve()
    except (OSError, ValueError, RuntimeError):
        return None
    # Must exist, be a regular file, and live under the tree (after symlink
    # resolution — a symlink pointing out of the tree fails here).
    try:
        if not real.is_file():
            return None
        real.relative_to(root_real)
    except (OSError, ValueError):
        return None
    return real


# ────────────────────────────────────────────────────────────────────────
# Per-mechanism resolution (C1, §3.1)
# ────────────────────────────────────────────────────────────────────────

# Env-var injection keys inside a plist/unit that pre-load attacker code into
# the launched (otherwise-clean) program. Their presence is a C3 fail.
_CODE_INJECT_ENV_KEYS: Final[frozenset[str]] = frozenset(
    {
        "BASH_ENV",
        "ENV",
        "DYLD_INSERT_LIBRARIES",
        "DYLD_LIBRARY_PATH",
        "LD_PRELOAD",
        "LD_LIBRARY_PATH",
        "NODE_OPTIONS",
        "PYTHONSTARTUP",
        "PERL5LIB",
        "GIT_SSH_COMMAND",
    }
)

# A heredoc body in the install file: ``cat > DST <<EOF ... EOF`` (or ``<<-``,
# quoted delimiter). Used to recover an inline plist/unit/cron body. re2-safe:
# the delimiter may be unquoted, single-, or double-quoted — enumerated as an
# alternation (NO backreference; a backref is not re2-compatible and the quote
# style is irrelevant to body extraction). A simple opener match then a
# line-walk to the closing delimiter (no DOTALL).
_HEREDOC_OPEN_RE: Final[re.Pattern[str]] = re.compile(
    r"<<-?\s*(?:'([A-Za-z_]\w*)'|\"([A-Za-z_]\w*)\"|([A-Za-z_]\w*))"
)


def _extract_heredoc_body(full_content: str, after_marker: str) -> str | None:
    """Return the body of the FIRST heredoc whose opener line also mentions
    ``after_marker`` (e.g. ``.plist`` / ``.service``). ``None`` if none."""
    if not full_content:
        return None
    lines = full_content.split("\n")
    for idx, line in enumerate(lines):
        if after_marker not in line:
            continue
        mo = _HEREDOC_OPEN_RE.search(line)
        if not mo:
            continue
        # The delimiter is whichever of the three alternatives matched.
        delim = mo.group(1) or mo.group(2) or mo.group(3)
        body: list[str] = []
        for nxt in lines[idx + 1 :]:
            if nxt.strip() == delim:
                return "\n".join(body)
            body.append(nxt)
        # Unterminated heredoc → fail-safe (treat as no body).
        return None
    return None


def _split_dst_src_paths(install_line: str) -> tuple[str | None, str | None]:
    """For ``cp SRC DST`` / ``install -m … SRC DST`` / ``ln -s SRC DST``,
    return ``(src, dst)`` of the LAST two non-flag tokens. ``(None, None)``
    on any tokenisation failure (fail-safe)."""
    try:
        toks = shlex.split(install_line, comments=False, posix=True)
    except ValueError:
        return (None, None)
    positional = [t for t in toks if not t.startswith("-")]
    # Drop the leading command token(s) — keep the trailing path pair.
    if len(positional) < 3:  # cmd + src + dst minimum
        return (None, None)
    return (positional[-2], positional[-1])


def _plist_program(data: object) -> tuple[str | None, list[str]]:
    """From a parsed plist dict, return ``(program, argv)``: ``Program`` (a
    string) or ``ProgramArguments`` (a list — ``argv[0]`` is the program)."""
    if not isinstance(data, dict):
        return (None, [])
    pa = data.get("ProgramArguments")
    if isinstance(pa, list) and pa and all(isinstance(x, str) for x in pa):
        return (pa[0], list(pa))
    prog = data.get("Program")
    if isinstance(prog, str) and prog:
        return (prog, [prog])
    return (None, [])


def _plist_extra_sources(data: object, plugin_root: Path) -> tuple[list[Path], bool]:
    """Return ``(extra_in_tree_source_paths, has_code_inject_env)``. A
    code-injecting ``EnvironmentVariables`` key (LD_PRELOAD/BASH_ENV/…) →
    the bool is True → the caller treats it as a C3 fail."""
    extras: list[Path] = []
    inject = False
    if not isinstance(data, dict):
        return (extras, inject)
    env = data.get("EnvironmentVariables")
    if isinstance(env, dict):
        for k in env:
            if isinstance(k, str) and k in _CODE_INJECT_ENV_KEYS:
                inject = True
    wd = data.get("WorkingDirectory")
    if isinstance(wd, str):
        # A WorkingDirectory is a dir, not a file; we don't scan it, but a
        # BASH_ENV-style script reference inside the env would already flip
        # ``inject``. Nothing extra to add here.
        pass
    return (extras, inject)


def _resolve_launchd(
    install_line: str, plugin_root: Path, full_content: str | None
) -> ResolvedTarget | None:
    src: str | None = None
    # Sub-case A: cp/install/ln SRC DST.plist — SRC is the in-tree plist.
    if ".plist" in install_line:
        s, _dst = _split_dst_src_paths(install_line)
        src = s
    data: object = None
    if src is not None:
        src_path = _resolve_in_tree(src, plugin_root)
        if src_path is not None:
            try:
                data = plistlib.loads(src_path.read_bytes())
            except (OSError, plistlib.InvalidFileException, ValueError):
                return None
    # Sub-case B: heredoc body (``cat > DST.plist <<EOF ... EOF``).
    if data is None and full_content:
        body = _extract_heredoc_body(full_content, ".plist")
        if body is not None:
            try:
                data = plistlib.loads(body.encode("utf-8"))
            except (plistlib.InvalidFileException, ValueError):
                return None
    if data is None:
        return None
    program, argv = _plist_program(data)
    if program is None:
        return None
    # The scannable program is the SCRIPT, not the interpreter — a plist
    # ``ProgramArguments: [python3, "<…>/daemon.py"]`` launches daemon.py.
    target = _interp_script_target(argv)
    if target is None:
        return None
    prog_path = _resolve_in_tree(target, plugin_root)
    if prog_path is None:
        return None
    extras, inject = _plist_extra_sources(data, plugin_root)
    if inject:
        # An env-inject plist launches a clean program but pre-loads code →
        # mark via a synthetic out-of-tree extra so the caller's C3 fails.
        # Simpler: refuse outright (fail-safe).
        return None
    return ResolvedTarget(program_path=prog_path, argv=argv, extra_sources=extras, mechanism="launchd")


def _systemd_exec_program(unit_text: str) -> tuple[str | None, list[str], bool]:
    """Parse a ``.service`` unit; return ``(program, argv, has_inject_env)``
    from ``ExecStart`` (after stripping the leading ``-``/``@``/``+``/``!``
    exec-prefix chars). ``has_inject_env`` is True for a code-injecting
    ``Environment=``/``EnvironmentFile=`` key."""
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    # systemd allows duplicate keys (ExecStartPre etc.); ConfigParser with
    # strict=False tolerates them. A parse error → fail-safe.
    try:
        parser.read_string(unit_text)
    except configparser.Error:
        return (None, [], False)
    if not parser.has_section("Service"):
        return (None, [], False)
    svc = parser["Service"]
    inject = False
    env_val = svc.get("Environment", "") or ""
    for key in _CODE_INJECT_ENV_KEYS:
        if key in env_val:
            inject = True
    if svc.get("EnvironmentFile"):
        # An external EnvironmentFile can inject arbitrary env → fail-safe.
        inject = True
    exec_start = svc.get("ExecStart")
    if not exec_start:
        return (None, [], inject)
    exec_start = exec_start.strip()
    # Strip systemd exec-prefix chars.
    exec_start = exec_start.lstrip("-@+!:")
    try:
        toks = shlex.split(exec_start, posix=True)
    except ValueError:
        return (None, [], inject)
    if not toks:
        return (None, [], inject)
    return (toks[0], toks, inject)


def _resolve_systemd(
    install_line: str, plugin_root: Path, full_content: str | None
) -> ResolvedTarget | None:
    unit_text: str | None = None
    if ".service" in install_line:
        s, _dst = _split_dst_src_paths(install_line)
        if s is not None:
            src_path = _resolve_in_tree(s, plugin_root)
            if src_path is not None:
                try:
                    unit_text = src_path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    return None
    if unit_text is None and full_content:
        unit_text = _extract_heredoc_body(full_content, ".service")
    if unit_text is None:
        return None
    program, argv, inject = _systemd_exec_program(unit_text)
    if program is None or inject:
        return None
    # ``ExecStart={python} {launcher}`` → the scannable program is the script.
    target = _interp_script_target(argv)
    if target is None:
        return None
    prog_path = _resolve_in_tree(target, plugin_root)
    if prog_path is None:
        return None
    return ResolvedTarget(program_path=prog_path, argv=argv, extra_sources=[], mechanism="systemd")


# A cron command after a ``@reboot`` (optional user field for /etc/cron.d).
# Capture stops at a closing quote / paren / pipe so the common
# ``(echo "@reboot CMD") | crontab -`` idiom yields CMD alone, not the
# ``") | crontab -`` suffix. ``[^"')|\n]+`` is re2-safe (a negated class).
_CRON_REBOOT_RE: Final[re.Pattern[str]] = re.compile(r"@reboot\s+([^\"')|\n]+)")


def _resolve_cron(
    install_line: str, plugin_root: Path, full_content: str | None
) -> ResolvedTarget | None:
    cmd: str | None = None
    mo = _CRON_REBOOT_RE.search(install_line)
    if mo:
        cmd = mo.group(1)
    elif full_content:
        # ``>> /etc/cron.d/foo`` with a body line ``@reboot user CMD``.
        for ln in full_content.split("\n"):
            mb = _CRON_REBOOT_RE.search(ln)
            if mb:
                cmd = mb.group(1)
                break
    if not cmd:
        return None
    try:
        toks = shlex.split(cmd, posix=True)
    except ValueError:
        return None
    if not toks:
        return None
    # An /etc/cron.d body carries a leading user field before the command;
    # if the first token is not a path-ish program AND a later token is, the
    # first is the user — but to stay fail-safe we resolve toks[0] and, if it
    # is not in-tree, retry from toks[1] ONCE (the user-field case).
    prog_path = _resolve_in_tree(toks[0], plugin_root)
    argv = toks
    if prog_path is None and len(toks) >= 2:
        prog_path = _resolve_in_tree(toks[1], plugin_root)
        argv = toks[1:]
    if prog_path is None:
        return None
    return ResolvedTarget(program_path=prog_path, argv=argv, extra_sources=[], mechanism="cron")


_PM2_START_RE: Final[re.Pattern[str]] = re.compile(r"pm2\s+start\s+(\S+)")


def _resolve_pm2(install_line: str, plugin_root: Path) -> ResolvedTarget | None:
    mo = _PM2_START_RE.search(install_line)
    if not mo:
        return None
    script = mo.group(1).strip("'\"")
    prog_path = _resolve_in_tree(script, plugin_root)
    if prog_path is None:
        return None
    return ResolvedTarget(program_path=prog_path, argv=[script], extra_sources=[], mechanism="pm2")


# rc.local / init.d: an appended command (``echo "CMD" >> /etc/rc.local`` or a
# heredoc to /etc/init.d/foo).
def _resolve_rc(
    install_line: str, plugin_root: Path, full_content: str | None
) -> ResolvedTarget | None:
    cmd: str | None = None
    # ``echo "..." >> /etc/rc.local`` — recover the quoted command.
    if "rc.local" in install_line or "init.d" in install_line:
        # Prefer a heredoc body if present.
        if full_content:
            body = _extract_heredoc_body(full_content, "init.d") or _extract_heredoc_body(
                full_content, "rc.local"
            )
            if body:
                # First non-shebang, non-comment, non-blank line is the cmd.
                for ln in body.split("\n"):
                    t = ln.strip()
                    if t and not t.startswith("#"):
                        cmd = t
                        break
        if cmd is None:
            # ``echo "CMD" >> /etc/rc.local`` — pull the quoted segment. Quote
            # style enumerated as an alternation (NO backreference — re2-safe).
            mq = re.search(r"""echo\s+(?:'([^']+)'|"([^"]+)")""", install_line)
            if mq:
                cmd = mq.group(1) or mq.group(2)
    if not cmd:
        return None
    try:
        toks = shlex.split(cmd, posix=True)
    except ValueError:
        return None
    if not toks:
        return None
    prog_path = _resolve_in_tree(toks[0], plugin_root)
    if prog_path is None:
        return None
    return ResolvedTarget(program_path=prog_path, argv=toks, extra_sources=[], mechanism="rc")


# Windows Run key / schtasks: the ``/d`` (reg add) or ``/tr`` (schtasks) value.
# Quote style enumerated as an alternation (NO backreference — re2-safe).
_WIN_RUN_VALUE_RE: Final[re.Pattern[str]] = re.compile(
    r"""/(?:d|tr)\s+(?:'([^']+)'|"([^"]+)")""", re.IGNORECASE
)


def _resolve_windows_run(install_line: str, plugin_root: Path) -> ResolvedTarget | None:
    mo = _WIN_RUN_VALUE_RE.search(install_line)
    if not mo:
        return None
    cmd = mo.group(1) or mo.group(2)
    try:
        toks = shlex.split(cmd, posix=False)
    except ValueError:
        return None
    if not toks:
        return None
    prog_path = _resolve_in_tree(toks[0], plugin_root)
    if prog_path is None:
        return None
    return ResolvedTarget(
        program_path=prog_path, argv=toks, extra_sources=[], mechanism="windows_run"
    )


# login items: ``defaults write … loginitems`` — the path argument.
_LOGINITEMS_PATH_RE: Final[re.Pattern[str]] = re.compile(
    r"""loginitems.*?(?:path|name)?\s*['"]([^'"]+)['"]""", re.IGNORECASE
)


def _resolve_loginitems(install_line: str, plugin_root: Path) -> ResolvedTarget | None:
    mo = _LOGINITEMS_PATH_RE.search(install_line)
    if not mo:
        return None
    prog_path = _resolve_in_tree(mo.group(1), plugin_root)
    if prog_path is None:
        return None
    return ResolvedTarget(
        program_path=prog_path, argv=[mo.group(1)], extra_sources=[], mechanism="loginitems"
    )


# Mechanism-detection needles: a persistence INSTALL line is dispatched to a
# resolver when its lowercased form contains one of these tokens. These are
# DETECTION DATA — raw-string signatures this analyzer matches against the
# install line under scrutiny — NOT a persistence mechanism this file performs.
# They live in a multi-line ``*_TOKENS`` collection (the pattern-source shape)
# so CPV's own self-scan recognizes the table as rule data and does not
# self-flag it. FN-safe: the self-scan skip is gated to CPV's OWN hash-pinned
# source (cpv_self_scan_skip_line / _CPV_IS_RUNNING_CPV), so a real persistence
# install in a THIRD-PARTY plugin still BLOCKS — it is never in this table.
_MECHANISM_TOKENS: Final[dict[str, tuple[str, ...]]] = {
    "launchd": ("launchagents", "launchdaemons", "launchctl", ".plist"),
    "systemd": (".service", "systemctl"),
    "cron": ("@reboot", "crontab", "cron.d"),
    "pm2": ("pm2",),
    "rclocal": ("rc.local", "init.d"),
    "windows": ("schtasks", "\\run", "/run", "reg add"),
    "loginitems": ("loginitems",),
}


def resolve_launched_target(
    install_line: str,
    install_file: str,
    plugin_root: Path,
    *,
    full_content: str | None = None,
) -> ResolvedTarget | None:
    """Map a persistence install line to the concrete in-tree file the daemon
    launches (C1, §3). Returns ``None`` (→ C1 fails → STAY CRITICAL) when the
    target is unresolvable, external, ``$VAR``-only, an inline-code launcher,
    or any parse fails. FAILS-SAFE throughout."""
    low = install_line.lower()
    resolver_order: list[ResolvedTarget | None] = []
    # Dispatch by the mechanism token(s) present on the line. The detection
    # needles live in the ``_MECHANISM_TOKENS`` table above (a pattern-source
    # collection — so the literals read as DATA, not as a persistence mechanism
    # this analyzer performs). Try each plausible resolver; first that resolves
    # wins.
    if any(t in low for t in _MECHANISM_TOKENS["launchd"]):
        resolver_order.append(_resolve_launchd(install_line, plugin_root, full_content))
    if any(t in low for t in _MECHANISM_TOKENS["systemd"]):
        resolver_order.append(_resolve_systemd(install_line, plugin_root, full_content))
    if any(t in low for t in _MECHANISM_TOKENS["cron"]):
        resolver_order.append(_resolve_cron(install_line, plugin_root, full_content))
    if any(t in low for t in _MECHANISM_TOKENS["pm2"]):
        resolver_order.append(_resolve_pm2(install_line, plugin_root))
    if any(t in low for t in _MECHANISM_TOKENS["rclocal"]):
        resolver_order.append(_resolve_rc(install_line, plugin_root, full_content))
    if any(t in low for t in _MECHANISM_TOKENS["windows"]):
        resolver_order.append(_resolve_windows_run(install_line, plugin_root))
    if any(t in low for t in _MECHANISM_TOKENS["loginitems"]):
        resolver_order.append(_resolve_loginitems(install_line, plugin_root))

    for rt in resolver_order:
        if rt is None:
            continue
        # C1 hard rule: an inline-code-flag argv has no scannable file.
        if _argv_has_inline_code_flag(rt.argv):
            return None
        return rt
    return None


# ────────────────────────────────────────────────────────────────────────
# Non-exploitable predicate (C3, §5) — incl. the NEW 3b input-listen detector
# ────────────────────────────────────────────────────────────────────────

# (3a) Dynamic external/mutable code load/exec. C2 catches most of these via
# SUPPLY_CHAIN/CMD_INJECTION, but a bare ``eval(x)`` is only ``SHELL_EXEC``
# (medium → below C2's gate), and 3a says ANY dynamic exec disqualifies.
_3A_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    # curl|wget … | interpreter
    re.compile(r"(?:curl|wget|fetch)\b[^\n|]*\|\s*(?:bash|sh|zsh|python\d?|node|perl|ruby|php)\b", re.IGNORECASE),
    # eval(fetch/axios/require/$(curl ...))
    re.compile(r"eval\s*\(\s*(?:await\s+)?(?:fetch|axios|require|\$\(\s*curl)", re.IGNORECASE),
    # any eval( — re2-safe boundary: start-of-line OR a non-identifier char
    re.compile(r"(?:^|[^.\w])eval\s*\("),
    # new Function( ... )
    re.compile(r"new\s+Function\s*\("),
    # Function('...') string-form
    re.compile(r"\bFunction\s*\(\s*['\"`]"),
    # setTimeout/setInterval with a string body (eval-equivalent)
    re.compile(r"set(?:Timeout|Interval)\s*\(\s*['\"]"),
    # Python exec( / compile(
    re.compile(r"(?:^|[^.\w])exec\s*\("),
    re.compile(r"(?:^|[^.\w])compile\s*\("),
    # os.system / subprocess shell=True / os.popen
    re.compile(r"os\.system\s*\("),
    re.compile(r"subprocess\.(?:call|run|Popen|check_output|check_call)\s*\([^)]*shell\s*=\s*True"),
    re.compile(r"os\.popen\s*\("),
    # node child_process / execSync
    re.compile(r"\bchild_process\b"),
    re.compile(r"\bexecSync\b"),
    # computed import (non-literal arg): __import__(VAR) / importlib.import_module(VAR)
    re.compile(r"__import__\s*\(\s*[A-Za-z_]\w*\s*[,)]"),
    re.compile(r"importlib\.import_module\s*\(\s*[A-Za-z_]\w*\s*[,)]"),
    # reflective builtins access
    re.compile(r"getattr\s*\(\s*__builtins__"),
    re.compile(r"globals\s*\(\s*\)\s*\["),
    re.compile(r"__builtins__\s*\["),
    # Dynamic process-image replacement / script-run / file-based dynamic-import
    # primitives (issue #152, ratified MAXIMUM-STRICTNESS). A boot daemon that
    # ``os.exec*`` / ``os.spawn*`` / ``posix_spawn`` / ``runpy.run_(path|module)``
    # / ``imp.load_*`` / ``spec_from_file_location``'s ANOTHER program is the
    # "loads another script dynamically" clean-but-exploitable shape. There is NO
    # ``~/.claude/plugins/`` sandbox exemption: even an exec of the plugin's OWN
    # cache/data path disqualifies — that target is mutable / version-stamped, so
    # what RUNS is not what was SCANNED. A FIXED-argv ``subprocess.run(["launchctl",
    # …])`` is intentionally NOT matched here — that is the persistence INSTALL
    # action (judged by C4), not a dynamic code-load. ``ctypes.CDLL`` is
    # deliberately excluded (dual-use: a benign daemon loads fixed system libs).
    # All re2-safe (no lookbehind/lookahead).
    re.compile(r"\bos\.exec[a-z]*\s*\("),  # os.execv / execve / execvp / execl …
    re.compile(r"\bos\.spawn[a-z]*\s*\("),  # os.spawnv / spawnl / spawnvp …
    re.compile(r"\bos\.posix_spawnp?\s*\("),  # os.posix_spawn / posix_spawnp
    re.compile(r"\brunpy\.run_(?:path|module)\s*\("),  # runpy.run_path / run_module
    re.compile(r"\bimp\.load_(?:source|module|compiled)\s*\("),  # legacy dynamic import
    re.compile(r"\bspec_from_file_location\s*\("),  # importlib file-based dynamic import
)

# Out-of-tree / mutable ``source`` / ``.`` of a path (a $VAR, /tmp, $HOME, or
# an absolute system path). An IN-TREE fixed ``source ./lib.sh`` is followed
# transitively (§7), NOT flagged — so this matches only the dangerous forms.
_OUT_OF_TREE_SOURCE_RE: Final[re.Pattern[str]] = re.compile(
    r"(?:^|[\s;&|])(?:source|\.)\s+(?:\$|~|/tmp/|/var/|/dev/|https?://)"
)

# computed require(VAR) / import(VAR) of a non-literal module
_COMPUTED_REQUIRE_RE: Final[re.Pattern[str]] = re.compile(
    r"(?:require|import)\s*\(\s*[A-Za-z_]\w*\s*\)"
)
# remote require/import
_REMOTE_REQUIRE_RE: Final[re.Pattern[str]] = re.compile(
    r"(?:require|import)\s*\(\s*['\"]https?://", re.IGNORECASE
)


def _matches_3a(content: str) -> bool:
    """(3a) dynamic external/mutable code load/exec present → disqualify."""
    for pat in _3A_PATTERNS:
        if pat.search(content):
            return True
    if _OUT_OF_TREE_SOURCE_RE.search(content):
        return True
    if _COMPUTED_REQUIRE_RE.search(content) or _REMOTE_REQUIRE_RE.search(content):
        return True
    return False


# (3b) Input-listen RCE surface — the NEW detection (no existing catalog rule
# but for outbound REVERSE_SHELL). Two halves: a listen/bind socket (ALONE
# disqualifies — a boot-daemon opening a port is an attack surface), and an
# eval/exec of an input channel.

_LISTEN_SOCKET_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    # Python servers / bind+listen
    re.compile(r"socketserver\.", re.IGNORECASE),
    re.compile(r"\bhttp\.server\b", re.IGNORECASE),
    re.compile(r"\bHTTPServer\s*\("),
    re.compile(r"\bBaseHTTPRequestHandler\b"),
    re.compile(r"asyncio\.start_server"),
    re.compile(r"\.bind\s*\(\s*\("),  # sock.bind((host, port))
    re.compile(r"\.listen\s*\("),
    re.compile(r"\.recvfrom\s*\("),
    # Node servers
    re.compile(r"\bnet\.createServer\b"),
    re.compile(r"\bhttps?\.createServer\b"),
    re.compile(r"\bdgram\.createSocket\b"),
    re.compile(r"new\s+WebSocket\.Server"),
    re.compile(r"\bws\.Server\b"),
    re.compile(r"\.listen\s*\(\s*\d"),  # express()/fastify().listen(PORT)
    # Go / others
    re.compile(r"\bnet\.Listen\b"),
    re.compile(r"\bListenAndServe\b"),
    # shell listeners
    re.compile(r"\bnc\s+-l\b"),
    re.compile(r"\bncat\s+-l\b"),
    re.compile(r"\bsocat\b[^\n]*LISTEN", re.IGNORECASE),
)

# A web framework route/endpoint construct (its presence + an exec sink → 3b).
_ENDPOINT_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"@app\.route\b"),
    re.compile(r"\bapp\.(?:get|post|put|delete|patch)\s*\("),
    re.compile(r"\brouter\.(?:get|post|put|delete|patch|use)\s*\("),
    re.compile(r"@(?:app|router)\.(?:get|post|put|delete|patch)\b"),  # FastAPI
)

# Watch-file-and-exec constructs (their presence + an exec sink → 3b).
_WATCH_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"\bwatchdog\."),
    re.compile(r"\binotify\b", re.IGNORECASE),
    re.compile(r"\bfs\.watch\s*\("),
    re.compile(r"\bchokidar\b"),
)

# Input-channel tokens — an eval/exec/system whose arg derives from these is
# RCE.
_INPUT_CHANNEL_RE: Final[re.Pattern[str]] = re.compile(
    r"sys\.stdin|(?:^|[^.\w])input\s*\(|sys\.argv|os\.environ|process\.env|process\.argv|"
    r"req\.body|req\.query|req\.params|request\.|readline|os\.mkfifo|fifo",
    re.IGNORECASE,
)

# Any exec sink (used for the endpoint/watch + sink combination).
_EXEC_SINK_RE: Final[re.Pattern[str]] = re.compile(
    r"(?:^|[^.\w])(?:eval|exec)\s*\(|os\.system\s*\(|subprocess\.|child_process|\bexecSync\b|"
    r"new\s+Function\s*\(",
    re.IGNORECASE,
)

# Deserialization of an external stream (defence-in-depth beyond C2's
# DESERIALIZATION catalog rule).
_DESER_STREAM_RE: Final[re.Pattern[str]] = re.compile(
    r"(?:pickle\.load|yaml\.load|torch\.load|joblib\.load|marshal\.load)\s*\(", re.IGNORECASE
)


def _eval_of_input_present(content: str) -> bool:
    """True iff an eval/exec/system sink co-occurs with an input channel
    token within a ≤3-line window (conservative file-level taint). Also fires
    when shell=True/os.system concatenates an input token."""
    lines = content.split("\n")
    n = len(lines)
    for i, line in enumerate(lines):
        if not _EXEC_SINK_RE.search(line):
            continue
        lo = max(0, i - 3)
        hi = min(n, i + 4)
        window = "\n".join(lines[lo:hi])
        if _INPUT_CHANNEL_RE.search(window):
            return True
    return False


def _matches_3b(content: str) -> bool:
    """(3b) input-listen RCE surface present → disqualify."""
    # A bind/listen socket ALONE disqualifies.
    for pat in _LISTEN_SOCKET_PATTERNS:
        if pat.search(content):
            return True
    # eval/exec of an input channel.
    if _eval_of_input_present(content):
        return True
    # Endpoint/route construct + ANY exec sink anywhere in the file.
    has_sink = bool(_EXEC_SINK_RE.search(content))
    if has_sink:
        for pat in _ENDPOINT_PATTERNS:
            if pat.search(content):
                return True
        for pat in _WATCH_PATTERNS:
            if pat.search(content):
                return True
    # Deserialization of a stream whose source looks external.
    if _DESER_STREAM_RE.search(content) and _INPUT_CHANNEL_RE.search(content):
        return True
    return False


def _non_exploitable(content: str, ext: str) -> bool:
    """Return ``True`` (DISQUALIFY → C3 FAILS) if the launched file matches the
    3a (dynamic external/mutable code load/exec) OR 3b (input-listen RCE)
    predicate. ``ext`` is reserved for future per-language refinement."""
    return _matches_3a(content) or _matches_3b(content)


# ────────────────────────────────────────────────────────────────────────
# Install-line cleanliness (C4) + the C2 scan
# ────────────────────────────────────────────────────────────────────────


# A SEPARATE sink on the install line itself (C4): a shell ``eval``/``exec``
# with a space-form argument (``eval "$X"`` — no paren, so ``_matches_3a``'s
# ``eval\(`` misses it), a ``source``/``.`` of a non-in-tree path, or a
# pipe-to-interpreter. re2-safe.
_INSTALL_LINE_SHELL_SINK_RE: Final[re.Pattern[str]] = re.compile(
    r"(?:^|[\s;&|])(?:eval|exec)\s+['\"$]"  # eval "$x" / exec $cmd / eval 'x'
    r"|(?:^|[\s;&|])(?:source|\.)\s+(?:\$|~|/tmp/|/var/)"  # source $X / . /tmp/y
    r"|\|\s*(?:bash|sh|zsh|python\d?|node|perl|ruby|php)\b",  # ... | sh
    re.IGNORECASE,
)


def _install_line_clean(install_line: str) -> bool:
    """C4: the install line itself carries NO separate exec/exfil sink beyond
    the persistence verb. Returns ``True`` (clean) when no 3a sink, no
    curl|sh, and no shell eval/exec/source sink is present on the line;
    ``False`` → STAY CRITICAL."""
    # Re-run the 3a predicate (catches paren-form eval/exec, curl|interp,
    # subprocess shell=True, …) + a shell space-form sink check on the line.
    if _matches_3a(install_line):
        return False
    if _INSTALL_LINE_SHELL_SINK_RE.search(install_line):
        return False
    return True


# A launch of another script via an interpreter / source: ``exec bash X`` /
# ``python X`` / ``source X`` / ``. X``. Group 1 is the launched token.
_LAUNCH_TOKEN_RE: Final[re.Pattern[str]] = re.compile(
    r"(?:^|[\s;&|])(?:exec\s+)?(?:bash|sh|zsh|python\d?|node|perl|ruby|source|\.)\s+(\S+)",
    re.IGNORECASE,
)
# A bare ``exec <program>`` (no interpreter) — ``exec ./real`` / ``exec /opt/x``.
_EXEC_BARE_RE: Final[re.Pattern[str]] = re.compile(r"(?:^|[\s;&|])exec\s+(\S+)")


def _launch_targets(content: str, plugin_root: Path) -> list[Path] | None:
    """Collect EVERY in-tree program ``content`` launches — an interpreter
    launch (``bash X`` / ``python X`` / ``source X`` / ``. X``) or a bare
    ``exec X`` — de-duplicated, in order.

    Per line, interpreter-launch matches take precedence over bare-exec (so
    ``exec python3 ./x`` yields ``./x``, not ``python3``); ACROSS lines, ALL
    launches are collected (NO first-match short-circuit — that short-circuit
    was the multi-exec FN hole: a launcher running ``python ./clean.py`` then
    ``python ./evil.py`` had only ``clean.py`` scanned, so an evil second
    target cleared).

    Returns the list of resolved in-tree next-hop paths; an EMPTY list means the
    file launches nothing (a clean leaf). Returns ``None`` — the chain MUST FAIL
    — the moment ANY launch token does NOT resolve to an in-tree fixed-path file
    (an inline-code flag ``-c``, a ``$VAR``, an out-of-tree path, or an external
    binary). FN-safe: a launcher that execs a clean script AND an
    unfollowed/unresolvable one can never clear."""
    targets: list[Path] = []
    seen: set[str] = set()
    for raw in content.split("\n"):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        # Per line, prefer interpreter-launch matches; only if there are none,
        # fall back to the bare ``exec <prog>`` form. ``finditer`` so a single
        # line that launches more than once (``bash ./a && bash ./b``) yields
        # every target, not just the first.
        matches = list(_LAUNCH_TOKEN_RE.finditer(line)) or list(_EXEC_BARE_RE.finditer(line))
        for mo in matches:
            candidate = mo.group(1)
            # An inline-code launch (``bash -c '...'`` / ``python -c ...``) has
            # the flag as the captured token → no scannable file → the launched
            # code is not provably inert → FAIL the chain.
            if candidate.startswith("-"):
                return None
            nxt = _resolve_in_tree(candidate, plugin_root)
            if nxt is None:  # leaves the tree / is variable / unresolvable → FAIL
                return None
            k = str(nxt)
            if k not in seen:
                seen.add(k)
                targets.append(nxt)
    return targets


def _scan_target_clean(content: str, rel_path: str) -> bool:
    """C2: ``scan_content`` yields no non-suppressed, non-demoted CRITICAL/
    MAJOR finding in the execution/exfil category set. ``True`` = clean.
    FAILS-SAFE: a scanner import failure → ``False`` (treat as dirty)."""
    try:
        from cpv_skillaudit_native import scan_content  # type: ignore[import-not-found]
    except ImportError:
        return False
    try:
        findings = scan_content(content, rel_path)
    except Exception:
        # Any scanner error → fail-safe (cannot prove clean).
        return False
    for f in findings:
        if f.get("suppressed") or f.get("demoted"):
            continue
        if f.get("severity") not in _DISQUALIFYING_SEVERITIES:
            continue
        if f.get("category") in _EXEC_EXFIL_CATEGORIES:
            return False
    return True


def _target_chain_passes(
    program_path: Path,
    plugin_root: Path,
    on_path: set[str],
    proven: set[str],
    depth: int,
) -> bool:
    """Run C2 (CLEAN) + C3 (NON-EXPLOITABLE) on ``program_path`` and follow
    EVERY thin-launcher → real-script hop transitively, bounded at
    ``_MAX_CHAIN`` (§7). Returns ``True`` only when the program AND every
    program it (transitively) launches are in-tree, clean, and inert; ``False``
    (→ STAY CRITICAL) on any failure, an out-of-tree/unresolvable hop, a cycle,
    or depth overflow.

    ``on_path`` is the set of resolved paths on the current DFS stack (the cycle
    guard); ``proven`` memoizes paths already fully verified clean, so a file
    reached by several launch paths (a diamond) is verified exactly once —
    diamond- and blow-up-safe — and a legitimate re-visit returns its proven
    result instead of falsely tripping the cycle guard."""
    if depth > _MAX_CHAIN:
        return False
    try:
        real = program_path.resolve()
    except (OSError, RuntimeError):
        return False
    key = str(real)
    if key in proven:  # already fully verified clean+inert (diamond/blow-up-safe)
        return True
    if key in on_path:  # cycle on the current DFS path
        return False
    on_path.add(key)
    ok = False
    try:
        try:
            content = real.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return False
        rel = real.name
        try:
            rel = str(real.relative_to(plugin_root.resolve()))
        except (OSError, ValueError):
            pass
        ext = real.suffix.lower()
        # C3 — NON-EXPLOITABLE (this hop).
        if _non_exploitable(content, ext):
            return False
        # C2 — CLEAN (this hop).
        if not _scan_target_clean(content, rel):
            return False
        # §7 — follow EVERY launched program. A leaf (no launch token) yields an
        # empty list → ``all()`` is vacuously True → PASS. A launch token whose
        # hop is unresolvable / out-of-tree / inline-eval → ``None`` → FAIL (the
        # chain leaves the tree). Otherwise ALL launched targets must pass — no
        # first-match short-circuit, which was the multi-exec FN hole where a
        # clean-first/evil-second launcher cleared because only the first target
        # was scanned.
        nxts = _launch_targets(content, plugin_root)
        if nxts is None:
            return False
        ok = all(
            _target_chain_passes(nxt, plugin_root, on_path, proven, depth + 1)
            for nxt in nxts
        )
    finally:
        on_path.discard(key)
    if ok:
        proven.add(key)
    return ok


# ────────────────────────────────────────────────────────────────────────
# Public predicate
# ────────────────────────────────────────────────────────────────────────


def persistence_launches_clean_inert_target(
    install_line: str,
    install_file: str,
    plugin_root: Path,
    *,
    full_content: str | None = None,
) -> bool:
    """True iff the persistence mechanism on ``install_line`` launches a
    target that is RESOLVABLE-in-tree (C1) AND CLEAN (C2) AND NON-EXPLOITABLE
    (C3) AND the install line itself is clean (C4). FALSE (→ STAY CRITICAL) on
    any failure or uncertainty.

    Follows a launcher→real-script indirection chain up to ``_MAX_CHAIN`` hops
    (§7); leaving the tree or becoming unresolvable at any hop → ``False``.

    The clear is INTRINSIC — computed from the launched code's content/AST,
    never a self-declaration. Every persistence-malware shape still BLOCKS.
    """
    # C4 FIRST (cheap, no I/O) — a separate sink on the install line itself
    # disqualifies regardless of what the daemon launches.
    if not _install_line_clean(install_line):
        return False
    # C1 — RESOLVABLE in-tree.
    rt = resolve_launched_target(
        install_line, install_file, plugin_root, full_content=full_content
    )
    if rt is None:
        return False
    # The C1 resolver already rejected an inline-code-flag argv. Now run the
    # transitive C2+C3 chain over the resolved program (and any extra_sources).
    # ``proven`` is shared across all top-level calls so a script reached from
    # both the program and an extra_source is verified once; each top-level call
    # gets its own fresh ``on_path`` cycle-guard.
    proven: set[str] = set()
    if not _target_chain_passes(rt.program_path, plugin_root, set(), proven, 1):
        return False
    # Every plist/unit ``extra_sources`` is a hop-1 target that must also pass.
    for extra in rt.extra_sources:
        if not _target_chain_passes(extra, plugin_root, set(), proven, 1):
            return False
    return True
