#!/usr/bin/env python3
"""Security-audit red-team group F2 (taint self-skip + getattr FP) — RT6.

The central CPV self-validate surfaced CPV ITSELF as INVALID on RC-73 taint
findings of the shape "tainted '<param>' reaches getattr(obj, <tainted attr
name>)" against CPV's OWN benign reflection:

  * scripts/publish.py — a ``__getattr__`` stream-proxy: ``getattr(self._real,
    name)`` (the result is RETURNED, never executed).
  * tests/* — the ubiquitous dynamic-severity dispatch ``getattr(report,
    level)(message, file, line)`` (a fixed set of method names — critical /
    major / minor / nit — selected by a parameter).

Two independent defects combined to make these fire:

  RT6-A (universal FP — ``cpv_taint_engine._check_dynamic_getattr``): the
    dynamic-getattr sink fired on ANY tainted attribute NAME regardless of
    whether the dispatch could execute anything. ``getattr(self._real, name)``
    that merely RETURNS a value on an ORDINARY object is benign — it is the
    ``__getattr__`` proxy idiom every Python codebase uses. The sink now fires
    only when the dispatch can actually execute / control a process, i.e. when
    EITHER the object is a capability module (``getattr(os, x)`` hands out
    ``os.system``) OR the getattr RESULT is immediately invoked
    (``getattr(obj, tainted)(...)`` — the real gadget). A value-returning
    reflection on an ordinary object clears. FN-safe: ``getattr(os, user_input)``
    and ``getattr(obj, tainted)()`` still fire.

  RT6-B (self-scan-skip omission — ``validate_security.check_phase10_taint``):
    Phase 10 was the SOLE phase in the module that did NOT filter its findings
    through ``cpv_self_scan_skip``. So even the ``getattr(report, level)(...)``
    shape — which legitimately still fires (the result IS called) — surfaced on
    CPV's own SHA-verified test files. It now applies the same hash-anchored
    self-skip every other phase uses, so when CPV scans ITSELF its own genuine
    files are exempt. The skip is SHA-gated (``cpv_self_scan_skip`` returns True
    only when the self-scan flag is armed AND the file's SHA256 matches CPV's
    canonical manifest), so a THIRD-PARTY plugin's ``getattr(report, level)()``
    — or a TAMPERED CPV lookalike whose SHA differs — is never skipped.

The group-F plugin gate (``validate_plugin._run_security_execclass_gate``) calls
``check_phase10_taint`` directly, so RT6-B fixes the gate AND the standalone
``security`` subcommand at once — there is no separate gate-only path to patch.

GOVERNING CONTRACT (never-suppress, FN-safe): neither fix mutes a rule, relaxes
``--strict``, nor adds a name/path allow-list. RT6-A removes a genuine
false positive that affected every plugin; RT6-B makes CPV trust its OWN
SHA-verified code via the existing identity-gated mechanism (never spoofable).
Every test below is TWO-SIDED: the benign/self case clears AND the malicious /
third-party gadget still fires.
"""

from __future__ import annotations

import hashlib
import sys
import tempfile
from pathlib import Path

import pytest

REPO = Path(__file__).parent.parent
SCRIPTS_DIR = REPO / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import validate_plugin as vp  # noqa: E402
import validate_security as vs  # noqa: E402
from cpv_taint_engine import analyze_file  # noqa: E402
from cpv_validation_common import ValidationReport, _extract_rule_id  # noqa: E402

# ───────────────────────────────────────────────────────────────────────────
# Fixture builder — a minimal structurally-valid THIRD-PARTY plugin whose ONLY
# variable is the hooks/util.py payload (mirrors test_secaudit_F so the clean
# tree is plugin-gate-VALID on its own and any verdict change is the payload).
# ───────────────────────────────────────────────────────────────────────────

_PLUGIN_JSON = (
    '{{"name": "{name}", "version": "1.0.0", '
    '"description": "Red-team fixture for the RT6 taint getattr self-skip / FP gate."}}'
)

_SKILL_MD = """\
---
name: x
description: A test skill that does a thing. Use when you need to test the thing in a controlled environment for plugin validation purposes.
---

# X skill

## Steps

1. Do the first thing.
2. Do the second thing.
3. Done.
"""


