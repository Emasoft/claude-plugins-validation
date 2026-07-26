#!/usr/bin/env python3
"""Launch-time agent-definition preflight for `cpv-agent`.

`cpv-agent` runs this ONCE at launch, before doing the work it was actually
dispatched for. It answers one question deterministically and cheaply: *are any
agent definitions in scope broken?* If yes, `cpv-agent` dispatches
`cpv-plugin-fixer-agent` to repair them (it never hand-edits) and then continues
with the original request.

WHY a script instead of letting the agent improvise the check: detection must be
identical on every launch and must cost ZERO model tokens when everything is
clean, which is the overwhelmingly common case. Resolving directories, applying
the severity policy, and deciding "blocking or not" are mechanical, so they
belong here; only the decision to dispatch a fixer needs a model.

This module does NOT re-implement any validation — it imports
`validate_agents_directory` and reuses `exit_code`/`exit_code_strict()`, so the
severity contract has exactly one definition (WARNING never blocks; NIT blocks
only under --strict). A second copy of that policy would drift.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from validate_agent import validate_agents_directory  # noqa: E402

EXIT_CLEAN = 0
EXIT_FINDINGS = 1
EXIT_ERROR = 2

# Severity levels that a preflight will hand to the fixer. INFO/PASSED are
# advisory and must never trigger an edit — auto-"fixing" advisories would churn
# the user's agent files on every single launch for no correctness gain.
_BLOCKING_LEVELS = ("CRITICAL", "MAJOR", "MINOR", "NIT")


def _has_agent_files(directory: Path) -> bool:
    try:
        return any(p.suffix.lower() == ".md" for p in directory.iterdir() if p.is_file())
    except OSError:
        return False


def resolve_agent_dirs(target: Path | None = None, *, home: Path | None = None) -> list[Path]:
    """Return the agent directories in scope for one `cpv-agent` launch.

    Scope, widest-value-first:
      * ``<target>/agents`` — the plugin currently being worked on;
      * ``<target>/.claude/agents`` — that project's project-scope agents;
      * ``~/.claude/agents`` — the user-scope agents, which apply to every
        session on this machine and are therefore in scope for any launch.

    Only existing directories that actually contain ``.md`` files are returned,
    de-duplicated by RESOLVED path so a symlinked or repeated location is never
    scanned (or fixed) twice.
    """
    home = home or Path.home()
    candidates: list[Path] = []
    if target is not None:
        candidates.append(target / "agents")
        candidates.append(target / ".claude" / "agents")
    candidates.append(home / ".claude" / "agents")

    resolved: list[Path] = []
    seen: set[Path] = set()
    for candidate in candidates:
        try:
            real = candidate.resolve()
        except OSError:
            continue
        if real in seen or not real.is_dir() or not _has_agent_files(real):
            continue
        seen.add(real)
        resolved.append(real)
    return resolved


def preflight(dirs: list[Path], *, strict: bool = True) -> dict:
    """Scan `dirs` and report which agent definitions are BLOCKING-broken."""
    blocking: list[dict] = []
    scanned = 0

    for directory in dirs:
        for report in validate_agents_directory(directory):
            scanned += 1
            code = report.exit_code_strict() if strict else report.exit_code
            if code == EXIT_CLEAN:
                continue
            findings = [
                {"level": r.level, "message": r.message}
                for r in report.results
                if r.level in _BLOCKING_LEVELS
            ]
            blocking.append(
                {
                    "path": report.agent_path,
                    "exit_code": code,
                    "findings": findings,
                }
            )

    return {
        "dirs": [str(d) for d in dirs],
        "scanned": scanned,
        "blocking_count": len(blocking),
        "blocking": blocking,
        "verdict": "FINDINGS" if blocking else "CLEAN",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Detect broken agent definitions in scope for a cpv-agent launch.",
    )
    parser.add_argument(
        "target",
        nargs="?",
        default=None,
        help="Plugin/project root whose agents/ should also be checked",
    )
    parser.add_argument(
        "--dir",
        action="append",
        default=[],
        dest="dirs",
        help="Explicit agent directory to check (repeatable; skips auto-resolution)",
    )
    parser.add_argument("--json", action="store_true", help="Emit the full JSON verdict")
    parser.add_argument(
        "--no-strict",
        action="store_true",
        help="Do not treat NIT as blocking (default: strict, NIT blocks)",
    )
    args = parser.parse_args(argv)

    if args.dirs:
        dirs = []
        for raw in args.dirs:
            path = Path(raw).expanduser()
            if not path.is_dir():
                print(f"Error: {path} is not a directory", file=sys.stderr)
                return EXIT_ERROR
            dirs.append(path.resolve())
    else:
        target = Path(args.target).expanduser().resolve() if args.target else None
        dirs = resolve_agent_dirs(target)

    if not dirs:
        # No agent directories anywhere in scope is a legitimate CLEAN, not an
        # error — most plugins ship no agents at all.
        if args.json:
            print(json.dumps({"dirs": [], "scanned": 0, "blocking_count": 0, "blocking": [], "verdict": "CLEAN"}))
        else:
            print("AGENT-PREFLIGHT: CLEAN (no agent directories in scope)")
        return EXIT_CLEAN

    result = preflight(dirs, strict=not args.no_strict)

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"AGENT-PREFLIGHT: {result['verdict']} — {result['scanned']} agent(s) scanned, {result['blocking_count']} blocking")
        for item in result["blocking"]:
            first = item["findings"][0]["message"] if item["findings"] else "(no message)"
            print(f"  {item['path']}: {first}")

    return EXIT_FINDINGS if result["blocking"] else EXIT_CLEAN


if __name__ == "__main__":
    sys.exit(main())
