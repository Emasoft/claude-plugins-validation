#!/usr/bin/env python3
"""cpv_fix_ledger.py — compact by-file findings LEDGER for the CPV fix loop.

The fix loop (``cpv-plugin-fixer-agent`` / ``cpv-marketplace-fixer-agent``) validates with
``--strict --json``, producing a large findings report. Re-reading that whole
report every iteration is the dominant token cost: cost ≈ turns ×
per-turn-context — the full report rides forward in the transcript and is
re-charged on *every* later turn, so a 1746-result report read once can cost
millions of tokens across a 20-iteration fix loop.

This module distills a findings JSON into a compact ledger the agent reads
*instead* of the full report:

* findings grouped **by file**, sorted by line within each file;
* split into **MECH** (mechanically auto-fixable — ``fixable: true``) and
  **INTEL** (needs judgement — ``fixable: false``);
* a tiny ``--text`` surface for the lean read;
* INFO / PASSED results dropped entirely (they are not findings; the fixer
  never acts on them).

It is a **READ-ONLY transform**. It never validates, never edits a plugin,
and never suppresses or relaxes a finding — it only re-shapes an existing
findings JSON. The severity semantics it encodes MIRROR CPV's own rules; it
invents none of them (see ``finding_is_blocking`` / ``warning_is_blocking``).

Input schema (confirmed against
``remote_validation.py plugin . --strict --json``)::

    {
      "exit_code": int,
      "counts":  {...},                 # lowercase-keyed totals — ignored here
      "results": [ ValidationResult.to_dict(), ... ],
      "security_gates": {...}           # ignored
    }

Each result (see ``cpv_validation_common.ValidationResult.to_dict``)::

    level:      UPPERCASE — CRITICAL|MAJOR|MINOR|NIT|WARNING|INFO|PASSED
    message:    str (always present)
    file:       str | null | absent
    line:       int | null | absent
    fixable:    present & true  ⇒ mechanically fixable (absent ⇒ false)
    fix_id:     str, only when fixable
    category:   str, absent when empty
    suggestion: str, absent when None

CLI::

    cpv_fix_ledger.py build --json <findings.json> --out <ledger.json> \
        [--text <ledger.txt>]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

# --------------------------------------------------------------------------
# Severity semantics — MIRRORED from CPV, never invented here.
# --------------------------------------------------------------------------

# INFO / PASSED are NOT findings — the fixer never acts on them, so they are
# excluded from the ledger entirely. Every other level is a finding.
_NON_FINDING_LEVELS: frozenset[str] = frozenset({"INFO", "PASSED"})

# Always-blocking severities under ``--strict``. This MIRRORS
# ``cpv_fix_loop_state._BLOCKING_SEVERITIES`` (critical/major/minor/nit) — the
# single source of truth for "what blocks the gate". WARNING is evaluated
# separately (see below); INFO/PASSED never block and are not findings.
_ALWAYS_BLOCKING_LEVELS: frozenset[str] = frozenset({"CRITICAL", "MAJOR", "MINOR", "NIT"})

# Per-level summary buckets we count (lowercase, matching the input ``counts``).
_SUMMARY_LEVELS: tuple[str, ...] = ("critical", "major", "minor", "nit", "warning")

# Bucket key used when a finding has no associated file.
_NO_FILE = "<no-file>"

# --------------------------------------------------------------------------
# WARNING blocking classification — encoded AS DATA, verbatim from
# skills/cpv-fix-validation/references/iterative-fix-loop.md
#   §"WARNING evaluation rules"  (rules 1-6)
#   §"Publish-blocking warning categories"  (the pattern table)
#   §"Truly advisory warnings"  (the safe-to-leave list)
#
# The doc's governing rule is FN-safe: "When in doubt, treat a WARNING as a
# blocker rather than advisory. The cost of a false positive (agent asks user)
# is much lower than the cost of a false negative (agent ships a broken
# plugin)." So a WARNING is a PUBLISH-BLOCKER by default; it is ADVISORY only
# when it matches an explicit advisory marker AND does not also match a
# publish-blocker marker. An UNKNOWN warning therefore stays BLOCKING — never
# marked advisory. This is exactly the discipline the task mandates.
# --------------------------------------------------------------------------

# Lowercase substrings that mark a WARNING as a PUBLISH-BLOCKER. Checked first,
# so if a message matches both a blocker and an advisory marker it stays
# blocking (the FN-safe direction).
_PUBLISH_BLOCKING_WARNING_MARKERS: frozenset[str] = frozenset(
    {
        # 1. missing CI infrastructure
        "ci workflow not found",
        "missing validate.yml",
        "validate.yml",
        "update-submodules.yml not found",
        "notify-marketplace.yml not found",
        # 2. missing publish pipeline files
        "no pre-push hook installed",
        "publish.py not executable",
        "chmod +x required",
        # 3. broken marketplace-integration plumbing
        "not on default branch",
        "marketplace_pat not configured",
        "missing repository secret",
        # 4. platform declared but unsupported
        "platform declares",
        # 5. version mismatch across manifests
        "version mismatch",
        "does not match plugin.json version",
        # 6. unsatisfiable / yanked dependency version
        "not satisfiable",
    }
)

# Lowercase substrings that mark a WARNING as TRULY ADVISORY (safe to leave).
_ADVISORY_WARNING_MARKERS: frozenset[str] = frozenset(
    {
        "--skip-platform-checks",
        "not natively available on windows",
        "language detection:",
        "consider pruning",  # "Lockfile <name> present — consider pruning"
        "optional metadata missing",
        "submodule advisory:",
        "orphan lockfile detected",
    }
)


def warning_is_blocking(message: str) -> bool:
    """Return True iff a WARNING-level finding blocks publish.

    Mirrors iterative-fix-loop.md exactly: a publish-blocker marker → blocking;
    otherwise an advisory marker → advisory; otherwise (unknown) → blocking
    (the FN-safe default — a finding is never silently downgraded to advisory).
    """
    text = (message or "").lower()
    if any(marker in text for marker in _PUBLISH_BLOCKING_WARNING_MARKERS):
        return True
    if any(marker in text for marker in _ADVISORY_WARNING_MARKERS):
        return False
    return True  # FN-safe: in doubt, treat a WARNING as a blocker.


def finding_is_blocking(level: str, message: str) -> bool:
    """Return True iff a finding (any non-INFO/PASSED level) blocks publish.

    CRITICAL/MAJOR/MINOR/NIT always block under ``--strict`` (mirrors
    ``cpv_fix_loop_state._BLOCKING_SEVERITIES``). WARNING is delegated to
    ``warning_is_blocking``. An unrecognized finding level is treated as
    blocking (FN-safe — a novel severity is never silently dropped).
    """
    lvl = (level or "").upper()
    if lvl == "WARNING":
        return warning_is_blocking(message)
    if lvl in _ALWAYS_BLOCKING_LEVELS:
        return True
    return True  # unknown finding level → FN-safe blocking


def _is_finding(level: str) -> bool:
    """True unless the level is INFO/PASSED (the non-actionable levels)."""
    return (level or "").upper() not in _NON_FINDING_LEVELS


def _extract_results(data: Any) -> list[Any]:
    """Locate the results list in a findings JSON.

    Accepts the standard wrapper ``{"results": [...]}`` (what
    ``remote_validation.py`` emits) or a bare list of result dicts. Anything
    else yields an empty list (an empty/zeroed ledger — never a crash).
    """
    if isinstance(data, dict):
        results = data.get("results")
        if isinstance(results, list):
            return results
        return []
    if isinstance(data, list):
        return data
    return []


def _coerce_line(raw: Any) -> int | None:
    """Normalize a line value to int | None (tolerating a stringified int)."""
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _sort_bucket(bucket: dict[str, list[dict[str, Any]]]) -> None:
    """Sort each file's entries by line ascending; line-less entries last."""
    for entries in bucket.values():
        entries.sort(key=lambda e: (e["line"] is None, e["line"] if e["line"] is not None else 0))


