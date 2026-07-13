"""Wave-2 CI-failure root-fix — two-sided tests.

Three defects, each pinned from BOTH sides (a test that only asserts "the bad
thing didn't happen" is worthless without a positive control proving the same
code path still fires):

* **TASK 1 — the RC-3 generator loop.** Wave 1 taught ``standardize`` to emit
  ``.cspell.json`` and taught ``cpv_ci_preflight._gate_cspell`` to FAIL when
  Mega-Linter's SPELL is enabled with no dictionary — but the GENERATOR emitted
  no cspell config, so a FRESHLY SCAFFOLDED plugin failed a gate it had done
  nothing to deserve. Proven closed by scaffolding a real plugin and running the
  real gate on it with NO ``standardize`` run (and, as the positive control,
  proving the same gate still FAILS the instant the dictionary is removed).

* **TASK 2 — the publish gate.** ``cpv_ci_preflight`` mirrored CI's Lint job but
  NOTHING invoked it; it was "enforced" only by prose in the agent files, which
  an agent can skip. Now a real gate in BOTH publish pipelines. Pinned two-sided:
  it BLOCKS on a real parity defect, and it does NOT block when a tool is merely
  ABSENT.

* **TASK 3 — the marketplace RC-8 fail-OPEN hole.** The emitted marketplace CI
  treated ANY exit >= 5 as "advisory WARNING" and PASSED — but CPV's exit codes
  stop at 4, so >= 5 only ever meant a CRASH (`uvx: command not found` = 127, an
  OOM kill = 137). The gate greened having validated nothing. The emitted shell
  is EXECUTED here under bash to prove the crash path now fails.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import cpv_ci_preflight  # noqa: E402
import generate_marketplace_repo as gmr  # noqa: E402
import standardize_plugin  # noqa: E402
from cpv_ci_preflight import PreflightResult, _gate_cspell, _megalinter_enabled_linters  # noqa: E402
from generate_plugin_repo import (  # noqa: E402
    PluginParams,
    gen_cspell_json,
    gen_publish_py,
    generate_all_files,
)


def _params(**kw: object) -> PluginParams:
    base: dict = {
        "name": "demo-plugin",
        "description": "A demo plugin",
        "author": "Emasoft",
        "author_email": "demo@example.com",
        "github_owner": "Emasoft",
    }
    base.update(kw)
    return PluginParams(**base)  # type: ignore[arg-type]


def _scaffold(tmp_path: Path, p: PluginParams | None = None) -> Path:
    """Write a REAL generated plugin to disk. No `standardize` run — that is the
    whole point: the scaffold must stand on its own."""
    p = p or _params()
    target = tmp_path / p.name
    for rel, content, is_exec in generate_all_files(p):
        dest = target / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")
        if is_exec:
            dest.chmod(0o755)
    return target


# ─────────────────────────────────────────────────────────────────────────────
# TASK 1 — the generator emits the cspell dictionary (RC-3 loop closed)
# ─────────────────────────────────────────────────────────────────────────────


def test_generated_plugin_ships_a_cspell_dictionary() -> None:
    names = [rel for rel, _c, _e in generate_all_files(_params())]
    assert ".cspell.json" in names


def test_cspell_config_is_valid_json_with_the_canonical_shape() -> None:
    cfg = json.loads(gen_cspell_json(_params(), generate_all_files(_params())))
    assert cfg["version"] == "0.2"
    assert cfg["useGitignore"] is True
    assert isinstance(cfg["ignorePaths"], list) and cfg["ignorePaths"]
    words = cfg["words"]
    assert words == sorted(words), "words must be sorted for a stable diff"
    assert len(words) == len(set(words)), "words must be deduped"


def test_dictionary_carries_the_plugins_own_proper_nouns() -> None:
    """The plugin's OWN name/author/component names — what a generic dictionary
    can never know, and precisely what made CI red."""
    words = json.loads(gen_cspell_json(_params(), generate_all_files(_params())))["words"]
    for proper_noun in ("demo", "plugin", "emasoft", "skills", "menu"):
        assert proper_noun in words, f"{proper_noun!r} missing — CI would flag it"


def test_dictionary_carries_the_tech_terms_a_bare_cspell_trips_on() -> None:
    """The never-false-block half: these were MEASURED failing against the real
    cspell binary on a bare scaffold (wave-1 report)."""
    words = json.loads(gen_cspell_json(_params(), generate_all_files(_params())))["words"]
    for term in ("pyproject", "venv", "pipefail", "mypy", "pytest", "shellcheck"):
        assert term in words


def test_dictionary_is_vocabulary_not_a_mute_button() -> None:
    """POSITIVE CONTROL for the dictionary itself. A word list that whitelisted
    real misspellings would 'fix' CI by disabling the check."""
    words = json.loads(gen_cspell_json(_params(), generate_all_files(_params())))["words"]
    for typo in ("teh", "recieve", "seperate", "mispeled", "wrod"):
        assert typo not in words


def test_component_names_are_read_back_out_of_the_emitted_file_list() -> None:
    """The seed reads the scaffold's OWN component paths, so a component added to
    the scaffold later is dictionary-seeded automatically — there is no second
    place to remember to update."""
    files = list(generate_all_files(_params()))
    files.append(("agents/widget-inspector.md", "x", False))
    files.append(("commands/frobnicate-thing.md", "x", False))
    words = json.loads(gen_cspell_json(_params(), files))["words"]
    for token in ("widget", "inspector", "frobnicate", "thing"):
        assert token in words


def test_generator_and_standardize_render_the_SAME_dictionary(tmp_path: Path) -> None:
    """THE DRIFT LOCK. Two divergent copies of a word list is the exact bug RC-3
    exists to kill: the local cspell probe and CI's Mega-Linter must read ONE
    file. `standardize` owns the canonical dictionary; the generator imports it.
    If either renderer's SHAPE changes, this fails loudly instead of drifting."""
    target = _scaffold(tmp_path)
    from_generator = (target / ".cspell.json").read_text(encoding="utf-8")
    from_standardize = standardize_plugin._render_canonical_cspell_config(target)
    assert from_generator == from_standardize


