#!/usr/bin/env python3
"""Programmatic CPV vs Claude CLI validation-diff harness.

Why this exists:
    TRDD-b4c6cbe7 establishes INV-1: "for every Claude CLI validation
    finding, CPV emits an equivalent or stronger finding." This harness
    is the mechanical engine that produces the evidence for INV-1 +
    INV-2 by running BOTH validators on the same fixture set and
    diffing the findings.

Outputs:
    - `cli_only` rows  → CPV gaps (INV-1 violations to remediate)
    - `cpv_only` rows  → CPV-extensions OR false-positives
    - `both` rows      → shared coverage (the "already aligned" set)

Public API:
    run_cli(plugin_root: Path) -> CliReport
    run_cpv(plugin_root: Path) -> CpvReport
    diff(cli: CliReport, cpv: CpvReport) -> Diff
    audit_grid(grid_root: Path) -> list[GridRow]
    write_audit_report(rows: list[GridRow], path: Path) -> None
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class Finding:
    """One validation finding produced by either CLI or CPV.

    `severity` uses CPV's canonical levels (CRITICAL/MAJOR/MINOR/NIT/WARNING/INFO/PASSED);
    CLI output is normalized into the same vocabulary in `_normalize_cli_severity`.
    """

    severity: str
    message: str
    file: str | None = None
    line: int | None = None
    source: str = "cpv"  # "cpv" or "cli"

    def fingerprint(self) -> tuple[str, str]:
        """Severity + lowercased keyword signature for cross-tool diffing.

        Why this shape:
            CLI messages and CPV messages rarely match word-for-word, so a
            character-level diff would produce zero overlaps. Instead we
            extract the "key topic" of the finding (the field name + a
            normalized keyword) and pair it with severity. This is a
            heuristic, deliberately conservative — false-negatives are
            preferable to false-positives because the auditor will read
            every row by hand.
        """
        topic = self._extract_topic(self.message)
        return (self.severity.upper(), topic)

    @staticmethod
    def _extract_topic(message: str) -> str:
        """Pull a normalized topic string out of a free-form message.

        Recognised patterns (in priority order):
            "<field>: Invalid input: ..."     → "<field>"
            "Unrecognized key: \"<key>\""       → "unknown:<key>"
            "Missing required field '<f>'"     → "missing:<f>"
            "Missing recommended field '<f>'"  → "recommended:<f>"
            anything else                      → first non-stopword token
        """
        m = re.match(r"^\s*([\w.-]+):\s+Invalid", message)
        if m:
            return f"field:{m.group(1).lower()}"
        m = re.search(r"Unrecognized key:\s*[\"']?([^\"']+)[\"']?", message)
        if m:
            return f"unknown:{m.group(1).lower()}"
        m = re.search(r"[Mm]issing required field [\"']([\w-]+)[\"']", message)
        if m:
            return f"missing:{m.group(1).lower()}"
        m = re.search(r"[Mm]issing recommended field [\"']([\w-]+)[\"']", message)
        if m:
            return f"recommended:{m.group(1).lower()}"
        m = re.search(r"frontmatter", message, re.IGNORECASE)
        if m:
            return "frontmatter"
        # Fallback: stable first 80 chars, lowercased, alphanumeric only
        normalised = re.sub(r"[^a-z0-9]+", "-", message.lower()).strip("-")
        return normalised[:80]


@dataclass
class CliReport:
    """Output of `claude plugin validate <path>` for one fixture."""

    fixture: Path
    available: bool  # False if `claude` CLI is missing
    exit_code: int | None
    findings: list[Finding] = field(default_factory=list)
    raw_stdout: str = ""
    raw_stderr: str = ""


@dataclass
class CpvReport:
    """Output of `validate_plugin.py --strict --json <path>` for one fixture."""

    fixture: Path
    exit_code: int | None
    findings: list[Finding] = field(default_factory=list)
    raw_stdout: str = ""
    raw_stderr: str = ""


@dataclass
class Diff:
    """Symmetric diff of CLI vs CPV findings on a single fixture."""

    fixture: Path
    cli_only: list[Finding] = field(default_factory=list)
    cpv_only: list[Finding] = field(default_factory=list)
    both: list[tuple[Finding, Finding]] = field(default_factory=list)


@dataclass
class GridRow:
    """One row of the audit grid — fixture + CLI + CPV + diff."""

    fixture: Path
    cli: CliReport
    cpv: CpvReport
    diff: Diff


# ---------------------------------------------------------------------------
# CLI runner
# ---------------------------------------------------------------------------


_CLI_SEVERITY_HEADER_RE = re.compile(
    r"^\s*(?P<sym>[✘✔⚠✖])\s+"
    r"(?P<phrase>(Found\s+\d+\s+(error|warning|warnings|errors)|Validation\s+(passed|failed)))",
    re.IGNORECASE,
)
_CLI_BULLET_RE = re.compile(r"^\s*[❯»>]\s+(?P<body>.+?)\s*$")


def _normalize_cli_severity(symbol_or_phrase: str) -> str:
    """Translate CLI's symbols/phrases into CPV severity names."""
    s = symbol_or_phrase.strip()
    if "✘" in s or "✖" in s or "error" in s.lower() or "failed" in s.lower():
        return "CRITICAL"
    if "⚠" in s or "warning" in s.lower():
        return "WARNING"
    return "INFO"


