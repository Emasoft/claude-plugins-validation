"""Fork safety — never fork a multithreaded process (TRDD-4KQXN8ZW).

## What this guards

Forking a multithreaded process copies mutex STATE but not the owning threads,
so a child can inherit ``sys.stderr``'s lock held by a thread that does not
exist there and hang on its first write. **Linux defaults to ``fork``, macOS
to ``spawn``** — so the hazard is invisible on a developer's Mac.

v3.23.0 shipped exactly that: progress markers emitted from
``ThreadPoolExecutor`` workers turned a tiny ``validate_plugin.py --json`` run
from 8.7s into a >300s timeout on Linux, failing CI *and* Release, after a
fully green local suite of 11,484 tests.

v3.23.1 removed the one emit site — the INSTANCE. These tests guard the CLASS:
no pool in ``scripts/`` may be built from the platform default start method.

## Why a SOURCE-level invariant rather than a behavioural one

The deadlock needs a fork to land while another thread happens to hold a lock,
so a behavioural test is a race and would flake. "No pool inherits the platform
default" is exact, instant, and machine-independent — the same reasoning that
replaced the wall-clock parallelism assertion with a ``threading.Barrier`` in
v3.19.2.
"""

from __future__ import annotations

import ast
import multiprocessing as mp
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
_SCRIPTS = _REPO / "scripts"

sys.path.insert(0, str(_SCRIPTS))

from cpv_fork_safety import (  # noqa: E402
    FORK_SAFE_METHODS,
    safe_mp_context,
    safe_start_method,
)

# ---------------------------------------------------------------------------
# The helper itself
# ---------------------------------------------------------------------------


def test_fork_is_never_an_offered_method() -> None:
    """LOAD-BEARING: ``fork`` must never enter the preference list. Everything
    else here is downstream of this one fact."""
    assert "fork" not in FORK_SAFE_METHODS


def test_start_method_is_never_fork() -> None:
    assert safe_start_method() != "fork"
    assert safe_start_method() in FORK_SAFE_METHODS


def test_context_is_never_fork() -> None:
    assert safe_mp_context().get_start_method() != "fork"


def test_spawn_is_the_pinned_method() -> None:
    """spawn everywhere, so the method a developer exercises locally is the one
    Linux CI exercises. macOS already defaulted to spawn — this makes Linux
    match the platform CPV has actually been tested on."""
    assert safe_start_method() == "spawn"


def test_forkserver_is_deliberately_NOT_used() -> None:
    """REGRESSION LOCK — forkserver is the textbook answer and was measured
    FASTEST (94.7s vs spawn 120.2s), so it is a standing temptation.

    It was rejected because its server process is started ONCE and REUSED:
    children inherit the environment captured at server start, not the caller's
    current environment. CPV is configured through env vars, so a variable set
    after the first pool was created would silently never reach a worker. That
    is worse than the deadlock — it fails QUIETLY with wrong results instead of
    hanging. It broke 5 tests that pass under spawn, each of which passed in
    isolation and failed in sequence: the signature of server reuse.
    """
    assert "forkserver" not in FORK_SAFE_METHODS
    if "forkserver" in mp.get_all_start_methods():
        assert safe_start_method() != "forkserver"


def test_only_spawn_is_offered(monkeypatch: pytest.MonkeyPatch) -> None:
    """Windows offers only spawn. The choice must not silently reach for fork."""
    monkeypatch.setattr(mp, "get_all_start_methods", lambda: ["spawn", "fork"])
    assert safe_start_method() == "spawn"


def test_fork_only_platform_still_refuses_fork(monkeypatch: pytest.MonkeyPatch) -> None:
    """Even if a platform offered ONLY fork, we must not select it — failing
    safe matters more than failing fast, because the wrong answer here is a
    deadlock in somebody else's CI."""
    monkeypatch.setattr(mp, "get_all_start_methods", lambda: ["fork"])
    assert safe_start_method() == "spawn"


def test_explicit_context_beats_a_forced_fork_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """THE POINT OF THE FIX: an explicit context must win even when the process
    default has been set to fork, because that default is what Linux gives us."""
    monkeypatch.setattr(mp, "get_start_method", lambda allow_none=False: "fork")
    assert safe_mp_context().get_start_method() != "fork"


# ---------------------------------------------------------------------------
# The source-level invariant
# ---------------------------------------------------------------------------


def _kwarg_names(call: ast.Call) -> set[str]:
    return {kw.arg for kw in call.keywords if kw.arg is not None}


def _callee_name(call: ast.Call) -> str:
    """Return the dotted callee name (``mp.get_context`` / ``ProcessPoolExecutor``)."""
    node: ast.expr = call.func
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def find_default_context_pools(source: str) -> list[tuple[int, str]]:
    """Return ``(lineno, reason)`` for every pool built from the platform default.

    Three shapes, all of which inherit ``fork`` on Linux:

    * ``ProcessPoolExecutor(...)`` with no ``mp_context=``
    * ``mp.Pool(...)`` / ``multiprocessing.Pool(...)`` — always the default
    * a BARE ``get_context()`` — an argument (``get_context("spawn")``) is fine
    """
    violations: list[tuple[int, str]] = []
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _callee_name(node)
        leaf = name.rsplit(".", 1)[-1]
        if leaf == "ProcessPoolExecutor" and "mp_context" not in _kwarg_names(node):
            violations.append((node.lineno, "ProcessPoolExecutor without mp_context="))
        elif leaf == "get_context" and not node.args:
            violations.append((node.lineno, "bare get_context() inherits the platform default"))
        elif leaf == "Pool" and name.split(".")[0] in {"mp", "multiprocessing"}:
            violations.append((node.lineno, "multiprocessing.Pool always uses the default context"))
    return violations


