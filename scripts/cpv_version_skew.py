#!/usr/bin/env python3
"""Read-only version-skew checker (GitHub issue #212).

WHAT IT DOES
============
Compares the version of an INSTALLED plugin (its
``.claude-plugin/plugin.json``) against the version pinned for that same
plugin in a marketplace's ``marketplace.json`` (matched by plugin name).
Reports whether the installed copy is in sync, or how far behind it is
(major / minor / patch), plus a best-effort "breaking change likely" hint
mined from the marketplace repo's CHANGELOG.md.

READ-ONLY — it never writes, upgrades, or installs anything.

TARGETS FOR <marketplace-ref>
==============================
* Local path to a ``marketplace.json`` file
* Local path to a plugin dir / marketplace dir (containing
  ``.claude-plugin/marketplace.json`` or ``marketplace.json``)
* ``github:owner/repo`` or a GitHub/GitLab URL (fetched via the same
  fetcher ``cpv_pre_install_scan.py`` uses — no second fetcher)

EXIT CODES
==========
0 — in sync
1 — major version skew
2 — minor/patch version skew
3 — could not run (missing file, plugin not found in marketplace,
    unparsable version, network/fetch failure)
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path
from typing import Any

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

# Reuse the ONE existing GitHub/local/archive fetcher — cpv_pre_install_scan.py
# already solves "local path | GitHub URL | owner/repo | archive" safely
# (sandboxed, depth-1 clone, path-traversal-checked). Writing a second
# fetcher here would drift from it (never-a-second-copy rule).
from cpv_pre_install_scan import _fetch_target, _is_url  # noqa: E402

_SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


def _strict_semver(raw: str) -> tuple[int, int, int] | None:
    """Parse a strict MAJOR.MINOR.PATCH string. No `packaging` dependency."""
    m = _SEMVER_RE.match(raw.strip())
    if not m:
        return None
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)))


def _load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"{path} does not contain a JSON object")
    return data


def _read_installed_plugin(plugin_dir: Path) -> tuple[str, str]:
    """Return (name, version) from an installed plugin's manifest."""
    manifest = plugin_dir / ".claude-plugin" / "plugin.json"
    if not manifest.is_file():
        raise FileNotFoundError(f"no .claude-plugin/plugin.json under {plugin_dir}")
    data = _load_json(manifest)
    name = data.get("name")
    version = data.get("version")
    if not isinstance(name, str) or not name:
        raise ValueError(f"{manifest} has no valid 'name'")
    if not isinstance(version, str) or not version:
        raise ValueError(f"{manifest} has no valid 'version'")
    return name, version


def _locate_marketplace_json(root: Path) -> Path:
    for candidate in (root / ".claude-plugin" / "marketplace.json", root / "marketplace.json"):
        if candidate.is_file():
            return candidate
    if root.is_file() and root.name.lower() == "marketplace.json":
        return root
    raise FileNotFoundError(f"no marketplace.json found under {root}")


def _resolve_marketplace_ref(ref: str, sandbox: Path) -> Path:
    """Fetch/locate <marketplace-ref> and return the path to marketplace.json.

    A bare local path (no "github:" prefix, not a URL) is resolved against the
    filesystem FIRST and, if missing, fails as "no such path" rather than being
    handed to _fetch_target — which would classify it via _is_owner_repo's
    regex. That regex allows dots (it must, to match real "owner/repo" slugs),
    so a relative path like ".claude-plugin/marketplace.json" ALSO matches the
    "owner/repo" shape and was being sent to `git clone
    https://github.com/.claude-plugin/marketplace.json.git` — a doomed remote
    fetch for what was actually just a missing local file (bug found by the
    coordinator running the tool from a cwd where the file didn't exist).
    """
    if ref.startswith("github:"):
        spec = ref[len("github:") :]
    else:
        spec = ref
        if not _is_url(spec):
            candidate = Path(spec).expanduser()
            if candidate.exists():
                spec = str(candidate)
            else:
                raise FileNotFoundError(f"no such path: {spec}")
    root, _label = _fetch_target(spec, sandbox)
    return _locate_marketplace_json(root)


def _find_marketplace_entry(marketplace: dict[str, Any], plugin_name: str) -> dict[str, Any] | None:
    plugins = marketplace.get("plugins")
    if not isinstance(plugins, list):
        return None
    for entry in plugins:
        if isinstance(entry, dict) and entry.get("name") == plugin_name:
            return entry
    return None


