"""Regression tests for issue #236 - TOKEN_STEAL CRITICAL on a Rust test assertion.

``assert!(req.contains("Authorization: Bearer test-key"));`` only COMPARES a literal
header text inside an assertion; it sends nothing. The Rust context classifier now
clears TOKEN_STEAL for a single-line assertion macro whose credential text is only a
string-literal operand of ``.contains(`` / ``.starts_with(`` / ``.ends_with(`` / ``==``
/ ``!=`` / ``assert_eq!`` args. No cfg(test) span demotion (owner policy). Each
assertion is two-sided: sibling shapes that carry a real credential flow keep firing.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from cpv_skillaudit_native import scan_content  # noqa: E402


@pytest.fixture(autouse=True)
def _no_scan_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CPV_SCAN_CACHE", "0")


def _rules(line: str) -> set[str]:
    content = "fn t() {\n" + line + "\n}\n"
    return {f["ruleId"] for f in scan_content(content, "src/a.rs") if not f.get("suppressed")}


def test_assert_contains_literal_header_cleared() -> None:
    """assert!(x.contains("<header literal>")) sends nothing -> no TOKEN_STEAL."""
    assert "TOKEN_STEAL" not in _rules('assert!(req.contains("Authorization: Bearer test-key"));')


def test_assert_eq_literal_header_cleared() -> None:
    """assert_eq!(h, "<header literal>") only compares -> no TOKEN_STEAL."""
    assert "TOKEN_STEAL" not in _rules('assert_eq!(h, "Authorization: Bearer test-key");')


def test_assert_ne_and_comparison_operators_cleared() -> None:
    """assert_ne!, debug_assert! with ==/!= and starts_with/ends_with literals clear."""
    assert "TOKEN_STEAL" not in _rules('assert_ne!(h, "Authorization: Bearer test-key");')
    assert "TOKEN_STEAL" not in _rules('debug_assert!(h == "Authorization: Bearer test-key");')
    assert "TOKEN_STEAL" not in _rules('assert!(h.starts_with("Authorization: Bearer test-key"));')
    assert "TOKEN_STEAL" not in _rules('assert!(h.ends_with("Authorization: Bearer test-key"));')


def test_format_with_env_var_still_fires() -> None:
    """A header built from env::var is a real credential flow -> still TOKEN_STEAL."""
    assert "TOKEN_STEAL" in _rules('let h = format!("Authorization: Bearer {}", std::env::var("K").unwrap());')


def test_assert_with_env_var_or_format_still_fires() -> None:
    """An assertion that also touches env::var / format! / concatenation keeps firing."""
    assert "TOKEN_STEAL" in _rules(
        'assert!(req.contains("Authorization: Bearer test-key") && std::env::var("K").is_ok());'
    )
    assert "TOKEN_STEAL" in _rules('assert!(req.contains(&format!("Authorization: Bearer {}", t)));')
    assert "TOKEN_STEAL" in _rules('assert!(req.contains(&("Authorization: Bearer ".to_string() + t)));')


def test_literal_outside_assertion_or_with_trailing_statement_still_fires() -> None:
    """A literal not confined to one assertion call (second statement on the line) keeps firing."""
    assert "TOKEN_STEAL" in _rules('assert!(ok); send("Authorization: Bearer test-key");')
    assert "TOKEN_STEAL" in _rules('let h = "Authorization: Bearer test-key";')



def test_assert_eq_literal_nested_in_call_still_fires() -> None:
    """A literal nested inside a call is not a top-level assert_eq! operand -> still TOKEN_STEAL."""
    assert "TOKEN_STEAL" in _rules('assert_eq!(send(u, "Authorization: Bearer x"), 1);')
    assert "TOKEN_STEAL" in _rules('assert_ne!(send(u, "Authorization: Bearer x"), 1);')


def test_disqualifiers_decide() -> None:
    """Each disqualifier (header/bearer_auth/reqwest/curl/Command::new/char literal) keeps an otherwise-cleared line firing."""
    lit = '"Authorization: Bearer test-key"'
    for extra in (
        f"req.header(\"a\", \"b\").contains({lit})",
        f"client.bearer_auth(t).contains({lit})",
        f"reqwest::get(u).contains({lit})",
        f"curl_cmd.contains({lit})",
        f"Command::new(\"x\").contains({lit})",
        f"s.contains(\x27\"\x27) && s.contains({lit})",
    ):
        assert "TOKEN_STEAL" in _rules(f"assert!({extra});"), extra


def test_header_call_unchanged() -> None:
    """req.header("Authorization", "Bearer ...") outside an assert: current behaviour (no TOKEN_STEAL) unchanged."""
    assert _rules('req.header("Authorization", "Bearer test-key");') == set()


def test_bearer_auth_in_assert_unchanged() -> None:
    """assert!(client.bearer_auth(env::var(..))...) : current behaviour unchanged (TOKEN_STEAL not cleared by classifier)."""
    assert _rules('assert!(client.bearer_auth(std::env::var("TOKEN").unwrap()).send().is_ok());') == set()
