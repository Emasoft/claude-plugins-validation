#!/usr/bin/env python3
"""TRDD-793ac32a — `.gitmodules` URL allowlist validator.

PSS (perfect-skill-suggester) demonstrated the submodule pattern that
TRDD-793ac32a adopts: dev-only artefacts live in a git submodule that
Claude Code's shallow-clone install does NOT recurse into. The pattern
saves megabytes per install, but it has a security gap PSS doesn't
defend against: **`.gitmodules` URL tampering**.

Attack surface (per TRDD-793ac32a §2.2):
    1. End user — SAFE. Claude Code never recurses into submodules at
       install time, so the URL is never resolved on the user's machine.
    2. Developer — HIGH risk. `git submodule update --init` clones from
       whatever URL `.gitmodules` declares, with no built-in allowlist.
    3. CI runner — HIGH risk. `actions/checkout@v6` with
       `submodules: recursive` clones from arbitrary URLs at build time.

This module provides the allowlist + URL-shape validation that closes
the (2) and (3) attack vectors. It is invoked by:
  * `scripts/validate_plugin.py` (publish-time check, blocks releases
    with non-allowlisted submodule URLs)
  * `git-hooks/pre-push` (pre-push gate, refuses to push if
    `.gitmodules` introduces a new non-allowlisted URL)

Exit codes (CLI mode):
    0 — `.gitmodules` is absent OR every URL passes validation
    1 — at least one URL was rejected; details printed to stderr

Public API:
    `validate_gitmodules(plugin_root)` returns
        list[GitmodulesFinding]
    `parse_gitmodules_urls(plugin_root)` returns
        list[tuple[submodule_name, url, path]]

Per TRDD-793ac32a §9 risk #1: submodule URL injection is the #1
CRITICAL risk for the strip-dev-parts feature. Skipping this validator
is FORBIDDEN — `cpv strip-dev-parts` refuses to operate without it.
"""

from __future__ import annotations

import fnmatch
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

# ── Constants ──────────────────────────────────────────────────────────────────

# Schemes the validator accepts. SSH is allowed because some legitimate
# org workflows use deploy keys for private submodules. Anything else
# (file://, ftp://, http://) is rejected.
_ALLOWED_SCHEMES: frozenset[str] = frozenset({"https", "git+ssh", "ssh"})

# RFC3986-style userinfo (`user@host`). Rejected outright — embedded
# credentials in URLs are an exfiltration vector and a credential-leak
# trap when the URL leaks to logs.
_USERINFO_REJECT_RE = re.compile(r"://[^/@]+@")

# Path-traversal markers. Rejected outright — `..` in a URL path
# component opens up host-substitution attacks via misparsing.
_PATH_TRAVERSAL_RE = re.compile(r"\.\.")

# Embedded backslash or newline. Rejected outright — these don't
# legitimately appear in URLs, and their presence usually signals
# deliberate parser confusion.
_FORBIDDEN_CHARS_RE = re.compile(r"[\\\n\r]")

# The default allowlist applied when `cpv.strip.allowed_submodule_urls`
# is absent and `cpv.strip.require_url_allowlist` is True (the default).
# Rule: same owner as parent repo OR `Emasoft` (transitional shared-dev
# repos). Per TRDD-793ac32a §2.2.
_DEFAULT_TRANSITIONAL_OWNER: str = "Emasoft"


# ── Data classes ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class GitmodulesFinding:
    """A finding from validating a single `.gitmodules` URL.

    Severity is one of `WARNING` (recoverable / advisory) or `CRITICAL`
    (publish-blocking).
    """

    severity: str
    code: str
    submodule_name: str
    url: str
    path: str
    message: str


# ── Internal helpers ───────────────────────────────────────────────────────────


