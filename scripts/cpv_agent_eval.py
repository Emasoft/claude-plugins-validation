#!/usr/bin/env python3
"""Selectively compare an agent against its converted variants (spec §6, TRDD-I5X0TY2F).

The three architectures of spec §1.1 trade in OPPOSITE directions — ALL-IN-ONE pays
one large cache creation for turn-1 readiness, ONE-FOR-ALL pays more turns for a
near-empty context per node, PLUGIN-OMNI pays a menu lookup to keep the prefix tiny —
so which one wins is an empirical question about a SPECIFIC agent. This module answers
it for any SUBSET of ``original,all-in-one,one-for-all,plugin-omni``.

TWO TIERS, KEPT STRICTLY APART. Every number carries the tier that produced it,
because **a static estimate presented as a measured result is the one failure mode
that makes the whole tool worthless**:

* **Tier 1 — static cost model.** ALWAYS runs, ZERO LLM calls, real measurements over
  real files: the cached-prefix estimate (the agent body PLUS the FULL content of every
  preloaded skill — ``skills:`` frontmatter injects that content into every invocation,
  so it IS a prefix cost), the per-invocation injected tokens, the tool-schema surface,
  the closure size in files and bytes, turn-1 readiness, and the projected cost of N
  turns under the prefix-cache read rate. Deterministic, so this is the tier the tests
  assert.
* **Tier 2 — live A/B/C.** OPT-IN via ``--live``, never implied. No mocks, no simulated
  numbers, no fabricated comparison: if it cannot run it reports UNKNOWN and exits
  non-zero, and a missing task file is an error rather than an empty pass.

THE ECOSYSTEM EVAL SCHEMA IS ADOPTED, NOT REINVENTED (``evaluating-skills.md``). Only
the configuration names change (``with_skill``/``without_skill`` → ``original`` /
``all-in-one`` / ``one-for-all`` / ``plugin-omni``):

* input ``evals/evals.json`` — ``{skill_name, evals: [{id, prompt, expected_output, files}]}``;
* per run ``timing.json`` — ``{total_tokens, duration_ms}``;
* aggregate ``benchmark.json`` — ``{run_summary: {<config>: {pass_rate, time_seconds,
  tokens}}, delta: {...}}``, each metric ``{mean, stddev}``.

Three details of that schema are load-bearing and are honoured here:

1. **Each run starts from a CLEAN context** (a fresh subagent per run). A shared context
   makes the comparison meaningless, so the DRIVER (the skill) dispatches one fresh
   subagent per run; this script never reuses one.
2. **``total_tokens`` / ``duration_ms`` come from the task-completion notification and
   are "not persisted anywhere else"** — so the driver captures them the instant a run
   finishes, into ``<runs-dir>/<config>/<eval-id>[/<run-id>]/timing.json``. A run whose
   timing was lost is UNKNOWN here, never 0.
3. **``stddev`` is meaningless on single runs**, so it is OMITTED rather than emitted as
   a fake 0; it appears only once an eval has been repeated.

**THE DELTA IS THE DELIVERABLE.** It states what a variant COSTS (time, tokens) and what
it BUYS (pass rate). This tool reports it and STOPS: it ranks nothing and prints no
verdict, because "higher pass rate for more tokens" is a trade-off for the human.

Why the live tier ingests captured runs instead of dispatching them itself: a Python
process cannot spawn a Claude Code subagent, and the honest alternative to "cannot
dispatch" is NOT a simulation — it is to make the driver capture real numbers and to
report UNKNOWN when it did not. "Cannot check" is never reported as clean.

CPV is UNIVERSAL: nothing here depends on an install slug, a marketplace, or a cache
path — a pre-publish source has none of those.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from convert_agent import read_markdown_parts  # noqa: E402
from cpv_agent_closure import (  # noqa: E402
    DEFAULT_MAX_DEPTH,
    AgentClosure,
    closure_files,
    resolve_agent_closure,
)
from cpv_token_estimate import estimate_tokens  # noqa: E402
from cpv_validation_common import EXIT_OK  # noqa: E402

# The canonical variant vocabulary (spec §1.1). These names are the project's
# vocabulary — they appear verbatim in code, findings, skills, menus and docs.
VARIANT_NAMES: tuple[str, ...] = ("original", "all-in-one", "one-for-all", "plugin-omni")
BASELINE_VARIANT = "original"

TIER1 = "tier1-static"
TIER2 = "tier2-live"

# Prefix-cache pricing multipliers. Turn 1 CREATES the cached prefix (write rate);
# every later turn RE-READS it (read rate). Expressed as multipliers so the projection
# is a rate-weighted token figure, not a currency amount CPV would have to keep current.
CACHE_WRITE_RATE = 1.25
CACHE_READ_RATE = 0.1

DEFAULT_TURN_PROJECTIONS: tuple[int, ...] = (1, 10, 50, 100)
DEFAULT_TASKS_PATH = "evals/evals.json"

EXIT_ERROR = 1
EXIT_LIVE_UNKNOWN = 2

_NOT_EVALUATED = "NOT-EVALUATED"


class EvalInputError(Exception):
    """A caller-supplied input is unusable. Raised instead of degrading to an empty
    pass — a comparison built on nothing is worse than no comparison."""


# ---------------------------------------------------------------------------
# Variant selection
# ---------------------------------------------------------------------------
def parse_variants(raw: str) -> list[str]:
    """Parse a ``--variants`` CSV into an ordered, de-duplicated selection.

    An unknown name raises rather than being dropped: silently ignoring
    ``--variants original,mono`` would produce a table that answers a question the
    caller did not ask.
    """
    names = [part.strip() for part in raw.split(",")]
    selected: list[str] = []
    for name in names:
        if not name:
            continue
        if name not in VARIANT_NAMES:
            raise EvalInputError(f"unknown variant {name!r} — pick from {', '.join(VARIANT_NAMES)}")
        if name not in selected:
            selected.append(name)
    if not selected:
        raise EvalInputError(f"--variants selected nothing — pick from {', '.join(VARIANT_NAMES)}")
    return selected


# ---------------------------------------------------------------------------
# Tier 1 — the static cost model
# ---------------------------------------------------------------------------
def token_count(text: str) -> int:
    """Conservative offline token count for ``text``.

    Deliberately calls :func:`estimate_tokens` WITHOUT ``allow_api`` so Tier 1 makes
    zero network calls and stays deterministic — the property the test suite asserts.
    """
    return estimate_tokens(text).tokens


def project_turn_cost(prefix_tokens: int, turns: int) -> float:
    """Rate-weighted token cost of ``turns`` turns carrying ``prefix_tokens`` of prefix.

    Turn 1 pays the cache-WRITE rate (it creates the entry); each later turn pays the
    cache-READ rate on the same prefix. A non-positive ``turns`` is 0 — never negative.
    """
    if turns <= 0:
        return 0.0
    return round(prefix_tokens * CACHE_WRITE_RATE + prefix_tokens * CACHE_READ_RATE * (turns - 1), 2)


@dataclass(frozen=True)
class StaticProfile:
    """One variant's Tier-1 measurements (or its NOT-EVALUATED reason)."""

    variant: str
    evaluated: bool
    tier: str = TIER1
    reason: str | None = None
    agent_path: str | None = None
    agent_body_tokens: int = 0
    preload_tokens: int = 0
    cached_prefix_tokens: int = 0
    per_invocation_injected_tokens: int = 0
    tool_schema_surface: int | None = None
    tools_inherited: bool = False
    preloaded_skills: tuple[str, ...] = ()
    runtime_skills: tuple[str, ...] = ()
    closure_file_count: int = 0
    closure_bytes: int = 0
    turn1_ready: bool = False
    projected_turn_cost: Mapping[str, float] = field(default_factory=dict)
    # Preloads whose prefix cost could NOT be priced (unresolvable, or resolved but
    # unreadable). Each was counted as 0 tokens, which UNDERSTATES this variant's real
    # cost — so a delta against a variant whose preloads all priced is not a like-for-like
    # comparison. Tracked as a count so the report can say so instead of leaving a reader
    # to infer it from the notes table.
    unpriced_preloads: int = 0
    notes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "variant": self.variant,
            "evaluated": self.evaluated,
            "tier": self.tier,
            "reason": self.reason,
            "agent_path": self.agent_path,
            "agent_body_tokens": self.agent_body_tokens,
            "preload_tokens": self.preload_tokens,
            "cached_prefix_tokens": self.cached_prefix_tokens,
            "per_invocation_injected_tokens": self.per_invocation_injected_tokens,
            "tool_schema_surface": self.tool_schema_surface,
            "tools_inherited": self.tools_inherited,
            "preloaded_skills": list(self.preloaded_skills),
            "runtime_skills": list(self.runtime_skills),
            "closure_file_count": self.closure_file_count,
            "closure_bytes": self.closure_bytes,
            "turn1_ready": self.turn1_ready,
            "projected_turn_cost": dict(self.projected_turn_cost),
            "unpriced_preloads": self.unpriced_preloads,
            "notes": list(self.notes),
        }


