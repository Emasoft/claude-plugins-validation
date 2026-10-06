#!/usr/bin/env python3
"""CPVPPC pin — update an external-repo unit's `ref` to the remote's current HEAD.

Never executes repo code; `git ls-remote` is a network probe only. The new
SHA must be one the REMOTE itself reports (an explicit --sha is verified the
same way — plan section 3.3: "refusing a SHA not reachable on the engine
remote"). Updates cpvppc.yaml AND regenerates the lock's config sha256, so
CFG-002 stays consistent.

Exit codes: 0 pinned · 2 nothing to pin (in-tree unit / unknown unit /
missing config) · 4 the requested SHA is not reported by the remote ·
5 network unavailable (UNKNOWN semantics — never silently succeed).
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

if __package__ in (None, ""):  # pragma: no cover — direct-script invocation
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cpvppc.init import (  # noqa: E402
    EXIT_ERROR,
    _load_yaml,  # noqa: E402
    render_config,
    render_lock,
)

EXIT_OK = 0
EXIT_NOT_PINNABLE = 2
EXIT_SHA_UNREACHABLE = 4
EXIT_NETWORK = 5
_LS_REMOTE_TIMEOUT = 60
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def _ls_remote_sha(url: str, timeout: int = _LS_REMOTE_TIMEOUT) -> tuple[str | None, str | None]:
    """The remote's HEAD sha, or (None, reason). Network failure -> (None, 'network')."""
    try:
        proc = subprocess.run(  # noqa: S603 — fixed argv [git, ls-remote, url]
            ["git", "ls-remote", url, "HEAD"],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return None, "network"
    except OSError:
        return None, "network"
    if proc.returncode != 0:
        # ls-remote reports unreachable hosts/auth as non-zero — UNKNOWN, never a pin.
        return None, "network"
    out = proc.stdout.strip().splitlines()
    if not out:
        return None, "remote"
    sha = out[0].split()[0].strip()
    if not _SHA_RE.match(sha):
        return None, "remote"
    return sha, None


def _load(repo: Path) -> tuple[dict[str, Any] | None, str]:
    path = repo / "cpvppc.yaml"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None, "missing cpvppc.yaml"
    doc = _load_yaml(text)
    if not isinstance(doc, dict):
        return None, "cpvppc.yaml is not a YAML mapping"
    return doc, ""


def pin_unit(repo: Path, unit_name: str, requested_sha: str | None = None) -> int:
    cfg_path = repo / "cpvppc.yaml"
    doc, reason = _load(repo)
    if doc is None:
        print(f"error: {reason}", file=sys.stderr)
        return EXIT_NOT_PINNABLE
    unit = next((u for u in doc.get("units", []) if isinstance(u, dict) and u.get("name") == unit_name), None)
    if unit is None:
        print(f"error: no unit named {unit_name!r} in cpvppc.yaml", file=sys.stderr)
        return EXIT_NOT_PINNABLE
    src = unit.get("source") or {}
    ext = src.get("external-repo") if isinstance(src, dict) else None
    if not isinstance(ext, dict) or not ext.get("url"):
        print(f"error: unit {unit_name!r} is not an external-repo unit — nothing to pin", file=sys.stderr)
        return EXIT_NOT_PINNABLE
    url = str(ext["url"])
    old = str(ext.get("ref", ""))

    new_sha, why = _ls_remote_sha(url)
    if new_sha is None and why == "network":
        print(f"UNKNOWN: cannot reach {url} — network unavailable, refusing to change the pin", file=sys.stderr)
        return EXIT_NETWORK
    if requested_sha is not None:
        if not _SHA_RE.match(requested_sha):
            print(f"error: --sha {requested_sha!r} is not a full 40-hex sha", file=sys.stderr)
            return EXIT_ERROR
        if new_sha is None or requested_sha != new_sha:
            print(
                f"error: requested sha {requested_sha} is not the HEAD the remote reports"
                f" (remote reports {new_sha or 'nothing'}); refusing an unreachable SHA",
                file=sys.stderr,
            )
            return EXIT_SHA_UNREACHABLE
    elif new_sha is None:
        print(f"error: {url} reports no HEAD sha — refusing to pin", file=sys.stderr)
        return EXIT_SHA_UNREACHABLE

    ext["ref"] = new_sha
    text = render_config(doc)
    raw = text.encode("utf-8")
    lock = render_lock(doc, raw)
    cfg_path.write_bytes(raw)
    (repo / ".cpvppc-lock.json").write_text(json.dumps(lock, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(f"pinned {unit_name}: {old or '(unset)'} -> {new_sha}")
    print(f"updated {cfg_path} and {repo / '.cpvppc-lock.json'}")
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="cpvppc-pin", description=__doc__)
    ap.add_argument("repo", type=Path, help="repo root whose cpvppc.yaml carries the unit")
    ap.add_argument("unit", help="unit name to re-pin")
    ap.add_argument("--sha", metavar="SHA", help="pin this exact sha (still verified against the remote)")
    args = ap.parse_args(argv)
    repo = args.repo.resolve()
    if not repo.is_dir():
        print(f"error: {repo} is not a directory", file=sys.stderr)
        return EXIT_ERROR
    return pin_unit(repo, args.unit, requested_sha=args.sha)


if __name__ == "__main__":
    raise SystemExit(main())
