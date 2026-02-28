#!/usr/bin/env python3
"""Tests for TOC embedding validation functions.

Tests validate_toc_embedding() and extract_toc_headings() from cpv_validation_common.
These functions ensure that when a SKILL.md links to a .md reference file that has a
Table of Contents, the SKILL.md embeds at least some of those TOC headings inline so
agents can see what content is available before navigating.

Coverage: 10 tests covering all major code paths.
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
        assert "Table of Contents" in minor_results[0].message

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
        """Only 1 of 5 TOC entries embedded produces MINOR (needs at least 2)."""
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
        # SKILL.md embeds only 1 of the 5 headings (below the threshold of 2)
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

        # Should get MINOR since only 1 of 5 headings embedded (needs at least 2)
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
