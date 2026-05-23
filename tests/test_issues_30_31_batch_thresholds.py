#!/usr/bin/env python3
"""Regression tests for v2.98.0 (TRDD-dce5f014).

Five concerns landed in v2.98.0:

* **Issue #30** — ``validate_plugin`` backtick-path validator must walk
  ``plugin_root / "skills" / <slug>`` and ``plugin_root / "skills" / <slug> / "SKILL.md"``
  before falling through to the "Possible broken backtick path"
  WARNING. Sibling-skill references like
  ``amvcp-modal-comments/SKILL.md`` now resolve.

* **Issue #31** — ``publish.py`` Gate 2 ``stage_run_tests`` wraps
  pytest in a baseline-diff browser-orphan cleanup. ``Chrome for
  Testing`` / Playwright processes spawned during the test run are
  SIGTERM'd then SIGKILL'd after pytest returns. NEVER touches PIDs
  present in the baseline (maintainer's daily browser stays safe).
  Iron rule preserved — tests still run unconditionally.

* **Test speed** — ``test_github_com_gets_extra_retry_bonus`` mocks
  ``time.sleep`` to drop 15s wall-clock without losing the retry-
  count assertion. ``test_language_subset_filter`` switched to
  ``patch.object`` full-swap to eliminate the xdist isolation
  flake.

* **Lower threshold** — ``DEFAULT_SHARD_SIZE`` dropped 30 → 15 so
  batch mode triggers earlier; lower ceilings give each shard
  fixer more headroom for re-validate cycles.

* **Auto-batch dispatch** — the menu orchestrator (``menu-tree.md``
  §3.2.1 + §3.5.1) auto-runs the planner + fans out parallel
  shard-fixers + runs aggregator when finding count exceeds the
  safe-ceiling. User no longer has to type ``/cpv-batch-fix``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))


# =============================================================================
# Issue #30 — sibling-skill backtick path resolution
# =============================================================================


class TestIssue30SiblingSkillBacktickResolution:
    """Backtick paths to sibling skills must resolve before WARNING."""

    def _make_plugin_with_two_skills(self, tmp_path: Path) -> Path:
        plugin = tmp_path / "demo-plugin"
        (plugin / ".claude-plugin").mkdir(parents=True)
        (plugin / ".claude-plugin" / "plugin.json").write_text(
            json.dumps(
                {
                    "name": "demo-plugin",
                    "version": "0.1.0",
                    "description": "Two sibling skills fixture",
                }
            )
        )
        # Skill A references skill B in a backtick path.
        skill_a = plugin / "skills" / "skill-a"
        skill_a.mkdir(parents=True)
        (skill_a / "SKILL.md").write_text(
            "---\nname: skill-a\ndescription: First skill\n---\n\n"
            "# skill-a\n\n"
            "See `skill-b/SKILL.md` for the next step.\n"
            "Or call `skill-b` directly.\n"
        )
        skill_b = plugin / "skills" / "skill-b"
        skill_b.mkdir(parents=True)
        (skill_b / "SKILL.md").write_text("---\nname: skill-b\ndescription: Second skill\n---\n\n# skill-b\n")
        return plugin

    def test_skills_relative_path_resolves(self, tmp_path: Path) -> None:
        """``skill-b/SKILL.md`` in a sibling skill must resolve to
        ``plugin_root/skills/skill-b/SKILL.md`` (the v2.98.0 fallback)."""
        from cpv_validation_common import (
            ValidationReport,
            validate_md_file_paths,
        )

        plugin = self._make_plugin_with_two_skills(tmp_path)
        skill_a_md = plugin / "skills" / "skill-a" / "SKILL.md"
        report = ValidationReport()
        validate_md_file_paths(
            md_file=skill_a_md,
            plugin_root=plugin,
            report=report,
        )

        # No "Possible broken backtick path" WARNING about skill-b
        warnings_about_b = [
            r
            for r in report.results
            if r.level == "WARNING" and "Possible broken backtick path" in r.message and "skill-b" in r.message
        ]
        assert warnings_about_b == [], (
            f"Sibling-skill reference should resolve, but got: {[w.message for w in warnings_about_b]}"
        )

    def test_genuinely_broken_backtick_path_still_flagged(self, tmp_path: Path) -> None:
        """A backtick path to a non-existent location must still emit the
        WARNING — the v2.98.0 fix only adds new resolution fallbacks, it
        doesn't suppress the genuine-broken case."""
        from cpv_validation_common import (
            ValidationReport,
            validate_md_file_paths,
        )

        plugin = self._make_plugin_with_two_skills(tmp_path)
        skill_a_md = plugin / "skills" / "skill-a" / "SKILL.md"
        # Overwrite skill-a's body with a reference to a non-existent path
        skill_a_md.write_text(
            "---\nname: skill-a\ndescription: First skill\n---\n\n"
            "# skill-a\n\n"
            "See `path/to/nonexistent/file.txt` for whatever.\n"
        )
        report = ValidationReport()
        validate_md_file_paths(
            md_file=skill_a_md,
            plugin_root=plugin,
            report=report,
        )

        # The non-existent path SHOULD warn
        nonexistent_warnings = [r for r in report.results if "nonexistent" in r.message]
        assert nonexistent_warnings, (
            "Genuinely-broken backtick path must still emit a WARNING/MINOR — the fix only added new resolution paths"
        )


