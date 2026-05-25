#!/usr/bin/env python3
"""
Claude Plugins Validation - Rules Validator

Validates rule files (.md) in a plugin's rules/ directory.
Rules are plain markdown files loaded alongside CLAUDE.md into the model context.
They support optional YAML frontmatter with a `paths` field for path-specific rules.

Based on: https://docs.anthropic.com/en/docs/claude-code/memory

Usage:
    uv run python scripts/validate_rules.py path/to/rules/
    uv run python scripts/validate_rules.py path/to/rules/ --verbose
    uv run python scripts/validate_rules.py path/to/rules/ --json

Exit codes:
    0 - All checks passed
    1 - CRITICAL issues found
    2 - MAJOR issues found
    3 - MINOR issues found
    4 - NIT issues found (only in --strict mode)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from cpv_parallel_runner import ScanResult, parallel_scan
from cpv_validation_common import (
    COLORS,
    SECRET_PATTERNS,
    USER_PATH_PATTERNS,
    ValidationReport,
    ValidationResult,
    check_utf8_encoding,
    save_report_and_print_summary,
)

# =============================================================================
# Constants
# =============================================================================

# Token budget for all rules combined (loaded into context alongside CLAUDE.md)
MAX_RULES_TOKENS = 10_000

# Known frontmatter fields for rules files
KNOWN_RULES_FRONTMATTER_FIELDS = {"paths"}

# Character-to-token conversion ratios by script category.
# Based on Claude's BPE tokenizer behavior:
# - Latin/ASCII text: ~4 characters per token (0.25 tokens/char)
# - CJK ideographs (Chinese, Japanese kanji, Korean hanja): ~1 char per token
# - Japanese kana (hiragana, katakana): ~1.5 chars per token
# - Korean hangul syllables: ~1 char per token
# - Cyrillic, Greek, Arabic, Hebrew, Thai, Devanagari: ~2 chars per token
# Conservative estimates (slightly overcount tokens) to warn early.
TOKEN_RATIO_LATIN = 0.25  # 1 token per ~4 chars
TOKEN_RATIO_CJK = 1.0  # 1 token per ~1 char
TOKEN_RATIO_KANA = 0.7  # 1 token per ~1.5 chars
TOKEN_RATIO_OTHER_SCRIPTS = 0.5  # 1 token per ~2 chars


# =============================================================================
# Token Estimation
# =============================================================================


def _classify_char(ch: str) -> str:
    """Classify a character into a script category for token estimation.

    Returns one of: 'cjk', 'kana', 'other_script', 'latin'
    """
    cp = ord(ch)

    # CJK Unified Ideographs and extensions (Chinese, Japanese kanji, Korean hanja)
    if (
        0x4E00 <= cp <= 0x9FFF  # CJK Unified Ideographs
        or 0x3400 <= cp <= 0x4DBF  # CJK Extension A
        or 0x20000 <= cp <= 0x2A6DF  # CJK Extension B
        or 0x2A700 <= cp <= 0x2B73F  # CJK Extension C
        or 0x2B740 <= cp <= 0x2B81F  # CJK Extension D
        or 0xF900 <= cp <= 0xFAFF  # CJK Compatibility Ideographs
    ):
        return "cjk"

    # Japanese Kana
    if (
        0x3040 <= cp <= 0x309F  # Hiragana
        or 0x30A0 <= cp <= 0x30FF  # Katakana
        or 0x31F0 <= cp <= 0x31FF  # Katakana Phonetic Extensions
    ):
        return "kana"

    # Korean Hangul syllables
    if 0xAC00 <= cp <= 0xD7AF:
        return "cjk"  # Hangul syllables tokenize similarly to CJK

    # Korean Jamo
    if 0x1100 <= cp <= 0x11FF or 0x3130 <= cp <= 0x318F:
        return "cjk"

    # Other non-Latin scripts (Cyrillic, Arabic, Hebrew, Thai, Devanagari, etc.)
    cat = unicodedata.category(ch)
    if cat.startswith("L"):  # Letter category
        # Check if it's outside basic Latin + Latin Extended
        if cp > 0x024F:  # Beyond Latin Extended-B
            return "other_script"

    return "latin"


def estimate_tokens(text: str) -> tuple[int, dict[str, int]]:
    """Estimate token count for text using language-aware character ratios.

    Returns:
        (estimated_tokens, char_counts_by_category)
    """
    counts: dict[str, int] = {"cjk": 0, "kana": 0, "other_script": 0, "latin": 0}

    for ch in text:
        if ch.isspace():
            # Whitespace is part of Latin tokenization
            counts["latin"] += 1
            continue
        category = _classify_char(ch)
        counts[category] += 1

    estimated = (
        counts["cjk"] * TOKEN_RATIO_CJK
        + counts["kana"] * TOKEN_RATIO_KANA
        + counts["other_script"] * TOKEN_RATIO_OTHER_SCRIPTS
        + counts["latin"] * TOKEN_RATIO_LATIN
    )

    return int(estimated), counts


def _dominant_language(char_counts: dict[str, int]) -> str:
    """Return the dominant language category for reporting."""
    total = sum(char_counts.values())
    if total == 0:
        return "empty"

    cjk_total = char_counts.get("cjk", 0) + char_counts.get("kana", 0)
    other = char_counts.get("other_script", 0)

    if cjk_total > total * 0.3:
        return "CJK-heavy"
    elif other > total * 0.3:
        return "non-Latin"
    return "Latin"


# =============================================================================
# Validation Functions
# =============================================================================


def validate_rule_file(rule_path: Path, report: ValidationReport, rel_path: str) -> str:
    """Validate a single rule file.

    Returns:
        The text content of the rule (for token counting).
    """
    # Read raw bytes for encoding check
    try:
        raw = rule_path.read_bytes()
    except Exception as e:
        report.major(f"Cannot read rule file: {e}", rel_path)
        return ""

    # UTF-8 encoding check
    check_utf8_encoding(raw, report, rel_path)

    try:
        content = raw.decode("utf-8")
    except UnicodeDecodeError:
        report.major("Rule file is not valid UTF-8", rel_path)
        return ""

    # Empty file check
    stripped = content.strip()
    if not stripped:
        report.minor("Rule file is empty", rel_path)
        return ""

    # Frontmatter validation (optional for rules). A real frontmatter block is
    # `---` on its own line, content, then a closing `---` on its own line. A
    # bare leading `---` with NO closing fence is a horizontal rule / ordinary
    # body — the old `content.split("---", 2)` mis-parsed that, read the empty
    # parts[2] as the body, and falsely reported "frontmatter but no content
    # body" even though the content lived in parts[1]. (audit MINOR doc #2)
    lines = content.split("\n")
    if lines and lines[0].strip() == "---":
        closing_idx = None
        for idx in range(1, len(lines)):
            if lines[idx].strip() == "---":
                closing_idx = idx
                break
        if closing_idx is not None:
            fm_text = "\n".join(lines[1:closing_idx])
            body = "\n".join(lines[closing_idx + 1 :])
            if fm_text.strip():
                try:
                    frontmatter = yaml.safe_load(fm_text)
                    if isinstance(frontmatter, dict):
                        _validate_frontmatter(frontmatter, report, rel_path)
                    else:
                        report.minor("Frontmatter is not a YAML mapping", rel_path)
                except yaml.YAMLError as e:
                    report.major(f"Invalid YAML frontmatter: {e}", rel_path)
        else:
            # Leading `---` but no closing fence → not frontmatter; all body.
            body = content
    else:
        body = content

    # Check body is not just whitespace after frontmatter
    if not body.strip():
        report.minor("Rule file has frontmatter but no content body", rel_path)

    # Scan for secrets
    for pattern, description in SECRET_PATTERNS:
        if re.search(pattern, content):
            report.critical(f"Potential secret found: {description}", rel_path)

    # Scan for private paths
    for pattern in USER_PATH_PATTERNS:
        match = re.search(pattern, content)
        if match:
            report.major(f"Private path found: {match.group()}", rel_path)

    report.passed(f"Rule file validated: {rel_path}", rel_path)
    return content


def _validate_frontmatter(frontmatter: dict[str, Any], report: ValidationReport, rel_path: str) -> None:
    """Validate rule frontmatter fields.

    Per memory.md L159-221: rule files may declare a single known YAML
    frontmatter field, ``paths`` — an array of glob patterns. When present,
    the rule loads only when Claude reads a file matching one of the globs.

    Validation rules:

    - Unknown top-level frontmatter keys → MINOR (Claude Code ignores them,
      but typos like ``path:`` instead of ``paths:`` silently disable
      path-matching, so we surface the typo).
    - ``paths`` MUST be an array of strings.
    - Each glob must NOT be absolute (start with ``/``) — absolute patterns
      never match relative lookups, so the rule would never load.
    - Each glob must NOT contain ``..`` segments that would escape the
      project root (``../**`` would match files outside the repo).
    """
    # Check for unknown fields — MINOR so typos of `paths:` are visible.
    for key in frontmatter:
        if key not in KNOWN_RULES_FRONTMATTER_FIELDS:
            report.minor(
                f"Unknown frontmatter field '{key}' in rule file — only 'paths' is recognized by Claude Code.",
                rel_path,
            )

    # Validate 'paths' field
    if "paths" in frontmatter:
        paths = frontmatter["paths"]
        if not isinstance(paths, list):
            report.major("'paths' must be an array of glob patterns", rel_path)
            return
        for i, p in enumerate(paths):
            if not isinstance(p, str):
                report.major(f"paths[{i}] must be a string, got {type(p).__name__}", rel_path)
                continue
            if not p.strip():
                report.minor(f"paths[{i}] is empty", rel_path)
                continue
            # Absolute glob → MAJOR. Relative lookups never match absolute
            # patterns, so an absolute glob silently prevents the rule
            # from ever loading. Both POSIX ("/...") and Windows ("C:\\")
            # absolutes are rejected.
            if p.startswith("/") or (len(p) >= 2 and p[1] == ":"):
                report.major(
                    f"paths[{i}] '{p}' is absolute — globs must be relative "
                    "to the project root or the rule will never match.",
                    rel_path,
                )
                continue
            # `..` segment escape check: split on both "/" and "\\" so
            # Windows-style patterns are caught too. A single ".." at the
            # start means the glob would match files outside the repo.
            segments = re.split(r"[\\/]", p)
            depth = 0
            for seg in segments:
                if seg == "..":
                    depth -= 1
                    if depth < 0:
                        report.major(
                            f"paths[{i}] '{p}' uses '..' segments that escape "
                            "the project root — the glob would match files "
                            "outside the repo.",
                            rel_path,
                        )
                        break
                elif seg and seg != ".":
                    depth += 1


# =============================================================================
# Per-file parallel worker (task #384 / agent A10)
#
# Pattern: identical to validate_security / validate_xref / validate_cache.
# The per-file work is CPU-bound (UTF-8 decode + YAML parse + N regex
# scans for secrets + N regex scans for private paths). At ~1345 .md
# files in the CPV repo, a serial loop takes ~2.8s wall-clock; dispatching
# via the shared ProcessPoolExecutor harness brings this well under 2.8s.
#
# Worker contract:
#   * Top-level function (closures + lambdas are NOT pickleable by
#     ProcessPoolExecutor — fail at submit time).
#   * Single arg, pickleable (a frozen dataclass that bundles the file
#     path + rel_path + a flag indicating which line-number anchor to use).
#   * Returns ``(content, list[ValidationResult])`` — the content is needed
#     for token counting after the per-file scans complete, and the
#     ValidationResult list is replayed onto the shared master report IN
#     INPUT ORDER so the parallel path emits findings bit-identically to
#     the serial path. ValidationResult is a dataclass of primitives —
#     pickles cleanly.
#
# Escape hatch: ``CPV_RULES_PARALLEL=0`` (or false/no/off, case-insensitive)
# forces the serial path. Default = parallel. Mirrors CPV_SECURITY_PARALLEL,
# CPV_XREF_PARALLEL, CPV_CACHE_PARALLEL.
# =============================================================================


@dataclass(frozen=True)
class _RuleWorkUnit:
    """One per-file rule scan plus its rel_path context.

    Frozen + primitives-only so the unit pickles cleanly across the
    ProcessPoolExecutor worker boundary. Paths are passed as strings (then
    reconstructed inside the worker) — same defensive choice the cache
    validator's ``_CacheWorkUnit`` makes for Windows worker safety.

    Fields:
        file_path_str: Absolute path to the rule file being scanned.
        rel_path: Pre-computed relative path string for the report
            (computed against either ``plugin_root`` or
            ``rules_dir.parent`` — the choice is made at the call site, so
            the worker never needs ``plugin_root``).
    """

    file_path_str: str
    rel_path: str


def _scan_one_rule_file(unit: _RuleWorkUnit) -> tuple[str, list[ValidationResult]]:
    """Top-level pickleable worker: validate one rule file, return
    ``(content, list_of_results)``.

    Creates a LOCAL ``ValidationReport``, calls the existing
    ``validate_rule_file`` against it (so the per-file logic stays
    identical to the serial path), then returns the captured content and
    results. The parent process replays results in input order onto the
    shared master report, preserving bit-identical output ordering.

    Errors: any exception inside ``validate_rule_file`` propagates back
    to ``parallel_scan`` which captures it in ``ScanResult.error``. The
    parent's replay loop surfaces it as a per-file WARNING (consistent
    with the spec contract shared across A2/A6/A8/A9 validators).
    """
    file_path = Path(unit.file_path_str)
    local_report = ValidationReport()
    content = validate_rule_file(file_path, local_report, unit.rel_path)
    return content, list(local_report.results)


def _rules_parallel_enabled() -> bool:
    """Read the ``CPV_RULES_PARALLEL`` env-var.

    Returns False when set to ``"0"`` / ``"false"`` / ``"no"`` / ``"off"``
    (case-insensitive) — the serial path is taken. Any other value, or no
    value at all, returns True (default = parallel). Mirrors the parsing
    in ``_cache_parallel_enabled`` / ``_xref_parallel_enabled`` for
    cross-validator consistency.
    """
    val = os.environ.get("CPV_RULES_PARALLEL")
    if val is None:
        return True
    return val.strip().lower() not in {"0", "false", "no", "off"}


def validate_rules_directory(
    rules_dir: Path,
    report: ValidationReport | None = None,
    plugin_root: Path | None = None,
) -> ValidationReport:
    """Validate all rule files in a rules/ directory.

    Args:
        rules_dir: Path to the rules/ directory
        report: Optional existing report to merge into
        plugin_root: Optional plugin root for relative path display

    Returns:
        ValidationReport with all results
    """
    if report is None:
        report = ValidationReport()

    if not rules_dir.is_dir():
        report.info("No rules/ directory found")
        return report

    # Find all .md files recursively
    rule_files = sorted(rules_dir.rglob("*.md"))

    if not rule_files:
        report.info("No rule files (*.md) found in rules/")
        return report

    report.info(f"Found {len(rule_files)} rule file(s)")

    # Pre-compute (file_path, rel_path) pairs OUTSIDE the worker so the
    # decision between plugin_root and rules_dir.parent is made once at the
    # call site (Path objects aren't passed across the worker boundary —
    # only the resolved rel_path string is).
    units: list[_RuleWorkUnit] = []
    for rule_path in rule_files:
        if plugin_root:
            rel_path = str(rule_path.relative_to(plugin_root))
        else:
            rel_path = str(rule_path.relative_to(rules_dir.parent))
        units.append(_RuleWorkUnit(file_path_str=str(rule_path), rel_path=rel_path))

    # Task #384 / A10: dispatch per-file scans via the shared parallel_scan
    # harness, then replay results onto the master report IN INPUT ORDER
    # so the parallel path's output sequence is bit-identical to the
    # serial path. The CPV_RULES_PARALLEL=0 escape hatch routes through
    # the serial branch — kept structurally identical to the parallel
    # branch's replay loop so any future per-file logic added to
    # validate_rule_file is automatically picked up by both paths.
    #
    # Why ``chunk_size`` > 1: per-file work is small (read + YAML parse +
    # ~10 regex scans, microseconds per file). The IPC overhead of
    # ProcessPoolExecutor submission is ~1ms per task minimum — at
    # one-task-per-file, IPC dominates and the parallel branch ends up
    # slower than serial. Batching ~32 files per task reduces IPC
    # ~32x and keeps the worker pool saturated for typical plugin sizes
    # (a 1300-file CPV repo becomes ~40 tasks across ~10 workers ≈ 4
    # tasks per worker, plenty of room for load-balancing without
    # paying per-file submit overhead).
    all_content: list[str] = []
    if _rules_parallel_enabled() and len(units) > 1:
        scan_results = parallel_scan(
            units, _scan_one_rule_file, chunk_size=32  # type: ignore[arg-type]
        )
    else:
        # Serial fallback: synthesize the same ScanResult shape so the
        # replay loop below stays uniform. This is the path
        # CPV_RULES_PARALLEL=0 exercises.
        #
        # NOTE: ``ScanResult.findings`` is typed as ``list`` in the harness
        # because most workers return finding lists. Our worker
        # returns a ``(content, list[ValidationResult])`` tuple so the
        # main process can both replay results AND accumulate per-file
        # content for the combined token-budget check. The harness never
        # inspects ``findings`` — only the call site does — so the typing
        # mismatch is a strict-mypy concern, not a runtime one.
        scan_results = [
            ScanResult(
                file_path=u,
                findings=_scan_one_rule_file(u),  # type: ignore[arg-type]
                error=None,
            )
            for u in units
        ]

    for idx, sr in enumerate(scan_results):
        unit = units[idx]
        if sr.error is not None:
            # Worker raised — spec mandates "surface as a per-file
            # WARNING in the report (don't crash the whole validator)".
            # Use the pre-computed rel_path for consistency with the
            # serial format.
            report.warning(
                f"Could not validate rule file: {sr.error}",
                unit.rel_path,
            )
            # No content from a failed scan — count as empty toward the
            # combined token estimate (serial path also skips on read
            # failure via early return).
            all_content.append("")
            continue

        # sr.findings is the tuple (content, list_of_results) returned by
        # the worker. Replay each result onto the master report, preserving
        # every field (level/message/file/line/phase/fixable/fix_id and now
        # category/suggestion — audit m9) so the parallel path's output is a
        # byte-for-byte match for the serial loop, including any sub-category
        # tag and remediation hint a rule attached.
        content, results = sr.findings
        for r in results:
            report.add(
                level=r.level,
                message=r.message,
                file=r.file,
                line=r.line,
                phase=r.phase,
                fixable=r.fixable,
                fix_id=r.fix_id,
                category=r.category,
                suggestion=r.suggestion,
            )
        all_content.append(content)

    # Token size check across ALL rule files combined
    combined_text = "\n".join(all_content)
    estimated_tokens, char_counts = estimate_tokens(combined_text)
    lang = _dominant_language(char_counts)
    total_chars = sum(char_counts.values())

    if estimated_tokens > MAX_RULES_TOKENS:
        report.warning(
            f"Total rules content is ~{estimated_tokens:,} estimated tokens "
            f"({total_chars:,} chars, {lang} content) — exceeds {MAX_RULES_TOKENS:,} token budget. "
            f"Large rules consume model context and may degrade performance. "
            f"Consider splitting into path-specific rules or reducing content.",
        )
    elif estimated_tokens > MAX_RULES_TOKENS * 0.8:
        report.warning(
            f"Total rules content is ~{estimated_tokens:,} estimated tokens "
            f"({total_chars:,} chars, {lang} content) — approaching {MAX_RULES_TOKENS:,} token budget. "
            f"Consider reviewing for redundancy.",
        )
    else:
        report.passed(
            f"Total rules content: ~{estimated_tokens:,} estimated tokens "
            f"({total_chars:,} chars, {lang} content) — within budget"
        )

    return report


# =============================================================================
# Output Functions
# =============================================================================


def print_results(report: ValidationReport, verbose: bool = False) -> None:
    """Print validation results in human-readable format."""
    colors = COLORS

    counts: dict[str, int] = {"CRITICAL": 0, "MAJOR": 0, "MINOR": 0, "NIT": 0, "WARNING": 0, "INFO": 0, "PASSED": 0}
    for r in report.results:
        counts[r.level] += 1

    print("\n" + "=" * 60)
    print("Rules Validation Report")
    print("=" * 60)

    print("\nSummary:")
    print(f"  {colors['CRITICAL']}CRITICAL: {counts['CRITICAL']}{colors['RESET']}")
    print(f"  {colors['MAJOR']}MAJOR:    {counts['MAJOR']}{colors['RESET']}")
    print(f"  {colors['MINOR']}MINOR:    {counts['MINOR']}{colors['RESET']}")
    print(f"  {colors['NIT']}NIT:      {counts['NIT']}{colors['RESET']}")
    print(f"  {colors['WARNING']}WARNING:  {counts['WARNING']}{colors['RESET']}")
    if verbose:
        print(f"  {colors['INFO']}INFO:     {counts['INFO']}{colors['RESET']}")
        print(f"  {colors['PASSED']}PASSED:   {counts['PASSED']}{colors['RESET']}")

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

    print("\n" + "-" * 60)
    if report.exit_code == 0:
        print(f"{colors['PASSED']}✓ All rules checks passed{colors['RESET']}")
    elif report.exit_code == 1:
        print(f"{colors['CRITICAL']}✗ CRITICAL issues — rules will not load{colors['RESET']}")
    elif report.exit_code == 2:
        print(f"{colors['MAJOR']}✗ MAJOR issues found{colors['RESET']}")
    else:
        print(f"{colors['MINOR']}! MINOR issues found{colors['RESET']}")

    print()


def print_json(report: ValidationReport) -> None:
    """Print validation results as JSON."""
    output = {
        "exit_code": report.exit_code,
        "counts": {
            "critical": sum(1 for r in report.results if r.level == "CRITICAL"),
            "major": sum(1 for r in report.results if r.level == "MAJOR"),
            "minor": sum(1 for r in report.results if r.level == "MINOR"),
            "nit": sum(1 for r in report.results if r.level == "NIT"),
            "warning": sum(1 for r in report.results if r.level == "WARNING"),
            "info": sum(1 for r in report.results if r.level == "INFO"),
            "passed": sum(1 for r in report.results if r.level == "PASSED"),
        },
        "results": [{"level": r.level, "message": r.message, "file": r.file, "line": r.line} for r in report.results],
    }
    print(json.dumps(output, indent=2))


# =============================================================================
# Main
# =============================================================================


def main() -> int:
    """Main entry point."""
    from cpv_validation_common import launcher_epilog

    parser = argparse.ArgumentParser(
        description="Validate Claude Code rule files in a rules/ directory.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=launcher_epilog("rules"),
    )
    parser.add_argument("path", help="Path to rules/ directory or plugin root")
    parser.add_argument("--verbose", "-v", action="store_true", help="Show all results")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--strict", action="store_true", help="Strict mode — NIT issues also block validation")
    parser.add_argument(
        "--report", type=str, default=None, help="Save detailed report to file, print only summary to stdout"
    )
    args = parser.parse_args()

    path = Path(args.path).resolve()
    if not path.exists():
        print(f"Error: {path} does not exist", file=sys.stderr)
        return 1

    # If path is a plugin root, look for rules/ subdir
    if path.is_dir() and (path / "rules").is_dir():
        rules_dir = path / "rules"
        plugin_root = path
    elif path.is_dir():
        rules_dir = path
        plugin_root = path.parent
    else:
        print(f"Error: {path} is not a directory", file=sys.stderr)
        return 1

    # Verify content type — rules directory must contain .md rule files
    if not list(rules_dir.glob("*.md")):
        print(f"Error: No rule files (.md) found in {rules_dir}", file=sys.stderr)
        return 1

    report = validate_rules_directory(rules_dir, plugin_root=plugin_root)

    if args.json:
        print_json(report)
    else:
        if args.report:
            save_report_and_print_summary(
                report, Path(args.report), "Rules Validation", print_results, args.verbose, plugin_path=args.path
            )
        else:
            print_results(report, args.verbose)

    if args.strict:
        return report.exit_code_strict()
    return report.exit_code


if __name__ == "__main__":
    sys.exit(main())
