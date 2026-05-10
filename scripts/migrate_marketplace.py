#!/usr/bin/env python3
"""Normalize an existing marketplace.json against the canonical CPV schema.

Per the Phase 0 marketplace survey, real-world marketplaces have drift:
  * Some entries use `source.url` (older form), others use `source.repo`.
    Canonical is `{type: "github", repo: "owner/name"}`.
  * Some entries point at GitHub repos that 404 (deleted / renamed).
  * Some entries use `relative-path` source style without normalization.

This tool reads marketplace.json, applies the migrations, and writes
back atomically. Probes each github repo for live-ness via `gh api`
(retry-wrapped; safe under transient hiccups).

Usage:
    uv run python scripts/migrate_marketplace.py <marketplace-root>
    uv run python scripts/migrate_marketplace.py <marketplace-root> --check
    uv run python scripts/migrate_marketplace.py <marketplace-root> --no-probe

`--check` exits 1 if migrations would change the file (CI gate).
`--no-probe` skips the live-ness probe (offline mode).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cpv_network_resilience import gh_with_retry  # noqa: E402


def parse_github_url(url: str) -> tuple[str, str] | None:
    """Return (owner, repo) parsed from a github URL, else None."""
    if not url:
        return None
    url = url.strip().rstrip("/")
    if url.endswith(".git"):
        url = url[:-4]
    # https://github.com/<owner>/<repo>
    if url.startswith(("http://", "https://")):
        parsed = urlparse(url)
        if parsed.netloc != "github.com":
            return None
        parts = parsed.path.strip("/").split("/")
        if len(parts) >= 2:
            return parts[0], parts[1]
        return None
    # git@github.com:<owner>/<repo>
    if url.startswith("git@github.com:"):
        body = url[len("git@github.com:") :]
        parts = body.split("/", 1)
        if len(parts) == 2:
            return parts[0], parts[1]
    return None


def normalize_source(source: object) -> tuple[object, str | None]:
    """Return (normalized-source, change-description-or-None).

    Migrations applied:
      * `{"url": "https://github.com/owner/repo"}` → `{"type":"github","repo":"owner/repo"}`
      * `{"type":"github","url":...}`             → drop url, keep repo
      * String form `"https://github.com/owner/repo"` → `{type:"github","repo":...}`
    """
    if isinstance(source, str):
        parsed = parse_github_url(source)
        if parsed:
            owner, repo = parsed
            return {"type": "github", "repo": f"{owner}/{repo}"}, (f"string url → {{type:github, repo:{owner}/{repo}}}")
        return source, None
    if not isinstance(source, dict):
        return source, None

    src_dict: dict[str, object] = dict(source)

    if "url" in src_dict and "repo" not in src_dict:
        url = src_dict.get("url")
        if isinstance(url, str):
            parsed = parse_github_url(url)
            if parsed:
                owner, repo = parsed
                new: dict[str, object] = {"type": "github", "repo": f"{owner}/{repo}"}
                # Preserve other fields (path, ref, etc.) except the migrated url.
                for k, v in src_dict.items():
                    if k not in ("url", "type", "repo"):
                        new[k] = v
                return new, f"url:{url} → repo:{owner}/{repo}"
    return source, None


def probe_repo_alive(owner_repo: str) -> tuple[bool, str]:
    """Return (alive, status_text). Uses gh api with retry."""
    try:
        res = gh_with_retry(
            ["gh", "api", f"repos/{owner_repo}", "--jq", ".name"],
            check=False,
            capture_output=True,
            max_attempts=3,
            backoff=4.0,
        )
    except FileNotFoundError:
        return True, "gh CLI not installed — assumed alive (cannot probe)"
    if res.returncode == 0 and res.stdout.strip():
        return True, "alive"
    err = (res.stderr or "").strip().splitlines()
    last = err[-1] if err else "unknown"
    return False, f"DEAD ({last})"


def migrate_marketplace(
    root: Path,
    *,
    check_only: bool = False,
    probe: bool = True,
) -> int:
    mkt = root / ".claude-plugin" / "marketplace.json"
    if not mkt.is_file():
        print(f"  [migrate] no .claude-plugin/marketplace.json at {root}", file=sys.stderr)
        return 1

    text = mkt.read_text(encoding="utf-8")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        print(f"  [migrate] marketplace.json is not valid JSON: {e}", file=sys.stderr)
        return 1

    plugins = data.get("plugins", [])
    if not isinstance(plugins, list):
        print("  [migrate] plugins field is not a list", file=sys.stderr)
        return 1

    changes: list[str] = []
    dead_repos: list[str] = []

    for entry in plugins:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name", "<unnamed>")
        new_source, desc = normalize_source(entry.get("source"))
        if desc is not None:
            entry["source"] = new_source
            changes.append(f"  - {name}: source migrated ({desc})")

        if probe and isinstance(entry.get("source"), dict):
            src = entry["source"]
            if src.get("type") == "github" and isinstance(src.get("repo"), str):
                alive, status = probe_repo_alive(src["repo"])
                if not alive:
                    dead_repos.append(f"  - {name} ({src['repo']}): {status}")

    if not changes:
        # No source-shape changes needed — don't touch the file just to
        # add a trailing newline.
        print(f"  [migrate] {mkt}: no changes needed.")
        if dead_repos:
            print("  [migrate] DEAD repos detected (manual cleanup required):", file=sys.stderr)
            for line in dead_repos:
                print(line, file=sys.stderr)
            return 1
        return 0

    new_text = json.dumps(data, indent=2) + "\n"

    if check_only:
        print(f"  [check] {mkt}: would apply {len(changes)} migration(s):")
        for c in changes:
            print(c)
        if dead_repos:
            print("  [check] DEAD repos:")
            for line in dead_repos:
                print(line)
        return 1

    # Atomic write: tmp + rename so a crash mid-write doesn't corrupt the file.
    tmp = mkt.with_suffix(".json.tmp")
    tmp.write_text(new_text, encoding="utf-8")
    tmp.replace(mkt)
    print(f"  [migrate] {mkt}: applied {len(changes)} migration(s):")
    for c in changes:
        print(c)
    if dead_repos:
        print(
            "\n  [migrate] DEAD repos detected — entries STILL IN marketplace.json. "
            "Decide whether to remove or restore each:",
            file=sys.stderr,
        )
        for line in dead_repos:
            print(line, file=sys.stderr)
        return 1
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument(
        "marketplace_root",
        nargs="?",
        default=".",
        type=Path,
        help="Path to marketplace root (containing .claude-plugin/marketplace.json)",
    )
    p.add_argument("--check", action="store_true", help="Exit 1 if migrations would change the file. CI gate mode.")
    p.add_argument(
        "--no-probe", action="store_true", help="Skip live-ness probe of each plugin's github repo (offline mode)."
    )
    args = p.parse_args()
    return migrate_marketplace(
        args.marketplace_root.resolve(),
        check_only=args.check,
        probe=not args.no_probe,
    )


if __name__ == "__main__":
    sys.exit(main())