# The numeric Tier-1 fields a delta is computed over. Kept explicit so a new field
# cannot silently start (or stop) appearing in the delta table.
_DELTA_FIELDS: tuple[str, ...] = (
    "agent_body_tokens",
    "preload_tokens",
    "cached_prefix_tokens",
    "per_invocation_injected_tokens",
    "closure_file_count",
    "closure_bytes",
)


def _closure_size(closure: AgentClosure) -> tuple[int, int]:
    """``(file count, byte count)`` of every file a REACHABLE skill of the closure ships.

    An unreadable file contributes 0 bytes but is still counted — dropping it would
    under-report the closure and make a variant look leaner than it is.
    """
    total = 0
    files = closure_files(closure)
    for path in files:
        try:
            total += path.stat().st_size
        except OSError:
            continue
    return len(files), total


def profile_variant(
    variant: str,
    agent_path: Path | None,
    *,
    roots: Sequence[Path] | None = None,
    max_depth: int = DEFAULT_MAX_DEPTH,
    turns: Sequence[int] = DEFAULT_TURN_PROJECTIONS,
) -> StaticProfile:
    """Measure one variant. A variant with no file is NOT-EVALUATED, never dropped."""
    if variant not in VARIANT_NAMES:
        raise EvalInputError(f"unknown variant {variant!r} — pick from {', '.join(VARIANT_NAMES)}")
    if agent_path is None:
        return StaticProfile(
            variant=variant,
            evaluated=False,
            reason=f"no --{variant} file supplied",
        )
    if not agent_path.is_file():
        return StaticProfile(
            variant=variant,
            evaluated=False,
            reason=f"{agent_path} is not a file",
            agent_path=str(agent_path),
        )
    parts = read_markdown_parts(agent_path)
    if parts is None:
        return StaticProfile(
            variant=variant,
            evaluated=False,
            reason=f"{agent_path} has no readable YAML frontmatter",
            agent_path=str(agent_path),
        )
    frontmatter, body = parts

    closure = resolve_agent_closure(agent_path, roots=roots, max_depth=max_depth)

    notes: list[str] = []
    preloaded: list[str] = []
    runtime: list[str] = []
    preload_tokens = 0
    unpriced_preloads = 0
    counted: set[str] = set()
    for ref in closure.refs:
        if ref.origin == "preload":
            if ref.name not in preloaded:
                preloaded.append(ref.name)
            if ref.resolved_path is None:
                unpriced_preloads += 1
                notes.append(
                    f"preload '{ref.name}' does not resolve in any skill root — counted as 0 prefix "
                    "tokens, and it is a silent no-op at dispatch (validate_agent AC1 covers it)"
                )
                continue
            if ref.name in counted:
                continue
            counted.add(ref.name)
            try:
                text = Path(ref.resolved_path).read_text(encoding="utf-8", errors="replace")
            except OSError:
                unpriced_preloads += 1
                notes.append(f"preload '{ref.name}' resolved to an unreadable file — counted as 0 prefix tokens")
                continue
            # The FULL SKILL.md content is what `skills:` injects into every
            # invocation, so the whole file — frontmatter included — is prefix cost.
            preload_tokens += token_count(text)
        elif ref.reachable and ref.name not in runtime:
            runtime.append(ref.name)

    body_tokens = token_count(body)
    prefix_tokens = body_tokens + preload_tokens
    file_count, byte_count = _closure_size(closure)

    tools = closure.tools_declared
    if tools is None:
        notes.append("no `tools:` field — the agent inherits every session tool, so the schema surface is the session's")
    if not closure.can_load_at_runtime:
        notes.append(
            "the `Skill` tool gate is SHUT, so every runtime invocation in this body is DEAD and the "
            "`skills:` preload is the agent's only skill access (spec §1)"
        )

    # Turn-1 readiness: is every skill the agent can reach already in the prefix? A
    # reachable runtime/transitive ref is content that arrives only after a Skill call,
    # so the agent is NOT ready at turn 1. No refs at all is trivially ready.
    turn1_ready = not runtime
    if not closure.refs:
        notes.append("this agent reaches no skills at all — turn-1 readiness is trivially true")

    return StaticProfile(
        variant=variant,
        evaluated=True,
        reason=None,
        agent_path=str(agent_path),
        agent_body_tokens=body_tokens,
        preload_tokens=preload_tokens,
        cached_prefix_tokens=prefix_tokens,
        # The preload portion IS the per-invocation injected cost: it is what the
        # `skills:` list adds to every invocation over and above the agent body, and
        # what would vanish if the same skill were reached at runtime instead.
        per_invocation_injected_tokens=preload_tokens,
        tool_schema_surface=None if tools is None else len(tools),
        tools_inherited=tools is None,
        preloaded_skills=tuple(preloaded),
        runtime_skills=tuple(runtime),
        closure_file_count=file_count,
        closure_bytes=byte_count,
        turn1_ready=turn1_ready,
        projected_turn_cost={str(n): project_turn_cost(prefix_tokens, n) for n in turns},
        unpriced_preloads=unpriced_preloads,
        notes=tuple(notes),
    )


