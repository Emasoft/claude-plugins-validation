"""Tests for GitHub issue #143 — the standardize/upgrade half of the jscpd
copy-paste gate-parity fix.

The canonical local pre-push gate (``publish.py --gate``) gains a jscpd
copy-paste check at PARITY with CI's Mega-Linter ``COPYPASTE_JSCPD``. Both sides
read ONE source-of-truth config — ``.jscpd.json`` (threshold 5) — that jscpd
auto-discovers at the repo root. ``standardize`` must provision that file for an
existing adopter plugin so the gate it runs has a config to read.

Under ``--fix`` standardize CREATES ``.jscpd.json`` when absent and LEAVES an
existing one untouched (never clobbers a user's tuned config without
``--force-templates``). The AUDIT (no ``--fix``) path only WARNs — it never
mutates — and additionally surfaces a ``scripts/publish.py`` that predates the
gate.

Every test is two-sided: the provisioning/audit behaviour happens AND the
conservative-direction sibling (audit does not mutate; an existing config is not
overwritten) holds.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from standardize_plugin import (  # noqa: E402
    _CANONICAL_JSCPD_CONFIG,
    _JSCPD_CONFIG_REL,
    AuditItem,
    _render_canonical_jscpd_config,
    audit_jscpd_config,
    fix_missing_files,
    provision_jscpd_config,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_plugin(tmp_path: Path, *, with_publish: bool | str = False) -> Path:
    """Lay down a minimal plugin tree with a valid plugin.json.

    ``with_publish``:
      * False        → no scripts/publish.py.
      * True         → a publish.py that ALREADY carries the jscpd gate.
      * a str        → a publish.py with that exact body (e.g. a pre-gate one).
    """
    root = tmp_path / "plug"
    cp = root / ".claude-plugin"
    cp.mkdir(parents=True)
    (cp / "plugin.json").write_text(
        json.dumps({"name": "plug", "version": "0.1.0", "description": "t", "author": "X"}, indent=2),
        encoding="utf-8",
    )
    if with_publish:
        scripts = root / "scripts"
        scripts.mkdir(parents=True, exist_ok=True)
        if with_publish is True:
            body = '#!/usr/bin/env python3\n# Gate 2b: Copy-paste check (jscpd, parity with CI)\nprint("ok")\n'
        else:
            body = str(with_publish)
        (scripts / "publish.py").write_text(body, encoding="utf-8")
    return root


def _read_jscpd(root: Path) -> dict:
    return json.loads((root / _JSCPD_CONFIG_REL).read_text(encoding="utf-8"))


# ===========================================================================
# Canonical content constant — parses as JSON with threshold 5
# ===========================================================================


def test_canonical_jscpd_content_parses_with_threshold_5():
    """The canonical .jscpd.json content parses as JSON and pins threshold 5."""
    rendered = _render_canonical_jscpd_config()
    data = json.loads(rendered)
    assert data["threshold"] == 5, data
    # The rendered text MUST be exactly what the constant serializes (one source).
    assert data == _CANONICAL_JSCPD_CONFIG
    # And it carries the parity-relevant config: minTokens, the console reporter,
    # and the dev-submodule / fixtures / vendored ignore globs.
    assert data["minTokens"] == 50
    assert data["reporters"] == ["console"]
    assert data["gitignore"] is True
    for glob in ("**/tests_dev/**", "**/fixtures/**", "**/node_modules/**", "**/.git/**"):
        assert glob in data["ignore"], glob


def test_canonical_constant_threshold_is_5():
    """The constant itself (not just the render) pins threshold 5 (negative: not some other value)."""
    assert _CANONICAL_JSCPD_CONFIG["threshold"] == 5
    assert _CANONICAL_JSCPD_CONFIG["threshold"] != 0


# ===========================================================================
# --fix: provision .jscpd.json when absent
# ===========================================================================


def test_fix_provisions_jscpd_when_absent(tmp_path):
    """--fix CREATES .jscpd.json (threshold 5) when it is absent."""
    root = _make_plugin(tmp_path)
    assert not (root / _JSCPD_CONFIG_REL).exists()
    notes = provision_jscpd_config(root, dry_run=False)
    assert notes, "expected a create note"
    assert (root / _JSCPD_CONFIG_REL).is_file()
    assert _read_jscpd(root)["threshold"] == 5


def test_fix_provisioned_file_is_exact_canonical_bytes(tmp_path):
    """The provisioned file is byte-identical to the canonical render (one source of truth)."""
    root = _make_plugin(tmp_path)
    provision_jscpd_config(root, dry_run=False)
    assert (root / _JSCPD_CONFIG_REL).read_text(encoding="utf-8") == _render_canonical_jscpd_config()


# ===========================================================================
# --fix: NEVER overwrite an existing .jscpd.json
# ===========================================================================


def test_fix_does_not_overwrite_existing_jscpd(tmp_path):
    """--fix LEAVES a user's existing .jscpd.json untouched (no clobber without --force-templates)."""
    root = _make_plugin(tmp_path)
    custom = '{\n  "threshold": 12,\n  "minTokens": 99\n}\n'
    (root / _JSCPD_CONFIG_REL).write_text(custom, encoding="utf-8")
    notes = provision_jscpd_config(root, dry_run=False)
    assert notes == [], notes
    # Byte-for-byte preserved — the user's tuned threshold survives.
    assert (root / _JSCPD_CONFIG_REL).read_text(encoding="utf-8") == custom