def _build_plugin(tmp_path: Path, name: str, util_py: str) -> Path:
    """Write a minimal valid THIRD-PARTY plugin into ``tmp_path/<name>`` with
    ``hooks/util.py`` set to ``util_py``. Returns the plugin root Path."""
    root = tmp_path / name
    (root / ".claude-plugin").mkdir(parents=True)
    (root / "skills" / "x").mkdir(parents=True)
    (root / "hooks").mkdir(parents=True)
    (root / ".claude-plugin" / "plugin.json").write_text(_PLUGIN_JSON.format(name=name), encoding="utf-8")
    (root / "skills" / "x" / "SKILL.md").write_text(_SKILL_MD, encoding="utf-8")
    (root / "hooks" / "util.py").write_text(util_py, encoding="utf-8")
    (root / "LICENSE").write_text("MIT License\n\nCopyright (c) 2026\n", encoding="utf-8")
    (root / "README.md").write_text(f"# {name}\n\nTest plugin.\n", encoding="utf-8")
    (root / ".gitignore").write_text("__pycache__/\n*.pyc\n.venv/\n", encoding="utf-8")
    return root


# Payloads ────────────────────────────────────────────────────────────────────

# BENIGN — the ``__getattr__`` stream-proxy idiom that tripped publish.py:134.
# The tainted attr NAME (the function parameter) reaches getattr, but the result
# is RETURNED, not invoked, and the object is an ordinary instance attribute, so
# it cannot execute anything. RT6-A clears it.
_PAYLOAD_BENIGN_PROXY = '''\
"""Utility helper module."""
from typing import Any


class Proxy:
    """A __getattr__ delegate to a wrapped stream (publish.py shape)."""

    def __init__(self, real: Any) -> None:
        self._real = real

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)
'''

# MALICIOUS — the real getattr gadget on a CAPABILITY module. The tainted attr
# name selects an os attribute (e.g. "system") even though the result here is
# only bound, not called. RT6-A keeps it firing because the object is dangerous.
_PAYLOAD_GADGET_OS = '''\
"""Utility helper module."""
import os
import sys


def run() -> None:
    """Pull an arbitrary os attribute by attacker-controlled name."""
    attr = sys.argv[1]
    fn = getattr(os, attr)  # noqa
    return fn
'''

# MALICIOUS — the real getattr gadget on an ORDINARY object whose RESULT is
# immediately invoked: getattr(obj, tainted)(). The tainted name selects which
# method runs and it is called right there. RT6-A keeps it firing via the
# result-invoked gate (object is ordinary, so this proves the gate, not the
# dangerous-module branch, catches it).
_PAYLOAD_GADGET_CALLED = '''\
"""Utility helper module."""
import sys


def run(obj) -> None:
    """Invoke an arbitrary method on obj chosen by attacker-controlled name."""
    method = sys.argv[1]
    return getattr(obj, method)()  # noqa
'''

# Source the test suite uses for the genuine "dynamic severity dispatch" shape:
# getattr(report, level)(message, file, line). The result IS called, so RT6-A
# does NOT clear it — only RT6-B's SHA-gated self-skip exempts it, and only for
# CPV's own files. A THIRD-PARTY copy of this shape MUST still fire.
_SRC_DYNAMIC_LEVEL = (
    "def emit(report, level):\n"
    "    getattr(report, level)('m', 'fixture.py', 1)\n"
)


def _taint_sinks(py_source: str) -> list[str]:
    """Run the taint engine on a one-off temp .py and return its finding sinks."""
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "m.py"
        f.write_text(py_source, encoding="utf-8")
        return [fi.sink for fi in analyze_file(f)]


def _exec_class_findings(report: ValidationReport) -> list:
    """Execution-class blocking findings the plugin-gate merge keeps."""
    from cpv_validation_common import _SECURITY_GATE_BUCKETS

    def is_exec(message: str) -> bool:
        rid = _extract_rule_id(message)
        return rid in vp._EXECCLASS_RCE_RULE_IDS or "A" in _SECURITY_GATE_BUCKETS.get(rid, frozenset())

    return [r for r in report.results if r.level in ("CRITICAL", "MAJOR", "MINOR", "NIT") and is_exec(r.message)]


def _has_taint(report: ValidationReport) -> bool:
    """True iff the report carries any RC-73/RC-74 dynamic-getattr taint finding."""
    return any(_extract_rule_id(r.message) in {"RC-73", "RC-74"} for r in report.results)


# ═══════════════════════════════════════════════════════════════════════════
# 1. RT6-A — the dynamic-getattr sink is FP-safe at the ENGINE level.
# ═══════════════════════════════════════════════════════════════════════════


