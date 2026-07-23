#!/usr/bin/env python3
"""Two-sided test matrix for the dependency agent-context-writer detector
(issue #174, ``scripts/cpv_dependency_agent_writers.py`` + RC-165 in
``validate_security.py``).

The detector scores THREE independent states of a dependency-borne agent-context
writer separately, so a common opt-in capability is never reported with the
language of a live install-time attack:

* state 1 — a known-writer dependency is DECLARED → WARNING (capability present)
* state 2 — an install-time trigger runs the writer → CRITICAL
    * 2a — the plugin's OWN package.json lifecycle script invokes the writer
    * 2b — the declared writer dependency itself auto-runs at install
* state 3 — the writer's distinctive artifacts are PRESENT in the tree → MAJOR

Every positive case has a MINIMAL-MUTATION negative sibling (a non-writer dep, a
non-lifecycle script, a scripts:{} dependency, the legit-for-a-plugin root
``.mcp.json``) so a test proves the DETECTOR'S GATE, not an incidental
difference.

Two surfaces are exercised:

* the module-level ``scan_dependency_agent_writers`` predicate (unit), and
* the ``validate_security.check_dependency_agent_writers`` emit onto a real
  ``ValidationReport`` (integration — proves each state lands in its intrinsic
  CRITICAL / MAJOR / WARNING bucket).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

os.environ["CPV_SCAN_CACHE"] = "0"

REPO = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import cpv_dependency_agent_writers as dw  # noqa: E402
import validate_security as vsec  # noqa: E402
from cpv_validation_common import ValidationReport  # noqa: E402

# ────────────────────────────────────────────────────────────────────────
# Fixture helpers
# ────────────────────────────────────────────────────────────────────────


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _package_json(
    root: Path,
    *,
    deps: dict | None = None,
    dev: dict | None = None,
    scripts: dict | None = None,
) -> None:
    obj: dict = {"name": "fixture-plugin", "version": "1.0.0"}
    if deps is not None:
        obj["dependencies"] = deps
    if dev is not None:
        obj["devDependencies"] = dev
    if scripts is not None:
        obj["scripts"] = scripts
    _write(root / "package.json", json.dumps(obj))


def _severities(findings) -> list[str]:
    return [f.severity for f in findings]


def _levels(report, level: str) -> list:
    """RC-165 result messages at a given uppercase level in a ValidationReport."""
    return [r for r in report.results if r.level == level and "RC-165" in r.message]


# ────────────────────────────────────────────────────────────────────────
# State 1 — capability present (WARNING) + negatives
# ────────────────────────────────────────────────────────────────────────


def test_state1_declared_playwright_is_warning(tmp_path: Path) -> None:
    """A plugin that declares the npm `playwright` dependency raises exactly one
    WARNING (capability present), no critical, no major."""
    _package_json(tmp_path, dev={"playwright": "^1.44.0"})
    findings = dw.scan_dependency_agent_writers(tmp_path)
    assert _severities(findings) == ["warning"]
    assert "capability present" in findings[0].message
    assert "not necessarily triggered" in findings[0].message


def test_scoped_playwright_test_is_warning(tmp_path: Path) -> None:
    """The scoped `@playwright/test` package name also carries the capability."""
    _package_json(tmp_path, dev={"@playwright/test": "^1.44.0"})
    findings = dw.scan_dependency_agent_writers(tmp_path)
    assert _severities(findings) == ["warning"]


def test_non_writer_dependency_no_finding(tmp_path: Path) -> None:
    """MINIMAL MUTATION of the state-1 positive: swap the writer dep for common
    non-writer deps → zero findings (the gate is the named list, not 'has deps')."""
    _package_json(tmp_path, deps={"express": "^4.0.0", "lodash": "^4.17.0"})
    assert dw.scan_dependency_agent_writers(tmp_path) == []


def test_pypi_playwright_is_not_the_npm_writer(tmp_path: Path) -> None:
    """The `init-agents` CLI is the npm playwright's. A pypi `playwright` (the
    Python browser lib, no init-agents) must NOT match — ecosystem-scoped."""
    _write(tmp_path / "requirements.txt", "playwright==1.44.0\n")
    assert dw.scan_dependency_agent_writers(tmp_path) == []


def test_no_manifest_at_all_no_finding(tmp_path: Path) -> None:
    """An empty tree (a fresh, uninstalled, marketplace-less source) yields no
    finding and does not raise."""
    assert dw.scan_dependency_agent_writers(tmp_path) == []


# ────────────────────────────────────────────────────────────────────────
# State 2a — the plugin's OWN install script invokes the writer (CRITICAL)
# ────────────────────────────────────────────────────────────────────────


def test_state2a_postinstall_invokes_writer_is_critical(tmp_path: Path) -> None:
    """The plugin's OWN package.json postinstall runs `playwright init-agents` →
    CRITICAL (it auto-writes agent-context files at the plugin's install)."""
    _package_json(
        tmp_path,
        dev={"playwright": "^1.44.0"},
        scripts={"postinstall": "npx playwright init-agents --loop claude"},
    )
    findings = dw.scan_dependency_agent_writers(tmp_path)
    assert "critical" in _severities(findings)
    crit = [f for f in findings if f.severity == "critical"]
    assert len(crit) == 1
    assert "at install" in crit[0].message


def test_state2a_non_lifecycle_script_is_not_critical(tmp_path: Path) -> None:
    """MINIMAL MUTATION: the SAME command under a NON-lifecycle key (`test`) does
    NOT auto-run at install → no state-2a critical (only the state-1 warning from
    the declared dep)."""
    _package_json(
        tmp_path,
        dev={"playwright": "^1.44.0"},
        scripts={"test": "playwright init-agents --loop claude"},
    )
    findings = dw.scan_dependency_agent_writers(tmp_path)
    assert "critical" not in _severities(findings)
    assert _severities(findings) == ["warning"]


def test_state2a_lifecycle_script_without_writer_is_clean(tmp_path: Path) -> None:
    """A benign lifecycle script (no writer command) and no writer dep → zero
    findings (the gate is the writer command, not 'has a postinstall')."""
    _package_json(tmp_path, deps={"express": "^4.0.0"}, scripts={"postinstall": "node build.js"})
    assert dw.scan_dependency_agent_writers(tmp_path) == []


# ────────────────────────────────────────────────────────────────────────
# State 2b — the installed DEPENDENCY itself auto-runs at install (CRITICAL)
# ────────────────────────────────────────────────────────────────────────


def test_state2b_installed_dep_with_install_hook_is_critical(tmp_path: Path) -> None:
    """When node_modules is present and the writer dep's OWN package.json has a
    postinstall hook (a NEWER version than the named list knew about), escalate
    to CRITICAL."""
    _package_json(tmp_path, dev={"playwright": "^1.99.0"})
    _write(
        tmp_path / "node_modules" / "playwright" / "package.json",
        json.dumps({"name": "playwright", "scripts": {"postinstall": "node install.js"}}),
    )
    findings = dw.scan_dependency_agent_writers(tmp_path)
    assert "critical" in _severities(findings)


def test_state2b_installed_dep_without_install_hook_stays_warning(tmp_path: Path) -> None:
    """MINIMAL MUTATION: the real playwright ships `scripts: {}` — no install
    trigger — so an installed copy with an empty scripts block stays state-1
    WARNING, never critical (capability present, never triggered)."""
    _package_json(tmp_path, dev={"playwright": "^1.44.0"})
    _write(
        tmp_path / "node_modules" / "playwright" / "package.json",
        json.dumps({"name": "playwright", "scripts": {}}),
    )
    findings = dw.scan_dependency_agent_writers(tmp_path)
    assert _severities(findings) == ["warning"]


def test_state2b_absent_node_modules_is_uninstalled_safe(tmp_path: Path) -> None:
    """No node_modules at all (a fresh source) → the recorded install_trigger
    (False for playwright) governs → WARNING only, no crash."""
    _package_json(tmp_path, dev={"playwright": "^1.44.0"})
    findings = dw.scan_dependency_agent_writers(tmp_path)
    assert _severities(findings) == ["warning"]


# ────────────────────────────────────────────────────────────────────────
# State 3 — the writer's distinctive artifacts are present (MAJOR) + negatives
# ────────────────────────────────────────────────────────────────────────


def test_state3_claude_agents_artifact_is_major(tmp_path: Path) -> None:
    """A declared writer dep PLUS a `.claude/agents/*.md` artifact (a consumer
    footprint a plugin never legitimately ships) → MAJOR."""
    _package_json(tmp_path, dev={"playwright": "^1.44.0"})
    _write(tmp_path / ".claude" / "agents" / "planner.md", "# planner\n")
    findings = dw.scan_dependency_agent_writers(tmp_path)
    assert "major" in _severities(findings)
    major = [f for f in findings if f.severity == "major"]
    assert any(".claude/agents/planner.md" in f.evidence for f in major)


def test_state3_copilot_setup_workflow_artifact_is_major(tmp_path: Path) -> None:
    """The bare `init-agents` command falls through to the Copilot generator and
    writes `.github/workflows/copilot-setup-steps.yml`; its presence → MAJOR."""
    _package_json(tmp_path, dev={"playwright": "^1.44.0"})
    _write(tmp_path / ".github" / "workflows" / "copilot-setup-steps.yml", "on: push\n")
    findings = dw.scan_dependency_agent_writers(tmp_path)
    assert "major" in _severities(findings)


def test_state3_root_mcp_json_is_excluded(tmp_path: Path) -> None:
    """MINIMAL MUTATION: a plugin legitimately ships a root `.mcp.json` (bundled
    MCP server). It is a capability path but NOT a state-3 signal → no MAJOR,
    only the state-1 WARNING."""
    _package_json(tmp_path, dev={"playwright": "^1.44.0"})
    _write(tmp_path / ".mcp.json", '{"mcpServers": {}}')
    findings = dw.scan_dependency_agent_writers(tmp_path)
    assert "major" not in _severities(findings)
    assert _severities(findings) == ["warning"]


def test_state3_artifact_without_writer_dep_is_silent(tmp_path: Path) -> None:
    """MINIMAL MUTATION: the artifact is present but NO writer dep is declared →
    zero findings (state 3 is keyed on a declared writer, not bare files, so a
    plugin that just happens to carry a `.github/` file is not flagged)."""
    _package_json(tmp_path, deps={"express": "^4.0.0"})
    _write(tmp_path / ".claude" / "agents" / "planner.md", "# planner\n")
    assert dw.scan_dependency_agent_writers(tmp_path) == []


# ────────────────────────────────────────────────────────────────────────
# Self-scan + emit integration
# ────────────────────────────────────────────────────────────────────────


def test_cpv_self_scan_is_clean() -> None:
    """CPV declares no known-writer dependency, so scanning its own tree yields
    no dependency-writer finding (guards the dogfood self-validate)."""
    assert dw.scan_dependency_agent_writers(REPO) == []


def test_emit_routes_severities_onto_report(tmp_path: Path) -> None:
    """The validate_security emit routes each state to the right report bucket:
    a state-2a critical AND a state-3 major land as critical + major, and the
    state-1 warning lands as a warning — proving the effective_severity + getattr
    dispatch."""
    _package_json(
        tmp_path,
        dev={"playwright": "^1.44.0"},
        scripts={"postinstall": "playwright init-agents --loop claude"},
    )
    _write(tmp_path / ".claude" / "agents" / "planner.md", "# planner\n")
    report = ValidationReport()
    issues = vsec.check_dependency_agent_writers(tmp_path, report)
    assert issues >= 3
    # A genuine install-time trigger stays CRITICAL (real code, not a sample/doc).
    assert _levels(report, "CRITICAL"), "state-2a install trigger should be CRITICAL"
    # The present artifact is MAJOR.
    assert _levels(report, "MAJOR"), "state-3 present artifact should be MAJOR"
    # The capability is a non-blocking WARNING.
    assert _levels(report, "WARNING"), "state-1 capability should be WARNING"
