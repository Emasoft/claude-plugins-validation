"""Tests for ``validate_canonical_pipeline_drift`` (v2.66.0).

This validator emits a WARNING per plugin that has any of its canonical
pipeline files (publish.py, pre-push, ci.yml, …) drift from the latest CPV
template. The plugin-fixer agent picks up the WARNING and offers
`/cpv-upgrade-plugin` to migrate.

Tests cover:
- Stale pre-push hook produces a WARNING.
- Verbatim canonical pre-push hook produces no WARNING.
- CPV self-scan is skipped silently.
- Missing pipeline files do not trigger drift WARNINGs (validate_pipeline_readiness already handles those).
- Multiple drifts produce ONE WARNING **per drifted file** so the reader
  can see WHICH file drifted in the per-finding `file=` column (issue #21
  ask #3).
- Each WARNING body embeds a `difflib.unified_diff` with `@@` hunk
  markers so the reader can see WHICH LINES drifted, not just which
  file.
- The diff is capped at 10 hunks / 200 lines per file with a
  truncation marker so a pathologically large drift cannot flood the
  report.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = REPO_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from generate_plugin_repo import PluginParams  # noqa: E402


def _make_minimal_plugin(tmp_path: Path, name: str = "test-plugin") -> Path:
    """Create a plugin folder with just the manifest. No pipeline files."""
    p = tmp_path / name
    (p / ".claude-plugin").mkdir(parents=True)
    (p / ".claude-plugin" / "plugin.json").write_text(
        json.dumps(
            {
                "name": name,
                "version": "0.1.0",
                "description": "test",
                "author": {"name": "Tester", "email": "t@example.com"},
                "repository": f"https://github.com/Emasoft/{name}",
            }
        )
    )
    return p


def _make_canonical_pre_push(plugin_root: Path) -> None:
    """Generate the canonical pre-push hook content into the plugin."""
    from generate_plugin_repo import gen_pre_push_hook  # type: ignore[import]
    from standardize_plugin import _params_from_manifest  # type: ignore[import]

    manifest = json.loads((plugin_root / ".claude-plugin" / "plugin.json").read_text())
    params = _params_from_manifest(manifest)

    hooks_dir = plugin_root / "git-hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    (hooks_dir / "pre-push").write_text(gen_pre_push_hook(params))


def test_stale_pre_push_hook_emits_warning(tmp_path: Path) -> None:
    """A pre-push hook that doesn't match the canonical template → WARNING."""
    from cpv_validation_common import ValidationReport
    from validate_plugin import validate_canonical_pipeline_drift

    plugin_root = _make_minimal_plugin(tmp_path)
    (plugin_root / "git-hooks").mkdir()
    (plugin_root / "git-hooks" / "pre-push").write_text(
        "#!/usr/bin/env bash\n# Stale pre-push hook from before TRDD-bbff5bc5\necho 'old'\n"
    )

    report = ValidationReport()
    validate_canonical_pipeline_drift(plugin_root, report)

    warnings = [r for r in report.results if r.level == "WARNING"]
    assert len(warnings) == 1, f"Expected 1 WARNING, got {len(warnings)}: {warnings}"
    assert "RC-PIPELINE-DRIFT-001" in warnings[0].message
    assert "git-hooks/pre-push" in warnings[0].message
    assert "/cpv-upgrade-plugin" in warnings[0].message


def test_canonical_pre_push_hook_emits_no_warning(tmp_path: Path) -> None:
    """A byte-identical canonical hook → no drift, no warning."""
    from cpv_validation_common import ValidationReport
    from validate_plugin import validate_canonical_pipeline_drift

    plugin_root = _make_minimal_plugin(tmp_path)
    _make_canonical_pre_push(plugin_root)

    report = ValidationReport()
    validate_canonical_pipeline_drift(plugin_root, report)

    drift_warnings = [r for r in report.results if r.level == "WARNING" and "RC-PIPELINE-DRIFT-001" in r.message]
    assert not drift_warnings, f"Canonical hook should produce no drift WARNING, got {drift_warnings}"


def test_cpv_self_scan_is_skipped(tmp_path: Path) -> None:
    """When the plugin under test IS CPV itself, drift checking is bypassed."""
    from cpv_validation_common import ValidationReport
    from validate_plugin import validate_canonical_pipeline_drift

    # Use the actual CPV repo root — its plugin.json says
    # name == "claude-plugins-validation".
    report = ValidationReport()
    validate_canonical_pipeline_drift(REPO_ROOT, report)

    drift_warnings = [r for r in report.results if r.level == "WARNING" and "RC-PIPELINE-DRIFT-001" in r.message]
    assert not drift_warnings, f"CPV self-scan must skip drift check, got {drift_warnings}"


