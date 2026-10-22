#!/usr/bin/env python3
"""Issue #246: URL schemes, MDX/JSX literals and ~~~ fences are not broken file links.

Every test is two-sided: the false-positive shape stops firing AND a real
broken relative link (also next to the false-positive shape) is still reported.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

import cpv_validation_common as common  # noqa: E402
import validate_documentation as vdoc  # noqa: E402

JSX_PAGE = """# Chapter

export const Memory = () => {
  const items = [{ id: "memory-dir", label: "Memory" }](ghost-target).map((x) => x.id);
  return items;
};

See [broken](missing-target.md) here.
"""


def _doc_messages(tmp_path: Path, text: str) -> list[str]:
    (tmp_path / "README.md").write_text(text, encoding="utf-8")
    report = vdoc.DocumentationValidationReport()
    vdoc.validate_broken_links(tmp_path, report)
    return [r.message for r in report.results if r.level != "PASSED"]


def _ref_messages(tmp_path: Path, text: str) -> list[str]:
    md = tmp_path / "README.md"
    md.write_text(text, encoding="utf-8")
    report = common.ValidationReport()
    common.validate_md_file_paths(md, tmp_path, report)
    return [r.message for r in report.results if r.level != "PASSED"]


def test_doc_scheme_target_not_reported_real_broken_still_is(tmp_path: Path) -> None:
    """A claude-cli:// link target is skipped; a broken relative link beside it still fires."""
    msgs = _doc_messages(tmp_path, "[open](claude-cli://open/session) and [bad](nope.md)\n")
    assert len(msgs) == 1 and "nope.md" in msgs[0]


def test_ref_scheme_target_not_reported_real_broken_still_is(tmp_path: Path) -> None:
    """validate_md_file_paths skips scheme:// targets; a broken relative link still fires."""
    msgs = _ref_messages(tmp_path, "[open](claude-cli://open/session) and [bad](nope.md)\n")
    assert len(msgs) == 1 and "nope.md" in msgs[0]


def test_doc_jsx_literal_not_parsed_as_link_next_line_broken_still_is(tmp_path: Path) -> None:
    """A JS object literal in link position is skipped; a broken link on the line right after the export block fires."""
    msgs = _doc_messages(tmp_path, JSX_PAGE)
    assert len(msgs) == 1 and "missing-target.md" in msgs[0]


def test_doc_broken_link_on_line_right_after_export_block_still_reported(tmp_path: Path) -> None:
    """A real broken link on the very next line after an export block (no blank line) is still reported."""
    page = "export const A = [{ id: 1 }];\n[bad](nope.md)\n"
    msgs = _doc_messages(tmp_path, page)
    assert len(msgs) == 1 and "nope.md" in msgs[0]


def test_doc_broken_link_on_same_line_as_brace_bracket_still_reported(tmp_path: Path) -> None:
    """A real broken link on a line that also holds `[{` is still reported."""
    msgs = _doc_messages(tmp_path, "const a = [{ id: 1 }].map(f); see [bad](nope.md)\n")
    assert len(msgs) == 1 and "nope.md" in msgs[0]


def test_doc_multiline_brace_link_text_skipped(tmp_path: Path) -> None:
    """Multi-line link text opening with a brace (JS literal) is skipped; the next real broken link fires."""
    page = "[{\n id: 1,\n}](x.md)\n\n[bad](nope.md)\n"
    msgs = _doc_messages(tmp_path, page)
    assert len(msgs) == 1 and "nope.md" in msgs[0]


def test_doc_plain_link_text_with_colon_not_skipped(tmp_path: Path) -> None:
    """Ordinary link text (no leading brace) is never treated as a JS literal."""
    msgs = _doc_messages(tmp_path, "[note: see this](nope.md)\n")
    assert len(msgs) == 1 and "nope.md" in msgs[0]


def test_ref_tilde_fence_links_not_reported(tmp_path: Path) -> None:
    """Links inside a ~~~ fence and inside a ```` fence holding a nested ``` fence are examples; links outside still fire."""
    page = "````markdown\n```\ncode\n```\n[a](in-fence-one.md)\n````\n\n~~~markdown\n[b](in-fence-two.md)\n~~~\n\n[c](outside.md)\n"
    msgs = _ref_messages(tmp_path, page)
    assert len(msgs) == 1 and "outside.md" in msgs[0]


@pytest.mark.parametrize("check", [_doc_messages, _ref_messages])
def test_crlf_fence_closer_ends_block(tmp_path: Path, check) -> None:
    """A CRLF file closes its fence on "```\r"; the broken link after the block is still reported."""
    page = "```md\r\n[in](hidden.md)\r\n```\r\n\r\n[bad](nope.md)\r\n"
    msgs = check(tmp_path, page)
    assert len(msgs) == 1 and "nope.md" in msgs[0]