class TestDynamicGetattrFpSafe:
    """``_check_dynamic_getattr`` clears benign value-returning reflection on an
    ordinary object while keeping every real getattr gadget firing."""

    def test_benign_proxy_reflection_clears(self):
        """BENIGN: ``getattr(self._real, name)`` — result returned, ordinary
        object — must NOT fire (the publish.py / proxy-idiom false positive)."""
        sinks = _taint_sinks(
            "class P:\n    def __getattr__(self, name):\n        return getattr(self._real, name)\n"
        )
        assert not any("getattr" in s for s in sinks), sinks

    def test_gadget_on_capability_module_still_fires(self):
        """MALICIOUS: ``getattr(os, attr)`` with tainted attr — capability
        module → still fires even though the result is only bound, not called."""
        sinks = _taint_sinks("import os, sys\nattr = sys.argv[1]\nfn = getattr(os, attr)\n")
        assert any("getattr" in s for s in sinks), sinks

    def test_gadget_result_invoked_on_ordinary_object_still_fires(self):
        """MALICIOUS: ``getattr(obj, tainted)()`` — ordinary object but the
        result is IMMEDIATELY INVOKED → the real gadget still fires (proves the
        result-called gate, not the dangerous-module branch, catches it)."""
        sinks = _taint_sinks("import sys\nm = sys.argv[1]\ngetattr(obj, m)()\n")
        assert any("getattr" in s for s in sinks), sinks

    def test_dynamic_level_dispatch_result_called_still_fires(self):
        """``getattr(report, level)(...)`` — result IS called → RT6-A does NOT
        silence it. This is what makes RT6-B (SHA-gated self-skip) necessary for
        CPV's own files and proves a THIRD-PARTY copy of the shape still fires."""
        sinks = _taint_sinks(_SRC_DYNAMIC_LEVEL)
        assert any("getattr" in s for s in sinks), sinks

    def test_setattr_unaffected_still_fires(self):
        """``setattr(obj, name, v)`` with tainted name — an attribute WRITE has
        no returned value to use as data, so RT6-A's getattr-only gate leaves it
        on the original any-tainted-name contract. Must still fire."""
        sinks = _taint_sinks("import os\nname = os.environ['ATTR']\nsetattr(obj, name, 1)\n")
        assert any("setattr" in s for s in sinks), sinks


# ═══════════════════════════════════════════════════════════════════════════
# 2. RT6-A at the PLUGIN GATE — a third-party plugin's benign proxy clears, a
#    third-party gadget still blocks.
# ═══════════════════════════════════════════════════════════════════════════


class TestDynamicGetattrAtPluginGate:
    """The group-F execution-class gate (which runs check_phase10_taint) clears
    a third-party benign proxy and blocks a third-party getattr gadget."""

    def test_third_party_benign_proxy_clears_at_gate(self, tmp_path):
        """BENIGN third-party: ``getattr(self._real, name)`` proxy → the
        plugin-gate exec pass merges ZERO RC-73/74 taint findings."""
        root = _build_plugin(tmp_path, "rt6-benign-proxy", _PAYLOAD_BENIGN_PROXY)
        report = ValidationReport()
        vp._run_security_execclass_gate(root, report)
        assert not _has_taint(report), (
            "a benign __getattr__ proxy must not produce an RC-73/74 finding at "
            f"the plugin gate; got {[(r.level, r.message[:70]) for r in report.results]}"
        )

    def test_third_party_os_gadget_still_blocks_at_gate(self, tmp_path):
        """MALICIOUS third-party: ``getattr(os, tainted)`` → the plugin-gate
        exec pass STILL merges the RC-73 taint finding (the gadget is not in
        CPV's manifest, so no self-skip applies — FN-safe)."""
        root = _build_plugin(tmp_path, "rt6-os-gadget", _PAYLOAD_GADGET_OS)
        report = ValidationReport()
        vp._run_security_execclass_gate(root, report)
        assert _has_taint(report), (
            "getattr(os, tainted) on a third-party plugin must still fire RC-73 "
            f"at the plugin gate; got {[(r.level, r.message[:70]) for r in report.results]}"
        )
        # And it is genuinely execution-class (Bucket A), so it drives the verdict.
        assert _exec_class_findings(report), "the RC-73 gadget must be an execution-class finding"

    def test_third_party_invoked_gadget_still_blocks_at_gate(self, tmp_path):
        """MALICIOUS third-party: ``getattr(obj, tainted)()`` (result invoked on
        an ordinary object) → still merges RC-73 at the gate."""
        root = _build_plugin(tmp_path, "rt6-called-gadget", _PAYLOAD_GADGET_CALLED)
        report = ValidationReport()
        vp._run_security_execclass_gate(root, report)
        assert _has_taint(report), (
            "getattr(obj, tainted)() must still fire RC-73 at the plugin gate; "
            f"got {[(r.level, r.message[:70]) for r in report.results]}"
        )


# ═══════════════════════════════════════════════════════════════════════════
# 3. RT6-B — check_phase10_taint applies the SHA-gated self-scan-skip.
#    Two-sided: CPV-self SHA-match exempt; SHA-mismatch / third-party still fires.
# ═══════════════════════════════════════════════════════════════════════════