def _classify_skew(installed: tuple[int, int, int], marketplace: tuple[int, int, int]) -> str:
    if installed == marketplace:
        return "none"
    if installed[0] != marketplace[0]:
        return "major"
    if installed[1] != marketplace[1]:
        return "minor"
    return "patch"


_BREAKING_RE = re.compile(r"\bBREAKING\b", re.IGNORECASE)


def _breaking_hint(marketplace_json_path: Path, installed_ver: str, marketplace_ver: str) -> bool:
    """Best-effort: does the marketplace repo's CHANGELOG.md mention BREAKING
    anywhere between the two versions? False (never an error) when no
    changelog is reachable — this is advisory only.
    """
    root = marketplace_json_path.parent
    if root.name == ".claude-plugin":
        root = root.parent
    changelog = root / "CHANGELOG.md"
    if not changelog.is_file():
        return False
    try:
        text = changelog.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    # Scope to the section between the two version headings when both are
    # findable; otherwise scan the whole file (conservative — more likely
    # to surface a true hint than to miss one, and never blocks anything).
    start = text.find(installed_ver)
    end = text.find(marketplace_ver)
    if start != -1 and end != -1:
        lo, hi = sorted((start, end))
        segment = text[lo:hi]
    else:
        segment = text
    return bool(_BREAKING_RE.search(segment))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    parser.add_argument("installed_plugin_dir", help="Path to the installed plugin directory.")
    parser.add_argument(
        "marketplace_ref",
        help="Local path to marketplace.json (or a dir containing it), or github:owner/repo / a GitHub URL.",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON on stdout instead of human-readable text.")
    args = parser.parse_args(argv)

    def fail(reason: str) -> int:
        if args.json:
            print(json.dumps({"reason": reason}, indent=2))
        else:
            print(f"Error: {reason}", file=sys.stderr)
        return 3

    plugin_dir = Path(args.installed_plugin_dir)
    if not plugin_dir.is_dir():
        return fail(f"not a directory: {plugin_dir}")

    try:
        plugin_name, installed_ver = _read_installed_plugin(plugin_dir)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return fail(str(exc))

    installed_semver = _strict_semver(installed_ver)
    if installed_semver is None:
        return fail(f"installed version {installed_ver!r} is not a valid MAJOR.MINOR.PATCH semver")

    sandbox = Path(tempfile.mkdtemp(prefix="cpv-version-skew-"))
    try:
        try:
            marketplace_json_path = _resolve_marketplace_ref(args.marketplace_ref, sandbox)
            marketplace = _load_json(marketplace_json_path)
        except (
            OSError,
            ValueError,
            RuntimeError,
            json.JSONDecodeError,
            tarfile.TarError,
            zipfile.BadZipFile,
        ) as exc:
            return fail(f"could not resolve marketplace ref: {exc}")

        entry = _find_marketplace_entry(marketplace, plugin_name)
        if entry is None:
            return fail(f"plugin {plugin_name!r} not found in marketplace")

        marketplace_ver = entry.get("version")
        if not isinstance(marketplace_ver, str) or not marketplace_ver:
            return fail(f"marketplace entry for {plugin_name!r} has no pinned 'version'")

        marketplace_semver = _strict_semver(marketplace_ver)
        if marketplace_semver is None:
            return fail(f"marketplace version {marketplace_ver!r} is not a valid MAJOR.MINOR.PATCH semver")

        skew = _classify_skew(installed_semver, marketplace_semver)
        breaking_hint = _breaking_hint(marketplace_json_path, installed_ver, marketplace_ver) if skew != "none" else False

        result = {
            "plugin": plugin_name,
            "installed": installed_ver,
            "marketplace": marketplace_ver,
            "skew": skew,
            "breaking_hint": breaking_hint,
            "reason": None,
        }

        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(f"Plugin:      {plugin_name}")
            print(f"Installed:   {installed_ver}")
            print(f"Marketplace: {marketplace_ver}")
            print(f"Skew:        {skew}")
            if skew != "none":
                print(f"Breaking hint: {'yes' if breaking_hint else 'no (or no changelog found)'}")

        if skew == "none":
            return 0
        if skew == "major":
            return 1
        return 2
    finally:
        shutil.rmtree(sandbox, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
