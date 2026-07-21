"""Tests for the hybrid google-re2 / Python re matcher (cpv_re2_matcher).

Coverage organised into four buckets per the J3 contract:

  A — RE2.Set integration: simple patterns, multi-hit, no-hit, ordering,
      empty input, catalog reload.
  B — Fallback for incompatibles: lookahead, lookbehind, backreference,
      mixed RE2+fallback corpus.
  C — google-re2 missing: monkeypatched ``re2`` module → 100% Python re
      path, parity with RE2-on output, INFO log message.
  D — Error handling: invalid regex → InvalidPattern (CRITICAL surface),
      zero patterns, 1000-pattern corpus, pickle round-trip, thread safety.

The tests do NOT depend on google-re2 being installed — every bucket
exercises the fallback path by either monkeypatching or by feeding RE2
patterns it must reject. When google-re2 IS installed the RE2 layer is
exercised additionally; when it's not, the fallback layer carries the
whole load and the suite still passes.
"""

from __future__ import annotations

import logging
import pickle
import sys
import threading
from pathlib import Path

import pytest

# Add scripts directory to path for imports.
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import cpv_re2_matcher as matcher_mod  # noqa: E402
from cpv_re2_matcher import (  # noqa: E402
    HybridMatcher,
    InvalidPattern,
    _pattern_is_re2_unsafe,
    _Re2MatchProxy,
    _reset_log_once_for_tests,
)


@pytest.fixture(autouse=True)
def _reset_log_once():
    """Ensure every test sees its own log-once state."""
    _reset_log_once_for_tests()
    yield
    _reset_log_once_for_tests()


# ─────────────────────────────────────────────────────────────────────────────
# Bucket A — RE2.Set integration
# ─────────────────────────────────────────────────────────────────────────────


class TestBucketA_Re2SetIntegration:
    """RE2-compatible patterns flow through the fast RE2.Set layer."""

    def test_a01_compiles_ten_simple_patterns(self) -> None:
        """All 10 simple substring patterns compile cleanly with zero invalid entries."""
        patterns = {f"R{i:02d}": f"keyword{i}" for i in range(10)}
        m = HybridMatcher(patterns)
        assert m.stats["re2_compiled"] + m.stats["re_fallback"] == 10
        assert m.stats["invalid"] == 0

    def test_a02_scan_returns_matching_rule_ids(self) -> None:
        """scan() returns the exact rule_id for a single-hit substring match."""
        m = HybridMatcher({"FIND_FOO": r"foo"})
        results = m.scan("xx foo yy")
        assert len(results) == 1
        assert results[0][0] == "FIND_FOO"
        assert results[0][1].group() == "foo"

    def test_a03_multiple_patterns_match_same_input(self) -> None:
        """Input matching N patterns surfaces all N rule_ids."""
        m = HybridMatcher(
            {
                "RA_FOO": r"foo",
                "RA_BAR": r"bar",
                "RA_BAZ": r"baz",
            }
        )
        results = m.scan("foo bar baz")
        rule_ids = [r[0] for r in results]
        assert rule_ids == ["RA_BAR", "RA_BAZ", "RA_FOO"]

    def test_a04_no_matches_returns_empty_list(self) -> None:
        """scan() with input that matches NO pattern returns []."""
        m = HybridMatcher({"X1": r"alpha", "X2": r"beta"})
        assert m.scan("zzz qqq mmm") == []

    def test_a05_output_order_stable_by_rule_id(self) -> None:
        """scan() output is sorted by rule_id ascending then by span start."""
        m = HybridMatcher(
            {
                "ZZZ_LAST": r"hit",
                "AAA_FIRST": r"hit",
                "MMM_MID": r"hit",
            }
        )
        results = m.scan("hit")
        ids = [r[0] for r in results]
        assert ids == sorted(ids), f"Output not sorted: {ids}"
        assert ids == ["AAA_FIRST", "MMM_MID", "ZZZ_LAST"]

    def test_a06_empty_input_returns_empty(self) -> None:
        """scan('') is a no-op that returns [] without touching the engines."""
        m = HybridMatcher({"R": r"anything"})
        assert m.scan("") == []

    def test_a07_catalog_reload_creates_independent_matcher(self) -> None:
        """A new HybridMatcher with a different catalog is fully independent."""
        m1 = HybridMatcher({"OLD": r"alpha"})
        m2 = HybridMatcher({"NEW": r"beta"})
        assert m1.scan("alpha beta") == [("OLD", _any_match_with_span(0, 5))] or [
            r[0] for r in m1.scan("alpha beta")
        ] == ["OLD"]
        assert [r[0] for r in m2.scan("alpha beta")] == ["NEW"]

    def test_a08_span_info_correct_for_re2_hits(self) -> None:
        """Match span returned by RE2 layer reflects actual position in input."""
        m = HybridMatcher({"FOO": r"foo"})
        results = m.scan("xxxxfoozzz")
        assert len(results) == 1
        assert results[0][1].span() == (4, 7)
        assert results[0][1].group() == "foo"

    def test_a09_stats_includes_re2_available_flag(self) -> None:
        """stats['re2_available'] reflects whether google-re2 imported."""
        m = HybridMatcher({"R": r"x"})
        assert "re2_available" in m.stats
        assert isinstance(m.stats["re2_available"], bool)

    def test_a10_stats_route_counts_sum_to_pattern_count(self) -> None:
        """re2_compiled + re_fallback + invalid == len(patterns)."""
        patterns = {f"R{i}": f"pat{i}" for i in range(5)}
        m = HybridMatcher(patterns)
        s = m.stats
        assert s["re2_compiled"] + s["re_fallback"] + s["invalid"] == 5


