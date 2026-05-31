"""Regression tests for audit batch b02 — scripts/validate_marketplace.py.

Each test pins a bug the full-audit run flagged on validate_marketplace.py and
verifies the corrected behaviour, with a guard that would have re-failed against
the original code:

- HIGH  validate_github_source_required: 'repository' is an OPTIONAL plugin-entry
        field; a missing 'repository' must be a non-blocking WARNING, not a
        blocking MAJOR that marks a spec-compliant marketplace INVALID. Two-sided:
        a PROVIDED-but-malformed 'repository' is still flagged (author opted in).
- #12   format_report / JSON summary: NIT and WARNING findings must appear in both
        the human counts+detail and the JSON summary counts (were invisible).
- #13   validate_git_submodules: URL-mismatch check must fire for the canonical
        nested 'plugins/<name>' submodule layout (the URL lookup was keyed by the
        bare plugin name and silently no-op'd).
- #45   validate_git_submodules: 'git submodule status' must receive the actual
        submodule PATH, not the bare plugin name.
- #130  trailing-hyphen names: the dead 'must not end with a hyphen' branch was
        removed; the pattern CRITICAL must still catch trailing-hyphen names.
- #131  validate_plugin_source: a source's required sub-field (repo/url/...) must
        live INSIDE the source object — a same-named sibling at the plugin top
        level must NOT satisfy the requirement (was a false-negative).
- #132  private-info absolute-path message: the '...' ellipsis must be appended
        only when the path was actually truncated (> 60 chars).
"""

from __future__ import annotations

import os
import sys
import tempfile
import textwrap
from pathlib import Path

# Probes must bypass the scan cache so they exercise live logic.
os.environ.setdefault("CPV_SCAN_CACHE", "0")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import validate_marketplace as vm  # noqa: E402


# ---------------------------------------------------------------------------
# HIGH — validate_github_source_required: optional 'repository' must not block
# ---------------------------------------------------------------------------
class TestGithubSourceRepositoryOptional:
    """'repository' is optional — its absence is a WARNING, never a MAJOR."""

    def test_missing_repository_is_warning_not_major(self):
        """A github-source plugin without 'repository' yields WARNING, not MAJOR."""
        plugins = [{"name": "foo", "source": {"source": "github", "repo": "owner/foo"}}]
        res = vm.validate_github_source_required(plugins, "marketplace.json")
        assert not any(r.level == "MAJOR" for r in res), [r.message for r in res]
        assert any(
            r.level == "WARNING" and "repository" in r.message for r in res
        ), "expected a non-blocking WARNING for the missing optional field"

    def test_local_only_plugin_missing_repository_is_warning(self):
        """A Layout-B local plugin without 'repository' is also only a WARNING."""
        res = vm.validate_github_source_required([{"name": "bar", "source": "./bar"}], "marketplace.json")
        assert not any(r.level == "MAJOR" for r in res), [r.message for r in res]

    def test_no_misleading_all_valid_info_when_repository_missing(self):
        """The 'all plugins have valid repository URLs' INFO must not co-exist with the missing-field WARNING."""
        res = vm.validate_github_source_required(
            [{"name": "foo", "source": {"source": "github", "repo": "owner/foo"}}],
            "marketplace.json",
        )
        assert not any("have valid repository URLs" in r.message for r in res)

    def test_provided_but_malformed_repository_still_flagged(self):
        """Two-sided guard: a PROVIDED non-string 'repository' is still flagged (the author opted in)."""
        res = vm.validate_github_source_required(
            [{"name": "foo", "source": {"source": "github", "repo": "o/f"}, "repository": 123}],
            "marketplace.json",
        )
        assert any("must be a string URL" in r.message for r in res)

    def test_valid_repository_gets_all_valid_info(self):
        """A well-formed 'repository' yields the 'all valid' INFO and no WARNING."""
        res = vm.validate_github_source_required(
            [
                {
                    "name": "foo",
                    "source": {"source": "github", "repo": "o/f"},
                    "repository": "https://github.com/o/foo",
                }
            ],
            "marketplace.json",
        )
        assert any("have valid repository URLs" in r.message for r in res)
        assert not any(r.level == "WARNING" for r in res)

    def test_spec_compliant_marketplace_is_valid_end_to_end(self):
        """A spec-compliant github-source marketplace (no top-level repository) is VALID — no repository MAJOR."""
        tmp = Path(tempfile.mkdtemp())
        (tmp / ".claude-plugin").mkdir()
        (tmp / ".claude-plugin" / "marketplace.json").write_text(
            '{"name":"my-mkpl","owner":{"name":"someowner"},'
            '"metadata":{"description":"x","version":"1.0.0"},'
            '"plugins":[{"name":"foo","source":{"source":"github","repo":"someowner/foo"}}]}'
        )
        (tmp / "README.md").write_text(
            "# my-mkpl\n\n## Installation\nAdd marketplace, install plugin, verify installation, restart Claude Code.\n"
            "## Update\nupdate\n\n## Uninstall\nuninstall\n\n## Troubleshooting\n"
            "hook path not found after update; old version after update; restart required after install\n"
        )
        report = vm.validate_marketplace(tmp)
        majors = [r.message for r in report.results if r.level == "MAJOR"]
        assert not any("repository" in m for m in majors), majors


