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
        json.dumps(pj),
        encoding="utf-8",
    )
    if gitmodules_text is not None:
        (plugin / ".gitmodules").write_text(gitmodules_text, encoding="utf-8")
    return plugin


# ── parse_gitmodules_urls ──────────────────────────────────────────────────────


def test_parse_gitmodules_urls_returns_empty_when_file_absent(tmp_path):
    plugin = _make_plugin(tmp_path)
    assert cvg.parse_gitmodules_urls(plugin) == []


def test_parse_gitmodules_urls_extracts_name_url_path(tmp_path):
    plugin = _make_plugin(
        tmp_path,
        gitmodules_text="""\
[submodule "tests"]
\tpath = dev/tests
\turl = https://github.com/Emasoft/demo-tests.git
[submodule "design"]
\tpath = dev/design
\turl = https://github.com/Emasoft/demo-design.git
""",
    )
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
            "name": "demo",
            "version": "0.1.0",
            "description": "x",
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
            "name": "demo",
            "version": "0.1.0",
            "description": "x",
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


def test_validate_gitmodules_default_rule_accepts_same_owner(tmp_path, monkeypatch):
    # No explicit allowlist; default rule (same-owner-only) kicks in. The submodule
    # owner equals the parent repo owner, so it is accepted — no personal carve-out.
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


def test_validate_gitmodules_default_rule_rejects_cross_owner_no_carveout(tmp_path, monkeypatch):
    # Regression for the removed `OR Emasoft` carve-out (issue #175 follow-up): on a
    # THIRD-PARTY plugin (parent owner != Emasoft), an Emasoft-owned submodule URL must
    # be REJECTED by the same-owner-only default rule. Under the old carve-out this
    # returned findings == []; a universal validator must carry no personal allowlist.
    monkeypatch.setattr(cvg, "_read_remote_owner", lambda root: "acme")
    plugin = _make_plugin(
        tmp_path,
        gitmodules_text="""\
[submodule "tests"]
\tpath = dev/tests
\turl = https://github.com/Emasoft/demo-tests.git
""",
    )
    findings = cvg.validate_gitmodules(plugin)
    assert any(f.code == "STRIP-G013" for f in findings)


def test_validate_gitmodules_opt_out_emits_warning(tmp_path):
    plugin = _make_plugin(
        tmp_path,
        plugin_json={
            "name": "demo",
            "version": "0.1.0",
            "description": "x",
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


# ── validate_strip_gitmodules: fail-closed on import failure (TRDD-793ac32a) ──


def test_validate_strip_gitmodules_fails_closed_when_helper_missing(tmp_path: Path, monkeypatch) -> None:
    """When ``cpv_validate_gitmodules`` cannot be imported (helper missing,
    shadowed, or shipped from a stripped CPV release), the orchestrator
    MUST emit a CRITICAL with code RC-STRIP-GITMODULES-IMPORT-FAILED —
    NEVER degrade to a soft warning.

    Rationale: the .gitmodules URL allowlist is a CRITICAL-tier security
    check (TRDD-793ac32a §2.2). A missing security validator is itself a
    security failure — silently passing the plugin would turn the
    validator into a fail-open path that an attacker can exploit by
    deleting / shadowing the helper module on disk before invoking CPV.
    """
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    import validate_plugin
    from cpv_validation_common import ValidationReport

    plugin = _make_plugin(
        tmp_path,
        gitmodules_text="""\
[submodule "tests"]
\tpath = dev/tests
\turl = https://github.com/Emasoft/cpv-tests.git
""",
    )

    # Force the import inside validate_strip_gitmodules to fail. The function
    # does the import lazily inside its try/except, so we shadow the module
    # in sys.modules with None to make the next ``from cpv_validate_gitmodules
    # import validate_gitmodules`` raise ImportError. This is the canonical
    # way to simulate "helper not installed" without removing files on disk.
    monkeypatch.setitem(sys.modules, "cpv_validate_gitmodules", None)

    report = ValidationReport()
    validate_plugin.validate_strip_gitmodules(plugin, report)

    criticals = [r for r in report.results if r.level == "CRITICAL"]
    assert criticals, f"Missing helper MUST emit CRITICAL — got {[r.level for r in report.results]}"
    msg = criticals[0].message
    assert "RC-STRIP-GITMODULES-IMPORT-FAILED" in msg, (
        f"CRITICAL must carry the RC code so the fixer agent can route it; got: {msg}"
    )
    assert "refusing to validate" in msg, (
        "Message MUST state explicitly that CPV is refusing to validate, "
        "so the user understands this is fail-closed (not a transient warning)."
    )
    # And no soft WARNING should be present — a hidden warning would defeat
    # the fail-closed guarantee even if the CRITICAL is also emitted.
    soft_warnings = [r for r in report.results if r.level == "WARNING" and "STRIP-GITMODULES" in r.message]
    assert not soft_warnings, (
        "fail-CLOSED means CRITICAL only — any WARNING would silently pass "
        "in non-strict mode and defeat the whole point. Got: "
        f"{[w.message for w in soft_warnings]}"
    )


def test_validate_strip_gitmodules_noop_when_gitmodules_absent(tmp_path: Path) -> None:
    """No ``.gitmodules`` file → no findings (the validator is a no-op,
    not a fail-closed). Pin so a future import-tightening regression
    can't accidentally make every plugin emit CRITICAL.
    """
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    import validate_plugin
    from cpv_validation_common import ValidationReport

    plugin = _make_plugin(tmp_path)  # no gitmodules_text → no .gitmodules
    report = ValidationReport()
    validate_plugin.validate_strip_gitmodules(plugin, report)

    assert not report.results, (
        f"validate_strip_gitmodules must be silent when .gitmodules is absent. Got: {report.results}"
    )
