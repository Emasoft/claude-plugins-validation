#!/usr/bin/env python3
"""CI-parity static checks (CIP-1..CIP-6) — the #137-143 defect detectors.

The root cause of the #137-143 family is that the fixer/upgrade agents declare
DONE on ``validate_plugin --strict``, which does NOT mirror the gates the
adopting plugin's GitHub-CI ``ci.yml`` Lint job runs (jscpd / actionlint /
mypy / ``uv sync --extra dev``). A canonical upgrade that is *locally clean*
therefore still red-CIs.

This module is the static half of the local CI-parity preflight: it greps the
generated workflows + ``pyproject.toml`` for the SIX concrete defect SHAPES
the #137-143 incidents shipped, each as an FN-safe **two-sided** check —
it FIRES on the defect shape and PASSES on a clean / canon tree, and never
false-blocks a plugin that simply does not use the workflow in question.

The six checks (all grounded in shipped incidents):

* **CIP-1** — an *inverted* ``CLAUDE_PRIVATE_USERNAMES: ${{ github.repository_owner }}``
  in any ``.github/workflows/*.yml`` (#140). That env names the PRIVATE
  usernames to redact; setting it to the PUBLIC repo owner makes CPV flag every
  owner GitHub URL as a leak, failing the downstream CI Validate job.
* **CIP-2** — a conditional import-fallback shim
  (``try: import … except ImportError: def …``) carrying
  ``# type: ignore[no-redef]`` but MISSING ``misc`` (#142 Defect-1). Under
  ``mypy --strict`` the fallback ``def`` needs ``[no-redef, misc]`` (the
  conditional-variant non-identical-signature rule), so a bare ``[no-redef]``
  ships 12 MINORs that block the adopter's ``--strict`` gate.
* **CIP-3** — ``ci.yml`` / ``release.yml`` run ``uv sync --extra dev`` but
  ``pyproject.toml`` lacks ``[project.optional-dependencies].dev`` (#142
  Defect-2) → CI fails ``Extra `dev` is not defined``.
* **CIP-4** — a CPV-shipped superseded ``validate.yml`` present ALONGSIDE the
  consolidated ``ci.yml`` (#142 Defect-4). ``ci.yml``'s Validate job supersedes
  it, and the orphaned ``validate.yml``'s pre-existing shellcheck SC2086 then
  fails ``ci.yml``'s actionlint Lint job.
* **CIP-5** — ``ci.yml`` enables Mega-Linter ``COPYPASTE_JSCPD`` but no
  ``.jscpd.json`` config exists (#143). jscpd auto-reads ``.jscpd.json``; with
  none, the copy-paste gate uses jscpd's defaults and the local
  ``publish.py`` Gate-2b has no shared config to enforce parity with.
* **CIP-6** — a ``.github/workflows/*.yml`` pins ``claude-plugins-validation``
  at a git ref (``git+https://github.com/Emasoft/claude-plugins-validation@<ref>``
  / ``uvx --from git+…@<ref>`` / ``…claude-plugins-validation.git@<ref>``) where
  ``<ref>`` is NOT a resolvable CPV ref (TRDD-HZSI0BZ6; the dominant field
  failure). CPV's default branch is ``master``, so ``@main`` does not exist and
  ``uvx --from git+…@main`` fails ``Git operation failed / Updating … (main)``.
  A plugin migrated by an OLD CPV (≤v2.137, pre-#139) was pinned ``@main`` and
  never re-published, so nothing re-pins it and the workflow red-CIs forever.
  Only ``master``, a ``v<semver>`` tag, or a 7-40 hex commit SHA passes;
  ``@main`` / ``@develop`` / ``@HEAD`` / ``@feature-x`` FIRE.

Pure stdlib (``tomllib`` for ``pyproject.toml`` — graceful fallback on
pre-3.11 / unparseable). Every regex is re2-safe (no lookbehind / lookahead /
backreference) so the module is import-safe wherever CPV runs.

Public interface (the keystone the preflight + the migration matrix call):

    check_ci_parity(plugin_path: Path) -> list[ParityFinding]
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import NamedTuple

__all__ = ["ParityFinding", "check_ci_parity"]


class ParityFinding(NamedTuple):
    """One CI-parity defect.

    Attributes:
        check_id: The CIP rule id (``"CIP-1"`` … ``"CIP-6"``).
        severity: ``"MAJOR"`` / ``"MINOR"`` / ``"WARNING"`` — the same
            severity vocabulary the validators use. A defect that fails the
            adopter's CI hard is MAJOR; a style/parity gap is MINOR.
        message: A human-readable description naming the file and the fix.
        file: The plugin-relative path the finding is about (``""`` when no
            single file applies).
    """

    check_id: str
    severity: str
    message: str
    file: str


# ─────────────────────────────────────────────────────────────────────────
# Shared helpers — workflow discovery + lenient text read (no pyyaml dep).
# ─────────────────────────────────────────────────────────────────────────


def _workflow_files(plugin_path: Path) -> list[Path]:
    """Return ``.github/workflows/*.yml`` + ``*.yaml`` in a stable order.

    Returns ``[]`` when the directory is absent — a plugin with no workflows
    has nothing to check (the degrade-not-false-block contract).
    """
    wf_dir = plugin_path / ".github" / "workflows"
    if not wf_dir.is_dir():
        return []
    files = [p for p in wf_dir.iterdir() if p.is_file() and p.suffix in (".yml", ".yaml")]
    return sorted(files, key=lambda p: p.name)


def _read_text(path: Path) -> str | None:
    """Read a file leniently; return None when it cannot be read.

    Never raises — an unreadable / binary workflow is treated as "no signal"
    (return None) so a single bad file cannot abort the whole preflight.
    """
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


# ─────────────────────────────────────────────────────────────────────────
# CIP-1 — inverted CLAUDE_PRIVATE_USERNAMES (#140)
# ─────────────────────────────────────────────────────────────────────────

# Matches `CLAUDE_PRIVATE_USERNAMES: ${{ github.repository_owner }}` with
# arbitrary whitespace inside the `${{ }}` and around the colon. re2-safe
# (no lookaround). The KEY signal is the repo-owner expression on the RHS —
# the LOCAL `CLAUDE_PRIVATE_USERNAMES="$(whoami)"` scan idiom (a shell
# assignment, no `: ${{ … }}`) never matches.
_INVERTED_PRIVATE_USERNAMES_RE = re.compile(
    r"CLAUDE_PRIVATE_USERNAMES\s*:\s*\$\{\{\s*github\.repository_owner\s*\}\}"
)


def _check_inverted_private_usernames(plugin_path: Path) -> list[ParityFinding]:
    findings: list[ParityFinding] = []
    for wf in _workflow_files(plugin_path):
        text = _read_text(wf)
        if text is None:
            continue
        rel = str(wf.relative_to(plugin_path))
        for m in _INVERTED_PRIVATE_USERNAMES_RE.finditer(text):
            line = text.count("\n", 0, m.start()) + 1
            findings.append(
                ParityFinding(
                    "CIP-1",
                    "MAJOR",
                    f"{rel}:{line}: inverted CLAUDE_PRIVATE_USERNAMES — it is set to the "
                    f"PUBLIC repo owner (`${{{{ github.repository_owner }}}}`), but that env "
                    f"lists PRIVATE usernames to redact. CPV will flag every owner GitHub "
                    f"URL/email as a leak and the downstream CI Validate job fails under "
                    f"--strict. Drop the line (keep PLUGIN_SKIP_GITHUB_INTEGRITY=1); a CI "
                    f"runner has no developer local-username to protect.",
                    rel,
                )
            )
    return findings


# ─────────────────────────────────────────────────────────────────────────
# CIP-2 — import-fallback shim missing `misc` (#142 Defect-1)
# ─────────────────────────────────────────────────────────────────────────

# A `# type: ignore[...]` comment whose code-list CONTAINS `no-redef` but NOT
# `misc`. We scan the bracketed list verbatim (split on commas) rather than a
# regex alternation so e.g. `[no-redefine]` or `[misc-thing]` cannot
# accidentally match. re2-safe.
_TYPE_IGNORE_RE = re.compile(r"#\s*type:\s*ignore\[([^\]]*)\]")
# The conditional-fallback shim lives under an `except ImportError` (or a bare
# `except`) guard. We only flag a `[no-redef]`-without-`misc` comment that sits
# inside such a fallback block — a `[no-redef]` on an unconditional redefinition
# is a different (and usually intentional) shape and is left alone.
_EXCEPT_IMPORT_RE = re.compile(r"^\s*except\b.*ImportError")
_DEF_OR_CLASS_RE = re.compile(r"^\s*(?:def|class|async\s+def)\s")


def _ignore_codes(comment_inner: str) -> set[str]:
    """Split a ``type: ignore[...]`` bracket body into its error codes."""
    return {tok.strip() for tok in comment_inner.split(",") if tok.strip()}


def _check_import_fallback_misc(plugin_path: Path) -> list[ParityFinding]:
    findings: list[ParityFinding] = []
    scripts_dir = plugin_path / "scripts"
    if not scripts_dir.is_dir():
        return findings
    for py in sorted(scripts_dir.rglob("*.py")):
        text = _read_text(py)
        if text is None:
            continue
        lines = text.splitlines()
        # Track whether we are currently inside an `except ImportError:` block
        # by remembering the indent of the most recent such `except`. A `def`
        # / `class` at a DEEPER indent than that except is a fallback shim.
        except_indent: int | None = None
        rel = str(py.relative_to(plugin_path))
        for idx, raw in enumerate(lines):
            stripped = raw.lstrip()
            if not stripped:
                continue  # blank line keeps the current block context
            indent = len(raw) - len(stripped)
            if _EXCEPT_IMPORT_RE.match(raw):
                except_indent = indent
                continue
            # A line at or below the except's indent (and not blank) closes the
            # fallback block — unless it is the fallback body's def/class.
            if except_indent is not None and indent <= except_indent:
                except_indent = None
            in_fallback = except_indent is not None and indent > except_indent
            if not in_fallback:
                continue
            # Only a def/class line carries the shim's signature-redefinition
            # ignore comment.
            if not _DEF_OR_CLASS_RE.match(raw):
                continue
            m = _TYPE_IGNORE_RE.search(raw)
            if not m:
                continue
            codes = _ignore_codes(m.group(1))
            if "no-redef" in codes and "misc" not in codes:
                findings.append(
                    ParityFinding(
                        "CIP-2",
                        "MINOR",
                        f"{rel}:{idx + 1}: import-fallback shim carries "
                        f"`# type: ignore[no-redef]` but is missing `misc`. Under "
                        f"`mypy --strict` a conditional-fallback def needs "
                        f"`[no-redef, misc]` (the non-identical-signature rule) — a bare "
                        f"`[no-redef]` ships MINORs that block the adopter's --strict gate. "
                        f"Add `misc`: `# type: ignore[no-redef, misc]`.",
                        rel,
                    )
                )
    return findings


# ─────────────────────────────────────────────────────────────────────────
# CIP-3 — `uv sync --extra dev` without a declared dev extra (#142 Defect-2)
# ─────────────────────────────────────────────────────────────────────────

# `uv sync --extra dev` with arbitrary intervening whitespace. We require the
# literal `--extra dev` token so a bare `uv sync` (CPV's own ci.yml) never
# triggers this check. re2-safe.
_UV_SYNC_EXTRA_DEV_RE = re.compile(r"uv\s+sync\b[^\n]*--extra\s+dev\b")


def _pyproject_declares_dev_extra(plugin_path: Path) -> bool | None:
    """Return whether ``[project.optional-dependencies].dev`` is declared.

    * True  — the table has a non-empty (or empty) ``dev`` key list.
    * False — pyproject exists and parses but ``dev`` is absent.
    * None  — undeterminable (no pyproject / no tomllib / unparseable);
      the caller treats None as "no signal" and does NOT fire CIP-3.
    """
    pyproject = plugin_path / "pyproject.toml"
    if not pyproject.is_file():
        return None
    try:
        import tomllib  # type: ignore[import-not-found]
    except ImportError:
        # Python < 3.11 — refuse to guess. Plugins on those interpreters were
        # never going to run the canonical 3.12+ workflows anyway.
        return None
    text = _read_text(pyproject)
    if text is None:
        return None
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    project = data.get("project")
    opt = project.get("optional-dependencies") if isinstance(project, dict) else None
    if not isinstance(opt, dict):
        return False
    return "dev" in opt


def _check_dev_extra_declared(plugin_path: Path) -> list[ParityFinding]:
    declares = _pyproject_declares_dev_extra(plugin_path)
    if declares is None or declares:
        # No determinable pyproject, or the dev extra IS declared → no defect.
        return []
    # The dev extra is absent. Only a defect if a workflow actually runs
    # `uv sync --extra dev` (otherwise the missing extra harms nothing).
    offending: list[str] = []
    for wf in _workflow_files(plugin_path):
        text = _read_text(wf)
        if text is None:
            continue
        if _UV_SYNC_EXTRA_DEV_RE.search(text):
            offending.append(str(wf.relative_to(plugin_path)))
    if not offending:
        return []
    return [
        ParityFinding(
            "CIP-3",
            "MAJOR",
            f"{', '.join(offending)} run `uv sync --extra dev`, but pyproject.toml has no "
            f"`[project.optional-dependencies].dev` table — CI fails with "
            f"\"Extra `dev` is not defined\". Add a `dev = [\"pytest\", \"ruff\", \"mypy\"]` "
            f"extra (or run `standardize --fix`, which auto-provisions it).",
            "pyproject.toml",
        )
    ]


# ─────────────────────────────────────────────────────────────────────────
# CIP-4 — superseded validate.yml alongside ci.yml (#142 Defect-4)
# ─────────────────────────────────────────────────────────────────────────

# A CPV-shipped validate.yml is recognised conservatively — it must carry BOTH
# a CPV plugin-validate COMMAND marker AND a CPV-validate workflow name. An
# unrelated workflow named validate.yml that lacks either marker is NEVER
# matched, so this can never flag a user's own validation workflow.
_CPV_VALIDATE_CMD_MARKERS = (
    "cpv-remote-validate plugin",
    "validate_plugin.py",
    "remote_validation.py plugin",
)
_CPV_VALIDATE_NAME_RE = re.compile(r"(?m)^name\s*:\s*(?P<name>.+?)\s*$")
_CPV_VALIDATE_NAME_MARKERS = ("validate", "validation")


def _is_cpv_shipped_validate_yml(path: Path) -> bool:
    """Return True only when ``path`` is recognisably a CPV-shipped validate.yml.

    Mirrors ``standardize_plugin._is_cpv_shipped_validate_yml`` — conservative
    by construction (requires both a CPV-validate command marker and a
    CPV-validate workflow name).
    """
    text = _read_text(path)
    if text is None:
        return False
    low = text.lower()
    if not any(marker in low for marker in _CPV_VALIDATE_CMD_MARKERS):
        return False
    for m in _CPV_VALIDATE_NAME_RE.finditer(text):
        wf_name = m.group("name").strip().lower()
        if any(marker in wf_name for marker in _CPV_VALIDATE_NAME_MARKERS):
            return True
    return False


def _check_superseded_validate_yml(plugin_path: Path) -> list[ParityFinding]:
    wf_dir = plugin_path / ".github" / "workflows"
    if not wf_dir.is_dir():
        return []
    ci = wf_dir / "ci.yml"
    validate = wf_dir / "validate.yml"
    # Only a defect when the consolidated ci.yml is PRESENT (its Validate job
    # is what supersedes the standalone validate.yml). A validate.yml on its
    # own (no ci.yml) is the plugin's only validation gate — never flag it.
    if not ci.is_file() or not validate.is_file():
        return []
    if not _is_cpv_shipped_validate_yml(validate):
        return []
    rel = str(validate.relative_to(plugin_path))
    return [
        ParityFinding(
            "CIP-4",
            "MAJOR",
            f"{rel}: a CPV-shipped validate.yml is present alongside ci.yml — ci.yml's "
            f"Validate job supersedes it, and the orphaned validate.yml's shellcheck "
            f"SC2086 then fails ci.yml's actionlint Lint job. Remove validate.yml (or run "
            f"`standardize --fix`, which safe-deletes it and re-points branch protection).",
            rel,
        )
    ]


# ─────────────────────────────────────────────────────────────────────────
# CIP-5 — COPYPASTE_JSCPD enabled but no .jscpd.json (#143)
# ─────────────────────────────────────────────────────────────────────────

# `COPYPASTE_JSCPD` appears in the Mega-Linter `ENABLE_LINTERS` list (or as a
# `COPYPASTE_JSCPD_ARGUMENTS:` key). We look for the bare token in any
# workflow file. A plugin whose ci.yml never enables it (e.g. CPV's own ci.yml,
# which runs no Mega-Linter) never triggers CIP-5 even with no .jscpd.json.
_COPYPASTE_JSCPD_RE = re.compile(r"\bCOPYPASTE_JSCPD\b")


def _check_jscpd_config(plugin_path: Path) -> list[ParityFinding]:
    # The config can live at the repo root (.jscpd.json) or be embedded in a
    # Mega-Linter config. We only assert the canonical standalone .jscpd.json
    # the local publish.py Gate-2b + CI both auto-read.
    if (plugin_path / ".jscpd.json").is_file():
        return []
    enabling: list[str] = []
    for wf in _workflow_files(plugin_path):
        text = _read_text(wf)
        if text is None:
            continue
        if _COPYPASTE_JSCPD_RE.search(text):
            enabling.append(str(wf.relative_to(plugin_path)))
    if not enabling:
        return []
    return [
        ParityFinding(
            "CIP-5",
            "MINOR",
            f"{', '.join(enabling)} enable Mega-Linter COPYPASTE_JSCPD but no `.jscpd.json` "
            f"config exists. jscpd auto-reads `.jscpd.json`; without it the local "
            f"publish.py Gate-2b and CI's copy-paste gate share no threshold config (the "
            f"#143 parity gap). Add a `.jscpd.json` (threshold 5 + ignore globs mirroring "
            f".mega-linter.yml), or run `standardize --fix` to provision it.",
            ".jscpd.json",
        )
    ]


# ─────────────────────────────────────────────────────────────────────────
# CIP-6 — stale / invalid CPV ref pinned in a workflow (#TRDD-HZSI0BZ6)
# ─────────────────────────────────────────────────────────────────────────

# Capture the ref a workflow pins ``claude-plugins-validation`` at. The pin
# always reads ``…claude-plugins-validation[.git]@<ref>`` — whether spelled as
# ``git+https://github.com/Emasoft/claude-plugins-validation@<ref>``, an
# ``uvx --from git+…@<ref>``, or a bare ``…claude-plugins-validation.git@<ref>``.
# We anchor on the project name + an OPTIONAL ``.git`` + ``@`` and capture the
# ref as a run of ref-legal characters (alnum / ``.`` / ``_`` / ``-`` / ``/``)
# — it terminates at whitespace, a quote, ``#``, or any other delimiter. The
# pinned-version form ``claude-plugins-validation==<ver>`` (the #137 PyPI-wheel
# selector) has ``==`` not ``@`` and is deliberately NOT matched (it carries no
# git ref to validate). re2-safe: no lookbehind / lookahead / backreference.
_CPV_REF_PIN_RE = re.compile(r"claude-plugins-validation(?:\.git)?@([A-Za-z0-9._\-/]+)")

# A ``v<semver>`` release tag: ``v<major>.<minor>.<patch>`` with an OPTIONAL
# prerelease / build suffix (``-rc.1``, ``-beta``, ``+meta``). Anchored full-match
# (``fullmatch`` at the call site) so a trailing-garbage ref does not slip through.
_SEMVER_TAG_RE = re.compile(r"v\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.\-]+)?")
# A git commit SHA: 7-40 lowercase-or-uppercase hex chars (short or full form).
_COMMIT_SHA_RE = re.compile(r"[0-9a-fA-F]{7,40}")


def _is_resolvable_cpv_ref(ref: str) -> bool:
    """Return True when ``ref`` is a CPV ref that ``uvx --from git+…@<ref>`` resolves.

    Resolvable ⇔ ``master`` (CPV's default branch), a ``v<semver>`` release tag,
    or a 7-40 char hex commit SHA. Everything else (``main`` / ``develop`` /
    ``HEAD`` / a feature-branch name) is a STALE / invalid pin that red-CIs.
    """
    if ref == "master":
        return True
    if _SEMVER_TAG_RE.fullmatch(ref):
        return True
    return bool(_COMMIT_SHA_RE.fullmatch(ref))


def _check_stale_cpv_ref(plugin_path: Path) -> list[ParityFinding]:
    findings: list[ParityFinding] = []
    for wf in _workflow_files(plugin_path):
        text = _read_text(wf)
        if text is None:
            continue
        rel = str(wf.relative_to(plugin_path))
        for m in _CPV_REF_PIN_RE.finditer(text):
            ref = m.group(1)
            if _is_resolvable_cpv_ref(ref):
                continue
            line = text.count("\n", 0, m.start()) + 1
            findings.append(
                ParityFinding(
                    "CIP-6",
                    "MAJOR",
                    f"{rel}:{line}: pins claude-plugins-validation at `@{ref}`, which is not "
                    f"a resolvable CPV ref — CPV's default branch is `master`, so "
                    f"`uvx --from git+…@{ref}` fails (`Git operation failed / Updating … "
                    f"({ref})`) and the workflow red-CIs. Re-pin to a `v<semver>` tag (the "
                    f"current CPV release) or `master` — run `standardize --fix` to re-pin "
                    f"the CPV ref, or have the upgrade agent rewrite it on its next run.",
                    rel,
                )
            )
    return findings


# ─────────────────────────────────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────────────────────────────────


def check_ci_parity(plugin_path: Path) -> list[ParityFinding]:
    """Run all six CI-parity static checks against a plugin tree.

    Each check is FN-safe two-sided — it FIRES on the #137-143 defect shape and
    PASSES on a clean / canon tree, and a plugin that does not use a given
    workflow never draws a false finding from that check.

    Returns the findings in CIP-id order (1..6). An empty list means the tree
    is parity-clean for all six dimensions.
    """
    plugin_path = Path(plugin_path)
    findings: list[ParityFinding] = []
    findings.extend(_check_inverted_private_usernames(plugin_path))
    findings.extend(_check_import_fallback_misc(plugin_path))
    findings.extend(_check_dev_extra_declared(plugin_path))
    findings.extend(_check_superseded_validate_yml(plugin_path))
    findings.extend(_check_jscpd_config(plugin_path))
    findings.extend(_check_stale_cpv_ref(plugin_path))
    return findings
