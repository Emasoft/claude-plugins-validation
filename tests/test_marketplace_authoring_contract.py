#!/usr/bin/env python3
"""Architectural tests for the marketplace-authoring-contract skill (TRDD-962fdc55).

Wave 7-B wires the `marketplace-authoring-contract` skill into the five
plugin-touching agents/commands:

* `agents/plugin-creator.md`
* `agents/plugin-fixer.md`
* `agents/marketplace-fixer.md`
* `commands/cpv-upgrade-plugin.md`
* `commands/cpv-migrate-marketplace.md`

The tests in this file enforce three invariants:

1. Every in-scope agent/command declares the skill in its YAML frontmatter
   (the loader test). Without this, the agent never reads the contract.
2. The skill itself ships with all 7 reference files (the references test).
   Missing a reference would silently break a sub-rule.
3. The contract's known-field allowlist stays aligned with the validator's
   `_KNOWN_MARKETPLACE_ENTRY_FIELDS` set (the drift-ratchet test). Drift
   between the contract docs and the validator is a self-inconsistency
   bug — both ends must be updated together or the test fails so the
   maintainer notices.

The drift-ratchet test (per TRDD §8.1) asserts the contract's recommended
allowlist is a subset of the validator's accepted set. The contract is
intentionally narrower than the validator (which accepts any plugin-manifest
field per `plugin-marketplaces.md:180-181`); the contract recommends a
closed 15-field subset for agent authoring. Drift in the wrong direction
(contract proposing a field the validator rejects) is the breakage that
matters and what this test guards against.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
AGENTS_DIR = PLUGIN_ROOT / "agents"
COMMANDS_DIR = PLUGIN_ROOT / "commands"
SKILLS_DIR = PLUGIN_ROOT / "skills"
CONTRACT_DIR = SKILLS_DIR / "marketplace-authoring-contract"

# Ensure scripts dir is on path so we can import the validator's allowlist.
sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))


# ---------------------------------------------------------------------------
# Frontmatter helper — shared with test_agent_model_tiers.py style
# ---------------------------------------------------------------------------


def _load_frontmatter(path: Path) -> dict:
    """Parse the YAML frontmatter block from a markdown file.

    Splits on ``\\n---\\n`` to avoid false positives on `---` characters
    inside the YAML body. Raises AssertionError if the frontmatter is
    missing or malformed.
    """
    import yaml

    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        raise AssertionError(f"{path} missing frontmatter — first line is not '---'")
    parts = text.split("\n---\n", 1)
    if len(parts) != 2:
        parts = text.split("\n---", 1)
        if len(parts) != 2:
            raise AssertionError(f"{path} missing closing frontmatter fence")
    head = parts[0]
    yaml_body = head.split("\n", 1)[1] if "\n" in head else ""
    data = yaml.safe_load(yaml_body) or {}
    if not isinstance(data, dict):
        raise AssertionError(f"{path} frontmatter is not a mapping: {type(data).__name__}")
    return data


# The five files that must load the contract — per TRDD §6 wiring spec.
IN_SCOPE_FILES: list[tuple[str, Path]] = [
    ("plugin-creator", AGENTS_DIR / "plugin-creator.md"),
    ("plugin-fixer", AGENTS_DIR / "plugin-fixer.md"),
    ("marketplace-fixer", AGENTS_DIR / "marketplace-fixer.md"),
    ("cpv-upgrade-plugin", COMMANDS_DIR / "cpv-upgrade-plugin.md"),
    ("cpv-migrate-marketplace", COMMANDS_DIR / "cpv-migrate-marketplace.md"),
]


# ---------------------------------------------------------------------------
# Test 1 — skill loader appears in every in-scope agent/command frontmatter
# ---------------------------------------------------------------------------


def test_all_in_scope_agents_load_contract_skill() -> None:
    """Every plugin-touching agent must declare marketplace-authoring-contract.

    Per TRDD §6: plugin-creator, plugin-fixer, marketplace-fixer,
    cpv-upgrade-plugin, and cpv-migrate-marketplace must declare the
    contract skill in their `skills:` frontmatter so the loader pulls
    it in BEFORE the agent emits any marketplace.json. Without this
    loader, the agent has no proactive guidance and falls back to ad-hoc
    drafting (the broken pre-Wave-7 state).
    """
    missing: list[str] = []
    for label, path in IN_SCOPE_FILES:
        assert path.exists(), f"{label} file missing at {path} — TRDD §6 in-scope file removed?"
        fm = _load_frontmatter(path)
        skills = fm.get("skills") or []
        if not isinstance(skills, list):
            raise AssertionError(f"{label}.skills frontmatter must be a list, got {type(skills).__name__}")
        if "marketplace-authoring-contract" not in skills:
            missing.append(label)
    assert not missing, (
        "These in-scope agents/commands do NOT declare marketplace-authoring-contract "
        f"in their `skills:` frontmatter: {missing}. TRDD-962fdc55 §6 requires the "
        "loader on all five. Without the loader, the agent skips the proactive "
        "contract guidance and reverts to the broken pre-Wave-7 state."
    )


# ---------------------------------------------------------------------------
# Test 2 — skill folder ships all 7 references
# ---------------------------------------------------------------------------


EXPECTED_REFERENCES = frozenset(
    {
        "name-canonicalisation.md",
        "version-strategy.md",
        "known-fields.md",
        "source-shape.md",
        "layout-decision-tree.md",
        "common-pitfalls.md",
        "preflight-recipe.md",
    }
)


def test_contract_skill_has_all_seven_references() -> None:
    """The contract skill must ship all 7 reference files (TRDD §5)."""
    refs_dir = CONTRACT_DIR / "references"
    assert refs_dir.is_dir(), f"references/ dir missing under {CONTRACT_DIR}"
    actual = {p.name for p in refs_dir.glob("*.md")}
    missing = EXPECTED_REFERENCES - actual
    assert not missing, (
        f"marketplace-authoring-contract references missing: {sorted(missing)}. "
        f"TRDD-962fdc55 §5 requires all 7 reference files. Found: {sorted(actual)}."
    )


def test_contract_skill_md_exists_and_references_all_seven() -> None:
    """SKILL.md must exist and cite all 7 reference files in its Resources block.

    The SKILL.md uses the progressive-disclosure pattern — agents read
    SKILL.md first, then drill into references on demand. A reference
    file that exists on disk but isn't cited from SKILL.md will be
    ignored by agents loading the skill.
    """
    skill_md = CONTRACT_DIR / "SKILL.md"
    assert skill_md.exists(), f"SKILL.md missing at {skill_md}"
    text = skill_md.read_text(encoding="utf-8")
    for ref_name in sorted(EXPECTED_REFERENCES):
        assert f"references/{ref_name}" in text, (
            f"SKILL.md does not cite references/{ref_name} — the progressive-disclosure "
            "loader won't pull it in. Add a link or summary entry pointing at the file."
        )


# ---------------------------------------------------------------------------
# Test 3 — drift-ratchet: contract allowlist subset of validator allowlist
# ---------------------------------------------------------------------------


# Identifier pattern for parsing the allowlist block. The contract's
# known-fields.md uses one field per line followed by " — " (em-dash) and
# a description; we extract the leading identifier.
_FIELD_LINE_RE = re.compile(r"^([a-zA-Z_$][a-zA-Z0-9_]*)\s+—")


def _parse_contract_known_fields() -> set[str]:
    """Extract the field-name set from known-fields.md's Allowlist block.

    The known-fields.md reference uses a fenced code block under the
    `## The Allowlist` heading. Each non-blank, non-fence line starts with
    an identifier followed by an em-dash and a description, e.g.::

        name             — REQUIRED, canonical plugin identifier (see ...)
        description      — short user-facing string

    We extract the leading identifier from every such line. Format must
    stay machine-parseable per known-fields.md §"Self-Consistency With the
    Validator".
    """
    known_fields_md = CONTRACT_DIR / "references" / "known-fields.md"
    text = known_fields_md.read_text(encoding="utf-8")

    # Find the `## The Allowlist` section body.
    pattern = re.compile(
        r"^##\s+The\s+Allowlist\s*$"  # Section header
        r"(?P<body>.*?)"  # Body (lazy)
        r"(?=^##\s)",  # Next H2 heading
        re.MULTILINE | re.DOTALL,
    )
    match = pattern.search(text)
    assert match is not None, (
        "known-fields.md is missing the `## The Allowlist` section header. The parser cannot extract the field set."
    )
    body = match.group("body")

    fields: set[str] = set()
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("```") or stripped.startswith("#"):
            continue
        m = _FIELD_LINE_RE.match(stripped)
        if m is not None:
            fields.add(m.group(1))
    return fields


# Known cross-source drift entries — the contract's known-fields.md lists
# these four fields as valid marketplace-entry fields, but the validator's
# OPTIONAL_PLUGIN_FIELDS set does NOT include them. They appear elsewhere
# in CPV (e.g. alwaysLoad / headersHelper are MCP-server fields per
# `scripts/validate_mcp.py:65,68`, claude_versions / platforms are
# documented in the canonical Anthropic spec but never declared at the
# marketplace-entry level by CPV).
#
# This set is the EXPLICIT allowlist of "known stale entries" — the
# drift-ratchet test below treats them as expected and allows them
# through. New drift NOT in this allowlist is a real bug and fails the
# test.
#
# When the underlying drift is fixed (either by adding the field to the
# validator's allowlist, by promoting these fields to officially-supported
# marketplace-entry status, OR by removing them from known-fields.md),
# delete the entry from this set so the test will catch new occurrences.
_KNOWN_CONTRACT_DRIFT_FIELDS = frozenset(
    {
        "alwaysLoad",  # MCP server field per validate_mcp.py:68 — not marketplace
        "headersHelper",  # MCP server field per validate_mcp.py:65 — not marketplace
        "claude_versions",  # spec-documented but not in validator's OPTIONAL list
        "platforms",  # spec-documented but not in validator's OPTIONAL list
    }
)


def test_contract_known_fields_match_validator_allowlist() -> None:
    """Drift-ratchet: the contract's Allowlist must be a subset of the validator's.

    The contract intentionally recommends a closed subset of fields for
    agent-authored marketplace entries — narrower than the validator's
    `_KNOWN_MARKETPLACE_ENTRY_FIELDS`, which accepts any plugin-manifest
    field per `plugin-marketplaces.md:180-181`. The contract's narrowing
    is by design — it prevents agents from emitting fields with unclear
    semantics. But the contract MUST NOT propose any field the validator
    would reject; that's the drift direction this ratchet guards against.

    Four fields are explicitly grandfathered into `_KNOWN_CONTRACT_DRIFT_FIELDS`
    (above) — those are pre-existing drift between contract and validator
    inherited from Wave 7-A. When the underlying issue is fixed, remove the
    field from the allowlist so the test catches re-introduction.

    If this test fails on a field NOT in the known-drift allowlist:
    - Contract proposes field X but validator rejects X → fix one of:
      (a) add X to `OPTIONAL_PLUGIN_FIELDS` in `validate_marketplace.py`
          if X is now a valid official field, OR
      (b) remove X from `known-fields.md` if it should never have been
          recommended.
    - Direction is one-way: validator may accept MORE than contract
      recommends (plugin-manifest fields auto-allowed); contract must
      NEVER recommend MORE than validator accepts.
    """
    from validate_marketplace import _KNOWN_MARKETPLACE_ENTRY_FIELDS

    contract_fields = _parse_contract_known_fields()
    assert contract_fields, (
        "Parser found ZERO fields in known-fields.md's Allowlist block. "
        "Check the file format hasn't drifted away from the documented "
        "machine-parseable shape (one field per line, `<name> — <desc>`)."
    )

    validator_fields = set(_KNOWN_MARKETPLACE_ENTRY_FIELDS)
    contract_minus_validator = contract_fields - validator_fields
    # Strip the known/grandfathered drift entries — they are tracked as a
    # follow-up bug, not flagged on every test run.
    new_drift = contract_minus_validator - _KNOWN_CONTRACT_DRIFT_FIELDS
    assert not new_drift, (
        "NEW drift detected — the contract's known-fields.md proposes fields "
        f"that validate_marketplace.py rejects AND are not in the known-drift "
        f"allowlist: {sorted(new_drift)}. "
        "Either add them to OPTIONAL_PLUGIN_FIELDS in validate_marketplace.py "
        "(if they became official) or remove them from known-fields.md. "
        f"\nContract fields: {sorted(contract_fields)}"
        f"\nValidator fields: {sorted(validator_fields)}"
        f"\nKnown-drift allowlist: {sorted(_KNOWN_CONTRACT_DRIFT_FIELDS)}"
    )
