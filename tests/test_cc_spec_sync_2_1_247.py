"""CC spec-drift sync for the v2.1.247 window.

Method (the recorded one): `curl` the RAW docs enumerated from ``llms.txt`` and
mechanically set-diff each authoritative table against CPV's constants — never
a WebFetch summary, which has false-negatived twice.

The whole window's plugin-spec surface is two names, and both were confirmed
absent at HEAD before being called a gap (``git show HEAD:<file> | grep -c`` →
0), so nothing here can pass vacuously:

* **``SendFeedback``** — a tools-reference row (``| `SendFeedback` |``), marked
  *"Requires Claude Code v2.1.238 or later"*. It is therefore ALSO a miss of the
  v2.1.236–240 and v2.1.241–246 windows: the changelog only announced the tool
  at .247, while the docs had carried it since .238. **A tool-name diff keyed on
  the changelog window can lag the docs by releases** — the set-diff against the
  live table is what catches it, and it is the reason the diff is re-run over
  the whole table each window rather than over the window's new rows.
  Added to BOTH ``VALID_TOOLS`` (validity) and ``CANONICAL_TOOLS`` (detection
  breadth) — a structural test already pins ``VALID_TOOLS − {Task} ⊆
  CANONICAL_TOOLS``, so a one-sided add would fail the suite.
* **``feedbackDrafts``** — a ``### `<key>` `` heading in settings-reference.md
  (``off`` / ``quiet``), settable in managed settings.

Verified as needing NO change rather than assumed:

* the full ``### <key>`` set-diff against ``KNOWN_SETTINGS_KEYS`` leaves exactly
  ``feedbackDrafts`` at top level; every other doc-side name is a DOTTED nested
  key (``sandbox.*``, ``permissions.*``, ``worktree.*``), which that set models
  by design — a bare leaf entry there would excuse a genuine typo.
* ``BUILTIN_SLASH_COMMANDS`` — the ``/claude-api cost-optimize`` addition is a
  SUBCOMMAND of a bundled skill, not a new slash name; the doc-vs-CPV diff over
  ``commands.md`` came back empty.
* ``VALID_PLUGIN_ENV_VARS`` — ``CLAUDE_CODE_SEND_FEEDBACK`` is a per-session
  user toggle, not a plugin-scoped variable, and that set is a curated
  plugin-authoring allowlist rather than an env-vars.md catalog.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).parent.parent
SCRIPTS = REPO / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def _tool_findings(tools: str) -> list[str]:
    """Run the REAL validate_tools_field and return the 'unknown tool' messages."""
    from validate_agent import (  # type: ignore[import-not-found]
        AgentValidationReport,
        validate_tools_field,
    )

    report = AgentValidationReport()
    validate_tools_field({"tools": tools}, "x.md", report)
    return [
        str(getattr(r, "message", r)) for r in report.results if "Unknown tool" in str(getattr(r, "message", r))
    ]


class TestSendFeedbackTool:
    def test_accepted_by_the_real_agent_validator(self) -> None:
        assert not [m for m in _tool_findings("SendFeedback") if "SendFeedback" in m]

    def test_accepted_alongside_ordinary_tools(self) -> None:
        found = _tool_findings("Read, Bash, SendFeedback")
        assert not [m for m in found if "SendFeedback" in m]

    def test_positive_control_bogus_tool_still_flagged(self) -> None:
        """Without this, the two assertions above pass even if tool checking died."""
        assert [m for m in _tool_findings("SendFeedbackz") if "SendFeedbackz" in m]

    def test_present_in_the_detection_set_too(self) -> None:
        """VALID_TOOLS is validity; CANONICAL_TOOLS is permission-match breadth."""
        from cpv_tool_permission_match import CANONICAL_TOOLS  # type: ignore[import-not-found]

        assert "SendFeedback" in CANONICAL_TOOLS


class TestFeedbackDraftsSetting:
    def test_known_settings_key(self) -> None:
        from cc_scope_rules import KNOWN_SETTINGS_KEYS  # type: ignore[import-not-found]

        assert "feedbackDrafts" in KNOWN_SETTINGS_KEYS

    def test_typo_is_still_unknown(self) -> None:
        """Control: the set stays a typo detector."""
        from cc_scope_rules import KNOWN_SETTINGS_KEYS  # type: ignore[import-not-found]

        for typo in ("feedbackDraft", "feedBackDrafts", "feedbackdrafts"):
            assert typo not in KNOWN_SETTINGS_KEYS
