"""Tests for the Snyk Agent Scan wrapper (scripts/cpv_snyk_agent_scanner.py).

Every invariant is tested two-sided — the safe case AND its adversarial
control — because the wrapper's whole reason to exist is that the naive path
is unsafe:

  * invariant 1 (never a config file / only directories): build_scan_command
    REFUSES a file target and staging never emits an `.mcp.json`.
  * invariant 2 (--ci / --dangerously-run-mcp-servers banned): no assembled
    argv ever contains a FORBIDDEN_FLAG.
  * invariant 3 ("cannot check" != "clean"): a no-token / empty / unparseable
    run returns invoked=False and reports a WARNING, never a silent pass.
  * invariant 4 (staging is ephemeral, name-only, remaps to the real path):
    staged findings resolve back to the true component path + kind.

The subprocess is monkeypatched, so the suite is hermetic — no network, no
uvx download, no SNYK_TOKEN required.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import cpv_snyk_agent_scanner as sas  # noqa: E402
from cpv_validation_common import ValidationReport  # noqa: E402

# ---------------------------------------------------------------------------
# derive_severity — mirrors upstream agent_scan.printer.get_severity
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("issue", "expected"),
    [
        ({"code": "X001"}, "info"),  # X-prefix -> info
        ({"code": "E004", "extra_data": {"severity": "high"}}, "high"),  # real shape
        ({"code": "W007", "extra_data": {"severity": "high"}}, "high"),  # extra_data wins over W
        ({"code": "W008"}, "medium"),  # W-prefix fallback (no extra_data)
        ({"code": "E006"}, "high"),  # E-prefix fallback
        ({"code": "Q1"}, "info"),  # unknown prefix -> info
        ({"code": "E006", "extra_data": {"severity": "bogus"}}, "high"),  # invalid sev -> fallback, no crash
        ({"code": "X9", "extra_data": {"severity": "critical"}}, "info"),  # X short-circuits before extra_data
        ({"code": "C1", "extra_data": {"severity": "critical"}}, "critical"),
    ],
)
def test_derive_severity_matches_upstream(issue: dict[str, Any], expected: str) -> None:
    """Every documented get_severity branch resolves as upstream does."""
    assert sas.derive_severity(issue) == expected


def test_derive_severity_never_raises_on_bad_type() -> None:
    """Upstream RAISES on a non-str severity; we must NOT abort a whole scan."""
    # A non-string severity falls through to the code-prefix rule, not an exception.
    assert sas.derive_severity({"code": "E1", "extra_data": {"severity": 5}}) == "high"


# ---------------------------------------------------------------------------
# parse_findings — reference[0] -> entity path; defensive on junk
# ---------------------------------------------------------------------------


def _payload_one_entity(entity_path: str, issues: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        entity_path: {
            "path": entity_path,
            "servers": [{"name": "e", "server": {"path": entity_path + "/child", "type": "skill"}}],
            "issues": issues,
        }
    }


def test_parse_findings_resolves_reference_to_entity_path() -> None:
    payload = _payload_one_entity(
        "/plug/skills",
        [{"code": "E004", "message": "inject", "reference": [0, None], "extra_data": {"severity": "high"}}],
    )
    findings = sas.parse_findings(payload)
    assert len(findings) == 1
    assert findings[0].severity == "major"  # high -> major
    assert findings[0].code == "E004"
    assert findings[0].entity_path == "/plug/skills/child"


def test_parse_findings_global_issue_falls_back_to_scanned_path() -> None:
    """reference=None (a global issue) anchors to the scanned path, not a server."""
    payload = _payload_one_entity("/plug/skills", [{"code": "W001", "message": "g", "reference": None}])
    findings = sas.parse_findings(payload)
    assert findings[0].entity_path == "/plug/skills"


@pytest.mark.parametrize("junk", ["not json", "", b"\xff\xfe", "[1,2,3]", "42"])
def test_parse_findings_defensive_on_junk(junk: Any) -> None:
    """An unrecognised payload yields no findings rather than crashing."""
    assert sas.parse_findings(junk) == ()


def test_parse_findings_out_of_range_reference_is_safe() -> None:
    payload = {
        "/p": {
            "path": "/p",
            "servers": [],  # empty -> reference [0] is out of range
            "issues": [{"code": "E1", "message": "m", "reference": [0, None]}],
        }
    }
    findings = sas.parse_findings(payload)
    assert findings[0].entity_path == "/p"  # falls back to scanned path


def test_parse_scan_errors_surfaces_failures_but_not_successes() -> None:
    payload = {
        "/a": {"path": "/a", "issues": [], "error": {"is_failure": True, "message": "boom"}},
        "/b": {"path": "/b", "issues": [], "error": {"is_failure": False, "message": "note"}},
        "/c": {"path": "/c", "issues": [], "error": None},
    }
    errs = sas.parse_scan_errors(payload)
    assert any("boom" in e for e in errs)
    assert not any("note" in e for e in errs)  # is_failure False -> not surfaced


# ---------------------------------------------------------------------------
# build_scan_command — invariants 1 and 2
# ---------------------------------------------------------------------------


def test_build_scan_command_accepts_a_directory(tmp_path: Path) -> None:
    d = tmp_path / "skills"
    d.mkdir()
    cmd = sas.build_scan_command((d,))
    assert "--json" in cmd and "--skills" in cmd
    assert str(d) in cmd


def test_build_scan_command_refuses_a_file_target(tmp_path: Path) -> None:
    """A config file (or any file) as a target is the RCE vector — must refuse."""
    f = tmp_path / ".mcp.json"
    f.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="DIRECTORY"):
        sas.build_scan_command((f,))


def test_build_scan_command_refuses_empty_targets() -> None:
    """No targets => the scanner would walk the whole machine. Must refuse."""
    with pytest.raises(ValueError, match="no explicit targets"):
        sas.build_scan_command(())


def test_assembled_command_never_contains_a_forbidden_flag(tmp_path: Path) -> None:
    d = tmp_path / "skills"
    d.mkdir()
    cmd = sas.build_scan_command((d,))
    assert not (set(cmd) & sas.FORBIDDEN_FLAGS)


def test_forbidden_flags_are_the_execution_enabling_ones() -> None:
    """Regression-lock the ban list so nobody quietly drops one."""
    assert "--dangerously-run-mcp-servers" in sas.FORBIDDEN_FLAGS
    assert "--ci" in sas.FORBIDDEN_FLAGS


# ---------------------------------------------------------------------------
# native_skill_targets
# ---------------------------------------------------------------------------


def test_native_targets_prefers_skills_dir(tmp_path: Path) -> None:
    (tmp_path / "skills" / "a").mkdir(parents=True)
    assert sas.native_skill_targets(tmp_path) == (tmp_path / "skills",)


def test_native_targets_single_skill_repo_uses_root(tmp_path: Path) -> None:
    (tmp_path / "SKILL.md").write_text("---\nname: x\ndescription: y\n---\n", encoding="utf-8")
    assert sas.native_skill_targets(tmp_path) == (tmp_path,)


def test_native_targets_none_when_no_skills(tmp_path: Path) -> None:
    (tmp_path / "agents").mkdir()
    assert sas.native_skill_targets(tmp_path) == ()


# ---------------------------------------------------------------------------
# build_staged_tree — invariant 4 (and 1: never stages a config)
# ---------------------------------------------------------------------------


def _make_plugin(tmp_path: Path) -> Path:
    (tmp_path / "agents").mkdir()
    (tmp_path / "agents" / "a.md").write_text("---\nname: a\ndescription: d\n---\nbody-A\n", encoding="utf-8")
    (tmp_path / "commands" / "git").mkdir(parents=True)
    (tmp_path / "commands" / "git" / "c.md").write_text("plain command body-C\n", encoding="utf-8")
    (tmp_path / "rules").mkdir()
    (tmp_path / "rules" / "r.md").write_text("# rule\nbody-R with no frontmatter\n", encoding="utf-8")
    (tmp_path / "hooks").mkdir()
    (tmp_path / "hooks" / "h.sh").write_text("#!/bin/sh\necho hi\n", encoding="utf-8")
    # a config file that MUST NEVER be staged
    (tmp_path / ".mcp.json").write_text('{"mcpServers": {}}', encoding="utf-8")
    (tmp_path / "hooks" / "hooks.json").write_text("{}", encoding="utf-8")
    return tmp_path


def test_staged_tree_covers_every_surface(tmp_path: Path) -> None:
    plugin = _make_plugin(tmp_path)
    root, manifest = sas.build_staged_tree(plugin)
    try:
        assert root is not None
        rels = {rel for rel, _kind in manifest.values()}
        kinds = {kind for _rel, kind in manifest.values()}
        assert "agents/a.md" in rels
        assert "commands/git/c.md" in rels  # nested command
        assert "rules/r.md" in rels
        assert "hooks/h.sh" in rels
        assert kinds == {"agent", "command", "rule", "hook"}
    finally:
        if root is not None:
            import shutil

            shutil.rmtree(root, ignore_errors=True)


def test_staged_tree_never_stages_a_config_file(tmp_path: Path) -> None:
    """Invariant 1: no `.mcp.json` (or hooks.json) may appear in the staging tree."""
    plugin = _make_plugin(tmp_path)
    root, manifest = sas.build_staged_tree(plugin)
    try:
        assert root is not None
        # No manifest entry points at a config file...
        assert not any(rel.endswith(".json") for rel, _ in manifest.values())
        # ...and no `.mcp.json` exists anywhere under the staging root.
        assert not list(root.rglob(".mcp.json"))
        assert not list(root.rglob("hooks.json"))
    finally:
        if root is not None:
            import shutil

            shutil.rmtree(root, ignore_errors=True)


def test_staged_tree_synthetic_manifest_has_valid_frontmatter(tmp_path: Path) -> None:
    """A staged SKILL.md must carry name+description (the scanner rejects otherwise),
    even when the source component had no frontmatter at all."""
    plugin = _make_plugin(tmp_path)
    root, manifest = sas.build_staged_tree(plugin)
    try:
        assert root is not None
        rule_folder = next(Path(k) for k, (rel, _) in manifest.items() if rel == "rules/r.md")
        text = (rule_folder / "SKILL.md").read_text(encoding="utf-8")
        assert text.startswith("---\nname: ")
        assert "description:" in text.split("---", 2)[1]
        # The original body is wrapped verbatim (invariant 4).
        assert "body-R with no frontmatter" in text
    finally:
        if root is not None:
            import shutil

            shutil.rmtree(root, ignore_errors=True)


def test_staged_tree_hook_script_copied_beside_synthetic_manifest(tmp_path: Path) -> None:
    plugin = _make_plugin(tmp_path)
    root, manifest = sas.build_staged_tree(plugin)
    try:
        assert root is not None
        hook_folder = next(Path(k) for k, (rel, _) in manifest.items() if rel == "hooks/h.sh")
        assert (hook_folder / "SKILL.md").is_file()
        assert (hook_folder / "h.sh").read_text(encoding="utf-8").startswith("#!/bin/sh")
    finally:
        if root is not None:
            import shutil

            shutil.rmtree(root, ignore_errors=True)


def test_staged_tree_none_when_nothing_to_stage(tmp_path: Path) -> None:
    (tmp_path / "skills" / "s").mkdir(parents=True)  # only a native skill, nothing to stage
    root, manifest = sas.build_staged_tree(tmp_path)
    assert root is None
    assert manifest == {}


# ---------------------------------------------------------------------------
# resolve_component — remap (invariant 4)
# ---------------------------------------------------------------------------


def test_resolve_component_remaps_staged_finding_to_real_path() -> None:
    manifest = {"/tmp/stage/agent__x": ("agents/x.md", "agent")}
    finding = sas.SnykFinding("major", "E004", "m", "/tmp/stage/agent__x", {})
    assert sas.resolve_component(finding, Path("/plug"), manifest) == ("agents/x.md", "agent")


def test_resolve_component_native_skill_relativised() -> None:
    finding = sas.SnykFinding("major", "E006", "m", "/plug/skills/real", {})
    assert sas.resolve_component(finding, Path("/plug"), {}) == ("skills/real", "skill")


# ---------------------------------------------------------------------------
# run_snyk_agent_scan — invariant 3 (cannot check != clean)
# ---------------------------------------------------------------------------


def test_run_without_token_is_skipped_not_clean(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("SNYK_TOKEN", raising=False)
    res = sas.run_snyk_agent_scan(tmp_path)
    assert res.invoked is False
    assert "SNYK_TOKEN" in res.skipped_reason
    assert res.findings == ()


def test_run_empty_stdout_is_skipped_not_clean(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The core invariant-3 trap: no token -> exit 1, empty output. Must NOT parse to a green pass."""
    (tmp_path / "skills" / "s").mkdir(parents=True)
    (tmp_path / "skills" / "s" / "SKILL.md").write_text("---\nname: s\ndescription: d\n---\n", encoding="utf-8")
    monkeypatch.setenv("SNYK_TOKEN", "dummy")
    monkeypatch.setattr(sas.shutil, "which", lambda _n: "/usr/bin/uvx")

    def fake_run(*_a: Any, **_k: Any) -> Any:
        class C:
            stdout = ""  # empty, as the tokenless tool actually emits
            stderr = ""
            returncode = 1

        return C()

    monkeypatch.setattr(sas.subprocess, "run", fake_run)
    res = sas.run_snyk_agent_scan(tmp_path)
    assert res.invoked is False
    assert "NOT SCANNED" in res.skipped_reason


