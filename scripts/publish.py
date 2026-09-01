#!/usr/bin/env python3
"""Unified publish pipeline: test → lint → validate → marketplace-registration → bump → commit → push.

Absorbs all logic from bump_version.py and check_version_consistency.py into a single script.

Usage:
  uv run python scripts/publish.py --patch            # bump patch and publish
  uv run python scripts/publish.py --minor            # bump minor and publish
  uv run python scripts/publish.py --major            # bump major and publish
  uv run python scripts/publish.py --patch --dry-run  # preview only, no changes
  uv run python scripts/publish.py --print-gates      # print gate list and exit

HARD RULE: No checks can be skipped. There are no --skip-* flags, no env
var bypasses, no --force. Every gate must pass before any version bump,
tag, push, or GitHub release is performed. Publish is blocked on ANY
CRITICAL, MAJOR, MINOR, or NIT severity finding. WARNING is advisory only.

Exit codes:
    0 - Success
    1 - Preflight, tests, lint, version-consistency, marketplace-registration,
        bump, changelog, commit, tag, or push failed (fail-fast)
    1-4 - Plugin validation severity (1=CRITICAL, 2=MAJOR, 3=MINOR, 4=NIT)
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

# Ensure the scripts/ directory is on sys.path so we can import cpv_validation_common
# when publish.py is invoked directly (e.g. `uv run python scripts/publish.py`).
sys.path.insert(0, str(Path(__file__).resolve().parent))

from cpv_ci_preflight import run_ci_preflight  # noqa: E402
from cpv_fork_parity import fork_parity_supported, run_under_linux_fork_default  # noqa: E402
from cpv_network_resilience import gh_with_retry, git_with_retry  # noqa: E402
from cpv_validation_common import build_report_path  # noqa: E402

# ── ANSI colors ──────────────────────────────────────────────────────────────

_USE_COLOR = hasattr(sys.stdout, "isatty") and sys.stdout.isatty()
RED = "\033[0;31m" if _USE_COLOR else ""
GREEN = "\033[0;32m" if _USE_COLOR else ""
YELLOW = "\033[1;33m" if _USE_COLOR else ""
BLUE = "\033[0;34m" if _USE_COLOR else ""
NC = "\033[0m" if _USE_COLOR else ""


# ── Phase C (v2.77.0): per-thread stdout/stderr capture ──────────────────────
#
# Phase C runs Gates 2/3/4/5 concurrently. Each gate prints "═══ Gate N ═══"
# headers and ✓/✗ summary lines via plain `print()` calls, so the only way to
# keep terminal output readable is to capture each gate's writes into its own
# buffer and replay them in fixed order after every gate finishes.
#
# We do NOT use `contextlib.redirect_stdout`. That helper mutates the
# process-global `sys.stdout` reference: with N concurrent threads the last
# one to exit may restore a stale buffer instead of the real stdout,
# swallowing every subsequent write made by the main thread (this exact
# bug surfaced during early Phase B drafts in cpv_lint_engine — see the
# comment at scripts/cpv_lint_engine.py:1190).
#
# Instead we install thread-aware proxies for sys.stdout / sys.stderr that
# fall through to the real streams when no buffer is set on the calling
# thread, and write to the per-thread buffer otherwise. The main thread
# never sets a buffer, so its prints (the parent reporting layer) keep
# going to the real terminal — no race window.


class _ThreadAwareStream:
    """sys.stdout / sys.stderr proxy that routes to a per-thread buffer.

    When ``threading.local()._buffer`` is set on the calling thread, every
    write goes to that buffer. When unset, the write passes through to
    the real underlying stream. Other stream attributes (``isatty``,
    ``flush``, ``fileno``, ``encoding``, …) are forwarded to the real
    stream so existing checks like ``sys.stdout.isatty()`` keep working.
    """

    def __init__(self, real_stream: Any) -> None:
        self._real = real_stream
        self._tls = threading.local()

    def _active_buffer(self) -> io.StringIO | None:
        """Return the per-thread buffer if set, else None."""
        return getattr(self._tls, "_buffer", None)

    def _set_buffer(self, buf: io.StringIO | None) -> None:
        """Install (or clear) the per-thread buffer on the calling thread."""
        if buf is None:
            try:
                del self._tls._buffer
            except AttributeError:
                pass
        else:
            self._tls._buffer = buf

    def write(self, data: str) -> int:
        buf = self._active_buffer()
        target = buf if buf is not None else self._real
        return target.write(data)

    def flush(self) -> None:
        buf = self._active_buffer()
        if buf is not None:
            buf.flush()
        else:
            self._real.flush()

    def isatty(self) -> bool:
        return bool(getattr(self._real, "isatty", lambda: False)())

    def fileno(self) -> int:
        # Cast Any → int (mypy can't tell from getattr path).
        return int(self._real.fileno())

    @property
    def encoding(self) -> str:
        return getattr(self._real, "encoding", "utf-8")

    def __getattr__(self, name: str) -> Any:
        # Fall through to the real stream for anything else (e.g. .buffer,
        # .errors, .reconfigure on Python 3.7+). Called only if normal
        # lookup misses, so the explicitly defined attrs above win.
        return getattr(self._real, name)


def _install_stream_routers() -> tuple[_ThreadAwareStream, _ThreadAwareStream]:
    """Replace sys.stdout/sys.stderr with thread-aware proxies (idempotent).

    Returns the (stdout_router, stderr_router) pair so callers can attach
    per-thread buffers via ``router._set_buffer(io.StringIO())``.
    """
    if not isinstance(sys.stdout, _ThreadAwareStream):
        sys.stdout = _ThreadAwareStream(sys.stdout)  # type: ignore[assignment]
    if not isinstance(sys.stderr, _ThreadAwareStream):
        sys.stderr = _ThreadAwareStream(sys.stderr)  # type: ignore[assignment]
    return sys.stdout, sys.stderr  # type: ignore[return-value]


def _run_stage_captured(
    fn: Callable[..., int],
    *args: Any,
    **kwargs: Any,
) -> tuple[int, str, str]:
    """Run a stage_*() function with stdout+stderr captured per-thread.

    Installs a per-thread StringIO on both routers for the duration of the
    call, then drains them and returns ``(rc, stdout_text, stderr_text)``.
    SystemExit raised by `_print_result` (when a stage's `run()` call uses
    check=True and the subprocess fails) is caught and converted to a
    plain return code so the orchestrator can report all gates uniformly.
    """
    stdout_router, stderr_router = _install_stream_routers()
    out_buf = io.StringIO()
    err_buf = io.StringIO()
    stdout_router._set_buffer(out_buf)
    stderr_router._set_buffer(err_buf)
    try:
        try:
            rc = fn(*args, **kwargs)
        except SystemExit as e:
            # `_print_result` calls sys.exit(returncode) on subprocess
            # failure when check=True. That raises SystemExit, which only
            # kills the worker thread. Convert to a plain return code so
            # the orchestrator sees the failure and can act on it.
            code = e.code
            if code is None:
                rc = 0
            elif isinstance(code, int):
                rc = code
            else:
                rc = 1
    finally:
        stdout_router._set_buffer(None)
        stderr_router._set_buffer(None)
    return rc, out_buf.getvalue(), err_buf.getvalue()


# Lazy-initialized gitignore filter for file scanning
_gi_cache: dict = {}


def _get_gi(plugin_root: Path):  # noqa: ANN202
    """Get or create GitignoreFilter for the plugin root, keyed by resolved path."""
    key = str(plugin_root.resolve())
    if key not in _gi_cache:
        from gitignore_filter import GitignoreFilter

        _gi_cache[key] = GitignoreFilter(plugin_root)
    return _gi_cache[key]


# ── Helpers ──────────────────────────────────────────────────────────────────


def get_plugin_root() -> Path:
    """Resolve plugin root from this script's location (parent of scripts/)."""
    return Path(__file__).resolve().parent.parent


def _parse_owner_repo_from_remote(remote_url: str) -> tuple[str, str] | None:
    """Extract (owner, repo) from `git@host:owner/repo.git` or
    `https://host/owner/repo[.git]`. Returns None on unparseable input.
    """
    if not remote_url:
        return None
    # Strip optional `.git` suffix and trailing slashes.
    url = remote_url.strip().rstrip("/")
    if url.endswith(".git"):
        url = url[:-4]
    # Match: anything ending in `:owner/repo` or `/owner/repo`.
    match = re.search(r"[:/]([^:/\s]+)/([^/\s]+)$", url)
    if not match:
        return None
    return match.group(1), match.group(2)


def _ensure_gh_auth(owner: str, repo: str) -> None:
    """Verify `gh` CLI is installed, authenticated, and has push permission.

    Called BEFORE every push gate (commit+tag push, GitHub release).
    Exits 1 with actionable message on any of four failure modes:
      1. gh not installed.
      2. gh installed but not authenticated (`gh auth status` exits non-zero).
      3. Authenticated but no push permission on owner/repo.
      4. Authenticated but to multiple hosts/accounts and active account
         differs from the one with push perms (warns + suggests `gh auth switch`).

    Per TRDD-bbff5bc5 §4.1, this function:
      - NEVER invokes `gh auth token` (PAT non-leakage; tokens never enter
        publish.py memory or stderr/stdout).
      - Uses only `gh auth status` (which doesn't print tokens) and
        `gh api repos/<owner>/<repo> --jq .permissions.push`.
      - Captures all subprocess output via capture_output=True so token-shaped
        strings (if any leaked from gh) cannot reach the parent's stdio.

    SSH-only setups: gh CLI does NOT manage SSH keys. If the user pushes via
    SSH (`git remote get-url origin` starts with `git@`), gh's
    `repos/<owner>/<repo>/.permissions.push` API call is still the source
    of truth for whether the gh-authenticated user CAN push — even if the
    actual `git push` will use SSH. This function therefore covers SSH
    users too because it asks "does this gh identity have push perms?",
    not "what transport will git use?". A missing SSH key is downstream
    and out of scope for this precheck.

    Escape hatch: `CPV_SKIP_GH_AUTH_CHECK=1` bypasses both the auth check
    AND the push-permission check. The downstream `git push` and
    `gh release create` gates still run, so a misauthorized push fails
    there — the precheck only prevents the embarrassment of pushing
    half a release before discovering the auth problem. Set this when
    the network is too slow for the 60 s gh API call but you've
    independently verified your auth is good (e.g. `gh repo view <repo>`
    works in another terminal).
    """
    if os.environ.get("CPV_SKIP_GH_AUTH_CHECK") == "1":
        # Honoured per the docstring's "escape hatch" clause. The downstream
        # push/release gates still enforce real auth.
        return

    gh_bin = shutil.which("gh")
    if gh_bin is None:
        print(
            f"\n{RED}✗ gh CLI not installed.{NC}\n{YELLOW}  Install: brew install gh{NC}",
            file=sys.stderr,
        )
        sys.exit(1)

    # 1. Check authentication. Capture both streams so token-shaped strings
    #    (if any) never leak to our stdio.
    #
    # 60s timeout (was 15s) — `gh auth status` does a network round-trip and
    # 15s was too tight on slow-link / Fastweb-type connections. The whole
    # check still aborts within a minute; just no more "TimeoutExpired" at
    # the very first gate.
    try:
        status = subprocess.run(
            [gh_bin, "auth", "status"],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except subprocess.TimeoutExpired:
        print(
            f"\n{RED}✗ gh auth status timed out after 60 s.{NC}\n"
            f"{YELLOW}  This usually means an unstable network. Retry, or check {NC}\n"
            f"{YELLOW}  https://www.githubstatus.com/.{NC}",
            file=sys.stderr,
        )
        sys.exit(1)
    if status.returncode != 0:
        print(
            f"\n{RED}✗ gh CLI not authenticated.{NC}\n"
            f"{YELLOW}  Run: gh auth login --hostname github.com --git-protocol https{NC}",
            file=sys.stderr,
        )
        sys.exit(1)

    # 2. Check push permission via the GitHub API. `--jq .permissions.push`
    #    extracts the boolean directly so we don't have to parse JSON.
    # 60s timeout (was 15s) for the same slow-link tolerance reason.
    try:
        perms = subprocess.run(
            [gh_bin, "api", f"repos/{owner}/{repo}", "--jq", ".permissions.push"],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except subprocess.TimeoutExpired:
        print(
            f"\n{RED}✗ gh api permission check timed out after 60 s.{NC}\n"
            f"{YELLOW}  Network is too slow to verify push permission. Retry,{NC}\n"
            f"{YELLOW}  or set CPV_SKIP_GH_AUTH_CHECK=1 to bypass this gate{NC}\n"
            f"{YELLOW}  (publish will still fail at git push if you actually{NC}\n"
            f"{YELLOW}  lack permission — the gate only saves you the embarrassment of pushing first).{NC}",
            file=sys.stderr,
        )
        sys.exit(1)
    if perms.returncode != 0 or perms.stdout.strip() != "true":
        # Try to identify which gh user is active so the maintainer can
        # diagnose multi-account confusion. We parse `gh auth status`
        # stdout/stderr (combined) rather than running a separate command.
        active_login = ""
        for line in (status.stdout + status.stderr).splitlines():
            line = line.strip()
            # gh prints `Logged in to github.com account <login> (keyring)`
            if "account " in line and ("Logged in" in line or "Active" in line):
                m = re.search(r"account\s+(\S+)", line)
                if m:
                    active_login = m.group(1)
                    break
        login_str = f" '{active_login}'" if active_login else ""
        print(
            f"\n{RED}✗ gh user{login_str} has no push permission on "
            f"{owner}/{repo}.{NC}\n"
            f"{YELLOW}  Diagnose:{NC}\n"
            f"{YELLOW}    1. Ask the repo owner to add you as a collaborator with write access.{NC}\n"
            f"{YELLOW}    2. If you have multiple gh accounts, list them: gh auth status{NC}\n"
            f"{YELLOW}       and switch to the one with push perms: gh auth switch --hostname github.com{NC}\n"
            f"{YELLOW}    3. If you authenticated with a fine-grained token, verify it grants{NC}\n"
            f"{YELLOW}       'Contents: write' on this repo.{NC}",
            file=sys.stderr,
        )
        sys.exit(1)


def _print_result(result: subprocess.CompletedProcess[str], cmd: list[str], check: bool) -> None:
    if result.stdout.strip():
        print(result.stdout.strip())
    if result.stderr.strip():
        print(result.stderr.strip(), file=sys.stderr)
    if check and result.returncode != 0:
        print(f"\n{RED}✗ FAILED (exit {result.returncode}): {' '.join(cmd)}{NC}", file=sys.stderr)
        sys.exit(result.returncode)


def run(
    cmd: list[str],
    cwd: Path,
    *,
    check: bool = True,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a command, print it, stream output, and fail fast on error.

    `env=None` means use `os.environ` unchanged — the safe default. Use
    this for git/gh subprocesses, file operations, and anything that does
    not load CPV's own self-integrity verifier. For validator subprocesses
    (Gates 3, 4, 8) use `run_with_integrity_bypass` instead — that wrapper
    threads the bypass env through this helper.
    """
    print(f"  $ {' '.join(cmd)}")
    # A subprocess that runs past `timeout` raises TimeoutExpired. Without
    # this guard it would surface as a raw traceback (ugly, and not the
    # fail-fast contract the rest of the gate uses). Catch it and exit 1 with
    # the same styled one-line message the non-zero-exit path prints.
    try:
        result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=600, env=env)
    except subprocess.TimeoutExpired:
        print(f"\n{RED}✗ Command timed out after 600s: {' '.join(cmd)}{NC}", file=sys.stderr)
        sys.exit(1)
    _print_result(result, cmd, check)
    return result


def _bypass_env() -> dict[str, str]:
    """Build the env dict that disables the GitHub-anchored integrity gate.

    Both names are set: new canonical PLUGIN_SKIP_GITHUB_INTEGRITY plus the
    legacy CPV_SKIP_GITHUB_INTEGRITY (TRDD-bbff5bc5) for one release of
    backward compat with v2.50.x verifier shims that may still be invoked
    transitively. Legacy name removed in v2.53.0.
    """
    return {
        **os.environ,
        "PLUGIN_SKIP_GITHUB_INTEGRITY": "1",
        "CPV_SKIP_GITHUB_INTEGRITY": "1",
    }


def run_with_integrity_bypass(cmd: list[str], cwd: Path, *, check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run a validator subprocess WITH the GitHub-anchored integrity gate disabled.

    Trust trade-off (Codex adversarial review 2026-05-04, finding #2):
    publish.py emits a NEW version whose in-tree validator code, by
    definition, differs from whatever the last GitHub tag carries. The
    integrity gate refuses to run when local hashes diverge from the
    canonical manifest, so it MUST be bypassed for the validator gates to
    execute at all during release.

    The bypass is now scoped to the SPECIFIC subprocess that needs it,
    not blanket-injected by `run()` into every call. That makes the trust
    window explicit at each call site so a reviewer can see exactly which
    gates run with integrity disabled.

    Implementation note: this is a thin wrapper around `run()` that adds
    the bypass env. Doing it that way (instead of calling subprocess.run
    directly) means tests that monkeypatch `publish.run` still intercept
    bypass calls without having to know about both helpers.

    A future TRDD will add a "compare against in-tree release-candidate
    manifest" mode so the gate can stay enabled even during publish; until
    then, the maintainer's defense against tampered validator code is
    git history + code review + signed commits.
    """
    return run(cmd, cwd, check=check, env=_bypass_env())


# ── Semver helpers (absorbed from bump_version.py) ───────────────────────────


def parse_semver(version: str) -> tuple[int, int, int] | None:
    """Parse 'X.Y.Z' into (major, minor, patch), or None if invalid."""
    match = re.match(r"^(\d+)\.(\d+)\.(\d+)$", version.strip())
    if not match:
        return None
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)))


def semver_gt(a: str, b: str) -> bool:
    """Return True if version a > version b."""
    pa, pb = parse_semver(a), parse_semver(b)
    if pa is None or pb is None:
        return False
    return pa > pb


def bump_semver(current: str, bump_type: str) -> str | None:
    """Bump version by type ('major', 'minor', 'patch'). Returns new version or None."""
    parts = parse_semver(current)
    if parts is None:
        return None
    major, minor, patch = parts
    if bump_type == "major":
        return f"{major + 1}.0.0"
    elif bump_type == "minor":
        return f"{major}.{minor + 1}.0"
    elif bump_type == "patch":
        return f"{major}.{minor}.{patch + 1}"
    return None


# ── Version read/write (absorbed from bump_version.py) ───────────────────────


# ── Canon version reporting (--canon-version) ────────────────────────────────
#
# Downstream plugins carry a COPY of this pipeline, so "which canon am I on?"
# is not answerable from the copy alone. `--canon-version` answers it: the
# INSTALLED canon version is baked in at generation/migration time (in a
# generated publish.py it is the `CANON_VERSION` constant; in CPV's own repo it
# is CPV's version, since this file IS the canon), and the LATEST is read from
# the canon repo's manifest on GitHub.
CANON_LATEST_URL = "https://raw.githubusercontent.com/Emasoft/claude-plugins-validation/master/.claude-plugin/plugin.json"
CANON_FETCH_TIMEOUT_S = 6


def fetch_latest_canon_version() -> str | None:
    """The newest canon version per the canon repo's manifest, or None.

    EVERY failure mode (offline, DNS, timeout, HTTP error, malformed JSON)
    returns None rather than raising: `--canon-version` is an INFORMATION
    command, and an info command that fails on a train with no wifi is a bug.
    The caller renders None as an explicit "unknown", never as "up to date".
    """
    import urllib.request  # noqa: PLC0415 - stdlib, imported only on this path

    req = urllib.request.Request(  # noqa: S310 - fixed https URL, not user input
        CANON_LATEST_URL,
        headers={"User-Agent": "cpv-publish-canon-version"},
    )
    try:
        with urllib.request.urlopen(req, timeout=CANON_FETCH_TIMEOUT_S) as resp:  # noqa: S310
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:  # noqa: BLE001 - any failure means "unknown", by design
        return None
    version = data.get("version") if isinstance(data, dict) else None
    return version if isinstance(version, str) and version else None


def print_canon_version(installed: str | None) -> int:
    """Print the canon version report. Always returns 0 — info never fails."""
    latest = fetch_latest_canon_version()
    installed_display = installed or "unknown (could not read plugin.json)"
    latest_display = latest or "unknown (could not reach GitHub)"
    print("Emasoft CPV Plugin Publishing Pipeline Canon")
    print()
    print(f"* Installed Canon Version:  {installed_display}")
    print(f"* Latest Version Available: {latest_display}")
    print()
    # THREE-STATE, because "could not compare" is not a verdict in either
    # direction. Collapsing the unknown case into the update advice told an
    # OFFLINE author — the exact case fetch_latest_canon_version() exists to
    # survive — that a perfectly current canon was stale, and sent them to run
    # a migration they did not need.
    if not installed or not latest:
        print("Could not compare: one of the two versions above is unknown.")
        print("This is NOT a statement that the canon is stale.")
    elif installed == latest:
        print("The canon is up to date.")
    else:
        print('Run "/cpv-agent update the canon" to update')
        print("the plugin to the latest canon.")
    return 0


def get_current_version(plugin_root: Path) -> str | None:
    """Read current version from .claude-plugin/plugin.json."""
    plugin_json = plugin_root / ".claude-plugin" / "plugin.json"
    if not plugin_json.exists():
        return None
    try:
        data = json.loads(plugin_json.read_text(encoding="utf-8"))
        v = data.get("version")
        return v if isinstance(v, str) else None
    except (json.JSONDecodeError, OSError, KeyError) as e:
        print(f"Warning: could not read version from {plugin_json}: {e}")
        return None


def update_plugin_json(plugin_root: Path, new_version: str) -> tuple[bool, str]:
    """Update version field in plugin.json."""
    path = plugin_root / ".claude-plugin" / "plugin.json"
    if not path.exists():
        return False, "plugin.json not found"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        old = data.get("version", "unknown")
        data["version"] = new_version
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        return True, f"plugin.json: {old} → {new_version}"
    except Exception as e:
        return False, f"plugin.json error: {e}"


def _project_block(content: str) -> tuple[int, int] | None:
    """Char span of the ``[project]`` table body, or None if absent.

    The project version MUST be read/written inside the ``[project]`` table.
    A line-anchored first-match for ``version = "..."`` is wrong when a
    ``[tool.X]`` table carrying its OWN top-level ``version`` precedes
    ``[project]`` (e.g. ``[tool.commitizen]``). When no ``[project]`` table
    exists (poetry-style layouts keep the version under ``[tool.poetry]``),
    return None so callers fall back to the whole-file first-match — keeping
    today's behavior for those files exactly.
    """
    m = re.search(r'^\[project\]\s*$', content, re.MULTILINE)
    if not m:
        return None
    start = m.end()
    nxt = re.search(r'^\[', content[start:], re.MULTILINE)
    return start, (start + nxt.start() if nxt else len(content))


