"""Issue #231 — the generated publish.py must pass the scanner that generated it.

The canonical ``publish.py`` installs trufflehog on demand and, on the
``go install`` fallback, used to prepend GOBIN to ``PATH``. CPV's own skillaudit
ENV_INJECTION rule flags a PATH mutation MAJOR (correctly: every later subprocess
resolves through it), so a freshly standardized plugin failed its own ``--strict``
gate. Canon now resolves the binary with ``shutil.which("trufflehog", path=_gobin)``
and calls it by that path.

Three layers, each two-sided:

* the GENERATED file, scanned by the REAL scanner, draws no ENV_INJECTION —
  and the pre-fix shape spliced into the same file still does (positive
  control: the scanner is not merely blind to this file);
* the MIGRATOR turns the pre-fix shape into bytes identical to canon, is
  idempotent, and leaves an unrecognised shape byte-identical while reporting it;
* the migrator runs on a plain ``--fix`` (driven, not grepped).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import cpv_skillaudit_native as native  # noqa: E402
import generate_plugin_repo as gen  # noqa: E402
import standardize_plugin as sp  # noqa: E402

# The pre-fix statement, assembled so this test file itself carries no line the
# ENV_INJECTION rule would match (it is a needle, not a mutation).
_PREPEND = 'os.environ["PATH"]' + ' = _gobin + os.pathsep + os.environ.get("PATH", "")'

# The block exactly as canon shipped it before #231 (frozen historical shape).
_OLD_BLOCK = (
    '    if not shutil.which("trufflehog"):\n'
    '        cprint(f"  {YELLOW}trufflehog missing — installing it as a pipeline dependency...{NC}")\n'
    "        # A stalled installer must land on the styled BLOCKED path below, not\n"
    "        # die with a raw TimeoutExpired traceback (audit row 16).\n"
    "        try:\n"
    '            if shutil.which("brew"):\n'
    '                subprocess.run(["brew", "install", "trufflehog"], timeout=900)\n'
    '            if not shutil.which("trufflehog") and shutil.which("go"):\n'
    "                subprocess.run(\n"
    '                    ["go", "install", "github.com/trufflesecurity/trufflehog/v3@latest"],\n'
    "                    timeout=900)\n"
    "                # `go install` drops the binary in GOBIN/GOPATH/bin, which is\n"
    "                # often not yet on PATH in this process.\n"
    '                _gobin = os.environ.get("GOBIN") or str(\n'
    '                    Path(os.environ.get("GOPATH") or (Path.home() / "go")) / "bin")\n'
    "                " + _PREPEND + "\n"
    "        except subprocess.TimeoutExpired:\n"
    '            cprint(f"  {YELLOW}The trufflehog installer timed out (>900s).{NC}")\n'
    "        except (OSError, subprocess.SubprocessError) as _exc:\n"
    '            cprint(f"  {YELLOW}The trufflehog installer failed to run: {_exc}{NC}")\n'
    '    if not shutil.which("trufflehog"):\n'
)
_OLD_ARGV = '["trufflehog", "filesystem", _sec_root,'
_NEW_ARGV = '[_trufflehog, "filesystem", _sec_root,'


def _canon() -> str:
    canon = sp._canonical_publish_py()
    assert canon is not None
    return canon


def _old_shape(canon: str) -> str:
    """Canon with the #231 block reverted to its pre-fix shape."""
    block = sp._slice_between(canon, sp._TRUFFLEHOG_NEW_START, sp._TRUFFLEHOG_BLOCK_END)
    assert block is not None and canon.count(_NEW_ARGV) == 1
    return canon.replace(block, _OLD_BLOCK, 1).replace(_NEW_ARGV, _OLD_ARGV, 1)


def _env_injection(text: str) -> list[dict[str, object]]:
    return [
        f
        for f in native.scan_content(text, "scripts/publish.py")
        if f.get("ruleId") == "ENV_INJECTION" and not f.get("suppressed")
    ]


@pytest.fixture(autouse=True)
def _no_scan_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CPV_SCAN_CACHE", "0")


# ── the generated file vs the REAL scanner ──────────────────────────────────


def test_generated_publish_py_draws_no_env_injection(tmp_path: Path) -> None:
    """A standard-profile plugin generated into tmp_path: its publish.py is clean."""
    params = gen.PluginParams(
        name="issue-231-sample", description="x", author="A", author_email="a@a.a",
        github_owner="Emasoft",
    )
    target = tmp_path / "issue-231-sample"
    target.mkdir()
    gen.generate_plugin_repo(target, params)
    text = (target / "scripts" / "publish.py").read_text(encoding="utf-8")
    assert "_gobin" in text, "fixture no longer covers the go-install fallback"
    assert _PREPEND not in text
    assert _env_injection(text) == []


