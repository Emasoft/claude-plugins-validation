#!/usr/bin/env python3
"""CPV doctor user-scope recipes (D9..D13) — TRDD-d1f74670.

Defect classes that only surface at user-scope (``~/.claude/``): stub files
left behind by a failed download, stale hardcoded years, dead local-script
references, and skill invocations missing/carrying the wrong namespace. D9
(ghost-agent dispatch) is a thin wrapper over the existing engine shipped by
TRDD-25b9be90 (``scripts/validate_xref.py``) applied to the user-scope tree;
D10..D13 are new here.

Each ``check_*`` function takes a root directory and a
:class:`cpv_validation_common.ValidationReport` and appends findings to it.
D13 (namespace correctness) is deliberately usable in BOTH user-scope and
plugin-scope per the TRDD's "Universal applicability" note.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from cpv_validation_common import BUILTIN_SLASH_COMMANDS, ValidationReport
from validate_xref import (
    BUILTIN_AGENTS,
    _extract_dispatch_refs,
    _resolve_dispatch_ref,
    parse_yaml_frontmatter,
)

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_COMPONENT_DIRS = ("skills", "agents", "commands")


def _strip_frontmatter(content: str) -> tuple[dict[str, Any] | None, str, int]:
    """Return (frontmatter_dict_or_None, body, body_start_line_1indexed)."""
    fm = parse_yaml_frontmatter(content)
    lines = content.split("\n")
    if fm is None or not lines or lines[0].strip() != "---":
        return None, content, 1
    closing_idx = None
    for idx in range(1, len(lines)):
        if lines[idx].strip() == "---":
            closing_idx = idx
            break
    if closing_idx is None:
        return None, content, 1
    body = "\n".join(lines[closing_idx + 1 :])
    return fm, body, closing_idx + 2


def _iter_instruction_files(root: Path) -> list[Path]:
    """Every SKILL.md / agent / command markdown file under root's component dirs."""
    files: list[Path] = []
    if not root.exists():
        return files
    for comp in _COMPONENT_DIRS:
        comp_dir = root / comp
        if not comp_dir.is_dir():
            continue
        files.extend(sorted(comp_dir.rglob("*.md")))
    return files


def _fenced_lang_at(lines: list[str], idx: int) -> str | None:
    """Return the fence language open at line idx (0-indexed), or None."""
    lang: str | None = None
    for i in range(idx + 1):
        stripped = lines[i].strip()
        if stripped.startswith("```"):
            if lang is None:
                lang = stripped[3:].strip().lower() or ""
            else:
                lang = None
    return lang


_DOC_FENCE_LANGS = {"text", "output", "console", "log"}


# ---------------------------------------------------------------------------
# D9 — Ghost-agent dispatch (delegates to TRDD-25b9be90's engine)
# ---------------------------------------------------------------------------


def check_ghost_dispatch(root: Path, report: ValidationReport) -> None:
    """D9 — flag Task()/subagent_type dispatch to a non-existent agent.

    Reuses ``validate_xref._extract_dispatch_refs`` /
    ``_resolve_dispatch_ref`` (the engine TRDD-25b9be90 shipped) applied
    per-line to the user-scope skills/agents/commands tree, so a finding
    carries a real line number. ``available_agents`` is the user-scope
    ``~/.claude/agents/`` inventory itself (there is no "plugin" here).
    """
    agents_dir = root / "agents"
    available_agents = {p.stem for p in agents_dir.glob("*.md")} if agents_dir.is_dir() else set()

    for path in _iter_instruction_files(root):
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        lines = content.split("\n")
        for lineno, line in enumerate(lines, start=1):
            fence_lang = _fenced_lang_at(lines, lineno - 1)
            if fence_lang in _DOC_FENCE_LANGS:
                continue
            for kind, name in _extract_dispatch_refs(line):
                if kind == "dynamic":
                    report.minor(
                        f'RC-GHOST-DISPATCH-002: dynamic subagent_type "{name}" — cannot statically verify',
                        file=str(path),
                        line=lineno,
                    )
                    continue
                status, _canonical = _resolve_dispatch_ref(
                    name, available_agents, user_scope_agents=None
                )
                if status == "cross_plugin":
                    report.nit(
                        f'RC-GHOST-DISPATCH-003: namespaced dispatch "{name}" — cannot verify cross-plugin',
                        file=str(path),
                        line=lineno,
                    )
                elif status == "ghost" and name not in BUILTIN_AGENTS:
                    report.critical(
                        f'RC-GHOST-DISPATCH-001: Task() dispatch to non-existent agent "{name}" — runtime will silently no-op',
                        file=str(path),
                        line=lineno,
                    )