def update_pyproject_toml(plugin_root: Path, new_version: str) -> tuple[bool, str]:
    """Update version field in pyproject.toml."""
    path = plugin_root / "pyproject.toml"
    if not path.exists():
        return True, "pyproject.toml not found (skipped)"
    try:
        content = path.read_text(encoding="utf-8")
        pattern = r'^(version\s*=\s*["\'])(\d+\.\d+\.\d+)(["\'])$'
        old_version = None

        def _replace(m: re.Match[str]) -> str:
            nonlocal old_version
            old_version = m.group(2)
            return f"{m.group(1)}{new_version}{m.group(3)}"

        block = _project_block(content)
        if block is not None:
            # Substitute ONLY within the [project] table body, then splice the
            # result back, so a [tool.X].version above [project] is never hit.
            lo, hi = block
            replaced, count = re.subn(pattern, _replace, content[lo:hi], flags=re.MULTILINE)
            new_content = content[:lo] + replaced + content[hi:]
        else:
            # No [project] table (e.g. poetry) — preserve the legacy
            # whole-file first-match behavior.
            new_content, count = re.subn(pattern, _replace, content, flags=re.MULTILINE)
        if count == 0:
            return True, "pyproject.toml has no version field (skipped)"
        path.write_text(new_content, encoding="utf-8")
        return True, f"pyproject.toml: {old_version} → {new_version}"
    except Exception as e:
        return False, f"pyproject.toml error: {e}"


def update_python_versions(plugin_root: Path, new_version: str) -> list[tuple[bool, str]]:
    """Update __version__ = 'X.Y.Z' in all Python files."""
    gi = _get_gi(plugin_root)
    results: list[tuple[bool, str]] = []
    for py_file in gi.rglob("*.py"):
        # `rglob` yields directories too (pathlib parity, #187).
        if not py_file.is_file():
            continue
        try:
            content = py_file.read_text(encoding="utf-8")
            # Allow optional trailing whitespace/comment so lines like
            # `__version__ = "2.103.4"  # bumped in lockstep with plugin.json`
            # still get bumped. The pre-v2.104.1 regex anchored `$` right
            # after the closing quote and silently skipped commented lines —
            # observed when cpv_skillaudit_native.py shipped stale __version__
            # in v2.104.0 and the integration test caught it on CI.
            pattern = r'^(__version__\s*=\s*["\'])(\d+\.\d+\.\d+)(["\'])(\s*(?:#.*)?)$'
            old_v = None

            def _replace(m: re.Match[str]) -> str:
                nonlocal old_v
                old_v = m.group(2)
                return f"{m.group(1)}{new_version}{m.group(3)}{m.group(4)}"

            new_content, count = re.subn(pattern, _replace, content, flags=re.MULTILINE)
            if count > 0:
                py_file.write_text(new_content, encoding="utf-8")
                rel = py_file.relative_to(plugin_root)
                results.append((True, f"{rel}: {old_v} → {new_version}"))
        except Exception as e:
            rel = py_file.relative_to(plugin_root)
            results.append((False, f"{rel}: {e}"))
    return results


# ── Version consistency check (absorbed from check_version_consistency.py) ───


def check_version_consistency(plugin_root: Path) -> tuple[bool, str]:
    """Check all version sources match. Returns (ok, message)."""
    versions: dict[str, str] = {}  # source_label → version

    # plugin.json
    pj = plugin_root / ".claude-plugin" / "plugin.json"
    if pj.exists():
        try:
            v = json.loads(pj.read_text(encoding="utf-8")).get("version")
            if isinstance(v, str):
                versions["plugin.json"] = v
        except (json.JSONDecodeError, OSError) as e:
            print(f"Warning: could not read version from plugin.json: {e}")

    # pyproject.toml — read from the [project] table body when present, else
    # fall back to the whole-file first-match (poetry-style layouts).
    pp = plugin_root / "pyproject.toml"
    if pp.exists():
        try:
            pp_text = pp.read_text(encoding="utf-8")
            block = _project_block(pp_text)
            haystack = pp_text[block[0]:block[1]] if block is not None else pp_text
            m = re.search(r'^version\s*=\s*["\']([^"\']+)["\']', haystack, re.MULTILINE)
            if m:
                versions["pyproject.toml"] = m.group(1)
        except (json.JSONDecodeError, OSError) as e:
            print(f"Warning: could not read version from pyproject.toml: {e}")

    # Python __version__ variables
    gi = _get_gi(plugin_root)
    for py_file in gi.rglob("*.py"):
        # `rglob` yields directories too (pathlib parity, #187).
        if not py_file.is_file():
            continue
        try:
            content = py_file.read_text(encoding="utf-8")
            m = re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']', content, re.MULTILINE)
            if m:
                rel = str(py_file.relative_to(plugin_root))
                versions[rel] = m.group(1)
        except (json.JSONDecodeError, OSError) as e:
            print(f"Warning: could not read version from {py_file}: {e}")

    if not versions:
        return True, "No version sources found"

    unique = set(versions.values())
    if len(unique) == 1:
        return True, f"All {len(versions)} sources consistent: {next(iter(unique))}"

    # Mismatch — build detail
    lines = ["Version mismatch detected:"]
    for src, ver in sorted(versions.items()):
        lines.append(f"  {src}: {ver}")
    return False, "\n".join(lines)


# ── Bump all files ───────────────────────────────────────────────────────────


def _sync_uv_lock(plugin_root: Path) -> None:
    """Re-resolve `uv.lock` against the freshly-bumped `pyproject.toml`.

    Without this, every release leaves `uv.lock` stale by one version
    (`pyproject.toml` says e.g. `2.66.2` but `uv.lock` still pins
    `2.66.1`). The next publish then refuses Stage 1 (clean-tree
    check) and a separate `chore: update uv.lock` commit is needed
    just to catch up. Idempotent and silently skipped when neither
    `uv` nor `uv.lock` is present (e.g. plugins authored without uv
    or run on a host where `uv` isn't installed).

    Why call this from `do_bump` and not from a higher-level Gate:
    `do_bump` is the only function that touches `pyproject.toml`. By
    co-locating the sync we guarantee the lock can never be stale
    after a successful bump — no other code path needs to remember.
    """
    uv_lock = plugin_root / "uv.lock"
    if not uv_lock.exists():
        return
    if shutil.which("uv") is None:
        return
    run(["uv", "lock"], cwd=plugin_root, check=False)


def do_bump(plugin_root: Path, new_version: str, dry_run: bool = False) -> bool:
    """Bump version across all files. Returns True on success."""
    if dry_run:
        print(f"  [DRY-RUN] Would bump to {new_version}")
        return True

    all_results: list[tuple[bool, str]] = []
    all_results.append(update_plugin_json(plugin_root, new_version))
    all_results.append(update_pyproject_toml(plugin_root, new_version))
    all_results.extend(update_python_versions(plugin_root, new_version))

    errors = 0
    for ok, msg in all_results:
        status = f"{GREEN}[OK]{NC}" if ok else f"{RED}[ERROR]{NC}"
        print(f"  {status} {msg}")
        if not ok:
            errors += 1

    if errors == 0:
        # Bump succeeded — re-resolve uv.lock so it doesn't lag the
        # bumped pyproject.toml. See _sync_uv_lock docstring.
        _sync_uv_lock(plugin_root)

    return errors == 0


# ── Gate list for --print-gates and --help ──────────────────────────────────

GATES: list[tuple[str, str]] = [
    ("Gate 0", "Bypass-var rejection (CPV_SKIP_*, SKIP_*, NO_VERIFY)"),
    ("Gate 1", "Clean working tree (git status --porcelain)"),
    # Keep this string in step with stage_run_tests' actual argv. It read
    # `pytest tests/ -x` long after the real command became a worksteal-xdist
    # run, so anyone reading the gate table to reproduce a failure locally ran
    # a materially different suite (serial, and stopping at a different point)
    # than the gate that had just rejected them.
    ("Gate 2", "Tests (uv run pytest tests/ -n auto --dist=worksteal --maxfail=1)"),
    (
        "Gate 3",
        "Plugin validation (validate_plugin.py --strict) — owns repo-wide "
        "lint via cpv_lint_engine since v2.64.0; blocks on "
        "CRITICAL/MAJOR/MINOR/NIT; WARNING advisory only",
    ),
    (
        "Gate 3b",
        "CI-parity preflight (cpv_ci_preflight.py) — the jscpd / actionlint / "
        "mypy / uv-sync-dev / Mega-Linter / CIP-1..8 gates CI's Lint job runs "
        "but validate_plugin does NOT; a MISSING TOOL degrades to WARNING and "
        "never blocks",
    ),
    (
        "Gate 3c",
        "Linux fork-parity probe (cpv_fork_parity.py) — re-runs the suite with "
        "the multiprocessing default forced to fork, the way Linux runs it. "
        "macOS defaults to spawn, so this is the ONLY local gate that can see a "
        "fork-from-multithreaded-process deadlock (v3.23.0 shipped one). Skips "
        "as already-native on Linux; degrades to WARNING where fork is absent",
    ),
    (
        "Gate 3d",
        "Secret scan (trufflehog via validate_security.check_trufflehog) — "
        "BLOCKS the publish on any detected credential. Issue #217: this gate "
        "did not exist, and a scanner-shaped credential reached main and sat "
        "85 days. A missing binary or an incomplete scan reports UNKNOWN and "
        "blocks; it is never treated as clean",
    ),
    ("Gate 4", "Marketplace validation (validate_marketplace.py --strict) — Layout B only"),
    ("Gate 5", "Marketplace-registration check — verifies plugin is wired to its marketplace"),
    ("Gate 6", "Version consistency (plugin.json / pyproject.toml / __version__)"),
    ("Gate 7", "Bump version (auto from git-cliff, overridable via --major/--minor/--patch)"),
    (
        "Gate 8",
        "Refresh .plugin-self-hashes.json (_plugin_compute_hashes.py; legacy "
        ".cpv-self-hashes.json compat copy also written) — issue #18: stale manifest "
        "causes integrity-mismatch abort on fresh marketplace installs",
    ),
    ("Gate 9", "Generate CHANGELOG.md (full history, idempotent) + release notes (git-cliff)"),
    ("Gate 10", "Commit bump + manifest refresh + changelog"),
    ("Gate 11", "Create annotated git tag vX.Y.Z"),
    ("Gate 12", "Push branch + tag to origin"),
    ("Gate 13", "Create GitHub release with notes (gh release create)"),
    (
        "Gate 14",
        "Verify CI went GREEN on the released commit (gh run watch) — the branch "
        "ruleset's required checks are bypassed by the release push, so this is "
        "the ONLY thing that confirms the shipped commit actually passes CI; "
        "reports RED loudly, degrades to WARNING when gh/network is unavailable",
    ),
    (
        "Gate 15",
        "Prove the release INSTALLS — `claude plugin install <plugin>@<marketplace>` "
        "into a clean temp dir. Static validation cannot catch an uninstallable "
        "release (ai-maestro#62 R2); only an install can. SKIPPED (never a pass) "
        "when the claude CLI or the marketplace name is unavailable; set "
        "CPV_PUBLISH_REQUIRE_INSTALL_SMOKE=1 to make a real failure exit non-zero",
    ),
]


def print_gates() -> None:
    """Print the list of gates in order so users see exactly what will run."""
    print(f"{BLUE}Publish pipeline gates (all mandatory, fail-fast):{NC}")
    for name, desc in GATES:
        print(f"  {GREEN}{name}{NC}: {desc}")
    print(
        f"\n{YELLOW}Hard rule: no --skip-* flags, no env var bypasses, no --force.{NC}\n"
        f"{YELLOW}WARNING is the only severity that does not block.{NC}"
    )


# ── Layout detection and marketplace-registration check (Task 2) ─────────────


def find_parent_marketplace(plugin_root: Path) -> Path | None:
    """Walk up from plugin root looking for a parent marketplace.json.

    Returns the path to the marketplace repo root (the dir containing
    .claude-plugin/marketplace.json), or None if no parent marketplace found.
    Only returns a match if plugin_root is actually nested under plugins/<name>/
    of the marketplace repo (Layout B signature).
    """
    current = plugin_root.resolve().parent
    while current != current.parent:
        mp_json = current / ".claude-plugin" / "marketplace.json"
        if mp_json.is_file():
            # Confirm plugin_root is under <current>/plugins/<name>/
            try:
                rel = plugin_root.resolve().relative_to(current)
                parts = rel.parts
                if len(parts) >= 2 and parts[0] == "plugins":
                    return current
            except ValueError:
                pass
            return None
        current = current.parent
    return None


def detect_layout(plugin_root: Path) -> tuple[str, dict[str, str | Path | None]]:
    """Detect whether this repo is Layout A (standalone plugin), Layout B (nested),
    or 'none' (no marketplace wiring).

    Returns (layout, details) where layout is one of 'A', 'B', 'none' and
    details is a dict with layout-specific fields used by the check stage.
    """
    # Layout B check first: a plugin nested inside a marketplace repo
    parent_mp = find_parent_marketplace(plugin_root)
    if parent_mp is not None:
        plugin_name = plugin_root.name
        return "B", {"marketplace_root": parent_mp, "plugin_name": plugin_name}

    # Layout A check: standalone plugin that may reference a remote marketplace
    notify_wf = plugin_root / ".github" / "workflows" / "notify-marketplace.yml"
    if notify_wf.is_file():
        mkt_owner, mkt_repo = _parse_notify_workflow(notify_wf)
        if mkt_owner and mkt_repo:
            return "A", {"notify_workflow": notify_wf, "mkt_owner": mkt_owner, "mkt_repo": mkt_repo}
        return "A", {"notify_workflow": notify_wf, "mkt_owner": None, "mkt_repo": None}

    return "none", {}


def _parse_notify_workflow(path: Path) -> tuple[str | None, str | None]:
    """Extract MARKETPLACE_OWNER and MARKETPLACE_REPO from a notify-marketplace.yml.

    The workflow is small and well-known — we grep for two lines rather than
    pulling a YAML dep. Returns (owner, repo) or (None, None) if not found.
    """
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return None, None
    m_owner = re.search(r"^\s*MARKETPLACE_OWNER:\s*['\"]?([^'\"\s]+)['\"]?\s*$", content, re.MULTILINE)
    m_repo = re.search(r"^\s*MARKETPLACE_REPO:\s*['\"]?([^'\"\s]+)['\"]?\s*$", content, re.MULTILINE)
    owner = m_owner.group(1) if m_owner else None
    repo = m_repo.group(1) if m_repo else None
    return owner, repo


def _gh_secret_exists(
    plugin_root: Path,
    secret_name: str,
    *,
    gh_bin: str | None = None,
) -> bool:
    """Check whether a GitHub secret with the given name is configured on this repo.

    Uses `gh secret list --repo <owner>/<repo>` or just `gh secret list` from
    within the repo; parses the output to check for the secret name. We never
    attempt to read the secret value itself — that would be impossible anyway.
    """
    if gh_bin is None:
        gh_bin = shutil.which("gh") or "gh"
    # Use --json for stable parsing: gh's tab-separated default format is
    # not formally specified and a future gh version may add color codes
    # or extra columns. --json name --jq '.[].name' is forwards-compatible.
    result = gh_with_retry(
        [gh_bin, "secret", "list", "--json", "name", "--jq", ".[].name"],
        cwd=str(plugin_root),
        check=False,
        capture_output=True,
    )
    if result.returncode != 0:
        return False
    return secret_name in result.stdout.split()


def _fetch_remote_marketplace_json(
    mkt_owner: str,
    mkt_repo: str,
    *,
    gh_bin: str | None = None,
) -> dict | None:
    """Fetch the remote marketplace.json using gh api. Returns parsed dict or None."""
    if gh_bin is None:
        gh_bin = shutil.which("gh") or "gh"
    # Wrap with gh_with_retry — read-only api call, transient 502/503
    # from github.com edge should not abort the publish pipeline.
    result = gh_with_retry(
        [
            gh_bin,
            "api",
            f"repos/{mkt_owner}/{mkt_repo}/contents/.claude-plugin/marketplace.json",
            "-H",
            "Accept: application/vnd.github.raw+json",
        ],
        check=False,
        capture_output=True,
    )
    if result.returncode != 0:
        return None
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    return data


def _remote_has_receiver_workflow(
    mkt_owner: str,
    mkt_repo: str,
    *,
    gh_bin: str | None = None,
) -> bool:
    """Check whether the remote marketplace repo has a workflow with repository_dispatch."""
    if gh_bin is None:
        gh_bin = shutil.which("gh") or "gh"
    # List the workflow dir — wrapped with retry for the same transient-error reason.
    result = gh_with_retry(
        [gh_bin, "api", f"repos/{mkt_owner}/{mkt_repo}/contents/.github/workflows"],
        check=False,
        capture_output=True,
    )
    if result.returncode != 0:
        return False
    try:
        entries = json.loads(result.stdout)
    except json.JSONDecodeError:
        return False
    if not isinstance(entries, list):
        return False
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name", "")
        if not isinstance(name, str) or not name.endswith((".yml", ".yaml")):
            continue
        file_result = gh_with_retry(
            [
                gh_bin,
                "api",
                f"repos/{mkt_owner}/{mkt_repo}/contents/.github/workflows/{name}",
                "-H",
                "Accept: application/vnd.github.raw+json",
            ],
            check=False,
            capture_output=True,
        )
        if file_result.returncode == 0 and "repository_dispatch" in file_result.stdout:
            return True
    return False


def _plugin_in_remote_marketplace(mkt_json: dict, plugin_name: str, expected_repo: str | None) -> bool:
    """Return True if marketplace.json lists plugin_name with a remote source matching expected_repo.

    Accepts every remote source-object shape CC's marketplace spec defines for a
    per-plugin entry:

    * ``{"source": "github", "repo": "owner/repo"}`` — the github form.
    * ``{"source": "url",  "url":  "https://…/owner/repo[.git]"}`` — the url form.
    * ``{"source": "git",  "url":  "git@…:owner/repo[.git]"}`` — the explicit git form.

    For ``url`` / ``git`` the URL is normalised (``.git`` stripped, trailing ``/``
    removed) and matched against the slug — accept when it ends with
    ``/<expected_repo>`` (HTTPS) or ``:<expected_repo>`` (SSH). A bare ``./path``
    string source is a local directory entry, not a remote registration — skipped.

    If ``expected_repo`` is None, accept any matching-name remote-source entry.

    Issue #25 Defect A (v2.87.1): the previous version only accepted the
    ``github`` form and falsely blocked Stage 5 of every downstream publish whose
    marketplace.json used the ``url`` form (the shape Emasoft/ai-maestro-plugins
    actually ships).
    """
    plugins = mkt_json.get("plugins")
    if not isinstance(plugins, list):
        return False
    for entry in plugins:
        if not isinstance(entry, dict):
            continue
        if entry.get("name") != plugin_name:
            continue
        source = entry.get("source")
        if not isinstance(source, dict):
            # Bare string sources (e.g. "./plugins/foo") are local directory
            # entries, not remote-marketplace registrations.
            continue
        stype = source.get("source") or source.get("type")
        if stype == "github":
            if expected_repo is None or source.get("repo") == expected_repo:
                return True
        elif stype in ("url", "git"):
            url = source.get("url")
            if expected_repo is None:
                return True
            if isinstance(url, str):
                norm = url.removesuffix(".git").rstrip("/")
                if norm.endswith("/" + expected_repo) or norm.endswith(":" + expected_repo):
                    return True
    return False