def test_no_script_builds_a_pool_from_the_platform_default() -> None:
    """THE REGRESSION GUARD. A new call site that inherits the default puts the
    v3.23.0 deadlock straight back, on Linux only, where nobody will see it."""
    offenders: list[str] = []
    for path in sorted(_SCRIPTS.glob("*.py")):
        for lineno, reason in find_default_context_pools(path.read_text(encoding="utf-8")):
            offenders.append(f"{path.relative_to(_REPO)}:{lineno} — {reason}")
    assert not offenders, "pool(s) built from the platform default start method:\n  " + "\n  ".join(offenders)


def test_the_scan_is_not_vacuous() -> None:
    """Guard the guard: a detector that silently matches nothing would let every
    assertion above pass while proving nothing. Assert it still SEES the real
    call sites it is meant to police."""
    seen = 0
    for path in sorted(_SCRIPTS.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and _callee_name(node).rsplit(".", 1)[-1] in {
                "ProcessPoolExecutor",
                "get_context",
                "Pool",
            }:
                seen += 1
    assert seen >= 2, f"expected to find the known pool call sites, saw {seen}"


# --- two-sided: the detector must FIRE on each violating shape ---------------


@pytest.mark.parametrize(
    ("source", "needle"),
    [
        ("from concurrent.futures import ProcessPoolExecutor\nProcessPoolExecutor(max_workers=4)\n", "mp_context"),
        ("import multiprocessing as mp\nctx = mp.get_context()\n", "bare get_context"),
        ("import multiprocessing as mp\np = mp.Pool(4)\n", "default context"),
        ("import multiprocessing\np = multiprocessing.Pool(4)\n", "default context"),
    ],
)
def test_detector_fires_on_a_violating_shape(source: str, needle: str) -> None:
    found = find_default_context_pools(source)
    assert found, f"detector missed a real violation:\n{source}"
    assert any(needle in reason for _, reason in found)


@pytest.mark.parametrize(
    "source",
    [
        # The compliant shapes — must NOT fire.
        "from concurrent.futures import ProcessPoolExecutor\nProcessPoolExecutor(max_workers=4, mp_context=CTX)\n",
        "import multiprocessing as mp\nctx = mp.get_context('spawn')\n",
        "import multiprocessing as mp\nctx = mp.get_context('forkserver')\n",
        # A same-named method on an unrelated object is not a multiprocessing pool.
        "session.Pool(4)\n",
    ],
)
def test_detector_stays_silent_on_a_compliant_shape(source: str) -> None:
    assert not find_default_context_pools(source), f"false positive on:\n{source}"


# ---------------------------------------------------------------------------
# Gate 3c placement + the no-suite guard
# ---------------------------------------------------------------------------


def test_gate_3c_runs_between_the_parallel_block_and_gate_6() -> None:
    """PLACEMENT IS LOAD-BEARING: strictly after the parallel preflight (it
    re-runs the suite, so it must not contend with Gate 2's ``-n auto`` pytest)
    and strictly BEFORE the bump/commit/tag/push, so a hang aborts with the tree
    untouched instead of stranding a tag for a release never cut."""
    src = (_SCRIPTS / "publish.py").read_text(encoding="utf-8")
    body = src.split("    prefetch = _start_prefetch(", 1)[1]
    i_par = body.index("run_preflight_parallel(")
    i_fork = body.index("stage_fork_parity(")
    i_ver = body.index("stage_version_consistency(")
    i_bump = body.index("stage_bump(")
    assert i_par < i_fork < i_ver < i_bump


def test_gate_3c_skips_when_there_is_no_suite_to_probe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A tree with no tests/ must SKIP, not fail.

    Without this guard the gate ran pytest against an empty tree, got pytest's
    "no tests collected" exit code, and reported it as a fork failure — a
    FABRICATED finding. Gate 2 already blocks a publish with no tests, so the
    skip can never mask a missing suite. Caught by the full serial suite, not by
    inspection.
    """
    sys.path.insert(0, str(_SCRIPTS))
    import publish  # noqa: PLC0415 - imported lazily; heavy module

    monkeypatch.delenv("PLUGIN_FORK_PARITY_CMD", raising=False)
    assert publish.stage_fork_parity(tmp_path) == 0


def test_gate_3c_timeout_is_typo_safe(monkeypatch: pytest.MonkeyPatch) -> None:
    """A garbage or non-positive override must fall back to the default, never
    DISABLE the guard or set a ceiling every publish would trip."""
    sys.path.insert(0, str(_SCRIPTS))
    import publish  # noqa: PLC0415

    default = publish._DEFAULT_FORK_PARITY_TIMEOUT
    for bad in ("", "   ", "nonsense", "0", "-30"):
        monkeypatch.setenv("PLUGIN_FORK_PARITY_TIMEOUT", bad)
        assert publish._fork_parity_timeout() == default, f"bad override {bad!r} changed the deadline"
    monkeypatch.setenv("PLUGIN_FORK_PARITY_TIMEOUT", "42.5")
    assert publish._fork_parity_timeout() == 42.5


def test_both_known_pool_sites_are_compliant() -> None:
    """Name the two sites explicitly so a refactor that moves them somewhere
    unpoliced is visible rather than silently losing coverage."""
    for rel in ("cpv_parallel_runner.py", "cpv_scan_supervisor.py"):
        path = _SCRIPTS / rel
        assert path.exists(), f"{rel} moved — update this guard"
        assert not find_default_context_pools(path.read_text(encoding="utf-8"))
        assert "cpv_fork_safety" in path.read_text(encoding="utf-8"), f"{rel} no longer uses the fork-safety SSOT"
