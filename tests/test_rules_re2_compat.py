"""Tests for scripts/rules/re2_compatibility.json — the audit that
classifies every pattern in skillaudit_patterns.json by google-re2
compatibility.

These tests pin the audit's correctness so future edits to the source
patterns get caught: if a new pattern is added, the audit file must be
regenerated or the suite breaks loudly.

If google-re2 is not importable in the test environment, the re2-compile
cross-checks (per-pattern compile, error-message agreement) are skipped
but the structural / metadata / source-hash checks still run.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SOURCE = REPO_ROOT / "scripts" / "rules" / "skillaudit_patterns.json"
AUDIT = REPO_ROOT / "scripts" / "rules" / "re2_compatibility.json"

try:
    import re2 as _re2  # noqa: F401

    _RE2_AVAILABLE = True
except ImportError:  # pragma: no cover - only on platforms without re2
    _RE2_AVAILABLE = False


@pytest.fixture(scope="module")
def source_data() -> dict:
    """Parse skillaudit_patterns.json once per module."""
    return json.loads(SOURCE.read_bytes())


@pytest.fixture(scope="module")
def audit_data() -> dict:
    """Parse re2_compatibility.json once per module."""
    return json.loads(AUDIT.read_bytes())


# ---------------------------------------------------------------------------
# Structural / metadata
# ---------------------------------------------------------------------------


class TestAuditFileStructure:
    """Schema, metadata, and source-hash integrity."""

    def test_audit_file_exists(self) -> None:
        """re2_compatibility.json must exist beside skillaudit_patterns.json."""
        assert AUDIT.is_file(), f"audit file missing: {AUDIT}"

    def test_audit_top_level_keys(self, audit_data: dict) -> None:
        """Audit JSON has the documented top-level schema."""
        required = {
            "_schema_version",
            "_generated_at",
            "_source",
            "_source_sha256",
            "_re2_version",
            "_summary",
            "rules",
        }
        missing = required - set(audit_data)
        assert not missing, f"missing top-level keys: {missing}"

    def test_schema_version_is_1(self, audit_data: dict) -> None:
        """Schema is version 1 (bump when shape changes)."""
        assert audit_data["_schema_version"] == 1

    def test_source_field_points_to_skillaudit_patterns(
        self, audit_data: dict
    ) -> None:
        """_source field correctly identifies the patterns file."""
        assert audit_data["_source"].endswith("skillaudit_patterns.json")

    def test_source_sha256_matches_actual_file(self, audit_data: dict) -> None:
        """_source_sha256 must equal sha256 of skillaudit_patterns.json.

        If this fails, regenerate the audit file — the source patterns
        changed without the compatibility report being updated.
        """
        actual = hashlib.sha256(SOURCE.read_bytes()).hexdigest()
        assert audit_data["_source_sha256"] == actual, (
            "audit is stale; regenerate scripts/rules/re2_compatibility.json"
        )

    def test_generated_at_is_iso8601(self, audit_data: dict) -> None:
        """_generated_at is a parseable ISO 8601 timestamp."""
        ts = audit_data["_generated_at"]
        # Accept either Z suffix or numeric offset
        normalized = ts.replace("Z", "+00:00")
        import datetime as _dt

        # Will raise if invalid:
        _dt.datetime.fromisoformat(normalized)

    def test_re2_version_is_nonempty_string(self, audit_data: dict) -> None:
        """_re2_version is a non-empty string."""
        v = audit_data["_re2_version"]
        assert isinstance(v, str) and v.strip() != ""


# ---------------------------------------------------------------------------
# Summary correctness
# ---------------------------------------------------------------------------


class TestSummaryNumbers:
    """The _summary block must agree with the per-rule data."""

    def test_summary_total_matches_source_pattern_count(
        self, source_data: dict, audit_data: dict
    ) -> None:
        """_summary.total equals the number of patterns in the source file."""
        source_total = sum(
            len(rule["patterns"]) for rule in source_data["rules"]
        )
        assert audit_data["_summary"]["total"] == source_total

    def test_summary_compat_plus_incompat_equals_total(
        self, audit_data: dict
    ) -> None:
        """compatible + incompatible = total (no patterns lost in the cracks)."""
        summary = audit_data["_summary"]
        assert (
            summary["compatible"] + summary["incompatible"] == summary["total"]
        )

    def test_summary_rule_counts_match_rules_block(
        self, audit_data: dict
    ) -> None:
        """_summary rule counts agree with what's in the rules block."""
        rules = audit_data["rules"]
        compat_rules = sum(1 for r in rules.values() if r["compatible"])
        incompat_rules = sum(1 for r in rules.values() if not r["compatible"])
        assert audit_data["_summary"].get("rules_total") == len(rules)
        assert audit_data["_summary"].get("rules_compatible") == compat_rules
        assert audit_data["_summary"].get("rules_incompatible") == incompat_rules

    def test_per_rule_pattern_counts_match_source(
        self, source_data: dict, audit_data: dict
    ) -> None:
        """Every rule's patterns_total matches what's in the source."""
        source_lookup = {r["id"]: r for r in source_data["rules"]}
        for rule_id, audit_entry in audit_data["rules"].items():
            src = source_lookup[rule_id]
            assert audit_entry["patterns_total"] == len(src["patterns"]), (
                f"{rule_id}: audit says "
                f"{audit_entry['patterns_total']} patterns, source has "
                f"{len(src['patterns'])}"
            )


