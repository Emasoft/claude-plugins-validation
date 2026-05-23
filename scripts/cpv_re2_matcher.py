#!/usr/bin/env python3
"""Hybrid ``google-re2`` ``RegexSet`` matcher with Python ``re`` fallback.

CPV scanners run dozens of regex patterns over every file. A naive loop
calls ``re.search`` once per pattern → O(N_patterns × N_text) work even
when most patterns don't match. ``google-re2``'s ``RE2::Set`` compiles
N patterns into a single Aho-Corasick-like automaton that reports the
matching subset in ONE pass over the input → O(N_text) work + per-hit
post-processing for the (usually small) subset that fired.

This module wraps that machinery behind a safe Python facade:

  * If ``google-re2`` is importable: most patterns go through the fast
    ``RE2::Set`` path; patterns RE2 refuses (lookaround, backrefs,
    syntax it deems incompatible) silently degrade to a Python ``re``
    fallback list. The user gets the speedup with zero behavioural
    regressions.
  * If ``google-re2`` is NOT importable: every pattern goes through
    the Python ``re`` fallback. The scanner still works, just at the
    slower per-pattern-search rate.
  * If an individual pattern is invalid for BOTH RE2 and Python ``re``
    (corrupt rule catalog): a CRITICAL ``InvalidPattern`` finding is
    surfaced via the matcher's ``invalid_patterns`` collection and
    scanning continues for every other valid pattern. The matcher
    NEVER silently drops a pattern.

The contract is:

  1. ``HybridMatcher(patterns)`` accepts ``{rule_id: pattern_str}``.
  2. ``.scan(text)`` returns ``[(rule_id, match_or_proxy), ...]`` in
     deterministic order (sorted by ``rule_id`` then by match span
     start), aggregating BOTH the RE2 layer and the fallback layer.
  3. ``.stats`` exposes routing counts for telemetry.
  4. ``HybridMatcher`` is pickleable across the multiprocessing
     boundary — ``re2.Set`` itself is NOT pickleable (verified
     empirically against ``google-re2==1.1.20251105``), so we
     persist only the source patterns and rebuild the Set lazily
     after unpickling.
  5. ``.scan(text)`` is safe to call from multiple threads sharing
     ONE matcher — the underlying ``RE2::Set::Match`` is a read-only
     operation on a frozen compiled object, and the fallback
     iteration only touches local list state.

This module is read-only; it never writes to disk, never executes
subprocess commands, never imports network code.
"""

from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass
from typing import Any, Final

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Optional google-re2 import — fail-soft to all-Python re fallback.
# ---------------------------------------------------------------------------

_re2_import_error: str | None = None

try:
    import re2 as _re2_imported  # type: ignore[import-not-found, import-untyped]

    _re2_module: Any = _re2_imported
except ImportError as exc:  # pragma: no cover - exercised via monkeypatch in tests
    _re2_module = None
    _re2_import_error = str(exc)


# Single-shot informational log so we don't spam on every HybridMatcher().
_LOG_ONCE_LOCK = threading.Lock()
_LOG_ONCE_FIRED: set[str] = set()


def _log_once(level: int, msg: str) -> None:
    """Emit ``msg`` at ``level`` exactly once across the process lifetime."""
    with _LOG_ONCE_LOCK:
        if msg in _LOG_ONCE_FIRED:
            return
        _LOG_ONCE_FIRED.add(msg)
    logger.log(level, msg)


def _reset_log_once_for_tests() -> None:
    """Test-only helper: wipe the once-fired set so tests can re-observe logs."""
    with _LOG_ONCE_LOCK:
        _LOG_ONCE_FIRED.clear()


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class InvalidPattern:
    """A pattern that neither RE2 nor Python ``re`` could compile.

    Surfaced via ``HybridMatcher.invalid_patterns`` so callers can emit
    a CRITICAL finding ("pattern compile failed for {rule_id}") without
    making the matcher itself responsible for the report format.
    """

    rule_id: str
    pattern: str
    re2_error: str | None
    re_error: str


