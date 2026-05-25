"""Regression tests for the 2026-05-25 cpv_dedup audit findings #12/#15/#16.

Each finding gets TWO-SIDED coverage — a test that pins the intended new
behaviour AND a test that would still fail if the fix were the trivial
"do nothing / suppress everything" non-fix:

  * #12 (dead ``import os``) — the module imports and works after the import
    was removed (positive), and ``os`` is genuinely gone from the module
    namespace (negative: proves the import was deleted, not just shadowed).
  * #15 (missing ``--`` separator in the fclones argv) — the argv passes
    ``--`` immediately before the positional ``stage_root`` so a path
    beginning ``-`` is treated as a path, not a flag (positive), with the
    options appearing before the separator (negative: proves the path was
    actually moved past the ``--``, not that ``--`` was bolted on in the
    wrong place).
  * #16 (no containment assertion in ``apply_dedup``) — a victim INSIDE
    ``stage_root`` is unlinked (positive) while a victim OUTSIDE
    ``stage_root`` is NOT unlinked when the boundary is supplied (negative:
    the guard fires). Plus backward-compat: with no boundary the original
    delete-everything behaviour is preserved.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import cpv_dedup as dedup  # noqa: E402

# ── #12 — dead ``import os`` removed ───────────────────────────────


class TestFinding12DeadImportOs:
    def test_module_still_imports_and_core_helpers_work(self) -> None:
        """Positive: removing the dead import did not break the module."""
        # A representative pure helper still works end-to-end.
        result = dedup.parse_dedup_groups({"groups": [{"files": ["/s/a", "/s/b"]}]})
        assert result == {Path("/s/a"): [Path("/s/a"), Path("/s/b")]}
        # The public surface is intact.
        for name in (
            "DedupResult",
            "is_fclones_available",
            "run_fclones",
            "parse_dedup_groups",
            "apply_dedup",
            "bucket_canonical_to_members",
        ):
            assert hasattr(dedup, name), f"missing public symbol {name}"

    def test_os_is_no_longer_a_module_attribute(self) -> None:
        """Negative: ``os`` is genuinely gone, not merely unused.

        If the fix had only deleted the ``_ = os`` suppression line but left
        ``import os`` in place, ``dedup.os`` would still resolve and this
        assertion would fail — so this is what makes the pair two-sided.
        """
        assert not hasattr(dedup, "os")


# ── #15 — ``--`` end-of-options separator in the fclones argv ──────


class TestFinding15FclonesDashDashSeparator:
    @staticmethod
    def _capture_argv(monkeypatch: pytest.MonkeyPatch, stage_root: Path) -> list[str]:
        """Run ``run_fclones`` with a fake subprocess that records the argv."""
        captured: dict[str, list[str]] = {}

        class _FakeCompleted:
            returncode = 0
            stdout = "{}"
            stderr = ""

        def fake_run(cmd: list[str], *a: object, **kw: object) -> _FakeCompleted:
            captured["cmd"] = list(cmd)
            return _FakeCompleted()

        monkeypatch.setattr(dedup, "is_fclones_available", lambda: True)
        monkeypatch.setattr(dedup.subprocess, "run", fake_run)
        # stage_root must be a real directory so the is_dir() gate passes.
        result = dedup.run_fclones(stage_root)
        assert result.attempted is True
        return captured["cmd"]

    def test_dashdash_immediately_precedes_path(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Positive: the argv contains ``--`` directly before stage_root."""
        cmd = self._capture_argv(monkeypatch, tmp_path)
        assert "--" in cmd, f"no end-of-options separator in argv: {cmd}"
        sep_index = cmd.index("--")
        # The path is the very next token after ``--``.
        assert cmd[sep_index + 1] == str(tmp_path)
        # And the path is the LAST positional (nothing trails it).
        assert sep_index + 1 == len(cmd) - 1

    def test_options_appear_before_the_separator(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Negative: the option flags sit BEFORE ``--`` (not after it).

        A naive "append ``--`` at the end" non-fix would leave the path in
        front of the options and the separator dangling — this test pins the
        flags to the pre-separator region so the path was genuinely moved.
        """
        cmd = self._capture_argv(monkeypatch, tmp_path)
        sep_index = cmd.index("--")
        before = cmd[:sep_index]
        for flag in ("--format", "json", "--hidden", "--no-ignore"):
            assert flag in before, f"{flag} should precede the -- separator: {cmd}"
        # subcommand and program name are also before the separator.
        assert before[:2] == ["fclones", "group"]

    def test_path_beginning_with_dash_is_not_swallowed_as_flag(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Positive: a stage_root whose name starts with ``-`` lands after ``--``.

        This is the exact scenario the separator defends against. We don't run
        real fclones (the dir need not exist on disk for the argv check), so we
        bypass the is_dir() gate by faking it too.
        """
        dashy = Path("/tmp/-weird-stage-root")
        captured: dict[str, list[str]] = {}

        class _FakeCompleted:
            returncode = 0
            stdout = "{}"
            stderr = ""

        monkeypatch.setattr(dedup, "is_fclones_available", lambda: True)
        monkeypatch.setattr(dedup.Path, "is_dir", lambda self: True)
        monkeypatch.setattr(
            dedup.subprocess,
            "run",
            lambda cmd, *a, **kw: captured.__setitem__("cmd", list(cmd)) or _FakeCompleted(),
        )
        dedup.run_fclones(dashy)
        cmd = captured["cmd"]
        sep_index = cmd.index("--")
        assert cmd[sep_index + 1] == str(dashy)
        # The dashy path must NOT appear before the separator (where clap would
        # try to parse it as an option).
        assert str(dashy) not in cmd[:sep_index]


# ── #16 — containment guard in ``apply_dedup`` ─────────────────────


class TestFinding16ContainmentGuard:
    def test_victim_inside_stage_root_is_unlinked(self, tmp_path: Path) -> None:
        """Positive: a duplicate under stage_root is deleted when guard is on."""
        stage_root = tmp_path / "stage"
        stage_root.mkdir()
        canonical = stage_root / "canonical.md"
        victim = stage_root / "dup.md"
        canonical.write_text("same")
        victim.write_text("same")

        removed, saved = dedup.apply_dedup(
            {canonical: [canonical, victim]}, stage_root=stage_root
        )
        assert removed == 1
        assert saved == len("same")
        assert canonical.exists()
        assert not victim.exists()

    def test_victim_outside_stage_root_is_NOT_unlinked(self, tmp_path: Path) -> None:
        """Negative: a crafted victim outside stage_root survives — guard fires.

        The dedup_map is hand-built (the exact tamper scenario the guard
        defends against): the canonical is inside the staging tree but the
        ``victim`` points at a sibling file OUTSIDE it. With the boundary
        supplied, that victim must be skipped (neither deleted nor counted).
        """
        stage_root = tmp_path / "stage"
        stage_root.mkdir()
        canonical = stage_root / "canonical.md"
        canonical.write_text("same")

        outside = tmp_path / "OUTSIDE_precious.md"  # sibling of stage_root
        outside.write_text("do not delete me")

        removed, saved = dedup.apply_dedup(
            {canonical: [canonical, outside]}, stage_root=stage_root
        )
        # The escaping victim is skipped: zero removed, zero counted.
        assert removed == 0
        assert saved == 0
        assert outside.exists(), "containment guard failed — outside file deleted!"
        assert outside.read_text() == "do not delete me"

    def test_dotdot_escape_is_blocked(self, tmp_path: Path) -> None:
        """Negative: a ``..``-relative victim that resolves outside is blocked."""
        stage_root = tmp_path / "stage"
        stage_root.mkdir()
        canonical = stage_root / "canonical.md"
        canonical.write_text("same")

        outside = tmp_path / "escape.md"
        outside.write_text("safe")
        # Express the victim path with a traversal segment that resolves out.
        sneaky = stage_root / ".." / "escape.md"

        removed, _ = dedup.apply_dedup(
            {canonical: [canonical, sneaky]}, stage_root=stage_root
        )
        assert removed == 0
        assert outside.exists(), "dotdot traversal escaped the containment guard"

    def test_mixed_inside_and_outside_victims(self, tmp_path: Path) -> None:
        """Inside victim deleted, outside victim spared, in one dedup_map."""
        stage_root = tmp_path / "stage"
        stage_root.mkdir()
        canonical = stage_root / "canonical.md"
        inside = stage_root / "dup.md"
        canonical.write_text("same")
        inside.write_text("same")

        outside = tmp_path / "OUTSIDE.md"
        outside.write_text("same")

        removed, _ = dedup.apply_dedup(
            {canonical: [canonical, inside, outside]}, stage_root=stage_root
        )
        assert removed == 1
        assert not inside.exists()
        assert outside.exists()

    def test_no_boundary_preserves_original_behaviour(self, tmp_path: Path) -> None:
        """Backward-compat: without stage_root, every duplicate is deleted.

        This proves the guard is opt-in — the ``validate_security`` caller that
        does not pass a boundary keeps the pre-fix delete-everything semantics.
        """
        canonical = tmp_path / "canonical"
        dup1 = tmp_path / "dup1"
        dup2 = tmp_path / "dup2"
        for p in (canonical, dup1, dup2):
            p.write_text("same")

        removed, _ = dedup.apply_dedup({canonical: [canonical, dup1, dup2]})
        assert removed == 2
        assert canonical.exists()
        assert not dup1.exists()
        assert not dup2.exists()

    def test_dry_run_with_boundary_counts_inside_only(self, tmp_path: Path) -> None:
        """dry_run + boundary: inside victim counted, outside victim ignored."""
        stage_root = tmp_path / "stage"
        stage_root.mkdir()
        canonical = stage_root / "canonical.md"
        inside = stage_root / "dup.md"
        canonical.write_text("12345")
        inside.write_text("12345")

        outside = tmp_path / "OUTSIDE.md"
        outside.write_text("12345")

        removed, saved = dedup.apply_dedup(
            {canonical: [canonical, inside, outside]},
            stage_root=stage_root,
            dry_run=True,
        )
        assert removed == 1  # only the inside victim is counted
        assert saved == 5
        # Nothing is actually deleted in dry-run.
        assert inside.exists()
        assert outside.exists()

    def test_unresolvable_boundary_is_failsafe_noop(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Fail-safe: if the boundary can't be resolved, delete nothing.

        Rather than fall back to an unguarded delete, an unresolvable
        ``stage_root`` makes the whole pass a no-op.
        """
        stage_root = tmp_path / "stage"
        stage_root.mkdir()
        canonical = stage_root / "canonical.md"
        victim = stage_root / "dup.md"
        canonical.write_text("same")
        victim.write_text("same")

        real_resolve = Path.resolve

        def boom_resolve(self: Path, *a: object, **kw: object) -> Path:
            # Only the boundary resolution explodes; leave others intact.
            if self == stage_root:
                raise OSError("cannot resolve boundary")
            return real_resolve(self, *a, **kw)

        monkeypatch.setattr(dedup.Path, "resolve", boom_resolve)
        removed, saved = dedup.apply_dedup(
            {canonical: [canonical, victim]}, stage_root=stage_root
        )
        assert removed == 0
        assert saved == 0
        assert victim.exists(), "fail-safe breached — victim deleted despite bad boundary"


# ── _is_within helper (direct unit coverage) ───────────────────────


class TestIsWithinHelper:
    def test_path_inside_boundary(self, tmp_path: Path) -> None:
        boundary = tmp_path.resolve()
        inside = tmp_path / "a" / "b.md"
        assert dedup._is_within(inside, boundary) is True

    def test_path_equal_to_boundary(self, tmp_path: Path) -> None:
        boundary = tmp_path.resolve()
        assert dedup._is_within(tmp_path, boundary) is True

    def test_path_outside_boundary(self, tmp_path: Path) -> None:
        boundary = (tmp_path / "stage").resolve()
        (tmp_path / "stage").mkdir()
        outside = tmp_path / "elsewhere.md"
        assert dedup._is_within(outside, boundary) is False

    def test_sibling_prefix_is_not_inside(self, tmp_path: Path) -> None:
        """``/a/stage`` must not be considered the parent of ``/a/stage-evil``.

        A naive ``str.startswith`` check would wrongly accept ``stage-evil`` as
        inside ``stage``; the parents-based check rejects it.
        """
        stage = tmp_path / "stage"
        stage_evil = tmp_path / "stage-evil"
        stage.mkdir()
        stage_evil.mkdir()
        victim = stage_evil / "file.md"
        assert dedup._is_within(victim, stage.resolve()) is False
