#!/usr/bin/env python3
"""
Claude Plugins Validation - Hook Precedence Validator

Validates cross-hook precedence in a hooks.json file. Per hooks.md L989,
when multiple hooks match the same (event, matcher) pair their decisions
aggregate by precedence:

    deny > defer > ask > allow

CPV's per-hook validators do not aggregate across hooks. This module
walks a hooks.json file, groups hooks by (event, matcher), and emits:

  * MINOR when >=2 hooks on the same (event, matcher) declare conflicting
    inline permissionDecision values (silent override by precedence).
  * INFO when >=2 hooks are on the same (event, matcher) but one or more
    are exec scripts whose decision cannot be statically determined.
  * PASSED when every group has <=1 hook OR all hooks share the same
    inline decision.

Spec reference:
  - https://code.claude.com/docs/en/hooks.md#L989

Usage:
    uv run python scripts/validate_hook_precedence.py path/to/hooks.json
    uv run python scripts/validate_hook_precedence.py path/to/hooks.json --strict
    uv run python scripts/validate_hook_precedence.py path/to/hooks.json --report out.md

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
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cpv_validation_common import (
    ValidationReport,
    save_report_and_print_summary,
)

# Precedence ordering from hooks.md L989. Lower index = higher priority.
# deny > defer > ask > allow  →  deny at index 0 wins.
PRECEDENCE_ORDER: tuple[str, ...] = ("deny", "defer", "ask", "allow")
PRECEDENCE_RANK: dict[str, int] = {decision: rank for rank, decision in enumerate(PRECEDENCE_ORDER)}

# Canonical precedence-ordering string for user-facing messages.
PRECEDENCE_DESCRIPTION = "deny>defer>ask>allow"

# Matcher used for events that do not support matchers (hooks.md). We collapse
# hooks without a matcher onto a single group key so they still aggregate.
NO_MATCHER_SENTINEL = ""


@dataclass(frozen=True)
class PrecedenceFinding:
    """One detected cross-hook precedence situation for a (event, matcher) group."""

    event: str
    matcher: str
    hook_count: int
    inline_decisions: frozenset[str]
    has_unknown_decisions: bool
    resolved_decision: str | None

    def format_message(self) -> str:
        """Format a human-readable message describing the finding."""
        matcher_display = self.matcher if self.matcher != NO_MATCHER_SENTINEL else "<no-matcher>"
        header = f"({self.event}, {matcher_display}): {self.hook_count} hooks"
        if self.inline_decisions:
            sorted_decisions = sorted(self.inline_decisions)
            decisions_str = "{" + ", ".join(f"'{d}'" for d in sorted_decisions) + "}"
            parts = [f"inline decisions {decisions_str}"]
            if self.has_unknown_decisions:
                parts.append("plus exec scripts with unknowable decisions")
            if self.resolved_decision is not None:
                parts.append(f"precedence {PRECEDENCE_DESCRIPTION} resolves to {self.resolved_decision}")
            return f"{header}; " + " — ".join(parts)
        # All decisions unknown (all exec scripts).
        return (
            f"{header}; all hooks are exec scripts — "
            f"decisions unknowable; precedence {PRECEDENCE_DESCRIPTION} applies at runtime"
        )


def extract_inline_permission_decision(hook: dict[str, Any]) -> str | None:
    """Return the inline permissionDecision for a hook, or None if unknowable.

    A hook's decision is "inline" only when it is *literally declared* in the
    hook configuration — e.g. via a ``hookSpecificOutput.permissionDecision``
    field on the hook entry itself. For ``command`` hooks that exec a script,
    the decision depends on the script's runtime output, so it cannot be
    inferred statically and this function returns None.

    The check is permissive:
      * ``hook["hookSpecificOutput"]["permissionDecision"]`` — primary path.
      * ``hook["permissionDecision"]`` — shorthand inline field.

    Both paths must produce a string in {deny, defer, ask, allow} to count.
    Other values (wrong type, unknown string) are ignored and None is returned.
    """
    if not isinstance(hook, dict):
        return None

    # Primary path: hookSpecificOutput.permissionDecision
    specific = hook.get("hookSpecificOutput")
    if isinstance(specific, dict):
        decision = specific.get("permissionDecision")
        if isinstance(decision, str) and decision in PRECEDENCE_RANK:
            return decision

    # Shorthand: top-level permissionDecision on the hook entry.
    top_level = hook.get("permissionDecision")
    if isinstance(top_level, str) and top_level in PRECEDENCE_RANK:
        return top_level

    return None


def _coerce_matcher(raw_matcher: Any) -> str:
    """Coerce an arbitrary matcher field to a string group key.

    Missing or non-string matchers map to the empty-string sentinel so that
    hooks without a matcher aggregate into a single group per event.
    """
    if isinstance(raw_matcher, str):
        return raw_matcher
    return NO_MATCHER_SENTINEL


def group_hooks_by_event_matcher(
    hooks_data: dict[str, Any],
) -> dict[tuple[str, str], list[dict[str, Any]]]:
    """Group every hook in a hooks.json document by ``(event, matcher)``.

    The input ``hooks_data`` is the parsed JSON root object (the thing that
    contains the ``hooks`` key). Its structure, per the official spec, is::

        {
          "hooks": {
            "<event>": [
              { "matcher": "<pattern>", "hooks": [ {<hook>}, {<hook>}, ... ] },
              ...
            ],
            ...
          }
        }

    Malformed sub-trees (wrong types) are silently skipped. This function
    NEVER raises — callers supply malformed data freely.
    """
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}

    if not isinstance(hooks_data, dict):
        return groups

    hooks_section = hooks_data.get("hooks")
    if not isinstance(hooks_section, dict):
        return groups

    for event_name, event_blocks in hooks_section.items():
        if not isinstance(event_name, str) or not isinstance(event_blocks, list):
            continue

        for block in event_blocks:
            if not isinstance(block, dict):
                continue

            matcher_key = _coerce_matcher(block.get("matcher"))
            inner_hooks = block.get("hooks")
            if not isinstance(inner_hooks, list):
                continue

            for hook in inner_hooks:
                if not isinstance(hook, dict):
                    continue
                groups.setdefault((event_name, matcher_key), []).append(hook)

    return groups


def _resolve_by_precedence(decisions: set[str]) -> str | None:
    """Return the winning decision under the precedence rule, or None if empty.

    Only considers values in ``PRECEDENCE_RANK``. Unknown values are ignored
    by construction — ``extract_inline_permission_decision`` only yields
    values from that set.
    """
    ranked = [d for d in decisions if d in PRECEDENCE_RANK]
    if not ranked:
        return None
    return min(ranked, key=lambda d: PRECEDENCE_RANK[d])


def detect_precedence_conflicts(
    groups: dict[tuple[str, str], list[dict[str, Any]]],
) -> list[PrecedenceFinding]:
    """Detect cross-hook precedence findings.

    A group yields a finding when it contains >=2 hooks. The finding is:

      * a conflict (>=2 distinct inline decisions) — MINOR severity on caller,
      * an unknown-only group (all exec scripts, no inline decisions) — INFO,
      * a mixed group (some inline, some unknown) — INFO with precedence note,
      * a uniform group (all inline hooks share one decision) — no finding.

    The caller decides severity based on ``inline_decisions`` size.
    """
    findings: list[PrecedenceFinding] = []

    for (event, matcher), hook_list in sorted(groups.items()):
        if len(hook_list) < 2:
            continue

        inline_decisions: set[str] = set()
        unknown_count = 0
        for hook in hook_list:
            decision = extract_inline_permission_decision(hook)
            if decision is None:
                unknown_count += 1
            else:
                inline_decisions.add(decision)

        has_unknown = unknown_count > 0
        # Uniform-decision groups produce no finding: no silent override.
        if len(inline_decisions) <= 1 and not has_unknown:
            continue
        # A group with exactly one inline decision and no unknowns is uniform.
        # A group with exactly one inline decision BUT unknowns still needs a
        # finding because the exec script might return anything at runtime.
        if len(inline_decisions) == 1 and not has_unknown:
            continue

        resolved = _resolve_by_precedence(inline_decisions) if inline_decisions else None
        findings.append(
            PrecedenceFinding(
                event=event,
                matcher=matcher,
                hook_count=len(hook_list),
                inline_decisions=frozenset(inline_decisions),
                has_unknown_decisions=has_unknown,
                resolved_decision=resolved,
            )
        )

    return findings


def validate_hook_precedence(
    hooks_path: Path,
    report: ValidationReport | None = None,
) -> ValidationReport:
    """Validate cross-hook precedence for a hooks.json file.

    Args:
        hooks_path: Path to the hooks.json file to analyse.
        report: Optional existing ValidationReport to accumulate into. A new
            one is created when omitted.

    Returns:
        The ValidationReport populated with findings.

    The function never raises on missing or malformed input — it always
    returns a report. Missing/invalid JSON produces CRITICAL results.
    """
    if report is None:
        report = ValidationReport()

    file_label = str(hooks_path)

    if not hooks_path.exists():
        report.critical(f"hooks file not found: {hooks_path}", file=file_label)
        return report

    if not hooks_path.is_file():
        report.critical(f"hooks path is not a file: {hooks_path}", file=file_label)
        return report

    try:
        raw = hooks_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        report.critical(f"cannot read hooks file: {exc}", file=file_label)
        return report

    try:
        data = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError as exc:
        report.critical(f"invalid JSON in hooks file: {exc.msg} (line {exc.lineno})", file=file_label)
        return report

    if not isinstance(data, dict):
        report.critical("hooks file root must be a JSON object", file=file_label)
        return report

    groups = group_hooks_by_event_matcher(data)
    findings = detect_precedence_conflicts(groups)

    if not findings:
        report.passed(
            "No cross-hook precedence conflicts detected",
            file=file_label,
        )
        return report

    for finding in findings:
        message = finding.format_message()
        if len(finding.inline_decisions) >= 2:
            report.minor(message, file=file_label)
        else:
            # Either all-unknown (INFO) or single-inline-plus-unknown (INFO).
            report.info(message, file=file_label)

    return report


def print_results(report: ValidationReport, verbose: bool = False) -> None:
    """Print validation results in a human-readable format."""
    counts = report.count_by_level()

    print("\n" + "=" * 60)
    print("Hook Precedence Validation Report")
    print("=" * 60)

    print("\nSummary:")
    print(f"  CRITICAL: {counts['CRITICAL']}")
    print(f"  MAJOR:    {counts['MAJOR']}")
    print(f"  MINOR:    {counts['MINOR']}")
    print(f"  NIT:      {counts['NIT']}")
    print(f"  WARNING:  {counts['WARNING']}")
    if verbose:
        print(f"  INFO:     {counts['INFO']}")
        print(f"  PASSED:   {counts['PASSED']}")

    print("\nDetails:")
    for result in report.results:
        if result.level in {"PASSED", "INFO"} and not verbose:
            continue
        file_info = f" ({result.file})" if result.file else ""
        line_info = f":{result.line}" if result.line else ""
        print(f"  [{result.level}] {result.message}{file_info}{line_info}")

    print("\n" + "-" * 60)
    if report.exit_code == 0:
        print("All hook precedence checks passed")
    elif report.exit_code == 1:
        print("CRITICAL issues found")
    elif report.exit_code == 2:
        print("MAJOR issues found")
    elif report.exit_code == 3:
        print("MINOR issues found")
    else:
        print("NIT issues found (--strict mode)")
    print()


def main() -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description=(
            "Validate cross-hook precedence in a Claude Code hooks.json file. "
            "Flags groups of >=2 hooks on the same (event, matcher) pair where "
            "inline permissionDecision values conflict and would be silently "
            "resolved by precedence (deny>defer>ask>allow)."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="Show all results, including INFO/PASSED")
    parser.add_argument("--strict", action="store_true", help="Strict mode — NIT issues also block")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument(
        "--report-path",
        "--report",
        dest="report_path",
        type=str,
        default=None,
        help="Save detailed report to file, print only compact summary to stdout",
    )
    parser.add_argument(
        "path",
        nargs="?",
        help="Path to hooks.json file (or to a directory containing hooks.json)",
    )
    args = parser.parse_args()

    if not args.path:
        print("Error: hooks.json path is required", file=sys.stderr)
        parser.print_help(sys.stderr)
        return 1

    target = Path(args.path).resolve()
    if not target.exists():
        print(f"Error: {target} does not exist", file=sys.stderr)
        return 1

    if target.is_dir():
        candidate = target / "hooks.json"
        if not candidate.exists():
            print(f"Error: no hooks.json found in directory {target}", file=sys.stderr)
            return 1
        target = candidate

    report = validate_hook_precedence(target)

    if args.json:
        payload = {
            "exit_code": report.exit_code,
            "counts": report.count_by_level(),
            "results": [
                {"level": r.level, "message": r.message, "file": r.file, "line": r.line}
                for r in report.results
            ],
        }
        print(json.dumps(payload, indent=2))
    else:
        if args.report_path:
            save_report_and_print_summary(
                report,
                Path(args.report_path),
                "Hook Precedence Validation",
                print_results,
                args.verbose,
                plugin_path=str(target),
            )
        else:
            print_results(report, args.verbose)

    if args.strict:
        return report.exit_code_strict()
    return report.exit_code


if __name__ == "__main__":
    sys.exit(main())
