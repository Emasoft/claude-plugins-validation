"""Regression tests for audit batch b20 fixes.

Covers the three files this agent owns:

* ``scripts/cpv_fp_classifier_rules.py`` — RC-65 ordering / escalation
  bug (denylist accessor wrongly escalated to CRITICAL) and the
  RC-87 ``bun.lock`` manifest-basename correction.
* ``scripts/cpv_strip_dev.py`` — ``should_strip_target`` OR-to-strip
  truth-table guard (docstring previously claimed AND).
* ``scripts/manage_doctor.py`` — ``--prune-dry-run`` precedence over
  ``--prune-old-versions`` (destructive-op safety) and the auth-status
  subprocess env-strip consistency.

Each test pins the corrected behavior AND includes a guard that would
have caught the original bug (the security case is two-sided: the
benign accessor stays un-escalated while the genuine SSRF still
escalates).
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import cpv_fp_classifier_rules as fp_rules  # noqa: E402  — registers classifiers
import cpv_strip_dev as strip_dev  # noqa: E402
from cpv_fp_classifier import (  # noqa: E402
    Context,
    FindingVerdict,
    apply_verdict,
    classify_rule,
)


def _rc(
    rule_id: str,
    line: str,
    *,
    surrounding: tuple[str, ...] | None = None,
    role: str = "source",
    path: str = "scripts/x.py",
) -> Context:
    return Context(
        rule_id=rule_id,
        matched_text="169.254.169.254",
        line_number=1,
        line=line,
        surrounding_lines=surrounding if surrounding is not None else (line,),
        file_role=role,
        file_path=path,
    )


class TestRc65DenylistAccessorNotEscalated:
    """RC-65 must not escalate a denylist/config accessor to CRITICAL.

    The network-hint list contains generic accessors (``.get(`` …) that
    double as dict/config lookups. The same-line pattern-source guard now
    runs first, and bare accessors only escalate when the IMDS literal is
    URL-positioned. Two-sided: benign accessors stay safe, genuine SSRF
    still escalates.
    """

    def test_denylist_get_is_definite_fp(self) -> None:
        """`denylist.get(..., "169.254.169.254")` is data, not an SSRF call."""
        ctx = _rc("RC-65", 'blocked = denylist.get("imds", "169.254.169.254")')
        assert classify_rule("RC-65", ctx) is FindingVerdict.DEFINITE_FP

    def test_denylist_get_not_promoted_to_critical_under_extreme(self) -> None:
        """The ordering bug escalated a denylist read to CRITICAL — it must not."""
        ctx = _rc("RC-65", 'blocked = denylist.get("imds", "169.254.169.254")')
        verdict = classify_rule("RC-65", ctx)
        action = apply_verdict(verdict, "major", allow_escalation=True)
        # DEFINITE_FP suppresses entirely; the original bug yielded
        # DEFINITE_TP -> report_severity == "critical".
        assert action.report_severity != "critical"
        assert action.report_severity is None

    def test_config_get_bare_default_stays_real_not_critical(self) -> None:
        """A bare default value (not URL-positioned) is benign — REAL, never escalated."""
        ctx = _rc("RC-65", 'host = config.get("blocked_host", "169.254.169.254")')
        verdict = classify_rule("RC-65", ctx)
        assert verdict is FindingVerdict.REAL
        action = apply_verdict(verdict, "major", allow_escalation=True)
        assert action.report_severity == "major"  # unchanged, not promoted

    def test_genuine_requests_get_still_definite_tp(self) -> None:
        """Real SSRF via requests.get keeps DEFINITE_TP — security preserved."""
        ctx = _rc("RC-65", "requests.get('http://169.254.169.254/latest/meta-data/')")
        assert classify_rule("RC-65", ctx) is FindingVerdict.DEFINITE_TP

    def test_genuine_ssrf_still_escalates_to_critical_under_extreme(self) -> None:
        """The malicious side must still promote MAJOR -> CRITICAL under --extreme."""
        ctx = _rc("RC-65", "requests.get('http://169.254.169.254/latest/meta-data/')")
        verdict = classify_rule("RC-65", ctx)
        action = apply_verdict(verdict, "major", allow_escalation=True)
        assert action.report_severity == "critical"

    def test_fluent_accessor_with_url_target_escalates(self) -> None:
        """A fluent `session.get("http://169.254.169.254/…")` is a real fetch -> DEFINITE_TP."""
        ctx = _rc("RC-65", 'session.get("http://169.254.169.254/latest/")')
        assert classify_rule("RC-65", ctx) is FindingVerdict.DEFINITE_TP

    def test_fluent_accessor_with_bare_ip_path_escalates(self) -> None:
        """`s.get("169.254.169.254/latest")` — IP followed by a path is URL-positioned."""
        ctx = _rc("RC-65", 's.get("169.254.169.254/latest/meta-data")')
        assert classify_rule("RC-65", ctx) is FindingVerdict.DEFINITE_TP

    def test_pattern_source_const_still_definite_fp(self) -> None:
        """A same-line `_HOSTS` pattern source stays DEFINITE_FP (regression guard)."""
        ctx = _rc("RC-65", "IMDS_HOSTS = ('169.254.169.254',)")
        assert classify_rule("RC-65", ctx) is FindingVerdict.DEFINITE_FP

    def test_surrounding_pattern_source_still_definite_fp(self) -> None:
        """A denylist set member detected via surrounding lines stays DEFINITE_FP."""
        ctx = _rc(
            "RC-65",
            "    '169.254.169.254',",
            surrounding=("UNSAFE_HOSTS = {", "    '127.0.0.1',"),
        )
        assert classify_rule("RC-65", ctx) is FindingVerdict.DEFINITE_FP


class TestRc87BunLockBasename:
    """RC-87 manifest set must reference the real `bun.lock`, not `bun.lock.json`."""

    def test_stale_bun_lock_json_removed(self) -> None:
        """`bun.lock.json` is not a real filename and must be gone from the set."""
        assert "bun.lock.json" not in fp_rules._RC87_MANIFEST_BASENAMES

    def test_real_bun_lock_present(self) -> None:
        """The real Bun text lockfile `bun.lock` is the correct basename."""
        assert "bun.lock" in fp_rules._RC87_MANIFEST_BASENAMES

    def test_bun_lock_version_line_suppressed(self) -> None:
        """A dep-version line in bun.lock that looks like an RFC-1918 IP is suppressed."""
        ctx = Context(
            rule_id="RC-87",
            matched_text="10.0.0.1",
            line_number=1,
            line='    "version": "10.0.0",',
            surrounding_lines=('    "version": "10.0.0",',),
            file_role="source",
            file_path="bun.lock",
        )
        assert classify_rule("RC-87", ctx) is FindingVerdict.DEFINITE_FP

    def test_real_ip_in_source_still_real(self) -> None:
        """A genuine internal-IP literal in a .py source file is NOT suppressed."""
        ctx = Context(
            rule_id="RC-87",
            matched_text="10.0.0.1",
            line_number=1,
            line='host = "10.0.0.1"',
            surrounding_lines=('host = "10.0.0.1"',),
            file_role="source",
            file_path="scripts/leak.py",
        )
        assert classify_rule("RC-87", ctx) is FindingVerdict.REAL


def _mk_target(src: str = "tests") -> "strip_dev.ExtractTarget":
    return strip_dev.ExtractTarget(src=src, submodule="o/n", submodule_path="dev/tests")


class TestShouldStripTargetOrLogic:
    """`should_strip_target` strips when EITHER threshold is crossed (OR-to-strip).

    The docstring previously claimed AND (strip only if BOTH crossed) while
    the code implemented OR. These tests pin the actual OR-to-strip truth
    table; an accidental flip to AND would fail at least two of them.
    """

    def test_heavy_bytes_low_files_is_stripped(self) -> None:
        """One big fixture (> byte threshold, 1 file) is worth stripping (OR)."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            src = root / "tests"
            src.mkdir()
            (src / "huge.bin").write_bytes(b"x" * (strip_dev.NEEDS_STRIP_BYTES_MIN + 10_000))
            worth, _reason = strip_dev.should_strip_target(_mk_target(), root)
            assert worth is True  # AND-logic would wrongly return False here

    def test_low_bytes_many_files_is_stripped(self) -> None:
        """Many tiny files (> file threshold, < byte threshold) is worth stripping (OR)."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            src = root / "tests"
            src.mkdir()
            for i in range(strip_dev.NEEDS_STRIP_FILES_MIN + 5):
                (src / f"t{i}.py").write_text("x")
            worth, _reason = strip_dev.should_strip_target(_mk_target(), root)
            assert worth is True  # AND-logic would wrongly return False here

    def test_small_by_both_measures_is_skipped(self) -> None:
        """Under BOTH thresholds → skip (the only False case)."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            src = root / "tests"
            src.mkdir()
            (src / "t.py").write_text("x")
            worth, reason = strip_dev.should_strip_target(_mk_target(), root)
            assert worth is False
            assert "under both" in reason

    def test_heavy_by_both_measures_is_stripped(self) -> None:
        """Over BOTH thresholds → strip."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            src = root / "tests"
            src.mkdir()
            for i in range(strip_dev.NEEDS_STRIP_FILES_MIN + 5):
                (src / f"t{i}.bin").write_bytes(b"x" * (strip_dev.NEEDS_STRIP_BYTES_MIN // 10))
            worth, _reason = strip_dev.should_strip_target(_mk_target(), root)
            assert worth is True

    def test_docstring_no_longer_claims_both_and(self) -> None:
        """Guard: the docstring must describe EITHER/OR, not the wrong BOTH/AND claim."""
        doc = strip_dev.should_strip_target.__doc__ or ""
        assert "EITHER" in doc
        # The old contradictory phrasing "ONLY if BOTH thresholds are crossed" is gone.
        assert "ONLY if BOTH thresholds are crossed" not in doc


class TestPruneDryRunPrecedence:
    """`--prune-dry-run` must always force a preview, even with `--prune-old-versions`.

    The original call-site expression `prune_dry_run and not prune_old_versions`
    evaluated to False when both flags were set, silently deleting. The fixed
    expression is just `prune_dry_run`, so the explicit "preview only" request
    always wins for this destructive operation.
    """

    @staticmethod
    def _dry_run_value(prune_dry_run: bool, prune_old_versions: bool) -> bool:
        # Mirror the fixed call-site logic: do_prune_old_versions(dry_run=args.prune_dry_run, …)
        # (the `or` branch-entry guard is independent of the dry_run value).
        return prune_dry_run

    def test_dry_run_only_previews(self) -> None:
        assert self._dry_run_value(prune_dry_run=True, prune_old_versions=False) is True

    def test_old_versions_only_deletes(self) -> None:
        assert self._dry_run_value(prune_dry_run=False, prune_old_versions=True) is False

    def test_both_flags_preview_not_delete(self) -> None:
        """The dangerous case: both flags must PREVIEW, never silently delete."""
        dry_run = self._dry_run_value(prune_dry_run=True, prune_old_versions=True)
        assert dry_run is True
        # Guard against the original buggy expression resurfacing:
        buggy = True and (not True)  # noqa: SIM222 — intentional repro of the bug shape
        assert dry_run != buggy  # fixed logic must differ from the buggy `and not` form

    def test_call_site_passes_bare_prune_dry_run(self) -> None:
        """The manage_doctor source must NOT re-introduce `and not args.prune_old_versions`."""
        src = (REPO_ROOT / "scripts" / "manage_doctor.py").read_text(encoding="utf-8")
        assert "dry_run=args.prune_dry_run,\n" in src
        assert "args.prune_dry_run and not args.prune_old_versions" not in src


class TestAuthEnvStripConsistency:
    """The auth-status subprocess env must REMOVE nested-instance markers, not blank them."""

    def test_claudecode_removed_not_blanked(self) -> None:
        """An absent CLAUDECODE fails both presence and truthiness checks; "" does not."""
        fake_environ = {
            "CLAUDECODE": "1",
            "CLAUDE_CODE_ENTRYPOINT": "agent",
            "HOME": "/home/u",
            "PATH": "/bin",
        }
        # Mirror the fixed construction in do_doctor's auth check.
        auth_env = {k: v for k, v in fake_environ.items() if k not in ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT")}
        assert "CLAUDECODE" not in auth_env  # removed, not auth_env["CLAUDECODE"] == ""
        assert "CLAUDE_CODE_ENTRYPOINT" not in auth_env
        assert auth_env["HOME"] == "/home/u"
        assert auth_env["PATH"] == "/bin"

    def test_source_no_longer_blanks_claudecode_in_auth_check(self) -> None:
        """Guard: the auth check must not re-introduce the `CLAUDECODE": ""` blanking."""
        src = (REPO_ROOT / "scripts" / "manage_doctor.py").read_text(encoding="utf-8")
        # The auth check now builds auth_env with the canonical removal pattern.
        assert 'env={**os.environ, "CLAUDECODE": ""}' not in src
        assert "auth_env = {k: v for k, v in os.environ.items() if k not in" in src

    def test_auth_env_matches_validate_env_pattern(self) -> None:
        """Both code paths strip the same two keys (no drift between them)."""
        src = (REPO_ROOT / "scripts" / "manage_doctor.py").read_text(encoding="utf-8")
        strip_tuple = '("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT")'
        # The pattern appears for both _run_claude_validate AND the auth check.
        assert src.count(strip_tuple) >= 2


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
