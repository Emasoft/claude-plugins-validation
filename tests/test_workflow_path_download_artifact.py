#!/usr/bin/env python3
"""TRDD-V7K2QF8M — RC-WORKFLOW-PATH-BROKEN must not flag DOWNLOADED ARTIFACTS.

A CI fan-in job consumes a path that an earlier ``actions/download-artifact``
step materialises at runtime under its ``with.path:`` directory — the path can
NEVER exist in the repo checkout:

    - uses: actions/download-artifact@v8
      with:
        path: reports-in
    - run: uv run python scripts/validate_plugin.py . --merge-report reports-in/light.json --strict

``reports-in/light.json`` is produced on the runner by the download step, not a
broken repo reference. Flagging it "does not exist on disk" is a FP on the
standard matrix-shard fan-in shape (this is CPV's own free-CI Validate job).

The fix suppresses a path token when it resolves UNDER a directory that an
``actions/download-artifact`` step in its SAME job declares via ``with.path:``.
Signal (b) of the issue-#116 fix is blind to this because it only harvests
``run:`` bodies, and a download step is a ``uses:`` step.

Two-sided coverage:
  * FP side  — a ``reports-in/*.json`` literal AND glob consumed after a
    download-artifact ``path: reports-in`` step → no MAJOR.
  * Genuine side — a broken ref OUTSIDE the artifact dir, a ``reports-in/*``
    ref in a job with NO download step, and a cross-job download (per-job
    scoping) all STILL flag MAJOR.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = REPO_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


# ── Helpers ────────────────────────────────────────────────────────────────────


def _make_minimal_plugin(tmp_path: Path, name: str = "test-plugin") -> Path:
    """Create a plugin folder with just the manifest. No workflows, no scripts."""
    p = tmp_path / name
    (p / ".claude-plugin").mkdir(parents=True)
    (p / ".claude-plugin" / "plugin.json").write_text(
        json.dumps(
            {
                "name": name,
                "version": "0.1.0",
                "description": "test",
                "author": {"name": "Tester", "email": "t@example.com"},
                "repository": f"https://github.com/Emasoft/{name}",
            }
        )
    )
    return p


def _write_workflow(plugin_root: Path, name: str, body: str) -> Path:
    wf_dir = plugin_root / ".github" / "workflows"
    wf_dir.mkdir(parents=True, exist_ok=True)
    path = wf_dir / name
    path.write_text(body, encoding="utf-8")
    return path


def _run_validator(plugin_root: Path):
    from cpv_validation_common import ValidationReport
    from validate_plugin import validate_workflow_path_broken

    report = ValidationReport()
    validate_workflow_path_broken(plugin_root, report)
    return report


def _findings(report) -> list:
    return [r for r in report.results if r.level == "MAJOR" and "RC-WORKFLOW-PATH-BROKEN" in r.message]


# ── FP side: downloaded-artifact paths must NOT flag ────────────────────────────


def test_download_artifact_literal_paths_suppressed(tmp_path: Path) -> None:
    """The exact free-CI repro: an aggregate job downloads reports into
    ``reports-in/`` then merges them — every ``reports-in/*.json`` literal is
    produced at runtime, so no MAJOR."""
    plugin = _make_minimal_plugin(tmp_path)
    _write_workflow(
        plugin,
        "ci.yml",
        """\
name: ci
on: [push]
jobs:
  validate:
    runs-on: ubuntu-latest
    steps:
      - name: Download all validate reports
        uses: actions/download-artifact@v8
        with:
          path: reports-in
          merge-multiple: true
      - name: Assert all reports present
        run: |
          for f in reports-in/light.json reports-in/sa-1.json reports-in/sa-2.json; do
            test -f "$f"
          done
      - name: Merge and enforce the strict verdict
        run: cat reports-in/light.json reports-in/sa-1.json reports-in/sa-2.json
""",
    )
    findings = _findings(_run_validator(plugin))
    assert not findings, f"Downloaded-artifact paths must not flag, got: {[f.message for f in findings]}"


def test_download_artifact_glob_suppressed(tmp_path: Path) -> None:
    """A GLOB under the download-artifact dir (``reports-in/*.json``) is also a
    produced path — suppressed before the zero-match glob check → no MAJOR."""
    plugin = _make_minimal_plugin(tmp_path)
    _write_workflow(
        plugin,
        "ci.yml",
        """\
name: ci
on: [push]
jobs:
  validate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/download-artifact@v8
        with:
          path: reports-in
      - run: ls reports-in/*.json
""",
    )
    findings = _findings(_run_validator(plugin))
    assert not findings, f"Downloaded-artifact glob must not flag, got: {[f.message for f in findings]}"


def test_download_artifact_nested_subdir_suppressed(tmp_path: Path) -> None:
    """A token nested deeper under the artifact dir is still recognised as
    produced (prefix match, not exact match)."""
    plugin = _make_minimal_plugin(tmp_path)
    _write_workflow(
        plugin,
        "ci.yml",
        """\
name: ci
on: [push]
jobs:
  validate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/download-artifact@v8
        with:
          path: dl
      - run: cat dl/shard-1/report.json
""",
    )
    findings = _findings(_run_validator(plugin))
    assert not findings, f"Nested downloaded-artifact path must not flag, got: {[f.message for f in findings]}"


# ── Genuine side: real broken refs must STILL flag ──────────────────────────────


def test_broken_ref_outside_artifact_dir_still_flags(tmp_path: Path) -> None:
    """A broken repo reference in the SAME job as a download step, but NOT under
    the artifact dir, STILL flags — the suppression is scoped to the dir."""
    plugin = _make_minimal_plugin(tmp_path)
    (plugin / "scripts").mkdir()
    _write_workflow(
        plugin,
        "ci.yml",
        """\
name: ci
on: [push]
jobs:
  validate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/download-artifact@v8
        with:
          path: reports-in
      - run: python scripts/removed-real-file.py reports-in/light.json
""",
    )
    findings = _findings(_run_validator(plugin))
    assert len(findings) == 1, f"Expected exactly 1 MAJOR, got {len(findings)}: {[f.message for f in findings]}"
    assert "scripts/removed-real-file.py" in findings[0].message


def test_artifact_ref_in_job_without_download_still_flags(tmp_path: Path) -> None:
    """A ``reports-in/*`` reference in a job that has NO download-artifact step
    STILL flags — the directory is not produced there."""
    plugin = _make_minimal_plugin(tmp_path)
    _write_workflow(
        plugin,
        "ci.yml",
        """\
name: ci
on: [push]
jobs:
  lint:
    runs-on: ubuntu-latest
    steps:
      - run: cat reports-in/light.json
""",
    )
    findings = _findings(_run_validator(plugin))
    assert len(findings) == 1, f"Expected exactly 1 MAJOR, got {len(findings)}: {[f.message for f in findings]}"
    assert "reports-in/light.json" in findings[0].message


def test_cross_job_download_does_not_suppress(tmp_path: Path) -> None:
    """FN-safety: a download-artifact step in job A must NOT suppress a
    ``reports-in/*`` reference in job B — artifacts live on one runner, so the
    download must be in the SAME job. jobB's broken ref still flags."""
    plugin = _make_minimal_plugin(tmp_path)
    _write_workflow(
        plugin,
        "ci.yml",
        """\
name: ci
on: [push]
jobs:
  joba:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/download-artifact@v8
        with:
          path: reports-in
      - run: echo done
  jobb:
    runs-on: ubuntu-latest
    steps:
      - run: cat reports-in/light.json
""",
    )
    findings = _findings(_run_validator(plugin))
    jobb_flagged = any("reports-in/light.json" in f.message for f in findings)
    assert jobb_flagged, f"jobB's cross-job ref must still flag, got: {[(f.line, f.message) for f in findings]}"


def test_omitted_path_does_not_over_suppress(tmp_path: Path) -> None:
    """A download-artifact step with NO ``with.path:`` (extracts into the CWD)
    records no directory, so a genuine broken ref in that job STILL flags — the
    conservative choice that never masks a missing file."""
    plugin = _make_minimal_plugin(tmp_path)
    (plugin / "scripts").mkdir()
    _write_workflow(
        plugin,
        "ci.yml",
        """\
name: ci
on: [push]
jobs:
  validate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/download-artifact@v8
      - run: python scripts/gone.py
""",
    )
    findings = _findings(_run_validator(plugin))
    assert len(findings) == 1, f"Expected exactly 1 MAJOR, got {len(findings)}: {[f.message for f in findings]}"
    assert "scripts/gone.py" in findings[0].message
