"""Issue #127 — three validator-check false-positive fixes (validator-LOGIC /
STYLE-PORTABILITY; NOT security-scanner changes).

Every assertion below is TWO-SIDED — the FP clears AND a real positive still
fires:

FP-1 (`_collect_script_refs`): a `scripts/*.py` token inside a `#` comment is
    documentation, not a live invocation, so it must NOT flag as dangling. A
    real `run:`/invocation token (before any `#`) still records.

FP-2 (`validate_no_absolute_paths`): the style/portability rule must honor
    `cpv.exclude_paths` (and VENDORED_DIR_NAMES / .gitmodules) — an absolute
    path under an excluded/vendored subtree is skipped; a NON-excluded absolute
    path anywhere else still fires.

FP-3 (`validate_bin_executables`): a gitignored-AND-untracked bin/ file
    (`.DS_Store`, editor temp) never ships, so its exec bit is irrelevant and
    must be skipped; a TRACKED non-executable script in bin/ still flags
    (v2.126.26 "skip gitignored-AND-untracked" semantics), and a non-git tree
    scans everything.

The git-fixture pattern is modeled on `test_gitignore_evasion_hardening.py`.
"""

from __future__ import annotations

import json
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from cpv_validation_common import (  # noqa: E402
    ValidationReport,
    validate_no_absolute_paths,
)
from validate_plugin import (  # noqa: E402
    _collect_script_refs,
    validate_bin_executables,
)

_needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git not available")


def _git(d: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=d, check=True, capture_output=True, text=True)


def _init_repo(d: Path) -> None:
    _git(d, "init", "-q")
    _git(d, "config", "user.email", "t@t.t")
    _git(d, "config", "user.name", "t")


# ───────────────────────── FP-1 — dangling-ref comment skip ──────────────────


def test_fp1_comment_only_line_records_no_ref() -> None:
    """A full-line `#` comment mentioning a removed script → no dangling ref."""
    text = "        # scripts/validate_plugin.py — it does not exist in scaffolded plugins.\n"
    assert _collect_script_refs(text, "ci.yml") == []


def test_fp1_issue11_comment_records_no_ref() -> None:
    """The exact scaffold comment that seeds the FP → no ref."""
    text = "      # Issue #11: removed local scripts/validate_plugin.py invocation.\n"
    assert _collect_script_refs(text, "release.yml") == []


def test_fp1_real_run_invocation_still_records() -> None:
    """A genuine `run:` invocation of a (removed) script MUST still record."""
    text = "        run: uv run python scripts/gone.py . --strict\n"
    refs = _collect_script_refs(text, "ci.yml")
    assert [r[0] for r in refs] == ["gone.py"]


def test_fp1_mixed_line_records_only_the_run_ref() -> None:
    """On a mixed line only the pre-comment (live) ref records, not the comment one."""
    text = "        run: python scripts/foo.py  # see scripts/bar.py (doc)\n"
    refs = _collect_script_refs(text, "ci.yml")
    assert [r[0] for r in refs] == ["foo.py"]


def test_fp1_hash_inside_quoted_string_is_not_a_comment() -> None:
    """A `#` inside a quoted string is not a comment marker — defensive quote tracking."""
    text = '        run: echo "scripts/x.py # not a comment"\n'
    refs = _collect_script_refs(text, "ci.yml")
    assert [r[0] for r in refs] == ["x.py"]


# ───────────────────────── FP-2 — absolute-path exclude_paths ────────────────


