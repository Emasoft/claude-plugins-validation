#!/usr/bin/env python3
"""Generate a complete Claude Code plugin repository scaffold.

Creates all standard files for a plugin repo: manifest, pyproject.toml,
.gitignore, README with badge markers, LICENSE, cliff.toml, CI/CD workflows,
git hooks, publish script, and empty component directories.

Usage:
    uv run scripts/generate_plugin_repo.py <target-dir> \\
      --name <plugin-name> --description <desc> \\
      --author <name> --author-email <email> \\
      --license MIT --python-version 3.12 \\
      --github-owner <owner> --marketplace <mkt-name> \\
      [--dry-run]
"""

import argparse
import importlib.metadata
import json
import os
import re
import shutil
import stat
import sys
from dataclasses import dataclass
from pathlib import Path

# Canonical-pipeline PROFILE vocabulary (TRDD-e9f13df1, issues #128 / #115 /
# #130 / #118-d2). `gen_publish_py` and `generate_plugin_repo` are profile-aware
# so a `submodule-build` plugin (PSS shape, #128) generates a SUBMODULE-AWARE
# publish.py variant instead of the standard one. The module is a sibling under
# scripts/ (pure stdlib) — its dir is sys.path[0] when this file runs as a
# script and is already inserted by validate_plugin.py / the tests when imported.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from cpv_pipeline_profile import (  # noqa: E402 — sibling import after the path insert above
    KNOWN_PROFILES,
    PROFILE_STANDARD,
    PROFILE_SUBMODULE_BUILD,
    resolve_pipeline_profile,
)

# -- ANSI colors (disabled when NO_COLOR is set or stdout is not a tty) ------


def _colors_supported() -> bool:
    """Return True only when the terminal supports ANSI escape sequences.

    Uses sys.platform (rather than os.name) so that pyright's type-narrowing
    can analyze both branches — os.name == "nt" is evaluated as unreachable
    on non-Windows hosts and flagged as "code not analyzed", but sys.platform
    comparisons are understood by static analyzers.
    """
    if os.environ.get("NO_COLOR"):
        return False
    if sys.platform.startswith("win"):
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
        except (AttributeError, OSError):
            pass
        return bool(os.environ.get("WT_SESSION") or os.environ.get("ANSICON"))
    return hasattr(sys.stdout, "isatty") and sys.stdout.isatty()


_USE_COLOR = _colors_supported()

RED = "\033[0;31m" if _USE_COLOR else ""
GREEN = "\033[0;32m" if _USE_COLOR else ""
YELLOW = "\033[1;33m" if _USE_COLOR else ""
BLUE = "\033[0;34m" if _USE_COLOR else ""
BOLD = "\033[1m" if _USE_COLOR else ""
NC = "\033[0m" if _USE_COLOR else ""


# =============================================================================
# DATA CLASS
# =============================================================================


VALID_LANGUAGES = {"python", "js", "ts", "rust", "go", "deno", "elixir", "ruby", "java", "kotlin"}


# TRDD-83ab59e7: per-language manifest files used by `--language auto`
# resolution and per-language scaffolding. Each language MUST appear in
# VALID_LANGUAGES and (for non-python) MUST have a manifest generator
# wired up in `generate_all_files()`. Order matches the TRDD spec table.
LANGUAGE_MANIFESTS: dict[str, str] = {
    "python": "pyproject.toml",
    "js": "package.json",
    "ts": "package.json",
    "rust": "Cargo.toml",
    "go": "go.mod",
    "deno": "deno.json",
    "elixir": "mix.exs",
    "ruby": "Gemfile",
    "java": "pom.xml",
    "kotlin": "build.gradle.kts",
}


# Last-resort CPV ref used only when neither the installed-package version nor
# this generator's own plugin.json can be read (e.g. the script was vendored out
# of the CPV repo with no package metadata). It MUST be a ref that actually
# exists on the CPV remote, or every `git+https://…@<ref>` / `uvx --from
# …@<ref>` downstream callsite 404s ("ref not found"). CPV's default branch is
# `master` (there is no `main` ref), so the fallback is `master` — NOT `main`
# (issue #139, which 404'd `standardize` via uvx). A concrete version tag is
# always preferred over this branch fallback (see _default_cpv_ref) because a
# tag keeps the downstream UV cache key stable so the cold CPV build is paid
# ONCE per tag, and stops every CPV release from red-lighting all downstream CI
# with zero plugin changes (root-cause #2 of the phase-3 CI-failure analysis).
_FALLBACK_CPV_REF = "master"


def _default_cpv_ref() -> str:
    """Return the CPV git ref a freshly-scaffolded plugin should pin to.

    Resolves to the CPV version that is doing the scaffolding — read from
    THIS plugin's own ``.claude-plugin/plugin.json`` ``version`` and prefixed
    with ``v`` (e.g. ``v2.133.0``). A plugin scaffolded by CPV 2.133.0 pins
    its publish.py / ci.yml / release.yml CPV calls to ``@v2.133.0`` so that
    a later CPV release with a stricter rule does NOT silently break the
    plugin's CI; the maintainer bumps the pin deliberately and re-runs CI to
    green.

    Resolution order (each attempt isolated so a failure cleanly tries the
    next — the generator must never crash because it could not introspect its
    own version):

      1. ``importlib.metadata.version("claude-plugins-validation")`` — works
         when CPV runs as a uvx/pip-installed PACKAGE (the layout in which
         ``.claude-plugin/plugin.json`` is NOT present). Issue #139: without
         this, the installed-package case fell straight through to the fallback,
         which used to be the non-existent ``main`` ref → every standardized
         plugin pinned ``@main`` → ``uvx --from …@main`` 404'd.
      2. ``.claude-plugin/plugin.json`` ``version`` — the in-repo case (running
         the generator from a CPV checkout). The lookup walks up from this file
         (scripts/generate_plugin_repo.py) to the repo root.
      3. ``_FALLBACK_CPV_REF`` (``master``) — when neither source is readable.

    The returned version is always prefixed with a leading ``v`` (e.g.
    ``v2.137.0``); the fallback branch ref is returned verbatim. The ``pypi``
    CPV source strips the leading ``v`` again for the wheel form (see
    ``cpv_uvx_from_arg``), so the prefix convention is safe for both sources.
    """
    # 1. Installed-package metadata (pip/uvx layout — no plugin.json on disk).
    try:
        version = importlib.metadata.version("claude-plugins-validation")
    except importlib.metadata.PackageNotFoundError:
        version = None
    if isinstance(version, str) and version.strip():
        version = version.strip()
        return version if version.startswith("v") else f"v{version}"

    # 2. In-repo manifest (running from a CPV checkout).
    try:
        manifest = Path(__file__).resolve().parent.parent / ".claude-plugin" / "plugin.json"
        version = json.loads(manifest.read_text(encoding="utf-8")).get("version")
    except (OSError, ValueError, json.JSONDecodeError):
        version = None
    if isinstance(version, str) and version.strip():
        version = version.strip()
        return version if version.startswith("v") else f"v{version}"

    # 3. Last resort — a ref that actually exists on the CPV remote.
    return _FALLBACK_CPV_REF


# Recognized CPV distribution sources (issue #137). "git" is the historical
# default (cold from-source build); "pypi" fetches the published wheel.
CPV_SOURCE_GIT = "git"
CPV_SOURCE_PYPI = "pypi"
VALID_CPV_SOURCES = {CPV_SOURCE_GIT, CPV_SOURCE_PYPI}

# The PyPI distribution name of the published CPV wheel. The `pypi` source pins
# to an EXACT version (`==`) — the same ref the `git` source pins to, with any
# leading `v` stripped (a PyPI version is `2.136.1`, never `v2.136.1`).
_CPV_PYPI_DIST = "claude-plugins-validation"

# NOTE: the CPV-source helper FUNCTIONS (cpv_uvx_from_arg / cpv_uvx_needs_pyyaml
# / cpv_uvx_pyyaml_shell_fragment) are defined AFTER the `PluginParams` dataclass
# below — they take a `PluginParams` argument and this module does NOT use
# `from __future__ import annotations`, so the annotation must resolve at def
# time. They are grouped with the other `gen_*`/param-consuming helpers there.

# ── Sharded test matrix ↔ dev-extra COUPLING (RC-9, CI-failure forensics) ────
# The emitted ci.yml runs the test suite as a pytest-split SHARD MATRIX
# (`pytest --splits N --group K`). Those flags come from the `pytest-split`
# plugin — pytest does not know them natively, so a repo whose dev extra omits
# the dependency dies with `pytest: error: unrecognized arguments: --splits
# --group` (real failure: ai-maestro-web-scenario-tester run 28959141245).
# The shard count and the requirement therefore live in ONE place and are
# consumed by BOTH gen_ci_yml (the matrix) and gen_pyproject_toml (the dev
# extra), so the two can never desync in a later edit. If a future template
# ever emits a NON-sharded matrix, drop the `--splits`/`--group` flags AND this
# requirement together (tests/test_canon_rc1_rc8_rc9_template.py pins the
# biconditional).
TEST_SHARD_COUNT = 2
PYTEST_SPLIT_REQUIREMENT = "pytest-split>=0.9"


def resolve_language(arg: str, target: Path) -> str:
    """Resolve the --language CLI argument to a concrete language string.

    For `--language auto`, calls `detect_languages(target)` and picks the
    first language found in the canonical priority order. If detection
    finds nothing (or `target` doesn't exist), falls back to `python` so
    the original behaviour is preserved.

    Args:
        arg: The raw value of --language (one of VALID_LANGUAGES, or "auto").
        target: The target directory the plugin will be generated into.

    Returns:
        A concrete language string from VALID_LANGUAGES.

    Why a thin wrapper instead of doing this inline in main(): so tests
    can exercise the auto-detection path without mocking argparse, and so
    `standardize_plugin.py` (which audits an EXISTING plugin) can call
    the same resolver to pick the correct language.
    """
    if arg != "auto":
        return arg
    if not target.exists() or not target.is_dir():
        return "python"
    # Local import to avoid a hard cycle: detect_language imports nothing
    # from this module, but keeping the import local also means tools that
    # `import generate_plugin_repo` for a single helper do not pay the
    # detection-module import cost.
    from detect_language import detect_languages  # noqa: PLC0415

    detected = detect_languages(target)
    if not detected:
        return "python"
    # TRDD priority: prefer ts > js when both are present, then walk the
    # rest of LANGUAGE_MANIFESTS in declaration order. This matches the
    # detect_language module's own discriminator (tsconfig.json wins).
    for lang in LANGUAGE_MANIFESTS:
        if lang in detected:
            return lang
    return "python"


# ── Phase 6: single-input slurp helpers ───────────────────────────────────


def _read_md_frontmatter(path: Path) -> dict[str, str]:
    """Return the YAML-ish frontmatter of a .md file as a flat dict.

    Stops at the closing `---` line. Uses a tiny line-by-line parser
    instead of pyyaml so this script stays dependency-free for the
    callers that import it without a venv.
    """
    if not path.is_file():
        return {}
    text = path.read_text(encoding="utf-8", errors="replace")
    if not text.startswith("---\n"):
        return {}
    body = text[4:]
    end = body.find("\n---")
    if end < 0:
        return {}
    fm: dict[str, str] = {}
    for line in body[:end].splitlines():
        if ":" not in line or line.startswith("#"):
            continue
        key, _, value = line.partition(":")
        fm[key.strip()] = value.strip()
    return fm


def _classify_md(path: Path) -> str:
    """Return 'skill' / 'command' / 'agent' for a .md file.

    Heuristic order:
      1. Filename `SKILL.md` → skill (will be placed under skills/<parent>/).
      2. Frontmatter has `allowed-tools:` → command (per CC spec, only
         commands declare allowed-tools).
      3. Default → agent (the catch-all bucket; agents have the richest
         frontmatter surface).
    """
    if path.name == "SKILL.md":
        return "skill"
    fm = _read_md_frontmatter(path)
    if "allowed-tools" in fm:
        return "command"
    return "agent"


_REQUIRED_SKILL_SECTIONS = (
    "## Overview",
    "## When to use",
    "## Instructions",
    "## Prerequisites",
    "## Output",
    "## Error Handling",
    "## Resources",
)


def _audit_slurped_skill(dest_md: Path) -> None:
    """Print actionable WARN lines if a slurped SKILL.md is missing the
    sections CPV's strict validator requires.

    The slurp does NOT modify user content — that would be surprising and
    risky. Instead, it surfaces every missing section so the user can add
    them before publishing. Each missing section becomes one WARN line
    with the exact heading text to paste in.
    """
    try:
        text = dest_md.read_text(encoding="utf-8")
    except OSError:
        return
    missing = [h for h in _REQUIRED_SKILL_SECTIONS if h not in text]
    if not missing:
        return
    print(
        f"  [slurp] {YELLOW}WARN{NC} skill {dest_md.name} is missing "
        f"{len(missing)} required section(s) for CPV strict-mode validation:"
    )
    for heading in missing:
        print(f"           - add a `{heading}` section before publishing")
    fm_match = re.search(r"^---\n(.*?)\n---", text, re.DOTALL | re.MULTILINE)
    fm_block = fm_match.group(1) if fm_match else ""
    if "Trigger with" not in fm_block:
        print(
            f"  [slurp] {YELLOW}WARN{NC} skill {dest_md.name} description "
            f"is missing 'Trigger with ...' phrase (Nixtla strict mode "
            f"requires both 'Use when ...' AND 'Trigger with ...')."
        )


def _slurp_one(target_root: Path, src: Path, kind: str) -> int:
    """Copy `src` into the right component folder of `target_root`.

    `kind` is one of: 'skill', 'agent', 'command', 'mcp', 'scripts'.
    Returns the number of files copied.
    """
    n = 0
    if kind == "skill":
        # If src is a directory containing SKILL.md, copy whole tree.
        # If src is a SKILL.md file, copy under skills/<parent-dir-name>/.
        if src.is_dir() and (src / "SKILL.md").is_file():
            dest = target_root / "skills" / src.name
            dest.mkdir(parents=True, exist_ok=True)
            for f in src.rglob("*"):
                if f.is_file():
                    rel = f.relative_to(src)
                    dest_f = dest / rel
                    dest_f.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(f, dest_f)
                    n += 1
            print(f"  [slurp] skill {src} → {dest.relative_to(target_root)}/ ({n} files)")
            _audit_slurped_skill(dest / "SKILL.md")
        elif src.is_file() and src.name == "SKILL.md":
            # Use parent dir name OR the skill's `name:` frontmatter.
            fm = _read_md_frontmatter(src)
            skill_name = fm.get("name") or src.parent.name or "imported-skill"
            dest_dir = target_root / "skills" / skill_name
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest_md = dest_dir / "SKILL.md"
            shutil.copy2(src, dest_md)
            n = 1
            print(f"  [slurp] skill {src} → skills/{skill_name}/SKILL.md")
            _audit_slurped_skill(dest_md)
        else:
            print(f"  [slurp] {YELLOW}WARN{NC} --skill {src}: not a SKILL.md or skill dir; skipped")
        return n

    if kind in ("agent", "command"):
        if not src.is_file() or src.suffix != ".md":
            print(f"  [slurp] {YELLOW}WARN{NC} --{kind} {src}: not a .md file; skipped")
            return 0
        sub = "agents" if kind == "agent" else "commands"
        dest_dir = target_root / sub
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest_f = dest_dir / src.name
        shutil.copy2(src, dest_f)
        print(f"  [slurp] {kind} {src} → {sub}/{src.name}")
        return 1

    if kind == "mcp":
        # Either a directory containing .mcp.json OR the .mcp.json itself.
        if src.is_dir():
            mcp = src / ".mcp.json"
            if not mcp.is_file():
                print(f"  [slurp] {YELLOW}WARN{NC} --mcp-server {src}: no .mcp.json found; skipped")
                return 0
            shutil.copy2(mcp, target_root / ".mcp.json")
            n += 1
            # Also copy any sibling files referenced by .mcp.json (best-effort).
            for f in src.rglob("*"):
                if f.is_file() and f.name != ".mcp.json":
                    rel = f.relative_to(src)
                    dest_f = target_root / "mcp-server" / rel
                    dest_f.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(f, dest_f)
                    n += 1
            print(f"  [slurp] mcp {src} → .mcp.json + {n - 1} sidecar files")
        elif src.is_file() and src.name == ".mcp.json":
            shutil.copy2(src, target_root / ".mcp.json")
            n = 1
            print(f"  [slurp] mcp {src} → .mcp.json")
        else:
            print(f"  [slurp] {YELLOW}WARN{NC} --mcp-server {src}: not a .mcp.json file or dir; skipped")
        return n

    if kind == "scripts":
        if not src.is_dir():
            print(f"  [slurp] {YELLOW}WARN{NC} --scripts {src}: not a directory; skipped")
            return 0
        dest_dir = target_root / "scripts"
        dest_dir.mkdir(parents=True, exist_ok=True)
        for f in src.iterdir():
            if f.is_file():
                shutil.copy2(f, dest_dir / f.name)
                n += 1
        print(f"  [slurp] scripts {src} → scripts/ ({n} files)")
        return n

    return 0


def _do_slurp(
    target_root: Path,
    *,
    from_paths: list[Path],
    skill_paths: list[Path],
    agent_paths: list[Path],
    command_paths: list[Path],
    mcp_paths: list[Path],
    scripts_paths: list[Path],
) -> int:
    """Apply all --from / --skill / --agent / --command / --mcp-server /
    --scripts flags to `target_root`. Returns total files copied.

    --from PATH is auto-classified via _classify_md (for .md files) or
    inferred from the filename (.mcp.json → mcp; directory of scripts
    → scripts; SKILL.md → skill).
    """
    total = 0
    for p in from_paths:
        if not p.exists():
            print(f"  [slurp] {YELLOW}WARN{NC} --from {p}: does not exist; skipped")
            continue
        if p.is_file() and p.name == ".mcp.json":
            kind = "mcp"
        elif p.is_file() and p.suffix == ".md":
            kind = _classify_md(p)
        elif p.is_dir() and (p / "SKILL.md").is_file():
            kind = "skill"
        elif p.is_dir() and (p / ".mcp.json").is_file():
            kind = "mcp"
        elif p.is_dir():
            kind = "scripts"
        else:
            print(f"  [slurp] {YELLOW}WARN{NC} --from {p}: cannot classify; skipped")
            continue
        total += _slurp_one(target_root, p, kind)

    for p in skill_paths:
        total += _slurp_one(target_root, p, "skill")
    for p in agent_paths:
        total += _slurp_one(target_root, p, "agent")
    for p in command_paths:
        total += _slurp_one(target_root, p, "command")
    for p in mcp_paths:
        total += _slurp_one(target_root, p, "mcp")
    for p in scripts_paths:
        total += _slurp_one(target_root, p, "scripts")
    return total


@dataclass
class PluginParams:
    """All parameters needed to scaffold a plugin repository."""

    name: str
    description: str
    author: str
    author_email: str
    license: str = "MIT"
    python_version: str = "3.12"
    github_owner: str = ""
    marketplace: str = ""
    version: str = "0.1.0"
    language: str = "python"  # One of VALID_LANGUAGES
    self_marketplace: bool = False  # Layout C: emit .claude-plugin/marketplace.json with self-entry
    strip_dev: bool = True  # TRDD-793ac32a: emit cpv.strip block in plugin.json (default ON)
    # Per-plugin marketplace OWNER override — set by the migration path
    # when the existing notify-marketplace.yml targets a different owner
    # than the plugin itself (e.g. plugin at Emasoft/* lives in a different
    # marketplace org). Empty falls back to github_owner.
    #
    # NOTE (v2.86.0): the secret NAME is NOT per-plugin. CPV enforces the
    # canonical name `MARKETPLACE_PAT` everywhere; deviations are flagged
    # via an [ACTION REQUIRED] migration warning, not preserved. See
    # TRDD-canonical-pipeline-hardening for the rationale.
    marketplace_owner: str = ""  # Owner segment for MARKETPLACE_OWNER (when ≠ plugin owner)
    # Pinned CPV ref the generated pipeline fetches the validator at. Empty
    # means "resolve to the scaffolding CPV's own version" (see
    # cpv_ref_resolved). Set explicitly via --cpv-ref to pin a different tag.
    # Pinning (not tracking HEAD) is root-cause-#2's keystone fix: it makes the
    # downstream UV cache key stable and stops every CPV release from breaking
    # all downstream CI with no plugin change.
    cpv_ref: str = ""
    # Which DISTRIBUTION the generated pipeline fetches the CPV validator from
    # (issue #137). Default ``"git"`` keeps today's exact behavior: every
    # callsite resolves to ``git+https://…/claude-plugins-validation@<ref>``
    # (a cold from-source build). ``"pypi"`` instead fetches the published
    # wheel ``claude-plugins-validation==<ver>`` (fast, no compile, and pyyaml
    # is a declared wheel dependency so the ``--with pyyaml`` shim is dropped).
    # NON-BREAKING: an absent / "git" value reproduces the historical output
    # byte-for-byte. Set via ``--cpv-source pypi``.
    cpv_source: str = "git"
    # Emit the (per-plugin) notify-marketplace.yml even when the marketplace is
    # the placeholder name — normally we skip it (root-cause #9: a placeholder
    # notify workflow red-lights every release because the secret/target are
    # unfilled). Forced on for migrations that genuinely target the placeholder.
    force_notify: bool = False

    @property
    def cpv_ref_resolved(self) -> str:
        """The concrete CPV git ref to pin downstream pipeline calls to.

        Returns the explicit ``cpv_ref`` when set, else the scaffolding CPV's
        own version (``_default_cpv_ref()``). Every workflow/publish generator
        substitutes this so all five ``git+https://…/claude-plugins-validation``
        callsites + the README snippet pin the SAME ref.
        """
        return self.cpv_ref.strip() if self.cpv_ref.strip() else _default_cpv_ref()

    @property
    def repo_name(self) -> str:
        """GitHub repo name — defaults to plugin name."""
        return self.name

    @property
    def github_url(self) -> str:
        """Full GitHub URL for the plugin."""
        return f"https://github.com/{self.github_owner}/{self.repo_name}"


# =============================================================================
# CPV-SOURCE SELECTOR (issue #137) — single source of truth for how every
# generated pipeline file references the CPV validator. Defined here (after the
# `PluginParams` dataclass) because they take a `PluginParams` and this module
# does not use `from __future__ import annotations`. The recognized source
# constants (CPV_SOURCE_GIT / CPV_SOURCE_PYPI / VALID_CPV_SOURCES / _CPV_PYPI_DIST)
# live near `_default_cpv_ref` above.
# =============================================================================


def cpv_uvx_from_arg(p: PluginParams) -> str:
    """Return the ``uvx --from`` package spec for the generated CPV callsites.

    Single source of truth for how every generated pipeline file (publish.py,
    ci.yml, release.yml, README) references CPV (issue #137). Routes on
    ``p.cpv_source``:

      * ``"git"`` (default, NON-BREAKING) →
        ``git+https://github.com/Emasoft/claude-plugins-validation@<ref>`` — the
        exact historical form. ``<ref>`` is ``p.cpv_ref_resolved``.
      * ``"pypi"`` → ``claude-plugins-validation==<ver>`` where ``<ver>`` is
        ``p.cpv_ref_resolved`` with any leading ``v`` stripped (a PyPI version
        is bare, e.g. ``2.136.1``). When the ref is NOT a concrete version
        (``main`` / a branch / a SHA — no published wheel exists for it), the
        spec degrades to the bare dist name ``claude-plugins-validation`` (uvx
        resolves the latest wheel) rather than emitting an unsatisfiable
        ``==main``.

    Any unrecognized ``cpv_source`` falls back to the ``git`` form (fail-safe —
    the default never silently changes). Side-effect-free.
    """
    if p.cpv_source == CPV_SOURCE_PYPI:
        ver = p.cpv_ref_resolved.lstrip("v").strip()
        # A non-version ref (branch/SHA/"main") has no matching published wheel;
        # a `==<ref>` pin would be unsatisfiable, so resolve the latest wheel
        # instead. A version starts with a digit (`2`, `2.136`, `2.136.1`).
        if ver and re.match(r"^[0-9]+(?:\.[0-9]+)*", ver):
            return f"{_CPV_PYPI_DIST}=={ver}"
        return _CPV_PYPI_DIST
    return f"git+https://github.com/Emasoft/claude-plugins-validation@{p.cpv_ref_resolved}"


def cpv_uvx_needs_pyyaml(p: PluginParams) -> bool:
    """True iff the generated ``uvx`` callsites must add ``--with pyyaml``.

    The ``git`` (from-source) install does not declare pyyaml, so the callsites
    inject it with ``--with pyyaml``. The published ``pypi`` wheel declares
    pyyaml as a runtime dependency, so the shim is dropped (issue #137).
    """
    return p.cpv_source != CPV_SOURCE_PYPI


def cpv_uvx_pyyaml_shell_fragment(p: PluginParams, *, indent: str, cont: str) -> str:
    r"""Return the ``--with pyyaml`` shell continuation fragment, or "".

    Used inside the workflow f-strings (gen_ci_yml, gen_release_yml) to emit the
    line::

        --with pyyaml <cont>
        <indent>

    immediately before the ``cpv-remote-validate …`` line — but ONLY for the
    ``git`` source (issue #137). For the ``pypi`` source the wheel already
    declares pyyaml, so the fragment is empty and the ``cpv-remote-validate``
    line follows the ``uvx --from …`` line directly.

    ``indent`` is the leading whitespace of the following shell line; ``cont``
    is the line-continuation token (a single ``\`` — pass ``"\\"`` from a normal
    string literal). Both are passed by the callsite so the emitted YAML matches
    the surrounding indentation exactly. When git: returns
    ``"--with pyyaml <cont>\n<indent>"``. When pypi: returns ``""``.
    """
    if not cpv_uvx_needs_pyyaml(p):
        return ""
    return f"--with pyyaml {cont}\n{indent}"


# The machine-readable verdict line every CPV plugin run prints
# (`validate_plugin.print_report`). Its PRESENCE is the proof that the validator
# actually ran and produced a verdict — see gen_cpv_validate_run_block.
CPV_SUMMARY_MARKER = "SUMMARY: CRITICAL="


def gen_cpv_validate_run_block(p: PluginParams, report_path: str) -> str:
    """Return the `run:` body of the remote-CPV validation step (10-space indent).

    Shared by gen_ci_yml (Validate job) and gen_release_yml (release job) so the
    two error handlers can never diverge.

    RC-8 (CI-failure forensics, 2026-07-13) — the handler this replaces was
    MISLEADING and, worse, FAIL-OPEN:

    * It printed ``CRITICAL/MAJOR/MINOR/NIT found`` for ANY non-zero exit. A cold
      ``uvx --from git+…`` build that dies on a transient GitHub git-fetch
      (``Failed to resolve `--with` requirement / Git operation failed``) exits 1
      — byte-identical to a CRITICAL verdict — so an infra flake was reported as
      a validation failure and triage went down the wrong path
      (ai-maestro-orchestrator-agent run 27940567560).
    * ci.yml's handler treated ANY exit ``>= 5`` as "advisory WARNING-level" and
      exited 0. But CPV's exit codes stop at 4 (``cpv_validation_common``:
      EXIT_OK 0 / CRITICAL 1 / MAJOR 2 / MINOR 3 / NIT 4 — WARNING never gets an
      exit code of its own), so ``>= 5`` is NEVER a verdict: a
      ``uvx: command not found`` (127) or an OOM-killed run (137) SILENTLY
      PASSED the gate. release.yml's handler had the same hole in the other
      direction (it only errored on 1-4 and fell through to publish otherwise).

    Because uvx itself also exits 1/2, the exit code ALONE cannot separate the
    two cases. The block therefore additionally requires CPV's own SUMMARY line
    as PROOF the validator ran:

    * exit 0                          → pass (never blocked; the marker is not
                                        required, so this can never false-block a
                                        clean run).
    * exit 1-4 **and** a SUMMARY line → real findings → fail, labelled findings.
    * anything else                   → the validator FAILED TO RUN → fail,
                                        labelled infra/network.

    Fail-closed by design: an infra failure is NOT "no findings", so it must
    never green the gate — but it is now reported as what it actually is.

    RC-180 (diagnosability, 2026-07-28) — the output is `tee`d rather than
    redirected. With `> file 2>&1` plus a trailing `cat`, a healthy run and a
    hung one are BYTE-IDENTICAL in the log for the entire window: nothing is
    printed until the command returns, so when the job is killed at its
    `timeout-minutes` the `cat` never runs and the log shows NOTHING about what
    was in flight.

    `PYTHONUNBUFFERED=1` makes the PHASE BANNERS land at their true time instead
    of whenever a 4-8 KB buffer fills (the validator calls no explicit flush), so
    a hung run shows which phase it died in. Measured honestly on CPV's own
    releases, that is ALL it buys: with it present, 1794 of 1804 lines still
    arrive at exit — those lines are the FINAL REPORT, which the validator
    generates at the end by program structure, not output waiting in a buffer.
    An earlier version of this comment claimed unbuffering was what made the tee
    stream; re-measuring disproved that. Richer per-phase progress is a real and
    still-open improvement (issue #180's second ask), not something this delivers.

    `${{PIPESTATUS[0]}}` is used rather than `$?` so the exit code is the
    VALIDATOR's, not `tee`'s — reading `tee`'s status here would report success
    for every failed validation. This matters more than it looks: GitHub's
    default `run:` shell is `bash -e {{0}}` WITHOUT `-o pipefail` (confirmed in
    the same run log), so `$?` after a pipeline really would be `tee`'s.

    Args:
        p: the plugin params (selects the git/pypi CPV source).
        report_path: where to capture the validator's combined output. ci.yml
            keeps it OUT of the checkout (``$RUNNER_TEMP``) so the validator
            cannot scan its own report; release.yml writes it into the workspace
            because the file is uploaded as a release asset (issue #121).
    """
    cpv_from = cpv_uvx_from_arg(p)
    cpv_pyyaml = cpv_uvx_pyyaml_shell_fragment(p, indent=" " * 14, cont="\\")
    return f"""set +e
          PYTHONUNBUFFERED=1 uvx --from {cpv_from} \\
              {cpv_pyyaml}cpv-remote-validate plugin . --strict \\
              2>&1 | tee "{report_path}"
          # Quoted on every use: with `exit_code=$?` shellcheck could infer the
          # value is numeric and stayed silent, but it cannot infer that through
          # ${{PIPESTATUS[0]}}, so an unquoted expansion trips SC2086 — and the
          # generated Lint job runs actionlint, which would turn every scaffolded
          # plugin's CI red.
          exit_code=${{PIPESTATUS[0]}}
          set -e
          if [ "$exit_code" -eq 0 ]; then
            echo "Validation passed"
            exit 0
          fi
          # CPV's verdict exit codes are 1-4. Anything else — and any 1-4 with no
          # SUMMARY line — means the validator never produced a verdict (a failed
          # uvx/git fetch exits 1 or 2 exactly like a findings verdict).
          if [ "$exit_code" -ge 1 ] && [ "$exit_code" -le 4 ] \\
             && grep -q "{CPV_SUMMARY_MARKER}" "{report_path}"; then
            echo "::error::Validation failed (exit $exit_code: CRITICAL/MAJOR/MINOR/NIT found)"
            exit "$exit_code"
          fi
          echo "::error::CPV validator FAILED TO RUN (exit $exit_code) — infra/network/install failure, NOT a validation verdict. No findings were produced; do not read this as CRITICAL/MAJOR/MINOR/NIT. See the log above (a cold 'uvx --from git+...' build can fail on a transient GitHub git-fetch)."
          exit 1"""


# =============================================================================
# LANGUAGE-SPECIFIC MANIFEST GENERATORS
# =============================================================================