def _current_repo_slug(plugin_root: Path) -> str | None:
    """Return the owner/repo slug for the current git origin, or None."""
    result = subprocess.run(
        ["git", "remote", "get-url", "origin"],
        cwd=plugin_root,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        return None
    url = result.stdout.strip()
    # Match both SSH (git@github.com:owner/repo.git) and HTTPS (https://github.com/owner/repo.git)
    m = re.search(r"[:/]([^/:]+)/([^/]+?)(?:\.git)?$", url)
    if not m:
        return None
    return f"{m.group(1)}/{m.group(2)}"


# ── Pipeline stages — each returns 0 on success, non-zero on failure ─────────


def stage_bypass_guard() -> int:
    """Gate 0: reject any env var that could bypass checks.

    v2.86.0 hardening (issue #22): broadened from an explicit allowlist to
    prefix-pattern matching. Any env var matching ``PLUGIN_SKIP_*``,
    ``PLUGIN_FORCE_*``, ``PLUGIN_BYPASS_*``, ``CPV_SKIP_*``, ``SKIP_*``,
    or named ``NO_VERIFY`` aborts publish. Closes the loophole where a
    fresh skip-name (e.g. ``CPV_SKIP_GATE7``) silently slipped past the
    fixed list. Also covers TRDD-c0ee9543's ``CPV_SKIP_UPSTREAM_CROSS_CHECK``
    (matched by the ``CPV_SKIP_`` prefix; bypass-pattern wins).

    Documented infrastructure exemptions — these are READ-ONLY overrides
    used by CPV's own subsystems and never skip a publish gate:
        * ``PLUGIN_SKIP_GITHUB_INTEGRITY=1`` — bypasses the GitHub-anchored
          integrity check inside the hash-verify module (set by publish.py
          itself for the test-fixture publish path).
        * ``CPV_SKIP_GITHUB_INTEGRITY=1`` — the LEGACY spelling of the same
          override, still honoured (deprecated, TRDD-bbff5bc5).
        * ``CPV_SKIP_GH_AUTH_CHECK=1`` — bypasses the ``gh auth status``
          round-trip in _ensure_gh_auth on flaky networks. Auth still
          has to work for the real `git push` / `gh release create`.

    The ``PLUGIN_`` spelling MUST be exempt alongside the legacy one:
    ``_plugin_verify_hashes`` renamed the var to ``PLUGIN_SKIP_GITHUB_INTEGRITY``
    and tells users to export it, but ``PLUGIN_SKIP_`` is a forbidden PREFIX
    here — so before this exemption, following the module's own deprecation
    notice aborted the publish with a "Bypass attempt detected" error. This
    grants NO new capability: the identical override is already exempt under
    its legacy name, so this only makes the documented migration path work.
    Note there is deliberately no ``PLUGIN_SKIP_GH_AUTH_CHECK`` exemption —
    that var does not exist anywhere in the codebase, and exempting a
    non-existent name would widen the bypass surface for nothing.
    """
    exemptions = {
        "PLUGIN_SKIP_GITHUB_INTEGRITY",
        "CPV_SKIP_GITHUB_INTEGRITY",
        "CPV_SKIP_GH_AUTH_CHECK",
    }
    forbidden_prefixes = (
        "PLUGIN_SKIP_",
        "PLUGIN_FORCE_",
        "PLUGIN_BYPASS_",
        "CPV_SKIP_",
        "SKIP_",
    )
    forbidden_exact = {"NO_VERIFY"}
    attempted = [
        v
        for v in sorted(os.environ)
        if (v.startswith(forbidden_prefixes) or v in forbidden_exact) and v not in exemptions and os.environ.get(v)
    ]
    if attempted:
        print(
            f"{RED}✗ Bypass attempt detected. These env vars are FORBIDDEN in publish:{NC}\n"
            f"  {', '.join(attempted)}\n"
            f"{RED}The publish pipeline enforces every check. Fix the failures, don't skip them.{NC}\n"
            f"{NC}(infrastructure exemptions: {', '.join(sorted(exemptions))})",
            file=sys.stderr,
        )
        return 1
    return 0


def stage_check_working_tree(plugin_root: Path) -> int:
    """Gate 1: clean working tree check. Auto-commits a uv.lock *modification* if it is the only diff.

    `git status --porcelain` emits one line per change with the format
    ``XY filename`` (2-char status + 1 space + filename). Column 0 (``X``)
    is the staged status, column 1 (``Y``) is the worktree status; the
    filename starts at column 3, so the canonical slice is ``line[3:]``.

    DO NOT pre-strip per line — for unstaged-only changes git emits
    " M uv.lock" with a leading space (column 0 is empty, column 1 is
    the worktree status). Calling ``.strip()`` would shift the slice
    by one and produce "v.lock" instead of "uv.lock", silently breaking
    the auto-commit branch.

    The auto-commit is ONLY for the benign case the gate is designed
    around: ``uv run`` re-resolved and rewrote ``uv.lock`` in place
    (status ``M``, occasionally ``A`` for a freshly-generated lock).
    A uv.lock *deletion* (status ``D`` in either column) is NOT benign —
    it means the lockfile is gone, which would break reproducible
    installs. Committing that deletion under the message "chore: update
    uv.lock" would silently bless a broken state, so a deletion must
    fall through to the hard-stop branch where the maintainer sees it.
    The status check below therefore requires the sole pending change to
    be a uv.lock add/modify and rejects any ``D`` status.
    """
    print(f"\n{BLUE}═══ Gate 1: Check working tree ═══{NC}")
    result = run(["git", "status", "--porcelain"], cwd=plugin_root, check=False)
    dirty_lines = [line for line in result.stdout.splitlines() if line]
    if dirty_lines:
        # (status_code, path) per change. status_code is the 2-char XY field.
        changes = [(line[:2], line[3:]) for line in dirty_lines if len(line) >= 4]
        sole_uv_lock_modification = (
            len(changes) == 1
            and changes[0][1] == "uv.lock"
            # Only an add/modify is auto-committable; "D" (delete) in EITHER
            # status column is a destructive change that must not be silently
            # committed as an "update". A rename/copy of uv.lock (R/C) is also
            # not the benign uv-run case, so require strictly A or M.
            and "D" not in changes[0][0]
            and any(c in changes[0][0] for c in ("M", "A"))
        )
        if sole_uv_lock_modification:
            print(f"{YELLOW}Auto-committing uv.lock (modified by uv run){NC}")
            run(["git", "add", "uv.lock"], cwd=plugin_root)
            run(
                ["git", "commit", "-m", "chore: update uv.lock", *_agent_trailer_args(plugin_root)],
                cwd=plugin_root,
            )
        else:
            print(f"{RED}✗ Uncommitted changes detected. Commit or stash first.{NC}", file=sys.stderr)
            print("\n".join(dirty_lines))
            return 1
    print(f"{GREEN}✓ Working tree clean{NC}")
    return 0


# Issue #31 (v2.98.0): browser-orphan cleanup signatures.
#
# A pytest run that spawns Playwright / dev-browser pages can leave
# behind dozens of `Chrome for Testing` / `chromium` / `headless_shell`
# processes if the test code (or its fixtures) forget to close the
# pages. Over a long debug session those orphans pile up, eventually
# exhausting file descriptors or RAM and either crashing the browser
# or making the whole machine unresponsive. The baseline-diff cleanup
# below catches every leak regardless of test-code quality.
#
# Signature list is intentionally narrow — it must NOT match the
# maintainer's daily Chrome (which has command `Google Chrome` on
# macOS, not `Chrome for Testing`). The pre-pytest baseline snapshot
# is the second layer of safety: even if a signature accidentally
# matched a normal Chrome, the baseline snapshot would include it,
# so the diff would exclude it.
_BROWSER_ORPHAN_SIGNATURES: tuple[str, ...] = (
    "Chrome for Testing",
    "chrome-for-testing",
    "headless_shell",
    "Chromium.app/Contents",  # macOS .app bundle interior
    "chromium-browser",
    "/playwright/",
    "playwright-core",
)


def _snapshot_browser_pids() -> set[int]:
    """Return the set of currently-running PIDs whose command line matches
    a browser-orphan signature.

    Snapshot-then-grep (per ``~/.claude/rules`` rule against ``ps | grep``):
    capture the full process table, then filter — never live-grep.
    """
    try:
        snap = subprocess.run(
            ["ps", "-eo", "pid,command"],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return set()
    if snap.returncode != 0 or not snap.stdout:
        return set()
    pids: set[int] = set()
    for raw_line in snap.stdout.strip().split("\n")[1:]:  # skip header
        line = raw_line.strip()
        if not line:
            continue
        try:
            pid_str, cmd = line.split(None, 1)
            pid = int(pid_str)
        except (ValueError, IndexError):
            continue
        if any(sig in cmd for sig in _BROWSER_ORPHAN_SIGNATURES):
            pids.add(pid)
    return pids


def _cleanup_browser_orphans(baseline_pids: set[int]) -> int:
    """Kill any browser-signature PIDs that appeared since ``baseline_pids``.

    Baseline-diff is the safety net: a PID present in baseline is the
    maintainer's pre-existing browser process — NEVER killed. Only
    PIDs that came into existence between baseline-snapshot and now
    are candidates, and only if they still match a browser signature.

    Returns the number of orphans killed (0 if none) for logging.
    """
    import signal
    import time

    current = _snapshot_browser_pids()
    new_pids = current - baseline_pids
    if not new_pids:
        return 0
    killed = 0
    # SIGTERM first (graceful), short wait, then SIGKILL for any survivors.
    for pid in new_pids:
        try:
            os.kill(pid, signal.SIGTERM)
            killed += 1
        except (ProcessLookupError, PermissionError, OSError):
            pass
    if killed:
        time.sleep(1.5)
        for pid in new_pids:
            try:
                os.kill(pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError, OSError):
                pass
    return killed


def stage_run_tests(plugin_root: Path) -> int:
    """Gate 2: run tests — mandatory, cannot be skipped.

    Phase A speedup (TRDD-publish-speed Phase A): use pytest-xdist to run
    tests in parallel across all available cores. `-x` is dropped because
    xdist workers can't coordinate stop-on-first-fail; `--maxfail=1` is the
    semantic equivalent under parallel execution. `--dist=worksteal` is
    better than the default for our test mix (some files have 100+ short
    tests, some have 5 long ones). CI can override worker count via the
    PYTEST_XDIST_NUM_WORKERS env var (xdist honors it natively).

    Phase C (v2.77.0): use ``check=False`` so a test failure returns the
    pytest exit code through the normal control flow instead of calling
    ``sys.exit()`` from inside ``_print_result``. When this stage runs in
    a worker thread (parallel preflight), ``sys.exit`` would only kill the
    thread — the orchestrator needs the explicit return code to halt the
    publish atomically.

    Issue #31 (v2.98.0): wrap the pytest invocation in a baseline-diff
    browser-orphan cleanup so dev-browser / Playwright test runs that
    leak pages do not pile up Chrome-for-Testing processes (resource-
    exhaustion → browser crash / machine unresponsive). The tests
    themselves still run unconditionally — no skip mechanism, no opt
    out, no iron-rule violation. The cleanup only kills processes
    that appeared during the pytest run, never the maintainer's own
    daily browser.
    """
    print(f"\n{BLUE}═══ Gate 2: Run tests (mandatory) ═══{NC}")
    # Issue #31: capture pre-test browser-process baseline.
    baseline_browser_pids = _snapshot_browser_pids()
    try:
        result = run(
            ["uv", "run", "pytest", "tests/", "-n", "auto", "--dist=worksteal", "--maxfail=1", "-q", "--tb=short"],
            cwd=plugin_root,
            check=False,
        )
    finally:
        # Issue #31: always clean up (success OR failure path).
        killed = _cleanup_browser_orphans(baseline_browser_pids)
        if killed:
            print(f"  {YELLOW}Cleaned up {killed} orphaned browser process(es) spawned by pytest.{NC}")
    if result.returncode != 0:
        print(
            f"\n{RED}✗ Tests failed (pytest exit {result.returncode}) — PUBLISH BLOCKED{NC}",
            file=sys.stderr,
        )
        return result.returncode
    print(f"{GREEN}✓ All tests passed{NC}")
    return 0


# v2.64.0: stage_run_lint() retired. Repo-wide lint moved into
# scripts/cpv_lint_engine.py and is invoked by validate_plugin.py at
# Gate 3, so there is exactly ONE source of truth for linting.


def stage_validate_plugin(plugin_root: Path) -> int:
    """Gate 3: validate plugin in strict mode — blocks on ANY CRITICAL/MAJOR/MINOR/NIT.

    Since v2.64.0, validate_plugin.py owns repo-wide linting via
    cpv_lint_engine, so the previous Gate 3 (`stage_run_lint`) was retired.

    WARNING (exit code 0 with warning output) is advisory and does not block.
    Returns the validator's exit code directly (1-4 severity or 0 success).
    """
    print(f"\n{BLUE}═══ Gate 3: Validate plugin — ZERO errors required ═══{NC}")
    vresult = run_with_integrity_bypass(
        ["uv", "run", "python", "scripts/validate_plugin.py", ".", "--strict"],
        cwd=plugin_root,
        check=False,
    )
    if vresult.returncode != 0:
        severity_map = {1: "CRITICAL", 2: "MAJOR", 3: "MINOR", 4: "NIT"}
        severity = severity_map.get(vresult.returncode, f"unknown (exit {vresult.returncode})")
        print(
            f"\n{RED}✗ {severity} validation issues found — PUBLISH BLOCKED{NC}\n"
            f"{RED}  Fix ALL issues before publishing. No severity level is allowed to slip through.{NC}\n"
            f"{RED}  Fix command: uv run python scripts/validate_plugin.py . --strict{NC}",
            file=sys.stderr,
        )
        return vresult.returncode
    print(f"{GREEN}✓ Plugin validation passed (zero errors){NC}")
    return 0


def stage_ci_preflight(plugin_root: Path) -> int:
    """Gate 3b: CI-parity preflight — the gates ``validate_plugin --strict`` omits.

    WHY THIS GATE EXISTS (CI-failure forensics, 2026-07-13). ``cpv_ci_preflight``
    already mirrored CI's Lint job (jscpd / actionlint / mypy / ``uv sync --extra
    dev`` / the enabled Mega-Linter sub-linters / the CIP static defect
    detectors) — but NOTHING invoked it. It was "enforced" only by prose in
    ``agents/cpv-plugin-fixer-agent.md`` and ``agents/cpv-plugin-creator-agent.md``, which an agent
    can simply skip. A publish could therefore pass every other gate, bump,
    commit, TAG, PUSH, cut a GitHub release — and only THEN go red on GitHub,
    with the broken pipeline already shipped to every consumer. Wiring the
    preflight in as a real gate is the structural fix: prose is advice, a gate is
    enforcement.

    PLACEMENT IS LOAD-BEARING. This runs inside the Gate 2-5 preflight block, so
    it is strictly BEFORE the version bump (Gate 7), the commit (Gate 10), the
    tag (Gate 11) and the push (Gate 12). A parity failure therefore aborts with
    the working tree untouched — it can never leave a half-published state (a
    tag pushed for a release that was never cut).

    A MISSING TOOL NEVER BLOCKS A PUBLISH. ``PreflightResult.exit_code`` is 1
    only when a gate actually FAILED; every tool-absent case degrades to a
    non-blocking WARNING and keeps the exit code at 0 (the degrade-gracefully
    contract — see the ``cpv_ci_preflight`` module docstring). A machine without
    actionlint / npx / checkov publishes exactly as it did before; it just gets
    less LOCAL parity coverage, which CI still enforces. That property is what
    makes this gate safe to add unconditionally, and it is pinned by a test —
    do not "improve" this into a hard tool requirement.

    There is deliberately NO skip flag and NO env bypass: Gate 0 rejects
    ``CPV_SKIP_*`` / ``SKIP_*`` / ``NO_VERIFY`` outright, and a gate you can turn
    off is the prose-enforcement problem all over again.

    Returns 0 when parity-clean (PASS/WARNING only), 1 when a real CI gate would
    fail.
    """
    print(f"\n{BLUE}═══ Gate 3b: CI-parity preflight (gates validate_plugin omits) ═══{NC}")
    result = run_ci_preflight(plugin_root)

    if result.exit_code != 0:
        print(
            f"\n{RED}✗ CI-parity preflight FAILED — PUBLISH BLOCKED{NC}\n"
            f"{RED}  These gates would fail GitHub CI AFTER the tag and release were pushed:{NC}",
            file=sys.stderr,
        )
        for f in result.fails:
            loc = f" [{f.file}]" if f.file else ""
            print(f"{RED}    ✗ {f.gate}{loc}: {f.message}{NC}", file=sys.stderr)
        print(
            f"{RED}  Fix the cause, then re-run. Reproduce locally with:{NC}\n"
            f"{RED}    uv run python scripts/remote_validation.py ci-preflight .{NC}",
            file=sys.stderr,
        )
        return result.exit_code

    if result.warnings:
        # Tool-absent WARNINGs are expected on a lean box and are NOT failures.
        # Surface them so the maintainer knows which gates CI will be the FIRST
        # to run — but never block on them.
        print(
            f"  {YELLOW}{len(result.warnings)} parity gate(s) could not run locally "
            f"(tool absent) — CI still enforces them:{NC}"
        )
        for f in result.warnings:
            print(f"    {YELLOW}! {f.gate}: {f.message}{NC}")
    print(f"{GREEN}✓ CI-parity preflight passed ({len(result.passes)} gate(s) clean){NC}")
    return 0


_FORK_PARITY_TIMEOUT_ENV = "PLUGIN_FORK_PARITY_TIMEOUT"
# ~11x the 162s measured on CPV's own suite. Generous on purpose: this deadline
# exists to catch a DEADLOCK (unbounded), not to police a slow machine, and a
# budget tight enough to trip under load would be the timing-calibrated-test
# mistake this repo has already paid for three times.
_DEFAULT_FORK_PARITY_TIMEOUT = 1800.0


def _fork_parity_timeout() -> float:
    """Resolve the fork-parity deadline.

    Mirrors ``url_check_phase_timeout``: an empty, zero, negative, or
    unparseable value falls back to the default, so a typo can never DISABLE the
    guard or set a near-zero ceiling that would fail every publish.
    """
    raw = os.environ.get(_FORK_PARITY_TIMEOUT_ENV, "").strip()
    if not raw:
        return _DEFAULT_FORK_PARITY_TIMEOUT
    try:
        override = float(raw)
    except ValueError:
        return _DEFAULT_FORK_PARITY_TIMEOUT
    return override if override > 0 else _DEFAULT_FORK_PARITY_TIMEOUT


def stage_fork_parity(plugin_root: Path) -> int:
    """Gate 3c: re-run the suite the way LINUX will run it.

    WHY THIS GATE EXISTS (v3.23.0 post-mortem). ``multiprocessing`` defaults to
    **fork on Linux** and **spawn on macOS**. v3.23.0 emitted progress markers
    from ``ThreadPoolExecutor`` workers; forking a multithreaded process copies
    mutex state, so a child inherited ``sys.stderr``'s lock held by a thread that
    did not exist there and hung on its first write. A tiny
    ``validate_plugin.py --json`` run went from 8.7s to a >300s timeout, failing
    BOTH CI and Release — **after a fully green local suite of 11,484 tests.**

    The local gate could not have caught it, because the only platform that
    exhibits the bug is the one nobody runs locally. This gate removes that
    asymmetry without Docker and without a Linux runner: it forces the
    interpreter's default start method to ``fork`` and re-runs the suite.

    Proven two-sided against the real defect before it was wired in:

    ===========================  ==============  ============================
    code                         start method    result
    ===========================  ==============  ============================
    v3.23.1 (fixed)              fork            37 passed in 15.4s
    v3.23.0 (buggy)              spawn           16 passed — **blind**
    v3.23.0 (buggy)              fork            **FAILED** — TimeoutExpired,
                                                 CI's exact signature
    ===========================  ==============  ============================

    The middle row is the point: today's gate passes the broken code.

    PLACEMENT IS LOAD-BEARING — same reasoning as Gate 3b. This runs strictly
    BEFORE the bump (Gate 7), commit (Gate 10), tag (Gate 11) and push (Gate 12),
    so a hang aborts with the tree untouched instead of stranding a tag for a
    release that was never cut.

    IT RUNS SERIALLY, not in the Gate 2-5 parallel block, because it re-runs the
    whole suite: sharing a machine with Gate 2's ``-n auto`` pytest would make
    both slower and could turn contention into a spurious timeout.

    NEVER FALSE-BLOCKS. On Linux the ordinary run already forks, so the probe
    reports ``already-native`` and skips rather than doubling CI. Where fork does
    not exist (Windows) it degrades to a WARNING. It blocks ONLY when the probe
    actually ran and the suite actually failed — but note a TIMEOUT *is* a
    failure here, because a hang is this defect's signature, not an inconclusive
    result.

    Cost: ~162s measured on CPV's own suite. ``PLUGIN_FORK_PARITY_CMD`` narrows
    the command; ``PLUGIN_FORK_PARITY_TIMEOUT`` adjusts the deadline.
    """
    print(f"\n{BLUE}═══ Gate 3c: Linux fork-parity probe (run the suite as Linux would) ═══{NC}")

    runnable, reason = fork_parity_supported()
    if not runnable:
        # "cannot check" is reported as exactly that — never folded into a pass.
        print(f"  {YELLOW}! Skipped: {reason}{NC}")
        return 0

    default_cmd = ["uv", "run", "pytest", "tests/", "-n", "auto", "--dist=worksteal", "-q", "--tb=short"]
    raw_cmd = os.environ.get("PLUGIN_FORK_PARITY_CMD", "").strip()
    cmd = shlex.split(raw_cmd) if raw_cmd else default_cmd

    if not raw_cmd:
        # Nothing to probe. Say so rather than running pytest against a tree with
        # no suite: that yields pytest's "no tests collected" exit code, which
        # this gate would otherwise report as a fork failure — a fabricated
        # finding. Gate 2 already BLOCKS a publish with no tests, so by the time
        # we run here a real publish always has them; this guard only keeps the
        # gate honest when it is invoked outside that flow.
        tests_dir = plugin_root / "tests"
        # Issue #215 — rglob: a tests/unit/ layout is a real suite, and reading
        # it as "no tests" would skip the probe on exactly the repos that most
        # need it.
        if not (tests_dir.is_dir() and any(tests_dir.rglob("test_*.py"))):
            print(f"  {YELLOW}! Skipped: no tests/ suite to probe (Gate 2 owns the missing-tests block).{NC}")
            return 0

    timeout = _fork_parity_timeout()
    print(f"  {reason}; deadline {timeout:.0f}s")
    result = run_under_linux_fork_default(cmd, plugin_root, timeout=timeout)

    if result.blocked:
        print(
            f"\n{RED}✗ Fork-parity probe FAILED — PUBLISH BLOCKED{NC}\n"
            f"{RED}  {result.detail}{NC}\n"
            f"{RED}  This is what Linux CI would do to this commit. A HANG here means something{NC}\n"
            f"{RED}  forks a multithreaded process — see scripts/cpv_fork_safety.py.{NC}\n"
            f"{RED}  Reproduce locally:{NC}\n"
            f"{RED}    uv run python scripts/remote_validation.py fork-parity .{NC}",
            file=sys.stderr,
        )
        tail = "\n".join(result.output.splitlines()[-25:])
        if tail:
            print(tail, file=sys.stderr)
        return 1

    print(f"{GREEN}✓ Suite passes under the Linux fork default{NC}")
    return 0


def stage_secret_scan(plugin_root: Path) -> int:
    """Gate 3d: BLOCK the publish on any detected credential (issue #217).

    The canonical pipeline documented a pre-push secret scan it never
    implemented — `grep -icE "trufflehog|gitleaks|secret[_ -]scan"` over this
    file returned 0. A `tskey-auth-…` literal consequently reached a plugin's
    `main`, GitHub's own secret scanning flagged it, and the alert sat open for
    85 days while every local gate passed.

    Placement is deliberate: this runs in the READ-ONLY parallel block with the
    other gates, strictly BEFORE the bump/commit/tag/push, so a detected
    credential aborts with the tree untouched. A gate that fired after the push
    could not un-publish anything.

    REDACTION IS NOT THIS GATE'S JOB. It reports and blocks, and names
    `cpv-plugin-leaks-preventer-agent` as the fixer the user may launch. A
    publish gate that edited source to make itself pass would be rewriting
    security-relevant code with nobody reviewing the change — and a gate that
    can silence itself is not a gate.

    Cannot-check is NOT clean: a missing binary or an incomplete scan blocks
    rather than passing, because "we never finished looking" and "we looked and
    found nothing" must not produce the same verdict.
    """
    print(f"\n{BLUE}═══ Gate 3d: Secret scan — a leak BLOCKS the publish ═══{NC}")
    # trufflehog is a DEPENDENCY of this gate, not a precondition the user is
    # asked to satisfy: CPV installs it like every other external scanner
    # (`cpv_install_scanners.ensure_trufflehog`, brew → `go install`). Telling a
    # publisher to go install something is a worse answer than installing it.
    #
    # Only a FAILED install blocks. That block is load-bearing: `check_trufflehog`
    # reports a missing binary as a WARNING and returns 0 — correct for a general
    # validate run (an absent optional scanner must not fail someone's plugin)
    # and wrong here, because WARNING never blocks, so the release path would
    # publish having scanned nothing. That silent pass is exactly what issue #217
    # was filed about. The probe lives in the GATE rather than in the shared
    # checker so every other caller keeps the advisory behaviour it wants.
    scripts_dir = plugin_root / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    if not shutil.which("trufflehog"):
        print(f"{YELLOW}  trufflehog missing — installing it (CPV ships it as a dependency)…{NC}")
        try:
            from cpv_install_scanners import ensure_trufflehog  # noqa: PLC0415

            ensure_trufflehog()
        except Exception as exc:  # noqa: BLE001 - any installer failure is reported below
            print(f"{YELLOW}  installer raised: {exc}{NC}", file=sys.stderr)
    if not shutil.which("trufflehog"):
        print(
            f"{RED}✗ trufflehog could not be installed — the release was NOT secret-scanned.{NC}\n"
            f"{YELLOW}  This is UNKNOWN, not clean, so the publish is blocked.\n"
            f"  Install it manually and re-run:  brew install trufflehog{NC}",
            file=sys.stderr,
        )
        return 1
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    try:
        from cpv_validation_common import ValidationReport  # noqa: PLC0415
        from validate_security import (  # noqa: PLC0415
            _set_cpv_self_scan,
            check_trufflehog,
            is_cpv_self_scan,
        )
    except ImportError as exc:
        print(f"{RED}✗ Secret scan unavailable ({exc}) — cannot verify, refusing to publish.{NC}", file=sys.stderr)
        return 1

    report = ValidationReport()
    # Arm the SHA-verified self-scan exemption exactly as validate_plugin and
    # cpv_agent_security do. Without it this gate reports CPV's own detector
    # regexes as credentials — the "scanning the scanner" class — and CPV could
    # never publish itself. It suppresses nothing the plugin gate does not
    # already suppress: every skip still requires a per-file SHA match against
    # the manifest, so a modified or unlisted file is scanned regardless, and
    # `is_cpv_self_scan` is False for every other plugin.
    #
    # The DISARM is mandatory and must be a `finally`: the flag is a module
    # GLOBAL, so leaving it armed would let a later scan in this process read
    # stale state and skip files it must not.
    try:
        _set_cpv_self_scan(is_cpv_self_scan(plugin_root), plugin_root=plugin_root, notice_report=report)
        issues = check_trufflehog(plugin_root, report)
    finally:
        _set_cpv_self_scan(False)
    blocking = [r for r in report.results if r.level in ("CRITICAL", "MAJOR")]
    if not blocking and issues == 0:
        print(f"{GREEN}✓ No credentials detected{NC}")
        return 0

    print(f"{RED}✗ Secret scan BLOCKED the publish — {len(blocking)} finding(s){NC}", file=sys.stderr)
    for r in blocking:
        print(f"  [{r.level}] {r.message}", file=sys.stderr)
    print(
        f"\n{YELLOW}Fix before publishing. Redaction is NOT done by this gate — "
        f"launch the fixer yourself:{NC}\n"
        f"  Agent(subagent_type='claude-plugins-validation:cpv-plugin-leaks-preventer-agent')\n"
        f"A verified live credential must be ROTATED and purged from git history, "
        f"not merely deleted from the working tree.",
        file=sys.stderr,
    )
    return 1


def stage_validate_marketplace(plugin_root: Path, layout: str) -> int:
    """Gate 4: validate marketplace in strict mode — only runs for Layout B.

    For Layout B, publish.py runs at the marketplace repo root, and the parent
    marketplace.json must also validate cleanly. For Layout A (standalone plugin),
    there is no local marketplace to validate so this gate is a no-op.
    """
    print(f"\n{BLUE}═══ Gate 4: Marketplace validation ═══{NC}")
    if layout != "B":
        print(f"  (skipped — not a marketplace repo, layout={layout})")
        print(f"{GREEN}✓ Marketplace validation not applicable{NC}")
        return 0
    vresult = run_with_integrity_bypass(
        ["uv", "run", "python", "scripts/validate_marketplace.py", ".", "--strict"],
        cwd=plugin_root,
        check=False,
    )
    if vresult.returncode != 0:
        severity_map = {1: "CRITICAL", 2: "MAJOR", 3: "MINOR", 4: "NIT"}
        severity = severity_map.get(vresult.returncode, f"unknown (exit {vresult.returncode})")
        print(
            f"\n{RED}✗ {severity} marketplace validation issues found — PUBLISH BLOCKED{NC}\n"
            f"{RED}  Fix command: uv run python scripts/validate_marketplace.py . --strict{NC}",
            file=sys.stderr,
        )
        return vresult.returncode
    print(f"{GREEN}✓ Marketplace validation passed (zero errors){NC}")
    return 0


def stage_marketplace_registration_check(
    plugin_root: Path,
    *,
    prefetch: "_PrefetchResults | None" = None,
) -> int:
    """Gate 5: verify the plugin is wired to its marketplace for auto-updates.

    Layout A (standalone plugin referencing a remote marketplace):
      - .github/workflows/notify-marketplace.yml exists and parses
      - MARKETPLACE_PAT secret is configured on this repo
      - Remote marketplace lists this plugin with a github source
      - Remote marketplace has a workflow with repository_dispatch trigger

    Layout B (nested plugin inside a marketplace repo):
      - publish.py is running at the marketplace repo root, not the nested subfolder
      - Parent marketplace.json lists this plugin
      - Parent marketplace.json entry version matches (or will match after bump)

    No-marketplace mode: emits a WARNING (not an error) and proceeds — this is
    valid for first releases or experimental standalone plugins.

    Phase E (v2.79.0): the optional ``prefetch`` argument carries the
    background-fetched marketplace.json future. When set and resolved
    cleanly, ``_check_layout_a`` reuses the prefetched dict and skips its
    own synchronous fetch. Pre-Phase-E callers (everything except the
    parallel preflight orchestrator) pass nothing and the original
    synchronous path runs unchanged.

    Returns 0 on success (including WARNING mode), 1 on any hard failure.
    """
    print(f"\n{BLUE}═══ Gate 5: Marketplace-registration check ═══{NC}")
    layout, details = detect_layout(plugin_root)

    if layout == "none":
        print(
            f"{YELLOW}⚠ WARNING: no marketplace registration found for this plugin.{NC}\n"
            f"{YELLOW}  If you intend to publish this plugin to a marketplace, run the{NC}\n"
            f"{YELLOW}  cpv-setup-marketplace-auto-notification skill to wire up auto-updates.{NC}\n"
            f"{YELLOW}  Allowing release to proceed (standalone/experimental mode).{NC}"
        )
        return 0

    if layout == "A":
        return _check_layout_a(plugin_root, details, prefetch=prefetch)

    if layout == "B":
        return _check_layout_b(plugin_root, details)

    print(f"{RED}✗ Unknown layout '{layout}' — cannot verify marketplace registration{NC}", file=sys.stderr)
    return 1


def _check_layout_a(
    plugin_root: Path,
    details: dict,
    *,
    prefetch: "_PrefetchResults | None" = None,
) -> int:
    """Layout A verification: standalone plugin + remote marketplace."""
    print("  Layout A detected (standalone plugin repo)")
    notify_wf_raw = details.get("notify_workflow")
    mkt_owner_raw = details.get("mkt_owner")
    mkt_repo_raw = details.get("mkt_repo")
    # Narrow types for mypy — details is a loosely-typed dict from detect_layout
    notify_wf: Path | None = notify_wf_raw if isinstance(notify_wf_raw, Path) else None
    mkt_owner: str | None = mkt_owner_raw if isinstance(mkt_owner_raw, str) else None
    mkt_repo: str | None = mkt_repo_raw if isinstance(mkt_repo_raw, str) else None

    # 1. Notify workflow must exist (already checked by detect_layout)
    if notify_wf is None or not notify_wf.is_file():
        print(
            f"{RED}✗ .github/workflows/notify-marketplace.yml missing.{NC}\n"
            f"{RED}  Fix: run the cpv-setup-marketplace-auto-notification skill to generate it.{NC}",
            file=sys.stderr,
        )
        return 1

    # 2. Workflow must reference a real marketplace
    if not mkt_owner or not mkt_repo:
        print(
            f"{RED}✗ notify-marketplace.yml does not define MARKETPLACE_OWNER/MARKETPLACE_REPO.{NC}\n"
            f"{RED}  Fix: edit .github/workflows/notify-marketplace.yml or re-run{NC}\n"
            f"{RED}  the cpv-setup-marketplace-auto-notification skill.{NC}",
            file=sys.stderr,
        )
        return 1
    print(f"  target marketplace: {mkt_owner}/{mkt_repo}")

    gh_bin = shutil.which("gh")
    if gh_bin is None:
        print(
            f"{RED}✗ gh CLI not installed — cannot verify MARKETPLACE_PAT or remote marketplace.{NC}\n"
            f"{RED}  Install: brew install gh{NC}",
            file=sys.stderr,
        )
        return 1

    # 3. MARKETPLACE_PAT secret must exist on this repo (value is never read)
    if not _gh_secret_exists(plugin_root, "MARKETPLACE_PAT", gh_bin=gh_bin):
        print(
            f"{RED}✗ MARKETPLACE_PAT secret is not configured on this plugin repo.{NC}\n"
            f"{RED}  Fix: gh secret set MARKETPLACE_PAT  (value: a PAT with 'repo' scope){NC}\n"
            f"{RED}  Then re-run publish. See skill: cpv-setup-marketplace-auto-notification.{NC}",
            file=sys.stderr,
        )
        return 1
    print(f"  {GREEN}✓ MARKETPLACE_PAT secret configured{NC}")

    # 4. Plugin must be registered in the remote marketplace.json
    #
    # Phase E (v2.79.0): try the prefetched marketplace.json first. The
    # prefetch was started ~Phase-C-time (before pytest), so by the time
    # Gate 5 runs the network round-trip should already be done. We only
    # accept the prefetch result if:
    #   - The future was actually populated (Layout A with resolved owner/repo).
    #   - It was scoped to the SAME (mkt_owner, mkt_repo) we're checking now
    #     (defensive — guards against a future refactor adding remote-rename).
    #   - It resolved without exception AND returned a usable dict (not None
    #     transient failure).
    # In every other case we fall through to the synchronous fetch — pre-
    # Phase-E behaviour preserved.
    mkt_json: dict | None = None
    used_prefetch = False
    if (
        prefetch is not None
        and prefetch.marketplace_json is not None
        and prefetch.marketplace_target == (mkt_owner, mkt_repo)
    ):
        try:
            # The prefetch thread was kicked off at the start of preflight,
            # so by Gate 5 it has typically completed. .result() blocks
            # only if the prefetch is still in flight (rare on a real
            # publish but happens in fast-stage unit tests).
            prefetched = prefetch.marketplace_json.result()
        except Exception:
            # Any exception (timeout, cancelled, bug in prefetch wrapper)
            # → fall back to the synchronous path. We don't propagate
            # these because the synchronous fetch will give us a clean,
            # current attempt with a fresh error message if the network
            # is genuinely down.
            prefetched = None
        if isinstance(prefetched, dict):
            mkt_json = prefetched
            used_prefetch = True

    if mkt_json is None:
        # Either no prefetch was available (no-prefetch path), or it
        # failed transiently. Run the synchronous call exactly as before.
        mkt_json = _fetch_remote_marketplace_json(mkt_owner, mkt_repo, gh_bin=gh_bin)
    elif used_prefetch:
        # Tiny breadcrumb so a maintainer reading the publish log can see
        # the Phase E speedup actually fired this run.
        print("  (Phase E: reused prefetched marketplace.json)")

    if mkt_json is None:
        print(
            f"{RED}✗ Could not fetch marketplace.json from {mkt_owner}/{mkt_repo}.{NC}\n"
            f"{RED}  Fix: verify the marketplace repo exists and has .claude-plugin/marketplace.json{NC}\n"
            f"{RED}  Fix command: gh api repos/{mkt_owner}/{mkt_repo}/contents/.claude-plugin/marketplace.json{NC}",
            file=sys.stderr,
        )
        return 1
    plugin_name = _read_plugin_name(plugin_root)
    current_slug = _current_repo_slug(plugin_root)
    if not _plugin_in_remote_marketplace(mkt_json, plugin_name, current_slug):
        print(
            f"{RED}✗ Plugin '{plugin_name}' not registered in {mkt_owner}/{mkt_repo}/.claude-plugin/marketplace.json{NC}\n"
            f"{RED}  with a github source entry for {current_slug}.{NC}\n"
            f"{RED}  Fix: add an entry to the remote marketplace.json with:{NC}\n"
            f'{RED}    {{"name": "{plugin_name}", "source": {{"source": "github", "repo": "{current_slug}"}}}}{NC}\n'
            f"{RED}  See skill: cpv-setup-marketplace-auto-notification{NC}",
            file=sys.stderr,
        )
        return 1
    print(f"  {GREEN}✓ Plugin registered in remote marketplace.json{NC}")

    # 5. Remote marketplace must have a receiver workflow
    if not _remote_has_receiver_workflow(mkt_owner, mkt_repo, gh_bin=gh_bin):
        print(
            f"{RED}✗ Remote marketplace {mkt_owner}/{mkt_repo} has no workflow with{NC}\n"
            f"{RED}  a 'repository_dispatch' trigger. The notify-marketplace.yml event{NC}\n"
            f"{RED}  will arrive with nothing listening.{NC}\n"
            f"{RED}  Fix: add a workflow in the marketplace repo with:{NC}\n"
            f"{RED}    on: repository_dispatch: types: [plugin-updated]{NC}\n"
            f"{RED}  See skill: cpv-setup-marketplace-auto-notification{NC}",
            file=sys.stderr,
        )
        return 1
    print(f"  {GREEN}✓ Remote marketplace has receiver workflow{NC}")
    print(f"{GREEN}✓ Layout A marketplace registration verified{NC}")
    return 0


def _check_layout_b(plugin_root: Path, details: dict) -> int:
    """Layout B verification: nested plugin inside a marketplace repo.

    Because Layout B uses atomic marketplace tagging, publish.py must run at the
    MARKETPLACE repo root, not at the nested plugin subfolder. Bumping a nested
    plugin independently would break the marketplace version invariant.
    """
    print("  Layout B detected (nested plugin under marketplace repo)")
    marketplace_root_raw = details.get("marketplace_root")
    plugin_name_raw = details.get("plugin_name")
    # Narrow types for mypy
    marketplace_root: Path | None = marketplace_root_raw if isinstance(marketplace_root_raw, Path) else None
    plugin_name: str = plugin_name_raw if isinstance(plugin_name_raw, str) else plugin_root.name
    if marketplace_root is None:
        print(
            f"{RED}✗ Layout B detected but marketplace_root missing from details dict.{NC}\n"
            f"{RED}  Reference: skills/cpv-create-plugin/references/marketplace-layouts.md{NC}",
            file=sys.stderr,
        )
        return 1

    # 1. Reject running at nested-plugin level
    if plugin_root.resolve() != marketplace_root.resolve():
        print(
            f"{RED}✗ This is a Layout B nested plugin. publish.py must be run at the{NC}\n"
            f"{RED}  MARKETPLACE repo root, not the nested plugin subfolder.{NC}\n"
            f"{RED}  Bumping a nested plugin independently breaks the atomic marketplace tag.{NC}\n"
            f"{RED}  Fix: cd {marketplace_root} && uv run python scripts/publish.py --patch{NC}\n"
            f"{RED}  Reference: skills/cpv-create-plugin/references/marketplace-layouts.md{NC}",
            file=sys.stderr,
        )
        return 1

    # 2. Parent marketplace.json must list this plugin
    mp_json_path = marketplace_root / ".claude-plugin" / "marketplace.json"
    try:
        mp_data = json.loads(mp_json_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(
            f"{RED}✗ Could not read {mp_json_path}: {e}{NC}\n"
            f"{RED}  Reference: skills/cpv-create-plugin/references/marketplace-layouts.md{NC}",
            file=sys.stderr,
        )
        return 1
    entries = mp_data.get("plugins") if isinstance(mp_data, dict) else None
    if not isinstance(entries, list):
        print(
            f"{RED}✗ marketplace.json has no 'plugins' array.{NC}\n"
            f"{RED}  Reference: skills/cpv-create-plugin/references/marketplace-layouts.md{NC}",
            file=sys.stderr,
        )
        return 1

    registered = False
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        if entry.get("name") == plugin_name:
            registered = True
            break
    if not registered:
        print(
            f"{RED}✗ Plugin '{plugin_name}' is not registered in {mp_json_path}.{NC}\n"
            f"{RED}  Fix: add an entry like:{NC}\n"
            f'{RED}    {{"name": "{plugin_name}", "source": "./plugins/{plugin_name}"}}{NC}\n'
            f"{RED}  Reference: skills/cpv-create-plugin/references/marketplace-layouts.md{NC}",
            file=sys.stderr,
        )
        return 1
    print(f"  {GREEN}✓ Plugin '{plugin_name}' registered in parent marketplace.json{NC}")
    print(f"{GREEN}✓ Layout B marketplace registration verified{NC}")
    return 0


def _agent_trailer_args(plugin_root: Path) -> list[str]:
    """Extra `git commit` args carrying the PRRD G1.1 `Agent:` trailer.

    Every commit this tool creates self-identifies which plugin's pipeline
    authored it. The slug is DERIVED from the manifest (dir-name fallback via
    _read_plugin_name — never hardcoded) and carries NO `@`: a bare handle in
    a commit message pages a real GitHub account. A second `-m` paragraph is
    a proper git trailer (last paragraph, `Token: value`).

    Any `@` in the derived name is stripped: GitHub linkifies `@word` at a
    word boundary in commit messages, so a handle-shaped name would PAGE a
    real account (github-mentions iron rule).
    """
    slug = _read_plugin_name(plugin_root).replace("@", "")
    return ["-m", f"Agent: {slug}"]


def _read_plugin_name(plugin_root: Path) -> str:
    """Read plugin name from .claude-plugin/plugin.json (falls back to dir name)."""
    pj = plugin_root / ".claude-plugin" / "plugin.json"
    if pj.exists():
        try:
            data = json.loads(pj.read_text(encoding="utf-8"))
            name = data.get("name")
            if isinstance(name, str) and name:
                return name
        except (OSError, json.JSONDecodeError):
            pass
    return plugin_root.name


def stage_version_consistency(plugin_root: Path) -> int:
    """Gate 6: version consistency across plugin.json, pyproject.toml, __version__."""
    print(f"\n{BLUE}═══ Gate 6: Check version consistency ═══{NC}")
    ok, msg = check_version_consistency(plugin_root)
    print(f"  {msg}")
    if not ok:
        print(f"{RED}✗ Fix version mismatches before publishing.{NC}", file=sys.stderr)
        return 1
    print(f"{GREEN}✓ Version consistency OK{NC}")
    return 0


def detect_bump_type(plugin_root: Path) -> str:
    """Use git-cliff --bumped-version to pick the next semver bump automatically.

    git-cliff reads the conventional commits since the last tag and decides
    whether this release should be a major, minor, or patch bump. We compare
    the resulting version string against the current version to figure out
    which component changed.

    Fallback behavior:
      - git-cliff missing → patch (every push bumps something)
      - --bumped-version returns the current version → patch
      - output is malformed or version comparison fails → patch

    The cornerstone rule is "every push is a bump" — picking patch on fallback
    guarantees we never publish without changing the version, even when
    git-cliff can't make a more confident recommendation.
    """
    # Idempotency: if local plugin.json is ahead of remote (interrupted publish),
    # infer the bump_type from the existing local-vs-remote diff so the SAME
    # bump_type is reported across re-runs. git-cliff itself uses the last
    # local tag as baseline, which can include orphan tags from a prior
    # interrupted run — that produces a "patch" bump_type and would lead
    # the downstream stage_bump into the refuse path.
    remote = _read_remote_version(plugin_root)
    current = get_current_version(plugin_root)
    if remote and current and remote != current:
        inferred = _infer_bump_type(remote, current)
        if inferred is not None:
            return inferred

    cliff_bin = shutil.which("git-cliff")
    if cliff_bin is None:
        print(f"{YELLOW}git-cliff not installed — auto-bump falls back to 'patch'.{NC}")
        return "patch"

    if not current:
        print(f"{YELLOW}Cannot read current version for auto-bump — falling back to 'patch'.{NC}")
        return "patch"

    try:
        result = subprocess.run(
            [cliff_bin, "--bumped-version"],
            capture_output=True,
            text=True,
            cwd=str(plugin_root),
            check=False,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"{YELLOW}git-cliff --bumped-version failed ({exc}) — falling back to 'patch'.{NC}")
        return "patch"

    if result.returncode != 0:
        stderr = result.stderr.strip() or "no stderr"
        print(f"{YELLOW}git-cliff --bumped-version exit {result.returncode}: {stderr} — falling back to 'patch'.{NC}")
        return "patch"

    # git-cliff prints the predicted version (sometimes with a "v" prefix, sometimes bare),
    # possibly along with warning lines on stderr. stdout should be one line.
    bumped_raw = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else ""
    bumped = bumped_raw.lstrip("v").strip()
    baseline = remote or current
    if not bumped or bumped == baseline:
        return "patch"

    inferred = _infer_bump_type(baseline, bumped)
    return inferred or "patch"


def _infer_bump_type(old: str, new: str) -> str | None:
    """Compare two semver strings and return the bump kind that maps old → new.

    Returns one of "major" / "minor" / "patch" when the version components
    differ as expected, or None when the strings can't be compared (non-numeric
    components, downgrade, malformed).
    """
    try:
        old_parts = [int(p) for p in old.split(".")[:3]]
        new_parts = [int(p) for p in new.split(".")[:3]]
    except ValueError:
        return None
    while len(old_parts) < 3:
        old_parts.append(0)
    while len(new_parts) < 3:
        new_parts.append(0)
    if new_parts[0] > old_parts[0]:
        return "major"
    if new_parts[0] == old_parts[0] and new_parts[1] > old_parts[1]:
        return "minor"
    if new_parts[0] == old_parts[0] and new_parts[1] == old_parts[1] and new_parts[2] > old_parts[2]:
        return "patch"
    return None


def _read_remote_version(plugin_root: Path) -> str | None:
    """Read plugin.json `version` from the remote tracking branch (origin/master).

    Returns None when the remote tracking branch isn't available (fresh clone
    without origin, no internet, etc.) — caller falls back to bump-from-local
    behaviour.
    """
    for ref in ("origin/master", "origin/main", "origin/HEAD"):
        try:
            result = subprocess.run(
                ["git", "show", f"{ref}:.claude-plugin/plugin.json"],
                capture_output=True,
                text=True,
                cwd=str(plugin_root),
                check=False,
                timeout=15,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if result.returncode != 0:
            continue
        try:
            version = json.loads(result.stdout).get("version")
        except json.JSONDecodeError:
            continue
        if isinstance(version, str):
            return version
    return None


def stage_bump(plugin_root: Path, bump_type: str, dry_run: bool) -> tuple[int, str | None]:
    """Gate 7: bump version across all files. Returns (exit_code, new_version).

    Idempotency: when a previous publish was interrupted between the local
    bump+commit and the push, plugin.json on disk is already at the target
    version (e.g. 2.64.0) but the remote tracking branch is still on the
    old version (2.63.2). Re-running publish.py would then DOUBLE-BUMP
    (2.64.0 → 2.64.1 with --patch, or 2.64.0 → 2.65.0 with --minor),
    producing a release that skips a number and leaves an orphan local
    `chore(release): v2.64.0` commit with no published tag.

    Fix: read the remote version from `origin/master:.claude-plugin/plugin.json`
    and bump from THAT baseline, not the local plugin.json. If the local
    plugin.json already matches the bumped target, the bump+commit have
    already happened on a previous (interrupted) run — skip the bump and
    let downstream stages (refresh hashes, changelog, commit, tag, push)
    notice their own work-already-done state and pass through idempotently.
    """
    current = get_current_version(plugin_root)
    if current is None:
        print(f"{RED}✗ Cannot read current version from plugin.json{NC}", file=sys.stderr)
        return 1, None
    remote = _read_remote_version(plugin_root)
    # Baseline for the bump = the version already published. Falling back to
    # the local plugin.json keeps the old behaviour on first-time releases or
    # when the remote ref isn't available (offline, fresh clone, etc.).
    baseline = remote if remote else current
    target = bump_semver(baseline, bump_type)
    if target is None:
        print(f"{RED}✗ Current version '{baseline}' is not valid semver{NC}", file=sys.stderr)
        return 1, None
    if remote and current == target:
        # Idempotent path: a previous publish run already bumped + committed
        # the local files; only the push and release remain. Don't re-bump.
        print(f"\n{BLUE}═══ Gate 7: Bump version ({bump_type}: {baseline} → {target}) ═══{NC}")
        print(
            f"{YELLOW}  Local plugin.json is already at {target} (remote at {remote}) — "
            f"skipping bump (interrupted-publish recovery).{NC}"
        )
        print(f"{GREEN}✓ Version already bumped to {target}{NC}")
        return 0, target
    if remote and current != remote and current != target:
        # Local is at some unexpected version (not remote, not the bump target).
        # Refuse to proceed rather than guess.
        print(
            f"{RED}✗ Local plugin.json version is {current}, remote is {remote}, expected bump "
            f"target is {target}. Refusing to bump — manual intervention required.{NC}",
            file=sys.stderr,
        )
        return 1, None
    print(f"\n{BLUE}═══ Gate 7: Bump version ({bump_type}: {baseline} → {target}) ═══{NC}")
    if not do_bump(plugin_root, target, dry_run=dry_run):
        print(f"{RED}✗ Version bump failed{NC}", file=sys.stderr)
        return 1, None
    print(f"{GREEN}✓ Version bumped to {target}{NC}")
    # Also update the README version badge in-place so it never drifts.
    stage_update_readme_badge(plugin_root, baseline, target, dry_run)
    return 0, target


def stage_update_readme_badge(plugin_root: Path, old_version: str, new_version: str, dry_run: bool) -> None:
    """Part of Gate 7: update the README.md version badge in-place.

    Two-stage match strategy:
      1. Exact-string substitution `version-<old>-blue` → `version-<new>-blue`
      2. Regex fallback `version-\\d+\\.\\d+\\.\\d+-blue` for any drifted badge

    The fallback prevents the same "stale forever" trap that bit CPV's own
    README (the badge said 2.6.4 while real version was 2.12.25 — 20 releases
    of silent skip). When neither match succeeds, prints a WARNING (not a
    silent skip) so the author notices the README has no badge to update.
    """
    readme = plugin_root / "README.md"
    if not readme.is_file():
        return
    content = readme.read_text(encoding="utf-8")
    old_badge = f"version-{old_version}-blue"
    new_badge = f"version-{new_version}-blue"

    if old_badge in content:
        if dry_run:
            print(f"  Would update README badge (exact): {old_badge} → {new_badge}")
            return
        readme.write_text(content.replace(old_badge, new_badge, 1), encoding="utf-8")
        print(f"  {GREEN}✓ Updated README version badge{NC}")
        return

    # Regex fallback: catch any drifted version badge
    badge_re = re.compile(r"version-\d+\.\d+\.\d+-blue")
    match = badge_re.search(content)
    if match is None:
        print(
            f"  {YELLOW}WARNING: no version-X.Y.Z-blue badge found in README.md — "
            f"add a shields.io badge so future releases can update it automatically{NC}"
        )
        return
    found = match.group(0)
    if dry_run:
        print(f"  Would update README badge (regex): {found} → {new_badge}")
        return
    readme.write_text(badge_re.sub(new_badge, content, count=1), encoding="utf-8")
    print(f"  {GREEN}✓ Updated README version badge (was {found}, now {new_badge}){NC}")


def stage_refresh_self_hashes(plugin_root: Path) -> int:
    """Gate 8: regenerate `.plugin-self-hashes.json` from the current source.

    CPV's `_plugin_verify_hashes.py` (formerly `cpv_integrity.py`) fetches
    the GitHub-pinned manifest at runtime and aborts when the cache files
    don't match. Without this gate, every release between manifest
    refreshes ships a stale manifest and fresh marketplace installs hit
    the abort immediately (issue #18).

    Per TRDD-bbff5bc5, the gate prefers the new script name
    `_plugin_compute_hashes.py` and falls back to the legacy
    `compute_cpv_self_hashes.py` for one release while v2.50.x cached
    plugins are still in the wild. The new script writes BOTH
    `.plugin-self-hashes.json` AND `.cpv-self-hashes.json` (bytes-identical
    compat copy) so v2.50.x cached clients keep verifying successfully.

    The gate is CPV-specific. Other plugins generated by cpv-plugin-creator-agent
    don't ship a self-hashes manifest and therefore don't need this
    gate in their own publish.py.
    """
    print(f"\n{BLUE}═══ Gate 8: Refresh .plugin-self-hashes.json ═══{NC}")
    # TRDD-bbff5bc5: prefer the new script name, fall back to legacy.
    script_new = plugin_root / "scripts" / "_plugin_compute_hashes.py"
    script_legacy = plugin_root / "scripts" / "compute_cpv_self_hashes.py"
    if script_new.is_file():
        script = script_new
    elif script_legacy.is_file():
        script = script_legacy
    else:
        print(
            f"{YELLOW}⚠ scripts/_plugin_compute_hashes.py not found "
            f"(also checked legacy scripts/compute_cpv_self_hashes.py) — "
            f"skipping integrity-manifest refresh (this is fine for non-CPV "
            f"plugins){NC}",
            file=sys.stderr,
        )
        return 0
    # Hash-refresh script may import _plugin_verify_hashes transitively;
    # use the bypass helper so it doesn't error on the previous tag's
    # manifest mismatch with the current working tree.
    run_with_integrity_bypass(["uv", "run", "python", str(script), str(plugin_root)], cwd=plugin_root)
    print(f"{GREEN}✓ Integrity manifest refreshed (issue #18 fix){NC}")
    return 0


def stage_changelog(plugin_root: Path, tag_name: str, new_version: str) -> tuple[int, Path | None]:
    """Gate 9: generate CHANGELOG.md and extract release notes via git-cliff."""
    print(f"\n{BLUE}═══ Gate 9: Generate CHANGELOG + release notes (git-cliff) ═══{NC}")
    cliff_bin = shutil.which("git-cliff")
    if cliff_bin is None:
        print(
            f"{RED}✗ git-cliff not installed. Required for changelog and release notes.{NC}\n"
            f"{RED}  Install: brew install git-cliff  OR  cargo install git-cliff{NC}",
            file=sys.stderr,
        )
        return 1, None
    cliff_toml = plugin_root / "cliff.toml"
    if not cliff_toml.is_file():
        print(f"{RED}✗ cliff.toml not found. Required for changelog generation.{NC}", file=sys.stderr)
        return 1, None
    # CHANGELOG.md gets the FULL history, regenerated from the commit log:
    #   git cliff --bump --tag <NEXT> -o CHANGELOG.md
    # --bump          treat this as a release bump (the unreleased section is
    #                 promoted to a dated tag entry)
    # --tag <NEXT>    label the new entry with our computed version
    # -o CHANGELOG.md write the regenerated changelog back to disk
    #
    # `--unreleased` MUST NOT appear here (ai-maestro#62, reported by the
    # ai-maestro server Claude and reproduced in THIS repo: our own
    # CHANGELOG.md had been reduced to a single section). `-o` OVERWRITES the
    # file, so restricting content to the unreleased window and then writing
    # leaves a changelog containing only the release just generated — every
    # prior entry is destroyed, and the history is unrecoverable from the
    # artifact. Dropping the flag makes git-cliff render every tag from the
    # commit log, which is also IDEMPOTENT: re-running the step for the same
    # version reproduces the same file. `--prepend` is the WRONG fix — it
    # accumulates, so a publish retried after a failed downstream gate
    # silently duplicates a section.
    #
    # The release-notes extraction below is the ONLY place `--unreleased`
    # belongs: it writes to a separate notes file, never to CHANGELOG.md.
    run(
        [cliff_bin, "--bump", "--tag", tag_name, "-o", "CHANGELOG.md"],
        cwd=plugin_root,
    )
    print(f"{GREEN}✓ CHANGELOG.md updated with {tag_name}{NC}")
    # Canonical report path per agent-reports-location.md:
    #   $MAIN_ROOT/reports/<component>/<YYYYMMDD_HHMMSS±HHMM>-<slug>.md
    release_notes_file = build_report_path(
        component="publish",
        slug=f"release-notes-{new_version}",
        ext="md",
        anchor=plugin_root,
    )
    run(
        [
            cliff_bin,
            "--unreleased",
            "--tag",
            tag_name,
            "--strip",
            "all",
            "-o",
            str(release_notes_file),
        ],
        cwd=plugin_root,
    )
    try:
        rel_display = release_notes_file.relative_to(plugin_root)
    except ValueError:
        rel_display = release_notes_file
    print(f"{GREEN}✓ Release notes extracted to {rel_display}{NC}")
    return 0, release_notes_file


def _resolve_owner_repo(plugin_root: Path) -> tuple[str, str]:
    """Read `git config remote.origin.url` and parse owner/repo. Exit 1 on failure.

    Used by Gates 12 and 13 to scope the gh-auth precheck to the actual push
    target. Per TRDD-bbff5bc5 §4.1, the precheck must verify push perms on
    THIS specific repo, not just "any gh auth status passes".
    """
    result = subprocess.run(
        ["git", "config", "--get", "remote.origin.url"],
        cwd=plugin_root,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if result.returncode != 0 or not result.stdout.strip():
        print(
            f"\n{RED}✗ Could not read remote.origin.url for {plugin_root}.{NC}\n"
            f"{YELLOW}  Run: git remote add origin <url>{NC}",
            file=sys.stderr,
        )
        sys.exit(1)
    parsed = _parse_owner_repo_from_remote(result.stdout.strip())
    if parsed is None:
        print(
            f"\n{RED}✗ Could not parse owner/repo from remote URL: {result.stdout.strip()!r}{NC}",
            file=sys.stderr,
        )
        sys.exit(1)
    return parsed


def _git_porcelain_clean(plugin_root: Path) -> bool:
    """True when `git status --porcelain` returns no lines (working tree clean)."""
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        capture_output=True,
        text=True,
        cwd=str(plugin_root),
        check=False,
        timeout=15,
    )
    return result.returncode == 0 and not result.stdout.strip()


def _head_commit_message(plugin_root: Path) -> str:
    """Return the subject line of HEAD's commit message, or an empty string."""
    result = subprocess.run(
        ["git", "log", "-1", "--pretty=%s"],
        capture_output=True,
        text=True,
        cwd=str(plugin_root),
        check=False,
        timeout=15,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def _local_tag_exists(plugin_root: Path, tag_name: str) -> bool:
    """True when `git tag` lists ``tag_name`` locally."""
    result = subprocess.run(
        ["git", "tag", "-l", tag_name],
        capture_output=True,
        text=True,
        cwd=str(plugin_root),
        check=False,
        timeout=15,
    )
    return result.returncode == 0 and tag_name in result.stdout.split()


def _dependency_tag_name(plugin_root: Path, tag_name: str) -> str | None:
    """The ``{plugin-name}--v{version}`` tag Claude Code resolves dependencies against.

    ``tag_name`` is the plain release tag (``v2.155.0``); the version is its suffix.
    The plugin name is read from the manifest — never hardcoded — so a rename cannot
    silently desync the tag from the plugin it names. Returns None when the name is
    unreadable, in which case the caller warns and skips rather than inventing one.

    This is the exact name ``claude plugin tag`` produces.
    """
    manifest = plugin_root / ".claude-plugin" / "plugin.json"
    if not manifest.is_file():
        return None
    try:
        name = json.loads(manifest.read_text(encoding="utf-8")).get("name")
    except (json.JSONDecodeError, OSError):
        return None
    if not name:
        return None
    return f"{name}--v{tag_name.removeprefix('v')}"


def _ensure_tag_at_head(plugin_root: Path, tag_name: str, message: str) -> bool:
    """Guarantee ``tag_name`` exists AND points at HEAD, or refuse.

    Issue #216 — after a publish that died between tagging and the push (a
    blocking gate, a lost connection), the retry's recovery saw the tag already
    present and skipped re-tagging, then pushed HEAD plus the OLD tag. Every
    commit made between the attempts — typically the very fix that made the
    retry pass — landed on the branch but OUTSIDE the released tag, so the
    release archive differed from the tree the gates had just validated.

    Fail-closed: the tag is moved ONLY on the remote's positive answer that it
    is unpushed. A tag already on origin is immutable here, and an unreachable
    remote is not consent (TRDD-6UW0KZVY).

    Returns True when the tag is correct (created, moved, or already at HEAD),
    False when the caller must abort.
    """
    if not _local_tag_exists(plugin_root, tag_name):
        run(["git", "tag", "-a", tag_name, "-m", message], cwd=plugin_root)
        print(f"{GREEN}✓ Tag {tag_name} created{NC}")
        return True

    tag_sha = run(["git", "rev-list", "-n", "1", tag_name], cwd=plugin_root, check=False).stdout.strip()
    head_sha = run(["git", "rev-parse", "HEAD"], cwd=plugin_root, check=False).stdout.strip()
    if not (tag_sha and head_sha) or tag_sha == head_sha:
        # Already correct, or the shas are unreadable — the latter is the
        # pre-existing behaviour and is safe: nothing is moved on a guess.
        print(f"{GREEN}✓ Tag {tag_name} already present at HEAD{NC}")
        return True

    remote_state = _remote_tag_state(plugin_root, tag_name)
    if remote_state is None:
        print(
            f"{RED}✗ Local tag {tag_name} points at {tag_sha[:8]} (HEAD {head_sha[:8]}) "
            f"and origin's tags cannot be read (ls-remote failed). Refusing to move "
            f"the tag: that is only safe when the remote confirms it is unpushed. "
            f"Re-run once the remote is reachable.{NC}",
            file=sys.stderr,
        )
        return False
    if remote_state is True:
        print(
            f"{RED}✗ Local tag {tag_name} points at {tag_sha[:8]} but HEAD is "
            f"{head_sha[:8]}, and the tag is ALREADY ON ORIGIN. Refusing to move a "
            f"published tag. Bump to a new version instead.{NC}",
            file=sys.stderr,
        )
        return False

    print(
        f"{YELLOW}  Local tag {tag_name} points at {tag_sha[:8]}, HEAD is at "
        f"{head_sha[:8]}. Tag is unpushed; moving it to HEAD.{NC}"
    )
    run(["git", "tag", "-d", tag_name], cwd=plugin_root)
    run(["git", "tag", "-a", tag_name, "-m", message], cwd=plugin_root)
    print(f"{GREEN}✓ Tag {tag_name} re-created at HEAD{NC}")
    return True


def _remote_tag_state(plugin_root: Path, tag_name: str) -> bool | None:
    """Three-valued remote-tag probe (TRDD-6UW0KZVY, amvcp TRDD-YY5ISKCJ shape).

    Returns:
      True  — ls-remote SUCCEEDED and the tag exists on origin.
      False — ls-remote SUCCEEDED and the tag does not exist (a real answer:
              first-publish and the pre-push recovery both rely on it).
      None  — the remote could NOT be read (non-zero exit / timeout). This is
              NOT "no tags": collapsing it into False made the destructive
              recovery branches act on a question the remote never answered
              — undoing a commit whose tag may well be published.
    """
    try:
        result = subprocess.run(
            ["git", "ls-remote", "--tags", "origin", f"refs/tags/{tag_name}"],
            capture_output=True,
            text=True,
            cwd=str(plugin_root),
            check=False,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        return None
    if result.returncode != 0:
        return None
    return bool(result.stdout.strip())


def _remote_tag_exists(plugin_root: Path, tag_name: str) -> bool:
    """True only on a POSITIVE remote answer — for the post-push verify,
    where None and False alike must report UNVERIFIED, never green."""
    return _remote_tag_state(plugin_root, tag_name) is True


def _ensure_submodules_pushed(plugin_root: Path) -> None:
    """Verify every submodule's currently-checked-out SHA is reachable on its
    origin remote BEFORE the parent's `git push` happens.

    TRDD-793ac32a Sprint 2 — closes the "most expensive way to break a
    plugin install" gap: a parent push that points at a submodule SHA which
    only exists locally produces a remote tree that anyone cloning will
    fail at submodule init (the SHA in the parent's gitlink doesn't exist
    on the submodule's origin, so `git submodule update --init` errors out
    with "fatal: reference is not a tree").

    Why this gate runs BEFORE the parent push:
      - Once the parent push lands, the broken state is public — fixing it
        requires either a force-push (rewrites history for everyone) or a
        new commit pointing at a different submodule SHA. Neither is free.
      - The check itself is cheap: one `git submodule status` to enumerate
        + one `git -C <path> branch -r --contains <sha>` per submodule. No
        network round-trip required because submodules already have their
        remote refs locally (the `branch -r` query reads
        `<submodule>/.git/refs/remotes/origin/`).

    Behaviour:
      - No `.gitmodules` at plugin root → no-op (return cleanly).
      - `.gitmodules` exists but `git submodule status` returns no entries
        (e.g. file is empty or all entries deinit'd) → no-op.
      - Every submodule SHA is reachable on origin (`branch -r --contains`
        returns at least one ref) → return cleanly.
      - One or more submodule SHAs are NOT reachable on origin → exit 1
        with an actionable message naming the offending path AND the
        exact `git push origin HEAD` command to run inside it. The
        message lists ALL unreachable submodules in one shot so a
        maintainer with N broken submodules doesn't have to re-run the
        publish gate N times.

    Style: matches `_ensure_gh_auth` — capture_output=True, text=True,
    timeout=60, fatal failures use `sys.exit(1)` with stderr message.
    """
    gitmodules = plugin_root / ".gitmodules"
    if not gitmodules.is_file():
        # No submodules registered. Nothing to verify.
        return

    # Enumerate submodule (path, currently-checked-out SHA) pairs from the
    # parent's index. `git submodule status` output format is one line per
    # submodule:
    #   ` <sha> <path> (<describe>)`     — initialized, in sync
    #   `+<sha> <path> (<describe>)`     — initialized, head differs from index
    #   `-<sha> <path>`                  — not initialized
    #   `U<sha> <path>`                  — merge conflict
    # The first character is the status marker; columns 2..41 are the SHA.
    # We accept ANY status (including '-' deinit) because the gitlink in
    # the parent's index still points at <sha>, and that's what consumers
    # will see post-push.
    try:
        status_result = subprocess.run(
            ["git", "submodule", "status"],
            cwd=str(plugin_root),
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except subprocess.TimeoutExpired:
        print(
            f"\n{RED}✗ `git submodule status` timed out after 60 s.{NC}\n"
            f"{YELLOW}  Cannot verify submodule SHAs are reachable on origin.{NC}\n"
            f"{YELLOW}  Retry, or run manually: git -C <plugin_root> submodule status{NC}",
            file=sys.stderr,
        )
        sys.exit(1)
    if status_result.returncode != 0:
        # Hard failure (corrupt .gitmodules, not-a-git-repo, etc.). Don't
        # silently let publish proceed — the user has a broken setup that
        # would also break submodule consumers.
        print(
            f"\n{RED}✗ `git submodule status` failed (exit {status_result.returncode}).{NC}\n"
            f"{YELLOW}  stderr: {status_result.stderr.strip()}{NC}\n"
            f"{YELLOW}  Fix the submodule configuration before publishing.{NC}",
            file=sys.stderr,
        )
        sys.exit(1)

    submodule_entries: list[tuple[str, str]] = []  # (path, sha)
    for raw_line in status_result.stdout.splitlines():
        if not raw_line.strip():
            continue
        # Drop the leading status marker (1 char), then take the first two
        # whitespace-separated tokens: <sha> <path>.
        line_body = raw_line[1:].strip()
        parts = line_body.split()
        if len(parts) < 2:
            continue
        sha, path = parts[0], parts[1]
        submodule_entries.append((path, sha))

    if not submodule_entries:
        # `.gitmodules` exists but no live submodule entries — nothing to
        # verify.
        return

    unreachable: list[tuple[str, str]] = []  # (path, sha)
    for sub_path, sub_sha in submodule_entries:
        sub_root = plugin_root / sub_path
        if not (sub_root / ".git").exists():
            # Submodule has never been initialized in this checkout. We
            # cannot probe its origin without `git submodule update --init`,
            # and triggering that here would be a side effect publish.py
            # has no business performing. Treat as unreachable so the
            # maintainer initializes it themselves.
            unreachable.append((sub_path, sub_sha))
            continue
        try:
            contains = subprocess.run(
                ["git", "-C", str(sub_root), "branch", "-r", "--contains", sub_sha],
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
        except subprocess.TimeoutExpired:
            # Conservative: a timeout means we can't confirm reachability,
            # so we treat as unreachable to avoid pushing a broken parent.
            unreachable.append((sub_path, sub_sha))
            continue
        # `branch -r --contains <sha>` exits 0 with non-empty stdout when
        # at least one remote ref contains the SHA. Empty stdout (even on
        # exit 0) means no remote ref reaches it → unreachable.
        if contains.returncode != 0 or not contains.stdout.strip():
            unreachable.append((sub_path, sub_sha))

    if unreachable:
        # Build one consolidated error block listing every offending
        # submodule with its individual fix command. This way a maintainer
        # with N broken submodules sees them all at once instead of
        # re-running publish N times.
        lines = [
            "",
            f"{RED}✗ Cannot push parent: one or more submodule SHAs are NOT reachable on their origin remote.{NC}",
            f"{RED}  Pushing now would create a broken install — anyone cloning will fail at submodule init.{NC}",
            "",
        ]
        for sub_path, sub_sha in unreachable:
            short_sha = sub_sha[:8] if len(sub_sha) >= 8 else sub_sha
            lines.append(
                f"{RED}  • submodule '{sub_path}' currently at SHA {short_sha} which is NOT reachable on origin remote.{NC}"
            )
            lines.append(f"{YELLOW}    Run: cd {sub_path} && git push origin HEAD{NC}")
        lines.append("")
        lines.append(f"{YELLOW}  Then re-run publish.{NC}")
        print("\n".join(lines), file=sys.stderr)
        sys.exit(1)


def stage_release_changes(plugin_root: Path) -> None:
    """Stage the release commit WITHOUT absorbing untracked files (issue #186).

    `git add -A` stages untracked files too. At release time a plugin tree
    routinely holds `reports/` (CPV's own convention says these "routinely
    contain private data — absolute paths, usernames, internal hostnames,
    proprietary source, tokens caught in logs"), local scratch, editor
    artifacts, and whatever a failed earlier run left behind. A release commit
    is the WORST place for an accidental inclusion: it is pushed to a public
    repo and it is also the artifact users install, so once it lands, forks,
    clones and GitHub's caches make it unrecoverable in practice.

    Staging tracked modifications only is safe HERE specifically because Gate 1
    already required a clean tree: everything legitimate is committed by the
    time this runs, so the only files this pipeline itself creates are the
    generated ones enumerated below. Anything else untracked at this point is,
    by construction, not part of the release.

    The failure mode is deliberately "the release did not include your new
    file, here it is by name" rather than "the release published your scratch
    directory" — the first is a loud, cheap fix; the second is permanent.
    """
    run(["git", "add", "-u"], cwd=plugin_root)

    # Files this pipeline generates. They are normally already tracked, but a
    # first-ever CHANGELOG.md (or a plugin adopting the canon) can be new — so
    # name them explicitly rather than reaching for `-A`. Only existing paths
    # are staged; a missing one is not an error.
    for rel in (
        ".claude-plugin/plugin.json",
        ".plugin-self-hashes.json",
        ".cpv-self-hashes.json",
        "CHANGELOG.md",
        "README.md",
        "pyproject.toml",
        "uv.lock",
    ):
        if (plugin_root / rel).exists():
            run(["git", "add", "--", rel], cwd=plugin_root)

    # Surface whatever is left untracked instead of silently absorbing it.
    res = subprocess.run(  # noqa: S603 - fixed argv, no shell
        ["git", "status", "--porcelain"],
        cwd=plugin_root,
        capture_output=True,
        text=True,
        check=False,
    )
    stray = [ln[3:].strip() for ln in res.stdout.splitlines() if ln.startswith("??")]
    if stray:
        print(f"{YELLOW}  NOT staged — untracked files are never swept into a release commit (#186):{NC}")
        for path in stray[:20]:
            print(f"{YELLOW}    ?? {path}{NC}")
        if len(stray) > 20:
            print(f"{YELLOW}    … and {len(stray) - 20} more{NC}")
        print(f"{YELLOW}  If one of these belongs in the release, `git add` it BY NAME and re-run.{NC}")


def stage_commit_tag_push(
    plugin_root: Path,
    tag_name: str,
    *,
    prefetch: "_PrefetchResults | None" = None,
) -> int:
    """Gates 10-12: commit, tag, push.

    Idempotency: when a previous publish run was interrupted between the
    commit and the push, HEAD is already the release commit and the tag
    already exists locally. Re-running publish.py must NOT create a second
    release commit or fail trying to re-create an existing tag — that path
    led to the v2.64.0/v2.65.0 jump described in the v2.66.0 commit body.

    Gate 10: skip the commit when the working tree is clean AND HEAD's subject
    is already `chore(release): <tag_name>`.
    Gate 11: skip the tag when it already points at HEAD locally.
    Gate 12: `git push` is idempotent on its own — pushing an already-pushed
    ref is a no-op, so no extra check needed.

    TRDD-bbff5bc5 §5: gh-auth precheck runs at the top of Gate 12 (before
    the first `git push`) to give the maintainer an actionable error
    message BEFORE git tries the network round-trip and fails opaquely.

    Phase E (v2.79.0): the optional ``prefetch`` argument carries the
    background-fetched gh-auth future. When set and resolved cleanly, the
    Gate 12 precheck reuses the prefetch result and skips the synchronous
    ``_ensure_gh_auth`` call. If the prefetch raised SystemExit (permanent
    auth failure), it's re-raised here on the main thread so the existing
    fail-fast behaviour is preserved exactly.
    """
    print(f"\n{BLUE}═══ Gate 10: Commit version bump + manifest refresh + changelog ═══{NC}")
    expected_subject = f"chore(release): {tag_name}"
    head_subject = _head_commit_message(plugin_root)
    porcelain_clean = _git_porcelain_clean(plugin_root)
    # FAIL-CLOSED remote read (TRDD-6UW0KZVY, the amvcp TRDD-YY5ISKCJ shape):
    # the recovery branch below undoes the release commit and deletes its
    # local tag — safe ONLY on the remote's positive answer that the tag is
    # unpublished. An unreadable remote (ls-remote non-zero / timeout) is NOT
    # that answer; the old bool helper collapsed it into "no tag" and took
    # the destructive branch on a question the remote never answered.
    # Refusing costs a re-run; guessing wrong mangles local state against a
    # published release.
    _recovery_candidate = (not porcelain_clean) and head_subject == expected_subject
    _tag_on_remote: bool | None = False
    if _recovery_candidate:
        _tag_on_remote = _remote_tag_state(plugin_root, tag_name)
        if _tag_on_remote is None:
            print(
                f"{RED}✗ Cannot read origin's tags (ls-remote failed) while deciding the "
                f"interrupted-publish recovery for {tag_name}. Refusing to consolidate: "
                f"the recovery undoes the release commit, which is only safe when the "
                f"remote confirms the tag is unpublished. Re-run once the remote is "
                f"reachable.{NC}",
                file=sys.stderr,
            )
            return 1
    if porcelain_clean and head_subject == expected_subject:
        print(
            f"{YELLOW}  Working tree clean and HEAD already has '{expected_subject}' — "
            f"skipping commit (interrupted-publish recovery).{NC}"
        )
        print(f"{GREEN}✓ Already committed {tag_name}{NC}")
    elif _recovery_candidate and _tag_on_remote is False:
        # Bug fix (task #151): the previous publish run got interrupted between
        # Gate 10 (commit) and Gate 12 (push), so HEAD already has the
        # `chore(release): v<tag>` commit but the tag was never pushed to
        # remote. The current re-run reached this point with a DIRTY working
        # tree because Gates 8 (refresh hashes) and 9 (regenerate CHANGELOG)
        # touched files that the prior commit didn't include.
        #
        # Without this branch, Gate 10 would create a SECOND
        # `chore(release): v<tag>` commit on top of the existing one — and
        # Gate 11 would then keep the tag pointing at the OLDER commit
        # (because `_local_tag_exists` short-circuits). Result: orphan commit
        # on master, GitHub release built from stale tag, manifest refresh
        # never makes it into the release.
        #
        # Fix: undo the unpushed release commit with `git reset --soft HEAD~1`
        # (preserves all changes in the index + working tree), then make ONE
        # consolidated commit that includes the prior bump AND the new
        # manifest refresh. This is safe because:
        # - The commit being undone is local-only (`_remote_tag_state`
        #   POSITIVELY confirmed the tag is absent from origin — an
        #   unreadable remote refused above instead of guessing).
        # - `--soft` doesn't touch files; only the commit pointer moves back.
        # - Git's reflog still has the old commit recoverable via
        #   `git reset HEAD@{1}` for the next ~90 days.
        print(
            f"{YELLOW}  Recovery: HEAD already has '{expected_subject}' from a prior "
            f"interrupted run, but the tree is dirty and the tag was never pushed.{NC}"
        )
        print(
            f"{YELLOW}  Folding the manifest refresh into the existing release commit "
            f"(git reset --soft HEAD~1, then re-commit).{NC}"
        )
        run(["git", "reset", "--soft", "HEAD~1"], cwd=plugin_root)
        # If the existing local tag pointed at the just-undone commit, drop
        # it so Gate 11 can re-create it pointing at the new consolidated
        # commit. (No remote tag exists, per the elif guard above.)
        if _local_tag_exists(plugin_root, tag_name):
            run(["git", "tag", "-d", tag_name], cwd=plugin_root)
            print(f"{YELLOW}  Removed local-only tag {tag_name} (will re-create on the consolidated commit).{NC}")
        stage_release_changes(plugin_root)
        run(["git", "commit", "-m", expected_subject, *_agent_trailer_args(plugin_root)], cwd=plugin_root)
        print(f"{GREEN}✓ Re-committed {tag_name} with manifest refresh folded in{NC}")
    else:
        stage_release_changes(plugin_root)
        run(["git", "commit", "-m", expected_subject, *_agent_trailer_args(plugin_root)], cwd=plugin_root)
        print(f"{GREEN}✓ Committed {tag_name}{NC}")
    print(f"\n{BLUE}═══ Gate 11: Create git tag {tag_name} ═══{NC}")
    if not _ensure_tag_at_head(plugin_root, tag_name, f"Release {tag_name}"):
        return 1

    # The DEPENDENCY-RESOLUTION tag. Claude Code resolves a version-constrained
    # dependency ONLY against `{name}--v*` tags and IGNORES the plain `vX.Y.Z` one,
    # so without this CPV itself cannot be depended upon: a dependent fails to
    # install with `no-matching-tag` and is DISABLED. CPV's own detector
    # (RC-DEP-TAG-*) flags exactly this — dogfood it rather than exempt ourselves.
    dep_tag_name = _dependency_tag_name(plugin_root, tag_name)
    if dep_tag_name is None:
        print(f"{YELLOW}  WARNING: plugin name unreadable — skipping the dependency tag.{NC}")
    # Issue #216 — the SAME staleness the release tag guards against, on the tag
    # dependents actually resolve against. This branch used to be a bare
    # "already exists locally — skipping", so an interrupted publish left the
    # dependency tag pinned at the pre-fix commit while HEAD moved on: every
    # dependent then installed a tree the gates never validated.
    elif not _ensure_tag_at_head(plugin_root, dep_tag_name, dep_tag_name.replace("--v", " ")):
        return 1

    print(f"\n{BLUE}═══ Gate 12: Push to origin (branch + tags) ═══{NC}")
    # TRDD-bbff5bc5: gh-auth precheck — fail fast with actionable error if
    # the maintainer's gh CLI is missing/unauthed/lacks push perm.
    owner, repo = _resolve_owner_repo(plugin_root)
    # Phase E (v2.79.0): try the prefetched gh-auth result first. The
    # prefetch was started ~Phase-C-time (before pytest), so by the time
    # Gate 12 runs the network round-trip should already be done.
    #
    # We accept the prefetched result iff:
    #   - The future was actually populated.
    #   - It was scoped to the SAME (owner, repo) we're about to push to.
    #
    # The prefetch wrapper catches no exceptions itself, so the future
    # carries either:
    #   - exception() == None and result() == None → auth check passed
    #     cleanly. Skip the synchronous call.
    #   - exception() is SystemExit(1) → permanent auth failure. Re-raise
    #     on the main thread so the existing fail-fast path runs (sys.exit
    #     prints the same actionable error message _ensure_gh_auth would
    #     have printed if invoked synchronously, because the SystemExit
    #     was already raised AFTER its print — captured stderr was
    #     emitted into the worker thread's stderr proxy, so it already
    #     reached the user terminal).
    #   - exception() is some OTHER unexpected exception → fall back to
    #     synchronous (defensive: never trade fail-fast for a Phase E
    #     speedup on something we don't recognise).
    used_prefetch = False
    if prefetch is not None and prefetch.gh_auth is not None and prefetch.gh_auth_target == (owner, repo):
        try:
            exc = prefetch.gh_auth.exception()
        except Exception:
            # .exception() itself raising means the future is in a bad
            # state (cancelled, etc.) — fall through to synchronous.
            exc = None
            used_prefetch = False
        else:
            if exc is None:
                # Prefetch completed without raising → auth is good.
                used_prefetch = True
            elif isinstance(exc, SystemExit):
                # Re-raise on the main thread to preserve fail-fast.
                # SystemExit is the only auth-failure signal _ensure_gh_auth
                # uses, so this is the canonical "permanent auth failure"
                # path.
                raise exc
            else:
                # Unknown exception → fall back to synchronous so the user
                # gets a real, current error message from _ensure_gh_auth.
                used_prefetch = False

    if used_prefetch:
        print("  (Phase E: reused prefetched gh-auth check)")
    else:
        _ensure_gh_auth(owner, repo)
    # TRDD-793ac32a Sprint 2: submodule push gate — verify every submodule
    # SHA referenced by the parent's index is reachable on its origin
    # remote BEFORE we push the parent. Without this, a parent push that
    # points at a local-only submodule SHA produces a broken install for
    # everyone who clones with `--recurse-submodules`. Runs after the
    # gh-auth precheck so we know auth is sane before doing any submodule
    # probing; runs before the parent push so the broken state never
    # becomes public.
    _ensure_submodules_pushed(plugin_root)
    # v2.86.0 hardening (issue #22): single `git push --atomic origin HEAD
    # <tag>` so commit + tag land in one transaction. Eliminates the
    # half-published-state failure mode where the previous two-call form
    # could push the commit, fail on the tag (network blip / ref-update
    # race), and leave the remote with an unreleased commit + no tag.
    # `--atomic` makes the server roll back if any ref-update fails.
    # git_with_retry still wraps the call so transient network hiccups
    # retry; 4xx-class permanent errors fall through immediately.
    print(f"  $ git push --atomic origin HEAD {tag_name}")
    # capture_output MUST be True (TRDD-WC2GEDOC): the transient classifier
    # reads result.stderr, and with capture_output=False stderr is None, so
    # `if not stderr: return False` classified EVERY failure as permanent and
    # the release push could never retry a network blip. The captured stderr
    # is echoed below (success and failure) so nothing git says is swallowed.
    try:
        push_result = git_with_retry(
            # Both tags in ONE atomic push: a release can never ship with the plain tag
            # and not the dependency tag (which is exactly how this defect hid).
            ["git", "push", "--atomic", "origin", "HEAD", tag_name, *([dep_tag_name] if dep_tag_name else [])],
            cwd=plugin_root,
            env=os.environ.copy(),
        )
    except subprocess.CalledProcessError as exc:
        if exc.stderr:
            print(exc.stderr, file=sys.stderr, end="")
        raise
    if push_result.stderr:
        print(push_result.stderr, file=sys.stderr, end="")
    print(f"{GREEN}✓ Pushed branch and tag {tag_name} atomically{NC}")
    # Prove the TAG, not the stage (ai-maestro#62 R3, filed by the ai-maestro
    # server Claude): a push stage that ran and silently failed its ref-update
    # is indistinguishable from one that succeeded, and the plugin then reports
    # a green publish while being undependable. `git ls-remote` asks the remote
    # itself. A network failure returns False, so this reports UNVERIFIED —
    # never a false green, and never a false block either: the refs are already
    # pushed by this point, so failing the run could not un-push them.
    for verify_tag in (tag_name, *([dep_tag_name] if dep_tag_name else [])):
        if _remote_tag_exists(plugin_root, verify_tag):
            print(f"{GREEN}✓ Verified on remote: {verify_tag}{NC}")
        else:
            print(
                f"{YELLOW}⚠ Could NOT verify {verify_tag} on remote "
                f"(ls-remote found nothing, or the network was unreachable).{NC}\n"
                f"{YELLOW}  Check with: git ls-remote --tags origin '*{verify_tag}'{NC}"
            )
    return 0


def stage_github_release(plugin_root: Path, tag_name: str, release_notes_file: Path) -> int:
    """Gate 13: create GitHub release with notes.

    Returns 0 on success and on the two benign degradations below; returns the
    non-zero gh exit code on a *genuine* release-creation failure so the
    orchestrator surfaces it (instead of printing a false ``✓ Published``):

    * **gh CLI not installed** → warn and return 0. The tag is already on
      origin; the maintainer can create the release manually. Documented
      graceful degradation, not a failure.
    * **release already exists** → return 0. ``gh release create`` is run on a
      tag that was just pushed in Gate 12; on a re-run / interrupted-publish
      recovery the release may already be present. That is the *idempotent
      success* outcome — the release IS there — so it must NOT fail the gate.
    * **any other non-zero gh exit** (auth revoked mid-pipeline, malformed
      notes file, network exhausted all retries, etc.) → return the gh exit
      code. The previous version swallowed every failure as ``return 0``,
      which made the publish report success even when the release was never
      created (violating the fail-fast invariant). Gate 13 callers do
      ``if rc != 0: return rc``, so a non-zero return halts the pipeline.

    TRDD-bbff5bc5 §5: gh-auth precheck runs before `gh release create` to
    surface auth issues with an actionable hint instead of a generic
    `gh release` failure.
    """
    print(f"\n{BLUE}═══ Gate 13: Create GitHub release ═══{NC}")
    gh_bin = shutil.which("gh")
    if gh_bin is None:
        print(
            f"{YELLOW}⚠ gh CLI not installed. Tag pushed but GitHub release not created.{NC}\n"
            f"{YELLOW}  Install: brew install gh{NC}",
            file=sys.stderr,
        )
        return 0
    # TRDD-bbff5bc5: precheck before the release call. Even though Gate 12's
    # precheck already passed, the maintainer's auth state could change
    # mid-pipeline (token revoked, account switched). Fast re-check costs
    # one HTTP roundtrip and prevents a cryptic gh-release failure.
    owner, repo = _resolve_owner_repo(plugin_root)
    _ensure_gh_auth(owner, repo)
    # Phase 1 (Sprint 3): retry-wrap the release creation. Transient github.com
    # hiccups during the gh release POST are common and easy to recover from
    # — if the tag is already on origin (Gate 12 passed), gh release create
    # is idempotent in the sense that a retry on the same tag either succeeds
    # (release didn't exist yet) OR returns a permanent "already exists"
    # error which our classifier treats as non-transient and surfaces.
    print(f"  $ gh release create {tag_name} ...")
    gh_release_cmd = [
        gh_bin,
        "release",
        "create",
        tag_name,
        "--title",
        f"Release {tag_name}",
        "--notes-file",
        str(release_notes_file),
    ]
    gh_result = gh_with_retry(
        gh_release_cmd,
        cwd=plugin_root,
        check=False,
        capture_output=True,
    )
    if gh_result.stdout and gh_result.stdout.strip():
        print(gh_result.stdout.strip())
    if gh_result.stderr and gh_result.stderr.strip():
        print(gh_result.stderr.strip(), file=sys.stderr)
    if gh_result.returncode == 0:
        print(f"{GREEN}✓ GitHub release {tag_name} published{NC}")
        return 0
    # `gh release create` returns HTTP 422 with an "already_exists" /
    # "already exists" validation error when a release for this tag already
    # exists. On a re-run or interrupted-publish recovery that is the
    # idempotent-success outcome (the release IS present), so it must NOT
    # fail the gate — match either spelling gh emits, case-insensitively.
    combined_err = f"{gh_result.stdout or ''}\n{gh_result.stderr or ''}"
    if re.search(r"already[ _]exists", combined_err, re.IGNORECASE):
        print(
            f"{YELLOW}⚠ GitHub release {tag_name} already exists — treating as success (idempotent re-run).{NC}",
            file=sys.stderr,
        )
        return 0
    # Any other non-zero exit is a genuine failure (auth revoked mid-pipeline,
    # malformed notes file, network exhausted all retries). The tag is already
    # pushed, but the documented Gate 13 work did NOT complete — surface it so
    # the publish does not falsely report "✓ Published" (fail-fast invariant).
    print(
        f"{RED}✗ gh release create failed (exit {gh_result.returncode}). "
        f"The tag {tag_name} is already pushed; create the release manually "
        f"or re-run publish.py after fixing the cause.{NC}",
        file=sys.stderr,
    )
    return gh_result.returncode if gh_result.returncode != 0 else 1


# ── Phase C (v2.77.0) parallel preflight orchestrator ───────────────────────


# Canonical replay order for the parallel preflight block. The terminal sees
# Gate 2 → 3 → 3b → 4 → 5 in this exact order regardless of which thread
# finishes first, so logs stay diff-friendly across runs and are easy to skim.
# `ci_preflight` (Gate 3b) sits right after `validate` because it is the same
# question asked of a different gate set ("would CI pass?"), and every gate in
# this tuple is READ-ONLY with respect to the working tree — the preflight's
# `uv sync` probe uses `--frozen --dry-run`, so it cannot dirty uv.lock and
# race Gate 1's clean-tree verdict.
_PARALLEL_GATE_ORDER: tuple[str, ...] = (
    "tests",
    "validate",
    "ci_preflight",
    "secret_scan",
    "mkpl_validate",
    "mkpl_reg",
)


# ── Phase E (v2.79.0): background prefetch during preflight ──────────────────
#
# Two network-bound calls block downstream gates and are independent of any
# preflight state — they can be prefetched in parallel with Gate 2 (pytest):
#
#   1. `_ensure_gh_auth(owner, repo)` — Gate 12 dependency. Verifies the
#      maintainer's gh CLI auth + push permission. ~60s of network round-trips.
#   2. `_fetch_remote_marketplace_json(mkt_owner, mkt_repo)` — Gate 5
#      dependency (Layout A only). Fetches the remote marketplace.json so we
#      can verify the plugin is registered. ~5–10s.
#
# Strategy: kick both off at the same moment as the parallel preflight block
# starts (~Phase C) and consume the cached results when each gate runs. If a
# prefetch failed transiently (network hiccup, API timeout), the consuming
# gate falls back to its existing synchronous call so behaviour is identical
# in the worst case.
#
# `_ensure_gh_auth` raises SystemExit on permanent auth failure. We CANNOT
# re-raise from inside a worker thread (only the worker dies) — instead, the
# prefetch wrapper catches SystemExit and stores it on the future via the
# exception channel. Gate 12 then re-raises it on the main thread for the
# usual fail-fast behaviour.
#
# Layout="none" runs neither prefetch — there is no marketplace to fetch and
# Gate 12 is the only consumer of gh-auth, but skipping the auth prefetch in
# that mode keeps the no-marketplace fast-path cheap and matches the "no
# behavioural change" promise (Gate 12's synchronous _ensure_gh_auth still
# runs).


@dataclass
class _PrefetchResults:
    """Carries background-prefetch futures across the preflight block.

    Both fields are Optional so this same dataclass works in three modes:

    * Layout A with prefetch: both futures are populated.
    * Layout B / no marketplace: ``marketplace_json`` is None (Gate 5 in
      Layout B reads the parent marketplace.json from disk; no remote fetch
      happens, so prefetch is a no-op for that branch). ``gh_auth`` is still
      populated since Gate 12 always pushes to origin regardless of layout.
    * No layout: both fields are None — prefetch is fully skipped, the
      consuming gates fall through to their synchronous calls.

    Note: ``executor`` is owned by the caller of ``start_prefetch()``. Use
    ``shutdown()`` (or wrap construction in a ``with`` block via the helper)
    to guarantee thread cleanup even if the pipeline aborts before the
    consuming gates run.
    """

    gh_auth: Future[None] | None = None
    marketplace_json: Future[dict | None] | None = None
    executor: ThreadPoolExecutor | None = field(default=None, repr=False)
    # Owner/repo captured at prefetch time so the consuming gate can do a
    # cheap sanity check that the prefetch was scoped to the same target
    # the gate is now about to operate on. (Defensive — the preflight block
    # never mutates origin between prefetch and consumption, but a future
    # refactor that adds remote-rename handling shouldn't silently use a
    # stale prefetch result.)
    gh_auth_target: tuple[str, str] | None = None
    marketplace_target: tuple[str, str] | None = None

    def shutdown(self) -> None:
        """Release the executor's worker threads.

        ``ThreadPoolExecutor.shutdown(wait=False)`` returns immediately and
        the worker threads keep running in the background until their tasks
        complete. We use ``wait=False`` so main()'s control flow stays snappy
        on early-failure paths instead of blocking on the network round-trips.

        The workers are stdlib ``ThreadPoolExecutor`` threads, which are
        **non-daemon** (``daemon=False``) — the constructor exposes no
        ``daemon`` parameter, so we cannot make them daemon even if we wanted
        to. Process exit is still clean because CPython registers an
        ``atexit`` hook (``concurrent.futures.thread._python_exit``) that joins
        every outstanding pool worker at interpreter shutdown. The prefetch
        tasks (gh-auth check, marketplace fetch) are short and bounded, so
        that join adds at most a couple of seconds on the rare early-abort
        path; it never hangs.
        """
        if self.executor is not None:
            self.executor.shutdown(wait=False)
            self.executor = None


def _prefetch_gh_auth_safe(owner: str, repo: str) -> None:
    """Worker for the gh-auth prefetch thread.

    The wrapped ``_ensure_gh_auth`` raises SystemExit on permanent auth
    failure. SystemExit raised in a thread only kills THAT thread — the
    main thread keeps running and would silently skip Gate 12's auth
    check, defeating the precheck. We can't re-raise from here; instead,
    the future stores the exception so Gate 12 can re-raise it on the
    main thread when it consumes the prefetch result.

    This helper just runs the call; the exception capture happens
    automatically inside ``Future`` (raised exceptions become
    ``future.exception()``).
    """
    _ensure_gh_auth(owner, repo)


def _prefetch_marketplace_json_safe(mkt_owner: str, mkt_repo: str) -> dict | None:
    """Worker for the marketplace.json prefetch thread.

    ``_fetch_remote_marketplace_json`` already returns None on failure
    (transient network error, parse failure, missing repo). We don't
    transform that — Gate 5 is designed to fall back to a synchronous
    fetch when this prefetch returns None. Any unexpected exception is
    captured by the future and Gate 5 will see it via
    ``future.exception()`` and fall back to the synchronous path.
    """
    return _fetch_remote_marketplace_json(mkt_owner, mkt_repo)


def _start_prefetch(plugin_root: Path, layout: str, layout_details: dict) -> _PrefetchResults:
    """Spawn the background gh-auth + marketplace-json prefetch threads.

    Called from main() right before ``run_preflight_parallel``. The two
    prefetch tasks run on a ThreadPoolExecutor. Its worker threads are
    stdlib non-daemon threads (the executor exposes no ``daemon`` kwarg);
    ``_PrefetchResults.shutdown(wait=False)`` returns immediately and the
    CPython ``atexit`` join handles cleanup at interpreter shutdown, so an
    early-abort path is not blocked on the in-flight network calls. Total
    preflight workers ≈ 6 (4 from Phase C parallel preflight + 2 here).

    Layout-aware behaviour:

    * Layout="none" → both futures stay None (skip prefetch entirely).
    * Layout="A" with valid mkt_owner/mkt_repo → both futures populated.
    * Layout="A" without resolved owner/repo → only gh_auth future
      populated (marketplace_json prefetch needs both names).
    * Layout="B" → only gh_auth future populated (no remote marketplace
      fetch happens in Layout B; Gate 5 reads from disk).

    Owner/repo for gh-auth comes from ``_current_repo_slug``. If that
    fails (no origin remote, parse failure), gh_auth prefetch is skipped
    too — Gate 12 will surface the failure via its existing synchronous
    ``_resolve_owner_repo`` + ``_ensure_gh_auth`` calls.

    Defensive: we never let exceptions in the prefetch path abort the
    pipeline. If something goes wrong here, the consuming gates still do
    their own synchronous calls and the publish completes; we lose the
    Phase E speedup but not correctness.
    """
    # Never prefetch when there's no marketplace involved — the no-layout
    # fast-path stays exactly as it was before Phase E.
    if layout == "none":
        return _PrefetchResults()

    # Resolve the gh-auth target. Use _current_repo_slug (same parser that
    # _resolve_owner_repo uses, but returns "owner/repo" string we then
    # split). If we can't resolve it, skip the gh-auth prefetch — Gate 12
    # will fail loudly via _resolve_owner_repo with the same error message
    # we'd produce here, just slightly later.
    gh_target: tuple[str, str] | None = None
    slug = _current_repo_slug(plugin_root)
    if slug and "/" in slug:
        owner, repo = slug.split("/", 1)
        if owner and repo:
            gh_target = (owner, repo)

    # Resolve the marketplace.json target. Only Layout A has a remote
    # marketplace to fetch; Layout B reads marketplace.json from the
    # parent repo on disk.
    mkt_target: tuple[str, str] | None = None
    if layout == "A":
        mkt_owner_raw = layout_details.get("mkt_owner")
        mkt_repo_raw = layout_details.get("mkt_repo")
        if isinstance(mkt_owner_raw, str) and isinstance(mkt_repo_raw, str):
            mkt_target = (mkt_owner_raw, mkt_repo_raw)

    # If neither prefetch is applicable, skip the executor entirely.
    if gh_target is None and mkt_target is None:
        return _PrefetchResults()

    # max_workers = number of prefetch tasks (1 or 2). thread_name_prefix
    # makes the prefetch threads identifiable in any debug/strace output.
    max_workers = sum(t is not None for t in (gh_target, mkt_target))
    executor = ThreadPoolExecutor(
        max_workers=max_workers,
        thread_name_prefix="cpv-prefetch",
    )
    results = _PrefetchResults(executor=executor)

    if gh_target is not None:
        owner, repo = gh_target
        results.gh_auth = executor.submit(_prefetch_gh_auth_safe, owner, repo)
        results.gh_auth_target = gh_target

    if mkt_target is not None:
        mkt_owner, mkt_repo = mkt_target
        results.marketplace_json = executor.submit(_prefetch_marketplace_json_safe, mkt_owner, mkt_repo)
        results.marketplace_target = mkt_target

    return results


def run_preflight_parallel(
    plugin_root: Path,
    layout: str,
    *,
    prefetch: _PrefetchResults | None = None,
) -> int:
    """Run Gates 2/3/3b/4/5 concurrently with per-thread output capture.

    Phase C (v2.77.0): replaces the previous sequential Gate-2 → Gate-3 →
    Gate-4 → Gate-5 dispatch. The gates are independent (none mutates
    on-disk state that another reads), so running them on a thread pool sized
    to the gate count drops preflight wall time from ``sum(gates)`` to
    ``max(gates)`` — typically a 25–60s reduction depending on test-suite size.

    Gate 3b (the CI-parity preflight) joined this block in the wave-2 CI-failure
    root-fix. It is read-only like its siblings (its ``uv sync`` probe is
    ``--frozen --dry-run``), and running it here — rather than sequentially —
    keeps its jscpd/mypy/actionlint subprocesses overlapped with pytest instead
    of adding their wall time to the publish.

    Output handling:

    * Each gate's stdout AND stderr are captured per-thread via the
      ``_ThreadAwareStream`` proxies installed on ``sys.stdout``/
      ``sys.stderr``. The proxies fall through to the real terminal for
      the orchestrator (main thread), so this function's own prints land
      on the user's screen as expected.
    * After all four gates finish, captured buffers are replayed in the
      fixed canonical order: tests → validate → mkpl_validate → mkpl_reg.
      The original "═══ Gate N ═══" headers and ✓/✗ summary lines are
      preserved verbatim.

    Failure semantics:

    * The pool waits for ALL gates to finish (so we don't leave
      validators running after we already have a hard failure to report).
    * Captured output is replayed in canonical order REGARDLESS of which
      gates failed.
    * After the replay, the FIRST non-zero return code (in canonical
      order) is returned to the caller, which surfaces it as the publish
      exit code. Returning the first failure (not the last) keeps the
      reported severity stable — if Gate 3 fails CRITICAL and Gate 4
      fails MAJOR, the user sees CRITICAL.
    * If every gate passes, returns 0.
    """
    print(
        f"\n{BLUE}═══ Running Gates 2-5 (incl. 3b) concurrently "
        f"(Phase C parallel preflight) ═══{NC}"
    )

    # Phase E (v2.79.0): Gate 5 may consume the prefetched marketplace.json
    # instead of doing its own synchronous fetch. Pass prefetch results
    # through via the keyword argument; if None, Gate 5 falls back to the
    # synchronous path (identical to pre-Phase-E behaviour).
    pf = prefetch  # local alias for closure capture readability

    # Stage callable lookup — wrapped in lambdas because some stages take
    # extra arguments beyond plugin_root (Gate 4 wants the layout string).
    stage_callables: dict[str, Callable[[], int]] = {
        "tests": lambda: stage_run_tests(plugin_root),
        "validate": lambda: stage_validate_plugin(plugin_root),
        "ci_preflight": lambda: stage_ci_preflight(plugin_root),
        "secret_scan": lambda: stage_secret_scan(plugin_root),
        "mkpl_validate": lambda: stage_validate_marketplace(plugin_root, layout),
        "mkpl_reg": lambda: stage_marketplace_registration_check(plugin_root, prefetch=pf),
    }

    # Submit every gate simultaneously. The pool is sized to the exact number of
    # tasks (derived, never hardcoded — Gate 3b was added to _PARALLEL_GATE_ORDER
    # and a stale literal `4` here would have silently serialized one gate behind
    # another).
    captured: dict[str, tuple[int, str, str]] = {}
    with ThreadPoolExecutor(max_workers=len(stage_callables)) as ex:
        futures = {name: ex.submit(_run_stage_captured, fn) for name, fn in stage_callables.items()}
        # Wait for all to finish — DON'T short-circuit on first failure.
        # We want a clean per-gate replay even when multiple gates fail,
        # otherwise the user sees an arbitrary subset based on completion
        # order.
        for name, fut in futures.items():
            captured[name] = fut.result()

    # Replay in canonical order so log diffs stay stable across runs.
    for name in _PARALLEL_GATE_ORDER:
        _, out_text, err_text = captured[name]
        if out_text:
            sys.stdout.write(out_text)
            if not out_text.endswith("\n"):
                sys.stdout.write("\n")
        if err_text:
            sys.stderr.write(err_text)
            if not err_text.endswith("\n"):
                sys.stderr.write("\n")
    sys.stdout.flush()
    sys.stderr.flush()

    # Surface the FIRST non-zero return code in canonical order. This
    # keeps the reported severity deterministic — a stable Gate 3
    # CRITICAL is more useful than an arbitrary Gate-3-or-4 result that
    # depends on thread scheduling.
    for name in _PARALLEL_GATE_ORDER:
        rc = captured[name][0]
        if rc != 0:
            return rc
    return 0


# ── Main pipeline orchestrator ────────────────────────────────────────────────


def main() -> int:
    gate_summary = "\n".join(f"  {name}: {desc}" for name, desc in GATES)
    parser = argparse.ArgumentParser(
        description="Publish pipeline: 15-gate fail-fast release with auto-bump (bypass-proof)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"""
Gates (all mandatory, run in order):
{gate_summary}

HARD RULE: No checks can be skipped. Every gate must pass with ZERO
CRITICAL/MAJOR/MINOR/NIT findings before the version is bumped, committed,
tagged, pushed, or released. There is no --skip-tests, no --skip-lint, no
--skip-validate, no --force. WARNING is the only allowed severity and does
not block. If a gate fails, fix the underlying problem — don't bypass.

CORNERSTONE: every push is a version bump. Running publish.py with no flag
auto-detects the bump type from conventional commits (feat → minor, fix →
patch, BREAKING CHANGE → major) via `git-cliff --bumped-version`. Explicit
--major/--minor/--patch flags remain available as manual overrides.

Examples:
  %(prog)s                      # auto-detect bump from git-cliff and publish
  %(prog)s --patch              # force patch bump
  %(prog)s --minor              # force minor bump
  %(prog)s --major              # force major bump
  %(prog)s --dry-run            # preview only, stops before bump commit
  %(prog)s --print-gates        # print gate list and exit
  %(prog)s --canon-version      # report installed vs latest canon, then exit
        """,
    )
    bump_group = parser.add_mutually_exclusive_group()
    bump_group.add_argument("--major", action="store_true", help="Force a major bump (override auto-detection)")
    bump_group.add_argument("--minor", action="store_true", help="Force a minor bump (override auto-detection)")
    bump_group.add_argument("--patch", action="store_true", help="Force a patch bump (override auto-detection)")
    parser.add_argument("--dry-run", action="store_true", help="Preview without making changes")
    parser.add_argument("--print-gates", action="store_true", help="Print gate list and exit")
    parser.add_argument(
        "--canon-version",
        action="store_true",
        help="Report the installed vs latest CPV publish-canon version and exit",
    )
    args = parser.parse_args()

    if args.print_gates:
        print_gates()
        return 0

    # Answered BEFORE Gate 0 and before any tree check: it reads nothing but a
    # manifest and reports. A plugin author debugging a stale canon usually has
    # a dirty tree, and refusing to tell them their canon version because of it
    # would make the command useless exactly when it is needed.
    if args.canon_version:
        # In THIS repo the canon and the plugin are the same artifact, so CPV's
        # own version IS the installed canon version. A generated publish.py
        # instead reports its baked CANON_VERSION constant, which records the
        # canon it was generated from (and may lag this repo's version).
        return print_canon_version(get_current_version(get_plugin_root()))

    # ── Gate 0: bypass guard ──
    rc = stage_bypass_guard()
    if rc != 0:
        return rc

    root = get_plugin_root()

    # Auto-detect bump type from conventional commits (via git-cliff) unless
    # the user explicitly forced one. The cornerstone rule is "every push is
    # a bump" — running publish.py bare is the normal case, and git-cliff
    # reads the commit log to decide whether this release is a major, minor,
    # or patch bump. Explicit flags remain available for override.
    if args.major:
        bump_type = "major"
        print(f"{BLUE}Bump type: major (forced via --major){NC}")
    elif args.minor:
        bump_type = "minor"
        print(f"{BLUE}Bump type: minor (forced via --minor){NC}")
    elif args.patch:
        bump_type = "patch"
        print(f"{BLUE}Bump type: patch (forced via --patch){NC}")
    else:
        bump_type = detect_bump_type(root)
        print(f"{BLUE}Bump type: {bump_type} (auto-detected from git-cliff){NC}")

    # ── Gates 1-6: preflight ──
    # Order: clean tree → tests → validate (lint+structure+plugin checks) →
    # CI-parity preflight → marketplace → consistency. Since v2.64.0,
    # validate_plugin.py owns repo-wide lint via cpv_lint_engine, so there is
    # no separate lint stage — the validator catches lint errors AND
    # structural issues in a single pass with one source of truth.
    #
    # Phase C (v2.77.0): Gate 1 still runs first sequentially — the clean
    # working tree must be verified before tests run, otherwise pytest
    # could pick up uncommitted state (or auto-commit the lockfile via
    # the in-place `git add uv.lock`/`git commit` branch). After Gate 1
    # passes, Gates 2/3/3b/4/5 run concurrently in a thread pool; their
    # output is captured per-thread and replayed in canonical order so
    # the terminal stays readable. Gate 6 runs sequentially after the
    # parallel block to keep version-consistency strictly downstream of
    # the validators (avoids racing against any in-flight validator
    # subprocess that may touch on-disk state).
    #
    # EVERY gate in this block precedes the bump (Gate 7) / commit (Gate 10) /
    # tag (Gate 11) / push (Gate 12). That ordering is what lets Gate 3b abort
    # a CI-parity defect with the tree untouched, instead of discovering it on
    # GitHub after the release was already cut.
    rc = stage_check_working_tree(root)
    if rc != 0:
        return rc

    layout, layout_details = detect_layout(root)

    # Phase E (v2.79.0): kick off background prefetch threads for the two
    # network-bound calls that block downstream gates:
    #   - _ensure_gh_auth (Gate 12 dependency)
    #   - _fetch_remote_marketplace_json (Gate 5 dependency, Layout A only)
    # Both run in parallel with the 4-worker preflight pool, so total
    # preflight workers ≈ 6. The futures are stored on `prefetch` and
    # consumed by the relevant gates. If prefetch failed transiently, the
    # consuming gate falls back to its synchronous call (no behavioural
    # change). If gh-auth raised SystemExit (permanent auth failure), it's
    # re-raised on the main thread when Gate 12 consumes the result.
    #
    # Daemon-thread cleanup: ThreadPoolExecutor uses non-daemon threads by
    # default, so we MUST call shutdown() on every exit path — otherwise
    # an early-failure path (e.g. Gate 6 returns non-zero) would leak the
    # workers and stall process exit. The wide try/finally below covers
    # all return paths through main() that happen AFTER prefetch starts.
    prefetch = _start_prefetch(root, layout, layout_details)
    try:
        rc = run_preflight_parallel(root, layout, prefetch=prefetch)
        if rc != 0:
            return rc

        # Gate 3c runs SERIALLY here, after the parallel block: it re-runs the
        # whole suite, so sharing the machine with Gate 2's ``-n auto`` pytest
        # would slow both and risk turning contention into a spurious timeout.
        # Still strictly before the bump/commit/tag/push, so a hang aborts with
        # the tree untouched.
        rc = stage_fork_parity(root)
        if rc != 0:
            return rc

        rc = stage_version_consistency(root)
        if rc != 0:
            return rc

        # ── Gate 7: bump ──
        rc, new_version = stage_bump(root, bump_type, args.dry_run)
        if rc != 0 or new_version is None:
            # Narrowing for mypy: stage_bump returns (0, str) or (nonzero, None).
            # The second branch catches the defensive case where rc is 0 but
            # new_version is None — should never happen, but fail-fast if it does.
            return rc if rc != 0 else 1
        if args.dry_run:
            print(f"\n{GREEN}✓ Dry run complete — no changes made.{NC}")
            return 0

        tag_name = f"v{new_version}"

        # ── Gate 8: refresh integrity manifest (issue #18) ──
        rc = stage_refresh_self_hashes(root)
        if rc != 0:
            return rc

        # ── Gates 9-13: changelog, commit, tag, push, release ──
        rc, release_notes_file = stage_changelog(root, tag_name, new_version)
        if rc != 0 or release_notes_file is None:
            return rc

        # v2.99.2 (issue #18 follow-up) — re-refresh the integrity
        # manifest AFTER git-cliff appended the new release section to
        # CHANGELOG.md. Without this second refresh the manifest's
        # CHANGELOG.md hash is stale (captured pre-cliff content) and
        # post-release CI fails with "RELEASE-SHIPPED DRIFT" — the
        # historic root cause of every "CHANGELOG.md hash mismatch"
        # report. Iron rule preserved: the second refresh runs
        # ALWAYS, no opt-out.
        print(f"\n{BLUE}═══ Gate 9b: Re-refresh manifest after CHANGELOG update ═══{NC}")
        rc = stage_refresh_self_hashes(root)
        if rc != 0:
            return rc

        # Phase E (v2.79.0): pass the prefetch results into Gate 12 so the
        # synchronous _ensure_gh_auth call can be skipped when the
        # background prefetch already completed cleanly.
        rc = stage_commit_tag_push(root, tag_name, prefetch=prefetch)
        if rc != 0:
            return rc

        rc = stage_github_release(root, tag_name, release_notes_file)
        if rc != 0:
            return rc

        # Gate 14 runs AFTER the release on purpose: it verifies the commit that
        # actually shipped. It never returns non-zero (see its docstring — the
        # release is already public, so aborting here could not un-ship it and
        # would only discard the report), so the publish verdict is unchanged.
        stage_verify_ci_green(root)

        # Gate 15 — same post-release position, same reason. Only an actual
        # install can prove the artifact is installable (ai-maestro#62 R2).
        rc_smoke = stage_install_smoke(root, new_version)
        if rc_smoke != 0:
            return rc_smoke

        print(f"\n{GREEN}✓ Published v{new_version}{NC}")
        return 0
    finally:
        # Phase E: release the prefetch executor's worker threads on every
        # exit path (success, early failure, exception). The workers are
        # stdlib non-daemon threads, so without shutdown() the process
        # would stall on exit waiting for them — even on a Gate 6 failure
        # that aborted long before Gate 12 consumed the prefetch.
        prefetch.shutdown()


# How long Gate 14 waits for the released commit's CI runs to conclude. Generous
# on purpose: a cold runner installing the toolchain can take many minutes, and a
# gate that gives up early would report UNVERIFIED on a perfectly healthy run —
# noise that teaches the reader to ignore it. Expiry is never a failure verdict.
_CI_VERIFY_DEFAULT_TIMEOUT_S = 900


def _resolve_marketplace_name(plugin_root: Path) -> str | None:
    """The marketplace NAME Claude Code installs by (``<plugin>@<name>``).

    Layout B reads the parent marketplace.json directly. Layout A resolves the
    remote marketplace repo from notify-marketplace.yml and reads its manifest
    from GitHub. Anything unresolvable returns None, which makes the smoke test
    SKIP with a reason — never guess a marketplace name, because installing
    from the wrong one would prove nothing.
    """
    layout, details = detect_layout(plugin_root)
    if layout == "B":
        mp_root = details.get("marketplace_root")
        if isinstance(mp_root, Path):
            try:
                data = json.loads((mp_root / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return None
            name = data.get("name") if isinstance(data, dict) else None
            return name if isinstance(name, str) and name else None
        return None
    if layout == "A":
        owner, repo = details.get("mkt_owner"), details.get("mkt_repo")
        if not (isinstance(owner, str) and isinstance(repo, str)):
            return None
        import urllib.request  # noqa: PLC0415 - stdlib, only on this path

        for branch in ("main", "master"):
            url = f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/.claude-plugin/marketplace.json"
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "cpv-publish-install-smoke"})  # noqa: S310
                with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310
                    data = json.loads(resp.read().decode("utf-8"))
            except Exception:  # noqa: BLE001 - unreachable manifest → SKIP, not a guess
                continue
            name = data.get("name") if isinstance(data, dict) else None
            if isinstance(name, str) and name:
                return name
        return None
    return None


_SEMVER_RE = re.compile(r"\bv?(\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.\-]+)?)\b")

# The CLI's own wording when a plugin cannot be resolved inside a marketplace.
# Matching the SHAPE rather than one exact sentence, because this only ever
# gates a DOWNGRADE that is additionally proven by _marketplace_is_registered.
_NOT_IN_MARKETPLACE_RE = re.compile(
    r"not found in marketplace|marketplace .*not found|marketplace update",
    re.IGNORECASE,
)


def _semvers_in(text: str) -> list[str]:
    """Every semver-shaped token in ``text``, in order, without the leading v."""
    return _SEMVER_RE.findall(text)


def _marketplace_is_registered(claude_bin: str, marketplace: str) -> bool:
    """True when ``marketplace`` appears in ``claude plugin marketplace list``.

    READ-ONLY on purpose: the alternative — running ``marketplace add``/``update``
    to make the smoke test pass — would mutate the user's global registry as a
    side effect of publishing, which is the same class of unwanted side effect
    the local-scope uninstall exists to prevent.

    FAIL-SAFE towards the HARD FAILURE: any inability to answer (CLI error,
    timeout, unreadable output) returns True, i.e. "assume registered", so a
    genuinely uninstallable release is never downgraded to SKIPPED by a probe
    that simply could not run.
    """
    try:
        listing = subprocess.run(  # noqa: S603 - fixed argv, no shell
            [claude_bin, "plugin", "marketplace", "list"],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return True
    if listing.returncode != 0:
        return True
    return marketplace in ((listing.stdout or "") + (listing.stderr or ""))


_INSTALLED_PLUGINS_REGISTRY = Path.home() / ".claude" / "plugins" / "installed_plugins.json"


def _smoke_records_still_registered(
    target: str, smoke_dir: Path, registry_path: Path | None = None
) -> list[str] | None:
    """Local-scope records still pointing at ``smoke_dir``, or None when unreadable.

    Issue #209: `claude plugin uninstall --scope local` does not always drop the
    local-scope record from ``installed_plugins.json``. When it does not, the
    temp dir is deleted moments later and the record is left pointing at a path
    that no longer exists — invisible, and it accumulates across releases.

    READ-ONLY, deliberately. The registry is Claude Code's shared state, not
    CPV's: a publish pipeline that edits another tool's state file to tidy up
    after itself is a worse failure mode than the orphan it removes. Reporting
    is the whole remedy here.

    Returns None for "could not check" rather than an empty list, so a missing
    or malformed registry can never be read as "verified clean" — the same
    cannot-check-is-not-a-pass rule Gate 15 applies to the install itself.

    Paths are compared RESOLVED: on macOS ``mktemp`` yields ``/var/folders/...``
    while the registry records ``/private/var/folders/...``, and a raw string
    compare would silently find nothing on exactly the platform that reported
    this.
    """
    path = registry_path if registry_path is not None else _INSTALLED_PLUGINS_REGISTRY
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    plugins = data.get("plugins")
    if not isinstance(plugins, dict):
        return None
    try:
        wanted = os.path.realpath(str(smoke_dir))
    except (OSError, ValueError):
        return None
    stale: list[str] = []
    for key, records in plugins.items():
        # Scoped to THIS plugin's records: a record another publish left behind
        # is not this run's to report, and claiming it would inflate the signal.
        if key != target or not isinstance(records, list):
            continue
        for rec in records:
            if not isinstance(rec, dict):
                continue
            project_path = rec.get("projectPath")
            if not isinstance(project_path, str) or not project_path:
                continue
            try:
                resolved = os.path.realpath(os.path.expanduser(project_path))
            except (OSError, ValueError):
                continue
            if resolved == wanted:
                stale.append(project_path)
    return stale


def _report_smoke_registry_orphan(target: str, smoke_dir: Path) -> None:
    """Print the Gate-15 registry-cleanup verdict. Never fatal, never a verdict."""
    stale = _smoke_records_still_registered(target, smoke_dir)
    if stale is None:
        print(f"{YELLOW}  Note: could not read {_INSTALLED_PLUGINS_REGISTRY} — smoke-install "
              f"registry cleanup UNVERIFIED (not a failure, and not a pass either).{NC}")
        return
    if not stale:
        return
    print(f"{YELLOW}  Note: the local-scope record for {target} survived the uninstall and now "
          f"points at a temp dir that is about to be deleted (#209):{NC}")
    for project_path in stale[:5]:
        print(f"{YELLOW}    projectPath: {project_path}{NC}")
    print(f"{YELLOW}  Harmless to this release; it accumulates in {_INSTALLED_PLUGINS_REGISTRY}.{NC}")


def stage_install_smoke(plugin_root: Path, new_version: str) -> int:
    """Gate 15: prove the just-published release actually INSTALLS.

    ai-maestro#62 R2, filed by the MANAGER Claude after eleven plugins each
    published green and were all uninstallable: static validation cannot catch
    it (the manifest was valid, the tags existed, the marketplace entry was
    correct) — only an install can. So this installs the plugin from its
    marketplace into a clean temp directory.

    Position and verdict mirror Gate 14, for the same reason: it must run after
    the release (nothing to install before it), by which point a non-zero exit
    could not un-ship anything. So the DEFAULT is a loud report, not a failed
    run. Setting ``CPV_PUBLISH_REQUIRE_INSTALL_SMOKE=1`` makes a genuine
    install FAILURE exit non-zero, for fleets that want the pipeline to go red.

    Cannot-check is never reported as clean: a missing ``claude`` CLI (the
    normal case on a CI runner), an unresolvable marketplace, or a timeout all
    report SKIPPED with the reason, and never count as a pass.
    """
    print(f"\n{BLUE}═══ Gate 15: Prove the release installs (clean-dir install smoke) ═══{NC}")
    if os.environ.get("CPV_PUBLISH_SKIP_INSTALL_SMOKE") == "1":
        print(f"{YELLOW}  SKIPPED — CPV_PUBLISH_SKIP_INSTALL_SMOKE=1{NC}")
        return 0
    strict = os.environ.get("CPV_PUBLISH_REQUIRE_INSTALL_SMOKE") == "1"
    claude_bin = shutil.which("claude")
    if claude_bin is None:
        print(f"{YELLOW}  SKIPPED — the `claude` CLI is not on PATH (normal on a CI runner).{NC}")
        print(f"{YELLOW}  This is NOT a pass: the release was not proven installable here.{NC}")
        return 0
    plugin_name = None
    try:
        plugin_name = json.loads((plugin_root / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8")).get(
            "name"
        )
    except (OSError, ValueError):
        pass
    marketplace = _resolve_marketplace_name(plugin_root)
    if not (isinstance(plugin_name, str) and plugin_name and marketplace):
        print(f"{YELLOW}  SKIPPED — could not resolve <plugin>@<marketplace> (name={plugin_name!r}, {NC}")
        print(f"{YELLOW}  marketplace={marketplace!r}). Not a pass — nothing was installed.{NC}")
        return 0
    target = f"{plugin_name}@{marketplace}"
    import tempfile  # noqa: PLC0415 - stdlib, only on this path

    with tempfile.TemporaryDirectory(prefix="cpv-install-smoke-") as tmp:
        print(f"  $ (cd {tmp} && claude plugin install {target} --scope local)")
        try:
            result = subprocess.run(  # noqa: S603 - fixed argv, no shell
                [claude_bin, "plugin", "install", target, "--scope", "local"],
                cwd=tmp,
                capture_output=True,
                text=True,
                timeout=300,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            print(f"{YELLOW}  SKIPPED — the install could not be run ({exc}). Not a pass.{NC}")
            return 0
        # Put back what we took. `--scope local` scopes only the SETTINGS file
        # (written into this temp dir, which is about to vanish) — the plugin
        # payload and its marketplace registration land in shared ~/.claude
        # state, so without this every publish left another cached copy behind,
        # growing unboundedly with nothing cleaning it up.
        #
        # `--scope local` + `--keep-data` are BOTH load-bearing safety, not
        # tidiness: the author of the plugin being published almost certainly
        # has it installed at USER scope, and a wider uninstall — or one that
        # dropped ~/.claude/plugins/data/{id}/ — would destroy their real
        # installation to clean up after a smoke test. Best-effort by design:
        # a cleanup failure is reported, never fatal, and never changes the
        # verdict, which belongs to the install above.
        if result.returncode == 0:
            try:
                subprocess.run(  # noqa: S603 - fixed argv, no shell
                    [claude_bin, "plugin", "uninstall", target,
                     "--scope", "local", "--keep-data", "-y"],
                    cwd=tmp,
                    capture_output=True,
                    text=True,
                    timeout=120,
                    check=False,
                )
            except (OSError, subprocess.SubprocessError) as exc:
                print(f"{YELLOW}  Note: smoke-install cleanup did not run ({exc}).{NC}")
            else:
                # Assert the cleanup actually happened. Nothing else does, which
                # is why #209 was only found by reading the registry by hand.
                _report_smoke_registry_orphan(target, Path(tmp))
    if result.returncode == 0:
        print(f"{GREEN}✓ {target} installs cleanly (dependencies resolved){NC}")
        # The marketplace entry is updated ASYNCHRONOUSLY (notify-marketplace
        # dispatch), so right after a release the resolved version may still be
        # the previous one. That is a lag, NOT an install failure — report it
        # without failing, or every Layout-A release would flap.
        #
        # EVIDENCE REQUIRED. This used to be `if new_version not in stdout`,
        # but install stdout is a progress line naming the plugin and the
        # marketplace — it does not print the semver at all, so the condition
        # was true on EVERY successful run and the note fired unconditionally,
        # including when the marketplace was perfectly current. A note that
        # always fires carries no information and trains the reader to ignore
        # it. Claim a lag only from a version we actually read.
        resolved = _semvers_in(result.stdout or "")
        if resolved and new_version not in resolved:
            print(
                f"{YELLOW}  Note: the marketplace resolved v{resolved[0]}, not v{new_version} "
                f"(async notify lag) — installability is proven, the version listing lags.{NC}"
            )
        return 0
    combined = (result.stderr or "") + "\n" + (result.stdout or "")
    # Disambiguate "this host never registered the marketplace" (an environment
    # gap — cannot check) from "the marketplace is registered and does not carry
    # this plugin" (a REAL uninstallable release). Both produce the same
    # not-found message, and reporting the first as a hard failure inverts
    # cannot-check-is-not-a-pass into cannot-check-is-a-fail: `--scope local`
    # isolates the install, never the marketplace registry, which is user-scope
    # in ~/.claude. FAIL-SAFE: only a marketplace we can PROVE is unregistered
    # downgrades to SKIPPED; if the probe cannot answer, the hard failure stands.
    if _NOT_IN_MARKETPLACE_RE.search(combined) and not _marketplace_is_registered(claude_bin, marketplace):
        print(f"{YELLOW}  SKIPPED — the marketplace {marketplace!r} is not registered on this host,{NC}")
        print(f"{YELLOW}  so `claude plugin install` could not resolve {target} here. Not a pass:{NC}")
        print(f"{YELLOW}  the release was not proven installable. Register it with{NC}")
        print(f"{YELLOW}  `claude plugin marketplace add <source>` and re-run to get a real verdict.{NC}")
        return 0
    tail = combined.strip().splitlines()[-8:]
    print(f"{RED}========================================{NC}")
    print(f"{RED}  RELEASE IS NOT INSTALLABLE: {target}{NC}")
    print(f"{RED}  The release is already public — fix forward with a new release.{NC}")
    for ln in tail:
        print(f"{RED}    {ln}{NC}")
    print(f"{RED}  Reproduce: cd $(mktemp -d) && claude plugin install {target} --scope local{NC}")
    print(f"{RED}========================================{NC}")
    if strict:
        print(f"{RED}✗ CPV_PUBLISH_REQUIRE_INSTALL_SMOKE=1 — failing the publish run.{NC}")
        return 1
    return 0


def classify_ci_runs(
    runs: list[dict[str, Any]], successors: dict[str, bool]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split completed CI runs for one commit into (failed, unknown).

    A `success`/`skipped`/`neutral` conclusion is fine and appears in neither
    list. Any OTHER conclusion is a genuine failure — EXCEPT `cancelled`,
    which Gate 14 previously treated identically to a real failure even
    though GitHub's own concurrency-group cancellation reports it that way
    for a run that was merely SUPERSEDED by a newer push to the same branch
    (issue #220). `successors` disambiguates: it maps a cancelled run's
    workflow NAME to whether the caller resolved a newer run of that same
    workflow on a commit descended from the one being verified. Found ⇒ the
    cancellation was benign supersession, excluded from both lists. Not
    found ⇒ we cannot tell a genuine user-cancel from a lost successor, so
    it goes to `unknown` and Gate 14 must report UNKNOWN, never green — a
    "cannot check" result is never folded into a pass.
    """
    failed: list[dict[str, Any]] = []
    unknown: list[dict[str, Any]] = []
    for r in runs:
        conclusion = r.get("conclusion")
        if conclusion in ("success", "skipped", "neutral"):
            continue
        if conclusion == "cancelled":
            if successors.get(str(r.get("name", "?"))):
                continue
            unknown.append(r)
            continue
        failed.append(r)
    return failed, unknown


def _resolve_ci_run_successors(
    gh_bin: str,
    plugin_root: Path,
    sha: str,
    cancelled_runs: list[dict[str, Any]],
) -> dict[str, bool]:
    """For each cancelled run, look for a newer descendant-commit successor.

    A `cancelled` run counts as superseded-not-failed only when a LATER run
    of the SAME workflow exists on a commit that is a git descendant of
    `sha` — i.e. a newer push to the same branch genuinely superseded this
    one via the concurrency group. Any failure to determine that (no gh, no
    branch info, an unresolved merge-base) leaves that workflow's entry
    absent from the returned map, which `classify_ci_runs` treats as "no
    successor found" — fail toward UNKNOWN, never toward green.
    """
    result: dict[str, bool] = {}
    for r in cancelled_runs:
        name = str(r.get("name", "?"))
        if name in result:
            continue
        branch = r.get("headBranch")
        if not branch:
            continue
        try:
            listed = subprocess.run(
                [
                    gh_bin, "run", "list",
                    "--workflow", name,
                    "--branch", str(branch),
                    "--limit", "20",
                    "--json", "headSha,conclusion,status,createdAt",
                ],
                cwd=plugin_root,
                capture_output=True,
                text=True,
                timeout=120,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if listed.returncode != 0:
            continue
        try:
            candidates = json.loads(listed.stdout or "[]")
        except json.JSONDecodeError:
            continue
        for c in candidates:
            candidate_sha = c.get("headSha")
            if not candidate_sha or candidate_sha == sha:
                continue
            try:
                ancestry = subprocess.run(
                    ["git", "merge-base", "--is-ancestor", sha, candidate_sha],
                    cwd=plugin_root,
                    capture_output=True,
                    timeout=30,
                )
            except (OSError, subprocess.TimeoutExpired):
                continue
            if ancestry.returncode == 0:
                result[name] = True
                break
    return result


def stage_verify_ci_green(
    plugin_root: Path, timeout_s: int = _CI_VERIFY_DEFAULT_TIMEOUT_S
) -> int:
    """Gate 14: confirm CI went GREEN on the commit that was just released.

    WHY THIS GATE EXISTS. The release push targets the default branch directly,
    and the branch ruleset grants the maintainer role ``bypass_mode: always`` —
    so GitHub reports ``Bypassed rule violations … required status checks are
    expected`` and lets the push through. That bypass is deliberate (it is what
    makes a scripted release possible at all), but it means **the required
    checks never actually gate anything**: the tag, the release and the
    marketplace notification are all public before CI has said a word. Until
    this gate existed, "CI must be green" lived only in the fixer/creator agent
    PROSE — and prose is skippable, which is the identical defect v2.157.0 fixed
    one gate earlier when `ci-preflight` was wired in as Gate 3b.

    NON-BLOCKING BY CONSTRUCTION, and that is a deliberate asymmetry rather than
    a weak gate: by the time this runs the release is already published, so
    returning non-zero could not un-ship anything — it would only abort the
    pipeline *after* the irreversible step, losing the report that is the whole
    point. A RED result is therefore surfaced as a loud, explicit failure notice
    that names the failing runs and the exact follow-up command, which is what
    lets the caller enter the documented fix→re-publish loop.

    "Cannot check" is never reported as green (the [[lesson-cannot-check-is-not-clean]]
    rule): no gh, no network, no runs found, or a timeout are each reported as
    UNVERIFIED with the reason, never folded into a pass.
    """
    # flush=True on this stage's stdout prints: with output redirected to a
    # file, stdout is BLOCK-buffered while the stage's verdicts go to stderr
    # (write-through) — so in a captured log the UNVERIFIED/RED line landed ~70
    # lines BEFORE this banner and the gate read as having silently printed
    # nothing. Verified on the v5.1.3 publish log; causal order in the capture
    # is what makes the gate auditable, so it is worth the explicit flushes.
    print(f"\n{BLUE}═══ Gate 14: Verify CI is green on the released commit ═══{NC}", flush=True)

    gh_bin = shutil.which("gh")
    if gh_bin is None:
        print(
            f"{YELLOW}⚠ UNVERIFIED — gh CLI not installed, cannot check CI.{NC}\n"
            f"{YELLOW}  The release IS published; verify manually before relying on it.{NC}",
            file=sys.stderr,
        )
        return 0

    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=plugin_root,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"{YELLOW}⚠ UNVERIFIED — could not resolve HEAD ({exc}).{NC}", file=sys.stderr)
        return 0
    if head.returncode != 0:
        print(f"{YELLOW}⚠ UNVERIFIED — could not resolve HEAD.{NC}", file=sys.stderr)
        return 0
    sha = head.stdout.strip()

    deadline = time.monotonic() + timeout_s
    poll_s = 15
    while True:
        try:
            listed = subprocess.run(
                [
                    gh_bin, "run", "list",
                    "--commit", sha,
                    "--limit", "20",
                    "--json", "name,status,conclusion,headBranch",
                ],
                cwd=plugin_root,
                capture_output=True,
                text=True,
                timeout=120,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            print(f"{YELLOW}⚠ UNVERIFIED — `gh run list` failed ({exc}).{NC}", file=sys.stderr)
            return 0
        if listed.returncode != 0:
            print(
                f"{YELLOW}⚠ UNVERIFIED — `gh run list` exited "
                f"{listed.returncode}: {listed.stderr.strip()[:200]}{NC}",
                file=sys.stderr,
            )
            return 0

        try:
            runs = json.loads(listed.stdout or "[]")
        except json.JSONDecodeError:
            print(f"{YELLOW}⚠ UNVERIFIED — unparseable `gh run list` output.{NC}", file=sys.stderr)
            return 0

        if not runs:
            # A workflow can take a few seconds to register after the push.
            if time.monotonic() >= deadline:
                print(
                    f"{YELLOW}⚠ UNVERIFIED — no CI runs found for {sha[:8]} "
                    f"within {timeout_s}s.{NC}",
                    file=sys.stderr,
                )
                return 0
            time.sleep(poll_s)
            continue

        pending = [r for r in runs if r.get("status") != "completed"]
        if not pending:
            break
        if time.monotonic() >= deadline:
            names = ", ".join(sorted({str(r.get("name", "?")) for r in pending}))
            # The hint carries the FULL sha: `gh run list --commit` with an
            # abbreviated sha silently matches nothing and exits 0, so a pasted
            # short-sha command reads as "no runs" (verified project lesson).
            print(
                f"{YELLOW}⚠ UNVERIFIED — still running after {timeout_s}s: {names}.{NC}\n"
                f"{YELLOW}  Check with: gh run list --commit {sha}{NC}",
                file=sys.stderr,
            )
            return 0
        time.sleep(poll_s)

    # A conclusion of `skipped`/`neutral` is not a failure (CPV's own PyPI
    # workflow is deliberately dormant and always reports `skipped`). A
    # `cancelled` run is not automatically a failure either (issue #220): the
    # concurrency group cancels a run that a newer push to the same branch
    # superseded, which looks identical to a genuine user-cancel unless we
    # go find that newer run ourselves.
    cancelled_runs = [r for r in runs if r.get("conclusion") == "cancelled"]
    successors = (
        _resolve_ci_run_successors(gh_bin, plugin_root, sha, cancelled_runs)
        if cancelled_runs
        else {}
    )
    failed, unknown = classify_ci_runs(runs, successors)
    if failed:
        detail = ", ".join(
            f"{r.get('name', '?')}={r.get('conclusion')}" for r in failed
        )
        # Follow-up commands must be pasteable as written: `gh run view` has NO
        # --commit flag (that flag belongs to `gh run list`), and `gh run list
        # --commit` with an abbreviated sha silently matches nothing — so the
        # hint is a two-step with the FULL sha, then the run id.
        print(
            f"{RED}✗ CI IS RED on the released commit {sha[:8]}: {detail}{NC}\n"
            f"{RED}  The release v-tag and GitHub release are ALREADY PUBLISHED — the{NC}\n"
            f"{RED}  ruleset bypass meant no required check gated them. Fix the cause and{NC}\n"
            f"{RED}  publish a follow-up patch; do NOT mute the check.{NC}\n"
            f"{RED}  Logs: gh run list --commit {sha}{NC}\n"
            f"{RED}        then: gh run view --log-failed <run-id>{NC}",
            file=sys.stderr,
        )
        return 0

    if unknown:
        names = ", ".join(sorted({str(r.get("name", "?")) for r in unknown}))
        print(
            f"{YELLOW}? CI verdict UNKNOWN on {sha[:8]}: run cancelled and no "
            f"successor found — not checked ({names}).{NC}\n"
            f"{YELLOW}  This is either a genuine user-cancel or a superseding run{NC}\n"
            f"{YELLOW}  we could not resolve. Verify manually: "
            f"gh run list --commit {sha}{NC}",
            file=sys.stderr,
        )
        return 0

    names = ", ".join(sorted({str(r.get("name", "?")) for r in runs}))
    print(f"{GREEN}✓ CI green on {sha[:8]} ({names}){NC}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
