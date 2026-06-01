"""Audit batch B25 — regression tests for the verified+fixed findings.

Covers the two CODE findings on this agent's assigned files:

* Finding 58 (FIXED) — ``scripts/_minimal_yaml.py``: a folded block scalar
  (``key: >``) used to DROP blank lines (``" ".join(s for s in ... if s)``),
  running the words on either side of a blank line together. YAML 1.2 folds a
  run of ``k`` blank lines into ``k`` literal newlines. The fix re-implements
  the fold via ``_fold_block_lines`` so the result matches ``pyyaml`` across
  every blank-line / chomp combination.

* Finding 141 (REFUTED — guard test) — ``scripts/_plugin_verify_hashes.py``:
  the audit asked that ``verify_self_integrity`` set the process-level
  ``_VERIFIED_THIS_PROCESS`` flag to True on a hash MISMATCH when
  ``fail_on_mismatch=False``. Doing so would be a SECURITY BYPASS: the flag
  means "verification SUCCEEDED this process", and memoizing a FAILED
  verification as success makes a subsequent strict gate short-circuit to
  True. This test pins the correct (current) behavior: a failed verification
  is NEVER memoized as success.

The pure-documentation (.md command) fixes in this batch need no test.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml as real_yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import _plugin_verify_hashes as pvh  # noqa: E402
from _minimal_yaml import safe_load  # noqa: E402

# ---------------------------------------------------------------------------
# Finding 58 — folded scalar must convert blank lines to newlines (not drop)
# ---------------------------------------------------------------------------


def _doc(indicator: str, pat: list[str]) -> str:
    """Build a single-key frontmatter doc with a block scalar value.

    ``pat`` is the list of body lines; an empty string is a blank line.
    """
    body = "".join(("  " + line + "\n") if line else "\n" for line in pat)
    return f"key: {indicator}\n{body}"


def test_folded_scalar_blank_line_becomes_newline_not_dropped() -> None:
    """A blank line inside a ``>`` folded scalar folds to a newline, not nothing.

    This is the exact audit reproducer. Pre-fix the parser produced
    'line one line two line four after blank' (words run together); the fix
    yields 'line one line two\\nline four after blank\\n', matching pyyaml.
    """
    doc = "key: >\n  line one\n  line two\n\n  line four after blank\n"
    parsed = safe_load(doc)
    assert parsed is not None
    got = parsed["key"]
    assert got == "line one line two\nline four after blank\n"
    # Guard that would have caught the original bug: the two paragraphs must
    # NOT be glued together on a single line.
    assert "two line four" not in got
    assert "\n" in got


def test_folded_scalar_all_content_lines_still_fold_with_spaces() -> None:
    """No blank lines → every line folds with a single space (unchanged behavior)."""
    out = safe_load("description: >\n  hello world\n  next line\n")
    assert out == {"description": "hello world next line\n"}


@pytest.mark.parametrize(
    "indicator",
    [">", ">-", ">+"],
)
@pytest.mark.parametrize(
    "pat",
    [
        ["a", "b", "c"],
        ["a", "b", "", "c"],
        ["a", "", "", "b"],
        ["", "a", "b"],
        ["a", "b", "", ""],
        ["a"],
        ["", "", ""],
        ["a", "", "b", "", "c"],
        ["", "a", "", "b", ""],
        ["a", "b", "", "", "c"],
    ],
)
def test_folded_scalar_matches_pyyaml(indicator: str, pat: list[str]) -> None:
    """For every blank-line / chomp combination the fold equals pyyaml's output.

    pyyaml is the reference implementation for the supported subset; the
    minimal parser must agree byte-for-byte on folded scalars.
    """
    doc = _doc(indicator, pat)
    expected = real_yaml.safe_load(doc)["key"]
    parsed = safe_load(doc)
    assert parsed is not None
    assert parsed["key"] == expected


def test_literal_scalar_unaffected_by_folded_fix() -> None:
    """The literal (``|``) path still preserves line breaks verbatim.

    The fix touches only the folded branch; literal scalars must be unchanged.
    """
    out = safe_load("body: |\n  line1\n  line2\n")
    assert out == {"body": "line1\nline2\n"}


# ---------------------------------------------------------------------------
# Finding 141 — a FAILED integrity check must NOT be memoized as "verified"
# ---------------------------------------------------------------------------


def _arm_integrity_mismatch(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Arm the real verify_self_integrity code path, network-free.

    Resets the process flag, removes the pytest auto-bypass + skip env vars,
    and stubs the manifest fetch + added-file detector so the only signal is a
    single deliberate hash MISMATCH. Must be called from the test BODY — pytest
    re-sets ``PYTEST_CURRENT_TEST`` between fixture setup and the call phase, so
    deleting it in a fixture would not stick.
    """
    monkeypatch.setattr(pvh, "_VERIFIED_THIS_PROCESS", False, raising=False)
    # Defeat the in-test auto-bypass (env var PYTEST_CURRENT_TEST) and skip vars.
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.delenv("PLUGIN_SKIP_GITHUB_INTEGRITY", raising=False)
    monkeypatch.delenv("CPV_SKIP_GITHUB_INTEGRITY", raising=False)

    # A minimal plugin root with one real file that the manifest claims a WRONG
    # hash for.
    plugin_root = tmp_path / "plugin"
    (plugin_root / ".claude-plugin").mkdir(parents=True)
    (plugin_root / ".claude-plugin" / "plugin.json").write_text('{"name": "x", "version": "9.9.9"}', encoding="utf-8")
    tracked = plugin_root / "scripts" / "real_file.py"
    tracked.parent.mkdir(parents=True)
    tracked.write_text("print('hello')\n", encoding="utf-8")

    bad_manifest = {"hashed_files": {"scripts/real_file.py": "sha256:" + "0" * 64}}
    monkeypatch.setattr(pvh, "_fetch_github_manifest", lambda *_a, **_k: bad_manifest)
    # Isolate the signal to the hash mismatch — no spurious added-file findings.
    monkeypatch.setattr(pvh, "_detect_added_files", lambda *_a, **_k: [])
    return plugin_root


