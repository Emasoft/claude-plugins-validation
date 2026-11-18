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


class TestTrailingSentencePunctuation:
    """Issue #277: sentence punctuation after a path is not part of the path.

    The path regex stops only at whitespace/quotes/brackets, so `/usr/bin,` used
    to be captured with its comma; the exact-match `in` / `startswith` downgrade
    then missed it and a bare system dir in prose fired MAJOR in a doc file (no
    compliant remedy: `/usr/bin` has no `${CLAUDE_PLUGIN_ROOT}` form).
    """

    @pytest.mark.parametrize(
        "line",
        [
            "The test skips when lsof is not in /usr/bin, /bin, /usr/sbin or /sbin, ok.\n",
            "The binary is found in /usr/bin.\n",
            "For admin tools see /usr/sbin;\n",
            "Its PATH contains /usr/bin and more.\n",
            "Really, is it in /usr/bin?\n",
            "It is in /usr/bin!\n",
            "Look in /usr/bin:\n",
        ],
    )
    def test_system_dir_followed_by_punctuation_in_doc_does_not_block(self, tmp_path: Path, line: str) -> None:
        """A bare system bin dir followed by `, . ; : ! ?` in a .md file is not a blocking finding."""
        report = _scan(tmp_path, "notes.md", line)
        assert not _blocking(report), f"{line!r} blocked --strict: {_blocking(report)}"

    def test_home_path_followed_by_comma_still_fires_at_full_severity(self, tmp_path: Path) -> None:
        """POSITIVE CONTROL: a home path before a comma is still a MAJOR leak."""
        report = _scan(tmp_path, "notes.md", "It was built in /Users/alice/code/thing, then moved.\n")
        levels = [r.level.upper() for r in report.results if "Absolute path found" in r.message]
        assert levels == ["MAJOR"], f"a home path followed by a comma must fire MAJOR, got {levels}"

    def test_inner_dot_path_is_unchanged(self, tmp_path: Path) -> None:
        """Only a TRAILING run is trimmed: `/usr/local/bin/python3.12` still clears, an inner dot is kept."""
        assert not _blocking(_scan(tmp_path, "conf.yml", "interp: /usr/local/bin/python3.12\n"))
        report = _scan(tmp_path, "conf2.yml", "p: /etc/app.v2/thing,\n")
        messages = _blocking(report)
        assert len(messages) == 1, f"/etc/app.v2/thing must still fire once, got {messages}"
        assert "'/etc/app.v2/thing...'" in messages[0], f"trim must drop only the comma: {messages[0]}"


class TestTrailingPunctuationWindowsHomeAndMarketplace:
    """Issue #277: the trim is ONE shared helper (system paths only), so both scanners get it."""

    def test_usr_bin_python_with_inner_dot_is_unchanged(self, tmp_path: Path) -> None:
        """`/usr/bin/python3.12` (inner dot, no trailing punctuation) still clears, in code and in docs."""
        assert not _blocking(_scan(tmp_path, "conf.yml", "interp: /usr/bin/python3.12\n"))
        assert not _blocking(_scan(tmp_path, "notes.md", "Run /usr/bin/python3.12 first.\n"))

    def test_windows_home_path_followed_by_comma_still_fires_at_full_severity(self, tmp_path: Path) -> None:
        """POSITIVE CONTROL: a Windows home path before a comma is still a MAJOR leak."""
        report = _scan(tmp_path, "notes.md", "It was built in C:\\Users\\alice\\code\\thing, then moved.\n")
        levels = [r.level.upper() for r in report.results if "Absolute path found" in r.message]
        assert levels == ["MAJOR"], f"a Windows home path followed by a comma must fire MAJOR, got {levels}"

    def test_marketplace_scanner_reports_the_trimmed_path(self, tmp_path: Path) -> None:
        """The marketplace scanner reuses the pattern: its finding names the path, not the comma."""
        from validate_marketplace import validate_marketplace_private_info

        (tmp_path / "README.md").write_text("Spool lives in /var/spool/thing, see docs.\n", encoding="utf-8")
        results = validate_marketplace_private_info(tmp_path, [])
        messages = [r.message for r in results if "Absolute path found" in r.message]
        assert len(messages) == 1, f"expected one finding, got {messages}"
        assert "'/var/spool/thing'" in messages[0], f"the comma leaked into the path: {messages[0]}"

    def test_marketplace_scanner_home_path_with_comma_still_fires_major(self, tmp_path: Path) -> None:
        """POSITIVE CONTROL for the marketplace scanner: a home path before a comma is still MAJOR."""
        from validate_marketplace import validate_marketplace_private_info

        (tmp_path / "README.md").write_text("Built in /Users/alice/code/thing, then moved.\n", encoding="utf-8")
        results = validate_marketplace_private_info(tmp_path, [])
        found = [(r.level.upper(), r.message) for r in results if "Absolute path found" in r.message]
        assert [lvl for lvl, _ in found] == ["MAJOR"], f"expected one MAJOR, got {found}"
        assert "thing,'" in found[0][1], f"a home path must be reported exactly as before (untrimmed): {found[0][1]}"

    @pytest.mark.parametrize(
        "line",
        [
            "A Windows path looks like `C:\\Users\\...` or `C:/Users/...`.\n",
            "The settings live at /Users/.../.llm-externalizer/settings.yaml, see docs.\n",
        ],
    )
    def test_home_placeholder_ellipsis_is_not_trimmed_into_a_finding(self, tmp_path: Path, line: str) -> None:
        """REGRESSION (CPV self-validation): the `...` username placeholder must not be trimmed away.

        Trimming trailing dots on a HOME pattern turned the Windows Users path with an
        ellipsis into a bare Users path, which loses the placeholder username and fired
        MAJOR on CPV own docs. Only the system-path pattern is trimmed.
        """
        assert not _blocking(_scan(tmp_path, "notes.md", line)), f"{line!r} fired after the trim"