def parse_cli_output(text: str, fixture: Path) -> list[Finding]:
    """Parse the human-readable output of `claude plugin validate`.

    The CLI emits blocks like:

        Validating plugin manifest: /path/to/plugin.json

        ✘ Found 2 errors:

          ❯ name: Invalid input: expected string, received undefined
          ❯ author: Invalid input: expected object, received string

        Validating agent: /path/to/agents/x.md

        ⚠ Found 1 warning:

          ❯ frontmatter: No frontmatter block found. ...

        ✘ Validation failed

    We track the current target file (the most recent "Validating ...:"
    line) and the current severity (set by the most recent ✘/⚠ header)
    so each bullet ❯ inherits both.
    """
    findings: list[Finding] = []
    current_file: str | None = None
    current_severity: str = "CRITICAL"

    for raw_line in text.splitlines():
        line = raw_line.rstrip()

        # "Validating <kind>: <path>"
        m_target = re.match(r"^\s*Validating\s+[\w\s-]+?:\s+(?P<path>.+)\s*$", line)
        if m_target:
            current_file = m_target.group("path").strip()
            continue

        # Severity-setting header
        m_hdr = _CLI_SEVERITY_HEADER_RE.match(line)
        if m_hdr:
            current_severity = _normalize_cli_severity(line)
            continue

        # Bullet body
        m_bul = _CLI_BULLET_RE.match(line)
        if m_bul:
            findings.append(
                Finding(
                    severity=current_severity,
                    message=m_bul.group("body"),
                    file=_relativize_or_none(current_file, fixture),
                    source="cli",
                )
            )

    return findings


def _relativize_or_none(p: str | None, fixture: Path) -> str | None:
    if p is None:
        return None
    try:
        rel = Path(p).resolve().relative_to(fixture.resolve())
        return str(rel)
    except (ValueError, OSError):
        return p