def build_ledger(findings_json: Any) -> dict[str, Any]:
    """Distill a findings JSON into the compact ledger structure.

    Returns ``{"summary": {...}, "mech": {file: [...]}, "intel": {file: [...]}}``.
    An empty / malformed input yields an empty ledger with a zeroed summary.
    """
    results = _extract_results(findings_json)

    mech: dict[str, list[dict[str, Any]]] = {}
    intel: dict[str, list[dict[str, Any]]] = {}
    counts: dict[str, int] = {level: 0 for level in _SUMMARY_LEVELS}
    total = 0
    mech_n = 0
    intel_n = 0
    blocking_n = 0

    for r in results:
        level = str(r.get("level") or "").upper()
        if not _is_finding(level):
            continue  # INFO / PASSED — not a finding

        message = str(r.get("message") or "")
        total += 1
        key = level.lower()
        if key in counts:
            counts[key] += 1

        blocking = finding_is_blocking(level, message)
        if blocking:
            blocking_n += 1

        file_bucket = r.get("file") or _NO_FILE
        line = _coerce_line(r.get("line"))
        category = str(r.get("category") or "")
        # Missing suggestion falls back to the message so every ledger entry is
        # self-contained (the agent never has to open the full report for it).
        suggestion = r.get("suggestion")
        if suggestion is None or suggestion == "":
            suggestion = message
        suggestion = str(suggestion)

        if r.get("fixable"):
            mech_n += 1
            mech.setdefault(file_bucket, []).append(
                {
                    "line": line,
                    "level": level,
                    "category": category,
                    "fix_id": r.get("fix_id"),
                    "suggestion": suggestion,
                }
            )
        else:
            intel_n += 1
            intel.setdefault(file_bucket, []).append(
                {
                    "line": line,
                    "level": level,
                    "category": category,
                    "blocking": blocking,
                    "suggestion": suggestion,
                }
            )

    _sort_bucket(mech)
    _sort_bucket(intel)

    summary = {
        "critical": counts["critical"],
        "major": counts["major"],
        "minor": counts["minor"],
        "nit": counts["nit"],
        "warning": counts["warning"],
        "total": total,
        "mech": mech_n,
        "intel": intel_n,
        "blocking": blocking_n,
    }
    return {"summary": summary, "mech": mech, "intel": intel}