def _read_remote_owner(plugin_root: Path) -> str | None:
    """Return the owner of `git config remote.origin.url` (or None).

    Used to compute the default-rule allowlist when no explicit
    `allowed_submodule_urls` is set in plugin.json.
    """
    try:
        result = subprocess.run(
            ["git", "-C", str(plugin_root), "config", "--get", "remote.origin.url"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    url = result.stdout.strip().rstrip("/")
    if url.endswith(".git"):
        url = url[:-4]
    m = re.search(r"[:/]([^:/\s]+)/[^/\s]+$", url)
    return m.group(1) if m else None


def _read_strip_block(plugin_root: Path) -> dict[str, object]:
    """Return the `cpv.strip` block from plugin.json (or {})."""
    pj = plugin_root / ".claude-plugin" / "plugin.json"
    if not pj.is_file():
        return {}
    try:
        data = json.loads(pj.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    cpv_block = data.get("cpv") if isinstance(data, dict) else None
    if not isinstance(cpv_block, dict):
        return {}
    strip = cpv_block.get("strip")
    return strip if isinstance(strip, dict) else {}


def _scheme_of(url: str) -> str | None:
    """Return the lowercased scheme of `url` (without `://`), or None."""
    m = re.match(r"^([a-zA-Z][a-zA-Z0-9+\-.]*)://", url)
    if m:
        return m.group(1).lower()
    # Special-case SCP-shaped SSH (`git@github.com:owner/repo.git`).
    if re.match(r"^[a-zA-Z0-9_.-]+@[^:]+:", url):
        return "ssh"
    return None


def _owner_of(url: str) -> str | None:
    """Extract the owner segment from a GitHub-shaped URL.

    Recognises:
      * `https://github.com/<owner>/<repo>(.git)?`
      * `git@github.com:<owner>/<repo>(.git)?`
      * `ssh://git@github.com/<owner>/<repo>(.git)?`
    Returns None for any other shape.
    """
    # SCP-style SSH: git@github.com:owner/repo.git
    m = re.match(r"^[a-zA-Z0-9_.-]+@github\.com:([^/]+)/", url)
    if m:
        return m.group(1)
    # https or ssh URL
    m = re.match(r"^[a-z+]+://(?:[^/]+@)?github\.com/([^/]+)/", url)
    if m:
        return m.group(1)
    return None


def _matches_allowlist(url: str, allowlist: list[str]) -> bool:
    """Return True if `url` matches any glob entry in `allowlist`.

    Glob semantics: `*` matches any character except `/`; `?` matches
    one. Implemented via fnmatch (POSIX shell glob).
    """
    return any(fnmatch.fnmatchcase(url, pat) for pat in allowlist)


def _validate_url_shape(url: str) -> tuple[bool, str]:
    """Return (is_valid, reason). reason is empty when valid."""
    if not url:
        return False, "URL is empty"
    if _FORBIDDEN_CHARS_RE.search(url):
        return False, "URL contains backslash or newline"
    if _USERINFO_REJECT_RE.search(url):
        return False, (
            "URL contains embedded user info (`user@host`) — credentials "
            "in URLs are forbidden (exfiltration / credential-leak risk)"
        )
    if _PATH_TRAVERSAL_RE.search(url):
        return False, "URL contains path-traversal `..`"
    scheme = _scheme_of(url)
    if scheme is None:
        return False, "URL has no recognised scheme"
    if scheme not in _ALLOWED_SCHEMES:
        return False, (f"URL scheme `{scheme}` is not allowed (only {sorted(_ALLOWED_SCHEMES)} permitted)")
    return True, ""


# ── Public API ────────────────────────────────────────────────────────────────


def parse_gitmodules_urls(
    plugin_root: Path,
) -> list[tuple[str, str, str]]:
    """Return [(submodule_name, url, path)] for every entry in
    `<plugin_root>/.gitmodules`.

    Uses `git config --file .gitmodules --get-regexp` so the parsing is
    100% git-compatible (no homegrown INI parser). Returns an empty list
    if `.gitmodules` is absent or git is not on PATH.
    """
    gm = plugin_root / ".gitmodules"
    if not gm.is_file():
        return []
    try:
        urls_result = subprocess.run(
            [
                "git",
                "config",
                "--file",
                str(gm),
                "--get-regexp",
                r"^submodule\..*\.url$",
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        paths_result = subprocess.run(
            [
                "git",
                "config",
                "--file",
                str(gm),
                "--get-regexp",
                r"^submodule\..*\.path$",
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if urls_result.returncode != 0:
        return []

    name_to_url: dict[str, str] = {}
    name_to_path: dict[str, str] = {}
    for line in urls_result.stdout.splitlines():
        # Format: `submodule.<name>.url <url>`
        m = re.match(r"^submodule\.(.+)\.url\s+(.+)$", line)
        if m:
            name_to_url[m.group(1)] = m.group(2)
    for line in paths_result.stdout.splitlines():
        m = re.match(r"^submodule\.(.+)\.path\s+(.+)$", line)
        if m:
            name_to_path[m.group(1)] = m.group(2)
    return [(name, url, name_to_path.get(name, "")) for name, url in sorted(name_to_url.items())]


def validate_gitmodules(plugin_root: Path) -> list[GitmodulesFinding]:
    """Validate every URL in `<plugin_root>/.gitmodules` against the
    allowlist + shape rules. Returns a list of findings (empty if clean).

    Order of checks per URL:
      1. URL shape — scheme, characters, traversal, userinfo (CRITICAL)
      2. Allowlist match — explicit list OR default-rule (CRITICAL when
         `require_url_allowlist=True`; WARNING otherwise)
      3. SHA pin match — if `cpv.strip.extract[].submodule_commit_sha`
         is provided, the actual git-tree-tracked SHA must match
         (CRITICAL on mismatch)

    The function does not enforce ANY rule when `.gitmodules` is absent.
    """
    findings: list[GitmodulesFinding] = []
    entries = parse_gitmodules_urls(plugin_root)
    if not entries:
        return findings

    strip = _read_strip_block(plugin_root)
    explicit_allowlist = strip.get("allowed_submodule_urls")
    if not isinstance(explicit_allowlist, list):
        explicit_allowlist = None
    require_allowlist = strip.get("require_url_allowlist", True)

    # Default rule: same owner as parent OR `Emasoft`. Computed lazily
    # because reading `git config remote.origin.url` is one fork.
    parent_owner: str | None = None
    parent_owner_loaded: bool = False

    extract_entries = strip.get("extract", [])
    sha_pins: dict[str, str] = {}
    if isinstance(extract_entries, list):
        for entry in extract_entries:
            if not isinstance(entry, dict):
                continue
            path = entry.get("submodule_path") or entry.get("src")
            sha = entry.get("submodule_commit_sha")
            if isinstance(path, str) and isinstance(sha, str):
                sha_pins[path.rstrip("/")] = sha

    for name, url, path in entries:
        # 1. URL-shape check.
        ok, reason = _validate_url_shape(url)
        if not ok:
            findings.append(
                GitmodulesFinding(
                    severity="CRITICAL",
                    code="STRIP-G010",
                    submodule_name=name,
                    url=url,
                    path=path,
                    message=f"Submodule URL '{url}' rejected: {reason}",
                )
            )
            continue

        # 2. Allowlist match.
        if explicit_allowlist is not None:
            if not _matches_allowlist(url, explicit_allowlist):
                findings.append(
                    GitmodulesFinding(
                        severity="CRITICAL",
                        code="STRIP-G011",
                        submodule_name=name,
                        url=url,
                        path=path,
                        message=(
                            f"Submodule URL '{url}' is not in `cpv.strip.allowed_submodule_urls` ({explicit_allowlist})"
                        ),
                    )
                )
                continue
        elif require_allowlist:
            # Default rule: parent owner or Emasoft.
            if not parent_owner_loaded:
                parent_owner = _read_remote_owner(plugin_root)
                parent_owner_loaded = True
            url_owner = _owner_of(url)
            if url_owner is None:
                findings.append(
                    GitmodulesFinding(
                        severity="CRITICAL",
                        code="STRIP-G012",
                        submodule_name=name,
                        url=url,
                        path=path,
                        message=(
                            f"Submodule URL '{url}' is not GitHub-shaped; the "
                            "default allowlist rule (same-owner OR Emasoft) "
                            "cannot evaluate it. Add an explicit "
                            "`cpv.strip.allowed_submodule_urls` entry, OR set "
                            "`require_url_allowlist=false` to opt out."
                        ),
                    )
                )
                continue
            if url_owner != parent_owner and url_owner != _DEFAULT_TRANSITIONAL_OWNER:
                findings.append(
                    GitmodulesFinding(
                        severity="CRITICAL",
                        code="STRIP-G013",
                        submodule_name=name,
                        url=url,
                        path=path,
                        message=(
                            f"Submodule URL '{url}' owner '{url_owner}' is not "
                            f"the parent owner ('{parent_owner}') and not the "
                            f"transitional default ('{_DEFAULT_TRANSITIONAL_OWNER}'). "
                            f"Add it to `cpv.strip.allowed_submodule_urls`."
                        ),
                    )
                )
                continue
        else:
            # No explicit allowlist AND require_url_allowlist=False —
            # opt-out. Emit advisory WARNING for traceability.
            findings.append(
                GitmodulesFinding(
                    severity="WARNING",
                    code="STRIP-G014",
                    submodule_name=name,
                    url=url,
                    path=path,
                    message=(
                        f"Submodule URL '{url}' accepted without allowlist "
                        f"(require_url_allowlist=false). Reviewers must "
                        f"manually verify this URL on every change."
                    ),
                )
            )

        # 3. SHA-pin verification (best-effort; needs git in working repo).
        pinned_sha = sha_pins.get(path.rstrip("/"))
        if pinned_sha is not None:
            try:
                tree_result = subprocess.run(
                    ["git", "-C", str(plugin_root), "ls-tree", "HEAD", path],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=False,
                )
            except (OSError, subprocess.SubprocessError):
                tree_result = None
            actual_sha = ""
            if tree_result is not None and tree_result.returncode == 0:
                # ls-tree format: `<mode> <type> <sha>\t<path>`
                m = re.search(r"\s([0-9a-f]{40})\s", tree_result.stdout)
                if m:
                    actual_sha = m.group(1)
            if actual_sha and not actual_sha.startswith(pinned_sha[: len(actual_sha)]):
                findings.append(
                    GitmodulesFinding(
                        severity="CRITICAL",
                        code="STRIP-G015",
                        submodule_name=name,
                        url=url,
                        path=path,
                        message=(
                            f"Submodule '{path}' index SHA ({actual_sha[:12]}…) "
                            f"does not match recorded "
                            f"`cpv.strip.extract[].submodule_commit_sha` "
                            f"({pinned_sha[:12]}…). Either revert the submodule "
                            f"pointer change, or update the recorded SHA in "
                            f"plugin.json — and have a reviewer approve both."
                        ),
                    )
                )
    return findings


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. `python cpv_validate_gitmodules.py [<plugin_root>]`."""
    args = argv if argv is not None else sys.argv[1:]
    plugin_root = Path(args[0]).resolve() if args else Path.cwd().resolve()
    if not plugin_root.is_dir():
        print(f"ERROR: plugin root not found: {plugin_root}", file=sys.stderr)
        return 1
    findings = validate_gitmodules(plugin_root)
    if not findings:
        print(f"OK: .gitmodules in {plugin_root} passed allowlist validation.")
        return 0
    rc = 0
    for f in findings:
        print(
            f"[{f.severity}] [{f.code}] submodule={f.submodule_name!r} path={f.path!r} url={f.url!r}",
            file=sys.stderr,
        )
        print(f"  {f.message}", file=sys.stderr)
        if f.severity == "CRITICAL":
            rc = 1
    return rc


if __name__ == "__main__":
    sys.exit(main())