def _arm_self_scan_with_manifest(monkeypatch, plugin_root: Path, manifest: dict[str, str]) -> None:
    """Directly drive validate_security's module-level self-scan state through
    its REAL SHA-match path (``cpv_self_scan_skip``). This bypasses the network
    manifest fetch (only relevant for a non-running spoofed target) and pins an
    exact rel-path → sha256 manifest so the test exercises the genuine
    hash-anchored skip logic, not a stub."""
    monkeypatch.setattr(vs, "_CPV_SELF_SCAN_ACTIVE", True)
    monkeypatch.setattr(vs, "_CPV_SELF_PLUGIN_ROOT", plugin_root.resolve())
    monkeypatch.setattr(vs, "_CPV_SELF_HASH_MANIFEST", dict(manifest))
    monkeypatch.setattr(vs, "_CPV_IS_RUNNING_CPV", True)
    monkeypatch.setattr(vs, "_CPV_SELF_HASH_NOTICE_REPORT", None)


def _sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class TestTaintPhaseSelfSkip:
    """``check_phase10_taint`` exempts CPV's OWN SHA-verified taint patterns and
    nothing else."""

    def test_cpv_self_matching_sha_is_skipped(self, tmp_path, monkeypatch):
        """SELF (SHA matches): a ``getattr(report, level)(...)`` file whose
        SHA256 is in the armed manifest produces ZERO taint findings — CPV
        trusts its own SHA-verified code. (Without RT6-B this fired RC-73.)"""
        root = tmp_path / "cpv-self"
        root.mkdir()
        f = root / "src.py"
        f.write_text(_SRC_DYNAMIC_LEVEL, encoding="utf-8")
        # Sanity: the shape genuinely fires when NOT skipped (RT6-A leaves it).
        assert any("getattr" in s for s in (fi.sink for fi in analyze_file(f)))
        _arm_self_scan_with_manifest(monkeypatch, root, {"src.py": _sha256_of(f)})
        try:
            report = ValidationReport()
            n = vs.check_phase10_taint(root, report)
        finally:
            vs._set_cpv_self_scan(False)
        assert n == 0 and not _has_taint(report), (
            "a CPV-self SHA-verified file must be exempt from Phase 10 taint; "
            f"got {n} finding(s): {[(r.level, r.message[:70]) for r in report.results]}"
        )

    def test_cpv_self_sha_mismatch_still_fires(self, tmp_path, monkeypatch):
        """SELF (SHA does NOT match — tampered / untracked): the same file with
        a WRONG manifest hash is NOT skipped and STILL fires. Proves the skip is
        hash-anchored, never a blanket exemption of the file path or shape."""
        root = tmp_path / "cpv-tampered"
        root.mkdir()
        f = root / "src.py"
        f.write_text(_SRC_DYNAMIC_LEVEL, encoding="utf-8")
        # Manifest carries a DIFFERENT (stale/spoofed) hash for src.py.
        _arm_self_scan_with_manifest(monkeypatch, root, {"src.py": "0" * 64})
        try:
            report = ValidationReport()
            n = vs.check_phase10_taint(root, report)
        finally:
            vs._set_cpv_self_scan(False)
        assert n >= 1 and _has_taint(report), (
            "a SHA-mismatched (tampered/untracked) file must NOT be skipped — "
            f"the taint finding must still fire; got {n} finding(s)"
        )

    def test_third_party_not_in_manifest_still_fires(self, tmp_path, monkeypatch):
        """THIRD-PARTY (not in manifest): with self-scan armed but the file
        absent from the manifest, ``getattr(report, level)(...)`` still fires —
        a third-party plugin can never inherit CPV's self-skip."""
        root = tmp_path / "third-party"
        root.mkdir()
        f = root / "src.py"
        f.write_text(_SRC_DYNAMIC_LEVEL, encoding="utf-8")
        _arm_self_scan_with_manifest(monkeypatch, root, {})  # empty manifest → no match
        try:
            report = ValidationReport()
            n = vs.check_phase10_taint(root, report)
        finally:
            vs._set_cpv_self_scan(False)
        assert n >= 1 and _has_taint(report), (
            "a file absent from the manifest must never be self-skipped; "
            f"the taint finding must still fire; got {n} finding(s)"
        )

    def test_self_skip_does_not_leak_when_disarmed(self, tmp_path):
        """When self-scan is DISARMED (the default for every external plugin
        scan), ``cpv_self_scan_skip`` is a no-op and a taint finding fires
        normally — the skip cannot leak into a subsequent external scan."""
        vs._set_cpv_self_scan(False)  # explicit: disarmed
        root = tmp_path / "external"
        root.mkdir()
        f = root / "src.py"
        f.write_text(_SRC_DYNAMIC_LEVEL, encoding="utf-8")
        report = ValidationReport()
        n = vs.check_phase10_taint(root, report)
        assert n >= 1 and _has_taint(report), "disarmed self-scan must not suppress taint findings"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v", "-p", "no:cacheprovider"]))