def _truncate(text: str, limit: int = 100) -> str:
    """Flatten whitespace (incl. newlines) and cap at ``limit`` chars."""
    flat = " ".join(str(text).split())
    return flat[:limit] + "…" if len(flat) > limit else flat


def render_text(ledger: dict[str, Any]) -> str:
    """Render the compact, token-lean text view of a ledger.

    A one-line summary header, then a MECH section and an INTEL section, each
    grouping findings by file (``\\n<file> (<n>)`` header) with one line per
    finding: ``  L<line> <LEVEL> [<category>] <suggestion truncated ~100>``.
    INTEL warnings carry a ``BLOCKING`` / ``advisory`` marker (the one
    ambiguous case); the split itself is load-bearing — it tells the agent
    which findings it can auto-fix vs which need judgement.
    """
    s = ledger["summary"]
    lines: list[str] = [
        f"# fix-ledger  total={s['total']} mech={s['mech']} "
        f"intel={s['intel']} blocking={s['blocking']}  "
        f"C={s['critical']} MA={s['major']} MI={s['minor']} "
        f"N={s['nit']} W={s['warning']}"
    ]

    def emit_section(title: str, bucket: dict[str, list[dict[str, Any]]], is_intel: bool) -> None:
        n_find = sum(len(v) for v in bucket.values())
        lines.append("")
        lines.append(f"## {title}  ({n_find} in {len(bucket)} file(s))")
        if not bucket:
            lines.append("  (none)")
            return
        for file_bucket, entries in bucket.items():
            lines.append("")  # the '\n' before each file header
            lines.append(f"{file_bucket} ({len(entries)})")
            for e in entries:
                loc = f"L{e['line']}" if e["line"] is not None else "L?"
                marker = ""
                if is_intel and e["level"] == "WARNING":
                    marker = " BLOCKING" if e["blocking"] else " advisory"
                lines.append(
                    f"  {loc} {e['level']} [{e['category']}]{marker} {_truncate(e['suggestion'])}"
                )

    emit_section("MECH (auto-fixable)", ledger["mech"], is_intel=False)
    emit_section("INTEL (needs judgement)", ledger["intel"], is_intel=True)
    return "\n".join(lines) + "\n"


def _cmd_build(json_path: str, out_path: str, text_path: str | None) -> int:
    """Read findings JSON, write the ledger JSON (+ optional text view)."""
    with open(json_path, encoding="utf-8") as fh:
        data = json.load(fh)
    ledger = build_ledger(data)

    out_p = Path(out_path)
    out_p.parent.mkdir(parents=True, exist_ok=True)
    out_p.write_text(json.dumps(ledger, indent=2) + "\n", encoding="utf-8")

    if text_path:
        text_p = Path(text_path)
        text_p.parent.mkdir(parents=True, exist_ok=True)
        text_p.write_text(render_text(ledger), encoding="utf-8")

    s = ledger["summary"]
    print(
        f"[cpv-fix-ledger] total={s['total']} mech={s['mech']} "
        f"intel={s['intel']} blocking={s['blocking']} -> {out_p}"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Sub-command ``build`` transforms findings → ledger."""
    parser = argparse.ArgumentParser(
        prog="cpv_fix_ledger.py",
        description=(
            "Build a compact by-file findings LEDGER from a CPV "
            "`--strict --json` report so the fix loop can stop re-reading "
            "the full report every iteration."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build", help="Build a ledger from a findings JSON.")
    build.add_argument(
        "--json", required=True, metavar="FINDINGS.json", help="CPV --strict --json findings report."
    )
    build.add_argument("--out", required=True, metavar="LEDGER.json", help="Where to write the ledger JSON.")
    build.add_argument(
        "--text", metavar="LEDGER.txt", default=None, help="Optional compact text view of the ledger."
    )
    args = parser.parse_args(argv)

    # ``required=True`` on the subparsers guarantees ``build`` is the command.
    return _cmd_build(args.json, args.out, args.text)


if __name__ == "__main__":
    raise SystemExit(main())
