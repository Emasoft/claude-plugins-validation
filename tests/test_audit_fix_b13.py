"""Regression tests for audit batch b13 fixes in scripts/cpv_codemod.py.

Covers three findings from the full-audit report:

* #62 — ``add-standard-sections`` was silently omitted from the ``all``
  subcommand even though it is a valid standalone subcommand and the
  module docstring lists it among the transforms ``all`` runs.
* #63 — the ``all`` subcommand discarded every per-subcommand exit code
  and unconditionally returned 0, so a failing subcommand could not fail
  ``all``.
* #64 — ``_apply_external_skip_list`` crashed with an unhandled
  ``JSONDecodeError`` / ``OSError`` when ``plugin.json`` was malformed,
  aborting the whole ``all`` run with a traceback.

Each test asserts the corrected behavior AND includes a guard that would
have failed against the pre-fix code.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Probes/tests must not read the scan cache (project convention).
os.environ.setdefault("CPV_SCAN_CACHE", "0")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import cpv_codemod  # noqa: E402


# ── #62: add-standard-sections is part of `all` ──────────────────────────────
def test_all_subcommand_includes_add_standard_sections(monkeypatch, tmp_path):
    """`all` dispatches add-standard-sections (finding #62)."""
    (tmp_path / "README.md").write_text("# hi\n", encoding="utf-8")
    dispatched: list[str] = []

    def record(transform, plugin_root, apply, min_toc_lines):  # noqa: ANN001, ANN202
        dispatched.append(transform)
        return 0

    monkeypatch.setattr(cpv_codemod, "_run_subcommand", record)
    rc = cpv_codemod.main(["all", "--plugin", str(tmp_path)])
    assert rc == 0
    # Guard: pre-fix this list lacked 'add-standard-sections'.
    assert "add-standard-sections" in dispatched
    # Every parser choice except the umbrella 'all' must be reachable via 'all'.
    standalone = {
        c
        for action in cpv_codemod._build_parser()._actions
        if getattr(action, "dest", "") == "subcommand"
        for c in (action.choices or [])
        if c != "all"
    }
    assert standalone.issubset(set(dispatched)), standalone - set(dispatched)


# ── #63: `all` propagates the worst per-subcommand exit code ──────────────────
def test_all_subcommand_propagates_failure_exit_code(monkeypatch, tmp_path):
    """A failing subcommand makes `all` return non-zero (finding #63)."""
    (tmp_path / "README.md").write_text("# hi\n", encoding="utf-8")

    def one_fails(transform, plugin_root, apply, min_toc_lines):  # noqa: ANN001, ANN202
        # external-skip-list fails; everything else succeeds.
        return 1 if transform == "external-skip-list" else 0

    monkeypatch.setattr(cpv_codemod, "_run_subcommand", one_fails)
    rc = cpv_codemod.main(["all", "--plugin", str(tmp_path)])
    # Guard: pre-fix this returned 0 unconditionally.
    assert rc == 1


def test_all_subcommand_returns_zero_when_every_sub_succeeds(monkeypatch, tmp_path):
    """`all` still returns 0 when no subcommand fails (no regression)."""
    (tmp_path / "README.md").write_text("# hi\n", encoding="utf-8")
    monkeypatch.setattr(
        cpv_codemod,
        "_run_subcommand",
        lambda *a, **k: 0,
    )
    assert cpv_codemod.main(["all", "--plugin", str(tmp_path)]) == 0


# ── #64: malformed plugin.json yields a structured failure, not a crash ───────
def _write_manifest(plugin_root: Path, body: str) -> None:
    (plugin_root / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    (plugin_root / ".claude-plugin" / "plugin.json").write_text(body, encoding="utf-8")


def test_external_skip_list_malformed_json_no_crash(tmp_path):
    """Malformed plugin.json -> ok=False, no exception (finding #64)."""
    _write_manifest(tmp_path, "{ not valid json ")
    # Guard: pre-fix this raised json.JSONDecodeError instead of returning.
    result = cpv_codemod._apply_external_skip_list(tmp_path, apply=False)
    assert result.ok is False
    assert result.changed is False
    assert "plugin.json" in result.summary


def test_external_skip_list_malformed_json_returns_failure_exit(tmp_path):
    """_run_subcommand surfaces the malformed-manifest failure as exit 1."""
    _write_manifest(tmp_path, "}}} totally broken")
    rc = cpv_codemod._run_subcommand(
        "external-skip-list", tmp_path, apply=False, min_toc_lines=50
    )
    assert rc == 1


def test_all_run_does_not_abort_on_malformed_manifest(tmp_path):
    """A malformed manifest fails `all` cleanly (non-zero) without a traceback.

    The markdown transforms run first; external-skip-list then reports a
    structured failure instead of crashing mid-run (findings #63 + #64).
    """
    _write_manifest(tmp_path, "{ broken")
    (tmp_path / "README.md").write_text("# hi\n\n\n\n\nbody\n", encoding="utf-8")
    rc = cpv_codemod.main(["all", "--plugin", str(tmp_path)])
    # Non-zero because external-skip-list could not read the manifest,
    # but the call returns normally (no uncaught exception).
    assert rc == 1


def test_external_skip_list_valid_manifest_still_succeeds(tmp_path):
    """A well-formed manifest with no vendored dirs is a clean ok=True no-op."""
    _write_manifest(tmp_path, '{\n  "name": "demo"\n}\n')
    result = cpv_codemod._apply_external_skip_list(tmp_path, apply=False)
    assert result.ok is True
    assert result.changed is False