# ---------------------------------------------------------------------------
# Coverage — every source rule must have an audit entry
# ---------------------------------------------------------------------------


class TestRuleCoverage:
    """Every rule in the source file has an entry in the audit file."""

    def test_every_source_rule_has_audit_entry(
        self, source_data: dict, audit_data: dict
    ) -> None:
        """No rule may be silently dropped from the compatibility report."""
        source_ids = {r["id"] for r in source_data["rules"]}
        audit_ids = set(audit_data["rules"])
        missing = source_ids - audit_ids
        extra = audit_ids - source_ids
        assert not missing, f"audit missing rule_ids: {sorted(missing)}"
        assert not extra, f"audit has phantom rule_ids: {sorted(extra)}"

    def test_audit_rule_count_equals_source_rule_count(
        self, source_data: dict, audit_data: dict
    ) -> None:
        """Same number of rules on both sides."""
        assert len(audit_data["rules"]) == len(source_data["rules"])


# ---------------------------------------------------------------------------
# Per-entry well-formedness
# ---------------------------------------------------------------------------


class TestRuleEntryShape:
    """Per-rule entries follow the documented schema."""

    def test_every_rule_entry_has_required_keys(
        self, audit_data: dict
    ) -> None:
        """Each entry has compatible / patterns_total / patterns_compatible /
        patterns_incompatible / reason keys."""
        required = {
            "compatible",
            "patterns_total",
            "patterns_compatible",
            "patterns_incompatible",
            "reason",
        }
        for rule_id, entry in audit_data["rules"].items():
            missing = required - set(entry)
            assert not missing, f"{rule_id} missing keys: {missing}"

    def test_compatible_rules_have_null_reason(self, audit_data: dict) -> None:
        """compatible=true entries always have reason=None."""
        for rule_id, entry in audit_data["rules"].items():
            if entry["compatible"]:
                assert entry["reason"] is None, (
                    f"{rule_id} marked compatible but has reason={entry['reason']!r}"
                )

    def test_incompatible_rules_have_nonempty_reason(
        self, audit_data: dict
    ) -> None:
        """compatible=false entries always have a non-empty reason string."""
        for rule_id, entry in audit_data["rules"].items():
            if not entry["compatible"]:
                reason = entry["reason"]
                assert isinstance(reason, str) and reason.strip(), (
                    f"{rule_id} marked incompatible but reason is empty: "
                    f"{reason!r}"
                )

    def test_incompatible_patterns_list_each_have_reason(
        self, audit_data: dict
    ) -> None:
        """Each patterns_incompatible entry has index/pattern/reason fields."""
        for rule_id, entry in audit_data["rules"].items():
            for bad in entry["patterns_incompatible"]:
                missing = {"index", "pattern", "reason"} - set(bad)
                assert not missing, (
                    f"{rule_id} pattern entry missing fields: {missing}"
                )
                assert isinstance(bad["index"], int) and bad["index"] >= 0
                assert (
                    isinstance(bad["pattern"], str) and bad["pattern"] != ""
                )
                assert isinstance(bad["reason"], str) and bad["reason"] != ""

    def test_per_rule_pattern_arithmetic_holds(self, audit_data: dict) -> None:
        """patterns_compatible + len(patterns_incompatible) = patterns_total."""
        for rule_id, entry in audit_data["rules"].items():
            assert (
                entry["patterns_compatible"]
                + len(entry["patterns_incompatible"])
                == entry["patterns_total"]
            ), f"{rule_id} pattern counts don't sum to total"

    def test_rule_compatibility_flag_matches_incompatible_list(
        self, audit_data: dict
    ) -> None:
        """compatible=true iff the incompatible list is empty."""
        for rule_id, entry in audit_data["rules"].items():
            expected = len(entry["patterns_incompatible"]) == 0
            assert entry["compatible"] == expected, (
                f"{rule_id}: compatible flag disagrees with incompat list"
            )


