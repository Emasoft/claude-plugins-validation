"""Tests for Phase 4 minor / informational rules + RC-103/104 disposition."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from cpv_validation_common import (  # noqa: E402
    PHASE4_PATTERNS,
    ValidationReport,
    disposition,
    disposition_with_hold,
)
from validate_security import check_phase4_all  # noqa: E402


def _assert_pattern_matches(rule_id: str, text: str) -> None:
    matched = any(
        rid == rule_id and pat.search(text)
        for rid, _sev, pat, _msg in PHASE4_PATTERNS
    )
    assert matched, f"expected {rule_id} match on {text!r}"


def _assert_pattern_does_not_match(rule_id: str, text: str) -> None:
    matched = any(
        rid == rule_id and pat.search(text)
        for rid, _sev, pat, _msg in PHASE4_PATTERNS
    )
    assert not matched, f"expected NO {rule_id} match on {text!r}"


def _make_plugin(tmp_path: Path, files: dict[str, str]) -> Path:
    plugin = tmp_path / "demo"
    (plugin / ".claude-plugin").mkdir(parents=True)
    (plugin / ".claude-plugin" / "plugin.json").write_text(
        '{"name": "demo", "version": "0.0.1", "description": "test"}\n'
    )
    for rel, content in files.items():
        target = plugin / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return plugin


# -----------------------------------------------------------------------------
# RC-85 — License compliance
# -----------------------------------------------------------------------------


class TestRC85License:
    @pytest.mark.parametrize("text", [
        "Copyright 2024 ACME Corp. All Rights Reserved.",
        "Copyright (c) 2025 X. Proprietary.",
        "© 2024 Y. Confidential.",
        "SPDX-License-Identifier: UNLICENSED",
        "SPDX-License-Identifier: NONE",
    ])
    def test_proprietary_or_unlicensed(self, text: str) -> None:
        _assert_pattern_matches("RC-85", text)


# -----------------------------------------------------------------------------
# RC-87 — SSRF / suspicious IP
# -----------------------------------------------------------------------------


class TestRC87SsrfIp:
    @pytest.mark.parametrize("ip", [
        "127.0.0.1",
        "127.5.5.5",
        "10.0.0.1",
        "172.16.0.1",
        "192.168.1.1",
    ])
    def test_loopback_and_private(self, ip: str) -> None:
        _assert_pattern_matches("RC-87", f"requests.get('http://{ip}/api')")

    def test_link_local_outside_imds(self) -> None:
        _assert_pattern_matches("RC-87", "169.254.5.5")

    @pytest.mark.parametrize("text", [
        # v2.46 FP-A — Python float literals must NOT match the IPv4 regex.
        # The previous regex `\b10\.[0-9.]+\b` matched `10.0` because the
        # tail `[0-9.]+` allowed any digit-or-dot run. Real IPv4 needs all
        # four octets. These cases were FPs in 5 of the 7 emasoft plugins.
        "QUICK_CHECK: float = 10.0",
        "max_score: float = 10.0,",
        "weighted_sum += (cat_score.score / 10.0) * weight * 100",
        "weighted_sum += (cat_score.score / 10.0)",
        "(the user's v2.10.0 feature request)",
        "as of v2.10.0):",
        # SemVer-shaped strings that are definitely not IPs
        "version = '10.0'",
        "engines: '>=10.0'",
        # Two-octet strings that look like IPs but aren't
        "192.168 prefix",
        "172.16.x.y prefix",
    ])
    def test_no_fp_on_floats_and_partial_ip_strings(self, text: str) -> None:
        """RC-87 must NOT fire on Python float literals like `10.0`,
        SemVer strings like `v2.10.0`, or partial IP-shaped text.
        Real IPv4 has 4 octets — anything shorter is not an IP."""
        _assert_pattern_does_not_match("RC-87", text)


# -----------------------------------------------------------------------------
# RC-88 — Suspicious TLDs / shorteners / dev tunnels
# -----------------------------------------------------------------------------


class TestRC88SuspiciousTlds:
    @pytest.mark.parametrize("url", [
        "https://malware.tk/x",
        "https://evil.ml/y",
        "https://example.xyz",
    ])
    def test_free_tld(self, url: str) -> None:
        _assert_pattern_matches("RC-88", url)

    @pytest.mark.parametrize("url", [
        "https://bit.ly/abc",
        "https://tinyurl.com/xyz",
        "https://goo.gl/123",
    ])
    def test_shortener(self, url: str) -> None:
        _assert_pattern_matches("RC-88", url)

    @pytest.mark.parametrize("url", [
        "https://abc123.ngrok.io",
        "https://demo.trycloudflare.com",
        "https://x.serveo.net",
    ])
    def test_dev_tunnel(self, url: str) -> None:
        _assert_pattern_matches("RC-88", url)


# -----------------------------------------------------------------------------
# RC-86 — Token cost / resource abuse
# -----------------------------------------------------------------------------


class TestRC86TokenCost:
    def test_long_string_literal(self) -> None:
        long = '"' + ("x" * 5001) + '"'
        _assert_pattern_matches("RC-86", long)

    def test_high_loop_count(self) -> None:
        _assert_pattern_matches("RC-86", "for (let i = 0; i < 100000; i++) { ... }")

    def test_short_string_not_flagged(self) -> None:
        text = '"hello world"'
        matched = any(
            rid == "RC-86" and pat.search(text)
            for rid, _sev, pat, _msg in PHASE4_PATTERNS
        )
        assert not matched


# -----------------------------------------------------------------------------
# RC-103 — Disposition (verdict-tier)
# -----------------------------------------------------------------------------


class TestRC103Disposition:
    @pytest.mark.parametrize("counts,expected", [
        ({"CRITICAL": 2}, "critical"),
        ({"CRITICAL": 5, "MAJOR": 1}, "critical"),
        ({"CRITICAL": 1}, "unsafe"),
        ({"MAJOR": 3}, "unsafe"),
        ({"MAJOR": 5}, "unsafe"),
        ({"MAJOR": 1}, "suspicious"),
        ({"MAJOR": 2}, "suspicious"),
        ({"MINOR": 5}, "risky"),
        ({"MINOR": 10}, "risky"),
        ({"MINOR": 1}, "risky"),
        ({"WARNING": 1}, "risky"),
        ({}, "safe"),
        ({"CRITICAL": 0, "MAJOR": 0, "MINOR": 0, "WARNING": 0}, "safe"),
    ])
    def test_disposition_rules(self, counts: dict[str, int], expected: str) -> None:
        assert disposition(counts) == expected


# -----------------------------------------------------------------------------
# RC-104 — HOLD verdict
# -----------------------------------------------------------------------------


class TestRC104Hold:
    def test_hold_when_high_ambiguous(self) -> None:
        """3 findings, 1 ambiguous → 1/3 → hold."""
        counts = {"MAJOR": 3}
        assert disposition_with_hold(counts, ambiguous_count=1) == "hold"

    def test_no_hold_when_no_ambiguous(self) -> None:
        counts = {"MAJOR": 3}
        assert disposition_with_hold(counts, ambiguous_count=0) == "unsafe"

    def test_no_hold_when_low_ambiguous(self) -> None:
        """6 findings, 1 ambiguous → 1/6 < 1/3 → keep base verdict."""
        counts = {"MAJOR": 6}
        assert disposition_with_hold(counts, ambiguous_count=1) == "unsafe"

    def test_hold_with_zero_findings_ignored(self) -> None:
        """Ambiguous count meaningless when no findings."""
        assert disposition_with_hold({}, ambiguous_count=5) == "safe"


# -----------------------------------------------------------------------------
# End-to-end check_phase4_all
# -----------------------------------------------------------------------------


class TestCheckPhase4All:
    @pytest.mark.skip(
        reason=(
            "Suite-pollution Heisenbug — see TRDD-fa70f9b8. test PASSES locally "
            "and in isolated CI runs; FAILS deterministically when the full "
            "tests/ directory runs in CI Linux. Same pattern as "
            "test_main_verbose_text_output: report.results comes back empty even "
            "though _iter_scannable_files should yield src/cfg.py. The polluter "
            "is suspected to be in the _gi_cache / lru_cache / "
            "_CPV_SELF_SCAN_* module globals but has not been isolated. "
            "DO NOT REMOVE this skip until TRDD-fa70f9b8 status is RESOLVED."
        ),
    )
    def test_phase4_fires_on_real_file(self, tmp_path: Path) -> None:
        plugin = _make_plugin(tmp_path, {
            "src/cfg.py": "BASE_URL = 'https://abc.ngrok.io/api'",
        })
        report = ValidationReport()
        check_phase4_all(plugin, report)
        assert any("RC-88" in r.message for r in report.results)

    def test_clean_plugin_no_phase4_findings(self, tmp_path: Path) -> None:
        plugin = _make_plugin(tmp_path, {
            "src/main.py": "def hello(): return 42\n",
        })
        report = ValidationReport()
        check_phase4_all(plugin, report)
        assert not any(r.message.startswith("RC-") for r in report.results)