# ===========================================================================
# AUDIT (dry-run): WARN without mutating
# ===========================================================================


def test_audit_warns_missing_without_creating(tmp_path):
    """Audit (dry-run) surfaces the missing config AND does NOT create the file (negative on mutation)."""
    root = _make_plugin(tmp_path)
    notes = provision_jscpd_config(root, dry_run=True)
    assert any(_JSCPD_CONFIG_REL in n and "missing" in n for n in notes), notes
    assert not (root / _JSCPD_CONFIG_REL).exists(), "audit path must never write the file"


def test_audit_no_finding_when_config_present_and_no_publish(tmp_path):
    """Audit emits nothing when .jscpd.json exists and there is no publish.py (negative)."""
    root = _make_plugin(tmp_path)
    (root / _JSCPD_CONFIG_REL).write_text(_render_canonical_jscpd_config(), encoding="utf-8")
    assert provision_jscpd_config(root, dry_run=True) == []


def test_audit_warns_publish_lacks_gate(tmp_path):
    """Audit surfaces a publish.py that predates the gate (and never mutates it)."""
    pre_gate = '#!/usr/bin/env python3\n# Gate 2: ruff. Gate 3: validate. (no duplication gate)\nprint("publish")\n'
    root = _make_plugin(tmp_path, with_publish=pre_gate)
    # Provide the config so the ONLY finding is the stale-publish.py one.
    (root / _JSCPD_CONFIG_REL).write_text(_render_canonical_jscpd_config(), encoding="utf-8")
    before = (root / "scripts" / "publish.py").read_text(encoding="utf-8")
    notes = provision_jscpd_config(root, dry_run=True)
    assert any("publish.py" in n and "force-templates" in n for n in notes), notes
    # publish.py untouched — audit is read-only.
    assert (root / "scripts" / "publish.py").read_text(encoding="utf-8") == before


def test_audit_no_publish_warn_when_gate_present(tmp_path):
    """A publish.py that ALREADY carries the jscpd gate is not flagged (negative)."""
    root = _make_plugin(tmp_path, with_publish=True)
    (root / _JSCPD_CONFIG_REL).write_text(_render_canonical_jscpd_config(), encoding="utf-8")
    assert provision_jscpd_config(root, dry_run=True) == []


# ===========================================================================
# audit_jscpd_config — the AuditItem wrapper used by run_audit
# ===========================================================================


def test_audit_jscpd_config_warns_when_missing(tmp_path):
    """audit_jscpd_config yields a WARN AuditItem in the 'jscpd' category when the config is missing."""
    root = _make_plugin(tmp_path)
    items = audit_jscpd_config(root)
    assert items and all(it.category == "jscpd" for it in items)
    assert any(it.status == "WARN" and "missing" in it.message for it in items)


def test_audit_jscpd_config_passes_when_canonical(tmp_path):
    """A fully-canonical plugin (config present + gate in publish.py) reports a PASS (negative on WARN)."""
    root = _make_plugin(tmp_path, with_publish=True)
    (root / _JSCPD_CONFIG_REL).write_text(_render_canonical_jscpd_config(), encoding="utf-8")
    items = audit_jscpd_config(root)
    assert items and all(it.category == "jscpd" for it in items)
    assert all(it.status == "PASS" for it in items), [(it.status, it.message) for it in items]


# ===========================================================================
# End-to-end: fix_missing_files provisions .jscpd.json
# ===========================================================================


def test_fix_missing_files_provisions_jscpd(tmp_path):
    """End-to-end: fix_missing_files provisions .jscpd.json under --fix."""
    root = _make_plugin(tmp_path)
    results = [AuditItem("files", ".github/workflows/ci.yml", "MISSING", "ci missing")]
    fix_missing_files(root, results, dry_run=False)
    assert (root / _JSCPD_CONFIG_REL).is_file()
    assert _read_jscpd(root)["threshold"] == 5


def test_fix_missing_files_dry_run_does_not_create_jscpd(tmp_path):
    """fix_missing_files in dry-run reports but never writes .jscpd.json (negative on mutation)."""
    root = _make_plugin(tmp_path)
    results = [AuditItem("files", ".github/workflows/ci.yml", "MISSING", "ci missing")]
    fix_missing_files(root, results, dry_run=True)
    assert not (root / _JSCPD_CONFIG_REL).exists()


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
