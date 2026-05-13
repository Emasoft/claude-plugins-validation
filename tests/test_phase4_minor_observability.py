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
    matched = any(rid == rule_id and pat.search(text) for rid, _sev, pat, _msg in PHASE4_PATTERNS)
    assert matched, f"expected {rule_id} match on {text!r}"


def _assert_pattern_does_not_match(rule_id: str, text: str) -> None:
    matched = any(rid == rule_id and pat.search(text) for rid, _sev, pat, _msg in PHASE4_PATTERNS)
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
    @pytest.mark.parametrize(
        "text",
        [
            "Copyright 2024 ACME Corp. All Rights Reserved.",
            "Copyright (c) 2025 X. Proprietary.",
            "© 2024 Y. Confidential.",
            "SPDX-License-Identifier: UNLICENSED",
            "SPDX-License-Identifier: NONE",
        ],
    )
    def test_proprietary_or_unlicensed(self, text: str) -> None:
        _assert_pattern_matches("RC-85", text)


# -----------------------------------------------------------------------------
# RC-87 — SSRF / suspicious IP
# -----------------------------------------------------------------------------


class TestRC87SsrfIp:
    @pytest.mark.parametrize(
        "ip",
        [
            "127.0.0.1",
            "127.5.5.5",
            "10.0.0.1",
            "172.16.0.1",
            "192.168.1.1",
        ],
    )
    def test_loopback_and_private(self, ip: str) -> None:
        _assert_pattern_matches("RC-87", f"requests.get('http://{ip}/api')")

    def test_link_local_outside_imds(self) -> None:
        _assert_pattern_matches("RC-87", "169.254.5.5")

    @pytest.mark.parametrize(
        "text",
        [
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
        ],
    )
    def test_no_fp_on_floats_and_partial_ip_strings(self, text: str) -> None:
        """RC-87 must NOT fire on Python float literals like `10.0`,
        SemVer strings like `v2.10.0`, or partial IP-shaped text.
        Real IPv4 has 4 octets — anything shorter is not an IP."""
        _assert_pattern_does_not_match("RC-87", text)


# -----------------------------------------------------------------------------
# RC-88 — Suspicious TLDs / shorteners / dev tunnels
# -----------------------------------------------------------------------------


class TestRC88SuspiciousTlds:
    @pytest.mark.parametrize(
        "url",
        [
            "https://malware.tk/x",
            "https://evil.ml/y",
            "https://example.xyz",
        ],
    )
    def test_free_tld(self, url: str) -> None:
        _assert_pattern_matches("RC-88", url)

    @pytest.mark.parametrize(
        "url",
        [
            "https://bit.ly/abc",
            "https://tinyurl.com/xyz",
            "https://goo.gl/123",
        ],
    )
    def test_shortener(self, url: str) -> None:
        _assert_pattern_matches("RC-88", url)

    @pytest.mark.parametrize(
        "url",
        [
            "https://abc123.ngrok.io",
            "https://demo.trycloudflare.com",
            "https://x.serveo.net",
        ],
    )
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
        matched = any(rid == "RC-86" and pat.search(text) for rid, _sev, pat, _msg in PHASE4_PATTERNS)
        assert not matched


# -----------------------------------------------------------------------------
# RC-103 — Disposition (verdict-tier)
# -----------------------------------------------------------------------------