def gen_package_json(p: PluginParams) -> str:
    """Generate package.json for JS/TS plugins."""
    dev_deps: dict[str, str] = {"eslint": "^9.0.0"}
    if p.language == "ts":
        dev_deps["typescript"] = "^5.0.0"
    manifest: dict[str, object] = {
        "name": p.name,
        "version": p.version,
        "description": p.description,
        "author": f"{p.author} <{p.author_email}>",
        "license": p.license,
        "type": "module",
        "scripts": {
            "lint": "eslint scripts/" if p.language == "js" else "eslint scripts/ && tsc --noEmit",
            "test": "vitest run",
        },
        "devDependencies": dev_deps,
    }
    if p.github_owner:
        manifest["homepage"] = p.github_url
        manifest["repository"] = {"type": "git", "url": f"{p.github_url}.git"}
    return json.dumps(manifest, indent=2) + "\n"


def gen_tsconfig_json() -> str:
    """Generate tsconfig.json for TypeScript plugins."""
    return (
        json.dumps(
            {
                "compilerOptions": {
                    "target": "ES2022",
                    "module": "ESNext",
                    "moduleResolution": "bundler",
                    "strict": True,
                    "esModuleInterop": True,
                    "skipLibCheck": True,
                    "noEmit": True,
                },
                "include": ["scripts/**/*.ts"],
            },
            indent=2,
        )
        + "\n"
    )


def gen_cargo_toml(p: PluginParams) -> str:
    """Generate Cargo.toml for Rust plugins."""
    return f"""[package]
name = "{p.name}"
version = "{p.version}"
edition = "2021"
authors = ["{p.author} <{p.author_email}>"]
description = "{p.description}"
license = "{p.license}"

[dependencies]
"""


def gen_go_mod(p: PluginParams) -> str:
    """Generate go.mod for Go plugins."""
    module = f"github.com/{p.github_owner}/{p.repo_name}" if p.github_owner else p.name
    return f"""module {module}

go 1.22
"""


def gen_deno_json(p: PluginParams) -> str:
    """Generate deno.json for Deno plugins."""
    return (
        json.dumps(
            {
                "name": f"@{p.github_owner or 'local'}/{p.name}",
                "version": p.version,
                "exports": "./scripts/mod.ts",
                "tasks": {
                    "lint": "deno lint scripts/",
                    "test": "deno test",
                    "fmt": "deno fmt scripts/",
                },
            },
            indent=2,
        )
        + "\n"
    )


def _module_name_from_plugin(name: str) -> str:
    """Convert a kebab-case plugin name to a CamelCase Elixir/Java module.

    `my-test-plugin` -> `MyTestPlugin`. Used as the namespace for Elixir
    `defmodule` and Java/Kotlin package fragments. Idempotent on names
    that are already CamelCase.
    """
    parts = [p for p in name.replace("_", "-").split("-") if p]
    return "".join(p[:1].upper() + p[1:] for p in parts) or "Plugin"


def _atom_name_from_plugin(name: str) -> str:
    """Convert a kebab-case plugin name to an Elixir atom (snake_case).

    `my-test-plugin` -> `my_test_plugin`. Elixir atoms use snake_case for
    project app names. Idempotent on already-snake names.
    """
    return name.replace("-", "_").lower() or "plugin"


def gen_mix_exs(p: PluginParams) -> str:
    """Generate mix.exs for Elixir plugins.

    Produces a minimal valid Mix project with :credo (lint) and :ex_unit
    (built-in test framework, no extra dep) wired up. The defmodule name
    is CamelCase from the plugin name; the :app atom is snake_case.

    Why not embed test_paths or coverage config: keep the scaffold under
    50 lines so plugin authors can read it top-to-bottom and customise
    without un-learning Elixir defaults.
    """
    module = _module_name_from_plugin(p.name)
    atom = _atom_name_from_plugin(p.name)
    return f"""defmodule {module}.MixProject do
  use Mix.Project

  def project do
    [
      app: :{atom},
      version: "{p.version}",
      elixir: "~> 1.16",
      description: "{p.description}",
      deps: deps(),
      package: package(),
      preferred_cli_env: [credo: :dev, test: :test]
    ]
  end

  def application do
    [extra_applications: [:logger]]
  end

  defp deps do
    [
      {{:credo, "~> 1.7", only: [:dev, :test], runtime: false}}
    ]
  end

  defp package do
    [
      maintainers: ["{p.author}"],
      licenses: ["{p.license}"],
      links: %{{}}
    ]
  end
end
"""


def gen_gemfile(p: PluginParams) -> str:
    """Generate Gemfile for Ruby plugins.

    Pins `rubocop` (lint) and `rspec` (test) under the right Bundler
    groups so `bundle install --without development` works in CI without
    pulling in dev tools by accident.
    """
    return f"""# frozen_string_literal: true
# Gemfile for {p.name} ({p.version}) — {p.description}
source 'https://rubygems.org'

group :development do
  gem 'rubocop', '~> 1.60'
end

group :test do
  gem 'rspec', '~> 3.13'
end
"""


def gen_pom_xml(p: PluginParams) -> str:
    """Generate pom.xml for Java plugins (Maven layout).

    Targets Java 17 (Temurin LTS, GitHub Actions default) and wires
    junit-jupiter as the test framework. checkstyle is the lint pick
    declared in the TRDD; we expose it as a Maven plugin entry rather
    than a dep so `mvn checkstyle:check` works without extra config.
    """
    group_id = f"io.github.{p.github_owner}".replace("-", "_") if p.github_owner else "com.example"
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0"
         xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
         xsi:schemaLocation="http://maven.apache.org/POM/4.0.0
                             http://maven.apache.org/xsd/maven-4.0.0.xsd">
  <modelVersion>4.0.0</modelVersion>

  <groupId>{group_id}</groupId>
  <artifactId>{p.name}</artifactId>
  <version>{p.version}</version>
  <packaging>jar</packaging>

  <name>{p.name}</name>
  <description>{p.description}</description>

  <properties>
    <maven.compiler.source>17</maven.compiler.source>
    <maven.compiler.target>17</maven.compiler.target>
    <project.build.sourceEncoding>UTF-8</project.build.sourceEncoding>
  </properties>

  <dependencies>
    <dependency>
      <groupId>org.junit.jupiter</groupId>
      <artifactId>junit-jupiter</artifactId>
      <version>5.10.2</version>
      <scope>test</scope>
    </dependency>
  </dependencies>

  <build>
    <plugins>
      <plugin>
        <groupId>org.apache.maven.plugins</groupId>
        <artifactId>maven-checkstyle-plugin</artifactId>
        <version>3.3.1</version>
      </plugin>
      <plugin>
        <groupId>org.apache.maven.plugins</groupId>
        <artifactId>maven-surefire-plugin</artifactId>
        <version>3.2.5</version>
      </plugin>
    </plugins>
  </build>
</project>
"""


def gen_build_gradle_kts(p: PluginParams) -> str:
    """Generate build.gradle.kts for Kotlin plugins (Gradle Kotlin DSL).

    Pulls in the JVM Kotlin plugin and wires detekt for lint + JUnit5
    for tests. The Kotlin version is pinned to a recent stable so the
    initial scaffold builds on a fresh JDK 17 without surprise breakage.
    """
    group = f"io.github.{p.github_owner}".replace("-", "_") if p.github_owner else "com.example"
    return f"""// build.gradle.kts for {p.name} ({p.version})
// {p.description}

plugins {{
    kotlin("jvm") version "1.9.23"
    id("io.gitlab.arturbosch.detekt") version "1.23.6"
}}

group = "{group}"
version = "{p.version}"

repositories {{
    mavenCentral()
}}

dependencies {{
    testImplementation(kotlin("test"))
    testImplementation("org.junit.jupiter:junit-jupiter:5.10.2")
}}

tasks.test {{
    useJUnitPlatform()
}}

