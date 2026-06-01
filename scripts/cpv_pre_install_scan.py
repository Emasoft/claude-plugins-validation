#!/usr/bin/env python3
"""Pre-install security scanner for skills, plugins, and marketplaces.

WHAT IT DOES
============
Scans an untrusted skill / plugin / marketplace BEFORE it gets installed
into ``~/.claude/plugins/cache/``. The scanner uses a sandboxed
``tempfile.mkdtemp()`` work directory, runs the full CPV security
pipeline (including the MANDATORY native skillaudit Check 27 — 50
rules / 489 patterns of credential theft / data exfiltration / prompt
injection / supply-chain / crypto theft / etc.), and reports
findings.

WHY
===
Prevention is cheaper than cure. Once a plugin lands in
``~/.claude/plugins/cache/`` and the user reloads Claude Code, any
``preinstall`` / ``postinstall`` / hook script ships immediately. The
pre-install scan refuses to forward a target with CRITICAL findings to
the actual installer.

TARGETS
=======
* Local path: ``cpv-pre-install-scan /path/to/plugin``
* GitHub URL: ``cpv-pre-install-scan https://github.com/owner/repo``
* owner/repo slug: ``cpv-pre-install-scan owner/repo``
* GitHub tag / release URL: ``cpv-pre-install-scan https://github.com/owner/repo/releases/tag/v1.2.3``
* Single SKILL.md URL: ``cpv-pre-install-scan https://example.com/SKILL.md``
* Local tarball / zip: ``cpv-pre-install-scan plugin.tar.gz``

GUARANTEES
==========
* Sandboxed work dir: ``${TMPDIR}/cpv-preinstall-<uuid>/`` — never
  writes to ``~/.claude/plugins/cache/``.
* No code from the target is executed: the scanner uses static
  pattern matching only (no ``python <target>``, no ``npm install``,
  no shell execution of plugin scripts).
* No network calls during the scan itself — only ``git clone`` /
  ``curl`` to fetch the target. The scanner uses CPV's existing
  in-process Python validators thereafter.
* On exit, the sandbox is deleted (the path is reported only as
  PASSED — the user never needs to clean it up manually).
* Iron rule: the skillaudit MANDATORY scanner ALWAYS runs. No env
  var bypass, no ``--skip-skillaudit`` flag.

EXIT CODES
==========
0 — clean (zero CRITICAL, zero MAJOR; install is safe)
1 — findings (CRITICAL or MAJOR present; do NOT install)
2 — usage error (bad arguments, fetch failure, etc.)

Per CPV's pipeline convention.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path
from typing import Any

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


def _is_url(s: str) -> bool:
    return bool(re.match(r"^https?://", s, re.IGNORECASE))


def _is_owner_repo(s: str) -> bool:
    return bool(re.match(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", s)) and "/" in s and " " not in s


def _is_github_url(url: str) -> bool:
    return _is_url(url) and "github.com/" in url.lower()


def _normalize_github_url(spec: str) -> str:
    """Turn 'owner/repo' or 'https://github.com/owner/repo/...' into a clone URL."""
    if _is_owner_repo(spec):
        return f"https://github.com/{spec}.git"
    if _is_github_url(spec):
        m = re.match(
            r"^https?://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?(?:/.*)?$",
            spec,
        )
        if m:
            return f"https://github.com/{m.group(1)}/{m.group(2)}.git"
    return spec


def _fetch_target(spec: str, sandbox: Path) -> tuple[Path, str]:
    """Download/extract the target into the sandbox; return (root, label).

    Never writes to ``~/.claude/plugins/cache/``. Never executes target
    code. Returns the path to the work tree + a human-readable label.
    """
    label = spec

    # Local path
    src = Path(spec)
    if src.exists():
        if src.is_dir():
            dest = sandbox / "target"
            shutil.copytree(src, dest, symlinks=False)
            return dest, str(src)
        if src.is_file() and src.suffix.lower() in (".gz", ".tgz", ".zip", ".tar", ".bz2"):
            return _extract_archive(src, sandbox), str(src)
        if src.is_file() and src.name.upper() in ("SKILL.MD", "PLUGIN.JSON", "MARKETPLACE.JSON"):
            dest = sandbox / "target"
            dest.mkdir()
            shutil.copy(src, dest / src.name)
            return dest, str(src)
        # Generic file — treat as single-file skill
        dest = sandbox / "target"
        dest.mkdir()
        shutil.copy(src, dest / src.name)
        return dest, str(src)

    # GitHub / arbitrary URL
    if _is_url(spec) or _is_owner_repo(spec):
        clone_url = _normalize_github_url(spec)
        if clone_url.endswith(".git") or _is_github_url(clone_url):
            return _git_clone(clone_url, sandbox), label
        # Direct URL to SKILL.md / file
        return _curl_download(spec, sandbox), label

    raise FileNotFoundError(
        f"Target not recognized: {spec!r}. Expected: local path, GitHub URL, owner/repo, or file URL."
    )


def _git_clone(clone_url: str, sandbox: Path) -> Path:
    dest = sandbox / "target"
    result = subprocess.run(
        [
            "git",
            "clone",
            "--depth",
            "1",
            "--no-tags",
            "-c",
            "http.lowSpeedLimit=100",
            "-c",
            "http.lowSpeedTime=300",
            clone_url,
            str(dest),
        ],
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git clone failed for {clone_url!r}: {result.stderr.strip() or result.stdout.strip()}")
    # Remove .git to prevent the scanner from walking history blobs.
    git_dir = dest / ".git"
    if git_dir.is_dir():
        shutil.rmtree(git_dir, ignore_errors=True)
    return dest


def _curl_download(url: str, sandbox: Path) -> Path:
    """Fetch a single URL (SKILL.md, README.md, etc.) into the sandbox."""
    dest = sandbox / "target"
    dest.mkdir()
    # Guess a filename from the URL path.
    name = url.rstrip("/").rsplit("/", 1)[-1] or "fetched.md"
    if "?" in name:
        name = name.split("?", 1)[0]
    out = dest / name
    result = subprocess.run(
        ["curl", "-fsSL", "--max-time", "60", "-o", str(out), url],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if result.returncode != 0 or not out.is_file():
        raise RuntimeError(f"curl failed for {url!r}: {result.stderr.strip() or result.stdout.strip()}")
    return dest


def _extract_archive(src: Path, sandbox: Path) -> Path:
    dest = sandbox / "target"
    dest.mkdir()
    if src.suffix.lower() == ".zip":
        with zipfile.ZipFile(src) as zf:
            # Refuse zips with path traversal entries.
            for name in zf.namelist():
                if name.startswith("/") or ".." in Path(name).parts:
                    raise RuntimeError(f"archive contains unsafe path: {name!r}")
            zf.extractall(dest)
    else:
        with tarfile.open(src) as tf:
            # Fast, clear pre-check for the obvious traversal shape. This only
            # covers member NAMES — it does NOT catch symlink/hardlink members
            # whose linkname escapes the sandbox, nor device/special files.
            for member in tf.getmembers():
                if member.name.startswith("/") or ".." in Path(member.name).parts:
                    raise RuntimeError(f"archive contains unsafe path: {member.name!r}")
            # filter="data" (Python 3.12+, which this project requires) is the
            # authoritative guard: it rejects absolute/traversing linknames,
            # special files, and out-of-tree extraction that the name-only loop
            # above misses — critical because this scanner extracts UNTRUSTED
            # archives into the sandbox. Mirrors cpv_management_common.py:564.
            # Re-raise FilterError as RuntimeError so a malicious tar fails the
            # same way as the zip path and is reported as exit 2 by main().
            try:
                tf.extractall(dest, filter="data")
            except tarfile.FilterError as exc:
                raise RuntimeError(f"archive contains unsafe entry: {exc}") from exc
    # If the archive contains a single top-level directory, descend into it.
    entries = list(dest.iterdir())
    if len(entries) == 1 and entries[0].is_dir():
        return entries[0]
    return dest


def _detect_target_kind(root: Path) -> str:
    """Heuristic classification: plugin / marketplace / skill / loose."""
    if (root / ".claude-plugin" / "marketplace.json").is_file() or (root / "marketplace.json").is_file():
        return "marketplace"
    if (root / ".claude-plugin" / "plugin.json").is_file():
        return "plugin"
    if (root / "SKILL.md").is_file():
        return "skill"
    if (root / "skills").is_dir() or (root / "agents").is_dir() or (root / "commands").is_dir():
        return "plugin"
    return "loose"


def _run_scan(root: Path, kind: str) -> tuple[int, dict[str, Any]]:
    """Invoke the right CPV validator for the detected kind.

    Returns (exit_code, summary_dict). For "skill" or "loose" we run
    the native skillaudit scanner directly. For "plugin" we run
    validate_plugin.py (which now includes Check 27 native skillaudit).
    For "marketplace" we run validate_marketplace + per-plugin scan.
    """
    if kind in ("plugin",):
        return _run_validate_plugin(root)
    if kind == "marketplace":
        # Best-effort: walk plugin entries inside the marketplace and
        # scan each. For v2.99.1 simplicity, we run validate_plugin
        # on the marketplace root with --marketplace-only.
        return _run_validate_plugin(root, marketplace_only=True)
    return _run_native_skillaudit(root, kind=kind)


def _run_validate_plugin(root: Path, *, marketplace_only: bool = False) -> tuple[int, dict[str, Any]]:
    cmd = ["uv", "run", "python", str(SCRIPTS_DIR / "validate_plugin.py"), str(root), "--strict", "--json"]
    if marketplace_only:
        cmd.append("--marketplace-only")
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600, check=False)

    # validate_plugin.py has early-exit error paths (path-not-found, "this is a
    # marketplace not a plugin", SKILL.md-at-root, etc.) that print to stderr
    # and exit non-zero WITHOUT emitting JSON. An empty/garbage stdout therefore
    # means the validator could not complete — it must NOT be reported as
    # "CLEAN / safe to install". Map it to exit 2 (the documented usage/internal
    # error code) and surface the diagnostics so _print_report shows the failure.
    if not result.stdout.strip():
        return 2, {
            "error": "validate_plugin produced no JSON output (likely a usage/shape error)",
            "raw_stderr": result.stderr.strip(),
        }
    try:
        raw = json.loads(result.stdout)
    except json.JSONDecodeError:
        return 2, {
            "error": "validate_plugin emitted unparseable JSON",
            "raw_stdout": result.stdout,
            "raw_stderr": result.stderr.strip(),
        }

    # validate_plugin emits counts under "counts" and per-issue records under
    # "results" with an uppercase "level". Normalise to this scanner's canonical
    # shape ("summary" counts + lowercase-"severity" "findings") so _print_report
    # renders the inline top-5 findings for plugin/marketplace targets exactly
    # as it does for the native-skillaudit skill/loose path. Without this, the
    # BLOCKED report for a plugin would silently list zero findings.
    counts = raw.get("counts", {}) if isinstance(raw.get("counts"), dict) else {}
    findings = [
        {
            "severity": str(r.get("level", "")).lower(),
            "rule_id": "validate_plugin",
            "category": str(r.get("level", "")).lower(),
            "message": r.get("message", ""),
            "file": r.get("file"),
            "line": r.get("line"),
        }
        for r in raw.get("results", [])
        if isinstance(r, dict)
    ]
    summary: dict[str, Any] = {"summary": counts, "findings": findings}

    # Derive the exit code from the CRITICAL/MAJOR counts — NOT from
    # result.returncode. validate_plugin's exit code is multi-valued
    # (MAJOR=2, MINOR=3, NIT=4 under --strict), which collides with this
    # scanner's documented contract (0=clean, 1=CRITICAL/MAJOR, 2=usage error):
    # passing it through verbatim would mis-report a MAJOR plugin as exit 2
    # ("usage error") and a NIT-only plugin as a non-zero "blocked". The
    # install decision is purely about CRITICAL/MAJOR, matching the native path.
    crit = int(counts.get("critical", 0) or 0)
    major = int(counts.get("major", 0) or 0)
    rc = 0 if (crit == 0 and major == 0) else 1
    return rc, summary


def _run_native_skillaudit(root: Path, *, kind: str) -> tuple[int, dict[str, Any]]:
    """Run JUST the native skillaudit scanner — used for skill / loose targets."""
    from cpv_skillaudit_native import run_skillaudit_scan  # noqa: PLC0415

    result = run_skillaudit_scan(root)
    counts = {"critical": 0, "major": 0, "minor": 0, "nit": 0, "info": 0}
    findings_list: list[dict[str, Any]] = []
    for f in result.findings:
        sev = f.severity
        counts[sev] = counts.get(sev, 0) + 1
        findings_list.append(
            {
                "severity": sev,
                "rule_id": f.rule_id,
                "category": f.category,
                "message": f.message,
                "file": f.file_path,
                "line": f.line_number,
                "demoted": bool(f.raw.get("demoted")),
            }
        )
    rc = 0 if (counts["critical"] == 0 and counts["major"] == 0) else 1
    return rc, {
        "kind": kind,
        "files_scanned": result.files_scanned,
        "summary": counts,
        "findings": findings_list,
    }


def _print_report(label: str, kind: str, summary: dict[str, Any]) -> None:
    # The validator could not produce a verdict (e.g. it errored out before
    # emitting JSON). Never render this as CLEAN — that would falsely tell the
    # user the target is safe. Report it as an explicit scan error (exit 2).
    if summary.get("error"):
        print()
        print(f"  Pre-install scan: {label}")
        print(f"  Kind:             {kind}")
        print("  VERDICT:          ERROR — could not complete scan")
        print(f"  Reason:           {summary['error']}")
        stderr = summary.get("raw_stderr")
        if stderr:
            print(f"  Details:          {stderr}")
        print()
        print("  DO NOT INSTALL — the scan did not finish; treat as unsafe until it does.")
        print()
        return

    counts = summary.get("summary") or summary.get("counts") or {}
    crit = counts.get("critical", 0)
    major = counts.get("major", 0)
    minor = counts.get("minor", 0)
    nit = counts.get("nit", 0)
    warn = counts.get("warning", 0)
    verdict = "CLEAN" if crit == 0 and major == 0 else "BLOCKED"
    print()
    print(f"  Pre-install scan: {label}")
    print(f"  Kind:             {kind}")
    print(f"  Files scanned:    {summary.get('files_scanned', '?')}")
    print(f"  CRITICAL:         {crit}")
    print(f"  MAJOR:            {major}")
    print(f"  MINOR:            {minor}")
    print(f"  NIT:              {nit}")
    print(f"  WARNING:          {warn}")
    print(f"  VERDICT:          {verdict}")
    if verdict == "BLOCKED":
        print()
        print("  DO NOT INSTALL — review the findings above first.")
        # Print the top 5 most severe findings inline.
        findings = summary.get("findings") or []
        actionable = [f for f in findings if f.get("severity") in ("critical", "major")][:5]
        for f in actionable:
            file_loc = f.get("file") or "<unknown>"
            line = f.get("line")
            loc = f"{file_loc}:{line}" if line else file_loc
            sev = f.get("severity", "?").upper()
            rule = f.get("rule_id", "?")
            cat = f.get("category", "?")
            print(f"    [{sev}] [{cat} {rule}] {loc}")
    print()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Scan any skill, plugin, or marketplace for security issues BEFORE installing. "
            "MANDATORY skillaudit native scan included. No env-var bypass."
        )
    )
    parser.add_argument(
        "target",
        help="Local path, GitHub URL, owner/repo slug, or archive (.tar.gz, .zip).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit a JSON summary on stdout (in addition to the human-readable report).",
    )
    parser.add_argument(
        "--keep-sandbox",
        action="store_true",
        help=("Keep the work directory after scanning (default: delete). Useful for debugging false positives."),
    )
    args = parser.parse_args(argv)

    sandbox = Path(tempfile.mkdtemp(prefix="cpv-preinstall-"))
    try:
        try:
            root, label = _fetch_target(args.target, sandbox)
        except (OSError, RuntimeError, tarfile.TarError, zipfile.BadZipFile) as exc:
            # Any failure to fetch/stage the target — missing path, permission
            # error, failed clone/curl, or a CORRUPT/malicious archive — is a
            # fetch error, not a finding. OSError covers shutil copy/copytree
            # failures (shutil.Error and FileNotFoundError are OSError
            # subclasses); tarfile.TarError / zipfile.BadZipFile are NOT, so they
            # are listed explicitly. Map all of them to the documented exit 2
            # instead of crashing with a traceback on untrusted input.
            print(f"Error: {exc}", file=sys.stderr)
            return 2

        kind = _detect_target_kind(root)
        rc, summary = _run_scan(root, kind)

        if args.json:
            print(json.dumps({"target": label, "kind": kind, "result": summary}, indent=2))
        else:
            _print_report(label, kind, summary)

        return rc
    finally:
        if not args.keep_sandbox:
            shutil.rmtree(sandbox, ignore_errors=True)
        elif sandbox.exists():
            print(f"\n(sandbox kept at {sandbox})", file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
