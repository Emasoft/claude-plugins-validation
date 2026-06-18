#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# ///
"""Deterministic oscillation / convergence detector for the plugin-fixer loop.

The fixer's ``validate → fix → re-validate`` loop must terminate on exactly two
conditions: **CONVERGED** (no blocking findings remain) or **CYCLE** (the
finding-set has recurred — the loop is oscillating because a fix is not landing
or two findings pull against each other). There is deliberately **no iteration
cap** (TRDD-933592ac / the ``no-hardcoded-iteration-caps`` rule): a 300-finding
plugin legitimately needs 20+ iterations.

Why this module exists (the bug it kills)
------------------------------------------
The historical guard compared only the **immediately-previous** iteration
(``signature(N) == signature(N-1)``). That misses every multi-step cycle. The
canonical failure is the TOC-embed catch-22 on a references-heavy skill:

* iter 1 — ``{TOC-MINOR × K}``           → fixer embeds the full TOCs
* iter 2 — ``{SIZE-MAJOR, TOC-NIT × K}`` → SKILL.md is now over the body cap, so
  the fixer shrinks it
* iter 3 — ``{TOC-MINOR × K}`` again     → back to iter 1's state → A,B,A,B,…

Consecutive iterations *always differ*, so the single-step guard **never fires**
→ the loop runs forever → the agent exhausts its context window and crashes,
leaving a half-applied (corrupt) tree. This is exactly the field report behind
TRDD-933592ac.

The fix
-------
Record **every** iteration's signature in a small JSON state file and detect a
repeat against **any** prior iteration. Two properties make this correct and
safe:

* **FN-safe (never falsely stops a progressing loop).** The signature is the
  *multiset* of ``(severity, file, message)`` over the current findings. Any
  real progress — a finding cleared, a count dropped, a message changed
  (``0/13`` → ``1/13``) — changes the multiset, so a genuinely advancing loop
  produces a *new* signature every iteration and is reported ``PROGRESS``.
* **Guaranteed to terminate (no magic number).** The finding space is finite,
  so a non-converging loop must eventually revisit a signature (pigeonhole) and
  is reported ``CYCLE``. For the motivating catch-22 that happens on iteration 3.

**The state file is the load-bearing part.** The failure mode is *context
exhaustion* — the agent's own working memory degrades across 20+ iterations and
it forgets earlier signatures. A file on disk does not forget. So termination no
longer depends on the agent reliably hand-tracking a growing set across a long,
degrading context.

CLI
---
    cpv_fix_loop_state.py reset   --state <state.json>
    cpv_fix_loop_state.py record  --state <state.json> --findings <report.json>
                                  [--include-warnings] [--label "<note>"]
    cpv_fix_loop_state.py summary --state <state.json>

``record`` prints one verdict line and sets the exit code:

* ``CONVERGED iterations=<N>``                          exit 0  → go to final verify
* ``PROGRESS  iterations=<N> findings=<C>``             exit 0  → fix a batch, loop
* ``CYCLE     iterations=<N> repeat_of=<M> findings=<C>`` exit 2 → STOP, return [BLOCKED]

``--findings`` accepts a CPV ``--json`` report (a dict with a ``findings`` list)
**or** a bare JSON list of finding objects. Per finding it reads the severity
from ``severity``/``sev``/``level``, the path from ``file``/``path``/
``location``, and the message from ``message``/``msg``/``text``/``title``. By
default only ``critical``/``major``/``minor``/``nit`` count toward the loop set
(``--include-warnings`` adds ``warning``); ``info``/``suppressed`` never count.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

# Severities that count toward the loop's "blocking" finding-set. WARNING is
# opt-in (the agent evaluates warnings separately); info / suppressed never
# count (a suppressed finding is invisible to the gate).
_BLOCKING_SEVERITIES = frozenset({"critical", "major", "minor", "nit"})
_WARNING_SEVERITY = "warning"

# Field-name fallbacks — CPV's several validators emit slightly different shapes.
_SEVERITY_KEYS = ("severity", "sev", "level")
_FILE_KEYS = ("file", "path", "location", "rel_file", "filename")
_MESSAGE_KEYS = ("message", "msg", "text", "title", "description")

# Unit separator — joins a finding's fields without colliding with any byte that
# occurs in a path or message, so two distinct findings can never alias.
_US = "\x1f"

_CYCLE_EXIT = 2
_ERROR_EXIT = 1


def _norm_severity(raw: object) -> str:
    """Lowercase a severity token; map a few known synonyms to the canonical set."""
    s = str(raw or "").strip().lower()
    # Some validators prefix with "[" or include a marker; keep only the word.
    s = s.strip("[]").strip()
    if s in {"warn", "warning"}:
        return _WARNING_SEVERITY
    return s


def _first_present(d: dict[str, Any], keys: tuple[str, ...]) -> str:
    for k in keys:
        if k in d and d[k] not in (None, ""):
            return str(d[k])
    return ""


def _iter_finding_dicts(payload: Any) -> list[dict[str, Any]]:
    """Extract the finding objects from a CPV report dict or a bare list.

    Tolerant of the several CPV JSON shapes: a top-level ``findings`` list, a
    top-level ``results``/``issues`` list, or the payload already being a list
    of finding dicts. Non-dict entries are ignored (never guessed at).
    """
    if isinstance(payload, list):
        return [f for f in payload if isinstance(f, dict)]
    if isinstance(payload, dict):
        for key in ("findings", "results", "issues"):
            val = payload.get(key)
            if isinstance(val, list):
                return [f for f in val if isinstance(f, dict)]
    return []


def select_findings(payload: Any, *, include_warnings: bool = False) -> list[dict[str, str]]:
    """Return the loop-relevant findings as normalized ``{severity,file,message}`` dicts.

    Only blocking severities count by default; ``include_warnings`` adds WARNING.
    ``info``/``suppressed``/unknown severities are excluded — they do not gate
    the loop, so including them would let a purely-suppressed report look like it
    is "changing" and mask a real cycle.
    """
    wanted = set(_BLOCKING_SEVERITIES)
    if include_warnings:
        wanted.add(_WARNING_SEVERITY)
    out: list[dict[str, str]] = []
    for f in _iter_finding_dicts(payload):
        sev = _norm_severity(_first_present(f, _SEVERITY_KEYS))
        if sev not in wanted:
            continue
        out.append(
            {
                "severity": sev,
                "file": _first_present(f, _FILE_KEYS),
                "message": _first_present(f, _MESSAGE_KEYS),
            }
        )
    return out


def compute_signature(findings: list[dict[str, str]]) -> str:
    """Stable sha256 over the *multiset* of ``(severity, file, message)`` findings.

    Multiset (not set): every finding contributes a row INCLUDING duplicates,
    then the rows are sorted, so clearing one of two identical-keyed findings
    changes the signature (one fewer row) — that is real progress and must not
    look like "no change". Sorting makes the signature order-independent (the
    validator may emit findings in any order). An empty finding-set hashes to the
    canonical empty signature, which ``record`` reports as CONVERGED before any
    cycle check.
    """
    rows = sorted(f"{f.get('severity', '')}{_US}{f.get('file', '')}{_US}{f.get('message', '')}" for f in findings)
    blob = "\n".join(rows)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _load_state(state_path: Path) -> dict[str, Any]:
    if not state_path.is_file():
        return {"iterations": []}
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        # A corrupt/half-written state file must not crash the loop: start fresh
        # rather than propagate (the loop re-derives history from this point).
        return {"iterations": []}
    if not isinstance(data, dict) or not isinstance(data.get("iterations"), list):
        return {"iterations": []}
    return data


def _atomic_write_json(path: Path, obj: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".cpv_loop_state.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(obj, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def record(
    state_path: Path,
    findings: list[dict[str, str]],
    *,
    label: str = "",
) -> tuple[str, int]:
    """Append one iteration to the state file and return ``(verdict_line, exit_code)``.

    Verdict precedence: CONVERGED (empty set) > CYCLE (signature already seen in a
    PRIOR iteration) > PROGRESS (a new signature). Convergence is checked before
    the cycle guard so the terminal clean state is never mislabeled a cycle.
    """
    state = _load_state(state_path)
    iterations: list[dict[str, Any]] = state["iterations"]
    n = len(iterations) + 1
    count = len(findings)
    sig = compute_signature(findings)

    if count == 0:
        iterations.append({"n": n, "signature": sig, "findings": 0, "label": label, "verdict": "CONVERGED"})
        _atomic_write_json(state_path, state)
        return (f"CONVERGED iterations={n}", 0)

    # Cycle = this exact finding multiset already appeared in any PRIOR iteration.
    prior_match = next((it for it in iterations if it.get("signature") == sig), None)
    if prior_match is not None:
        m = int(prior_match.get("n", 0))
        iterations.append(
            {"n": n, "signature": sig, "findings": count, "label": label, "verdict": "CYCLE", "repeat_of": m}
        )
        _atomic_write_json(state_path, state)
        return (f"CYCLE iterations={n} repeat_of={m} findings={count}", _CYCLE_EXIT)

    iterations.append({"n": n, "signature": sig, "findings": count, "label": label, "verdict": "PROGRESS"})
    _atomic_write_json(state_path, state)
    return (f"PROGRESS iterations={n} findings={count}", 0)


def _cmd_reset(args: argparse.Namespace) -> int:
    state_path = Path(args.state)
    if state_path.exists():
        state_path.unlink()
    _atomic_write_json(state_path, {"iterations": []})
    print(f"RESET {state_path}")
    return 0


def _cmd_record(args: argparse.Namespace) -> int:
    findings_path = Path(args.findings)
    try:
        payload = json.loads(findings_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        print(f"ERROR could not read findings JSON {findings_path}: {exc}", file=sys.stderr)
        return _ERROR_EXIT
    findings = select_findings(payload, include_warnings=args.include_warnings)
    verdict, code = record(Path(args.state), findings, label=args.label or "")
    print(verdict)
    return code


def _cmd_summary(args: argparse.Namespace) -> int:
    state = _load_state(Path(args.state))
    iterations: list[dict[str, Any]] = state.get("iterations", [])
    if not iterations:
        print("(no iterations recorded)")
        return 0
    print(f"{'iter':>4}  {'verdict':<9}  {'findings':>8}  {'sig':<12}  label")
    for it in iterations:
        sig = str(it.get("signature", ""))[:12]
        extra = f" (repeat_of={it['repeat_of']})" if it.get("repeat_of") else ""
        print(
            f"{it.get('n', '?'):>4}  {str(it.get('verdict', '')):<9}  "
            f"{it.get('findings', '?'):>8}  {sig:<12}  {it.get('label', '')}{extra}"
        )
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="cpv_fix_loop_state.py",
        description="Deterministic oscillation/convergence detector for the plugin-fixer loop.",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("reset", help="Clear the loop-state file (start of a fresh fix loop).")
    r.add_argument("--state", required=True, help="Path to the loop-state JSON file.")
    r.set_defaults(func=_cmd_reset)

    rec = sub.add_parser("record", help="Record one iteration's findings; print verdict.")
    rec.add_argument("--state", required=True, help="Path to the loop-state JSON file.")
    rec.add_argument("--findings", required=True, help="Path to a CPV --json report or a bare findings list.")
    rec.add_argument(
        "--include-warnings",
        action="store_true",
        help="Count WARNING findings toward the loop set (default: blocking severities only).",
    )
    rec.add_argument("--label", default="", help="Optional human note recorded with this iteration.")
    rec.set_defaults(func=_cmd_record)

    s = sub.add_parser("summary", help="Print the recorded iteration history.")
    s.add_argument("--state", required=True, help="Path to the loop-state JSON file.")
    s.set_defaults(func=_cmd_summary)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    func = args.func
    result: int = func(args)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
