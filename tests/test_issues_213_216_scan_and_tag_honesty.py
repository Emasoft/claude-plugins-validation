#!/usr/bin/env python3
"""Issues #213 / #215 / #216 / #218 d3 — a scan or a tag must never LIE.

Four defects, one shape: something that did not happen was recorded as if it
had. A timed-out secret scan printed `[OK] RAN`; a cache keyed on a stale flag
list replayed a pre-fix "0 findings"; a top-level-only glob read a
``tests/unit/`` suite as "no tests"; a dependency tag left at the previous
attempt's commit was pushed as the release.

Each test below fails if the corresponding lie comes back.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import generate_plugin_repo  # noqa: E402
import validate_security  # noqa: E402


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=str(repo), capture_output=True, text=True, check=True)


# ---------------------------------------------------------------------------
# #218 defect 3 — an aborted scan must not render as a clean one
# ---------------------------------------------------------------------------


class TestTimeoutIsNotAVerdict:
    def test_timeout_status_renders_alarming_not_ok(self) -> None:
        """The TIMEOUT row must not wear the `[OK]` glyph a clean RAN row wears."""
        table = validate_security.format_scan_step_table(
            [{"num": 24, "name": "External: trufflehog", "status": "TIMEOUT", "findings": 1, "files": "", "details": "scan INCOMPLETE"}]
        )
        assert "TIMEOUT" in table
        assert "[OK]" not in table, "an aborted scan rendered with the clean-run glyph"

    def test_incomplete_mark_is_set_and_cleared(self) -> None:
        """The incomplete flag is per-run state, so it must clear as well as set."""
        validate_security._mark_scan_incomplete("trufflehog", True)
        assert validate_security.scan_was_incomplete("trufflehog") is True
        validate_security._mark_scan_incomplete("trufflehog", False)
        assert validate_security.scan_was_incomplete("trufflehog") is False

    def test_unknown_scanner_is_not_incomplete(self) -> None:
        """Positive control: the predicate is not simply always-True."""
        assert validate_security.scan_was_incomplete("no-such-scanner") is False


# ---------------------------------------------------------------------------
# #219 regression — the cache must key on the flags actually passed
# ---------------------------------------------------------------------------


class TestCacheKeyTracksTheRealFlags:
    SRC = (REPO_ROOT / "scripts" / "validate_security.py").read_text(encoding="utf-8")

    def test_results_flag_has_exactly_one_spelling(self) -> None:
        """The literal must appear once — in the constant — and nowhere else.

        v5.13.0 added the widened result set to the subprocess call but left the
        cache's curated flag copy at the old list, so hosts holding a pre-fix
        "0 findings" entry kept replaying it and the fix stayed invisible. Two
        spellings is how that happens; one constant is why it cannot recur.
        """
        literal = "--results=verified,unknown,unverified,filtered_unverified"
        assert self.SRC.count(f'"{literal}"') == 1, "the result-set flag is spelled in more than one place again"
        assert "TRUFFLEHOG_RESULTS_FLAG" in self.SRC

    def test_cache_argv_includes_the_constant_and_exclude_paths(self) -> None:
        """The cache's flag list must carry both post-fix flags."""
        assert validate_security.TRUFFLEHOG_RESULTS_FLAG.startswith("--results=")
        # The stub is built inline in _task_specialist; pin its two additions.
        assert "TRUFFLEHOG_RESULTS_FLAG,\n" in self.SRC
        stub_start = self.SRC.index('if binary_hint == "trufflehog":')
        stub = self.SRC[stub_start : stub_start + 1200]
        assert "TRUFFLEHOG_RESULTS_FLAG" in stub
        assert '"--exclude-paths"' in stub

    def test_incomplete_scan_is_not_cached(self) -> None:
        """A timed-out run must not be frozen into the cache as a result."""
        assert "if scan_was_incomplete(scanner_name):" in self.SRC


# ---------------------------------------------------------------------------
# #215 — a tests/unit/ layout is a real suite
# ---------------------------------------------------------------------------


class TestSubdirectoryTestLayoutIsFound:
    def test_emitted_gate_uses_rglob(self) -> None:
        src = (REPO_ROOT / "scripts" / "generate_plugin_repo.py").read_text(encoding="utf-8")
        assert 'test_dir.rglob("test_*.py")' in src
        assert 'test_dir.glob("test_*.py")' not in src, "the top-level-only probe is back"

    def test_workflow_guard_finds_nested_tests(self, tmp_path: Path) -> None:
        """Run the emitted shell guard for real against a tests/unit/ tree."""
        (tmp_path / "tests" / "unit").mkdir(parents=True)
        (tmp_path / "tests" / "unit" / "test_x.py").write_text("def test_x(): pass\n")
        guard = '[ -d "tests" ] && [ -n "$(find tests -name \'test_*.py\' -type f -print -quit)" ]'
        r = subprocess.run(["/bin/sh", "-c", guard], cwd=str(tmp_path))
        assert r.returncode == 0, "nested tests/unit/ suite read as absent"

    def test_workflow_guard_still_rejects_an_empty_tree(self, tmp_path: Path) -> None:
        """Positive control: the guard must still say NO when there are no tests."""
        (tmp_path / "tests").mkdir()
        guard = '[ -d "tests" ] && [ -n "$(find tests -name \'test_*.py\' -type f -print -quit)" ]'
        r = subprocess.run(["/bin/sh", "-c", guard], cwd=str(tmp_path))
        assert r.returncode != 0, "an empty tests/ dir passed the guard"