def test_pre_fix_shape_still_fires_env_injection() -> None:
    """Positive control: the same scanner on the pre-fix shape still reports it."""
    hits = _env_injection(_old_shape(_canon()))
    assert len(hits) == 1
    assert "PATH" in str(hits[0].get("lineContent", ""))


def test_every_trufflehog_call_uses_the_resolved_path() -> None:
    """No later call may rely on PATH: the scan argv uses the resolved binary."""
    canon = _canon()
    body = canon[canon.index("def _secret_scan(") : canon.index("def _fork_parity_probe(")]
    assert '["trufflehog"' not in body
    assert _NEW_ARGV in body
    assert 'shutil.which("trufflehog", path=_gobin)' in body
    # brew / already-installed paths still resolve through plain PATH.
    assert '_trufflehog = shutil.which("trufflehog")\n' in body
    assert '_trufflehog = _trufflehog or shutil.which("trufflehog")' in body
    compile(canon, "publish.py", "exec")


# ── the migrator ────────────────────────────────────────────────────────────


def _write(root: Path, text: str) -> Path:
    publish = root / "scripts" / "publish.py"
    publish.parent.mkdir(parents=True, exist_ok=True)
    publish.write_text(text, encoding="utf-8")
    return publish


def test_migrated_output_is_byte_identical_to_canon(tmp_path: Path) -> None:
    canon = _canon()
    publish = _write(tmp_path, _old_shape(canon))
    notes = sp.migrate_publish_py_trufflehog_path(tmp_path)
    assert len(notes) == 1 and "#231" in notes[0]
    migrated = publish.read_text(encoding="utf-8")
    assert migrated == canon
    assert _env_injection(migrated) == []


def test_migration_is_idempotent(tmp_path: Path) -> None:
    publish = _write(tmp_path, _old_shape(_canon()))
    sp.migrate_publish_py_trufflehog_path(tmp_path)
    once = publish.read_bytes()
    assert sp.migrate_publish_py_trufflehog_path(tmp_path) == []
    assert publish.read_bytes() == once


def test_dry_run_writes_nothing(tmp_path: Path) -> None:
    old = _old_shape(_canon())
    publish = _write(tmp_path, old)
    notes = sp.migrate_publish_py_trufflehog_path(tmp_path, dry_run=True)
    assert notes and notes[0].startswith("[dry-run]")
    assert publish.read_text(encoding="utf-8") == old


def test_unrecognised_shape_is_left_byte_identical_and_reported(tmp_path: Path) -> None:
    """A hand-written prepend outside the canon block: untouched, and said so."""
    text = (
        "import os\nimport shutil\n\n"
        "def install() -> None:\n"
        '    _gobin = "/opt/go/bin"\n'
        "    " + _PREPEND + "\n"
    )
    publish = _write(tmp_path, text)
    notes = sp.migrate_publish_py_trufflehog_path(tmp_path)
    assert len(notes) == 1 and "NOT migrated" in notes[0]
    assert publish.read_text(encoding="utf-8") == text


def test_old_block_without_the_argv_anchor_is_not_half_migrated(tmp_path: Path) -> None:
    """All-or-nothing: the block alone would leave `_trufflehog` unused."""
    text = _old_shape(_canon()).replace(_OLD_ARGV, '["trufflehog",  "filesystem", _sec_root,', 1)
    publish = _write(tmp_path, text)
    notes = sp.migrate_publish_py_trufflehog_path(tmp_path)
    assert len(notes) == 1 and "NOT migrated" in notes[0]
    assert publish.read_text(encoding="utf-8") == text


def test_file_without_the_prepend_is_silent(tmp_path: Path) -> None:
    text = "print('hello')\n"
    publish = _write(tmp_path, text)
    assert sp.migrate_publish_py_trufflehog_path(tmp_path) == []
    assert publish.read_text(encoding="utf-8") == text


def test_no_publish_py_is_silent(tmp_path: Path) -> None:
    assert sp.migrate_publish_py_trufflehog_path(tmp_path) == []


def test_plain_fix_path_runs_the_migrator(tmp_path: Path) -> None:
    """Driven through fix_missing_files WITHOUT --force-templates.

    One MISSING file is reported so the call reaches the migrator block:
    fix_missing_files returns early when nothing is missing, which skips EVERY
    publish.py migrator, not just this one (pre-existing; reported separately).
    """
    publish = _write(tmp_path, _old_shape(_canon()))
    manifest = tmp_path / ".claude-plugin" / "plugin.json"
    manifest.parent.mkdir()
    manifest.write_text(
        '{"name": "issue-231-sample", "version": "0.1.0", "description": "x",'
        ' "author": {"name": "A"}}',
        encoding="utf-8",
    )
    missing = [sp.AuditItem("files", "cliff.toml", "MISSING", "absent")]
    sp.fix_missing_files(tmp_path, results=missing, force_templates=False)
    migrated = publish.read_text(encoding="utf-8")
    assert _PREPEND not in migrated
    assert _NEW_ARGV in migrated
