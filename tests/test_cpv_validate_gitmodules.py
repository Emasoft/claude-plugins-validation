"""Tests for scripts/cpv_validate_gitmodules.py — TRDD-793ac32a §2.2.

Pin the URL allowlist + URL-shape rules so the strip-dev-parts feature
cannot regress to PSS's "no defense against .gitmodules tampering" gap.

All git/subprocess interactions are stubbed via monkeypatch so the tests
never touch real GitHub or local git state.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import cpv_validate_gitmodules as cvg  # noqa: E402

# ── Helpers ────────────────────────────────────────────────────────────────────


def _make_plugin(
    tmp_path: Path,
    plugin_json: dict | None = None,
    gitmodules_text: str | None = None,
) -> Path:
    """Create a minimal plugin tree at tmp_path/demo with optional .gitmodules."""
    plugin = tmp_path / "demo"
    (plugin / ".claude-plugin").mkdir(parents=True)
    pj = plugin_json or {"name": "demo", "version": "0.1.0", "description": "x"}
    (plugin / ".claude-plugin" / "plugin.json").write_text(
        json.dumps(pj), encoding="utf-8",
    )
    if gitmodules_text is not None:
        (plugin / ".gitmodules").write_text(gitmodules_text, encoding="utf-8")
    return plugin


# ── parse_gitmodules_urls ──────────────────────────────────────────────────────


def test_parse_gitmodules_urls_returns_empty_when_file_absent(tmp_path):
    plugin = _make_plugin(tmp_path)
    assert cvg.parse_gitmodules_urls(plugin) == []


def test_parse_gitmodules_urls_extracts_name_url_path(tmp_path):
    plugin = _make_plugin(tmp_path, gitmodules_text="""\
[submodule "tests"]
\tpath = dev/tests
\turl = https://github.com/Emasoft/demo-tests.git
[submodule "design"]
\tpath = dev/design
\turl = https://github.com/Emasoft/demo-design.git
""")
    entries = cvg.parse_gitmodules_urls(plugin)
    assert len(entries) == 2
    names = {e[0] for e in entries}
    assert names == {"tests", "design"}
    urls = {e[1] for e in entries}
    assert "https://github.com/Emasoft/demo-tests.git" in urls
    assert "https://github.com/Emasoft/demo-design.git" in urls


# ── URL-shape validation ───────────────────────────────────────────────────────


def test_url_shape_rejects_empty():
    ok, reason = cvg._validate_url_shape("")
    assert ok is False
    assert "empty" in reason


def test_url_shape_rejects_userinfo():
    ok, reason = cvg._validate_url_shape("https://attacker@github.com/Emasoft/x.git")
    assert ok is False
    assert "user" in reason.lower()


def test_url_shape_rejects_path_traversal():
    ok, reason = cvg._validate_url_shape("https://github.com/../etc/passwd")
    assert ok is False
    assert "path-traversal" in reason or ".." in reason


def test_url_shape_rejects_backslash():
    ok, reason = cvg._validate_url_shape("https://github.com\\evil/x.git")
    assert ok is False
    assert "backslash" in reason or "newline" in reason


def test_url_shape_rejects_http():
    ok, reason = cvg._validate_url_shape("http://github.com/Emasoft/x.git")
    assert ok is False
    assert "scheme" in reason and "http" in reason


def test_url_shape_rejects_file_scheme():
    ok, reason = cvg._validate_url_shape("file:///etc/passwd")
    assert ok is False
    assert "scheme" in reason


def test_url_shape_accepts_https_github():
    ok, _ = cvg._validate_url_shape("https://github.com/Emasoft/cpv-tests.git")
    assert ok is True


def test_url_shape_accepts_ssh():
    ok, _ = cvg._validate_url_shape("git@github.com:Emasoft/cpv-tests.git")
    assert ok is True


# ── Allowlist match ───────────────────────────────────────────────────────────


def test_allowlist_match_exact():
    assert cvg._matches_allowlist(
        "https://github.com/Emasoft/cpv-tests.git",
        ["https://github.com/Emasoft/cpv-tests.git"],
    )


def test_allowlist_match_glob():
    assert cvg._matches_allowlist(
        "https://github.com/Emasoft/cpv-tests.git",
        ["https://github.com/Emasoft/*.git"],
    )


def test_allowlist_no_match():
    assert not cvg._matches_allowlist(
        "https://github.com/attacker/x.git",
        ["https://github.com/Emasoft/*"],
    )


# ── End-to-end validate_gitmodules ────────────────────────────────────────────


def test_validate_gitmodules_no_file_no_findings(tmp_path):
    plugin = _make_plugin(tmp_path)
    assert cvg.validate_gitmodules(plugin) == []


def test_validate_gitmodules_passes_with_explicit_allowlist(tmp_path):
    plugin = _make_plugin(
        tmp_path,
        plugin_json={
            "name": "demo", "version": "0.1.0", "description": "x",
            "cpv": {
                "strip": {
                    "allowed_submodule_urls": ["https://github.com/Emasoft/*.git"],
                },
            },
        },
        gitmodules_text="""\
