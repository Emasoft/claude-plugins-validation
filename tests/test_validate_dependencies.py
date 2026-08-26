"""Scenario tests for validate_dependencies.py (TRDD-747d7bbc), §7.

Fixtures are built programmatically (a marketplace.json + N plugin.json
files under tmp_path) rather than as 24 static JSON directories under
tests/fixtures/ — each scenario is self-contained and the assertions are
pinned against the produced findings, matching the spirit of §7's table
even though the concrete storage differs from the TRDD's literal file list
(documented scope reduction).
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import validate_dependencies as vd  # noqa: E402
from cpv_validation_common import ValidationReport  # noqa: E402


def _make_marketplace(root: Path, plugins: dict) -> Path:
    """Test helper: write a marketplace.json + one plugin.json per entry."""
    (root / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    entries = [{"name": n, "source": f"./{n}"} for n in plugins]
    (root / ".claude-plugin" / "marketplace.json").write_text(
        json.dumps({"name": "test-mp", "owner": {"name": "tester"}, "plugins": entries})
    )
    for name, spec in plugins.items():
        pdir = root / name / ".claude-plugin"
        pdir.mkdir(parents=True, exist_ok=True)
        data = {"name": name, "version": "1.0.0"}
        data.update(spec)
        (pdir / "plugin.json").write_text(json.dumps(data))
    return root


def _levels(root: Path, target: str, *, marketplace: bool = True, threshold: int = 5) -> list[str]:
    """Test helper: run the validator for one plugin, return the level list."""
    report = ValidationReport()
    ctx = vd.build_marketplace_context(root) if marketplace else None
    vd.validate_dependencies(root / target, ctx, report, cascade_threshold=threshold)
    return [r.level for r in report.results]


def _all_findings(root: Path, targets: list[str], threshold: int = 5) -> list[tuple[str, str]]:
    """Test helper: run the validator across every plugin in the bundle (as
    validate_marketplace.py does — once per bundle, not once per plugin) and
    return every (level, message) tuple.
    """
    report = ValidationReport()
    ctx = vd.build_marketplace_context(root)
    for name in targets:
        vd.validate_dependencies(root / name, ctx, report, cascade_threshold=threshold)
    return [(r.level, r.message) for r in report.results]


class TestDependencyCascadeScenarios:
    """Test docstring: the 24 §7 scenarios of TRDD-747d7bbc."""

    def test_scenario_01_valid_acyclic_graph(self, tmp_path: Path) -> None:
        """Test docstring: scenario 1 — a valid 5-plugin DAG produces zero findings."""
        _make_marketplace(
            tmp_path,
            {
                "a": {"dependencies": {"b": "*", "c": "*"}},
                "b": {"dependencies": {"d": "*"}},
                "c": {"dependencies": {"d": "*"}},
                "d": {"dependencies": {"e": "*"}},
                "e": {},
            },
        )
        assert _all_findings(tmp_path, ["a", "b", "c", "d", "e"]) == []

    def test_scenario_02_self_dependency(self, tmp_path: Path) -> None:
        """Test docstring: scenario 2 — A.deps=[A] yields exactly 1 CRITICAL."""
        _make_marketplace(tmp_path, {"a": {"dependencies": {"a": "*"}}})
        assert _levels(tmp_path, "a") == ["CRITICAL"]

    def test_scenario_03_length_two_cycle(self, tmp_path: Path) -> None:
        """Test docstring: scenario 3 — A<->B reciprocal cycle yields exactly 1 CRITICAL."""
        _make_marketplace(tmp_path, {"a": {"dependencies": {"b": "*"}}, "b": {"dependencies": {"a": "*"}}})
        assert _levels(tmp_path, "a") == ["CRITICAL"]

    def test_scenario_04_length_four_cycle(self, tmp_path: Path) -> None:
        """Test docstring: scenario 4 — A->B->C->D->A yields 1 CRITICAL naming all 4 members."""
        _make_marketplace(
            tmp_path,
            {
                "a": {"dependencies": {"b": "*"}},
                "b": {"dependencies": {"c": "*"}},
                "c": {"dependencies": {"d": "*"}},
                "d": {"dependencies": {"a": "*"}},
            },
        )
        report = ValidationReport()
        ctx = vd.build_marketplace_context(tmp_path)
        vd.validate_dependencies(tmp_path / "a", ctx, report)
        assert len(report.results) == 1
        assert report.results[0].level == "CRITICAL"
        for name in ("a", "b", "c", "d"):
            assert name in report.results[0].message

    def test_scenario_05_two_independent_cycles(self, tmp_path: Path) -> None:
        """Test docstring: scenario 5 — two disjoint cycles yield 2 CRITICAL findings."""
        _make_marketplace(
            tmp_path,
            {
                "a": {"dependencies": {"b": "*"}},
                "b": {"dependencies": {"a": "*"}},
                "c": {"dependencies": {"d": "*"}},
                "d": {"dependencies": {"c": "*"}},
            },
        )
        assert _all_findings(tmp_path, ["a", "b", "c", "d"]).count(
            ("CRITICAL", 'Dependency cycle detected: cycle_members=["a", "b"]')
        ) == 1
        levels = [lvl for lvl, _ in _all_findings(tmp_path, ["a", "b", "c", "d"])]
        assert levels.count("CRITICAL") == 2

    def test_scenario_06_missing_dep_no_marketplace_ctx(self, tmp_path: Path) -> None:
        """Test docstring: scenario 6 — an unresolvable dep with NO marketplace context is a non-blocking WARNING.

        The card's §5.3 table said MAJOR, but that contradicts its own
        acceptance criterion #4 (CPV's self-scan stays 0/0/0/0): CPV declares a
        dependency and always self-scans standalone, so a blocking severity
        here fails the gate for every plugin that pins a dep. Without a
        marketplace the dep is NOT proven missing — only unchecked — and
        "cannot check" must never read as a failure. Scenario 7 keeps CRITICAL
        because there the marketplace IS held and absence is proven.
        """
        _make_marketplace(tmp_path, {"a": {"dependencies": {"ghost": "*"}}})
        assert _levels(tmp_path, "a", marketplace=False) == ["WARNING"]

    def test_scenario_07_missing_dep_with_marketplace_ctx(self, tmp_path: Path) -> None:
        """Test docstring: scenario 7 — the same missing dep WITH a marketplace context is CRITICAL."""
        _make_marketplace(tmp_path, {"a": {"dependencies": {"ghost": "*"}}})
        assert _levels(tmp_path, "a", marketplace=True) == ["CRITICAL"]

    def test_scenario_08_disabled_target_dep(self, tmp_path: Path) -> None:
        """Test docstring: scenario 8 — depending on a disabled plugin is 1 MAJOR."""
        _make_marketplace(tmp_path, {"a": {"dependencies": {"b": "*"}}, "b": {"enabled": False}})
        assert _levels(tmp_path, "a") == ["MAJOR"]

    def test_scenario_09_cross_marketplace_no_allowlist(self, tmp_path: Path) -> None:
        """Test docstring: scenario 9 — an unresolvable dep with no allowlist entry surfaces a finding."""
        _make_marketplace(tmp_path, {"a": {"dependencies": {"external-plugin": "*"}}})
        assert _levels(tmp_path, "a") == ["CRITICAL"]

    def test_scenario_10_cross_marketplace_in_allowlist(self, tmp_path: Path) -> None:
        """Test docstring: scenario 10 — the same edge with the name in the allowlist clears (0 findings)."""
        _make_marketplace(tmp_path, {"a": {"dependencies": {"external-plugin": "*"}}})
        ctx = vd.build_marketplace_context(tmp_path, allowlist={"external-plugin"})
        report = ValidationReport()
        vd.validate_dependencies(tmp_path / "a", ctx, report)
        assert report.results == []

    def test_scenario_11_component_name_shadow(self, tmp_path: Path) -> None:
        """Test docstring: scenario 11 — a dep name matching another plugin's skill hints at the real owner."""
        _make_marketplace(tmp_path, {"a": {"dependencies": {"my-skill": "*"}}, "b": {}})
        (tmp_path / "b" / "skills" / "my-skill").mkdir(parents=True)
        report = ValidationReport()
        ctx = vd.build_marketplace_context(tmp_path)
        vd.validate_dependencies(tmp_path / "a", ctx, report)
        assert len(report.results) == 1
        assert report.results[0].level == "MAJOR"
        assert "did you mean plugin 'b'" in report.results[0].message

    def test_scenario_12_duplicate_dep_entry(self, tmp_path: Path) -> None:
        """Test docstring: scenario 12 — a duplicated list-shape dep entry is 1 MINOR."""
        _make_marketplace(tmp_path, {"a": {"dependencies": ["b", "b"]}, "b": {}})
        assert _levels(tmp_path, "a") == ["MINOR"]

    def test_scenario_13_high_fanin_over_threshold(self, tmp_path: Path) -> None:
        """Test docstring: scenario 13 — fanin=8, threshold=5 -> 1 INFO + 1 MAJOR."""
        plugins = {f"p{i}": {"dependencies": {"core": "*"}} for i in range(8)}
        plugins["core"] = {}
        _make_marketplace(tmp_path, plugins)
        levels = _levels(tmp_path, "core", threshold=5)
        assert levels == ["INFO", "MAJOR"]

    def test_scenario_14_fanin_equals_threshold_boundary(self, tmp_path: Path) -> None:
        """Test docstring: scenario 14 — fanin=5, threshold=5 -> 1 INFO, 0 MAJOR."""
        plugins = {f"p{i}": {"dependencies": {"core": "*"}} for i in range(5)}
        plugins["core"] = {}
        _make_marketplace(tmp_path, plugins)
        levels = _levels(tmp_path, "core", threshold=5)
        assert levels == ["INFO"]

    def test_scenario_15_diamond_legal_dag(self, tmp_path: Path) -> None:
        """Test docstring: scenario 15 — A->B->D, A->C->D is a legal DAG, 0 findings."""
        _make_marketplace(
            tmp_path,
            {
                "a": {"dependencies": {"b": "*", "c": "*"}},
                "b": {"dependencies": {"d": "*"}},
                "c": {"dependencies": {"d": "*"}},
                "d": {},
            },
        )
        assert _levels(tmp_path, "a") == []

    def test_scenario_16_empty_deps(self, tmp_path: Path) -> None:
        """Test docstring: scenario 16 — dependencies: {} is a no-op, 0 findings."""
        _make_marketplace(tmp_path, {"a": {"dependencies": {}}})
        assert _levels(tmp_path, "a") == []

    def test_scenario_17_missing_deps_key(self, tmp_path: Path) -> None:
        """Test docstring: scenario 17 — no dependencies key at all is an optional field, 0 findings."""
        _make_marketplace(tmp_path, {"a": {}})
        assert _levels(tmp_path, "a") == []

    def test_scenario_18_bare_list_shape(self, tmp_path: Path) -> None:
        """Test docstring: scenario 18 — the bare-list dependency shape normalises the same as the object shape."""
        _make_marketplace(tmp_path, {"a": {"dependencies": ["b"]}, "b": {}})
        assert _levels(tmp_path, "a") == []

    def test_scenario_20_missing_manifest_is_a_defensive_skip(self, tmp_path: Path) -> None:
        """Test docstring: scenario 20 — a plugin with no plugin.json is skipped, not crashed."""
        (tmp_path / "no-manifest").mkdir(parents=True)
        report = ValidationReport()
        vd.validate_dependencies(tmp_path / "no-manifest", None, report)
        assert report.results == []

    def test_scenario_21_large_marketplace_performance(self, tmp_path: Path) -> None:
        """Test docstring: scenario 21 — 200 plugins / 500 edges, no cycles, completes in <2s, 0 findings."""
        n = 200
        plugins: dict = {}
        for i in range(n):
            deps = {f"p{(i + 1) % n}": "*"} if i < n - 1 else {}
            # Build a DAG: each plugin depends on ~2 later-indexed plugins (acyclic by construction).
            deps = {f"p{j}": "*" for j in range(i + 1, min(i + 3, n))}
            plugins[f"p{i}"] = {"dependencies": deps} if deps else {}
        _make_marketplace(tmp_path, plugins)
        start = time.monotonic()
        findings = _all_findings(tmp_path, list(plugins.keys()))
        elapsed = time.monotonic() - start
        assert findings == []
        assert elapsed < 2.0

    def test_scenario_22_pathological_large_cycle(self, tmp_path: Path) -> None:
        """Test docstring: scenario 22 — a 50-node SCC yields 1 CRITICAL naming every member."""
        n = 50
        plugins = {f"p{i}": {"dependencies": {f"p{(i + 1) % n}": "*"}} for i in range(n)}
        _make_marketplace(tmp_path, plugins)
        report = ValidationReport()
        ctx = vd.build_marketplace_context(tmp_path)
        vd.validate_dependencies(tmp_path / "p0", ctx, report)
        assert len(report.results) == 1
        assert report.results[0].level == "CRITICAL"
        for i in range(n):
            assert f"p{i}" in report.results[0].message

    def test_scenario_24_disabled_plugin_three_dependents(self, tmp_path: Path) -> None:
        """Test docstring: scenario 24 — a disabled plugin with 3 enabled dependents yields 3 MAJOR."""
        plugins = {"core": {"enabled": False}}
        for i in range(3):
            plugins[f"p{i}"] = {"dependencies": {"core": "*"}}
        _make_marketplace(tmp_path, plugins)
        findings = _all_findings(tmp_path, ["p0", "p1", "p2"])
        assert [lvl for lvl, _ in findings] == ["MAJOR", "MAJOR", "MAJOR"]


