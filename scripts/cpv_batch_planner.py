#!/usr/bin/env python3
"""cpv_batch_planner — slice a validation report into parallel-fix shards.

Zero-LLM-cost planner that reads a plugin's validation report (via
``validate_plugin.py --json``), groups findings by file, and writes N
shard-manifest JSON files plus a top-level ``index.json``. The
``/cpv-batch-fix`` slash command consumes these manifests and dispatches
N ``plugin-fixer`` agents in parallel (one per shard) — each agent gets
a fresh context window (size depends on which model ``plugin-fixer``
runs: bare opus/sonnet = 200K, the [1m] variants = 1M, future models
may differ) and only sees its own shard. The recommended shard size
keeps each shard under ~50% of the model's context to avoid the
performance drop above that threshold.

Design — TRDD-71e68ab5 (`design/tasks/TRDD-20260519_114050+0200-71e68ab5-batch-fix-parallel-sharding.md`).

Usage::

    python3 scripts/cpv_batch_planner.py <plugin-path> [options]

Options:

    --shard-size N        max findings per shard (default 30)
    --max-parallel N      cap concurrent shards (default 8, max 16)
    --session-dir PATH    output dir (default /tmp/cpv-batch/<ts>/)
    --min-severity LEVEL  drop findings below this floor (default minor)
    --report PATH         use existing JSON validation report instead of
                          running validate_plugin.py
    --no-color            forwarded to validate_plugin.py

Output:

    <session-dir>/index.json            — top-level index of all shards
    <session-dir>/shard-{1..N}.json     — per-shard manifests

Each shard manifest lists ONLY the findings for that shard's files.
The planner enforces "one file → one shard" so two parallel fixers
never edit the same file.

Exit code: 0 on success, 1 on any planning error.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SEVERITY_ORDER = {"CRITICAL": 0, "MAJOR": 1, "MINOR": 2, "NIT": 3, "WARNING": 4, "INFO": 5, "PASSED": 6}

# DEFAULT_SHARD_SIZE is calibrated for ``plugin-fixer``'s current default
# model (bare ``opus``, 200K context) and keeps each shard well under the
# ~50% utilisation threshold above which model quality begins to degrade.
# When ``plugin-fixer.model`` is upgraded to a 1M-context variant
# (``opus[1m]`` / ``sonnet[1m]``) raise this with ``--shard-size`` to
# ~50-75 (still <50% of 1M). Future models with different context
# windows will need their own tuning.
#
# v2.98.0: lowered from 30 → 15. Lower ceilings mean batch mode triggers
# earlier — better context safety, finer parallelism, faster wall-clock
# on medium-finding-count plugins. The previous 30 left less headroom
# for per-finding work (skim → diagnose → fix → re-validate cycles can
# consume 3-5K tokens each, so 30 findings @ 5K = 150K tokens, dangerously
# close to the 100K safe-ceiling on bare opus).
DEFAULT_SHARD_SIZE = 15
DEFAULT_MAX_PARALLEL = 8
MAX_PARALLEL_CAP = 16
SCHEMA_VERSION = 2  # v2 — scope-based ownership instead of file list

# Scope kinds — control how much the shard agent may refactor within its
# ownership scope. ``skill_dir`` grants the agent full edit/create/delete
# rights inside the named skill directory AND the right to create sibling
# skill directories when splitting an oversized SKILL.md into smaller
# focused skills. ``file`` is the conservative scope: the agent may edit
# only that single file (no create/delete of siblings).
SCOPE_KIND_SKILL_DIR = "skill_dir"
SCOPE_KIND_FILE = "file"


@dataclass
class Finding:
    level: str
    message: str
    file: str | None
    line: int | None

    def to_dict(self) -> dict[str, Any]:
        return {"level": self.level, "message": self.message, "file": self.file, "line": self.line}


@dataclass
class Scope:
    """A unit of ownership for one shard.

    ``scope_path`` is either:

    * A directory (when ``scope_kind == "skill_dir"``) — the shard owns
      everything inside that directory tree AND may create new sibling
      skill dirs when refactoring (e.g. splitting an oversized SKILL.md
      into multiple focused skills). New sibling skills must use a name
      prefix derived from the original (e.g. ``skills/foo/`` →
      ``skills/foo-a/`` + ``skills/foo-b/``) so they cannot collide with
      another shard's scope.
    * A single file (when ``scope_kind == "file"``) — the shard may only
      edit that file. No create/delete of siblings.

    Backwards-compat alias: ``file_path`` returns ``scope_path`` for
    consumers that still use the v1 field name.
    """

    scope_path: str
    scope_kind: str  # SCOPE_KIND_SKILL_DIR | SCOPE_KIND_FILE
    findings: list[Finding] = field(default_factory=list)

    @property
    def file_path(self) -> str:
        """v1 compat alias — drop after v2 settles."""
        return self.scope_path

    @property
    def count(self) -> int:
        return len(self.findings)


# v1 alias — keep ``FileGroup`` callable from existing tests while we
# transition. Drop after the next minor release if no tests still use it.
FileGroup = Scope


@dataclass
class Shard:
    shard_id: int
    scopes: list[Scope] = field(default_factory=list)

    @property
    def files(self) -> list[Scope]:  # v1 compat alias
        return self.scopes

    @property
    def finding_count(self) -> int:
        return sum(f.count for f in self.files)


def run_validate_plugin(plugin_path: Path) -> dict[str, Any]:
    """Run validate_plugin.py --json on the given plugin and return parsed output.

    Honors ``PLUGIN_SKIP_GITHUB_INTEGRITY=1`` in the caller's environment
    so dev checkouts with locally-modified files still produce a report.
    """
    repo_root = Path(__file__).resolve().parent.parent
    validate_script = repo_root / "scripts" / "validate_plugin.py"
    if not validate_script.exists():
        raise FileNotFoundError(f"validate_plugin.py not found at {validate_script}")
    cmd = [sys.executable, str(validate_script), str(plugin_path), "--json", "--no-color"]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=600)
    if not result.stdout.strip():
        raise RuntimeError(
            f"validate_plugin.py produced no JSON output (exit={result.returncode}). stderr: {result.stderr[:500]}"
        )
    # validate_plugin --json prefixes its JSON with non-JSON pipeline output
    # (repo-lint banners, etc.). The JSON object is the final block — find
    # the first `{` after the last blank/banner line and parse from there.
    return _parse_validate_plugin_json(result.stdout)


def _parse_validate_plugin_json(stdout: str) -> dict[str, Any]:
    """Pull the JSON object out of validate_plugin --json's mixed stdout.

    Repo-lint banners are emitted before the JSON dump. The JSON itself
    starts with a top-level ``{`` followed by ``"exit_code":``. Find that
    marker and parse from there to the end.
    """
    marker = '{\n  "exit_code"'
    idx = stdout.find(marker)
    if idx < 0:
        # Try alternate marker without indent
        marker = '{"exit_code"'
        idx = stdout.find(marker)
    if idx < 0:
        raise RuntimeError(
            "validate_plugin.py output does not contain expected JSON marker "
            "('\"exit_code\"'). Truncated stdout:\n" + stdout[:500]
        )
    try:
        parsed: dict[str, Any] = json.loads(stdout[idx:])
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"validate_plugin.py JSON output unparseable at offset {idx}: {exc}") from exc
    return parsed


def load_report(report_path: Path) -> dict[str, Any]:
    """Load a pre-existing JSON validation report from disk."""
    if not report_path.exists():
        raise FileNotFoundError(f"Report not found: {report_path}")
    parsed: dict[str, Any] = json.loads(report_path.read_text())
    return parsed


def filter_findings(results: list[dict[str, Any]], min_severity: str) -> list[Finding]:
    """Filter raw report results down to actionable findings at or above ``min_severity``.

    Drops INFO/PASSED rows unconditionally. Drops anything WITHOUT a ``file:``
    reference (those are plugin-wide findings the shard fixer can't route
    to a specific file).
    """
    floor = SEVERITY_ORDER.get(min_severity.upper(), SEVERITY_ORDER["MINOR"])
    findings: list[Finding] = []
    for r in results:
        level = r.get("level")
        if not isinstance(level, str):
            continue
        rank = SEVERITY_ORDER.get(level.upper(), 99)
        if rank > floor:
            continue
        file_ref = r.get("file")
        if not file_ref:
            continue
        findings.append(
            Finding(
                level=level.upper(),
                message=r.get("message", ""),
                file=str(file_ref),
                line=r.get("line"),
            )
        )
    return findings


def derive_scope(file_path: str) -> tuple[str, str]:
    """Derive ``(scope_path, scope_kind)`` from a finding's file reference.

    Findings inside a skill directory get a ``skill_dir`` scope spanning
    the whole skill — this grants the shard agent the right to refactor
    freely (split a too-big SKILL.md, externalise content to
    ``references/``, create new sibling skills with prefix-matched names).
    Every other path is a ``file`` scope: only that file may be edited.
    """
    parts = file_path.split("/")
    if len(parts) >= 2 and parts[0] == "skills" and parts[1]:
        return (f"skills/{parts[1]}/", SCOPE_KIND_SKILL_DIR)
    return (file_path, SCOPE_KIND_FILE)


def group_by_scope(findings: list[Finding]) -> list[Scope]:
    """Group findings by ``derive_scope()``. One Scope per distinct scope path.

    Replaces v1's ``group_by_file``: in v2 a scope can cover multiple
    files in the same skill directory (the planner unifies them so a
    single shard owns the whole skill, including refactoring rights).
    """
    by_scope: dict[str, Scope] = {}
    for f in findings:
        assert f.file is not None
        scope_path, scope_kind = derive_scope(f.file)
        if scope_path not in by_scope:
            by_scope[scope_path] = Scope(scope_path=scope_path, scope_kind=scope_kind)
        by_scope[scope_path].findings.append(f)
    # Stable order: by descending finding count, then alphabetical
    return sorted(by_scope.values(), key=lambda s: (-s.count, s.scope_path))


# v1 alias for existing callers / tests
def group_by_file(findings: list[Finding]) -> list[Scope]:
    """v1 compat — forwards to ``group_by_scope`` so test fixtures keep working."""
    return group_by_scope(findings)


def shard_groups(scopes: list[Scope], shard_size: int) -> list[Shard]:
    """Pack scopes into shards using a greedy First-Fit-Decreasing heuristic.

    Each shard holds up to ``shard_size`` findings. A single scope's
    findings always stay together (never split across shards) — that's
    the whole point of scope-based ownership. If a single scope has
    more than ``shard_size`` findings, it gets its own oversized shard
    and a warning is emitted to stderr.

    Uses First-Fit Decreasing: scopes sorted by descending count, each
    placed in the first shard with room, else a new shard.
    """
    shards: list[Shard] = []
    next_id = 1
    for sc in scopes:  # already sorted descending in group_by_scope()
        if sc.count > shard_size:
            print(
                f"warning: scope {sc.scope_path} has {sc.count} findings — "
                f"exceeds shard_size {shard_size}; placing in its own oversized shard",
                file=sys.stderr,
            )
            shards.append(Shard(shard_id=next_id, scopes=[sc]))
            next_id += 1
            continue
        placed = False
        for s in shards:
            if s.finding_count + sc.count <= shard_size:
                s.scopes.append(sc)
                placed = True
                break
        if not placed:
            shards.append(Shard(shard_id=next_id, scopes=[sc]))
            next_id += 1
    return shards


def make_session_dir(explicit: Path | None) -> Path:
    """Resolve the session output directory.

    If ``explicit`` is given, use it verbatim. Otherwise build
    ``/tmp/cpv-batch/<YYYYMMDD_HHMMSS±HHMM>/`` per the report-location rule.
    """
    if explicit is not None:
        session_dir = explicit
    else:
        ts = time.strftime("%Y%m%d_%H%M%S") + time.strftime("%z")
        tmp_root = Path(os.environ.get("TMPDIR", "/tmp")).resolve()
        session_dir = tmp_root / "cpv-batch" / ts
    session_dir.mkdir(parents=True, exist_ok=True)
    return session_dir


def write_shard_manifest(
    shard: Shard,
    plugin_path: Path,
    report_source: str,
    session_dir: Path,
    total_shards: int,
    shard_size: int,
) -> Path:
    """Write one shard's manifest JSON and return its absolute path."""
    manifest_path = session_dir / f"shard-{shard.shard_id}.json"
    status_path = session_dir / f"shard-{shard.shard_id}.status.json"
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "shard_id": shard.shard_id,
        "shard_of": total_shards,
        "plugin_path": str(plugin_path.resolve()),
        "report_source": report_source,
        "max_findings": shard_size,
        "status_path": str(status_path),
        "scopes": [
            {
                "scope_path": sc.scope_path,
                "scope_kind": sc.scope_kind,
                "finding_count": sc.count,
                "findings": [f.to_dict() for f in sc.findings],
            }
            for sc in shard.scopes
        ],
        # v1 compat alias — readers that only know about ``files`` see the
        # same data (with ``path`` as ``scope_path`` for back-compat).
        "files": [
            {
                "path": sc.scope_path,
                "scope_kind": sc.scope_kind,
                "finding_count": sc.count,
                "findings": [f.to_dict() for f in sc.findings],
            }
            for sc in shard.scopes
        ],
    }
    manifest_path.write_text(json.dumps(manifest, indent=2))
    return manifest_path


