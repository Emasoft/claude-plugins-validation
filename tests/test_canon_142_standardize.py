"""Tests for GitHub issue #142 standardize defects #2 (dev-extra provisioning)
and #4 (superseded validate.yml removal).

Defect #2 — the canonical ci.yml / release.yml run ``uv sync --extra dev``, but
``standardize`` only WARNED when the adopting plugin's pyproject lacked a
``[project.optional-dependencies].dev`` table with pytest/ruff/mypy — it never
provisioned it, so the plugin failed CI with
"Extra `dev` is not defined in the project's `optional-dependencies` table".
Under ``--fix`` standardize now AUTO-PROVISIONS the dev extra (and AUGMENTS a
partial one), while the AUDIT (no ``--fix``) path still only WARNs.

Defect #4 — standardize adds the consolidated ci.yml (whose Validate job
replaces the standalone "Plugin Validation" validate.yml) but left the
superseded validate.yml in place; ci.yml's actionlint Lint job then tripped on
validate.yml's pre-existing SC2086. Standardize now removes a CPV-shipped
validate.yml (identity-guarded) and emits a branch-protection follow-up note —
never an unrelated user workflow.

Every test is two-sided: the FP/defect clears AND the conservative-direction
sibling (audit does not mutate; an unrelated workflow is never removed) holds.
"""

from __future__ import annotations

import json
import sys
import tomllib
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from standardize_plugin import (  # noqa: E402
    _PROVISION_DEV_EXTRA,
    AuditItem,
    _canonical_dev_extras_missing,
    _is_cpv_shipped_validate_yml,
    audit_pyproject,
    fix_missing_files,
    provision_dev_extra,
    remove_superseded_validate_yml,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_pyproject(root: Path, body: str) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "pyproject.toml").write_text(body, encoding="utf-8")


def _dev_names(root: Path) -> set[str]:
    """Return the lowercased PEP-508 names declared in the dev extra."""
    data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    dev = data.get("project", {}).get("optional-dependencies", {}).get("dev", [])
    names: set[str] = set()
    for spec in dev:
        names.add(spec.split(">")[0].split("=")[0].split("<")[0].split("[")[0].split(";")[0].strip().lower())
    return names


def _make_plugin(tmp_path: Path, *, dev: list[str] | None, with_canonical_workflow: bool = False) -> Path:
    """Lay down a minimal plugin tree. ``dev=None`` → no dev extra at all."""
    root = tmp_path / "plug"
    cp = root / ".claude-plugin"
    cp.mkdir(parents=True)
    (cp / "plugin.json").write_text(
        json.dumps({"name": "plug", "version": "0.1.0", "description": "t", "author": "X"}, indent=2),
        encoding="utf-8",
    )
    if dev is None:
        _write_pyproject(root, '[project]\nname = "plug"\nversion = "0.1.0"\ndependencies = []\n')
    else:
        dev_list = "[" + ", ".join(f'"{s}"' for s in dev) + "]"
        _write_pyproject(
            root,
            f'[project]\nname = "plug"\nversion = "0.1.0"\n'
            f"[project.optional-dependencies]\ndev = {dev_list}\n",
        )
    if with_canonical_workflow:
        wf = root / ".github" / "workflows"
        wf.mkdir(parents=True)
        (wf / "ci.yml").write_text("name: CI\non: [push]\njobs: {}\n", encoding="utf-8")
    return root


_CPV_VALIDATE_YML = (
    "name: Plugin Validation\n"
    "on: [push, pull_request]\n"
    "jobs:\n"
    "  validate:\n"
    "    runs-on: ubuntu-latest\n"
    "    steps:\n"
    "      - uses: actions/checkout@v5\n"
    "      - run: uvx cpv-remote-validate plugin . --strict\n"
    '      - run: echo "done" >> $GITHUB_STEP_SUMMARY\n'
)

_CI_YML = (
    "name: CI\n"
    "on: [push, pull_request]\n"
    "jobs:\n"
    "  validate:\n"
    "    name: Validate\n"
    "    runs-on: ubuntu-latest\n"
    "    steps:\n"
    "      - run: uvx cpv-remote-validate plugin . --strict\n"
)