def run_cli(plugin_root: Path) -> CliReport:
    """Invoke `claude plugin validate <plugin_root>` and parse the output.

    When the CLI is unavailable (not on PATH), returns a CliReport with
    `available=False` and empty findings. The audit downgrades to a
    "CPV-only" mode in that case — see write_audit_report().
    """
    cli_path = shutil.which("claude")
    if cli_path is None:
        return CliReport(
            fixture=plugin_root,
            available=False,
            exit_code=None,
            raw_stderr="claude CLI not found on PATH — skipping CLI checks.",
        )

    proc = subprocess.run(
        [cli_path, "plugin", "validate", str(plugin_root)],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    findings = parse_cli_output(proc.stdout + "\n" + proc.stderr, plugin_root)
    return CliReport(
        fixture=plugin_root,
        available=True,
        exit_code=proc.returncode,
        findings=findings,
        raw_stdout=proc.stdout,
        raw_stderr=proc.stderr,
    )


# ---------------------------------------------------------------------------
# CPV runner
# ---------------------------------------------------------------------------


def _validate_plugin_script() -> Path:
    """Resolve the path to scripts/validate_plugin.py relative to this file."""
    return Path(__file__).resolve().parents[2] / "scripts" / "validate_plugin.py"


def run_cpv(plugin_root: Path) -> CpvReport:
    """Invoke `validate_plugin.py --strict --json <plugin_root>`.

    `--strict` is forced so NIT-severity findings count too; `--json`
    gives us a structured payload we can diff without re-parsing ANSI
    output.

    The script prints a banner before the JSON payload (the "[REPO
    LINT]" header), so we slice the stdout from the first `{` onward.
    """
    env = {
        **os.environ,
        # Skip the GitHub-anchored integrity check — audit fixtures are
        # ephemeral and don't have a matching upstream tag.
        "CPV_SKIP_GITHUB_INTEGRITY": "1",
        "PLUGIN_SKIP_GITHUB_INTEGRITY": "1",
        # Avoid sending live network requests during the audit run.
        "CPV_OFFLINE": "1",
        "PLUGIN_OFFLINE": "1",
    }
    proc = subprocess.run(
        ["uv", "run", "python", str(_validate_plugin_script()), str(plugin_root), "--strict", "--json", "--no-color"],
        capture_output=True,
        text=True,
        check=False,
        env=env,
        timeout=300,
    )
    payload = _slice_json_object(proc.stdout)
    findings: list[Finding] = []
    if payload is not None:
        # `results` is the structured per-finding array CPV emits. Cast to a
        # list-of-dicts because mypy cannot narrow `dict[str, object]`.
        raw_results = payload.get("results") or []
        if isinstance(raw_results, list):
            for entry in raw_results:
                if not isinstance(entry, dict):
                    continue
                findings.append(
                    Finding(
                        severity=str(entry.get("level", "INFO")).upper(),
                        message=str(entry.get("message", "")),
                        file=entry.get("file"),
                        line=entry.get("line"),
                        source="cpv",
                    )
                )
    return CpvReport(
        fixture=plugin_root,
        exit_code=proc.returncode,
        findings=findings,
        raw_stdout=proc.stdout,
        raw_stderr=proc.stderr,
    )


def _slice_json_object(stdout: str) -> dict[str, object] | None:
    """Extract the first balanced JSON object from a multi-line stdout.

    `validate_plugin.py --json` emits a banner before the payload, so we
    locate the first `{` then scan with a brace counter that respects
    string literals. Returns None when no balanced object is found.
    """
    start = stdout.find("{")
    if start == -1:
        return None
    depth = 0
    in_str = False
    escape = False
    for i in range(start, len(stdout)):
        ch = stdout[i]
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == '"' and not escape:
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    obj = json.loads(stdout[start : i + 1])
                except json.JSONDecodeError:
                    return None
                # json.loads returns Any; narrow to our declared shape.
                if isinstance(obj, dict):
                    return obj
                return None
    return None


# ---------------------------------------------------------------------------
# Diff engine
# ---------------------------------------------------------------------------


def diff(cli: CliReport, cpv: CpvReport) -> Diff:
    """Symmetric-difference the CLI + CPV findings by fingerprint.

    Two findings match when their `fingerprint()` tuples are equal,
    ignoring file/line. Each CLI finding is greedily paired with the
    first unmatched CPV finding of the same fingerprint. Leftovers go
    into `cli_only` (gaps) or `cpv_only` (extensions/false-positives).
    """
    result = Diff(fixture=cli.fixture)
    cpv_pool: list[Finding] = list(cpv.findings)

    for cli_finding in cli.findings:
        fp = cli_finding.fingerprint()
        matched: Finding | None = None
        for cand in cpv_pool:
            if cand.fingerprint() == fp:
                matched = cand
                break
        if matched is not None:
            cpv_pool.remove(matched)
            result.both.append((cli_finding, matched))
        else:
            result.cli_only.append(cli_finding)

    # CPV-only findings ignore PASSED/INFO entries — those are inventory,
    # not gaps. Anything MINOR+ that CLI did NOT see is suspicious enough
    # to surface for human review.
    for cpv_finding in cpv_pool:
        if cpv_finding.severity.upper() in {"PASSED", "INFO"}:
            continue
        result.cpv_only.append(cpv_finding)

    return result


# ---------------------------------------------------------------------------
# Grid audit + report
# ---------------------------------------------------------------------------


def audit_grid(grid_root: Path) -> list[GridRow]:
    """Run CLI + CPV against every fixture under grid_root, return rows."""
    rows: list[GridRow] = []
    fixtures = sorted(p for p in grid_root.iterdir() if p.is_dir())
    for fixture in fixtures:
        cli = run_cli(fixture)
        cpv = run_cpv(fixture)
        rows.append(GridRow(fixture=fixture, cli=cli, cpv=cpv, diff=diff(cli, cpv)))
    return rows


def _format_finding_row(f: Finding) -> str:
    """Render a finding as a single markdown-table cell."""
    file_part = f" ({f.file})" if f.file else ""
    return f"`{f.severity}` {f.message.strip()}{file_part}"


def write_audit_report(rows: list[GridRow], path: Path) -> None:
    """Write the coverage-surface audit report to `path` (markdown)."""
    cli_available = any(r.cli.available for r in rows)
    total_cli_only = sum(len(r.diff.cli_only) for r in rows)
    total_cpv_only = sum(len(r.diff.cpv_only) for r in rows)
    total_both = sum(len(r.diff.both) for r in rows)

    lines: list[str] = []
    lines.append("# Coverage-Surface Audit — CPV vs `claude plugin validate`\n")
    lines.append(f"**TRDD:** b4c6cbe7\n**Generated:** 2026-05-11\n**Fixtures audited:** {len(rows)}\n")
    if not cli_available:
        lines.append(
            "\n> WARNING: `claude` CLI was unavailable when this audit ran — "
            "CLI rows are empty. Re-run with `claude` on `$PATH` to populate "
            "the CLI columns.\n"
        )
    lines.append("")
    lines.append("## 1. Summary counts\n")
    lines.append("| Metric | Count |")
    lines.append("|---|---|")
    lines.append(f"| Fixtures | {len(rows)} |")
    lines.append(f"| CLI-only findings (CPV gaps) | {total_cli_only} |")
    lines.append(f"| CPV-only findings (extensions or false positives) | {total_cpv_only} |")
    lines.append(f"| Findings agreed on (both flagged) | {total_both} |")
    lines.append("")

    lines.append("## 2. Per-fixture diff matrix\n")
    lines.append("| Fixture | CLI exit | CPV exit | CLI-only | CPV-only | Both |")
    lines.append("|---|---:|---:|---:|---:|---:|")
    for row in rows:
        cli_exit = "n/a" if not row.cli.available else str(row.cli.exit_code)
        lines.append(
            f"| `{row.fixture.name}` | {cli_exit} | {row.cpv.exit_code} | "
            f"{len(row.diff.cli_only)} | {len(row.diff.cpv_only)} | {len(row.diff.both)} |"
        )
    lines.append("")

    lines.append("## 3. CLI-only findings (CPV gaps)\n")
    if total_cli_only == 0:
        lines.append("_No CLI-only findings detected (or CLI unavailable)._\n")
    else:
        lines.append(
            "Each row here is a candidate child-TRDD: a finding the\n"
            "official CLI flags that CPV does not currently emit.\n"
        )
        for row in rows:
            if not row.diff.cli_only:
                continue
            lines.append(f"### {row.fixture.name}\n")
            for f in row.diff.cli_only:
                lines.append(f"- {_format_finding_row(f)}")
            lines.append("")

    lines.append("## 4. CPV-only findings (extensions / false positives)\n")
    if total_cpv_only == 0:
        lines.append("_No CPV-only MINOR+ findings._\n")
    else:
        lines.append(
            "CPV-only findings represent either: (a) intentional extensions\n"
            "where CPV enforces stricter rules than CLI, or (b) false\n"
            "positives that should be silenced. Triage each row.\n"
        )
        for row in rows:
            if not row.diff.cpv_only:
                continue
            lines.append(f"### {row.fixture.name}\n")
            for f in row.diff.cpv_only:
                lines.append(f"- {_format_finding_row(f)}")
            lines.append("")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# CLI entry
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    repo_root = Path(__file__).resolve().parents[2]
    default_grid = repo_root / "tests" / "audit" / "fixtures" / "grid"
    default_report = repo_root / "design" / "audits" / "coverage-surface-2026-05-11.md"
    parser.add_argument("--grid", type=Path, default=default_grid)
    parser.add_argument("--report", type=Path, default=default_report)
    parser.add_argument(
        "--single",
        type=Path,
        default=None,
        help="Run the diff on a single plugin root instead of the whole grid.",
    )
    args = parser.parse_args(argv)

    if args.single is not None:
        cli = run_cli(args.single)
        cpv = run_cpv(args.single)
        d = diff(cli, cpv)
        print(
            json.dumps(
                {
                    "fixture": str(args.single),
                    "cli_available": cli.available,
                    "cli_exit": cli.exit_code,
                    "cpv_exit": cpv.exit_code,
                    "cli_only": [f.__dict__ for f in d.cli_only],
                    "cpv_only": [f.__dict__ for f in d.cpv_only],
                    "both": [(a.__dict__, b.__dict__) for a, b in d.both],
                },
                indent=2,
            )
        )
        return 0

    rows = audit_grid(args.grid)
    write_audit_report(rows, args.report)
    print(f"Wrote audit report → {args.report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
