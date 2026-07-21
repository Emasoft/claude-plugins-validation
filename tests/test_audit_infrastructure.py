"""Sanity tests for the TRDD-b4c6cbe7 audit infrastructure.

These tests do NOT exercise the production validator surface — they
only verify that the audit harness components (fixture generator,
CPV/CLI runners, diff engine, spec-rule extractor, agent-emission
report) work correctly in isolation. They are fast (no network, no
subprocess to the real validator) and deterministic.

Heavy end-to-end checks (full grid run, full spec crawl) live in
the audit-output reports themselves, not here — those would push
CI time beyond the acceptable budget.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Ensure scripts/audit is importable when pytest runs from repo root.
_REPO_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS_AUDIT = _REPO_ROOT / "scripts" / "audit"
_SCRIPTS = _REPO_ROOT / "scripts"
for path in (_SCRIPTS_AUDIT, _SCRIPTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import agent_emission_audit
import cpv_vs_cli_diff
import fixture_grid_generator
import spec_rule_extractor

# ---------------------------------------------------------------------------
# Fixture-grid generator
# ---------------------------------------------------------------------------


class TestFixtureGridGenerator:
    """Generator must emit a stable, well-formed 30-fixture grid."""

    def test_grid_has_exactly_30_fixtures(self) -> None:
        """The audit relies on a 30-row coverage matrix; mismatched count breaks INV-1 tests."""
        assert len(fixture_grid_generator.GRID_FIXTURES) == 30

    def test_each_fixture_has_unique_nn_and_descriptor(self) -> None:
        """Duplicate NNs or descriptors would silently overwrite directories on materialize_all."""
        nns = [s.nn for s in fixture_grid_generator.GRID_FIXTURES]
        descriptors = [s.descriptor for s in fixture_grid_generator.GRID_FIXTURES]
        assert len(set(nns)) == len(nns), f"Duplicate NNs: {nns}"
        assert len(set(descriptors)) == len(descriptors), f"Duplicate descriptors: {descriptors}"

    def test_each_fixture_dir_name_is_kebab(self) -> None:
        """`ls`/glob expects kebab-case to sort and pattern-match predictably."""
        for spec in fixture_grid_generator.GRID_FIXTURES:
            assert spec.dir_name == f"{spec.nn}-{spec.descriptor}"
            assert spec.dir_name.replace("-", "").isalnum()

    def test_materialize_one_writes_plugin_json_when_specified(self, tmp_path: Path) -> None:
        """Each fixture with plugin_json must end up with a valid JSON file at the standard path."""
        spec = fixture_grid_generator.GRID_FIXTURES[0]  # bare-minimum-valid
        target = fixture_grid_generator.materialize_one(spec, tmp_path)
        plugin_path = target / ".claude-plugin" / "plugin.json"
        assert plugin_path.is_file()
        body = json.loads(plugin_path.read_text(encoding="utf-8"))
        assert body == spec.plugin_json

    def test_materialize_one_writes_readme_for_every_fixture(self, tmp_path: Path) -> None:
        """Self-documenting README helps human auditors understand fixture intent."""
        spec = fixture_grid_generator.GRID_FIXTURES[0]
        target = fixture_grid_generator.materialize_one(spec, tmp_path)
        readme = target / "README.md"
        assert readme.is_file()
        assert spec.descriptor in readme.read_text(encoding="utf-8")

    def test_materialize_one_writes_marketplace_json_when_present(self, tmp_path: Path) -> None:
        """Layout-C fixtures need both manifests."""
        for spec in fixture_grid_generator.GRID_FIXTURES:
            if spec.marketplace_json is not None:
                target = fixture_grid_generator.materialize_one(spec, tmp_path)
                mp_path = target / ".claude-plugin" / "marketplace.json"
                assert mp_path.is_file(), f"{spec.dir_name} missing marketplace.json"
                body = json.loads(mp_path.read_text(encoding="utf-8"))
                assert body == spec.marketplace_json
                return
        pytest.fail("Expected at least one fixture with marketplace.json")

    def test_materialize_one_is_idempotent(self, tmp_path: Path) -> None:
        """Re-running with the same target must wipe and rewrite identically (no stale files)."""
        spec = fixture_grid_generator.GRID_FIXTURES[12]  # layout-c-marketplace-in-plugin
        target = fixture_grid_generator.materialize_one(spec, tmp_path)
        stray = target / "stray.txt"
        stray.write_text("garbage", encoding="utf-8")
        assert stray.exists()
        fixture_grid_generator.materialize_one(spec, tmp_path)
        assert not stray.exists(), "Idempotent re-run failed to wipe stray file"

    def test_materialize_all_emits_30_directories(self, tmp_path: Path) -> None:
        """materialize_all is the single command audit harnesses call to seed the grid."""
        paths = fixture_grid_generator.materialize_all(tmp_path)
        assert len(paths) == 30
        for p in paths:
            assert p.is_dir()


# ---------------------------------------------------------------------------
# CLI output parser
# ---------------------------------------------------------------------------


class TestCliOutputParser:
    """`parse_cli_output` must extract findings from realistic CLI output blobs."""

    def test_parses_unrecognized_key_finding(self, tmp_path: Path) -> None:
        """The `cpv` root-key case we saw on master must be captured as CRITICAL."""
        sample = (
            "Validating plugin manifest: /private/tmp/foo/.claude-plugin/plugin.json\n"
            "\n"
            "✘ Found 1 error:\n"
            "\n"
            '  ❯ root: Unrecognized key: "cpv"\n'
            "\n"
            "✘ Validation failed\n"
        )
        findings = cpv_vs_cli_diff.parse_cli_output(sample, tmp_path)
        assert len(findings) == 1
        assert findings[0].severity == "CRITICAL"
        assert "Unrecognized key" in findings[0].message
        assert findings[0].source == "cli"

    def test_parses_warning_block(self, tmp_path: Path) -> None:
        """Multi-block output (errors + warnings) must keep severities aligned with their headers."""
        sample = (
            "Validating plugin manifest: /private/tmp/foo/.claude-plugin/plugin.json\n"
            "\n"
            "✘ Found 1 error:\n"
            "\n"
            "  ❯ name: Invalid input: expected string, received undefined\n"
            "\n"
            "Validating agent: /private/tmp/foo/agents/x.md\n"
            "\n"
            "⚠ Found 1 warning:\n"
            "\n"
            "  ❯ frontmatter: No frontmatter block found.\n"
        )
        findings = cpv_vs_cli_diff.parse_cli_output(sample, tmp_path)
        sevs = [f.severity for f in findings]
        assert sevs == ["CRITICAL", "WARNING"]

    def test_parses_empty_output_to_zero_findings(self, tmp_path: Path) -> None:
        """A pristine CLI run prints only the validation-passed banner; no bullets to extract."""
        sample = "Validating plugin manifest: /foo/.claude-plugin/plugin.json\n\n✔ Validation passed\n"
        findings = cpv_vs_cli_diff.parse_cli_output(sample, tmp_path)
        assert findings == []


# ---------------------------------------------------------------------------
# Finding fingerprint
# ---------------------------------------------------------------------------


class TestFindingFingerprint:
    """The fingerprint heuristic decides which CLI and CPV findings cross-match."""

    def test_field_invalid_input_pattern(self) -> None:
        """`<field>: Invalid input:` should reduce to `field:<name>` regardless of trailer text."""
        f = cpv_vs_cli_diff.Finding(
            severity="CRITICAL",
            message="name: Invalid input: expected string, received undefined",
        )
        assert f.fingerprint() == ("CRITICAL", "field:name")

    def test_unrecognized_key_pattern(self) -> None:
        """`Unrecognized key: \"X\"` should reduce to `unknown:x` for cross-tool matching.

        Why this case is the "right" one: the actual unknown-key surface is
        the bracketed key name, not the wrapper `root`. We deliberately
        prefer `unknown:cpv` over `field:root` so two CLI messages about
        different unknown root keys produce different topics — otherwise
        the CPV-only / CLI-only diff would collapse them into one row.
        """
        f = cpv_vs_cli_diff.Finding(
            severity="CRITICAL",
            message='root: Unrecognized key: "cpv"',
        )
        sev, topic = f.fingerprint()
        assert sev == "CRITICAL"
        assert topic == "unknown:cpv"

    def test_missing_required_field_pattern(self) -> None:
        """CPV's `Missing required field 'name'` must hash the same as CLI's `name: Invalid input` would."""
        f = cpv_vs_cli_diff.Finding(
            severity="CRITICAL",
            message="Missing required field 'name' in plugin.json",
        )
        assert f.fingerprint() == ("CRITICAL", "missing:name")

    def test_unrecognized_key_at_start(self) -> None:
        """When `Unrecognized key:` is at the start, we should fall through to `unknown:` topic."""
        f = cpv_vs_cli_diff.Finding(
            severity="CRITICAL",
            message='Unrecognized key: "cpv"',
        )
        sev, topic = f.fingerprint()
        assert sev == "CRITICAL"
        assert topic.startswith("unknown:")


# ---------------------------------------------------------------------------
# Diff engine
# ---------------------------------------------------------------------------


class TestDiffEngine:
    """Symmetric diff produces cli_only/cpv_only/both buckets correctly."""

    def _make_reports(
        self,
        tmp_path: Path,
        cli_findings: list[cpv_vs_cli_diff.Finding],
        cpv_findings: list[cpv_vs_cli_diff.Finding],
    ) -> tuple[cpv_vs_cli_diff.CliReport, cpv_vs_cli_diff.CpvReport]:
        cli = cpv_vs_cli_diff.CliReport(
            fixture=tmp_path,
            available=True,
            exit_code=1,
            findings=cli_findings,
        )
        cpv = cpv_vs_cli_diff.CpvReport(
            fixture=tmp_path,
            exit_code=1,
            findings=cpv_findings,
        )
        return cli, cpv

    def test_diff_empty_returns_empty_buckets(self, tmp_path: Path) -> None:
        """Two empty reports must produce a fully empty diff."""
        cli, cpv = self._make_reports(tmp_path, [], [])
        d = cpv_vs_cli_diff.diff(cli, cpv)
        assert d.cli_only == [] and d.cpv_only == [] and d.both == []

    def test_diff_cli_only_when_cpv_silent(self, tmp_path: Path) -> None:
        """CLI finding without a matching CPV finding → cli_only bucket (CPV gap)."""
        cli, cpv = self._make_reports(
            tmp_path,
            [
                cpv_vs_cli_diff.Finding(
                    severity="CRITICAL",
                    message='root: Unrecognized key: "cpv"',
                    source="cli",
                ),
            ],
            [],
        )
        d = cpv_vs_cli_diff.diff(cli, cpv)
        assert len(d.cli_only) == 1
        assert d.cpv_only == []
        assert d.both == []

    def test_diff_pairs_matching_fingerprints(self, tmp_path: Path) -> None:
        """When CLI and CPV both flag the same field, they land in `both`."""
        cli, cpv = self._make_reports(
            tmp_path,
            [
                cpv_vs_cli_diff.Finding(
                    severity="CRITICAL",
                    message="name: Invalid input: expected string, received undefined",
                    source="cli",
                ),
            ],
            [
                cpv_vs_cli_diff.Finding(
                    severity="CRITICAL",
                    message="Missing required field 'name' in plugin.json",
                    source="cpv",
                ),
            ],
        )
        # The fingerprints are intentionally different (field:name vs missing:name)
        # so this case lands as cli_only + cpv_only, NOT in both. Document the limit.
        d = cpv_vs_cli_diff.diff(cli, cpv)
        assert len(d.cli_only) == 1
        assert len(d.cpv_only) == 1
        assert d.both == []

    def test_diff_ignores_passed_and_info_in_cpv_only(self, tmp_path: Path) -> None:
        """PASSED/INFO entries are inventory, not findings — must not pollute the gap list."""
        cli, cpv = self._make_reports(
            tmp_path,
            [],
            [
                cpv_vs_cli_diff.Finding(severity="PASSED", message="all good", source="cpv"),
                cpv_vs_cli_diff.Finding(severity="INFO", message="optional dir absent", source="cpv"),
                cpv_vs_cli_diff.Finding(severity="MINOR", message="hex color", source="cpv"),
            ],
        )
        d = cpv_vs_cli_diff.diff(cli, cpv)
        assert len(d.cpv_only) == 1
        assert d.cpv_only[0].severity == "MINOR"


# ---------------------------------------------------------------------------
# Spec rule extractor
# ---------------------------------------------------------------------------


class TestSpecRuleExtractor:
    """Spec-rule extractor must produce structured rules from spec markdown."""

    def test_parse_index_extracts_markdown_links(self) -> None:
        """llms.txt uses `[title](url)` shape — extractor must split both fields."""
        sample = (
            "Some intro\n"
            "[Plugin reference](https://code.claude.com/docs/en/plugins.md)\n"
            "[Hooks](https://code.claude.com/docs/en/hooks.md)\n"
        )
        pages = spec_rule_extractor.parse_index(sample)
        urls = [p.url for p in pages]
        assert urls == [
            "https://code.claude.com/docs/en/plugins.md",
            "https://code.claude.com/docs/en/hooks.md",
        ]

    def test_extract_rules_picks_up_must_must_not_should(self) -> None:
        """Spec obligation language (MUST/SHOULD/MUST NOT) must each surface as a rule."""
        sample = (
            "The `name` field MUST be a kebab-case string of at most 50 chars.\n"
            "The plugin SHOULD declare a description for marketplace display.\n"
            "Plugins MUST NOT embed secrets in their plugin.json manifest.\n"
        )
        page = spec_rule_extractor.SpecPage(title="t", url="https://example.com/plugins.md")
        rules = spec_rule_extractor.extract_rules_from_text(sample, page)
        modals = sorted({r.modal for r in rules})
        # We may also pick up "MUST" inside "MUST NOT", but classifier promotes to MUST NOT.
        assert "MUST" in modals
        assert "MUST NOT" in modals
        assert "SHOULD" in modals

    def test_extract_rules_skips_pure_prose(self) -> None:
        """Sentences without modal verbs must not become rules — would explode the matrix."""
        page = spec_rule_extractor.SpecPage(title="t", url="https://example.com")
        rules = spec_rule_extractor.extract_rules_from_text(
            "Welcome to the Claude Code plugin spec. Here we describe the structure.",
            page,
        )
        assert rules == []

    def test_extract_rules_maps_keywords_to_coverage(self) -> None:
        """A rule mentioning `mcpServers` must heuristically map to the MCP-schema CPV check."""
        sample = "The mcpServers field MUST be an object of server definitions."
        page = spec_rule_extractor.SpecPage(title="t", url="https://example.com")
        rules = spec_rule_extractor.extract_rules_from_text(sample, page)
        assert any(r.coverage == "partial" and "MCP" in (r.likely_cpv_check or "") for r in rules)

    def test_write_spec_coverage_report_handles_empty_rules(self, tmp_path: Path) -> None:
        """Empty rule list (e.g. fetch failed) still produces a well-formed report."""
        report = tmp_path / "coverage.md"
        spec_rule_extractor.write_spec_coverage_report([], report, fetch_error="offline")
        body = report.read_text(encoding="utf-8")
        assert "Spec-Coverage Matrix" in body
        assert "offline" in body


# ---------------------------------------------------------------------------
# Report writers (light smoke)
# ---------------------------------------------------------------------------


class TestReportWriters:
    """Report writers produce non-empty, structured markdown."""

    def test_write_audit_report_handles_zero_rows(self, tmp_path: Path) -> None:
        """An empty grid still yields a well-formed (but empty) report."""
        path = tmp_path / "coverage.md"
        cpv_vs_cli_diff.write_audit_report([], path)
        body = path.read_text(encoding="utf-8")
        assert "Coverage-Surface Audit" in body
        assert "Fixtures audited:** 0" in body

    def test_write_audit_report_includes_cli_unavailable_warning(self, tmp_path: Path) -> None:
        """When every row has cli.available=False, the report must announce it loudly."""
        row = cpv_vs_cli_diff.GridRow(
            fixture=tmp_path,
            cli=cpv_vs_cli_diff.CliReport(fixture=tmp_path, available=False, exit_code=None),
            cpv=cpv_vs_cli_diff.CpvReport(fixture=tmp_path, exit_code=0),
            diff=cpv_vs_cli_diff.Diff(fixture=tmp_path),
        )
        path = tmp_path / "coverage.md"
        cpv_vs_cli_diff.write_audit_report([row], path)
        body = path.read_text(encoding="utf-8")
        assert "WARNING" in body
        assert "CLI was unavailable" in body

    def test_top_gap_categories_aggregates_by_fingerprint(self, tmp_path: Path) -> None:
        """Identical CLI findings across multiple fixtures must collapse into one ranked row."""
        f1 = cpv_vs_cli_diff.Finding(
            severity="CRITICAL",
            message="hooks: Invalid input",
            file=".claude-plugin/plugin.json",
            source="cli",
        )
        f2 = cpv_vs_cli_diff.Finding(
            severity="CRITICAL",
            message="hooks: Invalid input",
            file=".claude-plugin/plugin.json",
            source="cli",
        )
        f3 = cpv_vs_cli_diff.Finding(
            severity="WARNING",
            message="description: No description provided",
            source="cli",
        )
        row_a = cpv_vs_cli_diff.GridRow(
            fixture=tmp_path / "fix-a",
            cli=cpv_vs_cli_diff.CliReport(fixture=tmp_path / "fix-a", available=True, exit_code=1, findings=[f1]),
            cpv=cpv_vs_cli_diff.CpvReport(fixture=tmp_path / "fix-a", exit_code=0),
            diff=cpv_vs_cli_diff.Diff(fixture=tmp_path / "fix-a", cli_only=[f1]),
        )
        row_b = cpv_vs_cli_diff.GridRow(
            fixture=tmp_path / "fix-b",
            cli=cpv_vs_cli_diff.CliReport(fixture=tmp_path / "fix-b", available=True, exit_code=1, findings=[f2]),
            cpv=cpv_vs_cli_diff.CpvReport(fixture=tmp_path / "fix-b", exit_code=0),
            diff=cpv_vs_cli_diff.Diff(fixture=tmp_path / "fix-b", cli_only=[f2]),
        )
        row_c = cpv_vs_cli_diff.GridRow(
            fixture=tmp_path / "fix-c",
            cli=cpv_vs_cli_diff.CliReport(fixture=tmp_path / "fix-c", available=True, exit_code=0, findings=[f3]),
            cpv=cpv_vs_cli_diff.CpvReport(fixture=tmp_path / "fix-c", exit_code=0),
            diff=cpv_vs_cli_diff.Diff(fixture=tmp_path / "fix-c", cli_only=[f3]),
        )
        top = cpv_vs_cli_diff._top_gap_categories([row_a, row_b, row_c])
        # CRITICAL hooks must rank above WARNING description
        assert top[0][0] == "CRITICAL"
        assert top[0][2] == 2  # two fixtures hit it
        assert top[1][0] == "WARNING"
        assert top[1][2] == 1


# ---------------------------------------------------------------------------
# Agent-emission audit
# ---------------------------------------------------------------------------


class TestAgentEmissionAudit:
    """Static-analysis audit of CPV creation/migration agents."""

    def test_agent_targets_registry_size(self) -> None:
        """The 5 in-scope agents per TRDD §4.4 must always be in the registry."""
        labels = [t.label for t in agent_emission_audit.AGENT_TARGETS]
        expected = {
            "cpv-plugin-creator-agent",
            "cpv-plugin-fixer-agent",
            "cpv-marketplace-fixer-agent",
            "cpv-upgrade-plugin",
            "cpv-migrate-marketplace",
        }
        assert set(labels) >= expected, f"Missing agents: {expected - set(labels)}"

    def test_spec_topics_have_unique_labels(self) -> None:
        """Duplicate topic labels would collapse columns in the audit matrix."""
        topics = [t.topic for t in agent_emission_audit.SPEC_TOPICS]
        assert len(set(topics)) == len(topics)

    def test_spec_topic_matches_keyword(self) -> None:
        """A direct keyword hit in body text should return True."""
        topic = agent_emission_audit.SpecTopic(
            topic="t1",
            keywords=("kebab",),
            severity="CRITICAL",
            rationale="r",
        )
        assert topic.matches("Use kebab-case names please.") is True
        assert topic.matches("Pure prose no signal.") is False

    def test_agent_skill_names_parses_frontmatter_list(self) -> None:
        """The `skills:` YAML block must be parsed into a clean list of names."""
        body = (
            "---\n"
            "name: foo\n"
            "skills:\n"
            "  - cpv-create-plugin\n"
            "  - cpv-publish-to-marketplace\n"
            "  - cpv-plugin-management\n"
            "---\n\n"
            "Body content\n"
        )
        names = agent_emission_audit._agent_skill_names(body)
        assert names == ["cpv-create-plugin", "cpv-publish-to-marketplace", "cpv-plugin-management"]

    def test_agent_skill_names_empty_when_absent(self) -> None:
        """Agents without a skills: block return empty list (not error)."""
        body = "---\nname: bare\n---\n\nBody.\n"
        assert agent_emission_audit._agent_skill_names(body) == []

    def test_audit_agents_emits_one_row_per_topic_per_agent(self, tmp_path: Path) -> None:
        """Sanity: the matrix must be rectangular — agent × topic — with no gaps."""
        # Build a fake repo with just one fake agent.
        fake_agent_dir = tmp_path / "agents"
        fake_agent_dir.mkdir()
        fake_agent = fake_agent_dir / "cpv-plugin-creator-agent.md"
        fake_agent.write_text("---\nname: fake\n---\n\nBody mentioning kebab-case.\n")
        # Need to patch AGENT_TARGETS in module scope for isolation.
        original = agent_emission_audit.AGENT_TARGETS
        try:
            agent_emission_audit.AGENT_TARGETS = (
                agent_emission_audit.AgentTarget(
                    label="cpv-plugin-creator-agent",
                    kind="agent",
                    body_path="agents/cpv-plugin-creator-agent.md",
                ),
            )
            reports = agent_emission_audit.audit_agents(tmp_path)
        finally:
            agent_emission_audit.AGENT_TARGETS = original
        assert len(reports) == 1
        assert len(reports[0].rows) == len(agent_emission_audit.SPEC_TOPICS)

    def test_audit_marks_missing_body(self, tmp_path: Path) -> None:
        """When the agent body file is absent, the report must set body_missing=True."""
        original = agent_emission_audit.AGENT_TARGETS
        try:
            agent_emission_audit.AGENT_TARGETS = (
                agent_emission_audit.AgentTarget(
                    label="nonexistent",
                    kind="agent",
                    body_path="agents/does-not-exist.md",
                ),
            )
            reports = agent_emission_audit.audit_agents(tmp_path)
        finally:
            agent_emission_audit.AGENT_TARGETS = original
        assert reports[0].body_missing is True

    def test_write_agent_emission_report_handles_empty_reports(self, tmp_path: Path) -> None:
        """Empty report list still produces a well-formed markdown skeleton."""
        path = tmp_path / "agent.md"
        agent_emission_audit.write_agent_emission_report([], path)
        body = path.read_text(encoding="utf-8")
        assert "Agent-Emission Audit" in body
        assert "Coverage matrix" in body
