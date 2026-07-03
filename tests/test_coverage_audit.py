"""Tests for the universal test-coverage audit advisory (issue #155).

``check_test_coverage`` is a NON-BLOCKING WARNING surface: it enumerates the
testable components a plugin ships (``scripts/*.py``, ``hooks/`` scripts,
``skills/*/SKILL.md``, ``commands/*.md``, ``agents/*.md``) by GENERIC Claude-Code
conventions, discovers tests generically (``test_*.py`` / ``*_test.py`` /
``*.test.{js,ts}`` / ``*.spec.{js,ts}`` anywhere under the plugin), and — ONLY
when the plugin already ships a test suite — emits ONE advisory WARNING listing
components with no discoverable test.

These are FN-safe TWO-SIDED tests: the FIRES side proves the advisory catches an
under-tested plugin (the issue-#155 "many scripts, one test" shape); the CLEARS
side proves it never fires on a fully-covered plugin, a test-less plugin (the
conservative gate), a component-less plugin, or a component covered only by a
test's content. Plus a load-bearing regression lock proving the WARNING never
changes the ``--strict`` exit code.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = REPO_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from cpv_validation_common import ValidationReport  # noqa: E402
from validate_plugin import check_test_coverage  # noqa: E402

# ── Helpers ─────────────────────────────────────────────────────────────────


def _warnings_for(files: dict[str, str]) -> list[str]:
    """Write ``{relative-path: content}`` into a temp plugin tree and return the
    RC-TEST-COVERAGE WARNING messages ``check_test_coverage`` emits."""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        for rel, content in files.items():
            p = root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
        report = ValidationReport()
        check_test_coverage(root, report)
        return [
            r.message
            for r in report.results
            if r.level == "WARNING" and "RC-TEST-COVERAGE" in r.message
        ]


# ── FIRES — an under-tested plugin (has a suite, but thin) ───────────────────


def test_fires_on_script_with_no_test_file() -> None:
    """A plugin that ships a test suite covering one script but not another →
    exactly one WARNING naming only the uncovered script."""
    warnings = _warnings_for(
        {
            "scripts/foo.py": "x = 1\n",
            "scripts/bar_uncovered.py": "y = 2\n",
            "tests/test_foo.py": "import foo\n",
        }
    )
    assert len(warnings) == 1, warnings
    assert "scripts/bar_uncovered.py" in warnings[0]
    # foo is covered by test_foo.py → not listed.
    assert "scripts/foo.py" not in warnings[0]


def test_fires_warning_names_the_uncovered_component() -> None:
    """The WARNING carries the RC tag, the advisory phrasing, and the component."""
    warnings = _warnings_for(
        {
            "scripts/widget_uncovered.py": "x = 1\n",
            "tests/test_placeholder.py": "assert True\n",
        }
    )
    assert len(warnings) == 1
    msg = warnings[0]
    assert "[RC-TEST-COVERAGE]" in msg
    assert "scripts/widget_uncovered.py" in msg
    assert "does not block the publish" in msg


def test_fires_across_all_component_types() -> None:
    """command / agent / skill / hook components are each enumerated — an
    uncovered one of every type is listed in the single WARNING."""
    warnings = _warnings_for(
        {
            "scripts/covered.py": "x = 1\n",
            "tests/test_covered.py": "import covered\n",
            "commands/mycmd.md": "# cmd\n",
            "agents/myagent.md": "# agent\n",
            "skills/myskill/SKILL.md": "# skill\n",
            "hooks/myhook.py": "print('hi')\n",
        }
    )
    assert len(warnings) == 1
    msg = warnings[0]
    assert "commands/mycmd.md" in msg
    assert "agents/myagent.md" in msg
    assert "skills/myskill/SKILL.md" in msg
    assert "hooks/myhook.py" in msg
    # covered.py is covered → not listed.
    assert "scripts/covered.py" not in msg


def test_fires_when_suite_is_a_js_or_ts_spec() -> None:
    """Test discovery is generic — a ``*.test.ts`` file satisfies the "has a
    suite" gate, and an uncovered Python script still fires."""
    warnings = _warnings_for(
        {
            "scripts/tool.py": "x = 1\n",
            "src/widget.test.ts": "test('x', () => {})\n",
        }
    )
    assert len(warnings) == 1
    assert "scripts/tool.py" in warnings[0]


