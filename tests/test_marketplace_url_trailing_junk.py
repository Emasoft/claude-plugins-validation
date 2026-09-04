"""Tests for the github.com trailing-slash / dangling '?'/'#' repository URL check.

Claude Code v2.1.259 fixed the CLIENT-side symptom ("Fixed marketplace repo URLs
on github.com with a trailing slash or dangling '?'/'#' producing an unusable
'.git' clone URL"), but the URL is still objectively malformed for any older
client or any other git tool that clones it. CPV flags it at WARNING — the one
severity tier that never blocks --strict — because CC itself no longer refuses
this shape and CPV must never invent a stricter publish gate than the spec.

Every malformed case here is paired with its clean sibling to prove the fix is
FN-safe (a clean URL never fires) and does not touch the pre-existing SSH /
scp-style / non-github-host handling.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from validate_marketplace import validate_repository_url  # noqa: E402


def _levels(results, level):
    return [r for r in results if r.level == level]


class TestGithubTrailingSlash:
    """A github.com repository URL with a trailing slash must WARN, never block."""

    def test_trailing_slash_warns(self):
        """A trailing slash on a github.com URL fires exactly one WARNING."""
        results = validate_repository_url("https://github.com/owner/repo/", "myplugin", "mp.json")
        warnings = _levels(results, "WARNING")
        assert len(warnings) == 1
        assert "trailing slash" in warnings[0].message
        assert warnings[0].suggestion == "Use 'https://github.com/owner/repo' instead"

    def test_no_blocking_severity(self):
        """The trailing-slash finding must never be CRITICAL/MAJOR/MINOR."""
        results = validate_repository_url("https://github.com/owner/repo/", "myplugin", "mp.json")
        assert not _levels(results, "CRITICAL")
        assert not _levels(results, "MAJOR")
        assert not _levels(results, "MINOR")

    def test_clean_plain_url_stays_silent(self):
        """The clean sibling (no trailing slash) must produce zero findings."""
        results = validate_repository_url("https://github.com/owner/repo", "myplugin", "mp.json")
        assert results == []


class TestGithubDanglingQuery:
    """A github.com repository URL ending in a bare '?' must WARN."""

    def test_dangling_query_warns(self):
        """A dangling '?' with an empty query fires exactly one WARNING."""
        results = validate_repository_url("https://github.com/owner/repo?", "myplugin", "mp.json")
        warnings = _levels(results, "WARNING")
        assert len(warnings) == 1
        assert "dangling '?'" in warnings[0].message
        assert warnings[0].suggestion == "Use 'https://github.com/owner/repo' instead"

    def test_non_empty_query_stays_silent(self):
        """A URL with an actual (non-empty) query string must not fire this rule."""
        results = validate_repository_url("https://github.com/owner/repo?ref=main", "myplugin", "mp.json")
        assert not any("dangling" in r.message for r in results)


class TestGithubDanglingFragment:
    """A github.com repository URL ending in a bare '#' must WARN."""

    def test_dangling_fragment_warns(self):
        """A dangling '#' with an empty fragment fires exactly one WARNING."""
        results = validate_repository_url("https://github.com/owner/repo#", "myplugin", "mp.json")
        warnings = _levels(results, "WARNING")
        assert len(warnings) == 1
        assert "dangling '#'" in warnings[0].message
        assert warnings[0].suggestion == "Use 'https://github.com/owner/repo' instead"

    def test_non_empty_fragment_stays_silent(self):
        """A URL with an actual (non-empty) fragment must not fire this rule."""
        results = validate_repository_url("https://github.com/owner/repo#readme", "myplugin", "mp.json")
        assert not any("dangling" in r.message for r in results)


class TestNonGithubHostsUnaffected:
    """The rule is scoped to github.com per CC's own changelog wording; other hosts are untouched."""

    def test_gitlab_trailing_slash_not_flagged_by_this_rule(self):
        """A trailing slash on a non-github.com host must not fire the github-specific rule."""
        results = validate_repository_url("https://gitlab.com/team/plugin/", "myplugin", "mp.json")
        assert not any("trailing slash" in r.message or "dangling" in r.message for r in results)


class TestPreExistingFormsUnaffected:
    """The pre-existing scp-style SSH and dotted-suffix handling must still work."""

    def test_scp_style_ssh_form_still_clean(self):
        """scp-style SSH URLs (git@host:owner/repo) must remain fully accepted."""
        results = validate_repository_url("git@github.com:owner/repo.git", "myplugin", "mp.json")
        assert results == []

    def test_dot_git_suffix_form_still_clean(self):
        """A URL already carrying the .git suffix must remain fully accepted."""
        results = validate_repository_url("https://github.com/owner/repo.git", "myplugin", "mp.json")
        assert results == []

    def test_dot_git_suffix_with_trailing_slash_still_warns(self):
        """A .git URL with a trailing slash is just as unusable and must still warn."""
        results = validate_repository_url("https://github.com/owner/repo.git/", "myplugin", "mp.json")
        warnings = _levels(results, "WARNING")
        assert len(warnings) == 1
        assert warnings[0].suggestion == "Use 'https://github.com/owner/repo.git' instead"