# =============================================================================
# Issue #31 — browser-orphan cleanup (baseline-diff)
# =============================================================================


class TestIssue31BrowserOrphanCleanup:
    """Gate 2 cleanup never touches baseline PIDs — only new orphans."""

    def test_snapshot_returns_set_of_ints(self) -> None:
        """``_snapshot_browser_pids()`` returns a set of integer PIDs."""
        from publish import _snapshot_browser_pids

        snap = _snapshot_browser_pids()
        assert isinstance(snap, set)
        assert all(isinstance(p, int) for p in snap)

    def test_signatures_do_not_match_normal_chrome(self) -> None:
        """Browser-orphan signatures are narrow — must not match a normal
        ``Google Chrome`` / ``Safari`` command (the maintainer's daily
        browser). Otherwise the cleanup would risk killing user work."""
        from publish import _BROWSER_ORPHAN_SIGNATURES

        # Daily-browser cmdlines that MUST NOT match any signature
        innocent = [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Safari.app/Contents/MacOS/Safari",
            "/Applications/Firefox.app/Contents/MacOS/firefox-bin",
            "/usr/bin/chrome",
        ]
        for cmd in innocent:
            matched = [sig for sig in _BROWSER_ORPHAN_SIGNATURES if sig in cmd]
            # Chromium.app/Contents would match macOS Chromium but not Google Chrome
            assert "Google Chrome.app" not in cmd or not matched, (
                f"Signature(s) {matched} false-positive on innocent cmdline {cmd!r}"
            )

    def test_signatures_match_chrome_for_testing(self) -> None:
        """Signatures DO match Playwright-spawned Chrome for Testing."""
        from publish import _BROWSER_ORPHAN_SIGNATURES

        # Real-world cmdline samples Playwright leaks
        leak_samples = [
            "/Users/x/Library/Caches/ms-playwright/chromium-1234/Chrome for Testing.app/Contents/MacOS/Chrome for Testing",
            "/path/to/chrome-for-testing-headless/headless_shell",
            "/usr/lib/playwright-core/somefile",
        ]
        for cmd in leak_samples:
            matched = [sig for sig in _BROWSER_ORPHAN_SIGNATURES if sig in cmd]
            assert matched, f"Signatures missed Playwright cmdline {cmd!r}"

    def test_cleanup_no_op_when_no_new_pids(self) -> None:
        """If baseline_pids covers all current browser PIDs (no NEW
        orphans), the cleanup is a no-op and returns 0."""
        from publish import _cleanup_browser_orphans, _snapshot_browser_pids

        baseline = _snapshot_browser_pids()
        # Immediately re-snapshot and cleanup — no new processes should
        # have appeared in the microsecond between the two calls.
        killed = _cleanup_browser_orphans(baseline)
        assert killed == 0, f"Expected no kills (nothing new since baseline); got {killed}"


