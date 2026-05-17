#!/usr/bin/env python3
"""Regression tests for the v2.90.0 menu unification (TRDD-c50531c2).

**Historical context.** v2.89.3 invoked ``scripts/format_menu.py`` via Bash
on every menu turn. The fix in v2.89.4 (TRDD-b8dd7f6b + TRDD-3ce2f864)
moved menu rendering into a ``cpv-format-menu`` fork-skill and pre-rendered
the first-contact menus directly in each orchestrator command body.

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


def test_v290_only_cpv_main_menu_command_remains() -> None:
    """commands/ MUST contain exactly one file: `cpv-main-menu.md`.

    Per TRDD-c50531c2 (v2.90.0 menu unification) every other slash
    command was either deleted or converted into a `user-invocable: false`
    skill. The single surviving slash command is `/cpv-main-menu`, which
    delegates to `cpv-main-menu-agent`.
    """
    md_files = sorted(p.name for p in COMMANDS_DIR.glob("*.md"))
    assert md_files == ["cpv-main-menu.md"], (
        f"Expected commands/ to contain exactly cpv-main-menu.md "
        f"(v2.90.0 per TRDD-c50531c2), found: {md_files}"
    )
