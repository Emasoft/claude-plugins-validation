"""TRDD-FK9Y6NCL — generate_marketplace_repo.py on the README plugin-table canon.

The generator now emits the canonical `scripts/render_readme_table.py`, a README
carrying the PLUGIN-VERSIONS block, an update workflow that renders it before the
change-check, and a `--check` drift gate.

Real filesystem, real subprocesses, no mocks: every test scaffolds a marketplace
with the actual generator and runs the actual emitted scripts and shell.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = REPO_ROOT / "templates" / "scripts" / "render_readme_table.py"

scripts_dir = REPO_ROOT / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

import generate_marketplace_repo as gmr  # noqa: E402
import setup_marketplace_automation as sma  # noqa: E402

START = "<!-- PLUGIN-VERSIONS-START -->"
END = "<!-- PLUGIN-VERSIONS-END -->"

# The exact hardened stop condition the emitted update_catalog.py carries (R3
# guard). The negative control below mutates THIS text away to reconstruct the
# pre-hardening scan, so a respelling must break the test loudly rather than turn
# the mutation into a silent no-op.
HARDENED_STOP = """            if line.strip() == PLUGIN_VERSIONS_START or (
                line.startswith("## ") and line.strip() != "## Plugins"
            ):"""
UNHARDENED_STOP = """            if line.startswith("## ") and line.strip() != "## Plugins":"""


def _scaffold(tmp_path: Path, *, plugins: list[str] | None = None, github_owner: str = "demo-org") -> Path:
    """Scaffold a real marketplace and return its root."""
    target = tmp_path / "mp"
    rc = gmr.generate_marketplace_repo(
        target,
        "demo-mp",
        "Demo Org",
        "Demo marketplace",
        github_owner,
        list(plugins or []),
        dry_run=False,
    )
    assert rc == 0
    return target


def _block(text: str) -> str:
    """The PLUGIN-VERSIONS block, markers included."""
    return text[text.index(START) : text.index(END) + len(END)]


def _run(argv: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, cwd=str(cwd), capture_output=True, text=True, timeout=60)


def _set_plugins(root: Path, plugins: list[dict]) -> None:
    mj = root / ".claude-plugin" / "marketplace.json"
    data = json.loads(mj.read_text(encoding="utf-8"))
    data["plugins"] = plugins
    mj.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------
# The emitted renderer is the canonical one, byte for byte
# --------------------------------------------------------------------------


def test_emitted_renderer_is_byte_identical_to_the_template(tmp_path: Path) -> None:
    """The scaffold's renderer must equal templates/scripts/render_readme_table.py.

    Two generators now ship this script — this one and
    setup_marketplace_automation.py's copy step — and a marketplace scaffolded
    either way has to behave identically. Byte parity is the only assertion that
    holds: "both mention render_readme_table" passes on two files that disagree
    about what the script does.
    """
    root = _scaffold(tmp_path)
    emitted = root / "scripts" / "render_readme_table.py"
    assert emitted.is_file()
    assert emitted.read_text(encoding="utf-8") == TEMPLATE.read_text(encoding="utf-8")


def test_emitted_renderer_is_executable(tmp_path: Path) -> None:
    root = _scaffold(tmp_path)
    assert (root / "scripts" / "render_readme_table.py").stat().st_mode & 0o111


def test_local_mode_also_emits_the_renderer(tmp_path: Path) -> None:
    """A local marketplace has no CI but still carries the generated block.

    Emitting the block without the script that rewrites it would leave a table
    nobody can regenerate.
    """
    root = _scaffold(tmp_path, github_owner="")
    assert (root / "scripts" / "render_readme_table.py").is_file()
    assert START in (root / "README.md").read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# The scaffold passes its own --check gate — empty AND populated
# --------------------------------------------------------------------------


@pytest.mark.parametrize("plugins", [[], ["demo-org/alpha"], ["demo-org/alpha", "demo-org/beta"]])
def test_fresh_scaffold_passes_its_own_check_gate(tmp_path: Path, plugins: list[str]) -> None:
    """Running the emitted gate's own command on a fresh scaffold exits 0.

    This is the acceptance criterion measured by RUNNING the command the emitted
    validate-readme-table.yml runs, not by reading the YAML. The empty case is the
    one that used to be impossible: the renderer refused an empty plugin list, so a
    brand-new marketplace could never pass the gate it ships with.
    """
    root = _scaffold(tmp_path, plugins=plugins)
    result = _run([sys.executable, "scripts/render_readme_table.py", "--check"], root)
    assert result.returncode == 0, result.stdout + result.stderr


def test_local_scaffold_also_passes_the_check_gate(tmp_path: Path) -> None:
    root = _scaffold(tmp_path, github_owner="")
    result = _run([sys.executable, "scripts/render_readme_table.py", "--check"], root)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("github_owner", ["demo-org", ""])
def test_readme_carries_the_markers(tmp_path: Path, github_owner: str) -> None:
    text = (_scaffold(tmp_path, github_owner=github_owner) / "README.md").read_text(encoding="utf-8")
    assert START in text
    assert END in text
    assert "## Plugin Versions" in text


def test_versions_block_placeholder_row_has_four_cells(tmp_path: Path) -> None:
    """The versions table has FOUR columns; a 3-cell placeholder would be malformed.

    update_catalog.py's own `(no plugins yet)` row is 3 cells because its install
    table has 3 columns — copying it verbatim here is the mistake this pins.
    """
    text = (_scaffold(tmp_path) / "README.md").read_text(encoding="utf-8")
    row = next(ln for ln in _block(text).splitlines() if "no plugins yet" in ln)
    assert row.count("|") == 5, row  # 4 cells => 5 delimiters
    assert row == "| *(no plugins yet)* | | | |"


# --------------------------------------------------------------------------
# The emitted workflows
# --------------------------------------------------------------------------


def test_update_workflow_renders_the_table_before_the_change_check() -> None:
    """A README-only diff must still be committed, so the render step precedes the check.

    Compares STEP INDICES from the parsed YAML, not string offsets. The obvious
    `wf.index("render_readme_table.py") < wf.index("Check for changes")` measures the
    first mention of the script — which is inside this step's own COMMENT block, 284
    bytes ahead of the `run:` line. That version passes while measuring nothing about
    step order, and would keep passing if the invocation moved after the change-check.
    """
    yaml = pytest.importorskip("yaml")
    steps = yaml.safe_load(gmr._update_catalog_workflow("demo-mp"))["jobs"]["update-readme"]["steps"]
    names = [s.get("name") for s in steps]
    assert names.index("Regenerate README plugin-versions table") < names.index("Check for changes")
    # ... and the step really does invoke the renderer, not just name it in a comment.
    render = steps[names.index("Regenerate README plugin-versions table")]
    assert "render_readme_table.py" in render["run"]


def test_validate_readme_table_workflow_runs_the_check_gate(tmp_path: Path) -> None:
    yaml = pytest.importorskip("yaml")
    root = _scaffold(tmp_path)
    wf_path = root / ".github" / "workflows" / "validate-readme-table.yml"
    assert wf_path.is_file()
    doc = yaml.safe_load(wf_path.read_text(encoding="utf-8"))
    assert "jobs" in doc
    runs = [s.get("run", "") for job in doc["jobs"].values() for s in job["steps"]]
    assert any("render_readme_table.py --check" in r for r in runs)
    # Read-only: the gate must never be able to rewrite the repo it is checking.
    assert doc["permissions"] == {"contents": "read"}


def test_local_mode_emits_no_workflows_at_all(tmp_path: Path) -> None:
    """Control: the new gate must not smuggle CI into a local-only marketplace."""
    root = _scaffold(tmp_path, github_owner="")
    assert not (root / ".github" / "workflows").exists()


# --------------------------------------------------------------------------
# R5 — the unattended update workflow must not blank an emptied manifest
# --------------------------------------------------------------------------


def _run_render_step(root: Path) -> tuple[subprocess.CompletedProcess[str], str]:
    """Execute the update workflow's render step verbatim, in a real marketplace.

    Returns the completed process and the contents of its `$GITHUB_OUTPUT` file, so
    the step-output the commit gate keys on is measured rather than assumed.
    """
    yaml = pytest.importorskip("yaml")
    doc = yaml.safe_load(gmr._update_catalog_workflow("demo-mp"))
    step = next(
        s
        for s in doc["jobs"]["update-readme"]["steps"]
        if s.get("name") == "Regenerate README plugin-versions table"
    )
    # `python` is not guaranteed to exist on a dev box; the step's semantics are
    # what is under test, not the interpreter's name.
    run = step["run"].replace("python ", f"{sys.executable} ")
    outfile = root / "_github_output"
    outfile.write_text("", encoding="utf-8")
    proc = subprocess.run(
        ["bash", "-c", run],
        cwd=str(root),
        capture_output=True,
        text=True,
        timeout=60,
        env={**os.environ, "GITHUB_OUTPUT": str(outfile)},
    )
    return proc, outfile.read_text(encoding="utf-8")


def test_render_step_skips_and_warns_when_the_plugin_list_is_empty(tmp_path: Path) -> None:
    """An emptied manifest must NOT have its table blanked and committed by CI.

    The write step runs unattended on every marketplace.json push. If it blanked the
    table here, `--check` would then PASS (README and manifest agree) — the gate
    arriving after the damage it exists to catch. Skipping leaves the rows in place
    so `--check` goes red and a human looks.

    The load-bearing assertion is that the README is BYTE-UNCHANGED. Asserting only
    "no commit" would pass on an implementation that blanks the file and merely
    suppresses the commit, which defeats the whole guard.
    """
    root = _scaffold(tmp_path, plugins=["demo-org/alpha"])
    before = (root / "README.md").read_text(encoding="utf-8")
    assert "alpha" in _block(before)

    _set_plugins(root, [])
    result, outputs = _run_render_step(root)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "::warning::" in result.stdout
    assert "marketplace.json" in result.stdout
    assert "manifest_empty=true" in outputs
    assert (root / "README.md").read_text(encoding="utf-8") == before
    # And the desired end state: the gate is now RED, so the emptying is visible.
    check = _run([sys.executable, "scripts/render_readme_table.py", "--check"], root)
    assert check.returncode == 1


def test_render_step_runs_normally_on_a_populated_manifest(tmp_path: Path) -> None:
    """Positive control: the guard must not disable the step it guards."""
    root = _scaffold(tmp_path)
    _set_plugins(
        root,
        [
            {
                "name": "alpha",
                "description": "First",
                "version": "1.2.3",
                "source": {"source": "github", "repo": "demo-org/alpha"},
            }
        ],
    )
    result, outputs = _run_render_step(root)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "::warning::" not in result.stdout
    assert "manifest_empty=false" in outputs
    block = _block((root / "README.md").read_text(encoding="utf-8"))
    assert "alpha" in block
    assert "1.2.3" in block
    assert "no plugins yet" not in block


_ABSENT = object()  # parametrize sentinel: delete the `plugins` key entirely


@pytest.mark.parametrize(
    ("manifest_plugins", "expected_in_stderr"),
    [
        (_ABSENT, '"plugins" key'),
        ({"a": 1}, "dict"),
        ("abc", "str"),
        (None, "NoneType"),
    ],
)
def test_render_step_does_not_swallow_a_structurally_broken_manifest(
    tmp_path: Path, manifest_plugins: object, expected_in_stderr: str
) -> None:
    """A missing or wrongly-typed `plugins` must redden the job, never read as "empty".

    Both shapes "count zero" under any naive count, so a count-based skip would
    silently swallow exactly the two failures R1 promoted to loud exits — the same
    inversion, one layer up. The step must fall through to the renderer, whose shape
    guard then names the real reason and exits 1.
    """
    root = _scaffold(tmp_path, plugins=["demo-org/alpha"])
    before = (root / "README.md").read_text(encoding="utf-8")
    mj = root / ".claude-plugin" / "marketplace.json"
    data = json.loads(mj.read_text(encoding="utf-8"))
    if manifest_plugins is _ABSENT:
        data.pop("plugins")
    else:
        data["plugins"] = manifest_plugins
    mj.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    result, outputs = _run_render_step(root)

    assert result.returncode != 0, "a structurally broken manifest must fail the step"
    assert "::warning::" not in result.stdout, "must not report a structural failure as an empty list"
    # `manifest_empty=false` is written BEFORE the renderer fails, so a failed step
    # still publishes it. Harmless today (GitHub skips later steps in a failed job),
    # but it is the commit gate's input — a future `continue-on-error: true` here
    # would let the commit step run on this path.
    assert "manifest_empty=false" in outputs
    assert expected_in_stderr in result.stderr, result.stderr
    assert (root / "README.md").read_text(encoding="utf-8") == before


def test_commit_step_is_gated_off_on_the_empty_manifest_path() -> None:
    """R5's other half: skipping the renderer must skip the commit with it.

    Otherwise update_catalog.py's own blanked install table would still be committed
    unattended — the same damage by a different route.
    """
    yaml = pytest.importorskip("yaml")
    doc = yaml.safe_load(gmr._update_catalog_workflow("demo-mp"))
    steps = doc["jobs"]["update-readme"]["steps"]
    render = next(s for s in steps if s.get("name") == "Regenerate README plugin-versions table")
    commit = next(s for s in steps if s.get("name") == "Commit and push")
    assert render["id"] == "versions"
    assert "steps.versions.outputs.manifest_empty != 'true'" in commit["if"]
    # Control: the pre-existing change-check gate must survive alongside it.
    assert "steps.changes.outputs.has_changes == 'true'" in commit["if"]


# --------------------------------------------------------------------------
# R3 — update_catalog.py and the versions block must not collide
# --------------------------------------------------------------------------


def test_update_catalog_preserves_the_versions_block_end_to_end(tmp_path: Path) -> None:
    """The two renderers share a README; running one must not damage the other's block.

    The emitted README puts the versions block AFTER `## Plugins`, so this is the
    live path, not a hypothetical: update_catalog.py's region scan walks straight
    into the block on every run.
    """
    root = _scaffold(tmp_path, plugins=["demo-org/alpha"])
    before = (root / "README.md").read_text(encoding="utf-8")

    result = _run([sys.executable, "scripts/update_catalog.py", str(root)], root)
    assert result.returncode == 0, result.stdout + result.stderr

    after = (root / "README.md").read_text(encoding="utf-8")
    assert START in after and END in after
    assert _block(after) == _block(before)
    # The gate the block exists to satisfy is still green.
    check = _run([sys.executable, "scripts/render_readme_table.py", "--check"], root)
    assert check.returncode == 0, check.stdout + check.stderr


def test_the_two_renderers_are_idempotent_as_a_pair(tmp_path: Path) -> None:
    """The workflow runs BOTH on every push, so the pair must converge in one pass.

    One pass is not the variance: if either edit shifted a blank line the other's
    split depends on, pass two would differ from pass one and the --check gate in the
    OTHER workflow would go red on a repo that had just committed a clean render.
    """
    root = _scaffold(tmp_path, plugins=["demo-org/alpha"])
    catalog = [sys.executable, "scripts/update_catalog.py", str(root)]
    renderer = [sys.executable, "scripts/render_readme_table.py"]

    for argv in (catalog, renderer):
        assert _run(argv, root).returncode == 0
    after_first = (root / "README.md").read_text(encoding="utf-8")

    for argv in (catalog, renderer):
        assert _run(argv, root).returncode == 0
    assert (root / "README.md").read_text(encoding="utf-8") == after_first

    assert _run([sys.executable, "scripts/render_readme_table.py", "--check"], root).returncode == 0


def test_update_catalog_still_replaces_the_plugins_install_table(tmp_path: Path) -> None:
    """Positive control: the marker-stop must not stop the scan from doing its job."""
    root = _scaffold(tmp_path)
    assert "| (no plugins yet) | | |" in (root / "README.md").read_text(encoding="utf-8")

    _set_plugins(
        root,
        [{"name": "alpha", "description": "First", "source": {"source": "github", "repo": "demo-org/alpha"}}],
    )
    result = _run([sys.executable, "scripts/update_catalog.py", str(root)], root)
    assert result.returncode == 0, result.stdout + result.stderr

    after = (root / "README.md").read_text(encoding="utf-8")
    assert "[alpha](https://github.com/demo-org/alpha)" in after
    assert "| (no plugins yet) | | |" not in after  # the 3-cell install row is gone
    assert START in after  # ... and the block still survived


def test_unhardened_scan_destroys_the_marker(tmp_path: Path) -> None:
    """Negative control: without the marker-stop, the scan really does eat the marker.

    Reconstructed by MUTATING the emitted source rather than vendoring a copy, so
    the control tracks the shipped script. The `!=` assertion is mandatory: a later
    respelling of the stop condition would otherwise make the replacement a silent
    no-op and let this control pass while mutating nothing.
    """
    root = _scaffold(tmp_path, plugins=["demo-org/alpha"])
    original = (root / "scripts" / "update_catalog.py").read_text(encoding="utf-8")
    assert HARDENED_STOP in original, "the hardened stop condition was respelled — update HARDENED_STOP"
    mutated = original.replace(HARDENED_STOP, UNHARDENED_STOP)
    assert mutated != original, "mutation was a no-op — this control would pass vacuously"

    legacy = root / "scripts" / "legacy_update_catalog.py"
    legacy.write_text(mutated, encoding="utf-8")
    result = _run([sys.executable, str(legacy), str(root)], root)
    assert result.returncode == 0, result.stdout + result.stderr

    after = (root / "README.md").read_text(encoding="utf-8")
    assert START not in after, "the pre-hardening scan was expected to delete the START marker"
    # ... and that is exactly what makes the gate unfixable from the manifest side.
    check = _run([sys.executable, "scripts/render_readme_table.py", "--check"], root)
    assert check.returncode == 1
    assert "markers" in check.stderr


# --------------------------------------------------------------------------
# Legacy population — setup_marketplace_automation warns, never rewrites
# --------------------------------------------------------------------------


def test_setup_warns_about_an_unhardened_update_catalog(tmp_path: Path, capsys) -> None:
    """A marketplace scaffolded by an older CPV keeps its marker-destroying script.

    The fixture is a two-line stand-in, adequate only because detection is a
    substring test. If detection ever becomes structural, this fixture stops
    representing an un-hardened script and the test starts passing for free.
    """
    root = tmp_path / "legacy-mp"
    (root / "scripts").mkdir(parents=True)
    (root / "scripts" / "update_catalog.py").write_text(
        'if line.startswith("## "):\n    pass\n', encoding="utf-8"
    )
    assert sma.warn_if_update_catalog_is_unhardened(root) is True
    err = capsys.readouterr().err
    assert "update_catalog.py" in err
    assert "PLUGIN-VERSIONS-START" in err
    # WARN, never patch: the file is not CPV's to rewrite in someone else's repo.
    assert (root / "scripts" / "update_catalog.py").read_text(encoding="utf-8").startswith("if line.")


def test_setup_is_silent_for_a_hardened_update_catalog(tmp_path: Path, capsys) -> None:
    """Positive control: a guard that fires on correct code is a guard people delete."""
    root = tmp_path / "current-mp"
    (root / "scripts").mkdir(parents=True)
    (root / "scripts" / "update_catalog.py").write_text(
        gmr._update_catalog_script("demo-mp"), encoding="utf-8"
    )
    assert sma.warn_if_update_catalog_is_unhardened(root) is False
    assert capsys.readouterr().err == ""


def test_setup_is_silent_when_there_is_no_update_catalog(tmp_path: Path, capsys) -> None:
    root = tmp_path / "no-catalog-mp"
    (root / "scripts").mkdir(parents=True)
    assert sma.warn_if_update_catalog_is_unhardened(root) is False
    assert capsys.readouterr().err == ""
