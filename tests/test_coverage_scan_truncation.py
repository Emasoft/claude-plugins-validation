"""RC-TEST-COVERAGE stopped inventing findings on a suite bigger than its budget.

THE BUG, measured on CPV's own tree: ``_coverage_test_blobs`` concatenated test
files until a 5 MB cap and then **broke out of the loop**. CPV ships 7.7 MB of
tests, so 35% of its own test files were never read, and every component whose
only test lived in that unread tail was reported as having "no discoverable
test". The advisory named 21 components; 11 were real and **10 were artifacts of
where the byte budget happened to fall** — a finding that changes with
filesystem iteration order is not a finding.

Two things are pinned here, and the second is the one that matters:

* the scan now settles every component it can, regardless of suite size; and
* when the budget genuinely IS exhausted with components still unaccounted for,
  the check reports that it could not determine coverage instead of naming them.
  A truncated scan is not evidence of an untested component. This is the mirror
  of the project's "cannot check is never a pass" rule — cannot check is not a
  FAILURE either, and an advisory that guesses in the accusing direction is one
  a reader learns to ignore.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

os.environ.setdefault("CPV_SCAN_CACHE", "0")

import validate_plugin as vp  # noqa: E402


def _plugin_with_suite(root: Path, *, components: int, padding_per_file: int) -> None:
    """A plugin with ``components`` scripts, each tested in its own padded test file."""
    (root / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    (root / ".claude-plugin" / "plugin.json").write_text(
        '{"name": "p", "version": "1.0.0", "description": "x"}', encoding="utf-8"
    )
    (root / "scripts").mkdir(exist_ok=True)
    (root / "tests").mkdir(exist_ok=True)
    for i in range(components):
        (root / "scripts" / f"mod_{i}.py").write_text(f"VALUE = {i}\n", encoding="utf-8")
        # The mention that makes the component discoverable sits at the END of a
        # padded file, so a byte-capped reader that stops early misses it.
        (root / "tests" / f"test_thing_{i}.py").write_text(
            ("# padding\n" * padding_per_file) + f"import mod_{i}\n",
            encoding="utf-8",
        )


def _coverage_messages(root: Path) -> list[str]:
    report = vp.ValidationReport()
    vp.check_test_coverage(root, report)
    return [r.message for r in report.results if "RC-TEST-COVERAGE" in r.message]


# The budget that shipped the bug. The fixture below is deliberately larger than
# this and smaller than the current cap, so it reproduces the exact condition that
# used to truncate the scan and now must not.
_HISTORICAL_CAP_THAT_TRUNCATED = 5_000_000


def test_every_component_is_found_even_when_the_suite_exceeds_the_budget(tmp_path: Path) -> None:
    """A suite past the OLD 5 MB budget is now fully scanned, inventing nothing."""
    root = tmp_path / "plugin"
    # 40 test files, each naming its own module LAST so a reader that stops early
    # misses the mention — the shape that produced the false findings.
    _plugin_with_suite(root, components=40, padding_per_file=20_000)
    total = sum(len(p.read_text(encoding="utf-8")) for p in (root / "tests").glob("*.py"))
    assert total > _HISTORICAL_CAP_THAT_TRUNCATED, (
        f"precondition: fixture must exceed the old cap to reproduce the bug, got {total}"
    )
    assert total < vp._COVERAGE_CONTENT_SCAN_CAP, (
        "precondition: fixture must fit the CURRENT budget, or this asserts the wrong thing"
    )
    assert _coverage_messages(root) == [], "an over-budget suite still produced a coverage finding"


def test_partial_scan_reports_uncertainty_instead_of_naming_components(
    tmp_path: Path, monkeypatch
) -> None:
    """When the budget truly runs out, say so — never list components as untested."""
    root = tmp_path / "plugin"
    _plugin_with_suite(root, components=6, padding_per_file=400)
    # One genuinely untested component forces the scan to read everything, and a
    # tiny budget then guarantees it cannot.
    (root / "scripts" / "orphan.py").write_text("X = 1\n", encoding="utf-8")
    monkeypatch.setattr(vp, "_COVERAGE_CONTENT_SCAN_CAP", 10)

    messages = _coverage_messages(root)
    assert len(messages) == 1, messages
    assert "could not determine test coverage" in messages[0], messages[0]
    assert "orphan.py" not in messages[0], "a partial scan must not name a component untested"


def test_a_genuinely_untested_component_is_still_reported(tmp_path: Path) -> None:
    """CONTROL: the advisory must still fire — the fix widens discovery, not silence.

    Without this, a change that reported nothing at all would satisfy both tests
    above while destroying the check.
    """
    root = tmp_path / "plugin"
    _plugin_with_suite(root, components=2, padding_per_file=1)
    (root / "scripts" / "never_tested.py").write_text("X = 1\n", encoding="utf-8")

    messages = _coverage_messages(root)
    assert len(messages) == 1, messages
    assert "never_tested.py" in messages[0], messages[0]
    assert "could not determine" not in messages[0], "a completed scan must give the real list"


def test_matcher_reports_completion_honestly(tmp_path: Path, monkeypatch) -> None:
    """`_coverage_match_tokens` returns complete=False only when it truly stopped short."""
    root = tmp_path / "plugin"
    _plugin_with_suite(root, components=3, padding_per_file=200)
    test_files = sorted((root / "tests").glob("*.py"))

    matched, complete = vp._coverage_match_tokens(test_files, {"mod_0", "mod_1", "mod_2"})
    assert complete is True
    assert matched == {"mod_0", "mod_1", "mod_2"}

    monkeypatch.setattr(vp, "_COVERAGE_CONTENT_SCAN_CAP", 10)
    _matched, complete_after = vp._coverage_match_tokens(test_files, {"mod_0", "mod_1", "nope"})
    assert complete_after is False, "an exhausted budget must be reported as an incomplete scan"


def test_matcher_stops_early_once_everything_is_accounted_for(tmp_path: Path) -> None:
    """The budget is a runtime guard, not a source of findings — so it must rarely bind.

    A token set that the first file satisfies must not require reading the rest;
    that early exit is what lets the cap be generous without costing anything.
    """
    root = tmp_path / "plugin"
    _plugin_with_suite(root, components=5, padding_per_file=50_000)
    test_files = sorted((root / "tests").glob("*.py"))
    reads: list[str] = []

    # A recording shim rather than a Path subclass — the matcher only ever calls
    # `.read_text()` and reads `.name`, so this exercises the real code path.
    class _Shim:
        def __init__(self, real: Path) -> None:
            self._real = real
            self.name = real.name

        def read_text(self, *a: object, **k: object) -> str:
            reads.append(self._real.name)
            return self._real.read_text(*a, **k)  # type: ignore[arg-type]

    shims = [_Shim(p) for p in test_files]
    matched, complete = vp._coverage_match_tokens(shims, {"mod_0"})  # type: ignore[arg-type]
    assert complete is True
    assert matched == {"mod_0"}
    assert len(reads) < len(test_files), f"read every file to find one token: {reads}"


# ─────────────────────────────────────────────────────────────────────────────
# POPULATION: dead copies must not credit coverage, and the exclusion must show
#
# A deleted skill in safe-delete staging, an archived copy under a gitignored
# `*_dev/` tree, and a second CHECKOUT under `.claude/worktrees/` all hold real
# files with real frontmatter — no parser can tell them from the live article.
# Only a population rule can, and the honest rule is "does git say this ships?",
# never a list of directory names: naming conventions belong to one author, and
# CPV validates everyone's plugins.
#
# The anti-evasion half is load-bearing and is inherited from v2.126.26: a
# TRACKED file that is also gitignored still ships in `git archive`, so it must
# keep counting. Dropping by name would have dropped it too.
# ─────────────────────────────────────────────────────────────────────────────
import subprocess  # noqa: E402


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        capture_output=True,
        env={**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null"},
    )


def _git_plugin(root: Path) -> None:
    """A real repo with one component and one live test naming it."""
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin" / "plugin.json").write_text(
        '{"name": "p", "version": "1.0.0", "description": "x"}', encoding="utf-8"
    )
    (root / "scripts").mkdir()
    (root / "scripts" / "widget.py").write_text("X = 1\n", encoding="utf-8")
    (root / "tests").mkdir()
    (root / "tests" / "test_live.py").write_text("import unrelated\n", encoding="utf-8")
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "t")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "init")


def test_a_gitignored_untracked_test_does_not_credit_a_component(tmp_path: Path) -> None:
    """A dead copy in safe-delete staging must not report a component as covered."""
    root = tmp_path / "plugin"
    _git_plugin(root)
    (root / ".gitignore").write_text(".trashcan/\n", encoding="utf-8")
    dead = root / ".trashcan" / "20260101_000000+0000" / "tests"
    dead.mkdir(parents=True)
    (dead / "test_widget.py").write_text("import widget\n", encoding="utf-8")

    messages = _coverage_messages(root)
    assert len(messages) == 1, messages
    assert "scripts/widget.py" in messages[0], (
        "a deleted test in .trashcan/ credited a live component as covered"
    )


def test_the_excluded_population_is_stated_in_the_finding(tmp_path: Path) -> None:
    """A count whose scope is invisible is the shape that misleads — so print it."""
    root = tmp_path / "plugin"
    _git_plugin(root)
    (root / ".gitignore").write_text("scratch_dev/\n", encoding="utf-8")
    scratch = root / "scratch_dev"
    scratch.mkdir()
    (scratch / "test_archived.py").write_text("import widget\n", encoding="utf-8")

    (message,) = _coverage_messages(root)
    assert "non-shipped test file(s) excluded" in message, message
    assert "gitignored and untracked" in message, message


def test_a_TRACKED_gitignored_test_still_counts(tmp_path: Path) -> None:
    """ANTI-EVASION (v2.126.26): .gitignore does not untrack — a tracked file ships.

    This is the control that a name-based exclusion would have failed. Dropping
    `*_dev/` by NAME would drop this file too, and it is part of the artifact.
    """
    root = tmp_path / "plugin"
    _git_plugin(root)
    shipped = root / "scratch_dev"
    shipped.mkdir()
    (shipped / "test_widget.py").write_text("import widget\n", encoding="utf-8")
    _git(root, "add", "-f", "scratch_dev/test_widget.py")
    _git(root, "commit", "-qm", "ship it")
    # Ignored AFTER being tracked — the exact state that still ships.
    (root / ".gitignore").write_text("scratch_dev/\n", encoding="utf-8")

    assert _coverage_messages(root) == [], (
        "a TRACKED test file was dropped as unshipped — it ships in git archive"
    )


def test_without_git_nothing_is_dropped(tmp_path: Path) -> None:
    """No repo means the present tree IS the artifact — drop nothing."""
    root = tmp_path / "plugin"
    _plugin_with_suite(root, components=2, padding_per_file=1)
    files, dropped = vp._coverage_discover_tests(root)
    assert dropped == 0
    assert len(files) == 2
