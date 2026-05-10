"""Tests for ``validate_workflow_path_broken`` (RC-WORKFLOW-PATH-BROKEN).

Issue #21 ask #2 — escalate broken-glob / broken-literal-path references in
workflow YAML ``run:`` bodies to MAJOR with file:line citations.

Symptom this rule catches: a canonical-pipeline migration that consolidates
several ``scripts/*.sh`` helpers into ``publish.py`` but leaves the workflow
YAML invoking shellcheck on globs that now match zero files. The workflow
silently passes (shellcheck reports zero issues on zero files), so the
plugin ships with no shellcheck coverage even though CI says "green."

Tests cover:
- All paths valid → 0 findings.
- One missing literal → 1 MAJOR.
- One zero-match glob → 1 MAJOR.
- Mixed valid/invalid → exact MAJOR count.
- Flag tokens, URLs, env-var refs, KEY=VALUE, $(...) NOT flagged.
- ai-maestro-janitor v0.4.1 reproduction: shellcheck on three zero-match
  globs in a tree where only the literals exist → exactly 3 MAJOR.
- Plugin without ``.github/workflows/`` → silent no-op.
- Block scalar ``run: |`` multi-line bodies are scanned line-by-line and
  the line citation matches the offending body line, not the ``run:`` line.
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


# ── Tests ─────────────────────────────────────────────────────────────────────


def test_no_workflows_dir_is_silent_noop(tmp_path: Path) -> None:
    """Plugin without .github/workflows/ → no findings, no exception."""
    plugin = _make_minimal_plugin(tmp_path)
    report = _run_validator(plugin)
    assert not _findings(report), f"Expected silent no-op when workflows dir absent, got: {report.results}"


def test_all_paths_valid_emits_zero_findings(tmp_path: Path) -> None:
    """When every literal and glob in the run: bodies resolves on disk,
    the validator emits zero MAJOR findings.
    """
    plugin = _make_minimal_plugin(tmp_path)
    (plugin / "scripts").mkdir()
    (plugin / "scripts" / "publish.py").write_text("# ok\n")
    (plugin / "scripts" / "lint.py").write_text("# ok\n")
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
      - uses: actions/checkout@v6
      - run: python scripts/publish.py
      - run: python scripts/lint.py
""",
    )
    report = _run_validator(plugin)
    assert not _findings(report), f"All-valid workflow must emit 0 MAJOR, got: {[r.message for r in _findings(report)]}"


def test_one_missing_literal_emits_one_major(tmp_path: Path) -> None:
    """A single ``run:`` body referencing a literal path that doesn't
    exist on disk → exactly one MAJOR with the file:line citation.
    """
    plugin = _make_minimal_plugin(tmp_path)
    # Note: scripts/dispatch.sh is INTENTIONALLY not created.
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
      - run: bash scripts/dispatch.sh
""",
    )
    report = _run_validator(plugin)
    findings = _findings(report)
    assert len(findings) == 1, f"Expected exactly 1 MAJOR, got {len(findings)}: {[f.message for f in findings]}"
    f = findings[0]
    assert f.file == ".github/workflows/ci.yml"
    assert f.line is not None and f.line > 0
    assert "scripts/dispatch.sh" in f.message
    assert "literal path" in f.message


def test_one_zero_match_glob_emits_one_major(tmp_path: Path) -> None:
    """A glob with zero matches → exactly one MAJOR. The whole reason this
    rule exists: a zero-match glob makes shellcheck a silent no-op.
    """
    plugin = _make_minimal_plugin(tmp_path)
    # Note: scripts/detectors/ deliberately empty / absent.
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
      - run: shellcheck scripts/detectors/*.sh
""",
    )
    report = _run_validator(plugin)
    findings = _findings(report)
    assert len(findings) == 1, (
        f"Expected exactly 1 MAJOR for zero-match glob, got {len(findings)}: {[f.message for f in findings]}"
    )
    f = findings[0]
    assert "scripts/detectors/*.sh" in f.message
    assert "zero files" in f.message
    assert "glob" in f.message


