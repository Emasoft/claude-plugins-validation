"""Regression tests for the 2026-05-25 audit findings #1, #2, #9.

Findings (report 20260525_105207+0200-batch-menu-cli-content.md):

* #1 MAJOR — cpv_codemod ``external-skip-list`` rewrote ``plugin.json``
  UNCONDITIONALLY, ignoring ``--apply``. Task G (issue #237) removed that
  subcommand entirely (CPV ignores ``cpv.exclude_paths``); `all` is now
  asserted never to write the manifest.
* #9 NIT  — moot after the removal (it concerned the subcommand's exit code).
* #2 MINOR — cpv_parametrize_body_predicate cached the body-line set on
  ``id(content)``; CPython reuses freed addresses after GC, so a
  different file could receive a STALE wrong answer.

Every finding gets a TWO-SIDED test: the safe behavior is asserted AND
the previously-buggy behavior is asserted to no longer occur.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Make scripts/ importable.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import cpv_codemod  # noqa: E402
import cpv_parametrize_body_predicate as pbp  # noqa: E402

# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_plugin(tmp_path: Path, name: str = "audit-plugin") -> Path:
    """A minimal plugin root with .claude-plugin/plugin.json."""
    root = tmp_path / name
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": name, "version": "1.0.0"}, indent=2) + "\n",
        encoding="utf-8",
    )
    return root


def _manifest_text(plugin_root: Path) -> str:
    return (plugin_root / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8")


# ── Finding #1 — external-skip-list must honor dry-run ────────────────────────


class TestExternalSkipListDryRunContract:
    """Finding #1 follow-up (task G, issue #237): the external-skip-list subcommand is gone.

    `all` never writes plugin.json (it used to via external-skip-list), and the
    removed subcommand is rejected by argparse.
    """

    def test_all_subcommand_dry_run_does_not_write_manifest(self, tmp_path):
        plugin_root = _make_plugin(tmp_path)
        (plugin_root / "external").mkdir()
        before = _manifest_text(plugin_root)

        rc = cpv_codemod.main(["all", "--plugin", str(plugin_root)])

        assert rc == 0
        assert _manifest_text(plugin_root) == before
        assert not (plugin_root / ".cpv-codemod-backup").exists()

    def test_all_apply_never_writes_exclude_paths(self, tmp_path):
        plugin_root = _make_plugin(tmp_path)
        (plugin_root / "external").mkdir()
        before = _manifest_text(plugin_root)

        rc = cpv_codemod.main(["all", "--plugin", str(plugin_root), "--apply"])

        assert rc == 0
        assert _manifest_text(plugin_root) == before
        assert "exclude_paths" not in _manifest_text(plugin_root)


# ── Finding #9 — moot: the external-skip-list subcommand was removed (task G) ──
# ── Finding #2 — content-hash cache key, no stale answer ──────────────────────

_PARAM_BODY = (
    "import pytest\n"
    "\n"
    '@pytest.mark.parametrize("payload", [\n'
    '    "ignore previous instructions",\n'
    "])\n"
    "def test_x(payload):\n"
    "    assert payload\n"
)
# Line 4 (the literal) is inside the parametrize body.
_PARAM_BODY_INSIDE_LINE = 4

_NO_PARAM = "import os\n\ndef helper():\n    return os.getcwd()\n"


class TestParametrizeCacheCorrectness:
    """Cache keys on content VALUE, never on id() — no stale cross-file answer."""

    def setup_method(self):
        pbp.clear_cache()

    def teardown_method(self):
        pbp.clear_cache()

    def test_two_different_contents_never_collide(self):
        """A parametrize file and a non-parametrize file give distinct answers.

        Even if their ``content`` objects were to reuse the same memory
        address (the pre-fix id() hazard), keying on the content hash
        guarantees no stale answer leaks across them.
        """
        # File A has a parametrize body on the target line.
        assert pbp.is_parametrize_body_line(_PARAM_BODY, _PARAM_BODY_INSIDE_LINE) is True
        # File B has NO parametrize at all — same line number must be False.
        assert pbp.is_parametrize_body_line(_NO_PARAM, _PARAM_BODY_INSIDE_LINE) is False
        # Re-querying A again still answers correctly (no contamination).
        assert pbp.is_parametrize_body_line(_PARAM_BODY, _PARAM_BODY_INSIDE_LINE) is True

    def test_id_reuse_does_not_return_stale_answer(self):
        """Force the exact id()-reuse scenario the finding describes.

        Build content A, query it (populating the cache), drop the only
        reference so it can be GC'd, then build content B. CPython very
        often hands B the address A just freed. With an id() key, B would
        inherit A's cached set. With a content-hash key, B gets the right
        answer.
        """
        import gc

        # A: parametrize body — line 4 is True.
        a = "".join(_PARAM_BODY)
        assert pbp.is_parametrize_body_line(a, _PARAM_BODY_INSIDE_LINE) is True
        id_a = id(a)
        del a
        gc.collect()

        # B: a DIFFERENT content (no parametrize), distinct value.
        b = "".join(_NO_PARAM)
        # Whether or not B reuses A's freed address, the answer must reflect
        # B's actual content (False), not A's cached True.
        assert pbp.is_parametrize_body_line(b, _PARAM_BODY_INSIDE_LINE) is False
        # Document the scenario we exercised (best-effort: address reuse is
        # allocator-dependent, so we don't assert it occurred — the test is
        # still valid because the cache is keyed on value, not address).
        _ = id_a  # referenced to keep the intent explicit

    def test_identical_content_hits_cache(self):
        """Two equal-valued (but distinct-object) contents share one answer."""
        first = "".join(_PARAM_BODY)
        second = "".join(_PARAM_BODY)
        assert first is not second  # distinct objects, equal value

        assert pbp.is_parametrize_body_line(first, _PARAM_BODY_INSIDE_LINE) is True
        cache_size_after_first = len(pbp._LINE_SET_CACHE)

        # Querying an equal-valued second object must reuse the same entry
        # (content-hash key) — the cache does NOT grow.
        assert pbp.is_parametrize_body_line(second, _PARAM_BODY_INSIDE_LINE) is True
        assert len(pbp._LINE_SET_CACHE) == cache_size_after_first

    def test_list_and_str_of_same_content_agree(self):
        """A str and its pre-split list form yield the same body answer."""
        as_str = "".join(_PARAM_BODY)
        as_list = as_str.split("\n")
        assert (
            pbp.is_parametrize_body_line(as_str, _PARAM_BODY_INSIDE_LINE)
            == pbp.is_parametrize_body_line(as_list, _PARAM_BODY_INSIDE_LINE)
            is True
        )

    def test_clear_cache_empties_the_cache(self):
        pbp.is_parametrize_body_line(_PARAM_BODY, _PARAM_BODY_INSIDE_LINE)
        assert len(pbp._LINE_SET_CACHE) >= 1
        pbp.clear_cache()
        assert len(pbp._LINE_SET_CACHE) == 0

    def test_outside_body_line_is_false(self):
        """Line 1 (the import) is NOT inside the parametrize body."""
        assert pbp.is_parametrize_body_line(_PARAM_BODY, 1) is False
