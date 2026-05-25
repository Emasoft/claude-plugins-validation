#!/usr/bin/env python3
"""Tests for scripts/cpv_token_estimate.py.

Covers:
- Fixture parity: the raw o200k_base count matches reference counts captured
  from ``tiktoken`` (the authoritative o200k_base tokenizer, which
  ``gpt-tokenizer`` ports) across en/zh/th/hi/ar/code/mixed and more.
- The conservative property: ``estimate_tokens`` rounds up and never reports
  fewer tokens than the raw o200k count.
- Tier selection: default is BPE (tier 2); forcing the vocab missing falls back
  to the heuristic (tier 3); empty input is handled in every tier.
- The gzip vocab loads and parses correctly.

The reference counts below were produced by:

    import tiktoken
    enc = tiktoken.get_encoding("o200k_base")
    len(enc.encode(text))

``tiktoken`` is NOT a runtime dependency of CPV; it was used only to generate
these golden numbers. The port is validated to byte-exact token-id parity
against ``tiktoken`` on tens of thousands of random multilingual strings during
development; this suite pins the curated subset so regressions surface in CI.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import cpv_token_estimate as cte  # noqa: E402

# Each entry: (label, text, expected_o200k_token_count_from_tiktoken).
# These are EXACT o200k_base counts (before the Claude correction factor).
FIXTURES: list[tuple[str, str, int]] = [
    ("en", "The quick brown fox jumps over the lazy dog.", 10),
    ("en_punct", "Hello, world! It's a test.", 8),
    ("zh", "你好，世界！这是一个测试。", 8),
    ("th", "สวัสดีชาวโลก นี่คือการทดสอบ", 14),
    ("hi", "नमस्ते दुनिया, यह एक परीक्षण है।", 11),
    ("ar", "مرحبا بالعالم، هذا اختبار.", 8),
    ("ru", "Привет, мир! Это тест.", 8),
    ("ja", "こんにちは世界。テストです。", 7),
    ("ko", "안녕하세요 세계. 테스트입니다.", 7),
    ("code", "def foo(x):\n    return x * 2  # double it\n", 15),
    ("json", '{"key": "value", "n": 42, "arr": [1,2,3]}', 22),
    ("mixed", "Hello 世界 🌍 test123 café", 7),
    ("emoji", "🚀🔥✨👍🏽", 7),
    ("whitespace", "   leading and   multiple   spaces\t\ttabs\n\nnewlines", 13),
    ("numbers", "1234567890 3.14159 1,000,000", 15),
    ("empty", "", 0),
    ("single_char", "a", 1),
    ("camel", "XMLHttpRequest getHTTPResponseCode CamelCase snake_case", 11),
]


@pytest.fixture(autouse=True)
def _reset_module_cache() -> None:
    """Reset the module-level vocab cache and path before each test.

    Tests that force the vocab missing mutate module globals; this fixture
    restores a clean state so test order never matters.
    """
    cte._RANKS = None
    cte._RANKS_LOAD_FAILED = False
    cte._VOCAB_PATH = Path(cte.__file__).parent / "data" / "o200k_base.tiktoken.gz"


# ---------------------------------------------------------------------------
# Fixture parity vs tiktoken.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("label,text,expected", FIXTURES, ids=[f[0] for f in FIXTURES])
def test_raw_o200k_parity(label: str, text: str, expected: int) -> None:
    """The raw o200k_base count matches the tiktoken reference exactly."""
    assert cte.count_o200k_tokens(text) == expected


def test_parity_covers_required_scripts() -> None:
    """Sanity: the fixture set spans the scripts named in the task."""
    labels = {f[0] for f in FIXTURES}
    for required in ("en", "zh", "th", "hi", "ar", "code", "mixed"):
        assert required in labels


# ---------------------------------------------------------------------------
# Conservative property.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("label,text,expected", FIXTURES, ids=[f[0] for f in FIXTURES])
def test_estimate_is_conservative(label: str, text: str, expected: int) -> None:
    """estimate_tokens never under-counts relative to the raw o200k count."""
    est = cte.estimate_tokens(text)
    assert est.tokens >= expected


@pytest.mark.parametrize("label,text,expected", FIXTURES, ids=[f[0] for f in FIXTURES])
def test_estimate_applies_claude_correction(label: str, text: str, expected: int) -> None:
    """The default BPE estimate equals ceil(raw * CLAUDE_CORRECTION)."""
    est = cte.estimate_tokens(text)
    assert est.tokens == math.ceil(expected * cte.CLAUDE_CORRECTION)


def test_correction_factor_is_above_one() -> None:
    """The Claude correction must inflate (never deflate) the count."""
    assert cte.CLAUDE_CORRECTION > 1.0


def test_longer_text_never_fewer_tokens() -> None:
    """Monotonic-ish sanity: a superset string is never cheaper."""
    short = cte.estimate_tokens("hello").tokens
    longer = cte.estimate_tokens("hello hello hello hello hello").tokens
    assert longer >= short


# ---------------------------------------------------------------------------
# Tier selection.
# ---------------------------------------------------------------------------
def test_default_tier_is_bpe() -> None:
    """With the vocab present and no API opt-in, the BPE tier is used."""
    est = cte.estimate_tokens("Hello, world!")
    assert est.method == "bpe"


def test_empty_input_bpe_tier() -> None:
    """Empty input costs zero tokens in the BPE tier."""
    est = cte.estimate_tokens("")
    assert est.tokens == 0
    assert est.method == "bpe"


def test_tier3_when_vocab_missing() -> None:
    """When the vendored vocab cannot load, the heuristic tier is selected."""
    cte._VOCAB_PATH = Path("/definitely/not/a/real/path/o200k_base.tiktoken.gz")
    cte._RANKS = None
    cte._RANKS_LOAD_FAILED = False
    est = cte.estimate_tokens("Hello 世界 test")
    assert est.method == "heuristic"
    assert est.tokens > 0


def test_tier3_empty_input() -> None:
    """The heuristic tier returns zero tokens for empty input."""
    cte._VOCAB_PATH = Path("/definitely/not/a/real/path/o200k_base.tiktoken.gz")
    cte._RANKS = None
    cte._RANKS_LOAD_FAILED = False
    est = cte.estimate_tokens("")
    assert est.method == "heuristic"
    assert est.tokens == 0


def test_tier3_is_conservative_vs_bpe() -> None:
    """The heuristic fallback must not under-count relative to the BPE tier.

    A size gate that falls back to the heuristic must stay safe, so the
    heuristic count is expected to be >= the BPE count on typical text.
    """
    multilingual = [
        "The quick brown fox jumps over the lazy dog.",
        "人工智能正在改变世界。机器学习模型可以处理大量数据。",
        "นี่คือการทดสอบโทเค็นไนเซอร์",
        "नमस्ते दुनिया यह एक परीक्षण है",
        "مرحبا بالعالم هذا اختبار",
        "def estimate(text):\n    return len(text) * 2\n",
    ]
    for text in multilingual:
        bpe_tokens = cte.estimate_tokens(text).tokens
        cte._VOCAB_PATH = Path("/nope/missing.gz")
        cte._RANKS = None
        cte._RANKS_LOAD_FAILED = False
        heuristic_tokens = cte.estimate_tokens(text).tokens
        cte._VOCAB_PATH = Path(cte.__file__).parent / "data" / "o200k_base.tiktoken.gz"
        cte._RANKS = None
        cte._RANKS_LOAD_FAILED = False
        assert heuristic_tokens >= bpe_tokens, f"heuristic under-counted on {text!r}"


def test_api_tier_skipped_without_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    """The API tier is never consulted unless both opt-ins are set."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake-not-used")
    monkeypatch.delenv("CPV_TOKEN_EXACT", raising=False)
    # allow_api True but CPV_TOKEN_EXACT unset => must NOT use the API tier.
    est = cte.estimate_tokens("hello", allow_api=True)
    assert est.method == "bpe"