class TestRC103Disposition:
    @pytest.mark.parametrize(
        "counts,expected",
        [
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
        ],
    )
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
    # TRDD-fa70f9b8 — re-enabled 2026-05-10, hardened 2026-05-13.
    # The conftest autouse fixture `_trdd_fa70f9b8_reset_global_state` resets
    # the known polluters (`_CPV_SELF_SCAN_*`, `_CLASSIFIER_*` module globals
    # + the two `lru_cache`d helpers in cpv_validation_common) BEFORE every
    # test. Locally that's enough. On CI under `-n auto --dist=worksteal`
    # we still hit empty `report.results` ~1/5 runs (TRDD-3199124d-Wave2
    # incident). The test now ALSO performs an explicit, redundant reset
    # immediately before calling `check_phase4_all`, and emits a
    # diagnostic dump of `report.results` on failure so future debugging
    # is one log line away.
    def test_phase4_fires_on_real_file(self, tmp_path: Path) -> None:
        # Belt-and-suspenders reset: re-call the setters in case anything
        # between the autouse fixture and this line touched module state
        # (parametrized prior-class fixtures, xdist worker setup, etc.).
        import validate_security as vs  # noqa: PLC0415

        vs._set_cpv_self_scan(False, plugin_root=None, notice_report=None)
        vs._set_classifier_active(False)

        plugin = _make_plugin(
            tmp_path,
            {
                "src/cfg.py": "BASE_URL = 'https://abc.ngrok.io/api'",
            },
        )
        report = ValidationReport()
        check_phase4_all(plugin, report)

        # On failure, dump the full state so CI logs let us diagnose what
        # leaked. A bare `assert False` would just say "no RC-88 message".
        matched_rc88 = any("RC-88" in r.message for r in report.results)
        if not matched_rc88:
            # Probe the iterator + skip logic to pinpoint the empty-results cause.
            try:
                yielded = [(rel_path, len(content)) for _fp, rel_path, content in vs._iter_scannable_files(plugin)]
            except Exception as e:  # noqa: BLE001
                yielded = f"_iter_scannable_files raised: {e!r}"
            try:
                from cpv_validation_common import get_gitignore_filter  # noqa: PLC0415

                gi = get_gitignore_filter(plugin)
                walk_files = []
                for root, _dirs, files in gi.walk(plugin):
                    for f in files:
                        walk_files.append(str(Path(root, f).relative_to(plugin)))
            except Exception as e:  # noqa: BLE001
                walk_files = f"gi.walk raised: {e!r}"
            # Also probe each per-file skip predicate explicitly.
            cfg_py = plugin / "src" / "cfg.py"
            rel_cfg = "src/cfg.py"
            try:
                skip_result = vs.cpv_self_scan_skip(rel_cfg)
            except Exception as e:  # noqa: BLE001
                skip_result = f"raised: {e!r}"
            try:
                lockfile_result = vs.is_lockfile(rel_cfg)
            except Exception as e:  # noqa: BLE001
                lockfile_result = f"raised: {e!r}"
            try:
                binary_result = vs.is_binary_file(cfg_py) if cfg_py.is_file() else "no file"
            except Exception as e:  # noqa: BLE001
                binary_result = f"raised: {e!r}"
            diag = {
                "result_count": len(report.results),
                "results": [(r.level, r.message[:120]) for r in report.results],
                "self_scan_active": vs._CPV_SELF_SCAN_ACTIVE,
                "self_scan_root": str(vs._CPV_SELF_PLUGIN_ROOT),
                "self_scan_manifest_size": len(vs._CPV_SELF_HASH_MANIFEST),
                "classifier_active": vs._CLASSIFIER_ACTIVE,
                "plugin_dir": str(plugin),
                "plugin_dir_exists": plugin.is_dir(),
                "cfg_py_exists": cfg_py.is_file(),
                "cfg_py_content": cfg_py.read_text(encoding="utf-8") if cfg_py.is_file() else "<file missing>",
                "iter_scannable_yielded": yielded,
                "gi_walk_files": walk_files,
                "skip_cfg_py": skip_result,
                "lockfile_cfg_py": lockfile_result,
                "binary_cfg_py": binary_result,
            }
            pytest.fail(f"RC-88 not in report.results — diagnostic dump: {diag}")

    def test_clean_plugin_no_phase4_findings(self, tmp_path: Path) -> None:
        plugin = _make_plugin(
            tmp_path,
            {
                "src/main.py": "def hello(): return 42\n",
            },
        )
        report = ValidationReport()
        check_phase4_all(plugin, report)
        assert not any(r.message.startswith("RC-") for r in report.results)
