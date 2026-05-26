#!/usr/bin/env python3
"""Regression lock for issue #44: ``skills/<name>/`` as a filesystem-path or
URL segment must NOT be treated as an intra-plugin skill reference.

Bug (pre-fix): the bare ``(?:skill|skills)/<name>`` pattern matched anywhere
in a doc — including absolute paths (``/mnt/skills/user/...``,
``~/.pi/agent/skills/vercel-deploy/...``) and URLs
(``https://example.com/skills/foo``). The reference-checker then looked up
``user`` / ``vercel-deploy`` / ``foo`` as sibling skills, didn't find them,
and emitted MAJOR ``Reference to non-existent skill 'X'`` — forcing
plugin authors to obfuscate accurate filesystem-path documentation.

Fix: add a 2-char negative lookbehind ``(?<![A-Za-z0-9~]/)`` so the
``skills/`` segment only matches when NOT preceded by an alphanumeric-or-
tilde path token + ``/``. Intra-plugin shapes (bare, relative ``../``/
``./``, bracketed, variable-expanded ``${CLAUDE_PLUGIN_ROOT}/``,
``[label](skills/X/...)``) still match.

Two-sided coverage: the POSITIVE side proves intra-plugin references still
fire (so the existence check still catches typos); the NEGATIVE side
proves the three concrete user-reported shapes from issue #44 are now
silent.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from validate_xref import SKILL_REF_PATTERN  # noqa: E402


def _matches(text: str) -> list[str]:
    """Return the captured skill names from ``text`` (empty list if none)."""
    return SKILL_REF_PATTERN.findall(text)


class TestAbsolutePathSegmentsAreNotSkillRefs:
    """The three concrete shapes from issue #44 — all must yield zero matches."""

    def test_absolute_unix_path_skips(self) -> None:
        """``/mnt/skills/user/vercel-deploy/`` is an absolute filesystem path,
        not a sibling-skill reference (was emitting 2 MAJOR FPs)."""
        assert _matches("/mnt/skills/user/vercel-deploy/") == []

    def test_home_relative_path_skips(self) -> None:
        """``~/.pi/agent/skills/vercel-deploy/`` is a home-relative path."""
        assert _matches("~/.pi/agent/skills/vercel-deploy/") == []

    def test_url_skips(self) -> None:
        """``https://example.com/skills/foo`` is a URL path, not a skill ref."""
        assert _matches("https://example.com/skills/foo") == []

    def test_doc_line_from_issue_44_skips(self) -> None:
        """The exact doc line from the issue body — both ``user`` and
        ``vercel-deploy`` were flagged MAJOR before the fix."""
        line = (
            "`share.py` only looks in the Pi skill paths "
            "(`~/.pi/agent/skills/vercel-deploy/` and `/mnt/skills/user/vercel-deploy/`)"
        )
        # The path-segment shapes must yield zero matches; nothing else in
        # the line resembles a skill reference.
        assert _matches(line) == []

    def test_file_uri_skips(self) -> None:
        """``file:///mnt/skills/foo`` follows the same word-slash-skills shape."""
        assert _matches("file:///mnt/skills/foo") == []


class TestIntraPluginRefsStillMatch:
    """Legitimate intra-plugin shapes must keep matching (the existence
    check is what catches typos — we must not silently drop real refs)."""

    def test_bare_relative_matches(self) -> None:
        assert _matches("see skills/fix-validation for the recipe") == ["fix-validation"]

    def test_dot_slash_relative_matches(self) -> None:
        assert _matches("(./skills/foo)") == ["foo"]

    def test_dot_dot_slash_relative_matches(self) -> None:
        """``../skills/bar`` is the canonical markdown-link shape."""
        assert _matches("[bar](../skills/bar/SKILL.md)") == ["bar"]

    def test_bracketed_matches(self) -> None:
        assert _matches("(skills/canonical-pipeline)") == ["canonical-pipeline"]

    def test_backtick_quoted_matches(self) -> None:
        assert _matches("`skills/the-skills-menu`") == ["the-skills-menu"]

    def test_variable_expansion_matches(self) -> None:
        """``${CLAUDE_PLUGIN_ROOT}/skills/foo`` — the closing ``}`` isn't
        alphanumeric, so the lookbehind doesn't fire and the existence
        check still runs (catches typos in plugin-relative refs)."""
        assert _matches("${CLAUDE_PLUGIN_ROOT}/skills/cpv-doctor") == ["cpv-doctor"]

    def test_at_start_of_text_matches(self) -> None:
        """``skills/foo`` at the very start has no preceding context, so the
        2-char lookbehind can't fire — the ref still matches and the
        existence check still catches typos."""
        assert _matches("skills/plugin-validation-skill") == ["plugin-validation-skill"]


class TestFixIsPreciseNotBlanket:
    """The fix MUST NOT suppress every ``/skills/`` — only those that are
    clearly path segments. These are deliberately-edge shapes that must
    still produce a match (so a real broken ref isn't hidden)."""

    def test_paren_open_then_absolute_still_matches(self) -> None:
        """``(/skills/foo)`` — the opening ``(`` (not alphanumeric) before
        ``/`` means this is an in-text bracketed reference, not a path
        rooted from filesystem root — still match (existence check will
        sort it out)."""
        assert _matches("(/skills/foo)") == ["foo"]

    def test_space_then_skills_matches(self) -> None:
        """Plain prose ``the skills/X reference`` — single space before
        ``skills`` is not an alphanumeric+slash, lookbehind silent."""
        assert _matches("Load the skills/example reference") == ["example"]
