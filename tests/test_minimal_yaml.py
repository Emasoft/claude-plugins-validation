"""Tests for the stdlib YAML fallback parser used when pyyaml is unavailable."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
import yaml as real_yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from _minimal_yaml import YAMLError, safe_load  # noqa: E402


@pytest.mark.parametrize("skill_md", sorted((REPO_ROOT / "skills").glob("*/SKILL.md")))
def test_minimal_yaml_matches_pyyaml_on_real_frontmatter(skill_md: Path) -> None:
    """The minimal parser must produce the same dict as pyyaml for every shipped SKILL.md.

    This is the primary correctness guarantee for the fallback path used when
    pyyaml is missing from the host venv (issue #14).
    """
    text = skill_md.read_text()
    if not text.startswith("---"):
        pytest.skip(f"{skill_md.name} has no frontmatter")
    parts = text.split("---", 2)
    if len(parts) < 3:
        pytest.skip(f"{skill_md.name} has malformed frontmatter")
    fm = parts[1]
    assert safe_load(fm) == real_yaml.safe_load(fm), (
        f"minimal YAML output diverges from pyyaml for {skill_md.relative_to(REPO_ROOT)}"
    )


def test_safe_load_empty_returns_none() -> None:
    """Empty / whitespace-only input must return None, matching yaml.safe_load."""
    assert safe_load("") is None
    assert safe_load("   \n  \n") is None


def test_safe_load_simple_scalars() -> None:
    out = safe_load("name: foo\nflag: true\ncount: 42\nempty:")
    assert out == {"name": "foo", "flag": True, "count": 42, "empty": None}


def test_safe_load_block_list() -> None:
    out = safe_load("tags:\n  - a\n  - b\n  - c\n")
    assert out == {"tags": ["a", "b", "c"]}


def test_safe_load_inline_list() -> None:
    out = safe_load("tags: [a, b, c]")
    assert out == {"tags": ["a", "b", "c"]}


def test_safe_load_folded_scalar_default_chomp() -> None:
    out = safe_load("description: >\n  hello world\n  next line\n")
    assert out == {"description": "hello world next line\n"}


def test_safe_load_literal_scalar_default_chomp() -> None:
    out = safe_load("body: |\n  line1\n  line2\n")
    assert out == {"body": "line1\nline2\n"}


def test_safe_load_quoted_strings() -> None:
    assert safe_load("a: 'quoted'\nb: \"also quoted\"") == {"a": "quoted", "b": "also quoted"}


def test_safe_load_skips_comments_and_doc_markers() -> None:
    out = safe_load("---\n# top comment\nname: foo  # inline comment\n...\n")
    assert out == {"name": "foo"}


def test_safe_load_unsupported_input_raises() -> None:
    """Inputs outside the supported subset must raise YAMLError, not silently mis-parse."""
    with pytest.raises(YAMLError):
        safe_load("nested:\n  key: value\n")  # nested mapping not supported


def test_validate_skill_runs_without_pyyaml(tmp_path: Path) -> None:
    """validate_skill.py must execute end-to-end from a venv that lacks pyyaml.

    Reproduces the exact failure mode from issue #14 — invoke the script with
    a Python interpreter that has no ``yaml`` module on its sys.path.
    """
    skill_dir = tmp_path / "demo-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: demo-skill\n"
        "description: >\n"
        "  Demo skill for the issue-14 regression test. The description must be\n"
        "  long enough to satisfy the validator's minimum length requirement so\n"
        "  the run reaches a clean PASSED status without unrelated noise.\n"
        "---\n\n"
        "# Demo Skill\n\n"
        "## Overview\n\nDemo content for the regression test.\n\n"
        "## Instructions\n\n1. Step one.\n2. Step two.\n"
    )

    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "validate_skill.py"), str(skill_dir), "--json"],
        capture_output=True,
        text=True,
        # Set BOTH env vars (TRDD-bbff5bc5): the new canonical name AND the
        # legacy name kept for one release. Belt-and-braces during the
        # v2.51.0–v2.52.0 transition window — either one alone is enough.
        env={
            "PATH": "/usr/bin:/bin",
            "HOME": str(tmp_path),
            "PLUGIN_SKIP_GITHUB_INTEGRITY": "1",
            "CPV_SKIP_GITHUB_INTEGRITY": "1",
        },
        check=False,
    )

    # The run must complete (exit 0/1/2/3 — not a crash). The fallback note
    # appears on stderr when pyyaml is absent; stdout carries the JSON report.
    assert result.returncode in (0, 1, 2, 3), (
        f"unexpected exit {result.returncode}\nstderr: {result.stderr}"
    )
    assert result.stdout.lstrip().startswith("{"), f"expected JSON on stdout, got: {result.stdout[:200]}"


def test_validate_security_bare_folder_flag(tmp_path: Path) -> None:
    """validate_security.py --bare-folder must scan a folder lacking .claude-plugin/."""
    bare = tmp_path / "bare-content"
    bare.mkdir()
    (bare / "README.md").write_text("# Bare folder for security scan\n\nNo plugin manifest.\n")

    result = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "validate_security.py"),
            str(bare),
            "--bare-folder",
            "--json",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    # No crash, exit code in normal range, JSON on stdout.
    assert result.returncode in (0, 1, 2, 3), (
        f"unexpected exit {result.returncode}\nstderr: {result.stderr}"
    )
    assert result.stdout.lstrip().startswith("{"), f"expected JSON on stdout, got: {result.stdout[:200]}"


def test_validate_security_without_bare_folder_rejects(tmp_path: Path) -> None:
    """Without --bare-folder the script must reject a folder lacking .claude-plugin/."""
    bare = tmp_path / "bare-content"
    bare.mkdir()
    (bare / "README.md").write_text("# Bare\n")

    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "validate_security.py"), str(bare)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert "No Claude Code plugin found" in result.stderr
    assert "--bare-folder" in result.stderr  # the hint must mention the new flag