def test_missing_pipeline_files_do_not_trigger_drift(tmp_path: Path) -> None:
    """A plugin without any pipeline files yet should produce zero drift WARNINGs.

    `validate_pipeline_readiness` already flags MISSING files; emitting drift
    on top would be redundant noise.
    """
    from cpv_validation_common import ValidationReport
    from validate_plugin import validate_canonical_pipeline_drift

    plugin_root = _make_minimal_plugin(tmp_path)
    # No pipeline files written.

    report = ValidationReport()
    validate_canonical_pipeline_drift(plugin_root, report)

    assert not [r for r in report.results if r.level == "WARNING"], (
        f"Missing files should produce 0 drift WARNINGs, got {report.results}"
    )


def test_multiple_drifts_emit_per_file_warnings(tmp_path: Path) -> None:
    """Two drifted files → ONE warning PER drifted file (issue #21 ask #3).

    The validator was reworked in v2.74+ to emit one finding per drifted
    file with an embedded unified diff so the reader can immediately see
    WHICH lines drifted (not just WHICH files). This replaces the older
    behaviour of one consolidated warning naming every drifted file.
    """
    from cpv_validation_common import ValidationReport
    from validate_plugin import validate_canonical_pipeline_drift

    plugin_root = _make_minimal_plugin(tmp_path)
    (plugin_root / "git-hooks").mkdir()
    (plugin_root / "git-hooks" / "pre-push").write_text("#!/usr/bin/env bash\necho stale\n")
    (plugin_root / "scripts").mkdir()
    (plugin_root / "scripts" / "publish.py").write_text("# stale publish.py\n")

    report = ValidationReport()
    validate_canonical_pipeline_drift(plugin_root, report)

    drift_warnings = [r for r in report.results if r.level == "WARNING" and "RC-PIPELINE-DRIFT-001" in r.message]
    assert len(drift_warnings) == 2, f"Expected one WARNING per drifted file (2 total), got {len(drift_warnings)}"
    files = {w.file for w in drift_warnings}
    assert "git-hooks/pre-push" in files
    assert "scripts/publish.py" in files


def test_drift_warning_includes_unified_diff_with_line_numbers(tmp_path: Path) -> None:
    """Issue #21 ask #3: the WARNING body MUST embed a unified diff with @@
    line markers so the user sees WHICH lines drifted, not just the file.
    """
    from cpv_validation_common import ValidationReport
    from validate_plugin import validate_canonical_pipeline_drift

    plugin_root = _make_minimal_plugin(tmp_path)
    (plugin_root / "git-hooks").mkdir()
    (plugin_root / "git-hooks" / "pre-push").write_text(
        "#!/usr/bin/env bash\n# obviously not the canonical hook\necho 'stale'\n"
    )

    report = ValidationReport()
    validate_canonical_pipeline_drift(plugin_root, report)

    drift_warnings = [r for r in report.results if r.level == "WARNING" and "RC-PIPELINE-DRIFT-001" in r.message]
    assert len(drift_warnings) == 1
    msg = drift_warnings[0].message
    # Unified-diff signature — fromfile/tofile headers + @@ hunk markers.
    assert "canonical/git-hooks/pre-push" in msg, "unified diff header missing"
    assert "plugin/git-hooks/pre-push" in msg, "unified diff header missing"
    assert "@@" in msg, "unified diff hunk marker @@ missing — line numbers not embedded"
    # The drifted line content should appear in the diff body so the reader
    # can grep-correlate the citation.
    assert "stale" in msg


def test_drift_warning_caps_diff_at_max_hunks_or_lines(tmp_path: Path) -> None:
    """A pathologically large drift must be truncated, not flooded into the
    report. Cap is documented at 10 hunks or 200 diff lines per file.
    """
    from cpv_validation_common import ValidationReport
    from validate_plugin import validate_canonical_pipeline_drift

    plugin_root = _make_minimal_plugin(tmp_path)
    (plugin_root / "git-hooks").mkdir()
    # 500-line bogus hook so the cap kicks in.
    (plugin_root / "git-hooks" / "pre-push").write_text(
        "#!/usr/bin/env bash\n" + "\n".join(f"echo line-{i}" for i in range(500)) + "\n"
    )

    report = ValidationReport()
    validate_canonical_pipeline_drift(plugin_root, report)

    drift_warnings = [r for r in report.results if r.level == "WARNING" and "RC-PIPELINE-DRIFT-001" in r.message]
    assert len(drift_warnings) == 1
    msg = drift_warnings[0].message
    # The truncation footer is the marker that the cap fired.
    assert "truncated" in msg, "Large drift must be truncated with a marker, not flooded into the report"


