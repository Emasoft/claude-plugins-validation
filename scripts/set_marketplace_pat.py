#!/usr/bin/env python3
"""Set the MARKETPLACE_PAT secret on one or more GitHub repositories.

This helper exists so agents (plugin-creator, marketplace-fixer,
migrate-marketplace-architecture, setup-marketplace-auto-notification) do NOT
have to improvise ``gh`` command lines. Improvised commands have repeatedly
used ``echo "$MARKETPLACE_PAT" | gh secret set MARKETPLACE_PAT`` which is
*wrong* for two reasons:

1. ``echo`` appends a trailing newline that ends up stored inside the secret.
   The plugin repo then sends a malformed Authorization header and the
   marketplace-side ``checkout`` fails with ``Bad credentials`` / ``401``.
2. The pipe form relies on stdin handling that is fragile across ``sh`` /
   ``zsh`` / ``bash`` / Windows PowerShell variations — especially when
   invoked from a subprocess with ``stdin=devnull``.

The correct invocation is ``gh secret set NAME --repo OWNER/REPO --body "$VALUE"``.
This script wraps that and enforces it.

Usage::

    uv run python scripts/set_marketplace_pat.py OWNER/REPO [OWNER/REPO ...]
    uv run python scripts/set_marketplace_pat.py --secret MY_PAT OWNER/REPO
    uv run python scripts/set_marketplace_pat.py --verify-only OWNER/REPO

Behavior:
    - Reads the PAT value from ``$MARKETPLACE_PAT`` in the environment.
    - If not set, prints a clear error and exits 2 (do NOT prompt — the
      caller decides whether to interactively create a token).
    - For each repository, runs ``gh secret set MARKETPLACE_PAT --repo <repo>
      --body "$MARKETPLACE_PAT"`` and verifies the secret afterwards with
      ``gh secret list``.
    - Never prints the PAT value, not even a prefix. Length check only.

Exit codes:
    0  all repos updated and verified
    1  one or more ``gh secret set`` calls failed
    2  $MARKETPLACE_PAT not set (caller must create one first)
    3  gh CLI not installed or not authenticated
    4  bad usage / malformed repo argument
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys

SECRET_NAME_DEFAULT = "MARKETPLACE_PAT"
REPO_PATTERN = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")


def _die(code: int, msg: str) -> "int":
    print(msg, file=sys.stderr)
    return code


def _require_gh() -> str:
    """Return absolute path to gh or exit 3 with a clear message."""
    gh = shutil.which("gh")
    if gh is None:
        sys.exit(_die(3, "set_marketplace_pat: 'gh' CLI not found on PATH. Install via: brew install gh"))
    return gh


def _check_auth(gh: str) -> None:
    """Fail fast if gh is not authenticated."""
    r = subprocess.run([gh, "auth", "status"], capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(_die(3, f"set_marketplace_pat: 'gh auth status' failed:\n{r.stderr}"))


def _valid_repo(repo: str) -> bool:
    return bool(REPO_PATTERN.match(repo))


def _secret_exists(gh: str, repo: str, secret_name: str) -> bool:
    """Return True iff ``gh secret list`` shows the secret on ``repo``."""
    r = subprocess.run(
        [gh, "secret", "list", "--repo", repo],
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
        return False
    for line in r.stdout.splitlines():
        if line.split("\t", 1)[0].strip() == secret_name:
            return True
    return False


def _set_secret(gh: str, repo: str, secret_name: str, value: str) -> int:
    """Set the secret via --body. Never uses stdin/echo-pipe. Never logs value."""
    r = subprocess.run(
        [gh, "secret", "set", secret_name, "--repo", repo, "--body", value],
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
        print(
            f"set_marketplace_pat: FAILED on {repo} (exit {r.returncode})\n"
            f"{r.stderr}",
            file=sys.stderr,
        )
        return 1
    if not _secret_exists(gh, repo, secret_name):
        print(
            f"set_marketplace_pat: set returned 0 but verification "
            f"(gh secret list) does NOT show {secret_name} on {repo}",
            file=sys.stderr,
        )
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Set the MARKETPLACE_PAT secret on one or more GitHub repos "
            "using the correct 'gh secret set --body' form. Never uses echo-pipe."
        ),
    )
    parser.add_argument(
        "repos",
        nargs="+",
        metavar="OWNER/REPO",
        help="One or more GitHub repositories (e.g. Emasoft/my-plugin)",
    )
    parser.add_argument(
        "--secret",
        default=SECRET_NAME_DEFAULT,
        help=f"Secret name to set (default: {SECRET_NAME_DEFAULT})",
    )
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="Do not set anything — only check whether the secret exists on each repo",
    )
    args = parser.parse_args()

    # Validate all repo arguments first
    bad_repos = [r for r in args.repos if not _valid_repo(r)]
    if bad_repos:
        return _die(4, f"set_marketplace_pat: malformed OWNER/REPO: {', '.join(bad_repos)}")

    # In set mode (not --verify-only), read + validate the PAT BEFORE touching
    # gh. PAT-missing is the most common failure mode and we want to return
    # the most specific error (exit 2) instead of the generic "gh not found"
    # (exit 3). Verify-only mode doesn't need the PAT at all.
    pat = ""
    if not args.verify_only:
        pat = os.environ.get("MARKETPLACE_PAT", "")
        if not pat:
            return _die(
                2,
                "set_marketplace_pat: $MARKETPLACE_PAT is not set in the environment.\n"
                "  Export it first, e.g. in your shell rc file:\n"
                "    export MARKETPLACE_PAT=ghp_xxxxxxxxxxxxxxxxxxxx\n"
                "  Then re-run this script. Never paste the token on the command line.",
            )
        # Reject obviously malformed PATs (whitespace, newlines from copy-paste)
        if pat.strip() != pat or "\n" in pat or "\r" in pat:
            return _die(
                2,
                "set_marketplace_pat: $MARKETPLACE_PAT contains whitespace or newlines.\n"
                "  This is almost always a copy-paste error — rotate the token and re-export.",
            )

    gh = _require_gh()
    _check_auth(gh)

    if args.verify_only:
        all_ok = True
        for repo in args.repos:
            present = _secret_exists(gh, repo, args.secret)
            status = "present" if present else "MISSING"
            print(f"  {repo}: {args.secret} {status}")
            all_ok = all_ok and present
        return 0 if all_ok else 1

    print(f"  Using $MARKETPLACE_PAT (length {len(pat)} chars) — never logged")

    failures = 0
    for repo in args.repos:
        print(f"  Setting {args.secret} on {repo}...")
        rc = _set_secret(gh, repo, args.secret, pat)
        if rc == 0:
            print(f"  {repo}: {args.secret} set and verified")
        else:
            failures += 1
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