[submodule "tests"]
\tpath = dev/tests
\turl = https://github.com/Emasoft/demo-tests.git
""",
    )
    findings = cvg.validate_gitmodules(plugin)
    assert findings == []


def test_validate_gitmodules_rejects_alien_owner_with_explicit_allowlist(tmp_path):
    plugin = _make_plugin(
        tmp_path,
        plugin_json={
            "name": "demo", "version": "0.1.0", "description": "x",
            "cpv": {
                "strip": {
                    "allowed_submodule_urls": ["https://github.com/Emasoft/*"],
                },
            },
        },
        gitmodules_text="""\
[submodule "tests"]
\tpath = dev/tests
\turl = https://github.com/attacker/demo-tests.git
""",
    )
    findings = cvg.validate_gitmodules(plugin)
    assert len(findings) == 1
    assert findings[0].severity == "CRITICAL"
    assert findings[0].code == "STRIP-G011"
    assert "attacker" in findings[0].message


def test_validate_gitmodules_rejects_userinfo_url(tmp_path):
    plugin = _make_plugin(
        tmp_path,
        gitmodules_text="""\
[submodule "tests"]
\tpath = dev/tests
\turl = https://attacker@github.com/Emasoft/x.git
""",
    )
    findings = cvg.validate_gitmodules(plugin)
    assert any(f.code == "STRIP-G010" for f in findings)


def test_validate_gitmodules_rejects_file_scheme(tmp_path):
    plugin = _make_plugin(
        tmp_path,
        gitmodules_text="""\
[submodule "tests"]
\tpath = dev/tests
\turl = file:///etc/passwd
""",
    )
    findings = cvg.validate_gitmodules(plugin)
    assert any(f.code == "STRIP-G010" for f in findings)


def test_validate_gitmodules_default_rule_accepts_emasoft(tmp_path, monkeypatch):
    # No explicit allowlist; default rule kicks in. Stub remote owner.
    monkeypatch.setattr(cvg, "_read_remote_owner", lambda root: "Emasoft")
    plugin = _make_plugin(
        tmp_path,
        gitmodules_text="""\
[submodule "tests"]
\tpath = dev/tests
\turl = https://github.com/Emasoft/demo-tests.git
""",
    )
    findings = cvg.validate_gitmodules(plugin)
    assert findings == []


def test_validate_gitmodules_default_rule_rejects_alien(tmp_path, monkeypatch):
    monkeypatch.setattr(cvg, "_read_remote_owner", lambda root: "Emasoft")
    plugin = _make_plugin(
        tmp_path,
        gitmodules_text="""\
[submodule "tests"]
\tpath = dev/tests
\turl = https://github.com/attacker/x.git
""",
    )
    findings = cvg.validate_gitmodules(plugin)
    assert any(f.code == "STRIP-G013" for f in findings)


def test_validate_gitmodules_opt_out_emits_warning(tmp_path):
    plugin = _make_plugin(
        tmp_path,
        plugin_json={
            "name": "demo", "version": "0.1.0", "description": "x",
            "cpv": {
                "strip": {
                    "require_url_allowlist": False,
                },
            },
        },
        gitmodules_text="""\
[submodule "tests"]
\tpath = dev/tests
\turl = https://github.com/anything/x.git
""",
    )
    findings = cvg.validate_gitmodules(plugin)
    assert len(findings) == 1
    assert findings[0].severity == "WARNING"
    assert findings[0].code == "STRIP-G014"


def test_main_returns_zero_when_no_gitmodules(tmp_path, monkeypatch, capsys):
    plugin = _make_plugin(tmp_path)
    monkeypatch.chdir(plugin)
    rc = cvg.main([str(plugin)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "OK" in out


def test_main_returns_one_on_critical(tmp_path, monkeypatch, capsys):
    plugin = _make_plugin(
        tmp_path,
        gitmodules_text="""\
[submodule "tests"]
\tpath = dev/tests
\turl = file:///etc/passwd
""",
    )
    rc = cvg.main([str(plugin)])
    assert rc == 1
    err = capsys.readouterr().err
    assert "CRITICAL" in err
    assert "STRIP-G010" in err


# ── Owner extraction edge cases ───────────────────────────────────────────────


def test_owner_of_https():
    assert cvg._owner_of("https://github.com/Emasoft/cpv-tests.git") == "Emasoft"


def test_owner_of_ssh_scp_style():
    assert cvg._owner_of("git@github.com:Emasoft/cpv-tests.git") == "Emasoft"


def test_owner_of_ssh_url_style():
    assert cvg._owner_of("ssh://git@github.com/Emasoft/cpv-tests.git") == "Emasoft"


def test_owner_of_non_github():
    assert cvg._owner_of("https://gitlab.com/x/y.git") is None
