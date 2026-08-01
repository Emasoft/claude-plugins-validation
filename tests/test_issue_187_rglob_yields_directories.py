"""Issue #187 — `GitignoreFilter.rglob` never yielded directories.

The reported symptom was a validator output that contradicted the filesystem:
a plugin with 11 committed binaries in `bin/` was told "users will need to
compile before use". The reporter guessed a naming convention mismatch; the
actual cause is one level down and explains BOTH of their findings at once.

`GitignoreFilter.rglob` pushed a directory onto its descent stack but never
tested it against the pattern, so `rglob("bin")` returned `[]` for a real,
tracked, populated `bin/`. Two consequences:

  1. `has_bin` was False for every plugin -> the "no pre-compiled binaries"
     WARNING fired regardless of what `bin/` actually held.
  2. The whole platform-coverage section returned early on an empty
     `all_bin_dirs`, so CPV had never once checked binary platform coverage.

An empty iterator is indistinguishable from "the tree really has none", which
is why this was invisible from every call site.

Every test here is two-sided: the fix must find a tracked `bin/` AND must not
start finding a gitignored one (the gitignore pruning is the FN-safety
property this method exists for).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from gitignore_filter import GitignoreFilter  # noqa: E402


def _git_init(root: Path) -> None:
    subprocess.run(["git", "init", "-q", "."], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.invalid"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=root, check=True)


def _make_tree(root: Path, *, gitignore: str | None = None, with_bin: bool = True) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    _git_init(root)
    (root / "src").mkdir()
    (root / "src" / "main.rs").write_text("fn main() {}\n", encoding="utf-8")
    (root / "Cargo.toml").write_text('[package]\nname = "demo"\nversion = "0.1.0"\n', encoding="utf-8")
    if with_bin:
        (root / "bin").mkdir()
        for name in (
            "demo-darwin-arm64",
            "demo-darwin-x86_64",
            "demo-linux-arm64",
            "demo-linux-x86_64",
            "demo-windows-x86_64.exe",
        ):
            (root / "bin" / name).write_bytes(b"\x7fELF fake compiled output\n")
    if gitignore is not None:
        (root / ".gitignore").write_text(gitignore, encoding="utf-8")
    return root


# ---------------------------------------------------------------- walker level


def test_rglob_yields_a_matching_directory(tmp_path: Path) -> None:
    """The regression itself: a tracked, populated `bin/` must be found."""
    root = _make_tree(tmp_path / "p")
    found = [p for p in GitignoreFilter(root.resolve()).rglob("bin")]
    assert [p.name for p in found] == ["bin"]
    assert found[0].is_dir()


def test_has_bin_expression_is_true_for_a_populated_bin(tmp_path: Path) -> None:
    """The exact expression validate_cross_platform computes."""
    root = _make_tree(tmp_path / "p").resolve()
    bin_dirs = list(GitignoreFilter(root).rglob("bin"))
    assert any(d.is_dir() and any(d.iterdir()) for d in bin_dirs) is True


def test_gitignored_directory_is_still_pruned(tmp_path: Path) -> None:
    """FN-safety control: yielding dirs must not defeat gitignore pruning."""
    root = _make_tree(tmp_path / "p", gitignore="bin/\n").resolve()
    assert list(GitignoreFilter(root).rglob("bin")) == []


def test_empty_bin_directory_is_found_but_reads_as_no_binaries(tmp_path: Path) -> None:
    """A dir that exists but is empty must not be mistaken for shipped binaries."""
    root = _make_tree(tmp_path / "p", with_bin=False).resolve()
    (root / "bin").mkdir()
    bin_dirs = list(GitignoreFilter(root).rglob("bin"))
    assert [d.name for d in bin_dirs] == ["bin"]
    assert any(d.is_dir() and any(d.iterdir()) for d in bin_dirs) is False


def test_file_patterns_are_unaffected(tmp_path: Path) -> None:
    """Existing file-glob callers must see exactly what they saw before."""
    root = _make_tree(tmp_path / "p").resolve()
    gi = GitignoreFilter(root)
    assert [p.name for p in gi.rglob("*.rs")] == ["main.rs"]
    assert [p.name for p in gi.rglob("*.toml")] == ["Cargo.toml"]


def test_pathlib_parity_for_a_directory_pattern(tmp_path: Path) -> None:
    """`rglob` documents itself as Path.rglob minus the descent into ignored dirs.

    With nothing ignored, the two must agree — that equivalence is the property
    the bug silently violated.
    """
    root = _make_tree(tmp_path / "p").resolve()
    mine = {p.resolve() for p in GitignoreFilter(root).rglob("bin")}
    theirs = {p.resolve() for p in root.rglob("bin")}
    assert mine == theirs


def test_nested_bin_directory_is_found(tmp_path: Path) -> None:
    """Descent still happens; a match deeper in the tree is yielded too."""
    root = _make_tree(tmp_path / "p").resolve()
    nested = root / "tools" / "helper" / "bin"
    nested.mkdir(parents=True)
    (nested / "helper-linux-x86_64").write_bytes(b"\x7fELF\n")
    found = {str(p.relative_to(root)) for p in GitignoreFilter(root).rglob("bin")}
    assert found == {"bin", "tools/helper/bin"}


def test_a_directory_is_both_descended_into_and_matched(tmp_path: Path) -> None:
    """Matching must not consume the descent (bin/ containing a nested bin/)."""
    root = _make_tree(tmp_path / "p").resolve()
    (root / "bin" / "bin").mkdir()
    (root / "bin" / "bin" / "inner").write_bytes(b"\x7fELF\n")
    found = {str(p.relative_to(root)) for p in GitignoreFilter(root).rglob("bin")}
    assert found == {"bin", "bin/bin"}


# ------------------------------------------------------------- validator level


def _validate(root: Path) -> dict:
    """Run the REAL plugin validator and return its parsed JSON report."""
    (root / ".claude-plugin").mkdir(exist_ok=True)
    (root / ".claude-plugin" / "plugin.json").write_text(
        json.dumps(
            {
                "name": "demo187",
                "description": "fixture plugin for issue 187 bin detection",
                "version": "0.1.0",
                "author": {"name": "T", "email": "t@example.invalid"},
            }
        ),
        encoding="utf-8",
    )
    res = subprocess.run(
        [sys.executable, str(SCRIPTS / "validate_plugin.py"), str(root), "--json"],
        capture_output=True,
        text=True,
        check=False,
        env={
            "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
            "PLUGIN_SKIP_GITHUB_INTEGRITY": "1",
            "CPV_SCAN_CACHE": "0",
            "PYTHONPATH": str(SCRIPTS),
        },
    )
    # The validator prints progress banners to stdout ahead of the JSON object,
    # so take the last balanced object rather than parsing the whole stream.
    start = res.stdout.find('{\n  "exit_code"')
    if start == -1:
        start = res.stdout.find("{")
    try:
        return json.loads(res.stdout[start:])
    except (json.JSONDecodeError, ValueError) as exc:  # pragma: no cover
        pytest.fail(f"validator emitted no parseable JSON (rc={res.returncode}): {exc}\n{res.stdout[-500:]}")


def _messages(report: dict) -> list[str]:
    """Every finding message.

    Reads `results`, the key the validator actually emits. An earlier draft of
    this harness read a `findings` key that does not exist, so it returned []
    for every run — which made the "this message must be ABSENT" test pass
    while proving nothing. `_assert_report_is_non_vacuous` is the guard against
    that failure mode returning.
    """
    return [str(f.get("message", "")) for f in report.get("results", [])]


def _assert_report_is_non_vacuous(report: dict) -> None:
    """A report with no findings cannot support an absence assertion."""
    assert report.get("results"), "validator produced no findings — an absence assertion would be vacuous"


def test_populated_bin_no_longer_claims_users_must_compile(tmp_path: Path) -> None:
    """The reporter's exact false claim, against a bin/ full of binaries."""
    root = _make_tree(tmp_path / "p").resolve()
    report = _validate(root)
    _assert_report_is_non_vacuous(report)
    msgs = _messages(report)
    assert not [m for m in msgs if "Users will need to compile before use" in m]
    # Positive control: the same run DOES reach this code path and reports the
    # binaries, so the absence above is a real clear and not an empty report.
    assert [m for m in msgs if "with compiled binaries in bin/" in m]


