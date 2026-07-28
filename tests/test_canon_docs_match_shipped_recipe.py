"""The canon reference docs must not teach a shape the generator no longer emits.

CPV fixes land in `generate_plugin_repo.py`, but agents author workflows by
reading `skills/cpv-setup-plugin-repo/references/*.md`. When those drift, an
"upgrade" re-introduces the very defect the generator was just fixed for — this
already happened once (v2.137.1: the canon references PRESCRIBED an inverted
`CLAUDE_PRIVATE_USERNAMES`, so every upgrade re-created issue #140).

The propagation audit for #179/#180 found several such copies. Rather than fix
each instance and wait for the next one, these tests pin the PROPERTIES that
matter, scoped to the blocks that actually run the validator so unrelated shell
in the docs is never flagged.
"""

from __future__ import annotations

import re
from pathlib import Path

_REFS = Path(__file__).resolve().parents[1] / "skills" / "cpv-setup-plugin-repo" / "references"


def _validator_blocks() -> list[tuple[Path, str]]:
    """Every fenced/indented region of a canon doc that invokes the validator.

    Scoped deliberately: a doc may show plenty of other shell, and flagging that
    would make this guard noisy enough to be disabled.
    """
    blocks: list[tuple[Path, str]] = []
    for doc in sorted(_REFS.glob("*.md")):
        text = doc.read_text(encoding="utf-8")
        for chunk in text.split("run: |"):
            if "cpv-remote-validate" in chunk:
                # Keep the recipe body: up to the next step or heading.
                body = re.split(r"\n\s*-\s+name:|\n## ", chunk)[0]
                blocks.append((doc, body))
    return blocks


def test_there_is_something_to_check() -> None:
    """LOAD-BEARING: if the scoping regex stops matching, every test below would
    pass over an empty list and prove nothing."""
    assert _validator_blocks(), "no validator recipe found in the canon references"


def test_no_doc_teaches_the_blind_redirect() -> None:
    """`> file 2>&1` + a trailing `cat` makes a hung run indistinguishable from a
    healthy one, and a job killed at its cap never reaches the `cat` (#180)."""
    for doc, body in _validator_blocks():
        assert "cpv-remote-validate" not in body or "| tee " in body, (
            f"{doc.name}: validator recipe does not stream its output"
        )
        assert not re.search(r">\s*\"?\$?\{?[\w/.$-]*validation-report\.txt\"?\s+2>&1", body), (
            f"{doc.name}: validator recipe still uses the blind redirect"
        )


def test_no_doc_teaches_a_buffered_tee() -> None:
    """The validator calls no explicit flush, so without PYTHONUNBUFFERED its
    phase banners surface only when a 4-8 KB buffer happens to fill — and those
    banners are what tell you WHICH phase a hung run died in.

    Scoped claim, measured: this does NOT make the report stream. ~1794 of 1804
    lines arrive at exit either way, because they are the final report the
    validator generates at the end by program structure. The guard exists so the
    progress markers stay correctly timestamped, not to promise live output.
    """
    for doc, body in _validator_blocks():
        assert "PYTHONUNBUFFERED" in body, f"{doc.name}: tee'd recipe does not force unbuffered output"


def test_no_doc_reads_tees_exit_status() -> None:
    """After a pipeline, `$?` is tee's status — reading it greens every failed
    validation, a fail-OPEN gate."""
    for doc, body in _validator_blocks():
        assert "exit_code=$?" not in body, f"{doc.name}: reads $? after a pipeline instead of PIPESTATUS"


def test_no_doc_leaves_exit_code_unquoted() -> None:
    """shellcheck cannot infer numeric-ness through PIPESTATUS, so an unquoted
    expansion trips SC2086 — and the Lint job's actionlint turns that into red CI
    for every plugin scaffolded from the doc."""
    for doc, body in _validator_blocks():
        assert "[ $exit_code " not in body, f"{doc.name}: unquoted $exit_code in a test expression"
        assert not re.search(r"\bexit \$exit_code\s*$", body, re.MULTILINE), (
            f"{doc.name}: unquoted $exit_code in an exit"
        )


def test_no_doc_teaches_a_fail_open_handler() -> None:
    """RC-8: CPV's verdict codes stop at 4, so a handler that only errors on 1-4
    and falls through otherwise SILENTLY PASSES `uvx: command not found` (127)
    and an OOM kill (137). The SUMMARY line is required as proof the validator
    actually ran, because uvx itself also exits 1/2."""
    for doc, body in _validator_blocks():
        if "-le 4" not in body:
            continue
        assert "SUMMARY: CRITICAL=" in body, f"{doc.name}: gates on exit 1-4 without requiring the SUMMARY marker"
        assert "FAILED TO RUN" in body, f"{doc.name}: has no explicit non-verdict branch — falls through fail-open"
