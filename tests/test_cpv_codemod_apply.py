#!/usr/bin/env python3
"""Tests for cpv_codemod's fix_id-dispatched ``apply`` mode (Phase 2, TRDD-GVMOKJBB).

Coverage — every transform assertion is TWO-SIDED (fixes its target AND leaves a
look-alike untouched):

* ``_apply_fix_chmod_exec`` transform:
  - fixes a shebang script that is not executable (--apply)
  - leaves a look-alike WITHOUT a shebang untouched
  - dry-run reports the change but writes nothing
  - idempotent: an already-executable file is a no-op skip
  - skips a vendored file and a missing file (fail-safe)
* ``apply --json`` CLI dispatch:
  - dry-run reports fixable entries, writes nothing
  - --apply fixes the MECH (fixable) entries and is idempotent (2nd run no-op)
  - NON-fixable findings are ignored entirely (apply only acts on the MECH set)
  - a fixable finding with an unregistered fix_id is reported + skipped
  - a bad/missing findings path returns 1 (never a traceback)

The exec-bit + os.access(X_OK) semantics are Unix-only, and the validator branch
that emits the tag is gated on ``not IS_WINDOWS`` — so the whole module skips on
Windows.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

# Make scripts/ importable.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import cpv_codemod  # noqa: E402

pytestmark = pytest.mark.skipif(
    sys.platform == "win32",
    reason="chmod-exec relies on POSIX exec bits / os.access(X_OK) — Unix-only",
)


# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_plugin(tmp_path: Path, name: str = "apply-plugin") -> Path:
    """A minimal plugin root with .claude-plugin/plugin.json."""
    root = tmp_path / name
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": name, "version": "1.0.0"}, indent=2) + "\n",
        encoding="utf-8",
    )
    return root


def _script(root: Path, rel: str, *, shebang: bool, executable: bool) -> Path:
    """Create a script under ``root`` with/without a shebang and exec bit."""
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "#!/usr/bin/env python3\n" if shebang else ""
    path.write_text(body + "print('x')\n", encoding="utf-8")
    os.chmod(path, 0o755 if executable else 0o644)
    return path


def _finding(file: str, *, fixable: bool = True, fix_id: str | None = "chmod-exec", level: str = "WARNING") -> dict:
    """One ValidationResult.to_dict()-shaped finding entry."""
    d: dict[str, object] = {"level": level, "message": f"{file} needs attention", "file": file}
    if fixable:
        d["fixable"] = True
        if fix_id is not None:
            d["fix_id"] = fix_id
    return d


def _write_findings(tmp_path: Path, results: list[dict], name: str = "findings.json") -> Path:
    """Write a standard remote_validation.py wrapper around ``results``."""
    path = tmp_path / name
    path.write_text(
        json.dumps({"exit_code": 0, "counts": {}, "results": results, "security_gates": {}}),
        encoding="utf-8",
    )
    return path


# ── Transform: _apply_fix_chmod_exec (two-sided) ─────────────────────────────


class TestChmodExecTransform:
    """The chmod-exec transform fixes a shebang target and leaves look-alikes alone."""

    def test_fixes_shebang_target(self, tmp_path: Path):
        root = _make_plugin(tmp_path)
        script = _script(root, "scripts/hello.py", shebang=True, executable=False)
        backup_root = cpv_codemod._backup_dir(root)
        outcome = cpv_codemod._apply_fix_chmod_exec(
            {"file": "scripts/hello.py"}, root, backup_root, set(), apply=True
        )
        assert outcome.changed is True
        assert outcome.detail == "chmod +x applied"
        assert os.access(script, os.X_OK) is True
        # A per-file backup (preserving the original mode) was created.
        assert list(backup_root.rglob("hello.py"))

    def test_leaves_lookalike_without_shebang_untouched(self, tmp_path: Path):
        root = _make_plugin(tmp_path)
        script = _script(root, "scripts/nohashbang.py", shebang=False, executable=False)
        backup_root = cpv_codemod._backup_dir(root)
        outcome = cpv_codemod._apply_fix_chmod_exec(
            {"file": "scripts/nohashbang.py"}, root, backup_root, set(), apply=True
        )
        # Fail-safe: no shebang → never chmod'd.
        assert outcome.changed is False
        assert outcome.detail == "skip (no shebang)"
        assert os.access(script, os.X_OK) is False
        assert not backup_root.exists()

    def test_dry_run_does_not_change_mode(self, tmp_path: Path):
        root = _make_plugin(tmp_path)
        script = _script(root, "scripts/hello.py", shebang=True, executable=False)
        backup_root = cpv_codemod._backup_dir(root)
        outcome = cpv_codemod._apply_fix_chmod_exec(
            {"file": "scripts/hello.py"}, root, backup_root, set(), apply=False
        )
        assert outcome.changed is True
        assert outcome.detail == "would chmod +x (dry-run)"
        # Dry-run wrote nothing: still not executable, no backup.
        assert os.access(script, os.X_OK) is False
        assert not backup_root.exists()

    def test_idempotent_already_executable(self, tmp_path: Path):
        root = _make_plugin(tmp_path)
        _script(root, "scripts/ok.py", shebang=True, executable=True)
        backup_root = cpv_codemod._backup_dir(root)
        outcome = cpv_codemod._apply_fix_chmod_exec(
            {"file": "scripts/ok.py"}, root, backup_root, set(), apply=True
        )
        assert outcome.changed is False
        assert outcome.detail == "skip (already executable)"

    def test_skips_vendored_file(self, tmp_path: Path):
        root = _make_plugin(tmp_path)
        script = _script(root, "external/lib.py", shebang=True, executable=False)
        backup_root = cpv_codemod._backup_dir(root)
        outcome = cpv_codemod._apply_fix_chmod_exec(
            {"file": "external/lib.py"}, root, backup_root, set(), apply=True
        )
        assert outcome.changed is False
        assert outcome.detail == "skip (vendored)"
        assert os.access(script, os.X_OK) is False

    def test_skips_missing_file(self, tmp_path: Path):
        root = _make_plugin(tmp_path)
        backup_root = cpv_codemod._backup_dir(root)
        outcome = cpv_codemod._apply_fix_chmod_exec(
            {"file": "scripts/ghost.py"}, root, backup_root, set(), apply=True
        )
        assert outcome.changed is False
        assert outcome.detail == "skip (file not found)"


# ── CLI: apply --json dispatch ────────────────────────────────────────────────


class TestApplyJsonCli:
    """`cpv-codemod apply --json <report>` dispatches fixable findings by fix_id."""

    def test_dispatch_table_has_chmod_exec(self):
        assert "chmod-exec" in cpv_codemod._FIX_ID_DISPATCH

    def test_dry_run_reports_but_writes_nothing(self, tmp_path: Path, capsys):
        root = _make_plugin(tmp_path)
        script = _script(root, "scripts/hello.py", shebang=True, executable=False)
        findings = _write_findings(tmp_path, [_finding("scripts/hello.py")])
        rc = cpv_codemod.main(["apply", "--plugin", str(root), "--json", str(findings)])
        assert rc == 0
        assert os.access(script, os.X_OK) is False  # nothing written
        assert not (root / ".cpv-codemod-backup").exists()
        out = capsys.readouterr().out
        assert "would chmod +x (dry-run)" in out
        assert "would apply 1 fix" in out

    def test_apply_fixes_mech_and_is_idempotent(self, tmp_path: Path, capsys):
        root = _make_plugin(tmp_path)
        script = _script(root, "scripts/hello.py", shebang=True, executable=False)
        findings = _write_findings(tmp_path, [_finding("scripts/hello.py")])

        # 1st --apply: fixes the file.
        rc = cpv_codemod.main(["apply", "--plugin", str(root), "--json", str(findings), "--apply"])
        assert rc == 0
        assert os.access(script, os.X_OK) is True
        first = capsys.readouterr().out
        assert "chmod +x applied" in first
        assert "applied 1 fix" in first

        # 2nd --apply on the SAME findings: no-op (already executable).
        rc2 = cpv_codemod.main(["apply", "--plugin", str(root), "--json", str(findings), "--apply"])
        assert rc2 == 0
        assert os.access(script, os.X_OK) is True
        second = capsys.readouterr().out
        assert "skip (already executable)" in second
        assert "applied 0 fix" in second

    def test_non_fixable_findings_are_ignored(self, tmp_path: Path, capsys):
        """A NON-fixable finding pointing at a fixable-looking file is never acted on."""
        root = _make_plugin(tmp_path)
        script = _script(root, "scripts/hello.py", shebang=True, executable=False)
        # Same file, same shape — but fixable:false. Apply must ignore it entirely.
        findings = _write_findings(tmp_path, [_finding("scripts/hello.py", fixable=False)])
        rc = cpv_codemod.main(["apply", "--plugin", str(root), "--json", str(findings), "--apply"])
        assert rc == 0
        assert os.access(script, os.X_OK) is False  # untouched
        out = capsys.readouterr().out
        assert "applied 0 fix(es) across 0 fixable finding(s)" in out

    def test_unregistered_fix_id_is_reported_and_skipped(self, tmp_path: Path, capsys):
        root = _make_plugin(tmp_path)
        script = _script(root, "scripts/hello.py", shebang=True, executable=False)
        findings = _write_findings(
            tmp_path,
            [
                _finding("scripts/hello.py", fix_id="chmod-exec"),
                _finding("scripts/other.md", fix_id="not-a-real-fix"),
            ],
        )
        rc = cpv_codemod.main(["apply", "--plugin", str(root), "--json", str(findings), "--apply"])
        assert rc == 0
        assert os.access(script, os.X_OK) is True  # the known one fixed
        out = capsys.readouterr().out
        assert "skip (no transform registered)" in out
        assert "1 with no registered transform" in out

    def test_missing_findings_file_returns_1(self, tmp_path: Path):
        root = _make_plugin(tmp_path)
        rc = cpv_codemod.main(["apply", "--plugin", str(root), "--json", str(tmp_path / "nope.json")])
        assert rc == 1

    def test_apply_without_json_errors(self, tmp_path: Path):
        root = _make_plugin(tmp_path)
        with pytest.raises(SystemExit):
            cpv_codemod.main(["apply", "--plugin", str(root)])
