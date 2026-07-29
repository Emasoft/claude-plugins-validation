#!/usr/bin/env python3
"""Linux fork-parity probe: run a command as Linux would run it (TRDD-4KQXN8ZW).

## Why this exists

``multiprocessing`` defaults to **fork on Linux** and **spawn on macOS**. Every
deadlock caused by forking a multithreaded process is therefore invisible to a
developer on a Mac and fatal in CI. CPV shipped exactly that in v3.23.0: a
green 11,484-test local suite, then a >300s timeout on Linux that failed CI and
Release.

This probe removes the asymmetry *before* publishing, without Docker and
without a Linux runner: it forces the interpreter's default start method to
``fork`` and re-runs the caller's command. Verified two-sided against the real
v3.23.0 defect:

===========================  ==============  ==================================
code                         start method    result
===========================  ==============  ==================================
v3.23.1 (fixed)              fork            37 passed in 15.4s
v3.23.0 (buggy)              spawn           16 passed — **blind**
v3.23.0 (buggy)              fork            **FAILED** — TimeoutExpired, the
                                             exact signature CI produced
===========================  ==============  ==================================

The middle row is the whole point: the gate a developer actually runs could not
see the defect.

## Applicability

* Where the default is **already fork** (Linux), the ordinary test run covers
  this and the probe reports ``already-native`` and skips — it never doubles CI.
* Where ``fork`` is unavailable (Windows), it reports ``unsupported`` and
  degrades to a WARNING. It never false-blocks.
* It only ever forces the *default*; code that pins an explicit context (as CPV
  now does — see ``cpv_fork_safety``) is correctly unaffected, so the probe
  measures real exposure rather than a configuration nobody ships.
"""

from __future__ import annotations

import multiprocessing as mp
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

__all__ = ["ForkParityResult", "fork_parity_supported", "run_under_linux_fork_default", "write_sitecustomize"]

# Chains to a project's own sitecustomize so forcing the start method never
# silently disables setup the plugin depends on.
_SITECUSTOMIZE = '''\
"""Temporary: force the Linux default multiprocessing start method (CPV fork-parity probe)."""
import importlib.util as _ilu
import os as _os
import sys as _sys

try:
    import multiprocessing as _mp

    _mp.set_start_method("fork", force=True)
except Exception:
    pass

# Do not shadow a sitecustomize the project itself provides — chain to it.
_self_dir = _os.path.dirname(_os.path.abspath(__file__))
for _entry in _sys.path:
    if not _entry:
        continue
    try:
        if _os.path.abspath(_entry) == _self_dir:
            continue
        _cand = _os.path.join(_entry, "sitecustomize.py")
        if _os.path.isfile(_cand):
            _spec = _ilu.spec_from_file_location("_cpv_chained_sitecustomize", _cand)
            if _spec and _spec.loader:
                _spec.loader.exec_module(_ilu.module_from_spec(_spec))
            break
    except Exception:
        break
'''


@dataclass(frozen=True)
class ForkParityResult:
    """Outcome of a fork-parity run.

    ``status`` is one of:

    * ``ran``            — the probe executed; ``returncode`` is authoritative.
    * ``already-native`` — the platform default is already fork; nothing to prove.
    * ``unsupported``    — fork is unavailable here (Windows); advisory only.
    """

    status: str
    returncode: int = 0
    detail: str = ""
    output: str = ""

    @property
    def blocked(self) -> bool:
        """True only when the probe actually RAN and the command failed.

        A probe that could not run is never a block — "cannot check" must not be
        reported as "clean", but it must not fail a publish either.
        """
        return self.status == "ran" and self.returncode != 0


def fork_parity_supported() -> tuple[bool, str]:
    """Return ``(runnable, reason)`` for this platform."""
    try:
        methods = mp.get_all_start_methods()
        default = mp.get_start_method(allow_none=False)
    except Exception as exc:  # pragma: no cover - defensive
        return (False, f"multiprocessing unavailable: {exc}")
    if "fork" not in methods:
        return (False, f"fork unavailable on {sys.platform}")
    if default == "fork":
        return (False, "platform default is already fork — the normal run covers it")
    return (True, f"default is {default}; probing with fork")


