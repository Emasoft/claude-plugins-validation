"""Regression guards for the full-audit batch-31 doc fixes.

Batch 31 corrected stale/false claims in seven CPV skill docs. Three of
those corrections assert concrete facts about the behaviour of CPV's own
scripts; this file locks those facts in so the docs cannot silently drift
back to the wrong claim:

- Finding 111 (skills/cpv-scaffold-agent/SKILL.md): the Output section used to
  claim the agent template emits ``model`` / ``maxTurns`` / ``skills``
  frontmatter fields. ``add_component._agent_template`` emits none of them.
- Finding 112 (skills/cpv-scaffold-command/SKILL.md): the Error-Handling table
  used to claim a bare ``Bash`` in ``allowed-tools`` produces a MAJOR
  finding. ``validate_command.validate_allowed_tools_field`` treats a bare
  known tool as PASSED — only a genuinely MALFORMED pattern is MAJOR.
- Finding 105 (skills/cpv-link-plugin-marketplace/SKILL.md): the doc used to
  claim existing entries are NEVER overwritten / duplicates are skipped.
  ``manage_plugin.do_link_plugin`` REPLACES a same-name entry in place with
  fresh metadata.

All tests exercise the real scripts in-process — no mocks, no subprocess,
no network. CPV_SCAN_CACHE is irrelevant here (none of these paths touch the
skillaudit scan cache) but is pinned to 0 in the environment for parity with
the rest of the audit-fix probes.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("CPV_SCAN_CACHE", "0")

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import add_component as ac  # noqa: E402
import manage_plugin as mp  # noqa: E402
import validate_command as vc  # noqa: E402

# ── Finding 111 — cpv-scaffold-agent Output claim ────────────────────────────────


def test_agent_template_omits_model_maxturns_skills():
    """add_component._agent_template emits no model/maxTurns/skills fields.

    Guards the corrected cpv-scaffold-agent Output section: the template only
    scaffolds name + description (+ tools when given). If a future change
    started emitting these optional fields, the doc would need updating too.
    """
    out = ac._agent_template("my-agent", "agent summary", "Read, Bash")
    assert "name: my-agent" in out
    assert "description: agent summary" in out
    assert "tools: Read, Bash" in out
    # The three fields the old doc falsely advertised must NOT appear.
    assert "model:" not in out
    assert "maxTurns:" not in out
    assert "skills:" not in out


def test_agent_template_omits_tools_line_when_no_tools():
    """Without --tools the agent template emits only name + description."""
    out = ac._agent_template("ag", "desc", "")
    assert "name: ag" in out
    assert "description: desc" in out
    assert "tools:" not in out
    assert "model:" not in out
    assert "maxTurns:" not in out
    assert "skills:" not in out


# ── Finding 112 — cpv-scaffold-command bare-Bash claim (two-sided) ────────────────


def _allowed_tools_levels(value: object) -> list[str]:
    """Run only the allowed-tools field check and return the severity levels."""
    report = vc.CommandValidationReport()
    vc.validate_allowed_tools_field({"allowed-tools": value}, "commands/probe.md", report)
    return [r.level for r in report.results]


def test_bare_bash_in_allowed_tools_is_not_flagged():
    """Benign side: a bare 'Bash' allowed-tools value produces no finding.

    This is the exact value the cpv-scaffold-command default emits
    (``add_component._command_template`` uses ``allowed_tools or "Bash"``),
    so if bare Bash were a MAJOR the scaffold's own default would be invalid.
    """
    levels = _allowed_tools_levels("Bash")
    assert "MAJOR" not in levels
    assert "MINOR" not in levels
    assert "WARNING" not in levels
    assert "CRITICAL" not in levels
    assert "PASSED" in levels


def test_command_template_default_uses_bare_bash():
    """The scaffold default really is bare 'Bash' — anchors the two-sided test."""
    out = ac._command_template("do", "x", "")
    assert "allowed-tools: Bash\n" in out


def test_malformed_tool_pattern_is_major():
    """Malicious/broken side: a genuinely malformed pattern IS a MAJOR.

    Proves the fix did not blanket-suppress the check — only a bare known
    tool is clean; a syntactically broken pattern still blocks. This is the
    guard that distinguishes 'bare Bash is fine' from 'allowed-tools is
    never validated'.
    """
    levels = _allowed_tools_levels("Bash((")
    assert "MAJOR" in levels


# ── Finding 105 — link-plugin replace-in-place semantics ─────────────────────


def _make_marketplace(tmp_path: Path) -> Path:
    mkt: Path = tmp_path / "mkt"
    (mkt / ".claude-plugin").mkdir(parents=True)
    (mkt / ".claude-plugin" / "marketplace.json").write_text(
        json.dumps({"name": "hub", "owner": "Emasoft", "plugins": []}, indent=2),
        encoding="utf-8",
    )
    return mkt


def test_link_plugin_replaces_same_name_entry_with_fresh_metadata(tmp_path: Path):
    """do_link_plugin replaces a same-name entry in place (not skip, not dup).

    Guards the corrected cpv-link-plugin-marketplace doc: a second link of the
    same plugin name does NOT leave the stale entry untouched and does NOT
    append a duplicate — it removes the old entry and appends a fresh one.
    Two local plugin dirs with the SAME name but DIFFERENT version/description
    prove the metadata is refreshed, which the old 'duplicates are skipped'
    wording wrongly denied.
    """
    mkt = _make_marketplace(tmp_path)

    def _make_plugin(name: str, version: str, desc: str) -> Path:
        d: Path = tmp_path / f"plug-{version}"
        (d / ".claude-plugin").mkdir(parents=True)
        (d / ".claude-plugin" / "plugin.json").write_text(
            json.dumps({"name": name, "version": version, "description": desc}),
            encoding="utf-8",
        )
        return d

    first = _make_plugin("samename", "1.0.0", "old description")
    second = _make_plugin("samename", "2.0.0", "new description")

    mp.do_link_plugin(str(mkt), str(first), quiet=True)
    mp.do_link_plugin(str(mkt), str(second), quiet=True)

    mj = json.loads((mkt / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
    matches = [p for p in mj["plugins"] if p.get("name") == "samename"]
    # Replaced, never duplicated.
    assert len(matches) == 1
    # Metadata refreshed to the second link's values — disproves "skipped".
    assert matches[0]["version"] == "2.0.0"
    assert matches[0]["description"] == "new description"


def test_link_plugin_preserves_other_plugins(tmp_path: Path):
    """Entries for OTHER plugin names survive a same-name replace untouched."""
    mkt = _make_marketplace(tmp_path)
    mp.do_link_plugin(str(mkt), "Emasoft/keep-me", quiet=True)
    mp.do_link_plugin(str(mkt), "Emasoft/replace-me", quiet=True)
    mp.do_link_plugin(str(mkt), "Emasoft/replace-me", quiet=True)

    mj = json.loads((mkt / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
    names = [p.get("name") for p in mj["plugins"]]
    assert names.count("keep-me") == 1
    assert names.count("replace-me") == 1


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