# ── CLEARS — fully covered / no suite / no components / content-covered ───────


def test_clears_when_every_component_has_a_matching_test() -> None:
    """Full coverage (every component name-matched by a test) → ZERO findings."""
    warnings = _warnings_for(
        {
            "scripts/alpha.py": "x = 1\n",
            "scripts/beta.py": "y = 2\n",
            "tests/test_alpha.py": "import alpha\n",
            "tests/test_beta.py": "import beta\n",
        }
    )
    assert warnings == [], warnings


def test_clears_when_no_test_suite_present() -> None:
    """The conservative gate: components but NO test files → stay silent (do not
    nag a plugin that never opted into testing)."""
    warnings = _warnings_for(
        {
            "scripts/alpha.py": "x = 1\n",
            "scripts/beta.py": "y = 2\n",
        }
    )
    assert warnings == [], warnings


def test_clears_when_no_testable_components() -> None:
    """A plugin with a test file but no testable component dirs → no findings."""
    warnings = _warnings_for({"README.md": "# hi\n", "tests/test_x.py": "assert True\n"})
    assert warnings == [], warnings


def test_clears_when_component_covered_by_test_content() -> None:
    """A test whose FILENAME doesn't match but whose CONTENT mentions the
    component → covered (generous matching under-warns, the safe direction)."""
    warnings = _warnings_for(
        {
            "scripts/gamma.py": "z = 1\n",
            "tests/test_suite.py": "from scripts import gamma\n",
        }
    )
    assert warnings == [], warnings


def test_ignores_test_files_in_vendored_dirs() -> None:
    """A ``test_*.py`` inside ``node_modules`` is NOT the plugin's suite — it
    neither satisfies the gate nor covers a same-named component."""
    warnings = _warnings_for(
        {
            "scripts/thing.py": "x = 1\n",
            "node_modules/pkg/test_thing.py": "import thing\n",
        }
    )
    # Vendored test skipped → no plugin suite → gate → silent.
    assert warnings == [], warnings


# ── WARNING is NON-BLOCKING (the load-bearing regression lock) ───────────────


def test_warning_does_not_block_strict_exit_code() -> None:
    """A plugin whose ONLY finding is this WARNING still exits 0 under --strict
    (WARNING never changes the verdict / blocks --strict — advisory only)."""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        (root / "scripts").mkdir()
        (root / "scripts" / "uncov.py").write_text("x = 1\n", encoding="utf-8")
        (root / "tests").mkdir()
        (root / "tests" / "test_other.py").write_text("y = 2\n", encoding="utf-8")
        report = ValidationReport()
        check_test_coverage(root, report)
        assert report.has_warning is True
        assert report.exit_code == 0
        assert report.exit_code_strict() == 0


def test_finding_is_warning_severity_only() -> None:
    """The finding is WARNING severity — never CRITICAL/MAJOR/MINOR/NIT."""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        (root / "scripts").mkdir()
        (root / "scripts" / "uncov.py").write_text("x = 1\n", encoding="utf-8")
        (root / "tests").mkdir()
        (root / "tests" / "test_other.py").write_text("y = 2\n", encoding="utf-8")
        report = ValidationReport()
        check_test_coverage(root, report)
        rc = [r for r in report.results if "RC-TEST-COVERAGE" in r.message]
        assert rc, "expected the finding to fire"
        assert all(r.level == "WARNING" for r in rc)
        assert report.has_critical is False
        assert report.has_major is False
        assert report.has_minor is False
        assert report.has_nit is False


# ── Robustness — never crash on unreadable input ─────────────────────────────


def test_no_crash_on_binary_garbage_test_file() -> None:
    """A test file with binary garbage → read swallowed, no crash, no finding
    for the (thereby content-uncheckable) component beyond the filename match."""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        (root / "scripts").mkdir()
        (root / "scripts" / "a.py").write_text("x = 1\n", encoding="utf-8")
        (root / "tests").mkdir()
        (root / "tests" / "test_bin.py").write_bytes(b"\xff\xfe\x00\x01garbage\x00")
        report = ValidationReport()
        # Must not raise.
        check_test_coverage(root, report)