def test_cspell_json_is_not_force_templated_by_standardize() -> None:
    """`standardize._FILE_TO_GENERATOR` force-templates a file by calling
    `gen_func(params)`. `gen_cspell_json` takes a SECOND argument, so registering
    it there would TypeError — and force-templating a dictionary would CLOBBER
    words the author curated anyway. The legacy-plugin path is
    `provision_cspell_config`, which AUGMENTS. Pin the exclusion so a future
    maintainer does not wire it up and break the scaffold."""
    assert ".cspell.json" not in standardize_plugin._FILE_TO_GENERATOR
    assert hasattr(standardize_plugin, "provision_cspell_config")


def test_non_python_scaffold_ships_no_cspell_config() -> None:
    """A non-python scaffold ships no `.mega-linter.yml`, so nothing enables
    SPELL_CSPELL and a dictionary would be dead weight."""
    names = [rel for rel, _c, _e in generate_all_files(_params(language="rust"))]
    assert ".cspell.json" not in names
    assert ".mega-linter.yml" not in names


def test_LOOP_CLOSED_freshly_scaffolded_plugin_passes_the_cspell_gate(tmp_path: Path) -> None:
    """THE REGRESSION THIS WAVE EXISTS TO FIX.

    A plugin straight out of the generator — with NO `standardize --fix` run —
    must pass the very gate wave 1 armed. Before this fix it FAILED: the
    generator emitted zero cspell config while `gen_mega_linter_yml` enabled
    SPELL_CSPELL, so the scaffold could not pass its own canonical pipeline.
    """
    target = _scaffold(tmp_path)
    assert (target / ".mega-linter.yml").is_file()
    enabled = _megalinter_enabled_linters(target)
    assert enabled is not None and "SPELL_CSPELL" in enabled, (
        "precondition: the scaffold's Mega-Linter must enable SPELL — otherwise "
        "this test would pass vacuously by skipping the gate"
    )

    result = PreflightResult(plugin_path=target)
    _gate_cspell(result, enabled)

    cspell_fails = [f for f in result.fails if f.gate == "cspell"]
    assert not cspell_fails, f"fresh scaffold FAILS its own cspell gate: {cspell_fails}"