def test_missing_bin_still_says_users_must_compile(tmp_path: Path) -> None:
    """FN-safety control: a genuinely compile-required plugin must still WARN."""
    root = _make_tree(tmp_path / "p", with_bin=False).resolve()
    msgs = _messages(_validate(root))
    assert [m for m in msgs if "Users will need to compile before use" in m]


def test_platform_coverage_check_actually_runs(tmp_path: Path) -> None:
    """It was dead code: `all_bin_dirs` was always empty, so it returned early."""
    root = _make_tree(tmp_path / "p").resolve()
    msgs = _messages(_validate(root))
    assert [m for m in msgs if "compiled binary file(s)" in m]
    assert [m for m in msgs if "cover recommended platforms" in m]


def test_platform_coverage_reports_a_gap(tmp_path: Path) -> None:
    """Two-sided: incomplete coverage must be reported, not glossed over."""
    root = _make_tree(tmp_path / "p", with_bin=False).resolve()
    (root / "bin").mkdir()
    (root / "bin" / "demo-darwin-arm64").write_bytes(b"\x7fELF\n")
    msgs = _messages(_validate(root))
    assert [m for m in msgs if "Compiled binaries missing for" in m]


def test_gitignored_bin_is_not_credited_as_shipped(tmp_path: Path) -> None:
    """A gitignored+untracked bin/ does not ship, so the WARNING must remain."""
    root = _make_tree(tmp_path / "p", gitignore="bin/\n").resolve()
    msgs = _messages(_validate(root))
    assert [m for m in msgs if "Users will need to compile before use" in m]
