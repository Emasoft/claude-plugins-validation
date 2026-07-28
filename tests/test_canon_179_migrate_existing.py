"""A generator fix reaches NEW plugins; the fleet that reported #179 needs a migrator.

`generate_plugin_repo` is the single template source, and `standardize_plugin`
does regenerate from it — but only for files that do not exist yet. An existing
`scripts/publish.py` is never overwritten by a plain `--fix`, and the plugins
that hit #179 are exactly the ones that cannot safely run `--force-templates`
(customized or ahead-of-canon). So without a surgical migrator the fix lands
only on plugins that never had the bug.

This mirrors `migrate_publish_py_dependency_tag`, which exists for the same
reason. The tests below therefore care about three things in equal measure: the
migration WORKS, it is IDEMPOTENT, and an unrecognised file is left BYTE-
IDENTICAL rather than half-edited — a partially-rewritten publish.py in someone
else's repo is a worse outcome than an unmigrated one.

The pre-fix fixture is built by INVERTING the shipped fix against the live
template, so it is the exact text the old generator emitted rather than a
hand-typed approximation that could drift.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from scripts.generate_plugin_repo import PluginParams, gen_publish_py  # noqa: E402
from scripts.standardize_plugin import (  # noqa: E402
    migrate_publish_py_test_suite_timeout,
)


def _params() -> PluginParams:
    return PluginParams(
        name="my-test-plugin",
        description="A test plugin",
        author="Test Author",
        author_email="test@example.com",
        license="MIT",
        python_version="3.12",
        github_owner="test-owner",
        marketplace="test-marketplace",
        version="0.1.0",
    )


# The run() helper exactly as the generator emitted it BEFORE the #179 fix.
# Kept verbatim (not reconstructed) so the fixture is the real historical text.
_HISTORICAL_RUN = '''def run(
    cmd: list[str], cwd: Path | None = None, *, check: bool = True, capture: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Run a command, stream output, fail-fast on error."""
    cprint(f"  {BLUE}$ {' '.join(cmd)}{NC}")
    # A subprocess exceeding `timeout` raises TimeoutExpired; without this it
    # would die with a raw traceback instead of the styled fail-fast message
    # every other failure path uses. Catch it and exit 1.
    try:
        result = subprocess.run(cmd, cwd=str(cwd) if cwd else None, text=True,
                                capture_output=capture, timeout=300)
    except subprocess.TimeoutExpired:
        cprint(f"  {RED}Command timed out after 300s: {' '.join(cmd)}{NC}")
        sys.exit(1)