def test_mixed_valid_and_invalid_emits_exact_count(tmp_path: Path) -> None:
    """Three references — one valid literal, one missing literal, one
    zero-match glob → exactly 2 MAJOR (NOT 3, NOT 1).
    """
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
      - run: python scripts/publish.py
      - run: bash scripts/missing.sh
      - run: shellcheck scripts/zero/*.sh
""",
    )
    report = _run_validator(plugin)
    findings = _findings(report)
    assert len(findings) == 2, f"Expected exactly 2 MAJOR, got {len(findings)}: {[f.message for f in findings]}"
    msgs = " ".join(f.message for f in findings)
    assert "scripts/missing.sh" in msgs
    assert "scripts/zero/*.sh" in msgs
    assert "scripts/publish.py" not in msgs


def test_flags_urls_envvars_substitutions_not_flagged(tmp_path: Path) -> None:
    """Flag tokens (``-x``), URLs, env-var refs (``$FOO``, ``${{ matrix.x
    }}``), KEY=VALUE assignments, and command substitutions (``$(...)``)
    must NOT be classified as paths and must NOT trigger findings.
    """
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
      - run: |
          curl -fsSL https://example.com/install.sh | bash
          python -x scripts/publish.py
          export FOO=bar
          echo "$FOO ${HOME} ${{ matrix.os }}"
          echo "$(date +%Y)"
""",
    )
    report = _run_validator(plugin)
    findings = _findings(report)
    assert not findings, (
        f"Flags, URLs, env-vars, command substitutions must not be flagged. Got: {[f.message for f in findings]}"
    )


def test_ai_maestro_janitor_v041_reproduction(tmp_path: Path) -> None:
    """Repro of the ai-maestro-janitor v0.4.1 symptom that triggered
    issue #21.

    Workflow: ``shellcheck scripts/dispatch.sh scripts/detectors/*.sh
    scripts/hooks/*.sh scripts/lib/*.sh .githooks/pre-push``

    Tree:
      - ``scripts/dispatch.sh`` exists (literal — should pass).
      - ``.githooks/pre-push`` exists (literal — should pass).
      - ``scripts/detectors/`` does not exist → glob = zero matches.
      - ``scripts/hooks/`` does not exist → glob = zero matches.
      - ``scripts/lib/`` does not exist → glob = zero matches.

    Expected: exactly 3 MAJOR (one per zero-match glob), 0 false-positive
    on the two valid literals.
    """
    plugin = _make_minimal_plugin(tmp_path)
    (plugin / "scripts").mkdir()
    (plugin / "scripts" / "dispatch.sh").write_text("#!/usr/bin/env bash\n")
    (plugin / ".githooks").mkdir()
    (plugin / ".githooks" / "pre-push").write_text("#!/usr/bin/env bash\n")

    _write_workflow(
        plugin,
        "ci.yml",
        """\
name: ci
on: [push]
jobs:
  shellcheck:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v6
      - run: shellcheck scripts/dispatch.sh scripts/detectors/*.sh scripts/hooks/*.sh scripts/lib/*.sh .githooks/pre-push
""",
    )

    report = _run_validator(plugin)
    findings = _findings(report)
    assert len(findings) == 3, (
        f"Expected exactly 3 MAJOR (one per zero-match glob), got {len(findings)}: {[f.message for f in findings]}"
    )
    msgs = " | ".join(f.message for f in findings)
    assert "scripts/detectors/*.sh" in msgs
    assert "scripts/hooks/*.sh" in msgs
    assert "scripts/lib/*.sh" in msgs
    # The two literal paths that DO exist must NOT be flagged.
    for f in findings:
        assert "scripts/dispatch.sh" not in f.message, (
            f"False positive on existing literal scripts/dispatch.sh: {f.message}"
        )
        assert ".githooks/pre-push" not in f.message, (
            f"False positive on existing literal .githooks/pre-push: {f.message}"
        )


def test_for_loop_glob_does_not_attach_semicolon(tmp_path: Path) -> None:
    """A `for x in scripts/hooks/*.py; do` loop must NOT trigger
    RC-WORKFLOW-PATH-BROKEN. shlex.split does not consume ``;`` as a token
    separator (it is a shell metacharacter, not whitespace), so the loop
    header produces the token ``scripts/hooks/*.py;`` — semicolon glued
    on. Without trailing-operator stripping the validator treats that as
    a glob, expands it via Python's ``glob`` module, gets zero matches
    (because no real file ends in ``.py;``), and emits a spurious MAJOR.

    This regression test reproduces the ai-maestro-janitor v0.4.2 publish
    failure where the hook smoke loop and the weekly-audit detector loop
    both tripped this bug despite the underlying glob being valid.
    """
    plugin = _make_minimal_plugin(tmp_path)
    (plugin / "scripts" / "hooks").mkdir(parents=True)
    (plugin / "scripts" / "hooks" / "on-session-start.py").write_text("# ok\n")
    (plugin / "scripts" / "hooks" / "on-stop-failure.py").write_text("# ok\n")
    _write_workflow(
        plugin,
        "ci.yml",
        """\
name: ci
on: [push]
jobs:
  smoke:
    runs-on: ubuntu-latest
    steps:
      - run: |
          rc=0
          for h in scripts/hooks/*.py; do
            timeout 30 ./"$h" || rc=$?
          done
          exit $rc
""",
    )
    report = _run_validator(plugin)
    findings = _findings(report)
    assert not findings, (
        "for-loop with attached `;` and `./\"$h\"` body must produce zero "
        f"findings, got: {[f.message for f in findings]}"
    )


def test_shell_variable_inside_token_not_flagged(tmp_path: Path) -> None:
    """Tokens containing a shell variable reference anywhere — not just
    at the start — must NOT be classified as a literal path.

    ``shlex.split('./"$h"', posix=True)`` returns ``['./$h']``: the
    surrounding quotes are stripped and the variable reference survives
    in the middle of the token. The pre-fix version of
    ``_looks_like_workflow_path`` only excluded tokens that *started*
    with ``$``, so ``./$h`` slipped past and was reported as a missing
    literal path. The fix rejects any token containing ``$`` anywhere.

    Also covers ``${VAR}`` mid-token (``path/to/${VAR}/file.sh``) which
    the pre-fix code would have flagged for the same reason.
    """
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
      - run: |
          h=scripts/publish.py
          ./"$h"
          ./${h}
          bash path/to/${VAR}/file.sh
""",
    )
    report = _run_validator(plugin)
    findings = _findings(report)
    assert not findings, (
        "Tokens containing $VAR anywhere must not be statically validated. "
        f"Got: {[f.message for f in findings]}"
    )


def test_strip_shell_ops_preserves_real_zero_match_globs(tmp_path: Path) -> None:
    """Stripping trailing ``;`` must not mask the ORIGINAL bug the
    validator was built to catch: a glob with NO trailing operator that
    legitimately matches zero files (the canonical migration symptom
    where ``scripts/detectors/*.sh`` survives in the workflow but
    ``scripts/detectors/`` no longer exists).

    Concretely: a clean ``scripts/missing/*.sh`` (no semicolon, no shell
    operator, no variable ref) must still emit a MAJOR. Otherwise the
    bug-fix would be a regression on the validator's whole purpose.
    """
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
  shellcheck:
    runs-on: ubuntu-latest
    steps:
      - run: shellcheck scripts/missing/*.sh
""",
    )
    report = _run_validator(plugin)
    findings = _findings(report)
    assert len(findings) == 1, (
        f"Expected exactly 1 MAJOR for a real zero-match glob, got "
        f"{len(findings)}: {[f.message for f in findings]}"
    )
    assert "scripts/missing/*.sh" in findings[0].message


def test_block_scalar_run_body_line_numbers(tmp_path: Path) -> None:
    """A multi-line ``run: |`` body must be scanned line-by-line and the
    citation must point at the offending body line, NOT the ``run:`` line
    itself. (file:line → grep-correlatable diagnostic).
    """
    plugin = _make_minimal_plugin(tmp_path)
    body = """\
name: ci
on: [push]
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - run: |
          # line 8
          echo hello
          bash scripts/missing-on-line-10.sh
          echo done
"""
    _write_workflow(plugin, "ci.yml", body)
    report = _run_validator(plugin)
    findings = _findings(report)
    assert len(findings) == 1, (
        f"Expected exactly 1 MAJOR for the only broken literal, got {len(findings)}: {[f.message for f in findings]}"
    )
    f = findings[0]
    # The offending line is line 10 of the YAML (1-indexed).
    assert f.line == 10, f"Expected citation at line 10 (the bash line), got line {f.line}"
    assert "scripts/missing-on-line-10.sh" in f.message