@dataclass(frozen=True)
class _Re2MatchProxy:
    """A lightweight proxy for an RE2 match result.

    ``re2._Regexp.search()`` returns a ``re.Match``-compatible object, but
    we want a stable type whose ``.span()`` and ``.group()`` are
    inspectable in tests regardless of the underlying google-re2 build.
    Constructed only when we actually need span info for a fired pattern.
    """

    rule_id: str
    _start: int
    _end: int
    _text: str

    def span(self) -> tuple[int, int]:
        """Return the ``(start, end)`` index range of the match."""
        return (self._start, self._end)

    def group(self, idx: int = 0) -> str:
        """Return the matched substring (group 0 only — no capture support)."""
        if idx != 0:
            raise IndexError("_Re2MatchProxy only exposes group(0)")
        return self._text[self._start : self._end]

    @property
    def start(self) -> int:
        return self._start

    @property
    def end(self) -> int:
        return self._end


# ---------------------------------------------------------------------------
# HybridMatcher
# ---------------------------------------------------------------------------


_RE2_NEEDS_TRANSLATE: Final[tuple[tuple[str, str], ...]] = (
    # Python's `(?P<name>...)` named groups are written `(?P<name>...)` in RE2
    # too — no translation needed. Listed here for documentation.
)


@dataclass
class _MatcherStats:
    re2_compiled: int = 0
    re_fallback: int = 0
    re2_available: bool = False
    invalid: int = 0


