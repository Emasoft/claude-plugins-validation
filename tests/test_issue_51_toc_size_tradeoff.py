#!/usr/bin/env python3
"""Two-sided tests for GitHub issue #51 — size-aware TOC-embedding demotion.

The COMPLETE-TOC-embedding rule and the 5000-token body cap pull in OPPOSITE
directions on the SAME SKILL.md: the TOC rule forces more verbatim content IN,
the cap punishes size. A skill that links many reference docs is pushed toward
the cap BY satisfying the TOC rule, then over it. Issue #51 resolves the
contradiction: when the body already exceeds the cap, the TOC-completeness
findings are DEMOTED from MINOR to NIT (with an explicit note) so the author
sees ONE actionable signal — the size MAJOR — instead of self-contradictory
guidance. The finding is NEVER silently dropped, and under-cap skills keep the
full MINOR (the rule is unchanged for them).

Only fix-path #3 (size-aware demotion) is implemented. Fix-path #4 (stable
size-cap message prefix) is intentionally NOT done: it exists to make a
plugin's OWN `message_substring` false-positive allowlist survive content
edits — but that self-exemption allowlist is exactly what the no-self-exemption
directive eliminates, so hardening CPV's messages to support it is
counter-productive. See the issue-#51 triage report.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import validate_skill_comprehensive as vsc  # noqa: E402
from cpv_validation_common import ValidationReport, validate_toc_embedding  # noqa: E402


def _ref_with_toc(d: Path, name: str = "guide.md") -> None:
    (d / name).write_text(
        "# Guide\n\n## Table of Contents\n\n"
        "- [Alpha](#alpha)\n- [Beta](#beta)\n- [Gamma](#gamma)\n\n"
        "## Alpha\n\nA\n\n## Beta\n\nB\n\n## Gamma\n\nG\n",
        encoding="utf-8",
    )


class TestTocDemotionUnit:
    """Unit-level: validate_toc_embedding honours body_over_token_cap."""

    def _setup(self, tmp_path: Path) -> tuple[str, Path]:
        refs = tmp_path / "references"
        refs.mkdir()
        _ref_with_toc(refs)
        content = "# Skill\n\nSee [the guide](references/guide.md) for details.\n"
        skill = tmp_path / "SKILL.md"
        skill.write_text(content, encoding="utf-8")
        return content, skill

    def test_under_cap_keeps_minor(self, tmp_path: Path):
        """Default (under-cap): the COMPLETE-TOC finding is MINOR, no demotion note."""
        content, skill = self._setup(tmp_path)
        r = ValidationReport()
        validate_toc_embedding(content, skill, tmp_path, r)
        toc = [x for x in r.results if "COMPLETE TOC" in x.message]
        assert toc, "a linked-not-embedded TOC must produce the COMPLETE-TOC finding"
        assert all(x.level == "MINOR" for x in toc)
        assert not any("Demoted to NIT" in x.message for x in r.results)

    def test_over_cap_demotes_to_nit(self, tmp_path: Path):
        """Over-cap: the COMPLETE-TOC finding is NIT with the demotion note —
        NEVER silently dropped, and no MINOR remains."""
        content, skill = self._setup(tmp_path)
        r = ValidationReport()
        validate_toc_embedding(content, skill, tmp_path, r, body_over_token_cap=True)
        toc = [x for x in r.results if "COMPLETE TOC" in x.message]
        assert toc, "TOC-completeness finding must still be emitted (never silently dropped)"
        assert all(x.level == "NIT" for x in toc)
        assert all("Demoted to NIT" in x.message for x in toc)
        assert not any(x.level == "MINOR" and "COMPLETE TOC" in x.message for x in r.results)

    def test_list_item_branch_also_demoted(self, tmp_path: Path):
        """The list-item TOC branch is demoted too."""
        refs = tmp_path / "references"
        refs.mkdir()
        _ref_with_toc(refs)
        content = "# Skill\n\n- [the guide](references/guide.md)\n"
        skill = tmp_path / "SKILL.md"
        skill.write_text(content, encoding="utf-8")
        r = ValidationReport()
        validate_toc_embedding(content, skill, tmp_path, r, body_over_token_cap=True)
        toc = [x for x in r.results if "COMPLETE TOC" in x.message]
        assert toc and all(x.level == "NIT" for x in toc)


class TestTocDemotionEndToEnd:
    """End-to-end via validate_skill: the caller computes over-cap from the
    SAME body + estimator the size gate uses, so the demotion predicate matches
    the size-MAJOR predicate exactly."""

    def _make_skill(self, tmp_path: Path, body_chars: int) -> Path:
        plugin = tmp_path / "plugin"
        skill_dir = plugin / "skills" / "big-skill"
        skill_dir.mkdir(parents=True)
        refs = skill_dir / "references"
        refs.mkdir()
        _ref_with_toc(refs)
        phrase = "The validator accepts well-formed skill bodies as documented. "
        body = (phrase * (body_chars // len(phrase) + 1))[:body_chars]
        content = (
            "---\n"
            "name: big-skill\n"
            "description: A skill whose body length is calibrated for the issue #51 regression tests.\n"
            "---\n\n"
            "# Big skill\n\n"
            "## Overview\n\n"
            "See [the guide](references/guide.md) for details.\n\n"
            f"{body}\n"
        )
        (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")
        return skill_dir

    def test_over_cap_skill_demotes_toc_and_keeps_size_major(self, tmp_path: Path):
        """The triage headline: an over-cap body keeps the size MAJOR AND demotes
        the TOC-completeness finding to NIT (one actionable signal, not two
        contradictory ones)."""
        # ~28000 chars ≈ 7000 tokens — over the 5000-token cap.
        skill_dir = self._make_skill(tmp_path, 28000)
        report = vsc.validate_skill(skill_dir)
        size_majors = [r for r in report.results if r.level == "MAJOR" and "token" in r.message.lower()]
        assert size_majors, "an over-cap body MUST still trip the token MAJOR"
        toc = [r for r in report.results if "COMPLETE TOC" in r.message]
        assert toc, "the TOC-completeness finding must still be emitted"
        assert all(r.level == "NIT" for r in toc), [(r.level, r.message[:60]) for r in toc]

    def test_under_cap_skill_keeps_toc_minor(self, tmp_path: Path):
        """Two-sided: an under-cap body trips no size MAJOR and keeps the TOC MINOR."""
        # ~12000 chars ≈ 3000 tokens — under the cap.
        skill_dir = self._make_skill(tmp_path, 12000)
        report = vsc.validate_skill(skill_dir)
        size_majors = [r for r in report.results if r.level == "MAJOR" and "token" in r.message.lower()]
        assert not size_majors, "an under-cap body must not trip the token MAJOR"
        toc = [r for r in report.results if "COMPLETE TOC" in r.message]
        assert toc and all(r.level == "MINOR" for r in toc), [(r.level, r.message[:60]) for r in toc]
