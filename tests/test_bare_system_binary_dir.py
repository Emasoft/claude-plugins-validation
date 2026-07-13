#!/usr/bin/env python3
"""Bare system binary directories are never a portability finding.

Found during the xhigh code review of the RC-6 config-comment fix. RC-6 stopped
a `/usr/bin` mention inside a config COMMENT from blocking `--strict`, but the
underlying defect was one level down and RC-6 only masked it:

    _SYSTEM_BINARY_PREFIXES = ("/usr/bin/", "/bin/", ...)   # every entry ends in "/"

`startswith()` against those matches `/usr/bin/python` but NOT a bare `/usr/bin`,
and `_is_system_path_run()` bails on `":" not in matched_text` — so a bare system
binary directory reached MINOR, which blocks `--strict` (exit 3), in EVERY
context: a YAML value, a `.py` comment, a doc. RC-6 only cleared it inside config
comments.

The rule already declares such a path never-actionable ("System binary paths are
expected for tool detection"; issue #158: an FHS path "CANNOT be rewritten to a
plugin-relative form"). A bare `/usr/bin` is exactly that.

TWO-SIDED, per finding: the FP clears AND the sibling that must still fire, does.
An absence-assertion with no positive control proves nothing.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from cpv_validation_common import (  # noqa: E402
    ValidationReport,
    scan_file_for_absolute_paths,
)

# Bare directory forms of every entry in _SYSTEM_BINARY_PREFIXES.
BARE_SYSTEM_BIN_DIRS = [
    "/usr/bin",
    "/usr/local/bin",
    "/opt/homebrew/bin",
    "/bin",
    "/sbin",
    "/usr/sbin",
]


def _scan(tmp_path: Path, name: str, content: str) -> ValidationReport:
    target = tmp_path / name
    target.write_text(content, encoding="utf-8")
    report = ValidationReport()
    scan_file_for_absolute_paths(target, report, tmp_path)
    return report


# The field is `level`, NOT `severity`. Read from the class, never from memory:
# a `getattr(r, "severity", "")` typo returns "" for every result, so `_blocking`
# yields [] and EVERY absence-assertion in this file passes vacuously. That is
# exactly how a suppression ships disguised as a fix — see
# `_blocking_helper_actually_works` below, which pins it.
_BLOCKING_LEVELS = {"CRITICAL", "MAJOR", "MINOR", "NIT"}


def _blocking(report: ValidationReport) -> list[str]:
    """Messages at a level that blocks `--strict` (NIT and above; INFO/WARNING do not)."""
    return [r.message for r in report.results if r.level.upper() in _BLOCKING_LEVELS]


class TestHelperIsNotVacuous:
    """Guard the guard.

    Every FP-clear test below asserts `not _blocking(report)`. If `_blocking()`
    is broken (wrong attribute name → always []), all of them pass while proving
    NOTHING. This pins the helper against exactly that: it must detect a finding
    that certainly blocks, and must not count an INFO.
    """

    def test_blocking_helper_actually_works(self) -> None:
        report = ValidationReport()
        report.minor("a blocking minor", "f.yml", 1)
        report.info("a non-blocking info", "f.yml", 2)
        blocking = _blocking(report)
        assert blocking == ["a blocking minor"], (
            f"_blocking() is broken — it must see the MINOR and ignore the INFO, got {blocking}"
        )


class TestBareSystemBinaryDirClears:
    """The FP side: a bare system bin dir must not block --strict."""

    @pytest.mark.parametrize("bin_dir", BARE_SYSTEM_BIN_DIRS)
    def test_bare_dir_in_yaml_value_does_not_block(self, tmp_path: Path, bin_dir: str) -> None:
        """A bare system bin dir as a YAML VALUE is not a blocking finding."""
        report = _scan(tmp_path, "conf.yml", f"toolpath: {bin_dir}\n")
        assert not _blocking(report), f"{bin_dir} as a value blocked --strict: {_blocking(report)}"

    @pytest.mark.parametrize("bin_dir", BARE_SYSTEM_BIN_DIRS)
    def test_bare_dir_in_python_comment_does_not_block(self, tmp_path: Path, bin_dir: str) -> None:
        """RC-6 only cleared CONFIG comments; a .py comment must clear too, via the dir fix."""
        report = _scan(tmp_path, "tool.py", f"# we look for the binary under {bin_dir} first\n")
        assert not _blocking(report), f"{bin_dir} in a .py comment blocked --strict"

    def test_trailing_slash_form_still_clears(self, tmp_path: Path) -> None:
        """The pre-existing prefix behaviour is untouched: /usr/bin/python stays clear."""
        report = _scan(tmp_path, "conf.yml", "interp: /usr/bin/python3\n")
        assert not _blocking(report)


class TestNearMissesStillFire:
    """The POSITIVE CONTROL. Without these the tests above prove nothing."""

    def test_confusable_prefix_still_fires(self, tmp_path: Path) -> None:
        """/usr/binfoo is NOT a system bin dir — exact match must not clear it."""
        report = _scan(tmp_path, "conf.yml", "p: /usr/binfoo/thing\n")
        assert _blocking(report), "/usr/binfoo was cleared — the match is not exact"

    def test_home_path_still_fires(self, tmp_path: Path) -> None:
        """A home path is the leak this rule exists for; it must still block."""
        report = _scan(tmp_path, "conf.yml", "p: /Users/alice/code/thing\n")
        assert _blocking(report), "a /Users home path was cleared — the leak rule is broken"

    def test_home_path_in_config_comment_still_fires(self, tmp_path: Path) -> None:
        """RC-6 clears config comments ONLY for system paths — a home path in one still fires."""
        report = _scan(tmp_path, "conf.yml", "#   built from /Users/alice/src originally\n")
        assert _blocking(report), "a home path inside a config comment was cleared"

    def test_non_binary_system_path_still_fires(self, tmp_path: Path) -> None:
        """The fix is BARE-BIN-DIR-narrow, not "every FHS path is fine".

        This is the control that pins the blast radius. `/etc/passwd` is an FHS
        system path, so a broader fix (clearing anything `_segment_is_system_path`
        accepts) would have silently downgraded it too. It must still block: only
        the six bare BIN directories are cleared, nothing else under /usr, /etc,
        /var, ...
        """
        report = _scan(tmp_path, "conf.yml", "p: /etc/passwd\n")
        assert _blocking(report), "/etc/passwd was cleared — the fix over-broadened beyond bin dirs"


class TestDerivedListStaysInSync:
    """The bare-dir list is DERIVED, so it cannot drift from the prefixes."""

    def test_dirs_are_derived_from_prefixes(self) -> None:
        from cpv_validation_common import (
            _SYSTEM_BINARY_DIRS,
            _SYSTEM_BINARY_PREFIXES,
        )

        assert _SYSTEM_BINARY_DIRS == tuple(p.rstrip("/") for p in _SYSTEM_BINARY_PREFIXES)
        assert len(_SYSTEM_BINARY_DIRS) == len(_SYSTEM_BINARY_PREFIXES)
        # No entry may retain a trailing slash — that would make the exact match dead.
        assert not any(d.endswith("/") for d in _SYSTEM_BINARY_DIRS)
