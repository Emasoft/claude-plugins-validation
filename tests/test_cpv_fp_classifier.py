"""Tests for the context-aware FP classifier scaffolding (TRDD-fe006962).

Covers the enum, the registry, the dispatch helper, and the
verdict-to-severity translation. Per-rule classifier behaviour is
exercised in dedicated tests once each rule is wired in (Step 2 of
the TRDD).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from cpv_fp_classifier import (  # noqa: E402
    RULE_CLASSIFIERS,
    Context,
    FindingVerdict,
    apply_verdict,
    classify_rule,
    demote_severity,
    file_role_of,
    has_sink_nearby,
    register_classifier,
)


@pytest.fixture(autouse=True)
def _isolate_registry():
    """Snapshot RULE_CLASSIFIERS so tests can register without leaking state.

    The registry is module-level for production use; tests that
    register their own classifiers must not pollute the global view
    seen by other tests.
    """

    saved = dict(RULE_CLASSIFIERS)
    RULE_CLASSIFIERS.clear()
    yield
    RULE_CLASSIFIERS.clear()
    RULE_CLASSIFIERS.update(saved)


def _ctx(rule_id: str = "RC-21", **overrides) -> Context:
    base = dict(
        rule_id=rule_id,
        matched_text="os.environ.copy()",
        line_number=42,
        line="env = os.environ.copy()",
        surrounding_lines=("subprocess.Popen(cmd, env=env)",),
        file_role="source",
        file_path="src/launcher.py",
        plugin_meta={},
    )
    base.update(overrides)
    return Context(**base)


class TestFindingVerdictEnum:
    def test_four_tiers_present(self) -> None:
        assert FindingVerdict.DEFINITE_FP.value == "definite_fp"
        assert FindingVerdict.LIKELY_FP.value == "likely_fp"
        assert FindingVerdict.REAL.value == "real"
        assert FindingVerdict.DEFINITE_TP.value == "definite_tp"


class TestRegistry:
    def test_register_then_classify(self) -> None:
        @register_classifier("RC-21")
        def _cls(ctx: Context) -> FindingVerdict:
            return FindingVerdict.LIKELY_FP

        assert classify_rule("RC-21", _ctx()) is FindingVerdict.LIKELY_FP

    def test_unknown_rule_defaults_to_real(self) -> None:
        # No registration → REAL preserves legacy behaviour.
        assert classify_rule("RC-NEVER-REGISTERED", _ctx()) is FindingVerdict.REAL

    def test_duplicate_registration_rejected(self) -> None:
        @register_classifier("RC-22")
        def _first(ctx: Context) -> FindingVerdict:
            return FindingVerdict.REAL

        with pytest.raises(ValueError, match="Duplicate classifier registration for RC-22"):
            @register_classifier("RC-22")
            def _second(ctx: Context) -> FindingVerdict:
                return FindingVerdict.REAL


class TestDemoteSeverity:
    def test_one_step_demotion(self) -> None:
        assert demote_severity("critical") == "major"
        assert demote_severity("major") == "minor"
        assert demote_severity("minor") == "nit"
        assert demote_severity("nit") == "info"
        assert demote_severity("info") == "passed"

    def test_clamps_at_bottom(self) -> None:
        # Already at passed → stays at passed even with multiple steps.
        assert demote_severity("passed") == "passed"
        assert demote_severity("passed", steps=10) == "passed"

    def test_unknown_severity_passes_through(self) -> None:
        assert demote_severity("warning") == "warning"

    def test_multi_step(self) -> None:
        assert demote_severity("critical", steps=2) == "minor"
        assert demote_severity("critical", steps=4) == "info"


class TestApplyVerdict:
    def test_definite_fp_suppresses(self) -> None:
        action = apply_verdict(FindingVerdict.DEFINITE_FP, "critical")
        assert action.report_severity is None
        assert "suppressed" in action.note

    def test_likely_fp_demotes_one(self) -> None:
        action = apply_verdict(FindingVerdict.LIKELY_FP, "critical")
        assert action.report_severity == "major"
        assert "critical → major" in action.note

    def test_real_keeps_severity(self) -> None:
        action = apply_verdict(FindingVerdict.REAL, "minor")
        assert action.report_severity == "minor"
        assert "real finding" in action.note

    def test_definite_tp_no_escalation_by_default(self) -> None:
        action = apply_verdict(FindingVerdict.DEFINITE_TP, "major")
        assert action.report_severity == "major"
        # No escalation note because allow_escalation=False is the default.
        assert "promoted" not in action.note

    def test_definite_tp_with_escalation(self) -> None:
        action = apply_verdict(FindingVerdict.DEFINITE_TP, "major", allow_escalation=True)
        assert action.report_severity == "critical"
        assert "promoted" in action.note

    def test_definite_tp_at_critical_does_not_overflow(self) -> None:
        # Critical is the top — escalation must clamp.
        action = apply_verdict(FindingVerdict.DEFINITE_TP, "critical", allow_escalation=True)
        assert action.report_severity == "critical"


class TestFileRoleOf:
    def test_test_path(self) -> None:
        assert file_role_of("tests/test_foo.py") == "test"
        assert file_role_of("src/__test_helper.py") == "test"

    def test_fixture_path(self) -> None:
        assert file_role_of("tests/fixtures/sample.py") == "fixture"

    def test_doc_path(self) -> None:
        assert file_role_of("docs/intro.md") == "doc"
        assert file_role_of("README.md") == "doc"

    def test_sample_path(self) -> None:
        assert file_role_of(".env.example") == "sample"
        assert file_role_of("config.template") == "sample"
        assert file_role_of("examples/quickstart.py") == "sample"

    def test_source_default(self) -> None:
        assert file_role_of("src/main.py") == "source"
        assert file_role_of("scripts/build.sh") == "source"


class TestHasSinkNearby:
    def test_match(self) -> None:
        assert has_sink_nearby(
            ("subprocess.Popen(cmd)",),
            ("subprocess.", "child_process."),
        ) is True

    def test_no_match(self) -> None:
        assert has_sink_nearby(
            ("print('hello')",),
            ("subprocess.", "child_process."),
        ) is False

    def test_empty_lines(self) -> None:
        assert has_sink_nearby((), ("subprocess.",)) is False

    def test_substring_not_word_match(self) -> None:
        # has_sink_nearby is intentionally substring-only — the sink list
        # owners are responsible for picking distinctive substrings.
        assert has_sink_nearby(("urllib.request.urlopen(url)",), ("urlopen",)) is True


class TestContext:
    def test_immutable_dataclass(self) -> None:
        ctx = _ctx()
        with pytest.raises(Exception):  # FrozenInstanceError
            ctx.matched_text = "different"  # type: ignore[misc]
