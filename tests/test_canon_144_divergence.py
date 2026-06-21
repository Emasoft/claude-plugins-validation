"""Two-sided tests for the intentional-divergence drift mechanism (issue #144Ba).

Agent C3 of TRDD-034b4061. The upgrade flow (`standardize --force-templates`)
was regressing plugins that DELIBERATELY customized a shared-canon file
(cliff.toml, .markdownlint.json, …). Two coupled changes are tested here:

1. ``resolve_intentional_divergence`` (cpv_pipeline_profile) reads the OPTIONAL
   ``cpv.pipeline.intentional_divergence`` manifest list of repo-relative paths.

2. ``validate_canonical_pipeline_drift`` (validate_plugin), for a file the
   plugin declares divergent:
     * DROPS the "/cpv-upgrade-plugin / --force-templates" upgrade nudge,
     * still emits an auditable INFORMATIONAL note (visible, non-blocking),
   while the SAME drifted file WITHOUT the declaration still gets the
   (softened) upgrade nudge as a WARNING.

The two-sided contract per file:
  * declared → no upgrade-nudge WARNING; an INFO note IS present; non-blocking.
  * not-declared → the (softened) upgrade nudge IS present (WARNING).
  * the ahead-of-canon "would DOWNGRADE" guidance for an UNMARKED file is
    unchanged.

A drifted ``cliff.toml`` with arbitrary plain content carries NO hardening
marker on either diff side, so ``_classify_drift_direction`` returns ``plain``
→ the BEHIND/PLAIN branch (the only one that emits the upgrade nudge). That is
exactly the branch the declaration must suppress.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = REPO_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def _make_plugin(tmp_path: Path, *, divergence: list[str] | None = None, name: str = "div-plugin") -> Path:
    """A minimal plugin manifest, optionally declaring intentional_divergence."""
    p = tmp_path / name
    (p / ".claude-plugin").mkdir(parents=True)
    manifest: dict[str, object] = {
        "name": name,
        "version": "0.1.0",
        "description": "test",
        "author": {"name": "Tester", "email": "t@example.com"},
        "repository": f"https://github.com/Emasoft/{name}",
    }
    if divergence is not None:
        manifest["cpv"] = {"pipeline": {"intentional_divergence": divergence}}
    (p / ".claude-plugin" / "plugin.json").write_text(json.dumps(manifest))
    return p


def _write_stale_cliff(plugin_root: Path) -> None:
    """A drifted, hardening-marker-free cliff.toml → drift direction `plain`."""
    (plugin_root / "cliff.toml").write_text(
        "# hand-tuned changelog config — deliberately divergent\n"
        "[changelog]\nheader = 'My Custom Changelog'\n[git]\nconventional_commits = true\n"
    )


def _drift_results(plugin_root: Path):
    from cpv_validation_common import ValidationReport
    from validate_plugin import validate_canonical_pipeline_drift

    report = ValidationReport()
    validate_canonical_pipeline_drift(plugin_root, report)
    return report.results


def _drift_findings_for(plugin_root: Path, rel: str):
    """All RC-PIPELINE-DRIFT-001 results that target `rel`."""
    return [r for r in _drift_results(plugin_root) if "RC-PIPELINE-DRIFT-001" in r.message and (r.file == rel or rel in r.message)]


# ── resolve_intentional_divergence (the reader) ──────────────────────────────


def test_resolve_divergence_absent_is_empty(tmp_path: Path) -> None:
    """No manifest key → empty set (the no-behavior-change default)."""
    from cpv_pipeline_profile import resolve_intentional_divergence

    p = _make_plugin(tmp_path, divergence=None)
    assert resolve_intentional_divergence(p) == frozenset()


def test_resolve_divergence_reads_list(tmp_path: Path) -> None:
    """A declared list of paths is returned verbatim."""
    from cpv_pipeline_profile import resolve_intentional_divergence

    p = _make_plugin(tmp_path, divergence=["cliff.toml", ".markdownlint.json"])
    assert resolve_intentional_divergence(p) == frozenset({"cliff.toml", ".markdownlint.json"})


def test_resolve_divergence_normalizes_and_filters(tmp_path: Path) -> None:
    """Backslashes → `/`; non-string and empty entries are dropped."""
    from cpv_pipeline_profile import resolve_intentional_divergence

    p = _make_plugin(tmp_path, divergence=[".github\\workflows\\ci.yml", "", "  ", 42, "cliff.toml"])  # type: ignore[list-item]
    assert resolve_intentional_divergence(p) == frozenset({".github/workflows/ci.yml", "cliff.toml"})


def test_resolve_divergence_non_list_is_empty(tmp_path: Path) -> None:
    """A non-list value (e.g. a string) yields the empty set, never a crash."""
    from cpv_pipeline_profile import resolve_intentional_divergence

    p = tmp_path / "bad"
    (p / ".claude-plugin").mkdir(parents=True)
    (p / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": "bad", "version": "0.1.0", "cpv": {"pipeline": {"intentional_divergence": "cliff.toml"}}})
    )
    assert resolve_intentional_divergence(p) == frozenset()


# ── validate_canonical_pipeline_drift: the two-sided nudge contract ──────────


def test_declared_divergent_file_drops_upgrade_nudge(tmp_path: Path) -> None:
    """A drifted file LISTED in intentional_divergence → NO upgrade nudge."""
    p = _make_plugin(tmp_path, divergence=["cliff.toml"])
    _write_stale_cliff(p)

    findings = _drift_findings_for(p, "cliff.toml")
    assert findings, "the divergent cliff.toml must still surface a drift finding (visible, not suppressed)"
    blob = "\n".join(r.message for r in findings)
    # The upgrade RECOMMENDATION is withheld for a declared-divergent file. The
    # note may still NAME the commands when explaining WHY the nudge is withheld
    # ("force-templating it via `/cpv-upgrade-plugin` ... would REGRESS it"), so
    # we assert against the imperative recommendation phrasing the nudge
    # branches use, not the bare command tokens.
    assert "Run `/cpv-upgrade-plugin`" not in blob
    assert "--fix --force-templates`) to migrate" not in blob  # the BEHIND/PLAIN nudge phrasing
    assert "Do NOT run `--force-templates`" not in blob  # the AHEAD-branch phrasing
    assert "not recommending an upgrade" in blob


def test_declared_divergent_file_emits_informational_note(tmp_path: Path) -> None:
    """The declared-divergent file's finding is an INFORMATIONAL note (visible)."""
    p = _make_plugin(tmp_path, divergence=["cliff.toml"])
    _write_stale_cliff(p)

    findings = _drift_findings_for(p, "cliff.toml")
    assert len(findings) == 1, f"expected exactly one note for the divergent file, got {findings}"
    note = findings[0]
    assert note.level == "INFO", f"a declared divergence must be an INFO note, not {note.level}"
    assert "intentional divergence" in note.message
    assert "cpv.pipeline.intentional_divergence" in note.message