def test_POSITIVE_CONTROL_the_cspell_gate_still_fails_without_the_dictionary(
    tmp_path: Path,
) -> None:
    """The other side of the test above. If the gate could not fail, the test
    above would be proving nothing at all — it would pass on a broken gate."""
    target = _scaffold(tmp_path)
    (target / ".cspell.json").unlink()

    result = PreflightResult(plugin_path=target)
    _gate_cspell(result, _megalinter_enabled_linters(target))

    cspell_fails = [f for f in result.fails if f.gate == "cspell"]
    assert cspell_fails, "the RC-3 gate no longer fires — wave 1's fix has regressed"


# ─────────────────────────────────────────────────────────────────────────────
# TASK 2 — the CI-parity preflight is a REAL publish gate (CPV's own publish.py)
# ─────────────────────────────────────────────────────────────────────────────


def _publish_module():  # noqa: ANN202
    import publish

    return publish


def test_publish_declares_gate_3b() -> None:
    publish = _publish_module()
    labels = [name for name, _desc in publish.GATES]
    assert "Gate 3b" in labels
    # Lettered — so Gates 4..13 keep their numbers (the pre-push hook's "Gate 2b"
    # set this convention).
    assert labels.count("Gate 4") == 1
    assert labels[-1] == "Gate 13"


def test_gate_3b_runs_in_the_preflight_block_before_the_bump() -> None:
    publish = _publish_module()
    assert "ci_preflight" in publish._PARALLEL_GATE_ORDER
    src = Path(publish.__file__).read_text(encoding="utf-8")
    main_src = src[src.index("def main()") :]
    # The preflight block (which contains Gate 3b) must be dispatched before the
    # version bump — a parity failure must never leave a half-published state.
    assert main_src.index("run_preflight_parallel(") < main_src.index("stage_bump(")


def test_gate_3b_is_actually_dispatched_by_the_parallel_block() -> None:
    """A gate listed in the order tuple but never submitted to the pool would
    KeyError at replay — pin that it is really wired in."""
    publish = _publish_module()
    src = Path(publish.__file__).read_text(encoding="utf-8")
    assert '"ci_preflight": lambda: stage_ci_preflight(plugin_root)' in src


def test_gate_3b_BLOCKS_on_a_real_parity_defect(tmp_path: Path, monkeypatch) -> None:
    """POSITIVE CONTROL — the gate must be able to FAIL, or the 'does not block'
    test below proves nothing.

    A prior session shipped a suppression test with no positive control; the
    'fix' was inert and nobody noticed. Not again.
    """
    publish = _publish_module()
    root = tmp_path / "plug"
    (root / ".github" / "workflows").mkdir(parents=True)
    # A REAL CIP-6 defect: CPV's default branch is `master`, so an `@main` pin
    # 404s on the runner and the workflow red-CIs forever.
    (root / ".github" / "workflows" / "ci.yml").write_text(
        "name: CI\n"
        "on: [push]\n"
        "jobs:\n"
        "  validate:\n"
        "    runs-on: ubuntu-latest\n"
        "    steps:\n"
        "      - run: uvx --from "
        "git+https://github.com/Emasoft/claude-plugins-validation@main "
        "cpv-remote-validate plugin . --strict\n",
        encoding="utf-8",
    )
    rc = publish.stage_ci_preflight(root)
    assert rc != 0, "a stale @main CPV pin must BLOCK the publish (CIP-6)"


def test_gate_3b_does_NOT_block_when_a_tool_is_merely_ABSENT(tmp_path: Path, monkeypatch) -> None:
    """THE LOAD-BEARING SAFETY PROPERTY. A missing actionlint / npx / mypy / uv
    must degrade to a non-blocking WARNING — never a publish block. A dev box
    without the toolchain must still be able to ship."""
    publish = _publish_module()
    root = tmp_path / "plug"
    (root / ".github" / "workflows").mkdir(parents=True)
    (root / ".github" / "workflows" / "ci.yml").write_text(
        "name: CI\non: [push]\njobs:\n  t:\n    runs-on: ubuntu-latest\n    steps:\n      - run: echo hi\n",
        encoding="utf-8",
    )
    # Every external tool is gone.
    monkeypatch.setattr(cpv_ci_preflight.shutil, "which", lambda _name: None)

    result = cpv_ci_preflight.run_ci_preflight(root)
    assert result.exit_code == 0, (
        f"tool absence must NEVER block a publish, got FAILs: {[(f.gate, f.message) for f in result.fails]}"
    )
    assert result.warnings, "tool absence should still be SURFACED as a warning"
    assert publish.stage_ci_preflight(root) == 0