detekt {{
    buildUponDefaultConfig = true
}}
"""


# =============================================================================
# TEMPLATE GENERATORS
# =============================================================================


def gen_plugin_json(p: PluginParams) -> str:
    """Generate .claude-plugin/plugin.json manifest content."""
    manifest = {
        "name": p.name,
        "version": p.version,
        "description": p.description,
        "author": {
            "name": p.author,
            "email": p.author_email,
        },
        "license": p.license,
        "keywords": [],
    }
    # Only include homepage/repository when github_owner is set (avoids double-slash URLs)
    if p.github_owner:
        manifest["homepage"] = p.github_url
        manifest["repository"] = p.github_url
    # TRDD-793ac32a: emit cpv.strip block when --strip-dev (default).
    # Lets the user run `cpv strip-dev-parts` later without editing
    # plugin.json — the block is the configuration the engine reads.
    if p.strip_dev:
        # PSS-style: ONE submodule per plugin (not three). Default extracts
        # tests/ only — typically the heaviest dev folder. design/ and
        # git-hooks/ are tiny (<300 KB combined) and stay in the main repo.
        # Plugin authors can add more extract entries by hand if their
        # plugin has additional heavy dev folders worth stripping.
        owner = p.github_owner or "<owner>"
        manifest["cpv"] = {
            "strip": {
                "extract": [
                    {
                        "src": "tests/",
                        "submodule": f"{owner}/{p.repo_name}-tests",
                        "submodule_path": "tests/",
                        # PSS pattern: submodule mounts at the same path
                        # as the original folder, so all references in
                        # CI / scripts / README continue to work unchanged.
                    },
                ],
                "require_url_allowlist": True,
            }
        }
    return json.dumps(manifest, indent=2) + "\n"


def gen_self_marketplace_json(p: PluginParams) -> str:
    """Generate .claude-plugin/marketplace.json with a single self-entry (Layout C)."""
    self_entry: dict[str, object] = {
        "name": p.name,
        "source": "./",
        "version": p.version,
        "description": p.description,
        "author": {
            "name": p.author,
            "email": p.author_email,
        },
        "license": p.license,
    }
    if p.github_owner:
        self_entry["homepage"] = p.github_url
        self_entry["repository"] = p.github_url
    manifest: dict[str, object] = {
        "name": p.name,
        "owner": {
            "name": p.author,
            "email": p.author_email,
        },
        "metadata": {
            "version": p.version,
            "description": p.description,
        },
        "plugins": [self_entry],
    }
    return json.dumps(manifest, indent=2) + "\n"


def gen_pyproject_toml(p: PluginParams) -> str:
    """Generate pyproject.toml with hatchling build system and ruff config."""
    return f"""[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["scripts"]

[project]
name = "{p.name}"
version = "{p.version}"
description = "{p.description}"
readme = "README.md"
requires-python = ">={p.python_version}"
dependencies = []

[project.optional-dependencies]
dev = [
    "mypy>=1.19.1",
    "pyyaml>=6.0",
    "pytest>=8.0.0",
    "pytest-cov>=4.1.0",
    # REQUIRED by ci.yml's sharded test matrix: it runs
    # `pytest --splits N --group K`, and those flags exist only when
    # pytest-split is installed (without it every shard dies with
    # "pytest: error: unrecognized arguments: --splits --group").
    # Emitted from generate_plugin_repo.PYTEST_SPLIT_REQUIREMENT, the same
    # constant that drives the matrix — do not drop one without the other.
    "{PYTEST_SPLIT_REQUIREMENT}",
    "ruff>=0.14.14",
]

[tool.ruff]
line-length = 120
# Test fixtures are deliberately-malformed sample data that exercise this
# plugin's validators/tests — ruff must never lint or format them locally
# (e.g. `ruff check scripts/ tests/`) or it would "correct" the defects the
# tests depend on. Mirrors the Mega-Linter FILTER_REGEX_EXCLUDE used in CI.
extend-exclude = ["**/fixtures", "**/testdata", "**/__fixtures__"]
# The git hooks are Python but git requires those exact EXTENSIONLESS filenames, so
# ruff's default discovery skips them and `ruff check git-hooks/` prints "No Python
# files found" followed by "All checks passed" — a VACUOUS green over the script that
# gates every push. CPV shipped a NameError in its own pre-push exactly this way. CPV's
# lint engine now reads shebangs, but a plugin's OWN ruff run (its CI, its hooks) does
# not go through CPV, so it needs this line too.
extend-include = ["git-hooks/pre-push", "git-hooks/pre-commit"]

[tool.ruff.lint]
select = ["E", "F", "W", "I"]
ignore = ["E501"]

[tool.ruff.lint.per-file-ignores]
"tests/*.py" = ["E402"]

[tool.mypy]
python_version = "{p.python_version}"
warn_return_any = true
warn_unused_configs = true

[tool.pyright]
pythonVersion = "{p.python_version}"
extraPaths = ["scripts", "tests"]
reportMissingImports = "warning"
typeCheckingMode = "basic"
"""


def gen_python_version(p: PluginParams) -> str:
    """Generate .python-version file."""
    return f"{p.python_version}\n"


def gen_gitignore(p: PluginParams) -> str:
    """Generate comprehensive .gitignore for a Claude Code plugin repo."""
    _ = p  # unused but kept for consistent signature
    return """# Python
__pycache__/
*.py[cod]
*$py.class
*.so
.Python
*.egg-info/
.eggs/
dist/
build/
.coverage
.venv/
venv/
.pytest_cache/

# Type checking
.mypy_cache/
.dmypy.json
dmypy.json

# Linting
.ruff_cache/

# IDE
.idea/
.vscode/
*.swp
*.swo

# OS
.DS_Store
Thumbs.db

# Environment
.env
.env.*

# Dev folders (NEVER PUBLISH - development artifacts only)
# Wildcard pattern catches all: docs_dev, scripts_dev, tests_dev, samples_dev,
# examples_dev, downloads_dev, libs_dev, builds_dev, etc.
*_dev/

# Agent/script reports — ALWAYS gitignored since they often contain private data
# (full paths, source snippets, API output, validation results, env metadata).
# Canonical rule: every agent/skill/script that saves a report MUST write
# under the main-repo `./reports/<component>/<YYYYMMDD_HHMMSS±HHMM>-<slug>.md`.
# Neither folder may ever be tracked. `reports_dev/` is also covered by the
# `*_dev/` rule above, listed explicitly because both entries must be present.
reports/
reports_dev/

# Node
node_modules/

# Claude Code
.claude/
llm_externalizer_output/
.tldr/

# Mega-Linter
megalinter-reports/
mega-linter.log

# Rust (remove Cargo.lock line for binary plugins)
target/
Cargo.lock
"""


def gen_readme(p: PluginParams) -> str:
    """Generate README.md with badges, installation, usage, and development sections."""
    owner = p.github_owner
    repo = p.repo_name
    # Skip badge URLs if github_owner is empty (avoids broken // in URLs)
    if owner:
        badges = (
            f"[![CI](https://github.com/{owner}/{repo}/actions/workflows/ci.yml/badge.svg)]"
            f"(https://github.com/{owner}/{repo}/actions/workflows/ci.yml)\n"
            f"[![Version](https://img.shields.io/badge/version-{p.version}-blue)]"
            f"(https://github.com/{owner}/{repo})\n"
            f"[![License](https://img.shields.io/badge/license-{p.license}-green)](LICENSE)"
        )
    else:
        badges = "<!-- Badges will appear here once github_owner is set -->"
    # Build GitHub-specific sections only when github_owner is set (avoids broken URLs)
    if owner:
        from_github = f"""### From GitHub

```bash
gh repo clone {owner}/{repo}
cd {repo}
uv venv --python {p.python_version}
source .venv/bin/activate
uv pip install -e .
```

### As a Claude Code Plugin

Add to your Claude Code configuration:

```json
{{
  "plugins": [
    "https://github.com/{owner}/{repo}"
  ]
}}
```"""
        marketplace_section = f"""## Marketplace

This plugin is available on the [{p.marketplace} marketplace](https://github.com/{owner}/{p.marketplace})."""
        author_section = f"""## Author

**{p.author}** - [GitHub](https://github.com/{owner})"""
    else:
        from_github = ""
        marketplace_section = (
            f"""## Marketplace

This plugin is available on the {p.marketplace} marketplace."""
            if p.marketplace
            else ""
        )
        author_section = f"""## Author

**{p.author}**"""

    return f"""# {p.name}

<!--BADGES-START-->
{badges}
<!--BADGES-END-->

{p.description}

## Installation

### From Marketplace

```bash
# 1. Add the marketplace (first time only)
claude plugin marketplace add {owner}/{p.marketplace if p.marketplace else repo}

# 2. Install the plugin
claude plugin install {p.name}@{p.marketplace if p.marketplace else repo}

# 3. Restart Claude Code (or run /reload-plugins) to activate
```

{from_github}

## Uninstall

```bash
claude plugin uninstall {p.name}
```

## Update

```bash
claude plugin update {p.name}@{p.marketplace if p.marketplace else repo}
```

## Troubleshooting

| Issue | Resolution |
|-------|------------|
| Plugin not appearing after install | Restart Claude Code or run `/reload-plugins` |
| Old version still showing after update | Restart Claude Code; if still stale, run `claude plugin update {p.name}` again |
| Hook path not found after update | Re-run `uv run python scripts/publish.py --install-hook` |
| `marketplace not found` error | Run `claude plugin marketplace update {p.marketplace if p.marketplace else repo}` to refresh |
| Permission denied on script | Ensure scripts are executable: `chmod +x scripts/*.py` |
| Import errors after install | Re-run `uv pip install -e .` to refresh the venv |
| Session won't pick up new hooks | Restart required — `/reload-plugins` does NOT re-read project-scoped settings.json hooks |

## Usage

```bash
# Run the plugin
uv run python scripts/main.py --help
```

## Development

### Prerequisites

- Python >= {p.python_version}
- [uv](https://docs.astral.sh/uv/) package manager

### Setup

```bash
uv venv --python {p.python_version}
source .venv/bin/activate
uv pip install -e ".[dev]"
```

### Testing

```bash
uv run pytest tests/ -v
```

### Linting

```bash
uv run ruff check --fix scripts/ tests/
uv run mypy scripts/
```

> Lint only — no formatters. `ruff check --fix` (linter autofix) is fine for
> Python/JS; never run `ruff format` / `prettier` / a markdown formatter, and
> never `markdownlint --fix`. Formatters reflow structured Markdown (skills,
> agents, docs, frontmatter, `[[wiki links]]`); fix Markdown findings by hand.

## Project Structure

```
{repo}/
├── .claude-plugin/
│   └── plugin.json          # Plugin manifest
├── .github/
│   └── workflows/           # CI/CD workflows
├── git-hooks/               # Git hooks (pre-push)
├── scripts/                 # Plugin source code
├── tests/                   # Test suite
├── pyproject.toml           # Project configuration
├── cliff.toml               # Changelog generation config
├── README.md                # This file
├── LICENSE                  # License file
└── .gitignore               # Git ignore rules
```

{marketplace_section}

## License

This project is licensed under the {p.license} License. See [LICENSE](LICENSE) for details.

{author_section}
"""


def gen_the_skills_menu_skill(p: PluginParams) -> str:
    """Generate skills/cpv-the-skills-menu/SKILL.md — the per-plugin skill catalog.

    Newly-scaffolded plugins ship with cpv-the-skills-menu method out of
    the box (TRDD-9dd64dbf). The catalog starts empty (no plugin skills
    yet) and grows as the plugin author adds skills. Agents in the new
    plugin should declare `skills: [cpv-the-skills-menu]` and load
    operational skills dynamically via the Skill() tool.
    """
    return f"""---
name: cpv-the-skills-menu
description: "Dynamic skill menu for the {p.name} plugin. Teaches agents which skills are available, when to use them, and how to load them with the Skill() tool. Use when an agent needs to pick a downstream skill at runtime. Used by every {p.name} agent via cpv-the-skills-menu method (TRDD-9dd64dbf)."
user-invocable: false
allowed-tools: Read
---

# cpv-the-skills-menu — universal {p.name} skill catalog

## Overview

This skill is the **catalog** every {p.name} agent consults to
discover operational skills at runtime. The agent preloads only this
catalog in its `skills:` frontmatter; everything else loads on demand
via the `Skill()` tool.

## Prerequisites

- The calling agent has `Skill` in its `tools:` list.
- A clear task statement so you can pick the right skill.

## Instructions

Follow these steps in order:

1. Identify the task domain.
2. Skim the **Plugin Skills** section below and pick a candidate.
3. Invoke the chosen skill via `Skill({{skill: "{p.name}:<name>"}})`
   (use the plugin namespace prefix — cross-plugin references require it).
4. Follow the loaded skill's own checklist; do NOT load another skill
   until the first one returns.
5. Surface the downstream skill's summary to the caller.

## Output

This catalog returns nothing itself — it documents invocations for
OTHER skills. The chosen downstream skill produces the actual output.

## Standalone Skills

No standalone (user/local/project-scope) skills are tracked by this
plugin's catalog yet. Add entries here as the plugin starts to
reference standalone skills outside its own namespace.

## Plugin Skills

This plugin has no operational skills yet. As you add skills to
`skills/<name>/SKILL.md`, list them here so agents can discover them:

| # | Domain | Skills |
|---|--------|--------|
| 1 | (add when you scaffold your first skill) | (e.g. `my-skill`, `other-skill`) |

All entries above are invoked as
`Skill({{skill: "{p.name}:<name>"}})`.

## Resources

- [cpv-the-skills-menu-create](../cpv-the-skills-menu-create/SKILL.md) —
  the migrator skill in the CPV plugin that can regenerate this
  catalog from the plugin's current skill inventory at any time
  (not bundled in this plugin; install
  `claude-plugins-validation` to access it).
"""


def gen_license_mit(p: PluginParams) -> str:
    """Generate MIT license text."""
    return f"""MIT License

Copyright (c) 2025 {p.author}

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""


def gen_cliff_toml(p: PluginParams) -> str:
    """Generate cliff.toml for git-cliff changelog generation."""
    # TOML uses triple-double-quotes (""") for multi-line strings, which collides
    # with Python triple-quoted strings. We inject them via a variable.
    tq = '"""\n'
    # v2.86.0 hardening (issue #22):
    # * Em-dash separator ``— `` instead of `` - `` between version and date.
    #   Matches the typographic style of the rest of CPV's docs and is the
    #   form release.yml's section-extraction awk script looks for. KEEP it:
    #   release.yml's awk extractor matches the ``## [ver] — date`` SECTION
    #   header, so reverting ``— `` to `` - `` would break release extraction.
    # * Drop ``striptags`` from the group renderer — conventional-commit
    #   group names never contain HTML and the filter just adds template
    #   surface for nothing.
    #
    # issue #144 — RESTORE the commit scope prefix + short hash on each
    # rendered commit line (the v2.86.0 "drop scope as redundant noise"
    # decision lost changelog traceability — a reader could no longer tell
    # which component a change touched or which commit it was). The scope is
    # rendered conditionally (``{% if commit.scope %}``) so an unscoped commit
    # is unaffected, and the 7-char short hash (``commit.id | truncate``) is
    # appended in parens. This is fully compatible with the em-dash awk: the
    # extractor keys on the SECTION header, NOT the per-commit line format, so
    # any commit-line shape is safe.
    body_template = (
        "{% if version %}\\\n"
        '    ## [{{ version | trim_start_matches(pat="v") }}]'
        ' — {{ timestamp | date(format="%Y-%m-%d") }}\n'
        "{% else %}\\\n"
        "    ## [Unreleased]\n"
        "{% endif %}\\\n"
        "{% for group, commits in commits | group_by(attribute="
        '"group") %}\n'
        "    ### {{ group | upper_first }}\n"
        "    {% for commit in commits %}\n"
        "        - {% if commit.scope %}**{{ commit.scope }}:** "
        "{% endif %}{{ commit.message | upper_first }}"
        '{% if commit.id %} ({{ commit.id | truncate(length=7, end="") }})'
        "{% endif %}\\\n"
        "    {% endfor %}\n"
        "{% endfor %}\n"
    )
    lines = [
        "# git-cliff configuration for changelog generation",
        "# https://git-cliff.org",
        "",
        "[changelog]",
    ]
    # Build the TOML content as a list of lines, then join
    # We handle the triple-quoted TOML strings by direct string building
    result = "\n".join(lines) + "\n"
    result += "header = " + tq
    result += "# Changelog\n\nAll notable changes to this project will be documented in this file.\n\n"
    result += tq
    result += "body = " + tq
    result += body_template
    result += tq
    result += "footer = " + tq
    result += "---\n*Generated by [git-cliff](https://git-cliff.org)*\n"
    result += tq
    result += "trim = true\n"
    result += "postprocessors = []\n"
    result += "\n"
    result += "[git]\n"
    result += "conventional_commits = true\n"
    result += "filter_unconventional = true\n"
    result += "split_commits = false\n"
    result += "commit_preprocessors = [\n"
    result += r"  { pattern = '\((\w+\s)?#([0-9]+)\)',"
    result += f' replace = "([#${{2}}](https://github.com/{p.github_owner}/{p.name}/issues/${{2}}))" }},\n'
    result += r"  { pattern = '\s+$', replace = " + '"" },\n'
    result += "]\n"
    result += "commit_parsers = [\n"
    result += '  { message = "^feat", group = "Features" },\n'
    result += '  { message = "^fix", group = "Bug Fixes" },\n'
    result += '  { message = "^doc", group = "Documentation" },\n'
    result += '  { message = "^perf", group = "Performance" },\n'
    result += '  { message = "^refactor", group = "Refactor" },\n'
    result += '  { message = "^style", group = "Styling" },\n'
    result += '  { message = "^test", group = "Testing" },\n'
    result += '  { message = "^chore\\\\(release\\\\)", skip = true },\n'
    # issue #144 — also skip a bare ``release:`` / ``release(scope):`` commit
    # so the release-tagging commit never renders as a noisy ``### Release``
    # changelog group (``release`` is not a conventional-commit type; without
    # this skip a fallback could title-case it into its own section).
    result += '  { message = "^release", skip = true },\n'
    result += '  { message = "^chore\\\\(deps\\\\)", skip = true },\n'
    result += '  { message = "^chore\\\\(pr\\\\)", skip = true },\n'
    result += '  { message = "^chore\\\\(pull\\\\)", skip = true },\n'
    result += '  { message = "^chore|^ci", group = "Miscellaneous Tasks" },\n'
    result += '  { body = ".*security", group = "Security" },\n'
    result += '  { message = "^revert", group = "Revert" },\n'
    result += "]\n"
    result += "protect_breaking_commits = false\n"
    result += "filter_commits = false\n"
    result += 'tag_pattern = "v[0-9].*"\n'
    result += 'skip_tags = ""\n'
    result += 'ignore_tags = ""\n'
    result += "topo_order = false\n"
    result += 'sort_commits = "oldest"\n'
    return result


def gen_publish_py(p: PluginParams, profile: str = PROFILE_STANDARD) -> str:
    """Generate scripts/publish.py — unified publish pipeline with --gate mode.

    The CPV validator/branch-rule callsites embedded in the publish.py body
    are PINNED to ``p.cpv_ref_resolved`` (root-cause #2): the raw template
    carries the bare ``git+https://…/claude-plugins-validation`` URL, and the
    three occurrences are rewritten to ``…@<ref>`` after the template is built.
    Pinning keeps the cold ``uvx --from git+…`` build cached per-tag and stops
    a future stricter CPV release from breaking this plugin's gate with no
    plugin change.

    ``profile`` (TRDD-e9f13df1, issues #128 / #115) selects the publish-pipeline
    VARIANT:

      * ``standard`` / ``remote-validation`` — the body is byte-identical to
        the historical output (the standard template + the ref rewrite). This
        is a HARD guarantee: existing tests assert the exact standard bytes, and
        every unrecognized / unknown ``profile`` value also takes this path
        (fail-safe). The remote-validation gate shape lives in the standard
        template already (G3/Stage-4 drive ``cpv-remote-validate``), so it
        needs no separate variant here.
      * ``submodule-build`` (PSS, #128) — the standard body PLUS an additive,
        marker-delimited section carrying the four load-bearing submodule
        behaviors: (1) submodule-commit-before-gitlink, (2)
        submodule-push-before-parent, (3) a gitlink-tolerant clean-tree
        preflight, and (4) the #128 source-change-detection FIX (detect a
        source change with ``git -C <submodule> diff <tag/sha> -- <src-globs>``,
        NOT a parent-repo ``*.rs`` glob — the parent only ever sees the
        ``160000`` gitlink, so the standard preflight concludes "no source
        change" and ships STALE binaries). The section is PURELY ADDITIVE: it
        is appended after the standard body, so for every other profile the
        return value is unchanged.

    The branch is additive by construction so the standard path can never
    regress: the standard ``result`` is built first and returned untouched for
    all non-``submodule-build`` profiles.
    """
    template = r'''#!/usr/bin/env python3
"""Unified publish pipeline: bypass-guard -> lint -> validate (remote CPV) -> test -> bump -> badge -> changelog -> commit -> push -> release.

Modes:
  --gate                  Pre-push gate: orchestrator check + lint (ruff/jscpd/
                          actionlint/mypy) + validate + tests only (no bump/push).
                          Called by git-hooks/pre-push automatically.
  --install-hook          Install git-hooks/pre-push into .git/hooks/ and set core.hooksPath.
  --install-branch-rules  Apply the cpv-branch-rules GitHub ruleset to the origin
                          (server-side CI enforcement — run once after first push).
  (no flag)               Full release pipeline (11 stages, fail-fast). The bump type
                          is AUTO-DETECTED via `git-cliff --bumped-version` from the
                          conventional commits on HEAD.
  --patch/--minor/--major Force a specific bump type (overrides auto-detection).

Pipeline stages (all fail-fast — any non-zero exit aborts):
   0. Bypass guard — reject CPV_SKIP_*, SKIP_*, NO_VERIFY env vars
   1. Check working tree is clean
   2. Lint files (ruff)
   3. Validate plugin (uvx cpv-remote-validate plugin . --strict — fetches
      the canonical CPV validator from GitHub so this plugin never vendors
      a local copy and never drifts from upstream rules)
   4. Run tests (pytest)
  4b. CI-parity preflight (uvx cpv-remote-validate ci-preflight . — the
      jscpd / actionlint / mypy / uv-sync-dev / Mega-Linter / static-CIP gates
      that CI's Lint job runs but `validate_plugin --strict` does NOT). Runs
      BEFORE the bump/commit/tag/push, so a pipeline defect can never leave a
      half-published state. A MISSING local tool degrades to a WARNING and never
      blocks the publish.
   5. Marketplace-registration check (Layout A: notify workflow + PAT secret +
      remote marketplace.json registration + remote receiver workflow;
      Layout B: must run from marketplace root + nested plugin must be listed)
   6. Check version consistency across all sources
   7. Bump version in plugin.json, pyproject.toml, and __version__ vars
   8. Update README version badge
   9. Generate changelog (git-cliff)
  10. Commit, tag, push
  11. Create GitHub release (gh CLI)

Gate stages (--gate mode, called by pre-push hook):
   G0. Orchestrator check — direct `git push` is blocked; only publish.py
       may initiate a push (verified via process ancestry, NOT env vars).
   G1. Version bump check (local vs remote, auto-detects origin/HEAD)
   G2. Lint (ruff)
   G2b. Copy-paste check (jscpd, parity with ci.yml Mega-Linter COPYPASTE_JSCPD;
        WARNs+skips if jscpd/npx unavailable so a push is never false-blocked)
   G2c. Workflow lint (actionlint, parity with ci.yml Lint job; WARNs+skips if
        actionlint unavailable so a push is never false-blocked)
   G2d. Type-check (mypy scripts/ --ignore-missing-imports, parity with ci.yml
        Lint job; WARNs+skips if mypy unavailable so a push is never false-blocked)
   G2e. Compiled-component build gates (cargo clippy+test / go vet+build+test /
        dotnet build / swift build / zig build), each self-detecting; WARNs+skips
        if the toolchain is unavailable so a push is never false-blocked. C/C++ is
        detected + noted (built in CI; no false-block-safe local command) (issue #175)
   G2f. Shell lint (shellcheck, parity with ci.yml Mega-Linter BASH_SHELLCHECK;
        WARNs+skips if shellcheck unavailable so a push is never false-blocked)
   G3. Validate (uvx cpv-remote-validate plugin . --strict)
   G4. Tests (pytest)

Usage:
    uv run python scripts/publish.py                      # auto-bump from git-cliff
    uv run python scripts/publish.py --gate
    uv run python scripts/publish.py --install-hook
    uv run python scripts/publish.py --install-branch-rules
    uv run python scripts/publish.py --patch              # force patch
    uv run python scripts/publish.py --minor              # force minor
    uv run python scripts/publish.py --major              # force major
    uv run python scripts/publish.py --dry-run            # preview (auto-bump)

Cornerstone rule: a plugin CANNOT be pushed unless validation passes with
0 issues (WARNING allowed). There are no exceptions and no bypass flags.
Every push is blocked unless scripts/publish.py orchestrates it end-to-end
AND stage_validate / stage_tests / stage_lint all succeed.
"""

import argparse
import json
import os
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path

# Load gh / git retry wrappers from the sibling module so every push +
# `gh release create` survives transient github.com hiccups (the retry
# pattern from ~/.claude/rules/github-timeouts.md). Shipped verbatim
# from the canonical CPV install via gen_cpv_network_resilience_py().
sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    # `pyright: ignore[reportAssignmentType]` (on the import line itself):
    # the typed real import and the ImportError fallback shims below are
    # conditional variants of the same names; Pyright flags the typed import as
    # not assignable to the fallback's loose declared type (its mypy counterpart
    # is the [no-redef, misc] on the shims). Suppress exactly that — the
    # standard import-fallback idiom. issue #151.
    from cpv_network_resilience import gh_with_retry, git_with_retry  # pyright: ignore[reportAssignmentType]
except ImportError:
    # Fallback: scripts/cpv_network_resilience.py was not shipped with this
    # plugin (older scaffold). Define no-op shims so publish.py still works,
    # but warn so the user knows to refresh via `cpv standardize --force-templates`.
    print(
        "[publish.py] WARNING: scripts/cpv_network_resilience.py missing — "
        "network calls will not auto-retry on transient errors. "
        "Run `cpv standardize --force-templates` to refresh.",
        file=sys.stderr,
    )
    # `misc` is needed alongside `no-redef`: under `mypy --strict` the typed
    # real import (cpv_network_resilience) and these minimal fallback shims are
    # conditional variants of the same name with NON-IDENTICAL signatures, which
    # `--strict` reports as [misc] ("All conditional function variants must have
    # identical signatures"); the combined code suppresses exactly that, the
    # standard import-fallback idiom (cf. the tomli fallback at
    # cpv_lint_engine.py with [no-redef,import-not-found]).
    def gh_with_retry(cmd, **kwargs):  # type: ignore[no-redef, misc]
        kwargs.pop("max_attempts", None)
        kwargs.pop("backoff", None)
        kwargs.setdefault("check", True)
        kwargs.setdefault("capture_output", False)
        return subprocess.run(cmd, **kwargs)
    def git_with_retry(cmd, **kwargs):  # type: ignore[no-redef, misc]
        kwargs.pop("max_attempts", None)
        kwargs.pop("backoff", None)
        kwargs.setdefault("check", True)
        kwargs.setdefault("capture_output", False)
        return subprocess.run(cmd, **kwargs)

# -- ANSI colors ---------------------------------------------------------------


def _colors_ok() -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    return hasattr(sys.stdout, "isatty") and sys.stdout.isatty()


_C = _colors_ok()
RED    = "\033[0;31m" if _C else ""
GREEN  = "\033[0;32m" if _C else ""
YELLOW = "\033[1;33m" if _C else ""
BLUE   = "\033[0;34m" if _C else ""
BOLD   = "\033[1m" if _C else ""
DIM    = "\033[2m" if _C else ""
NC     = "\033[0m" if _C else ""


# -- Helpers -------------------------------------------------------------------


def cprint(msg: str) -> None:
    print(msg, flush=True)

# Wall-clock bound for the TEST SUITE specifically (issue #179). `run()`'s 300s
# default is sized for a lint/scan invocation; a real suite is minutes, so
# inheriting that default made gate G4 UNSATISFIABLE for any plugin whose tests
# run longer — a 13,618-test suite reached 47% at the cap and the gate killed its
# own run. A cap the suite cannot finish inside does not make the gate stricter,
# it makes it unprovable, and a timeout is indistinguishable from a hang. The
# bound is overridable so the NEXT larger suite does not have to patch the
# template — a fixed bound is exactly the defect being fixed here, and replacing
# 300 with a bigger constant would only move the cliff.
_TEST_SUITE_TIMEOUT_ENV = "PLUGIN_TEST_SUITE_TIMEOUT"
_DEFAULT_TEST_SUITE_TIMEOUT = 1800.0


def _test_suite_timeout() -> float:
    """Seconds allowed for the pytest gate; the env override wins when positive.

    An empty, zero, negative, or unparseable value falls back to the default.
    That asymmetry is deliberate: a typo must never SHORTEN the bound, because a
    near-zero ceiling would re-create the unsatisfiable gate this constant exists
    to remove.
    """
    raw = os.environ.get(_TEST_SUITE_TIMEOUT_ENV, "").strip()
    if not raw:
        return _DEFAULT_TEST_SUITE_TIMEOUT
    try:
        override = float(raw)
    except ValueError:
        return _DEFAULT_TEST_SUITE_TIMEOUT
    return override if override > 0 else _DEFAULT_TEST_SUITE_TIMEOUT


def run(
    cmd: list[str], cwd: Path | None = None, *, check: bool = True, capture: bool = False,
    timeout: float = 300,
) -> subprocess.CompletedProcess[str]:
    """Run a command, stream output, fail-fast on error.

    `timeout` stays at 300s by default — the right bound for the lint/scan steps
    that make up almost every call site, and the reason a hung one fails fast.
    Callers whose work is legitimately longer pass their own; see
    `_test_suite_timeout` for the test gate.
    """
    cprint(f"  {BLUE}$ {' '.join(cmd)}{NC}")
    # A subprocess exceeding `timeout` raises TimeoutExpired; without this it
    # would die with a raw traceback instead of the styled fail-fast message
    # every other failure path uses. Catch it and exit 1.
    try:
        result = subprocess.run(cmd, cwd=str(cwd) if cwd else None, text=True,
                                capture_output=capture, timeout=timeout)
    except subprocess.TimeoutExpired:
        # Report the ACTUAL bound: a hardcoded "300s" starts lying the moment any
        # caller overrides it, and a wrong number here sends triage the wrong way.
        cprint(f"  {RED}Command timed out after {timeout:g}s: {' '.join(cmd)}{NC}")
        sys.exit(1)
    if check and result.returncode != 0:
        cprint(f"  {RED}Command failed (exit {result.returncode}){NC}")
        sys.exit(result.returncode)
    return result

def get_repo_root() -> Path:
    r = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                       capture_output=True, text=True, check=True)
    return Path(r.stdout.strip())


# -- gh-auth precheck (TRDD-bbff5bc5) ---------------------------------------


def _parse_owner_repo_from_remote(remote_url: str) -> tuple[str, str] | None:
    """Extract (owner, repo) from `git@host:owner/repo.git` or
    `https://host/owner/repo[.git]`. Returns None on unparseable input.
    """
    if not remote_url:
        return None
    url = remote_url.strip().rstrip("/")
    if url.endswith(".git"):
        url = url[:-4]
    match = re.search(r"[:/]([^:/\s]+)/([^/\s]+)$", url)
    if not match:
        return None
    return match.group(1), match.group(2)


def _resolve_owner_repo(plugin_root: Path) -> tuple[str, str]:
    """Read remote.origin.url, parse (owner, repo). Exit 1 on failure."""
    result = subprocess.run(
        ["git", "config", "--get", "remote.origin.url"],
        cwd=str(plugin_root), capture_output=True, text=True, timeout=10, check=False,
    )
    if result.returncode != 0 or not result.stdout.strip():
        cprint(f"  {RED}Could not read remote.origin.url. Run: git remote add origin <url>{NC}")
        sys.exit(1)
    parsed = _parse_owner_repo_from_remote(result.stdout.strip())
    if parsed is None:
        cprint(f"  {RED}Could not parse owner/repo from remote URL: {result.stdout.strip()!r}{NC}")
        sys.exit(1)
    return parsed


def _ensure_gh_auth(owner: str, repo: str) -> None:
    """Verify gh CLI installed + authenticated + push perm on owner/repo.

    Called BEFORE every push gate. Exits 1 on any of: gh missing, not
    authed, no push permission. Per TRDD-bbff5bc5 §4.1: never invokes
    `gh auth token`; uses only `gh auth status` and `gh api` so PAT-shaped
    strings cannot leak to stdout/stderr.
    """
    if os.environ.get("CPV_SKIP_GH_AUTH_CHECK") == "1":
        return
    gh_bin = shutil.which("gh")
    if gh_bin is None:
        cprint(f"  {RED}gh CLI not installed. Install: brew install gh{NC}")
        sys.exit(1)
    # 60s timeout (was 15s) — slow-link tolerance; downstream push gates
    # still enforce real auth on failure.
    try:
        status = subprocess.run(
            [gh_bin, "auth", "status"],
            capture_output=True, text=True, timeout=60, check=False,
        )
    except subprocess.TimeoutExpired:
        cprint(f"  {RED}gh auth status timed out after 60 s — flaky network. Retry, or set CPV_SKIP_GH_AUTH_CHECK=1.{NC}")
        sys.exit(1)
    if status.returncode != 0:
        cprint(f"  {RED}gh CLI not authenticated.{NC}")
        cprint(f"  {YELLOW}Run: gh auth login --hostname github.com --git-protocol https{NC}")
        sys.exit(1)
    try:
        perms = subprocess.run(
            [gh_bin, "api", f"repos/{owner}/{repo}", "--jq", ".permissions.push"],
            capture_output=True, text=True, timeout=60, check=False,
        )
    except subprocess.TimeoutExpired:
        cprint(f"  {RED}gh permission check timed out after 60 s — set CPV_SKIP_GH_AUTH_CHECK=1 to bypass this gate.{NC}")
        sys.exit(1)
    if perms.returncode != 0 or perms.stdout.strip() != "true":
        active_login = ""
        for line in (status.stdout + status.stderr).splitlines():
            line = line.strip()
            if "account " in line and ("Logged in" in line or "Active" in line):
                m = re.search(r"account\s+(\S+)", line)
                if m:
                    active_login = m.group(1)
                    break
        login_str = f" '{active_login}'" if active_login else ""
        cprint(f"  {RED}gh user{login_str} has no push permission on {owner}/{repo}.{NC}")
        cprint(f"  {YELLOW}Diagnose:{NC}")
        cprint(f"  {YELLOW}  1. Ask the repo owner to add you as a collaborator with write access.{NC}")
        cprint(f"  {YELLOW}  2. If you have multiple gh accounts: gh auth status; gh auth switch{NC}")
        cprint(f"  {YELLOW}  3. If using a fine-grained token: ensure 'Contents: write' on this repo.{NC}")
        sys.exit(1)


# -- Semver --------------------------------------------------------------------

def parse_semver(version: str) -> tuple[int, int, int] | None:
    """Parse 'X.Y.Z' into (major, minor, patch)."""
    m = re.match(r"^(\d+)\.(\d+)\.(\d+)$", version.strip())
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)), int(m.group(3))

def bump_semver(current: str, bump_type: str) -> str | None:
    """Bump version by major/minor/patch. Returns new version string or None."""
    parsed = parse_semver(current)
    if not parsed:
        return None
    major, minor, patch = parsed
    if bump_type == "major":
        return f"{major + 1}.0.0"
    elif bump_type == "minor":
        return f"{major}.{minor + 1}.0"
    elif bump_type == "patch":
        return f"{major}.{minor}.{patch + 1}"
    return None


# -- Version readers/writers ---------------------------------------------------

def get_current_version(plugin_root: Path) -> str | None:
    """Read version from .claude-plugin/plugin.json."""
    pj = plugin_root / ".claude-plugin" / "plugin.json"
    if not pj.is_file():
        return None
    try:
        data = json.loads(pj.read_text(encoding="utf-8"))
        ver = data.get("version")
        return str(ver) if ver is not None else None
    except (json.JSONDecodeError, OSError):
        return None

def update_plugin_json(root: Path, new_ver: str) -> tuple[bool, str]:
    """Write version to .claude-plugin/plugin.json."""
    pj = root / ".claude-plugin" / "plugin.json"
    if not pj.is_file():
        return False, "plugin.json not found"
    try:
        data = json.loads(pj.read_text(encoding="utf-8"))
        data["version"] = new_ver
        pj.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        return True, f"plugin.json -> {new_ver}"
    except (json.JSONDecodeError, OSError) as e:
        return False, f"plugin.json update failed: {e}"

def update_self_marketplace_json(root: Path, new_ver: str) -> tuple[bool, str]:
    """Write version to .claude-plugin/marketplace.json (Layout C — both metadata and self-entry)."""
    mp = root / ".claude-plugin" / "marketplace.json"
    if not mp.is_file():
        return False, "no marketplace.json (not Layout C)"
    try:
        data = json.loads(mp.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        return False, f"marketplace.json read failed: {e}"
    # Bump metadata.version if present
    metadata = data.get("metadata")
    if isinstance(metadata, dict):
        metadata["version"] = new_ver
    # Bump the self-entry's version (the entry whose name matches plugin.json's name AND source is "./")
    plugin_json_path = root / ".claude-plugin" / "plugin.json"
    plugin_name: str | None = None
    if plugin_json_path.is_file():
        try:
            pdata = json.loads(plugin_json_path.read_text(encoding="utf-8"))
            plugin_name = pdata.get("name")
        except (json.JSONDecodeError, OSError):
            plugin_name = None
    plugins = data.get("plugins")
    bumped_entry = False
    if isinstance(plugins, list):
        for entry in plugins:
            if not isinstance(entry, dict):
                continue
            entry_name = entry.get("name")
            entry_source = entry.get("source")
            is_self = (
                (entry_name == plugin_name or plugin_name is None)
                and entry_source in ("./", {"source": "directory", "path": "./"})
            )
            if is_self:
                entry["version"] = new_ver
                bumped_entry = True
                break
    try:
        mp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    except OSError as e:
        return False, f"marketplace.json write failed: {e}"
    if bumped_entry:
        return True, f"marketplace.json (metadata + self-entry) -> {new_ver}"
    return True, f"marketplace.json (metadata only — no self-entry matched) -> {new_ver}"

def _project_block(content: str) -> tuple[int, int] | None:
    """Char span of the [project] table body, or None if absent.

    The project version lives in the [project] table. A whole-file first-match
    for `version = "..."` writes the WRONG version when a [tool.X] table with
    its own top-level `version` (e.g. [tool.commitizen]) precedes [project].
    When there is no [project] table (poetry keeps it under [tool.poetry]),
    return None so the caller falls back to the legacy whole-file first-match.
    """
    m = re.search(r'^\[project\]\s*$', content, re.MULTILINE)
    if not m:
        return None
    start = m.end()
    nxt = re.search(r'^\[', content[start:], re.MULTILINE)
    return start, (start + nxt.start() if nxt else len(content))

def update_pyproject_toml(root: Path, new_ver: str) -> tuple[bool, str]:
    """Write version to pyproject.toml."""
    pp = root / "pyproject.toml"
    if not pp.is_file():
        return False, "pyproject.toml not found"
    try:
        content = pp.read_text(encoding="utf-8")
        block = _project_block(content)
        if block is not None:
            lo, hi = block
            replaced = re.sub(
                r'^(version\s*=\s*")[^"]*(")',
                rf'\g<1>{new_ver}\2',
                content[lo:hi],
                count=1,
                flags=re.MULTILINE,
            )
            updated = content[:lo] + replaced + content[hi:]
        else:
            updated = re.sub(
                r'^(version\s*=\s*")[^"]*(")',
                rf'\g<1>{new_ver}\2',
                content,
                count=1,
                flags=re.MULTILINE,
            )
        if updated == content:
            return False, "pyproject.toml: version field not found"
        pp.write_text(updated, encoding="utf-8")
        return True, f"pyproject.toml -> {new_ver}"
    except OSError as e:
        return False, f"pyproject.toml update failed: {e}"

def update_python_versions(root: Path, new_ver: str) -> list[tuple[bool, str]]:
    """Update __version__ = '...' in all .py files under scripts/."""
    results: list[tuple[bool, str]] = []
    scripts_dir = root / "scripts"
    if not scripts_dir.is_dir():
        return results
    pattern = re.compile(r'^(__version__\s*=\s*["\'])([^"\']*)(["\']\s*)$', re.MULTILINE)
    for py_file in scripts_dir.rglob("*.py"):
        try:
            content = py_file.read_text(encoding="utf-8")
        except OSError:
            continue
        if not pattern.search(content):
            continue
        updated = pattern.sub(rf"\g<1>{new_ver}\3", content)
        if updated != content:
            py_file.write_text(updated, encoding="utf-8")
            results.append((True, f"{py_file.relative_to(root)} -> {new_ver}"))
    return results

def check_version_consistency(root: Path) -> tuple[bool, str]:
    """Verify all version sources match. Includes marketplace.json metadata
    and self-entry (Layout C) when present."""
    versions: dict[str, str | None] = {}

    # plugin.json
    pj = root / ".claude-plugin" / "plugin.json"
    if pj.is_file():
        try:
            versions["plugin.json"] = json.loads(pj.read_text(encoding="utf-8")).get("version")
        except (json.JSONDecodeError, OSError):
            versions["plugin.json"] = None

    # marketplace.json (Layout C) — both metadata.version and the self-entry's version
    mp = root / ".claude-plugin" / "marketplace.json"
    if mp.is_file():
        try:
            mp_data = json.loads(mp.read_text(encoding="utf-8"))
            md = mp_data.get("metadata")
            if isinstance(md, dict):
                versions["marketplace.json:metadata"] = md.get("version")
            plugins_arr = mp_data.get("plugins")
            if isinstance(plugins_arr, list):
                for entry in plugins_arr:
                    if not isinstance(entry, dict):
                        continue
                    src = entry.get("source")
                    if src == "./" or (
                        isinstance(src, dict) and src.get("source") == "directory" and src.get("path") == "./"
                    ):
                        versions["marketplace.json:self-entry"] = entry.get("version")
                        break
        except (json.JSONDecodeError, OSError):
            versions["marketplace.json"] = None

    # pyproject.toml — read from the [project] table body when present, else
    # fall back to the whole-file first-match (poetry-style layouts).
    pp = root / "pyproject.toml"
    if pp.is_file():
        pp_text = pp.read_text(encoding="utf-8")
        blk = _project_block(pp_text)
        hay = pp_text[blk[0]:blk[1]] if blk is not None else pp_text
        m = re.search(r'^version\s*=\s*"([^"]*)"', hay, re.MULTILINE)
        versions["pyproject.toml"] = m.group(1) if m else None

    found = {k: v for k, v in versions.items() if v is not None}
    if not found:
        return False, "No version sources found"
    unique = set(found.values())
    if len(unique) == 1:
        return True, f"All versions match: {unique.pop()}"
    details = ", ".join(f"{k}={v}" for k, v in found.items())
    return False, f"Version mismatch: {details}"

def _sync_uv_lock(root: Path) -> None:
    """Re-resolve ``uv.lock`` against the freshly-bumped ``pyproject.toml``.

    Without this, every release leaves ``uv.lock`` stale by one version
    (``pyproject.toml`` says e.g. ``2.66.2`` but ``uv.lock`` still pins the
    root package at ``2.66.1``). The NEXT publish then runs an outer
    ``uv run``/``uv lock``/``uv sync`` which re-syncs that single root-version
    line in place, DIRTYING the working tree — and Gate 1 (clean-tree check)
    aborts that publish before it does anything (issue #149). Co-locating the
    sync in do_bump (the only place pyproject.toml is written) guarantees the
    lock can never be stale after a successful bump. Idempotent; silently
    skipped when neither ``uv`` nor ``uv.lock`` is present (plugins authored
    without uv, or a host where uv isn't installed). ``check=False`` so a uv
    hiccup degrades to a no-op instead of aborting the bump.
    """
    if not (root / "uv.lock").is_file():
        return
    if shutil.which("uv") is None:
        return
    run(["uv", "lock"], root, check=False)

def do_bump(root: Path, new_ver: str, dry_run: bool = False) -> bool:
    """Orchestrate all version updates. Detects Layout C (marketplace.json at repo root)
    and bumps both manifests atomically when present."""
    cprint(f"\n{BOLD}Bumping to {new_ver}{' (dry-run)' if dry_run else ''}{NC}")

    is_layout_c = (root / ".claude-plugin" / "marketplace.json").is_file()

    if dry_run:
        cprint(f"  Would update plugin.json -> {new_ver}")
        if is_layout_c:
            cprint(f"  Would update marketplace.json (metadata + self-entry, Layout C) -> {new_ver}")
        cprint(f"  Would update pyproject.toml -> {new_ver}")
        cprint(f"  Would update __version__ vars -> {new_ver}")
        return True

    ok1, msg1 = update_plugin_json(root, new_ver)
    cprint(f"  {'OK' if ok1 else 'FAIL'}: {msg1}")

    ok_mp = True
    if is_layout_c:
        ok_mp, msg_mp = update_self_marketplace_json(root, new_ver)
        cprint(f"  {'OK' if ok_mp else 'FAIL'}: {msg_mp}")

    ok2, msg2 = update_pyproject_toml(root, new_ver)
    cprint(f"  {'OK' if ok2 else 'FAIL'}: {msg2}")

    py_results = update_python_versions(root, new_ver)
    for ok, msg in py_results:
        cprint(f"  {'OK' if ok else 'FAIL'}: {msg}")

    ok = ok1 and ok2 and ok_mp
    if ok:
        # Bump succeeded — bring uv.lock's root version along so the
        # post-publish tree is clean and the NEXT publish's outer `uv run`
        # doesn't re-sync uv.lock and trip Gate 1 (issue #149).
        _sync_uv_lock(root)
    return ok


# -- Hook installer ------------------------------------------------------------

def install_hook(root: Path) -> int:
    """Copy git-hooks/pre-push to .git/hooks/pre-push and set core.hooksPath."""
    cprint(f"\\n{BOLD}Installing git hooks...{NC}")
    source = root / "git-hooks" / "pre-push"
    if not source.is_file():
        cprint(f"  {RED}git-hooks/pre-push not found{NC}")
        return 1
    git_dir = root / ".git"
    if not git_dir.is_dir():
        cprint(f"  {RED}.git/ not found — is this a git repository?{NC}")
        return 1
    hooks_dir = git_dir / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    dest = hooks_dir / "pre-push"
    shutil.copy2(source, dest)
    dest.chmod(dest.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    cprint(f"  {GREEN}Installed: git-hooks/pre-push -> .git/hooks/pre-push{NC}")
    # Also set core.hooksPath so git finds hooks in git-hooks/ directly
    subprocess.run(["git", "config", "core.hooksPath", "git-hooks"],
                   cwd=str(root), check=False)
    cprint(f"  {GREEN}Set git config core.hooksPath = git-hooks{NC}")
    return 0


def _get_origin_slug(root: Path) -> str | None:
    """Return OWNER/REPO parsed from the current repo's origin remote, or None."""
    try:
        r = subprocess.run(
            ["git", "config", "--get", "remote.origin.url"],
            capture_output=True, text=True, cwd=str(root), check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0 or not r.stdout.strip():
        return None
    url = r.stdout.strip()
    # Handle git@github.com:OWNER/REPO.git and https://github.com/OWNER/REPO.git
    if url.startswith("git@"):
        _, _, path = url.partition(":")
    elif "//" in url:
        _, _, path = url.partition("//")
        # path is now "github.com/OWNER/REPO.git"
        path = path.split("/", 1)[1] if "/" in path else ""
    else:
        return None
    if path.endswith(".git"):
        path = path[:-4]
    parts = path.strip("/").split("/")
    if len(parts) != 2 or not parts[0] or not parts[1]:
        return None
    return f"{parts[0]}/{parts[1]}"


def install_branch_rules(root: Path) -> int:
    """Apply the cpv-branch-rules ruleset to the repo's GitHub origin.

    Auto-detects the OWNER/REPO slug from `git config remote.origin.url` and
    shells out to `uvx cpv-setup-branch-rules` so downstream plugins do not
    need to vendor setup_branch_rules.py locally. This is the server-side
    gate that enforces CI as a required status check — the local pre-push
    hook alone is bypassable with `git push --no-verify`, but a ruleset is
    enforced by GitHub itself.
    """
    cprint(f"\\n{BOLD}Installing branch-protection ruleset...{NC}")
    slug = _get_origin_slug(root)
    if slug is None:
        cprint(f"  {RED}Could not read origin remote URL — skipping.{NC}")
        cprint(f"  {YELLOW}Set `git remote add origin <url>` first, then retry.{NC}")
        return 1
    cprint(f"  Target repo: {slug}")
    try:
        r = subprocess.run(
            [
                "uvx",
                "--from",
                "git+https://github.com/Emasoft/claude-plugins-validation",
                "--with",
                "pyyaml",
                "cpv-setup-branch-rules",
                slug,
            ],
            cwd=str(root),
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        cprint(f"  {RED}uvx call failed: {exc}{NC}")
        return 1
    if r.returncode != 0:
        cprint(f"  {RED}cpv-setup-branch-rules exited with code {r.returncode}{NC}")
        return r.returncode
    cprint(f"  {GREEN}Branch rules applied to {slug}.{NC}")
    return 0


# -- Gate mode (pre-push quality checks) --------------------------------------

def _get_process_ancestry(max_depth: int = 30) -> list[tuple[int, str]]:
    """Walk parent processes via ps(1). Returns [(pid, cmdline), ...] closest-first.

    Used by the orchestrator check to verify that scripts/publish.py is an
    ancestor of the current pre-push gate invocation. Process ancestry is
    non-spoofable (unlike env vars, which a user could set with
    `CPV_PIPELINE=1 git push`).
    """
    ancestry: list[tuple[int, str]] = []
    pid = os.getpid()
    seen: set[int] = set()
    for _ in range(max_depth):
        if pid in seen or pid <= 0:
            break
        seen.add(pid)
        try:
            r = subprocess.run(
                ["ps", "-p", str(pid), "-o", "ppid=,args="],
                capture_output=True, text=True, timeout=5,
            )
        except (OSError, subprocess.SubprocessError):
            return []
        if r.returncode != 0:
            break
        line = r.stdout.strip()
        if not line:
            break
        parts = line.split(None, 1)
        if not parts:
            break
        try:
            ppid = int(parts[0])
        except ValueError:
            break
        cmdline = parts[1] if len(parts) > 1 else ""
        ancestry.append((pid, cmdline))
        if ppid <= 1:
            break
        pid = ppid
    return ancestry


def _called_by_publish_orchestrator(root: Path) -> bool:
    """Verify that scripts/publish.py (in publish mode, NOT --gate) is an ancestor.

    Expected chain for an orchestrated push:
        publish.py --patch|--minor|--major   (orchestrator)
          └─ git push
              └─ git (runs pre-push hook)
                  └─ sh (hook script)
                      └─ publish.py --gate   (this process)

    Walk the parent chain. At least one ancestor must be scripts/publish.py
    WITHOUT the --gate flag (that is, a publish orchestrator — not our own
    gate-mode re-entry).
    """
    expected_abs = str((root / "scripts" / "publish.py").resolve())
    expected_rel = "scripts/publish.py"
    for _pid, cmdline in _get_process_ancestry():
        if "publish.py" not in cmdline:
            continue
        if "--gate" in cmdline:
            continue
        if expected_abs in cmdline or expected_rel in cmdline:
            return True
    return False


def run_gate(root: Path) -> int:
    """Pre-push gate: blocks on any quality issue. Returns 0 if clean."""
    cprint(f"\n{BOLD}Pre-push gate checks{NC}\n")

    # Gate 0: Orchestrator check — only publish.py may trigger a push.
    # Prevents a user from running `git push` directly and bypassing the
    # version-bump / changelog / tag / release pipeline. Uses process
    # ancestry (non-spoofable), NOT env vars.
    cprint(f"{BLUE}[G0] Checking push orchestrator...{NC}")
    if not _called_by_publish_orchestrator(root):
        cprint("")
        cprint(f"  {RED}========================================{NC}")
        cprint(f"  {RED}  BLOCKED: Direct push not allowed{NC}")
        cprint(f"  {RED}  This pre-push hook only accepts pushes{NC}")
        cprint(f"  {RED}  initiated by scripts/publish.py.{NC}")
        cprint(f"  {RED}  Run one of:{NC}")
        cprint(f"  {RED}    uv run python scripts/publish.py --patch{NC}")
        cprint(f"  {RED}    uv run python scripts/publish.py --minor{NC}")
        cprint(f"  {RED}    uv run python scripts/publish.py --major{NC}")
        cprint(f"  {RED}========================================{NC}")
        return 1
    cprint(f"  {GREEN}Orchestrated by publish.py.{NC}")

    # Gate 1: Version bump check — local vs remote
    # Resolves origin/HEAD dynamically so the gate works on both `main` and
    # `master` default branches (and any other name). If none of the
    # candidates return a remote plugin.json, it's a first push and we allow.
    cprint(f"\n{BLUE}[G1] Checking version bump...{NC}")
    local_ver = get_current_version(root)
    if local_ver:
        # Try origin/HEAD first (most reliable), then explicit main/master
        candidates: list[str] = []
        try:
            sym = subprocess.run(
                ["git", "symbolic-ref", "refs/remotes/origin/HEAD"],
                capture_output=True, text=True, cwd=str(root), timeout=10,
            )
            if sym.returncode == 0 and sym.stdout.strip():
                # Output looks like "refs/remotes/origin/main"
                branch = sym.stdout.strip().split("/")[-1]
                candidates.append(f"origin/{branch}")
        except (OSError, subprocess.SubprocessError):
            pass
        for fallback in ("origin/main", "origin/master"):
            if fallback not in candidates:
                candidates.append(fallback)
        remote_ver: str | None = None
        matched_ref: str | None = None
        for ref in candidates:
            try:
                r = subprocess.run(
                    ["git", "show", f"{ref}:.claude-plugin/plugin.json"],
                    capture_output=True, text=True, cwd=str(root), timeout=10,
                )
            except (OSError, subprocess.SubprocessError):
                continue
            if r.returncode == 0 and r.stdout:
                try:
                    data = json.loads(r.stdout)
                    rv = data.get("version")
                    if isinstance(rv, str):
                        remote_ver = rv
                        matched_ref = ref
                        break
                except json.JSONDecodeError:
                    continue
        if remote_ver is None:
            cprint(f"  {YELLOW}No remote plugin.json found (first push?) — skipping version-bump check.{NC}")
        elif local_ver == remote_ver:
            cprint(f"  {RED}BLOCKED: Version not bumped — local {local_ver} == {matched_ref} {remote_ver}{NC}")
            return 1
        else:
            cprint(f"  {GREEN}Version bump OK: {remote_ver} → {local_ver} (via {matched_ref}){NC}")

    # Gate 2: Lint with ruff. MANDATORY — missing scripts/ dir is a BLOCK.
    cprint(f"\n{BLUE}[G2] Linting...{NC}")
    scripts_dir = root / "scripts"
    if not scripts_dir.is_dir():
        cprint(f"  {RED}BLOCKED: scripts/ directory missing — cannot lint.{NC}")
        return 1
    lint_result = subprocess.run(
        ["uv", "run", "ruff", "check", "scripts/"],
        cwd=str(root), timeout=120)
    if lint_result.returncode != 0:
        cprint(f"  {RED}BLOCKED: Lint issues found{NC}")
        return 1
    cprint(f"  {GREEN}Lint passed.{NC}")

    # Gate 2b: Copy-paste detection (jscpd) — PARITY with ci.yml Mega-Linter COPYPASTE_JSCPD.
    # CI's Lint job fails on jscpd duplication over the .jscpd.json threshold; surface it locally
    # BEFORE the bump/tag/push. jscpd needs Node/npx; if it cannot be obtained, DEGRADE to a
    # non-blocking WARNING (CI still enforces it) — a green gate then does NOT guarantee green CI
    # for the copy-paste dimension (issue #143). NEVER false-block a push on a tool-install failure.
    cprint(f"\n{BLUE}[G2b] Copy-paste check (jscpd, parity with CI)...{NC}")
    jscpd_bin = shutil.which("jscpd")
    # Resolve npx ONCE into a variable so mypy narrows it (a second
    # shutil.which("npx") call INSIDE the list keeps the element typed
    # `str | None`, making base_cmd `list[str | None]` → subprocess.run
    # [arg-type] under --strict). issue #151.
    npx_bin = shutil.which("npx")
    base_cmd = [jscpd_bin] if jscpd_bin else ([npx_bin, "--yes", "jscpd"] if npx_bin else None)
    if base_cmd is None:
        cprint(f"  {YELLOW}WARNING: jscpd/npx not found — copy-paste check SKIPPED locally.{NC}")
        cprint(f"  {YELLOW}CI's Mega-Linter WILL enforce it (.jscpd.json threshold). A green gate does")
        cprint(f"  {YELLOW}NOT guarantee green CI for the copy-paste dimension (issue #143). Install")
        cprint(f"  {YELLOW}Node/npx for full local parity.{NC}")
    else:
        # Probe distinguishes 'jscpd unavailable/uninstallable' (WARN) from 'jscpd ran, found dupes' (BLOCK).
        probe = subprocess.run(base_cmd + ["--version"], cwd=str(root),
                               capture_output=True, text=True, timeout=180)
        if probe.returncode != 0:
            cprint(f"  {YELLOW}WARNING: jscpd could not run (npx fetch/install failed) — SKIPPED locally.{NC}")
            cprint(f"  {YELLOW}CI's Mega-Linter WILL enforce it; green gate != green CI for copy-paste (issue #143).{NC}")
        else:
            cp = subprocess.run(base_cmd + ["."], cwd=str(root), timeout=300).returncode
            if cp != 0:
                cprint(f"  {RED}BLOCKED: jscpd found copy-paste duplication over the .jscpd.json threshold{NC}")
                cprint(f"  {RED}(parity with CI Mega-Linter). Reduce duplication or raise the threshold in .jscpd.json.{NC}")
                return 1
            cprint(f"  {GREEN}Copy-paste check passed.{NC}")

    # Gate 2c: Workflow-syntax lint (actionlint) — PARITY with ci.yml Lint job.
    # CI runs actionlint on .github/workflows/*; surface a workflow-syntax error
    # locally BEFORE the bump/tag/push. actionlint is a single static binary; if it
    # is not on PATH, DEGRADE to a non-blocking WARNING (CI still enforces it) — a
    # green gate then does NOT guarantee green CI for the workflow-syntax dimension.
    # NEVER false-block a push on a missing-tool case (the issue #143 pattern).
    cprint(f"\n{BLUE}[G2c] Workflow lint (actionlint, parity with CI)...{NC}")
    wf_dir = root / ".github" / "workflows"
    has_workflows = wf_dir.is_dir() and (any(wf_dir.glob("*.yml")) or any(wf_dir.glob("*.yaml")))
    actionlint_bin = shutil.which("actionlint")
    if not has_workflows:
        cprint(f"  {GREEN}No workflows to lint — skipped.{NC}")
    elif actionlint_bin is None:
        cprint(f"  {YELLOW}WARNING: actionlint not found — workflow lint SKIPPED locally.{NC}")
        cprint(f"  {YELLOW}CI's Lint job WILL enforce it. A green gate does NOT guarantee green CI")
        cprint(f"  {YELLOW}for the workflow-syntax dimension. Install actionlint for full parity.{NC}")
    else:
        al = subprocess.run([actionlint_bin], cwd=str(root), timeout=120).returncode
        if al != 0:
            cprint(f"  {RED}BLOCKED: actionlint found workflow-syntax errors (parity with CI Lint job).{NC}")
            return 1
        cprint(f"  {GREEN}Workflow lint passed.{NC}")

    # Gate 2d: Static type-check (mypy) — PARITY with ci.yml Lint job
    # (`uv run mypy scripts/ --ignore-missing-imports`). Surface a type error
    # locally BEFORE the bump/tag/push. A `--version` probe distinguishes
    # 'mypy unavailable' (WARN + skip, never false-block) from 'mypy ran, found
    # errors' (BLOCK) — the issue #143 degrade-gracefully pattern.
    cprint(f"\n{BLUE}[G2d] Type-check (mypy, parity with CI)...{NC}")
    mypy_bin = shutil.which("mypy")
    mypy_cmd = [mypy_bin] if mypy_bin else (["uv", "run", "mypy"] if shutil.which("uv") else None)
    if mypy_cmd is None:
        cprint(f"  {YELLOW}WARNING: mypy/uv not found — type-check SKIPPED locally.{NC}")
        cprint(f"  {YELLOW}CI's Lint job WILL enforce it; a green gate does NOT guarantee green CI for types.{NC}")
    else:
        probe = subprocess.run(mypy_cmd + ["--version"], cwd=str(root),
                               capture_output=True, text=True, timeout=120)
        if probe.returncode != 0:
            cprint(f"  {YELLOW}WARNING: mypy could not run — type-check SKIPPED locally.{NC}")
            cprint(f"  {YELLOW}CI's Lint job WILL enforce it; green gate != green CI for types.{NC}")
        else:
            mt = subprocess.run(mypy_cmd + ["scripts/", "--ignore-missing-imports"],
                                cwd=str(root), timeout=300).returncode
            if mt != 0:
                cprint(f"  {RED}BLOCKED: mypy found type errors in scripts/ (parity with CI Lint job).{NC}")
                return 1
            cprint(f"  {GREEN}Type-check passed.{NC}")

    # Gate 2e: compiled-component build gates (Rust/Go/.NET/Swift/Zig) -- issue #175.
    # Self-detecting + table-driven: for each language, glob its manifest in the tree
    # (a checked-out build-source submodule, or an in-tree component). No manifest ->
    # clean skip. Manifest present but toolchain absent -> WARN+skip (CI / the build
    # pipeline backstops it); toolchain ran + a command failed -> BLOCK. Mirrors the
    # G2b/G2c/G2d degrade-if-absent idiom so a missing toolchain never false-blocks a
    # push. Only Rust + Go get a test command (cargo test / go test are no-ops on zero
    # tests); the others are build-only (their test runners error on "no tests", which
    # would be a false block) -- CI runs the full per-language test matrix.
    cprint(f"\n{BLUE}[G2e] Compiled-component build gates (issue #175)...{NC}")
    _compiled_skip = {"target", ".git", "node_modules", ".venv", "vendor",
                      "dist", "build", "obj", "zig-out", "zig-cache", ".zig-cache"}

    def _find_manifests(pattern):
        found = [
            m for m in root.rglob(pattern)
            if not any(part in _compiled_skip for part in m.relative_to(root).parts)
        ]
        # Keep only top-level manifests: a workspace/module root covers its members, so
        # a nested manifest inside another matched manifest dir is not run standalone.
        return [m for m in found if not any(o is not m and o.parent in m.parents for o in found)]

    # (label, manifest glob, toolchain, builder(manifest) -> [(cmd, cwd), ...])
    _compiled_gates = [
        ("Rust", "Cargo.toml", "cargo", lambda m: [
            (["cargo", "clippy", "--manifest-path", str(m), "--all-targets", "--", "-D", "warnings"], str(root)),
            (["cargo", "test", "--manifest-path", str(m)], str(root)),
        ]),
        ("Go", "go.mod", "go", lambda m: [
            (["go", "vet", "./..."], str(m.parent)),
            (["go", "build", "./..."], str(m.parent)),
            (["go", "test", "./..."], str(m.parent)),
        ]),
        ("C#/.NET", "*.csproj", "dotnet", lambda m: [
            (["dotnet", "build", str(m)], str(root)),
        ]),
        ("Swift", "Package.swift", "swift", lambda m: [
            (["swift", "build"], str(m.parent)),
        ]),
        ("Zig", "build.zig", "zig", lambda m: [
            (["zig", "build"], str(m.parent)),
        ]),
    ]
    _saw_compiled = False
    for _label, _pattern, _tool, _builder in _compiled_gates:
        _manifests = _find_manifests(_pattern)
        if not _manifests:
            continue
        _saw_compiled = True
        if shutil.which(_tool) is None:
            cprint(f"  {YELLOW}WARNING: {_label} component(s) present but `{_tool}` not found -- {_label} build SKIPPED locally.{NC}")
            cprint(f"  {YELLOW}CI / the build pipeline WILL build it; a green gate does NOT guarantee green CI for {_label}.{NC}")
            continue
        for _manifest in _manifests:
            _rel = _manifest.relative_to(root)
            for _cmd, _cwd in _builder(_manifest):
                _shown = " ".join(_cmd)
                try:
                    _rc = subprocess.run(_cmd, cwd=_cwd, timeout=1200).returncode
                except subprocess.TimeoutExpired:
                    cprint(f"  {YELLOW}WARNING: `{_shown}` timed out (>1200s) for {_rel} -- SKIPPED locally; CI backstops.{NC}")
                    break
                if _rc != 0:
                    cprint(f"  {RED}BLOCKED: `{_shown}` failed for {_rel}.{NC}")
                    return 1
        cprint(f"  {GREEN}{_label} build passed ({len(_manifests)} component(s)).{NC}")

    # C/C++: build systems are non-uniform (CMake/Make/Meson/Autotools/Bazel...) with no
    # single false-block-safe local command, and a build depends on system libraries the
    # author machine may lack. So the local gate DETECTS + NOTES (never blocks) -- the
    # plugin build workflow (controlled toolchain) is the authoritative C/C++ builder, and
    # RC-SHIP-BINARY-ONLY (in the remote validator, G3) still enforces the ship-only-binary
    # canon for C/C++.
    def _tree_has(pattern):
        return any(
            not any(part in _compiled_skip for part in p.relative_to(root).parts)
            for p in root.rglob(pattern)
        )

    _cxx_src = any(_tree_has(x) for x in ("*.c", "*.cc", "*.cpp", "*.cxx"))
    _cxx_build = any(_tree_has(x) for x in ("CMakeLists.txt", "Makefile", "meson.build", "configure.ac"))
    if _cxx_src and _cxx_build:
        _saw_compiled = True
        cprint(f"  {YELLOW}NOTE: C/C++ component detected -- its build is authoritative in CI (controlled")
        cprint(f"  {YELLOW}toolchain); the local gate skips non-uniform C/C++ build systems to avoid a")
        cprint(f"  {YELLOW}false block on a missing system dependency. RC-SHIP-BINARY-ONLY still applies.{NC}")

    if not _saw_compiled:
        cprint(f"  {GREEN}No compiled component (Rust/Go/C/C++/.NET/Swift/Zig) -- skipped.{NC}")

    # Gate 2f: Shell lint (shellcheck) -- issue #175.
    # Self-detecting: runs ONLY when the plugin ships shell scripts (*.sh / *.bash).
    # No shell -> skip. Shell present but `shellcheck` absent -> WARN+skip (CI's
    # Mega-Linter BASH_SHELLCHECK backstops); shellcheck ran + found issues -> BLOCK.
    cprint(f"\n{BLUE}[G2f] Shell lint (shellcheck, issue #175)...{NC}")
    _shell_scripts = [
        s for s in list(root.rglob("*.sh")) + list(root.rglob("*.bash"))
        if not any(part in _compiled_skip for part in s.relative_to(root).parts)
    ]
    if not _shell_scripts:
        cprint(f"  {GREEN}No shell scripts -- skipped.{NC}")
    elif shutil.which("shellcheck") is None:
        cprint(f"  {YELLOW}WARNING: shell scripts present but `shellcheck` not found -- shell lint SKIPPED locally.{NC}")
        cprint(f"  {YELLOW}CI's Mega-Linter (BASH_SHELLCHECK) WILL enforce it; green gate != green CI for shell.{NC}")
    else:
        sc = subprocess.run(
            ["shellcheck", *[str(s) for s in sorted(_shell_scripts)]],
            cwd=str(root), timeout=180).returncode
        if sc != 0:
            cprint(f"  {RED}BLOCKED: shellcheck found issues (parity with CI Mega-Linter BASH_SHELLCHECK).{NC}")
            return 1
        cprint(f"  {GREEN}Shell lint passed ({len(_shell_scripts)} script(s)).{NC}")

    # Gate 3: Validate via REMOTE CPV validator. MANDATORY — no skip, no exceptions.
    # CORNERSTONE: a plugin cannot be pushed unless validation passes with 0
    # blocking issues (WARNING allowed). The validator is ALWAYS fetched from
    # GitHub so a tampered local copy cannot weaken the rules.
    cprint(f"\n{BLUE}[G3] Validating plugin (remote CPV)...{NC}")
    if not shutil.which("uvx"):
        cprint(f"  {RED}BLOCKED: uvx not found on PATH.{NC}")
        return 1
    ve = subprocess.run(
        ["uvx", "--from",
         "git+https://github.com/Emasoft/claude-plugins-validation",
         "--with", "pyyaml",
         "cpv-remote-validate", "plugin", ".", "--strict"],
        cwd=str(root), timeout=600).returncode
    # Exit codes: 0=pass, 1=CRITICAL, 2=MAJOR, 3=MINOR, 4=NIT, 5+=WARNING
    if ve != 0 and ve < 5:
        labels = {1: "CRITICAL", 2: "MAJOR", 3: "MINOR", 4: "NIT"}
        cprint(f"  {RED}BLOCKED: {labels.get(ve, f'exit {ve}')} issues found{NC}")
        return 1
    cprint(f"  {GREEN}Validation passed (0 blocking issues).{NC}")

    # Gate 4: Tests. MANDATORY — missing tests/ dir or zero tests is a BLOCK.
    cprint(f"\n{BLUE}[G4] Running tests...{NC}")
    test_dir = root / "tests"
    if not (test_dir.is_dir() and any(test_dir.glob("test_*.py"))):
        cprint(f"  {RED}BLOCKED: tests/ directory missing or empty.{NC}")
        cprint(f"  {RED}Every CPV plugin MUST ship tests.{NC}")
        return 1
    suite_timeout = _test_suite_timeout()
    try:
        te = subprocess.run(
            ["uv", "run", "pytest", "tests/", "-x", "-q", "--tb=short"],
            cwd=str(root), timeout=suite_timeout).returncode
    except subprocess.TimeoutExpired:
        cprint(f"  {RED}BLOCKED: Tests timed out after {suite_timeout:g}s.{NC}")
        cprint(f"  {RED}If the suite is legitimately longer, raise "
               f"{_TEST_SUITE_TIMEOUT_ENV} — do not trim or skip tests to fit.{NC}")
        return 1
    if te == 5:
        cprint(f"  {RED}BLOCKED: pytest collected 0 tests.{NC}")
        return 1
    if te != 0:
        cprint(f"  {RED}BLOCKED: Tests failed{NC}")
        return 1
    cprint(f"  {GREEN}Tests passed.{NC}")

    cprint(f"\n{GREEN}{BOLD}All gates passed.{NC}")
    return 0


# -- Pipeline stages -----------------------------------------------------------

def stage_bypass_guard() -> None:
    """Step 0: Reject any env var that could bypass a check. No exceptions.

    Issue #22 hardening (v2.86.0): broadened from a fixed allowlist to
    prefix-pattern matching. Any env var matching ``PLUGIN_SKIP_*``,
    ``CPV_SKIP_*``, ``SKIP_*``, or named ``NO_VERIFY`` aborts the publish.
    Closes the loophole where a fresh skip name (e.g. ``CPV_SKIP_GATE7``)
    that was not in the original explicit list would silently slip past.

    Explicit infrastructure exemptions remain — all are read-only
    overrides used by CPV's own integrity / auth subsystems and never
    skip a gate:
        * ``PLUGIN_SKIP_GITHUB_INTEGRITY=1`` — bypasses the GitHub-anchored
          integrity check (see the hash-verify module). That check is a
          defence against tampering, NOT a publish gate.
        * ``CPV_SKIP_GITHUB_INTEGRITY=1`` — the LEGACY spelling of the same
          override, still honoured (deprecated, TRDD-bbff5bc5).
        * ``CPV_SKIP_GH_AUTH_CHECK=1`` — used by `_ensure_gh_auth` to bypass
          the `gh auth status` round-trip on flaky networks. Auth still
          has to work for the actual `git push` / `gh release create`;
          this only skips the precheck.

    The ``PLUGIN_`` spelling MUST be exempt: the hash-verify module renamed
    the var and instructs users to export it, yet ``PLUGIN_SKIP_`` is a
    forbidden PREFIX here — so without this entry, following that module's
    own deprecation notice aborts the publish as a "bypass attempt". It
    grants NO new capability (the same override is already exempt under its
    legacy name). There is deliberately no ``PLUGIN_SKIP_GH_AUTH_CHECK``
    entry — no such var exists, and exempting a name nothing reads would
    widen the bypass surface for nothing.

    All are documented exemptions, listed below and excluded from the
    pattern match.
    """
    cprint(f"\n{BOLD}[0/11] Checking for bypass attempts...{NC}")
    # Explicit infrastructure exemptions — see docstring above.
    exemptions = {
        "PLUGIN_SKIP_GITHUB_INTEGRITY",
        "CPV_SKIP_GITHUB_INTEGRITY",
        "CPV_SKIP_GH_AUTH_CHECK",
    }
    forbidden_prefixes = ("PLUGIN_SKIP_", "CPV_SKIP_", "SKIP_")
    forbidden_exact = {"NO_VERIFY"}
    attempted = [
        v
        for v in sorted(os.environ)
        if (v.startswith(forbidden_prefixes) or v in forbidden_exact) and v not in exemptions
        if os.environ.get(v)
    ]
    if attempted:
        cprint(f"  {RED}BLOCKED: forbidden env vars set: {', '.join(attempted)}{NC}")
        cprint(f"  {RED}The publish pipeline enforces every check. "
               f"Fix failures, do not skip them.{NC}")
        cprint(f"  {DIM}(infrastructure exemptions: {', '.join(sorted(exemptions))}){NC}")
        sys.exit(1)
    cprint(f"  {GREEN}No bypass vars set.{NC}")

def stage_check_clean(root: Path) -> None:
    """Step 1: Working tree must be clean."""
    cprint(f"\n{BOLD}[1/11] Checking working tree...{NC}")
    r = run(["git", "status", "--porcelain"], cwd=root, capture=True)
    if r.stdout.strip():
        cprint(f"  {RED}Working tree is dirty. Commit or stash changes first.{NC}")
        cprint(r.stdout)
        sys.exit(1)
    cprint(f"  {GREEN}Clean.{NC}")

def stage_lint(root: Path) -> None:
    """Step 2: Lint + typecheck (ruff + mypy). MANDATORY — no skip.

    Runs ruff for style/syntax and mypy for static types in the same stage.
    Both must succeed — the cornerstone rule forbids any push with lint or
    type errors. Type-checking runs BEFORE the test suite so the cheap fails
    come before the expensive ones.
    """
    cprint(f"\n{BOLD}[2/11] Linting + type-checking...{NC}")
    scripts_dir = root / "scripts"
    if not scripts_dir.is_dir():
        cprint(f"  {RED}BLOCKED: scripts/ directory missing — cannot lint.{NC}")
        sys.exit(1)
    cprint(f"  {BLUE}ruff check scripts/{NC}")
    run(["uv", "run", "ruff", "check", "scripts/"], cwd=root)
    cprint(f"  {BLUE}mypy scripts/ --ignore-missing-imports{NC}")
    run(["uv", "run", "mypy", "scripts/", "--ignore-missing-imports"], cwd=root)
    cprint(f"  {GREEN}Lint + typecheck passed.{NC}")

# Issue #31 (v2.98.0): browser-orphan cleanup signatures.
#
# A pytest run that spawns Playwright / dev-browser pages can leave
# behind dozens of `Chrome for Testing` / `chromium` / `headless_shell`
# processes if the test code (or fixtures) forget to close pages. Over
# a long debug session those orphans pile up, exhausting file
# descriptors or RAM and eventually crashing the browser or making
# the machine unresponsive. The baseline-diff cleanup below catches
# every leak regardless of test-code quality. NEVER skips tests — the
# iron rule (no plugin with issues pushed) is preserved.
_BROWSER_ORPHAN_SIGNATURES = (
    "Chrome for Testing",
    "chrome-for-testing",
    "headless_shell",
    "Chromium.app/Contents",
    "chromium-browser",
    "/playwright/",
    "playwright-core",
)


def _snapshot_browser_pids() -> set:
    """Snapshot-then-grep — never live-grep — for browser-signature PIDs."""
    try:
        snap = subprocess.run(
            ["ps", "-eo", "pid,command"],
            capture_output=True, text=True, check=False, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return set()
    if snap.returncode != 0 or not snap.stdout:
        return set()
    pids = set()
    for raw_line in snap.stdout.strip().split("\n")[1:]:
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


def _cleanup_browser_orphans(baseline_pids: set) -> int:
    """Kill browser-signature PIDs that appeared since ``baseline_pids``.

    Baseline-diff: PIDs in baseline are pre-existing (maintainer's own
    daily browser) — NEVER killed. Only PIDs that came into existence
    during the pytest run are candidates.
    """
    import signal
    import time

    current = _snapshot_browser_pids()
    new_pids = current - baseline_pids
    if not new_pids:
        return 0
    killed = 0
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


def stage_tests(root: Path) -> None:
    """Step 3: Run pytest. MANDATORY — no skip, no exceptions.

    Cornerstone rule: failing tests block the push. Missing tests/ directory
    is a scaffolding bug and must be fixed, not bypassed.

    Order: tests run BEFORE the CPV validator so behavioral regressions fail
    fast on unit tests before the structural validator inspects the manifest.

    Issue #31 (v2.98.0): wrap the pytest invocation in a baseline-diff
    browser-orphan cleanup so dev-browser / Playwright leaks do not
    pile up Chrome-for-Testing processes. Tests still run
    unconditionally — the cleanup is a safety net, not a skip
    mechanism.
    """
    cprint(f"\n{BOLD}[3/11] Running tests...{NC}")
    test_dir = root / "tests"
    if not test_dir.is_dir():
        cprint(f"  {RED}BLOCKED: tests/ directory missing.{NC}")
        cprint(f"  {RED}Every CPV plugin MUST ship a tests/ directory.{NC}")
        sys.exit(1)
    baseline_browser_pids = _snapshot_browser_pids()
    try:
        r = run(["uv", "run", "pytest", "tests/", "-x", "-q", "--tb=short"], cwd=root,
                check=False, timeout=_test_suite_timeout())
    finally:
        killed = _cleanup_browser_orphans(baseline_browser_pids)
        if killed:
            cprint(f"  {YELLOW}Cleaned up {killed} orphaned browser process(es) spawned by pytest.{NC}")
    if r.returncode == 5:
        # pytest exit 5 = no tests collected. This is ALSO a block — no exceptions.
        cprint(f"  {RED}BLOCKED: pytest collected 0 tests.{NC}")
        cprint(f"  {RED}Every CPV plugin MUST ship at least one test.{NC}")
        sys.exit(1)
    if r.returncode != 0:
        cprint(f"  {RED}BLOCKED: tests failed (exit {r.returncode}).{NC}")
        sys.exit(r.returncode)
    cprint(f"  {GREEN}Tests passed.{NC}")


def stage_validate(root: Path) -> None:
    """Step 4: Validate plugin via REMOTE CPV validator. MANDATORY — no skip.

    Cornerstone rule: a plugin cannot be pushed unless validation passes
    with 0 issues (WARNING allowed). The validator is ALWAYS fetched from
    GitHub (git+https://github.com/Emasoft/claude-plugins-validation) via
    uvx so a local tampered copy cannot weaken the rules. No exceptions.

    Order: runs AFTER lint + tests so behavioral regressions fail fast
    before the structural validator even looks at the manifest.
    """
    cprint(f"\n{BOLD}[4/11] Validating plugin (remote CPV)...{NC}")
    if not shutil.which("uvx"):
        cprint(f"  {RED}BLOCKED: uvx not found on PATH.{NC}")
        cprint(f"  {RED}Install via: brew install uv  or  pip install uv{NC}")
        sys.exit(1)
    # Fetch CPV from GitHub and run validate_plugin remotely. --strict blocks
    # on CRITICAL(1), MAJOR(2), MINOR(3), NIT(4); WARNING(5+) passes.
    run([
        "uvx", "--from",
        "git+https://github.com/Emasoft/claude-plugins-validation",
        "--with", "pyyaml",
        "cpv-remote-validate", "plugin", ".", "--strict",
    ], cwd=root)
    cprint(f"  {GREEN}Validation passed (0 blocking issues).{NC}")


def stage_ci_preflight(root: Path) -> None:
    """Step 4b: CI-parity preflight via REMOTE CPV. MANDATORY — no skip.

    WHY THIS STAGE EXISTS. `validate_plugin --strict` (stage 4) does NOT run the
    gates this plugin's own GitHub-CI Lint job runs: jscpd copy-paste, actionlint,
    mypy, the `uv sync --extra dev` resolve, the enabled Mega-Linter sub-linters,
    and CPV's static CI-parity defect detectors. Without this stage a publish
    passes every LOCAL gate, bumps the version, commits, TAGS, PUSHES, and cuts a
    GitHub release — and only THEN goes red on GitHub, with the broken pipeline
    already shipped to everyone who installs the plugin.

    PLACEMENT IS LOAD-BEARING: this runs BEFORE stage_bump / stage_commit_and_push
    / stage_gh_release, so a parity failure aborts with the working tree untouched
    instead of leaving a half-published release behind.

    A MISSING TOOL NEVER BLOCKS THE PUBLISH. `ci-preflight` exits non-zero ONLY
    when a gate actually FAILED; every tool-absent case (no npx, no actionlint,
    no checkov, ...) degrades to a non-blocking WARNING and still exits 0. So a
    lean machine publishes exactly as before — it just gets less LOCAL coverage,
    which CI still enforces. Do not "harden" this into a hard tool requirement.
    """
    cprint(f"\n{BOLD}[4b/11] CI-parity preflight (remote CPV)...{NC}")
    if not shutil.which("uvx"):
        cprint(f"  {RED}BLOCKED: uvx not found on PATH.{NC}")
        cprint(f"  {RED}Install via: brew install uv  or  pip install uv{NC}")
        sys.exit(1)
    rc = subprocess.run([
        "uvx", "--from",
        "git+https://github.com/Emasoft/claude-plugins-validation",
        "--with", "pyyaml",
        "cpv-remote-validate", "ci-preflight", ".",
    ], cwd=str(root)).returncode
    if rc != 0:
        cprint(f"  {RED}BLOCKED: CI-parity preflight FAILED.{NC}")
        cprint(f"  {RED}The gates listed above would fail GitHub CI — and without this{NC}")
        cprint(f"  {RED}stage they would only have failed AFTER the tag and release were{NC}")
        cprint(f"  {RED}pushed. Fix the causes, then re-run publish.py.{NC}")
        sys.exit(1)
    cprint(f"  {GREEN}CI-parity preflight passed.{NC}")


# ── Marketplace-registration helpers (mirror of CPV's own publish.py Gate 6) ─

def _find_parent_marketplace(plugin_root: Path) -> Path | None:
    """Walk up looking for a parent marketplace.json (Layout B signature)."""
    current = plugin_root.resolve().parent
    while current != current.parent:
        mp = current / ".claude-plugin" / "marketplace.json"
        if mp.is_file():
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


def _detect_layout(plugin_root: Path) -> tuple[str, dict]:
    """Detect Layout A (standalone+notify), Layout B (nested), or 'none'."""
    parent = _find_parent_marketplace(plugin_root)
    if parent is not None:
        return "B", {"marketplace_root": parent, "plugin_name": plugin_root.name}
    notify_wf = plugin_root / ".github" / "workflows" / "notify-marketplace.yml"
    if notify_wf.is_file():
        try:
            content = notify_wf.read_text(encoding="utf-8")
        except OSError:
            content = ""
        m_owner = re.search(r"^\s*MARKETPLACE_OWNER:\s*[\"']?([^\"'\s]+)[\"']?\s*$", content, re.MULTILINE)
        m_repo = re.search(r"^\s*MARKETPLACE_REPO:\s*[\"']?([^\"'\s]+)[\"']?\s*$", content, re.MULTILINE)
        return "A", {
            "notify_workflow": notify_wf,
            "mkt_owner": m_owner.group(1) if m_owner else None,
            "mkt_repo": m_repo.group(1) if m_repo else None,
        }
    return "none", {}


def _gh_secret_exists(plugin_root: Path, secret_name: str) -> bool:
    """Check whether a GitHub secret with the given name exists on this repo."""
    gh = shutil.which("gh")
    if gh is None:
        return False
    r = subprocess.run([gh, "secret", "list"], cwd=str(plugin_root),
                       capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        return False
    for line in r.stdout.splitlines():
        if line.split("\t", 1)[0].strip() == secret_name:
            return True
    return False


def _current_repo_slug(plugin_root: Path) -> str | None:
    """Return owner/repo slug for current git origin, or None."""
    r = subprocess.run(["git", "remote", "get-url", "origin"], cwd=str(plugin_root),
                       capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        return None
    m = re.search(r"[:/]([^/:]+)/([^/]+?)(?:\.git)?$", r.stdout.strip())
    return f"{m.group(1)}/{m.group(2)}" if m else None


def _read_plugin_name(plugin_root: Path) -> str:
    pj = plugin_root / ".claude-plugin" / "plugin.json"
    if pj.is_file():
        try:
            data = json.loads(pj.read_text(encoding="utf-8"))
            name = data.get("name")
            if isinstance(name, str) and name:
                return name
        except (OSError, json.JSONDecodeError):
            pass
    return plugin_root.name


def _fetch_remote_marketplace_json(owner: str, repo: str) -> dict | None:
    gh = shutil.which("gh")
    if gh is None:
        return None
    r = subprocess.run(
        [gh, "api", f"repos/{owner}/{repo}/contents/.claude-plugin/marketplace.json",
         "-H", "Accept: application/vnd.github.raw+json"],
        capture_output=True, text=True, timeout=60,
    )
    if r.returncode != 0:
        return None
    try:
        data = json.loads(r.stdout)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _remote_has_receiver_workflow(owner: str, repo: str) -> bool:
    gh = shutil.which("gh")
    if gh is None:
        return False
    r = subprocess.run(
        [gh, "api", f"repos/{owner}/{repo}/contents/.github/workflows"],
        capture_output=True, text=True, timeout=60,
    )
    if r.returncode != 0:
        return False
    try:
        entries = json.loads(r.stdout)
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
        f = subprocess.run(
            [gh, "api", f"repos/{owner}/{repo}/contents/.github/workflows/{name}",
             "-H", "Accept: application/vnd.github.raw+json"],
            capture_output=True, text=True, timeout=60,
        )
        if f.returncode == 0 and "repository_dispatch" in f.stdout:
            return True
    return False


def _plugin_in_remote_marketplace(mkt_json: dict, plugin_name: str, expected_repo: str | None) -> bool:
    """Accept github/url/git source forms; match URL slug for url|git (issue #25 Defect A)."""
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


def stage_marketplace_registration(root: Path) -> None:
    """Step 5: Verify the plugin is wired to its marketplace for auto-updates.

    Mirror of CPV's own publish.py Gate 6. Three modes:
      - Layout A (standalone + notify-marketplace.yml): verifies workflow,
        MARKETPLACE_PAT secret, remote marketplace.json registration,
        remote receiver workflow with repository_dispatch trigger
      - Layout B (nested under <marketplace>/plugins/<name>/): refuses to
        publish from the nested folder, requires running at marketplace root
      - 'none' (no marketplace wiring): emits a WARNING and proceeds — valid
        for first releases or experimental standalone plugins
    """
    cprint(f"\n{BOLD}[5/11] Marketplace-registration check...{NC}")
    layout, details = _detect_layout(root)

    if layout == "none":
        cprint(f"  {YELLOW}WARNING: no marketplace registration found for this plugin.{NC}")
        cprint(f"  {YELLOW}If you intend to publish to a marketplace, run the{NC}")
        cprint(f"  {YELLOW}cpv-setup-marketplace-auto-notification skill to wire up auto-updates.{NC}")
        cprint(f"  {YELLOW}Allowing release to proceed (standalone/experimental mode).{NC}")
        return

    if layout == "A":
        cprint("  Layout A detected (standalone plugin repo)")
        notify_wf = details.get("notify_workflow")
        mkt_owner = details.get("mkt_owner")
        mkt_repo = details.get("mkt_repo")
        if not notify_wf or not Path(notify_wf).is_file():
            cprint(f"  {RED}BLOCKED: .github/workflows/notify-marketplace.yml missing.{NC}")
            sys.exit(1)
        if not mkt_owner or not mkt_repo:
            cprint(f"  {RED}BLOCKED: notify-marketplace.yml has no MARKETPLACE_OWNER/MARKETPLACE_REPO.{NC}")
            sys.exit(1)
        cprint(f"  target marketplace: {mkt_owner}/{mkt_repo}")
        if shutil.which("gh") is None:
            cprint(f"  {RED}BLOCKED: gh CLI not installed — cannot verify secret/marketplace.{NC}")
            sys.exit(1)
        if not _gh_secret_exists(root, "MARKETPLACE_PAT"):
            cprint(f"  {RED}BLOCKED: MARKETPLACE_PAT secret not configured on this plugin repo.{NC}")
            cprint(f"  {RED}  Fix: uv run python scripts/set_marketplace_pat.py {_current_repo_slug(root) or 'OWNER/REPO'}{NC}")
            sys.exit(1)
        cprint(f"  {GREEN}MARKETPLACE_PAT secret configured{NC}")
        mkt_json = _fetch_remote_marketplace_json(mkt_owner, mkt_repo)
        if mkt_json is None:
            cprint(f"  {RED}BLOCKED: cannot fetch marketplace.json from {mkt_owner}/{mkt_repo}.{NC}")
            sys.exit(1)
        plugin_name = _read_plugin_name(root)
        slug = _current_repo_slug(root)
        if not _plugin_in_remote_marketplace(mkt_json, plugin_name, slug):
            cprint(f"  {RED}BLOCKED: plugin '{plugin_name}' not registered in {mkt_owner}/{mkt_repo} marketplace.json.{NC}")
            cprint(f"  {RED}  Add an entry: {{\"name\": \"{plugin_name}\", \"source\": {{\"source\": \"github\", \"repo\": \"{slug}\"}}}}{NC}")
            sys.exit(1)
        cprint(f"  {GREEN}Plugin registered in remote marketplace.json{NC}")
        if not _remote_has_receiver_workflow(mkt_owner, mkt_repo):
            cprint(f"  {RED}BLOCKED: remote marketplace {mkt_owner}/{mkt_repo} has no workflow with repository_dispatch trigger.{NC}")
            cprint(f"  {RED}  See cpv-setup-marketplace-auto-notification skill.{NC}")
            sys.exit(1)
        cprint(f"  {GREEN}Remote marketplace has receiver workflow{NC}")
        cprint(f"  {GREEN}Layout A marketplace registration verified.{NC}")
        return

    if layout == "B":
        cprint("  Layout B detected (nested plugin under marketplace repo)")
        marketplace_root_raw = details.get("marketplace_root")
        marketplace_root: Path | None = marketplace_root_raw if isinstance(marketplace_root_raw, Path) else None
        plugin_name_raw = details.get("plugin_name")
        # Note: no type annotation here — mypy's no-redef rule complains even
        # though the Layout A branch above returns before reaching this
        # point. Plain assignment avoids the false positive in the generated
        # template output (which downstream CI runs with mypy --strict).
        plugin_name = plugin_name_raw if isinstance(plugin_name_raw, str) else root.name
        if marketplace_root is None:
            cprint(f"  {RED}BLOCKED: Layout B detected but marketplace_root unresolved.{NC}")
            sys.exit(1)
        if root.resolve() != marketplace_root.resolve():
            cprint(f"  {RED}BLOCKED: This is a Layout B nested plugin.{NC}")
            cprint(f"  {RED}  publish.py must run at the MARKETPLACE root, not the nested folder.{NC}")
            cprint(f"  {RED}  Bumping a nested plugin alone breaks the atomic marketplace tag.{NC}")
            cprint(f"  {RED}  Fix: cd {marketplace_root} && uv run python scripts/publish.py --patch{NC}")
            sys.exit(1)
        mp_path = marketplace_root / ".claude-plugin" / "marketplace.json"
        try:
            mp_data = json.loads(mp_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            cprint(f"  {RED}BLOCKED: cannot read {mp_path}: {e}{NC}")
            sys.exit(1)
        entries = mp_data.get("plugins") if isinstance(mp_data, dict) else None
        if not isinstance(entries, list):
            cprint(f"  {RED}BLOCKED: marketplace.json has no 'plugins' array.{NC}")
            sys.exit(1)
        if not any(isinstance(e, dict) and e.get("name") == plugin_name for e in entries):
            cprint(f"  {RED}BLOCKED: plugin '{plugin_name}' not registered in {mp_path}.{NC}")
            cprint(f"  {RED}  Add: {{\"name\": \"{plugin_name}\", \"source\": \"./plugins/{plugin_name}\"}}{NC}")
            sys.exit(1)
        cprint(f"  {GREEN}Plugin '{plugin_name}' registered in parent marketplace.json{NC}")
        cprint(f"  {GREEN}Layout B marketplace registration verified.{NC}")


def stage_consistency(root: Path) -> None:
    """Step 6: Check version consistency."""
    cprint(f"\n{BOLD}[6/11] Checking version consistency...{NC}")
    ok, msg = check_version_consistency(root)
    cprint(f"  {msg}")
    if not ok:
        cprint(f"  {RED}Fix version mismatch before publishing.{NC}")
        sys.exit(1)
    cprint(f"  {GREEN}Consistent.{NC}")

def _read_remote_version(plugin_root: Path) -> str | None:
    """Read .claude-plugin/plugin.json's `version` from origin/master (or main).

    Idempotency baseline: the publish pipeline reads the REMOTE version, not
    the local one, so an interrupted publish that already bumped + committed
    locally cannot double-bump on re-run. Returns None when offline / no
    remote ref / file missing — caller must fall back to local baseline.
    """
    for ref in ("origin/master", "origin/main", "origin/HEAD"):
        try:
            r = subprocess.run(
                ["git", "show", f"{ref}:.claude-plugin/plugin.json"],
                capture_output=True, text=True, cwd=str(plugin_root),
                check=False, timeout=15,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if r.returncode != 0:
            continue
        try:
            v = json.loads(r.stdout).get("version")
        except json.JSONDecodeError:
            continue
        if isinstance(v, str):
            return v
    return None


def _infer_bump_type(old: str, new: str) -> str | None:
    """Classify a semver delta as 'major', 'minor', 'patch', or None."""
    o = parse_semver(old)
    n = parse_semver(new)
    if o is None or n is None or n <= o:
        return None
    if n[0] != o[0]:
        return "major"
    if n[1] != o[1]:
        return "minor"
    return "patch"


def _git_porcelain_clean(root: Path) -> bool:
    """True iff `git status --porcelain` is empty (working tree clean)."""
    try:
        r = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True, text=True, cwd=str(root),
            check=False, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode == 0 and not r.stdout.strip()


def _head_commit_message(root: Path) -> str:
    """Return the subject line of HEAD, or '' on failure."""
    try:
        r = subprocess.run(
            ["git", "log", "-1", "--pretty=%s"],
            capture_output=True, text=True, cwd=str(root),
            check=False, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return r.stdout.strip() if r.returncode == 0 else ""


def _local_tag_exists(root: Path, tag: str) -> bool:
    """True iff `tag` already exists in the local git repo."""
    try:
        r = subprocess.run(
            ["git", "rev-parse", "--verify", f"refs/tags/{tag}"],
            capture_output=True, text=True, cwd=str(root),
            check=False, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode == 0


def _plugin_name(root: Path) -> str | None:
    """Read the plugin name from .claude-plugin/plugin.json."""
    pj = root / ".claude-plugin" / "plugin.json"
    if not pj.is_file():
        return None
    try:
        data = json.loads(pj.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    name = data.get("name")
    return str(name) if name else None


def _dependency_tag_name(root: Path, new_ver: str) -> str | None:
    """The `{plugin-name}--v{version}` tag Claude Code resolves dependencies against.

    Derived from the manifest, never hardcoded, so renaming the plugin cannot silently
    desync the tag from the plugin it names. Returns None when the name is unreadable,
    in which case the caller warns and skips rather than inventing a name.

    This is the exact name `claude plugin tag` produces.
    """
    name = _plugin_name(root)
    return f"{name}--v{new_ver}" if name else None


def stage_bump(root: Path, new_ver: str, dry_run: bool) -> None:
    """Step 7: Bump version. Idempotent — skips when local already matches target.

    Recovery semantics: when a previous publish was interrupted between the
    local commit+tag and the push (transient network failure during git push,
    pre-push hook reject, etc.), the local repo is at the bumped version while
    origin is one minor behind. Re-running publish.py would DOUBLE-BUMP
    (read-local-then-add-1 → next minor on top of the already-bumped local).
    The fix: read REMOTE plugin.json as baseline, infer bump type from
    local-vs-remote delta, and skip the bump entirely when local already
    matches the target.
    """
    cprint(f"\n{BOLD}[7/11] Bumping version...{NC}")
    current = get_current_version(root)
    remote = _read_remote_version(root)
    if remote and current and current == new_ver:
        cprint(f"  {YELLOW}Local plugin.json is already at {new_ver} (remote at {remote}) — "
               f"skipping bump (interrupted-publish recovery).{NC}")
        return
    if remote and current and current != remote and current != new_ver:
        cprint(f"  {RED}REFUSED: local plugin.json is at {current} but remote is at "
               f"{remote} and target is {new_ver}. Refuse to guess what state this is.{NC}")
        cprint(f"  {RED}Manual intervention required: align local with remote, then re-run.{NC}")
        sys.exit(1)
    if not do_bump(root, new_ver, dry_run=dry_run):
        cprint(f"  {RED}Version bump failed.{NC}")
        sys.exit(1)
    cprint(f"  {GREEN}Version bumped to {new_ver}.{NC}")

def stage_update_badges(root: Path, old_ver: str, new_ver: str, dry_run: bool) -> None:
    """Step 8: Replace version badge in README.md.

    Strategy:
      1. Try exact-string substitution `version-<old>-blue` → `version-<new>-blue`
      2. If the exact old version is not present, fall back to a regex that
         matches ANY `version-X.Y.Z-blue` pattern (handles drift from a hand-edit
         or a missed release). Prevents the "stale forever" trap that bit CPV
         itself when its README badge fell 20 releases behind.
      3. Emit a WARNING (not silent skip) when no badge is found at all so the
         author notices the README has no shields.io version badge to update.
    """
    cprint(f"\n{BOLD}[8/11] Updating README badge...{NC}")
    readme = root / "README.md"
    if not readme.exists():
        cprint(f"  {YELLOW}WARNING: no README.md — skipping badge update.{NC}")
        return
    content = readme.read_text(encoding="utf-8")
    old_badge = f"version-{old_ver}-blue"
    new_badge = f"version-{new_ver}-blue"

    if old_badge in content:
        if dry_run:
            cprint(f"  Would update badge (exact match): {old_badge} -> {new_badge}")
            return
        readme.write_text(content.replace(old_badge, new_badge, 1), encoding="utf-8")
        cprint(f"  {GREEN}Updated README badge: {old_ver} -> {new_ver}{NC}")
        return

    # Fallback: regex match on any version-X.Y.Z-blue pattern
    badge_re = re.compile(r"version-\d+\.\d+\.\d+-blue")
    match = badge_re.search(content)
    if match is None:
        cprint(f"  {YELLOW}WARNING: no version-X.Y.Z-blue badge found in README.md.{NC}")
        cprint(f"  {YELLOW}Add a shields.io badge so future releases can update it automatically.{NC}")
        return
    found = match.group(0)
    if dry_run:
        cprint(f"  Would update badge (regex match): {found} -> {new_badge}")
        return
    readme.write_text(badge_re.sub(new_badge, content, count=1), encoding="utf-8")
    cprint(f"  {GREEN}Updated README badge (was {found}, now {new_badge}){NC}")

def detect_bump_type(root: Path) -> str:
    """Auto-detect the next bump type from conventional commits via git-cliff.

    Runs `git-cliff --bumped-version` and compares the predicted version to
    the REMOTE one (origin/master) to determine major/minor/patch. Falls back
    to 'patch' on any failure (git-cliff missing, repo empty, parse error) so
    the cornerstone rule — every push is a bump — is never violated.

    Idempotency: when the local repo already has a release commit (interrupted
    publish), reading local plugin.json would over-shoot the bump (current is
    already the bumped version, git-cliff would compute current+1). Reading
    remote/origin gives the true baseline.

    Conventional commit mapping (git-cliff defaults):
      feat:                 -> minor
      fix:/perf:/refactor:  -> patch
      BREAKING CHANGE / !   -> major
    """
    cliff_bin = shutil.which("git-cliff")
    if cliff_bin is None:
        cprint(f"{YELLOW}git-cliff not installed — auto-bump falls back to 'patch'.{NC}")
        return "patch"
    current = _read_remote_version(root) or get_current_version(root)
    if not current:
        cprint(f"{YELLOW}Cannot read current version for auto-bump — falling back to 'patch'.{NC}")
        return "patch"
    try:
        r = subprocess.run(
            [cliff_bin, "--bumped-version"],
            capture_output=True,
            text=True,
            cwd=str(root),
            check=False,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return "patch"
    if r.returncode != 0:
        return "patch"
    out = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else ""
    bumped = out.lstrip("v").strip()
    if not bumped or bumped == current:
        return "patch"
    try:
        cur = [int(p) for p in current.split(".")[:3]]
        nxt = [int(p) for p in bumped.split(".")[:3]]
        while len(cur) < 3:
            cur.append(0)
        while len(nxt) < 3:
            nxt.append(0)
    except ValueError:
        return "patch"
    if nxt[0] > cur[0]:
        return "major"
    if nxt[1] > cur[1]:
        return "minor"
    return "patch"


def stage_changelog(root: Path, new_ver: str, dry_run: bool) -> None:
    """Step 9: Generate CHANGELOG.md with git-cliff using the bumped tag.

    Uses the git-cliff pattern recommended for release pipelines:
        git cliff --bump --unreleased --tag v<NEXT> -o CHANGELOG.md

    --bump          promote the unreleased section into a dated tag entry
    --unreleased    process only commits since the last tag
    --tag v<NEXT>   label the new entry with the computed version (prefixed v)
    -o CHANGELOG.md write the regenerated changelog back to disk
    """
    cprint(f"\n{BOLD}[9/11] Generating changelog (git-cliff)...{NC}")
    if not shutil.which("git-cliff"):
        cprint(f"  {YELLOW}git-cliff not installed — skipping changelog.{NC}")
        return
    cliff_toml = root / "cliff.toml"
    if not cliff_toml.is_file():
        cprint(f"  {YELLOW}No cliff.toml — skipping changelog.{NC}")
        return
    tag = f"v{new_ver}"
    if dry_run:
        cprint(f"  Would run: git-cliff --bump --unreleased --tag {tag} -o CHANGELOG.md")
        return
    run(
        ["git-cliff", "--bump", "--unreleased", "--tag", tag, "-o", "CHANGELOG.md"],
        cwd=root,
    )
    cprint(f"  {GREEN}CHANGELOG.md updated with {tag}.{NC}")

def stage_commit_and_push(root: Path, new_ver: str, dry_run: bool) -> None:
    """Step 10: Commit, tag, push. Idempotent on commit + tag.

    Idempotency: if HEAD's subject is already `chore: bump version to <new_ver>`
    AND the working tree is clean, skip the commit step (interrupted-publish
    recovery). If the tag already exists locally, skip the tag step. The push
    always runs — that is what brings the remote into sync.

    TRDD-bbff5bc5 §5: gh-auth precheck runs BEFORE the first push so the
    user gets an actionable error if their gh CLI is unauthed/lacks push
    perm — instead of an opaque git push failure mid-pipeline.
    """
    cprint(f"\n{BOLD}[10/11] Committing and pushing...{NC}")
    tag = f"v{new_ver}"
    # The DEPENDENCY-RESOLUTION tag. Since Claude Code 2.1.110 a version-constrained
    # dependency ({"name": "<plugin>", "version": ">=1.2"}) is resolved by listing this
    # repo's tags, keeping only those starting with "<plugin>--v", and fetching the
    # highest one satisfying the range. The plain vX.Y.Z tag is IGNORED by that
    # resolver, so a plugin shipping only vX.Y.Z cannot be depended upon: every
    # dependent fails to install with `no-matching-tag` and is DISABLED.
    #
    # It stays invisible until someone installs clean (an already-installed dependent
    # keeps working), which is exactly how it went unnoticed in the wild. So both tags
    # are created and pushed in the SAME atomic push -- a release can never ship with
    # one and not the other. NOTE the separator is a DOUBLE hyphen (`--v`); a single
    # `-v` does not match the resolver's prefix filter.
    dep_tag = _dependency_tag_name(root, new_ver)
    expected_subject = f"chore: bump version to {new_ver}"
    head_subject = _head_commit_message(root)
    tree_clean = _git_porcelain_clean(root)
    tag_exists = _local_tag_exists(root, tag)
    dep_tag_exists = dep_tag is not None and _local_tag_exists(root, dep_tag)
    push_refs = ["HEAD", tag] + ([dep_tag] if dep_tag else [])

    if dry_run:
        if head_subject == expected_subject and tree_clean:
            cprint(f"  Would skip commit (HEAD already '{expected_subject}', tree clean)")
        else:
            cprint(f"  Would commit: {expected_subject}")
        if tag_exists:
            cprint(f"  Would skip tag (already exists locally): {tag}")
        else:
            cprint(f"  Would tag: {tag}")
        if dep_tag is None:
            cprint(f"  {YELLOW}Would SKIP the dependency tag - plugin name unreadable.{NC}")
        elif dep_tag_exists:
            cprint(f"  Would skip dependency tag (already exists locally): {dep_tag}")
        else:
            cprint(f"  Would tag (dependency resolution): {dep_tag}")
        cprint(f"  Would push (atomic): origin {' '.join(push_refs)}")
        return

    if head_subject == expected_subject and tree_clean:
        cprint(f"  {YELLOW}HEAD is already '{expected_subject}' and tree is clean — "
               f"skipping commit (interrupted-publish recovery).{NC}")
    else:
        run(["git", "add", "-A"], cwd=root)
        run(["git", "commit", "-m", expected_subject], cwd=root)

    if tag_exists:
        cprint(f"  {YELLOW}Tag {tag} already exists locally — skipping tag step.{NC}")
    else:
        run(["git", "tag", "-a", tag, "-m", f"Release {tag}"], cwd=root)

    if dep_tag is None:
        # Warn loudly rather than silently omitting it: a silent skip is precisely how
        # this defect survived unnoticed across many releases.
        cprint(f"  {YELLOW}WARNING: cannot read the plugin name from "
               f".claude-plugin/plugin.json - SKIPPING the dependency tag. Dependent "
               f"plugins will fail to resolve this release with `no-matching-tag`.{NC}")
    elif dep_tag_exists:
        cprint(f"  {YELLOW}Tag {dep_tag} already exists locally — skipping.{NC}")
    else:
        run(["git", "tag", "-a", dep_tag, "-m", f"{_plugin_name(root)} {new_ver}"], cwd=root)

    # gh-auth precheck — fail fast with actionable error if gh missing/unauthed.
    owner, repo = _resolve_owner_repo(root)
    _ensure_gh_auth(owner, repo)
    # Atomic push: commit + tag land together or not at all. Eliminates the
    # half-published-state failure mode where `git push origin HEAD --tags`
    # could push the commit, fail on the tag (rejected/network), and leave
    # the remote with an unreleased commit + no tag. `--atomic` is a single
    # transaction in the wire protocol; the server rolls back if any ref
    # update fails. git_with_retry still wraps the call so transient
    # network hiccups (4xx-class permanent errors fall through immediately).
    cprint(f"  {BLUE}$ git push --atomic origin {' '.join(push_refs)}{NC}")
    git_with_retry(
        ["git", "push", "--atomic", "origin", *push_refs],
        cwd=str(root), capture_output=False,
    )
    _pushed = tag if dep_tag is None else f"{tag} + {dep_tag}"
    cprint(f"  {GREEN}Pushed {_pushed} atomically.{NC}")

def stage_gh_release(root: Path, new_ver: str, dry_run: bool) -> None:
    """Step 11: Create GitHub release via gh CLI.

    TRDD-bbff5bc5 §5: re-runs the gh-auth precheck before `gh release
    create` so an auth state change between gates 10 and 11 (token
    revoked, account switched) surfaces as an actionable error.
    """
    cprint(f"\n{BOLD}[11/11] Creating GitHub release...{NC}")
    tag = f"v{new_ver}"
    if not shutil.which("gh"):
        cprint(f"  {YELLOW}gh CLI not installed — skipping release.{NC}")
        return
    if dry_run:
        cprint(f"  Would create release: {tag}")
        return
    owner, repo = _resolve_owner_repo(root)
    _ensure_gh_auth(owner, repo)
    changelog_file = root / "CHANGELOG.md"
    # Use --notes-file when CHANGELOG exists (the git-cliff structured
    # release notes are the right thing to ship). Fall back to
    # --generate-notes only when no CHANGELOG is present. Passing both
    # flags simultaneously produces undefined behavior across gh versions
    # (some concatenate, some override) — never both.
    args = ["gh", "release", "create", tag, "--title", tag]
    if changelog_file.is_file():
        args.extend(["--notes-file", str(changelog_file)])
    else:
        args.append("--generate-notes")
    cprint(f"  {BLUE}$ {' '.join(args)}{NC}")
    result = gh_with_retry(args, cwd=str(root), check=False, capture_output=True)
    if result.stdout and result.stdout.strip():
        cprint(result.stdout.strip())
    if result.stderr and result.stderr.strip():
        print(result.stderr.strip(), file=sys.stderr)
    if result.returncode == 0:
        cprint(f"  {GREEN}Release created.{NC}")
        return
    # `gh release create` returns an "already_exists" / "already exists"
    # validation error when a release for this tag is already present. On a
    # re-run or interrupted-publish recovery that is the idempotent-success
    # outcome (the release IS there), so it must NOT abort — match either
    # spelling gh emits, case-insensitively.
    combined_err = f"{result.stdout or ''}\n{result.stderr or ''}"
    if re.search(r"already[ _]exists", combined_err, re.IGNORECASE):
        cprint(f"  {YELLOW}Release {tag} already exists — treating as success (idempotent re-run).{NC}")
        return
    # Any other non-zero exit is a genuine failure (auth revoked mid-pipeline,
    # malformed notes file, network exhausted all retries). The tag is already
    # pushed, but the documented final stage did NOT complete — abort so the
    # pipeline does not falsely report success (fail-fast invariant).
    cprint(f"  {RED}Failed to create release (exit code {result.returncode}).{NC}")
    cprint(f"  {RED}  The tag {tag} is pushed; create the release manually or re-run after fixing the cause.{NC}")
    sys.exit(1)


# -- Main ----------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Unified publish pipeline for Claude Code plugins.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    # Mutually exclusive modes: side-modes (--gate / --install-hook /
    # --install-branch-rules) are distinct entry points; --patch/--minor/--major
    # are OPTIONAL overrides for the auto-bump default. Calling publish.py with
    # no flags runs the full publish pipeline with an auto-detected bump type.
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument("--gate", action="store_true",
                            help="Pre-push gate mode: lint + copy-paste (jscpd) + validate + tests only (no bump/push)")
    mode_group.add_argument("--install-hook", action="store_true",
                            help="Install pre-push hook into .git/hooks/ and set core.hooksPath")
    mode_group.add_argument("--install-branch-rules", action="store_true",
                            dest="install_branch_rules",
                            help="Apply the cpv-branch-rules ruleset to the GitHub origin "
                                 "(enforces CI as a required status check — the server-side gate)")
    mode_group.add_argument("--patch", action="store_const", dest="bump", const="patch",
                            help="Force a patch bump (override auto-detection)")
    mode_group.add_argument("--minor", action="store_const", dest="bump", const="minor",
                            help="Force a minor bump (override auto-detection)")
    mode_group.add_argument("--major", action="store_const", dest="bump", const="major",
                            help="Force a major bump (override auto-detection)")
    parser.add_argument("--dry-run", action="store_true", help="Preview only, no changes")
    # NOTE: --skip-tests was intentionally removed. The cornerstone rule is that
    # every CPV plugin MUST pass validation with 0 issues (WARNING allowed) before
    # any push. Skipping tests would bypass that guarantee — there are no exceptions.
    args = parser.parse_args()

    root = get_repo_root()

    # --install-hook mode: just set up the hook and exit
    if args.install_hook:
        return install_hook(root)

    # --install-branch-rules mode: apply the server-side GitHub ruleset
    if args.install_branch_rules:
        return install_branch_rules(root)

    # --gate mode: run quality checks only (called by pre-push hook)
    if args.gate:
        return run_gate(root)

    # Full publish pipeline — auto-detect bump type unless user forced one.
    # Idempotency: read REMOTE plugin.json (origin/master) as the bump
    # baseline. When local is ahead (interrupted publish: bumped + committed
    # but not pushed), bumping from local would double-bump. From remote,
    # bumping recomputes the SAME target as the original interrupted run,
    # and stage_bump's "already-at-target" guard then skips the bump.
    local = get_current_version(root)
    if not local:
        cprint(f"{RED}Cannot read version from .claude-plugin/plugin.json{NC}")
        return 1
    remote = _read_remote_version(root)
    baseline = remote or local

    if args.bump is None:
        bump_type = detect_bump_type(root)
        cprint(f"{BLUE}Bump type: {bump_type} (auto-detected from git-cliff){NC}")
    else:
        bump_type = args.bump
        cprint(f"{BLUE}Bump type: {bump_type} (forced via --{bump_type}){NC}")

    new_ver = bump_semver(baseline, bump_type)
    if not new_ver:
        cprint(f"{RED}Cannot parse baseline version: {baseline}{NC}")
        return 1

    if remote and local != remote:
        cprint(f"{YELLOW}Local plugin.json is at {local} but origin is at {remote} — "
               f"using remote as bump baseline (interrupted-publish recovery).{NC}")
    current = baseline

    cprint(f"\n{BOLD}Publish pipeline: {current} -> {new_ver}{NC}")
    if args.dry_run:
        cprint(f"{YELLOW}(dry-run mode — no changes will be made){NC}")

    # Gate 0: reject bypass attempts BEFORE running any other stage.
    # Pipeline order (per the cornerstone rule "every push is a bump"):
    #   lint+typecheck → tests → validate → ci-preflight → marketplace-reg →
    #   consistency → bump → badge → changelog → commit → push → github release
    # Lint runs before tests (cheap fails first). Tests run before validate
    # so behavioral regressions fail the test suite before the structural
    # validator inspects the manifest.
    #
    # EVERY check above runs BEFORE stage_bump. That ordering is the whole point
    # of stage_ci_preflight: a CI-parity defect aborts the publish with the tree
    # untouched, instead of being discovered on GitHub after the tag was pushed
    # and the release was cut.
    stage_bypass_guard()
    stage_check_clean(root)
    stage_lint(root)
    stage_tests(root)  # MANDATORY — no skip flag, no exceptions
    stage_validate(root)
    stage_ci_preflight(root)  # MANDATORY — the gates validate_plugin omits
    stage_marketplace_registration(root)  # Gate 6 parity with CPV's own publish.py
    stage_consistency(root)
    stage_bump(root, new_ver, args.dry_run)
    stage_update_badges(root, current, new_ver, args.dry_run)
    stage_changelog(root, new_ver, args.dry_run)
    stage_commit_and_push(root, new_ver, args.dry_run)
    stage_gh_release(root, new_ver, args.dry_run)

    cprint(f"\n{GREEN}{BOLD}Published {new_ver} successfully!{NC}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''
    # Root-cause #2 + issue #137: route every CPV callsite in the generated
    # publish.py (G3 validate, Stage-4 validate, Stage-4b ci-preflight, the
    # branch-rules install) through `cpv_uvx_from_arg(p)`. For the default `git`
    # source this returns the pinned `git+https://…@<ref>` form (byte-identical
    # to the historical output — the cold uvx-from-git build stays cached per tag
    # and a stricter future CPV release cannot silently break this gate). For the
    # `pypi` source it returns `claude-plugins-validation==<ver>`. `str.replace`
    # rewrites EVERY occurrence, so a new CPV callsite added to the template is
    # routed automatically — but it must spell the URL as the exact bare literal
    # below, and it must spell `--with pyyaml` as one of the three forms handled
    # just after, or the pypi variant would ship a stale `--with pyyaml` shim.
    result = template.replace(
        "git+https://github.com/Emasoft/claude-plugins-validation",
        cpv_uvx_from_arg(p),
    )
    # Issue #137: the published `pypi` wheel declares pyyaml as a runtime
    # dependency, so drop the `--with pyyaml` shim from every inline argv list in
    # the generated publish.py. The three distinct argv spellings in the template
    # are removed by exact-literal replace (each is applied to ALL of its
    # occurrences); the `git` source leaves them untouched (byte-identical
    # output). The newline+indent in each match keeps the surrounding argv
    # structure intact.
    if not cpv_uvx_needs_pyyaml(p):
        result = result.replace(
            '                "--with",\n                "pyyaml",\n',
            "",
        )
        result = result.replace(
            '         "--with", "pyyaml",\n',
            "",
        )
        result = result.replace(
            '        "--with", "pyyaml",\n',
            "",
        )

    # PROFILE VARIANT (TRDD-e9f13df1, issue #128). For every profile EXCEPT
    # `submodule-build` the body is returned byte-identical to the historical
    # output above (HARD guarantee — the standard regression test pins it; an
    # unknown profile also takes this path, fail-safe). For `submodule-build`
    # we APPEND a marker-delimited section carrying the four PSS load-bearing
    # behaviors; appending (never editing the standard body) is what keeps the
    # standard path provably unchanged.
    if profile == PROFILE_SUBMODULE_BUILD:
        result += _gen_publish_py_submodule_section()
    return result


# Markers that delimit the appended submodule-build section. They double as the
# detector's signature: the drift check compares a submodule-build plugin's
# publish.py against `gen_publish_py(params, "submodule-build")`, so the variant
# is recognized when (and only when) it carries this exact section verbatim.
_SUBMODULE_BUILD_SECTION_BEGIN = (
    "# === BEGIN submodule-build profile section (TRDD-e9f13df1, issue #128) ==="
)
_SUBMODULE_BUILD_SECTION_END = (
    "# === END submodule-build profile section (TRDD-e9f13df1, issue #128) ==="
)


def _gen_publish_py_submodule_section() -> str:
    r"""Return the additive `submodule-build` publish.py section text.

    This is appended verbatim to the standard publish.py body by
    ``gen_publish_py(p, PROFILE_SUBMODULE_BUILD)``. It is a self-contained
    block of Python (it imports its own stdlib deps and re-derives ``ROOT``) so
    it parses and runs even though the standard body owns the actual pipeline
    entry point. The functions here are HELPERS the maintainer wires into their
    submodule-aware release flow; the section is deliberately import-safe and
    side-effect-free at module load (nothing runs at import time).

    It models, verbatim from PSS's real submodule-aware publish.py
    (``Emasoft/perfect-skill-suggester``), the four load-bearing behaviors the
    standard template lacks:

      1. ``submodule_source_changed`` — the #128 source-change-detection FIX.
         It runs ``git -C <submodule> diff <last-tag-or-sha> -- <src-globs>``
         INSIDE the submodule, so it sees the submodule's own files. The
         standard preflight uses a PARENT-repo glob, which only ever sees the
         ``160000`` gitlink and therefore ships STALE binaries.
      2. ``submodule_commit_before_gitlink`` — commit the version bump INSIDE
         the submodule first, THEN stage the moved gitlink in the parent.
      3. ``ensure_submodule_pushed`` — push the submodule remote BEFORE the
         parent push, so the parent gitlink never points at an unpushed sha
         ("not our ref" on clone).
      4. ``submodule_clean_tree_ok`` — a gitlink-tolerant clean-tree preflight:
         a ``160000`` gitlink whose submodule HEAD has moved is an EXPECTED
         release-time change, NOT a "dirty tree" abort.
    """
    # NOTE: this is the TEXT of generated Python, kept as a raw triple-quoted
    # string. The leading "\n\n\n" separates it from the standard body's
    # trailing `sys.exit(main())`. Backslashes are literal (raw string).
    return (
        "\n\n\n"
        + _SUBMODULE_BUILD_SECTION_BEGIN
        + r'''
# These helpers add submodule-aware behavior the standard canonical publish.py
# does not carry. A `submodule-build` plugin (build sources in a git submodule
# + pre-compiled binaries committed to bin/) wires them into its release flow.
# They are import-safe (nothing runs at module load) and side-effect-free until
# called. Modeled on Emasoft/perfect-skill-suggester's real publish.py.
# These imports sit mid-file (the section is appended after the standard body)
# so E402 is expected and silenced — the standard body already imported the same
# stdlib at the top; the aliased re-imports keep this section self-contained.
import subprocess as _sub  # noqa: E402
from pathlib import Path as _Path  # noqa: E402

# The plugin root (this file lives in <root>/scripts/publish.py).
_SUBMOD_ROOT = _Path(__file__).resolve().parent.parent


def _submod_run(cmd, **kwargs):
    """Run a git command from the plugin root, capturing output (text)."""
    kwargs.setdefault("cwd", str(_SUBMOD_ROOT))
    kwargs.setdefault("capture_output", True)
    kwargs.setdefault("text", True)
    return _sub.run(cmd, **kwargs)  # noqa: S603 — fixed git argv, no shell


def _build_source_submodule_paths():
    """Every build-source submodule path registered in the parent .gitmodules.

    A DEV/test submodule (strip-dev-parts: tests/dev/docs/...) is excluded — it
    is not a build source. Returns submodule paths relative to the plugin root.
    """
    gm = _SUBMOD_ROOT / ".gitmodules"
    if not gm.is_file():
        return []
    dev_hints = {"tests", "test", "dev", "docs", "doc", "examples", "samples", "fixtures"}
    paths = []
    for line in gm.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if line.startswith("path") and "=" in line:
            rel = line.split("=", 1)[1].strip().rstrip("/")
            if rel and rel.split("/", 1)[0].lower() not in dev_hints:
                paths.append(rel)
    return paths


def submodule_source_changed(submodule_path, src_globs=("src",)):
    """True iff build-source files changed in the submodule since the last tag.

    THE #128 FIX. The change is detected with
        git -C <submodule_path> diff <last_tag> HEAD -- <src_globs>
    run INSIDE the submodule, so it inspects the submodule's OWN files. The
    standard preflight detects rebuild-need with a PARENT-repo source glob,
    which only ever resolves to the `160000` gitlink (never the submodule's
    files) and therefore concludes "no source change" and ships STALE binaries.

    No tag yet → assume changed (force the build). Any git error → assume
    changed (fail safe toward rebuilding, never toward a stale ship).
    """
    sub = _SUBMOD_ROOT / submodule_path
    if not (sub / ".git").exists():
        # Not initialized as a submodule on this checkout — fall back to a
        # parent-repo diff of the submodule path (best effort).
        res = _submod_run(["git", "describe", "--tags", "--abbrev=0"])
        last_tag = res.stdout.strip() if res.returncode == 0 else ""
        if not last_tag:
            return True
        diff = _submod_run(["git", "diff", "--name-only", last_tag, "HEAD", "--", submodule_path])
        return bool(diff.stdout.strip())
    # Inside the submodule: find ITS last tag (fall back to the parent tag),
    # then diff the source globs there.
    tag_res = _submod_run(["git", "-C", str(sub), "describe", "--tags", "--abbrev=0"])
    last_tag = tag_res.stdout.strip() if tag_res.returncode == 0 else ""
    if not last_tag:
        parent_tag = _submod_run(["git", "describe", "--tags", "--abbrev=0"])
        last_tag = parent_tag.stdout.strip() if parent_tag.returncode == 0 else ""
    if not last_tag:
        return True
    args = ["git", "-C", str(sub), "diff", "--name-only", last_tag, "HEAD", "--", *src_globs]
    diff = _submod_run(args)
    if diff.returncode != 0:
        return True  # fail safe: can't prove "unchanged" → rebuild
    return bool(diff.stdout.strip())


def submodule_gitlink_moved(submodule_path):
    """True iff the parent's tree records a submodule sha != the submodule HEAD.

    A moved gitlink (`160000`) is the EXPECTED state during a release that bumps
    the submodule — it is NOT a dirty working tree to abort on.
    """
    sub = _SUBMOD_ROOT / submodule_path
    if not (sub / ".git").exists():
        return False
    tree = _submod_run(["git", "ls-tree", "HEAD", submodule_path])
    if tree.returncode != 0 or not tree.stdout.strip():
        return False
    parts = tree.stdout.split()
    if len(parts) < 3 or parts[0] != "160000":
        return False
    recorded = parts[2]
    head = _submod_run(["git", "-C", str(sub), "rev-parse", "HEAD"])
    if head.returncode != 0:
        return False
    return recorded != head.stdout.strip()


def submodule_clean_tree_ok():
    """Gitlink-tolerant clean-tree preflight.

    Returns True when the working tree is clean APART FROM build-source
    submodule gitlinks whose HEAD has legitimately moved for this release. A
    plain `git status --porcelain` would flag those moved gitlinks as a dirty
    tree and abort the release; this preflight tolerates them (and ONLY them).
    Any OTHER modification still makes the tree dirty.
    """
    status = _submod_run(["git", "status", "--porcelain"])
    if status.returncode != 0:
        return False
    submods = set(_build_source_submodule_paths())
    for line in status.stdout.splitlines():
        entry = line[3:].strip() if len(line) > 3 else line.strip()
        if entry in submods and submodule_gitlink_moved(entry):
            continue  # an expected, release-time gitlink move — tolerated
        if entry:
            return False  # a real uncommitted change → dirty
    return True


def submodule_commit_before_gitlink(submodule_path, new_version, sub_files=("Cargo.toml", "Cargo.lock")):
    """Commit the version bump INSIDE the submodule first, then stage the gitlink.

    Order is load-bearing: committing inside the submodule advances its HEAD;
    only AFTERWARD does staging the submodule path in the parent capture the new
    gitlink. Doing it in the reverse order stages a stale (or no) gitlink.
    """
    sub = _SUBMOD_ROOT / submodule_path
    if not (sub / ".git").exists():
        return
    staged = [str(sub / f) for f in sub_files if (sub / f).exists()]
    if staged:
        add = _submod_run(["git", "-C", str(sub), "add", *staged])
        if add.returncode != 0:
            raise SystemExit(f"submodule git add failed: {add.stderr.strip()}")
        commit = _submod_run(["git", "-C", str(sub), "commit", "-m", f"chore(release): {new_version}"])
        if commit.returncode != 0 and "nothing to commit" not in (commit.stdout + commit.stderr).lower():
            raise SystemExit(f"submodule git commit failed: {commit.stderr.strip()}")
    # Stage the MOVED gitlink in the parent (after the submodule HEAD advanced).
    stage = _submod_run(["git", "add", submodule_path])
    if stage.returncode != 0:
        raise SystemExit(f"git add (gitlink) failed: {stage.stderr.strip()}")


def ensure_submodule_pushed(submodule_path):
    """Push the submodule remote BEFORE the parent push.

    If the parent gitlink references a commit the submodule remote does not yet
    have, the parent must NOT be pushed first — a clone would fail with
    'not our ref'. Push the submodule, then let the caller push the parent.
    """
    sub = _SUBMOD_ROOT / submodule_path
    if not (sub / ".git").exists():
        return
    tree = _submod_run(["git", "ls-tree", "HEAD", submodule_path])
    if tree.returncode != 0 or not tree.stdout.strip():
        return
    parts = tree.stdout.split()
    if len(parts) < 3:
        return
    ref = parts[2]
    have = _submod_run(["git", "-C", str(sub), "fetch", "--dry-run", "origin", ref], timeout=30)
    if have.returncode == 0:
        return  # already on the remote
    push = _submod_run(["git", "-C", str(sub), "push"])
    if push.returncode != 0:
        raise SystemExit(
            f"submodule push failed: {push.stderr.strip()}\n"
            f"  Run 'git -C {submodule_path} push' manually before retrying."
        )
'''
        + _SUBMODULE_BUILD_SECTION_END
        + "\n"
    )


def gen_cpv_network_resilience_py() -> str:
    """Emit scripts/cpv_network_resilience.py for the new plugin.

    The new plugin's publish.py imports gh_with_retry / git_with_retry from
    this module to apply the documented retry pattern (~/.claude/rules/
    github-timeouts.md) to its own pushes and gh release calls. Shipping
    a verbatim copy keeps every plugin byte-identical on the resilience
    surface, so a fix landed in CPV propagates via `cpv standardize
    --force-templates` to every plugin in one go.
    """
    src = Path(__file__).resolve().parent / "cpv_network_resilience.py"
    return src.read_text(encoding="utf-8")


def gen_setup_hooks_py() -> str:
    """Generate scripts/setup-hooks.py — installs git hooks from git-hooks/ into .git/hooks/."""
    return '''#!/usr/bin/env python3
"""Install git hooks from git-hooks/ into .git/hooks/.

Usage: uv run python scripts/setup-hooks.py
"""

from __future__ import annotations

import shutil
import stat
import sys
from pathlib import Path


def get_repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def main() -> int:
    repo_root = get_repo_root()
    source_dir = repo_root / "git-hooks"
    target_dir = repo_root / ".git" / "hooks"

    if not source_dir.is_dir():
        print(f"ERROR: {source_dir} does not exist.", file=sys.stderr)
        return 1
    if not target_dir.is_dir():
        print(f"ERROR: {target_dir} does not exist. Is this a git repo?",
              file=sys.stderr)
        return 1

    hooks = [h for h in source_dir.iterdir() if not h.name.startswith(".")]
    if not hooks:
        print("No hooks found in git-hooks/.")
        return 0

    for hook_src in hooks:
        hook_dst = target_dir / hook_src.name
        shutil.copy2(hook_src, hook_dst)
        hook_dst.chmod(hook_dst.stat().st_mode
                       | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        print(f"  Installed: {hook_src.name} -> .git/hooks/{hook_src.name}")

    print(f"\\nDone. {len(hooks)} hook(s) installed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''


def gen_hooks_json(p: PluginParams) -> str:
    """Generate hooks/hooks.json — SessionStart hook to install deps into ${CLAUDE_PLUGIN_DATA}.

    Per official Anthropic docs, runtime dependencies should be installed into
    ${CLAUDE_PLUGIN_DATA} (persists across plugin updates) rather than
    ${CLAUDE_PLUGIN_ROOT} (wiped on every update).
    """
    _ = p  # unused but kept for consistent signature
    return """{
  "hooks": {
    "SessionStart": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "diff -q \\"${CLAUDE_PLUGIN_ROOT}/pyproject.toml\\" \\"${CLAUDE_PLUGIN_DATA}/pyproject.toml\\" >/dev/null 2>&1 || (cp \\"${CLAUDE_PLUGIN_ROOT}/pyproject.toml\\" \\"${CLAUDE_PLUGIN_DATA}/\\" && cd \\"${CLAUDE_PLUGIN_DATA}\\" && uv venv --python 3.12 -q && uv pip install -q -r \\"${CLAUDE_PLUGIN_ROOT}/pyproject.toml\\") || rm -f \\"${CLAUDE_PLUGIN_DATA}/pyproject.toml\\"",
            "statusMessage": "Installing plugin dependencies...",
            "timeout": 120
          }
        ]
      }
    ]
  }
}
"""


def gen_pre_push_hook(p: PluginParams) -> str:
    """Generate git-hooks/pre-push — a branch-aware POSIX-sh gate (issue #169).

    Two paths, one code flow:

    * A push to the DEFAULT branch (main/master) or ANY tag is a RELEASE. It
      still goes through ``publish.py --gate`` — the full release gate that
      requires publish.py process ancestry plus lint/validate/tests. Unchanged.
    * A push to any OTHER (feature) branch is ALLOWED so a fleet / reviewer / CI
      can share the branch — but ONLY after a passing secret scan of the commits
      being pushed. The scan is trufflehog over just the new commit range (so
      inherited history / test fixtures are never re-flagged). If trufflehog is
      not installed the push FAILS CLOSED — an unscanned push is never silently
      allowed.

    Server-side branch rulesets still gate the default branch behind PR +
    required checks, so a shared feature branch can never reach the default
    branch around the release gate.
    """
    _ = p  # unused but kept for consistent signature
    return """#!/usr/bin/env sh
# Pre-push hook — branch-aware gate (see gen_pre_push_hook docstring).
#   * default branch (main/master) or any tag -> full publish.py --gate
#     (release gate: publish.py process ancestry + lint/validate/tests)
#   * any other (feature) branch -> allowed, but ONLY after a passing
#     secret scan of the pushed commits (trufflehog). trufflehog absent =>
#     FAIL CLOSED (an unscanned push is never silently allowed).
# POSIX sh, fail-fast, single code path. Auto-generated by scripts/publish.py's
# generator — do NOT edit by hand; the pipeline rewrites it from the template.
set -u

REPO_ROOT="$(git rev-parse --show-toplevel)" || exit 1
cd "$REPO_ROOT" || exit 1

ZERO=0000000000000000000000000000000000000000

# Default branch name from origin/HEAD (e.g. "main"/"master"), or empty.
default_branch="$(git symbolic-ref --quiet --short refs/remotes/origin/HEAD 2>/dev/null | sed 's#^origin/##')"

# Full release gate: only publish.py may push the default branch / tags.
run_release_gate() {
    if command -v uv >/dev/null 2>&1; then
        uv run python scripts/publish.py --gate
    else
        python3 scripts/publish.py --gate
    fi
}

# Secret-scan ONLY the new commits of a feature-branch push.
#   $1 local sha   $2 remote sha   $3 short branch name
# Returns 0 (clean -> allow) or non-zero (secret found / cannot scan -> block).
secret_scan_range() {
    if ! command -v trufflehog >/dev/null 2>&1; then
        echo "BLOCKED: trufflehog is not installed — a feature-branch push must be" >&2
        echo "         secret-scanned first. Install trufflehog" >&2
        echo "         (https://github.com/trufflesecurity/trufflehog)" >&2
        echo "         or push through scripts/publish.py." >&2
        return 1
    fi
    # Scan from the commit already on the remote (nothing new before it). For a
    # brand-new branch (remote side all-zeros) start from the default branch tip
    # so inherited history is not re-scanned; if none resolves, scan the whole
    # branch (a fresh repo has no inherited content to false-positive on).
    base=""
    if [ "$2" != "$ZERO" ]; then
        base="$2"
    elif [ -n "$default_branch" ]; then
        base="$(git rev-parse --verify --quiet "origin/$default_branch" 2>/dev/null \
             || git rev-parse --verify --quiet "$default_branch" 2>/dev/null || true)"
    fi
    if [ -n "$base" ]; then
        trufflehog git "file://$REPO_ROOT" --since-commit "$base" --branch "$3" --no-update --fail
    else
        trufflehog git "file://$REPO_ROOT" --branch "$3" --no-update --fail
    fi
}

release_push=0
saw_feature=0
scan_failed=0

# git feeds "<local ref> <local sha> <remote ref> <remote sha>" lines on stdin.
while read -r localref localsha remoteref remotesha; do
    [ -z "$localref" ] && continue
    [ "$localsha" = "$ZERO" ] && continue    # ref deletion — nothing to gate/scan
    case "$remoteref" in
        refs/tags/*|refs/heads/main|refs/heads/master)
            release_push=1
            ;;
        refs/heads/*)
            short=${remoteref#refs/heads/}
            if [ -n "$default_branch" ] && [ "$short" = "$default_branch" ]; then
                release_push=1
            else
                saw_feature=1
                secret_scan_range "$localsha" "$remotesha" "$short" || scan_failed=1
            fi
            ;;
        *)
            release_push=1    # unknown ref shape — gate conservatively
            ;;
    esac
done

# A release ref is present, or nothing parsed -> enforce the full release gate.
if [ "$release_push" -eq 1 ] || [ "$saw_feature" -eq 0 ]; then
    run_release_gate
    exit $?
fi

# Feature-branch-only push: allowed iff every secret scan passed.
if [ "$scan_failed" -eq 1 ]; then
    echo "BLOCKED: secret scan failed for a feature-branch push (see above)." >&2
    exit 1
fi
exit 0
"""


def gen_ci_yml(p: PluginParams) -> str:
    """Generate .github/workflows/ci.yml — single consolidated CI workflow.

    Required-status-check contexts: the branch ruleset
    (setup_branch_rules.DEFAULT_PLUGIN_CHECK_CONTEXTS) requires the THREE bare
    contexts "Lint" / "Validate" / "Test". The first two are produced by jobs
    whose display name is exactly that. "Test", however, is a MATRIX (ubuntu +
    macOS, each split into serial pytest-split shards) — a matrix job reports
    SUFFIXED contexts ("Test matrix (ubuntu-latest, 1)" / "Test matrix
    (macos-latest, 2)"), never a bare "Test". To
    keep the required bare "Test" context satisfiable (root-cause #3: otherwise
    the PR is stuck "pending" forever / auto-merge never fires), a lightweight
    AGGREGATE GATE job named EXACTLY "Test" (`needs: [test]`) succeeds iff the
    matrix passed and is what produces the bare "Test" context.

    Jobs:
      - lint           : actionlint (workflow syntax) + Mega-Linter — reports "Lint"
      - validate       : uvx cpv-remote-validate plugin . --strict (issue #11) — reports "Validate"
      - test           : pytest MATRIX (ubuntu + macOS, serial shards) — reports "Test matrix (<os>, <shard>)"
      - test-gate       : aggregate gate, `needs: [test]` — reports the required bare "Test"
      - commitlint     : conventional-commit gate (pull_request only)

    Triggers on both master and main branches (handles repos renamed either way).
    Includes merge_group for GitHub merge-queue / auto-merge support.

    v2.86.0 hardening (issue #22):
    * SHA-pin every third-party action (gh-actions.md §"Pin third-party
      actions to a full commit SHA"). Major-tag aliases re-point silently
      on the upstream side, so a hostile tag rewrite would otherwise let
      an attacker swap action code. The trailing `# vX.Y.Z` comment is
      pinact-compatible — pinact run will keep it in sync on uv lock.
    * actionlint step in the lint job catches workflow YAML syntax
      regressions BEFORE they hit production (e.g. a stray `SCANDIR
      env:` block, a malformed matrix dimension, an undefined ${{ secrets.X
      }} reference). Cheap-fail-first ordering: workflow syntax errors
      should not waste a full mega-linter run.
    * commitlint on PRs rejects non-conventional commits at the door so
      git-cliff's --bump and the CHANGELOG generator never see a junk
      subject line. It reads the repo's `.commitlintrc.json`
      (:func:`gen_commitlintrc_json`) — config-conventional with
      `body-max-line-length` disabled, so a bot's machine-generated commit
      body cannot fail the gate (RC-1) while a human's badly-typed subject
      still does.
    * macOS matrix on the test job catches darwin-specific regressions
      (pathlib casing, mtime resolution, BSD `ps` vs procps-ng output).
      `fail-fast: false` so each OS reports its own failure even when
      one fails.

    v2.86.0+ follow-on hardening (issues #90 / #114 / #180):
    * Every job declares `timeout-minutes` so a hung action fails fast instead
      of burning the 360-min default (#90).
    * The validate job's timeout is a VALIDATION budget, not a build budget.
      The original #114 framing — that the cap had to cover a 12-20 min cold
      `uvx --from git+…` source build — no longer describes where the time
      goes. With the UV cache plus the `~/.cache/cpv` cache below, a field
      report measured the CPV build finishing 4 SECONDS into the step while the
      step still ran to the cap (#180). So do NOT read a timeout here as a
      fetch/build stall and go chasing the git ref or the pin; the budget is
      spent inside `cpv-remote-validate`. The validator now bounds its own
      long phases (the dead-link sweep got an aggregate budget in #180, as REPO
      LINT did in #162), and the step `tee`s its output so a killed run still
      shows what was in flight.
    * First-party `actions/*` are SHA-pinned too (not only third-party), so
      a hostile first-party tag rewrite cannot swap action code.
    """
    # Issue #137 routing (git vs pypi CPV source) + the RC-8 exit-code triage
    # now live in the shared gen_cpv_validate_run_block, so ci.yml and
    # release.yml can never drift apart on either. The report is captured to
    # $RUNNER_TEMP — OUTSIDE the checkout — so the validator never scans its own
    # (empty, half-written) output file (the v2.152.1 self-scan gotcha).
    validate_block = gen_cpv_validate_run_block(p, "$RUNNER_TEMP/cpv-validation-report.txt")
    # Sharded test matrix (RC-9): the shard count drives BOTH the matrix
    # dimension and the `--splits` flag, and pins the pytest-split dev-extra
    # requirement in gen_pyproject_toml.
    shard_groups = ", ".join(str(i) for i in range(1, TEST_SHARD_COUNT + 1))
    return f"""name: CI

# Required-context contract (cpv-setup-branch-rules wires the branch ruleset
# to the bare contexts Lint / Validate / Test — TRDD-bbff5bc5):
#   * "Lint" and "Validate" are produced by jobs named exactly that.
#   * "Test" is a MATRIX (ubuntu + macOS, serial shards), so the matrix lanes
#     report "Test matrix (<os>, <shard>)", NOT a bare "Test". The aggregate job named
#     exactly "Test" (needs: [test]) produces the required bare "Test" context
#     and succeeds only if the matrix passed. Do NOT rename these without also
#     updating setup_branch_rules.DEFAULT_PLUGIN_CHECK_CONTEXTS.

on:
  push:
    branches: [master, main]
  pull_request:
    branches: [master, main]
  merge_group:

permissions:
  contents: read

concurrency:
  group: ${{{{ github.workflow }}}}-${{{{ github.ref }}}}
  cancel-in-progress: true

jobs:
  lint:
    name: Lint
    runs-on: ubuntu-latest
    # Hard cap so a hung action / network flake doesn't burn the 360-min
    # default. Mega-Linter on a fresh repo is the slow step (~5-8 min);
    # 20 min is generous headroom (issue #90).
    timeout-minutes: 20
    permissions:
      contents: read
      issues: write
      pull-requests: write
    steps:
      - uses: actions/checkout@df4cb1c069e1874edd31b4311f1884172cec0e10 # v6.0.3
        with:
          fetch-depth: 0
          # Don't persist the checkout token in .git/config — zizmor flags
          # `artipacked` (credential persistence) otherwise (issue #151).
          persist-credentials: false

      # Cheap-fail-first: workflow-syntax errors before mega-linter.
      - name: Lint workflow YAML (actionlint)
        uses: rhysd/actionlint@914e7df21a07ef503a81201c76d2b11c789d3fca # v1.7.12

      - name: Mega-Linter
        uses: oxsecurity/megalinter@e08c2b05e3dbc40af4c23f41172ef1e068a7d651 # v8
        env:
          GITHUB_TOKEN: ${{{{ secrets.GITHUB_TOKEN }}}}
          VALIDATE_ALL_CODEBASE: false

      - name: Upload Mega-Linter reports
        if: always()
        uses: actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a # v7.0.1
        with:
          name: mega-linter-reports
          path: |
            megalinter-reports/
            mega-linter.log

  commitlint:
    name: Commitlint
    runs-on: ubuntu-latest
    # Single git-history scan — 10 min is ample (issue #90).
    timeout-minutes: 10
    # PRs only — push events on main don't need conventional-commit
    # enforcement (the commits are already merged).
    if: github.event_name == 'pull_request'
    steps:
      - uses: actions/checkout@df4cb1c069e1874edd31b4311f1884172cec0e10 # v6.0.3
        with:
          fetch-depth: 0
          # zizmor artipacked: don't persist the token in .git/config (issue #151).
          persist-credentials: false
      - name: Conventional-commits gate
        # Reads the repo's .commitlintrc.json (RC-1): config-conventional with
        # `body-max-line-length` OFF. Dependabot's machine-generated commit body
        # embeds a multi-line YAML dependency block that ALWAYS exceeds the
        # 100-char default, so with the bare fallback config EVERY Dependabot PR
        # on EVERY cpv-canonical-pipeline plugin failed this gate, forever. The gate
        # itself is NOT weakened — type-enum / subject rules still reject a
        # badly-typed human commit.
        #
        # Pinned to the COMMIT sha of v6.2.1, not the annotated-tag-object sha.
        # `git rev-parse v6.2.1` returns the tag OBJECT (6cf16ef…), which is NOT
        # a commit — `gh api .../commits/6cf16ef…` 404s and zizmor flags
        # ref-version-mismatch. Deref to the commit it points at (issue #151).
        uses: wagoid/commitlint-github-action@b948419dd99f3fd78a6548d48f94e3df7f6bf3ed # v6.2.1

  zizmor:
    # GitHub-Actions-specific static analysis (issue #151). Mega-Linter's
    # Checkov/Trivy do NOT fully cover GHA workflows, so keep a dedicated
    # zizmor pass. advanced-security (the action default) uploads SARIF to
    # code-scanning, which needs security-events: write; everything else is
    # least-privilege read.
    name: Workflow Security
    runs-on: ubuntu-latest
    timeout-minutes: 10
    permissions:
      contents: read
      security-events: write
    steps:
      - uses: actions/checkout@df4cb1c069e1874edd31b4311f1884172cec0e10 # v6.0.3
        with:
          # zizmor artipacked: don't persist the token in .git/config (issue #151).
          persist-credentials: false
      - name: Run zizmor
        uses: zizmorcore/zizmor-action@192e21d79ab29983730a13d1382995c2307fbcaa # v0.5.7

  validate:
    name: Validate
    runs-on: ubuntu-latest
    # VALIDATION budget (issues #90 + #114 + #180) — not a build budget.
    # A timeout here is NOT a git-fetch or cold-build stall: with the caches
    # below, the CPV build was measured finishing ~4s into the step while the
    # step still ran to this cap (#180). Do not chase the ref or the pin. Read
    # the step log — it is tee'd, so a killed run still shows what was running.
    # 30 min with headroom; do NOT lower it below 25.
    timeout-minutes: 30
    steps:
      # Plain checkout — scaffolded plugins ship NO submodules; asking the
      # checkout action to recurse occasionally flakes with "could not read
      # Username" auth errors against non-existent submodule URLs, taking
      # the Validate job down with a misleading 'process git failed exit
      # code 128'. Drop the recurse to remove the moot enumeration step.
      - uses: actions/checkout@df4cb1c069e1874edd31b4311f1884172cec0e10 # v6.0.3
        with:
          # zizmor artipacked: don't persist the token in .git/config (issue #151).
          persist-credentials: false

      - name: Install uv
        # enable-cache: true keys an actions/cache on UV_CACHE_DIR so the
        # cold `uvx --from git+…` build of CPV (12-20 min, issue #114) is
        # paid ONCE per lockfile/runner; every later run restores the warm
        # uv cache and resolves in seconds.
        uses: astral-sh/setup-uv@fac544c07dec837d0ccb6301d7b5580bf5edae39 # v8.2.0
        with:
          enable-cache: true

      - name: Set up Python
        run: uv python install {p.python_version}

      - name: Install dependencies
        run: uv sync --extra dev

      # Cross-run CPV scan-cache (optional, big win for fix loops).
      # The cache key includes the hash of .cpv-self-hashes.json so any
      # change to the validator's own source busts the cache automatically;
      # the restore-keys fallback still hits a same-OS warm cache on the
      # first run after a CPV bump so we don't pay the full cold-scan cost.
      # actions/cache@v5.0.5 is the latest SHA at scaffold time —
      # pinact-compatible (the `# v5.0.5` comment is kept in sync on `uv
      # lock`). gh-actions.md §"Pin third-party actions to a full commit SHA".
      - name: Restore CPV scan-cache
        uses: actions/cache@27d5ce7f107fe9357f9df03efb73ab90386fccae # v5.0.5
        with:
          path: ~/.cache/cpv
          key: cpv-scan-cache-${{{{ runner.os }}}}-${{{{ hashFiles('**/.cpv-self-hashes.json') }}}}
          restore-keys: |
            cpv-scan-cache-${{{{ runner.os }}}}-

      - name: Run plugin validation (remote CPV, --strict)
        # Fetches CPV from GitHub via uvx so downstream plugins do not need to
        # vendor scripts/validate_plugin.py. Matches publish.py's local gate
        # so CI and local gate agree. Issue #11: do NOT call local
        # scripts/validate_plugin.py — it does not exist in scaffolded plugins.
        # Root-cause #2: CPV is PINNED to @{p.cpv_ref_resolved} so the cold
        # uvx-from-git build is cached per tag and a stricter future CPV
        # release cannot break this gate with no plugin change.
        env:
          # Root-cause #4: on a fresh-checkout runner the local self-hash
          # manifest already matches the code, so CPV's GitHub-anchored
          # integrity fetch (a live urlopen to raw.githubusercontent.com) adds
          # no security but real latency/hang risk. Skip it.
          # Issue #140: this step deliberately does NOT pass the repo owner into
          # CPV's private-usernames allowlist. That allowlist is the set of names
          # to treat as PRIVATE, so seeding it with the PUBLIC owner makes CPV
          # flag every github.com/<owner>/ URL + the owner no-reply email as a
          # CRITICAL private-path leak, red-lighting --strict CI. In CI there is
          # no developer local-username to protect; the public owner is public.
          PLUGIN_SKIP_GITHUB_INTEGRITY: "1"
          # Issue #162: cap the AGGREGATE REPO LINT wall-clock. Each linter is
          # already per-linter-bounded, but on a cold runner uv/npm serialize the
          # concurrent uvx/npx first-run fetches on a global cache lock, so the
          # ~17-linter fan-out degrades toward serial and the phase can march past
          # this job's own timeout-minutes with orphaned uv/python children. 600s
          # is well under this job's ceiling yet ~17x the warm ~35s run, so it
          # never false-skips a healthy cold run. Raise it to tune, or set
          # PLUGIN_SKIP_REPO_LINT=1 if Mega-Linter already lints this repo.
          PLUGIN_REPO_LINT_PHASE_TIMEOUT: "600"
        run: |
          {validate_block}

  test:
    # NOTE: this is the test MATRIX. Its display name is "Test matrix" so the
    # lanes report "Test matrix (ubuntu-latest, 1)" / "Test matrix (macos-latest, 2)"
    # — NOT a bare "Test". The bare "Test" required context is produced by the
    # `test-gate` aggregate job below (root-cause #3). The job ID stays `test`.
    name: Test matrix
    # Two matrix dimensions:
    #   * os: [ubuntu-latest, macos-latest] — macOS added v2.86.0 (issue #22),
    #     catches darwin-specific regressions ubuntu-only runs miss (pathlib
    #     casing, mtime resolution, BSD `ps` vs procps-ng output).
    #   * group: [1, 2] — duration-balanced SERIAL test shards via pytest-split
    #     (TRDD-K7P2XR4Q). Each shard runs pytest WITHOUT -n (serial), so an
    #     order-dependent serial-pollution bug still surfaces within a shard;
    #     count-based split (no committed .test_durations needed — pytest-split
    #     degrades gracefully, fine for a small downstream suite). N=2 keeps the
    #     per-shard setup overhead modest on small suites.
    #     `--splits`/`--group` come from the pytest-split plugin, which the dev
    #     extra MUST declare (RC-9) — pyproject.toml's `dev` list and this matrix
    #     are both driven by generate_plugin_repo.TEST_SHARD_COUNT /
    #     PYTEST_SPLIT_REQUIREMENT so they cannot desync.
    # fail-fast: false so every (os, shard) leg reports its own failure.
    strategy:
      fail-fast: false
      matrix:
        os: [ubuntu-latest, macos-latest]
        group: [{shard_groups}]
    runs-on: ${{{{ matrix.os }}}}
    # Hard cap so a hung test / dependency install doesn't burn the 360-min
    # default. The warm uv cache keeps installs fast; 25 min covers a cold
    # cache plus a real test suite on both runners (issue #90).
    timeout-minutes: 25
    steps:
      - uses: actions/checkout@df4cb1c069e1874edd31b4311f1884172cec0e10 # v6.0.3
        with:
          # zizmor artipacked: don't persist the token in .git/config (issue #151).
          persist-credentials: false

      - name: Install uv
        uses: astral-sh/setup-uv@fac544c07dec837d0ccb6301d7b5580bf5edae39 # v8.2.0
        with:
          enable-cache: true

      - name: Set up Python
        run: uv python install {p.python_version}

      - name: Install dependencies
        run: uv sync --extra dev

      - name: Run tests (shard ${{{{ matrix.group }}}} of {TEST_SHARD_COUNT})
        run: |
          if [ -d "tests" ] && ls tests/test_*.py 1>/dev/null 2>&1; then
            set +e
            uv run pytest tests/ --splits {TEST_SHARD_COUNT} --group ${{{{ matrix.group }}}} -v
            code=$?
            set -e
            # Exit 5 = pytest "no tests collected": a legitimately-empty shard
            # when the suite has fewer tests than splits (common on small
            # downstream suites — the split simply put every test in the other
            # group). That is NOT a failure — treat it as a pass; propagate any
            # other non-zero code as a real failure.
            if [ "$code" -eq 5 ]; then
              echo "Empty shard (no tests landed in this split) — OK"
              exit 0
            fi
            exit $code
          else
            echo "No test files found, skipping"
          fi

  test-gate:
    # Aggregate gate that produces the bare "Test" required-status-check
    # context (root-cause #3). The branch ruleset requires "Test"; the matrix
    # above only reports "Test matrix (<os>)", so without this gate the
    # required "Test" check NEVER reports and the PR is stuck "pending"
    # forever / auto-merge never fires. This job succeeds iff every matrix lane
    # succeeded (and none was cancelled).
    name: Test
    runs-on: ubuntu-latest
    needs: [test]
    # if: always() so the gate runs even when a matrix lane failed — it then
    # FAILS (red "Test" check) instead of being skipped (which GitHub treats as
    # neither success nor failure and would also leave the required check
    # unsatisfied). The single status step is near-instant.
    if: ${{{{ always() }}}}
    # Trivial coordination job — 5 min is far more than enough (issue #90).
    timeout-minutes: 5
    steps:
      - name: Require all test-matrix lanes to have passed
        run: |
          if [ "${{{{ needs.test.result }}}}" = "success" ]; then
            echo "All test-matrix lanes passed."
            exit 0
          fi
          echo "::error::Test matrix did not pass (result: ${{{{ needs.test.result }}}})"
          exit 1
"""


def gen_release_yml(p: PluginParams) -> str:
    """Generate .github/workflows/release.yml — GitHub Release on semver tag.

    v2.86.0+ canon hardening parity with ci.yml:
    * Every action SHA-pinned (gh-actions.md §"Pin third-party actions to a
      full commit SHA") — first-party actions/* included, not just
      third-party, so a hostile tag rewrite cannot swap action code.
    * timeout-minutes on the release job (issue #90) — sized as a VALIDATION
      budget. The #114 cold-build framing (12-20 min for `uvx --from git+…`)
      is superseded: with the UV cache a field report measured that build at
      ~4s while the step still hit the cap (#180), so the time goes to
      validating, not fetching. A timeout here is NOT a git-fetch stall — do
      not chase the ref or the pin.
    * env-sanitized run blocks — every ${{{{ github.* }}}} consumed by a
      run: block is bound to an env: mapping first (gh-actions.md
      §"Avoid expression injection").

    SLSA / supply-chain provenance (issue #121):
    * SBOM generation (anchore/sbom-action) → an SPDX SBOM artifact, also
      attached to the release.
    * Build-provenance attestation (actions/attest-build-provenance) over
      the release assets — needs `id-token: write` + `attestations: write`.
    * Per-asset SHA256SUMS uploaded alongside the assets so consumers can
      verify integrity.
    """
    # Use p.python_version instead of hardcoded 3.12
    # Issue #137 (git-vs-pypi CPV source) + RC-8 (exit-code triage) both live in
    # the SHARED gen_cpv_validate_run_block, so this workflow and ci.yml cannot
    # drift apart on either. The report is written into the WORKSPACE (not
    # $RUNNER_TEMP like ci.yml) because it is uploaded as a release asset below
    # (issue #121) — the historical filename is kept so the asset name is stable.
    #
    # Historical note: this block used to be built with SINGLE-`\` Python source
    # continuations, which eat the newline and emit one FLATTENED shell line. The
    # shared helper emits real multi-line shell continuations instead; the
    # command is identical, only its line-wrapping changed.
    validate_block = gen_cpv_validate_run_block(p, "validation-report.txt")
    return f"""name: Release

on:
  push:
    tags:
      - 'v*.*.*'

# Least-privilege baseline. The release job widens to the writes it needs
# (contents: write to create the release + upload assets; id-token +
# attestations: write for build-provenance attestation — issue #121).
permissions:
  contents: read

jobs:
  release:
    runs-on: ubuntu-latest
    # VALIDATION budget (issues #90 + #114 + #180) — not a build budget.
    # A timeout here is NOT a git-fetch or cold-build stall: with the cache
    # below, the CPV build was measured finishing ~4s into the step while the
    # step still ran to this cap (#180). Do not chase the ref or the pin. The
    # step is tee'd, so a killed run still shows what was in flight.
    # 30 min with headroom; do NOT lower it below 25.
    timeout-minutes: 30
    permissions:
      contents: write       # create the release + upload assets
      id-token: write       # OIDC token for build-provenance attestation (#121)
      attestations: write   # write the provenance attestation (#121)
    steps:
      - uses: actions/checkout@df4cb1c069e1874edd31b4311f1884172cec0e10 # v6.0.3
        with:
          fetch-depth: 0
          persist-credentials: false

      - name: Install uv
        # enable-cache: true keys an actions/cache on UV_CACHE_DIR so the
        # cold `uvx --from git+…` build of CPV (12-20 min, issue #114) is
        # paid ONCE per lockfile/runner; later runs restore the warm cache.
        uses: astral-sh/setup-uv@fac544c07dec837d0ccb6301d7b5580bf5edae39 # v8.2.0
        with:
          enable-cache: true

      - name: Set up Python
        run: uv python install {p.python_version}

      - name: Install dependencies
        run: uv sync --extra dev

      - name: Run full plugin validation (remote CPV, --strict)
        # Fetches CPV from GitHub so downstream plugins do not need to vendor
        # scripts/validate_plugin.py. Matches publish.py's local gate and the
        # CI validate job. --strict blocks on CRITICAL/MAJOR/MINOR/NIT (CPV's
        # verdict exit codes 1-4; WARNING never gets an exit code of its own).
        # RC-8: any OTHER exit code means the validator FAILED TO RUN (a cold
        # uvx-from-git build can die on a transient GitHub git-fetch) — the
        # handler below says so instead of mislabelling it as findings, and
        # fails the release rather than publishing an unvalidated tag.
        # Issue #11: removed local scripts/validate_plugin.py invocation.
        # Root-cause #2: CPV is PINNED to @{p.cpv_ref_resolved} (same ref the
        # CI validate job and publish.py use) so the cold uvx-from-git build is
        # cached per tag and a stricter future CPV release cannot break the
        # release gate with no plugin change. Root-cause #5: this re-runs the
        # gate publish.py already passed locally — pinning all three callsites
        # to the SAME ref keeps that consistent; the gate itself stays intact
        # (no security check is weakened).
        env:
          # Root-cause #4: skip the GitHub-anchored integrity fetch on a
          # fresh-checkout runner (the local manifest already matches the code).
          # Issue #140: this step deliberately does NOT pass the repo owner into
          # CPV's private-usernames allowlist — that allowlist is the set of
          # names to treat as PRIVATE, so seeding it with the PUBLIC owner makes
          # CPV flag every github.com/<owner>/ URL + the owner no-reply email as
          # a CRITICAL private-path leak, red-lighting --strict CI. In CI there
          # is no developer local-username to protect; the public owner is public.
          PLUGIN_SKIP_GITHUB_INTEGRITY: "1"
          # Issue #162: cap the AGGREGATE REPO LINT wall-clock. Each linter is
          # already per-linter-bounded, but on a cold runner uv/npm serialize the
          # concurrent uvx/npx first-run fetches on a global cache lock, so the
          # ~17-linter fan-out degrades toward serial and the phase can march past
          # this job's own timeout-minutes with orphaned uv/python children. 600s
          # is well under this job's ceiling yet ~17x the warm ~35s run, so it
          # never false-skips a healthy cold run. Raise it to tune, or set
          # PLUGIN_SKIP_REPO_LINT=1 if Mega-Linter already lints this repo.
          PLUGIN_REPO_LINT_PHASE_TIMEOUT: "600"
        run: |
          {validate_block}

      - name: Run tests
        run: |
          if [ -d "tests" ]; then
            uv run pytest tests/ -v
          fi

      - name: Lint Python scripts
        run: uv run ruff check scripts/

      - name: Type check
        run: uv run mypy scripts/ --ignore-missing-imports

      - name: Generate SBOM (SPDX)
        # SLSA supply-chain control (issue #121): produce an SPDX SBOM of the
        # repository contents so the published artifact carries a software
        # bill of materials. Attached to the release below.
        uses: anchore/sbom-action@e22c389904149dbc22b58101806040fa8d37a610 # v0.24.0
        with:
          path: .
          format: spdx-json
          output-file: sbom.spdx.json
          # The action can attach the SBOM to the release itself; we also
          # upload it as an asset explicitly below for determinism.
          upload-release-assets: false
          upload-artifact: false

      - name: Generate changelog
        id: changelog
        # v2.86.0 hardening (issue #22): extract ONLY the matching
        # ``## [X.Y.Z] — YYYY-MM-DD`` block from CHANGELOG.md instead of
        # uploading the entire file. The release body should be the
        # release-specific notes, not the whole project history.
        # Fallback chain:
        #   1. CHANGELOG.md ## [version] section (curated, em-dash separator)
        #   2. CHANGELOG.md ## [version] section (legacy hyphen separator)
        #   3. Full CHANGELOG.md (when no section header matches)
        #   4. Auto-generated git log (when no CHANGELOG.md at all)
        env:
          TAG: ${{{{ github.ref_name }}}}
        run: |
          set -e
          # Strip leading 'v' so v1.2.3 matches ## [1.2.3] — ...
          VERSION="${{TAG#v}}"
          PREV_TAG=$(git describe --tags --abbrev=0 HEAD^ 2>/dev/null || echo "")
          if [ -z "$PREV_TAG" ]; then
            GITLOG=$(git log --pretty=format:"- %s (%h)" HEAD)
          else
            GITLOG=$(git log --pretty=format:"- %s (%h)" "$PREV_TAG..HEAD")
          fi
          if [ -f "CHANGELOG.md" ]; then
            # Extract the section header through the next ## or EOF. Try
            # em-dash (canonical) first, then legacy hyphen. awk prints
            # the block bounded by the matching header and the NEXT
            # `## ` at column 0 (or EOF).
            SECTION=$(awk -v ver="$VERSION" '
              $0 ~ "^## \\\\[" ver "\\\\] [—-] " {{found=1; print; next}}
              found && /^## / {{exit}}
              found {{print}}
            ' CHANGELOG.md)
            if [ -n "$SECTION" ]; then
              printf '%s\\n' "$SECTION" > changelog.txt
              echo "::notice::Release body extracted from CHANGELOG.md section for $VERSION"
            else
              echo "::warning::No CHANGELOG.md section matched ## [$VERSION] — falling back to full CHANGELOG.md"
              cp CHANGELOG.md changelog.txt
            fi
          else
            echo "$GITLOG" > changelog.txt
          fi
          echo "changelog_file=changelog.txt" >> "$GITHUB_OUTPUT"

      - name: Compute per-asset SHA256SUMS
        # SLSA supply-chain control (issue #121): checksum every release
        # asset so consumers can verify integrity. SHA256SUMS is itself
        # uploaded as an asset alongside the files it covers.
        run: |
          set -e
          : > SHA256SUMS
          for f in validation-report.txt sbom.spdx.json; do
            if [ -f "$f" ]; then
              sha256sum "$f" >> SHA256SUMS
            fi
          done
          echo "SHA256SUMS:"
          cat SHA256SUMS

      - name: Create or update GitHub Release (idempotent)
        # Idempotent shell flow — replaces the `softprops/action-gh-release`
        # action which 422s when publish.py's Gate-13 already created the
        # release locally. Pattern: `gh release view` to detect existence,
        # then `gh release edit` (with --notes-file fallback to existing body)
        # OR `gh release create` (when not pre-existing). Both branches
        # upload the validation-report, the SBOM, and SHA256SUMS (issue #121).
        env:
          GH_TOKEN: ${{{{ secrets.GITHUB_TOKEN }}}}
          TAG: ${{{{ github.ref_name }}}}
        run: |
          set -e
          if gh release view "$TAG" >/dev/null 2>&1; then
            echo "Release $TAG already exists — editing in place"
            gh release edit "$TAG" \
              --notes-file changelog.txt \
              --verify-tag
            gh release upload "$TAG" validation-report.txt sbom.spdx.json SHA256SUMS --clobber
          else
            echo "Release $TAG does not exist yet — creating"
            gh release create "$TAG" validation-report.txt sbom.spdx.json SHA256SUMS \
              --title "$TAG" \
              --notes-file changelog.txt \
              --verify-tag
          fi

      - name: Attest build provenance
        # SLSA build-provenance attestation (issue #121) over the release
        # assets. Needs id-token: write + attestations: write (declared on
        # the job). The attestation is recorded in the repo's attestations
        # store and verifiable with `gh attestation verify`.
        uses: actions/attest-build-provenance@a2bbfa25375fe432b6a289bc6b6cd05ecd0c4c32 # v4.1.0
        with:
          subject-path: |
            validation-report.txt
            sbom.spdx.json
"""


def gen_mega_linter_yml(p: PluginParams) -> str:
    """Generate .mega-linter.yml — Mega-Linter configuration."""
    _ = p  # unused but kept for consistent signature
    return """# Mega-Linter configuration
# https://megalinter.io/latest/configuration/

# Only lint changed files (faster, less noise)
APPLY_FIXES: none
VALIDATE_ALL_CODEBASE: false

# Enable these linter groups
ENABLE_LINTERS:
  - PYTHON_RUFF
  - PYTHON_MYPY
  - PYTHON_BANDIT
  - BASH_SHELLCHECK
  - BASH_SHFMT
  - JSON_JSONLINT
  - YAML_YAMLLINT
  - MARKDOWN_MARKDOWNLINT
  - SPELL_CSPELL
  - COPYPASTE_JSCPD
  - REPOSITORY_CHECKOV
  - REPOSITORY_TRIVY

# Issue #138: REPOSITORY_GITLEAKS is intentionally NOT enabled. MegaLinter runs
# gitleaks in repository mode (full git HISTORY), so a security-teaching plugin
# with example secrets in docs — even in deleted/renamed/old commits — fails the
# Lint job on FPs that are unfixable in the working tree. Secrets are already
# covered by publish.py's TruffleHog gate (with a public-info allowlist).

# Exclude paths — single-quoted YAML scalar so regex \\. is read literally
# (double-quoted YAML treats \\. as an invalid escape and yamllint rejects it).
# Test FIXTURES are excluded for EVERY language Mega-Linter runs: fixtures are
# deliberately-malformed sample data used to exercise the plugin's own
# validators/tests, so linting them would "fix" the very defects the tests rely
# on. Covers tests/fixtures, test/fixtures, spec/fixtures, __fixtures__ (JS),
# testdata (Go), and a bare fixtures/ dir.
FILTER_REGEX_EXCLUDE: '(tests_dev/|docs_dev/|scripts_dev/|samples_dev/|examples_dev/|builds_dev/|downloads_dev/|libs_dev/|llm_externalizer_output/|\\.claude/|\\.tldr/|tests?/fixtures/|spec/fixtures/|__fixtures__/|testdata/|fixtures/)'

# Python settings
PYTHON_RUFF_ARGUMENTS: "--select=E,F,W,I --ignore=E501"
PYTHON_MYPY_ARGUMENTS: "--ignore-missing-imports"

# Copy-paste detection — allow up to 5% duplication (0% is too strict for plugins)
COPYPASTE_JSCPD_ARGUMENTS: "--threshold 5"

# Checkov — skip workflow-level permission checks (we set permissions per-job)
REPOSITORY_CHECKOV_ARGUMENTS: "--skip-check CKV2_GHA_1"

# Markdown settings — allow long lines in README (badges).
# Single-quoted so the regex \\. is read literally by yamllint.
MARKDOWN_MARKDOWNLINT_FILTER_REGEX_EXCLUDE: 'CHANGELOG\\.md'

# Spell check — single-quoted so the regex \\. is read literally by yamllint.
SPELL_CSPELL_FILTER_REGEX_EXCLUDE: '(uv\\.lock|\\.json)'

# Disable reporters that create PR comments (we handle that ourselves).
# Issue #29 (v2.97.0): the deprecated DISABLE_REPORTERS list form fails
# Mega-Linter v8+ JSON Schema validation — the v8+ schema uses one
# boolean key per reporter. Keep the boolean form below.
GITHUB_COMMENT_REPORTER: false
"""


def gen_jscpd_json(p: PluginParams) -> str:
    """Generate ``.jscpd.json`` — the copy-paste-detector config (issue #143).

    SINGLE SOURCE OF TRUTH for the jscpd copy-paste check, auto-discovered by
    BOTH CI's Mega-Linter ``COPYPASTE_JSCPD`` step AND the local
    ``publish.py --gate`` G2b step (jscpd reads ``.jscpd.json`` from the repo
    root automatically). Before this file existed, the local gate ran ruff but
    NOT jscpd, so a publish could pass every local gate, tag+push+release, and
    THEN fail CI's Lint job on copy-paste duplication — a green gate did not
    predict green CI for the copy-paste dimension. With one shared config the
    two gates enforce the EXACT same threshold and ignore list.

    ``threshold`` 5 matches ``.mega-linter.yml``'s
    ``COPYPASTE_JSCPD_ARGUMENTS: "--threshold 5"``; the ``ignore`` globs mirror
    that file's ``FILTER_REGEX_EXCLUDE`` (same dev-only dirs + test fixtures +
    vendored ``node_modules``/``.git``) so a dev-dir or fixture duplicate is
    never counted by either gate.
    """
    _ = p  # unused but kept for consistent signature
    return """{
  \"threshold\": 5,
  \"minTokens\": 50,
  \"gitignore\": true,
  \"reporters\": [\"console\"],
  \"ignore\": [
    \"**/tests_dev/**\", \"**/docs_dev/**\", \"**/scripts_dev/**\", \"**/samples_dev/**\",
    \"**/examples_dev/**\", \"**/builds_dev/**\", \"**/downloads_dev/**\", \"**/libs_dev/**\",
    \"**/llm_externalizer_output/**\", \"**/.claude/**\", \"**/.tldr/**\",
    \"**/tests/fixtures/**\", \"**/test/fixtures/**\", \"**/spec/fixtures/**\",
    \"**/__fixtures__/**\", \"**/testdata/**\", \"**/fixtures/**\",
    \"**/node_modules/**\", \"**/.git/**\"
  ]
}
"""


def gen_commitlintrc_json(p: PluginParams) -> str:
    """Generate ``.commitlintrc.json`` — the conventional-commit config (RC-1).

    WHY THIS FILE EXISTS (CI-failure forensics, 2026-07-13): with no commitlint
    config in the repo, ``wagoid/commitlint-github-action`` falls back to bare
    ``@commitlint/config-conventional``, whose ``body-max-line-length`` is 100.
    Dependabot's auto-generated commit body embeds a multi-line YAML dependency
    block (``- dependency-name: …`` / ``update-type: …``) that ALWAYS exceeds
    100 chars, so the gate failed on EVERY Dependabot PR of EVERY
    cpv-canonical-pipeline plugin, forever, with no plugin change — the single
    largest ongoing red-CI signal in the fleet (4 of the 18 sampled failures,
    e.g. ai-maestro-maintainer-agent run 29217061586).

    THE FIX, and why it is this one rather than an actor exemption: the obvious
    alternative is to skip the whole commitlint job for ``github.actor ==
    'dependabot[bot]'``. That was rejected because (a) it exempts an ACTOR from
    the gate entirely rather than disabling the one meaningless RULE — the bot's
    commits stop being linted at all; (b) it does not survive the common case
    where a HUMAN pushes a fixup onto the Dependabot branch (the actor is then
    the human, the action re-lints the bot's commit, and the gate fails again);
    and (c) it needs a per-bot allowlist (renovate, pre-commit-ci, …) that must
    be maintained. Disabling ``body-max-line-length`` fixes the class once, for
    every actor and every event.

    THE GATE IS NOT WEAKENED. Only the body's *line length* — a purely cosmetic
    rule with zero signal on a machine-generated body (and routinely hostile to
    a pasted URL or stack trace in a human body) — is switched off. Every rule
    that carries meaning still fires: ``type-enum`` (the RC-5 failure, a
    correctly-rejected non-conventional type), ``subject-empty``,
    ``header-max-length``, ``type-case``, … A badly-typed human commit still
    fails CI exactly as before.
    """
    _ = p  # unused but kept for consistent signature
    return """{
  "extends": ["@commitlint/config-conventional"],
  "rules": {
    "body-max-line-length": [0]
  }
}
"""


def gen_cspell_json(p: PluginParams, files: list[tuple[str, str, bool]]) -> str:
    """Generate ``.cspell.json`` — the project spell-check dictionary (RC-3).

    WHY THIS FILE EXISTS. ``gen_mega_linter_yml`` enables ``SPELL_CSPELL``, so
    CI's Mega-Linter spell-checks the repo. With no project dictionary, cspell
    falls back to its bundled word list — which knows nothing of the plugin's own
    proper nouns (its name, its author, its agent/skill/command names) nor of
    ordinary tech vocabulary (``pyproject``, ``venv``, ``pipefail``, ``mypy``) —
    and the Lint job goes RED on a plugin that has nothing wrong with it.

    WHY IT IS EMITTED HERE AND NOT ONLY BY ``standardize --fix``. Wave 1 taught
    ``standardize`` to provision this file and taught
    ``cpv_ci_preflight._gate_cspell`` to FAIL when SPELL_CSPELL is enabled with
    no dictionary. But the GENERATOR emitted no cspell config — so a FRESHLY
    SCAFFOLDED plugin failed a gate it had done nothing to deserve, and only a
    separate ``standardize --fix`` run could clear it. A scaffold that cannot
    pass its own canonical pipeline is a broken scaffold. Emitting the dictionary
    here closes that loop: a generated plugin is CI-parity-clean out of the box.

    THE WORD LIST IS NOT DUPLICATED HERE. ``standardize_plugin`` owns the
    canonical dictionary (``_CSPELL_BASE_WORDS``) and the canonical ignore list
    (``_CSPELL_IGNORE_PATHS``); this function IMPORTS both. Two divergent copies
    of a word list is precisely the drift bug the RC-3 fix exists to kill — the
    generated file and the standardize-provisioned file must be the SAME file, or
    the local probe and CI disagree again. ``tests/test_wave2_generator_publish_gate.py``
    pins the two renderers byte-identical on a real scaffold, so a shape change in
    either one fails the build rather than silently drifting.

    NOT REGISTERED IN ``standardize_plugin._FILE_TO_GENERATOR``, on purpose. That
    map force-templates a file by calling ``gen_func(params)``, and this generator
    takes a SECOND argument, so registering it there would raise TypeError. It
    must stay out of the map anyway: force-templating a dictionary would CLOBBER
    words the author curated. An existing plugin's dictionary is provisioned (and
    AUGMENTED, never overwritten) by ``standardize_plugin.provision_cspell_config``
    — that is the legacy-plugin path; this function is the scaffold path.
    ``tests/test_wave2_generator_publish_gate.py`` pins the exclusion.

    Args:
        p: the plugin params (supplies the plugin's name + author — proper nouns
            that appear in the README byline and every doc heading).
        files: the scaffold's file list SO FAR. The plugin's own component names
            are read back OUT of it (``agents/*.md`` / ``commands/*.md`` stems and
            ``skills/<dir>/`` names), mirroring what
            ``standardize_plugin._cspell_plugin_terms`` reads off disk. Deriving
            them from the emitted list — rather than hardcoding "cpv-the-skills-menu"
            — means a component added to the scaffold later is dictionary-seeded
            automatically, with no second place to remember to update.
    """
    # LAZY import, matching the convention standardize_plugin already uses for
    # its own generate_plugin_repo callsites: the two modules reference each
    # other, and a module-level import here would risk a cycle.
    from standardize_plugin import _CSPELL_BASE_WORDS, _CSPELL_IGNORE_PATHS, _cspell_tokens

    # The plugin's OWN proper nouns — the words a generic dictionary can never
    # know. Same SOURCES as standardize_plugin._cspell_plugin_terms, which reads
    # them off a plugin that already exists on disk; at scaffold time the tree is
    # not written yet, so we read the equivalent facts from the params + the
    # file list being built.
    raw: list[str] = [p.name]
    if p.author:
        raw.append(p.author)
    for rel, _content, _is_exec in files:
        parts = rel.split("/")
        if len(parts) >= 2 and parts[0] in ("agents", "commands") and rel.endswith(".md"):
            raw.append(parts[-1][: -len(".md")])
        elif len(parts) >= 2 and parts[0] == "skills":
            raw.append(parts[1])

    terms: set[str] = set()
    for item in raw:
        terms.update(_cspell_tokens(item))

    config: dict[str, object] = {
        "version": "0.2",
        "language": "en",
        # CI only ever spell-checks tracked files; a local `cspell lint .` would
        # otherwise walk gitignored trees (reports/, .venv/) and fail on content
        # CI never sees — the opposite of parity.
        "useGitignore": True,
        "ignorePaths": list(_CSPELL_IGNORE_PATHS),
        "words": sorted(set(_CSPELL_BASE_WORDS) | terms),
    }
    return json.dumps(config, indent=2) + "\n"


def gen_markdownlint_json(p: PluginParams) -> str:
    """Generate .markdownlint.json — disables MD013 (line-length).

    Plugin .md files (skill descriptions, agent prompts, slash commands,
    hook recipes) commonly contain extremely long lines that cannot be
    safely wrapped:

      - Inline `!command` shell snippets that would break across newlines
      - Long URLs in references / badges
      - Pipe-delimited tables describing config keys
      - Frontmatter with comma-joined tool lists

    Wrapping any of these would change the rendered markdown / break the
    Claude Code parser. CPV-published plugins therefore disable MD013
    everywhere; the rest of the markdownlint defaults stay in effect.

    MD024 (no-duplicate-heading) is turned OFF entirely (issues #144 /
    #145a): a per-release CHANGELOG legitimately repeats the same sub-headings
    (``### Features``, ``### Bug Fixes``, …) across every version section, and
    BOTH MD024 modes flag that valid content — the old ``siblings_only: true``
    flagged repeated headings that ARE siblings within one section (the exact
    case issue #144 reported: clean → 4 errors), and the strict default flags
    every recurring heading even under distinct ``## [version]`` parents
    (verified with real markdownlint). Since a changelog necessarily recurs
    its section headings, the only non-hostile setting is OFF — which the
    reporter explicitly recommended. A genuine DUPLICATE TOP-LEVEL TITLE is
    still caught by MD025 below (single-title), so disabling MD024 does not
    let two ``# H1`` slip through.

    MD025 (single-title / single-H1) IS configured with an empty
    ``front_matter_title`` (issues #144 / #145a): a doc that carries a YAML
    frontmatter ``title:`` AND a body ``# H1`` (the common TRDD / design-doc
    shape) otherwise trips MD025 — markdownlint counts the frontmatter title
    as the document's H1 and then flags the body ``# H1`` as a second title.
    Setting ``front_matter_title`` to ``""`` tells markdownlint NOT to treat
    any frontmatter field as the title, so the body ``# H1`` is the sole H1
    and the false positive disappears. Harmless for plugins whose docs have
    no frontmatter title.
    """
    _ = p  # unused but kept for consistent signature
    return """{
  \"default\": true,
  \"MD007\": false,
  \"MD013\": false,
  \"MD022\": false,
  \"MD024\": false,
  \"MD025\": { \"front_matter_title\": \"\" },
  \"MD026\": false,
  \"MD029\": false,
  \"MD031\": false,
  \"MD032\": false,
  \"MD033\": false,
  \"MD034\": false,
  \"MD036\": false,
  \"MD037\": false,
  \"MD038\": false,
  \"MD040\": false,
  \"MD041\": false,
  \"MD046\": false,
  \"MD048\": false,
  \"MD049\": false,
  \"MD050\": false,
  \"MD051\": false,
  \"MD052\": false,
  \"MD053\": false,
  \"MD055\": false,
  \"MD057\": false,
  \"MD058\": false,
  \"MD059\": false,
  \"MD060\": false
}
"""


def gen_release_binaries_yml(p: PluginParams) -> str:
    """Generate ``.github/workflows/release-binaries.yml`` — the binary-release profile scaffold (#115).

    A best-effort scaffold for a plugin that ships COMPILED binaries (Rust by
    default), modelled on the proven ``memgrep-release.yml`` reference shape. The
    emitted workflow satisfies all FOUR invariants
    :func:`cpv_pipeline_profile.is_binary_release_canonical_shape` recognizes as
    CANONICAL, so a binary-release plugin scaffolded with this template clears
    the false "missing standard release.yml" drift flag:

      1. every third-party ``uses:`` is SHA-pinned (here every action is
         ``actions/``-org AND SHA-pinned, reusing the pins CPV trusts in its own
         workflows);
      2. a least-privilege split — the build + smoke jobs are ``contents: read``
         and EXACTLY ONE job (``release``) is ``contents: write``;
      3. a ``SHA256SUMS`` checksum step;
      4. a build ``matrix`` over targets (aarch64/x86_64 × apple-darwin/
         unknown-linux-gnu).

    It also carries a ``build-smoke`` job that compiles on push/PR — the
    "untested-until-release" guard (a tag-only build that no push job exercises
    is exactly the v2.136.0 ``RC-UNTESTED-UNTIL-RELEASE`` failure mode). The
    compile command defaults to ``cargo build --release --locked``; a non-Rust
    plugin replaces the build/stage steps but keeps the four invariants.

    Plain (non-f) template: the pervasive ``${{ … }}`` GitHub expressions and
    shell ``{print $1}`` braces need NO escaping — the only substitution is the
    binary name (``@@BIN@@`` → ``p.name``), done by an explicit ``.replace``.
    """
    template = r"""# .github/workflows/release-binaries.yml — binary-release profile (#115)
# Builds compiled binaries for several targets, checksums them, and attaches
# them to the GitHub Release. Generated by CPV's canonical pipeline as the
# binary-release scaffold (modelled on the proven memgrep-release.yml shape).
#
# KEEP these four structural invariants when you edit this file, or CPV's
# binary-release canon will WARN (it recognizes a CANONICAL binary-release
# workflow and clears the "missing standard release.yml" drift flag):
#   1. every third-party `uses:` SHA-pinned (the `actions/`-org pins below are
#      already full-SHA; pin any third-party action you add to a 40-hex commit);
#   2. least-privilege split — the build + smoke jobs are `contents: read`, and
#      EXACTLY ONE job (`release`) is `contents: write`;
#   3. a `SHA256SUMS` checksum step;
#   4. a build `matrix` over targets.
#
# STRICT SHIP-ONLY-BINARY canon (issue #175): the compiled SOURCE is NOT a git
# submodule (there is no .gitmodules to recurse). If this component's source
# lives in a SEPARATE repository, clone it by PINNED URL/tag in the build jobs
# (CPV's strip-dev model records it in .claude-plugin/plugin.json ->
# cpv.strip.extract[] as {path, url, sha}). For an in-tree crate, the plain
# checkout below is all you need.
#
# The binary name is `@@BIN@@` (the plugin name by default). For a Rust crate
# whose `[[bin]]` name differs, change `@@BIN@@` in the staging step only.
name: Release binaries

on:
  push:
    tags: ["v*"]
  workflow_dispatch:
    inputs:
      tag:
        description: "Existing release tag to (re)attach binaries to"
        required: true

# Least privilege: nothing by default; each job opts in to only what it needs.
permissions: {}

concurrency:
  group: release-binaries-${{ github.ref }}
  cancel-in-progress: false

jobs:
  # CI smoke (push/PR): compile on the host target so a build break is caught
  # BEFORE a tag is cut — the "untested-until-release" guard. Mirrors the real
  # release build command so a tag build can never surprise you.
  build-smoke:
    name: Build smoke (untested-until-release guard)
    if: github.event_name == 'push' || github.event_name == 'pull_request'
    runs-on: ubuntu-latest
    timeout-minutes: 20
    permissions:
      contents: read
    steps:
      - name: Checkout
        uses: actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd # v6.0.2
      # STRICT-CANON build source (issue #175): NOT a submodule (no .gitmodules).
      # If the compiled source lives in a SEPARATE repo, clone it by pinned URL/tag
      # here before the cargo steps (adapt --manifest-path accordingly):
      #   - name: Clone build source
      #     run: git clone --depth 1 --branch "<pinned-tag>" -- "<https-source-repo-url>" src
      # For an in-tree crate no clone step is needed.
      - name: Add clippy component
        run: rustup component add clippy
      - name: Clippy (deny warnings) - issue #175
        run: cargo clippy --release --locked --all-targets -- -D warnings
      - name: Test - issue #175
        run: cargo test --locked
      - name: Build (host target)
        run: cargo build --release --locked

  build:
    name: Build ${{ matrix.target }}
    if: startsWith(github.ref, 'refs/tags/') || github.event_name == 'workflow_dispatch'
    runs-on: ${{ matrix.os }}
    timeout-minutes: 30
    permissions:
      contents: read
    strategy:
      fail-fast: false
      matrix:
        include:
          - target: aarch64-apple-darwin
            os: macos-latest
          - target: x86_64-apple-darwin
            os: macos-latest
          - target: aarch64-unknown-linux-gnu
            os: ubuntu-latest
          - target: x86_64-unknown-linux-gnu
            os: ubuntu-latest
    steps:
      - name: Checkout
        uses: actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd # v6.0.2
      # STRICT-CANON build source (issue #175): NOT a submodule. Clone a separate
      # source repo by pinned URL/tag here if this component builds from one.
      - name: Add Rust target
        run: rustup target add "${{ matrix.target }}"
      - name: Build
        run: cargo build --release --locked --target "${{ matrix.target }}"
      - name: Stage binary + per-asset checksum
        run: |
          mkdir -p out
          bin="target/${{ matrix.target }}/release/@@BIN@@"
          asset="out/@@BIN@@-${{ matrix.target }}"
          cp "$bin" "$asset"
          shasum -a 256 "$asset" | awk '{print $1}' > "$asset.sha256"
      - name: Upload build artifact
        uses: actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a # v7.0.1
        with:
          name: @@BIN@@-${{ matrix.target }}
          path: out/*
          if-no-files-found: error

  release:
    name: Attach binaries + SHA256SUMS to the release
    needs: [build]
    runs-on: ubuntu-latest
    timeout-minutes: 15
    # The ONLY job with write access (least-privilege split): it uploads the
    # built assets to the release. The build matrix above stays read-only.
    permissions:
      contents: write
    steps:
      - name: Download all build artifacts
        uses: actions/download-artifact@3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c # v8.0.1
        with:
          path: dist
          merge-multiple: true
      - name: Combine per-asset checksums into SHA256SUMS
        run: |
          cd dist
          : > SHA256SUMS
          for f in *; do
            case "$f" in *.sha256 | SHA256SUMS) continue ;; esac
            shasum -a 256 "$f" >> SHA256SUMS
          done
          cat SHA256SUMS
      - name: Resolve the release tag
        id: tag
        run: |
          if [ "${{ github.event_name }}" = "workflow_dispatch" ]; then
            echo "tag=${{ github.event.inputs.tag }}" >> "$GITHUB_OUTPUT"
          else
            echo "tag=${{ github.ref_name }}" >> "$GITHUB_OUTPUT"
          fi
      - name: Upload binaries + SHA256SUMS to the release
        env:
          GH_TOKEN: ${{ github.token }}
        run: gh release upload "${{ steps.tag.outputs.tag }}" dist/* --clobber --repo "${{ github.repository }}"
"""
    return template.replace("@@BIN@@", p.name)


def gen_notify_marketplace_yml(p: PluginParams) -> str:
    """Generate .github/workflows/notify-marketplace.yml — marketplace notification.

    Per-plugin VALUES that vary: marketplace owner and marketplace repo.
    When ``p.marketplace_owner`` is set (e.g. detected from an existing
    notify-marketplace.yml during a ``--force-templates`` migration) it
    overrides ``p.github_owner`` so a plugin whose marketplace lives under
    a different owner doesn't have its OWNER overwritten.

    Per-plugin NAMES are NOT supported (v2.86.0+). The dispatch secret is
    ALWAYS named ``MARKETPLACE_PAT``. Plugins with deviant secret names
    get a loud ``[ACTION REQUIRED]`` warning at migration time directing
    the maintainer to ``gh secret set MARKETPLACE_PAT --body
    "$MARKETPLACE_PAT"`` rather than CPV preserving their off-canon name.
    Single canon-name policy keeps `cpv_setup_auth`, GH webhook receivers,
    and docs strictly aligned across every plugin.

    Env-var sanitization (gh-actions.md §"Avoid expression injection"):
    every ``${{{{ github.* }}}}`` consumed by a ``run:`` block is first
    bound to an ``env:`` mapping; the shell sees ``$VAR`` rather than the
    raw expression. Prevents shell-metacharacter exfil if the upstream
    repository metadata is ever crafted hostile.
    """
    marketplace_owner = p.marketplace_owner if p.marketplace_owner else p.github_owner
    marketplace_repo = p.marketplace if p.marketplace else "my-plugins-marketplace"
    return f"""# Notify marketplace repo when this plugin is updated
# Requires MARKETPLACE_PAT secret (Personal Access Token with repo scope).
# Create via: gh secret set MARKETPLACE_PAT --repo {marketplace_owner}/{p.repo_name} \\
#               --body "$MARKETPLACE_PAT"
# (assumes $MARKETPLACE_PAT is exported in your shell or .env file)

name: Notify Marketplace

on:
  push:
    branches: [main]
    paths:
      - '.claude-plugin/plugin.json'
      - 'hooks/**'
      - 'commands/**'
      - 'agents/**'
      - 'skills/**'
      - 'scripts/**'

env:
  MARKETPLACE_OWNER: '{marketplace_owner}'
  MARKETPLACE_REPO: '{marketplace_repo}'

# Least-privilege: the cross-repo dispatch uses the MARKETPLACE_PAT secret,
# not the workflow GITHUB_TOKEN, so this workflow needs no write scopes
# (gh-actions.md §"least privilege").
permissions:
  contents: read

jobs:
  notify:
    runs-on: ubuntu-latest
    # Single API dispatch — 5 min is generous (issue #90). If we exceed it,
    # the marketplace repo's API is very wrong.
    timeout-minutes: 5
    # Root-cause #9: NO-OP when MARKETPLACE_PAT is not configured. Expose the
    # secret's presence as a boolean (`secrets` is available in a job-level
    # env: mapping); the dispatch/summary steps are then gated on it via
    # `if: env.HAS_MARKETPLACE_PAT == 'true'`. Without the secret the job runs,
    # prints a notice, and SUCCEEDS (green) — instead of erroring with a gh
    # 404/auth failure and showing a red associated workflow on the release.
    env:
      HAS_MARKETPLACE_PAT: ${{{{ secrets.MARKETPLACE_PAT != '' }}}}
    steps:
      - name: Check marketplace secret
        # Always runs. When the PAT is missing, emit a friendly notice and let
        # the gated steps below skip — the job still ends green. When present,
        # this is a no-op log line.
        run: |
          if [ "$HAS_MARKETPLACE_PAT" = "true" ]; then
            echo "MARKETPLACE_PAT is configured — proceeding with marketplace notification."
          else
            echo "::notice::MARKETPLACE_PAT secret is not set — skipping marketplace notification (no-op)."
          fi

      # Sanitization: every `${{{{ github.* }}}}` value crossing into a shell `run:`
      # block is first bound to an `env:` mapping; the run-script sees `$VAR`
      # rather than raw expression interpolation. Prevents shell-injection if
      # upstream metadata is ever crafted hostile (gh-actions.md L66-77).
      - name: Get plugin info
        id: plugin
        if: env.HAS_MARKETPLACE_PAT == 'true'
        env:
          REPO_NAME: ${{{{ github.event.repository.name }}}}
          REF_SHA: ${{{{ github.sha }}}}
        run: |
          printf 'name=%s\\n' "$REPO_NAME" >> "$GITHUB_OUTPUT"
          printf 'ref=%s\\n'  "$REF_SHA"   >> "$GITHUB_OUTPUT"

      - name: Trigger marketplace update
        if: env.HAS_MARKETPLACE_PAT == 'true'
        # Defensive "second-latest" pin: v4.0.0, NOT the bleeding-edge v4.0.1.
        # v4.0.1's SHA was the user-visible symptom of the 2026-05-26 GitHub
        # Actions codeload auth outage; CPV stays one tag behind on the
        # dispatch action as a hedge. See
        # tests/test_canon_repository_dispatch_sha_pin.py for the full record.
        uses: peter-evans/repository-dispatch@5fc4efd1a4797ddb68ffd0714a238564e4cc0e6f # v4.0.0
        with:
          token: ${{{{ secrets.MARKETPLACE_PAT }}}}
          repository: ${{{{ env.MARKETPLACE_OWNER }}}}/${{{{ env.MARKETPLACE_REPO }}}}
          event-type: plugin-updated
          client-payload: |
            {{
              "plugin": "${{{{ steps.plugin.outputs.name }}}}",
              "ref": "${{{{ steps.plugin.outputs.ref }}}}",
              "source_repo": "${{{{ github.repository }}}}",
              "triggered_by": "${{{{ github.actor }}}}"
            }}

      - name: Summary
        if: env.HAS_MARKETPLACE_PAT == 'true'
        env:
          PLUGIN_NAME: ${{{{ steps.plugin.outputs.name }}}}
          PLUGIN_REF: ${{{{ steps.plugin.outputs.ref }}}}
        run: |
          {{
            printf '## Marketplace Notification\\n\\n'
            printf 'Triggered update in %s/%s\\n\\n' "$MARKETPLACE_OWNER" "$MARKETPLACE_REPO"
            printf -- '- Plugin: %s\\n' "$PLUGIN_NAME"
            printf -- '- Commit: %s\\n' "$PLUGIN_REF"
          }} >> "$GITHUB_STEP_SUMMARY"
"""


def gen_tests_init() -> str:
    """Generate tests/__init__.py placeholder."""
    return '"""Test suite for the plugin."""\n'


def gen_scripts_init(p: PluginParams) -> str:
    """Generate scripts/__init__.py with version."""
    return f'"""Plugin scripts for {p.name}."""\n\n__version__ = "{p.version}"\n'


# =============================================================================
# FILE ASSEMBLY
# =============================================================================


def generate_all_files(
    p: PluginParams, profile: str = PROFILE_STANDARD
) -> list[tuple[str, str, bool]]:
    """Return list of (relative_path, content, is_executable) for all scaffold files.

    ``profile`` (TRDD-e9f13df1, #128) selects the profile-aware generators
    (currently ``scripts/publish.py`` via :func:`gen_publish_py`). Defaults to
    ``standard`` so every existing caller and the byte-identity guarantee are
    unaffected.
    """
    files: list[tuple[str, str, bool]] = [
        # Manifest
        (".claude-plugin/plugin.json", gen_plugin_json(p), False),
        (".gitignore", gen_gitignore(p), False),
    ]
    # Layout C — also emit a self-referential marketplace manifest at repo root
    if p.self_marketplace:
        files.append((".claude-plugin/marketplace.json", gen_self_marketplace_json(p), False))
    # Language-specific project config
    if p.language == "python":
        files.extend(
            [
                ("pyproject.toml", gen_pyproject_toml(p), False),
                (".python-version", gen_python_version(p), False),
            ]
        )
    elif p.language in ("js", "ts"):
        files.append(("package.json", gen_package_json(p), False))
        if p.language == "ts":
            files.append(("tsconfig.json", gen_tsconfig_json(), False))
    elif p.language == "rust":
        files.append(("Cargo.toml", gen_cargo_toml(p), False))
    elif p.language == "go":
        files.append(("go.mod", gen_go_mod(p), False))
    elif p.language == "deno":
        files.append(("deno.json", gen_deno_json(p), False))
    elif p.language == "elixir":
        files.append(("mix.exs", gen_mix_exs(p), False))
    elif p.language == "ruby":
        files.append(("Gemfile", gen_gemfile(p), False))
    elif p.language == "java":
        files.append(("pom.xml", gen_pom_xml(p), False))
    elif p.language == "kotlin":
        files.append(("build.gradle.kts", gen_build_gradle_kts(p), False))
    files.extend(
        [
            # Documentation
            ("README.md", gen_readme(p), False),
            ("LICENSE", gen_license_mit(p), False),
            # Changelog config
            ("cliff.toml", gen_cliff_toml(p), False),
            # cpv-the-skills-menu method (TRDD-9dd64dbf): every newly-scaffolded
            # plugin ships with the catalog skill in place so agents can
            # adopt the dynamic-loading pattern from day 1. Empty until the
            # author adds operational skills.
            ("skills/cpv-the-skills-menu/SKILL.md", gen_the_skills_menu_skill(p), False),
        ]
    )
    # Python-specific scripts + CI/CD — only emitted for python language for now.
    # Non-python plugins get a minimal scaffold and must provide their own CI.
    if p.language == "python":
        files.extend(
            [
                ("scripts/__init__.py", gen_scripts_init(p), False),
                # Profile-aware (TRDD-e9f13df1, #128): a `submodule-build` plugin
                # gets the submodule-aware variant; `standard` is byte-identical.
                ("scripts/publish.py", gen_publish_py(p, profile), True),
                ("scripts/cpv_network_resilience.py", gen_cpv_network_resilience_py(), True),
                ("scripts/setup-hooks.py", gen_setup_hooks_py(), True),
                ("hooks/hooks.json", gen_hooks_json(p), False),
                ("git-hooks/pre-push", gen_pre_push_hook(p), True),
                (".mega-linter.yml", gen_mega_linter_yml(p), False),
                # Issue #143: shared copy-paste-detector config, read by BOTH
                # CI's Mega-Linter COPYPASTE_JSCPD step AND publish.py --gate
                # G2b — one source of truth so the local gate and CI agree on
                # the jscpd threshold/ignores (no green-gate-then-red-CI gap).
                (".jscpd.json", gen_jscpd_json(p), False),
                # RC-1: config-conventional with `body-max-line-length` OFF.
                # Without this file the commitlint job falls back to the bare
                # config, whose 100-char body limit EVERY Dependabot commit body
                # exceeds — failing CI on every bot PR, forever. The meaningful
                # rules (type-enum, subject-*) stay fully enforced.
                (".commitlintrc.json", gen_commitlintrc_json(p), False),
                (".markdownlint.json", gen_markdownlint_json(p), False),
                (".github/workflows/ci.yml", gen_ci_yml(p), False),
                (".github/workflows/release.yml", gen_release_yml(p), False),
                ("tests/__init__.py", gen_tests_init(), False),
            ]
        )
        # Root-cause #9: only emit the marketplace notifier when a REAL
        # marketplace is configured. With no `--marketplace`, the workflow
        # would target the placeholder "my-plugins-marketplace" and fire on
        # every release tag — erroring (gh 404/auth) and showing a red
        # associated workflow because the target/secret are unfilled. Skip it
        # for the placeholder unless `--force-notify` is set (the migration
        # path that genuinely targets the placeholder).
        if p.marketplace or p.force_notify:
            files.append(
                (".github/workflows/notify-marketplace.yml", gen_notify_marketplace_yml(p), False)
            )
        # RC-3: the cspell project dictionary, read by BOTH CI's Mega-Linter
        # SPELL_CSPELL step AND cpv_ci_preflight's local cspell probe — one
        # source of truth, so local and CI can never disagree about which words
        # are known. It is appended LAST, and deliberately so: its word list is
        # seeded from the plugin's own component names, which it reads back out
        # of `files`. Emitting it earlier would silently miss every component
        # appended after it. Python-only, alongside `.mega-linter.yml` — a
        # non-python scaffold ships no Mega-Linter config, so nothing enables
        # SPELL_CSPELL and a dictionary would be dead weight.
        files.append((".cspell.json", gen_cspell_json(p, files), False))
    else:
        # Minimal non-python scaffold — leaves CI/publish to the plugin author,
        # but ships a README section explaining the expected commands.
        files.append(
            (
                f"LANGUAGE-{p.language.upper()}-TODO.md",
                gen_language_todo(p),
                False,
            )
        )
    return files


def gen_language_todo(p: PluginParams) -> str:
    """Generate a TODO note for non-python plugins explaining what to add."""
    # Issue #137: route the README validate command through the source
    # selector. `git` → the pinned `git+…@<ref>` form + an inline `--with
    # pyyaml ` (byte-identical to the historical note); `pypi` → the wheel
    # spec, no pyyaml shim, and a wheel-version pin note.
    cpv_from = cpv_uvx_from_arg(p)
    cpv_pyyaml = "--with pyyaml " if cpv_uvx_needs_pyyaml(p) else ""
    cpv_pin_note = (
        f"`@{p.cpv_ref_resolved}`"
        if p.cpv_source == CPV_SOURCE_GIT
        else f"`{cpv_from}`"
    )
    return f"""# TODO: Wire up CI/CD for `{p.language}` plugin

This plugin was scaffolded with `--language {p.language}`. CPV's Python
scaffold (pyproject.toml, pytest, ruff, publish.py, pre-push hook) was
skipped because it does not apply to your language.

## What you still need to add

1. A lint command (e.g. `eslint`, `cargo clippy`, `golangci-lint`, `deno lint`)
2. A test runner (e.g. `vitest`, `cargo test`, `go test`, `deno test`)
3. A publish/release script that bumps the version in both `plugin.json` AND
   your language manifest (`package.json`, `Cargo.toml`, `go.mod`, `deno.json`)
4. A pre-push git hook that runs lint + tests + CPV validation before pushing
5. GitHub Actions workflows for CI + release

## CPV validates all plugins regardless of language

You can validate this plugin against the CPV ruleset from anywhere using
`uvx` — no need to clone or install CPV. CPV is pinned to the version that
scaffolded this plugin ({cpv_pin_note}) so your validation result is
stable; bump the pin deliberately when you adopt a newer CPV:

```bash
uvx --from {cpv_from} {cpv_pyyaml}\\
    cpv-remote-validate plugin . --strict
```

CPV checks:
- plugin.json manifest
- commands/, agents/, skills/, hooks/ structure
- No hardcoded secrets or personal paths
- Cross-references in all .md files

## Monitor, userConfig, channels, CLAUDE_PLUGIN_OPTION_*

All v2.1.80+ plugin features work regardless of language.
See `skills/cpv-canonical-pipeline/references/v2-1-80-features.md` in the CPV
plugin for schemas and examples.
"""


# =============================================================================
# DIRECTORY CREATION
# =============================================================================

# Standard component directories that every plugin repo should have
COMPONENT_DIRS = [
    ".claude-plugin",
    ".github/workflows",
    "agents",
    "commands",
    "git-hooks",
    "hooks",
    "scripts",
    "skills",
    "tests",
]


def generate_plugin_repo(
    target: Path, p: PluginParams, dry_run: bool = False, profile: str | None = None
) -> list[str]:
    """Write all scaffold files to target directory. Returns list of created file paths.

    ``profile`` (TRDD-e9f13df1, issues #128 / #115) selects the cpv-canonical-pipeline
    VARIANT used for the profile-aware generators (currently ``scripts/publish.py``
    via :func:`gen_publish_py`). When ``None`` (the default — fresh scaffolds and
    every existing caller), the profile is resolved from ``target`` via
    :func:`resolve_pipeline_profile`: an empty / standard target resolves to
    ``standard``, so the emitted publish.py is byte-identical to today. A
    regen/standardize callsite that already knows the plugin is e.g.
    ``submodule-build`` may pass it explicitly to preserve the submodule-aware
    variant instead of clobbering it with the standard one. An unrecognized value
    falls back to ``standard`` (fail-safe — never silently disable the canon).
    """
    if profile is None:
        profile = resolve_pipeline_profile(target)
    if profile not in KNOWN_PROFILES:
        profile = PROFILE_STANDARD
    created: list[str] = []

    # Create component directories (including empty ones for plugin structure)
    for dir_name in COMPONENT_DIRS:
        dir_path = target / dir_name
        if dry_run:
            print(f"  {BLUE}[dry-run]{NC} mkdir -p {dir_path}")
        else:
            dir_path.mkdir(parents=True, exist_ok=True)
        created.append(str(dir_path) + "/")

    # Write all generated files (profile-aware: a `submodule-build` plugin gets
    # the submodule-aware publish.py variant; `standard` is byte-identical).
    all_files = generate_all_files(p, profile)
    for rel_path, content, is_executable in all_files:
        file_path = target / rel_path

        if dry_run:
            print(f"  {BLUE}[dry-run]{NC} write {file_path} ({len(content)} bytes){' [exec]' if is_executable else ''}")
            created.append(str(file_path))
            continue

        # Ensure parent directory exists
        file_path.parent.mkdir(parents=True, exist_ok=True)

        # Write the file
        file_path.write_text(content, encoding="utf-8")

        # Set executable bit if needed
        if is_executable:
            file_path.chmod(file_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

        created.append(str(file_path))

    return created


# =============================================================================
# MAIN
# =============================================================================


def main() -> int:
    """Parse CLI arguments and generate the plugin repository scaffold."""
    parser = argparse.ArgumentParser(
        description="Generate a complete Claude Code plugin repository scaffold.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  uv run scripts/generate_plugin_repo.py /tmp/my-plugin \\
    --name my-plugin --description "A cool plugin" \\
    --author "John Doe" --author-email "john@example.com" \\
    --github-owner johndoe --marketplace my-marketplace

  uv run scripts/generate_plugin_repo.py ./new-plugin \\
    --name new-plugin --description "Plugin desc" \\
    --author Emasoft --author-email "713559+Emasoft@users.noreply.github.com" \\
    --github-owner Emasoft --marketplace claude-plugins-marketplace \\
    --dry-run
""",
    )
    parser.add_argument("target_dir", type=Path, help="Target directory for the new plugin repo")
    parser.add_argument("--name", required=True, help="Plugin name (lowercase, hyphens allowed)")
    parser.add_argument("--description", required=True, help="One-line plugin description")
    parser.add_argument("--author", required=True, help="Author display name")
    parser.add_argument("--author-email", required=True, help="Author email")
    parser.add_argument("--license", default="MIT", help="SPDX license identifier (default: MIT)")
    parser.add_argument("--python-version", default="3.12", help="Minimum Python version (default: 3.12)")
    parser.add_argument("--github-owner", default="", help="GitHub account or organization name")
    parser.add_argument("--marketplace", default="", help="Marketplace name for install commands")
    parser.add_argument("--version", default="0.1.0", help="Initial version (default: 0.1.0)")
    parser.add_argument(
        "--language",
        choices=sorted(VALID_LANGUAGES) + ["auto"],
        default="python",
        help="Plugin language (default: python). Use 'auto' to detect from "
        "an existing manifest in target_dir (uses detect_language). "
        "Non-python emits a minimal scaffold.",
    )
    parser.add_argument(
        "--self-marketplace",
        action="store_true",
        help="Layout C: also emit .claude-plugin/marketplace.json with a self-entry "
        '(source: "./"). Use when the repo should be both plugin and marketplace.',
    )
    # TRDD-793ac32a: dev-stripping. Default ON — emits cpv.strip block in
    # plugin.json so the user can run `cpv strip-dev-parts` later. The actual
    # extraction is NOT done at scaffold time; only the configuration.
    parser.add_argument(
        "--strip-dev",
        dest="strip_dev",
        action="store_true",
        default=True,
        help="(default) Emit cpv.strip block in plugin.json so dev-only "
        "folders can later be moved to per-plugin git submodules via "
        "`cpv strip-dev-parts` (TRDD-793ac32a). Saves ~12 MB per cache install.",
    )
    parser.add_argument(
        "--no-strip-dev",
        dest="strip_dev",
        action="store_false",
        help="Disable dev-stripping config (legacy mode — keep all dev parts in MAIN repo).",
    )
    parser.add_argument(
        "--cpv-ref",
        default="",
        metavar="REF",
        help="Git ref (tag/branch/SHA) the generated pipeline pins the CPV "
        "validator to (publish.py, ci.yml, release.yml, README). Default: the "
        "version of CPV doing the scaffolding, prefixed 'v' (e.g. v2.133.0). "
        "Pinning — not tracking HEAD — keeps the cold uvx-from-git build cached "
        "per tag and stops a stricter future CPV release from breaking CI with "
        "no plugin change.",
    )
    parser.add_argument(
        "--cpv-source",
        choices=sorted(VALID_CPV_SOURCES),
        default=CPV_SOURCE_GIT,
        help="Which CPV distribution the generated pipeline fetches the "
        "validator from (issue #137). 'git' (default, NON-BREAKING) builds CPV "
        "from source: `uvx --from git+https://…@<ref> --with pyyaml`. 'pypi' "
        "fetches the published wheel: `uvx --from claude-plugins-validation==<ver>` "
        "(fast, no compile, pyyaml is a declared wheel dependency so the "
        "`--with pyyaml` shim is dropped). 'pypi' pins to the same <ref> as "
        "--cpv-ref with any leading 'v' stripped.",
    )
    parser.add_argument(
        "--force-notify",
        dest="force_notify",
        action="store_true",
        default=False,
        help="Emit notify-marketplace.yml even when no real marketplace is set "
        "(the placeholder 'my-plugins-marketplace'). By default the notify "
        "workflow is skipped for the placeholder so a release never shows a red "
        "associated workflow because the secret/target were never configured.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Preview files without writing")

    # ── Phase 6: single-input slurp flags ────────────────────────────────
    # Each --skill/--agent/--command/--mcp-server/--scripts copies its
    # input into the right component folder of the new plugin. --from PATH
    # auto-classifies (best-effort): SKILL.md → skill; .md with
    # `allowed-tools:` frontmatter → command; .md with `tools:`/no
    # allowed-tools → agent; .mcp.json → MCP server; directory of scripts
    # → scripts/. Multiple flags can be combined.
    slurp_grp = parser.add_argument_group("slurp inputs (Phase 6)")
    slurp_grp.add_argument(
        "--from",
        dest="from_path",
        type=Path,
        action="append",
        default=[],
        metavar="PATH",
        help="Auto-classify and copy a file or folder into the new plugin "
        "(may repeat). SKILL.md → skill; .md with allowed-tools → command; "
        ".md without → agent; .mcp.json → MCP config; directory → scripts/.",
    )
    slurp_grp.add_argument(
        "--skill",
        type=Path,
        action="append",
        default=[],
        metavar="PATH",
        help="Copy SKILL.md (or folder containing SKILL.md) into skills/<name>/. May repeat.",
    )
    slurp_grp.add_argument(
        "--agent",
        type=Path,
        action="append",
        default=[],
        metavar="PATH",
        help="Copy a .md agent file into agents/. May repeat.",
    )
    slurp_grp.add_argument(
        "--command",
        type=Path,
        action="append",
        default=[],
        metavar="PATH",
        help="Copy a .md command file into commands/. May repeat.",
    )
    slurp_grp.add_argument(
        "--mcp-server",
        type=Path,
        action="append",
        default=[],
        metavar="PATH",
        help="Copy .mcp.json (or a directory containing one) into the plugin root. May repeat.",
    )
    slurp_grp.add_argument(
        "--scripts",
        type=Path,
        action="append",
        default=[],
        metavar="DIR",
        help="Copy all files from DIR into scripts/. May repeat.",
    )

    args = parser.parse_args()

    target = args.target_dir.resolve()

    # TRDD-83ab59e7: --language auto resolves against any pre-existing
    # manifest in target_dir. We resolve BEFORE constructing PluginParams
    # so the rest of the pipeline sees a concrete language and never has
    # to special-case "auto" again.
    resolved_language = resolve_language(args.language, target)
    if args.language == "auto":
        print(f"{BLUE}--language auto detected:{NC} {resolved_language}")

    # Build params
    params = PluginParams(
        name=args.name,
        description=args.description,
        author=args.author,
        author_email=args.author_email,
        license=args.license,
        python_version=args.python_version,
        github_owner=args.github_owner,
        marketplace=args.marketplace,
        version=args.version,
        language=resolved_language,
        self_marketplace=args.self_marketplace,
        strip_dev=args.strip_dev,
        cpv_ref=args.cpv_ref,
        cpv_source=args.cpv_source,
        force_notify=args.force_notify,
    )

    # Check target directory
    if target.exists() and any(target.iterdir()):
        print(f"{YELLOW}WARNING: Target directory is not empty: {target}{NC}")
        print(f"{YELLOW}Files will be added/overwritten.{NC}")

    print(f"\n{BOLD}Generating plugin scaffold: {params.name}{NC}")
    print(f"  Target: {target}")
    print(f"  Version: {params.version}")
    print(f"  Author: {params.author} <{params.author_email}>")
    print(f"  License: {params.license}")
    if params.github_owner:
        print(f"  GitHub: {params.github_url}")
    if params.marketplace:
        print(f"  Marketplace: {params.marketplace}")
    if params.self_marketplace:
        print("  Layout: C (marketplace-in-plugin, self-referential)")
    if args.dry_run:
        print(f"  {YELLOW}(dry-run mode){NC}")
    print()

    created = generate_plugin_repo(target, params, dry_run=args.dry_run)

    # ── Phase 6: post-scaffold slurp of user-provided inputs ─────────
    slurp_count = 0
    if not args.dry_run:
        slurp_count = _do_slurp(
            target,
            from_paths=args.from_path,
            skill_paths=args.skill,
            agent_paths=args.agent,
            command_paths=args.command,
            mcp_paths=args.mcp_server,
            scripts_paths=args.scripts,
        )
    elif any([args.from_path, args.skill, args.agent, args.command, args.mcp_server, args.scripts]):
        print(
            f"  {YELLOW}(dry-run: --from / --skill / --agent / --command / "
            f"--mcp-server / --scripts inputs are NOT slurped){NC}"
        )

    # Summary
    file_count = sum(1 for f in created if not f.endswith("/"))
    dir_count = sum(1 for f in created if f.endswith("/"))
    extra = f" + {slurp_count} slurped from --from/--skill/--agent/etc" if slurp_count else ""
    print(f"\n{GREEN}{BOLD}Done!{NC} Created {file_count} files in {dir_count} directories{extra}.")

    if not args.dry_run:
        print(f"\n{BOLD}Next steps:{NC}")
        print(f"  cd {target}")
        print("  git init && git add -A && git commit -m 'Initial scaffold'")
        print(f"  uv venv --python {params.python_version} && source .venv/bin/activate")
        print("  uv pip install -e .")
        print("  uv run python scripts/setup-hooks.py")
        print(f"\n{BOLD}After first push to GitHub:{NC}")
        print("  # Apply the server-side ruleset that enforces CI as a required check")
        print("  uv run python scripts/publish.py --install-branch-rules")

    return 0


if __name__ == "__main__":
    sys.exit(main())