def _write_plugin_json(root: Path, cpv_block: dict | None) -> None:
    root.joinpath(".claude-plugin").mkdir(parents=True, exist_ok=True)
    manifest: dict = {"name": "p", "version": "0.0.1"}
    if cpv_block is not None:
        manifest["cpv"] = cpv_block
    root.joinpath(".claude-plugin", "plugin.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )


def _abs_path_findings(report: ValidationReport) -> list:
    return [
        r
        for r in report.results
        if r.level in ("MINOR", "MAJOR") and "Absolute path found" in r.message
    ]


def test_fp2_excluded_vendored_doc_is_skipped(tmp_path: Path) -> None:
    """An absolute path under a `cpv.exclude_paths` subtree → 0 absolute-path findings."""
    _write_plugin_json(tmp_path, {"exclude_paths": ["skills/amw-shadcn-ui/docs/"]})
    doc = tmp_path / "skills" / "amw-shadcn-ui" / "docs" / "x.mdx"
    doc.parent.mkdir(parents=True, exist_ok=True)
    doc.write_text('import { cn } from "/lib/utils"\n', encoding="utf-8")
    report = ValidationReport()
    validate_no_absolute_paths(tmp_path, report, respect_gitignore=False)
    assert _abs_path_findings(report) == []


def test_fp2_nonexcluded_path_still_fires(tmp_path: Path) -> None:
    """A `/lib/...` absolute path OUTSIDE the excluded subtree still fires.

    NOTE: the same path that the FP clears (`/lib/utils`) is used here in a
    NON-excluded `scripts/run.sh` to prove the skip is scoped to `exclude_paths`,
    not a blanket `/lib/` carve-out. (`/usr/local/bin/...` is deliberately NOT
    used — it is a pre-existing allowlisted system-binary path that the rule
    reports as INFO, independent of issue #127.)
    """
    _write_plugin_json(tmp_path, {"exclude_paths": ["skills/amw-shadcn-ui/docs/"]})
    runner = tmp_path / "scripts" / "run.sh"
    runner.parent.mkdir(parents=True, exist_ok=True)
    runner.write_text('import x from "/lib/utils"\n', encoding="utf-8")
    report = ValidationReport()
    validate_no_absolute_paths(tmp_path, report, respect_gitignore=False)
    assert len(_abs_path_findings(report)) >= 1


def test_fp2_no_config_unchanged(tmp_path: Path) -> None:
    """No `cpv.exclude_paths` → the same `/lib/utils` still flags (no behavior change)."""
    _write_plugin_json(tmp_path, None)
    doc = tmp_path / "skills" / "amw-shadcn-ui" / "docs" / "x.mdx"
    doc.parent.mkdir(parents=True, exist_ok=True)
    doc.write_text('import { cn } from "/lib/utils"\n', encoding="utf-8")
    report = ValidationReport()
    validate_no_absolute_paths(tmp_path, report, respect_gitignore=False)
    assert len(_abs_path_findings(report)) >= 1


def test_fp2_vendored_dir_name_is_skipped(tmp_path: Path) -> None:
    """A VENDORED_DIR_NAMES subtree (e.g. node_modules/) is skipped via the same helper."""
    _write_plugin_json(tmp_path, None)
    vendored = tmp_path / "node_modules" / "pkg" / "x.mjs"
    vendored.parent.mkdir(parents=True, exist_ok=True)
    # `/lib/utils` is a genuine MINOR-class "system absolute path" (NOT an
    # INFO-only system-binary path), so this passes ONLY because node_modules/
    # is vendored-skipped — not because the path is otherwise benign.
    vendored.write_text('import x from "/lib/utils"\n', encoding="utf-8")
    report = ValidationReport()
    validate_no_absolute_paths(tmp_path, report, respect_gitignore=False)
    assert _abs_path_findings(report) == []


# ───────────────────────── FP-3 — bin/ unshipped skip ────────────────────────


def _bin_minor_names(report: ValidationReport) -> list[str]:
    return [
        r.message
        for r in report.results
        if r.level == "MINOR" and "is not executable" in r.message
    ]


@_needs_git
def test_fp3_gitignored_untracked_dsstore_is_skipped_tracked_script_fires(
    tmp_path: Path,
) -> None:
    """`bin/.DS_Store` (untracked+gitignored) skipped; tracked non-exec `bin/x.sh` flags."""
    (tmp_path / ".gitignore").write_text(".DS_Store\n", encoding="utf-8")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / ".DS_Store").write_bytes(b"\x00\x00")  # macOS Finder artifact
    script = bin_dir / "mytool.sh"
    script.write_text("#!/usr/bin/env bash\necho hi\n", encoding="utf-8")
    script.chmod(0o644)  # tracked, NOT executable → must flag
    _init_repo(tmp_path)
    _git(tmp_path, "add", "bin/mytool.sh", ".gitignore")  # .DS_Store NOT added
    _git(tmp_path, "commit", "-qm", "x")

    report = ValidationReport()
    validate_bin_executables(tmp_path, report)
    msgs = _bin_minor_names(report)
    assert any("mytool.sh" in m for m in msgs)  # real positive still fires
    assert not any(".DS_Store" in m for m in msgs)  # FP cleared


@_needs_git
def test_fp3_tracked_gitignored_file_is_still_scanned(tmp_path: Path) -> None:
    """Anti-evasion: a tracked-AND-gitignored bin/ file SHIPS → still scanned/flagged."""
    (tmp_path / ".gitignore").write_text("bin/payload\n", encoding="utf-8")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    payload = bin_dir / "payload"  # no extension → exec candidate
    payload.write_text("#!/bin/sh\necho hi\n", encoding="utf-8")
    payload.chmod(0o644)
    _init_repo(tmp_path)
    _git(tmp_path, "add", "-f", "bin/payload", ".gitignore")  # force-add the ignored file
    _git(tmp_path, "commit", "-qm", "x")

    report = ValidationReport()
    validate_bin_executables(tmp_path, report)
    assert any("payload" in m for m in _bin_minor_names(report))  # ships → still flagged


def test_fp3_non_git_tree_scans_everything(tmp_path: Path) -> None:
    """No `.git` → unshipped is None → present tree IS the artifact → scan all bin/ files."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    junk = bin_dir / ".DS_Store"
    junk.write_bytes(b"\x00\x00")
    junk.chmod(0o644)
    report = ValidationReport()
    validate_bin_executables(tmp_path, report)
    # off-git, the .DS_Store (no extension) is treated as an exec candidate and
    # flagged — no behavior change off-git.
    assert any(".DS_Store" in m for m in _bin_minor_names(report))


@_needs_git
def test_fp3_tracked_executable_script_passes(tmp_path: Path) -> None:
    """A tracked, EXECUTABLE bin/ script is fine — no false MINOR for it."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    script = bin_dir / "ok.sh"
    script.write_text("#!/usr/bin/env bash\necho ok\n", encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    _init_repo(tmp_path)
    _git(tmp_path, "add", "bin/ok.sh")
    _git(tmp_path, "commit", "-qm", "x")
    report = ValidationReport()
    validate_bin_executables(tmp_path, report)
    assert not any("ok.sh" in m for m in _bin_minor_names(report))