@pytest.mark.parametrize("check", [_doc_messages, _ref_messages])
def test_fence_closer_with_trailing_spaces(tmp_path: Path, check) -> None:
    """A closing fence followed by trailing spaces still closes; the link after it is reported."""
    page = "```md\n[in](hidden.md)\n```   \n\n[bad](nope.md)\n"
    msgs = check(tmp_path, page)
    assert len(msgs) == 1 and "nope.md" in msgs[0]


@pytest.mark.parametrize("check", [_doc_messages, _ref_messages])
def test_indented_list_item_fence_is_stripped(tmp_path: Path, check) -> None:
    """A fence indented under a list item (4+ spaces) is still a fence: the link inside is an example, the one after is reported."""
    page = "1. Step\n\n    ```md\n    [in](hidden.md)\n    ```\n\n[bad](nope.md)\n"
    msgs = check(tmp_path, page)
    assert len(msgs) == 1 and "nope.md" in msgs[0]


@pytest.mark.parametrize("check", [_doc_messages, _ref_messages])
def test_three_space_indented_fence_is_a_fence(tmp_path: Path, check) -> None:
    """A fence indented 3 spaces is a real fence: the link inside is an example, the one after is reported."""
    page = "   ```\n[in](hidden.md)\n   ```\n[bad](nope.md)\n"
    msgs = check(tmp_path, page)
    assert len(msgs) == 1 and "nope.md" in msgs[0]


@pytest.mark.parametrize("check", [_doc_messages, _ref_messages])
def test_backtick_fence_with_backtick_in_info_string_is_not_a_fence(tmp_path: Path, check) -> None:
    """A backtick "fence" whose info string has a backtick is inline code, so it hides nothing."""
    page = "```js `x`\n[bad](nope.md)\n```\n"
    msgs = check(tmp_path, page)
    assert any("nope.md" in m for m in msgs)


@pytest.mark.parametrize("check", [_doc_messages, _ref_messages])
def test_unclosed_fence_does_not_blind_the_check(tmp_path: Path, check) -> None:
    """An unclosed ``` opener is plain text (fail-safe), so a broken link below it is reported."""
    msgs = check(tmp_path, "```\n\n[bad](nope.md)\n")
    assert any("nope.md" in m for m in msgs)


@pytest.mark.parametrize("check", [_doc_messages, _ref_messages])
def test_file_scheme_target_still_reported(tmp_path: Path, check) -> None:
    """A file: target is a local file and is NOT skipped as a URL."""
    msgs = check(tmp_path, "[x](file:///no/such/file.md)\n")
    assert any("file:///no/such/file.md" in m for m in msgs)


@pytest.mark.parametrize("check", [_doc_messages, _ref_messages])
def test_windows_drive_double_slash_is_not_a_url(tmp_path: Path, check) -> None:
    """`C://x` (one-letter scheme) is not treated as a URL and is reported."""
    msgs = check(tmp_path, "[x](C://missing/x.md)\n")
    assert any("C://missing/x.md" in m for m in msgs)


def test_doc_template_style_brace_text_with_colon_is_still_checked(tmp_path: Path) -> None:
    """Decision: `[{config}: options](missing.md)` is ordinary link text (no `key:` right after the brace), so it is reported."""
    msgs = _doc_messages(tmp_path, "[{config}: options](missing.md)\n")
    assert len(msgs) == 1 and "missing.md" in msgs[0]


def test_ref_jsx_literal_skipped_next_line_broken_still_is(tmp_path: Path) -> None:
    """validate_md_file_paths skips a JS object literal in link position; the broken link after the export block fires."""
    msgs = _ref_messages(tmp_path, JSX_PAGE)
    assert len(msgs) == 1 and "missing-target.md" in msgs[0]


def test_ref_template_style_brace_text_with_colon_is_still_checked(tmp_path: Path) -> None:
    """`[{config}: options](missing.md)` is ordinary link text for validate_md_file_paths too."""
    msgs = _ref_messages(tmp_path, "[{config}: options](missing.md)\n")
    assert len(msgs) == 1 and "missing.md" in msgs[0]


def test_many_unclosed_openers_stay_fast_and_linear() -> None:
    """20000 unclosed openers finish quickly (no quadratic closer scan) and none is stripped."""
    import time

    text = "```\n" * 1 + "```js x\n" * 20000
    start = time.monotonic()
    out = common._strip_fenced_blocks(text)
    assert time.monotonic() - start < 2.0
    assert out.count("```js x") == 20000