# ─────────────────────────────────────────────────────────────────────────────
# Bucket B — Fallback for incompatibles
# ─────────────────────────────────────────────────────────────────────────────


class TestBucketB_Fallback:
    """Patterns RE2 refuses still match via the Python re fallback."""

    def test_b01_lookahead_routed_to_fallback(self) -> None:
        """A pattern with lookahead is rejected by RE2 → fallback path."""
        m = HybridMatcher({"LOOK": r"foo(?=bar)"})
        # If google-re2 is installed, the pattern should fall back.
        # If not, it goes to fallback by default. Either way: 1 fallback.
        assert m.stats["re_fallback"] == 1
        assert m.stats["re2_compiled"] == 0

    def test_b02_lookbehind_routed_to_fallback(self) -> None:
        """A pattern with lookbehind also falls through."""
        m = HybridMatcher({"BEHIND": r"(?<=foo)bar"})
        assert m.stats["re_fallback"] == 1
        results = m.scan("foobar")
        assert len(results) == 1
        assert results[0][0] == "BEHIND"

    def test_b03_backreference_routed_to_fallback(self) -> None:
        """Backref \\1 — not supported by RE2 — handled by Python re."""
        m = HybridMatcher({"BACKREF": r"(\w+)\s+\1"})
        assert m.stats["re_fallback"] == 1
        results = m.scan("hello hello")
        assert len(results) == 1
        assert results[0][0] == "BACKREF"

    def test_b04_mixed_corpus_matches_both_layers(self) -> None:
        """Mix of RE2-OK + RE2-incompatible: scan() surfaces hits from BOTH."""
        m = HybridMatcher(
            {
                "PLAIN": r"foo",  # RE2 layer
                "LOOK": r"bar(?=baz)",  # fallback layer — needs barbaz with NO space
            }
        )
        results = m.scan("foo and barbaz")
        rule_ids = sorted(r[0] for r in results)
        assert rule_ids == ["LOOK", "PLAIN"]

    def test_b05_fallback_patterns_match_same_input_as_re2(self) -> None:
        """Fallback patterns are scanned against the same text, not skipped."""
        m = HybridMatcher({"COMPLEX": r"(?<=abc)def(?=ghi)"})
        # Fallback layer should match this Python-re-only pattern.
        results = m.scan("xxabcdefghi yy")
        assert len(results) == 1
        assert results[0][0] == "COMPLEX"
        assert results[0][1].group() == "def"

    def test_b06_lookahead_no_match_returns_empty(self) -> None:
        """Fallback patterns honour their own no-match cases."""
        m = HybridMatcher({"LOOK": r"foo(?=bar)"})
        assert m.scan("foo qux") == []  # lookahead fails: 'bar' not after 'foo'