def write_index(
    session_dir: Path,
    plugin_path: Path,
    report_source: str,
    shards: list[Shard],
    manifest_paths: list[Path],
    shard_size: int,
    max_parallel: int,
    counts: dict[str, int],
) -> Path:
    """Write the top-level ``index.json`` consumed by the slash command."""
    index_path = session_dir / "index.json"
    payload = {
        "schema_version": SCHEMA_VERSION,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S") + time.strftime("%z"),
        "plugin_path": str(plugin_path.resolve()),
        "report_source": report_source,
        "shard_count": len(shards),
        "max_parallel": min(max_parallel, len(shards)) if shards else 0,
        "shard_size": shard_size,
        "total_findings": sum(s.finding_count for s in shards),
        "counts_by_severity": counts,
        "shards": [
            {
                "shard_id": s.shard_id,
                "scope_count": len(s.scopes),
                "file_count": len(s.scopes),  # v1 alias
                "finding_count": s.finding_count,
                "manifest_path": str(mp),
                "status_path": str(session_dir / f"shard-{s.shard_id}.status.json"),
            }
            for s, mp in zip(shards, manifest_paths, strict=True)
        ],
    }
    index_path.write_text(json.dumps(payload, indent=2))
    return index_path


def count_by_severity(findings: list[Finding]) -> dict[str, int]:
    counts = {"CRITICAL": 0, "MAJOR": 0, "MINOR": 0, "NIT": 0, "WARNING": 0}
    for f in findings:
        if f.level in counts:
            counts[f.level] += 1
    return counts


