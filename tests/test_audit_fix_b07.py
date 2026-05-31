#!/usr/bin/env python3
"""Regression tests for the b07 audit fixes in cpv_validation_common.py.

Pins five audit findings against cpv_validation_common.py so they can't regress:

- Finding 41 (REAL fix): ``parse_frontmatter`` must NOT treat an INDENTED ``---``
  inside a YAML block scalar as the closing delimiter — doing so truncated the
  scalar value and silently dropped every field after it (e.g. ``allowed-tools``)
  into the body. Two-sided: the broken-before input now parses fully, and a
  genuinely-unterminated frontmatter still returns ``None``.
- Finding 40 (hygiene + refutation): ``CLOUD_IMDS_PATTERNS`` no longer carries a
  byte-identical duplicate of the AWS ``169.254.169.254`` pattern under "Azure".
  The duplicate was dead code (the RC-65 consumer ``break``s on the first match
  per line, so it never produced a duplicate finding); the test pins that no two
  patterns are byte-identical AND that the Azure IP is still detected.
- Finding 9 (refutation guard): ``detect_mcp_unbounded_retry`` (RC-55) is
  CORRECTLY oriented — it fires on an unbounded retry-on-error ``while True`` (even
  one that breaks on success, because the failure path has no decay) and stays
  clean on bounded "give up after error" and ``time.sleep`` backoff shapes.
  Weakening this would be a security false-negative; the test locks the orientation.
- Finding 126 (hygiene): ``VALID_TOOLS`` contains ``"Workflow"`` exactly once and
  retains its full membership.

CPV_SCAN_CACHE is irrelevant to these pure-function/data assertions, but probes
that drive the scanner elsewhere should set it; these tests touch neither cache
nor disk.
"""

from __future__ import annotations

import sys
from pathlib import Path

scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

from cpv_validation_common import (  # noqa: E402
    CLOUD_IMDS_PATTERNS,
    VALID_TOOLS,
    detect_mcp_unbounded_retry,
    parse_frontmatter,
)


class TestFrontmatterBlockScalarDelimiter:
    """parse_frontmatter must not split on a ``---`` line inside a block scalar."""

    # A literal block scalar (|) whose body contains a line that is exactly
    # "---" (indented, as block-scalar content must be). The REAL closing
    # delimiter is the later column-0 "---". The trailing `allowed-tools` field
    # MUST survive — before the fix it was silently dropped into the body.
    _CONTENT = (
        "---\n"
        "name: demo\n"
        "description: |\n"
        "  first line\n"
        "  ---\n"
        "  third line\n"
        "allowed-tools: Read, Write\n"
        "---\n"
        "body text here\n"
    )

    def test_block_scalar_dashes_do_not_truncate_value(self):
        fm, body, end_line = parse_frontmatter(self._CONTENT)
        assert fm is not None, "valid frontmatter with a '---' line in a block scalar must parse"
        # The block scalar keeps ALL of its content, including the inner '---'.
        assert fm["description"].splitlines()[0] == "first line"
        assert "---" in fm["description"], "the indented '---' must stay inside the scalar value"
        assert "third line" in fm["description"]

    def test_field_after_block_scalar_is_not_dropped(self):
        fm, body, _ = parse_frontmatter(self._CONTENT)
        assert fm is not None
        # This is the load-bearing regression guard: before the fix the closing
        # delimiter was matched on the indented '---', so `allowed-tools` and the
        # rest of the file leaked into `body` and vanished from the frontmatter.
        assert fm.get("allowed-tools") == "Read, Write", (
            "a field declared AFTER a block scalar containing '---' must survive"
        )
        assert body.strip() == "body text here"

    def test_end_line_points_past_real_closing_delimiter(self):
        _fm, _body, end_line = parse_frontmatter(self._CONTENT)
        # The real closing '---' is line index 7 (0-based); fm_end_line is idx+1 = 8.
        assert end_line == 8, f"expected end_line 8 (past the col-0 closing '---'), got {end_line}"

    def test_substring_dashes_in_single_line_value_still_ok(self):
        """Two-sided guard: a '---' SUBSTRING in a one-line value is untouched."""
        content = "---\nname: t\ndescription: use --- as a separator here\n---\nBody.\n"
        fm, body, _ = parse_frontmatter(content)
        assert fm is not None
        assert fm["description"] == "use --- as a separator here"
        assert "Body." in body

    def test_trailing_whitespace_on_closing_delimiter_still_recognized(self):
        """rstrip (not strip) keeps trailing-whitespace delimiters valid."""
        fm, body, _ = parse_frontmatter("---\nname: x\n--- \nbody")
        assert fm == {"name": "x"}
        assert body.strip() == "body"

    def test_genuinely_unterminated_frontmatter_still_none(self):
        """Two-sided guard: a frontmatter with no col-0 closing '---' is still None."""
        fm, _body, end = parse_frontmatter("---\nname: x\ndescription: no closing fence\n\nBody.\n")
        assert fm is None
        assert end == 0