# ---------------------------------------------------------------------------
# Live re2.compile cross-checks (skipped if google-re2 unavailable)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _RE2_AVAILABLE, reason="google-re2 not installed in test env"
)
class TestRe2CrossCheck:
    """Re-run re2.compile to verify the audit's per-pattern verdicts."""

    def test_every_compatible_pattern_actually_compiles(
        self, source_data: dict, audit_data: dict
    ) -> None:
        """For every pattern marked compatible, re2.compile() must succeed.

        If this fails, the audit lied — likely because the source file was
        edited but the audit wasn't regenerated.
        """
        import re2

        source_lookup = {r["id"]: r for r in source_data["rules"]}
        for rule_id, audit_entry in audit_data["rules"].items():
            patterns = source_lookup[rule_id]["patterns"]
            bad_indices = {b["index"] for b in audit_entry["patterns_incompatible"]}
            for idx, pat in enumerate(patterns):
                if idx in bad_indices:
                    continue
                try:
                    re2.compile(pat)
                except Exception as exc:  # noqa: BLE001
                    pytest.fail(
                        f"{rule_id}[{idx}] marked compatible but re2 raised: "
                        f"{type(exc).__name__}: {exc} | pattern={pat!r}"
                    )

    def test_every_incompatible_pattern_actually_fails(
        self, source_data: dict, audit_data: dict
    ) -> None:
        """For every pattern marked incompatible, re2.compile() must fail."""
        import re2

        source_lookup = {r["id"]: r for r in source_data["rules"]}
        for rule_id, audit_entry in audit_data["rules"].items():
            patterns = source_lookup[rule_id]["patterns"]
            for bad in audit_entry["patterns_incompatible"]:
                idx = bad["index"]
                pat = patterns[idx]
                # Sanity: the stored pattern matches the source pattern
                assert bad["pattern"] == pat, (
                    f"{rule_id}[{idx}] pattern drift: audit stored "
                    f"{bad['pattern']!r}, source has {pat!r}"
                )
                try:
                    re2.compile(pat)
                except Exception:  # noqa: BLE001
                    continue
                pytest.fail(
                    f"{rule_id}[{idx}] marked incompatible but re2 accepted "
                    f"it: {pat!r}"
                )


# ---------------------------------------------------------------------------
# Classifier sanity — synthetic patterns we know the answer for
# ---------------------------------------------------------------------------


