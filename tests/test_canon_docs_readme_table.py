"""The marketplace README-table contract must be documented where agents read it.

CPV's canonical marketplace pipeline gained a `scripts/render_readme_table.py`
renderer: the Update-Versions workflow runs it BEFORE the change-check and
stages README.md, and the validate workflow runs it with `--check` as a gate.
The canonical markers are `<!-- PLUGIN-VERSIONS-START -->` /
`<!-- PLUGIN-VERSIONS-END -->`.

Every agent-facing skill that fixes or migrates a marketplace pipeline must
document this contract, or an agent mid-task has no recipe for the new
findings. And no doc in scope may recommend `metadata.version` as the
canonical field to bump/read — the canonical slot is the top-level `version`
(marketplace-level and per-plugin-entry); `metadata.version` is accepted only
for backward compatibility.

These tests read the files from disk (not assumed content) and FAIL if any of
them reverts.
"""

from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]

# (A `_SKILL_DOCS` list once sat here and was never asserted on. The two docs it named
# are each covered by their own named tests below, so it was a ghost, not missing coverage.)

# Every markdown file in scope for this task that may mention the dependency
# version field — must never treat `metadata.version` as canonical.
_METADATA_VERSION_SCOPE = [
    _ROOT / "skills" / "cpv-canonical-pipeline" / "SKILL.md",
    _ROOT / "skills" / "cpv-canonical-pipeline" / "references" / "detailed-standard.md",
    _ROOT / "skills" / "cpv-fix-validation" / "references" / "plugin-structure-fixes.md",
    _ROOT / "skills" / "cpv-migrate-marketplace-architecture" / "references" / "layout-a-migration.md",
    _ROOT / "skills" / "cpv-migrate-marketplace-architecture" / "references" / "layout-b-discipline.md",
    _ROOT / "skills" / "cpv-migrate-marketplace-architecture" / "references" / "layout-c-migration.md",
]


def _read(path: Path) -> str:
    assert path.is_file(), f"missing doc: {path}"
    return path.read_text(encoding="utf-8")


def test_canonical_pipeline_doc_documents_the_renderer() -> None:
    """The Marketplace Standard section must name the real shipped script."""
    text = _read(_ROOT / "skills" / "cpv-canonical-pipeline" / "references" / "detailed-standard.md")
    assert "render_readme_table.py" in text
    # `update_catalog.py` DOES exist — generate_marketplace_repo.py still emits it
    # (and tests/test_audit_fix_b14.py covers it). The assertion stands, but the
    # reason is that the CANON standard must name ONE mechanism: the renderer with
    # the PLUGIN-VERSIONS markers and the --check gate. Calling the older script
    # "non-existent" was false and would send a reader looking for a deletion that
    # never happened.
    assert "update_catalog.py" not in text, (
        "the canon standard must name render_readme_table.py, not the older "
        "update_catalog.py catalog mechanism"
    )


def test_canonical_pipeline_doc_documents_the_markers() -> None:
    text = _read(_ROOT / "skills" / "cpv-canonical-pipeline" / "references" / "detailed-standard.md")
    assert "<!-- PLUGIN-VERSIONS-START -->" in text
    assert "<!-- PLUGIN-VERSIONS-END -->" in text


def test_canonical_pipeline_doc_documents_the_check_gate() -> None:
    text = _read(_ROOT / "skills" / "cpv-canonical-pipeline" / "references" / "detailed-standard.md")
    assert "--check" in text


def test_canonical_pipeline_doc_states_top_level_version_is_canonical() -> None:
    text = _read(_ROOT / "skills" / "cpv-canonical-pipeline" / "references" / "detailed-standard.md")
    assert "top-level" in text and "version" in text
    assert "backward compat" in text


_PIPELINE_SRC = _ROOT / "scripts" / "validate_marketplace_pipeline.py"
_MARKETPLACE_FIXES = _ROOT / "skills" / "cpv-fix-validation" / "references" / "marketplace-fixes.md"


def _recipe_block(text: str, heading: str) -> str:
    """Return one `### <heading>` section, up to the next heading of level 2 OR 3.

    Terminating on `### ` alone OVER-RUNS: §5.18 is followed by `## 6. Version Sync
    Issues` and §8.10 by `## 9. Architecture …`, so each block swallowed the whole
    next section up to its first `###`. Measured: 33 and 35 lines, each including a
    level-2 heading that is not part of the recipe. The severity assertions below then
    passed by luck — they scanned a span that happened to contain no stale claim, and
    the next MINOR-severity recipe filed as §6.x would have failed the §5.18 test with
    a message naming the wrong section.
    """
    start = text.index(heading)
    rest = text[start + len(heading) :]
    match = re.search(r"\n#{2,3} ", rest)
    return heading + (rest[: match.start()] if match else rest)


