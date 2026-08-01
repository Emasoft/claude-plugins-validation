#!/usr/bin/env python3
"""Ship-only-binary canon — the three checks the canon shipped without (issue #185 §5/§6/§7).

`cpv_binary_attestation.py` closed §2/§3: a shipped binary now carries a record
tying it to a source commit. These are the three remaining holes the same design
review named, and each one is a case where the canon MOVES something out of the
plugin and then never checks what it moved it to.

**§5 — extracted-repo rot.** The canon moves compile source to a SEPARATE repo and
records it in `.claude-plugin/plugin.json` under `cpv.strip.extract[]` as
`{path, url, sha}`. Nothing has ever verified those still resolve. A record whose
repo was deleted, renamed, or made private is a dangling audit trail that still
READS like provenance — worse than no record, because it looks checked.
`verify_extract_records` validates the record SHAPE offline, and contacts the pinned
sha only when explicitly asked (`network=True`). Offline is the default because an
airgapped or pre-install scan must never fail for want of a network — but a check
that could not run must NEVER read as a check that passed, so an uncontacted record
is reported as `RC-EXTRACT-UNVERIFIED` rather than silently omitted.

**§6 — licence/NOTICE gating.** Shipping only the binary drops the source tree that
carried the LICENSE, so the installed artifact can end up with no licence text at
all — the user runs compiled code under terms nobody stated. `verify_binary_licences`
fires only when compiled artifacts actually ship; a source-only plugin is out of
scope (its own repo licence travels with it).

**§7 — build-graph-role reframing.** VERIFIED DEFECT, not a hypothesis: the existing
compiled-source rule keys on file EXTENSION (`validate_plugin.compiled_languages`
maps `.rs`/`.go`/`.c`/`.cs`/…), so it is structurally blind to a `dist/` bundle whose
source is `.ts` — a transpiled plugin ships both halves of its build graph and draws
nothing. `classify_build_role` asks what a path's ROLE is (does the build produce it,
or consume it?) instead of what its extension is, and `RC-BUILD-OUTPUT-SHIPS-SOURCE`
fires when both a build output and its generating source ship in the same tree.

WHAT THESE DO NOT PROVE. §5 verifies a commit is REACHABLE, never that it contains
the source the binary was built from (only a rebuild can — see the attestation
module). §7 infers roles from declared build config and conventional layout; it is
deliberately scoped to the transpiled families (js/css) that the extension-keyed rule
cannot see, so it neither duplicates nor second-guesses `RC-SHIP-BINARY-ONLY`.

FAIL-SAFE POSTURE, uniform across all three: a manifest that will not parse, a git
that will not run, a config that is not the shape we expected — every one of those
yields NO finding rather than a guess or a crash. A validator that invents a finding
from an IO error is worse than one that stays quiet, because the finding is
indistinguishable from a real one. The single exception is §5's UNVERIFIED line,
which exists precisely so "could not check" is visible instead of absent.

Every function returns `(rule_id, message)` pairs; the CALLER owns severity, exactly
as `cpv_binary_attestation.verify_attestations` does, so the same logic serves both
the advisory migration window and a later blocking canon.
"""

from __future__ import annotations

import json
import posixpath
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Final

from cpv_binary_attestation import iter_shipped_binaries
from cpv_pipeline_profile import _load_manifest

__all__ = [
    "EXTRACT_REQUIRED_FIELDS",
    "classify_build_role",
    "verify_binary_licences",
    "verify_build_roles",
    "verify_extract_records",
]

# ---------------------------------------------------------------------------
# §5 — extracted-repo rot
# ---------------------------------------------------------------------------

# A post-strip RECORD answers three questions: WHAT was removed from the tree
# (path), WHERE it went (url), and WHICH revision (sha). Any one missing makes the
# record unusable for `--restore` and worthless as provenance.
EXTRACT_REQUIRED_FIELDS: Final[tuple[str, ...]] = ("path", "url", "sha")