def test_declared_divergent_file_is_non_blocking(tmp_path: Path) -> None:
    """The INFO note for a declared divergence does not block --strict."""
    from cpv_validation_common import ValidationReport
    from validate_plugin import validate_canonical_pipeline_drift

    p = _make_plugin(tmp_path, divergence=["cliff.toml"])
    _write_stale_cliff(p)

    report = ValidationReport()
    validate_canonical_pipeline_drift(p, report)
    # The drift check on its own must not produce a --strict-blocking result.
    assert report.exit_code_strict() == 0


def test_undeclared_same_file_keeps_upgrade_nudge(tmp_path: Path) -> None:
    """The SAME drifted file WITHOUT the declaration → the upgrade nudge IS present."""
    p = _make_plugin(tmp_path, divergence=None)
    _write_stale_cliff(p)

    findings = _drift_findings_for(p, "cliff.toml")
    assert findings, "an undeclared drifted file must surface a drift WARNING"
    warnings = [r for r in findings if r.level == "WARNING"]
    assert warnings, f"undeclared drift must be a WARNING, got {[r.level for r in findings]}"
    blob = "\n".join(r.message for r in warnings)
    assert "/cpv-upgrade-plugin" in blob or "--force-templates" in blob


def test_undeclared_nudge_is_softened(tmp_path: Path) -> None:
    """The (kept) upgrade nudge now carries the regression CAUTION + the manifest key."""
    p = _make_plugin(tmp_path, divergence=None)
    _write_stale_cliff(p)

    blob = "\n".join(r.message for r in _drift_findings_for(p, "cliff.toml"))
    assert "CAUTION" in blob
    assert "cpv.pipeline.intentional_divergence" in blob
    assert "REGRESS" in blob


