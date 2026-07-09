#!/usr/bin/env python3
"""Two-sided tests for issue #158 — the absolute-path portability check must NOT
fire MAJOR on a colon-joined ``PATH``-shaped run of system directories.

Issue #158 (filed by the ai-maestro-janitor Claude, blocking a v0.35.6 publish):
``validate_no_absolute_paths`` raised a publish-blocking MAJOR on a literal
``/usr/bin:/bin:/usr/sbin:/sbin`` quoted in a design-doc — a forensic record of
the ``PATH`` macOS launchd handed a daemon. Root cause: the two existing
allowlists (``ALLOWED_DOC_PATH_PREFIXES`` and ``_SYSTEM_BINARY_PREFIXES``) match
with a ``/``-terminated prefix, but a ``PATH`` element is followed by ``:`` not
``/``, so a colon-joined system-dir run misses both.

FN-safe fix: ``_is_system_path_run`` clears a run ONLY when every colon-segment
is an FHS system directory. A run that mixes in a ``/Users/…`` / ``/home/…`` /
``/root/…`` segment (a real dev/home-path leak — the thing the rule guards) keeps
firing. Every test below is two-sided: the FP clears AND a real leak still fires.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Add scripts directory to path for imports
scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

from cpv_validation_common import (  # noqa: E402
    ValidationReport,
    _is_system_path_run,
    _segment_is_system_path,
    scan_file_for_absolute_paths,
)


def _scan(tmp_path: Path, name: str, content: str) -> ValidationReport:
    """Write ``content`` to ``tmp_path/name`` and run the absolute-path scan."""
    f = tmp_path / name
    f.write_text(content)
    report = ValidationReport()
    scan_file_for_absolute_paths(f, report, name)
    return report


def _blocking_levels(report: ValidationReport) -> list[str]:
    """MAJOR/MINOR/CRITICAL levels present (the ones a portability finding uses)."""
    return [r.level for r in report.results if r.level in {"CRITICAL", "MAJOR", "MINOR"}]


class TestSystemPathRunHelper:
    """Unit tests on the ``_is_system_path_run`` / ``_segment_is_system_path`` helpers."""

    def test_the_exact_issue_158_run_is_recognized(self) -> None:
        """The reported PATH value is recognized as an all-system run."""
        assert _is_system_path_run("/usr/bin:/bin:/usr/sbin:/sbin") is True

    def test_run_with_homebrew_and_local_dirs_is_recognized(self) -> None:
        """A realistic macOS PATH (homebrew + local + system) is all-system."""
        assert _is_system_path_run("/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin") is True

    def test_run_of_etc_and_var_is_recognized(self) -> None:
        """Non-binary system roots (/etc, /var) also qualify."""
        assert _is_system_path_run("/etc/foo:/var/bar") is True

    def test_single_path_is_not_a_run(self) -> None:
        """A single path (no colon) is NOT a run — single-path behavior is unchanged."""
        assert _is_system_path_run("/usr/bin/python3") is False

    def test_run_with_users_segment_is_rejected(self) -> None:
        """FN-safe: a /Users/… segment makes the run NOT all-system → still fires."""
        assert _is_system_path_run("/usr/bin:/Users/attacker/evil/bin") is False

    def test_run_with_home_segment_is_rejected(self) -> None:
        """FN-safe: a /home/… segment is not a system dir → run rejected."""
        assert _is_system_path_run("/usr/bin:/home/victim/.ssh") is False

    def test_run_with_root_home_segment_is_rejected(self) -> None:
        """/root (root's home) is deliberately excluded → run rejected."""
        assert _is_system_path_run("/usr/bin:/root/.ssh") is False

    def test_trailing_empty_segment_is_rejected(self) -> None:
        """A trailing colon (implicit CWD element) is suspicious → not cleared."""
        assert _is_system_path_run("/usr/bin:") is False

    def test_leading_empty_segment_is_rejected(self) -> None:
        """A leading colon (implicit CWD element) is suspicious → not cleared."""
        assert _is_system_path_run(":/usr/bin") is False

    def test_segment_exact_root_qualifies(self) -> None:
        """A bare root like /bin is itself a system location."""
        assert _segment_is_system_path("/bin") is True

    def test_segment_child_of_root_qualifies(self) -> None:
        """A child of a root qualifies."""
        assert _segment_is_system_path("/usr/local/bin") is True

    def test_segment_prefix_confusion_rejected(self) -> None:
        """/usrlocal must NOT match the /usr root (requires /usr or /usr/…)."""
        assert _segment_is_system_path("/usrlocal/evil") is False

    def test_segment_users_rejected(self) -> None:
        """A /Users home segment never qualifies."""
        assert _segment_is_system_path("/Users/someone/dev") is False


class TestIssue158FPClears:
    """The colon-joined system PATH run no longer produces a blocking finding."""

    def test_path_run_in_markdown_doc_clears(self, tmp_path: Path) -> None:
        """The exact #158 case: a PATH run in a .md design doc → no MAJOR/MINOR."""
        report = _scan(
            tmp_path,
            "design.md",
            "Observed launchd daemon PATH: `/usr/bin:/bin:/usr/sbin:/sbin`\n",
        )
        assert _blocking_levels(report) == []
        # It is still visible as an INFO note, not silently dropped.
        assert any(r.level == "INFO" for r in report.results)

    def test_path_run_in_shell_script_clears(self, tmp_path: Path) -> None:
        """The same run inside a shell script's PATH export → no MAJOR/MINOR."""
        report = _scan(
            tmp_path,
            "setup.sh",
            'export PATH="/usr/bin:/bin:/usr/sbin:/sbin"\n',
        )
        assert _blocking_levels(report) == []

    def test_realistic_macos_path_run_clears(self, tmp_path: Path) -> None:
        """A homebrew+local+system PATH run in a doc clears (matches #158 note)."""
        report = _scan(
            tmp_path,
            "notes.md",
            "PATH was `/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin`\n",
        )
        assert _blocking_levels(report) == []


class TestRealLeaksStillFire:
    """FN-safety: genuine dev/home-path leaks are unaffected by the run allowlist."""

    def test_users_dev_path_still_fires(self, tmp_path: Path) -> None:
        """A /Users/<name>/… dev path is a real leak → still blocks."""
        report = _scan(
            tmp_path,
            "notes.md",
            "Built at /Users/alicedev42/project/dist/bin/tool\n",
        )
        assert _blocking_levels(report), "a /Users/ dev path must still fire"

    def test_home_path_still_fires(self, tmp_path: Path) -> None:
        """A /home/<name>/… path is a real leak → still blocks.

        NB: the path is dot-free on purpose — this validator has a pre-existing
        line-7519 guard that skips any match containing a regex-special char
        (``.`` included), unrelated to the #158 fix.
        """
        report = _scan(
            tmp_path,
            "notes.md",
            "cache lives at /home/builduser/cache/thing/data\n",
        )
        assert _blocking_levels(report), "a /home/ path must still fire"

    def test_mixed_run_with_users_segment_still_fires(self, tmp_path: Path) -> None:
        """A PATH run that mixes in a /Users segment is NOT cleared → still blocks."""
        report = _scan(
            tmp_path,
            "notes.md",
            "PATH was `/usr/bin:/Users/attacker/evil/bin:/bin`\n",
        )
        assert _blocking_levels(report), "a run containing a /Users segment must still fire"

    def test_mixed_run_with_root_home_segment_still_fires(self, tmp_path: Path) -> None:
        """A run mixing in /root/… is not cleared (root excluded) → still fires.

        Dot-free path so the line-7519 regex-char guard doesn't swallow it; the
        point is that ``_is_system_path_run`` rejects the run because /root is
        excluded from ``_SYSTEM_PATH_ROOTS``.
        """
        report = _scan(
            tmp_path,
            "config.sh",
            'PATH="/usr/bin:/root/keys/id:/bin"\n',
        )
        assert _blocking_levels(report), "a run containing /root/ must still fire"
