#!/usr/bin/env python3
"""Issue #193 — the size-gate finding must show its work.

THE COMPLAINT, and what was actually wrong. The finding said
"~5167 tokens (limit 5000; bpe estimate)". The count is NOT raw BPE: it is
o200k_base BPE x1.3 Claude-correction (Claude's tokenizer is not public and
runs ~20-25% over o200k; the margin keeps a runtime-truncation gate strictly
conservative). The reporter measured their file with tiktoken/cl100k, got a
number ~35% lower, and reasonably concluded the gate was `chars/3.0151` — two
probe edits on homogeneous prose even reproduced a constant ratio. The LABEL
caused the misdiagnosis: "bpe estimate" asserted a precision and a method the
number does not have, and no measured inputs were printed.

Verified against their own numbers before fixing: CPV's raw o200k count of
their file is 3861 vs their cl100k 3841 (0.5% apart — the tokenizers agree),
and CPV's reported 5020 is exactly ceil(3861 x 1.3). The estimator is a real
tokenizer and the factor is deliberate; only the reporting was wrong.

So the fix is DISCLOSURE, not recalibration: the finding now carries the char
count, the raw o200k count, and the x1.3 factor. The estimator, the factor and
the caps are unchanged — these tests pin that too, because "fix the message"
must not become a route to quietly weakening a gate.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import cpv_token_estimate as cte  # noqa: E402
from cpv_validation_common import ValidationReport, check_token_limit  # noqa: E402

# Long enough to exceed a tiny limit deterministically.
_PROSE = "The validation gate measures skill bodies against a token budget. " * 40


def _one_finding(limit: int) -> str:
    report = ValidationReport()
    fired = check_token_limit(
        _PROSE, limit, report, file="skills/x/SKILL.md",
        field_label="SKILL.md body", advice="Trim it.",
    )
    assert fired is True
    majors = [r for r in report.results if r.level == "MAJOR"]
    assert len(majors) == 1
    return majors[0].message


def test_finding_states_what_it_measured_and_how():
    """Chars, raw o200k count, and the x1.3 factor — the reporter's exact asks."""
    msg = _one_finding(limit=50)
    assert "estimated Claude tokens" in msg
    assert f"{len(_PROSE)} chars" in msg
    assert "o200k_base BPE (" in msg
    assert "x1.3 Claude-correction" in msg


def test_finding_no_longer_claims_to_be_a_bare_bpe_count():
    """The misleading label is gone.

    "bpe estimate" is what sent the reporter reverse-engineering a divisor: it
    names a method the displayed number is not. The method line now comes from
    est.detail, which names both the BPE count AND the correction.
    """
    msg = _one_finding(limit=50)
    assert "bpe estimate" not in msg


def test_displayed_number_is_the_disclosed_arithmetic():
    """The headline count must equal ceil(raw_o200k x 1.3) — no hidden terms."""
    import math

    msg = _one_finding(limit=50)
    est = cte.estimate_tokens(_PROSE)
    raw = cte.count_o200k_tokens(_PROSE)
    assert est.tokens == math.ceil(raw * cte.CLAUDE_CORRECTION)
    assert f"~{est.tokens} estimated Claude tokens" in msg
    assert f"({raw} tokens)" in msg


def test_text_under_the_limit_emits_nothing():
    """CONTROL — disclosure must not have widened when the gate fires."""
    report = ValidationReport()
    fired = check_token_limit(
        "short.", 50, report, file="skills/x/SKILL.md",
        field_label="SKILL.md body", advice="Trim it.",
    )
    assert fired is False
    assert not [r for r in report.results if r.level == "MAJOR"]


# ── gate strength is NOT what this fix changes — pin it ─────────────────────


def test_correction_factor_and_caps_unchanged():
    """Recalibrating the margin or the caps is a separate, owner-level decision.

    If either assert fails, someone changed gate STRENGTH under the banner of a
    reporting fix — exactly the move the no-suppression policy forbids.
    """
    from cpv_validation_common import (
        AGENT_DESCRIPTION_TOKEN_LIMIT,
        DESCRIPTION_TOKEN_LIMIT,
        SKILL_BODY_TOKEN_LIMIT,
        WHEN_TO_USE_TOKEN_LIMIT,
    )

    assert cte.CLAUDE_CORRECTION == 1.3
    assert SKILL_BODY_TOKEN_LIMIT == 5000
    assert DESCRIPTION_TOKEN_LIMIT == 200
    assert WHEN_TO_USE_TOKEN_LIMIT == 100
    assert AGENT_DESCRIPTION_TOKEN_LIMIT == 300


def test_heuristic_fallback_discloses_itself_too():
    """When the vendored vocab is unavailable the message must say so.

    est.detail for tier 3 names the heuristic and its margin; the finding
    format carries detail verbatim, so this holds by construction — pinned
    against a regression that reformats detail away for one tier only.
    """
    est_detail_t3 = "per-script chars/token heuristic"
    src = (SCRIPTS / "cpv_token_estimate.py").read_text(encoding="utf-8")
    assert est_detail_t3 in src
    common = (SCRIPTS / "cpv_validation_common.py").read_text(encoding="utf-8")
    assert "{est.detail}" in common


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