# ─────────────────────────────────────────────────────────────────────────────
# Bucket C — google-re2 missing (force-disabled path)
# ─────────────────────────────────────────────────────────────────────────────


class TestBucketC_Re2Missing:
    """When google-re2 is unavailable, every pattern goes through Python re."""

    def test_c01_force_disabled_routes_everything_to_fallback(self) -> None:
        """_force_re2_disabled=True routes 100% of patterns to fallback."""
        m = HybridMatcher(
            {"A": r"alpha", "B": r"beta", "C": r"gamma"},
            _force_re2_disabled=True,
        )
        assert m.stats["re2_compiled"] == 0
        assert m.stats["re_fallback"] == 3
        assert m.stats["re2_available"] is False

    def test_c02_force_disabled_scan_parity_with_re2(self) -> None:
        """Output of force-disabled matcher matches the RE2-on matcher."""
        patterns = {"FOO": r"foo", "BAR": r"bar", "BAZ": r"baz"}
        text = "foo bar baz foo"

        m_default = HybridMatcher(patterns)
        m_no_re2 = HybridMatcher(patterns, _force_re2_disabled=True)

        ids_default = sorted(r[0] for r in m_default.scan(text))
        ids_no_re2 = sorted(r[0] for r in m_no_re2.scan(text))
        assert ids_default == ids_no_re2 == ["BAR", "BAZ", "FOO"]

    def test_c03_force_disabled_emits_info_log(self, caplog) -> None:
        """Disabling re2 logs 'RE2 unavailable' INFO once."""
        with caplog.at_level(logging.INFO, logger="cpv_re2_matcher"):
            HybridMatcher({"R": r"x"}, _force_re2_disabled=True)
        # Look for the message in any INFO record.
        infos = [r.getMessage() for r in caplog.records if r.levelname == "INFO"]
        assert any("RE2 unavailable" in msg for msg in infos), f"Missing INFO log; got: {infos}"

    def test_c04_monkeypatch_sys_modules_re2_to_none(self, monkeypatch) -> None:
        """Setting matcher_mod._re2_module = None forces 100% Python re path."""
        monkeypatch.setattr(matcher_mod, "_re2_module", None)
        m = HybridMatcher({"R1": r"alpha", "R2": r"beta"})
        assert m.stats["re2_compiled"] == 0
        assert m.stats["re_fallback"] == 2
        assert m.stats["re2_available"] is False
        # And scanning still works:
        results = m.scan("alpha beta gamma")
        ids = sorted(r[0] for r in results)
        assert ids == ["R1", "R2"]

    def test_c05_force_disabled_invalid_patterns_still_surface(self) -> None:
        """Invalid pattern hits the InvalidPattern path even with re2 disabled."""
        m = HybridMatcher(
            {"OK": r"good", "BROKEN": r"[abc"},  # missing ]
            _force_re2_disabled=True,
        )
        assert m.stats["invalid"] == 1
        assert len(m.invalid_patterns) == 1
        assert m.invalid_patterns[0].rule_id == "BROKEN"


# ─────────────────────────────────────────────────────────────────────────────
# Bucket D — Error handling
# ─────────────────────────────────────────────────────────────────────────────


