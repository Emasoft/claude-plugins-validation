#!/usr/bin/env python3
"""Dependency agent-context-writer detector (RC-165, issue #174).

CPV already flags a plugin's OWN components that write files an agent later
loads as instructions (RC-164 in-plugin write guard). This module closes the
sibling gap: the same write-capability can arrive from a plugin's **dependency**
rather than the plugin's own tree. A plugin that declares ``playwright`` inherits
a CLI (``playwright init-agents``) able to transcribe agent definitions into
``.claude/agents/*.md``, ``.mcp.json``, ``.github/agents/*.agent.md`` — and, with
no ``--loop`` flag, into ``.github/workflows/copilot-setup-steps.yml`` (control
falls through to the Copilot generator). So a dependency's CLI can introduce a
GitHub Actions workflow.

**THE THREE-STATE RULE (issue #174 — score capability vs live SEPARATELY; a
flat critical trains readers to discount the detector for the day a real
state-2 package shows up):**

| state | how it is established here                                   | severity     |
|-------|-------------------------------------------------------------|--------------|
| 1 CAN write   | the plugin DECLARES a known-writer dependency       | WARNING      |
| 2 DOES @install | an install-time trigger runs the writer           | CRITICAL     |
| 3 files PRESENT | the writer's distinctive output artifacts exist   | MAJOR        |

State 2 has two always-or-often-checkable forms, both CRITICAL:

* **2a** — the PLUGIN'S OWN ``package.json`` has a lifecycle script
  (``preinstall`` / ``install`` / ``postinstall`` / ``prepare`` /
  ``prepublishOnly``) that INVOKES a known-writer command. Always checkable
  (the plugin's own manifest is present in every source, installed or not).
* **2b** — the declared writer DEPENDENCY itself auto-runs at install. Recorded
  per named-list entry (uninstalled-safe); when ``node_modules/<pkg>`` is
  present its ``package.json`` lifecycle scripts are additionally consulted so a
  NEWER version that added an install hook still escalates.

**UNINSTALLED-SAFE (feedback: CPV scans uninstalled, marketplace-less plugins).**
The baseline (states 1, 2a, 3) reads only the plugin's own always-present files:
declared manifests (via ``cpv_sbom_writer.iter_dependencies`` — which already
skips ``node_modules``), the root ``package.json`` scripts, and the on-disk tree.
No detector here GATES on ``node_modules`` being present; the 2b escalation is a
bonus that gracefully no-ops when it is absent.

**TARGETED, not a node_modules scan.** State 1/2b key on a curated named list
of KNOWN agent-context writers (``playwright`` is entry one), matched against the
plugin's DECLARED dependency names — not a content scan of every vendored file.
The only ``node_modules`` read is a single named package's ``package.json`` for
the 2b escalation.

**LOW-FP state 3.** The artifact globs are the writer's *distinctive
consumer-repo footprint* — paths a well-formed Claude Code plugin never ships.
``.mcp.json`` at the plugin ROOT is DELIBERATELY EXCLUDED: a plugin legitimately
ships one to declare a bundled MCP server, so it is named in the state-1
capability text but never itself raises state 3.

**FN-safe.** The named list only ADDS detection; nothing here suppresses or
clears another rule. A known writer that is declared always raises at least
state 1. Each state is emitted at its INTRINSIC tier by the caller (NOT routed
through ``effective_severity``): the detector is already conservative — keyed on
a curated named list, on the writer's *distinctive* consumer-footprint paths,
and on a *real* install trigger — so the sample/test/doc demotion buys no FP
reduction, and it would wrongly demote a state-3 finding merely because an
agent-context artifact is a ``.md`` file.

Self-scan-clean: the named-list DATA lives in ALL-CAPS ``Final`` collections
(the pattern-source shape) so CPV's own self-scan reads it as rule data. All
regexes are re2-safe (no lookbehind / lookahead) — CI runs without google-re2.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Final, NamedTuple

# Declared-dependency enumeration is SHARED with the SBOM emitter (RC-106) so the
# declared-dep parsing cannot drift. ``iter_dependencies`` already skips
# ``node_modules`` / ``.venv`` / ``_dev`` folders and yields one ``Dependency``
# per declared entry across npm / pypi / cargo / golang manifests.
from cpv_sbom_writer import Dependency, iter_dependencies


class WriterSpec(NamedTuple):
    """One curated known agent-context-writer package family."""

    label: str  # human name, e.g. "Playwright init-agents"
    ecosystem: str  # "npm" | "pypi" | "cargo" | "golang"
    package_names: frozenset[str]  # lowercased declared-dep names carrying the capability
    command_pattern: re.Pattern[str]  # matches the writer invocation in a package.json script
    written_paths: tuple[str, ...]  # what it writes (for the capability message; may include legit-for-plugin paths)
    artifact_globs: tuple[str, ...]  # DISTINCTIVE consumer-footprint globs for state 3 (never a legit-for-plugin path)
    install_trigger: bool  # recorded: does INSTALLING the package itself write? (playwright = False)


class WriterFinding(NamedTuple):
    """One three-state dependency-writer finding."""

    severity: str  # "critical" | "major" | "warning" — the BASE tier before effective_severity
    message: str  # human-readable finding text
    evidence: str  # plugin-relative manifest / artifact path (for role-aware severity + reporting)
    line_no: int | None = None


# ────────────────────────────────────────────────────────────────────────
# The named list (§ "one list plus one guard", issue #174)
# ────────────────────────────────────────────────────────────────────────

# Playwright ships three agent definitions (planner / generator / healer) and a
# CLI ``playwright init-agents [--loop claude|copilot|opencode|vscode]`` that
# transcribes them into the consumer's coding-agent config. Verified in
# ``node_modules/playwright/lib/agents/generateAgents.js`` /
# ``node_modules/playwright/lib/program.js`` (issue #174): its ``package.json``
# has ``scripts: {}`` — NO install trigger — so on its own it is state 1
# (capability present, opt-in command). The bare command (no ``--loop``) falls
# through to the Copilot generator and writes ``.github/workflows/…``.
_PLAYWRIGHT_INIT_AGENTS_RE: Final[re.Pattern[str]] = re.compile(
    r"\bplaywright\b[^\n]*?\binit-agents\b"
)

# DISTINCTIVE consumer-repo footprint — paths a well-formed Claude Code PLUGIN
# never legitimately ships (a plugin uses ``agents/``, not ``.claude/agents/``).
# ``.mcp.json`` at the plugin ROOT is INTENTIONALLY ABSENT here: a plugin ships
# one to declare a bundled MCP server, so it is a capability path (below) but not
# a state-3 signal. ``.github/workflows/copilot-setup-steps.yml`` is the exact
# distinctive filename the bare command writes.
_AGENT_CONTEXT_ARTIFACT_GLOBS: Final[tuple[str, ...]] = (
    ".claude/agents/*.md",
    ".claude/prompts/*.md",
    ".github/agents/*.agent.md",
    ".github/chatmodes/*.chatmode.md",
    ".opencode/prompts/*.md",
    ".github/workflows/copilot-setup-steps.yml",
    ".vscode/mcp.json",
    "opencode.json",
)

# Full capability footprint (for the state-1 message) — INCLUDES the legit-for-
# plugin ``.mcp.json`` / ``opencode.json`` so the capability text is accurate.
_PLAYWRIGHT_WRITTEN_PATHS: Final[tuple[str, ...]] = (
    ".claude/agents/*.md",
    ".mcp.json",
    ".github/agents/*.agent.md",
    ".github/workflows/copilot-setup-steps.yml",
    ".opencode/prompts/*.md",
    "opencode.json",
    ".github/chatmodes/*.chatmode.md",
    ".vscode/mcp.json",
)

KNOWN_AGENT_CONTEXT_WRITERS: Final[tuple[WriterSpec, ...]] = (
    WriterSpec(
        label="Playwright init-agents",
        ecosystem="npm",
        package_names=frozenset({"playwright", "@playwright/test"}),
        command_pattern=_PLAYWRIGHT_INIT_AGENTS_RE,
        written_paths=_PLAYWRIGHT_WRITTEN_PATHS,
        artifact_globs=_AGENT_CONTEXT_ARTIFACT_GLOBS,
        install_trigger=False,
    ),
)

# npm package.json lifecycle keys that run automatically on install/publish.
_NPM_LIFECYCLE_SCRIPTS: Final[frozenset[str]] = frozenset(
    {"preinstall", "install", "postinstall", "prepare", "prepublishonly"}
)


# ────────────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────────────


def _spec_for_dep(dep: Dependency) -> WriterSpec | None:
    """Return the ``WriterSpec`` a declared dependency matches, or ``None``."""
    name = dep.name.strip().lower()
    for spec in KNOWN_AGENT_CONTEXT_WRITERS:
        if dep.ecosystem == spec.ecosystem and name in spec.package_names:
            return spec
    return None


def _read_json(path: Path) -> dict[str, object] | None:
    """Read a JSON object from ``path``; ``None`` on any read/parse failure
    (fail-safe — a missing / malformed manifest simply yields no finding, it
    never raises)."""
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _plugin_install_scripts_invoking_writer(
    plugin_root: Path,
) -> list[tuple[WriterSpec, str, str]]:
    """State 2a — the plugin's OWN root ``package.json`` runs a known-writer
    command in a lifecycle script. Returns ``(spec, script_name, manifest_rel)``
    per match. Always checkable (the root manifest is present in every source)."""
    hits: list[tuple[WriterSpec, str, str]] = []
    pkg = _read_json(plugin_root / "package.json")
    if pkg is None:
        return hits
    scripts = pkg.get("scripts")
    if not isinstance(scripts, dict):
        return hits
    for raw_name, raw_cmd in scripts.items():
        if not isinstance(raw_name, str) or not isinstance(raw_cmd, str):
            continue
        if raw_name.strip().lower() not in _NPM_LIFECYCLE_SCRIPTS:
            continue
        for spec in KNOWN_AGENT_CONTEXT_WRITERS:
            if spec.command_pattern.search(raw_cmd):
                hits.append((spec, raw_name, "package.json"))
    return hits


def _node_modules_pkg_json(plugin_root: Path, package_name: str) -> Path:
    """Map a (possibly scoped) npm package name to its installed manifest path.

    ``@playwright/test`` → ``node_modules/@playwright/test/package.json``.
    """
    return plugin_root.joinpath("node_modules", *package_name.split("/"), "package.json")


def _dep_has_install_trigger(plugin_root: Path, spec: WriterSpec) -> bool:
    """State 2b — does the DEPENDENCY itself auto-run at install?

    Recorded property first (uninstalled-safe). Then, only when the package is
    actually installed, its own ``package.json`` lifecycle scripts are consulted
    so a NEWER version that added an install hook the named list does not know
    about still escalates. Gracefully returns the recorded value when
    ``node_modules`` is absent.
    """
    if spec.install_trigger:
        return True
    for pkg_name in spec.package_names:
        pkg = _read_json(_node_modules_pkg_json(plugin_root, pkg_name))
        if pkg is None:
            continue
        scripts = pkg.get("scripts")
        if not isinstance(scripts, dict):
            continue
        for raw_name, raw_cmd in scripts.items():
            if (
                isinstance(raw_name, str)
                and isinstance(raw_cmd, str)
                and raw_cmd.strip()
                and raw_name.strip().lower() in _NPM_LIFECYCLE_SCRIPTS
            ):
                return True
    return False


def _present_writer_artifacts(plugin_root: Path, spec: WriterSpec) -> list[str]:
    """State 3 — the writer's DISTINCTIVE output artifacts present on disk.

    Returns a sorted list of plugin-relative artifact paths. ``Path.glob``
    handles the multi-segment globs (``.github/agents/*.agent.md``) and the
    exact-file globs (``.github/workflows/copilot-setup-steps.yml``) alike.
    """
    hits: set[str] = set()
    for pattern in spec.artifact_globs:
        for match in plugin_root.glob(pattern):
            if match.is_file():
                hits.add(str(match.relative_to(plugin_root)))
    return sorted(hits)


# ────────────────────────────────────────────────────────────────────────
# Public scan
# ────────────────────────────────────────────────────────────────────────


def scan_dependency_agent_writers(plugin_root: Path) -> list[WriterFinding]:
    """Three-state scan for dependency-borne agent-context writers (RC-165).

    Ordering is deterministic: state-2a plugin-install findings, then, per
    declared writer dependency (sorted), the state-1 capability finding, its
    state-2b install-trigger escalation, and (once per writer family) its
    state-3 present-artifact findings.
    """
    plugin_root = plugin_root.resolve()
    findings: list[WriterFinding] = []

    # State 2a — the plugin's OWN install scripts invoke a writer command.
    for spec, script_name, manifest_rel in _plugin_install_scripts_invoking_writer(plugin_root):
        findings.append(
            WriterFinding(
                "critical",
                f"plugin package.json '{script_name}' script invokes the {spec.label} "
                f"agent-context writer at install — auto-writes "
                f"{', '.join(spec.written_paths)} into whatever repo it runs in",
                manifest_rel,
            )
        )

    # Explicit None-check with a DISTINCT name (``matched``) so it does not
    # unify with the ``spec`` loop variable below — otherwise mypy joins the two
    # bindings in this scope and rejects assigning a ``WriterSpec | None`` here.
    writer_deps: list[tuple[Dependency, WriterSpec]] = []
    for dep in iter_dependencies(plugin_root):
        matched = _spec_for_dep(dep)
        if matched is not None:
            writer_deps.append((dep, matched))
    writer_deps.sort(key=lambda pair: (pair[0].name.lower(), pair[0].manifest))

    seen_artifact_specs: set[str] = set()
    for dep, spec in writer_deps:
        # State 1 — capability present (WARNING; a hugely common legit test dep
        # must NOT block a publish on mere presence).
        findings.append(
            WriterFinding(
                "warning",
                f"dependency '{dep.name}' ({dep.ecosystem}) bundles the {spec.label} "
                f"agent-context writer — able to write {', '.join(spec.written_paths)}; "
                f"capability present via an opt-in command, not necessarily triggered",
                dep.manifest,
            )
        )
        # State 2b — the dependency itself auto-runs its writer at install.
        if _dep_has_install_trigger(plugin_root, spec):
            findings.append(
                WriterFinding(
                    "critical",
                    f"dependency '{dep.name}' auto-runs the {spec.label} agent-context "
                    f"writer at install time (install-time lifecycle script present)",
                    dep.manifest,
                )
            )
        # State 3 — the writer's distinctive artifacts are present (once per
        # writer family, keyed on its label so two manifests of the same writer
        # do not double-report the same on-disk file).
        if spec.label not in seen_artifact_specs:
            seen_artifact_specs.add(spec.label)
            for artifact in _present_writer_artifacts(plugin_root, spec):
                findings.append(
                    WriterFinding(
                        "major",
                        f"agent-context artifact '{artifact}' matching the {spec.label} "
                        f"writer's output is present in the tree — verify it was reviewed "
                        f"and committed intentionally, not silently generated by the dependency",
                        artifact,
                    )
                )

    return findings