def _assert_advisory_recipe(heading: str, emitted_substring: str) -> None:
    """A recipe must be findable from the EMITTED text and must not misstate severity.

    Two failure modes this pins, both of which shipped in this change set before review:
    a `Message` row paraphrasing the finding so loosely that an agent holding the real
    report text cannot grep its way to the recipe; and a `Severity` row asserting MINOR
    for a finding the code emits at zero-weight INFO. A doc that misstates severity sends
    a reader to fix a publish blocker that does not exist, and stops them checking.
    """
    block = _recipe_block(_read(_MARKETPLACE_FIXES), heading)
    assert "| **Severity** | INFO" in block, f"{heading}: severity row must state INFO"
    assert "advisory" in block, f"{heading}: severity row must say it is advisory"
    # Scoped to the Severity ROW, not the whole block: a bare `"MINOR" not in block`
    # also rejects legitimate prose (e.g. a note that the finding was MINOR before the
    # canon existed), which is a rule that reddens on correct writing.
    assert "| **Severity** | MINOR" not in block, f"{heading}: stale MINOR severity row"
    assert emitted_substring in block, f"{heading}: recipe is not findable from the emitted text"
    # The doc↔code cross-check: the quoted message must be text the validator really emits.
    assert emitted_substring in _read(_PIPELINE_SRC), (
        f"{heading}: quoted message does not appear in validate_marketplace_pipeline.py — "
        "the recipe quotes a message the code never emits"
    )


def test_fix_validation_marketplace_fixes_has_workflow_recipe() -> None:
    """The advisory `marketplace_workflows` finding must have a fix recipe, quoted verbatim."""
    _assert_advisory_recipe(
        "### 5.18 Update workflow does not regenerate the README plugin table",
        "update-submodules.yml does not regenerate the README plugin table",
    )
    text = _read(_MARKETPLACE_FIXES)
    assert "render_readme_table.py" in text
    assert "PLUGIN-VERSIONS" in text


def test_fix_validation_marketplace_fixes_has_readme_markers_recipe() -> None:
    """The advisory `documentation` finding must have a fix recipe, quoted verbatim."""
    _assert_advisory_recipe(
        "### 8.10 README lacks PLUGIN-VERSIONS markers",
        "README.md is missing the <!-- PLUGIN-VERSIONS-START/END --> markers",
    )


def test_marketplace_error_index_routes_both_new_findings() -> None:
    text = _read(_ROOT / "skills" / "cpv-fix-validation" / "references" / "marketplace-error-index.md")
    assert "Update workflow does not regenerate the README plugin table" in text
    assert "marketplace-fixes §5.18" in text
    assert "README lacks PLUGIN-VERSIONS markers" in text
    assert "marketplace-fixes §8.10" in text


def test_no_doc_in_scope_recommends_metadata_version_as_canonical() -> None:
    """`metadata.version` may be MENTIONED (backward-compat), never presented as
    the field to bump/read from, or as a plain requirement with no qualifier.

    Checked per PARAGRAPH (blank-line-separated block), not per line, because
    markdown hard-wraps a sentence's qualifier onto the next physical line.
    """
    for path in _METADATA_VERSION_SCOPE:
        text = _read(path)
        for paragraph in text.split("\n\n"):
            if "metadata.version" not in paragraph:
                continue
            qualifies = (
                "backward compat" in paragraph
                or "backward-compat" in paragraph
                or "canonical" in paragraph
                or "legacy" in paragraph
                or "already present" in paragraph
                or "NIT" in paragraph
            )
            assert qualifies, f"{path.name}: unqualified metadata.version paragraph: {paragraph!r}"


def test_layout_c_migration_severity_matches_shipped_behaviour() -> None:
    """metadata.version-only drift is a NIT; only a disagreement is a WARNING."""
    text = _read(
        _ROOT / "skills" / "cpv-migrate-marketplace-architecture" / "references" / "layout-c-migration.md"
    )
    assert "NIT" in text
    assert "not a blocking finding" in text or "not a blocking" in text