def _make_plugin_with_workflows(tmp_path: Path, *, validate_yml: str | None, ci: bool = True) -> Path:
    root = tmp_path / "plug"
    cp = root / ".claude-plugin"
    cp.mkdir(parents=True)
    (cp / "plugin.json").write_text(
        json.dumps({"name": "plug", "version": "0.1.0", "description": "t", "author": "X"}, indent=2),
        encoding="utf-8",
    )
    wf = root / ".github" / "workflows"
    wf.mkdir(parents=True)
    if ci:
        (wf / "ci.yml").write_text(_CI_YML, encoding="utf-8")
    if validate_yml is not None:
        (wf / "validate.yml").write_text(validate_yml, encoding="utf-8")
    return root


# ===========================================================================
# Defect #2 — _canonical_dev_extras_missing: absent dev extra → ALL missing
# ===========================================================================


def test_d2_missing_reports_all_when_no_dev_extra(tmp_path):
    """A pyproject with NO optional-dependencies table → every canonical tool missing."""
    root = _make_plugin(tmp_path, dev=None)
    missing = _canonical_dev_extras_missing(root)
    assert set(missing) == {"pytest", "ruff", "mypy"}, missing


def test_d2_missing_reports_all_when_table_but_no_dev_key(tmp_path):
    """Table present (only a `docs` extra) but no `dev` key → all canonical missing."""
    root = tmp_path / "plug"
    _write_pyproject(
        root,
        '[project]\nname = "plug"\nversion = "0.1.0"\n'
        '[project.optional-dependencies]\ndocs = ["sphinx>=7"]\n',
    )
    assert set(_canonical_dev_extras_missing(root)) == {"pytest", "ruff", "mypy"}


def test_d2_missing_empty_when_no_pyproject(tmp_path):
    """No pyproject.toml → nothing to reconcile (negative)."""
    assert _canonical_dev_extras_missing(tmp_path) == []


def test_d2_missing_partial(tmp_path):
    """A dev extra with only ruff → mypy + pytest reported missing (negative on ruff)."""
    root = _make_plugin(tmp_path, dev=["ruff>=0.14.0"])
    missing = _canonical_dev_extras_missing(root)
    assert "ruff" not in missing
    assert "mypy" in missing and "pytest" in missing


# ===========================================================================
# Defect #2 — provision_dev_extra: provision / augment / preserve
# ===========================================================================


def test_d2_provision_creates_dev_extra_when_absent(tmp_path):
    """--fix provisions a fresh dev extra superset of {pytest,ruff,mypy} when none exists."""
    root = _make_plugin(tmp_path, dev=None)
    notes = provision_dev_extra(root, dry_run=False)
    assert notes, "expected a provisioning change note"
    assert {"pytest", "ruff", "mypy"} <= _dev_names(root)


def test_d2_provision_uses_exact_generator_literal_order(tmp_path):
    """The provisioned list matches the generator's literal, in order.

    RC-9 (2026-07-13 CI forensics) added a FOURTH, CONDITIONAL entry to
    ``_PROVISION_DEV_EXTRA``: ``pytest-split``, provisioned ONLY when a workflow
    runs a sharded pytest (``pytest … --splits``). The canonical trio is
    unchanged, and the second half of this test is the positive control for the
    conditionality — this fixture ships no sharded workflow, so the emitted dev
    extra is still EXACTLY ['pytest','ruff','mypy'] and no dependency the plugin
    does not use is invented.
    """
    assert _PROVISION_DEV_EXTRA == ("pytest", "ruff", "mypy", "pytest-split")
    root = _make_plugin(tmp_path, dev=None)
    provision_dev_extra(root, dry_run=False)
    data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    dev = data["project"]["optional-dependencies"]["dev"]
    # Unpinned, exact order, exact membership — byte-compatible with the generator
    # default. NOT sharded ⇒ pytest-split is correctly absent.
    assert dev == ["pytest", "ruff", "mypy"], dev


def test_d2_provision_augments_partial_and_preserves_existing(tmp_path):
    """A partial dev extra is AUGMENTED — missing tools added, existing entry + pin preserved."""
    root = _make_plugin(tmp_path, dev=["ruff>=0.14.0"])
    notes = provision_dev_extra(root, dry_run=False)
    assert notes
    text = (root / "pyproject.toml").read_text(encoding="utf-8")
    assert "ruff>=0.14.0" in text, "existing pinned entry must be preserved verbatim"
    assert {"pytest", "ruff", "mypy"} <= _dev_names(root)