def static_delta(profiles: Sequence[StaticProfile]) -> dict[str, dict[str, float]]:
    """Tier-1 delta of every evaluated variant against the evaluated ``original``.

    A NOT-EVALUATED variant (or a missing baseline) yields no delta — subtracting from
    a number nobody measured would be a fabricated figure.
    """
    by_name = {p.variant: p for p in profiles}
    baseline = by_name.get(BASELINE_VARIANT)
    if baseline is None or not baseline.evaluated:
        return {}
    out: dict[str, dict[str, float]] = {}
    for profile in profiles:
        if profile.variant == BASELINE_VARIANT or not profile.evaluated:
            continue
        row: dict[str, float] = {}
        for name in _DELTA_FIELDS:
            row[name] = float(getattr(profile, name) - getattr(baseline, name))
        for turn_key, value in profile.projected_turn_cost.items():
            base = baseline.projected_turn_cost.get(turn_key)
            if base is not None:
                row[f"projected_turn_cost_{turn_key}"] = round(value - base, 2)
        out[profile.variant] = row
    return out


@dataclass(frozen=True)
class StaticTierResult:
    """Tier 1 for the whole selection."""

    profiles: tuple[StaticProfile, ...]
    turns: tuple[int, ...]
    tier: str = TIER1

    def to_dict(self) -> dict[str, Any]:
        return {
            "tier": self.tier,
            "turns": list(self.turns),
            "profiles": [p.to_dict() for p in self.profiles],
            "delta": static_delta(self.profiles),
        }


def run_static_tier(
    selection: Mapping[str, Path | None],
    variants: Sequence[str],
    *,
    roots: Sequence[Path] | None = None,
    max_depth: int = DEFAULT_MAX_DEPTH,
    turns: Sequence[int] = DEFAULT_TURN_PROJECTIONS,
) -> StaticTierResult:
    """Profile every selected variant. Zero LLM calls; every figure is Tier 1."""
    profiles = [
        profile_variant(variant, selection.get(variant), roots=roots, max_depth=max_depth, turns=turns)
        for variant in variants
    ]
    return StaticTierResult(profiles=tuple(profiles), turns=tuple(turns))


# ---------------------------------------------------------------------------
# Tier 2 — the live A/B/C, on the ecosystem schema
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class EvalTask:
    """One eval case, verbatim from ``evals/evals.json``."""

    id: str
    prompt: str
    expected_output: str | None
    files: tuple[str, ...]


