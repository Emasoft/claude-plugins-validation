"""Issue #184 — all four TRDD lifecycle zones are treated as one corpus.

`design/{proposals,tasks,archived,refused}/` are four zones of ONE non-shippable
design corpus. A card moves between them by a pure `git mv` with ZERO content
change, so a verdict that depends on which zone a card sits in means **obeying
the governance rules turns a passing plugin red** — the gate penalises
conformance. Reported by the ai-maestro-plugin Claude after archiving 12
terminal TRDDs flipped their plugin from exit 0 to exit 4 (NIT=3) on identical
bytes.

Reproduced before the fix at v4.2.0: the same TRDD scored NIT=0 in
`design/tasks/` and NIT=3 in `design/archived/` (FS_WRITE, PRIVILEGE_ESC,
CMD_INJECTION). The deciding call is `_is_dev_scratch_path`, which
`validate_plugin._run_skillaudit_native._should_skip` applies to IN-PROCESS
skillaudit findings — not only to external-scanner output.

Every assertion here is two-sided: a lifecycle zone is recognised AND a
non-lifecycle sibling under the same `design/` parent still is not, so the fix
can never decay into a blanket `design/` mute.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import _plugin_compute_hashes as pch  # noqa: E402
import validate_security as vs  # noqa: E402

LIFECYCLE_ZONES = ("proposals", "tasks", "archived", "refused")

# Same `design/` parent, NOT a lifecycle zone — the control that keeps every
# assertion two-sided. If the fix ever widened to a blanket `design/` skip,
# these would start being suppressed and the control tests would fail.
NON_LIFECYCLE_DIRS = ("notes", "audits-wip", "scratch")


class TestDevScratchCoversEveryLifecycleZone:
    """`_is_dev_scratch_path` — the call that actually decided #184."""

    @pytest.mark.parametrize("zone", LIFECYCLE_ZONES)
    def test_lifecycle_zone_is_dev_scratch(self, zone: str) -> None:
        assert vs._is_dev_scratch_path(f"/plug/design/{zone}/TRDD-899317b3-x.md") is True

    @pytest.mark.parametrize("other", NON_LIFECYCLE_DIRS)
    def test_non_lifecycle_design_dir_is_not_dev_scratch(self, other: str) -> None:
        """The two-sided half: a non-lifecycle dir under design/ still scans."""
        assert vs._is_dev_scratch_path(f"/plug/design/{other}/TRDD-899317b3-x.md") is False

    def test_design_root_itself_is_not_dev_scratch(self) -> None:
        assert vs._is_dev_scratch_path("/plug/design/README.md") is False

    def test_an_ordinary_skill_is_never_dev_scratch(self) -> None:
        assert vs._is_dev_scratch_path("/plug/skills/demo/SKILL.md") is False


class TestSelfScanEligibleCoversEveryLifecycleZone:
    """The hash-anchored self-scan gate keeps its `trdd-` filename condition."""

    @pytest.mark.parametrize("zone", LIFECYCLE_ZONES)
    def test_trdd_in_zone_is_eligible(self, zone: str) -> None:
        assert vs._is_self_scan_eligible(f"design/{zone}/trdd-899317b3-x.md") is True

    @pytest.mark.parametrize("zone", LIFECYCLE_ZONES)
    def test_non_trdd_file_in_zone_is_not_eligible(self, zone: str) -> None:
        """The filename gate is retained — this grants nothing new."""
        assert vs._is_self_scan_eligible(f"design/{zone}/payload.md") is False


class TestSecurityFixReferenceCoversEveryLifecycleZone:
    @pytest.mark.parametrize("zone", LIFECYCLE_ZONES)
    def test_trdd_in_zone_is_reference(self, zone: str) -> None:
        assert vs.is_security_fix_reference(f"design/{zone}/TRDD-899317b3-x.md") is True

    @pytest.mark.parametrize("other", NON_LIFECYCLE_DIRS)
    def test_trdd_outside_lifecycle_is_not_reference(self, other: str) -> None:
        assert vs.is_security_fix_reference(f"design/{other}/TRDD-899317b3-x.md") is False


class TestManifestSkipParity:
    """`_plugin_compute_hashes` duplicates the zone list deliberately (it must
    stay import-free of the validator so the manifest builds standalone). Pin
    the parity so the two copies cannot drift."""

    @pytest.mark.parametrize("zone", LIFECYCLE_ZONES)
    def test_manifest_and_validator_agree_on_zone(self, zone: str) -> None:
        rel = f"design/{zone}/trdd-899317b3-x.md"
        assert pch.is_self_scan_eligible(rel) is vs._is_self_scan_eligible(rel) is True

    @pytest.mark.parametrize("other", NON_LIFECYCLE_DIRS)
    def test_manifest_and_validator_agree_on_non_zone(self, other: str) -> None:
        rel = f"design/{other}/trdd-899317b3-x.md"
        assert pch.is_self_scan_eligible(rel) is vs._is_self_scan_eligible(rel) is False

    def test_every_zone_constant_is_in_the_dev_scratch_list(self) -> None:
        for part in vs._TRDD_LIFECYCLE_DIR_PARTS:
            assert part in vs._DEV_SCRATCH_DIR_PARTS

    def test_the_constant_names_exactly_the_four_zones(self) -> None:
        assert set(vs._TRDD_LIFECYCLE_DIR_PARTS) == {f"/design/{z}/" for z in LIFECYCLE_ZONES}


class TestNonVacuity:
    """Prove the assertions are load-bearing: restore the pre-fix single-zone
    list and the archived-zone claims must FAIL while the tasks-zone claims
    still pass. Without this, a helper that returned True for everything would
    make the whole file pass vacuously."""

    def test_pre_fix_list_stops_recognising_archived_but_still_recognises_tasks(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        pre_fix = tuple(p for p in vs._DEV_SCRATCH_DIR_PARTS if not p.startswith("/design/") or p == "/design/tasks/")
        monkeypatch.setattr(vs, "_DEV_SCRATCH_DIR_PARTS", pre_fix)

        # The bug, restored: identical bytes, opposite verdict by zone.
        assert vs._is_dev_scratch_path("/plug/design/archived/TRDD-899317b3-x.md") is False
        assert vs._is_dev_scratch_path("/plug/design/tasks/TRDD-899317b3-x.md") is True

    def test_control_would_catch_a_blanket_design_mute(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """If someone 'simplified' the fix to a blanket `/design/` skip, the
        non-lifecycle control must fail — proving it guards that regression."""
        monkeypatch.setattr(vs, "_DEV_SCRATCH_DIR_PARTS", ("/design/",))
        assert vs._is_dev_scratch_path("/plug/design/notes/TRDD-899317b3-x.md") is True
