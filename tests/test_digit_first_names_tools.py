"""Digit-first names are accepted by every CPV authoring tool (TRDD-ZO54IL46).

Claude Code accepts names such as ``3-pillars-tasks-management``; the shared
validator pattern already does. These tests pin the remaining tool-level
patterns to the same rule, and keep the path-safety and prose guards intact.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import add_component  # noqa: E402
import add_dependencies  # noqa: E402
import cpv_pack_components  # noqa: E402
import cpv_strip_dev  # noqa: E402
import generate_marketplace_repo  # noqa: E402
import standardize_marketplace  # noqa: E402
import validate_xref  # noqa: E402
from cpv_validation_common import NAME_PATTERN  # noqa: E402

NAME_REGEXES = [
    add_component._NAME_RE,
    cpv_pack_components._NAME_RE,
    add_dependencies._SPEC_NAME_RE,
    generate_marketplace_repo.KEBAB_RE,
    standardize_marketplace.KEBAB_RE,
]


@pytest.mark.parametrize("regex", NAME_REGEXES)
def test_tool_name_regex_equals_shared_pattern(regex):
    """Each tool-level name regex is the shared validator pattern, so the two never drift."""
    assert regex.pattern == NAME_PATTERN.pattern


@pytest.mark.parametrize("regex", NAME_REGEXES)
@pytest.mark.parametrize("name", ["3-pillars-tasks-management", "3d", "2fa-helper", "a", "cpv-agent"])
def test_tool_name_regex_accepts_digit_first_and_letter_first(regex, name):
    """A digit-first or letter-first kebab-case name is accepted."""
    assert regex.match(name)


@pytest.mark.parametrize("regex", NAME_REGEXES)
@pytest.mark.parametrize("name", ["-x", "x-", "a--b", "A", "a_b", "../x", "a/b", ""])
def test_tool_name_regex_still_rejects_bad_names(regex, name):
    """Leading or trailing hyphen, double hyphen, uppercase, underscore and path parts stay rejected."""
    assert not regex.match(name)


def test_add_component_validate_name_accepts_digit_first():
    """add_component._validate_name no longer exits on a digit-first name."""
    add_component._validate_name("3-pillars-tasks-management", "skill")


def test_add_dependencies_parses_digit_first_spec():
    """A digit-first dependency name and marketplace parse as a spec."""
    assert add_dependencies._parse_add_spec("3-pillars-tasks-management@3p-market") == {
        "name": "3-pillars-tasks-management",
        "marketplace": "3p-market",
    }


@pytest.mark.parametrize("src", ["3d/tools", "src/3d", "docs_dev/", "a/b_c/d-e"])
def test_strip_dev_src_accepts_digit_first_segments(src):
    """A src path whose segments start with a digit is safe and accepted."""
    assert cpv_strip_dev._SAFE_SRC_RE.match(src)


@pytest.mark.parametrize("src", ["../x", "/abs", "a/../b", "a/.hidden", "A/b", ""])
def test_strip_dev_src_still_rejects_unsafe_paths(src):
    """Traversal, absolute, dot and uppercase paths stay rejected."""
    assert not cpv_strip_dev._SAFE_SRC_RE.match(src)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("spawn the 3d-renderer agent", ["3d-renderer"]),
        ("use 3-pillars-helper agent", ["3-pillars-helper"]),
        ("invoke cpv-spark agent", ["cpv-spark"]),
        ("use 2 agents in parallel", []),
        ("call 10 agent", []),
    ],
)
def test_agent_spawn_pattern_digit_first_needs_a_letter(text, expected):
    """A digit-first agent name is captured; a bare number in prose is not."""
    assert validate_xref.AGENT_SPAWN_PATTERN.findall(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("see skills/3-pillars-tasks/SKILL.md", ["3-pillars-tasks"]),
        ("see skills/cpv-main-menu-skill", ["cpv-main-menu-skill"]),
        ("see skills/amvcp-wf-#anchor", ["amvcp-wf"]),
        ("/mnt/skills/3d-user/", []),
    ],
)
def test_skill_ref_pattern_accepts_digit_first(text, expected):
    """A digit-first skill reference is captured; trailing hyphens and absolute paths still are not."""
    assert validate_xref.SKILL_REF_PATTERN.findall(text) == expected