'''


def _prefix_publish_py() -> str:
    """The publish.py the OLD generator emitted — the fix, inverted."""
    text = gen_publish_py(_params())

    # Restore the resolver+run() region to its verbatim pre-fix text. Replacing
    # the whole region beats inverting it edit-by-edit: run()'s docstring also
    # changed and names the new symbol, and a fixture that still mentions it
    # would look already-migrated to the migrator — passing every test below for
    # the wrong reason.
    start = text.index("# Wall-clock bound for the TEST SUITE specifically")
    end = text.index("    if check and result.returncode != 0:", start)
    text = text[:start] + _HISTORICAL_RUN + text[end:]

    # The G4 inline gate site.
    text = text.replace("    suite_timeout = _test_suite_timeout()\n    try:\n", "    try:\n")
    text = text.replace("cwd=str(root), timeout=suite_timeout).returncode", "cwd=str(root), timeout=300).returncode")
    text = text.replace(
        'cprint(f"  {RED}BLOCKED: Tests timed out after {suite_timeout:g}s.{NC}")\n'
        '        cprint(f"  {RED}If the suite is legitimately longer, raise "\n'
        '               f"{_TEST_SUITE_TIMEOUT_ENV} — do not trim or skip tests to fit.{NC}")',
        'cprint(f"  {RED}BLOCKED: Tests timed out after 300s.{NC}")',
    )

    # stage_tests.
    text = text.replace(
        '        r = run(["uv", "run", "pytest", "tests/", "-x", "-q", "--tb=short"], cwd=root,\n'
        "                check=False, timeout=_test_suite_timeout())",
        '        r = run(["uv", "run", "pytest", "tests/", "-x", "-q", "--tb=short"], cwd=root, check=False)',
    )
    return text


def _write_plugin(tmp_path: Path, publish_src: str) -> Path:
    root = tmp_path / "plugin"
    (root / "scripts").mkdir(parents=True, exist_ok=True)
    (root / "scripts" / "publish.py").write_text(publish_src, encoding="utf-8")
    return root


# --------------------------------------------------------------------------
# The fixture itself must be honest — otherwise every test below is theatre
# --------------------------------------------------------------------------


def test_prefix_fixture_really_is_the_broken_shape() -> None:
    """LOAD-BEARING: if inverting the fix silently no-ops, every migration test
    below would be migrating an already-fixed file and would pass vacuously."""
    old = _prefix_publish_py()
    assert "_test_suite_timeout" not in old, "the inverted fixture still carries the fix"
    assert "capture_output=capture, timeout=300)" in old, "run() is not in its pre-fix shape"
    assert "cwd=str(root), timeout=300).returncode" in old, "the G4 site is not in its pre-fix shape"
    ast.parse(old)


# --------------------------------------------------------------------------
# Migration
# --------------------------------------------------------------------------


def test_migrates_an_existing_prefix_publish_py(tmp_path: Path) -> None:
    root = _write_plugin(tmp_path, _prefix_publish_py())
    notes = migrate_publish_py_test_suite_timeout(root)
    assert notes, "an unmigrated publish.py produced no note"

    migrated = (root / "scripts" / "publish.py").read_text(encoding="utf-8")
    assert "_test_suite_timeout" in migrated
    assert "capture_output=capture, timeout=timeout)" in migrated
    assert "cwd=str(root), timeout=suite_timeout).returncode" in migrated
    assert "timeout=300" not in migrated.split("\ndef run(", 1)[1].split("\ndef get_repo_root", 1)[0].replace(
        "timeout: float = 300", ""
    ), "run() still hardcodes a 300s bound in its body"
    ast.parse(migrated)


def test_migrated_file_matches_the_current_template(tmp_path: Path) -> None:
    """The migrator and the generator must not drift into two different fixes."""
    root = _write_plugin(tmp_path, _prefix_publish_py())
    migrate_publish_py_test_suite_timeout(root)
    migrated = (root / "scripts" / "publish.py").read_text(encoding="utf-8")
    assert migrated == gen_publish_py(_params()), "migrated output diverges from freshly generated canon"


def test_migration_is_idempotent(tmp_path: Path) -> None:
    """Re-running --fix must not stack a second resolver into the file."""
    root = _write_plugin(tmp_path, _prefix_publish_py())
    migrate_publish_py_test_suite_timeout(root)
    first = (root / "scripts" / "publish.py").read_text(encoding="utf-8")
    notes = migrate_publish_py_test_suite_timeout(root)
    second = (root / "scripts" / "publish.py").read_text(encoding="utf-8")
    assert second == first, "a second migration pass rewrote the file"
    assert notes == [], f"an already-migrated file still reported work: {notes}"


def test_current_template_needs_no_migration(tmp_path: Path) -> None:
    """A freshly scaffolded plugin is already correct — migrating it is a bug."""
    root = _write_plugin(tmp_path, gen_publish_py(_params()))
    before = (root / "scripts" / "publish.py").read_text(encoding="utf-8")
    assert migrate_publish_py_test_suite_timeout(root) == []
    assert (root / "scripts" / "publish.py").read_text(encoding="utf-8") == before


# --------------------------------------------------------------------------
# Fail-safe: never half-edit somebody else's publish.py
# --------------------------------------------------------------------------


def test_unrecognised_publish_py_is_left_byte_identical(tmp_path: Path) -> None:
    """LOAD-BEARING: a partially-rewritten publish.py in another repo is worse
    than an unmigrated one — it can fail to import at push time."""
    weird = "#!/usr/bin/env python3\nimport sys\n\n\ndef main() -> int:\n    return 0\n"
    root = _write_plugin(tmp_path, weird)
    notes = migrate_publish_py_test_suite_timeout(root)
    assert (root / "scripts" / "publish.py").read_text(encoding="utf-8") == weird
    assert notes, "an unrecognised shape was silently skipped instead of being surfaced"
    assert "publish.py" in notes[0]


def test_missing_publish_py_is_not_an_error(tmp_path: Path) -> None:
    """Most plugins standardize before they have one."""
    root = tmp_path / "plugin"
    root.mkdir()
    assert migrate_publish_py_test_suite_timeout(root) == []


def test_dry_run_reports_without_writing(tmp_path: Path) -> None:
    root = _write_plugin(tmp_path, _prefix_publish_py())
    before = (root / "scripts" / "publish.py").read_text(encoding="utf-8")
    notes = migrate_publish_py_test_suite_timeout(root, dry_run=True)
    assert (root / "scripts" / "publish.py").read_text(encoding="utf-8") == before
    assert notes and notes[0].startswith("[dry-run]")


def test_unreadable_publish_py_does_not_raise(tmp_path: Path) -> None:
    """standardize must survive a binary or mis-encoded file, not traceback."""
    root = tmp_path / "plugin"
    (root / "scripts").mkdir(parents=True)
    (root / "scripts" / "publish.py").write_bytes(b"\xff\xfe\x00binary")
    assert migrate_publish_py_test_suite_timeout(root) == []


# --------------------------------------------------------------------------
# Wiring — a migrator nothing calls is dead code
# --------------------------------------------------------------------------


def test_migrator_runs_on_a_plain_fix() -> None:
    """It must NOT be gated behind --force-templates: the plugins that need it
    are precisely the ones that cannot safely force-template."""
    src = (Path(__file__).resolve().parents[1] / "scripts" / "standardize_plugin.py").read_text(encoding="utf-8")
    assert src.count("migrate_publish_py_test_suite_timeout(") >= 2, "the migrator is never called"
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "migrate_publish_py_test_suite_timeout":
            break
    else:
        raise AssertionError("no direct call to the migrator found")
