"""CLAUDE.md's inventory table declares itself authoritative — pin the numbers that are.

WHY THIS EXISTS. CLAUDE.md opens by calling its inventory load-bearing and telling
every agent to keep it current. Nothing enforced that, and the version row drifted
TWICE before anyone noticed: it read `5.16.2` against an actual `5.17.0` (the
v5.17.0 publish updated `plugin.json` and not the document that claims to be the
reference), and the test-file row read `506` against an actual `516`.

A stale number in a document that asserts its own authority is worse than no
document: the next reader trusts it precisely because of that assertion, and the
error propagates into every count derived from it. `publish.py`'s
`check_version_consistency` compares `pyproject.toml` against `plugin.json` and
has never looked at CLAUDE.md, which is exactly how the drift survived a release.

The version row is pinned EXACTLY because it has a single machine-readable source
of truth. The test-file row is pinned with a TOLERANCE rather than exactly — see
`test_claude_md_test_file_count_is_not_wildly_stale` for why an exact pin there
would be a maintenance trap rather than a guard.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CLAUDE_MD = REPO_ROOT / "CLAUDE.md"
PLUGIN_JSON = REPO_ROOT / ".claude-plugin" / "plugin.json"

# The row is `| **version** | `5.18.0` | ... |` — the backticked cell is the claim.
_VERSION_ROW_RE = re.compile(r"^\|\s*\*\*version\*\*\s*\|\s*`([^`]+)`\s*\|", re.MULTILINE)
_TEST_FILES_ROW_RE = re.compile(r"^\|\s*\*\*test files\*\*\s*\|\s*\*\*(\d+)\*\*\s*\|", re.MULTILINE)


def _claude_md() -> str:
    return CLAUDE_MD.read_text(encoding="utf-8")


def _declared_version() -> str:
    m = _VERSION_ROW_RE.search(_claude_md())
    # An anchor that stops matching would make every assertion below pass
    # vacuously, which is the failure mode this whole file guards against.
    assert m is not None, "the CLAUDE.md version row no longer matches its anchor — fix the regex, do not delete the test"
    return m.group(1)


def _declared_test_file_count() -> int:
    m = _TEST_FILES_ROW_RE.search(_claude_md())
    assert m is not None, "the CLAUDE.md test-files row no longer matches its anchor — fix the regex, do not delete the test"
    return int(m.group(1))


def _actual_version() -> str:
    return str(json.loads(PLUGIN_JSON.read_text(encoding="utf-8"))["version"])


def _actual_test_file_count() -> int:
    return len(list((REPO_ROOT / "tests").glob("test_*.py")))


def test_claude_md_version_row_matches_the_manifest() -> None:
    """The one number with a single source of truth, so it is pinned exactly."""
    declared = _declared_version()
    actual = _actual_version()
    assert declared == actual, (
        f"CLAUDE.md's inventory claims version {declared!r} but "
        f".claude-plugin/plugin.json says {actual!r}. CLAUDE.md declares itself "
        "authoritative, so a reader will believe the wrong one. Update the row in "
        "the same change that bumps the manifest."
    )


def test_claude_md_test_file_count_is_not_wildly_stale() -> None:
    """Pinned with a tolerance, deliberately — an exact pin here would be a trap.

    Every commit that adds or removes a test file would fail an exact assertion,
    so the row would be edited reflexively to whatever number makes the suite
    green — which is precisely the incrementing-without-re-deriving habit that let
    `506` survive. A tolerance catches the real failure (a count that has stopped
    tracking reality) while leaving ordinary test churn alone.
    """
    declared = _declared_test_file_count()
    actual = _actual_test_file_count()
    drift = abs(declared - actual)
    assert drift <= 5, (
        f"CLAUDE.md claims {declared} test files, the tree has {actual} "
        f"(drift {drift}). RE-DERIVE it with the command in that row — do not "
        "increment the previous entry, which is how the stale 506 survived a release."
    )


def test_the_anchors_actually_match_something() -> None:
    """A guard for the guards: a regex that matches nothing passes every test above."""
    text = _claude_md()
    assert _VERSION_ROW_RE.search(text) is not None
    assert _TEST_FILES_ROW_RE.search(text) is not None
