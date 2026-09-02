"""Parity test for the four `pipeline-rules.md` reference copies (TRDD-EZHM759T).

The skills cpv-canonical-pipeline, cpv-setup-plugin-repo, cpv-standardize-plugin,
and cpv-create-plugin each ship their own copy of `references/pipeline-rules.md`.
The four copies had drifted (different md5, 159-264 lines, each carrying unique
sections the others lacked — the `dev` extra provisioning, superseded
validate.yml removal, and `.jscpd.json` provisioning sections were only present
in one or two of the four). A drifted duplicate is a stale-doc trap: an agent
reading the "wrong" copy re-introduces a bug another copy was already fixed for
(the recorded v2.137.1/v3.22.1 lesson).

The copies were unioned into one canonical text and synced byte-for-byte. This
test is the regression lock — it fails the moment any one copy is edited
without propagating the edit to its three siblings.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

PIPELINE_RULES_COPIES = [
    REPO_ROOT / "skills" / "cpv-canonical-pipeline" / "references" / "pipeline-rules.md",
    REPO_ROOT / "skills" / "cpv-setup-plugin-repo" / "references" / "pipeline-rules.md",
    REPO_ROOT / "skills" / "cpv-standardize-plugin" / "references" / "pipeline-rules.md",
    REPO_ROOT / "skills" / "cpv-create-plugin" / "references" / "pipeline-rules.md",
]

REQUIRED_SECTION_TITLES = [
    "The `dev` extra MUST exist (issue #142 Defect #2)",
    "Superseded validate.yml Removal (issue #142 Defect #4)",
    "### `standardize` provisions `.jscpd.json`",
]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_all_four_pipeline_rules_copies_are_byte_identical() -> None:
    """The four skill copies of pipeline-rules.md must be byte-for-byte identical."""
    for copy in PIPELINE_RULES_COPIES:
        assert copy.exists(), f"pipeline-rules.md copy missing: {copy}"

    hashes = {str(copy): _sha256(copy) for copy in PIPELINE_RULES_COPIES}
    distinct_hashes = set(hashes.values())
    assert len(distinct_hashes) == 1, (
        "pipeline-rules.md copies have drifted — not byte-identical:\n"
        + "\n".join(f"  {path}: {digest}" for path, digest in hashes.items())
    )


def test_canonical_copy_is_nonempty_and_carries_the_unioned_sections() -> None:
    """Positive control: the canonical copy is non-empty and holds every
    section that used to exist in only one or two of the four drifted copies."""
    canonical = PIPELINE_RULES_COPIES[0]
    body = canonical.read_text()

    assert len(body.strip()) > 0, f"{canonical} is empty"

    for title in REQUIRED_SECTION_TITLES:
        assert title in body, (
            f"{canonical} is missing the unioned section {title!r} — the "
            f"pipeline-rules.md merge lost content that used to live in one "
            f"of the four drifted copies."
        )