def test_run_unparseable_stdout_is_skipped(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    (tmp_path / "skills" / "s").mkdir(parents=True)
    (tmp_path / "skills" / "s" / "SKILL.md").write_text("---\nname: s\ndescription: d\n---\n", encoding="utf-8")
    monkeypatch.setenv("SNYK_TOKEN", "dummy")
    monkeypatch.setattr(sas.shutil, "which", lambda _n: "/usr/bin/uvx")

    def fake_run(*_a: Any, **_k: Any) -> Any:
        class C:
            stdout = "this is not json"
            stderr = ""
            returncode = 0

        return C()

    monkeypatch.setattr(sas.subprocess, "run", fake_run)
    res = sas.run_snyk_agent_scan(tmp_path)
    assert res.invoked is False
    assert "NOT SCANNED" in res.skipped_reason


def test_run_timeout_is_skipped_with_reason(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    (tmp_path / "skills" / "s").mkdir(parents=True)
    (tmp_path / "skills" / "s" / "SKILL.md").write_text("---\nname: s\ndescription: d\n---\n", encoding="utf-8")
    monkeypatch.setenv("SNYK_TOKEN", "dummy")
    monkeypatch.setattr(sas.shutil, "which", lambda _n: "/usr/bin/uvx")

    def fake_run(*_a: Any, **_k: Any) -> Any:
        raise subprocess.TimeoutExpired(cmd="snyk", timeout=1)

    monkeypatch.setattr(sas.subprocess, "run", fake_run)
    res = sas.run_snyk_agent_scan(tmp_path)
    assert res.invoked is False
    assert "timed out" in res.skipped_reason


def test_run_no_scannable_surface_is_skipped(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A plugin with no skills and nothing stageable skips with a reason, never inventing a target."""
    monkeypatch.setenv("SNYK_TOKEN", "dummy")
    monkeypatch.setattr(sas.shutil, "which", lambda _n: "/usr/bin/uvx")
    res = sas.run_snyk_agent_scan(tmp_path)  # empty plugin dir
    assert res.invoked is False
    assert "nothing for Snyk Agent Scan to do" in res.skipped_reason


# ---------------------------------------------------------------------------
# report_findings — WARNING on skip, remap on report, filter on real path
# ---------------------------------------------------------------------------


def test_report_skip_emits_warning_not_pass() -> None:
    res = sas.SnykScanResult(invoked=False, findings=(), skipped_reason="no token", scan_errors=())
    rep = ValidationReport()
    n = sas.report_findings(res, Path("/plug"), rep)
    assert n == 0
    assert rep.has_warning  # visible, not a silent clean (has_warning is a property)


def test_report_remaps_staged_finding_and_tags_kind() -> None:
    res = sas.SnykScanResult(
        invoked=True,
        findings=(sas.SnykFinding("major", "E004", "inject", "/tmp/stage/agent__x", {}),),
        skipped_reason="",
        scan_errors=(),
        staging_manifest={"/tmp/stage/agent__x": ("agents/x.md", "agent")},
    )
    rep = ValidationReport()
    n = sas.report_findings(res, Path("/plug"), rep)
    assert n == 1
    r = rep.results[0]
    assert r.file == "agents/x.md"  # real path, not the temp path
    assert "· agent" in (r.message or "")  # kind tag present for a staged surface


def test_report_should_skip_filters_on_the_real_path() -> None:
    res = sas.SnykScanResult(
        invoked=True,
        findings=(sas.SnykFinding("major", "E004", "m", "/tmp/stage/agent__x", {}),),
        skipped_reason="",
        scan_errors=(),
        staging_manifest={"/tmp/stage/agent__x": ("agents/x.md", "agent")},
    )
    rep = ValidationReport()
    # The filter is handed the REAL remapped path, so it can act on it.
    seen: list[str] = []

    def should_skip(path: str, _line: int | None) -> bool:
        seen.append(path)
        return path == "agents/x.md"

    n = sas.report_findings(res, Path("/plug"), rep, should_skip=should_skip)
    assert n == 0
    assert seen == ["agents/x.md"]  # filter saw the real path, not the temp folder


def test_report_scan_error_surfaces_as_warning() -> None:
    res = sas.SnykScanResult(invoked=True, findings=(), skipped_reason="", scan_errors=("skills/x: analysis failed",))
    rep = ValidationReport()
    sas.report_findings(res, Path("/plug"), rep)
    assert rep.has_warning


# ---------------------------------------------------------------------------
# Review fixes — staging failure cleanup/skip, safe env parse, global-issue
# labelling, opt-in scanner classification.
# ---------------------------------------------------------------------------


def test_env_int_defaults_on_garbage(monkeypatch: pytest.MonkeyPatch) -> None:
    """A malformed timeout env var must not raise (it is parsed at import)."""
    monkeypatch.setenv("CPV_SNYK_SCAN_TIMEOUT_S", "not-a-number")
    assert sas._env_int("CPV_SNYK_SCAN_TIMEOUT_S", 600) == 600
    monkeypatch.setenv("CPV_SNYK_SCAN_TIMEOUT_S", "")
    assert sas._env_int("CPV_SNYK_SCAN_TIMEOUT_S", 600) == 600
    monkeypatch.setenv("CPV_SNYK_SCAN_TIMEOUT_S", "42")
    assert sas._env_int("CPV_SNYK_SCAN_TIMEOUT_S", 600) == 42


def test_staged_tree_cleans_up_and_reraises_on_write_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A filesystem error during staging must tear down the temp tree AND
    propagate (never leak the dir, never silently drop the component)."""
    (tmp_path / "agents").mkdir()
    (tmp_path / "agents" / "a.md").write_text("body", encoding="utf-8")

    created: list[Path] = []
    real_mkdtemp = sas.tempfile.mkdtemp

    def spy_mkdtemp(*a: Any, **k: Any) -> str:
        d = real_mkdtemp(*a, **k)
        created.append(Path(d).resolve())
        return d

    monkeypatch.setattr(sas.tempfile, "mkdtemp", spy_mkdtemp)

    def boom(*_a: Any, **_k: Any) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(sas, "_write_synthetic_skill_manifest", boom)

    with pytest.raises(OSError):
        sas.build_staged_tree(tmp_path)
    # The temp dir it created must be gone (no leak).
    assert created and not created[0].exists()


def test_run_returns_visible_skip_on_staging_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    (tmp_path / "agents").mkdir()
    (tmp_path / "agents" / "a.md").write_text("body", encoding="utf-8")
    monkeypatch.setenv("SNYK_TOKEN", "dummy")
    monkeypatch.setattr(sas.shutil, "which", lambda _n: "/usr/bin/uvx")
    monkeypatch.setattr(sas, "build_staged_tree", lambda _p: (_ for _ in ()).throw(OSError("perm denied")))
    res = sas.run_snyk_agent_scan(tmp_path)
    assert res.invoked is False
    assert "staging failed" in res.skipped_reason


def test_resolve_component_labels_global_staged_issue() -> None:
    """A global issue anchored at the staging ROOT (parent of the manifest keys)
    must not leak the temp path."""
    manifest = {"/tmp/stage/agent__x": ("agents/x.md", "agent")}
    finding = sas.SnykFinding("minor", "W001", "global", "/tmp/stage", {})
    assert sas.resolve_component(finding, Path("/plug"), manifest) == ("<staged instruction surfaces>", "staged")


def test_snyk_is_classified_optional_for_install_exit() -> None:
    """snyk-agent-scan is opt-in: its install failure must not fail the batch exit."""
    import cpv_install_scanners as cis  # noqa: PLC0415

    assert "snyk-agent-scan" in cis._OPTIONAL_SCANNERS
