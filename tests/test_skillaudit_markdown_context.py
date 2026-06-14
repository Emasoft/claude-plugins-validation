#!/usr/bin/env python3
"""Regression locks for scripts/_skillaudit_markdown_context.py (TRDD-a4260cc6).

The v2.100.0 markdown context classifier suppresses SkillAudit false
positives on safe markdown constructs (inline-code spans, prose,
data-language fenced blocks) and demotes neutral fenced examples
(non-shell code-language fenced blocks, untagged fenced blocks).

Iron rule preserved: matches inside executable shell fences return
``"unknown"`` so the downstream heuristic chain (bash-uplift,
docstring detection, etc.) still classifies them — the classifier
never silently drops a shell-fence finding.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).parent.parent
SCRIPTS_DIR = REPO / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


# ────────────────────────────────────────────────────────────────────────
# Match falls inside a backtick-quoted inline-code span on prose line.
# ────────────────────────────────────────────────────────────────────────


class TestSafeDocInlineCode:
    def test_janitor_arm_inside_backticks_on_prose_line(self) -> None:
        """`/janitor-arm` inside inline-code span on prose → safe_doc."""
        import _skillaudit_markdown_context as ctx

        src = "re-run `/janitor-arm` if no drift surfaces"
        verdict = ctx.classify("README.md", src, 0, "/janitor-arm", "CMD_INJECTION")
        assert verdict == "safe_doc"

    def test_subprocess_run_inside_backticks_on_prose_line(self) -> None:
        """`subprocess.run` inside inline-code span on prose → safe_doc."""
        import _skillaudit_markdown_context as ctx

        src = "Use `subprocess.run` carefully"
        verdict = ctx.classify("README.md", src, 0, "subprocess.run", "CMD_INJECTION")
        assert verdict == "safe_doc"

    def test_curl_url_inside_backticks_on_prose_line(self) -> None:
        """`curl https://example.com` inside inline-code span on prose → safe_doc."""
        import _skillaudit_markdown_context as ctx

        src = "The `curl https://example.com` example"
        verdict = ctx.classify("README.md", src, 0, "curl https://example.com", "CMD_INJECTION")
        assert verdict == "safe_doc"

    def test_line_is_only_inline_code_span(self) -> None:
        """Benign command mention `echo hi` in inline code → safe_literal.

        A backtick span headed by a benign allowlisted command with no
        dangerous args is fully suppressed (zero-FP): it is a 100%-certain
        non-threat, so it must not surface even as a NIT in
        instruction-loadable files. Dangerous inline-code (``curl … | sh``,
        ``cat /etc/passwd``) still stays visible — see the iron-rule tests.
        """
        import _skillaudit_markdown_context as ctx

        src = "`echo hi`"
        verdict = ctx.classify("README.md", src, 0, "echo hi", "CMD_INJECTION")
        assert verdict == "safe_literal"

    def test_sudo_apt_get_inside_backticks_on_prose_line(self) -> None:
        """`sudo apt-get install X` inside inline-code span on prose → safe_doc."""
        import _skillaudit_markdown_context as ctx

        src = "Run `sudo apt-get install X` to set up"
        verdict = ctx.classify("README.md", src, 0, "sudo apt-get install X", "CMD_INJECTION")
        assert verdict == "safe_doc"


# ────────────────────────────────────────────────────────────────────────
# Match is plain prose text (outside any code span and any fenced block).
# Prose is rendered as HTML by markdown engines; nothing executes.
# ────────────────────────────────────────────────────────────────────────


class TestSafeDocProse:
    def test_plain_prose_paragraph_mentioning_curl(self) -> None:
        """Plain prose mentioning curl → safe_doc."""
        import _skillaudit_markdown_context as ctx

        src = "This tool runs curl and wget to fetch data."
        verdict = ctx.classify("README.md", src, 0, "curl", "CMD_INJECTION")
        assert verdict == "safe_doc"

    def test_list_item_with_subprocess_run_mention(self) -> None:
        """List item mentioning subprocess.run → safe_doc."""
        import _skillaudit_markdown_context as ctx

        src = "- Uses subprocess.run heavily for git operations."
        verdict = ctx.classify("README.md", src, 0, "subprocess.run", "CMD_INJECTION")
        assert verdict == "safe_doc"

    def test_heading_mentioning_sudo_apt_get(self) -> None:
        """Heading mentioning sudo apt-get → safe_doc."""
        import _skillaudit_markdown_context as ctx

        src = "## How sudo apt-get integration works"
        verdict = ctx.classify("README.md", src, 0, "sudo apt-get", "CMD_INJECTION")
        assert verdict == "safe_doc"

    def test_table_row_with_gh_release_backticks(self) -> None:
        """Table row with benign `gh release` inline code → safe_literal.

        Benign allowlisted command mention with no dangerous args → fully
        suppressed (zero-FP), same as ``test_line_is_only_inline_code_span``.
        """
        import _skillaudit_markdown_context as ctx

        src = "| Step | Run `gh release` |"
        verdict = ctx.classify("README.md", src, 0, "gh release", "CMD_INJECTION")
        assert verdict == "safe_literal"

    def test_numbered_list_with_os_system_call(self) -> None:
        """Numbered list mentioning os.system → safe_doc."""
        import _skillaudit_markdown_context as ctx

        src = '1. The script calls os.system("clear") on startup.'
        verdict = ctx.classify("README.md", src, 0, "os.system", "CMD_INJECTION")
        assert verdict == "safe_doc"


# ────────────────────────────────────────────────────────────────────────
# Match inside a data-language fenced block. Data formats don't execute.
# ────────────────────────────────────────────────────────────────────────


class TestSafeDocFencedData:
    def test_match_inside_json_fence(self) -> None:
        """Match inside ```json fence → safe_doc (data fence)."""
        import _skillaudit_markdown_context as ctx

        src = '```json\n{"cmd": "curl https://example.com"}\n```'
        # Line 0 is ```json, line 1 is content, line 2 is ```
        verdict = ctx.classify("doc.md", src, 1, "curl https://example.com", "URL_SUSPICIOUS")
        assert verdict == "safe_doc"

    def test_match_inside_yaml_fence(self) -> None:
        """Match inside ```yaml fence → safe_doc (data fence)."""
        import _skillaudit_markdown_context as ctx

        src = "```yaml\ncommand: subprocess.run\n```"
        verdict = ctx.classify("doc.md", src, 1, "subprocess.run", "CMD_INJECTION")
        assert verdict == "safe_doc"

    def test_match_inside_toml_fence(self) -> None:
        """Match inside ```toml fence → safe_doc (data fence)."""
        import _skillaudit_markdown_context as ctx

        src = '```toml\ncmd = "curl example.com"\n```'
        verdict = ctx.classify("doc.md", src, 1, "curl example.com", "URL_SUSPICIOUS")
        assert verdict == "safe_doc"

    def test_match_inside_env_fence(self) -> None:
        """Match inside ```env fence → safe_doc (data fence)."""
        import _skillaudit_markdown_context as ctx

        src = "```env\nWEBHOOK_URL=https://example.com\n```"
        verdict = ctx.classify("doc.md", src, 1, "https://example.com", "URL_SUSPICIOUS")
        assert verdict == "safe_doc"


# ────────────────────────────────────────────────────────────────────────
# Match inside a non-executable code-language fence OR an untagged fence.
# Demote rather than drop — the example might be illustrative but it's
# not executed by the markdown renderer; agent triages at NIT level.
# ────────────────────────────────────────────────────────────────────────


class TestCodeFenceNeutral:
    def test_match_inside_python_fence(self) -> None:
        """Match inside ```python fence → code_fence_neutral, EXCEPT the
        provably-safe static-argv subprocess shape, which issue #81 now
        suppresses (`safe_literal`). `subprocess.run(["x"])` is a list-literal
        argv with no shell=True / interpolation / shell-or-code-interpreter
        argv0 — the provably-safe shape — so it is fully suppressed rather than
        demoted to a publish-blocking NIT. (The general code_fence_neutral rule
        is still covered by test_match_inside_javascript_fence and
        test_match_inside_untagged_fence below.)"""
        import _skillaudit_markdown_context as ctx

        src = '```python\nresult = subprocess.run(["x"])\n```'
        verdict = ctx.classify("doc.md", src, 1, 'subprocess.run(["x"])', "CMD_INJECTION")
        assert verdict == "safe_literal"

    def test_match_inside_javascript_fence(self) -> None:
        """Match inside ```javascript fence → code_fence_neutral."""
        import _skillaudit_markdown_context as ctx

        src = '```javascript\nconst url = "https://example.com";\n```'
        verdict = ctx.classify("doc.md", src, 1, "https://example.com", "URL_SUSPICIOUS")
        assert verdict == "code_fence_neutral"

    def test_match_inside_untagged_fence(self) -> None:
        """Match inside ``` (no language) fence → code_fence_neutral."""
        import _skillaudit_markdown_context as ctx

        src = "```\nrun: subprocess.run\n```"
        verdict = ctx.classify("doc.md", src, 1, "subprocess.run", "CMD_INJECTION")
        assert verdict == "code_fence_neutral"


# ────────────────────────────────────────────────────────────────────────
# Iron rule: shell fences and parse failures return "unknown" so the
# downstream heuristic chain still classifies them. The classifier
# NEVER silently drops a finding in these cases.
# ────────────────────────────────────────────────────────────────────────


class TestUnknown:
    def test_match_inside_bash_fence(self) -> None:
        """Match inside ```bash fence → unknown (executable shell fence)."""
        import _skillaudit_markdown_context as ctx

        src = "```bash\ncurl https://webhook.site/x\n```"
        verdict = ctx.classify("doc.md", src, 1, "curl https://webhook.site/x", "URL_SUSPICIOUS")
        assert verdict == "unknown"

    def test_match_inside_sh_fence(self) -> None:
        """Match inside ```sh fence → unknown (executable shell fence)."""
        import _skillaudit_markdown_context as ctx

        src = "```sh\nsubprocess.run --evil\n```"
        verdict = ctx.classify("doc.md", src, 1, "subprocess.run", "CMD_INJECTION")
        assert verdict == "unknown"

    def test_line_idx_out_of_bounds(self) -> None:
        """line_idx out of bounds → unknown (iron-rule preservation)."""
        import _skillaudit_markdown_context as ctx

        src = "single line of text"
        # Negative
        assert ctx.classify("doc.md", src, -1, "text", "CMD_INJECTION") == "unknown"
        # Past end
        assert ctx.classify("doc.md", src, 5, "text", "CMD_INJECTION") == "unknown"
