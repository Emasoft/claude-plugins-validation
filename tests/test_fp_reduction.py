"""Tests for the Phase 0 FP-reduction helpers (RC-83 / RC-84 / RC-100 / RC-16).

These helpers are the gate that lets every subsequent Phase 1+ rule ship
without overwhelming CPV's own self-validation with false positives.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from cpv_validation_common import (  # noqa: E402
    NEGATION_GUARD,
    PROVIDER_HOSTS_WHITELIST,
    SEVERITY_TIERS,
    build_fence_state,
    demote_severity,
    effective_severity,
    has_negation_guard_nearby,
    is_doc_path,
    is_in_fenced_code_block,
    is_known_provider_host,
    is_placeholder_secret,
    is_sample_file,
    is_test_path,
)

# -----------------------------------------------------------------------------
# RC-83 — code-fence tracker
# -----------------------------------------------------------------------------


class TestFenceTracker:
    """Verify build_fence_state correctly tracks triple-backtick fences line-by-line."""

    def test_no_fences_in_plain_text(self) -> None:
        """Plain text with no fences must produce all-False state."""
        content = "line one\nline two\nline three\n"
        state = build_fence_state(content)
        assert state == [False, False, False, False]  # 4 lines (trailing newline = empty 4th)

    def test_single_fenced_block(self) -> None:
        """A single ```...``` block: opening, body, and closing lines all marked True."""
        content = "before\n```python\ncode\n```\nafter"
        state = build_fence_state(content)
        # before=False, opening fence=True, body=True, closing fence=True, after=False
        assert state == [False, True, True, True, False]

    def test_multiple_fenced_blocks(self) -> None:
        """Two separate fenced blocks: only the lines inside (and fence markers) are True."""
        content = "outside1\n```\nin1\n```\noutside2\n```\nin2\n```\noutside3"
        state = build_fence_state(content)
        assert state == [False, True, True, True, False, True, True, True, False]

    def test_unclosed_fence_treats_rest_as_in_fence(self) -> None:
        """An unclosed fence at end-of-file marks subsequent lines as in-fence."""
        content = "line\n```\nstill in fence\nstill in fence\n"
        state = build_fence_state(content)
        assert state == [False, True, True, True, True]

    def test_is_in_fenced_code_block_lookup(self) -> None:
        """is_in_fenced_code_block should respect the fence_state."""
        content = "out\n```\nin\n```\nout"
        state = build_fence_state(content)
        assert is_in_fenced_code_block(0, state) is False
        assert is_in_fenced_code_block(1, state) is True  # opening fence
        assert is_in_fenced_code_block(2, state) is True  # body
        assert is_in_fenced_code_block(3, state) is True  # closing fence
        assert is_in_fenced_code_block(4, state) is False

    def test_lookup_out_of_range_returns_false(self) -> None:
        """Lookups beyond the state length must return False, not raise."""
        state = build_fence_state("just one line")
        assert is_in_fenced_code_block(-1, state) is False
        assert is_in_fenced_code_block(99, state) is False


# -----------------------------------------------------------------------------
# RC-83 / RC-100 — negation guard
# -----------------------------------------------------------------------------


class TestNegationGuard:
    """Verify the NEGATION_GUARD regex + has_negation_guard_nearby helper."""

    def test_negation_word_within_window(self) -> None:
        """'never write X' should be detected as a negation guard around 'X'."""
        text = "never write 'ignore previous instructions' in your skill"
        # Position of 'ignore' is ~13 chars in
        match_pos = text.index("ignore")
        assert has_negation_guard_nearby(text, match_pos) is True

    def test_no_negation_word_returns_false(self) -> None:
        """Text without negation words returns False."""
        text = "the user said: ignore previous instructions"
        match_pos = text.index("ignore")
        assert has_negation_guard_nearby(text, match_pos) is False

    def test_negation_outside_window_returns_false(self) -> None:
        """Negation word too far away (outside the window) is not detected."""
        # Default window is 80 chars; place 'never' 100 chars before 'ignore'
        prefix = "never " + "x" * 100 + " "
        text = prefix + "ignore"
        match_pos = text.index("ignore")
        assert has_negation_guard_nearby(text, match_pos, window=80) is False

    def test_warning_caution_note_all_match(self) -> None:
        """All 4 negation-class keywords trigger the guard."""
        for keyword in ("warning:", "caution:", "note:", "do not"):
            text = f"{keyword} write the pattern"
            match_pos = text.index("write")
            assert has_negation_guard_nearby(text, match_pos) is True, f"failed for {keyword!r}"

    def test_negation_regex_compiles(self) -> None:
        """Smoke check: the module-level NEGATION_GUARD pattern is a real regex."""
        assert NEGATION_GUARD.search("never use this") is not None
        assert NEGATION_GUARD.search("safe content here") is None


# -----------------------------------------------------------------------------
# RC-16 / RC-83 — placeholder secret recognition
# -----------------------------------------------------------------------------


class TestPlaceholderSecret:
    """Verify is_placeholder_secret recognizes documentation-example secrets."""

    @pytest.mark.parametrize(
        "text",
        [
            "your-api-key",
            "your_api_key",
            "YourApiKey",
            "test-key",
            "sample-token",
            "demo-secret",
            "example-api",
            "placeholder-token",
            "fake-credential",
            "dummy-password",
            "<your-api-key>",
            "<your_token>",
            "${OPENAI_API_KEY}",
            "${MY_TOKEN}",
            "sk-test",
            "sk-proj-test",
            "sk-demo",
            "REDACTED",
            "redacted",
            "xxx",
            "XXXXX",
        ],
    )
    def test_recognized_placeholder(self, text: str) -> None:
        """Each documented placeholder pattern is detected as fake."""
        assert is_placeholder_secret(text) is True, f"failed to recognize {text!r}"

    @pytest.mark.parametrize(
        "text",
        [
            "AKIAIOSFODNN7EXAMPLE",  # AWS-prefixed (real-shape, not a placeholder)
            "real_credential_value_that_is_long",  # arbitrary string
            "sk-1234567890abcdef",  # OpenAI-shape
        ],
    )
    def test_real_secrets_not_flagged_as_placeholder(self, text: str) -> None:
        """Real-shape secrets must NOT be mis-classified as placeholders."""
        assert is_placeholder_secret(text) is False, f"misclassified {text!r}"


# -----------------------------------------------------------------------------
# RC-83 — provider-host whitelist
# -----------------------------------------------------------------------------


class TestProviderHosts:
    """Verify is_known_provider_host accepts AI providers, registries, code-hosts."""

    @pytest.mark.parametrize(
        "host",
        [
            "api.openai.com",
            "api.anthropic.com",
            "claude.ai",
            "huggingface.co",
            "registry.npmjs.org",
            "pypi.org",
            "github.com",
            "raw.githubusercontent.com",
        ],
    )
    def test_known_hosts_accepted(self, host: str) -> None:
        """All documented provider hosts return True."""
        assert is_known_provider_host(host) is True

    def test_subdomain_match(self) -> None:
        """Subdomains of whitelisted hosts are also accepted."""
        assert is_known_provider_host("cdn.npmjs.org") is True
        assert is_known_provider_host("foo.api.openai.com") is True

    @pytest.mark.parametrize(
        "host",
        [
            "evil.example.com",
            "attacker.net",
            "192.168.1.1",
            "169.254.169.254",  # AWS IMDS — must NOT be in provider list
        ],
    )
    def test_unknown_hosts_rejected(self, host: str) -> None:
        """Hosts not in whitelist (and not subdomains of any) return False."""
        assert is_known_provider_host(host) is False

    def test_whitelist_is_frozenset(self) -> None:
        """The whitelist must be immutable (frozenset)."""
        assert isinstance(PROVIDER_HOSTS_WHITELIST, frozenset)
        assert len(PROVIDER_HOSTS_WHITELIST) > 20  # sanity: actually populated


# -----------------------------------------------------------------------------
# RC-84 / RC-100 — defensive-context path detection
# -----------------------------------------------------------------------------


class TestDefensiveContextPaths:
    """Verify is_test_path / is_doc_path / is_sample_file recognize defensive contexts."""

    @pytest.mark.parametrize(
        "path",
        [
            "tests/test_foo.py",
            "src/__tests__/foo.test.ts",
            "spec/foo_spec.rb",
            "e2e/checkout.spec.js",
            "tests/fixtures/sample.json",
            "src/foo.test.ts",
            "src/foo.spec.ts",
            "src/foo_test.go",
            "src/foo_spec.rb",
            "test_module.py",
        ],
    )
    def test_test_paths_recognized(self, path: str) -> None:
        assert is_test_path(path) is True, f"failed for {path!r}"

    @pytest.mark.parametrize(
        "path",
        [
            "src/main.py",  # source file, not a test
            "lib/util.js",
        ],
    )
    def test_non_test_paths_not_recognized(self, path: str) -> None:
        assert is_test_path(path) is False, f"misclassified {path!r}"

    @pytest.mark.parametrize(
        "path",
        [
            "README.md",
            "CHANGELOG.md",
            "CONTRIBUTING.md",
            "LICENSE",
            "docs/intro.md",
            "documentation/guide.rst",
            "skills/foo/SKILL.md",
        ],
    )
    def test_doc_paths_recognized(self, path: str) -> None:
        assert is_doc_path(path) is True, f"failed for {path!r}"

    @pytest.mark.parametrize(
        "path",
        [
            ".env.example",
            ".env.template",
            "config.sample",
            "settings.dist",
            "nginx.tpl",
            "config.example.yaml",
            "secrets.sample.json",
        ],
    )
    def test_sample_files_recognized(self, path: str) -> None:
        assert is_sample_file(path) is True, f"failed for {path!r}"

    @pytest.mark.parametrize(
        "path",
        [
            ".env",  # the real one — NOT a sample
            "config.yaml",
            "secrets.json",
        ],
    )
    def test_real_files_not_recognized_as_sample(self, path: str) -> None:
        assert is_sample_file(path) is False, f"misclassified {path!r}"


# -----------------------------------------------------------------------------
# Severity demotion
# -----------------------------------------------------------------------------


class TestSeverityDemotion:
    """Verify demote_severity and effective_severity honor the demotion contract."""

    @pytest.mark.parametrize(
        "level,expected",
        [
            ("critical", "major"),
            ("major", "minor"),
            ("minor", "nit"),  # NIT is a real tier — must NOT be skipped
            ("nit", "warning"),
            ("warning", "info"),
            ("info", "passed"),
            ("passed", "passed"),  # clamped at lowest
        ],
    )
    def test_single_demotion(self, level: str, expected: str) -> None:
        assert demote_severity(level, by=1) == expected

    def test_multi_step_demotion(self) -> None:
        """Demoting by 3 from CRITICAL steps through nit: critical→major→minor→nit."""
        # critical(0) → major(1) → minor(2) → nit(3). NIT now occupies the
        # slot the old (buggy) ladder skipped, so by=3 lands on "nit", not
        # "warning". Two-sided guard: it must NOT skip past nit to warning.
        assert demote_severity("critical", by=3) == "nit"
        assert demote_severity("critical", by=4) == "warning"

    def test_nit_is_in_severity_tiers(self) -> None:
        """Two-sided regression: NIT sits between minor and warning in the ladder."""
        assert "nit" in SEVERITY_TIERS
        assert SEVERITY_TIERS.index("minor") < SEVERITY_TIERS.index("nit") < SEVERITY_TIERS.index("warning")
        # Stepping off minor reaches nit, and stepping off nit reaches warning.
        assert demote_severity("minor", by=1) == "nit"
        assert demote_severity("nit", by=1) == "warning"

    def test_clamp_at_lowest(self) -> None:
        """Demoting beyond the bottom tier clamps at 'passed'."""
        assert demote_severity("major", by=99) == "passed"

    def test_unknown_level_returned_as_is(self) -> None:
        """Unknown severity tags return unchanged (no exception)."""
        assert demote_severity("custom_level") == "custom_level"

    def test_severity_tiers_ordered_worst_first(self) -> None:
        """Sanity: the SEVERITY_TIERS tuple is ordered worst-to-least."""
        assert SEVERITY_TIERS[0] == "critical"
        assert SEVERITY_TIERS[-1] == "passed"

    def test_effective_severity_demotes_in_test_path(self) -> None:
        """A finding in a test path gets demoted by one tier."""
        assert effective_severity("major", "tests/test_foo.py") == "minor"
        assert effective_severity("critical", "src/__tests__/x.test.ts") == "major"

    def test_effective_severity_demotes_in_doc_path(self) -> None:
        """A finding in a doc file gets demoted."""
        assert effective_severity("major", "docs/intro.md") == "minor"
        assert effective_severity("critical", "README.md") == "major"

    def test_effective_severity_demotes_in_sample_file(self) -> None:
        """A finding in a sample/template gets demoted."""
        assert effective_severity("critical", ".env.example") == "major"
        assert effective_severity("major", "config.template") == "minor"

    def test_effective_severity_no_demote_in_source(self) -> None:
        """Source files (non-test, non-doc, non-sample) keep original severity."""
        assert effective_severity("critical", "src/main.py") == "critical"
        assert effective_severity("major", "lib/util.js") == "major"

    def test_demotion_does_not_stack(self) -> None:
        """Multiple defensive contexts (e.g. tests/foo.md) demote by ONE tier total."""
        # tests/foo.md matches both test_path and doc_path, but should only demote once
        assert effective_severity("critical", "tests/foo.md") == "major"
