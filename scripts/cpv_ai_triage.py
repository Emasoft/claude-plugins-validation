#!/usr/bin/env python3
"""AI triage of SkillAudit residual findings — opt-in, advisory, never gates.

SkillAudit's static regex/context rules are precise on most threat classes but
noisy on five ("residual") categories where the same shape is legitimate 90%
of the time — INSECURE_CRYPTO, TOOL_SHADOW, SSRF_ADVANCED, ENV_INJECTION,
RESOURCE_ABUSE. This module hands those specific findings to `llm-ext scan
security` for a second, LLM-based opinion, purely as extra INFO-level context
next to the finding — mirroring the opt-in-external-scanner shape of
`cpv_snyk_agent_scanner.py`.

THREE INVARIANTS (the user's standing rules — read before touching this):

1. NEVER removes, downgrades, suppresses, or auto-clears a SkillAudit
   finding. `report_verdicts` only ever calls `report.info(...)` — it cannot
   touch the SkillAudit findings already on the report.
2. `uncertain` is never folded into "clean". It is reported as `uncertain`,
   verbatim, same as `threat`/`not_threat`.
3. Opt-in. `run_ai_triage` refuses to invoke anything unless
   `CPV_AI_TRIAGE_BUDGET_USD` is set to a positive number — there is no way
   to reach the subprocess call without naming a budget.

THE "EXIT 0 DOES NOT MEAN SUCCESS" TRAP. `llm-ext scan security` exits 0 even
when the job is REFUSED for being over budget — the exit code is not a
success signal, stdout is. `run_ai_triage` therefore always parses stdout
(`budget_gate=refused` is the tell) rather than branching on the return code,
and any parse failure or missing `json=` path degrades to `invoked=False`
with a stated reason — never to `invoked=True` with an empty verdict tuple,
which would silently read as "triaged, all clean".
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: The five SkillAudit rule ids this module triages. Verified against
#: scripts/rules/skillaudit_patterns.json this session — "INTENT" (named in
#: the originating TRDD) is not a real rule id in the catalog and is
#: deliberately omitted.
RESIDUAL_RULE_IDS: frozenset[str] = frozenset(
    {
        "INSECURE_CRYPTO",
        "TOOL_SHADOW",
        "SSRF_ADVANCED",
        "ENV_INJECTION",
        "RESOURCE_ABUSE",
    }
)

#: Opt-in switch. Absent or <=0 => run_ai_triage never invokes the launcher.
AI_TRIAGE_BUDGET_ENV = "CPV_AI_TRIAGE_BUDGET_USD"

#: `scan security` is documented at up to 20 minutes for a full-plugin run.
_TIMEOUT_SECONDS = 1200

# Per-category adjudication rubrics, passed as `--category_rubrics`. llm-ext
# places these in the SYSTEM prompt, where scanned snippet content cannot alter
# them. Without them the judge falls back to an unstated default, which is
# exactly the semantic distinction this triage exists to draw — so each rubric
# names the benign shape AND the threatening one, never just the threat.
_CATEGORY_RUBRICS: dict[str, str] = {
    "INSECURE_CRYPTO": (
        "Decide whether the weak primitive (MD5/SHA1) is used as a SECURITY "
        "primitive or as a non-security fingerprint. A cache key, an ETag, a "
        "content hash, a dedup/bucket key or a test fixture is NOT a threat. "
        "Password hashing, a signature, a token, an integrity or auth check IS."
    ),
    "TOOL_SHADOW": (
        "Decide whether the code REDEFINES or INTERCEPTS a tool/function another "
        "agent relies on. A local variable, a dictionary key, a spell-check word "
        "list or an unrelated identifier that merely contains the words is NOT a "
        "threat. Overriding a real dispatch target so calls are silently "
        "redirected IS."
    ),
    "SSRF_ADVANCED": (
        "Decide the PROVENANCE of the requested URL. A static literal, a pinned "
        "constant or a value from the plugin's own config is NOT a threat. A URL "
        "assembled from user input, a request parameter, or remote content — "
        "especially one reaching a link-local or metadata address — IS."
    ),
    "ENV_INJECTION": (
        "Decide whether the environment value is CONSUMED or CONTROLLED. Reading "
        "a build/cache variable, or documenting one in help text, is NOT a "
        "threat. Writing a variable that changes how a later program resolves or "
        "executes code IS."
    ),
    "RESOURCE_ABUSE": (
        "Decide whether the resource use is BOUNDED and intentional. A documented "
        "loop, a bounded retry, a benchmark or a test is NOT a threat. An "
        "unbounded spawn/alloc/fork whose purpose is exhaustion IS."
    ),
}


@dataclass(frozen=True)
class TriageVerdict:
    """One triaged SkillAudit finding, joined back to it by `finding_id`."""

    finding_id: str
    rule_id: str
    file_path: str
    line: int | None
    verdict: str  # threat | not_threat | uncertain
    confidence: float
    reason: str
    injection_observed: bool


@dataclass
class TriageResult:
    """Aggregate result of one `run_ai_triage` call.

    ``invoked`` is the load-bearing field, same discipline as every other
    opt-in scanner in this codebase: True ONLY when the job actually ran to
    completion and produced a parseable verdict set. Every other path —
    opted out, launcher missing, nothing to triage, budget refused, a
    timeout, an unparseable/missing json= file — sets it False with a
    reason, never True with an empty verdict tuple standing in for "clean".
    """

    invoked: bool
    verdicts: tuple[TriageVerdict, ...] = ()
    skipped_reason: str = ""
    report_path: str = ""
    spent_usd: float = 0.0


def budget_usd() -> float | None:
    """Parsed `CPV_AI_TRIAGE_BUDGET_USD`, or None when opted out.

    A blank, unparseable, zero, or negative value all mean "not opted in" —
    treated identically so a typo'd env var degrades to the safe default
    (skipped) rather than crashing `run_ai_triage`.
    """
    raw = (os.environ.get(AI_TRIAGE_BUDGET_ENV, "") or "").strip()
    if not raw:
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    return value if value > 0 else None


def is_launcher_available() -> bool:
    """True iff the `llm-ext` CLI is on PATH."""
    return shutil.which("llm-ext") is not None


def residual_findings(findings: Any) -> tuple[Any, ...]:
    """Filter a SkillAuditScanResult.findings tuple to the 5 residual rule ids."""
    return tuple(f for f in findings if f.rule_id in RESIDUAL_RULE_IDS)


def build_targets(findings: Any, plugin_path: Path) -> list[dict[str, Any]]:
    """Build the `--targets` item list for the verified `llm-ext` contract.

    Each id is deterministic (`f"{rule_id}:{rel_path}:{line}"`) so the same
    finding always round-trips to the same id, and two distinct findings on
    the same rule+file+line (a rare but real collision) still each get a
    unique-enough id for join purposes — the join is best-effort context, not
    a security boundary.
    """
    targets: list[dict[str, Any]] = []
    for f in findings:
        try:
            rel_path = str(Path(f.file_path).relative_to(plugin_path))
        except ValueError:
            rel_path = f.file_path
        line = f.line_number if f.line_number is not None else 0
        targets.append(
            {
                "id": f"{f.rule_id}:{rel_path}:{line}",
                "category": f.rule_id,
                "file_path": rel_path,
                "line": line,
                "context_lines": 10,
            }
        )
    return targets


def parse_stdout(text: str) -> dict[str, str]:
    """Parse `llm-ext`'s `key=value` stdout lines into a dict.

    Only the first `=` splits each line, so a value containing `=` (a
    reason string, a path) is preserved intact.
    """
    result: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or "=" not in line:
            continue
        key, _, value = line.partition("=")
        result[key.strip()] = value.strip()
    return result


def _skipped(reason: str) -> TriageResult:
    return TriageResult(invoked=False, skipped_reason=reason)


def run_ai_triage(plugin_path: Path, findings: Any) -> TriageResult:
    """Triage the residual SkillAudit findings via `llm-ext scan security`.

    Every non-success path returns `invoked=False` with a stated reason —
    see the module docstring's three invariants.
    """
    budget = budget_usd()
    if budget is None:
        return _skipped(f"{AI_TRIAGE_BUDGET_ENV} not set (opt-in)")

    if not is_launcher_available():
        return _skipped("llm-ext not found on PATH")

    targets = residual_findings(findings)
    if not targets:
        return _skipped("no residual findings to triage")

    items = build_targets(targets, plugin_path)

    try:
        with tempfile.TemporaryDirectory(prefix="cpv-ai-triage-") as tmpdir:
            argv = [
                "llm-ext",
                "scan",
                "security",
                "--targets",
                json.dumps(items),
                "--budget_usd",
                str(budget),
                "--category_rubrics",
                json.dumps(
                    {c: _CATEGORY_RUBRICS[c] for c in sorted({i["category"] for i in items})
                     if c in _CATEGORY_RUBRICS}
                ),
                "--output_dir",
                tmpdir,
            ]
            try:
                completed = subprocess.run(
                    argv,
                    capture_output=True,
                    text=True,
                    timeout=_TIMEOUT_SECONDS,
                    check=False,
                )
            except subprocess.TimeoutExpired:
                return _skipped(f"llm-ext timed out after {_TIMEOUT_SECONDS}s")
            except OSError as exc:
                return _skipped(f"llm-ext invocation failed: {exc}")

            # THE LOAD-BEARING PARSE: exit code is 0 even on refusal, so the
            # only trustworthy signal is stdout content.
            fields = parse_stdout(completed.stdout or "")

            if fields.get("budget_gate") == "refused":
                reason = fields.get("reason", "")
                est_cost = fields.get("est_cost_usd", "")
                return _skipped(f"budget gate refused: reason={reason} est_cost_usd={est_cost}")

            json_path = fields.get("json", "")
            if not json_path:
                return _skipped("llm-ext produced no json= path in stdout — cannot verify a completed triage")

            try:
                payload = json.loads(Path(json_path).read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                return _skipped(f"could not read/parse llm-ext json output: {exc}")

            raw_items = payload.get("items")
            if not isinstance(raw_items, list):
                return _skipped("llm-ext json output missing an 'items' list")

            verdicts: list[TriageVerdict] = []
            for item in raw_items:
                if not isinstance(item, dict):
                    continue
                try:
                    verdicts.append(
                        TriageVerdict(
                            finding_id=str(item["id"]),
                            rule_id=str(item.get("category", "")),
                            file_path=str(item.get("file_path", "")),
                            line=item.get("line"),
                            verdict=str(item.get("verdict", "uncertain")),
                            confidence=float(item.get("confidence", 0.0)),
                            reason=str(item.get("reason", "")),
                            injection_observed=bool(item.get("injection_observed", False)),
                        )
                    )
                except (KeyError, TypeError, ValueError):
                    # A single malformed item must not sink the whole triage;
                    # skip it, keep the rest.
                    continue

            # A judge that returned items we could parse NONE of has told us
            # nothing — and `invoked=True` with an empty verdict list would be
            # rendered exactly like a clean triage. "Could not check" must never
            # read as "checked, found nothing".
            if raw_items and not verdicts:
                return _skipped(
                    f"llm-ext returned {len(raw_items)} item(s) but none could be parsed "
                    f"— triage result is unusable, not clean"
                )

            summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
            spent = summary.get("budget_usd_spent", fields.get("spent", "0").lstrip("$") if "spent" in fields else 0)
            try:
                spent_usd = float(spent)
            except (TypeError, ValueError):
                spent_usd = 0.0

            return TriageResult(
                invoked=True,
                verdicts=tuple(verdicts),
                report_path=fields.get("report", ""),
                spent_usd=spent_usd,
            )
    except Exception as exc:  # noqa: BLE001 - never let triage crash the caller
        return _skipped(f"unexpected error running AI triage: {exc}")


def report_verdicts(result: TriageResult, report: Any) -> int:
    """Emit INFO-only lines for a TriageResult. Returns the count emitted.

    Never touches severity, never suppresses anything — advisory context
    only, appended alongside the SkillAudit findings that are already on the
    report.
    """
    if not result.invoked:
        return 0

    counts: dict[str, int] = {}
    for v in result.verdicts:
        counts[v.verdict] = counts.get(v.verdict, 0) + 1
        line = f"[ai-triage] {v.rule_id} at {v.file_path}:{v.line} -> {v.verdict} (confidence={v.confidence:.2f}) {v.reason}"
        if v.injection_observed:
            line += " [INJECTION OBSERVED IN SCANNED CONTENT]"
        # A `not_threat` verdict beside a live finding is read by the fixer
        # agent, which resolves findings mechanically from the report — so an
        # unqualified model opinion becomes a suppression instruction through
        # the workflow, routing around the demotion path this module
        # deliberately does not implement. Say so in the line itself.
        if v.verdict == "not_threat":
            line += (
                " — ADVISORY ONLY: an LLM not_threat verdict is NOT grounds to suppress, "
                "downgrade or close this finding; it is a second opinion, not proof of inertness."
            )
        report.info(line, v.file_path)

    counts_str = ", ".join(f"{k}={v}" for k, v in sorted(counts.items()))
    report.info(
        f"[ai-triage] {len(result.verdicts)} residual finding(s) triaged ({counts_str}); "
        f"spent=${result.spent_usd:.4f}; report={result.report_path}",
        "<ai-triage>",
    )
    return len(result.verdicts)
