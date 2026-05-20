#!/usr/bin/env python3
"""SkillAudit (megamind-0x/skillaudit) wrapper — MANDATORY external scanner.

SkillAudit is a Node.js-based security scanner for AI agent skills,
MCP manifests, and agent projects. It detects credential stealers, data
exfiltration, prompt injection, MCP schema poisoning, A2A attacks,
shell exec, obfuscation, supply-chain hazards, container escapes,
persistence, and crypto theft — 43 rule categories / 401 patterns.

Reference: https://github.com/megamind-0x/skillaudit

This wrapper enforces the IRON RULE:
* The scanner is invoked on every security validation pass.
* There is NO env-var opt-out and NO ``--skip-skillaudit`` CLI flag.
* When ``npx`` / ``skillaudit`` is missing the scanner reports a
  CRITICAL finding (not a WARNING / SKIPPED) so the iron rule
  "no plugin with issues must be pushed to GitHub ever" still holds.

Invocation:
    npx --yes skillaudit <plugin_path> --json --fail-on critical

Output is parsed from stdout JSON, mapped into CPV's severity model,
and appended to the ValidationReport via ``report_findings``.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Severity mapping from skillaudit risk levels to CPV ValidationReport
# levels. SkillAudit uses: critical / high / moderate / low / clean.
# CPV uses: critical / major / minor / nit / info.
_SKILLAUDIT_TO_CPV_SEVERITY: dict[str, str] = {
    "critical": "critical",
    "high": "major",
    "moderate": "minor",
    "low": "nit",
    "clean": "info",
    # Per-finding severities (when skillaudit emits them at finding level
    # rather than as the overall riskLevel) sometimes use these synonyms:
    "medium": "minor",
    "info": "info",
}

# Bounded execution. SkillAudit is normally fast (43 rules, no network
# fetches for local-path scans), but a cold `npx` resolution can add
# 1-2s on first invocation. Default 5 minutes is generous for any
# plugin tree size.
DEFAULT_TIMEOUT_SECONDS = int(os.environ.get("CPV_SKILLAUDIT_TIMEOUT_S", "300"))

_PACKAGE_NAME = "skillaudit"


@dataclass(frozen=True)
class SkillAuditFinding:
    """One normalised finding from skillaudit JSON output."""

    severity: str  # CPV-canonical: critical/major/minor/nit/info
    rule_id: str  # SkillAudit rule ID (e.g. "CRED_ENV_READ")
    message: str  # Human-readable finding text
    file_path: str  # Relative path inside the scanned plugin
    line_number: int | None  # 1-indexed line, None if not applicable
    raw: dict[str, Any]  # Original skillaudit finding object


@dataclass(frozen=True)
class SkillAuditScanResult:
    """Aggregate result of one ``skillaudit`` invocation."""

    invoked: bool  # True iff the scanner ran to completion
    findings: tuple[SkillAuditFinding, ...]
    skipped_reason: str  # Empty when invoked; explains why otherwise
    raw_stdout: str  # Captured stdout (JSON when invoked OK)
    raw_stderr: str  # Captured stderr (diagnostics)
    exit_code: int  # subprocess exit code; -1 if not invoked


def is_skillaudit_available() -> bool:
    """True iff a launcher for skillaudit is available on PATH.

    Accepts EITHER the persistent ``skillaudit`` binary (installed
    globally via ``npm install -g skillaudit``) OR ``npx``, which can
    resolve the package on the fly via ``npx --yes skillaudit``.
    """
    return shutil.which("skillaudit") is not None or shutil.which("npx") is not None


def build_scan_command(plugin_path: Path) -> list[str]:
    """Build the argv for the skillaudit scan.

    Prefers the persistent ``skillaudit`` binary (faster — no npx
    resolution cost). Falls back to ``npx --yes skillaudit`` so the
    scanner runs on machines that only have Node.js + npx without a
    pre-installed global package.

    The ``--json`` flag is REQUIRED for machine-parsing. We do NOT
    pass ``--fail-on`` here because the CPV report aggregates the
    finding severities itself — exit-code-based gating happens at the
    CPV pipeline level, not at the per-scanner level.
    """
    if shutil.which("skillaudit"):
        prefix: list[str] = ["skillaudit"]
    else:
        # `--yes` accepts the npx package-install prompt non-interactively.
        prefix = ["npx", "--yes", _PACKAGE_NAME]
    return prefix + [str(plugin_path), "--json"]


def parse_findings(
    json_blob: str | bytes | dict[str, Any],
) -> tuple[SkillAuditFinding, ...]:
    """Convert skillaudit JSON output into ordered SkillAuditFinding tuples.

    The scanner's JSON shape is approximately:
        {
          "riskLevel": "moderate",
          "findings": [
            {"ruleId": "CRED_ENV_READ", "severity": "high",
             "message": "...", "file": "...", "line": 42},
            ...
          ],
          "summary": {...}
        }

    Some skillaudit builds nest findings under ``results[].findings`` like
    the Cisco scanner; both shapes are handled defensively. Findings
    without an explicit ``severity`` fall back to the top-level
    ``riskLevel``.
    """
    if isinstance(json_blob, (str, bytes)):
        try:
            data = json.loads(json_blob)
        except json.JSONDecodeError:
            return ()
    else:
        data = json_blob

    if not isinstance(data, dict):
        return ()

    overall_level = str(data.get("riskLevel") or data.get("risk") or "").lower()
    findings: list[SkillAuditFinding] = []
    for raw in _iter_raw_findings(data):
        findings.append(_normalise_finding(raw, overall_level))
    return tuple(findings)


def _iter_raw_findings(data: dict[str, Any]) -> Iterable[dict[str, Any]]:
    """Yield raw finding dicts regardless of top-level shape variation."""
    direct = data.get("findings")
    if isinstance(direct, list):
        for item in direct:
            if isinstance(item, dict):
                yield item
        return
    results = data.get("results")
    if isinstance(results, list):
        for result in results:
            if isinstance(result, dict):
                inner = result.get("findings")
                if isinstance(inner, list):
                    for item in inner:
                        if isinstance(item, dict):
                            yield item


def _normalise_finding(
    raw: dict[str, Any],
    fallback_level: str,
) -> SkillAuditFinding:
    """Map one skillaudit finding object to a SkillAuditFinding dataclass."""
    severity_raw = (
        raw.get("severity")
        or raw.get("severity_level")
        or raw.get("level")
        or fallback_level
        or "info"
    )
    severity_key = str(severity_raw).lower()
    severity = _SKILLAUDIT_TO_CPV_SEVERITY.get(severity_key, "minor")

    rule_id = (
        raw.get("ruleId")
        or raw.get("rule_id")
        or raw.get("id")
        or raw.get("category")
        or "skillaudit.unknown"
    )

    message = (
        raw.get("message")
        or raw.get("description")
        or raw.get("title")
        or raw.get("rule")
        or ""
    )
    if not isinstance(message, str):
        message = str(message)

    file_path = (
        raw.get("file")
        or raw.get("file_path")
        or raw.get("path")
        or (raw.get("location") or {}).get("file")
        or ""
    )

    line_raw = (
        raw.get("line")
        or raw.get("line_number")
        or (raw.get("location") or {}).get("line")
    )
    try:
        line_number: int | None = int(line_raw) if line_raw is not None else None
    except (TypeError, ValueError):
        line_number = None

    return SkillAuditFinding(
        severity=severity,
        rule_id=str(rule_id),
        message=message,
        file_path=str(file_path),
        line_number=line_number,
        raw=raw,
    )


def run_skillaudit_scan(
    plugin_path: Path,
    *,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
) -> SkillAuditScanResult:
    """Invoke skillaudit and return parsed findings.

    Returns ``SkillAuditScanResult.invoked == False`` when neither
    ``skillaudit`` nor ``npx`` is on PATH or the scan crashes /
    times out. Callers MUST treat "not invoked" as a CRITICAL coverage
    gap, not as "no findings" — see ``report_findings`` for the
    iron-rule enforcement.
    """
    if not is_skillaudit_available():
        return SkillAuditScanResult(
            invoked=False,
            findings=(),
            skipped_reason=(
                "neither `skillaudit` nor `npx` on PATH — install Node.js "
                "(>=18) and re-run `cpv-doctor --install-scanners`"
            ),
            raw_stdout="",
            raw_stderr="",
            exit_code=-1,
        )

    cmd = build_scan_command(plugin_path)
    try:
        completed = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return SkillAuditScanResult(
            invoked=False,
            findings=(),
            skipped_reason=f"skillaudit scan timed out after {timeout_seconds}s",
            raw_stdout=exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or ""),
            raw_stderr=exc.stderr.decode() if isinstance(exc.stderr, bytes) else (exc.stderr or ""),
            exit_code=-2,
        )
    except FileNotFoundError as exc:
        return SkillAuditScanResult(
            invoked=False,
            findings=(),
            skipped_reason=f"skillaudit invocation failed: {exc}",
            raw_stdout="",
            raw_stderr=str(exc),
            exit_code=-3,
        )

    findings = parse_findings(completed.stdout) if completed.stdout.strip() else ()
    return SkillAuditScanResult(
        invoked=True,
        findings=findings,
        skipped_reason="",
        raw_stdout=completed.stdout,
        raw_stderr=completed.stderr,
        exit_code=completed.returncode,
    )


def report_findings(
    result: SkillAuditScanResult,
    plugin_path: Path,
    report: Any,
    should_skip: "Callable[[str, int | None], bool] | None" = None,
) -> int:
    """Adapt a SkillAuditScanResult into ValidationReport.<severity>(...) calls.

    Iron-rule enforcement: when ``result.invoked is False``, this
    function appends a CRITICAL finding (NOT info / NOT warning).
    skillaudit is mandatory — a plugin that could not be scanned by
    skillaudit cannot be marked clean.

    Returns the count of findings appended (including the iron-rule
    CRITICAL when the scanner could not run).
    """
    if not result.invoked:
        report.critical(
            f"skillaudit MANDATORY scanner could not run — {result.skipped_reason}. "
            "Install Node.js >= 18 and re-run `cpv-doctor --install-scanners`.",
            "<external-scanner>",
        )
        return 1

    appended = 0
    for finding in result.findings:
        line = finding.line_number
        rel_file = _relativise(finding.file_path, plugin_path)
        if should_skip is not None and should_skip(finding.file_path or rel_file, line):
            continue
        message = f"[skillaudit {finding.rule_id}] {finding.message}".strip()
        if finding.severity == "info":
            report.info(message, rel_file)
        else:
            method = getattr(report, finding.severity, None) or report.minor
            method(message, rel_file, line)
        appended += 1
    return appended


def _relativise(file_path: str, plugin_root: Path) -> str:
    """Return a path relative to plugin_root if possible, else the original."""
    if not file_path:
        return "<unknown>"
    candidate = Path(file_path)
    try:
        return str(candidate.relative_to(plugin_root))
    except ValueError:
        return file_path