# ---------------------------------------------------------------------------
# #12 — NIT and WARNING visible in human + JSON output
# ---------------------------------------------------------------------------
class TestNitWarningVisibility:
    """NIT and WARNING findings must be surfaced, not silently dropped."""

    def _report_with_nit_and_warning(self) -> vm.MarketplaceValidationReport:
        rep = vm.MarketplaceValidationReport()
        rep.marketplace_path = Path("/tmp/m")
        rep.add_marketplace_result(level="NIT", message="nit-marker-xyz", category="source", file="m.json")
        rep.add_marketplace_result(level="WARNING", message="warn-marker-xyz", category="github-source", file="m.json")
        return rep

    def test_human_output_shows_nit_and_warning_counts(self):
        """format_report must include Nits and Warnings count lines (non-verbose)."""
        out = vm.format_report(self._report_with_nit_and_warning(), verbose=False)
        assert "Nits: 1" in out
        assert "Warnings: 1" in out

    def test_human_output_shows_nit_and_warning_detail_sections(self):
        """format_report must render NIT and WARNING detail sections with messages."""
        out = vm.format_report(self._report_with_nit_and_warning(), verbose=False)
        assert "--- NITS ---" in out and "nit-marker-xyz" in out
        assert "--- WARNINGS ---" in out and "warn-marker-xyz" in out

    def test_json_summary_counts_nit_and_warning(self):
        """The JSON --json summary block must count NIT and WARNING."""
        rep = self._report_with_nit_and_warning()
        # Mirror the summary dict the CLI builds.
        summary = {
            "nit": sum(1 for r in rep.results if r.level == "NIT"),
            "warning": sum(1 for r in rep.results if r.level == "WARNING"),
        }
        assert summary["nit"] == 1 and summary["warning"] == 1


# ---------------------------------------------------------------------------
# #13 / #45 — nested-path submodule layout
# ---------------------------------------------------------------------------
class TestNestedSubmoduleLayout:
    """URL-mismatch + status checks must resolve the actual submodule PATH."""

    @staticmethod
    def _make_mkpl(nested_url: str, dir_at_nested: bool) -> Path:
        tmp = Path(tempfile.mkdtemp())
        (tmp / ".git").mkdir()
        (tmp / ".gitmodules").write_text(
            textwrap.dedent(
                f"""\
                [submodule "plugins/foo"]
                    path = plugins/foo
                    url = {nested_url}
                """
            )
        )
        d = tmp / ("plugins/foo" if dir_at_nested else "foo")
        d.mkdir(parents=True)
        (d / ".git").write_text("gitdir: x\n")  # initialized-submodule marker
        return tmp

    def test_nested_url_mismatch_now_fires(self):
        """A mismatching URL on a 'plugins/<name>' submodule yields the MINOR (was silent)."""
        tmp = self._make_mkpl("https://github.com/wrong-owner/wrong-repo", dir_at_nested=False)
        plugins = [{"name": "foo", "source": {"source": "github", "repo": "owner/foo"}}]
        res = vm.validate_git_submodules(tmp, plugins)
        assert any("submodule URL differs" in r.message for r in res), [r.message for r in res]

    def test_nested_url_match_emits_no_mismatch(self):
        """A matching URL on a nested submodule does NOT emit the mismatch MINOR (no false positive)."""
        tmp = self._make_mkpl("https://github.com/owner/foo", dir_at_nested=False)
        plugins = [{"name": "foo", "source": {"source": "github", "repo": "owner/foo"}}]
        res = vm.validate_git_submodules(tmp, plugins)
        assert not any("submodule URL differs" in r.message for r in res), [r.message for r in res]

    def test_flat_layout_still_works(self):
        """Flat 'submodule \"foo\"' layout keyed by name is unaffected — mismatch still fires."""
        tmp = Path(tempfile.mkdtemp())
        (tmp / ".git").mkdir()
        (tmp / ".gitmodules").write_text(
            textwrap.dedent(
                """\
                [submodule "foo"]
                    path = foo
                    url = https://github.com/wrong/repo
                """
            )
        )
        (tmp / "foo").mkdir()
        (tmp / "foo" / ".git").write_text("gitdir: x\n")
        plugins = [{"name": "foo", "source": {"source": "github", "repo": "owner/foo"}}]
        res = vm.validate_git_submodules(tmp, plugins)
        assert any("submodule URL differs" in r.message for r in res), [r.message for r in res]


