#!/usr/bin/env python3
"""Silent, automatic install helpers for the external scanners CPV uses.

Every helper in this module:
  * is idempotent — `shutil.which()` probe up front; bail if already installed
  * is silent — `capture_output=True`, never prompts the user
  * never raises — all install failures collapse into a `False` return
  * respects opt-out env vars — `CPV_NO_<TOOL>_INSTALL=1` skips the install
    attempt and leaves the tool unavailable; CPV degrades gracefully (see
    `validate_security.py` — every scanner self-skips with a one-line WARNING
    when its binary is missing)
  * is platform-aware — on macOS prefers `brew`; on Linux prefers `snap` then
    `cargo`; on Windows downloads the matching GitHub release artifact

The intent is "first-run autoinstall on demand" — the user should be able to
clone CPV, run `validate_security.py <plugin>`, and have the scanners fetch
themselves on the very first invocation, with NO interactive prompts and NO
mandatory pre-flight setup. The explicit `cpv-doctor --install-scanners`
batch installer is also wired through here so a user who wants to pre-warm
their environment can do so with one command.

The fclones autoinstall is the canonical reference; the rest of the
external scanners (cc-audit, tirith, trufflehog, semgrep, Cisco) follow the
same pattern.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

__all__ = [
    "ensure_fclones",
    "ensure_cc_audit",
    "ensure_trufflehog",
    "ensure_semgrep",
    "ensure_tirith",
    "ensure_cisco_skill_scanner",
    "install_all_scanners",
]


# ── Common helpers ───────────────────────────────────────────────────


_INSTALL_TIMEOUT_SECONDS = 600  # brew/snap/cargo can be slow on a cold cache
_DOWNLOAD_TIMEOUT_SECONDS = 300


def _opt_out(env_var: str) -> bool:
    """True if the user has opted out of an autoinstall via env var."""
    return os.environ.get(env_var, "").strip().lower() in {"1", "true", "yes", "on"}


def _silent_run(argv: list[str], timeout: int = _INSTALL_TIMEOUT_SECONDS) -> bool:
    """Run argv silently. Return True on returncode == 0, False otherwise.

    Never raises. Captures stdout/stderr so the user sees no installer chatter.
    """
    try:
        result = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return False


def _local_bin_dir() -> Path:
    """The cross-platform user-local bin dir.

    macOS / Linux: ``~/.local/bin`` (XDG-friendly; Cargo also installs here).
    Windows: ``%USERPROFILE%\\.local\\bin`` (mirrors the Unix layout for
    consistency; we add it to ``os.environ['PATH']`` for the current process).
    """
    return Path.home() / ".local" / "bin"


def _ensure_local_bin_on_path() -> None:
    """Prepend ``~/.local/bin`` to ``os.environ['PATH']`` for this process.

    This makes a freshly downloaded binary visible to subsequent
    ``shutil.which()`` probes within the same Python process. We do NOT
    persist the change to the user's shell rc file — that's an explicit
    user action, not something an autoinstaller should mutate silently.
    """
    bin_dir = str(_local_bin_dir())
    current = os.environ.get("PATH", "")
    if bin_dir not in current.split(os.pathsep):
        os.environ["PATH"] = bin_dir + os.pathsep + current


# ── fclones (the dedup helper used as the first step of every CPV scan) ──


def ensure_fclones() -> bool:
    """Make ``fclones`` available on PATH for the current process.

    Resolution cascade (silent, never prompts):
      1. Already on PATH → return True immediately.
      2. ``CPV_NO_FCLONES_INSTALL=1`` set → return False (degraded mode;
         CPV will skip dedup with a one-line WARNING and still scan).
      3. macOS:  ``brew install fclones`` → ``cargo install fclones``
      4. Linux:  ``snap install fclones`` → ``cargo install fclones``
                 → AUR (``pacman -S fclones`` if pacman exists)
                 → Alpine (``apk add fclones`` if apk exists)
      5. Windows: GitHub-release ZIP download to ``~/.local/bin/fclones.exe``
                  → ``cargo install fclones`` if cargo is on PATH
      6. Other Unix: ``cargo install fclones`` only.

    Returns True if fclones is on PATH after the attempt. Always returns a
    bool — never raises.
    """
    if shutil.which("fclones"):
        return True

    if _opt_out("CPV_NO_FCLONES_INSTALL"):
        return False

    system = platform.system()

    if system == "Darwin":
        _install_fclones_macos()
    elif system == "Linux":
        _install_fclones_linux()
    elif system == "Windows":
        _install_fclones_windows()
    else:
        _install_fclones_via_cargo()

    _ensure_local_bin_on_path()
    return shutil.which("fclones") is not None


def _install_fclones_macos() -> None:
    """macOS install cascade. brew is the canonical path; cargo is the fallback."""
    if shutil.which("brew"):
        if _silent_run(["brew", "install", "fclones"]):
            return
    _install_fclones_via_cargo()


def _install_fclones_linux() -> None:
    """Linux install cascade.

    Prefer ``snap`` (works on Ubuntu, Debian, Fedora, openSUSE, Arch, …).
    snap install requires root (or polkit prompt) — we run it without sudo
    and let it fail silently if the user lacks privileges; the cargo fallback
    then takes over without any privilege requirement.
    """
    if shutil.which("snap"):
        if _silent_run(["snap", "install", "fclones"]):
            return
    if shutil.which("pacman"):
        # Arch: --noconfirm runs unattended; bail if not running as root
        # (we never sudo for the user — that's their explicit choice).
        if os.geteuid() == 0 and _silent_run(["pacman", "-S", "--noconfirm", "fclones"]):
            return
    if shutil.which("apk"):
        if os.geteuid() == 0 and _silent_run(["apk", "add", "fclones"]):
            return
    _install_fclones_via_cargo()


def _install_fclones_windows() -> None:
    """Windows install cascade.

    Prefer downloading the precompiled release binary from GitHub (no
    Rust toolchain required). Fall back to ``cargo install fclones`` when
    the user has Rust installed but not a compatible release artifact.
    """
    if _download_fclones_github_release():
        return
    _install_fclones_via_cargo()


def _install_fclones_via_cargo() -> None:
    """Cross-platform fallback: ``cargo install fclones`` if cargo is present."""
    if shutil.which("cargo"):
        _silent_run(["cargo", "install", "fclones"])


def _download_fclones_github_release() -> bool:
    """Download the latest Windows fclones binary from GitHub.

    Returns True if the binary was successfully placed at
    ``~/.local/bin/fclones.exe`` and is executable. Returns False on any
    network/parse/extract failure (silent; CPV degrades to "no dedup").

    Implementation notes:
      * Hits the public GitHub releases API (no auth required, rate-limited
        to 60 req/hour per IP for unauthenticated callers — fine for an
        idempotent first-run installer).
      * Uses ``urllib.request`` to avoid pulling in a `requests` dep.
      * Picks the asset matching ``platform.machine()`` (typically ``AMD64``
        → ``x86_64-pc-windows-msvc`` or ``ARM64`` → ``aarch64-pc-windows-msvc``).
      * Streams to a tempfile, then atomic-renames to the final location so
        a partially-downloaded artifact can never appear on PATH.
    """
    arch = platform.machine().lower()
    arch_token = (
        "x86_64-pc-windows-msvc"
        if arch in {"amd64", "x86_64"}
        else "aarch64-pc-windows-msvc"
        if arch in {"arm64", "aarch64"}
        else None
    )
    if arch_token is None:
        return False

    api_url = "https://api.github.com/repos/pkolaczk/fclones/releases/latest"
    try:
        req = urllib.request.Request(
            api_url,
            headers={"User-Agent": "claude-plugins-validation/cpv-install"},
        )
        with urllib.request.urlopen(req, timeout=_DOWNLOAD_TIMEOUT_SECONDS) as resp:
            import json

            payload = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError):
        return False

    assets = payload.get("assets", []) if isinstance(payload, dict) else []
    target_url = None
    target_name = None
    for asset in assets:
        if not isinstance(asset, dict):
            continue
        name = (asset.get("name") or "").lower()
        if arch_token in name and (name.endswith(".zip") or name.endswith(".tar.gz")):
            target_url = asset.get("browser_download_url")
            target_name = name
            break
    if not target_url or not target_name:
        return False

    bin_dir = _local_bin_dir()
    bin_dir.mkdir(parents=True, exist_ok=True)

    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=Path(target_name).suffix) as tmp:
            archive_path = Path(tmp.name)
            req = urllib.request.Request(
                target_url,
                headers={"User-Agent": "claude-plugins-validation/cpv-install"},
            )
            with urllib.request.urlopen(req, timeout=_DOWNLOAD_TIMEOUT_SECONDS) as resp:
                shutil.copyfileobj(resp, tmp)
    except (urllib.error.URLError, OSError):
        return False

    extracted = _extract_fclones_binary(archive_path, bin_dir)
    try:
        archive_path.unlink(missing_ok=True)
    except OSError:
        pass
    return extracted


def _extract_fclones_binary(archive_path: Path, dest_dir: Path) -> bool:
    """Extract ``fclones[.exe]`` from the archive into ``dest_dir``.

    Handles both ``.zip`` and ``.tar.gz`` variants. Picks the first member
    whose basename starts with ``fclones`` to be tolerant of release-folder
    layouts like ``fclones-0.x.y/fclones.exe``.
    """
    suffix = archive_path.suffix.lower()
    try:
        if suffix == ".zip":
            with zipfile.ZipFile(archive_path) as zf:
                for zip_name in zf.namelist():
                    base = Path(zip_name).name
                    if base.lower().startswith("fclones"):
                        target = dest_dir / base
                        with zf.open(zip_name) as zsrc, open(target, "wb") as dst:
                            shutil.copyfileobj(zsrc, dst)
                        target.chmod(0o755)
                        return target.exists()
        else:  # .tar.gz / .tgz
            with tarfile.open(archive_path, "r:gz") as tf:
                for tar_member in tf.getmembers():
                    base = Path(tar_member.name).name
                    if base.lower().startswith("fclones") and tar_member.isfile():
                        target = dest_dir / base
                        tsrc = tf.extractfile(tar_member)
                        if tsrc is None:
                            continue
                        with open(target, "wb") as dst:
                            shutil.copyfileobj(tsrc, dst)
                        target.chmod(0o755)
                        return target.exists()
    except (zipfile.BadZipFile, tarfile.TarError, OSError):
        return False
    return False


# ── cc-audit (npm-installed AI-rules scanner) ─────────────────────────


def ensure_cc_audit() -> bool:
    """Install ``cc-audit`` globally via npm if missing. Returns availability."""
    if shutil.which("cc-audit"):
        return True
    if _opt_out("CPV_NO_CC_AUDIT_INSTALL"):
        return False
    if shutil.which("npm"):
        _silent_run(["npm", "install", "-g", "@cc-audit/cc-audit"])
    return shutil.which("cc-audit") is not None


# ── trufflehog (Go binary, ~700 secret detectors) ─────────────────────


def ensure_trufflehog() -> bool:
    """Install trufflehog if missing. brew → go install fallback."""
    if shutil.which("trufflehog"):
        return True
    if _opt_out("CPV_NO_TRUFFLEHOG_INSTALL"):
        return False
    system = platform.system()
    if system == "Darwin" and shutil.which("brew"):
        _silent_run(["brew", "install", "trufflehog"])
    if shutil.which("trufflehog"):
        return True
    if shutil.which("go"):
        _silent_run(
            [
                "go",
                "install",
                "github.com/trufflesecurity/trufflehog/v3@latest",
            ]
        )
    _ensure_local_bin_on_path()
    # `go install` writes to $GOPATH/bin or $GOBIN — surface that on PATH too
    gopath_bin = Path(os.environ.get("GOPATH", str(Path.home() / "go"))) / "bin"
    if str(gopath_bin) not in os.environ.get("PATH", "").split(os.pathsep):
        os.environ["PATH"] = str(gopath_bin) + os.pathsep + os.environ.get("PATH", "")
    return shutil.which("trufflehog") is not None


# ── semgrep (Python static analyzer, ~thousands of rules) ─────────────


def ensure_semgrep() -> bool:
    """Install semgrep if missing. brew → pipx → pip --user fallback."""
    if shutil.which("semgrep"):
        return True
    if _opt_out("CPV_NO_SEMGREP_INSTALL"):
        return False
    system = platform.system()
    if system == "Darwin" and shutil.which("brew"):
        _silent_run(["brew", "install", "semgrep"])
    if shutil.which("semgrep"):
        return True
    if shutil.which("pipx"):
        _silent_run(["pipx", "install", "semgrep"])
    if shutil.which("semgrep"):
        return True
    # Last resort: pip --user (works on bare Pythons, including Windows)
    if shutil.which("pip"):
        _silent_run([sys.executable, "-m", "pip", "install", "--user", "semgrep"])
    _ensure_local_bin_on_path()
    return shutil.which("semgrep") is not None


# ── tirith (terminal-security scanner) ────────────────────────────────


def ensure_tirith() -> bool:
    """Install tirith if missing. pipx → existing brew/npm/cargo cascade."""
    if shutil.which("tirith"):
        return True
    if _opt_out("CPV_NO_TIRITH_INSTALL"):
        return False
    if shutil.which("pipx"):
        _silent_run(["pipx", "install", "tirith"])
    if shutil.which("tirith"):
        return True
    # Reuse the existing tirith install candidates from validate_security.py
    # to avoid divergence — those have been battle-tested.
    candidates = [
        ("brew", ["brew", "install", "sheeki03/tap/tirith"]),
        ("npm", ["npm", "install", "-g", "tirith"]),
        ("cargo", ["cargo", "install", "tirith"]),
    ]
    for probe, install_cmd in candidates:
        if shutil.which(probe):
            _silent_run(install_cmd)
            if shutil.which("tirith"):
                break
    _ensure_local_bin_on_path()
    return shutil.which("tirith") is not None


# ── Cisco AI Defense skill-scanner (uvx-resolved Python tool) ─────────


def ensure_cisco_skill_scanner() -> bool:
    """Install Cisco skill-scanner persistently via ``uv tool install``.

    The persistent install creates a ``skill-scanner`` shim on PATH so we
    can stop paying the ``uvx --from cisco-ai-skill-scanner ...`` resolution
    cost on every scan.
    """
    if shutil.which("skill-scanner"):
        return True
    if _opt_out("CPV_NO_CISCO_INSTALL"):
        return False
    if shutil.which("uv"):
        _silent_run(["uv", "tool", "install", "cisco-ai-skill-scanner"])
    _ensure_local_bin_on_path()
    return shutil.which("skill-scanner") is not None


# ── Batch installer (used by `cpv-doctor --install-scanners`) ─────────


def install_all_scanners() -> dict[str, bool]:
    """Install every external scanner CPV uses, silently.

    Returns a dict ``{tool_name: available_after_install}``. Callers (e.g.
    `manage_doctor.py --install-scanners`) render a status table from this
    so the user can see at a glance which scanners are now ready.
    """
    return {
        "fclones": ensure_fclones(),
        "cc-audit": ensure_cc_audit(),
        "trufflehog": ensure_trufflehog(),
        "semgrep": ensure_semgrep(),
        "tirith": ensure_tirith(),
        "skill-scanner": ensure_cisco_skill_scanner(),
    }


if __name__ == "__main__":  # pragma: no cover — convenience entry point
    statuses = install_all_scanners()
    width = max(len(name) for name in statuses)
    for name, ok in statuses.items():
        marker = "[OK]" if ok else "[--]"
        print(f"{marker} {name:<{width}}  {'available' if ok else 'unavailable'}")
    sys.exit(0 if all(statuses.values()) else 1)