class TestCloudImdsNoDuplicatePattern:
    """CLOUD_IMDS_PATTERNS: the dead AWS/Azure 169.254.169.254 duplicate is gone."""

    def test_no_byte_identical_duplicate_patterns(self):
        sources = [p.pattern for p in CLOUD_IMDS_PATTERNS]
        dupes = sorted({s for s in sources if sources.count(s) > 1})
        assert not dupes, f"byte-identical (dead) duplicate IMDS patterns remain: {dupes}"

    def test_169_254_169_254_appears_exactly_once(self):
        sources = [p.pattern for p in CLOUD_IMDS_PATTERNS]
        n = sources.count(r"\b169\.254\.169\.254\b")
        assert n == 1, f"the 169.254.169.254 pattern must appear exactly once, found {n}"

    def test_azure_imds_ip_still_detected_via_aws_entry(self):
        """Removing the dead duplicate must not lose coverage: Azure shares the IP."""
        azure = "169.254.169.254/metadata/identity/oauth2/token"
        assert any(p.search(azure) for p in CLOUD_IMDS_PATTERNS), "Azure IMDS IP must still match"

    def test_other_providers_still_covered(self):
        def matches(s: str) -> bool:
            return any(p.search(s) for p in CLOUD_IMDS_PATTERNS)

        assert matches("metadata.google.internal")  # GCP host
        assert matches("169.254.170.2")  # GCP variant
        assert matches("ManagedIdentityExtension")  # Azure MIE
        assert matches("100.100.100.200")  # Alibaba
        assert matches("ECS_CONTAINER_METADATA_URI_V4")  # ECS env


class TestRc55RetryOrientation:
    """RC-55 detect_mcp_unbounded_retry is correctly oriented (finding 9 refuted)."""

    def test_unbounded_retry_with_break_on_success_fires(self):
        """while True + immediate retry-on-error + break-on-success → abuse, fires."""
        code = (
            "while True:\n"
            "    try:\n"
            "        requests.get(u)\n"
            "        break\n"
            "    except Exception:\n"
            "        continue\n"
        )
        assert detect_mcp_unbounded_retry(code), (
            "an unbounded retry-on-error loop is a rate-limit-abuse vector even when it "
            "breaks on success — the failure path has no decay; RC-55 must fire"
        )

    def test_truly_unbounded_no_exit_fires(self):
        code = "while True:\n    try:\n        requests.get(u)\n    except Exception:\n        continue\n"
        assert detect_mcp_unbounded_retry(code)

    def test_give_up_after_error_is_bounded_and_clean(self):
        """break INSIDE except = terminate on first failure → bounded, no finding."""
        code = (
            "while True:\n"
            "    try:\n"
            "        requests.get(u)\n"
            "        x = 1\n"
            "    except Exception:\n"
            "        break\n"
        )
        assert not detect_mcp_unbounded_retry(code)

    def test_backoff_sleep_before_continue_is_clean(self):
        """time.sleep before continue is a legitimate backoff → no finding."""
        code = (
            "while True:\n"
            "    try:\n"
            "        requests.get(u)\n"
            "        break\n"
            "    except Exception:\n"
            "        time.sleep(5)\n"
            "        continue\n"
        )
        assert not detect_mcp_unbounded_retry(code)


class TestValidToolsNoDuplicateWorkflow:
    """VALID_TOOLS carries 'Workflow' exactly once (finding 126)."""

    def test_workflow_present_once(self):
        # VALID_TOOLS is a set, so membership is the observable invariant.
        assert "Workflow" in VALID_TOOLS

    def test_source_has_single_workflow_entry(self):
        """The set literal in source must declare 'Workflow' only once (no dead dup)."""
        src = (scripts_dir / "cpv_validation_common.py").read_text(encoding="utf-8")
        count = src.count('    "Workflow",  #')
        assert count == 1, f"expected one 'Workflow' set entry in source, found {count}"

    def test_core_tools_intact(self):
        for t in ("Read", "Bash", "Task", "Agent", "Skill", "Monitor", "SlashCommand", "MCPSearch"):
            assert t in VALID_TOOLS, f"{t} unexpectedly missing from VALID_TOOLS"
