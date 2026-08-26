#!/usr/bin/env python3
"""Regression tests for the v2.90.0 menu unification (TRDD-c50531c2).

**Historical context.** v2.89.3 invoked the now-removed CPV menu renderer
via Bash on every menu turn. The fix in v2.89.4 (TRDD-b8dd7f6b +
TRDD-3ce2f864) moved menu rendering into a fork-skill and pre-rendered
the first-contact menus directly in each orchestrator command body.
TRDD-4de479a0 Phase 4 (Wave 1 + this wave) replaces both with the
externalised `claude-menu-system` Stop-hook emitter, brokered through
`scripts/print_menu.py` — zero-token `systemMessage` render, no fork.

**v2.90.0 change.** Per TRDD-c50531c2 the four menu-orchestrator commands
(`cpv-doctor`, `cpv-fix-validation`, `cpv-fix-marketplace-validation`,
`cpv-cache-optimize`) were DELETED. Their workflows are now routed through
`/cpv-main-menu` (the single surviving slash command) → existing agents.
None of the v2.89.4 invariants apply anymore because the files they pinned
do not exist.

This file is therefore reduced to a single set of negative invariants:
the four orchestrators MUST stay deleted, and a regression that
re-introduces any of them would break the menu unification design.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
COMMANDS_DIR = REPO_ROOT / "commands"

# Four orchestrators that lived in v2.89.4 and were deleted in v2.90.0
# per TRDD-c50531c2. They MUST stay deleted — re-introducing one would
# re-fragment the single-entry-point design.
DELETED_ORCHESTRATORS = [
    "cpv-doctor.md",
    "cpv-fix-validation.md",
    "cpv-fix-marketplace-validation.md",
    "cpv-cache-optimize.md",
]


@pytest.mark.parametrize("cmd", DELETED_ORCHESTRATORS)
def test_v290_orchestrator_command_stays_deleted(cmd: str) -> None:
    """Each former menu-orchestrator command MUST stay deleted (v2.90.0).

    Per TRDD-c50531c2 (menu unification) the four orchestrator commands
    were deleted in favor of routing every workflow through
    `/cpv-main-menu`. Re-creating any of them would fragment the
    single-entry-point design and re-introduce the v2.89.3 menu-visibility
    failure modes.
    """
    path = COMMANDS_DIR / cmd
    assert not path.exists(), (
        f"{cmd} was re-introduced. Per TRDD-c50531c2 (v2.90.0) this file "
        f"MUST stay deleted — its workflow is routed through "
        f"/cpv-main-menu to the matching agent. Re-creating it fragments "
        f"the single-entry-point design."
    )


def test_v290_only_documented_slash_commands_remain() -> None:
    """commands/ MUST contain only the documented entry points.

    Per TRDD-c50531c2 (v2.90.0 menu unification) every routine slash
    command was either deleted or converted into a ``user-invocable: false``
    skill. ``/cpv-main-menu`` is the discovery surface.

    Per TRDD-71e68ab5 (v2.91.0) ``/cpv-batch-fix`` was added as a
    direct-entry power-user command — the doctor recommends it by exact
    name when a plugin has 100+ findings.

    Per TRDD-9dd64dbf (v2.95.0) ``/cpv-the-skills-menu-create`` was added
    as a direct-entry universal migrator — it operates on OTHER plugins
    so menu navigation inside CPV would be a UX dead end.

    Per TRDD-84525d4a (v2.99.1) ``/cpv-pre-install-scan`` was added
    as a direct-entry pre-install security gate — it runs BEFORE the
    user opens any menu, so menu navigation defeats the gate's timing.

    Any new direct-entry command requires its own TRDD.
    """
    allowed = {
        "cpv-main-menu.md",
        "cpv-batch-fix.md",
        "cpv-the-skills-menu-create.md",
        "cpv-pre-install-scan.md",
        # TRDD-3dcbb37c (v2.101.0) — Batch skills family. Direct-entry
        # commands because the user fans out fleet-wide operations
        # (marketplace URL / list / @listfile inputs) directly, not via
        # menu navigation per-plugin.
        "cpv-batch-validate.md",
        "cpv-batch-security-audit.md",
        "cpv-batch-caching-audit.md",
        "cpv-batch-caching-optimize.md",
        # Phase 3 same-turn variants.
        "cpv-batch-validate-and-fix.md",
        "cpv-batch-full-scan-and-fix.md",
        # Phase 4 scope-aware doctor batch (TRDD-a175f78d).
        "cpv-batch-scope-diagnose.md",
        "cpv-batch-scope-fix.md",
        "cpv-batch-scope-diagnose-and-fix.md",
        # Direct free-form entry to the general-purpose cpv-agent worker
        # (user directive 2026-07-23) — `/cpv-agent <request>` reaches the
        # worker directly, same target as the menu's `A — Ask the agent`.
        "cpv-agent.md",
    }
    md_files = {p.name for p in COMMANDS_DIR.glob("*.md")}
    unexpected = md_files - allowed
    missing = allowed - md_files
    assert not unexpected and not missing, (
        f"commands/ must contain exactly {sorted(allowed)} (v2.90.0 + v2.91.0 + v2.95.0 + v2.99.1 + v2.101.0). "
        f"Unexpected: {sorted(unexpected)}. Missing: {sorted(missing)}."
    )
