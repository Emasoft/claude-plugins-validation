#!/usr/bin/env python3
"""Context-aware false-positive classifier for CPV security rules.

Implements Step 1 + Step 2 + Step 4 of TRDD-fe006962-f3ec-48e8-bb2b-849ec7109d2c.

Background — every CPV security rule today is a pattern-match plus a
pile of binary skip-conditions (in-comment skip, in-test-path skip,
lockfile skip, …). v2.40.x reduced the false-positive rate to ~0% on
seven emasoft-plugins, but at the cost of suppressing the SAME RULE
in contexts where it would still be a true positive. RC-21 (bulk
env-var harvest) is the canonical example: `os.environ.copy()` for
subprocess prep is benign, but the same call piped to a remote write
is exactly the exfil signal we want to keep.

This module provides the shared infrastructure that lets every rule
opt into a richer signal — `REAL`, `LIKELY_FP`, `DEFINITE_FP` — without
restructuring its existing scan loop. The default behaviour is opt-in
via the `--with-classifier` flag wired into `validate_security.py`
(off by default until the corpus regression suite is in place).

Module layout:

* `FindingVerdict` — the four-tier enum used by every classifier
* `Context` — read-only bundle handed to a classifier
* `Classifier` — protocol every per-rule function must satisfy
* `classify_rule` — dispatch helper that picks the right classifier
* `apply_verdict` — translates a verdict to a `ValidationReport` call
* `Registry` — `RULE_CLASSIFIERS: dict[str, Classifier]` populated by
  `register_classifier` decorators in callers

Step 3 (corpus) lives under `tests/fixtures/fp_corpus/<rule_id>.md`.
A separate harness script runs each classifier against its corpus and
prints precision/recall per rule.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Protocol


class FindingVerdict(Enum):
    """Verdict tier returned by every per-rule classifier.

    The four tiers form a ladder from `DEFINITE_FP` (suppress entirely)
    through `LIKELY_FP` (demote one severity) to `REAL` (report at
    declared severity) and `DEFINITE_TP` (optional escalation, requires
    an explicit `--extreme` flag at the validator). The middle tiers
    are the load-bearing ones — they let CPV say "I'm 70% sure this
    matters" instead of forcing every match into a binary report-or-skip
    decision.
    """

    DEFINITE_FP = "definite_fp"
    LIKELY_FP = "likely_fp"
    REAL = "real"
    DEFINITE_TP = "definite_tp"


@dataclass(frozen=True)
class Context:
    """Read-only context handed to every classifier.

    Built once per match by the scan loop. Carries everything a
    classifier could plausibly inspect without re-reading the file:
    the matched substring, the surrounding lines, the file's role
    classification (test / doc / fixture / source), and a small
    plugin-meta dict so domain rules (RC-22 clipboard exemption) can
    reason about the plugin's declared purpose.

    Fields use frozen dataclasses so a classifier can never mutate
    state that another classifier observed.
    """

    rule_id: str
    matched_text: str
    line_number: int
    line: str
    surrounding_lines: tuple[str, ...]
    file_role: str  # "source" | "test" | "doc" | "fixture" | "sample"
    file_path: str
    plugin_meta: dict[str, object] = field(default_factory=dict)


class Classifier(Protocol):
    """Protocol every per-rule classifier function must implement.

    A classifier is a pure function from `Context` to `FindingVerdict`.
    Pure means: no I/O (the scan loop already pre-loaded the file);
    no global mutation; no exceptions for control flow. Heuristic
    classifiers should default to `REAL` on uncertainty so a buggy
    classifier can never cause a true positive to silently disappear.
    """

    def __call__(self, ctx: Context) -> FindingVerdict: ...


# Registry — populated by the `register_classifier` decorator from
# rule-specific modules. Iteration order doesn't matter; lookup is
# always by rule_id.
RULE_CLASSIFIERS: dict[str, Classifier] = {}


def register_classifier(rule_id: str) -> Callable[[Classifier], Classifier]:
    """Decorator that registers a classifier for `rule_id`.

    Conflicts are flagged loudly: registering two classifiers for the
    same rule is almost always a copy-paste bug. The decorator returns
    the original function unchanged so callers can still test it
    directly.
    """

    def _wrap(fn: Classifier) -> Classifier:
        if rule_id in RULE_CLASSIFIERS:
            existing = RULE_CLASSIFIERS[rule_id]
            existing_name = getattr(existing, "__name__", repr(existing))
            raise ValueError(f"Duplicate classifier registration for {rule_id}: already bound to {existing_name}")
        RULE_CLASSIFIERS[rule_id] = fn
        return fn

    return _wrap


def classify_rule(rule_id: str, ctx: Context) -> FindingVerdict:
    """Dispatch helper — return the verdict for `rule_id` given `ctx`.

    If no classifier is registered for `rule_id`, returns `REAL` so the
    legacy single-shot pattern → severity behaviour is preserved.
    Treating "no classifier" as "fully trust the regex" keeps the
    rollout incremental: rules opt in one at a time without forcing a
    big-bang refactor.
    """

    classifier = RULE_CLASSIFIERS.get(rule_id)
    if classifier is None:
        return FindingVerdict.REAL
    return classifier(ctx)


# Severity demotion ladder — see TRDD §Step 4. The classifier output
# is translated to one of these actions by `apply_verdict`. The
# escalation tier (DEFINITE_TP → severity bump) is gated behind a
# caller-controlled flag because moving CRITICAL → CRITICAL+ would
# require new output formatting; v1 keeps it as a no-op.
_SEVERITY_DEMOTION_ORDER = ("critical", "major", "minor", "nit", "info", "passed")


def demote_severity(severity: str, *, steps: int = 1) -> str:
    """Return the severity name `steps` tiers below `severity`.

    Bottom-clamps at `passed` so DEFINITE_FP-on-info doesn't generate
    a non-existent severity. Mirrors the demotion table in CPV's
    `effective_severity` so the two paths agree on tier order.
    """

    if severity not in _SEVERITY_DEMOTION_ORDER:
        return severity
    idx = _SEVERITY_DEMOTION_ORDER.index(severity)
    new_idx = min(idx + steps, len(_SEVERITY_DEMOTION_ORDER) - 1)
    return _SEVERITY_DEMOTION_ORDER[new_idx]


@dataclass(frozen=True)
class VerdictAction:
    """Outcome of applying a verdict to a declared severity.

    `report_severity is None` means "do not report". Callers reading
    this value should branch on `None` rather than introspecting
    `verdict` so the demotion ladder logic stays in one place.
    """

    verdict: FindingVerdict
    report_severity: str | None
    note: str  # Short human-readable rationale for the demotion / suppression.


def apply_verdict(
    verdict: FindingVerdict,
    declared_severity: str,
    *,
    allow_escalation: bool = False,
) -> VerdictAction:
    """Translate `verdict` + `declared_severity` into a `VerdictAction`.

    `allow_escalation=True` lets `DEFINITE_TP` bump the severity one
    tier (CRITICAL stays CRITICAL since there's nothing higher).
    Default is False so the safe rollout never inflates findings.
    """

    severity = declared_severity.lower()

    if verdict is FindingVerdict.DEFINITE_FP:
        return VerdictAction(verdict, None, "definite false positive — suppressed")

    if verdict is FindingVerdict.LIKELY_FP:
        demoted = demote_severity(severity, steps=1)
        return VerdictAction(verdict, demoted, f"likely FP — demoted {severity} → {demoted}")

    if verdict is FindingVerdict.DEFINITE_TP and allow_escalation:
        idx = _SEVERITY_DEMOTION_ORDER.index(severity) if severity in _SEVERITY_DEMOTION_ORDER else 0
        new_idx = max(idx - 1, 0)
        promoted = _SEVERITY_DEMOTION_ORDER[new_idx]
        return VerdictAction(verdict, promoted, f"definite TP — promoted {severity} → {promoted}")

    return VerdictAction(verdict, severity, "real finding — declared severity")


# -----------------------------------------------------------------------------
# Helpers used by per-rule classifiers — kept here so each rule module stays
# focused on its own decision tree.
# -----------------------------------------------------------------------------


def file_role_of(rel_path: str) -> str:
    """Classify the file's role from its path.

    Heuristic — uses path segments only. CPV already has the
    authoritative `is_test_path` / `is_doc_path` / `is_sample_file`
    helpers in `cpv_validation_common.py`; the role string keeps the
    classifier independent of the rest of CPV at module import time.
    Callers in the scan loop should override this with the canonical
    helpers when available.
    """

    rel = rel_path.replace("\\", "/").lower()
    if "/tests/fixtures/" in rel or rel.startswith("tests/fixtures/"):
        return "fixture"
    basename = rel.rsplit("/", 1)[-1]
    if (
        "/tests/" in rel
        or rel.startswith("tests/")
        or "/test_" in rel
        or basename.startswith("test_")
        or "test_" in basename  # catches `__test_helper.py`, `_test_x.py`, etc.
        or basename.endswith("_test.py")
        or basename.endswith(".test.ts")
        or basename.endswith(".test.tsx")
        or basename.endswith(".test.js")
        or basename.endswith(".spec.ts")
        or basename.endswith(".spec.js")
    ):
        return "test"
    if "/docs/" in rel or rel.startswith("docs/") or rel.endswith((".md", ".mdx", ".rst", ".txt", ".markdown")):
        return "doc"
    if (
        rel.endswith((".example", ".sample", ".template"))
        or "/examples/" in rel
        or rel.startswith("examples/")
        or "/samples/" in rel
        or rel.startswith("samples/")
    ):
        return "sample"
    return "source"


def has_sink_nearby(
    surrounding_lines: tuple[str, ...] | list[str],
    sink_hints: tuple[str, ...],
) -> bool:
    """Return True if any surrounding line contains a sink-API hint.

    Used by classifiers that need a "is the matched value used?" check.
    Pure substring match — case-sensitive, no regex compilation cost
    per call. Sink lists are kept short on purpose: a too-broad list
    starts re-introducing the FPs we just removed.
    """

    return any(hint in line for line in surrounding_lines for hint in sink_hints)


__all__ = [
    "Classifier",
    "Context",
    "FindingVerdict",
    "RULE_CLASSIFIERS",
    "VerdictAction",
    "apply_verdict",
    "classify_rule",
    "demote_severity",
    "file_role_of",
    "has_sink_nearby",
    "register_classifier",
]
