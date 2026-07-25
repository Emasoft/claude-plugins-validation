"""The pre-push hook's script budget: generous enough to not FALSE-block, still fail-closed.

`run_script`'s budget was 180s. The identical `validate_plugin.py . --verbose --strict`
invocation the hook makes measured 42-51s standalone on the tree being pushed, yet
repeatedly exceeded 180s when driven from `publish.py`'s Gate 12 — four consecutive
release attempts were blocked on a clean tree (and once before, at v3.14.0). The in-situ
cause is still unidentified (machine load, the post-Gate-10 tree, a `uv lock` re-sync and
a captured-pipe deadlock were each measured and ruled out), so the budget carries margin
and the hook now always reports elapsed time.

The load-bearing half is the SECOND test: a budget increase must never turn into a
strictness decrease. A timeout still has to BLOCK the push.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CPV_HOOK = REPO_ROOT / "git-hooks" / "pre-push"


def _load_hook_namespace() -> dict:
    """Exec the hook as a module WITHOUT running main(), and return its namespace."""
    src = CPV_HOOK.read_text(encoding="utf-8")
    ns: dict = {"__name__": "prepush_under_test", "__file__": str(CPV_HOOK)}
    exec(compile(src, str(CPV_HOOK), "exec"), ns)  # noqa: S102 - executing our own hook by design
    return ns


def test_hook_is_syntactically_valid() -> None:
    """A hook that cannot compile blocks EVERY push, so this guards the whole gate."""
    subprocess.run([sys.executable, "-m", "py_compile", str(CPV_HOOK)], check=True)


def test_run_script_budget_has_margin_over_measured_runtime() -> None:
    """The script budget must exceed the measured ~50s validate run by a wide margin."""
    import inspect

    ns = _load_hook_namespace()
    default = inspect.signature(ns["run_script"]).parameters["timeout"].default
    assert default >= 600, f"budget regressed to {default}s — false blocks on a clean tree return"


def test_timeout_still_fails_closed(tmp_path: Path) -> None:
    """LOAD-BEARING: a script that exceeds its budget must BLOCK the push, not pass it.

    Raising the budget must never become 'let an unvalidated tree through'.
    """
    ns = _load_hook_namespace()
    slow = tmp_path / "slow.py"
    slow.write_text("import time\ntime.sleep(30)\n", encoding="utf-8")
    rc = ns["run_script"]([sys.executable], slow, None, timeout=2)
    assert rc != 0, "FAIL-CLOSED BROKEN: a timed-out validation reported success"


def test_successful_run_reports_elapsed_time(tmp_path: Path, capfd) -> None:
    """Elapsed time is reported on success too — a run creeping toward the budget
    is the early warning, and it is invisible if only timeouts are reported."""
    ns = _load_hook_namespace()
    quick = tmp_path / "quick.py"
    quick.write_text("print('ok')\n", encoding="utf-8")
    rc = ns["run_script"]([sys.executable], quick, None, timeout=30)
    assert rc == 0
    out = capfd.readouterr().out
    assert "completed in" in out and "budget" in out, f"no timing instrumentation in output: {out!r}"


def test_installed_hook_matches_the_tracked_source() -> None:
    """The installed hook must not drift from the tracked one.

    It had: the installed copy was 467 lines while the tracked source was 599 (missing
    the issue-#169 branch-aware gates entirely), so the gate actually enforcing pushes was
    not the gate under review. Only meaningful when a hook is installed.
    """
    installed = REPO_ROOT / ".git" / "hooks" / "pre-push"
    if not installed.is_file():
        return  # fresh clone / CI checkout — nothing installed to compare
    assert installed.read_text(encoding="utf-8") == CPV_HOOK.read_text(encoding="utf-8"), (
        "installed .git/hooks/pre-push has drifted from git-hooks/pre-push — reinstall it"
    )