# ─────────────────────────────────────────────────────────────────────────────
# TASK 2 (b) — the same gate in the GENERATED publish.py template
# ─────────────────────────────────────────────────────────────────────────────


def test_template_publish_py_has_the_preflight_stage() -> None:
    src = gen_publish_py(_params())
    assert "def stage_ci_preflight(root: Path) -> None:" in src
    assert '"cpv-remote-validate", "ci-preflight", ".",' in src


def test_template_runs_the_preflight_BEFORE_bump_commit_tag_push() -> None:
    src = gen_publish_py(_params())
    main_src = src[src.rindex("def main()") :]
    i_pre = main_src.index("stage_ci_preflight(root)")
    for later in ("stage_bump(", "stage_changelog(", "stage_commit_and_push(", "stage_gh_release("):
        assert i_pre < main_src.index(later), f"{later} must run AFTER the preflight"


def test_template_compiles_for_both_cpv_sources() -> None:
    for source in ("git", "pypi"):
        src = gen_publish_py(_params(cpv_source=source))
        compile(src, f"<publish-{source}.py>", "exec")


def test_template_pypi_variant_strips_the_pyyaml_shim_from_the_new_callsite() -> None:
    """The new CPV callsite must spell `--with pyyaml` in one of the three forms
    the pypi post-process strips — otherwise the pypi wheel variant would ship a
    stale shim."""
    src = gen_publish_py(_params(cpv_source="pypi"))
    assert '"--with", "pyyaml"' not in src
    assert "claude-plugins-validation==" in src


def test_template_preflight_never_hard_requires_a_tool() -> None:
    """The generated stage must not invent its own tool requirement — the
    degrade-gracefully contract lives in ci-preflight itself."""
    src = gen_publish_py(_params())
    stage = src[src.index("def stage_ci_preflight") : src.index("# ── Marketplace-registration")]
    # uvx is the one hard requirement, and only because stage_validate already
    # requires it two stages earlier (so this can never be the first blocker).
    assert stage.count("sys.exit(1)") == 2  # no-uvx, and a real preflight failure
    # Scoped to the ARGV LIST, not the whole stage: the docstring legitimately
    # mentions `validate_plugin --strict` when explaining what this gate adds.
    argv = stage[stage.index("rc = subprocess.run([") : stage.index("], cwd=str(root))")]
    assert '"--strict"' not in argv, "ci-preflight takes no --strict; it is a parity gate"
    assert '"ci-preflight"' in argv


# ─────────────────────────────────────────────────────────────────────────────
# TASK 3 — the marketplace RC-8 fail-OPEN hole
# ─────────────────────────────────────────────────────────────────────────────


def test_marketplace_workflow_has_no_fail_open_branch() -> None:
    wf = gmr._validate_workflow()
    assert "-ge 5" not in wf, "the >=5 'advisory' branch is the fail-OPEN hole"
    assert "__CPV_" not in wf, "unsubstituted placeholder leaked into the workflow"


def test_marketplace_workflow_classifies_all_three_validators() -> None:
    wf = gmr._validate_workflow()
    assert wf.count("FAILED TO RUN") == 3, "every validator step must classify infra failure"
    for marker in (
        gmr.CPV_MARKETPLACE_VERDICT_MARKER,
        gmr.CPV_PLUGIN_VERDICT_MARKER,
        gmr.CPV_PIPELINE_VERDICT_MARKER,
    ):
        assert marker in wf


def test_marketplace_workflow_is_valid_yaml_and_valid_shell() -> None:
    yaml = pytest.importorskip("yaml")
    wf = gmr._validate_workflow()
    doc = yaml.safe_load(wf)
    runs = [s["run"] for s in doc["jobs"]["validate"]["steps"] if "run" in s]
    assert len(runs) >= 4
    for r in runs:
        proc = subprocess.run(["bash", "-n"], input=r, text=True, capture_output=True)
        assert proc.returncode == 0, f"emitted shell is not parseable:\n{proc.stderr}\n{r}"


def test_plugin_verdict_marker_is_the_SSOT_not_a_retyped_copy() -> None:
    from generate_plugin_repo import CPV_SUMMARY_MARKER

    assert gmr.CPV_PLUGIN_VERDICT_MARKER is CPV_SUMMARY_MARKER


