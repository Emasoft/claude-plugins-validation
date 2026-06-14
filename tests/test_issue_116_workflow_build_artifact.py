#!/usr/bin/env python3
"""Issue #116 — RC-WORKFLOW-PATH-BROKEN must not flag mid-job BUILD ARTIFACTS.

A CI job that builds a binary and then runs it references a path that can NEVER
exist in the repo:

    - run: bash scripts/memgrep/stage.sh x86_64-unknown-linux-gnu memgrep-linux-x64
    - run: ./dist/memgrep-linux-x64 --help >/dev/null

`./dist/memgrep-linux-x64` is created by the staging step immediately above, in
the same job. Flagging it as "literal path does not exist on disk" is a FP on
the normal shape of every build/release workflow.

The fix suppresses a literal-path finding when EITHER signal holds:
  (a) the path sits under a conventional BUILD-OUTPUT directory
      (``dist/``, ``build/``, ``target/``, ``out/``, ``bin/``, ``.bin/``,
      ``output/``, ``release/``, ``artifacts/`` — leading ``./`` tolerated); OR
  (b) an EARLIER step in the SAME job plausibly CREATES it — an earlier run:
      mentions the same path, or runs a build/compile/stage command.

Two-sided coverage:
  * FP side — a ``./dist/...`` artifact used after a stage/build step → no MAJOR;
    a non-build-dir path that a LATER step references after an earlier step
    named/built it → no MAJOR.
  * Genuine side — a real broken canonical-entry-point reference (NOT under a
    build dir, NOT created by an earlier same-job step) STILL flags MAJOR.
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


# ── FP side: build artifacts must NOT flag ──────────────────────────────────────


def test_issue_repro_dist_artifact_after_stage_step_not_flagged(tmp_path: Path) -> None:
    """The exact #116 repro: a stage step builds ``./dist/memgrep-linux-x64``,
    a later step runs it → no MAJOR (signal a: build-output dir)."""
    plugin = _make_minimal_plugin(tmp_path)
    (plugin / "scripts" / "memgrep").mkdir(parents=True)
    (plugin / "scripts" / "memgrep" / "stage.sh").write_text("#!/usr/bin/env bash\ncargo build --release\n")
    _write_workflow(
        plugin,
        "ci.yml",
        """\
name: ci
on: [push]
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - name: Stage via the shared release script
        run: bash scripts/memgrep/stage.sh x86_64-unknown-linux-gnu memgrep-linux-x64
      - name: Staged binary runs
        run: ./dist/memgrep-linux-x64 --help >/dev/null
""",
    )
    findings = _findings(_run_validator(plugin))
    assert not findings, f"Build-output-dir artifact must not flag, got: {[f.message for f in findings]}"


def test_build_output_dirs_suppressed(tmp_path: Path) -> None:
    """Every conventional build-output root with a leading ``./`` is recognised
    so a run-only reference under it does not flag (signal a)."""
    plugin = _make_minimal_plugin(tmp_path)
    for d in ("dist", "build", "target", "out", "output", "release", "artifacts"):
        _write_workflow(
            plugin,
            f"{d}.yml",
            f"""\
name: {d}
on: [push]
jobs:
  run-only:
    runs-on: ubuntu-latest
    steps:
      - run: ./{d}/produced-binary --help
""",
        )
    findings = _findings(_run_validator(plugin))
    assert not findings, f"Paths under build-output dirs must not flag, got: {[f.message for f in findings]}"


def test_signal_b_later_step_references_earlier_built_path(tmp_path: Path) -> None:
    """A LATER step references a non-build-dir path that an EARLIER same-job step
    named/built (via a build command) → no MAJOR (signal b)."""
    plugin = _make_minimal_plugin(tmp_path)
    (plugin / "scripts").mkdir()
    _write_workflow(
        plugin,
        "ci.yml",
        """\
name: ci
on: [push]
jobs:
  gen:
    runs-on: ubuntu-latest
    steps:
      - run: |
          mkdir -p generated
          python -m mygen --out generated/bundle.js
      - run: node generated/bundle.js --check
""",
    )
    findings = _findings(_run_validator(plugin))
    # The later `node generated/bundle.js` reference (an earlier step named it)
    # must be suppressed. (The earlier --out line is the creating step; it does
    # not matter here because the path it names is also referenced earlier.)
    later = [f for f in findings if f.line is not None and f.line >= 9]
    assert not later, f"Later ref to an earlier-built path must not flag, got: {[f.message for f in later]}"


def test_signal_b_earlier_make_build_command(tmp_path: Path) -> None:
    """A later step references a non-build-dir path; an earlier same-job step ran
    a ``make`` build command → no MAJOR (signal b, build-command keyword)."""
    plugin = _make_minimal_plugin(tmp_path)
    (plugin / "scripts").mkdir()
    _write_workflow(
        plugin,
        "ci.yml",
        """\
name: ci
on: [push]
jobs:
  gen:
    runs-on: ubuntu-latest
    steps:
      - run: make all
      - run: bash scripts/built-by-make.sh
""",
    )
    findings = _findings(_run_validator(plugin))
    assert not findings, f"Path after a make build step must not flag, got: {[f.message for f in findings]}"


# ── Genuine side: real broken refs must STILL flag ──────────────────────────────


def test_genuine_broken_canonical_ref_still_flags(tmp_path: Path) -> None:
    """A real broken reference to a removed canonical entry-point — NOT under a
    build dir, NOT created by an earlier step — STILL flags MAJOR."""
    plugin = _make_minimal_plugin(tmp_path)
    (plugin / "scripts").mkdir()
    (plugin / "scripts" / "publish.py").write_text("# ok\n")
    _write_workflow(
        plugin,
        "ci.yml",
        """\
name: ci
on: [push]
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - run: python scripts/removed-real-file.py
""",
    )
    findings = _findings(_run_validator(plugin))
    assert len(findings) == 1, f"Expected exactly 1 MAJOR, got {len(findings)}: {[f.message for f in findings]}"
    assert "scripts/removed-real-file.py" in findings[0].message


def test_broken_ref_no_earlier_build_step_still_flags(tmp_path: Path) -> None:
    """A non-build-dir path with an earlier step that does NOT build it (just an
    ``echo``) STILL flags — signal (b) requires a plausible creator."""
    plugin = _make_minimal_plugin(tmp_path)
    (plugin / "scripts").mkdir()
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
      - run: echo hello
      - run: bash scripts/gone.sh
""",
    )
    findings = _findings(_run_validator(plugin))
    assert len(findings) == 1, f"Expected exactly 1 MAJOR, got {len(findings)}: {[f.message for f in findings]}"
    assert "scripts/gone.sh" in findings[0].message


def test_cross_job_creation_does_not_suppress(tmp_path: Path) -> None:
    """FN-safety: a build step in job A must NOT suppress a broken non-build-dir
    reference in job B — the creation must be in the SAME job. Both broken refs
    (jobA's make-target and jobB's run) still flag."""
    plugin = _make_minimal_plugin(tmp_path)
    (plugin / "scripts").mkdir()
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
      - run: make scripts/x.sh
  jobb:
    runs-on: ubuntu-latest
    steps:
      - run: bash scripts/x.sh
""",
    )
    findings = _findings(_run_validator(plugin))
    # jobb's reference has NO earlier same-job build step → must flag.
    jobb_flagged = any("scripts/x.sh" in f.message and f.line is not None and f.line >= 11 for f in findings)
    assert jobb_flagged, f"jobB's cross-job ref must still flag, got: {[(f.line, f.message) for f in findings]}"
