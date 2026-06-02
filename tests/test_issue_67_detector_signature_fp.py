#!/usr/bin/env python3
"""Two-sided regression tests for GitHub issue #67 — validate_security's code/CLI
RC rules false-firing on a security PLUGIN's own regex SIGNATURES (the "validator
scanning a validator" meta-FP).

RC-46 (security-disabling CLI arg) and RC-87 (RFC-1918 IP) matched the CONTENT of a
detector's ``re.compile(r"…")`` signature. The fix skips them when the match sits
inside a RAW-STRING literal — false-negative-safe, because a REAL CLI arg / hardcoded
IP is a NORMAL string, never a raw string.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import cpv_validation_common as cvc  # noqa: E402
import validate_security as vs  # noqa: E402


def _rc_fires(py_line: str, rule_id: str) -> bool:
    """Run every validate_security phase over a synthetic .py and report whether
    ``rule_id`` is emitted."""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        (root / ".claude-plugin").mkdir()
        (root / ".claude-plugin" / "plugin.json").write_text('{"name":"p","version":"1.0.0"}')
        (root / "scripts").mkdir()
        (root / "scripts" / "f.py").write_text(py_line + "\n")
        report = cvc.ValidationReport()
        for name, fn in vars(vs).items():
            if name.startswith("check_phase") and callable(fn):
                try:
                    fn(root, report)
                except Exception:  # noqa: BLE001 — best-effort across phases
                    pass
        return any(rule_id in (r.message or "") for r in report.results)


class TestRc46DetectorSignature:
    def test_insecure_flag_in_regex_signature_not_flagged(self) -> None:
        """--insecure inside a re.compile(r"…") signature is the detector's needle."""
        assert not _rc_fires('_X = re.compile(r"verify=False|--insecure|--no-sandbox")', "RC-46")

    def test_real_no_sandbox_subprocess_arg_still_flags(self) -> None:
        """A real --no-sandbox subprocess arg (NORMAL string) still fires RC-46."""
        assert _rc_fires('subprocess.run(["chrome", "--no-sandbox", url])', "RC-46")


class TestRc87DetectorSignature:
    def test_rfc1918_in_regex_signature_not_flagged(self) -> None:
        """An RFC-1918 IP inside a re.compile(r"…") signature is the detector's needle."""
        assert not _rc_fires(r'_IP = re.compile(r"10\.\d+\.\d+\.\d+|192\.168\.\d+\.\d+")', "RC-87")

    def test_real_hardcoded_private_ip_still_flags(self) -> None:
        """A real hardcoded private IP (NORMAL string) still fires RC-87."""
        assert _rc_fires('BACKEND = "10.0.0.5:8080"', "RC-87")
