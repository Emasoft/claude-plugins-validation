#!/usr/bin/env python3
"""Tests for TOC embedding validation functions.

Tests validate_toc_embedding() and extract_toc_headings() from cpv_validation_common.
These functions ensure that when a SKILL.md links to a .md reference file that has a
Table of Contents, the SKILL.md embeds at least some of those TOC headings inline so
agents can see what content is available before navigating.

Coverage: 25 tests covering all major code paths including list-item ambiguity handling
and backtick reference detection.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Add scripts directory to path for imports
scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

from cpv_validation_common import ValidationReport, extract_toc_headings, validate_toc_embedding  # noqa: E402


class TestExtractTocHeadings:
    """Tests for extract_toc_headings() — parses TOC sections from markdown content."""

    def test_extract_toc_headings_with_toc(self):
        """File content with '## Table of Contents' and bullet entries returns heading list."""
        md_content = """\
# My Reference File

Some intro text about this file.

## Table of Contents

- [Getting Started](#getting-started)
- [Configuration](#configuration)
- [API Reference](#api-reference)
- [Troubleshooting](#troubleshooting)

## Getting Started

Content here...

## Configuration

More content...
"""
        result = extract_toc_headings(md_content)
        assert result == ["Getting Started", "Configuration", "API Reference", "Troubleshooting"]

    def test_extract_toc_headings_no_toc(self):
        """File content without any TOC section returns empty list."""
        md_content = """\
# Simple Document

## Introduction

This document has no table of contents section.

## Details

Some details here.
"""
        result = extract_toc_headings(md_content)
        assert result == []

    def test_extract_toc_headings_numbered_toc(self):
        """TOC with numbered entries like '1. [Title](#anchor)' returns titles."""
        md_content = """\
# Reference Guide

## Table of Contents

1. [Installation](#installation)
2. [Usage Guide](#usage-guide)
3. [Advanced Features](#advanced-features)

## Installation

Install steps...
"""
        result = extract_toc_headings(md_content)
        assert "Installation" in result
        assert "Usage Guide" in result
        assert "Advanced Features" in result
        assert len(result) == 3


class TestValidateTocEmbedding:
    """Tests for validate_toc_embedding() — checks that SKILL.md embeds TOC from references."""

    def test_validate_toc_embedding_all_embedded(self, tmp_path: Path):
        """SKILL.md with TOC entries embedded near links produces PASSED result."""
        # Create the referenced file with a TOC
        ref_dir = tmp_path / "references"
        ref_dir.mkdir()
        ref_file = ref_dir / "api-guide.md"
        ref_file.write_text("""\
# API Guide

## Table of Contents

- [Authentication](#authentication)
- [Endpoints](#endpoints)
- [Error Handling](#error-handling)

## Authentication

Auth details...

## Endpoints

Endpoint details...

## Error Handling

Error info...
""")
        # Create SKILL.md that links to the reference and embeds TOC headings nearby
        skill_content = """\
# My Skill

## References

See the [API Guide](references/api-guide.md) for details.

### API Guide Contents

- Authentication
- Endpoints
- Error Handling

## Usage

Use the skill like this...
"""
        skill_path = tmp_path / "SKILL.md"
        skill_path.write_text(skill_content)

        report = ValidationReport()
        validate_toc_embedding(skill_content, skill_path, tmp_path, report)

        # Should get a PASSED result since all TOC headings are embedded
        passed_results = [r for r in report.results if r.level == "PASSED"]
        minor_results = [r for r in report.results if r.level == "MINOR"]
        assert len(passed_results) == 1, f"Expected 1 PASSED, got {len(passed_results)}. Results: {report.results}"
        assert len(minor_results) == 0

    def test_validate_toc_embedding_missing_toc(self, tmp_path: Path):
        """SKILL.md links to file with TOC but does not embed entries produces MINOR."""
        # Create the referenced file with a TOC
        ref_dir = tmp_path / "references"
        ref_dir.mkdir()
        ref_file = ref_dir / "setup-guide.md"
        ref_file.write_text("""\
# Setup Guide

## Table of Contents

- [Prerequisites](#prerequisites)
- [Installation](#installation)
- [Configuration](#configuration)

## Prerequisites

You need Python 3.12+...

## Installation

Run pip install...

## Configuration

Edit config.yaml...
""")
        # Create SKILL.md that links to the reference but does NOT embed any TOC headings
        skill_content = """\
# My Skill

## References

See the [Setup Guide](references/setup-guide.md) for details.

## Usage

Use the skill like this...
"""
        skill_path = tmp_path / "SKILL.md"
        skill_path.write_text(skill_content)

        report = ValidationReport()
        validate_toc_embedding(skill_content, skill_path, tmp_path, report)

        # Should get a MINOR since TOC headings are not embedded
        minor_results = [r for r in report.results if r.level == "MINOR"]
        assert len(minor_results) == 1
        assert "setup-guide.md" in minor_results[0].message
        assert "0/3 TOC headings" in minor_results[0].message

    def test_validate_toc_embedding_nonexistent_file(self, tmp_path: Path):
        """Link points to non-existent file produces no error (graceful skip)."""
        skill_content = """\
# My Skill

See the [Missing Guide](references/does-not-exist.md) for details.

## Usage

Use the skill...
"""
        skill_path = tmp_path / "SKILL.md"
        skill_path.write_text(skill_content)

        report = ValidationReport()
        validate_toc_embedding(skill_content, skill_path, tmp_path, report)

        # No results at all — non-existent files are silently skipped
        assert len(report.results) == 0

    def test_validate_toc_embedding_non_md_link(self, tmp_path: Path):
        """Link to .py file produces no TOC check (only .md files checked)."""
        # Create a .py file (the regex won't match .py links at all)
        py_file = tmp_path / "helper.py"
        py_file.write_text("def helper(): pass\n")

        skill_content = """\
# My Skill

See the [Helper](helper.py) for implementation.

## Usage

Use the skill...
"""
        skill_path = tmp_path / "SKILL.md"
        skill_path.write_text(skill_content)

        report = ValidationReport()
        validate_toc_embedding(skill_content, skill_path, tmp_path, report)

        # No results — .py links don't match _MD_LINK_RE (which requires .md)
        assert len(report.results) == 0

    def test_validate_toc_embedding_ref_without_toc(self, tmp_path: Path):
        """Referenced file exists but has no TOC produces no MINOR (separate check)."""
        # Create the referenced file WITHOUT a TOC
        ref_dir = tmp_path / "references"
        ref_dir.mkdir()
        ref_file = ref_dir / "simple-doc.md"
        ref_file.write_text("""\
# Simple Document

## Introduction

This document has no table of contents.

## Details

Some details here.
""")
        skill_content = """\
# My Skill

See the [Simple Doc](references/simple-doc.md) for details.

## Usage

Use the skill...
"""
        skill_path = tmp_path / "SKILL.md"
        skill_path.write_text(skill_content)

        report = ValidationReport()
        validate_toc_embedding(skill_content, skill_path, tmp_path, report)

        # No MINOR — referenced file has no TOC, so embedding check is skipped
        minor_results = [r for r in report.results if r.level == "MINOR"]
        assert len(minor_results) == 0

    def test_validate_toc_embedding_partial_embedding(self, tmp_path: Path):
        """Only 1 of 5 TOC entries embedded produces MINOR (all must be present)."""
        # Create reference with 5 TOC entries
        ref_dir = tmp_path / "references"
        ref_dir.mkdir()
        ref_file = ref_dir / "big-guide.md"
        ref_file.write_text("""\
# Big Guide

## Table of Contents

- [Overview](#overview)
- [Architecture](#architecture)
- [Deployment](#deployment)
- [Monitoring](#monitoring)
- [Scaling](#scaling)

## Overview

Overview content...

## Architecture

Architecture content...

## Deployment

Deployment content...

## Monitoring

Monitoring content...

## Scaling

Scaling content...
""")
        # SKILL.md embeds only 1 of the 5 headings (all 5 must be present)
        skill_content = """\
# My Skill

See the [Big Guide](references/big-guide.md) for details.

Key topics:
- Overview

## Usage

Use the skill...
"""
        skill_path = tmp_path / "SKILL.md"
        skill_path.write_text(skill_content)

        report = ValidationReport()
        validate_toc_embedding(skill_content, skill_path, tmp_path, report)

        # Should get MINOR since only 1 of 5 headings embedded (all 5 required)
        minor_results = [r for r in report.results if r.level == "MINOR"]
        assert len(minor_results) == 1
        assert "big-guide.md" in minor_results[0].message

    def test_validate_toc_embedding_no_references(self, tmp_path: Path):
        """SKILL.md with no reference links produces no results."""
        skill_content = """\
# My Skill

This skill does amazing things.

## Usage

Just run `my-skill` and it works.

## Examples

```bash
my-skill --verbose
```
"""
        skill_path = tmp_path / "SKILL.md"
        skill_path.write_text(skill_content)

        report = ValidationReport()
        validate_toc_embedding(skill_content, skill_path, tmp_path, report)

        # No results at all — no .md links to check
        assert len(report.results) == 0

    def test_validate_toc_embedding_list_item_with_toc_not_embedded(self, tmp_path: Path):
        """Link in list item to file with TOC but no embedding produces MINOR.

        REGRESSION (2026-05-02 per user feedback on issue #18): the
        previous behavior was WARNING because list items could be
        ambiguous (TOC title vs reference). In practice they almost
        always ARE references, and a missing-or-partial TOC breaks
        progressive discovery either way. Severity is now MINOR — same
        as the standalone reference branch.
        """
        ref_dir = tmp_path / "references"
        ref_dir.mkdir()

        ref_file = ref_dir / "config-guide.md"
        ref_file.write_text("""\
# Config Guide

## Table of Contents

- [Basic Config](#basic-config)
- [Advanced Config](#advanced-config)
- [Troubleshooting](#troubleshooting)

## Basic Config

Config content...

## Advanced Config

Advanced content...

## Troubleshooting

Troubleshooting content...
""")

        # Link is in a list item — used to be reported as WARNING (ambiguous);
        # now reported as MINOR because progressive discovery breaks regardless.
        skill_content = """\
# My Skill

## Resources

  - [Config Guide](references/config-guide.md)

## Usage

Use the skill like this...
"""
        skill_path = tmp_path / "SKILL.md"
        skill_path.write_text(skill_content)

        report = ValidationReport()
        validate_toc_embedding(skill_content, skill_path, tmp_path, report)

        # Should get MINOR (severity-bumped from WARNING per user feedback)
        warning_results = [r for r in report.results if r.level == "WARNING"]
        minor_results = [r for r in report.results if r.level == "MINOR"]
        assert len(minor_results) == 1, f"Expected 1 MINOR, got {len(minor_results)}"
        assert len(warning_results) == 0, "Severity bumped from WARNING to MINOR"
        assert "config-guide.md" in minor_results[0].message
        assert "ambiguity" in minor_results[0].message.lower()

    def test_validate_toc_embedding_list_item_with_toc_embedded(self, tmp_path: Path):
        """Link in list item to file with TOC and TOC IS embedded produces PASSED."""
        ref_dir = tmp_path / "references"
        ref_dir.mkdir()

        ref_file = ref_dir / "api-ref.md"
        ref_file.write_text("""\
# API Reference

## Table of Contents

- [Endpoints](#endpoints)
- [Authentication](#authentication)

## Endpoints

Endpoints...

## Authentication

Auth...
""")

        # Link in list item but TOC entries are embedded right after
        skill_content = """\
# My Skill

## Resources

- [API Reference](references/api-ref.md)
  - Endpoints
  - Authentication

## Usage

Use the skill...
"""
        skill_path = tmp_path / "SKILL.md"
        skill_path.write_text(skill_content)

        report = ValidationReport()
        validate_toc_embedding(skill_content, skill_path, tmp_path, report)

        # Should PASS — TOC is embedded even though link is in a list
        passed_results = [r for r in report.results if r.level == "PASSED"]
        warning_results = [r for r in report.results if r.level == "WARNING"]
        assert len(passed_results) == 1
        assert len(warning_results) == 0

    def test_validate_toc_embedding_list_item_file_no_toc_not_exempt(self, tmp_path: Path):
        """Link in list item to non-exempt file without TOC produces NIT."""
        ref_dir = tmp_path / "references"
        ref_dir.mkdir()

        # File exists but has no TOC section
        ref_file = ref_dir / "quick-notes.md"
        ref_file.write_text("""\
# Quick Notes

## Introduction

Some notes without a proper TOC section.

## Details

More details...
""")

        skill_content = """\
# My Skill

## See Also

- [Quick Notes](references/quick-notes.md)

## Usage

Use the skill...
"""
        skill_path = tmp_path / "SKILL.md"
        skill_path.write_text(skill_content)

        report = ValidationReport()
        validate_toc_embedding(skill_content, skill_path, tmp_path, report)

        # Should get NIT about the file missing its TOC (not about embedding)
        nit_results = [r for r in report.results if r.level == "NIT"]
        minor_results = [r for r in report.results if r.level == "MINOR"]
        assert len(nit_results) == 1
        assert "quick-notes.md" in nit_results[0].message
        assert "Table of Contents" in nit_results[0].message
        assert len(minor_results) == 0

    def test_validate_toc_embedding_list_item_exempt_file_no_toc(self, tmp_path: Path):
        """Link in list item to exempt file (e.g. agent .md) without TOC produces no error."""
        # Create an agent file (exempt from TOC requirement)
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()

        agent_file = agents_dir / "my-agent.md"
        agent_file.write_text("""\
# My Agent

Agent definition without a TOC.

## Tools

Uses Read, Write, Bash.
""")

        skill_content = """\
# My Skill

## Related

- [My Agent](agents/my-agent.md)

## Usage

Use the skill...
"""
        skill_path = tmp_path / "SKILL.md"
        skill_path.write_text(skill_content)

        report = ValidationReport()
        validate_toc_embedding(skill_content, skill_path, tmp_path, report)

        # No NIT — agent files are exempt from TOC requirement
        nit_results = [r for r in report.results if r.level == "NIT"]
        assert len(nit_results) == 0

    def test_validate_toc_embedding_non_list_still_minor(self, tmp_path: Path):
        """Non-list standalone reference without TOC embedding is still MINOR."""
        ref_dir = tmp_path / "references"
        ref_dir.mkdir()

        ref_file = ref_dir / "api-ref.md"
        ref_file.write_text("""\
# API Reference

## Table of Contents

- [Endpoints](#endpoints)
- [Authentication](#authentication)
- [Rate Limits](#rate-limits)

## Endpoints

Endpoints...

## Authentication

Auth...

## Rate Limits

Limits...
""")

        # Non-list standalone reference (paragraph context)
        skill_content = """\
# My Skill

See the [API Reference](references/api-ref.md) for API details.

## Usage

Use the skill...
"""
        skill_path = tmp_path / "SKILL.md"
        skill_path.write_text(skill_content)

        report = ValidationReport()
        validate_toc_embedding(skill_content, skill_path, tmp_path, report)

        # Should get MINOR — clear standalone reference, not ambiguous
        minor_results = [r for r in report.results if r.level == "MINOR"]
        assert len(minor_results) == 1
        assert "api-ref.md" in minor_results[0].message


class TestBacktickRefDetection:
    """Tests for backtick reference detection in validate_toc_embedding()."""

    @staticmethod
    def _make_ref_with_toc(ref_dir: Path, name: str) -> None:
        """Helper: create a reference .md file with a 3-heading TOC."""
        (ref_dir / name).write_text(
            "# Guide\n\n## Table of Contents\n\n"
            "- [Alpha](#alpha)\n- [Beta](#beta)\n- [Gamma](#gamma)\n\n"
            "## Alpha\n\nA\n\n## Beta\n\nB\n\n## Gamma\n\nG\n"
        )

    @staticmethod
    def _make_ref_no_toc(ref_dir: Path, name: str) -> None:
        """Helper: create a reference .md file WITHOUT a TOC."""
        (ref_dir / name).write_text("# Simple\n\n## Intro\n\nContent.\n")

    def test_backtick_ref_reports_format_minor(self, tmp_path: Path):
        """Backtick ref to file with TOC produces format MINOR + TOC embedding MINOR."""
        ref_dir = tmp_path / "references"
        ref_dir.mkdir()
        self._make_ref_with_toc(ref_dir, "api-guide.md")

        skill_content = "# Skill\n\nSee `references/api-guide.md` for details.\n"
        skill_path = tmp_path / "SKILL.md"
        skill_path.write_text(skill_content)

        report = ValidationReport()
        validate_toc_embedding(skill_content, skill_path, tmp_path, report)

        minors = [r for r in report.results if r.level == "MINOR"]
        assert len(minors) == 2, (
            f"Expected 2 MINORs (format + TOC), got {len(minors)}: {[m.message[:60] for m in minors]}"
        )
        assert any("backtick" in m.message.lower() for m in minors)
        assert any("0/3 TOC headings" in m.message for m in minors)

    def test_backtick_ref_no_toc_only_format_minor(self, tmp_path: Path):
        """Backtick ref to file without TOC produces only format MINOR."""
        ref_dir = tmp_path / "references"
        ref_dir.mkdir()
        self._make_ref_no_toc(ref_dir, "simple-doc.md")

        skill_content = "# Skill\n\nSee `references/simple-doc.md` for info.\n"
        skill_path = tmp_path / "SKILL.md"
        skill_path.write_text(skill_content)

        report = ValidationReport()
        validate_toc_embedding(skill_content, skill_path, tmp_path, report)

        minors = [r for r in report.results if r.level == "MINOR"]
        assert len(minors) == 1
        assert "backtick" in minors[0].message.lower()

    def test_backtick_ref_nonexistent_file(self, tmp_path: Path):
        """Backtick ref to non-existent file produces no report."""
        skill_content = "# Skill\n\nSee `references/does-not-exist.md` for info.\n"
        skill_path = tmp_path / "SKILL.md"
        skill_path.write_text(skill_content)

        report = ValidationReport()
        validate_toc_embedding(skill_content, skill_path, tmp_path, report)

        assert len(report.results) == 0

    def test_backtick_ref_in_fenced_code_ignored(self, tmp_path: Path):
        """Backtick ref inside fenced code block is ignored."""
        ref_dir = tmp_path / "references"
        ref_dir.mkdir()
        self._make_ref_with_toc(ref_dir, "api-guide.md")

        skill_content = "# Skill\n\n```markdown\nSee `references/api-guide.md` for details.\n```\n\nDone.\n"
        skill_path = tmp_path / "SKILL.md"
        skill_path.write_text(skill_content)

        report = ValidationReport()
        validate_toc_embedding(skill_content, skill_path, tmp_path, report)

        # No results — backtick ref inside code block is ignored
        assert len(report.results) == 0

    def test_backtick_ref_same_file_as_link_no_double_toc(self, tmp_path: Path):
        """Both proper link and backtick ref to same file: format MINOR but no double TOC check."""
        ref_dir = tmp_path / "references"
        ref_dir.mkdir()
        self._make_ref_with_toc(ref_dir, "api-guide.md")

        skill_content = (
            "# Skill\n\n"
            "See the [API Guide](references/api-guide.md) for details.\n"
            "- Alpha\n- Beta\n- Gamma\n\n"
            "Also see `references/api-guide.md` in the codebase.\n"
        )
        skill_path = tmp_path / "SKILL.md"
        skill_path.write_text(skill_content)

        report = ValidationReport()
        validate_toc_embedding(skill_content, skill_path, tmp_path, report)

        passed = [r for r in report.results if r.level == "PASSED"]
        minors = [r for r in report.results if r.level == "MINOR"]
        # 1 PASSED from proper link (TOC embedded), 1 MINOR for backtick format
        assert len(passed) == 1
        assert len(minors) == 1
        assert "backtick" in minors[0].message.lower()

    def test_backtick_ref_bare_filename(self, tmp_path: Path):
        """Backtick ref with bare filename (no directory) is detected."""
        ref_file = tmp_path / "notes.md"
        ref_file.write_text("# Notes\n\n## Intro\n\nContent.\n")

        skill_content = "# Skill\n\nSee `notes.md` for info.\n"
        skill_path = tmp_path / "SKILL.md"
        skill_path.write_text(skill_content)

        report = ValidationReport()
        validate_toc_embedding(skill_content, skill_path, tmp_path, report)

        minors = [r for r in report.results if r.level == "MINOR"]
        assert len(minors) == 1
        assert "backtick" in minors[0].message.lower()

    def test_backtick_ref_non_md_ignored(self, tmp_path: Path):
        """Backtick refs to non-.md files (script.py, git status) are not matched."""
        skill_content = "# Skill\n\nRun `validate_plugin.py` and `git status` to check.\n"
        skill_path = tmp_path / "SKILL.md"
        skill_path.write_text(skill_content)

        report = ValidationReport()
        validate_toc_embedding(skill_content, skill_path, tmp_path, report)

        assert len(report.results) == 0

    def test_backtick_ref_multiple_same_line(self, tmp_path: Path):
        """Two backtick refs on one line produce two format MINORs."""
        ref_dir = tmp_path / "references"
        ref_dir.mkdir()
        self._make_ref_no_toc(ref_dir, "a.md")
        self._make_ref_no_toc(ref_dir, "b.md")

        skill_content = "# Skill\n\nSee `references/a.md` and `references/b.md` for details.\n"
        skill_path = tmp_path / "SKILL.md"
        skill_path.write_text(skill_content)

        report = ValidationReport()
        validate_toc_embedding(skill_content, skill_path, tmp_path, report)

        minors = [r for r in report.results if r.level == "MINOR"]
        assert len(minors) == 2
        filenames = [m.message for m in minors]
        assert any("a.md" in f for f in filenames)
        assert any("b.md" in f for f in filenames)

    def test_backtick_ref_with_toc_fully_embedded(self, tmp_path: Path):
        """Backtick ref with all TOC headings nearby: format MINOR but TOC counts as checked."""
        ref_dir = tmp_path / "references"
        ref_dir.mkdir()
        self._make_ref_with_toc(ref_dir, "api-guide.md")

        skill_content = "# Skill\n\nSee `references/api-guide.md` for details.\n- Alpha\n- Beta\n- Gamma\n\nDone.\n"
        skill_path = tmp_path / "SKILL.md"
        skill_path.write_text(skill_content)

        report = ValidationReport()
        validate_toc_embedding(skill_content, skill_path, tmp_path, report)

        minors = [r for r in report.results if r.level == "MINOR"]
        passed = [r for r in report.results if r.level == "PASSED"]
        # 1 format MINOR (backtick) + 1 PASSED (TOC fully embedded)
        assert len(minors) == 1
        assert "backtick" in minors[0].message.lower()
        assert len(passed) == 1

    def test_backtick_ref_in_tilde_fence_ignored(self, tmp_path: Path):
        """Backtick ref inside tilde-fenced code block is ignored."""
        ref_dir = tmp_path / "references"
        ref_dir.mkdir()
        self._make_ref_with_toc(ref_dir, "api-guide.md")

        skill_content = "# Skill\n\n~~~\nSee `references/api-guide.md` here.\n~~~\n\nDone.\n"
        skill_path = tmp_path / "SKILL.md"
        skill_path.write_text(skill_content)

        report = ValidationReport()
        validate_toc_embedding(skill_content, skill_path, tmp_path, report)

        assert len(report.results) == 0