# The only URL shape `cpv_strip_dev` ever writes, re-validated here rather than
# trusted: an https github.com/<owner>/<repo>. No ssh, no embedded credentials, no
# port, no third path segment. This is also what makes the §5 network probe safe —
# the host is pinned by the grammar, so a manifest can never redirect the fetch at
# an attacker-chosen endpoint.
_GITHUB_HTTPS_URL_RE: Final[re.Pattern[str]] = re.compile(
    r"^https://github\.com/([A-Za-z0-9][A-Za-z0-9._-]*)/([A-Za-z0-9][A-Za-z0-9._-]*)$"
)

# 7 hex is git's own shortest unambiguous abbreviation; 40 is a full sha1 object
# id, which is what the strip recorder writes. The upper bound stays at 40 rather
# than widening to a 64-hex sha256 id, because widening it would also admit every
# 41-63 character typo — and an abbreviated-or-mistyped sha is exactly the record
# that silently stops resolving later.
_EXTRACT_SHA_RE: Final[re.Pattern[str]] = re.compile(r"^[0-9a-fA-F]{7,40}$")

_GITHUB_API_TIMEOUT_S: Final[int] = 15


def _string_field(entry: dict[str, Any], key: str) -> str:
    """`entry[key]` as a stripped string, or "" when absent/blank/not a string."""
    value = entry.get(key)
    return value.strip() if isinstance(value, str) else ""


def _split_github_url(url: str) -> tuple[str, str] | None:
    """(owner, repo) for a well-formed https GitHub URL, else None.

    A single trailing `.git` is stripped before matching so both spellings of the
    same repo validate identically; `..` anywhere is refused outright rather than
    reasoned about.
    """
    if ".." in url:
        return None
    trimmed = url[: -len(".git")] if url.endswith(".git") else url
    m = _GITHUB_HTTPS_URL_RE.match(trimmed)
    if m is None:
        return None
    return m.group(1), m.group(2)


def _load_extract_entries(plugin_root: Path) -> tuple[list[dict[str, Any]], list[str]]:
    """Return (records, schema_errors) from `cpv.strip.extract[]`.

    A pre-strip DECLARATION (`{src, submodule}` — author-written, telling strip-dev
    what to extract) is NOT a record and is skipped silently: flagging it would
    report every plugin that has configured the canon but not yet run it. An entry
    carrying ANY record-shaped key is treated as a record attempt and must then
    carry all three, so a half-written record cannot hide in the declaration lane.
    """
    manifest = _load_manifest(plugin_root)
    cpv_block = manifest.get("cpv")
    if not isinstance(cpv_block, dict):
        return [], []
    strip_block = cpv_block.get("strip")
    if not isinstance(strip_block, dict):
        return [], []
    raw = strip_block.get("extract")
    if raw is None:
        return [], []
    if not isinstance(raw, list):
        return [], [f"cpv.strip.extract must be an array, got {type(raw).__name__}"]

    records: list[dict[str, Any]] = []
    errors: list[str] = []
    for idx, item in enumerate(raw):
        if not isinstance(item, dict):
            errors.append(f"cpv.strip.extract[{idx}] must be an object, got {type(item).__name__}")
            continue
        if not any(key in item for key in EXTRACT_REQUIRED_FIELDS):
            # A pure declaration: nothing has been extracted yet, so there is no
            # reference that could have rotted.
            continue
        label = _string_field(item, "path") or _string_field(item, "url") or "?"
        missing = [f for f in EXTRACT_REQUIRED_FIELDS if not _string_field(item, f)]
        if missing:
            errors.append(f"cpv.strip.extract[{idx}] ({label}) is missing: {', '.join(missing)}")
            continue
        url = _string_field(item, "url")
        if _split_github_url(url) is None:
            errors.append(
                f"cpv.strip.extract[{idx}] ({label}) url is not an https://github.com/<owner>/<repo> "
                f"reference: {url}"
            )
            continue
        sha = _string_field(item, "sha")
        if not _EXTRACT_SHA_RE.match(sha):
            errors.append(
                f"cpv.strip.extract[{idx}] ({label}) sha is not a 7-40 character git object id: {sha}"
            )
            continue
        records.append(item)
    return records, errors