# ---------------------------------------------------------------------------
# D10 — Stub / broken SKILL.md or agent.md
# ---------------------------------------------------------------------------

_STUB_ERROR_RE = re.compile(
    r"^\s*(404|Not Found|Error 4\d\d|<html|access denied|<!DOCTYPE)",
    re.IGNORECASE,
)


def check_stub_files(root: Path, report: ValidationReport) -> None:
    """D10 — a short file whose body is a failed-download error stub."""
    for path in _iter_instruction_files(root):
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        _fm, body, _start = _strip_frontmatter(content)
        stripped = body.strip()
        if len(stripped) < 200 and _STUB_ERROR_RE.search(stripped):
            report.major(
                "RC-STUB-FILE-001: file body looks like a failed-download stub "
                "(short body matching an HTTP-error/HTML pattern) — re-fetch from "
                "source or move the broken stub to backup",
                file=str(path),
            )


# ---------------------------------------------------------------------------
# D11 — Stale hardcoded year
# ---------------------------------------------------------------------------

_STALE_YEAR_RE = re.compile(
    r"(current year is 20\d\d|the year is 20\d\d|as of 20\d\d|> Note:[^\n]*20\d\d)",
    re.IGNORECASE,
)
_STALE_YEAR_EXCLUDE_RE = re.compile(
    r"(copyright|changelog|since|migrated|released|version|as of \d+ years?)",
    re.IGNORECASE,
)


def check_stale_year(root: Path, report: ValidationReport) -> None:
    """D11 — a hardcoded 'current year is YYYY' note that will go stale."""
    for path in _iter_instruction_files(root):
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        fm, body, body_start = _strip_frontmatter(content)
        lines = body.split("\n")
        found_any = False
        for i, line in enumerate(lines):
            fence_lang = _fenced_lang_at(lines, i)
            if fence_lang in _DOC_FENCE_LANGS:
                continue
            m = _STALE_YEAR_RE.search(line)
            if not m:
                continue
            if _STALE_YEAR_EXCLUDE_RE.search(line):
                continue
            report.minor(
                "RC-STALE-YEAR-001: stale hardcoded year note — replace with the "
                "dynamic-context substitution syntax `!`date +%Y`` (requires "
                "allowed-tools: Bash(date *))",
                file=str(path),
                line=body_start + i,
            )
            found_any = True
        if found_any and fm:
            allowed_tools = fm.get("allowed-tools") or fm.get("allowed_tools") or ""
            if "Bash(date" not in str(allowed_tools):
                report.info(
                    "RC-STALE-YEAR-001: frontmatter allowed-tools does not grant "
                    "Bash(date *) — add it so the !`date +%Y` fix actually works",
                    file=str(path),
                )


# ---------------------------------------------------------------------------
# D12 — Dead local-script reference
# ---------------------------------------------------------------------------

_SCRIPT_PATH_RE = re.compile(
    r"(?:~/\.claude/[a-z][a-z0-9_/.-]+\.(?:sh|py|js|ts|rb)"
    r"|\$CLAUDE_PROJECT_DIR/[a-z][a-z0-9_/.-]+\.(?:sh|py|js|ts|rb))"
)
_PLUGIN_TREE_MARKERS = ("plugins/cache/", "plugins/data/")


def _resolve_script_path(raw: str) -> Path:
    expanded = raw.replace("$CLAUDE_PROJECT_DIR", str(Path.cwd()))
    return Path(expanded).expanduser()


def _is_plugin_path(raw: str, resolved: Path) -> bool:
    text = f"{raw} {resolved}"
    return any(marker in text for marker in _PLUGIN_TREE_MARKERS)


def check_dead_script_refs(root: Path, report: ValidationReport) -> None:
    """D12 — a referenced local script that does not exist on disk.

    Scoped to user-scope + local-scope standalone components + hooks.
    Every path resolving into a plugin cache or data dir is skipped —
    a plugin may legitimately generate scripts in its data dir on first
    use, so a missing-now reference there is not a bug.
    """
    files = _iter_instruction_files(root)
    hooks_dir = root / "hooks"
    if hooks_dir.is_dir():
        files.extend(sorted(hooks_dir.rglob("*.json")))
    for settings in root.glob("settings*.json"):
        files.append(settings)

    for path in files:
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        lines = content.split("\n")
        for i, line in enumerate(lines):
            if line.strip().startswith("# "):
                continue
            fence_lang = _fenced_lang_at(lines, i)
            if fence_lang in {"text", "output", "console"}:
                continue
            for m in _SCRIPT_PATH_RE.finditer(line):
                raw = m.group(0)
                resolved = _resolve_script_path(raw)
                if _is_plugin_path(raw, resolved):
                    continue
                if resolved.exists():
                    continue
                report.major(
                    f'RC-DEAD-SCRIPT-REF-001: referenced script "{raw}" does not '
                    "exist on disk — remove the reference, create the script, or fix the path",
                    file=str(path),
                    line=i + 1,
                )