@dataclass(frozen=True)
class EvalSuite:
    """The task set: ``{skill_name, evals: [...]}``."""

    skill_name: str
    tasks: tuple[EvalTask, ...]
    path: str | None = None

    @property
    def ids(self) -> tuple[str, ...]:
        return tuple(task.id for task in self.tasks)


def load_task_suite(path: Path) -> EvalSuite:
    """Load ``evals/evals.json``. Every failure raises: a missing or malformed task
    file is an ERROR, never an empty pass."""
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise EvalInputError(f"task file {path} could not be read: {exc}") from exc
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise EvalInputError(f"task file {path} is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise EvalInputError(f"task file {path} must hold a JSON object")
    evals = payload.get("evals")
    if not isinstance(evals, list) or not evals:
        raise EvalInputError(f"task file {path} has no non-empty 'evals' list — zero cases cannot be a comparison")
    tasks: list[EvalTask] = []
    seen: set[str] = set()
    for index, entry in enumerate(evals):
        if not isinstance(entry, dict):
            raise EvalInputError(f"task file {path}: evals[{index}] must be an object")
        task_id = entry.get("id")
        if not isinstance(task_id, str) or not task_id.strip():
            raise EvalInputError(f"task file {path}: evals[{index}] has no 'id' — a run could not be matched to it")
        prompt = entry.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            raise EvalInputError(f"task file {path}: eval '{task_id}' has no 'prompt' — it cannot be dispatched")
        if task_id in seen:
            raise EvalInputError(f"task file {path}: duplicate eval id '{task_id}'")
        seen.add(task_id)
        expected = entry.get("expected_output")
        files = entry.get("files")
        tasks.append(
            EvalTask(
                id=task_id,
                prompt=prompt,
                expected_output=expected if isinstance(expected, str) else None,
                files=tuple(str(f) for f in files) if isinstance(files, list) else (),
            )
        )
    skill_name = payload.get("skill_name")
    return EvalSuite(
        skill_name=skill_name if isinstance(skill_name, str) else "",
        tasks=tuple(tasks),
        path=str(path),
    )


@dataclass(frozen=True)
class RunTiming:
    """One run's captured numbers. ``None`` means the value was LOST, not zero."""

    config: str
    eval_id: str
    run_id: str
    total_tokens: int | None
    duration_ms: int | None
    passed: bool | None
    source: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "config": self.config,
            "eval_id": self.eval_id,
            "run_id": self.run_id,
            "total_tokens": self.total_tokens,
            "duration_ms": self.duration_ms,
            "passed": self.passed,
            "source": self.source,
        }


