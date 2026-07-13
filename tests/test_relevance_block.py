#!/usr/bin/env python3
"""Two-sided tests for the marketplace `relevance` block (Claude Code v2.1.152+).

`relevance` is an OPTIONAL object on a marketplace plugin entry carrying the
signals Claude Code uses to SUGGEST the plugin (docs: plugin-relevance.md).
Before this change CPV emitted a publish-blocking MAJOR `RC-MKPL-UNKNOWN-FIELD`
on it — a false positive against a documented, spec-defined field.

Every clear below is paired with a still-fires case:

| Clear (must NOT fire)                          | Still fires (must fire)                       |
|------------------------------------------------|-----------------------------------------------|
| a valid `relevance` block → 0 findings          | a genuinely unknown field → RC-MKPL-UNKNOWN-FIELD |
| an entry with NO `relevance` → unaffected       | a non-object `relevance` → RC-MKPL-RELEVANCE-TYPE  |
| a bare hostname `api.stripe.com`                | scheme / port / path host → RC-MKPL-RELEVANCE-HOST |
"""

from __future__ import annotations

import json
import re
import sys
import tempfile
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent

# Make scripts importable for the validators.
sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))

from validate_marketplace import (  # noqa: E402
    validate_marketplace,
    validate_relevance_block,
)

JSON_PATH = "marketplace.json"