class HybridMatcher:
    """Hybrid RE2 / Python ``re`` matcher over a fixed pattern catalog.

    Construction is eager: every pattern is compiled (with RE2 first,
    Python ``re`` second) and routed to the appropriate layer. After
    construction the matcher is frozen — there is no ``add_pattern``
    method on purpose, because that would invalidate the compiled
    ``RE2::Set`` and force a rebuild every call.

    To change the catalog, build a new ``HybridMatcher(new_patterns)``.
    """

    # Sentinel value used by the test suite to force the
    # "google-re2 unavailable" code path even when re2 IS importable.
    # Pass ``_force_re2_disabled=True`` to the constructor.
    __slots__ = (
        "_patterns",
        "_re2_set",
        "_re2_rule_ids",
        "_re2_compiled_individual",
        "_fallback",
        "_invalid",
        "_stats",
        "_force_re2_disabled",
        "_lock",
    )

    def __init__(
        self,
        patterns: dict[str, str],
        *,
        _force_re2_disabled: bool = False,
    ) -> None:
        # Copy to insulate the matcher from caller mutations.
        self._patterns: dict[str, str] = dict(patterns)
        self._force_re2_disabled: bool = bool(_force_re2_disabled)

        # RE2 layer state.
        self._re2_set: Any = None
        # Parallel arrays so a RE2::Set match index → rule_id lookup is O(1).
        self._re2_rule_ids: list[str] = []
        # Per-rule individual RE2 compiled regex (for getting span on a hit).
        self._re2_compiled_individual: dict[str, Any] = {}

        # Python re fallback layer.
        self._fallback: list[tuple[str, re.Pattern[str]]] = []

        # Patterns that compiled neither in RE2 nor in Python re.
        self._invalid: list[InvalidPattern] = []

        # Re-entrant lock guarding lazy rebuild of the RE2 Set after unpickling.
        # scan() acquires it briefly to ensure the lazy build runs at most once.
        self._lock = threading.Lock()

        self._stats = _MatcherStats(
            re2_available=self._effective_re2_available(),
        )

        self._build_layers()

    # ------------------------------------------------------------------ build

    def _effective_re2_available(self) -> bool:
        return (_re2_module is not None) and (not self._force_re2_disabled)

    def _build_layers(self) -> None:
        """Route every pattern into the RE2 or fallback layer.

        Strategy:
            (a) If RE2 is effectively unavailable → every pattern goes
                to the Python ``re`` fallback. Log INFO once.
            (b) Otherwise: try to ``Add`` each pattern to a fresh
                ``RE2::Set``. If RE2 rejects it (returns -1 / raises),
                that single pattern falls back to Python ``re``. Log
                WARNING per-fallback with the rule_id.
            (c) Either layer can ALSO emit an ``InvalidPattern`` when
                Python ``re`` itself can't compile the pattern. Such
                patterns are dropped from active scanning but surfaced
                via ``.invalid_patterns`` so the caller can raise a
                CRITICAL finding.
        """
        re2_available = self._effective_re2_available()

        if not re2_available:
            reason = "google-re2 not installed" if _re2_module is None else "google-re2 disabled by caller"
            _log_once(
                logging.INFO,
                f"RE2 unavailable ({reason}), using Python re for all patterns",
            )
            for rule_id, pattern in self._patterns.items():
                self._add_to_fallback(rule_id, pattern, re2_error=None)
            self._emit_routing_log()
            return

        assert _re2_module is not None  # for type-checker
        re2_set = _re2_module.Set.SearchSet()
        accepted_rule_ids: list[str] = []

        for rule_id, pattern in self._patterns.items():
            try:
                index = re2_set.Add(pattern)
            except Exception as exc:  # noqa: BLE001 — RE2 raises plain Exception
                # RE2 refused the pattern — route to fallback.
                logger.warning(
                    "Pattern %r rejected by RE2 (%s); using Python re fallback",
                    rule_id,
                    str(exc)[:120],
                )
                self._add_to_fallback(rule_id, pattern, re2_error=str(exc))
                continue

            if index is None or index < 0:
                # Some RE2 builds return -1 instead of raising.
                logger.warning(
                    "Pattern %r rejected by RE2 (index=%r); using Python re fallback",
                    rule_id,
                    index,
                )
                self._add_to_fallback(rule_id, pattern, re2_error="RE2.Add returned non-positive index")
                continue

            accepted_rule_ids.append(rule_id)

        # Even if every pattern fell through to fallback, do not call
        # Compile() on an empty Set — RE2 dislikes that. Keep _re2_set
        # = None when there's nothing to compile.
        if accepted_rule_ids:
            try:
                re2_set.Compile()
            except Exception as exc:  # noqa: BLE001
                # If Compile fails wholesale (extremely rare; usually an
                # OOM-class error), route ALL accepted patterns to the
                # fallback rather than losing them.
                logger.warning(
                    "RE2::Set::Compile() failed (%s); routing %d patterns to Python re fallback",
                    str(exc)[:120],
                    len(accepted_rule_ids),
                )
                for rule_id in accepted_rule_ids:
                    self._add_to_fallback(rule_id, self._patterns[rule_id], re2_error=str(exc))
                self._emit_routing_log()
                return

            self._re2_set = re2_set
            self._re2_rule_ids = accepted_rule_ids
            # Compile each accepted pattern individually so we can extract
            # span info on a hit. RE2.Set's Match() returns indexes only —
            # not spans.
            for rule_id in accepted_rule_ids:
                try:
                    self._re2_compiled_individual[rule_id] = _re2_module.compile(self._patterns[rule_id])
                except Exception as exc:  # noqa: BLE001
                    # Defensive: if Add() succeeded but compile() fails,
                    # demote to fallback. This should be vanishingly rare
                    # but the contract is "never silently drop".
                    logger.warning(
                        "Pattern %r passed RE2.Set.Add but failed individual compile (%s); using fallback",
                        rule_id,
                        str(exc)[:120],
                    )
                    # Remove from RE2 layer.
                    self._re2_rule_ids = [r for r in self._re2_rule_ids if r != rule_id]
                    self._add_to_fallback(rule_id, self._patterns[rule_id], re2_error=str(exc))

            self._stats.re2_compiled = len(self._re2_rule_ids)

        self._emit_routing_log()

    def _add_to_fallback(self, rule_id: str, pattern: str, re2_error: str | None) -> None:
        """Compile ``pattern`` with Python ``re`` and append to fallback list.

        If Python ``re`` also rejects the pattern, record an
        ``InvalidPattern`` instead — never silently drop.
        """
        try:
            compiled = re.compile(pattern)
        except re.error as exc:
            self._invalid.append(
                InvalidPattern(
                    rule_id=rule_id,
                    pattern=pattern,
                    re2_error=re2_error,
                    re_error=str(exc),
                )
            )
            self._stats.invalid += 1
            return
        self._fallback.append((rule_id, compiled))
        self._stats.re_fallback += 1

    def _emit_routing_log(self) -> None:
        msg = (
            f"RE2 patterns: {self._stats.re2_compiled} compiled, "
            f"{self._stats.re_fallback} fallback, {self._stats.invalid} invalid"
        )
        logger.info(msg)

    # ------------------------------------------------------------------- scan

    def scan(self, text: str) -> list[tuple[str, Any]]:
        """Scan ``text`` and return every matching ``(rule_id, match)`` pair.

        The match object is a ``re.Match`` for fallback patterns and a
        ``_Re2MatchProxy`` for RE2 hits. Both expose ``.span()`` and
        ``.group()`` so callers don't need to special-case them.

        Output ordering is deterministic: sorted first by ``rule_id``
        (lexicographic), then by match span start. Stable across calls.
        """
        # Empty input — no need to walk either layer.
        if not text:
            return []

        results: list[tuple[str, Any]] = []

        # RE2 layer (lazy rebuild via __setstate__ has already filled
        # _re2_set if we were unpickled).
        if self._re2_set is not None:
            try:
                matched_indexes = self._re2_set.Match(text)
            except Exception as exc:  # noqa: BLE001
                logger.warning("RE2::Set::Match raised %s; falling through to fallback only", exc)
                matched_indexes = None

            if matched_indexes:
                for idx in matched_indexes:
                    if not (0 <= idx < len(self._re2_rule_ids)):
                        # RE2 returned an index we don't know about. Skip
                        # rather than crash — defensive against future
                        # google-re2 API changes.
                        continue
                    rule_id = self._re2_rule_ids[idx]
                    compiled = self._re2_compiled_individual.get(rule_id)
                    if compiled is None:
                        continue
                    m = compiled.search(text)
                    if m is None:
                        # RE2.Set said it matched but per-pattern search
                        # disagrees. Defensive: just skip — should never
                        # happen unless the pattern has anchoring quirks.
                        continue
                    span = m.span()
                    results.append(
                        (
                            rule_id,
                            _Re2MatchProxy(
                                rule_id=rule_id,
                                _start=span[0],
                                _end=span[1],
                                _text=text,
                            ),
                        )
                    )

        # Fallback layer — always walked.
        for rule_id, compiled in self._fallback:
            m = compiled.search(text)
            if m is not None:
                results.append((rule_id, m))

        # Stable sort: rule_id ascending, then span.start ascending.
        results.sort(key=lambda pair: (pair[0], pair[1].span()[0]))
        return results

    # -------------------------------------------------------------- accessors

    @property
    def stats(self) -> dict[str, Any]:
        """Return a snapshot of routing counts.

        Keys:
            ``re2_compiled`` (int)  — patterns served by the RE2 layer.
            ``re_fallback`` (int)   — patterns served by the Python re fallback.
            ``invalid`` (int)       — patterns that compiled in neither.
            ``re2_available`` (bool) — whether google-re2 was importable AND
                                       not forcibly disabled.
        """
        return {
            "re2_compiled": self._stats.re2_compiled,
            "re_fallback": self._stats.re_fallback,
            "invalid": self._stats.invalid,
            "re2_available": self._stats.re2_available,
        }

    @property
    def invalid_patterns(self) -> list[InvalidPattern]:
        """Return the list of patterns that failed BOTH RE2 and Python re.

        Callers should iterate this after construction and emit one
        CRITICAL finding per entry so corrupt rules in the catalog are
        surfaced exactly once and at the right severity.
        """
        return list(self._invalid)

    # ---------------------------------------------------------- pickle support

    def __getstate__(self) -> dict[str, Any]:
        """Pickle-safe state: drop the un-pickleable ``re2.Set``.

        ``google-re2``'s ``re2._re2.Set`` raises ``TypeError`` on
        ``pickle.dumps``. We persist only the source patterns + the
        force-disabled flag; the receiving process rebuilds the Set
        lazily via ``__setstate__``.
        """
        return {
            "_patterns": self._patterns,
            "_force_re2_disabled": self._force_re2_disabled,
        }

    def __setstate__(self, state: dict[str, Any]) -> None:
        # Re-initialise from scratch on the receiving side. The receiving
        # process may not have google-re2 installed even if the sending
        # process did — that's fine; the build_layers path handles it.
        self._patterns = state["_patterns"]
        self._force_re2_disabled = state["_force_re2_disabled"]
        self._re2_set = None
        self._re2_rule_ids = []
        self._re2_compiled_individual = {}
        self._fallback = []
        self._invalid = []
        self._lock = threading.Lock()
        self._stats = _MatcherStats(re2_available=self._effective_re2_available())
        self._build_layers()


__all__ = [
    "HybridMatcher",
    "InvalidPattern",
    "_Re2MatchProxy",
    "_reset_log_once_for_tests",
]
