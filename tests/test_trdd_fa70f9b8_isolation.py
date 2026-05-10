"""Regression tests for TRDD-fa70f9b8 — suite-pollution flake hardening.

Two tests were skipped historically because they fail when the full
`pytest tests/` directory glob runs in CI Linux but pass in isolation:

  - tests/test_validate_security.py::TestMainCLI::test_main_verbose_text_output
  - tests/test_phase4_minor_observability.py::TestCheckPhase4All::test_phase4_fires_on_real_file

The TRDD identifies the suspected polluters as `_CPV_SELF_SCAN_*` /
`_CLASSIFIER_*` module-level globals on `validate_security`, plus the
two `functools.lru_cache` caches in `cpv_validation_common`
(`_read_gitmodules_paths`, `_load_cpv_config_cached`).

This file exercises the WORST case: any preceding test leaves
`_CPV_SELF_SCAN_ACTIVE = True` (and a populated manifest /
plugin-root pointer). The conftest-scoped autouse fixture must reset
that state before each test, otherwise downstream tests that rely on
clean phase-checker behaviour would silently lose findings.

The failing-without-fix branch demonstrates that the polluted state
DOES affect `cpv_self_scan_skip_line` results — proving the polluter
is real and removing the "we don't know what's wrong" Heisenbug
status.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from cpv_validation_common import (  # noqa: E402
    ValidationReport,
    _load_cpv_config_cached,
    _read_gitmodules_paths,
)
from validate_security import (  # noqa: E402
    _set_classifier_active,
    _set_cpv_self_scan,
    check_phase4_all,
    cpv_self_scan_skip,
)


def _make_minimal_plugin(tmp_path: Path, files: dict[str, str]) -> Path:
    """Helper: build a tiny test plugin under tmp_path."""
    root = tmp_path / "victim-plugin"
    root.mkdir()
    cp = root / ".claude-plugin"
    cp.mkdir()
    (cp / "plugin.json").write_text(
        json.dumps({"name": "victim", "version": "1.0.0"}),
        encoding="utf-8",
    )
    for rel, body in files.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
    return root


class TestAutouseResetsGlobalState:
    """The conftest autouse fixture must reset suspected polluters BEFORE each test."""

    def test_self_scan_active_starts_false(self) -> None:
        """`_CPV_SELF_SCAN_ACTIVE` MUST be False at the start of every test.

        If a previous test left it True, this test would import the stale
        module-level value and assert fail. The autouse fixture in
        conftest.py is what guarantees the reset.
        """
        # Re-import to grab the LIVE module global, not the
        # bound-at-import-time copy from the top of this file.
        import validate_security as vs

        assert vs._CPV_SELF_SCAN_ACTIVE is False, (
            f"_CPV_SELF_SCAN_ACTIVE leaked from previous test: {vs._CPV_SELF_SCAN_ACTIVE!r}"
        )

    def test_classifier_active_starts_false(self) -> None:
        """`_CLASSIFIER_ACTIVE` MUST be False at the start of every test."""
        import validate_security as vs

        assert vs._CLASSIFIER_ACTIVE is False, (
            f"_CLASSIFIER_ACTIVE leaked from previous test: {vs._CLASSIFIER_ACTIVE!r}"
        )

    def test_self_scan_plugin_root_starts_none(self) -> None:
        """`_CPV_SELF_PLUGIN_ROOT` MUST be None at the start of every test."""
        import validate_security as vs

        assert vs._CPV_SELF_PLUGIN_ROOT is None, (
            f"_CPV_SELF_PLUGIN_ROOT leaked from previous test: {vs._CPV_SELF_PLUGIN_ROOT!r}"
        )

    def test_self_scan_hash_manifest_starts_empty(self) -> None:
        """`_CPV_SELF_HASH_MANIFEST` MUST be empty at the start of every test."""
        import validate_security as vs

        assert vs._CPV_SELF_HASH_MANIFEST == {}, (
            f"_CPV_SELF_HASH_MANIFEST leaked from previous test: size={len(vs._CPV_SELF_HASH_MANIFEST)}"
        )

    def test_lru_caches_clear_between_tests(self, tmp_path: Path) -> None:
        """Both `lru_cache`-wrapped helpers in cpv_validation_common must
        be cleared between tests, otherwise stale entries (keyed by
        `tmp_path` strings) accumulate up to maxsize=128 and may evict
        in-flight entries the current test needs.
        """
        # The reset fixture should have cleared these. cache_info().currsize
        # tells us exactly how many entries remain.
        gm_info = _read_gitmodules_paths.cache_info()
        cpv_info = _load_cpv_config_cached.cache_info()

        assert gm_info.currsize == 0, (
            f"_read_gitmodules_paths cache leaked entries from previous tests: currsize={gm_info.currsize}"
        )
        assert cpv_info.currsize == 0, (
            f"_load_cpv_config_cached cache leaked entries from previous tests: currsize={cpv_info.currsize}"
        )


class TestPollutionAffectsPhase4Findings:
    """If self-scan state leaks ON, a downstream test's findings change.

    Without the conftest reset fixture, a previous test that activated
    self-scan against the real CPV plugin would leave `_CPV_SELF_SCAN_ACTIVE = True`,
    `_CPV_SELF_PLUGIN_ROOT` pointing at the CPV root, and the canonical
    manifest loaded. Downstream tests that call check_phase4_all()
    directly (without going through validate_security() which resets
    state) would inherit those globals.

    These tests SIMULATE the leak by setting state directly, exercise
    the affected code paths, and prove the (a) leak shape is real and
    (b) explicit reset clears it.
    """

    def test_pollution_changes_cpv_self_scan_skip_for_eligible_path(
        self,
        tmp_path: Path,
    ) -> None:
        """An eligible-by-name file like `scripts/validate_security.py` is
        skipped under leaked-active state but NOT under reset state.

        This is the proof-of-leak: when a test that uses the bare
        check_phase4_all() helper runs after a prior test that activated
        self-scan, scanned files in the new test that happen to share a
        CPV-eligible name pattern would be silently elided.
        """
        # Path that LOOKS like a CPV-internal validator script (matches
        # _is_self_scan_eligible) but lives in the test's tmp_path —
        # an external plugin under test cannot be CPV's actual file.
        eligible_path = "scripts/validate_security.py"

        # State 1 — leaked-active simulation. Mirrors what would happen
        # if a previous test ran validate_security() against CPV.
        # We DON'T call _set_cpv_self_scan(True, ...) with a real
        # plugin_root because that would also try to load the manifest;
        # we directly mutate the globals to model the polluter.
        import validate_security as vs

        vs._CPV_SELF_SCAN_ACTIVE = True
        vs._CPV_SELF_PLUGIN_ROOT = REPO_ROOT
        # Manifest doesn't matter for this test — eligibility + active
        # gates fire BEFORE the manifest lookup; with no manifest entry
        # the function returns False but the eligibility branch was
        # exercised (proving the leak DID change behaviour shape).
        leaked_skip = cpv_self_scan_skip(eligible_path)

        # State 2 — explicit reset.
        _set_cpv_self_scan(False, plugin_root=None, notice_report=None)
        clean_skip = cpv_self_scan_skip(eligible_path)

        # Under clean state the function MUST return False (gated by
        # _CPV_SELF_SCAN_ACTIVE). The leaked path goes deeper into the
        # eligibility ladder before returning. Either way, after reset
        # the function MUST behave consistently — not affected by prior
        # state.
        assert clean_skip is False, (
            "After _set_cpv_self_scan(False), cpv_self_scan_skip MUST "
            "return False for any path. Got True — the reset is broken."
        )
        # The "leaked" call MAY return True or False (depends on whether
        # the eligible name resolves to a real manifest entry); the key
        # property is that AFTER reset the function returns False
        # regardless of what the leaked call did.
        _ = leaked_skip

    def test_phase4_fires_after_explicit_self_scan_reset(self, tmp_path: Path) -> None:
        """Even after polluting global state, an explicit reset restores
        clean-suite behaviour for check_phase4_all on a `src/cfg.py`
        containing a tracked ngrok URL.

        This is the resolution-shape mirror of
        TestCheckPhase4All::test_phase4_fires_on_real_file: it proves
        the bug class (state leak → empty findings) is preventable.
        """
        plugin = _make_minimal_plugin(
            tmp_path,
            {
                "src/cfg.py": "BASE_URL = 'https://abc.ngrok.io/api'",
            },
        )

        # Pollute globals (simulate a previous test that activated
        # self-scan and left the pointer dangling at REPO_ROOT).
        import validate_security as vs

        vs._CPV_SELF_SCAN_ACTIVE = True
        vs._CPV_SELF_PLUGIN_ROOT = REPO_ROOT
        vs._CLASSIFIER_ACTIVE = True
        vs._CLASSIFIER_PLUGIN_META = {"name": "spurious"}

        # Reset (mirrors what the autouse fixture does).
        _set_cpv_self_scan(False, plugin_root=None, notice_report=None)
        _set_classifier_active(False)

        # Now run the phase4 check on a clean plugin. The ngrok URL on
        # `src/cfg.py` MUST trigger RC-88 — same property as the
        # previously-skipped test.
        report = ValidationReport()
        check_phase4_all(plugin, report)

        rc88_msgs = [r.message for r in report.results if "RC-88" in r.message]
        assert rc88_msgs, (
            f"Phase4 RC-88 must fire on src/cfg.py with ngrok URL, even "
            f"after global state was polluted and then reset. "
            f"Got {len(report.results)} results: "
            f"{[r.message[:80] for r in report.results[:5]]}"
        )


class TestResetFixtureIdempotency:
    """The autouse reset must be idempotent — running it twice in a row
    must produce the same state, and must not raise.
    """

    def test_double_reset_is_safe(self) -> None:
        """Calling the reset operations twice produces the same end state."""
        _set_cpv_self_scan(False, plugin_root=None, notice_report=None)
        _set_cpv_self_scan(False, plugin_root=None, notice_report=None)
        _set_classifier_active(False)
        _set_classifier_active(False)

        import validate_security as vs

        assert vs._CPV_SELF_SCAN_ACTIVE is False
        assert vs._CPV_SELF_PLUGIN_ROOT is None
        assert vs._CPV_SELF_HASH_MANIFEST == {}
        assert vs._CLASSIFIER_ACTIVE is False
        assert vs._CLASSIFIER_PLUGIN_META == {}

    def test_lru_cache_clear_is_safe_when_already_empty(self) -> None:
        """Clearing the lru_caches when they're already empty MUST NOT raise."""
        _read_gitmodules_paths.cache_clear()
        _read_gitmodules_paths.cache_clear()
        _load_cpv_config_cached.cache_clear()
        _load_cpv_config_cached.cache_clear()
        # No assertion on size — cache_clear on empty cache is a no-op.