class TestBucketD_ErrorHandling:
    """Defensive paths: invalid input, zero patterns, scale, pickle, threads."""

    def test_d01_invalid_pattern_emits_invalid_record(self) -> None:
        """One unparseable regex → 1 InvalidPattern; other patterns still scan."""
        m = HybridMatcher({"GOOD": r"hello", "BAD": r"[unclosed"})
        assert m.stats["invalid"] == 1
        invalid = m.invalid_patterns
        assert len(invalid) == 1
        assert invalid[0].rule_id == "BAD"
        assert invalid[0].pattern == r"[unclosed"
        assert invalid[0].re_error  # non-empty error string
        # Good pattern still matches.
        results = m.scan("hello world")
        assert [r[0] for r in results] == ["GOOD"]

    def test_d02_invalid_pattern_record_is_frozen_dataclass(self) -> None:
        """InvalidPattern is a frozen dataclass — immutable record."""
        ip = InvalidPattern(rule_id="X", pattern="[", re2_error=None, re_error="boom")
        with pytest.raises((AttributeError, Exception)):
            ip.rule_id = "Y"  # type: ignore[misc]

    def test_d03_zero_patterns_does_not_crash(self) -> None:
        """Empty catalog: no scan, no panic, stats all zero."""
        m = HybridMatcher({})
        assert m.stats == {
            "re2_compiled": 0,
            "re_fallback": 0,
            "invalid": 0,
            "re2_available": m.stats["re2_available"],
        }
        assert m.scan("anything goes here") == []

    def test_d04_thousand_patterns_no_perf_cliff(self) -> None:
        """1000-pattern corpus compiles + scans in well under the 5s timeout.

        Patterns are word-boundary anchored so we don't get substring overlap
        (e.g. ``keyword_1`` matching the start of ``keyword_10``).
        """
        import time

        patterns = {f"R{i:04d}": rf"\bkeyword_{i}\b" for i in range(1000)}
        t0 = time.perf_counter()
        m = HybridMatcher(patterns)
        compile_secs = time.perf_counter() - t0
        assert compile_secs < 5.0, f"1000-pattern compile took {compile_secs:.2f}s — perf cliff"

        # Scanning text that hits exactly 10 of the 1000 patterns (those at
        # indexes 0, 10, 20, ..., 90). Word boundaries prevent overlap.
        text = " ".join(f"keyword_{i}" for i in range(0, 100, 10))
        t0 = time.perf_counter()
        results = m.scan(text)
        scan_secs = time.perf_counter() - t0
        assert scan_secs < 2.0, f"1000-pattern scan took {scan_secs:.2f}s — perf cliff"
        assert len(results) == 10

    def test_d05_pickle_round_trip_preserves_patterns(self) -> None:
        """HybridMatcher is pickleable (re2.Set is rebuilt lazily on unpickle)."""
        patterns = {"A": r"alpha", "B": r"(?<=foo)bar"}  # one RE2-OK, one fallback
        m = HybridMatcher(patterns)

        blob = pickle.dumps(m)
        m2 = pickle.loads(blob)
        assert isinstance(m2, HybridMatcher)

        # Original and round-tripped scan should match.
        text = "alpha foobar"
        ids_orig = sorted(r[0] for r in m.scan(text))
        ids_round = sorted(r[0] for r in m2.scan(text))
        assert ids_orig == ids_round
        assert ids_orig == ["A", "B"]

    def test_d06_pickle_round_trip_preserves_stats_structure(self) -> None:
        """After unpickling, stats keys are present and integer-typed."""
        m = HybridMatcher({"R": r"x"})
        m2 = pickle.loads(pickle.dumps(m))
        for k in ("re2_compiled", "re_fallback", "invalid"):
            assert isinstance(m2.stats[k], int)
        assert isinstance(m2.stats["re2_available"], bool)

    def test_d07_thread_safety_concurrent_scans(self) -> None:
        """10 threads concurrently scanning → no crashes, no dropped findings."""
        patterns = {f"R{i}": rf"word{i}" for i in range(5)}
        m = HybridMatcher(patterns)
        text = "word0 word1 word2 word3 word4"
        results: list[list[tuple[str, object]]] = []
        errors: list[BaseException] = []
        lock = threading.Lock()

        def worker():
            try:
                for _ in range(50):
                    r = m.scan(text)
                    with lock:
                        results.append(r)
            except BaseException as exc:  # pragma: no cover - failure mode
                with lock:
                    errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30.0)
            assert not t.is_alive(), "Worker thread hung"

        assert not errors, f"Threads crashed: {errors!r}"
        assert len(results) == 500  # 10 threads × 50 iterations
        # Every scan should return 5 hits.
        for r in results:
            assert len(r) == 5, f"Lost findings under concurrency: {r}"

    def test_d08_re2_match_proxy_span_and_group(self) -> None:
        """_Re2MatchProxy exposes .span() and .group() compatible with re.Match."""
        p = _Re2MatchProxy(rule_id="R", _start=2, _end=5, _text="xxfooyy")
        assert p.span() == (2, 5)
        assert p.group() == "foo"
        assert p.group(0) == "foo"
        assert p.start == 2
        assert p.end == 5

    def test_d09_re2_match_proxy_group_index_rejects_capture(self) -> None:
        """_Re2MatchProxy.group(1) raises (no capture-group support)."""
        p = _Re2MatchProxy(rule_id="R", _start=0, _end=3, _text="foo")
        with pytest.raises(IndexError):
            p.group(1)

    def test_d10_callers_copy_of_patterns_not_mutated(self) -> None:
        """Mutating the caller's dict after construction does NOT corrupt matcher state."""
        d = {"R1": r"alpha"}
        m = HybridMatcher(d)
        d["R2"] = r"beta"  # mutation AFTER construction
        results = m.scan("alpha beta")
        ids = [r[0] for r in results]
        # The matcher should only know about R1.
        assert ids == ["R1"]

    def test_d11_invalid_patterns_property_returns_copy(self) -> None:
        """invalid_patterns returns a copy — mutating it does not affect matcher."""
        m = HybridMatcher({"BAD": r"["})
        copy_a = m.invalid_patterns
        copy_a.clear()
        copy_b = m.invalid_patterns
        assert len(copy_b) == 1, "invalid_patterns should be defensive copy"

    def test_d12_stats_returns_fresh_dict_each_call(self) -> None:
        """stats returns a NEW dict per call — caller cannot pollute matcher state."""
        m = HybridMatcher({"R": r"x"})
        s1 = m.stats
        s1["re2_compiled"] = 999999
        s2 = m.stats
        assert s2["re2_compiled"] != 999999

    def test_d13_no_re2_with_invalid_pattern_routes_to_invalid(self, monkeypatch) -> None:
        """re2 disabled + invalid regex → InvalidPattern (no silent drop)."""
        monkeypatch.setattr(matcher_mod, "_re2_module", None)
        m = HybridMatcher({"BAD": r"(unclosed"})
        assert m.stats["invalid"] == 1
        assert m.invalid_patterns[0].rule_id == "BAD"
        assert m.invalid_patterns[0].re2_error is None  # re2 was unavailable, not rejecting

    def test_d14_pickle_round_trip_preserves_invalid_record(self) -> None:
        """An invalid pattern survives pickle round-trip and is re-detected."""
        m1 = HybridMatcher({"OK": r"x", "BAD": r"["})
        m2 = pickle.loads(pickle.dumps(m1))
        assert m2.stats["invalid"] == 1
        assert m2.invalid_patterns[0].rule_id == "BAD"


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _any_match_with_span(start: int, end: int):
    """Sentinel — unused; placeholder to silence linters in test_a07's old form."""

    class _AnyMatch:
        def __eq__(self, other):
            try:
                return other.span() == (start, end)
            except AttributeError:
                return False

    return _AnyMatch()