def _github_commit_exists(owner: str, repo: str, sha: str) -> bool | None:
    """True/False when GitHub answered, None when it could not be asked.

    None is a THIRD state on purpose. A rate-limit, a proxy, or a 5xx tells us
    nothing about the commit, and collapsing that into False would manufacture a
    rot finding out of a flaky network — the one thing that would teach a reader to
    ignore this rule.
    """
    url = f"https://api.github.com/repos/{owner}/{repo}/commits/{sha}"
    request = urllib.request.Request(  # noqa: S310 - host is pinned by _GITHUB_HTTPS_URL_RE
        url,
        headers={"Accept": "application/vnd.github+json", "User-Agent": "cpv-ship-canon"},
    )
    try:
        with urllib.request.urlopen(request, timeout=_GITHUB_API_TIMEOUT_S) as response:  # noqa: S310
            return 200 <= int(response.status) < 300
    except urllib.error.HTTPError as exc:
        # 404: repo gone/renamed/private, or the sha is not in it. 422: GitHub's
        # answer for a syntactically valid ref it cannot resolve. Both are rot.
        if exc.code in (404, 422):
            return False
        return None
    except (urllib.error.URLError, OSError, ValueError):
        return None


def verify_extract_records(plugin_root: Path, *, network: bool = False) -> list[tuple[str, str]]:
    """Check that `cpv.strip.extract[]` still points at something resolvable.

    Offline by default: with `network=False` the SHAPE of every record is checked
    and the records are reported as UNVERIFIED, because a validator that silently
    skipped the only check that can detect rot would be indistinguishable from one
    that ran it and found nothing wrong.

    With `network=True` each pinned sha is looked up. A definitive negative is
    `RC-EXTRACT-ROT`; anything short of a definitive answer stays UNVERIFIED.
    """
    findings: list[tuple[str, str]] = []
    records, errors = _load_extract_entries(plugin_root)

    for err in errors:
        findings.append(("RC-EXTRACT-MALFORMED", f"RC-EXTRACT-MALFORMED: {err}"))

    if not records:
        return findings

    if not network:
        listed = ", ".join(sorted(_string_field(r, "path") or _string_field(r, "url") for r in records))
        findings.append(
            (
                "RC-EXTRACT-UNVERIFIED",
                f"RC-EXTRACT-UNVERIFIED: {len(records)} extracted-source record(s) ({listed}) were NOT "
                f"contacted — this scan ran offline, so the pinned commit was never checked to still "
                f"exist. The record is well-formed; whether it still resolves is unknown. Re-run with "
                f"the network check enabled to verify it.",
            )
        )
        return findings

    for record in records:
        path = _string_field(record, "path")
        url = _string_field(record, "url")
        sha = _string_field(record, "sha")
        parts = _split_github_url(url)
        if parts is None:  # pragma: no cover - _load_extract_entries already rejected these
            continue
        owner, repo = parts
        exists = _github_commit_exists(owner, repo, sha)
        if exists is True:
            continue
        if exists is False:
            findings.append(
                (
                    "RC-EXTRACT-ROT",
                    f"RC-EXTRACT-ROT: '{path}' was extracted to {url} at {sha[:12]}, which no longer "
                    f"resolves — the repository is gone, renamed, or private, or the commit was removed. "
                    f"The source this plugin's shipped artifact came from is unreachable, so its "
                    f"provenance record proves nothing. Restore the repo or re-record the reference.",
                )
            )
            continue
        findings.append(
            (
                "RC-EXTRACT-UNVERIFIED",
                f"RC-EXTRACT-UNVERIFIED: '{path}' at {url} could not be contacted (network error, rate "
                f"limit, or an unexpected response), so its pinned commit {sha[:12]} was neither "
                f"confirmed nor disproved. This is NOT a clean result.",
            )
        )
    return findings


# ---------------------------------------------------------------------------
# §6 — licence / NOTICE gating
# ---------------------------------------------------------------------------

# Matches LICENSE, LICENCE, LICENSES, COPYING, NOTICE with any extension or
# qualifier (LICENSE-MIT, COPYING.LESSER, NOTICE.txt). Deliberately generous: the
# cost of accepting one oddly-named file is nothing, while a false "you ship no
# licence" on a plugin that does is a wrong accusation about its legal terms.
_LICENCE_NAME_RE: Final[re.Pattern[str]] = re.compile(
    r"^(?:licen[sc]es?|copying|notice)(?:[._-].*)?$", re.IGNORECASE
)