def write_sitecustomize(directory: Path) -> Path:
    """Write the start-method-forcing ``sitecustomize.py`` into ``directory``."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "sitecustomize.py"
    path.write_text(_SITECUSTOMIZE, encoding="utf-8")
    return path


def run_under_linux_fork_default(
    cmd: list[str],
    cwd: Path,
    *,
    timeout: float = 1800.0,
    env: dict[str, str] | None = None,
) -> ForkParityResult:
    """Run ``cmd`` with the interpreter default forced to ``fork``.

    The forcing happens via a temporary ``sitecustomize.py`` prepended to
    ``PYTHONPATH``, so it reaches SUBPROCESSES too — which matters because the
    v3.23.0 deadlock happened in a subprocess the test suite spawned, not in
    pytest's own process.
    """
    runnable, reason = fork_parity_supported()
    if not runnable:
        status = "already-native" if "already fork" in reason else "unsupported"
        return ForkParityResult(status=status, detail=reason)

    with tempfile.TemporaryDirectory(prefix="cpv-forkparity-") as tmp:
        site_dir = Path(tmp)
        write_sitecustomize(site_dir)
        child_env = dict(env or os.environ)
        existing = child_env.get("PYTHONPATH", "")
        child_env["PYTHONPATH"] = f"{site_dir}{os.pathsep}{existing}" if existing else str(site_dir)
        try:
            proc = subprocess.run(  # noqa: S603 - cmd is caller-controlled, not user input
                cmd,
                cwd=str(cwd),
                env=child_env,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            # A HANG is the signature of this defect class, so a timeout is a
            # real finding — never a "could not check".
            return ForkParityResult(
                status="ran",
                returncode=124,
                detail=f"timed out after {timeout:.0f}s under the Linux fork default — this is the deadlock signature",
            )
        except OSError as exc:
            return ForkParityResult(status="unsupported", detail=f"could not launch probe: {exc}")
        return ForkParityResult(
            status="ran",
            returncode=proc.returncode,
            detail=reason,
            output=(proc.stdout or "") + (proc.stderr or ""),
        )


def main() -> int:
    """CLI: ``cpv-remote-validate fork-parity <path> [-- cmd ...]``.

    Exists so the command ``publish.py`` prints on failure is real and
    reproducible by hand — an error message that names a command nobody can run
    is worse than no message.
    """
    import argparse

    # Split the command off BEFORE argparse sees it. The probed command routinely
    # carries its own flags (``python -c``, ``pytest -q``), and argparse would
    # claim them as its own — so everything after a literal ``--`` is opaque.
    argv = list(sys.argv[1:])
    cmd_override: list[str] = []
    if "--" in argv:
        idx = argv.index("--")
        argv, cmd_override = argv[:idx], argv[idx + 1 :]

    parser = argparse.ArgumentParser(
        prog="cpv-remote-validate fork-parity",
        description="Re-run a command with multiprocessing forced to fork, the way Linux runs it.",
        epilog="Pass the command to probe after a literal `--`, e.g.: fork-parity . -- uv run pytest tests/ -q",
    )
    parser.add_argument("path", nargs="?", default=".", help="plugin/repo root to run in")
    parser.add_argument("--timeout", type=float, default=1800.0, help="deadline in seconds (default: 1800)")
    # parse_known_args, not parse_args: the launcher (remote_validation.py)
    # already consumed the ``--`` separator — argparse strips the first one — so
    # by the time we are reached the probed command arrives as ordinary trailing
    # tokens. Anything we do not recognise is part of that command.
    args, rest = parser.parse_known_args(argv)

    cmd = cmd_override or rest or ["uv", "run", "pytest", "tests/", "-n", "auto", "--dist=worksteal", "-q"]
    result = run_under_linux_fork_default(cmd, Path(args.path).resolve(), timeout=args.timeout)

    if result.status != "ran":
        # Report inability as inability. Never as a pass.
        print(f"SKIPPED (probe not applicable): {result.detail}", file=sys.stderr)
        return 0
    if result.output:
        print(result.output)
    if result.blocked:
        print(f"FAILED under the Linux fork default: {result.detail}", file=sys.stderr)
        return 1
    print("PASSED under the Linux fork default")
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry
    raise SystemExit(main())
