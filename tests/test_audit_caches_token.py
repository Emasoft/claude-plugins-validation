#!/usr/bin/env python3
"""Two-sided regression tests for the audit caches/token MAJORs.

10-agent whole-plugin audit (TRDD-021250b5 follow-up):

cache #1 — the lint cache key now folds in the resolved linter config content, so
           editing .markdownlint.json / ruff config / .eslintrc invalidates the
           cache instead of returning stale findings for the 30-day TTL.
cache #3 — validate_security._is_self_scan_eligible and
           _plugin_compute_hashes.is_self_scan_eligible agree on RELATIVE
           tests/fixtures/ paths (the validator was missing the startswith clause,
           so tracked fixtures were scanned during self-scan -> self-FP).
token #1 — the Tier-3 heuristic estimator never under-counts emoji / symbol /
           combining-mark content (the never-under-count guarantee).
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

# U+0301 COMBINING ACUTE ACCENT (category Mn) — built via escape so the source
# bytes are unambiguously base-letter + combining-mark, not a precomposed glyph.
_COMBINING_ACUTE = "́"


class TestLintCacheConfigFingerprint:
    """cache #1 - editing a linter config changes the cache key."""

    def _key(self, root: Path):
        from cpv_lint_engine import _build_cache_key

        return _build_cache_key("markdown", [root / "a.md"], root, strict_missing_tools=False)

    def test_config_edit_changes_key(self):
        root = Path(tempfile.mkdtemp())
        try:
            (root / "a.md").write_text("# hi\n", encoding="utf-8")
            cfg = root / ".markdownlint.json"
            cfg.write_text('{"MD013": false}', encoding="utf-8")
            k1 = self._key(root)
            cfg.write_text('{"MD013": true, "MD012": false}', encoding="utf-8")
            k2 = self._key(root)
            assert k1.args_hash != k2.args_hash
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_same_config_same_key(self):
        """Two-sided: an unchanged config yields a stable key (cache still hits)."""
        root = Path(tempfile.mkdtemp())
        try:
            (root / "a.md").write_text("# hi\n", encoding="utf-8")
            (root / ".markdownlint.json").write_text('{"MD013": true}', encoding="utf-8")
            assert self._key(root).args_hash == self._key(root).args_hash
        finally:
            shutil.rmtree(root, ignore_errors=True)


class TestSelfScanEligibilityLockstep:
    """cache #3 - the two is_self_scan_eligible implementations agree."""

    _CASES = [
        "tests/fixtures/evil.py",  # relative (the drift case)
        "plugin/tests/fixtures/evil.py",  # with a leading segment
        "rules/skillaudit_patterns.json",
        "scripts/rules/skillaudit_patterns.json",
        "agents/foo.md",
        "README.md",
        "some/random/plugin_file.py",  # NOT eligible - must agree on False too
    ]

    def test_validator_and_manifest_agree(self):
        from _plugin_compute_hashes import is_self_scan_eligible as manifest_eligible
        from validate_security import _is_self_scan_eligible as validator_eligible

        for rel in self._CASES:
            assert validator_eligible(rel) == manifest_eligible(rel), (
                f"drift on {rel!r}: validator={validator_eligible(rel)} manifest={manifest_eligible(rel)}"
            )

    def test_relative_fixture_path_is_eligible(self):
        """The specific drift: a relative tests/fixtures/ path is eligible in both."""
        from _plugin_compute_hashes import is_self_scan_eligible as manifest_eligible
        from validate_security import _is_self_scan_eligible as validator_eligible

        assert validator_eligible("tests/fixtures/evil.py") is True
        assert manifest_eligible("tests/fixtures/evil.py") is True

    def test_unrelated_path_eligible_in_neither(self):
        """Two-sided: a non-CPV path is NOT eligible in either (no over-exempt)."""
        from _plugin_compute_hashes import is_self_scan_eligible as manifest_eligible
        from validate_security import _is_self_scan_eligible as validator_eligible

        assert validator_eligible("src/app/main.py") is False
        assert manifest_eligible("src/app/main.py") is False


class TestHeuristicNeverUnderCountsEmoji:
    """token #1 - the heuristic estimate is >= the BPE estimate on emoji/symbol/combining."""

    def _check(self, text: str) -> bool:
        from cpv_token_estimate import _estimate_heuristic, estimate_tokens

        return _estimate_heuristic(text) >= estimate_tokens(text).tokens

    def test_emoji_zwj_sequences_not_undercounted(self):
        assert self._check("\U0001f389\U0001f38a\U0001f680\U0001f525\U0001f4af" * 8)

    def test_combining_marks_not_undercounted(self):
        # base letter + U+0301 COMBINING ACUTE ACCENT (category Mn) - the audit's
        # combining-mark case, built explicitly (NOT a precomposed glyph).
        text = "".join(base + _COMBINING_ACUTE for base in "aeiou") * 20
        assert self._check(text)

    def test_symbol_dense_not_undercounted(self):
        assert self._check("★☆♠♣♥♦§¶†‡" * 10)

    def test_plain_latin_still_conservative(self):
        """Two-sided: ordinary prose is still bounded above the BPE estimate."""
        assert self._check("normal english prose here " * 20)

    def test_empty_text_is_zero(self):
        from cpv_token_estimate import _estimate_heuristic

        assert _estimate_heuristic("") == 0