re2_only = pytest.mark.skipif(
    matcher_mod._re2_module is None,
    reason="google-re2 not installed",
)


class TestRe2UnsafePreFilter:
    """Pre-filter routes re2-incompatible patterns to fallback WITHOUT an Add().

    The pre-filter (`_pattern_is_re2_unsafe`) exists so google-re2's C++ layer
    never parses — and never emits an absl `E0000 ... Error parsing` stderr
    line for — a pattern it will reject anyway. It must be PRECISE: it may not
    over-flag a re2-SAFE pattern (that would needlessly route it to the
    backtracking Python-re fallback, weakening re2's linear-time guarantee).
    """

    # A single backslash, used to build \u / \\u test patterns WITHOUT a
    # literal escape in the source (which the file tooling would fold into a
    # zero-width char that CPV's own INVISIBLE_TEXT rule then flags).
    _BS = chr(92)

    def test_flags_lookarounds_r_escape_and_backref(self) -> None:
        """Lookarounds, the \\R escape, and \\1-\\9 backrefs are re2-rejects → True."""
        bs = self._BS
        for pat in (
            r"foo(?!bar)",
            r"x(?=y)",
            r"(?<=a)b",
            r"(?<!a)b",
            r"(?>ab)",
            "HKEY.*" + bs + "Run",  # \R line-break escape
            "(a)" + bs + "1",  # backreference
        ):
            assert _pattern_is_re2_unsafe(pat) is True, pat

    def test_real_unicode_escape_flags_but_escaped_backslash_does_not(self) -> None:
        """A real \\u / \\U escape flags True; an escaped-backslash literal flags False."""
        bs = self._BS
        assert _pattern_is_re2_unsafe(bs + "u200b") is True  # real \u escape → re2 rejects
        assert _pattern_is_re2_unsafe(bs + "U0001F600") is True  # real \U escape → re2 rejects
        assert _pattern_is_re2_unsafe(bs + bs + "u200b") is False  # \\u = literal text → re2 safe
        assert _pattern_is_re2_unsafe(bs + bs + "Run") is False  # \\R = literal text → re2 safe

    def test_does_not_flag_ordinary_patterns(self) -> None:
        """Ordinary patterns and re2-supported escapes (\\d \\s \\w) flag False."""
        for pat in (r"abc.*def", r"\d+\s*\w+", r"(?:ldap|rmi)://host"):
            assert _pattern_is_re2_unsafe(pat) is False, pat

    @re2_only
    def test_lookahead_routes_to_fallback_and_still_matches(self) -> None:
        """A lookahead pattern lands in the Python-re fallback yet still fires."""
        m = HybridMatcher({"has_la": r"yaml\.load\s*\((?!.*SafeLoader)", "plain": r"eval\("})
        # The lookahead pattern is pre-filtered to fallback; the plain one to re2.
        assert m.stats["re_fallback"] >= 1
        assert m.has_re2_set is True
        hits = {rid for rid, _ in m.scan("yaml.load(open('x'))")}
        assert "has_la" in hits  # detection preserved via fallback

    @re2_only
    def test_escaped_backslash_literal_stays_on_re2_layer(self) -> None:
        """A re2-safe escaped-backslash literal is NOT demoted to fallback (no over-match)."""
        bs = self._BS
        m = HybridMatcher({"inv": bs + bs + "u200b", "plain": r"eval\("})
        # Both are re2-safe → zero fallback, both served by the RE2 Set.
        assert m.stats["re_fallback"] == 0
        assert m.stats["re2_compiled"] == 2
        # The literal pattern matches the 6-char text: backslash + "u200b".
        scan_text = "x = '" + bs + "u200b'"
        assert "inv" in {rid for rid, _ in m.scan(scan_text)}


# ─────────────────────────────────────────────────────────────────────────────
# A note for the maintainer
# ─────────────────────────────────────────────────────────────────────────────
#
# These tests are designed to pass whether or not google-re2 is installed.
# When google-re2 IS available, the RE2 layer is exercised; when it ISN'T,
# the fallback layer carries the whole load. The contract surface (.scan,
# .stats, .invalid_patterns, pickle round-trip) is identical either way.
#
# If you're adding a new test that REQUIRES google-re2 to be installed,
# guard it with:
#
#     re2_only = pytest.mark.skipif(
#         matcher_mod._re2_module is None,
#         reason="google-re2 not installed",
#     )
#
# … but prefer parity-style tests that work in both modes whenever possible.
