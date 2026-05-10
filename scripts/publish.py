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
import shutil
import subprocess
import sys
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

# Ensure the scripts/ directory is on sys.path so we can import cpv_validation_common
# when publish.py is invoked directly (e.g. `uv run python scripts/publish.py`).
sys.path.insert(0, str(Path(__file__).resolve().parent))

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
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=600, env=env)
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
        try:
            content = py_file.read_text(encoding="utf-8")
            pattern = r'^(__version__\s*=\s*["\'])(\d+\.\d+\.\d+)(["\'])$'
            old_v = None

            def _replace(m: re.Match[str]) -> str:
                nonlocal old_v
                old_v = m.group(2)
                return f"{m.group(1)}{new_version}{m.group(3)}"

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

    # pyproject.toml
    pp = plugin_root / "pyproject.toml"
    if pp.exists():
        try:
            m = re.search(r'^version\s*=\s*["\']([^"\']+)["\']', pp.read_text(encoding="utf-8"), re.MULTILINE)
            if m:
                versions["pyproject.toml"] = m.group(1)
        except (json.JSONDecodeError, OSError) as e:
            print(f"Warning: could not read version from pyproject.toml: {e}")

    # Python __version__ variables
    gi = _get_gi(plugin_root)
    for py_file in gi.rglob("*.py"):
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
    ("Gate 2", "Tests (uv run pytest tests/ -x)"),
    (
        "Gate 3",
        "Plugin validation (validate_plugin.py --strict) — owns repo-wide "
        "lint via cpv_lint_engine since v2.64.0; blocks on "
        "CRITICAL/MAJOR/MINOR/NIT; WARNING advisory only",
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
    ("Gate 9", "Generate CHANGELOG.md + release notes (git-cliff --bump --unreleased --tag)"),
    ("Gate 10", "Commit bump + manifest refresh + changelog"),
    ("Gate 11", "Create annotated git tag vX.Y.Z"),
    ("Gate 12", "Push branch + tag to origin"),
    ("Gate 13", "Create GitHub release with notes (gh release create)"),
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
    """Return True if marketplace.json lists plugin_name with github source pointing at expected_repo.

    If expected_repo is None, accept any github source entry matching plugin_name.
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
        if isinstance(source, dict):
            if source.get("source") != "github" and source.get("type") != "github":
                continue
            repo = source.get("repo")
            if expected_repo is None or repo == expected_repo:
                return True
        elif isinstance(source, str):
            # Bare directory source like "./plugins/foo"
            continue
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

    TRDD-bbff5bc5 §6.1: the canonical names are PLUGIN_SKIP_* / PLUGIN_FORCE_*
    / PLUGIN_BYPASS_*. The CPV_SKIP_* names are kept as legacy aliases for
    one release to ease migration of plugins still on v2.50.x.
    """
    forbidden = [
        # New canonical names (TRDD-bbff5bc5)
        "PLUGIN_SKIP_TESTS",
        "PLUGIN_SKIP_LINT",
        "PLUGIN_SKIP_VALIDATE",
        "PLUGIN_FORCE_PUBLISH",
        "PLUGIN_BYPASS_CHECKS",
        # Legacy aliases — removed in next release.
        "CPV_SKIP_TESTS",
        "CPV_SKIP_LINT",
        "CPV_SKIP_VALIDATE",
        "CPV_FORCE_PUBLISH",
        "CPV_BYPASS_CHECKS",
        # Generic bypass attempts — always rejected.
        "SKIP_TESTS",
        "SKIP_LINT",
        "SKIP_VALIDATE",
        "NO_VERIFY",
    ]
    attempted = [v for v in forbidden if os.environ.get(v)]
    if attempted:
        print(
            f"{RED}✗ Bypass attempt detected. These env vars are FORBIDDEN in publish:{NC}\n"
            f"  {', '.join(attempted)}\n"
            f"{RED}The publish pipeline enforces every check. Fix the failures, don't skip them.{NC}",
            file=sys.stderr,
        )
        return 1
    return 0


def stage_check_working_tree(plugin_root: Path) -> int:
    """Gate 1: clean working tree check. Auto-commits uv.lock if only diff.

    `git status --porcelain` emits one line per change with the format
    ``XY filename`` (2-char status + 1 space + filename). The filename
    starts at column 3, so the canonical slice is ``line[3:]``.

    DO NOT pre-strip per line — for unstaged-only changes git emits
    " M uv.lock" with a leading space (column 0 is empty, column 1 is
    the worktree status). Calling ``.strip()`` would shift the slice
    by one and produce "v.lock" instead of "uv.lock", silently breaking
    the auto-commit branch.
    """
    print(f"\n{BLUE}═══ Gate 1: Check working tree ═══{NC}")
    result = run(["git", "status", "--porcelain"], cwd=plugin_root, check=False)
    dirty_lines = [line for line in result.stdout.splitlines() if line]
    if dirty_lines:
        dirty_files = {line[3:] for line in dirty_lines if len(line) >= 4}
        if dirty_files == {"uv.lock"}:
            print(f"{YELLOW}Auto-committing uv.lock (modified by uv run){NC}")
            run(["git", "add", "uv.lock"], cwd=plugin_root)
            run(["git", "commit", "-m", "chore: update uv.lock"], cwd=plugin_root)
        else:
            print(f"{RED}✗ Uncommitted changes detected. Commit or stash first.{NC}", file=sys.stderr)
            print("\n".join(dirty_lines))
            return 1
    print(f"{GREEN}✓ Working tree clean{NC}")
    return 0


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
    """
    print(f"\n{BLUE}═══ Gate 2: Run tests (mandatory) ═══{NC}")
    result = run(
        ["uv", "run", "pytest", "tests/", "-n", "auto", "--dist=worksteal",
         "--maxfail=1", "-q", "--tb=short"],
        cwd=plugin_root,
        check=False,
    )
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
            f"{YELLOW}  setup-marketplace-auto-notification skill to wire up auto-updates.{NC}\n"
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
            f"{RED}  Fix: run the setup-marketplace-auto-notification skill to generate it.{NC}",
            file=sys.stderr,
        )
        return 1

    # 2. Workflow must reference a real marketplace
    if not mkt_owner or not mkt_repo:
        print(
            f"{RED}✗ notify-marketplace.yml does not define MARKETPLACE_OWNER/MARKETPLACE_REPO.{NC}\n"
            f"{RED}  Fix: edit .github/workflows/notify-marketplace.yml or re-run{NC}\n"
            f"{RED}  the setup-marketplace-auto-notification skill.{NC}",
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
            f"{RED}  Then re-run publish. See skill: setup-marketplace-auto-notification.{NC}",
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
        print(f"  (Phase E: reused prefetched marketplace.json)")

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
            f"{RED}  See skill: setup-marketplace-auto-notification{NC}",
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
            f"{RED}  See skill: setup-marketplace-auto-notification{NC}",
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
            f"{RED}  Reference: skills/create-plugin/references/marketplace-layouts.md{NC}",
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
            f"{RED}  Reference: skills/create-plugin/references/marketplace-layouts.md{NC}",
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
            f"{RED}  Reference: skills/create-plugin/references/marketplace-layouts.md{NC}",
            file=sys.stderr,
        )
        return 1
    entries = mp_data.get("plugins") if isinstance(mp_data, dict) else None
    if not isinstance(entries, list):
        print(
            f"{RED}✗ marketplace.json has no 'plugins' array.{NC}\n"
            f"{RED}  Reference: skills/create-plugin/references/marketplace-layouts.md{NC}",
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
            f"{RED}  Reference: skills/create-plugin/references/marketplace-layouts.md{NC}",
            file=sys.stderr,
        )
        return 1
    print(f"  {GREEN}✓ Plugin '{plugin_name}' registered in parent marketplace.json{NC}")
    print(f"{GREEN}✓ Layout B marketplace registration verified{NC}")
    return 0


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

    The gate is CPV-specific. Other plugins generated by plugin-creator
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
    # Use the pattern recommended by the git-cliff docs for release pipelines:
    #   git cliff --bump --unreleased --tag <NEXT> -o CHANGELOG.md
    # --bump          tells git-cliff to treat this as a release bump (so the
    #                 unreleased section is promoted to a dated tag entry)
    # --unreleased    process only commits since the last tag
    # --tag <NEXT>    label the new entry with our computed version
    # -o CHANGELOG.md write the full regenerated changelog back to disk
    run(
        [cliff_bin, "--bump", "--unreleased", "--tag", tag_name, "-o", "CHANGELOG.md"],
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


def _remote_tag_exists(plugin_root: Path, tag_name: str) -> bool:
    """True when ``tag_name`` exists on origin (per ``git ls-remote``).

    Used by Gate 10's recovery path to confirm a half-published release
    can be safely consolidated into one commit. If the tag is already on
    remote, the prior commit is the source-of-truth for that release and
    we MUST NOT undo it.

    Network failure → returns False conservatively. The caller's recovery
    branch is only entered when HEAD already has the chore(release) commit
    AND the tree is dirty; in the failure-to-check case, we'd just skip
    the recovery and create a second chore commit (same as the pre-fix
    behaviour, no regression).
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
        return False
    return result.returncode == 0 and bool(result.stdout.strip())


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
            lines.append(
                f"{YELLOW}    Run: cd {sub_path} && git push origin HEAD{NC}"
            )
        lines.append("")
        lines.append(f"{YELLOW}  Then re-run publish.{NC}")
        print("\n".join(lines), file=sys.stderr)
        sys.exit(1)


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
    if porcelain_clean and head_subject == expected_subject:
        print(
            f"{YELLOW}  Working tree clean and HEAD already has '{expected_subject}' — "
            f"skipping commit (interrupted-publish recovery).{NC}"
        )
        print(f"{GREEN}✓ Already committed {tag_name}{NC}")
    elif (not porcelain_clean) and head_subject == expected_subject and not _remote_tag_exists(plugin_root, tag_name):
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
        # - The commit being undone is local-only (`_remote_tag_exists`
        #   confirmed it).
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
        run(["git", "add", "-A"], cwd=plugin_root)
        run(["git", "commit", "-m", expected_subject], cwd=plugin_root)
        print(f"{GREEN}✓ Re-committed {tag_name} with manifest refresh folded in{NC}")
    else:
        run(["git", "add", "-A"], cwd=plugin_root)
        run(["git", "commit", "-m", expected_subject], cwd=plugin_root)
        print(f"{GREEN}✓ Committed {tag_name}{NC}")
    print(f"\n{BLUE}═══ Gate 11: Create git tag {tag_name} ═══{NC}")
    if _local_tag_exists(plugin_root, tag_name):
        # Validate that the existing tag points at HEAD. If it points at an
        # older commit AND the tag is unpushed, the prior run's recovery
        # branch above should already have handled it; this check catches
        # any remaining drift cases (manual tag created out-of-band, etc.).
        tag_sha = run(
            ["git", "rev-list", "-n", "1", tag_name], cwd=plugin_root, check=False
        ).stdout.strip()
        head_sha = run(["git", "rev-parse", "HEAD"], cwd=plugin_root, check=False).stdout.strip()
        if tag_sha and head_sha and tag_sha != head_sha and not _remote_tag_exists(plugin_root, tag_name):
            print(
                f"{YELLOW}  Local tag {tag_name} points at {tag_sha[:8]}, HEAD is at "
                f"{head_sha[:8]}. Tag is unpushed; moving it to HEAD.{NC}"
            )
            run(["git", "tag", "-d", tag_name], cwd=plugin_root)
            run(["git", "tag", "-a", tag_name, "-m", f"Release {tag_name}"], cwd=plugin_root)
            print(f"{GREEN}✓ Tag {tag_name} re-created at HEAD{NC}")
        else:
            print(f"{YELLOW}  Tag {tag_name} already exists locally — skipping (interrupted-publish recovery).{NC}")
            print(f"{GREEN}✓ Tag {tag_name} already present{NC}")
    else:
        run(["git", "tag", "-a", tag_name, "-m", f"Release {tag_name}"], cwd=plugin_root)
        print(f"{GREEN}✓ Tag {tag_name} created{NC}")
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
    if (
        prefetch is not None
        and prefetch.gh_auth is not None
        and prefetch.gh_auth_target == (owner, repo)
    ):
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
        print(f"  (Phase E: reused prefetched gh-auth check)")
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
    # Phase 1 (Sprint 3): both pushes wrapped in retry so a transient
    # github.com hiccup doesn't leave a half-published release (commit
    # local-only, tag local-only, or branch pushed without tag).
    # `git push` doesn't load _plugin_verify_hashes, so no integrity
    # bypass needed here.
    print("  $ git push origin HEAD")
    git_with_retry(
        ["git", "push", "origin", "HEAD"],
        cwd=plugin_root,
        env=os.environ.copy(),
        capture_output=False,
    )
    print(f"  $ git push origin {tag_name}")
    git_with_retry(
        ["git", "push", "origin", tag_name],
        cwd=plugin_root,
        env=os.environ.copy(),
        capture_output=False,
    )
    print(f"{GREEN}✓ Pushed branch and tag {tag_name}{NC}")
    return 0


def stage_github_release(plugin_root: Path, tag_name: str, release_notes_file: Path) -> int:
    """Gate 13: create GitHub release with notes. Warns (not errors) if gh missing.

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
    else:
        print(
            f"{YELLOW}⚠ gh release failed (tag is already pushed — you can create release manually){NC}",
            file=sys.stderr,
        )
    return 0


# ── Phase C (v2.77.0) parallel preflight orchestrator ───────────────────────


# Canonical replay order for the parallel preflight block. The terminal sees
# Gate 2 → 3 → 4 → 5 in this exact order regardless of which thread finishes
# first, so logs stay diff-friendly across runs and are easy to skim.
_PARALLEL_GATE_ORDER: tuple[str, ...] = ("tests", "validate", "mkpl_validate", "mkpl_reg")


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
        the worker threads continue running in the background until their
        tasks complete. Daemon=False threads would block process exit; we
        use ``wait=False`` to keep main()'s control flow snappy on early
        failure paths, then rely on the workers being daemon=True (set
        below in ``start_prefetch()``) so they die with the process.
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
    prefetch tasks run on a ThreadPoolExecutor with daemon worker threads
    so they don't block process exit on early failures. Total preflight
    workers ≈ 6 (4 from Phase C parallel preflight + 2 from this prefetch).

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
        results.marketplace_json = executor.submit(
            _prefetch_marketplace_json_safe, mkt_owner, mkt_repo
        )
        results.marketplace_target = mkt_target

    return results


def run_preflight_parallel(
    plugin_root: Path,
    layout: str,
    *,
    prefetch: _PrefetchResults | None = None,
) -> int:
    """Run Gates 2/3/4/5 concurrently with per-thread output capture.

    Phase C (v2.77.0): replaces the previous sequential Gate-2 → Gate-3 →
    Gate-4 → Gate-5 dispatch. The four gates are independent (none mutates
    on-disk state that another reads), so running them on a 4-worker thread
    pool drops preflight wall time from ``sum(gates)`` to ``max(gates)``
    — typically a 25–60s reduction depending on test-suite size.

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

    * The pool waits for ALL four gates to finish (so we don't leave
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
    print(f"\n{BLUE}═══ Running Gates 2-5 concurrently (Phase C parallel preflight) ═══{NC}")

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
        "mkpl_validate": lambda: stage_validate_marketplace(plugin_root, layout),
        "mkpl_reg": lambda: stage_marketplace_registration_check(plugin_root, prefetch=pf),
    }

    # Submit all four gates simultaneously. max_workers=4 is the exact
    # number of tasks — no point sizing the pool larger.
    captured: dict[str, tuple[int, str, str]] = {}
    with ThreadPoolExecutor(max_workers=4) as ex:
        futures = {
            name: ex.submit(_run_stage_captured, fn)
            for name, fn in stage_callables.items()
        }
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
        description="Publish pipeline: 14-gate fail-fast release with auto-bump (bypass-proof)",
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
        """,
    )
    bump_group = parser.add_mutually_exclusive_group()
    bump_group.add_argument("--major", action="store_true", help="Force a major bump (override auto-detection)")
    bump_group.add_argument("--minor", action="store_true", help="Force a minor bump (override auto-detection)")
    bump_group.add_argument("--patch", action="store_true", help="Force a patch bump (override auto-detection)")
    parser.add_argument("--dry-run", action="store_true", help="Preview without making changes")
    parser.add_argument("--print-gates", action="store_true", help="Print gate list and exit")
    args = parser.parse_args()

    if args.print_gates:
        print_gates()
        return 0

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
    # marketplace → consistency. Since v2.64.0, validate_plugin.py owns
    # repo-wide lint via cpv_lint_engine, so there is no separate lint
    # stage — the validator catches lint errors AND structural issues in a
    # single pass with one source of truth.
    #
    # Phase C (v2.77.0): Gate 1 still runs first sequentially — the clean
    # working tree must be verified before tests run, otherwise pytest
    # could pick up uncommitted state (or auto-commit the lockfile via
    # the in-place `git add uv.lock`/`git commit` branch). After Gate 1
    # passes, Gates 2/3/4/5 run concurrently in a thread pool; their
    # output is captured per-thread and replayed in canonical order so
    # the terminal stays readable. Gate 6 runs sequentially after the
    # parallel block to keep version-consistency strictly downstream of
    # the validators (avoids racing against any in-flight validator
    # subprocess that may touch on-disk state).
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

        # Phase E (v2.79.0): pass the prefetch results into Gate 12 so the
        # synchronous _ensure_gh_auth call can be skipped when the
        # background prefetch already completed cleanly.
        rc = stage_commit_tag_push(root, tag_name, prefetch=prefetch)
        if rc != 0:
            return rc

        rc = stage_github_release(root, tag_name, release_notes_file)
        if rc != 0:
            return rc

        print(f"\n{GREEN}✓ Published v{new_version}{NC}")
        return 0
    finally:
        # Phase E: release the prefetch executor's worker threads on every
        # exit path (success, early failure, exception). The workers are
        # stdlib non-daemon threads, so without shutdown() the process
        # would stall on exit waiting for them — even on a Gate 6 failure
        # that aborted long before Gate 12 consumed the prefetch.
        prefetch.shutdown()


if __name__ == "__main__":
    sys.exit(main())