def _has_licence_entry(directory: Path) -> bool:
    """True when `directory` holds a licence-shaped file or a non-empty licence dir."""
    try:
        entries = list(directory.iterdir())
    except OSError:
        return False
    for entry in entries:
        if not _LICENCE_NAME_RE.match(entry.name):
            continue
        if entry.is_file():
            return True
        if entry.is_dir():
            # The SPDX/REUSE layout puts the texts in a LICENSES/ directory. An
            # EMPTY one is not a licence, so it must not satisfy the check.
            try:
                if any(entry.iterdir()):
                    return True
            except OSError:
                continue
    return False


def verify_binary_licences(plugin_root: Path) -> list[tuple[str, str]]:
    """Flag a plugin that ships compiled artifacts but no licence text.

    Scoped to plugins that ACTUALLY ship a binary, reusing the same detector the
    attestation module uses (magic bytes, not the executable bit) so the two rules
    can never disagree about what "ships a binary" means. A source-only plugin is
    out of scope: its licence question is the repository's, not the artifact's.
    """
    binaries = iter_shipped_binaries(plugin_root)
    if not binaries:
        return []

    if _has_licence_entry(plugin_root) or _has_licence_entry(plugin_root / "bin"):
        return []

    shipped = ", ".join(sorted(p.relative_to(plugin_root).as_posix() for p in binaries)[:5])
    return [
        (
            "RC-BINARY-NO-LICENCE",
            f"RC-BINARY-NO-LICENCE: this plugin ships {len(binaries)} compiled artifact(s) ({shipped}) "
            f"but carries no LICENSE / LICENCE / COPYING / NOTICE file at its root or in bin/. Under the "
            f"ship-only-binary canon the source tree that held the licence is no longer part of the "
            f"plugin, so the installed artifact states no terms at all — the user runs compiled code "
            f"under a licence nobody declared. Add the licence text (and any NOTICE the dependencies "
            f"require) beside the binaries.",
        )
    ]


# ---------------------------------------------------------------------------
# §7 — build-graph roles
# ---------------------------------------------------------------------------

# A directory segment that names build OUTPUT by convention. Matched at any depth,
# because a monorepo-shaped plugin puts them at packages/<name>/dist.
_OUTPUT_DIR_SEGMENTS: Final[frozenset[str]] = frozenset({"dist", "build", "out"})
# Cargo's layout: only `target/release` is output; a bare `target` segment could be
# an ordinary directory name.
_OUTPUT_DIR_PAIRS: Final[tuple[tuple[str, str], ...]] = (("target", "release"),)
_BUNDLED_SUFFIXES: Final[tuple[str, ...]] = (".min.js", ".bundle.js", ".min.css", ".bundle.css")

# Source extensions mapped to the output FAMILY the build turns them into. Scoped
# on purpose to the transpiled families: a compiled language shipping its source
# beside a binary is already RC-SHIP-BINARY-ONLY's finding, and reporting it twice
# would train readers to skim both.
_SOURCE_EXT_FAMILY: Final[dict[str, str]] = {
    ".ts": "js",
    ".tsx": "js",
    ".jsx": "js",
    ".mts": "js",
    ".cts": "js",
    ".coffee": "js",
    ".svelte": "js",
    ".vue": "js",
    ".scss": "css",
    ".sass": "css",
    ".less": "css",
    ".styl": "css",
}
_OUTPUT_EXT_FAMILY: Final[dict[str, str]] = {
    ".js": "js",
    ".mjs": "js",
    ".cjs": "js",
    ".css": "css",
}

_WALK_SKIP_DIRS: Final[frozenset[str]] = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        "node_modules",
        "__pycache__",
        ".venv",
        "venv",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        ".idea",
        ".vscode",
        "vendor",
    }
)

_BUILD_SCRIPT_KEYS: Final[tuple[str, ...]] = ("build", "compile", "bundle", "prepare", "prepublishonly", "dist")
# Path-shaped tokens inside a build script command line. Bounded, no lookarounds
# (the catalog must stay re2-safe), and the extension filter happens in code.
_SCRIPT_PATH_TOKEN_RE: Final[re.Pattern[str]] = re.compile(r"[A-Za-z0-9_@./-]+\.[A-Za-z0-9]+")