# ---------------------------------------------------------------------------
# D13 — Namespace correctness for skill/agent invocations
# ---------------------------------------------------------------------------

_SKILL_CALL_RE = re.compile(r'Skill\(\s*\{\s*skill:\s*["\']([a-zA-Z0-9_.:-]+)["\']')
# A plausible slash-command invocation: "/" at line start or after whitespace
# (never mid-path — "/usr/bin" and "a/b" must not match), the name is not
# followed by another "/" (rejects a path segment), and it is not immediately
# followed by a non-space character (rejects "/usr/bin", "/etc/passwd", …).
# Fenced code blocks are excluded separately in _extract_skill_mentions —
# a code example is documentation, not an invocation.
_SKILL_SLASH_RE = re.compile(
    r"(?:^|(?<=\s))/([a-zA-Z][a-zA-Z0-9_-]*(?::[a-zA-Z0-9_-]+)?)(?!\S)"
)

# An inline `code span` is documentation by the card's own D13 rule, exactly
# like a fenced block — `powercfg /h off`, `schtasks /run /tn X`, `cmd /c ver`
# are Windows CLI FLAGS, not slash-command invocations.
_INLINE_CODE_RE = re.compile(r"(`+)(?:(?!\1).)*\1")

# An HTTP route in prose ("GET /health", "the /search endpoint") is a URL path,
# never a command — measured as 3 of the 7 residual FPs on a real ~/.claude.
_HTTP_VERB_BEFORE_RE = re.compile(
    r"\b(?:GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+$", re.IGNORECASE
)
_ENDPOINT_AFTER_RE = re.compile(r"^\s+endpoints?\b", re.IGNORECASE)


def _inline_code_spans(line: str) -> list[tuple[int, int]]:
    """Character spans covered by inline `code` runs on this line."""
    return [(m.start(), m.end()) for m in _INLINE_CODE_RE.finditer(line)]


# Filesystem roots a prose "/name" can legitimately end a phrase with
# ("write it to /tmp") — never slash-command invocations, even though nothing
# follows the name so the path-segment guard above cannot reject them.
_FS_ROOT_NAMES = frozenset(
    {
        "tmp", "usr", "etc", "var", "opt", "bin", "sbin", "lib", "dev",
        "proc", "sys", "run", "home", "root", "private", "srv", "boot", "mnt",
    }
)


def _build_resolution_maps(scope_roots: list[Path]) -> tuple[set[str], dict[str, str]]:
    """Return (S_LOCAL, P_PLUGIN) per the TRDD's resolution-map spec."""
    s_local: set[str] = set()
    for scope_root in scope_roots:
        skills_dir = scope_root / "skills"
        if skills_dir.is_dir():
            for skill_md in skills_dir.glob("*/SKILL.md"):
                s_local.add(skill_md.parent.name)
        # commands/*.md are invocable exactly like skills (a typed "/name" and
        # the Skill tool both reach them), so they are resolution targets —
        # without them every command's own usage doc self-reference is a false
        # RC-NAMESPACE-UNRESOLVED-001 (measured: 8 of the 34 residual).
        commands_dir = scope_root / "commands"
        if commands_dir.is_dir():
            for cmd_md in commands_dir.glob("*.md"):
                s_local.add(cmd_md.stem)

    p_plugin: dict[str, str] = {}
    cache_root = Path.home() / ".claude" / "plugins" / "cache"
    if cache_root.is_dir():
        for marketplace_dir in cache_root.iterdir():
            if not marketplace_dir.is_dir():
                continue
            for plugin_dir in marketplace_dir.iterdir():
                if not plugin_dir.is_dir():
                    continue
                version_dirs = sorted(
                    (d for d in plugin_dir.iterdir() if d.is_dir()),
                    key=lambda d: d.name,
                )
                if not version_dirs:
                    continue
                latest = version_dirs[-1]
                skills_dir = latest / "skills"
                if skills_dir.is_dir():
                    for skill_md in skills_dir.glob("*/SKILL.md"):
                        p_plugin[skill_md.parent.name] = plugin_dir.name
                # Plugin commands are namespaced invocables too (see the
                # s_local commands note above); setdefault so a same-named
                # skill keeps precedence.
                commands_dir = latest / "commands"
                if commands_dir.is_dir():
                    for cmd_md in commands_dir.glob("*.md"):
                        p_plugin.setdefault(cmd_md.stem, plugin_dir.name)
    return s_local, p_plugin


