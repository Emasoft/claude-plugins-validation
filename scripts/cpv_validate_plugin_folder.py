#!/usr/bin/env python3
"""Read-only full-coverage scan of ONE plugin/skill folder.

Backs the ``cpv-validate-plugin-folder`` skill. Orchestrates the existing
validators — it implements no checks of its own — merges their reports into a
single artefact, and names the exact agent that fixes each finding class.

WHY an orchestrator and not another validator: ``plugin`` mode already runs the
cache audit (CA-01..CA-07) and an execution-class security subset, but NOT the
full ``security`` mode where secret/leak detection lives. "Full scan" is
therefore a COMPOSITION of existing modes, and composing them here keeps one
source of truth for every rule.

WHY subprocesses and not imports: each mode runs through
``remote_validation.py``, whose whole job is environment isolation — it stops
the TARGET's ``pyproject.toml`` / ``.mypy.ini`` / a stale copy of
``cpv_validation_common.py`` from hijacking CPV's own imports. Importing the
validators directly here would silently drop that guarantee on exactly the
folders users point this at (an installed plugin scanning a foreign repo).

READ-ONLY: nothing is ever written inside the target. The merged report lands
under the CALLER's ``reports/cpv-validate-plugin-folder/`` per the agent-reports
rule. When the target IS the caller's project, that directory is the
conventional gitignored report location, not a modification of the plugin.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# Validator reports carry ANSI colour. Merging them raw produces a report file
# full of escape sequences — and CPV's own scanner then flags that file (the
# v2.107.1 "validator scanned its own ANSI report" defect). Strip on merge.
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from cpv_validation_common import (  # noqa: E402
    EXIT_CRITICAL,
    EXIT_MAJOR,
    EXIT_MINOR,
    EXIT_NIT,
    EXIT_OK,
    build_report_path,
)

_LAUNCHER = _SCRIPTS_DIR / "remote_validation.py"

# Worst-wins ordering. The exit vocabulary is 0..4 with 1 the MOST severe, so a
# plain max()/min() over the raw codes gets it backwards — rank explicitly.
_SEVERITY_RANK: dict[int, int] = {
    EXIT_OK: 0,
    EXIT_NIT: 1,
    EXIT_MINOR: 2,
    EXIT_MAJOR: 3,
    EXIT_CRITICAL: 4,
}

# Which agent fixes what. Static by design: the mapping is a property of CPV's
# own agent roster, not of the scanned folder, so deriving it per-run would be
# re-deciding a constant. A model is not needed to route a finding class.
_FIXERS: dict[str, str] = {
    "plugin": "cpv-plugin-fixer-agent — structural, manifest, component and doc findings",
    "skill": "cpv-plugin-fixer-agent — SKILL.md frontmatter, sections, description quality",
    "security": (
        "cpv-plugin-leaks-preventer-agent for LEAK / missing-safeguard findings; "
        "cpv-plugin-devitalizer-agent for execution-class findings that are detector "
        "signatures rather than live code"
    ),
    "cache": "cpv-cache-optimizer-agent — CA-01..CA-07 prompt-cache invalidation",
    "marketplace": "cpv-marketplace-fixer-agent — manifest, layout and architecture migration",
}


def is_remote_spec(raw: str) -> bool:
    """True when the argument names a remote repo rather than a local folder.

    A local path that EXISTS always wins, so a directory literally named like a
    slug is never mistaken for a repo.
    """
    if Path(raw).expanduser().exists():
        return False
    return bool(re.match(r"^https?://", raw, re.IGNORECASE)) or bool(
        re.match(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", raw)
    )


def resolve_target(raw: str | None) -> Path:
    """Resolve the folder to scan: explicit argument, else the project root.

    ``$CLAUDE_PROJECT_DIR`` is the harness's own answer for "the current
    project" and is preferred over ``cwd`` because a skill may be invoked from
    a subdirectory, where cwd would silently scan a fragment of the plugin and
    report it clean.
    """
    if raw:
        return Path(raw).expanduser().resolve()
    env_root = os.environ.get("CLAUDE_PROJECT_DIR")
    return Path(env_root).expanduser().resolve() if env_root else Path.cwd().resolve()


def detect_shape(target: Path) -> str:
    """Classify the folder so only applicable scans run.

    Returns ``plugin``, ``marketplace``, ``skill`` or ``unknown``. Shape drives
    which modes run; an unknown shape still gets the security scan, because a
    folder CPV cannot classify is exactly the one whose contents are unvetted.
    """
    if (target / ".claude-plugin" / "plugin.json").is_file():
        return "plugin"
    if (target / ".claude-plugin" / "marketplace.json").is_file():
        return "marketplace"
    if (target / "SKILL.md").is_file():
        return "skill"
    return "unknown"


def modes_for(shape: str) -> list[str]:
    """The scan set for a shape.

    ``security`` runs for every shape — it is the one scan whose absence is
    dangerous rather than merely incomplete.

    ``cache`` is deliberately NEVER listed. It is plugin-scoped: it raises
    CRITICAL on any folder without ``.claude-plugin/plugin.json``
    (validate_cache.py:1023), so on a skill or unknown folder it reports an
    inapplicability as a finding. Where a plugin.json DOES exist, ``plugin``
    mode already runs the same CA-01..CA-07 audit in-process, so a separate
    pass would be a duplicate. Both branches are wrong; there is no third.
    """
    if shape == "plugin":
        return ["plugin", "security"]
    if shape == "marketplace":
        return ["marketplace", "security"]
    if shape == "skill":
        return ["skill", "security"]
    return ["security"]


def run_mode(mode: str, target: Path, out: Path) -> tuple[int, str]:
    """Run ONE validator mode through the isolating launcher.

    Returns ``(exit_code, stdout)``. A non-zero code is a VERDICT (findings),
    not a crash — the caller distinguishes them by whether the code is in the
    0..4 vocabulary.
    """
    cmd = [
        sys.executable,
        str(_LAUNCHER),
        mode,
        str(target),
        "-o",
        str(out),
    ]
    if mode in {"plugin", "skill"}:
        cmd.append("--strict")
    proc = subprocess.run(  # noqa: S603 - fixed argv, no shell
        cmd,
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.returncode, proc.stdout + proc.stderr


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="cpv-validate-plugin-folder",
        description="Read-only full-coverage scan of one plugin/skill folder.",
    )
    parser.add_argument(
        "target",
        nargs="?",
        help="Folder to scan. Defaults to $CLAUDE_PROJECT_DIR, else the current directory.",
    )
    args = parser.parse_args()

    sandbox: str | None = None
    origin: str | None = None
    try:
        if args.target and is_remote_spec(args.target):
            # Reuse the pre-install-scan fetch path rather than cloning here:
            # it already normalises GitHub/GitLab URLs and owner/repo slugs,
            # strips the clone's .git, and never touches the plugin cache. A
            # second clone implementation would be a second thing to keep
            # correct.
            from cpv_pre_install_scan import _fetch_target  # noqa: PLC0415

            sandbox = tempfile.mkdtemp(prefix="cpv-folder-scan-")
            origin = args.target
            try:
                target, _ = _fetch_target(args.target, Path(sandbox))
            except Exception as exc:  # noqa: BLE001 - surface any fetch failure verbatim
                print(f"✗ Could not fetch {args.target!r}: {exc}", file=sys.stderr)
                return EXIT_CRITICAL
        else:
            target = resolve_target(args.target)

        if not target.is_dir():
            print(f"✗ Not a directory: {target}", file=sys.stderr)
            return EXIT_CRITICAL

        shape = detect_shape(target)

        # A cloned repo MUST be a plugin or a skill. Scanning an arbitrary
        # repository would report security findings about code that was never
        # a Claude Code component, which reads as a verdict on something it is
        # not. A local folder keeps the permissive path: the caller can
        # legitimately point this at a work-in-progress tree.
        if origin is not None and shape not in {"plugin", "skill"}:
            print(
                f"✗ {origin} is not a plugin or skill project "
                f"(no .claude-plugin/plugin.json and no SKILL.md at its root).",
                file=sys.stderr,
            )
            return EXIT_CRITICAL

        modes = modes_for(shape)

        report_path = build_report_path("cpv-validate-plugin-folder", f"scan-{target.name}")
        parts: list[str] = [
            f"# CPV full folder scan — `{target.name}`\n",
            f"- **Target:** `{target}`\n- **Shape:** {shape}\n"
            f"- **Modes:** {', '.join(modes)}\n- **Mode:** READ-ONLY (no file in the target is modified)\n",
        ]

        per_mode: dict[str, int] = {}
        failures: list[str] = []

        for mode in modes:
            sub = report_path.with_name(f"{report_path.stem}.{mode}.md")
            code, output = run_mode(mode, target, sub)
            per_mode[mode] = code
            if code not in _SEVERITY_RANK:
                # Outside the verdict vocabulary ⇒ the scanner could not run.
                # "Cannot check" is not "clean": surface it instead of folding it
                # into a pass.
                failures.append(f"{mode} (exit {code})")
            parts.append(f"\n---\n\n## Mode: `{mode}` (exit {code})\n")
            if sub.is_file():
                parts.append(_ANSI_RE.sub("", sub.read_text(encoding="utf-8", errors="replace")))
                sub.unlink()
            else:
                parts.append(f"\n```\n{_ANSI_RE.sub('', output.strip())}\n```\n")

        report_path.write_text("".join(parts), encoding="utf-8")

        worst = EXIT_OK
        for code in per_mode.values():
            if _SEVERITY_RANK.get(code, 0) > _SEVERITY_RANK[worst]:
                worst = code

        print(f"\nTarget:  {target}")
        print(f"Shape:   {shape}")
        print("\n| # | Scan | Exit | Verdict |")
        print("|---|------|------|---------|")
        names = {
            EXIT_OK: "clean",
            EXIT_CRITICAL: "CRITICAL",
            EXIT_MAJOR: "MAJOR",
            EXIT_MINOR: "MINOR",
            EXIT_NIT: "NIT",
        }
        for i, (mode, code) in enumerate(per_mode.items(), 1):
            print(f"| {i} | {mode} | {code} | {names.get(code, 'COULD-NOT-RUN')} |")

        if failures:
            print(f"\n⚠ COULD NOT RUN: {', '.join(failures)} — this is UNKNOWN, not clean.")

        print(f"\nReport:  {report_path}")

        if worst == EXIT_OK and not failures:
            print("\n✓ No findings. Nothing to fix.")
            return EXIT_OK

        print("\nTo fix — dispatch the agent for the scan that flagged:")
        for i, (mode, code) in enumerate(
            [(m, c) for m, c in per_mode.items() if c != EXIT_OK], 1
        ):
            print(f"  {i}. {mode} ({names.get(code, 'could-not-run')}) → {_FIXERS.get(mode, 'cpv-plugin-fixer-agent')}")
        print("\n  Or run the whole loop: /cpv-batch-full-scan-and-fix <path>")

        return worst
    finally:
        if sandbox:
            # The clone is ephemeral by contract. Removed on EVERY exit path,
            # including the early returns above, which is why it is a finally
            # and not a trailing call.
            shutil.rmtree(sandbox, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
