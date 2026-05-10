#!/usr/bin/env python3
"""Cisco AI Defense skill-scanner wrapper.

Invokes https://github.com/cisco-ai-defense/skill-scanner via `uvx` and
adapts its findings into CPV's ValidationReport. Programmatic-only mode:
NO API-key-requiring engines (no LLM, no Meta, no VirusTotal, no AI Defense
cloud). Uses the local engines: Static (YAML+YARA), Bytecode, Pipeline,
and Behavioral (AST dataflow).

Why uvx and not pip-install: keeps the dependency surface zero in the CPV
repo, runs the scanner in an isolated env, and the Cisco package and its
~90 transitive deps stay out of the CPV install surface. Cached after
first invocation.

Reference scan command (assembled by `build_scan_command()`):
    uvx --from cisco-ai-skill-scanner skill-scanner scan-all <plugin>
        --recursive
        --lenient            # tolerate Claude Code .claude/commands/*.md
        --use-behavioral     # AST dataflow (no API key)
        --policy balanced    # default risk preset
        --format json
        --compact
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

# Severity mapping from Cisco (lowercase) to CPV ValidationReport levels.
# Cisco uses: critical / high / medium / low / info.
# CPV uses: critical / major / minor / nit / info.
_CISCO_TO_CPV_SEVERITY: dict[str, str] = {
    "critical": "critical",
    "high": "major",
    "medium": "minor",
    "low": "nit",
    "info": "info",
}

# Bounded execution: the scanner clones, indexes, runs YARA, etc.
# Default 10 minutes covers most plugin sizes (cold-start uvx download
# of the ~90 transitive deps eats the first 60-120s on a fresh machine).
# Override via CPV_CISCO_SCAN_TIMEOUT_S=<seconds> for very large trees.
DEFAULT_TIMEOUT_SECONDS = int(os.environ.get("CPV_CISCO_SCAN_TIMEOUT_S", "600"))

# Pinned to a major-version range. Bumping this is an explicit decision so
# that a Cisco breaking change doesn't silently alter scan behaviour.
_CISCO_PACKAGE_SPEC = "cisco-ai-skill-scanner"


@dataclass(frozen=True)
class CiscoFinding:
    """One normalised finding from skill-scanner JSON output."""

    severity: str  # CPV-canonical: critical/major/minor/nit/info
    rule_id: str  # Cisco rule ID (e.g. "static.prompt_injection.v1")
    message: str  # Human-readable finding text
    file_path: str  # Relative path inside the scanned plugin
    line_number: int | None  # 1-indexed line, None if not applicable
    raw: dict[str, Any]  # Original Cisco finding object (for debugging)


@dataclass(frozen=True)
class CiscoScanResult:
    """Aggregate result of one `skill-scanner scan-all` invocation."""

    invoked: bool  # True iff uvx ran the scanner to completion
    findings: tuple[CiscoFinding, ...]
    skipped_reason: str  # Empty when invoked; explains why otherwise
    raw_stdout: str  # Captured stdout (JSON when invoked OK)
    raw_stderr: str  # Captured stderr (diagnostics)
    exit_code: int  # subprocess exit code; -1 if not invoked


def is_uvx_available() -> bool:
    """True iff a launcher for the Cisco skill-scanner is available.

    v2.48 — accepts EITHER the persistent ``skill-scanner`` binary (created
    by ``uv tool install cisco-ai-skill-scanner``) OR the ephemeral ``uvx``
    fallback. The persistent path skips the ~5-10s uvx resolution cost on
    every scan, which dominates the per-target startup overhead in
    marketplace bulk scans.
    """
    return shutil.which("skill-scanner") is not None or shutil.which("uvx") is not None


def build_scan_command(
    plugin_path: Path,
    *,
    json_output_path: Path,
    policy: str = "balanced",
    use_behavioral: bool = True,
    use_trigger: bool = True,
    package_spec: str = _CISCO_PACKAGE_SPEC,
) -> list[str]:
    """Build the argv for the programmatic-only scan.

    Programmatic-only means:
    - NO --use-llm (needs SKILL_SCANNER_LLM_API_KEY)
    - NO --enable-meta (needs an LLM key)
    - NO --use-virustotal (needs VIRUSTOTAL_API_KEY)
    - NO --use-aidefense (needs AI_DEFENSE_API_KEY)

    The `--lenient` flag is REQUIRED for Claude Code plugins because they
    don't ship a `SKILL.md`; the scanner falls back to scanning markdown
    files in the directory.

    v2.48 — prefers the persistent ``skill-scanner`` binary (installed via
    ``uv tool install cisco-ai-skill-scanner``) over the ephemeral
    ``uvx --from cisco-ai-skill-scanner skill-scanner`` resolution. The
    persistent path saves ~5-10s of resolve cost per invocation, which
    matters in marketplace bulk scans that spawn N invocations.
    """
    if shutil.which("skill-scanner"):
        # Persistent install path — direct binary call.
        prefix: list[str] = ["skill-scanner"]
    else:
        # Ephemeral uvx fallback — slower but works without a prior install.
        prefix = ["uvx", "--from", package_spec, "skill-scanner"]

    cmd: list[str] = prefix + [
        "scan-all",
        str(plugin_path),
        "--recursive",
        "--lenient",
        "--policy",
        policy,
        "--format",
        "json",
        "--output-json",
        str(json_output_path),
    ]
    if use_behavioral:
        cmd.append("--use-behavioral")
    if use_trigger:
        cmd.append("--use-trigger")
    return cmd


def parse_findings(json_blob: str | bytes | dict[str, Any]) -> tuple[CiscoFinding, ...]:
    """Convert skill-scanner JSON output into ordered CiscoFinding tuples.

    The scanner's JSON shape is `{"results": [{"skill_name": ..., "findings": [...]}, ...]}`.
    Each finding has at minimum: severity, rule_id, message, location.file, location.line.
    Older builds may emit `severity_level` instead of `severity`; both forms are read.
    """
    if isinstance(json_blob, (str, bytes)):
        data = json.loads(json_blob)
    else:
        data = json_blob

    findings: list[CiscoFinding] = []
    for result in _iter_results(data):
        for raw_finding in result.get("findings", ()):
            findings.append(_normalise_finding(raw_finding))
    return tuple(findings)


def _iter_results(data: dict[str, Any]) -> Iterable[dict[str, Any]]:
    """Yield per-skill result dicts regardless of top-level shape variation."""
    if not isinstance(data, dict):
        return
    if isinstance(data.get("results"), list):
        yield from data["results"]
        return
    # Single-skill scan emits a flat result without the "results" wrapper.
    if "findings" in data:
        yield data


def _normalise_finding(raw: dict[str, Any]) -> CiscoFinding:
    """Map one Cisco finding object to a CiscoFinding dataclass."""
    severity_raw = (raw.get("severity") or raw.get("severity_level") or "info").lower()
    severity = _CISCO_TO_CPV_SEVERITY.get(severity_raw, "minor")

    rule_id = raw.get("rule_id") or raw.get("ruleId") or raw.get("id") or "cisco.unknown"

    message = raw.get("message") or raw.get("description") or raw.get("title") or ""
    if not isinstance(message, str):
        message = str(message)

    location = raw.get("location") or {}
    file_path = location.get("file") or location.get("file_path") or raw.get("file") or ""
    line_raw = location.get("line") or location.get("line_number") or raw.get("line")
    try:
        line_number: int | None = int(line_raw) if line_raw is not None else None
    except (TypeError, ValueError):
        line_number = None

    return CiscoFinding(
        severity=severity,
        rule_id=str(rule_id),
        message=message,
        file_path=str(file_path),
        line_number=line_number,
        raw=raw,
    )


def run_cisco_scan(
    plugin_path: Path,
    *,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    policy: str = "balanced",
    use_behavioral: bool = True,
    use_trigger: bool = True,
) -> CiscoScanResult:
    """Invoke the Cisco scanner and return parsed findings.

    Returns a CiscoScanResult.invoked == False when uvx is missing or the
    invocation crashes — the caller should treat that as "scanner skipped",
    NOT as "no findings". Distinguishing those two cases is critical for
    CI gating decisions.
    """
    if not is_uvx_available():
        return CiscoScanResult(
            invoked=False,
            findings=(),
            skipped_reason="uvx not on PATH; install uv to enable Cisco skill-scanner",
            raw_stdout="",
            raw_stderr="",
            exit_code=-1,
        )

    json_path = plugin_path / ".cpv-cisco-scan.json"
    cmd = build_scan_command(
        plugin_path,
        json_output_path=json_path,
        policy=policy,
        use_behavioral=use_behavioral,
        use_trigger=use_trigger,
    )

    try:
        completed = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return CiscoScanResult(
            invoked=False,
            findings=(),
            skipped_reason=f"Cisco scan timed out after {timeout_seconds}s",
            raw_stdout=exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or ""),
            raw_stderr=exc.stderr.decode() if isinstance(exc.stderr, bytes) else (exc.stderr or ""),
            exit_code=-2,
        )
    except FileNotFoundError as exc:
        return CiscoScanResult(
            invoked=False,
            findings=(),
            skipped_reason=f"uvx invocation failed: {exc}",
            raw_stdout="",
            raw_stderr=str(exc),
            exit_code=-3,
        )

    findings: tuple[CiscoFinding, ...] = ()
    if json_path.is_file():
        try:
            findings = parse_findings(json_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            findings = ()
        finally:
            try:
                json_path.unlink()
            except OSError:
                pass
    elif completed.stdout.strip().startswith("{"):
        # Some skill-scanner builds emit JSON to stdout when --output-json
        # is set but the file write fails (e.g. read-only fs); fall back.
        try:
            findings = parse_findings(completed.stdout)
        except json.JSONDecodeError:
            findings = ()

    return CiscoScanResult(
        invoked=True,
        findings=findings,
        skipped_reason="",
        raw_stdout=completed.stdout,
        raw_stderr=completed.stderr,
        exit_code=completed.returncode,
    )


def report_findings(
    result: CiscoScanResult,
    plugin_path: Path,
    report: Any,
    should_skip: "Callable[[str, int | None], bool] | None" = None,
) -> int:
    """Adapt a CiscoScanResult into ValidationReport.<severity>(...) calls.

    Returns the count of findings appended (0 if the scanner was skipped
    or if `should_skip` filtered every finding out).

    The `report` argument is duck-typed against ValidationReport — only
    `.critical(msg, file, line)`, `.major(...)`, `.minor(...)`, `.nit(...)`,
    `.info(...)` are required, matching the existing CPV report API.

    `should_skip` is the per-finding filter callback. Signature:
    `(absolute_or_relative_file_path: str, line: int | None) -> bool`.
    Return True to drop the finding. Used by CPV's main() to wire in the
    self-scan / vendored-dep / dev-scratch / test-file / corpus filters
    so the Cisco scanner doesn't surface CPV's own rule catalogs as
    findings when CPV is scanning itself. The callback receives the
    finding's REPORTED file path verbatim (already a string); CPV's
    helpers handle abs↔rel normalisation internally.

    Without a callback every finding is reported — keeps the wrapper
    stand-alone usable outside CPV.
    """
    if not result.invoked:
        # Scanner was unavailable or timed out: surface as INFO so the
        # operator knows external coverage was missing for this run.
        # ValidationReport.info() takes (message, file=None) — no `line`.
        report.info(
            f"Cisco skill-scanner skipped — {result.skipped_reason}",
            "<external-scanner>",
        )
        return 0

    appended = 0
    for finding in result.findings:
        line = finding.line_number
        rel_file = _relativise(finding.file_path, plugin_path)
        # Apply the host's filter chain. The full reported file path
        # (which may be absolute) is passed first because CPV's
        # cpv_self_scan_skip uses substring matching that works on
        # both abs and rel forms.
        if should_skip is not None and should_skip(finding.file_path or rel_file, line):
            continue
        message = f"[cisco {finding.rule_id}] {finding.message}".strip()
        if finding.severity == "info":
            # ValidationReport.info() doesn't accept a line number.
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