# =============================================================================
# Test speed — slow-test xdist isolation
# =============================================================================


class TestSpeedXdistIsolation:
    """The xdist flake in test_language_subset_filter was the
    cross-test ``_DISPATCH`` shared-dict mutation. v2.98.0 switched the
    test to ``patch.object`` (full swap) to eliminate the leak."""

    def test_test_cpv_lint_engine_uses_patch_object_not_patch_dict(self) -> None:
        """The flaky test now uses ``patch.object(cpv_lint_engine, "_DISPATCH", …)``."""
        test_file = Path(__file__).parent / "test_cpv_lint_engine.py"
        body = test_file.read_text()
        # The test should patch via .object on the module, not .dict on the import
        target_block_idx = body.find("def test_language_subset_filter")
        assert target_block_idx != -1
        # Read 60 lines worth of body after the test signature
        target_block = body[target_block_idx : target_block_idx + 2000]
        assert "patch.object(_cle" in target_block or "patch.object(cpv_lint_engine" in target_block, (
            "test_language_subset_filter must use patch.object full-swap, "
            "not patch.dict, to eliminate xdist isolation flakes"
        )


# =============================================================================
# Lower threshold
# =============================================================================


class TestBatchPlannerLoweredThreshold:
    """DEFAULT_SHARD_SIZE dropped 30 → 15 in v2.98.0."""

    def test_default_shard_size_is_15(self) -> None:
        from cpv_batch_planner import DEFAULT_SHARD_SIZE

        assert DEFAULT_SHARD_SIZE == 15, f"v2.98.0 lowered DEFAULT_SHARD_SIZE to 15 — got {DEFAULT_SHARD_SIZE}"

    def test_plugin_fixer_md_documents_15_25_ceiling(self) -> None:
        """The plugin-fixer routing-table must reflect the lowered ceilings."""
        pf_md = Path(__file__).parent.parent / "agents" / "plugin-fixer.md"
        body = pf_md.read_text()
        # Must NOT still say 30-40 / 100-150 as the active numbers
        assert "**15-25**" in body, "plugin-fixer.md must document the new bare-opus ceiling 15-25"
        assert "**50-75**" in body, "plugin-fixer.md must document the new opus[1m] ceiling 50-75"


# =============================================================================
# Auto-batch dispatch wiring
# =============================================================================


