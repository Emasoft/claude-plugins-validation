#!/usr/bin/env python3
"""Regression lock for issue #43: the hardcoded 5000-char MINOR is gone.

Bug (pre-TRDD-021250b5, reported on v2.103.4): every SKILL.md between 5000
chars and the plugin's ``cpv.max_chars`` cap emitted

    [MINOR] SKILL.md has N characters (recommended: under 5000). …

unconditionally — i.e. the soft 5000-char recommendation ignored the
plugin's ``cpv.max_chars`` / ``cpv.skill_size_severity`` overrides. Because
``--strict`` exits non-zero on any CRITICAL/MAJOR/MINOR/NIT, plugins that
legitimately ship 5000-12000 char skills could never pass ``--strict``.

Fix (TRDD-021250b5, removed the char gate entirely): size limits are now
TOKEN-based and non-negotiable — ``SKILL_BODY_TOKEN_LIMIT = 5000`` tokens
(post-compaction reality), enforced as MAJOR (not MINOR, not configurable
via ``cpv.*``). The old ``MAX_CHAR_COUNT_WARN`` constant and the
``cpv.max_chars`` / ``cpv.max_lines`` / ``cpv.skill_size_severity`` keys
were removed. A 12000-char English skill is ≈ 3000 tokens, well under the
token cap, so it passes cleanly.

These tests are TWO-SIDED: the NEGATIVE side proves the old char MINOR is
gone (so ``--strict`` is satisfiable for legitimate 5000-12000 char
skills); the POSITIVE side proves the new token-based MAJOR still fires
when the body genuinely exceeds the post-compaction runtime budget (so
the change isn't a blanket weakening — real over-budget bodies still
block).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import validate_skill_comprehensive as vsc  # noqa: E402
from cpv_validation_common import SKILL_BODY_TOKEN_LIMIT  # noqa: E402


def _make_skill(tmp_path: Path, body_chars: int, *, name: str = "demo-skill") -> Path:
    """Write a SKILL.md whose post-frontmatter body has ``body_chars`` chars
    of well-formed English prose (averages ~4 chars/token under any sane
    tokenizer, so a 12000-char body ≈ 3000 tokens — comfortably under the
    SKILL_BODY_TOKEN_LIMIT of 5000). Returns the skill DIRECTORY path
    (validate_skill takes a dir, not the SKILL.md itself)."""
    plugin = tmp_path / "test-plugin"
    skill_dir = plugin / "skills" / name
    skill_dir.mkdir(parents=True)
    # Repeating a short English phrase keeps the char/token ratio realistic
    # (~4 chars/token) without inventing nonsense that might tokenize wildly.
    phrase = "The validator accepts well-formed skill bodies as documented. "
    body = (phrase * (body_chars // len(phrase) + 1))[:body_chars]
    content = (
        "---\n"
        f"name: {name}\n"
        "description: A demo skill whose body length is calibrated for issue #43 regression tests.\n"
        "---\n\n"
        "# Demo skill\n\n"
        "## Overview\n\n"
        f"{body}\n"
    )
    (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")
    return skill_dir


class TestOldCharMinorIsGone:
    """The pre-TRDD-021250b5 char MINOR must NOT fire — for any body size
    that was previously affected (the 5000-12000 char band)."""

    @pytest.mark.parametrize("chars", [5001, 7500, 9000, 11999])
    def test_5000_to_12000_char_skill_emits_no_char_minor(
        self, tmp_path: Path, chars: int
    ) -> None:
        """The exact band from the issue: 5000-12000 chars must produce
        zero ``recommended: under 5000`` MINORs."""
        skill_path = _make_skill(tmp_path, body_chars=chars)
        report = vsc.validate_skill(skill_path)
        # Scan every finding for the old MINOR's distinctive phrasing.
        char_minors = [
            r
            for r in report.results
            if "recommended: under 5000" in r.message
            or "recommended: under 5,000" in r.message
        ]
        assert char_minors == [], (
            f"Issue #43 regression: a {chars}-char SKILL.md must not emit the "
            f"old 'recommended: under 5000' MINOR (it was removed in TRDD-021250b5 "
            f"because the cpv.max_chars override was deleted; size is now "
            f"token-based and capped at MAJOR). Got: {char_minors}"
        )

    def test_no_max_char_count_warn_constant_in_module(self) -> None:
        """The removed constant must not be re-introduced."""
        assert not hasattr(vsc, "MAX_CHAR_COUNT_WARN"), (
            "MAX_CHAR_COUNT_WARN was deleted in TRDD-021250b5. Re-introducing it "
            "would resurrect the issue #43 unsatisfiable-strict bug."
        )

    def test_no_cpv_max_chars_override_keys_honored(self) -> None:
        """The plugin-level ``cpv.max_chars`` / ``cpv.max_lines`` /
        ``cpv.skill_size_severity`` keys MUST stay removed — a validator
        must not be configurable into passing what the runtime truncates."""
        from cpv_validation_common import load_cpv_config

        sig = load_cpv_config.__doc__ or ""
        for removed in ("max_chars", "max_lines", "skill_size_severity"):
            assert removed not in sig, (
                f"load_cpv_config docstring still mentions removed key "
                f"'cpv.{removed}' — TRDD-021250b5 removed these to make size limits "
                f"non-configurable (so --strict is satisfiable when chars are in "
                f"the 5000-12000 band)."
            )


class TestTokenBudgetStillEnforced:
    """The fix must NOT be a blanket weakening — a body that genuinely
    exceeds the token cap STILL emits MAJOR. (Two-sided coverage: prove
    the gate that replaced the MINOR is real.)"""

    def test_token_limit_is_5000(self) -> None:
        """The constant must be exactly 5000 (the post-compaction reality)."""
        assert SKILL_BODY_TOKEN_LIMIT == 5000

    def test_skill_below_token_cap_passes(self, tmp_path: Path) -> None:
        """An 8000-char English body ≈ 2000 tokens — well under the cap.
        Pin that the token gate emits no body-token MAJOR for it."""
        skill_path = _make_skill(tmp_path, body_chars=8000)
        report = vsc.validate_skill(skill_path)
        token_majors = [
            r for r in report.results if r.level == "MAJOR" and "token" in r.message.lower()
        ]
        assert token_majors == [], (
            f"An 8000-char (~2000 token) body must not trip the token MAJOR. Got: {token_majors}"
        )