def test_d2_provision_preserves_other_extras_and_tables(tmp_path):
    """Provisioning never disturbs other extras or other TOML tables."""
    root = tmp_path / "plug"
    _write_pyproject(
        root,
        '[project]\nname = "plug"\nversion = "0.1.0"\n'
        "[project.optional-dependencies]\n"
        'docs = ["sphinx>=7"]\n'
        'dev = [\n    "ruff>=0.14.0",\n]\n'
        "\n[tool.ruff]\nline-length = 120\n",
    )
    provision_dev_extra(root, dry_run=False)
    text = (root / "pyproject.toml").read_text(encoding="utf-8")
    assert "sphinx>=7" in text, "other extra dropped"
    assert "[tool.ruff]" in text and "line-length = 120" in text, "other table dropped"
    assert "ruff>=0.14.0" in text
    assert {"pytest", "ruff", "mypy"} <= _dev_names(root)
    # Still valid TOML
    tomllib.loads(text)


def test_d2_provision_noop_when_complete(tmp_path):
    """A complete dev extra is left untouched (negative — no mutation)."""
    root = _make_plugin(tmp_path, dev=["pytest", "ruff", "mypy"])
    before = (root / "pyproject.toml").read_text(encoding="utf-8")
    assert provision_dev_extra(root, dry_run=False) == []
    assert (root / "pyproject.toml").read_text(encoding="utf-8") == before


def test_d2_provision_noop_when_no_pyproject(tmp_path):
    """No pyproject → nothing to provision (negative)."""
    assert provision_dev_extra(tmp_path, dry_run=False) == []


def test_d2_provision_dry_run_does_not_mutate(tmp_path):
    """--dry-run reports the intended change but never writes pyproject (negative on mutation)."""
    root = _make_plugin(tmp_path, dev=None)
    before = (root / "pyproject.toml").read_text(encoding="utf-8")
    notes = provision_dev_extra(root, dry_run=True)
    assert notes and "dry-run" in notes[0].lower()
    assert (root / "pyproject.toml").read_text(encoding="utf-8") == before


# ===========================================================================
# Defect #2 — fix_missing_files provisions; audit does NOT mutate
# ===========================================================================


def test_d2_fix_provisions_when_workflow_emitted(tmp_path):
    """fix_missing_files (the --fix path) provisions the dev extra when ci.yml is emitted."""
    root = _make_plugin(tmp_path, dev=None)
    results = [AuditItem("files", ".github/workflows/ci.yml", "MISSING", "ci missing")]
    fix_missing_files(root, results, dry_run=False)
    assert {"pytest", "ruff", "mypy"} <= _dev_names(root)


def test_d2_audit_warns_but_does_not_mutate(tmp_path):
    """The AUDIT path (audit_pyproject) WARNs about the missing dev extra and never mutates."""
    root = _make_plugin(tmp_path, dev=None, with_canonical_workflow=True)
    before = (root / "pyproject.toml").read_text(encoding="utf-8")
    items = audit_pyproject(root)
    # pyproject is unchanged — audit is read-only
    assert (root / "pyproject.toml").read_text(encoding="utf-8") == before
    # …and a WARN was emitted for the dev extra
    dev_warns = [
        it
        for it in items
        if it.status == "WARN" and "dev" in it.name and "missing CI tools" in it.message
    ]
    assert dev_warns, [(it.name, it.status, it.message) for it in items]


def test_d2_audit_no_warn_without_canonical_workflow(tmp_path):
    """A plugin not using the canonical pipeline is NOT flagged for a missing dev extra (negative)."""
    root = _make_plugin(tmp_path, dev=None, with_canonical_workflow=False)
    items = audit_pyproject(root)
    assert not [it for it in items if it.status == "WARN" and "missing CI tools" in it.message]


# ===========================================================================
# Defect #4 — superseded validate.yml removal (identity-guarded, safe-delete)
# ===========================================================================


def test_d4_identity_matches_cpv_validate_yml(tmp_path):
    """The identity guard recognises a CPV-shipped 'Plugin Validation' validate.yml."""
    wf = tmp_path / ".github" / "workflows"
    wf.mkdir(parents=True)
    p = wf / "validate.yml"
    p.write_text(_CPV_VALIDATE_YML, encoding="utf-8")
    assert _is_cpv_shipped_validate_yml(p) is True