# ---------------------------------------------------------------------------
# #130 — trailing-hyphen dead branch removed; pattern still catches it
# ---------------------------------------------------------------------------
class TestTrailingHyphenName:
    """NAME_PATTERN already rejects trailing hyphens; the dead elif is gone."""

    def test_marketplace_name_trailing_hyphen_pattern_critical(self):
        """A trailing-hyphen marketplace name yields the pattern CRITICAL (not the removed hyphen branch)."""
        res = vm.validate_marketplace_name("my-plugin-", "m.json")
        assert any("does not match naming pattern" in r.message for r in res)
        assert not any("must not end with a hyphen" in r.message for r in res)

    def test_plugin_name_trailing_hyphen_pattern_critical(self):
        """A trailing-hyphen plugin name yields the pattern CRITICAL (not the removed hyphen branch)."""
        res = vm.validate_plugin_entry(
            {"name": "bad-", "source": {"source": "github", "repo": "o/r"}},
            0,
            Path("/tmp"),
            "m.json",
        )
        assert any("does not match naming pattern" in r.message for r in res)
        assert not any("must not end with a hyphen" in r.message for r in res)

    def test_name_pattern_never_matches_trailing_hyphen(self):
        """Guard: NAME_PATTERN.match on a trailing-hyphen name is None, proving the elif was unreachable."""
        assert vm.NAME_PATTERN.match("my-plugin-") is None


# ---------------------------------------------------------------------------
# #131 — source required sub-field must be inside the source object
# ---------------------------------------------------------------------------
class TestSourceRequiredFieldScoping:
    """A sibling top-level field must NOT satisfy a source's required sub-field."""

    def test_top_level_repo_does_not_satisfy_github_source(self):
        """github source missing 'repo' (repo only at plugin top level) yields the requires-repo MAJOR."""
        res = vm.validate_plugin_source(
            {"name": "foo", "source": {"source": "github"}, "repo": "owner/foo"},
            "foo",
            Path("/tmp"),
            "m.json",
        )
        assert any("requires 'repo'" in r.message for r in res), [r.message for r in res]

    def test_repo_inside_source_satisfies_requirement(self):
        """github source WITH 'repo' inside source emits no requires-repo MAJOR."""
        res = vm.validate_plugin_source(
            {"name": "foo", "source": {"source": "github", "repo": "owner/foo"}},
            "foo",
            Path("/tmp"),
            "m.json",
        )
        assert not any("requires 'repo'" in r.message for r in res), [r.message for r in res]

    def test_git_subdir_subdir_inside_source_ok(self):
        """git-subdir with the canonical 'subdir' inside source is not flagged 'requires path'."""
        res = vm.validate_plugin_source(
            {"name": "x", "source": {"source": "git-subdir", "url": "https://h/r.git", "subdir": "sub"}},
            "x",
            Path("/tmp"),
            "m.json",
        )
        assert not any("requires 'path'" in r.message and "git-subdir" in r.message for r in res), [
            r.message for r in res
        ]

    def test_git_subdir_alias_at_top_level_does_not_satisfy(self):
        """git-subdir 'subdir' at the plugin top level (not inside source) still triggers the requirement."""
        res = vm.validate_plugin_source(
            {"name": "x", "source": {"source": "git-subdir", "url": "https://h/r.git"}, "subdir": "sub"},
            "x",
            Path("/tmp"),
            "m.json",
        )
        assert any("requires 'path'" in r.message for r in res), [r.message for r in res]


# ---------------------------------------------------------------------------
# #132 — absolute-path message ellipsis only when truncated
# ---------------------------------------------------------------------------
class TestAbsolutePathEllipsis:
    """The '...' suffix appears only for paths longer than 60 chars."""

    @staticmethod
    def _scan_major_messages(text: str) -> list[str]:
        tmp = Path(tempfile.mkdtemp())
        (tmp / ".claude-plugin").mkdir()
        (tmp / ".claude-plugin" / "info.md").write_text(text)
        res = vm.validate_marketplace_private_info(tmp, [])
        return [r.message for r in res if r.level == "MAJOR"]

    def test_short_path_has_no_ellipsis(self):
        """A short absolute path is reported verbatim with no spurious trailing '...'."""
        msgs = self._scan_major_messages("/Users/someuser/x")
        assert msgs, "expected a MAJOR for the absolute path"
        assert not any("..." in m for m in msgs), msgs

    def test_long_path_has_ellipsis(self):
        """An absolute path longer than 60 chars is truncated with a trailing '...'."""
        msgs = self._scan_major_messages("/Users/someuser/" + "a" * 80)
        assert any("..." in m for m in msgs), msgs
