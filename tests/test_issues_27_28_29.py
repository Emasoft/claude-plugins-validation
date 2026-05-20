#!/usr/bin/env python3
"""Regression tests for issues #27, #28, #29 (TRDD-6edd2743 / v2.97.0).

Three issues filed against v2.96.0 by the ``visual-comunicator`` plugin
during its canon upgrade:

* **Issue #27** — ``validate_xref.SKILL_REF_PATTERN`` allowed a trailing
  hyphen in the captured token. Body text like ``skills/amvcp-wf-#anchor``
  produced phantom skill name ``amvcp-wf-`` that no plugin can ship,
  emitting a MAJOR "Reference to non-existent skill" and blocking the
  publish gate.

* **Issue #28** — ``cpv.allow_pipeline_drift`` in ``plugin.json`` was
  documented in the ``RC-PIPELINE-DRIFT-001`` WARNING help text since
  v2.86.0 but never actually consumed. Adding the key was a no-op.

* **Issue #29** — ``gen_mega_linter_yml`` emitted the deprecated
  ``DISABLE_REPORTERS:`` list form that fails Mega-Linter v8+ JSON
  Schema validation. Every plugin adopting the canon got a broken
  ``.mega-linter.yml``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))


# =============================================================================
# Issue #27 — phantom skill cross-reference
# =============================================================================


class TestIssue27SkillRefRegexTightening:
    """``SKILL_REF_PATTERN`` must not capture a token ending in ``-``."""

    def test_skills_amvcp_wf_anchor_does_not_capture_trailing_dash(self) -> None:
        """Body text like ``skills/amvcp-wf-#anchor`` must NOT yield a
        phantom ``amvcp-wf-`` capture — the boundary char terminates the
        token but a trailing dash must not leak into the capture."""
        from validate_xref import SKILL_REF_PATTERN

        matches = SKILL_REF_PATTERN.findall("see skills/amvcp-wf-#archetypes")
        # Either no match or a non-dash-terminated match
        for m in matches:
            assert not m.endswith("-"), (
                f"Capture {m!r} ends in '-' — issue #27 regression. "
                f"Pattern: {SKILL_REF_PATTERN.pattern}"
            )

    def test_amvcp_wf_archetypes_full_capture(self) -> None:
        """Full skill names like ``amvcp-wf-archetypes`` must still be
        captured correctly (the fix must not break valid refs)."""
        from validate_xref import SKILL_REF_PATTERN

        matches = SKILL_REF_PATTERN.findall("see skills/amvcp-wf-archetypes here")
        assert "amvcp-wf-archetypes" in matches, (
            f"Valid skill ref lost — got {matches}"
        )

    def test_single_letter_skill_name_still_matches(self) -> None:
        """The optional-tail form must still admit single-letter names."""
        from validate_xref import SKILL_REF_PATTERN

        matches = SKILL_REF_PATTERN.findall("see skills/a here")
        assert "a" in matches, f"Single-letter skill name not captured: {matches}"

    def test_trailing_dash_via_path_separator(self) -> None:
        """``skills/foo-/SKILL.md`` (trailing dash before slash) must NOT
        produce capture ``foo-`` — only ``foo``."""
        from validate_xref import SKILL_REF_PATTERN

        matches = SKILL_REF_PATTERN.findall("skills/foo-/SKILL.md")
        for m in matches:
            assert not m.endswith("-"), (
                f"Trailing-dash leak: {m!r} — issue #27 regression"
            )

    def test_belt_and_suspenders_post_filter(self, tmp_path: Path) -> None:
        """Even if a future regex change re-allows trailing hyphens, the
        post-filter in ``cross_reference_skills`` must drop captures
        ending in ``-`` before they reach the comparison."""
        # Build a fixture plugin with a SKILL.md that contains the
        # boundary-char trigger pattern, then run validate_skill_refs
        # and assert no phantom MAJOR is emitted.
        from validate_xref import CrossReferenceValidationReport, validate_skill_refs

        plugin = tmp_path / "fixture"
        skill = plugin / "skills" / "demo"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text(
            "---\nname: demo\ndescription: Demo skill\n---\n\n"
            "# demo\n\n"
            "Cross-reference: skills/demo-#section anchor.\n"
        )
        (plugin / ".claude-plugin").mkdir()
        (plugin / ".claude-plugin" / "plugin.json").write_text(
            json.dumps({"name": "demo", "version": "0.1.0", "description": "fix"})
        )

        report = CrossReferenceValidationReport()
        # Pass the actual skill name as the available set
        validate_skill_refs(plugin, report, {"demo"})

        majors = [r for r in report.results if r.level == "MAJOR"]
        phantom = [m for m in majors if m.message.endswith("'demo-'")]
        assert phantom == [], (
            f"Phantom 'demo-' MAJOR leaked through despite post-filter: "
            f"{[m.message for m in phantom]}"
        )


# =============================================================================
# Issue #29 — gen_mega_linter_yml v8+ schema
# =============================================================================


class TestIssue29MegaLinterTemplateV8Schema:
    """``gen_mega_linter_yml`` must emit v8+ boolean form, not the
    deprecated list form."""

    def _generated(self) -> str:
        """Invoke the template generator and return the YAML body."""
        # gen_mega_linter_yml is unparameterized in current source — call
        # via inspect-friendly path.
        import inspect

        import generate_plugin_repo as gen

        sig = inspect.signature(gen.gen_mega_linter_yml)
        return gen.gen_mega_linter_yml(None) if sig.parameters else gen.gen_mega_linter_yml()

    def test_v8_boolean_form_present(self) -> None:
        """The template MUST contain ``GITHUB_COMMENT_REPORTER: false``
        (the v8+ schema-correct form)."""
        body = self._generated()
        assert "GITHUB_COMMENT_REPORTER: false" in body, (
            "v8+ boolean form missing from gen_mega_linter_yml output"
        )

    def test_deprecated_list_form_absent_as_yaml_directive(self) -> None:
        """The template MUST NOT contain the deprecated list-style YAML
        directive. A comment line mentioning the deprecated name (for
        documentation of why it was removed) is fine — we filter to
        YAML directive lines only."""
        body = self._generated()
        # YAML directive: line starts with a key (no leading `#`) and
        # contains `DISABLE_REPORTERS:`
        offending = [
            line for line in body.splitlines()
            if "DISABLE_REPORTERS:" in line and not line.lstrip().startswith("#")
        ]
        assert offending == [], (
            f"Deprecated DISABLE_REPORTERS: directive still emitted: {offending}"
        )

    def test_deprecated_list_item_form_absent(self) -> None:
        """The deprecated form's list item ``- GITHUB_COMMENT_REPORTER``
        (under DISABLE_REPORTERS) must also be gone — only the boolean
        form should remain."""
        body = self._generated()
        # The list-item form is whitespace + `- GITHUB_COMMENT_REPORTER`
        offending = [
            line for line in body.splitlines()
            if line.strip() == "- GITHUB_COMMENT_REPORTER"
        ]
        assert offending == [], (
            f"Deprecated list-item form still present: {offending}"
        )


# =============================================================================
# Issue #28 — cpv.allow_pipeline_drift suppression
# =============================================================================


class TestIssue28AllowPipelineDriftHonoured:
    """``cpv.allow_pipeline_drift`` in plugin.json must suppress
    RC-PIPELINE-DRIFT-001 WARNINGs for listed files."""

    def _make_plugin(
        self,
        tmp_path: Path,
        drift_allowlist: list[str] | None = None,
    ) -> Path:
        """Build a fixture plugin with an intentionally-drifted file."""
        plugin = tmp_path / "drift-fixture"
        (plugin / ".claude-plugin").mkdir(parents=True)
        manifest: dict = {
            "name": "drift-fixture",
            "version": "0.1.0",
            "description": "drift suppression fixture",
        }
        if drift_allowlist is not None:
            manifest["cpv"] = {"allow_pipeline_drift": drift_allowlist}
        (plugin / ".claude-plugin" / "plugin.json").write_text(json.dumps(manifest))

        # Emit an obviously-drifted .mega-linter.yml — content
        # that no template would produce.
        (plugin / ".mega-linter.yml").write_text(
            "# drift-fixture intentional drift\n"
            "APPLY_FIXES: none\n"
            "DISABLE: []\n"
            "# This file intentionally diverges from canon.\n"
        )
        return plugin

    def test_drift_warning_fires_without_allow_list(self, tmp_path: Path) -> None:
        """Sanity check: the WARNING fires for a drifted file when no
        allow-list is present."""
        from cpv_validation_common import ValidationReport
        from validate_plugin import validate_canonical_pipeline_drift

        plugin = self._make_plugin(tmp_path, drift_allowlist=None)
        report = ValidationReport()
        validate_canonical_pipeline_drift(plugin, report)

        drift_warnings = [
            r for r in report.results
            if r.level == "WARNING" and "RC-PIPELINE-DRIFT-001" in r.message
            and ".mega-linter.yml" in r.message
        ]
        assert drift_warnings, (
            "Sanity baseline failed — RC-PIPELINE-DRIFT-001 must fire for "
            "a drifted file when no allow-list is present"
        )

    def test_drift_warning_suppressed_when_file_in_allow_list(self, tmp_path: Path) -> None:
        """When ``cpv.allow_pipeline_drift`` lists the drifted file's
        path, no RC-PIPELINE-DRIFT-001 WARNING is emitted for it."""
        from cpv_validation_common import ValidationReport
        from validate_plugin import validate_canonical_pipeline_drift

        plugin = self._make_plugin(
            tmp_path,
            drift_allowlist=[".mega-linter.yml"],
        )
        report = ValidationReport()
        validate_canonical_pipeline_drift(plugin, report)

        drift_warnings = [
            r for r in report.results
            if r.level == "WARNING" and "RC-PIPELINE-DRIFT-001" in r.message
            and ".mega-linter.yml" in r.message
        ]
        assert drift_warnings == [], (
            f"Suppression failed — drift WARNING still fired despite "
            f"cpv.allow_pipeline_drift containing the file path. Got: "
            f"{[w.message[:100] for w in drift_warnings]}"
        )

    def test_allow_list_non_list_silently_ignored(self, tmp_path: Path) -> None:
        """A malformed ``cpv.allow_pipeline_drift`` value (e.g. a string
        instead of a list) must not crash — fall back to empty list."""
        from cpv_validation_common import ValidationReport
        from validate_plugin import validate_canonical_pipeline_drift

        # Build the fixture directly with a malformed allow_pipeline_drift
        plugin = tmp_path / "malformed-drift-fixture"
        (plugin / ".claude-plugin").mkdir(parents=True)
        (plugin / ".claude-plugin" / "plugin.json").write_text(
            json.dumps({
                "name": "malformed-drift-fixture",
                "version": "0.1.0",
                "description": "malformed key",
                "cpv": {"allow_pipeline_drift": "not-a-list"},
            })
        )
        (plugin / ".mega-linter.yml").write_text(
            "# malformed-drift-fixture intentional drift\n"
            "APPLY_FIXES: none\n"
        )
        report = ValidationReport()
        # Must not crash
        try:
            validate_canonical_pipeline_drift(plugin, report)
        except Exception as exc:
            raise AssertionError(
                f"Malformed allow_pipeline_drift crashed validator: {exc}"
            ) from exc

    def test_allow_list_empty_strings_filtered(self, tmp_path: Path) -> None:
        """Whitespace-only or empty entries in the allow-list must be
        treated as no entry (no spurious matches against empty rel_path)."""
        from cpv_validation_common import ValidationReport
        from validate_plugin import validate_canonical_pipeline_drift

        plugin = self._make_plugin(
            tmp_path,
            drift_allowlist=["", "   ", ".mega-linter.yml"],
        )
        report = ValidationReport()
        validate_canonical_pipeline_drift(plugin, report)

        drift_warnings = [
            r for r in report.results
            if r.level == "WARNING" and "RC-PIPELINE-DRIFT-001" in r.message
            and ".mega-linter.yml" in r.message
        ]
        # Real entry .mega-linter.yml still suppresses
        assert drift_warnings == [], (
            f"Whitespace filtering broke real suppression: "
            f"{[w.message[:100] for w in drift_warnings]}"
        )
