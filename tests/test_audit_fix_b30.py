"""Regression tests for audit batch B30 (full-audit 20260531_122130+0200).

Covers two code fixes verified and applied by the B30 fix agent:

* Audit #90 — ``validate_project_scope.py`` fed a whole MCP server **URL**
  into ``looks_like_secret_key_name`` (a *key-name* substring heuristic),
  producing a high rate of false MINORs on benign endpoints whose host/path
  merely *contains* ``token``/``auth``/``secret``/``credential`` while
  *missing* the genuine leak shape (a credential in the URL userinfo). The
  fix replaces that call with a URL-aware ``_url_embeds_credential`` check.
  These are TWO-SIDED tests: benign URLs stay clean AND real embedded
  credentials are still caught (the userinfo case is now a security
  *improvement* — the old code missed it entirely).

* Audit #91 — ``validate_settings_marketplace.py`` emitted a spurious
  ``PASSED`` ("... source has valid URL") alongside a ``MAJOR`` whenever a
  ``git-subdir`` source had a valid ``url`` but was missing its required
  ``path``. The fix gates the PASSED on the source object accruing no
  blocking finding, mirroring the existing ``settings``-branch idiom.

All probes set ``CPV_SCAN_CACHE=0`` semantics by importing the validators
directly (no scan cache is consulted on these in-process calls).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

from cpv_validation_common import ValidationReport  # noqa: E402
from validate_project_scope import (  # noqa: E402
    _url_embeds_credential,
    validate_mcp_json_project_scope,
)
from validate_settings_marketplace import validate_source_object  # noqa: E402

# ---------------------------------------------------------------------------
# Audit #90 — URL-aware credential detection (validate_project_scope.py)
# ---------------------------------------------------------------------------

# Benign MCP endpoint URLs. NONE of these embeds a credential — they merely
# contain a secret-ish *word* in the host or path, which the old key-name
# heuristic false-matched. Each MUST be clean now.
_BENIGN_URLS = [
    "https://api.githubcopilot.com/mcp/",
    "https://mcp.example.com/oauth/token",  # 'token' is a route name, not a secret
    "https://example.com/auth/callback",  # 'auth' route
    "https://api.example.com/v1/credentials",  # 'credential' route
    "https://secrets.example.com/mcp",  # 'secret' in host
    "https://example.com/api_key-docs",  # 'api_key' substring in path
    "https://example.com/sse",
    "http://localhost:3000/mcp",
    "https://user@example.com/mcp",  # userinfo with NO password → benign
    "https://example.com/mcp?verbose=token",  # secret WORD as a value, non-secret key
    "https://example.com/mcp?retries=3",
]

# URLs that genuinely embed a credential. Each MUST be flagged.
_CREDENTIAL_URLS = [
    "https://user:ghp_AbCdEfGhIjKlMnOpQrStUvWxYz012345@example.com/mcp",  # userinfo PAT
    "https://token:abc123secretvalue@host/path",  # userinfo password
    "https://example.com/mcp?api_key=sk-ant-AbCdEfGhIjKlMnOpQrStUv",  # secret-shaped value
    "https://example.com/mcp?access_token=xoxb-1234567890-abcdef",  # secret-key + long value
    "https://example.com/mcp?secret=supersecretvalue123",  # secret-key + 8+ char value
]


def test_benign_urls_do_not_flag_as_credential():
    """No benign MCP URL is mis-detected as embedding a credential (audit #90 FP side)."""
    flagged = [u for u in _BENIGN_URLS if _url_embeds_credential(u)]
    assert flagged == [], f"benign URLs wrongly flagged as embedding a credential: {flagged}"


def test_credential_bearing_urls_are_detected():
    """Every URL that actually embeds a credential is detected (audit #90 TP side)."""
    missed = [u for u in _CREDENTIAL_URLS if not _url_embeds_credential(u)]
    assert missed == [], f"URLs that embed a credential were NOT detected: {missed}"


def test_userinfo_password_is_the_recovered_security_case():
    """A ``user:secret@host`` userinfo credential — missed by the old key-name heuristic — is now caught.

    This is the guard that would have caught the original bug: the previous
    ``looks_like_secret_key_name(url)`` returned False for this shape because
    a raw PAT in the userinfo never resembles a field-name keyword.
    """
    leak = "https://deploy:ghp_ZzYyXxWwVvUuTtSsRrQqPpOoNnMmLl998877@git.example.com/mcp"
    benign_no_password = "https://deploy@git.example.com/mcp"
    assert _url_embeds_credential(leak) is True
    assert _url_embeds_credential(benign_no_password) is False


def _write_mcp(tmp_path: Path, servers: dict) -> Path:
    mcp_path = tmp_path / ".mcp.json"
    mcp_path.write_text(json.dumps({"mcpServers": servers}), encoding="utf-8")
    return mcp_path


def test_mcp_json_benign_url_produces_no_minor(tmp_path: Path):
    """End-to-end: a .mcp.json with a benign secret-worded URL yields no MINOR (audit #90)."""
    mcp_path = _write_mcp(
        tmp_path,
        {"docs": {"type": "http", "url": "https://mcp.example.com/oauth/token"}},
    )
    report = ValidationReport()
    validate_mcp_json_project_scope(mcp_path, report)
    minors = [r.message for r in report.results if r.level == "MINOR"]
    assert minors == [], f"benign MCP URL produced unexpected MINOR(s): {minors}"
    assert any(r.level == "PASSED" for r in report.results), "expected a clean PASSED for the benign .mcp.json"


def test_mcp_json_userinfo_credential_produces_minor(tmp_path: Path):
    """End-to-end: a .mcp.json URL with userinfo credential is flagged MINOR (audit #90 security side)."""
    mcp_path = _write_mcp(
        tmp_path,
        {"leaky": {"type": "http", "url": "https://u:ghp_AbCdEfGhIjKlMnOpQrStUvWxYz012345@example.com/mcp"}},
    )
    report = ValidationReport()
    validate_mcp_json_project_scope(mcp_path, report)
    assert any(r.level == "MINOR" and "embeds a credential" in r.message for r in report.results), (
        f"expected a MINOR flagging the embedded credential, got: {[(r.level, r.message) for r in report.results]}"
    )


# ---------------------------------------------------------------------------
# Audit #91 — no spurious PASSED alongside MAJOR (validate_settings_marketplace.py)
# ---------------------------------------------------------------------------


def _levels(report: ValidationReport) -> list[str]:
    return [r.level for r in report.results]


def test_git_subdir_missing_path_emits_no_spurious_passed():
    """git-subdir with a valid url but missing required 'path' → MAJOR only, NO PASSED (audit #91)."""
    report = ValidationReport()
    validate_source_object(
        {"source": "git-subdir", "url": "https://example.com/repo.git"},
        "mymkpl",
        report,
        "settings.json",
    )
    levels = _levels(report)
    assert "MAJOR" in levels, f"expected a MAJOR for the missing 'path' field, got {levels}"
    # The guard that would have caught the original bug: a source object that
    # accrued a blocking finding must NOT also receive a green 'valid URL' line.
    assert "PASSED" not in levels, (
        "git-subdir missing 'path' wrongly emitted a spurious PASSED alongside the MAJOR: "
        f"{[(r.level, r.message) for r in report.results]}"
    )


def test_git_subdir_wrong_typed_path_emits_no_spurious_passed():
    """git-subdir with valid url but non-string 'path' → MAJOR only, NO PASSED (audit #91)."""
    report = ValidationReport()
    validate_source_object(
        {"source": "git-subdir", "url": "https://example.com/repo.git", "path": 123},
        "mymkpl",
        report,
        "settings.json",
    )
    levels = _levels(report)
    assert "MAJOR" in levels, f"expected a MAJOR for the wrong-typed 'path', got {levels}"
    assert "PASSED" not in levels, (
        "git-subdir with wrong-typed 'path' wrongly emitted a spurious PASSED: "
        f"{[(r.level, r.message) for r in report.results]}"
    )


def test_valid_git_subdir_still_passes():
    """A fully valid git-subdir (url + path) still emits its PASSED and no MAJOR (no over-correction)."""
    report = ValidationReport()
    validate_source_object(
        {"source": "git-subdir", "url": "https://example.com/repo.git", "path": "sub/dir"},
        "mymkpl",
        report,
        "settings.json",
    )
    levels = _levels(report)
    assert "MAJOR" not in levels, f"valid git-subdir unexpectedly produced a MAJOR: {levels}"
    assert any(r.level == "PASSED" and "valid URL" in r.message for r in report.results), (
        f"valid git-subdir should still emit its 'valid URL' PASSED, got {[(r.level, r.message) for r in report.results]}"
    )


def test_plain_url_and_git_sources_still_pass():
    """The plain 'url' and 'git' source types (only require 'url') still PASS when valid."""
    for stype in ("url", "git"):
        report = ValidationReport()
        validate_source_object(
            {"source": stype, "url": "https://example.com/marketplace.json"},
            "mymkpl",
            report,
            "settings.json",
        )
        levels = _levels(report)
        assert "MAJOR" not in levels, f"valid '{stype}' source unexpectedly produced MAJOR: {levels}"
        assert any(r.level == "PASSED" and "valid URL" in r.message for r in report.results), (
            f"valid '{stype}' source should emit its PASSED, got {[(r.level, r.message) for r in report.results]}"
        )