def test_declaration_only_affects_listed_file(tmp_path: Path) -> None:
    """Declaring cliff.toml does NOT suppress the nudge on an unlisted drifted file."""
    p = _make_plugin(tmp_path, divergence=["cliff.toml"])
    _write_stale_cliff(p)
    # A second drifted canon file that is NOT declared.
    (p / "git-hooks").mkdir()
    (p / "git-hooks" / "pre-push").write_text("#!/usr/bin/env bash\necho 'stale hook'\n")

    cliff = _drift_findings_for(p, "cliff.toml")
    hook = _drift_findings_for(p, "git-hooks/pre-push")
    assert all(r.level == "INFO" for r in cliff), "declared cliff.toml → INFO note only"
    assert any(r.level == "WARNING" for r in hook), "undeclared pre-push → upgrade WARNING"
    hook_blob = "\n".join(r.message for r in hook)
    assert "/cpv-upgrade-plugin" in hook_blob or "--force-templates" in hook_blob


def test_ahead_of_canon_downgrade_message_unchanged(tmp_path: Path) -> None:
    """An UNMARKED ahead-of-canon file still gets the 'would DOWNGRADE' guidance.

    A drifted file carrying a hardening marker on the PLUGIN (+) side and none
    on the canon (-) side classifies as `ahead` → the AHEAD branch, which says
    'Do NOT run --force-templates: it would DOWNGRADE this file.' This branch is
    correct and must be untouched by the #144Ba change.
    """
    # A pre-push hook is the easiest canon file to make `ahead`: add a line
    # carrying a hardening marker the canonical hook does not contain.
    from generate_plugin_repo import gen_pre_push_hook  # type: ignore[import]
    from standardize_plugin import _params_from_manifest  # type: ignore[import]

    p = _make_plugin(tmp_path, divergence=None)
    manifest = json.loads((p / ".claude-plugin" / "plugin.json").read_text())
    params = _params_from_manifest(manifest)
    canonical = gen_pre_push_hook(params)
    # Append an extra hardening line so the plugin is strictly AHEAD on a `+` line.
    (p / "git-hooks").mkdir()
    (p / "git-hooks" / "pre-push").write_text(canonical + "\n# extra: git push --atomic guard added locally\n")

    findings = _drift_findings_for(p, "git-hooks/pre-push")
    assert findings, "an ahead-of-canon drift must still surface"
    blob = "\n".join(r.message for r in findings)
    assert "DOWNGRADE" in blob, f"ahead-of-canon must keep the 'would DOWNGRADE' message: {blob}"
    # And it must NOT recommend the upgrade.
    assert "Run `/cpv-upgrade-plugin`" not in blob


def test_declared_divergent_ahead_file_still_only_informational(tmp_path: Path) -> None:
    """Even an ahead-of-canon file, if DECLARED divergent, is just an INFO note.

    The declaration short-circuits before the ahead/behind branches, so a
    declared file is always the single auditable INFO note (no WARNING at all).
    This proves the declaration's precedence is consistent.
    """
    from generate_plugin_repo import gen_pre_push_hook  # type: ignore[import]
    from standardize_plugin import _params_from_manifest  # type: ignore[import]

    p = _make_plugin(tmp_path, divergence=["git-hooks/pre-push"])
    manifest = json.loads((p / ".claude-plugin" / "plugin.json").read_text())
    params = _params_from_manifest(manifest)
    canonical = gen_pre_push_hook(params)
    (p / "git-hooks").mkdir()
    (p / "git-hooks" / "pre-push").write_text(canonical + "\n# extra: git push --atomic guard added locally\n")

    findings = _drift_findings_for(p, "git-hooks/pre-push")
    assert len(findings) == 1 and findings[0].level == "INFO"
    assert "intentional divergence" in findings[0].message