def test_d4_identity_rejects_unrelated_validate_yml(tmp_path):
    """The identity guard does NOT match an unrelated workflow named validate.yml (negative)."""
    wf = tmp_path / ".github" / "workflows"
    wf.mkdir(parents=True)
    p = wf / "validate.yml"
    p.write_text(
        "name: Schema Validation\non: [push]\njobs:\n  s:\n    runs-on: ubuntu-latest\n"
        "    steps:\n      - run: ajv validate -s schema.json -d data.json\n",
        encoding="utf-8",
    )
    assert _is_cpv_shipped_validate_yml(p) is False


def test_d4_identity_rejects_cmd_without_name(tmp_path):
    """A validate workflow that runs the CPV command but is NOT named like a CPV validate is not matched."""
    wf = tmp_path / ".github" / "workflows"
    wf.mkdir(parents=True)
    p = wf / "validate.yml"
    p.write_text(
        "name: My Custom Pipeline\non: [push]\njobs:\n  v:\n    runs-on: ubuntu-latest\n"
        "    steps:\n      - run: uvx cpv-remote-validate plugin . --strict\n",
        encoding="utf-8",
    )
    # Conservative: BOTH a CPV command AND a CPV-validate name are required.
    assert _is_cpv_shipped_validate_yml(p) is False


def test_d4_removes_superseded_validate_yml_and_emits_branch_note(tmp_path):
    """A CPV validate.yml is removed (moved to safe-delete location) + branch note emitted."""
    root = _make_plugin_with_workflows(tmp_path, validate_yml=_CPV_VALIDATE_YML, ci=True)
    notes = remove_superseded_validate_yml(root, dry_run=False)
    assert notes
    assert any("removed superseded" in n for n in notes), notes
    assert any("branch protection" in n.lower() for n in notes), notes
    assert not (root / ".github" / "workflows" / "validate.yml").is_file()
    # Safe-delete: moved, not hard-deleted.
    assert (root / "scripts_dev" / "superseded-workflows" / "validate.yml").is_file()


def test_d4_does_not_remove_unrelated_validate_yml(tmp_path):
    """An unrelated validate.yml is left in place (negative — never deletes user workflows)."""
    unrelated = (
        "name: Schema Validation\non: [push]\njobs:\n  s:\n    runs-on: ubuntu-latest\n"
        "    steps:\n      - run: ajv validate -s schema.json -d data.json\n"
    )
    root = _make_plugin_with_workflows(tmp_path, validate_yml=unrelated, ci=True)
    assert remove_superseded_validate_yml(root, dry_run=False) == []
    assert (root / ".github" / "workflows" / "validate.yml").is_file()


def test_d4_does_not_remove_when_ci_absent(tmp_path):
    """Without the replacement ci.yml, validate.yml is NOT removed (would strip only validation)."""
    root = _make_plugin_with_workflows(tmp_path, validate_yml=_CPV_VALIDATE_YML, ci=False)
    assert remove_superseded_validate_yml(root, dry_run=False) == []
    assert (root / ".github" / "workflows" / "validate.yml").is_file()


def test_d4_noop_when_no_validate_yml(tmp_path):
    """No validate.yml present → nothing to remove (negative)."""
    root = _make_plugin_with_workflows(tmp_path, validate_yml=None, ci=True)
    assert remove_superseded_validate_yml(root, dry_run=False) == []


def test_d4_dry_run_does_not_remove(tmp_path):
    """--dry-run reports the removal but leaves validate.yml in place (negative on mutation)."""
    root = _make_plugin_with_workflows(tmp_path, validate_yml=_CPV_VALIDATE_YML, ci=True)
    notes = remove_superseded_validate_yml(root, dry_run=True)
    assert notes and "dry-run" in notes[0].lower()
    assert (root / ".github" / "workflows" / "validate.yml").is_file()


def test_d4_fix_missing_files_removes_validate_yml_when_ci_emitted(tmp_path):
    """End-to-end: fix_missing_files removes the superseded validate.yml when ci.yml is emitted."""
    root = _make_plugin_with_workflows(tmp_path, validate_yml=_CPV_VALIDATE_YML, ci=True)
    # A pyproject so provisioning has somewhere to write (not under test here).
    _write_pyproject(root, '[project]\nname = "plug"\nversion = "0.1.0"\n')
    results = [AuditItem("files", ".github/workflows/ci.yml", "MISSING", "ci missing")]
    fix_missing_files(root, results, dry_run=False)
    assert not (root / ".github" / "workflows" / "validate.yml").is_file()


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