def _extract_skill_mentions(content: str) -> list[tuple[int, str]]:
    """Return (line_1indexed, referenced_name) for genuine invocation surfaces."""
    fm, body, body_start = _strip_frontmatter(content)
    mentions: list[tuple[int, str]] = []

    if fm:
        for key in ("skills", "allowed-skills"):
            value = fm.get(key)
            if isinstance(value, list):
                for entry in value:
                    if isinstance(entry, str):
                        mentions.append((1, entry))

    lines = body.split("\n")
    for i, line in enumerate(lines):
        for m in _SKILL_CALL_RE.finditer(line):
            mentions.append((body_start + i, m.group(1)))
        # Slash-command mentions are documentation/paths far more often than
        # real invocations when they sit inside a fenced code block (e.g. a
        # ```text``` transcript or a ```bash``` example) — skip those; a
        # genuine invocation is written as plain-prose or backtick-free text.
        if _fenced_lang_at(lines, i) is not None:
            continue
        code_spans = _inline_code_spans(line)
        for m in _SKILL_SLASH_RE.finditer(line):
            name = m.group(1)
            # Inline code is documentation (a CLI flag, a path), not an
            # invocation. Skill({skill:...}) is deliberately NOT subject to
            # this — that token is an invocation marker even in an example.
            if any(s <= m.start() < e for s, e in code_spans):
                continue
            # An HTTP route, not a command.
            if _HTTP_VERB_BEFORE_RE.search(line[: m.start()]) or _ENDPOINT_AFTER_RE.match(
                line[m.end() :]
            ):
                continue
            # "/plan", "/help", "/clear" resolve to Claude Code BUILT-INS and a
            # bare "/tmp" is a filesystem root — neither is an unresolved skill
            # reference, so counting them re-opens the D13 FP firehose one
            # name at a time (measured: 22 of the 34 residual findings).
            if name in BUILTIN_SLASH_COMMANDS or name in _FS_ROOT_NAMES:
                continue
            mentions.append((body_start + i, name))
    return mentions


def check_namespace_correctness(
    root: Path,
    report: ValidationReport,
    *,
    extra_scope_roots: list[Path] | None = None,
) -> None:
    """D13 — a skill invocation using the wrong (or missing) namespace prefix.

    Universal: works for a user-scope tree OR a single plugin's own tree
    (pass the plugin root and no ``extra_scope_roots``).
    """
    scope_roots = [root, *(extra_scope_roots or [])]
    s_local, p_plugin = _build_resolution_maps(scope_roots)

    for path in _iter_instruction_files(root):
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for lineno, ref in _extract_skill_mentions(content):
            if ":" in ref:
                ns, _, skill = ref.partition(":")
                if skill in s_local and skill not in p_plugin:
                    report.minor(
                        f'RC-NAMESPACE-SPURIOUS-001: drop the "{ns}:" prefix — '
                        f'"{skill}" is a standalone skill, not a plugin skill',
                        file=str(path),
                        line=lineno,
                    )
                continue

            in_local = ref in s_local
            in_plugin = ref in p_plugin
            if in_local and in_plugin:
                report.major(
                    f'RC-NAMESPACE-AMBIGUOUS-001: bare reference to "{ref}" is '
                    f"ambiguous — exists in both user-scope and {p_plugin[ref]}. "
                    "Pick one explicitly",
                    file=str(path),
                    line=lineno,
                )
            elif in_plugin and not in_local:
                report.major(
                    f'RC-NAMESPACE-MISSING-001: add namespace "{p_plugin[ref]}:{ref}"',
                    file=str(path),
                    line=lineno,
                )
            elif not in_local and not in_plugin:
                report.critical(
                    f'RC-NAMESPACE-UNRESOLVED-001: referenced skill "{ref}" not '
                    "found in user-scope, local-scope, or any installed plugin",
                    file=str(path),
                    line=lineno,
                )


# ---------------------------------------------------------------------------
# Entry point — run all five recipes against one root
# ---------------------------------------------------------------------------


def run_user_scope_recipes(root: Path, report: ValidationReport) -> None:
    """Run D9..D13 against ``root`` (typically ``~/.claude``)."""
    check_ghost_dispatch(root, report)
    check_stub_files(root, report)
    check_stale_year(root, report)
    check_dead_script_refs(root, report)
    check_namespace_correctness(root, report)