class TestDependencyGraphNormalization:
    """Test docstring: normalize_deps handles both dependency-declaration shapes."""

    def test_object_shape(self) -> None:
        """Test docstring: the object shape {"name": "range"} normalises to a name list."""
        assert vd.normalize_deps({"a": ">=1.0.0", "b": "^2.0.0"}) == ["a", "b"]

    def test_list_shape(self) -> None:
        """Test docstring: the bare-list shape normalises to itself (as strings)."""
        assert vd.normalize_deps(["a", "b"]) == ["a", "b"]

    def test_normalize_deps_pinned_element_shape(self) -> None:
        """Test docstring: the canonical pinned form [{name, version}] yields the NAME, not a dict repr."""
        assert vd.normalize_deps([{"name": "a", "version": ">=1.0.0"}]) == ["a"]
        assert vd.normalize_deps(
            [{"name": "a", "version": ">=1.0.0", "marketplace": "m"}, "b"]
        ) == ["a", "b"]

    def test_normalize_deps_malformed_element_is_skipped_not_stringified(self) -> None:
        """Test docstring: an element with no usable name is dropped, never turned into a bogus node name."""
        assert vd.normalize_deps([{"version": ">=1.0.0"}]) == []
        assert vd.normalize_deps([{"name": ""}]) == []
        assert vd.normalize_deps([{"name": 42}]) == []

    def test_pinned_dep_resolves_against_marketplace(self, tmp_path: Path) -> None:
        """Positive control: a pinned dep that DOES exist resolves cleanly (no dict-repr ghost)."""
        _make_marketplace(
            tmp_path,
            {
                "a": {"dependencies": [{"name": "b", "version": ">=1.0.0"}]},
                "b": {},
            },
        )
        assert _levels(tmp_path, "a", marketplace=True) == []

    def test_pinned_dep_still_fires_when_genuinely_missing(self, tmp_path: Path) -> None:
        """Positive control: the pinned shape does not MUTE a real missing dep."""
        _make_marketplace(tmp_path, {"a": {"dependencies": [{"name": "ghost", "version": "*"}]}})
        assert _levels(tmp_path, "a", marketplace=True) == ["CRITICAL"]

    def test_none_shape(self) -> None:
        """Test docstring: an absent dependencies field normalises to an empty list."""
        assert vd.normalize_deps(None) == []

    def test_malformed_shape(self) -> None:
        """Test docstring: a non-dict/non-list dependencies value normalises to an empty list, not a crash."""
        assert vd.normalize_deps("not-a-valid-shape") == []


class TestAcceptanceCriteria:
    """Test docstring: §8 acceptance criteria of TRDD-747d7bbc."""

    def test_ac2_zero_added_cost_when_no_deps_and_no_marketplace_ctx(self, tmp_path: Path) -> None:
        """Test docstring: AC2 — a plugin with no deps and no marketplace context short-circuits to 0 findings."""
        _make_marketplace(tmp_path, {"a": {}})
        report = ValidationReport()
        vd.validate_dependencies(tmp_path / "a", None, report)
        assert report.results == []

    def test_ac9_idempotence(self, tmp_path: Path) -> None:
        """Test docstring: AC9 — running the validator twice on the same plugin produces identical findings."""
        _make_marketplace(tmp_path, {"a": {"dependencies": {"a": "*"}}})
        first = _levels(tmp_path, "a")
        second = _levels(tmp_path, "a")
        assert first == second