def _run_git(plugin_root: Path, *args: str) -> str | None:
    """git stdout, or None when git could not answer. Never raises."""
    try:
        result = subprocess.run(  # noqa: S603 - fixed argv, no shell
            ["git", *args],
            cwd=plugin_root,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout if result.returncode == 0 else None


def _shipped_files(plugin_root: Path) -> list[str]:
    """Relative POSIX paths of the files the plugin SHIPS, sorted.

    Tracked files are the shipped surface: a `dist/` that is gitignored and
    untracked is a local build artifact the installed plugin never contains, and
    reporting it would be a false accusation. Tracked-AND-gitignored still ships,
    and `git ls-files` still lists it — matching CPV's standing anti-evasion
    invariant (a .gitignore entry does not untrack an already-tracked file).

    With no usable git answer (not a repo, git absent, nothing staged yet) the
    filesystem walk is the fallback, which is the right behaviour for the
    downloaded-tarball / pre-install-scan case.
    """
    out = _run_git(plugin_root, "ls-files", "-z")
    if out:
        tracked = [name for name in out.split("\0") if name]
        files = [name for name in tracked if (plugin_root / name).is_file()]
        if files:
            return sorted(files)
    return sorted(_walk_files(plugin_root))


def _walk_files(plugin_root: Path) -> list[str]:
    found: list[str] = []
    stack = [plugin_root]
    while stack:
        current = stack.pop()
        try:
            entries = list(current.iterdir())
        except OSError:
            continue
        for entry in entries:
            try:
                if entry.is_dir():
                    if entry.name not in _WALK_SKIP_DIRS:
                        stack.append(entry)
                elif entry.is_file():
                    found.append(entry.relative_to(plugin_root).as_posix())
            except OSError:
                continue
    return found


def _normalize_rel(value: str) -> str:
    """Normalize a config-declared path to a plugin-root-relative POSIX path."""
    cleaned = value.strip().replace("\\", "/").strip("/")
    while cleaned.startswith("./"):
        cleaned = cleaned[2:]
    return posixpath.normpath(cleaned) if cleaned else ""


def _under_any(rel: str, prefixes: set[str]) -> bool:
    return any(rel == prefix or rel.startswith(prefix + "/") for prefix in prefixes if prefix)


def _under_output_dir(rel: str) -> bool:
    parts = rel.split("/")[:-1]
    if any(part in _OUTPUT_DIR_SEGMENTS for part in parts):
        return True
    return any(
        parts[i] == first and parts[i + 1] == second
        for first, second in _OUTPUT_DIR_PAIRS
        for i in range(len(parts) - 1)
    )


def _load_json(path: Path) -> dict[str, Any] | None:
    """Parse a JSON config, or None. A tsconfig with comments is JSONC and will not
    parse — that yields NO signal rather than a guessed one."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _collect_strings(value: Any, out: list[str], depth: int = 0) -> None:
    """Flatten the strings out of a nested `exports`-style value. Depth-bounded so a
    pathological manifest cannot make this walk forever."""
    if depth > 6:
        return
    if isinstance(value, str):
        out.append(value)
    elif isinstance(value, list):
        for item in value:
            _collect_strings(item, out, depth + 1)
    elif isinstance(value, dict):
        for item in value.values():
            _collect_strings(item, out, depth + 1)


def _declared_output_prefixes(plugin_root: Path) -> set[str]:
    """Paths a build manifest names as its OUTPUT (tsconfig outDir / outFile)."""
    prefixes: set[str] = set()
    tsconfig = _load_json(plugin_root / "tsconfig.json")
    if tsconfig is None:
        return prefixes
    options = tsconfig.get("compilerOptions")
    if not isinstance(options, dict):
        return prefixes
    for key in ("outDir", "outFile"):
        value = options.get(key)
        if isinstance(value, str):
            normalized = _normalize_rel(value)
            if normalized and normalized != ".":
                prefixes.add(normalized)
    return prefixes


def _declared_build_inputs(plugin_root: Path) -> tuple[set[str], set[str]]:
    """(input_roots, entry_paths) — where the build reads from, per its own config.

    Roots come from evidence the plugin itself declares (tsconfig include/files/
    rootDir, a Cargo manifest's conventional `src/`, source paths named in build
    scripts), plus a `src/` directory if one exists. Entry paths (package.json
    main/module/browser/exports) are recorded but their DIRECTORIES are NOT made
    roots — `main` habitually points at the built bundle, so promoting `dist` to an
    input root would invert the whole classification.
    """
    roots: set[str] = set()
    entries: set[str] = set()

    if (plugin_root / "src").is_dir():
        roots.add("src")

    tsconfig = _load_json(plugin_root / "tsconfig.json")
    if tsconfig is not None:
        options = tsconfig.get("compilerOptions")
        if isinstance(options, dict) and isinstance(options.get("rootDir"), str):
            normalized = _normalize_rel(options["rootDir"])
            if normalized and normalized != ".":
                roots.add(normalized)
        include = tsconfig.get("include")
        if isinstance(include, list):
            for pattern in include:
                if not isinstance(pattern, str):
                    continue
                root = _glob_prefix(pattern)
                if root:
                    roots.add(root)
        files = tsconfig.get("files")
        if isinstance(files, list):
            for item in files:
                if isinstance(item, str):
                    normalized = _normalize_rel(item)
                    parent = posixpath.dirname(normalized)
                    if parent:
                        roots.add(parent)

    if (plugin_root / "Cargo.toml").is_file():
        roots.add("src")

    package = _load_json(plugin_root / "package.json")
    if package is not None:
        for key in ("main", "module", "browser", "exports"):
            collected: list[str] = []
            _collect_strings(package.get(key), collected)
            for value in collected:
                normalized = _normalize_rel(value)
                if normalized and "*" not in normalized:
                    entries.add(normalized)
        scripts = package.get("scripts")
        if isinstance(scripts, dict):
            for name, command in scripts.items():
                if not isinstance(name, str) or not isinstance(command, str):
                    continue
                if not any(key in name.lower() for key in _BUILD_SCRIPT_KEYS):
                    continue
                for token in _SCRIPT_PATH_TOKEN_RE.findall(command):
                    normalized = _normalize_rel(token)
                    if Path(normalized).suffix.lower() not in _SOURCE_EXT_FAMILY:
                        continue
                    parent = posixpath.dirname(normalized)
                    if parent:
                        roots.add(parent)

    return {r for r in roots if r and r != "."}, entries


def _glob_prefix(pattern: str) -> str:
    """The fixed directory prefix of a glob (`src/**/*.ts` -> `src`)."""
    normalized = _normalize_rel(pattern)
    parts: list[str] = []
    for part in normalized.split("/"):
        if any(ch in part for ch in "*?["):
            break
        parts.append(part)
    if parts and Path(parts[-1]).suffix:
        parts.pop()
    return "/".join(parts)


def _is_build_output(rel: str, shipped: set[str], declared: set[str]) -> bool:
    """Does this path's ROLE read as build output?

    A source-language file is never an output by LOCATION — that is the whole point
    of the reframing. A `.ts` inside `dist/` is source that was shipped into the
    output directory, which is exactly the shape §7 exists to surface; classifying
    it as an output would erase the finding.
    """
    name = rel.rsplit("/", 1)[-1].lower()
    suffix = Path(rel).suffix.lower()
    if suffix in _SOURCE_EXT_FAMILY:
        return False
    if name.endswith(_BUNDLED_SUFFIXES):
        return True
    if suffix == ".map":
        return True
    if _under_output_dir(rel):
        return True
    if rel + ".map" in shipped:
        return True
    return _under_any(rel, declared)


def _normalized_stem(rel: str) -> str:
    """`app.min.js` -> `app`; `app.js` -> `app`."""
    stem = Path(rel).stem
    for marker in (".min", ".bundle"):
        if stem.lower().endswith(marker):
            stem = stem[: -len(marker)]
    return stem


def _generating_sources(output: str, shipped: list[str], outputs: set[str], roots: set[str]) -> list[str]:
    """The shipped source files this output was plausibly generated FROM.

    Pairing requires three agreements at once — output family, file stem, and
    location (under a declared input root, or in the output's own directory). Any
    one alone would over-fire: a `test/utils.js` shares a stem with `dist/utils.js`
    without generating it.
    """
    family = _OUTPUT_EXT_FAMILY.get(Path(output).suffix.lower())
    if family is None:
        return []
    stem = _normalized_stem(output)
    output_dir = posixpath.dirname(output)
    found: list[str] = []
    for candidate in shipped:
        if candidate in outputs or candidate == output:
            continue
        if _SOURCE_EXT_FAMILY.get(Path(candidate).suffix.lower()) != family:
            continue
        if Path(candidate).stem != stem:
            continue
        if _under_any(candidate, roots) or posixpath.dirname(candidate) == output_dir:
            found.append(candidate)
    return sorted(found)


def classify_build_role(plugin_root: Path) -> dict[str, Any]:
    """Partition the shipped tree into build INPUTS and OUTPUTS, by role.

    Returns `{"outputs", "inputs", "input_roots", "output_prefixes", "findings"}`,
    where `findings` holds `(rule_id, message)` pairs in the same shape as the other
    checks here. The classification is returned alongside the findings on purpose:
    a role judgement that cannot be inspected is a role judgement nobody can argue
    with, and this one is inferred, not proven.
    """
    empty: dict[str, Any] = {
        "outputs": [],
        "inputs": [],
        "input_roots": [],
        "output_prefixes": [],
        "findings": [],
    }
    try:
        shipped = _shipped_files(plugin_root)
    except OSError:  # pragma: no cover - _walk_files already swallows per-entry errors
        return empty
    if not shipped:
        return empty

    shipped_set = set(shipped)
    declared_outputs = _declared_output_prefixes(plugin_root)
    input_roots, entry_paths = _declared_build_inputs(plugin_root)

    outputs = [rel for rel in shipped if _is_build_output(rel, shipped_set, declared_outputs)]
    output_set = set(outputs)
    inputs = [
        rel
        for rel in shipped
        if rel not in output_set
        and (
            (Path(rel).suffix.lower() in _SOURCE_EXT_FAMILY and _under_any(rel, input_roots))
            or rel in entry_paths
        )
    ]

    findings: list[tuple[str, str]] = []
    for output in outputs:
        for source in _generating_sources(output, shipped, output_set, input_roots):
            findings.append(
                (
                    "RC-BUILD-OUTPUT-SHIPS-SOURCE",
                    f"RC-BUILD-OUTPUT-SHIPS-SOURCE: '{output}' is build output and its generating source "
                    f"'{source}' ships beside it, so this plugin distributes both halves of its build "
                    f"graph. The existing compiled-source rule keys on file EXTENSION and is blind to "
                    f"this pair, because a transpiled bundle's source is not a compiled-language file. "
                    f"Ship the output and reference the source out-of-tree, or ship the source and let "
                    f"the consumer build — shipping both means nobody can tell which one runs.",
                )
            )
    return {
        "outputs": outputs,
        "inputs": inputs,
        "input_roots": sorted(input_roots),
        "output_prefixes": sorted(declared_outputs),
        "findings": findings,
    }


def verify_build_roles(plugin_root: Path) -> list[tuple[str, str]]:
    """`classify_build_role`'s findings alone, for callers that only wire findings."""
    result = classify_build_role(plugin_root)
    findings: list[tuple[str, str]] = result["findings"]
    return findings


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    network = "--network" in args
    paths = [a for a in args if not a.startswith("-")]
    root = Path(paths[0] if paths else ".").resolve()

    findings = [
        *verify_extract_records(root, network=network),
        *verify_binary_licences(root),
        *verify_build_roles(root),
    ]
    for _rule, message in findings:
        print(message)
    if not findings:
        print(f"ship-canon: OK — {root.name} has no extract-record, licence, or build-role finding")
    # UNVERIFIED is informational: it reports what was NOT checked, so it must not
    # be the reason a caller sees a failure exit.
    blocking = [f for f in findings if f[0] != "RC-EXTRACT-UNVERIFIED"]
    return 1 if blocking else 0


if __name__ == "__main__":  # pragma: no cover - CLI entry
    raise SystemExit(main())