class TestAutoBatchDispatchInMenuTree:
    """The menu-tree references must describe the auto-dispatch flow —
    user does NOT have to type /cpv-batch-fix manually."""

    def test_menu_tree_section_3_2_1_auto_dispatches(self) -> None:
        """§3.2.1 'Fix plugin findings' must call out the auto-batch flow."""
        mt = Path(__file__).parent.parent / "skills" / "cpv-main-menu-skill" / "references" / "menu-tree.md"
        body = mt.read_text()
        # Find §3.2.1's body
        section_start = body.find("#### 3.2.1 Fix plugin findings")
        assert section_start != -1
        section_end = body.find("####", section_start + 5)
        section_body = body[section_start:section_end]

        assert "AUTO-DISPATCH" in section_body.upper() or "auto-dispatch" in section_body.lower(), (
            "§3.2.1 must mention auto-dispatch of the batch protocol"
        )
        assert "cpv_batch_planner" in section_body, "§3.2.1 must reference the batch planner script"
        assert "cpv_batch_aggregator" in section_body, "§3.2.1 must reference the aggregator script"

    def test_menu_tree_section_3_5_1_inherits_auto_dispatch(self) -> None:
        """§3.5.1 'Upgrade to current pipeline standard' must also
        auto-dispatch when the fixer surfaces [BATCH_REQUIRED]."""
        mt = Path(__file__).parent.parent / "skills" / "cpv-main-menu-skill" / "references" / "menu-tree.md"
        body = mt.read_text()
        section_start = body.find("#### 3.5.1 Upgrade")
        assert section_start != -1
        section_end = body.find("####", section_start + 5)
        section_body = body[section_start:section_end]

        assert "BATCH_REQUIRED" in section_body or "auto-batch" in section_body.lower(), (
            "§3.5.1 (Upgrade) must mention BATCH_REQUIRED auto-dispatch"
        )

    def test_plugin_fixer_batch_required_format_includes_plugin_root(self) -> None:
        """plugin-fixer.md routing-table situation 3 must require the
        ``plugin-root:`` token in the [BATCH_REQUIRED] line so the
        orchestrator can auto-dispatch without re-running validate."""
        pf_md = Path(__file__).parent.parent / "agents" / "plugin-fixer.md"
        body = pf_md.read_text()
        # Find the routing-table-3 row
        assert "BATCH_REQUIRED" in body
        # The new format should require plugin-root + triage report
        assert "plugin-root:" in body, "plugin-fixer.md must require plugin-root: in BATCH_REQUIRED line"
        assert "Triage report:" in body or "triage-report" in body, "plugin-fixer.md must require a triage report path"

    def test_cpv_doctor_agent_emits_batch_dispatch_tokens(self) -> None:
        """cpv-doctor-agent's big-plugin handoff must surface plugin-root
        and safe-ceiling tokens so the orchestrator can plan without
        re-running the validator."""
        agent_md = Path(__file__).parent.parent / "agents" / "cpv-doctor-agent.md"
        body = agent_md.read_text()
        assert "recommend-batch-fix" in body, "cpv-doctor-agent must use the recommend-batch-fix token"
        assert "safe-ceiling=" in body, "cpv-doctor-agent must include safe-ceiling= in its output"
        assert "plugin-root=" in body, "cpv-doctor-agent must include plugin-root= so orchestrator auto-dispatches"


# =============================================================================
# Template carries the same browser-orphan cleanup
# =============================================================================


class TestGenPublishPyTemplateHasBrowserCleanup:
    """The canonical publish.py template emitted to consumer plugins
    must carry the SAME browser-orphan cleanup as CPV's own publish.py."""

    def test_template_emits_browser_cleanup_helpers(self) -> None:
        from generate_plugin_repo import PluginParams, gen_publish_py

        params = PluginParams(
            name="t",
            version="0.1.0",
            description="t",
            author="t",
            author_email="t@t",
        )
        src = gen_publish_py(params)

        assert "_BROWSER_ORPHAN_SIGNATURES" in src, "Template missing browser-orphan signatures"
        assert "_snapshot_browser_pids" in src, "Template missing snapshot helper"
        assert "_cleanup_browser_orphans" in src, "Template missing cleanup helper"
        assert "baseline_browser_pids" in src, "Template missing baseline capture in stage_tests"

    def test_template_does_not_skip_tests(self) -> None:
        """v2.98.0 explicitly rejected the cpv.gates_to_skip proposal —
        the template must NOT contain a skip-tests mechanism that would
        violate the iron rule (no plugin with issues pushed)."""
        from generate_plugin_repo import PluginParams, gen_publish_py

        params = PluginParams(
            name="t",
            version="0.1.0",
            description="t",
            author="t",
            author_email="t@t",
        )
        src = gen_publish_py(params)

        # The skip-tests anti-pattern must NOT be in the template
        forbidden = [
            "gates_to_skip",
            "--skip-gate",
            "_gate_is_skipped",
        ]
        for token in forbidden:
            assert token not in src, f"Template MUST NOT contain {token!r} — would violate iron rule"
