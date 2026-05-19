#!/usr/bin/env python3
"""Tests for v2.1.140 case- and separator-insensitive subagent_type matching.

CC v2.1.140 changelog:
    Improved Agent tool subagent_type matching to accept case- and
    separator-insensitive values (e.g. "Code Reviewer" resolves to
    code-reviewer)

CPV mirrors this so legal-but-non-canonical spellings are NIT (nudge
toward the canonical form), not MAJOR (broken reference).
"""

from __future__ import annotations

import sys
from pathlib import Path

scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

from validate_xref import (  # noqa: E402
    CrossReferenceValidationReport,
    _normalize_subagent_type,
    validate_subagent_type_matching,
)

# -----------------------------------------------------------------------------
# Normalizer unit tests
# -----------------------------------------------------------------------------


def test_normalize_kebab_case_unchanged():
    assert _normalize_subagent_type("code-reviewer") == "code-reviewer"


def test_normalize_title_case_with_space():
    assert _normalize_subagent_type("Code Reviewer") == "code-reviewer"


def test_normalize_snake_case():
    assert _normalize_subagent_type("code_reviewer") == "code-reviewer"


def test_normalize_camel_case_with_underscore():
    assert _normalize_subagent_type("Code_Reviewer") == "code-reviewer"


def test_normalize_multiple_spaces_collapsed():
    assert _normalize_subagent_type("Code   Reviewer") == "code-reviewer"


def test_normalize_mixed_separators():
    assert _normalize_subagent_type("Code _ Reviewer") == "code-reviewer"


def test_normalize_strips_whitespace_edges():
    assert _normalize_subagent_type("  code-reviewer  ") == "code-reviewer"


# -----------------------------------------------------------------------------
# Resolution tests (full integration via validate_subagent_type_matching)
# -----------------------------------------------------------------------------


def _make_plugin_with_references(tmp_path: Path, agents: list[str], refs: list[str]) -> Path:
    """Build a minimal plugin with given agent files and an orchestrator
    agent body that cites each `subagent_type: <ref>` value.

    Per TRDD-25b9be90 Phase 5: validate_subagent_type_matching scope is
    narrowed to executable directories (agents/, commands/, skills/), so
    the references must live inside one of those — not at plugin root.
    """
    plugin = tmp_path / "plugin"
    plugin.mkdir()
    (plugin / ".claude-plugin").mkdir()
    (plugin / ".claude-plugin" / "plugin.json").write_text(
        '{"name": "demo", "version": "0.0.1", "description": "test"}\n'
    )
    agents_dir = plugin / "agents"
    agents_dir.mkdir()
    for a in agents:
        (agents_dir / f"{a}.md").write_text(f"---\nname: {a}\ndescription: test agent\n---\n\nHello from {a}.\n")
    # Put the references in an orchestrator agent body (executable scope).
    lines = ["---\nname: orchestrator\ndescription: test orchestrator\n---\n\n# Demo plugin\n"]
    for r in refs:
        lines.append(f'Task(subagent_type: "{r}", prompt="hi")\n')
    (agents_dir / "orchestrator.md").write_text("".join(lines))
    return plugin


def test_canonical_kebab_case_reference_passes():
    """`subagent_type: "code-reviewer"` against agents/code-reviewer.md → no finding."""
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        plugin = _make_plugin_with_references(Path(td), ["code-reviewer"], ["code-reviewer"])
        report = CrossReferenceValidationReport()
        validate_subagent_type_matching(plugin, report, {"code-reviewer"})
        # Canonical references are silent — no findings.
        assert not any(
            r.level in ("CRITICAL", "MAJOR", "MINOR", "NIT") and "subagent_type" in r.message for r in report.results
        )


def test_title_case_with_space_emits_nit_not_major():
    """`subagent_type: "Code Reviewer"` against agents/code-reviewer.md → NIT (matches via v2.1.140)."""
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        plugin = _make_plugin_with_references(Path(td), ["code-reviewer"], ["Code Reviewer"])
        report = CrossReferenceValidationReport()
        validate_subagent_type_matching(plugin, report, {"code-reviewer"})
        nit_hits = [r for r in report.results if r.level == "NIT" and "Code Reviewer" in r.message]
        major_hits = [r for r in report.results if r.level == "MAJOR" and "Code Reviewer" in r.message]
        assert nit_hits, (
            f"expected NIT recommending canonical form, got: {[(r.level, r.message) for r in report.results]}"
        )
        assert not major_hits, (
            f"unexpected MAJOR on v2.1.140-resolvable reference: {[(r.level, r.message) for r in report.results]}"
        )
        # NIT body should mention the canonical form for actionable feedback.
        assert "code-reviewer" in nit_hits[0].message


def test_snake_case_emits_nit_not_major():
    """`subagent_type: "code_reviewer"` against agents/code-reviewer.md → NIT."""
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        plugin = _make_plugin_with_references(Path(td), ["code-reviewer"], ["code_reviewer"])
        report = CrossReferenceValidationReport()
        validate_subagent_type_matching(plugin, report, {"code-reviewer"})
        nit_hits = [r for r in report.results if r.level == "NIT" and "code_reviewer" in r.message]
        major_hits = [r for r in report.results if r.level == "MAJOR" and "code_reviewer" in r.message]
        assert nit_hits, f"expected NIT, got: {[(r.level, r.message) for r in report.results]}"
        assert not major_hits


def test_genuinely_missing_agent_emits_critical():
    """`subagent_type: "nonexistent-agent"` → CRITICAL RC-GHOST-DISPATCH-001 (per TRDD-25b9be90).

    Pre-TRDD-25b9be90 this was MAJOR; the bump to CRITICAL reflects the
    silent-failure class of the bug (runtime no-op, calling skill thinks
    it spawned a worker, nothing happens).
    """
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        plugin = _make_plugin_with_references(Path(td), ["code-reviewer"], ["nonexistent-agent"])
        report = CrossReferenceValidationReport()
        validate_subagent_type_matching(plugin, report, {"code-reviewer"})
        critical_hits = [r for r in report.results if r.level == "CRITICAL" and "nonexistent-agent" in r.message]
        nit_hits = [r for r in report.results if r.level == "NIT" and "nonexistent-agent" in r.message]
        assert critical_hits, (
            f"expected CRITICAL RC-GHOST-DISPATCH-001 on genuinely missing agent, got: "
            f"{[(r.level, r.message) for r in report.results]}"
        )
        assert "RC-GHOST-DISPATCH-001" in critical_hits[0].message
        assert not nit_hits


def test_mixed_canonical_and_normalized_references():
    """Mix of canonical and v2.1.140-normalized references produces 0 CRITICAL/MAJOR + 1 NIT."""
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        plugin = _make_plugin_with_references(
            Path(td),
            ["code-reviewer", "plugin-validator"],
            ["code-reviewer", "Plugin Validator"],
        )
        report = CrossReferenceValidationReport()
        validate_subagent_type_matching(plugin, report, {"code-reviewer", "plugin-validator"})
        nit_hits = [r for r in report.results if r.level == "NIT" and "Plugin Validator" in r.message]
        critical_hits = [r for r in report.results if r.level == "CRITICAL" and "subagent_type" in r.message]
        major_hits = [r for r in report.results if r.level == "MAJOR" and "subagent_type" in r.message]
        assert len(nit_hits) == 1
        assert len(critical_hits) == 0
        assert len(major_hits) == 0