@pytest.mark.parametrize(
    "marker,module_name,fn",
    [
        # Each marker must actually appear in the validator's real output, or the
        # "did it run" proof silently degrades into "it never runs" → every run
        # classified as an infra failure. This catches a rename at the source.
        ("Marketplace Validation Report", "validate_marketplace", "format_report"),
        ("SUMMARY: ", "validate_marketplace_pipeline", "format_text_report"),
    ],
)
def test_verdict_markers_match_the_real_validator_output(marker: str, module_name: str, fn: str) -> None:
    import importlib

    mod = importlib.import_module(module_name)
    src = Path(mod.__file__).read_text(encoding="utf-8")  # type: ignore[arg-type]
    formatter = src[src.index(f"def {fn}(") :]
    assert marker in formatter, f"{module_name}.{fn} no longer emits {marker!r}"


def _run_classifier(exit_code: int, report_text: str, tmp_path: Path) -> subprocess.CompletedProcess:
    """EXECUTE the emitted RC-8 classifier under bash with a synthetic validator
    outcome. This is the only way to prove the fail-open hole is really closed —
    a substring assertion cannot tell you what the shell DOES."""
    report = tmp_path / "report.txt"
    report.write_text(report_text, encoding="utf-8")
    block = gmr._cpv_verdict_shell(
        label="Marketplace validation",
        exit_var="exit_code",
        report_path=str(report),
        marker=gmr.CPV_MARKETPLACE_VERDICT_MARKER,
        indent="",
        pass_echo="Marketplace validation passed",
        findings_action="exit $exit_code",
        infra_action="exit 1",
    )
    script = f"set -e\nexit_code={exit_code}\n{block}\n"
    return subprocess.run(["bash", "-c", script], text=True, capture_output=True)


def test_rc8_clean_run_passes(tmp_path: Path) -> None:
    proc = _run_classifier(0, "Marketplace Validation Report\nall good\n", tmp_path)
    assert proc.returncode == 0
    assert "passed" in proc.stdout


def test_rc8_clean_run_passes_even_without_the_marker(tmp_path: Path) -> None:
    """exit 0 must never require the marker — a future output-format change must
    not be able to false-block a clean run."""
    assert _run_classifier(0, "", tmp_path).returncode == 0


def test_rc8_real_findings_fail_and_are_labelled_findings(tmp_path: Path) -> None:
    proc = _run_classifier(2, "Marketplace Validation Report\nMAJOR: bad\n", tmp_path)
    assert proc.returncode == 2
    assert "CRITICAL/MAJOR/MINOR/NIT found" in proc.stdout
    assert "FAILED TO RUN" not in proc.stdout


def test_rc8_THE_FAIL_OPEN_HOLE_a_crash_now_fails(tmp_path: Path) -> None:
    """THE BUG. `uvx: command not found` exits 127. The old handler read that as
    '>= 5 → only WARNING findings → advisory' and PASSED — the marketplace CI
    went green having validated nothing at all."""
    proc = _run_classifier(127, "uvx: command not found\n", tmp_path)
    assert proc.returncode != 0, "a crashed validator must NEVER green the gate"
    assert "FAILED TO RUN" in proc.stdout
    assert "infra/network/install failure" in proc.stdout


def test_rc8_oom_kill_now_fails(tmp_path: Path) -> None:
    proc = _run_classifier(137, "Killed\n", tmp_path)
    assert proc.returncode != 0
    assert "FAILED TO RUN" in proc.stdout


def test_rc8_infra_crash_that_exits_1_is_not_mislabelled_as_findings(tmp_path: Path) -> None:
    """The subtle half. A cold `uvx --from git+…` build that dies on a transient
    GitHub git-fetch exits 1 — byte-identical to a CRITICAL verdict. The exit code
    ALONE cannot separate them, which is why the marker is required as PROOF the
    validator actually produced a verdict."""
    proc = _run_classifier(1, "error: Git operation failed\n", tmp_path)
    assert proc.returncode != 0
    assert "FAILED TO RUN" in proc.stdout, "an infra crash must not be reported as findings"
    assert "CRITICAL/MAJOR/MINOR/NIT found" not in proc.stdout