def test_api_tier_falls_through_on_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """When the API tier is opted in but errors, it falls back to BPE."""
    monkeypatch.setenv("CPV_TOKEN_EXACT", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake-will-fail")
    # Force the internal API call to fail.
    monkeypatch.setattr(cte, "_estimate_api", lambda _text: None)
    est = cte.estimate_tokens("hello", allow_api=True)
    assert est.method == "bpe"


def test_api_tier_used_when_available(monkeypatch: pytest.MonkeyPatch) -> None:
    """When all opt-ins are set and the API succeeds, the API tier is used."""
    monkeypatch.setenv("CPV_TOKEN_EXACT", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake")
    monkeypatch.setattr(cte, "_estimate_api", lambda _text: 999)
    est = cte.estimate_tokens("hello", allow_api=True)
    assert est.method == "api"
    assert est.tokens == 999


# ---------------------------------------------------------------------------
# Vocab gzip loading.
# ---------------------------------------------------------------------------
def test_gzip_vocab_loads() -> None:
    """The vendored gzip vocab loads and parses into the expected rank count."""
    ranks = cte._load_ranks()
    assert ranks is not None
    # o200k_base has 199,998 byte-pair ranks (excluding special tokens).
    assert len(ranks) == 199998
    # All 256 single bytes must be present (byte-level BPE requirement).
    for byte_value in range(256):
        assert bytes([byte_value]) in ranks


def test_vocab_file_exists() -> None:
    """The gzip vocab ships at the documented path under scripts/data/."""
    path = Path(cte.__file__).parent / "data" / "o200k_base.tiktoken.gz"
    assert path.is_file()
    assert path.stat().st_size > 0


def test_ranks_cached_after_first_load() -> None:
    """The parsed ranks dict is cached in a module global after first use."""
    cte._RANKS = None
    first = cte._load_ranks()
    second = cte._load_ranks()
    assert first is second  # same object => cached, not re-parsed


def test_load_failure_is_remembered() -> None:
    """A failed vocab load is remembered and not retried."""
    cte._VOCAB_PATH = Path("/no/such/vocab.gz")
    cte._RANKS = None
    cte._RANKS_LOAD_FAILED = False
    assert cte._load_ranks() is None
    assert cte._RANKS_LOAD_FAILED is True


# ---------------------------------------------------------------------------
# TokenEstimate dataclass surface.
# ---------------------------------------------------------------------------
def test_token_estimate_fields() -> None:
    """TokenEstimate exposes tokens (int), method (str), detail (str)."""
    est = cte.estimate_tokens("hello world")
    assert isinstance(est.tokens, int)
    assert isinstance(est.method, str)
    assert isinstance(est.detail, str)
    assert est.method in ("api", "bpe", "heuristic")


def test_count_o200k_raises_without_vocab() -> None:
    """count_o200k_tokens raises a clear error when the vocab is unavailable."""
    cte._VOCAB_PATH = Path("/no/such/vocab.gz")
    cte._RANKS = None
    cte._RANKS_LOAD_FAILED = False
    with pytest.raises(RuntimeError, match="vocab"):
        cte.count_o200k_tokens("hello")