def test_drift_recommendation_text_only_claims_features_the_templates_emit() -> None:
    """Issue #118 defect 1: the RC-PIPELINE-DRIFT-001 remediation text must NOT
    advertise features the generated templates don't actually contain.

    The text previously promised "SHA-pinned actions, actionlint + commitlint
    gates, macOS matrix, env-sanitized run blocks" — but the templates didn't
    ship those, so the migration over-promised. Both recommendation strings
    (the "behind canon → migrate" branch and the "ahead of canon" softer
    branch) live as literals in ``validate_canonical_pipeline_drift``; this
    guard extracts whatever feature phrases either string names and asserts
    each is verifiably present in the corresponding generated template.
    """
    import re as _re

    from generate_plugin_repo import gen_ci_yml, gen_notify_marketplace_yml, gen_release_yml

    src = (REPO_ROOT / "scripts" / "validate_plugin.py").read_text(encoding="utf-8")
    # The full recommendation text is what the validator can put after
    # "{recommendation}" — grab the whole function body region so both
    # branches' literals are in scope for the phrase scan.
    fn_start = src.index("def validate_canonical_pipeline_drift")
    fn_body = src[fn_start : src.index("\ndef ", fn_start)]
    assert "Canon now bundles" in fn_body, "migrate-branch recommendation text missing"

    p = PluginParams(
        name="test-plugin",
        description="t",
        author="X",
        author_email="x@x",
        python_version="3.12",
        github_owner="Emasoft",
        marketplace="test-marketplace",
    )
    ci = gen_ci_yml(p)
    rel = gen_release_yml(p)
    notify = gen_notify_marketplace_yml(p)

    # Each feature phrase the recommendation text may name → the verifiable
    # template fact that MUST back it. A phrase present in the source text
    # without its backing fact is the #118-d1 over-promise regression.
    checks: list[tuple[str, bool]] = []

    def _all_pinned(*contents: str) -> bool:
        for content in contents:
            for line in content.splitlines():
                stripped = line.strip()
                if stripped.startswith(("- uses:", "uses:")):
                    sha = stripped.rsplit("@", 1)[1].split()[0]
                    if not _re.fullmatch(r"[0-9a-f]{40}", sha):
                        return False
        return True

    if "SHA-pinned actions" in fn_body:
        checks.append(("SHA-pinned actions across ci/release/notify", _all_pinned(ci, rel, notify)))
    if "timeout-minutes" in fn_body:
        checks.append(("timeout-minutes in ci+release+notify", all("timeout-minutes:" in c for c in (ci, rel, notify))))
    if "actionlint" in fn_body:
        checks.append(("actionlint in ci.yml", "rhysd/actionlint" in ci))
    if "commitlint" in fn_body:
        checks.append(("commitlint in ci.yml", "wagoid/commitlint-github-action" in ci))
    if "macOS test matrix" in fn_body:
        checks.append(("macOS test matrix in ci.yml", "macos-latest" in ci))
    if "env-sanitized" in fn_body:
        # ci.yml + release.yml bind github.* into env: for run blocks; notify too.
        checks.append(("env-sanitized run blocks", all("env:" in c for c in (ci, rel, notify))))
    if "SBOM" in fn_body:
        checks.append(("SBOM in release.yml", ("anchore/sbom-action" in rel or "attest-sbom" in rel)))
    if "build-provenance attestation" in fn_body:
        checks.append(("build-provenance attestation in release.yml", "actions/attest-build-provenance" in rel))
    if "SHA256SUMS" in fn_body:
        checks.append(("SHA256SUMS in release.yml", "SHA256SUMS" in rel))

    assert checks, "recommendation text named no recognizable feature phrases"
    failed = [label for label, ok in checks if not ok]
    assert not failed, f"RC-PIPELINE-DRIFT-001 text over-promises (no backing template feature): {failed}"
