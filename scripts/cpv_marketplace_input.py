#!/usr/bin/env python3
"""Universal batch-skill input resolver (TRDD-3dcbb37c §1).

Every ``cpv-batch-*`` skill family member accepts the same input
grammar:

* Single file               ``/path/to/foo.py``
* Single skill              ``/path/to/skills/<name>`` or its SKILL.md
* Single plugin (local)     ``/path/to/plugin-root``
* Single plugin (url)       ``https://github.com/owner/repo`` or ``owner/repo``
* Marketplace (local)       ``/path/to/marketplace-root``
* Marketplace (url)         ``https://github.com/owner/marketplace-repo`` or ``owner/repo``
* List of any of the above  ``--list a b c`` OR ``@/path/to/list.txt``

This module classifies the input deterministically and, for URL
inputs, clones the remote into ``${TMPDIR}/cpv-batch-input-<uuid>/``.
For marketplace inputs it enumerates ``.claude-plugin/marketplace.json``
and clones EVERY referenced plugin (URL marketplace) or maps the
local plugin paths (local marketplace).

The returned list of ``ResolvedInput`` carries a per-input
``cleanup_callback`` that the orchestrator calls in a ``finally:``
block after the batch completes. Cleanup is reference-counted across
plugins originating from the same marketplace clone.

Iron rule: ambiguity is a CRITICAL error. The resolver raises
``InputResolutionError`` with a clear remediation hint rather than
guessing.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Literal


InputKind = Literal["file", "skill", "plugin", "marketplace"]


class InputResolutionError(ValueError):
    """Raised when an input string cannot be unambiguously classified.

    The error message is the remediation hint. Callers surface it
    verbatim — no rewrapping.
    """


@dataclass
class ResolvedInput:
    """One resolved input ready for downstream consumption.

    Attributes:
        kind: tagged-union discriminator — ``file`` / ``skill`` /
            ``plugin`` / ``marketplace``. Note ``marketplace`` is
            present only as an intermediate kind when the resolver is
            ASKED to keep it (default behaviour is to expand
            marketplaces into per-plugin entries).
        abs_path: absolute local filesystem path. For URL inputs this
            is the temp-clone directory; for local inputs it's the
            user-supplied path resolved to canonical form.
        source_url: the original URL (when applicable); ``None`` for
            pure-local inputs.
        display_name: human-readable identifier — plugin name, skill
            slug, or filename. Used for the per-plugin status table.
        cleanup_callback: optional zero-arg cleanup. ``None`` when
            no cleanup is needed (the input is a local path the user
            owns); otherwise a reference-counted closure that removes
            the temp clone once every consumer is done.
        metadata: free-form key/value extras (e.g. plugin version
            from the marketplace.json entry, source spec original
            text). Never holds anything load-bearing — purely
            informational.
    """

    kind: InputKind
    abs_path: Path
    source_url: str | None = None
    display_name: str = ""
    cleanup_callback: Callable[[], None] | None = None
    metadata: dict[str, object] = field(default_factory=dict)


# Regex for github URL shapes the resolver accepts.
_GITHUB_URL_RE = re.compile(
    r"^(?:https?://github\.com/|git@github\.com:|github\.com/)"
    r"(?P<owner>[A-Za-z0-9_.-]+)/"
    r"(?P<repo>[A-Za-z0-9_.-]+?)(?:\.git)?(?:/.*)?$"
)
# `owner/repo` shorthand.
_OWNER_REPO_RE = re.compile(r"^(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+)$")


def is_url_shape(spec: str) -> bool:
    """Return True iff ``spec`` looks like a github URL or ``owner/repo``.

    Conservative: only github is recognised, because the resolver
    only knows how to clone github. Other VCS / hosts would need
    explicit support.
    """
    spec = spec.strip()
    if spec.startswith(("http://github.com", "https://github.com", "git@github.com", "github.com/")):
        return True
    # owner/repo shorthand — must be EXACTLY two segments (no extra slashes),
    # no leading dot/dash, no trailing slash. Avoid colliding with local
    # paths like "scripts/cli.py".
    if "/" in spec and not spec.startswith((".", "/", "~")):
        parts = spec.split("/")
        if len(parts) == 2 and _OWNER_REPO_RE.match(spec):
            # Reject if either segment matches a real local file/dir under
            # the current working directory — local wins over remote.
            if not Path(spec).exists():
                return True
    return False


def parse_github_url(spec: str) -> tuple[str, str]:
    """Return ``(owner, repo)`` from a github URL or ``owner/repo`` shorthand.

    Raises ``InputResolutionError`` if the spec isn't a valid github URL.
    """
    spec = spec.strip()
    m = _GITHUB_URL_RE.match(spec)
    if m:
        return m.group("owner"), m.group("repo")
    m = _OWNER_REPO_RE.match(spec)
    if m:
        return m.group("owner"), m.group("repo")
    raise InputResolutionError(
        f"Input {spec!r} looks URL-shaped but doesn't match a github URL or owner/repo shorthand. "
        f"Accepted forms: 'owner/repo', 'github.com/owner/repo', 'https://github.com/owner/repo'."
    )


def _mk_tmp_clone_dir() -> Path:
    """Create a fresh temp dir under ``${TMPDIR}/cpv-batch-input-<uuid>/``."""
    base = Path(tempfile.gettempdir()) / f"cpv-batch-input-{uuid.uuid4().hex[:12]}"
    base.mkdir(parents=True, exist_ok=False)
    return base


def _mk_cleanup_callback(path: Path) -> Callable[[], None]:
    """Build a cleanup callback that removes ``path`` once on call.

    Idempotent: subsequent calls are no-ops (the directory is already
    gone). Errors are swallowed — cleanup is best-effort.
    """
    done = {"called": False}

    def _cleanup() -> None:
        if done["called"]:
            return
        done["called"] = True
        try:
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
        except OSError:
            pass

    return _cleanup


def _shallow_clone(owner: str, repo: str, dest: Path, branch: str | None = None) -> Path:
    """Run ``git clone --depth 1`` (optionally ``--branch <branch>``) into ``dest``.

    Returns the path of the cloned repo (``dest / repo``). Raises
    ``InputResolutionError`` if the clone fails.
    """
    target = dest / repo
    url = f"https://github.com/{owner}/{repo}.git"
    cmd: list[str] = ["git", "clone", "--depth", "1"]
    if branch:
        cmd += ["--branch", branch]
    cmd += [url, str(target)]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    except (OSError, subprocess.SubprocessError) as exc:
        raise InputResolutionError(
            f"git clone of {owner}/{repo} failed: {exc!s}. "
            f"Check network connectivity and that the repo exists."
        ) from exc
    if result.returncode != 0:
        raise InputResolutionError(
            f"git clone of {owner}/{repo} returned {result.returncode}:\n"
            f"stderr: {result.stderr[:500]}"
        )
    if not target.is_dir():
        raise InputResolutionError(
            f"git clone of {owner}/{repo} reported success but {target} does not exist."
        )
    return target


def _read_marketplace_json(root: Path) -> dict | None:
    """Read ``.claude-plugin/marketplace.json`` from ``root``.

    Returns the parsed dict or ``None`` on any read/parse failure.
    Module-local copy so this resolver has no dependency on
    cpv_repo_shape's import surface.
    """
    mp = root / ".claude-plugin" / "marketplace.json"
    if not mp.is_file():
        return None
    try:
        data = json.loads(mp.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _expand_marketplace(
    market_root: Path,
    source_url: str | None,
    parent_cleanup: Callable[[], None] | None,
) -> list[ResolvedInput]:
    """Given a directory containing ``.claude-plugin/marketplace.json``,
    enumerate every referenced plugin into a list of ResolvedInputs.

    For URL-source marketplaces, every plugin source is followed
    (cloning into ``market_root.parent / clones / <repo>``); for local
    marketplaces, the resolver expects every plugin to live as a
    sibling of the marketplace root (Layout B) OR to be addressable
    via the ``source`` field. Plugins that point off-tree (URL
    source from a local marketplace, etc.) are also cloned.

    The parent_cleanup callback is shared by every per-plugin
    ResolvedInput (reference-counted via the closure below) so the
    market_root temp dir is removed only after the LAST per-plugin
    consumer finishes.
    """
    mp_data = _read_marketplace_json(market_root)
    if mp_data is None:
        raise InputResolutionError(
            f"{market_root} does not contain a readable .claude-plugin/marketplace.json"
        )
    plugins_raw = mp_data.get("plugins", [])
    if not isinstance(plugins_raw, list):
        raise InputResolutionError(
            f"{market_root}: marketplace.json `plugins` field is not a list"
        )

    # Reference-counted cleanup: invoke parent_cleanup only when the
    # last per-plugin consumer's cleanup_callback is called. Use a
    # dict to hold the counter so it survives closure capture.
    counter = {"remaining": 0}

    def _decrement_and_maybe_cleanup() -> None:
        counter["remaining"] -= 1
        if counter["remaining"] <= 0 and parent_cleanup is not None:
            parent_cleanup()

    clones_dir = market_root.parent / "plugin-clones"
    clones_dir.mkdir(exist_ok=True)

    resolved: list[ResolvedInput] = []
    for entry in plugins_raw:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name", "")).strip()
        if not name:
            continue
        version = entry.get("version")
        source = entry.get("source") or {}
        if not isinstance(source, dict):
            continue

        src_kind = str(source.get("source", "")).strip()
        if src_kind == "github":
            repo_spec = str(source.get("repo", "")).strip()
            if "/" not in repo_spec:
                continue
            owner, repo = repo_spec.split("/", 1)
            try:
                plugin_path = _shallow_clone(owner, repo, clones_dir, branch=None)
            except InputResolutionError:
                # Skip individual plugin failures but warn.
                continue
            plugin_url = f"https://github.com/{owner}/{repo}"
        elif src_kind in ("local", "path") or not src_kind:
            # Local: the `path` field (or the plugin name) is relative to
            # the marketplace root.
            path_str = str(source.get("path", "") or name).strip()
            plugin_path = (market_root / path_str).resolve()
            if not (plugin_path / ".claude-plugin" / "plugin.json").is_file():
                # Try the marketplace root's parent (Layout A — plugin
                # checked out alongside the marketplace).
                plugin_path = (market_root.parent / path_str).resolve()
            if not (plugin_path / ".claude-plugin" / "plugin.json").is_file():
                continue
            plugin_url = None
        else:
            # Unknown source kind — skip.
            continue

        counter["remaining"] += 1
        resolved.append(
            ResolvedInput(
                kind="plugin",
                abs_path=plugin_path,
                source_url=plugin_url,
                display_name=name,
                cleanup_callback=_decrement_and_maybe_cleanup,
                metadata={"marketplace_root": str(market_root), "plugin_version": version},
            )
        )
    return resolved


def _detect_local_kind(path: Path) -> InputKind:
    """Classify a LOCAL path into ``file`` / ``skill`` / ``plugin`` /
    ``marketplace``.

    Raises ``InputResolutionError`` on ambiguity.
    """
    path = path.resolve()
    if not path.exists():
        raise InputResolutionError(f"path {path} does not exist")

    if path.is_file():
        # SKILL.md → skill (return the parent dir).
        if path.name == "SKILL.md":
            return "skill"
        return "file"

    if not path.is_dir():
        raise InputResolutionError(f"path {path} is neither a file nor a directory")

    has_plugin = (path / ".claude-plugin" / "plugin.json").is_file()
    has_market = (path / ".claude-plugin" / "marketplace.json").is_file()
    has_skill = (path / "SKILL.md").is_file()

    # Disambiguation. Plugin + marketplace coexisting is "marketplace-in-plugin"
    # (Layout C, valid); prefer the marketplace classification (broader).
    if has_market and has_plugin:
        return "marketplace"  # Layout C
    if has_market:
        return "marketplace"
    if has_plugin:
        return "plugin"
    if has_skill:
        return "skill"

    # Look for ``skills/<name>/SKILL.md`` shape — caller passed
    # ``skills/<name>/`` directly.
    if (path / "SKILL.md").is_file():
        return "skill"

    raise InputResolutionError(
        f"path {path} is a directory but doesn't contain "
        ".claude-plugin/plugin.json, .claude-plugin/marketplace.json, or SKILL.md. "
        "Pass an explicit --input-kind to override."
    )


def _resolve_single_local(path: Path) -> list[ResolvedInput]:
    kind = _detect_local_kind(path)
    if kind == "file":
        return [ResolvedInput(kind="file", abs_path=path, display_name=path.name)]
    if kind == "skill":
        # If the user passed SKILL.md directly, use its parent as the skill dir.
        skill_dir = path.parent if path.is_file() and path.name == "SKILL.md" else path
        return [
            ResolvedInput(kind="skill", abs_path=skill_dir, display_name=skill_dir.name)
        ]
    if kind == "plugin":
        return [ResolvedInput(kind="plugin", abs_path=path, display_name=path.name)]
    if kind == "marketplace":
        return _expand_marketplace(path, source_url=None, parent_cleanup=None)
    raise InputResolutionError(f"unhandled local kind: {kind}")


def _resolve_single_url(spec: str) -> list[ResolvedInput]:
    owner, repo = parse_github_url(spec)
    tmp = _mk_tmp_clone_dir()
    cleanup = _mk_cleanup_callback(tmp)
    try:
        clone_path = _shallow_clone(owner, repo, tmp, branch=None)
    except InputResolutionError:
        cleanup()
        raise

    # After clone, classify the resulting directory.
    try:
        kind = _detect_local_kind(clone_path)
    except InputResolutionError:
        cleanup()
        raise

    source_url = f"https://github.com/{owner}/{repo}"

    if kind == "marketplace":
        return _expand_marketplace(
            clone_path, source_url=source_url, parent_cleanup=cleanup
        )
    if kind == "plugin":
        return [
            ResolvedInput(
                kind="plugin",
                abs_path=clone_path,
                source_url=source_url,
                display_name=repo,
                cleanup_callback=cleanup,
                metadata={"owner": owner, "repo": repo},
            )
        ]
    # URL clones of bare skills / single files are unusual; the user can
    # always point at the full plugin URL instead.
    cleanup()
    raise InputResolutionError(
        f"URL {source_url} cloned cleanly but the root is a {kind} — "
        "URL inputs must point at a plugin or marketplace repo."
    )


def _resolve_list_file(path: Path) -> list[str]:
    """Read a list file (one input spec per non-empty, non-comment line)."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise InputResolutionError(f"could not read list file {path}: {exc}") from exc
    specs: list[str] = []
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        specs.append(s)
    return specs


