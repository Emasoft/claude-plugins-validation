#!/usr/bin/env python3
"""
Claude Plugins Validation - Skill Validator

Validates individual skill directories according to Claude Code skill spec.
Based on: https://code.claude.com/docs/en/skills.md

Usage:
    uv run python scripts/validate_skill.py path/to/skill/
    uv run python scripts/validate_skill.py path/to/skill/ --verbose
    uv run python scripts/validate_skill.py path/to/skill/ --json

Exit codes:
    0 - All checks passed
    1 - CRITICAL issues found (skill will not work)
    2 - MAJOR issues found (significant problems)
    3 - MINOR issues found (may affect UX)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_USING_FALLBACK_YAML = False
_YAMLError: type[Exception] = Exception
try:
    import yaml as _real_yaml

    def _yaml_safe_load(text: str) -> Any:
        """Parse YAML frontmatter — pyyaml backend."""
        return _real_yaml.safe_load(text)

    _YAMLError = _real_yaml.YAMLError
except ImportError:
    # Fallback path so the script remains importable from a host venv that
    # lacks pyyaml. The minimal parser handles the narrow subset of YAML
    # used by skill frontmatter; complex shapes raise YAMLError and the
    # caller can prompt the user to install pyyaml. See issue #14.
    from _minimal_yaml import YAMLError as _MiniYAMLError
    from _minimal_yaml import safe_load as _mini_safe_load

    def _yaml_safe_load(text: str) -> Any:
        """Parse YAML frontmatter — minimal stdlib fallback (no pyyaml)."""
        return _mini_safe_load(text)

    _YAMLError = _MiniYAMLError
    _USING_FALLBACK_YAML = True

from cpv_validation_common import (  # noqa: E402  (import below conditional yaml fallback)
    BUILTIN_AGENT_TYPES,
    COLORS,
    DESCRIPTION_TOKEN_LIMIT,
    MIN_DESCRIPTION_CHARS,
    SKILL_FRONTMATTER_FIELDS,
    VALID_CONTEXT_VALUES,
    ValidationReport,
    check_token_limit,
    is_accepted_frontmatter_bool,
    is_known_skill_frontmatter_key,
    is_valid_model,
    save_report_and_print_summary,
    validate_component_name,
)
from cpv_validation_common import parse_frontmatter as _shared_parse_frontmatter  # noqa: E402

# Maximum recommended SKILL.md line count per Anthropic docs
MAX_SKILL_LINES = 500

# Known frontmatter fields per official docs
KNOWN_FRONTMATTER_FIELDS = SKILL_FRONTMATTER_FIELDS

# Identifier rule for `arguments` names — compiled once (single source of truth).
# A valid name must be a Python-style identifier so `$<name>` substitution works.
_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


@dataclass
class SkillValidationReport(ValidationReport):
    """Skill validation report with skill-specific metadata."""

    skill_path: str = ""


def validate_skill_md_exists(skill_path: Path, report: ValidationReport) -> bool:
    """Validate SKILL.md exists (required)."""
    skill_md = skill_path / "SKILL.md"

    if not skill_md.exists():
        report.critical("SKILL.md not found (required)", "SKILL.md")
        return False

    report.passed("SKILL.md exists", "SKILL.md")
    return True


def parse_frontmatter(content: str) -> tuple[dict[str, Any] | None, str, int]:
    """Parse YAML frontmatter from skill content.

    Thin wrapper over the shared ``cpv_validation_common.parse_frontmatter``
    (single source of truth — BOM strip + delimiter-LINE closer, audit m5/m6).
    Injects this module's pyyaml-or-``_minimal_yaml`` fallback (issue #14) so a
    host venv without pyyaml can still parse skill frontmatter.

    Returns ``(frontmatter_dict, body_content, frontmatter_end_line)`` and
    ``(None, content, 0)`` when no frontmatter is found.
    """
    return _shared_parse_frontmatter(content, yaml_loader=_yaml_safe_load, yaml_error=_YAMLError)


def validate_frontmatter(_skill_path: Path, content: str, report: ValidationReport) -> dict[str, Any] | None:
    """Validate YAML frontmatter structure and content."""
    del _skill_path  # parameter reserved for per-file reporting
    # Strip a leading UTF-8 BOM so the `startswith("---")` checks below agree
    # with parse_frontmatter (which strips it too). Without this, a BOM-prefixed
    # file would be mislabelled "No YAML frontmatter found". (audit MINOR m6)
    content = content.lstrip("﻿")
    # Check frontmatter exists
    if not content.startswith("---"):
        report.info("No YAML frontmatter found (optional but recommended)", "SKILL.md")
        return None

    # Parse frontmatter
    frontmatter, _body, _fm_end_line = parse_frontmatter(content)
    del _body, _fm_end_line  # only frontmatter dict is needed here

    if frontmatter is None:
        # Reaching here means parse_frontmatter failed. content.startswith("---")
        # is guaranteed True (line 123 already returned otherwise), so this is
        # always the "started with --- but failed to parse" case — a separate
        # bare `if frontmatter is None: return None` below was therefore dead
        # code and was removed (audit m165).
        report.critical(
            "Malformed YAML frontmatter (missing closing --- or invalid YAML)",
            "SKILL.md",
        )
        return None

    report.passed("Valid YAML frontmatter", "SKILL.md")

    # Validate known fields. The four v2.1.186 keys (display-name, default-enabled,
    # fallback, metadata) accept kebab/snake/camelCase, so use the casing-tolerant
    # test; every other key stays exact-match so typos still warn.
    for key in frontmatter.keys():
        if not is_known_skill_frontmatter_key(key):
            report.warning(
                f"Unknown frontmatter field '{key}' (may be ignored by CLI)",
                "SKILL.md",
            )

    return frontmatter


def validate_name_field(frontmatter: dict[str, Any], skill_dir_name: str, report: ValidationReport) -> None:
    """Validate the 'name' frontmatter field."""
    if "name" not in frontmatter:
        report.info(
            f"No 'name' field (will use directory name: {skill_dir_name})",
            "SKILL.md",
        )
        # Validate directory name as implicit skill name
        name = skill_dir_name
    else:
        name = frontmatter["name"]
        report.passed(f"'name' field present: {name}", "SKILL.md")

    if not isinstance(name, str):
        report.critical(f"'name' must be a string, got {type(name).__name__}", "SKILL.md")
        return

    # Uniform naming validation via shared function (includes dir-name match as MAJOR)
    validate_component_name(name, "skill", report, directory_name=skill_dir_name if "name" in frontmatter else None)


# TR3 (SkillSpector port, TRDD-de582146 / proposal TRDD-b0c85371): catch-all
# "trigger baiting" phrases in a skill description. A skill claiming to fire on
# EVERYTHING activates in unintended contexts and shadows other skills. These are
# anchored to genuinely-unscoped phrasings — a legitimately broad-but-scoped
# description ("use for any Python task") does NOT match, because the catch-all
# nouns here (request/message/prompt/query/everything/anything) signal
# "fires on every interaction" rather than a bounded domain. Advisory (WARNING),
# never blocking — over-broad activation is a quality/shadowing concern, not a
# security failure.
_CATCHALL_TRIGGER_RE: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\buse (?:this|it) for (?:everything|anything)\b",
        r"\bfor (?:any and all|all and any)\b",
        r"\b(?:on|for) every (?:request|message|prompt|query|input|interaction)s?\b",
        r"\bfor (?:all|any|every) (?:requests?|messages?|prompts?|queries|user inputs?|questions?)\b",
        r"\bwhenever (?:the )?user (?:says?|asks?|types?|sends?|writes?) (?:anything|something|a message)\b",
        r"\balways use this (?:skill|tool|command)\b",
        r"\b(?:matches|triggers|activates) on (?:everything|anything|all|any)\b",
        r"\bno matter what (?:the user )?(?:says|asks|wants|does)\b",
    )
)


def validate_description_field(frontmatter: dict[str, Any], body: str, report: ValidationReport) -> None:
    """Validate the 'description' frontmatter field."""
    if "description" not in frontmatter:
        # Check if body has content that could serve as description
        if body.strip():
            report.info(
                "No 'description' field (will use first paragraph of content)",
                "SKILL.md",
            )
        else:
            report.major(
                "No 'description' field and no body content for fallback",
                "SKILL.md",
            )
        return

    desc = frontmatter["description"]
    if not isinstance(desc, str):
        report.major(
            f"'description' must be a string, got {type(desc).__name__}",
            "SKILL.md",
        )
        return

    if len(desc) < MIN_DESCRIPTION_CHARS:
        report.minor(
            "Description is very short (may not help Claude decide when to use)",
            "SKILL.md",
        )

    # Token-based description gate — single source of truth (DESCRIPTION_TOKEN_LIMIT),
    # same canonical limit the comprehensive validator enforces. Replaces the old
    # pre-migration char-based `len(desc) > 500` MINOR. (audit MINOR agent #10)
    check_token_limit(
        desc,
        DESCRIPTION_TOKEN_LIMIT,
        report,
        "SKILL.md",
        "Description",
        "Shorten it — a long description dilutes the trigger signal.",
    )

    for rx in _CATCHALL_TRIGGER_RE:
        if rx.search(desc):
            report.warning(
                "Description uses a catch-all activation phrase — an overly-broad "
                "trigger makes the skill activate in unintended contexts and can shadow "
                "other skills. Scope the description to the skill's specific purpose.",
                "SKILL.md",
            )
            break

    report.passed("'description' field present", "SKILL.md")


def validate_context_field(frontmatter: dict[str, Any], report: ValidationReport) -> None:
    """Validate the 'context' frontmatter field."""
    if "context" not in frontmatter:
        return

    context = frontmatter["context"]

    if not isinstance(context, str):
        report.critical(
            f"'context' must be a string, got {type(context).__name__}",
            "SKILL.md",
        )
        return

    if context not in VALID_CONTEXT_VALUES:
        report.critical(
            f"Invalid 'context' value: '{context}'. Valid values: {VALID_CONTEXT_VALUES}",
            "SKILL.md",
        )
        return

    report.passed(f"'context' field valid: {context}", "SKILL.md")


def validate_agent_field(frontmatter: dict[str, Any], report: ValidationReport) -> None:
    """Validate the 'agent' frontmatter field."""
    if "agent" not in frontmatter:
        # Agent is only relevant if context: fork is set
        if frontmatter.get("context") == "fork":
            report.info(
                "'agent' not specified with context: fork (defaults to general-purpose)",
                "SKILL.md",
            )
        return

    agent = frontmatter["agent"]

    if not isinstance(agent, str):
        report.critical(
            f"'agent' must be a string, got {type(agent).__name__}",
            "SKILL.md",
        )
        return

    # Check if context: fork is set (required for agent to have effect)
    if frontmatter.get("context") != "fork":
        report.major(
            "'agent' field has no effect without 'context: fork'",
            "SKILL.md",
        )

    # Validate against known built-in types
    if agent in BUILTIN_AGENT_TYPES:
        report.passed(f"'agent' field valid (built-in): {agent}", "SKILL.md")
    else:
        # Could be a custom agent from .claude/agents/
        report.info(
            f"'agent' value '{agent}' is not a built-in type (may be custom from .claude/agents/)",
            "SKILL.md",
        )


def validate_boolean_field(
    frontmatter: dict[str, Any],
    field_name: str,
    report: ValidationReport,
) -> None:
    """Validate a boolean frontmatter field."""
    if field_name not in frontmatter:
        return

    value = frontmatter[field_name]

    if not is_accepted_frontmatter_bool(value):
        report.critical(
            f"'{field_name}' must be a boolean (true/false/yes/no/on/off/1/0), got {type(value).__name__}",
            "SKILL.md",
        )
        return

    report.passed(f"'{field_name}' field valid: {value}", "SKILL.md")


def validate_allowed_tools_field(frontmatter: dict[str, Any], report: ValidationReport) -> None:
    """Validate the 'allowed-tools' frontmatter field."""
    if "allowed-tools" not in frontmatter:
        return

    tools = frontmatter["allowed-tools"]

    if isinstance(tools, str):
        # Single tool or comma-separated list — respect parenthesized scopes
        # e.g. "Read, Bash(git:*,gh:*), Write" should yield 3 tools, not 4
        if "(" in tools and "," in tools:
            tool_list = []
            depth = 0
            current: list[str] = []
            for ch in tools:
                if ch == "(":
                    depth += 1
                    current.append(ch)
                elif ch == ")":
                    depth -= 1
                    current.append(ch)
                elif ch == "," and depth == 0:
                    token = "".join(current).strip()
                    if token:
                        tool_list.append(token)
                    current = []
                else:
                    current.append(ch)
            token = "".join(current).strip()
            if token:
                tool_list.append(token)
        else:
            tool_list = [t.strip() for t in tools.split(",")]
    elif isinstance(tools, list):
        tool_list = tools
    else:
        report.major(
            f"'allowed-tools' must be string or list, got {type(tools).__name__}",
            "SKILL.md",
        )
        return

    if not tool_list:
        # Empty allowed-tools ([] / "") is a VALID, explicit "no tools" (chat-only)
        # declaration — distinct from an ABSENT field, which means "inherit all
        # tools". Non-blocking (WARNING) but surfaced because an empty array is
        # usually a mistaken attempt at "allow everything".
        report.warning(
            "'allowed-tools' is empty ([]) — this forbids ALL tools, only "
            "chatting is allowed. If this is not intentional, fix it. If it was "
            "a mistaken attempt at allowing all tools, omitting the "
            "'allowed-tools' field entirely is the correct syntax (an absent "
            "field means all tools allowed).",
            "SKILL.md",
        )
        return

    report.passed(f"'allowed-tools' field valid: {len(tool_list)} tool(s)", "SKILL.md")


def validate_model_field(frontmatter: dict[str, Any], report: ValidationReport) -> None:
    """Validate the 'model' frontmatter field."""
    if "model" not in frontmatter:
        return

    model = frontmatter["model"]

    if not isinstance(model, str):
        report.major(
            f"'model' must be a string, got {type(model).__name__}",
            "SKILL.md",
        )
        return

    # Value-check the model via the shared single source of truth (same gate the
    # command + comprehensive validators use). Previously this path checked the
    # TYPE only, so a garbage value like `model: gpt-4` passed silently. (audit
    # MINOR m3 / MAJOR M1)
    if not is_valid_model(model):
        report.major(
            f"Invalid 'model' value: {model}. Valid: sonnet, opus, haiku, inherit, "
            "default, opusplan (optionally with [1m]), or full ID like claude-opus-4-6",
            "SKILL.md",
        )
        return

    report.passed(f"'model' field valid: {model}", "SKILL.md")


def validate_argument_hint_field(frontmatter: dict[str, Any], report: ValidationReport) -> None:
    """Validate the 'argument-hint' frontmatter field."""
    if "argument-hint" not in frontmatter:
        return

    hint = frontmatter["argument-hint"]

    if not isinstance(hint, str):
        report.major(
            f"'argument-hint' must be a string, got {type(hint).__name__}",
            "SKILL.md",
        )
        return

    report.passed(f"'argument-hint' field present: {hint}", "SKILL.md")


def validate_arguments_field(frontmatter: dict[str, Any], report: ValidationReport) -> list[str]:
    """Validate the 'arguments' frontmatter field (v2.1.121).

    Accepted forms (per skills.md):
      arguments: issue branch              # space-separated string
      arguments: ["issue", "branch"]       # YAML list

    Returns the list of declared argument names so callers can cross-validate
    `$<name>` substitutions in skill content.
    """
    if "arguments" not in frontmatter:
        return []
    raw = frontmatter["arguments"]
    if isinstance(raw, str):
        names = raw.split()
    elif isinstance(raw, list):
        if not all(isinstance(n, str) for n in raw):
            report.major(
                f"'arguments' list must contain only strings, got mixed types: {raw!r}",
                "SKILL.md",
            )
            return []
        names = list(raw)
    else:
        report.major(
            f"'arguments' must be a space-separated string or YAML list, got {type(raw).__name__}",
            "SKILL.md",
        )
        return []

    # Validate each name is a valid identifier — required for `$<name>` substitution.
    # Partition once via the module-compiled _IDENT_RE (single source of truth);
    # the old code ran re.fullmatch on every name TWICE with an inline literal. (audit NIT n2)
    valid = [n for n in names if _IDENT_RE.fullmatch(n)]
    invalid = [n for n in names if n not in valid]
    if invalid:
        report.major(
            f"'arguments' names must be valid identifiers (letters/digits/underscores, "
            f"starting with letter/underscore). Invalid: {invalid}",
            "SKILL.md",
        )

    report.passed(f"'arguments' field present: {names}", "SKILL.md")
    return valid


def validate_hooks_field(frontmatter: dict[str, Any], report: ValidationReport) -> None:
    """Validate the 'hooks' frontmatter field.

    Beyond the basic dict-shape check, this also runs the cross-platform +
    persistent-data checks against every command-type hook so skill-level
    hooks get the same scrutiny as hooks.json hooks (Windows portability,
    bash-only constructs, writes to ${CLAUDE_PLUGIN_ROOT}, etc.).
    """
    if "hooks" not in frontmatter:
        return

    hooks = frontmatter["hooks"]

    if not isinstance(hooks, dict):
        report.major(
            f"'hooks' must be an object, got {type(hooks).__name__}",
            "SKILL.md",
        )
        return

    # Recursively walk the hook tree and run cross-platform checks against
    # every command-type hook. Mirrors the validate_agent.py pattern.
    try:
        from validate_hook import check_hook_command_cross_platform
    except ImportError:
        check_hook_command_cross_platform = None  # type: ignore[assignment]

    if check_hook_command_cross_platform is not None:
        for event_config in hooks.values():
            if not isinstance(event_config, list):
                continue
            for matcher_block in event_config:
                if not isinstance(matcher_block, dict):
                    continue
                inner = matcher_block.get("hooks")
                if not isinstance(inner, list):
                    continue
                for h in inner:
                    if not isinstance(h, dict):
                        continue
                    if h.get("type") != "command":
                        continue
                    cmd = h.get("command")
                    if isinstance(cmd, str) and cmd.strip():
                        check_hook_command_cross_platform(cmd, report, file_label="SKILL.md")

    report.passed("'hooks' field present", "SKILL.md")


def validate_skill_content(
    content: str,
    report: ValidationReport,
    declared_args: list[str] | None = None,
) -> None:
    """Validate SKILL.md content (body after frontmatter).

    `declared_args` is the list of names declared via the frontmatter
    `arguments:` field (v2.1.121). When provided, any `$<name>` substitution
    in the body must match a declared name — otherwise the substitution
    silently expands to the empty string at runtime.
    """
    _, body, _ = parse_frontmatter(content)

    # Check for empty body
    if not body.strip():
        report.major("SKILL.md has no content after frontmatter", "SKILL.md")
        return

    # Check line count (recommendation: under 500 lines)
    total_lines = content.count("\n") + 1
    if total_lines > MAX_SKILL_LINES:
        report.minor(
            f"SKILL.md has {total_lines} lines (recommended: under {MAX_SKILL_LINES}). "
            "Consider moving detailed content to supporting files.",
            "SKILL.md",
        )
    else:
        report.passed(f"SKILL.md line count OK ({total_lines} lines)", "SKILL.md")

    # Check for $ARGUMENTS placeholder if skill seems action-oriented
    # (contains numbered steps, commands, etc.)
    if re.search(r"^\d+\.", body, re.MULTILINE) or "```bash" in body.lower():
        if "$ARGUMENTS" not in content:
            report.info(
                "Task-oriented skill without $ARGUMENTS placeholder (arguments will be appended automatically)",
                "SKILL.md",
            )

    # v2.1.121 — cross-validate `$<name>` substitutions against declared `arguments:`.
    #
    # Convention: skill arguments declared via `arguments:` are lowercase
    # snake_case (because they appear in YAML and human-readable docs). Shell
    # variables are conventionally UPPER_SNAKE_CASE (`$MAIN_ROOT`, `$REPORT`,
    # `$PWD`, etc.). We use that convention as the discriminator so a skill
    # author can freely document shell-variable usage without tripping a
    # "missing declared argument" finding.
    if declared_args is not None:
        # Strip fenced code blocks AND inline backtick spans first so that
        # `$VAR` examples in documentation don't trigger the check.
        stripped_body = re.sub(r"```.*?```", "", body, flags=re.DOTALL)
        stripped_body = re.sub(r"`[^`\n]*`", "", stripped_body)
        # Collect every $<name> occurrence.
        # Must skip `$ARGUMENTS`, `${...}` (env-var form is handled separately),
        # and `$<digit>` (positional form).
        # Also skip $ followed by punctuation/end-of-string (false matches).
        for m in re.finditer(r"\$([A-Za-z_][A-Za-z0-9_]*)\b", stripped_body):
            name = m.group(1)
            if name == "ARGUMENTS":
                continue  # well-known
            if name in declared_args:
                continue  # explicitly declared
            # Skip names that match known env vars used in skill substitution.
            if name in {
                "CLAUDE_SESSION_ID",
                "CLAUDE_EFFORT",
                "CLAUDE_SKILL_DIR",
                "CLAUDE_PLUGIN_ROOT",
                "CLAUDE_PLUGIN_DATA",
                "CLAUDE_PROJECT_DIR",
            }:
                continue
            # ALL_UPPERCASE names are shell-variable convention, NOT skill-arg
            # convention. Skill args are lowercase snake_case. So `$MAIN_ROOT`,
            # `$REPORT`, `$TIMESTAMP`, `$PWD`, `$HOME`, etc. are user-defined or
            # standard shell variables and not subject to the `$<name>` skill-arg
            # expansion contract. `name.isupper()` is True iff the name contains
            # at least one cased char and all cased chars are uppercase.
            if name.isupper():
                continue
            # Otherwise this is a likely-broken substitution.
            report.major(
                f"Skill content references `${name}` but '{name}' is not declared in "
                f"frontmatter `arguments:`. The substitution will silently expand "
                f"to the empty string at runtime.",
                "SKILL.md",
            )


def validate_directory_structure(skill_path: Path, report: ValidationReport) -> None:
    """Validate skill directory structure."""
    # Common optional directories per docs
    optional_dirs = ["scripts", "examples", "references", "assets", "templates"]

    for dir_name in optional_dirs:
        dir_path = skill_path / dir_name
        if dir_path.is_dir():
            report.passed(f"Optional directory exists: {dir_name}/")

    # Check for scripts that should be executable
    scripts_dir = skill_path / "scripts"
    if scripts_dir.is_dir():
        for script in scripts_dir.iterdir():
            if script.is_file() and script.suffix in {".sh", ".py", ".bash"}:
                if not os.access(script, os.X_OK):
                    report.major(
                        f"Script not executable: scripts/{script.name}",
                        f"scripts/{script.name}",
                    )
                else:
                    report.passed(
                        f"Script executable: scripts/{script.name}",
                        f"scripts/{script.name}",
                    )


def validate_supporting_files(skill_path: Path, report: ValidationReport) -> None:
    """Validate supporting files referenced in SKILL.md."""
    skill_md = skill_path / "SKILL.md"
    if not skill_md.exists():
        return

    content = skill_md.read_text(encoding="utf-8")

    # Find markdown links to local files
    local_refs = re.findall(r"\[([^\]]+)\]\(([^)]+)\)", content)

    for _, link_target in local_refs:
        # Skip external URLs
        if link_target.startswith(("http://", "https://", "mailto:")):
            continue

        # Skip pure in-page anchors
        if link_target.startswith("#"):
            continue

        # Strip a trailing `#fragment` before the existence check. A link like
        # `[API](references/api.md#section)` points at a SECTION inside a real
        # local file — the `#section` part is a heading anchor, not part of the
        # filename, so `skill_path / "references/api.md#section"` never exists
        # and would raise a false "Referenced file not found" MAJOR. Mirrors
        # validate_skill_comprehensive.py (which does the same split) so the
        # lightweight and comprehensive validators agree on anchored links.
        file_ref = link_target.split("#", 1)[0] if "#" in link_target else link_target
        if not file_ref:
            # Target was just `file.md#...` with an empty path before `#` —
            # already handled by the pure-anchor skip above, but guard anyway.
            continue

        # Check if referenced file exists
        ref_path = skill_path / file_ref
        if not ref_path.exists():
            report.major(
                f"Referenced file not found: {file_ref}",
                "SKILL.md",
            )
        else:
            report.passed(f"Referenced file exists: {file_ref}", "SKILL.md")


def validate_skill(skill_path: Path) -> SkillValidationReport:
    """Validate a complete skill directory.

    Args:
        skill_path: Path to the skill directory

    Returns:
        ValidationReport with all results
    """
    report = SkillValidationReport(skill_path=str(skill_path))

    # Check skill directory exists
    if not skill_path.is_dir():
        report.critical(f"Skill path is not a directory: {skill_path}")
        return report

    # Validate SKILL.md exists (required)
    if not validate_skill_md_exists(skill_path, report):
        return report

    # Read SKILL.md content
    skill_md = skill_path / "SKILL.md"
    content = skill_md.read_text(encoding="utf-8")

    # Validate frontmatter
    frontmatter = validate_frontmatter(skill_path, content, report)

    if frontmatter is not None:
        # Extract the body (content after frontmatter) ONCE — validate_description_field
        # inspects it for a fallback-description paragraph, and the tool-consistency
        # check needs it too. Passing the FULL `content` as `body` made
        # `body.strip()` always truthy (the frontmatter text is present), so the
        # "No 'description' field and no body content for fallback" MAJOR could
        # never fire and was silently demoted to the INFO branch (audit m20).
        _, _body, _ = parse_frontmatter(content)
        # Validate individual frontmatter fields
        validate_name_field(frontmatter, skill_path.name, report)
        validate_description_field(frontmatter, _body, report)
        validate_context_field(frontmatter, report)
        validate_agent_field(frontmatter, report)
        validate_boolean_field(frontmatter, "user-invocable", report)
        validate_boolean_field(frontmatter, "disable-model-invocation", report)
        validate_allowed_tools_field(frontmatter, report)
        # TRDD-94e06820: the body must not invoke a tool the declared field does
        # not grant — that call fails silently at runtime. Skipped when absent.
        from cpv_tool_permission_match import validate_body_tool_consistency  # noqa: PLC0415

        validate_body_tool_consistency(
            frontmatter.get("allowed-tools"), _body, report, filename="SKILL.md", field_name="allowed-tools"
        )
        validate_model_field(frontmatter, report)
        validate_argument_hint_field(frontmatter, report)
        # v2.1.121 — `arguments:` (separate from `argument-hint`) declares named
        # positional args used by `$<name>` substitution in skill content.
        declared_args = validate_arguments_field(frontmatter, report)
        validate_hooks_field(frontmatter, report)
    else:
        declared_args = []

    # Validate content (incl. cross-checking `$<name>` against declared_args)
    validate_skill_content(content, report, declared_args=declared_args)

    # Validate directory structure
    validate_directory_structure(skill_path, report)

    # Validate supporting files
    validate_supporting_files(skill_path, report)

    return report


def scan_one_skill(skill_dir: Path) -> list[dict]:
    """Top-level pickleable per-skill scan callable for ``parallel_scan``.

    Task #384: callers that need to validate MANY skills (e.g.
    ``validate_plugin.validate_skills`` walking ``plugin/skills/*``) want to
    fan the per-skill work out across CPU cores via
    ``cpv_parallel_runner.parallel_scan``. That harness requires a top-level
    importable callable taking exactly one ``Path`` arg and returning a list
    of pickleable findings — no closures, no shared mutable state.

    This shim wraps ``validate_skill()`` and serialises each
    ``ValidationResult`` into a plain dict via the existing ``to_dict()``
    contract. ``ValidationReport`` itself is a normal dataclass and would
    pickle fine, but returning the report object would force the harness to
    carry validator-specific types across process boundaries — dicts keep
    the per-validator surface generic (every parallel-scan agent returns
    the SAME shape: ``list[dict]`` with at minimum ``level`` + ``message``).

    The aggregator side (``validate_plugin.validate_skills``) reconstructs
    the per-skill report by feeding each dict back into
    ``ValidationReport.add(level, message, file, line)`` exactly the way
    the current serial loop already does (validate_plugin.py:3160).

    Errors raised inside this function bubble back to ``parallel_scan``
    which captures them into ``ScanResult.error`` (collect mode) — the
    validator itself never crashes the pool.

    Args:
        skill_dir: Path to the skill directory (must contain SKILL.md).

    Returns:
        List of finding dicts, one per ValidationResult on the report.
        Each dict has ``level`` + ``message`` and optionally ``file`` /
        ``line`` / ``phase`` / ``category`` / ``suggestion`` / ``fixable``
        / ``fix_id``. Order matches the order in which the validator
        appended results — preserved for deterministic downstream output.
    """
    report = validate_skill(skill_dir)
    return [r.to_dict() for r in report.results]


def print_results(report: SkillValidationReport, verbose: bool = False) -> None:
    """Print validation results in human-readable format."""
    colors = COLORS

    # Count by level
    counts = {"CRITICAL": 0, "MAJOR": 0, "MINOR": 0, "NIT": 0, "WARNING": 0, "INFO": 0, "PASSED": 0}
    for r in report.results:
        counts[r.level] += 1

    # Print header
    print("\n" + "=" * 60)
    print(f"Skill Validation: {report.skill_path}")
    print("=" * 60)

    # Print summary
    print("\nSummary:")
    print(f"  {colors['CRITICAL']}CRITICAL: {counts['CRITICAL']}{colors['RESET']}")
    print(f"  {colors['MAJOR']}MAJOR:    {counts['MAJOR']}{colors['RESET']}")
    print(f"  {colors['MINOR']}MINOR:    {counts['MINOR']}{colors['RESET']}")
    print(f"  {colors['NIT']}NIT:      {counts['NIT']}{colors['RESET']}")
    print(f"  {colors['WARNING']}WARNING:  {counts['WARNING']}{colors['RESET']}")
    if verbose:
        print(f"  {colors['INFO']}INFO:     {counts['INFO']}{colors['RESET']}")
        print(f"  {colors['PASSED']}PASSED:   {counts['PASSED']}{colors['RESET']}")

    # Print details
    print("\nDetails:")
    for r in report.results:
        if r.level == "PASSED" and not verbose:
            continue
        if r.level == "INFO" and not verbose:
            continue

        color = colors[r.level]
        reset = colors["RESET"]
        file_info = f" ({r.file})" if r.file else ""
        line_info = f":{r.line}" if r.line else ""
        print(f"  {color}[{r.level}]{reset} {r.message}{file_info}{line_info}")

    # Print final status
    print("\n" + "-" * 60)
    if report.exit_code == 0:
        print(f"{colors['PASSED']}✓ Skill validation passed{colors['RESET']}")
    elif report.exit_code == 1:
        crit = colors["CRITICAL"]
        rst = colors["RESET"]
        print(f"{crit}✗ CRITICAL issues - skill will not work{rst}")
    elif report.exit_code == 2:
        maj = colors["MAJOR"]
        rst = colors["RESET"]
        print(f"{maj}✗ MAJOR issues - significant problems{rst}")
    else:
        minor = colors["MINOR"]
        rst = colors["RESET"]
        print(f"{minor}! MINOR issues - may affect UX{rst}")

    print()


def print_json(report: SkillValidationReport) -> None:
    """Print validation results as JSON."""
    output = {
        "skill_path": report.skill_path,
        "exit_code": report.exit_code,
        "counts": {
            "critical": sum(1 for r in report.results if r.level == "CRITICAL"),
            "major": sum(1 for r in report.results if r.level == "MAJOR"),
            "minor": sum(1 for r in report.results if r.level == "MINOR"),
            "warning": sum(1 for r in report.results if r.level == "WARNING"),
            "nit": sum(1 for r in report.results if r.level == "NIT"),
            "info": sum(1 for r in report.results if r.level == "INFO"),
            "passed": sum(1 for r in report.results if r.level == "PASSED"),
        },
        "results": [{"level": r.level, "message": r.message, "file": r.file, "line": r.line} for r in report.results],
    }
    print(json.dumps(output, indent=2))


def main() -> int:
    """Main entry point.

    First action: verify CPV's own source has not been tampered with
    by checking each validator file's SHA256 against the GitHub
    canonical manifest. Exits with code 2 on mismatch.
    """
    from _plugin_verify_hashes import verify_self_integrity  # noqa: PLC0415

    verify_self_integrity(quiet=True)

    if _USING_FALLBACK_YAML:
        print(
            "Note: pyyaml not found in this venv — using minimal frontmatter parser. "
            "Install pyyaml for full YAML support: uv pip install pyyaml",
            file=sys.stderr,
        )
    parser = argparse.ArgumentParser(
        description="Validate a Claude Code skill directory.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Example: uv run python scripts/validate_skill.py skills/my-skill/",
    )
    parser.add_argument("skill_path", help="Path to the skill directory")
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Show all results including passed checks",
    )
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument(
        "--report", type=str, default=None, help="Save detailed report to file, print only summary to stdout"
    )
    parser.add_argument("--strict", action="store_true", help="Strict mode — NIT issues also block validation")
    args = parser.parse_args()

    skill_path = Path(args.skill_path).resolve()

    if not skill_path.exists():
        print(f"Error: {skill_path} does not exist", file=sys.stderr)
        return 1

    if not skill_path.is_dir():
        print(f"Error: {skill_path} is not a directory (expected a skill directory)", file=sys.stderr)
        return 1

    # Verify content type — skill directory must contain SKILL.md
    if not (skill_path / "SKILL.md").exists() and not (skill_path / "skill.md").exists():
        print(
            f"Error: No SKILL.md found in {skill_path}\nA valid skill directory must contain a SKILL.md file.",
            file=sys.stderr,
        )
        return 1

    report = validate_skill(skill_path)

    if args.json:
        print_json(report)
    else:
        if args.report:
            save_report_and_print_summary(
                report, Path(args.report), "Skill Validation", print_results, args.verbose, plugin_path=args.skill_path
            )
        else:
            print_results(report, args.verbose)

    if args.strict:
        return report.exit_code_strict()
    return report.exit_code


if __name__ == "__main__":
    sys.exit(main())