# The two examples straight out of the official plugin-relevance.md docs.
TERRAFORM_RELEVANCE = {
    "topic": "Terraform",
    "signals": {"cli": ["terraform"], "filesRead": ["**/*.tf"]},
}
STRIPE_RELEVANCE = {
    "topic": "Stripe",
    "signals": {
        "hosts": ["api.stripe.com"],
        "manifestDeps": [{"file": "package\\.json", "pattern": "stripe"}],
    },
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_RC_CODE_RE = re.compile(r"\[(RC-[A-Z0-9-]+)\]")


def _codes(results: list) -> list[str]:
    """Extract every bracketed RC-* code present in a result list.

    Parsed with a regex rather than matched against a hardcoded list of known
    codes: an allowlist silently yields [] for any code it has not been taught,
    so a NEW rule code would make every `_codes(...) == [...]` assertion fail
    confusingly — and, far worse, every `_codes(...) == []` assertion pass
    VACUOUSLY. The harness must not be able to go stale relative to the code
    it is testing.
    """
    out: list[str] = []
    for r in results:
        out.extend(_RC_CODE_RE.findall(r.message or ""))
    return out


def _check(relevance: object) -> list:
    return validate_relevance_block(relevance, "demo-plugin", JSON_PATH)


def _write_marketplace(tmp: Path, entry: dict) -> Path:
    """Layout-B fixture: marketplace.json + a sibling local plugin dir."""
    plugin_root = tmp / "plugin"
    (plugin_root / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    (plugin_root / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": "terraform-helpers", "version": "1.0.0"}, indent=2),
        encoding="utf-8",
    )
    mkpl_dir = tmp / ".claude-plugin"
    mkpl_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "name": "test-marketplace",
        "owner": {"name": "Tester"},
        "plugins": [{**entry, "source": "./plugin"}],
    }
    mkpl_dir.joinpath("marketplace.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return tmp


# ---------------------------------------------------------------------------
# VALID blocks — zero findings (the FP that motivated this work)
# ---------------------------------------------------------------------------


def test_doc_example_terraform_is_clean() -> None:
    """The official terraform example produces ZERO findings."""
    assert _check(TERRAFORM_RELEVANCE) == []


def test_doc_example_stripe_manifestdeps_is_clean() -> None:
    """The official stripe/manifestDeps example produces ZERO findings."""
    assert _check(STRIPE_RELEVANCE) == []


def test_all_five_signals_together_are_clean() -> None:
    """Every documented signal, at its documented shape, is accepted."""
    results = _check(
        {
            "topic": "Everything",
            "signals": {
                "cwd": ["**/infra/**"],
                "cli": ["terraform", "tofu"],
                "hosts": ["api.stripe.com", "registry.terraform.io"],
                "filesRead": ["**/*.tf", "**/*.tfvars"],
                "manifestDeps": [{"file": "package\\.json", "pattern": "stripe"}],
            },
        }
    )
    assert results == [], [r.message for r in results]


def test_topic_alone_without_signals_is_only_advisory() -> None:
    """`signals` absent → advisory WARNING, never an error."""
    results = _check({"topic": "Terraform"})
    assert len(results) == 1
    assert results[0].level == "WARNING"
    assert _codes(results) == ["RC-MKPL-RELEVANCE-NO-SIGNALS"]


def test_at_max_limits_is_clean() -> None:
    """Exactly at each documented limit → no LIMIT finding (boundary is inclusive)."""
    results = _check(
        {
            "topic": "T" * 64,
            "signals": {
                "cwd": ["a" * 256] * 10,
                "cli": ["c" * 64] * 10,
                "hosts": ["h" * 128] * 20,
                "filesRead": ["f" * 256] * 10,
                "manifestDeps": [{"file": "x" * 256, "pattern": "y" * 256}] * 10,
            },
        }
    )
    assert results == [], [r.message for r in results]


# ---------------------------------------------------------------------------
# MAJOR — RC-MKPL-RELEVANCE-TYPE
# ---------------------------------------------------------------------------


def test_relevance_not_an_object_is_major() -> None:
    for bad in ("Terraform", ["Terraform"], 42, True):
        results = _check(bad)
        assert len(results) == 1, bad
        assert results[0].level == "MAJOR"
        assert _codes(results) == ["RC-MKPL-RELEVANCE-TYPE"]


def test_signals_not_an_object_is_major() -> None:
    results = _check({"signals": ["cli"]})
    assert [r.level for r in results] == ["MAJOR"]
    assert _codes(results) == ["RC-MKPL-RELEVANCE-TYPE"]


def test_signal_value_not_an_array_is_major() -> None:
    results = _check({"signals": {"cli": "terraform"}})
    assert [r.level for r in results] == ["MAJOR"]
    assert _codes(results) == ["RC-MKPL-RELEVANCE-TYPE"]
    assert "relevance.signals.cli" in results[0].message


def test_signal_entry_not_a_string_is_major() -> None:
    results = _check({"signals": {"cli": [123]}})
    assert [r.level for r in results] == ["MAJOR"]
    assert _codes(results) == ["RC-MKPL-RELEVANCE-TYPE"]
    assert "relevance.signals.cli[0]" in results[0].message


def test_topic_not_a_string_is_major() -> None:
    results = _check({"topic": ["Terraform"], "signals": {"cli": ["terraform"]}})
    assert [r.level for r in results] == ["MAJOR"]
    assert _codes(results) == ["RC-MKPL-RELEVANCE-TYPE"]
    assert "relevance.topic" in results[0].message


def test_manifestdeps_entry_not_an_object_is_major() -> None:
    results = _check({"signals": {"manifestDeps": ["package.json"]}})
    assert [r.level for r in results] == ["MAJOR"]
    assert _codes(results) == ["RC-MKPL-RELEVANCE-TYPE"]


def test_manifestdeps_missing_file_or_pattern_is_major() -> None:
    results = _check({"signals": {"manifestDeps": [{"file": "package\\.json"}]}})
    assert [r.level for r in results] == ["MAJOR"]
    assert "'pattern'" in results[0].message

    results = _check({"signals": {"manifestDeps": [{"pattern": "stripe"}]}})
    assert [r.level for r in results] == ["MAJOR"]
    assert "'file'" in results[0].message


def test_manifestdeps_file_or_pattern_not_a_string_is_major() -> None:
    results = _check({"signals": {"manifestDeps": [{"file": 1, "pattern": 2}]}})
    assert [r.level for r in results] == ["MAJOR", "MAJOR"]
    assert _codes(results) == ["RC-MKPL-RELEVANCE-TYPE", "RC-MKPL-RELEVANCE-TYPE"]


# ---------------------------------------------------------------------------
# MAJOR — RC-MKPL-RELEVANCE-HOST (the bare-hostname rule)
# ---------------------------------------------------------------------------


def test_bare_hostname_is_valid() -> None:
    assert _check({"signals": {"hosts": ["api.stripe.com", "localhost"]}}) == []


def test_host_with_scheme_port_or_path_is_major() -> None:
    for bad in ("https://api.stripe.com", "api.stripe.com:443", "api.stripe.com/v1"):
        results = _check({"signals": {"hosts": [bad]}})
        assert len(results) == 1, bad
        assert results[0].level == "MAJOR", bad
        assert _codes(results) == ["RC-MKPL-RELEVANCE-HOST"], bad
        assert bad in results[0].message


def test_only_the_offending_host_entry_fires() -> None:
    """A bad host does not taint its valid siblings."""
    results = _check({"signals": {"hosts": ["api.stripe.com", "https://evil.example/x", "cdn.example.com"]}})
    assert len(results) == 1
    assert _codes(results) == ["RC-MKPL-RELEVANCE-HOST"]
    assert "[1]" in results[0].message


# ---------------------------------------------------------------------------
# WARNING — RC-MKPL-RELEVANCE-UNKNOWN
# ---------------------------------------------------------------------------


def test_unknown_key_under_relevance_is_warning() -> None:
    results = _check({"topic": "T", "bogus": 1, "signals": {"cli": ["terraform"]}})
    assert [r.level for r in results] == ["WARNING"]
    assert _codes(results) == ["RC-MKPL-RELEVANCE-UNKNOWN"]
    assert "'bogus'" in results[0].message


def test_unknown_key_under_signals_is_warning() -> None:
    results = _check({"signals": {"cli": ["terraform"], "bogusSignal": ["x"]}})
    assert [r.level for r in results] == ["WARNING"]
    assert _codes(results) == ["RC-MKPL-RELEVANCE-UNKNOWN"]
    assert "'bogusSignal'" in results[0].message


def test_empty_signals_is_advisory_warning_not_an_error() -> None:
    results = _check({"topic": "Terraform", "signals": {}})
    assert [r.level for r in results] == ["WARNING"]
    assert _codes(results) == ["RC-MKPL-RELEVANCE-NO-SIGNALS"]
    assert "never be suggested" in results[0].message


def test_signals_with_only_unknown_keys_is_flagged_inert() -> None:
    """Only-unknown keys is as inert as an empty object, and must say so.

    Previously each unknown key warned individually but nothing reported that
    the block as a whole can never produce a suggestion.
    """
    results = _check({"signals": {"bogusSignal": ["x"]}})
    assert _codes(results) == ["RC-MKPL-RELEVANCE-UNKNOWN", "RC-MKPL-RELEVANCE-NO-SIGNALS"]
    assert all(r.level == "WARNING" for r in results)


# ---------------------------------------------------------------------------
# RC-MKPL-RELEVANCE-LIMIT — WARNING, deliberately NON-BLOCKING.
#
# The docs enumerate what `claude plugin validate` rejects (unknown keys ->
# warning; non-object relevance; a hosts entry with scheme/port/path). A
# documented per-signal MAXIMUM is NOT among them, so Claude Code loads a
# marketplace that overruns one. A MINOR blocks --strict, so emitting MINOR
# would make CPV block a publish that Claude Code accepts. These assertions
# pin the severity so nobody "tightens" it back into an over-block.
# ---------------------------------------------------------------------------


def test_topic_over_64_chars_is_non_blocking_warning() -> None:
    results = _check({"topic": "T" * 65, "signals": {"cli": ["terraform"]}})
    assert [r.level for r in results] == ["WARNING"]
    assert _codes(results) == ["RC-MKPL-RELEVANCE-LIMIT"]


def test_too_many_entries_is_non_blocking_warning() -> None:
    for signal, over in (("cwd", 11), ("cli", 11), ("hosts", 21), ("filesRead", 11)):
        results = _check({"signals": {signal: ["x"] * over}})
        assert [r.level for r in results] == ["WARNING"], signal
        assert _codes(results) == ["RC-MKPL-RELEVANCE-LIMIT"], signal
        assert f"relevance.signals.{signal}" in results[0].message


def test_too_many_manifestdeps_is_non_blocking_warning() -> None:
    dep = {"file": "package\\.json", "pattern": "stripe"}
    results = _check({"signals": {"manifestDeps": [dep] * 11}})
    assert [r.level for r in results] == ["WARNING"]
    assert _codes(results) == ["RC-MKPL-RELEVANCE-LIMIT"]


def test_entry_over_length_limit_is_non_blocking_warning() -> None:
    for signal, length in (("cwd", 257), ("cli", 65), ("hosts", 129), ("filesRead", 257)):
        results = _check({"signals": {signal: ["a" * length]}})
        assert [r.level for r in results] == ["WARNING"], signal
        assert _codes(results) == ["RC-MKPL-RELEVANCE-LIMIT"], signal


def test_manifestdeps_regex_over_256_chars_is_non_blocking_warning() -> None:
    results = _check({"signals": {"manifestDeps": [{"file": "a" * 257, "pattern": "b" * 257}]}})
    assert [r.level for r in results] == ["WARNING", "WARNING"]
    assert _codes(results) == ["RC-MKPL-RELEVANCE-LIMIT", "RC-MKPL-RELEVANCE-LIMIT"]


def test_manifestdeps_entry_unknown_key_warns() -> None:
    """A typo'd sibling of file/pattern is ignored at load time — say so."""
    dep = {"file": "package\\.json", "pattern": "stripe", "bogus": 1}
    results = _check({"signals": {"manifestDeps": [dep]}})
    assert _codes(results) == ["RC-MKPL-RELEVANCE-UNKNOWN"]
    assert results[0].level == "WARNING"
    assert "'bogus'" in results[0].message


# ---------------------------------------------------------------------------
# End-to-end through the real marketplace validator
# ---------------------------------------------------------------------------


def test_e2e_relevance_no_longer_fires_unknown_field() -> None:
    """The FP is gone: a valid `relevance` block draws NO RC-MKPL-UNKNOWN-FIELD."""
    with tempfile.TemporaryDirectory() as td:
        tmp = _write_marketplace(
            Path(td),
            {"name": "terraform-helpers", "relevance": TERRAFORM_RELEVANCE},
        )
        report = validate_marketplace(tmp)
        offenders = [r for r in report.results if "RC-MKPL-UNKNOWN-FIELD" in (r.message or "")]
        assert offenders == [], [r.message for r in offenders]
        assert not [r for r in report.results if "RELEVANCE" in (r.message or "")]


def test_e2e_no_relevance_block_is_unaffected() -> None:
    """Regression guard: an entry with no `relevance` behaves exactly as before."""
    with tempfile.TemporaryDirectory() as td:
        tmp = _write_marketplace(Path(td), {"name": "terraform-helpers"})
        report = validate_marketplace(tmp)
        assert not [r for r in report.results if "RELEVANCE" in (r.message or "")]
        assert not [r for r in report.results if "RC-MKPL-UNKNOWN-FIELD" in (r.message or "")]


def test_e2e_a_genuinely_unknown_field_still_fires_major() -> None:
    """The allowlist widened by EXACTLY one field — `bogusField` still MAJORs."""
    with tempfile.TemporaryDirectory() as td:
        tmp = _write_marketplace(
            Path(td),
            {"name": "terraform-helpers", "relevance": TERRAFORM_RELEVANCE, "bogusField": "nope"},
        )
        report = validate_marketplace(tmp)
        offenders = [r for r in report.results if "RC-MKPL-UNKNOWN-FIELD" in (r.message or "") and r.level == "MAJOR"]
        assert len(offenders) == 1, [r.message for r in report.results]
        assert "'bogusField'" in offenders[0].message


def test_e2e_bad_host_surfaces_through_the_marketplace_validator() -> None:
    """A scheme-bearing host reaches the report as a MAJOR RC-MKPL-RELEVANCE-HOST."""
    with tempfile.TemporaryDirectory() as td:
        tmp = _write_marketplace(
            Path(td),
            {
                "name": "terraform-helpers",
                "relevance": {"signals": {"hosts": ["https://api.stripe.com/v1"]}},
            },
        )
        report = validate_marketplace(tmp)
        offenders = [r for r in report.results if "RC-MKPL-RELEVANCE-HOST" in (r.message or "")]
        assert len(offenders) == 1
        assert offenders[0].level == "MAJOR"