def resolve(
    input_spec: str | list[str],
    *,
    allow_url: bool = True,
) -> list[ResolvedInput]:
    """Top-level resolver — accepts every shape described in the
    module docstring.

    Args:
        input_spec: a single string (path, URL, ``@listfile``, or
            comma-separated multi) OR a list of strings (each entry
            resolved independently).
        allow_url: if False, URL specs raise InputResolutionError.
            Used by scope-aware skills (TRDD-a175f78d) which reject
            URL inputs.

    Returns:
        A list of ResolvedInput. Empty list means the input spec
        resolved to zero items (e.g. an empty list file).

    Raises:
        InputResolutionError: on ambiguous or unresolvable input.
    """
    if isinstance(input_spec, list):
        # Recurse over each entry; flatten.
        out: list[ResolvedInput] = []
        for entry in input_spec:
            out.extend(resolve(entry, allow_url=allow_url))
        return out

    spec = input_spec.strip()
    if not spec:
        raise InputResolutionError("input spec is empty")

    # Comma-separated list shorthand. The split is taken when every
    # comma-delimited part independently classifies as URL-shaped OR
    # exists on disk — that's the only safe heuristic for telling a
    # multi-spec apart from a single path that happens to contain a
    # comma. URL shapes never contain commas, so a URL collision is
    # impossible.
    if "," in spec and not spec.startswith("@") and not is_url_shape(spec):
        parts = [p.strip() for p in spec.split(",") if p.strip()]
        if len(parts) > 1 and all(
            is_url_shape(p) or Path(p).expanduser().exists() for p in parts
        ):
            return resolve(parts, allow_url=allow_url)

    # @listfile shape.
    if spec.startswith("@"):
        list_path = Path(spec[1:]).expanduser().resolve()
        specs = _resolve_list_file(list_path)
        return resolve(specs, allow_url=allow_url)

    # URL shape?
    if is_url_shape(spec):
        if not allow_url:
            raise InputResolutionError(
                f"URL inputs are not allowed for this skill — got {spec!r}. "
                "Use a LOCAL filesystem path."
            )
        return _resolve_single_url(spec)

    # Local path.
    path = Path(spec).expanduser()
    if not path.is_absolute():
        path = path.resolve()
    return _resolve_single_local(path)
