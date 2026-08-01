#!/usr/bin/env python3
"""
Claude Plugins Validation - Documentation Validator

Validates README.md and documentation files according to best practices.
Implements 13 documentation validation rules:

1. README.md should exist at plugin root
2. README should contain installation instructions
3. README should contain usage examples
4. README should contain description section
5. README should have proper markdown formatting
6. No broken internal links
7. CHANGELOG.md recommended
8. Heading hierarchy should have no skips
9. Code blocks should be closed
10. Code blocks should have language tags
11. List formatting should be proper
12. Table structure should be valid
13. Image references should be valid

Usage:
    uv run python scripts/validate_documentation.py path/to/plugin/
    uv run python scripts/validate_documentation.py path/to/plugin/ --verbose
    uv run python scripts/validate_documentation.py path/to/plugin/ --json

Exit codes:
    0 - All checks passed
    1 - CRITICAL issues found
    2 - MAJOR issues found
    3 - MINOR issues found
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from cpv_validation_common import (
    COLORS,
    ValidationReport,
    get_gitignore_filter,
    is_test_path,
    save_report_and_print_summary,
)

# =============================================================================
# Documentation Validation Report
# =============================================================================

# Dev-scratch / build / artifact directory names that are gitignored by
# convention across Emasoft plugins. The link / image checkers skip any path
# whose relative form starts with one of these segments. The gitignore-aware
# walk (GitignoreFilter) already prunes anything listed in the plugin's own
# .gitignore; this hardcoded set is a belt-and-suspenders fallback for plugins
# whose .gitignore happens NOT to list a conventional dev dir, so we never
# emit MAJOR "broken link" findings against unshipped scratch content.
_DEV_SCRATCH_DIRS: frozenset[str] = frozenset(
    {
        "docs_dev",
        "reports",
        "reports_dev",
        "scripts_dev",
        "samples_dev",
        "examples_dev",
        "tests_dev",
        "downloads_dev",
        "libs_dev",
        "builds_dev",
        ".trashcan",
        ".git",
        "node_modules",
        ".venv",
    }
)

# `templates/` ships in the package but its files contain INTENTIONAL
# placeholders (e.g. `<!-- https://... -->`, `owner/repo`, `<plugin-name>`)
# that are not real links. The LINK / IMAGE checkers skip it specifically;
# other checkers (which are README-only) are unaffected.
_LINK_CHECK_SKIP_DIRS: frozenset[str] = _DEV_SCRATCH_DIRS | {"templates"}

# Link targets that are NOT local relative files and must never be resolved
# against the filesystem.
_NON_LOCAL_LINK_PREFIXES: tuple[str, ...] = (
    "http://",
    "https://",
    "mailto:",
    "tel:",
    "ftp://",
    "//",  # protocol-relative URL (e.g. //cdn.example.com/x.png)
    "/",  # absolute path / doc-site route (e.g. /en/sub-agents) — not a local file
    "#",  # pure in-page anchor
    "data:",  # inline data URI (images)
)


# Placeholder / glob / regex link targets that are documentation examples,
# NOT real file paths. Mirrors the convention used by the backtick-path checker
# in cpv_validation_common (`_is_template_or_example_path`) so link/image
# targets are judged the same way: template vars ({var}, <placeholder>, $VAR,
# YYYY…, my-plugin/your-… tokens), glob patterns (*.md), and regex/meta chars.
_PLACEHOLDER_TARGET_RE = re.compile(
    r"[{}<>]|\$\w|YYYY|placeholder|my-plugin|my-agent|my-skill|your-",
    re.IGNORECASE,
)
_REGEX_META_TARGET_RE = re.compile(r"[?!\\^|+\[\]]")


def _is_placeholder_target(target: str) -> bool:
    """Return True if a link/image target is a doc placeholder, not a real path.

    Such targets (e.g. ``{link_target}``, ``<owner>/<repo>``, ``references/*.md``,
    ``["']([a-z0-9_-]+)``) can never resolve to a file on disk and would be
    false-positive "broken link" findings. We treat them as intentional
    documentation examples and skip them — same policy CPV's backtick-path
    checker already applies.
    """
    if _PLACEHOLDER_TARGET_RE.search(target):
        return True
    if "*" in target:  # glob: *.md, **/*.py
        return True
    return bool(_REGEX_META_TARGET_RE.search(target))


def _is_under_skip_dir(rel_path: Path, skip_dirs: frozenset[str]) -> bool:
    """Return True if any path segment of `rel_path` is in `skip_dirs`.

    Matching on every segment (not just the first) means a nested scratch dir
    like `plugin/foo/reports_dev/x.md` is skipped too, mirroring how gitignore
    dir rules apply at any depth.
    """
    return any(part in skip_dirs for part in rel_path.parts)


def _strip_code_regions(content: str) -> str:
    """Blank out fenced code blocks and inline code spans for link extraction.

    TRDDs and design docs embed regex / code snippets such as
    ``["']([a-z0-9_-]+)`` or ``[text](target)`` inside ``` fences or `inline`
    spans. Those are NOT markdown links. Replacing every code region with
    same-length blanks (newlines preserved) keeps line numbers stable while
    ensuring the link/image regex can never match text inside code.

    Returns a copy of `content` where:
      * fenced blocks (``` or ~~~ ... closing fence) are blanked, and
      * inline code spans (`...`, ``...``) on non-fence lines are blanked.
    """
    out_lines: list[str] = []
    in_fence = False
    fence_marker = ""  # the exact opening run, e.g. "```" or "~~~~"

    for line in content.split("\n"):
        stripped = line.lstrip()
        # A fence line is one whose first non-space run is >=3 backticks or
        # tildes. The closing fence must use the same character and be at
        # least as long as the opener (CommonMark rule).
        fence_char = ""
        if stripped.startswith("```"):
            fence_char = "`"
        elif stripped.startswith("~~~"):
            fence_char = "~"

        if fence_char:
            run_len = len(stripped) - len(stripped.lstrip(fence_char))
            if not in_fence:
                in_fence = True
                fence_marker = fence_char * run_len
                out_lines.append("")  # blank the opening fence line
                continue
            # Inside a fence: only a same-char run >= opener length closes it.
            if fence_char == fence_marker[0] and run_len >= len(fence_marker) and stripped.rstrip(fence_char) == "":
                in_fence = False
                fence_marker = ""
            out_lines.append("")  # blank every line inside / closing a fence
            continue

        if in_fence:
            out_lines.append("")  # blank all content lines inside a fence
            continue

        # Outside fences: blank inline code spans so backticked snippets that
        # look like links don't match. Replace each `...` span (1+ backticks)
        # with spaces of equal length to preserve column positions.
        out_lines.append(re.sub(r"`+[^`]*`+", lambda m: " " * len(m.group(0)), line))

    return "\n".join(out_lines)


def _iter_lines_with_fence_state(content: str) -> "list[tuple[int, str, str, bool, str, bool]]":
    """Yield one tuple per line: ``(index, raw_line, stripped, in_fence, opener_info, is_opener)``.

    Shared CommonMark-aware fence tracker for the structural rules (9-12).
    ``in_fence`` is True for every line that is INSIDE a fenced block AND for
    the opening / closing fence lines themselves — callers decide how to treat
    each. ``opener_info`` is the text after the opening fence run on an OPENING
    fence line (e.g. ``"python"`` for `````python``), else "". ``is_opener`` is
    True ONLY on an opening fence line.

    ``is_opener`` is exported explicitly because a consumer cannot reliably
    reconstruct opener-ness from ``in_fence`` alone: a CLOSING fence line is
    ALSO reported with ``in_fence=True``, so the previous "opening == in_fence
    and not prev_in_fence" heuristic missed an opener that immediately followed
    a closer (two adjacent fenced blocks with no blank line between them) —
    the language-tag check then silently skipped the second block. An explicit
    transition flag fixes that at the source.

    Honors BOTH ``````` and ``~~~`` fences and the CommonMark
    rules that the closing fence must use the same character and be at least as
    long as the opener. The four structural rules previously tracked only
    ``````` via ``startswith``, so a legal ``~~~``-fenced README
    produced spurious 'unclosed code block' MAJORs, skipped language-tag checks,
    and ran list/table scans inside code (audit m6). Mirrors the fence logic in
    :func:`_strip_code_regions` so both paths agree.
    """
    out: list[tuple[int, str, str, bool, str, bool]] = []
    in_fence = False
    fence_marker = ""  # the exact opening run, e.g. "```" or "~~~~"

    for i, line in enumerate(content.split("\n")):
        stripped = line.lstrip()
        fence_char = ""
        if stripped.startswith("```"):
            fence_char = "`"
        elif stripped.startswith("~~~"):
            fence_char = "~"

        if fence_char:
            run_len = len(stripped) - len(stripped.lstrip(fence_char))
            if not in_fence:
                # Opening fence: capture the text after the run (language tag).
                in_fence = True
                fence_marker = fence_char * run_len
                opener_info = stripped[run_len:].strip()
                out.append((i, line, stripped, True, opener_info, True))
                continue
            # Inside a fence: only a same-char run >= opener length, with
            # nothing but the fence char on the line, closes it.
            if fence_char == fence_marker[0] and run_len >= len(fence_marker) and stripped.rstrip(fence_char) == "":
                in_fence = False
                fence_marker = ""
                out.append((i, line, stripped, True, "", False))
                continue
            # A fence-looking line inside a fence that does NOT close it is
            # just content (still in_fence).
            out.append((i, line, stripped, True, "", False))
            continue

        out.append((i, line, stripped, in_fence, "", False))

    return out


def _content_has_unclosed_fence(content: str) -> tuple[bool, int]:
    """Return (unclosed, opening_line_1based) for the LAST still-open fence.

    Used by the code-block-closed rule. Recognizes both ``` and ~~~ openers.
    """
    in_fence = False
    fence_marker = ""
    open_line = 0
    for i, line in enumerate(content.split("\n")):
        stripped = line.lstrip()
        fence_char = ""
        if stripped.startswith("```"):
            fence_char = "`"
        elif stripped.startswith("~~~"):
            fence_char = "~"
        if not fence_char:
            continue
        run_len = len(stripped) - len(stripped.lstrip(fence_char))
        if not in_fence:
            in_fence = True
            fence_marker = fence_char * run_len
            open_line = i + 1
        elif fence_char == fence_marker[0] and run_len >= len(fence_marker) and stripped.rstrip(fence_char) == "":
            in_fence = False
            fence_marker = ""
    return in_fence, open_line


@dataclass
class DocumentationValidationReport(ValidationReport):
    """Validation report for documentation files.

    Extends ValidationReport with plugin_path tracking.
    All validation methods and properties are inherited from ValidationReport.
    """

    plugin_path: str = ""


# =============================================================================
# Rule 1: README.md should exist at plugin root
# =============================================================================


def validate_readme_exists(plugin_path: Path, report: DocumentationValidationReport) -> bool:
    """Validate that README.md exists at plugin root.

    Args:
        plugin_path: Path to the plugin directory
        report: Validation report to add results to

    Returns:
        True if README.md exists, False otherwise
    """
    readme = plugin_path / "README.md"
    if readme.exists():
        report.passed("README.md exists at plugin root", "README.md")
        return True
    else:
        # Accept any case-variant the rest of the validator recognizes.
        # _find_readme is the single source of truth for which README
        # spellings count; checking a hard-coded subset here (only
        # "readme.md") let a plugin shipping "Readme.md" pass the
        # content-section checks (which use _find_readme) yet still get a
        # spurious "README.md is missing" WARNING on case-sensitive
        # filesystems (Linux/CI), where "README.md".exists() is False for a
        # file literally named "Readme.md" (audit m87).
        variant = _find_readme(plugin_path)
        if variant is not None:
            report.minor(
                f"README.md exists but uses non-canonical case ({variant.name}) - consider renaming to README.md",
                variant.name,
            )
            return True

        # A missing README is a documentation-quality matter, NOT runtime
        # breakage and NOT Anthropic-invalidity (README is not required for a
        # plugin to load). Per the TRDD-021250b5 severity principle it is
        # advisory (WARNING) — a README-less plugin is VALID-with-a-warning,
        # consistent with the README content-section checks above.
        report.warning("README.md is missing at plugin root", "README.md")
        return False


# =============================================================================
# Rule 2: README should contain installation instructions
# =============================================================================


def validate_installation_section(plugin_path: Path, report: DocumentationValidationReport) -> None:
    """Validate that README contains installation instructions.

    Looks for sections named: Installation, Getting Started, Setup, Quick Start

    Args:
        plugin_path: Path to the plugin directory
        report: Validation report to add results to
    """
    readme = _find_readme(plugin_path)
    if readme is None:
        return  # Already reported in validate_readme_exists

    content = readme.read_text(encoding="utf-8")

    # Pattern matches ## Installation, ## Getting Started, ## Setup, ## Quick Start
    installation_patterns = [
        r"^#+\s*installation",
        r"^#+\s*getting\s+started",
        r"^#+\s*setup",
        r"^#+\s*quick\s*start",
    ]

    for pattern in installation_patterns:
        if re.search(pattern, content, re.IGNORECASE | re.MULTILINE):
            report.passed("README contains installation instructions", "README.md")
            return

    report.warning(
        "README missing installation section (## Installation, ## Getting Started, ## Setup, or ## Quick Start)",
        "README.md",
    )


# =============================================================================
# Rule 3: README should contain usage examples
# =============================================================================


def validate_usage_section(plugin_path: Path, report: DocumentationValidationReport) -> None:
    """Validate that README contains usage examples.

    Looks for sections named: Usage, Examples, How to Use

    Args:
        plugin_path: Path to the plugin directory
        report: Validation report to add results to
    """
    readme = _find_readme(plugin_path)
    if readme is None:
        return

    content = readme.read_text(encoding="utf-8")

    usage_patterns = [
        r"^#+\s*usage",
        r"^#+\s*examples?",
        r"^#+\s*how\s+to\s+use",
    ]

    for pattern in usage_patterns:
        if re.search(pattern, content, re.IGNORECASE | re.MULTILINE):
            report.passed("README contains usage section", "README.md")
            return

    report.warning(
        "README missing usage section (## Usage, ## Examples, or ## How to Use)",
        "README.md",
    )


# =============================================================================
# Rule 4: README should contain description section
# =============================================================================


def validate_description_section(plugin_path: Path, report: DocumentationValidationReport) -> None:
    """Validate that README contains a description.

    A description is considered present if there's content between the
    title (h1) and the first h2 section.

    Args:
        plugin_path: Path to the plugin directory
        report: Validation report to add results to
    """
    readme = _find_readme(plugin_path)
    if readme is None:
        return

    content = readme.read_text(encoding="utf-8")
    lines = content.split("\n")

    # Find the first h1 and first h2
    h1_idx = None
    h2_idx = None

    for i, line in enumerate(lines):
        if line.startswith("# ") and h1_idx is None:
            h1_idx = i
        elif line.startswith("## ") and h2_idx is None:
            h2_idx = i
            break

    if h1_idx is None:
        report.major("README missing title (# heading)", "README.md")
        return

    # Check for content between h1 and h2 (or end of file)
    end_idx = h2_idx if h2_idx is not None else len(lines)
    description_lines = lines[h1_idx + 1 : end_idx]
    description_content = "\n".join(description_lines).strip()

    # Need at least 20 characters of description content
    if len(description_content) >= 20:
        report.passed("README contains description section", "README.md")
    else:
        report.warning(
            "README missing description section after title (add content between # Title and first ## section)",
            "README.md",
        )


# =============================================================================
# Rule 5: README should have proper markdown formatting
# (Meta-rule - covered by rules 8-12)
# =============================================================================


# =============================================================================
# Rule 6: No broken internal links
# =============================================================================


def validate_broken_links(plugin_path: Path, report: DocumentationValidationReport) -> None:
    """Validate that LOCAL relative file links point to existing files.

    Only SHIPPED documentation is examined and only GENUINELY broken local
    relative links are flagged:

    * Gitignored / dev-scratch trees (docs_dev/, reports/, *_dev/, .git/,
      node_modules/, .venv/, .trashcan/, …) and templates/ are skipped — the
      former are never shipped, the latter contains intentional placeholders.
    * Link targets inside fenced code blocks or inline code spans are ignored
      (they are code/regex snippets, not links).
    * External / non-file targets are skipped: http(s)://, mailto:, tel:,
      ftp://, protocol-relative //, absolute paths (e.g. /en/sub-agents are
      doc-site routes, not local files), and pure #anchors. Any #anchor and
      ?query is stripped before resolving a relative path.

    Args:
        plugin_path: Path to the plugin directory
        report: Validation report to add results to
    """
    gi = get_gitignore_filter(plugin_path)
    plugin_root_resolved = plugin_path.resolve()

    for md_file in gi.rglob("*.md"):
        # `rglob` yields directories too (pathlib parity, #187).
        if not md_file.is_file():
            continue
        rel_md = md_file.relative_to(plugin_root_resolved)
        # Belt-and-suspenders: skip conventional dev/template dirs even if the
        # plugin's .gitignore does not list them.
        if _is_under_skip_dir(rel_md, _LINK_CHECK_SKIP_DIRS):
            continue
        # #50: test-fixture markdown (tests/, tests/fixtures/*-malicious.md, …)
        # intentionally contains fake/broken refs as test data — never
        # link-check it. Deterministic path-role test, NOT a plugin-config
        # opt-out (TRDD-02e1672b).
        if is_test_path(str(rel_md)):
            continue

        try:
            content = md_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue

        # Find all markdown links: [text](target), ignoring code regions.
        # The negative lookbehind `(?<!!)` excludes image syntax `![alt](src)`
        # — those are handled by validate_image_references, and without it the
        # link regex would double-report every image as a broken link too.
        scrubbed = _strip_code_regions(content)
        # Target group allows ONE level of balanced parens so a real filename
        # like `path/file(1).md` is not truncated at the first `)` (which would
        # false-flag `path/file(1` as a broken link). (audit WARNING doc #5)
        links = re.findall(r"(?<!!)\[([^\]]*)\]\(((?:[^()]|\([^()]*\))*)\)", scrubbed)

        for link_text, link_target in links:
            target = link_target.strip()
            # Skip external URLs, absolute/doc-site paths, anchors, empty.
            if not target or target.startswith(_NON_LOCAL_LINK_PREFIXES):
                continue

            # Skip documentation placeholders / globs / regex examples.
            if _is_placeholder_target(target):
                continue

            # Strip #anchor and ?query before resolving a relative file path.
            target_path = target.split("#", 1)[0].split("?", 1)[0]
            if not target_path:
                continue

            # Resolve relative to the markdown file's directory, then root.
            if (md_file.parent / target_path).exists():
                continue
            if (plugin_root_resolved / target_path).exists():
                continue

            report.major(
                f"Broken internal link: [{link_text}]({link_target})",
                str(rel_md),
            )


# =============================================================================
# Rule 7: CHANGELOG.md recommended
# =============================================================================


def validate_changelog_exists(plugin_path: Path, report: DocumentationValidationReport) -> None:
    """Validate that CHANGELOG.md exists (recommended).

    Args:
        plugin_path: Path to the plugin directory
        report: Validation report to add results to
    """
    changelog = plugin_path / "CHANGELOG.md"
    if changelog.exists():
        report.passed("CHANGELOG.md exists", "CHANGELOG.md")
        return

    # Check for variations. "CHANGELOG.md" is intentionally absent here — it
    # was already tested above and re-listing it made the first loop iteration
    # dead (it can only fail again at this point). On a case-insensitive
    # filesystem "changelog.md" also matched the prior check, but it is kept
    # for case-sensitive filesystems where it is a distinct, still-untested
    # spelling (audit m158).
    for variant in ["changelog.md", "CHANGES.md", "HISTORY.md"]:
        if (plugin_path / variant).exists():
            report.passed(f"Changelog found ({variant})", variant)
            return

    report.warning(
        "CHANGELOG.md is recommended for tracking version history",
        "CHANGELOG.md",
    )


# =============================================================================
# Rule 8: Heading hierarchy should have no skips
# =============================================================================


def validate_heading_hierarchy(plugin_path: Path, report: DocumentationValidationReport) -> None:
    """Validate that heading levels don't skip (h1 -> h3 is bad).

    Args:
        plugin_path: Path to the plugin directory
        report: Validation report to add results to
    """
    readme = _find_readme(plugin_path)
    if readme is None:
        return

    content = readme.read_text(encoding="utf-8")

    # Track current heading level
    current_level = 0
    issues_found = False

    # Use the shared fence-aware iterator (like rules 10-12) so a line such as
    # "# shell comment" or "### deep comment" INSIDE a ```/~~~ code block is not
    # mistaken for an ATX heading, which produced spurious "hierarchy skip"
    # WARNINGs for any README documenting commented shell snippets (audit m175).
    for i, line, _stripped, in_fence, _opener, _is_opener in _iter_lines_with_fence_state(content):
        if in_fence:
            continue
        # Match ATX-style headings (# Heading)
        match = re.match(r"^(#{1,6})\s+", line)
        if match:
            level = len(match.group(1))

            # Check if we skipped a level
            if current_level > 0 and level > current_level + 1:
                report.warning(
                    f"Heading hierarchy skip: level {current_level} to level {level} (line {i + 1})",
                    "README.md",
                    i + 1,
                )
                issues_found = True

            current_level = level

    if not issues_found and current_level > 0:
        report.passed("Heading hierarchy is correct", "README.md")


# =============================================================================
# Rule 9: Code blocks should be closed
# =============================================================================


def validate_code_block_closed(plugin_path: Path, report: DocumentationValidationReport) -> None:
    """Validate that all code blocks are properly closed.

    Checks that ``` fences are balanced (even count).

    Args:
        plugin_path: Path to the plugin directory
        report: Validation report to add results to
    """
    readme = _find_readme(plugin_path)
    if readme is None:
        return

    content = readme.read_text(encoding="utf-8")

    # CommonMark-aware fence tracking (``` and ~~~). A ~~~-fenced README is
    # legal Markdown; the old ``` -only startswith toggle could see its closing
    # ~~~ as body and emit a spurious 'unclosed code block' MAJOR (audit m6).
    unclosed, open_line = _content_has_unclosed_fence(content)

    if unclosed:
        report.major(
            f"Unclosed code block starting at line {open_line}",
            "README.md",
            open_line,
        )
    else:
        report.passed("All code blocks are properly closed", "README.md")


# =============================================================================
# Rule 10: Code blocks should have language tags
# =============================================================================


def validate_code_block_language_tags(plugin_path: Path, report: DocumentationValidationReport) -> None:
    """Validate that code blocks have language tags.

    Checks that code fences specify a language (```python not just ```).

    Args:
        plugin_path: Path to the plugin directory
        report: Validation report to add results to
    """
    readme = _find_readme(plugin_path)
    if readme is None:
        return

    content = readme.read_text(encoding="utf-8")

    issues_found = False
    # Walk logical lines with shared CommonMark fence state (``` and ~~~). The
    # iterator marks each OPENING fence line with ``is_opener=True`` and exposes
    # its language tag in ``opener_info``. We rely on ``is_opener`` directly
    # rather than the old "in_fence flips False->True" heuristic: a closing
    # fence is ALSO reported with in_fence=True, so two adjacent code blocks
    # (closer line immediately followed by the next opener, no blank line
    # between) made the heuristic miss the second opener and skip its
    # language-tag check entirely. (audit m6 — the old toggle ignored ~~~.)
    for i, _line, _stripped, _in_fence, opener_info, is_opener in _iter_lines_with_fence_state(content):
        if is_opener and not opener_info:
            report.warning(
                f"Code block at line {i + 1} missing language tag",
                "README.md",
                i + 1,
            )
            issues_found = True

    if not issues_found:
        report.passed("All code blocks have language tags", "README.md")


# =============================================================================
# Rule 11: List formatting should be proper
# =============================================================================


def validate_list_formatting(plugin_path: Path, report: DocumentationValidationReport) -> None:
    """Validate that list formatting is consistent.

    Checks for mixed list markers (-, *, +) in the same document.

    Args:
        plugin_path: Path to the plugin directory
        report: Validation report to add results to
    """
    readme = _find_readme(plugin_path)
    if readme is None:
        return

    content = readme.read_text(encoding="utf-8")

    # Track list markers used. Skip lines inside fenced code (``` AND ~~~) via
    # the shared CommonMark tracker so list scans never run inside a code block
    # (audit m6).
    markers_used: set[str] = set()

    for _i, _line, stripped, in_fence, _opener, _is_opener in _iter_lines_with_fence_state(content):
        if in_fence:
            continue

        # Check for unordered list items
        match = re.match(r"^([-*+])\s+", stripped)
        if match:
            markers_used.add(match.group(1))

    if len(markers_used) > 1:
        markers = ", ".join(sorted(markers_used))
        report.warning(
            f"Inconsistent list markers used: {markers} (prefer using one consistently)",
            "README.md",
        )
    elif markers_used:
        report.passed("List formatting is consistent", "README.md")


# =============================================================================
# Rule 12: Table structure should be valid
# =============================================================================


def _count_table_columns(row: str) -> int:
    """Count the number of columns in a markdown table row.

    Correctly handles:
    - Empty leading/trailing cells (``| | A | B |`` has 3 columns)
    - Pipes inside inline code spans (`` `a|b` `` does not split)
    - Escaped pipes (``\\|`` does not split)

    Args:
        row: A stripped table row string beginning and ending with ``|``.

    Returns:
        Number of cells in the row.
    """
    # Replace pipes inside inline code spans with a placeholder so they don't
    # act as column delimiters.  We match the shortest possible backtick span.
    sanitised = re.sub(r"`[^`]*`", lambda m: m.group(0).replace("|", "\x00"), row)
    # Replace escaped pipes with placeholder so they don't split.
    sanitised = sanitised.replace(r"\|", "\x00")
    # The row starts and ends with |; strip those boundary pipes then split.
    inner = sanitised[1:-1]  # remove leading and trailing |
    return len(inner.split("|"))


def validate_table_structure(plugin_path: Path, report: DocumentationValidationReport) -> None:
    """Validate that markdown tables have consistent structure.

    Checks that:
    - Separator row has correct number of columns
    - Data rows have same number of columns as header

    Demoted to advisory (report.warning) because column-count mismatches are
    a style/quality opinion; valid tables with empty leading cells or pipes
    inside inline code were previously false-flagged.

    Args:
        plugin_path: Path to the plugin directory
        report: Validation report to add results to
    """
    readme = _find_readme(plugin_path)
    if readme is None:
        return

    content = readme.read_text(encoding="utf-8")

    in_table = False
    header_cols = 0
    issues_found = False

    # Skip lines inside fenced code (``` AND ~~~) via the shared CommonMark
    # tracker so table scans never run inside a code block (audit m6).
    for i, _line, stripped, in_fence, _opener, _is_opener in _iter_lines_with_fence_state(content):
        if in_fence:
            continue

        # Check for table row. Require length >= 2: a lone "|" satisfies
        # startswith("|") AND endswith("|") on the SAME single char and would
        # open a spurious 1-column table context (audit n6). A valid 1-column
        # row like "| a |" is length >= 2 and still recognized.
        if len(stripped) >= 2 and stripped.startswith("|") and stripped.endswith("|"):
            cols = _count_table_columns(stripped)

            if not in_table:
                # Header row
                in_table = True
                header_cols = cols
            elif re.match(r"^\|[\s\-:|]+\|$", stripped):
                # Separator row — use same accurate counter
                sep_cols = _count_table_columns(stripped)
                if sep_cols != header_cols:
                    report.warning(
                        f"Table separator row has {sep_cols} columns, header has {header_cols} (line {i + 1})",
                        "README.md",
                        i + 1,
                    )
                    issues_found = True
            else:
                # Data row
                if cols != header_cols:
                    report.warning(
                        f"Table row has {cols} columns, header has {header_cols} (line {i + 1})",
                        "README.md",
                        i + 1,
                    )
                    issues_found = True
        else:
            # Not a table row - reset table state
            in_table = False
            header_cols = 0

    if not issues_found and header_cols > 0:
        report.passed("Table structure is valid", "README.md")


# =============================================================================
# Rule 13: Image references should be valid
# =============================================================================


def validate_image_references(plugin_path: Path, report: DocumentationValidationReport) -> None:
    """Validate that LOCAL relative image references point to existing files.

    Same scoping/filtering rules as :func:`validate_broken_links`: only shipped
    docs are scanned (gitignored / dev-scratch / templates dirs skipped), image
    references inside code regions are ignored, and only local relative targets
    are resolved (http(s)://, data:, protocol-relative //, absolute paths, and
    pure #anchors are skipped; #anchor / ?query stripped before resolving).

    Args:
        plugin_path: Path to the plugin directory
        report: Validation report to add results to
    """
    gi = get_gitignore_filter(plugin_path)
    plugin_root_resolved = plugin_path.resolve()

    for md_file in gi.rglob("*.md"):
        # `rglob` yields directories too (pathlib parity, #187).
        if not md_file.is_file():
            continue
        rel_md = md_file.relative_to(plugin_root_resolved)
        if _is_under_skip_dir(rel_md, _LINK_CHECK_SKIP_DIRS):
            continue
        # #50: skip test-fixture markdown (see validate_broken_links) — its
        # image refs are intentionally fake test data.
        if is_test_path(str(rel_md)):
            continue

        try:
            content = md_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue

        # Find all image references: ![alt](path), ignoring code regions.
        scrubbed = _strip_code_regions(content)
        # Balanced-paren target group (parity with the link check above) so an
        # image path like `img/pic(1).png` is not truncated. (audit WARNING doc #5)
        images = re.findall(r"!\[([^\]]*)\]\(((?:[^()]|\([^()]*\))*)\)", scrubbed)

        for alt_text, img_path in images:
            target = img_path.strip()
            # Skip external / non-file targets (includes data:, //, absolute).
            if not target or target.startswith(_NON_LOCAL_LINK_PREFIXES):
                continue

            # Skip documentation placeholders / globs / regex examples.
            if _is_placeholder_target(target):
                continue

            # Strip #anchor and ?query before resolving a relative file path.
            target_path = target.split("#", 1)[0].split("?", 1)[0]
            if not target_path:
                continue

            if (md_file.parent / target_path).exists():
                continue
            if (plugin_root_resolved / target_path).exists():
                continue

            report.major(
                f"Missing image: ![{alt_text}]({img_path})",
                str(rel_md),
            )


# =============================================================================
# Helper Functions
# =============================================================================


def _find_readme(plugin_path: Path) -> Path | None:
    """Find README.md in plugin directory (case-insensitive).

    Args:
        plugin_path: Path to the plugin directory

    Returns:
        Path to README.md if found, None otherwise
    """
    for name in ["README.md", "readme.md", "Readme.md"]:
        readme = plugin_path / name
        if readme.exists():
            return readme
    return None


# =============================================================================
# Main Validation Function
# =============================================================================


def validate_documentation(plugin_path: Path) -> DocumentationValidationReport:
    """Validate all documentation in a plugin directory.

    Runs all 13 validation rules and returns a complete report.

    Args:
        plugin_path: Path to the plugin directory

    Returns:
        DocumentationValidationReport with all results
    """
    report = DocumentationValidationReport(plugin_path=str(plugin_path))

    # Check plugin directory exists
    if not plugin_path.is_dir():
        report.critical(f"Plugin path is not a directory: {plugin_path}")
        return report

    # Rule 1: README.md should exist
    if not validate_readme_exists(plugin_path, report):
        # Can't validate other rules without README
        return report

    # Rule 2: Installation section
    validate_installation_section(plugin_path, report)

    # Rule 3: Usage section
    validate_usage_section(plugin_path, report)

    # Rule 4: Description section
    validate_description_section(plugin_path, report)

    # Rule 5 is covered by rules 8-12

    # Rule 6: Broken links
    validate_broken_links(plugin_path, report)

    # Rule 7: CHANGELOG recommended
    validate_changelog_exists(plugin_path, report)

    # Rule 8: Heading hierarchy
    validate_heading_hierarchy(plugin_path, report)

    # Rule 9: Code blocks closed
    validate_code_block_closed(plugin_path, report)

    # Rule 10: Code block language tags
    validate_code_block_language_tags(plugin_path, report)

    # Rule 11: List formatting
    validate_list_formatting(plugin_path, report)

    # Rule 12: Table structure
    validate_table_structure(plugin_path, report)

    # Rule 13: Image references
    validate_image_references(plugin_path, report)

    return report


# =============================================================================
# Output Functions
# =============================================================================


def print_results(report: DocumentationValidationReport, verbose: bool = False) -> None:
    """Print validation results in human-readable format.

    Args:
        report: The validation report to print
        verbose: If True, also show INFO and PASSED results
    """
    # ANSI colors
    colors = COLORS

    # Count by level. Must list EVERY level the shared ValidationReport can
    # emit — after the TRDD-021250b5 recalibration this validator emits WARNING
    # (and may emit NIT), and an absent key here KeyError-crashes the CLI on the
    # first such finding. A genuinely unknown level still raises (fail-fast).
    counts = {"CRITICAL": 0, "MAJOR": 0, "MINOR": 0, "NIT": 0, "WARNING": 0, "INFO": 0, "PASSED": 0}
    for r in report.results:
        counts[r.level] += 1

    # Print header
    print("\n" + "=" * 60)
    print(f"Documentation Validation: {report.plugin_path}")
    print("=" * 60)

    # Print summary
    print("\nSummary:")
    print(f"  {colors['CRITICAL']}CRITICAL: {counts['CRITICAL']}{colors['RESET']}")
    print(f"  {colors['MAJOR']}MAJOR:    {counts['MAJOR']}{colors['RESET']}")
    print(f"  {colors['MINOR']}MINOR:    {counts['MINOR']}{colors['RESET']}")
    # NIT must appear: this validator runs under --strict (L1102) where NIT
    # blocks (exit 4). Omitting it left a --strict user staring at exit 4 with
    # no NIT total in the summary (audit m7; matches validate_rules.py).
    print(f"  {colors['NIT']}NIT:      {counts['NIT']}{colors['RESET']}")
    print(f"  {colors['WARNING']}WARNING:  {counts['WARNING']}{colors['RESET']}")
    if verbose:
        print(f"  {colors['INFO']}INFO:     {counts['INFO']}{colors['RESET']}")
        print(f"  {colors['PASSED']}PASSED:   {counts['PASSED']}{colors['RESET']}")

    # Print details
    print("\nDetails:")
    for r in report.results:
        if r.level == "PASSED" and not verbose:
            continue
        if r.level == "INFO" and not verbose:
            continue

        color = colors[r.level]
        reset = colors["RESET"]
        file_info = f" ({r.file})" if r.file else ""
        line_info = f":{r.line}" if r.line else ""
        print(f"  {color}[{r.level}]{reset} {r.message}{file_info}{line_info}")

    # Print final status
    print("\n" + "-" * 60)
    if report.exit_code == 0:
        print(f"{colors['PASSED']}Documentation validation passed{colors['RESET']}")
    elif report.exit_code == 1:
        crit = colors["CRITICAL"]
        rst = colors["RESET"]
        print(f"{crit}CRITICAL issues - documentation incomplete{rst}")
    elif report.exit_code == 2:
        maj = colors["MAJOR"]
        rst = colors["RESET"]
        print(f"{maj}MAJOR issues - significant documentation problems{rst}")
    else:
        minor = colors["MINOR"]
        rst = colors["RESET"]
        print(f"{minor}MINOR issues - documentation could be improved{rst}")

    print()


def print_json(report: DocumentationValidationReport) -> None:
    """Print validation results as JSON.

    Args:
        report: The validation report to print
    """
    output = {
        "plugin_path": report.plugin_path,
        "exit_code": report.exit_code,
        "counts": {
            "critical": sum(1 for r in report.results if r.level == "CRITICAL"),
            "major": sum(1 for r in report.results if r.level == "MAJOR"),
            "minor": sum(1 for r in report.results if r.level == "MINOR"),
            # WARNING + NIT are emitted heavily by this validator now (README
            # existence, advisory heuristics) — JSON consumers were getting an
            # incomplete count. (audit MINOR doc #4)
            "warning": sum(1 for r in report.results if r.level == "WARNING"),
            "nit": sum(1 for r in report.results if r.level == "NIT"),
            "info": sum(1 for r in report.results if r.level == "INFO"),
            "passed": sum(1 for r in report.results if r.level == "PASSED"),
        },
        "results": [
            {
                "level": r.level,
                "message": r.message,
                "file": r.file,
                "line": r.line,
            }
            for r in report.results
        ],
    }
    print(json.dumps(output, indent=2))


# =============================================================================
# CLI Entry Point
# =============================================================================


def main() -> int:
    """Main entry point for CLI.

    Returns:
        Exit code (0=ok, 1=critical, 2=major, 3=minor)
    """
    from cpv_validation_common import launcher_epilog

    parser = argparse.ArgumentParser(
        description="Validate documentation files in a Claude Code plugin.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Checks: README structure, broken links, code blocks, image refs, heading hierarchy.\n\n"
        + launcher_epilog("docs"),
    )
    parser.add_argument("plugin_path", help="Path to the plugin directory")
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Show all results including passed checks",
    )
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument(
        "--report", type=str, default=None, help="Save detailed report to file, print only summary to stdout"
    )
    parser.add_argument("--strict", action="store_true", help="Strict mode — NIT issues also block validation")
    args = parser.parse_args()

    plugin_path = Path(args.plugin_path).resolve()

    if not plugin_path.exists():
        print(f"Error: {plugin_path} does not exist", file=sys.stderr)
        return 1

    if not plugin_path.is_dir():
        print(f"Error: {plugin_path} is not a directory", file=sys.stderr)
        return 1

    # Verify this is a plugin directory
    if not (plugin_path / ".claude-plugin").is_dir():
        print(
            f"Error: No Claude Code plugin found at {plugin_path}\nExpected a .claude-plugin/ directory.",
            file=sys.stderr,
        )
        return 1

    report = validate_documentation(plugin_path)

    if args.json:
        print_json(report)
    else:
        if args.report:
            save_report_and_print_summary(
                report,
                Path(args.report),
                "Documentation Validation",
                print_results,
                args.verbose,
                plugin_path=args.plugin_path,
            )
        else:
            print_results(report, args.verbose)

    if args.strict:
        return report.exit_code_strict()
    return report.exit_code


if __name__ == "__main__":
    sys.exit(main())