# ---------------------------------------------------------------------------
# #216 — a retry must not push the previous attempt's tag
# ---------------------------------------------------------------------------


class TestTagFollowsHead:
    def test_moves_an_unpushed_tag_to_head(self, tmp_path: Path, monkeypatch) -> None:
        import publish

        repo = tmp_path / "r"
        repo.mkdir()
        _git(repo, "init", "-q")
        _git(repo, "config", "user.email", "t@t.t")
        _git(repo, "config", "user.name", "t")
        (repo / "a").write_text("1\n")
        _git(repo, "add", "a")
        _git(repo, "commit", "-qm", "first attempt")
        _git(repo, "tag", "-a", "v1.0.0", "-m", "Release v1.0.0")
        stale = _git(repo, "rev-parse", "HEAD").stdout.strip()
        # The fix commit the retry is supposed to release.
        (repo / "a").write_text("2\n")
        _git(repo, "add", "a")
        _git(repo, "commit", "-qm", "the fix that made the retry pass")
        head = _git(repo, "rev-parse", "HEAD").stdout.strip()
        assert stale != head

        monkeypatch.setattr(publish, "_remote_tag_state", lambda *_a, **_k: False)
        assert publish._ensure_tag_at_head(repo, "v1.0.0", "Release v1.0.0") is True
        moved = _git(repo, "rev-list", "-n", "1", "v1.0.0").stdout.strip()
        assert moved == head, "the release tag still points at the interrupted attempt's commit"

    def test_refuses_when_the_tag_is_already_on_the_remote(self, tmp_path: Path, monkeypatch) -> None:
        """Published tags are immutable — refuse rather than rewrite one."""
        import publish

        repo = tmp_path / "r"
        repo.mkdir()
        _git(repo, "init", "-q")
        _git(repo, "config", "user.email", "t@t.t")
        _git(repo, "config", "user.name", "t")
        (repo / "a").write_text("1\n")
        _git(repo, "add", "a")
        _git(repo, "commit", "-qm", "released")
        _git(repo, "tag", "-a", "v1.0.0", "-m", "Release v1.0.0")
        published = _git(repo, "rev-parse", "HEAD").stdout.strip()
        (repo / "a").write_text("2\n")
        _git(repo, "add", "a")
        _git(repo, "commit", "-qm", "later work")

        monkeypatch.setattr(publish, "_remote_tag_state", lambda *_a, **_k: True)
        assert publish._ensure_tag_at_head(repo, "v1.0.0", "Release v1.0.0") is False
        assert _git(repo, "rev-list", "-n", "1", "v1.0.0").stdout.strip() == published

    def test_refuses_when_the_remote_cannot_be_read(self, tmp_path: Path, monkeypatch) -> None:
        """An unreachable remote is not consent to move a tag."""
        import publish

        repo = tmp_path / "r"
        repo.mkdir()
        _git(repo, "init", "-q")
        _git(repo, "config", "user.email", "t@t.t")
        _git(repo, "config", "user.name", "t")
        (repo / "a").write_text("1\n")
        _git(repo, "add", "a")
        _git(repo, "commit", "-qm", "one")
        _git(repo, "tag", "-a", "v1.0.0", "-m", "Release v1.0.0")
        (repo / "a").write_text("2\n")
        _git(repo, "add", "a")
        _git(repo, "commit", "-qm", "two")

        monkeypatch.setattr(publish, "_remote_tag_state", lambda *_a, **_k: None)
        assert publish._ensure_tag_at_head(repo, "v1.0.0", "Release v1.0.0") is False

    def test_dependency_tag_goes_through_the_same_guard(self) -> None:
        """#216's real bite: dependents resolve against the `name--vX.Y.Z` tag.

        That branch used to be a bare 'already exists locally — skipping'.
        """
        src = (REPO_ROOT / "scripts" / "publish.py").read_text(encoding="utf-8")
        assert "_ensure_tag_at_head(plugin_root, dep_tag_name" in src
        assert 'print(f"{YELLOW}  Tag {dep_tag_name} already exists locally — skipping.{NC}")' not in src


# ---------------------------------------------------------------------------
# #213 / #214 — the emitted hook
# ---------------------------------------------------------------------------


class TestEmittedHookHonesty:
    HOOK = generate_plugin_repo.gen_pre_push_hook(
        generate_plugin_repo.PluginParams(
            name="demo-plugin",
            description="demo",
            author="t",
            author_email="t@t.t",
        )
    )

    def test_unresolvable_ref_fails_closed(self) -> None:
        assert "does not resolve to a local ref" in self.HOOK
        assert 'git rev-parse --verify --quiet "$3"' in self.HOOK

    def test_header_names_the_real_writer(self) -> None:
        """#214 — the header claimed publish.py rewrites it; cpv-standardize does."""
        assert "cpv-standardize" in self.HOOK
        assert "Auto-generated by scripts/publish.py's" not in self.HOOK