def test_mismatch_with_fail_false_does_not_memoize_success(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys
) -> None:
    """On a hash mismatch with fail_on_mismatch=False the failure is NOT cached.

    Guards the security property behind finding 141: ``_VERIFIED_THIS_PROCESS``
    means "verified OK". Setting it True on a mismatch would let a later strict
    gate short-circuit to True and silently pass a tampered install.
    """
    plugin_root = _arm_integrity_mismatch(monkeypatch, tmp_path)
    result = pvh.verify_self_integrity(plugin_root, fail_on_mismatch=False, quiet=True)
    assert result is False, "a hash mismatch must return False"
    assert pvh._VERIFIED_THIS_PROCESS is False, (
        "a FAILED verification must not be memoized as success — otherwise a subsequent strict gate would be bypassed"
    )


def test_strict_gate_after_failed_probe_still_fails(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys) -> None:
    """A strict gate after a failed soft probe must still detect the tamper.

    This is the concrete bypass that setting the flag on mismatch would create:
    probe (soft) → strict gate. Because the soft probe did NOT memoize success,
    the strict gate re-evaluates and exits(2) instead of returning True.
    """
    plugin_root = _arm_integrity_mismatch(monkeypatch, tmp_path)
    # 1. Soft probe detects the mismatch and returns False (no memoization).
    assert pvh.verify_self_integrity(plugin_root, fail_on_mismatch=False, quiet=True) is False
    # 2. Strict gate (the default for validator entry points) must NOT be
    #    short-circuited to True — it re-checks and exits non-zero.
    with pytest.raises(SystemExit) as exc:
        pvh.verify_self_integrity(plugin_root, fail_on_mismatch=True, quiet=True)
    assert exc.value.code == 2
