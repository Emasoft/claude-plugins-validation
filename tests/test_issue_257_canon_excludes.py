"""Issue #257 item 5: no generated canon lint config may keep git-tracked content out of a scan.

SECURITY scanners (bandit, checkov, trivy ...) exclude nothing. STYLE tools (cspell, jscpd)
may skip only never-tracked trees and the exact lock-file names the canon creates. These
tests pin the exact surviving surface, so any added glob fails here.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import yaml

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import generate_plugin_repo as gen  # noqa: E402
import standardize_plugin  # noqa: E402

# Every surviving exclude, with the proof it can never hide tracked content.
ALLOWED_CSPELL_IGNORE = {
    "**/uv.lock",  # package-manager lock file the canon creates, exact name
    "**/package-lock.json",  # same
    "**/.git/**",  # VCS data, never tracked
    "**/node_modules/**",  # dependency tree, never tracked
    "**/.venv/**",  # virtualenv, never tracked
    "**/venv/**",
    "**/__pycache__/**",  # bytecode cache, never tracked
}
ALLOWED_JSCPD_IGNORE = ["**/node_modules/**", "**/.git/**"]

# Tracked content the old lists kept out of the scan.
TRACKED_SAMPLES = [
    ".claude-plugin/plugin.json",
    "hooks/hooks.json",
    ".plugin-self-hashes.json",
    ".claude/project/memory/page.md",
    "tests/fixtures/sample.md",
    "tests/test_x.py",
    "docs_dev/notes.md",
    "README.md",
]


def _params() -> gen.PluginParams:
    return gen.PluginParams(
        name="demo-plugin", description="d", author="A", author_email="a@b.c", github_owner="Emasoft"
    )


def test_cspell_ignore_paths_are_exactly_the_allowed_set() -> None:
    assert set(standardize_plugin._CSPELL_IGNORE_PATHS) == ALLOWED_CSPELL_IGNORE


def test_cspell_ignore_paths_contain_no_extension_or_tracked_dir_glob() -> None:
    for pat in standardize_plugin._CSPELL_IGNORE_PATHS:
        assert not re.search(r"\*\.\w+$", pat), f"extension glob {pat}"
        for tracked in ("fixtures", "testdata", ".claude", "_dev", "reports", ".tldr"):
            assert tracked not in pat, (tracked, pat)


def test_both_cspell_renderers_agree_and_carry_the_hex_precision_pattern() -> None:
    a = json.loads(standardize_plugin._render_canonical_cspell_config(Path("/nonexistent-plugin")))
    b = json.loads(gen.gen_cspell_json(_params(), []))
    assert a["ignorePaths"] == b["ignorePaths"] == list(standardize_plugin._CSPELL_IGNORE_PATHS)
    assert a["ignoreRegExpList"] == b["ignoreRegExpList"] == list(standardize_plugin._CSPELL_IGNORE_REGEXP)


def test_hex_pattern_skips_digests_but_not_words() -> None:
    """Precision instead of a blanket `**/*.json` exclusion: only digest-shaped tokens are skipped."""
    (pattern,) = standardize_plugin._CSPELL_IGNORE_REGEXP
    rx = re.compile(pattern)
    assert rx.search("sha256 " + "a3f9b2c4d5e60718" * 4)
    assert rx.search('"commit": "8356c603deadbeef01"')
    assert not rx.search("recieve the manifest")  # a misspelled word is still checked
    assert not rx.search("0123456789abcde")  # 15 hex chars: below the digest floor
    assert not rx.search("abcdefghijklmnop")  # 16 letters, not all hex digits


def test_jscpd_ignore_is_only_never_tracked_trees() -> None:
    assert json.loads(gen.gen_jscpd_json(_params()))["ignore"] == ALLOWED_JSCPD_IGNORE
    assert standardize_plugin._CANONICAL_JSCPD_CONFIG["ignore"] == ALLOWED_JSCPD_IGNORE


def test_mega_linter_has_no_filter_regex_exclude_key() -> None:
    cfg = yaml.safe_load(gen.gen_mega_linter_yml(_params()))
    assert "FILTER_REGEX_EXCLUDE" not in cfg
    # Security linters take no per-linter exclusion either.
    assert not [k for k in cfg if k.startswith(("PYTHON_BANDIT_FILTER", "REPOSITORY_")) and "EXCLUDE" in k]


def test_spell_exclude_regex_matches_lock_files_by_exact_name_only() -> None:
    cfg = yaml.safe_load(gen.gen_mega_linter_yml(_params()))
    rx = re.compile(cfg["SPELL_CSPELL_FILTER_REGEX_EXCLUDE"])
    for skipped in ("uv.lock", "sub/dir/uv.lock", "/tmp/lint/package-lock.json"):
        assert rx.search(skipped), skipped
    # Adversarial: names that merely CONTAIN the lock-file name, and every tracked sample.
    for scanned in ("my-uv.lock", "uv.lock.md", "uv.lockx", "docs/uv.lock/notes.md", *TRACKED_SAMPLES):
        assert not rx.search(scanned), scanned