def _read_json_object(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _as_int(value: Any) -> int | None:
    """Coerce a captured number, or ``None``. A non-number is a LOST value: reading it
    as 0 would turn a lost measurement into a flattering one."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return int(round(value))
    return None


def load_run_timings(runs_dir: Path, configs: Sequence[str]) -> list[RunTiming]:
    """Collect every captured run under ``<runs-dir>/<config>/<eval-id>[/<run-id>]/timing.json``.

    Only the SELECTED configs are read, so a stale run directory for an unselected
    variant can never leak into the comparison. ``passed`` is taken from ``timing.json``
    or a sibling ``result.json``; when neither carries it the run is UNKNOWN.
    """
    timings: list[RunTiming] = []
    for config in configs:
        config_dir = runs_dir / config
        if not config_dir.is_dir():
            continue
        for timing_file in sorted(config_dir.rglob("timing.json")):
            rel = timing_file.parent.relative_to(config_dir).parts
            if not rel:
                continue
            eval_id = rel[0]
            run_id = "/".join(rel[1:])
            payload = _read_json_object(timing_file) or {}
            passed = payload.get("passed")
            if not isinstance(passed, bool):
                sibling = _read_json_object(timing_file.parent / "result.json") or {}
                passed = sibling.get("passed")
                if not isinstance(passed, bool):
                    passed = None
            timings.append(
                RunTiming(
                    config=config,
                    eval_id=eval_id,
                    run_id=run_id,
                    total_tokens=_as_int(payload.get("total_tokens")),
                    duration_ms=_as_int(payload.get("duration_ms")),
                    passed=passed,
                    source=str(timing_file),
                )
            )
    return timings


def _mean(values: Sequence[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _stddev(values: Sequence[float]) -> float | None:
    """Sample standard deviation, or ``None`` when fewer than two values exist."""
    if len(values) < 2:
        return None
    mean = sum(values) / len(values)
    variance = sum((v - mean) ** 2 for v in values) / (len(values) - 1)
    return math.sqrt(variance)


def _metric(values: Sequence[float], *, repeats: bool) -> dict[str, float | None]:
    """One ``{mean[, stddev]}`` metric.

    ``stddev`` appears ONLY when an eval was repeated — with a single run per eval the
    spread is meaningless, and emitting 0 would claim a precision the data lacks.
    """
    out: dict[str, float | None] = {"mean": _mean(values)}
    if repeats:
        spread = _stddev(values)
        if spread is not None:
            out["stddev"] = spread
    return out


@dataclass(frozen=True)
class LiveTierResult:
    """Tier 2 for the whole selection, in the adopted ecosystem shape."""

    run_summary: Mapping[str, Mapping[str, Mapping[str, float | None]]]
    delta: Mapping[str, Mapping[str, float | None]]
    unknown: tuple[str, ...]
    detail: Mapping[str, tuple[str, ...]]
    runs: tuple[RunTiming, ...]
    tasks_path: str | None
    runs_dir: str | None
    tier: str = TIER2

    @property
    def status(self) -> str:
        """``OK`` only when every selected config was fully measured; else ``UNKNOWN``."""
        return "UNKNOWN" if self.unknown else "OK"

    def to_dict(self) -> dict[str, Any]:
        return {
            "tier": self.tier,
            "status": self.status,
            "tasks_path": self.tasks_path,
            "runs_dir": self.runs_dir,
            "run_summary": {k: {m: dict(v) for m, v in metrics.items()} for k, metrics in self.run_summary.items()},
            "delta": {k: dict(v) for k, v in self.delta.items()},
            "unknown": list(self.unknown),
            "detail": {k: list(v) for k, v in self.detail.items()},
            "runs": [r.to_dict() for r in self.runs],
        }


def run_live_tier(suite: EvalSuite, runs_dir: Path, configs: Sequence[str]) -> LiveTierResult:
    """Aggregate the captured runs into ``run_summary`` + ``delta``.

    A config is UNKNOWN when it has no runs, is missing a run for one of the suite's
    evals, or carries a run whose tokens / duration / outcome were lost. What IS known
    is still reported — but the status says UNKNOWN, because "cannot check" is not
    "clean".
    """
    timings = load_run_timings(runs_dir, configs)
    run_summary: dict[str, dict[str, dict[str, float | None]]] = {}
    detail: dict[str, tuple[str, ...]] = {}
    unknown: list[str] = []

    for config in configs:
        runs = [t for t in timings if t.config == config]
        problems: list[str] = []
        if not runs:
            problems.append("no captured runs — dispatch each eval in a FRESH subagent and capture its timing.json")
        covered = {t.eval_id for t in runs}
        missing = [task_id for task_id in suite.ids if task_id not in covered]
        if missing:
            # Worded "these evals" rather than "eval(s)": the parenthesised plural is
            # byte-identical to an `eval(` call and the scanner correctly cannot tell a
            # prose string from a real one. Rewording is the author-side fix; muting the
            # rule would be a suppression, and CPV never suppresses a security rule.
            problems.append(f"no run for these evals: {', '.join(missing)}")
        extra = sorted(covered - set(suite.ids))
        if extra:
            problems.append(f"run(s) for eval id(s) absent from the task file: {', '.join(extra)}")
        lost_tokens = [t.source for t in runs if t.total_tokens is None]
        lost_time = [t.source for t in runs if t.duration_ms is None]
        lost_outcome = [t.source for t in runs if t.passed is None]
        if lost_tokens:
            problems.append(f"total_tokens lost in {len(lost_tokens)} run(s) — UNKNOWN, not 0")
        if lost_time:
            problems.append(f"duration_ms lost in {len(lost_time)} run(s) — UNKNOWN, not 0")
        if lost_outcome:
            problems.append(f"pass/fail outcome missing in {len(lost_outcome)} run(s) — UNKNOWN, not a failure")

        repeats = len(runs) > len(covered)
        token_values = [float(t.total_tokens) for t in runs if t.total_tokens is not None]
        time_values = [t.duration_ms / 1000.0 for t in runs if t.duration_ms is not None]
        pass_values = [1.0 if t.passed else 0.0 for t in runs if t.passed is not None]
        run_summary[config] = {
            "pass_rate": _metric(pass_values, repeats=repeats),
            "time_seconds": _metric(time_values, repeats=repeats),
            "tokens": _metric(token_values, repeats=repeats),
        }
        if problems:
            unknown.append(config)
            detail[config] = tuple(problems)

    delta: dict[str, dict[str, float | None]] = {}
    baseline = run_summary.get(BASELINE_VARIANT)
    if baseline is not None and BASELINE_VARIANT not in unknown:
        for config, metrics in run_summary.items():
            if config == BASELINE_VARIANT or config in unknown:
                continue
            row: dict[str, float | None] = {}
            for metric_name in ("pass_rate", "time_seconds", "tokens"):
                mine = metrics[metric_name].get("mean")
                theirs = baseline[metric_name].get("mean")
                row[metric_name] = None if mine is None or theirs is None else mine - theirs
            delta[config] = row

    return LiveTierResult(
        run_summary=run_summary,
        delta=delta,
        unknown=tuple(unknown),
        detail=detail,
        runs=tuple(timings),
        tasks_path=suite.path,
        runs_dir=str(runs_dir),
    )


def benchmark_payload(live: LiveTierResult) -> dict[str, Any]:
    """The ``benchmark.json`` object, in the adopted ecosystem shape."""
    return {
        "run_summary": {k: {m: dict(v) for m, v in metrics.items()} for k, metrics in live.run_summary.items()},
        "delta": {k: dict(v) for k, v in live.delta.items()},
        "status": live.status,
        "unknown": list(live.unknown),
        "tier": live.tier,
    }


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------
def _table(headers: Sequence[str], rows: Iterable[Sequence[str]]) -> str:
    """A numbered markdown table. The leading ``#`` column exists so a reader can
    answer "row 3" without counting."""
    head = "| # | " + " | ".join(headers) + " |"
    rule = "|---|" + "|".join("---" for _ in headers) + "|"
    body = [f"| {index} | " + " | ".join(row) + " |" for index, row in enumerate(rows, start=1)]
    return "\n".join([head, rule, *body])


def _fmt_num(value: float | int | None) -> str:
    if value is None:
        return "UNKNOWN"
    if isinstance(value, int):
        return f"{value:,}"
    return f"{value:,.2f}"


def _fmt_delta(value: float | None) -> str:
    if value is None:
        return "UNKNOWN"
    return f"{value:+,.2f}"


def _like_for_like(profiles: Sequence[StaticProfile], variant: str) -> str:
    """Whether a delta row compares two fully-priced variants.

    An unpriced preload was counted as 0 tokens, so the side carrying it is understated
    and the delta is not a like-for-like comparison — it can even carry the wrong sign
    (measured: an ALL-IN-ONE variant read as 9,264 tokens CHEAPER than its original with
    its preloads unresolvable, and 13,545 tokens more EXPENSIVE once they resolved).
    Reporting the numbers without this flag invites exactly that wrong conclusion.
    """
    by_name = {p.variant: p for p in profiles}
    this = by_name.get(variant)
    baseline = by_name.get(BASELINE_VARIANT)
    sides = []
    if this is not None and this.unpriced_preloads:
        sides.append(f"{this.unpriced_preloads} in `{variant}`")
    if baseline is not None and baseline.unpriced_preloads:
        sides.append(f"{baseline.unpriced_preloads} in `{BASELINE_VARIANT}`")
    if not sides:
        return "yes"
    return "**no** — unpriced preloads: " + ", ".join(sides)


def _static_section(static: StaticTierResult) -> list[str]:
    lines = [
        f"## Tier 1 — static cost model [{TIER1}]",
        "",
        "Real measurements over real files, ZERO LLM calls. A `skills:` preload injects each",
        "named skill's FULL content into every invocation, so that content IS a prefix cost.",
        "",
    ]
    rows: list[list[str]] = []
    for profile in static.profiles:
        if not profile.evaluated:
            rows.append(
                [
                    profile.variant,
                    _NOT_EVALUATED,
                    *(["—"] * 6),
                    profile.reason or "no file supplied",
                ]
            )
            continue
        rows.append(
            [
                profile.variant,
                TIER1,
                _fmt_num(profile.cached_prefix_tokens),
                _fmt_num(profile.per_invocation_injected_tokens),
                "inherited" if profile.tools_inherited else _fmt_num(profile.tool_schema_surface),
                _fmt_num(profile.closure_file_count),
                _fmt_num(profile.closure_bytes),
                "yes" if profile.turn1_ready else "no",
                Path(profile.agent_path).name if profile.agent_path else "—",
            ]
        )
    lines.append(
        _table(
            [
                "variant",
                "tier",
                "cached prefix (tok)",
                "injected / invocation (tok)",
                "tool surface",
                "closure files",
                "closure bytes",
                "turn-1 ready",
                "file",
            ],
            rows,
        )
    )
    lines.append("")

    turn_rows: list[list[str]] = []
    for profile in static.profiles:
        if not profile.evaluated:
            continue
        turn_rows.append(
            [profile.variant, *[_fmt_num(profile.projected_turn_cost.get(str(n))) for n in static.turns]]
        )
    if turn_rows:
        lines += [
            f"### Projected N-turn prefix cost [{TIER1}]",
            "",
            f"Rate-weighted tokens: turn 1 at the cache-write rate ({CACHE_WRITE_RATE}x), "
            f"each later turn at the cache-read rate ({CACHE_READ_RATE}x).",
            "",
            _table(["variant", *[f"{n} turn(s)" for n in static.turns]], turn_rows),
            "",
        ]

    delta = static_delta(static.profiles)
    if delta:
        lines += [
            f"### Tier-1 delta vs `{BASELINE_VARIANT}` [{TIER1}]",
            "",
            "What the variant COSTS relative to the original. No ordering is implied.",
            "",
            "`like-for-like` is **no** when either side had a preload whose cost could not be"
            " priced. Such a preload counted as 0 tokens, which UNDERSTATES that side's real"
            " cost, so the delta can even carry the wrong SIGN — re-run with `--skills-root"
            " PATH` pointing at the skills before reading it as a comparison.",
            "",
            _table(
                [
                    "variant",
                    "cached prefix",
                    "injected / invocation",
                    "closure files",
                    "closure bytes",
                    "like-for-like",
                ],
                [
                    [
                        name,
                        _fmt_delta(row.get("cached_prefix_tokens")),
                        _fmt_delta(row.get("per_invocation_injected_tokens")),
                        _fmt_delta(row.get("closure_file_count")),
                        _fmt_delta(row.get("closure_bytes")),
                        _like_for_like(static.profiles, name),
                    ]
                    for name, row in delta.items()
                ],
            ),
            "",
        ]

    notes = [(p.variant, note) for p in static.profiles for note in p.notes]
    if notes:
        lines += [
            "### Tier-1 notes",
            "",
            _table(["variant", "note"], [[variant, note] for variant, note in notes]),
            "",
        ]
    return lines


def _live_section(live: LiveTierResult | None) -> list[str]:
    if live is None:
        return [
            f"## Tier 2 — live A/B/C [{TIER2}]",
            "",
            "**NOT RUN.** Opt in with `--live` (plus `--tasks` and `--runs-dir`). Nothing in the",
            "table above is a measured result — every figure is a Tier-1 static estimate.",
            "",
        ]
    lines = [
        f"## Tier 2 — live A/B/C [{TIER2}] — status: {live.status}",
        "",
        f"Task file: `{live.tasks_path}` · runs: `{live.runs_dir}` · captured runs: {len(live.runs)}",
        "",
        "Each run starts from a CLEAN context (a fresh subagent), and `total_tokens` /",
        "`duration_ms` come from the task-completion notification, captured the moment the run",
        "finished. A lost value is UNKNOWN, never 0.",
        "",
        _table(
            ["config", "tier", "pass_rate", "time_seconds", "tokens", "stddev reported"],
            [
                [
                    config,
                    TIER2,
                    _fmt_num(metrics["pass_rate"].get("mean")),
                    _fmt_num(metrics["time_seconds"].get("mean")),
                    _fmt_num(metrics["tokens"].get("mean")),
                    "yes" if "stddev" in metrics["tokens"] else "no (single runs)",
                ]
                for config, metrics in live.run_summary.items()
            ],
        ),
        "",
    ]
    if live.delta:
        lines += [
            f"### Tier-2 delta vs `{BASELINE_VARIANT}` [{TIER2}]",
            "",
            "What the variant COSTS (time, tokens) and what it BUYS (pass rate). This is the",
            "deliverable: a trade-off for the reader to weigh, not a verdict.",
            "",
            _table(
                ["config", "Δ pass_rate", "Δ time_seconds", "Δ tokens"],
                [
                    [
                        config,
                        _fmt_delta(row.get("pass_rate")),
                        _fmt_delta(row.get("time_seconds")),
                        _fmt_delta(row.get("tokens")),
                    ]
                    for config, row in live.delta.items()
                ],
            ),
            "",
        ]
    if live.unknown:
        lines += [
            "### UNKNOWN — measured nothing usable for these configs",
            "",
            _table(
                ["config", "why"],
                [[config, "; ".join(live.detail.get(config, ()))] for config in live.unknown],
            ),
            "",
        ]
    return lines


def render_report(static: StaticTierResult, live: LiveTierResult | None) -> str:
    """The findings-style report. Every number carries its tier, and nothing is ranked."""
    stamp = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S%z")
    lines = [
        "# Agent Variant Evaluation",
        "",
        f"Generated: {stamp}",
        "",
        "Two tiers, kept strictly apart. A Tier-1 figure is a STATIC ESTIMATE and is never a",
        "measured result; a Tier-2 figure is MEASURED from a real run. This report states the",
        "delta and stops — it orders nothing and declares nothing.",
        "",
    ]
    lines += _static_section(static)
    lines += _live_section(live)
    return "\n".join(lines).rstrip() + "\n"


def default_report_path(agent_path: Path) -> Path:
    """``<main-repo-root>/reports/cpv-agent-eval/<ts±tz>-<slug>.md``.

    Reuses ``validate_security._resolve_report_root`` so the anchor logic (main checkout
    root → ``CLAUDE_PROJECT_DIR`` → ``$TMPDIR``) has exactly one definition.
    """
    from validate_security import _resolve_report_root  # noqa: E402

    stamp = datetime.now(timezone.utc).astimezone().strftime("%Y%m%d_%H%M%S%z")
    slug = agent_path.stem or "agent"
    return _resolve_report_root() / "reports" / "cpv-agent-eval" / f"{stamp}-{slug}.md"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _build_parser() -> argparse.ArgumentParser:
    from cpv_validation_common import launcher_epilog  # noqa: E402

    parser = argparse.ArgumentParser(
        description="Compare an agent against any subset of its ALL-IN-ONE / ONE-FOR-ALL / PLUGIN-OMNI variants.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Tier 1 (static cost model) ALWAYS runs and makes zero LLM calls.\n"
            "Tier 2 (live A/B/C) is OPT-IN via --live and is never implied: it aggregates\n"
            "the REAL numbers a driver captured per run. It never simulates one.\n\n"
            "Live-tier layout (the ecosystem eval schema):\n"
            "  --tasks       evals/evals.json  {skill_name, evals:[{id,prompt,expected_output,files}]}\n"
            "  --runs-dir    <runs>/<config>/<eval-id>[/<run-id>]/timing.json  {total_tokens, duration_ms}\n"
            "                optional sibling result.json {passed: bool}\n"
            "  aggregate     benchmark.json  {run_summary:{<config>:{pass_rate,time_seconds,tokens}}, delta:{}}\n\n"
            "The tool reports the delta and STOPS — it ranks nothing and prints no verdict.\n\n"
            "Exit Codes:\n"
            "  0 - Tier 1 produced (and, with --live, every selected config measured)\n"
            "  1 - Input error (bad path, bad --variants, missing/malformed task file)\n"
            "  2 - Live tier UNKNOWN (a selected config was not fully measured)\n\n"
            + launcher_epilog("agent-eval")
        ),
    )
    parser.add_argument("--original", required=True, metavar="PATH", help="The ORIGINAL agent .md (the baseline)")
    parser.add_argument("--all-in-one", metavar="PATH", default=None, help="The ALL-IN-ONE variant .md")
    parser.add_argument("--one-for-all", metavar="PATH", default=None, help="The ONE-FOR-ALL variant .md")
    parser.add_argument("--plugin-omni", metavar="PATH", default=None, help="The PLUGIN-OMNI variant .md")
    parser.add_argument(
        "--variants",
        default=None,
        metavar="CSV",
        help=(
            "Which variants to evaluate, from original,all-in-one,one-for-all,plugin-omni. "
            "Defaults to the original plus every variant file supplied. A named variant whose "
            f"file is absent is reported {_NOT_EVALUATED}, never dropped from the table."
        ),
    )
    parser.add_argument(
        "--skills-root",
        action="append",
        default=None,
        dest="skills_roots",
        metavar="PATH",
        help="Skill directory to resolve each closure against (repeatable; default: auto-resolve)",
    )
    parser.add_argument(
        "--max-depth",
        type=int,
        default=DEFAULT_MAX_DEPTH,
        help=f"Transitive closure depth bound (default: {DEFAULT_MAX_DEPTH})",
    )
    parser.add_argument(
        "--turns",
        action="append",
        type=int,
        default=None,
        metavar="N",
        help=f"Turn count to project (repeatable; default: {', '.join(str(n) for n in DEFAULT_TURN_PROJECTIONS)})",
    )
    parser.add_argument("--live", action="store_true", help="Run the Tier-2 live A/B/C aggregation (opt-in)")
    parser.add_argument(
        "--tasks",
        default=DEFAULT_TASKS_PATH,
        metavar="PATH",
        help=f"Eval task file for the live tier (default: {DEFAULT_TASKS_PATH})",
    )
    parser.add_argument(
        "--runs-dir",
        default=None,
        metavar="PATH",
        help="Directory holding the captured per-run timing.json files (default: <tasks dir>/runs)",
    )
    parser.add_argument("--json", action="store_true", help="Emit the raw JSON payload (stdout stays pure JSON)")
    parser.add_argument("--report", default=None, metavar="PATH", help="Write the report here instead of reports/")
    parser.add_argument(
        "--benchmark",
        default=None,
        metavar="PATH",
        help="Write benchmark.json here (default: alongside the report; live tier only)",
    )
    return parser


def _resolve_agent_arg(raw: str | None, label: str, errors: list[str]) -> Path | None:
    if raw is None:
        return None
    path = Path(raw).expanduser()
    if not path.is_file():
        errors.append(f"--{label} {path} is not a file")
        return None
    if path.suffix.lower() != ".md":
        errors.append(f"--{label} {path} is not a Markdown (.md) agent file")
        return None
    return path


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point. Tier 1 always; Tier 2 only under ``--live``."""
    args = _build_parser().parse_args(argv)

    errors: list[str] = []
    selection: dict[str, Path | None] = {
        "original": _resolve_agent_arg(args.original, "original", errors),
        "all-in-one": _resolve_agent_arg(args.all_in_one, "all-in-one", errors),
        "one-for-all": _resolve_agent_arg(args.one_for_all, "one-for-all", errors),
        "plugin-omni": _resolve_agent_arg(args.plugin_omni, "plugin-omni", errors),
    }
    if errors:
        for message in errors:
            print(f"Error: {message}", file=sys.stderr)
        return EXIT_ERROR

    skills_roots: list[Path] | None = None
    if args.skills_roots is not None:
        skills_roots = []
        for raw in args.skills_roots:
            root = Path(raw).expanduser()
            if not root.is_dir():
                # Fail loudly. A silently dropped root leaves every name unresolved and
                # makes the whole cost model vacuous — 0 preload tokens for everyone.
                print(f"Error: --skills-root {root} is not a directory", file=sys.stderr)
                return EXIT_ERROR
            skills_roots.append(root.resolve())

    if args.max_depth < 1:
        print("Error: --max-depth must be >= 1", file=sys.stderr)
        return EXIT_ERROR

    turns = tuple(args.turns) if args.turns else DEFAULT_TURN_PROJECTIONS
    if any(n < 0 for n in turns):
        print("Error: --turns must be >= 0", file=sys.stderr)
        return EXIT_ERROR

    try:
        variants = (
            parse_variants(args.variants)
            if args.variants is not None
            else [name for name in VARIANT_NAMES if name == BASELINE_VARIANT or selection.get(name) is not None]
        )
    except EvalInputError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return EXIT_ERROR

    static = run_static_tier(
        selection,
        variants,
        roots=skills_roots,
        max_depth=args.max_depth,
        turns=turns,
    )

    live: LiveTierResult | None = None
    if args.live:
        tasks_path = Path(args.tasks).expanduser()
        try:
            suite = load_task_suite(tasks_path)
        except EvalInputError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return EXIT_ERROR
        runs_dir = Path(args.runs_dir).expanduser() if args.runs_dir else tasks_path.parent / "runs"
        live = run_live_tier(suite, runs_dir, variants)

    original_path = selection["original"]
    assert original_path is not None  # guarded above: --original is required and validated

    if args.json:
        print(
            json.dumps(
                {
                    "original": str(original_path),
                    "variants": variants,
                    "tier1": static.to_dict(),
                    "tier2": live.to_dict() if live is not None else None,
                },
                indent=2,
            )
        )
    else:
        report_path = Path(args.report).expanduser() if args.report else default_report_path(original_path)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(render_report(static, live), encoding="utf-8")
        print(f"Tier 1 [{TIER1}]: {sum(1 for p in static.profiles if p.evaluated)} of {len(variants)} variant(s) measured")
        for profile in static.profiles:
            if not profile.evaluated:
                print(f"  {profile.variant}: {_NOT_EVALUATED} — {profile.reason}")
        if live is None:
            print(f"Tier 2 [{TIER2}]: NOT RUN (opt in with --live)")
        else:
            print(f"Tier 2 [{TIER2}]: {live.status}")
            for config in live.unknown:
                print(f"  {config}: UNKNOWN — {'; '.join(live.detail.get(config, ()))}")
        print(f"Report: {report_path}")

    if live is not None:
        benchmark_path = (
            Path(args.benchmark).expanduser()
            if args.benchmark
            else (Path(args.report).expanduser().parent if args.report else None)
        )
        if benchmark_path is not None:
            if benchmark_path.is_dir() or not benchmark_path.suffix:
                benchmark_path = benchmark_path / "benchmark.json"
            benchmark_path.parent.mkdir(parents=True, exist_ok=True)
            benchmark_path.write_text(json.dumps(benchmark_payload(live), indent=2), encoding="utf-8")
            if not args.json:
                print(f"Benchmark: {benchmark_path}")
        if live.status != "OK":
            return EXIT_LIVE_UNKNOWN

    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