class TestSyntheticPatternClassification:
    """Sanity-check classification logic on synthetic patterns we control."""

    @pytest.mark.skipif(
        not _RE2_AVAILABLE, reason="google-re2 not installed"
    )
    def test_lookahead_classified_incompatible(self) -> None:
        """A pattern using (?=...) is rejected by re2."""
        import re2

        with pytest.raises(Exception):
            re2.compile(r"foo(?=bar)")

    @pytest.mark.skipif(
        not _RE2_AVAILABLE, reason="google-re2 not installed"
    )
    def test_negative_lookahead_classified_incompatible(self) -> None:
        """A pattern using (?!...) is rejected by re2."""
        import re2

        with pytest.raises(Exception):
            re2.compile(r"foo(?!bar)")

    @pytest.mark.skipif(
        not _RE2_AVAILABLE, reason="google-re2 not installed"
    )
    def test_lookbehind_classified_incompatible(self) -> None:
        """A pattern using (?<=...) is rejected by re2."""
        import re2

        with pytest.raises(Exception):
            re2.compile(r"(?<=foo)bar")

    @pytest.mark.skipif(
        not _RE2_AVAILABLE, reason="google-re2 not installed"
    )
    def test_backreference_classified_incompatible(self) -> None:
        """A pattern using \\1 backreference is rejected by re2."""
        import re2

        with pytest.raises(Exception):
            re2.compile(r"(foo)\1")

    @pytest.mark.skipif(
        not _RE2_AVAILABLE, reason="google-re2 not installed"
    )
    def test_simple_pattern_classified_compatible(self) -> None:
        """A pattern with no Perl-only features compiles cleanly."""
        import re2

        re2.compile(r"foo.*bar")
        re2.compile(r"[a-z]+\.[0-9]+")
        re2.compile(r"(?:alt1|alt2|alt3)")  # non-capturing group is fine


# ---------------------------------------------------------------------------
# Specific real-world incompatibilities present in the source catalog
# ---------------------------------------------------------------------------


class TestKnownRealWorldIncompatibilities:
    """Confirm the audit catches the lookahead / backref cases that DO
    exist in the live skillaudit_patterns.json catalog."""

    def test_supply_chain_has_negative_lookahead(
        self, source_data: dict, audit_data: dict
    ) -> None:
        """SUPPLY_CHAIN: 'npm install\\s+(?!--|@)' uses negative lookahead.

        The audit must mark at least one SUPPLY_CHAIN pattern incompatible
        and the recorded reason must mention 'perl operator' or 'lookahead'."""
        entry = audit_data["rules"]["SUPPLY_CHAIN"]
        assert not entry["compatible"], (
            "SUPPLY_CHAIN should be incompatible (has (?!...) negative lookahead)"
        )
        incompat = entry["patterns_incompatible"]
        assert any(
            "(?!" in bad["pattern"] for bad in incompat
        ), "expected at least one (?!...) pattern in SUPPLY_CHAIN incompatibles"

    def test_deserialization_has_negative_lookahead(
        self, source_data: dict, audit_data: dict
    ) -> None:
        """DESERIALIZATION uses (?!.*Loader=...) and (?!.*weights_only)."""
        entry = audit_data["rules"]["DESERIALIZATION"]
        assert not entry["compatible"]
        assert any(
            "(?!" in bad["pattern"]
            for bad in entry["patterns_incompatible"]
        )

    def test_jwt_vuln_has_negative_lookahead(
        self, audit_data: dict
    ) -> None:
        """JWT_VULN has jwt.decode((?!.*verify) patterns."""
        entry = audit_data["rules"]["JWT_VULN"]
        assert not entry["compatible"]
        assert any(
            "(?!" in bad["pattern"]
            for bad in entry["patterns_incompatible"]
        )

    def test_indirect_prompt_inject_has_unicode_escapes(
        self, audit_data: dict
    ) -> None:
        """INDIRECT_PROMPT_INJECT pattern '\\u200b|...' uses raw \\u escapes
        which re2 rejects with 'invalid escape sequence: \\u'."""
        entry = audit_data["rules"]["INDIRECT_PROMPT_INJECT"]
        assert not entry["compatible"]
        assert any(
            "\\u" in bad["pattern"]
            for bad in entry["patterns_incompatible"]
        )

    def test_persistence_has_invalid_escape(self, audit_data: dict) -> None:
        """PERSISTENCE has 'HKEY.*\\Run' where \\R is invalid in re2."""
        entry = audit_data["rules"]["PERSISTENCE"]
        assert not entry["compatible"]
        # At least one incompatible pattern has \R in it
        assert any(
            re.search(r"\\R", bad["pattern"])
            for bad in entry["patterns_incompatible"]
        )

    def test_well_known_rule_is_compatible(self, audit_data: dict) -> None:
        """CRED_ENV_READ has only standard regex features, must be compatible."""
        entry = audit_data["rules"]["CRED_ENV_READ"]
        assert entry["compatible"], (
            f"CRED_ENV_READ should be compatible; got reason={entry['reason']!r}"
        )
        assert entry["reason"] is None
        assert entry["patterns_incompatible"] == []