def plan(
    plugin_path: Path,
    *,
    shard_size: int,
    max_parallel: int,
    min_severity: str,
    session_dir: Path | None,
    report_path: Path | None,
) -> dict[str, Any]:
    """Top-level entry point used by both the CLI and tests."""
    if shard_size < 1:
        raise ValueError(f"shard_size must be >= 1, got {shard_size}")
    if max_parallel < 1 or max_parallel > MAX_PARALLEL_CAP:
        raise ValueError(f"max_parallel must be 1..{MAX_PARALLEL_CAP}, got {max_parallel}")
    if not plugin_path.exists() or not plugin_path.is_dir():
        raise FileNotFoundError(f"plugin path does not exist or is not a directory: {plugin_path}")

    if report_path is not None:
        raw = load_report(report_path)
        report_source = str(report_path.resolve())
    else:
        raw = run_validate_plugin(plugin_path)
        report_source = f"validate_plugin.py({plugin_path})"

    results = raw.get("results", [])
    if not isinstance(results, list):
        raise RuntimeError("validation report missing 'results' list")
    findings = filter_findings(results, min_severity)
    counts = count_by_severity(findings)

    out_dir = make_session_dir(session_dir)

    if not findings:
        index_path = write_index(out_dir, plugin_path, report_source, [], [], shard_size, max_parallel, counts)
        return {
            "session_dir": str(out_dir),
            "index_path": str(index_path),
            "shard_count": 0,
            "total_findings": 0,
            "counts": counts,
        }

    scopes = group_by_scope(findings)
    shards = shard_groups(scopes, shard_size)
    manifest_paths = [
        write_shard_manifest(s, plugin_path, report_source, out_dir, len(shards), shard_size) for s in shards
    ]
    index_path = write_index(
        out_dir, plugin_path, report_source, shards, manifest_paths, shard_size, max_parallel, counts
    )
    return {
        "session_dir": str(out_dir),
        "index_path": str(index_path),
        "shard_count": len(shards),
        "total_findings": len(findings),
        "counts": counts,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Slice a plugin's validation findings into parallel-fix shards.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("plugin_path", type=Path, help="Path to the plugin root to plan a batch fix for")
    parser.add_argument(
        "--shard-size",
        type=int,
        default=DEFAULT_SHARD_SIZE,
        help=f"Max findings per shard (default {DEFAULT_SHARD_SIZE})",
    )
    parser.add_argument(
        "--max-parallel",
        type=int,
        default=DEFAULT_MAX_PARALLEL,
        help=f"Max concurrent shards (default {DEFAULT_MAX_PARALLEL}, cap {MAX_PARALLEL_CAP})",
    )
    parser.add_argument(
        "--session-dir", type=Path, default=None, help="Output dir (default /tmp/cpv-batch/<timestamp>/)"
    )
    parser.add_argument(
        "--min-severity",
        default="minor",
        choices=["critical", "major", "minor", "nit", "warning"],
        help="Drop findings below this severity floor (default minor)",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=None,
        help="Use existing JSON validation report instead of running validate_plugin.py",
    )
    args = parser.parse_args(argv)

    try:
        out = plan(
            args.plugin_path,
            shard_size=args.shard_size,
            max_parallel=args.max_parallel,
            min_severity=args.min_severity,
            session_dir=args.session_dir,
            report_path=args.report,
        )
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
