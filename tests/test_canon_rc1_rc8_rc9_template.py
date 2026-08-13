"""RC-1 / RC-8 / RC-9 — cpv-canonical-pipeline TEMPLATE fixes + their CIP detectors.

Grounded in `reports/ci-failure-forensics/20260713_123038+0200-agent-pipeline-failures.md`
(235 workflow runs across 21 plugin repos):

* **RC-1** (4 failures, the largest ongoing red-CI source) — the emitted
  commitlint gate shipped no `.commitlintrc.json`, so it fell back to
  `@commitlint/config-conventional` (`body-max-line-length` = 100). Dependabot's
  auto-generated commit body embeds a YAML dependency block that always exceeds
  100 chars ⇒ EVERY Dependabot PR on EVERY cpv-canonical-pipeline plugin failed CI,
  forever. Fixed by shipping a canonical `.commitlintrc.json` that disables ONLY
  that rule; CIP-7 detects the defect in already-deployed repos.
* **RC-8** — the validate step printed "CRITICAL/MAJOR/MINOR/NIT found" for ANY
  non-zero exit (so a `uvx` git-fetch failure read as a validation verdict), and
  ci.yml additionally treated exit >= 5 as "advisory" and exited 0 (so an infra
  failure SILENTLY PASSED the gate). Fixed by branching on the exit code AND
  requiring CPV's own SUMMARY line as proof the validator ran.
* **RC-9** — the sharded pytest matrix (`--splits N --group K`) needs the
  `pytest-split` plugin; a repo whose dev extra omits it dies with
  `pytest: error: unrecognized arguments`. The shard count and the requirement
  now come from ONE pair of constants so the matrix and the dev extra cannot
  desync; CIP-8 detects the defect in already-deployed repos.

EVERY test here is TWO-SIDED: the defect/FP case is paired with a POSITIVE
CONTROL proving the same code path still fires on the genuine defect. A test that
only asserts an absence is worthless.
"""

import json
import sys
import tomllib
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from cpv_ci_parity_checks import check_ci_parity  # noqa: E402
from generate_plugin_repo import (  # noqa: E402
    PYTEST_SPLIT_REQUIREMENT,
    TEST_SHARD_COUNT,
    PluginParams,
    gen_ci_yml,
    gen_commitlintrc_json,
    gen_pyproject_toml,
    gen_release_yml,
    generate_all_files,
)


def _params(**kw: object) -> PluginParams:
    base: dict[str, object] = {
        "name": "demo-plugin",
        "description": "A demo plugin",
        "author": "Demo Author",
        "author_email": "demo@example.com",
        "version": "0.1.0",
    }
    base.update(kw)
    return PluginParams(**base)  # type: ignore[arg-type]


def _codes(findings: list, prefix: str) -> list:
    return [f for f in findings if f.check_id == prefix]


# ═════════════════════════════════════════════════════════════════════════
# RC-1 — the commitlint gate no longer fails every Dependabot PR
# ═════════════════════════════════════════════════════════════════════════


def test_rc1_template_emits_commitlintrc() -> None:
    """The scaffold ships `.commitlintrc.json` (the RC-1 fix) for a python plugin."""
    paths = [rel for rel, _content, _x in generate_all_files(_params())]
    assert ".commitlintrc.json" in paths


def test_rc1_commitlintrc_disables_only_body_max_line_length() -> None:
    """body-max-line-length is OFF; config-conventional (type-enum et al.) stays ON."""
    cfg = json.loads(gen_commitlintrc_json(_params()))
    assert cfg["extends"] == ["@commitlint/config-conventional"]
    assert cfg["rules"]["body-max-line-length"] == [0]
    # POSITIVE CONTROL for "the gate is not weakened": the ONLY rule the config
    # touches is the cosmetic one. RC-5 (a non-conventional `type`) is caught by
    # config-conventional's type-enum, which this config must NOT disable.
    assert list(cfg["rules"]) == ["body-max-line-length"], "no other rule may be disabled"
    assert "type-enum" not in cfg["rules"]


def test_rc1_commitlintrc_is_valid_json() -> None:
    """The emitted config parses — a broken config would make the gate error out."""
    assert isinstance(json.loads(gen_commitlintrc_json(_params())), dict)


def test_rc1_ci_still_runs_the_commitlint_gate() -> None:
    """POSITIVE CONTROL: the fix does NOT remove/skip the gate — it still runs on PRs."""
    ci = gen_ci_yml(_params())
    assert "wagoid/commitlint-github-action" in ci
    assert "if: github.event_name == 'pull_request'" in ci


# ─── CIP-7: detect the RC-1 defect in an already-deployed repo ────────────

_COMMITLINT_WF = """name: CI
on:
  pull_request:
jobs:
  commitlint:
    runs-on: ubuntu-latest
    if: github.event_name == 'pull_request'
    steps:
      - uses: wagoid/commitlint-github-action@b948419dd99f3fd78a6548d48f94e3df7f6bf3ed
"""


def _write_wf(root: Path, name: str, text: str) -> None:
    wf = root / ".github" / "workflows"
    wf.mkdir(parents=True, exist_ok=True)
    (wf / name).write_text(text, encoding="utf-8")


def test_cip7_fires_on_commitlint_gate_with_no_exemption_and_no_config(tmp_path: Path) -> None:
    """THE DEFECT: commitlint gate, no bot exemption, no config → WARNING."""
    _write_wf(tmp_path, "ci.yml", _COMMITLINT_WF)
    found = _codes(check_ci_parity(tmp_path), "CIP-7")
    assert len(found) == 1
    assert found[0].severity == "WARNING", "a migration detector must never block a publish"
    assert "Dependabot" in found[0].message


def test_cip7_clears_when_the_canonical_commitlintrc_is_present(tmp_path: Path) -> None:
    """THE FIX: shipping the canonical `.commitlintrc.json` clears CIP-7."""
    _write_wf(tmp_path, "ci.yml", _COMMITLINT_WF)
    (tmp_path / ".commitlintrc.json").write_text(gen_commitlintrc_json(_params()), encoding="utf-8")
    assert _codes(check_ci_parity(tmp_path), "CIP-7") == []


def test_cip7_still_fires_when_a_config_exists_but_leaves_the_rule_at_100(tmp_path: Path) -> None:
    """POSITIVE CONTROL: a config that does NOT disable the rule is still the defect."""
    _write_wf(tmp_path, "ci.yml", _COMMITLINT_WF)
    (tmp_path / ".commitlintrc.json").write_text(
        json.dumps(
            {
                "extends": ["@commitlint/config-conventional"],
                "rules": {"body-max-line-length": [2, "always", 100]},
            }
        ),
        encoding="utf-8",
    )
    assert len(_codes(check_ci_parity(tmp_path), "CIP-7")) == 1


def test_cip7_clears_on_an_actor_based_bot_exemption(tmp_path: Path) -> None:
    """The other legitimate fix (an actor guard) also clears the check."""
    _write_wf(
        tmp_path,
        "ci.yml",
        _COMMITLINT_WF.replace(
            "if: github.event_name == 'pull_request'",
            "if: github.event_name == 'pull_request' && github.actor != 'dependabot[bot]'",
        ),
    )
    assert _codes(check_ci_parity(tmp_path), "CIP-7") == []


def test_cip7_clears_on_a_multiline_bot_exemption(tmp_path: Path) -> None:
    """A `!contains(github.actor, 'dependabot')` guard on its own line also clears it."""
    _write_wf(
        tmp_path,
        "ci.yml",
        _COMMITLINT_WF.replace(
            "    if: github.event_name == 'pull_request'\n",
            "    if: >-\n      github.event_name == 'pull_request'\n"
            "      && !contains(github.actor, 'dependabot')\n",
        ),
    )
    assert _codes(check_ci_parity(tmp_path), "CIP-7") == []


def test_cip7_never_fires_on_a_repo_with_no_commitlint_gate(tmp_path: Path) -> None:
    """No commitlint anywhere → no config is needed → never nag (degrade-not-false-block)."""
    _write_wf(tmp_path, "ci.yml", "name: CI\non:\n  push:\njobs:\n  t:\n    steps:\n      - run: x\n")
    assert _codes(check_ci_parity(tmp_path), "CIP-7") == []


def test_cip7_clears_on_a_js_config_naming_the_rule(tmp_path: Path) -> None:
    """A commitlint.config.js that names the rule is accepted (we cannot eval JS)."""
    _write_wf(tmp_path, "ci.yml", _COMMITLINT_WF)
    (tmp_path / "commitlint.config.js").write_text(
        "module.exports = {extends: ['@commitlint/config-conventional'],"
        " rules: {'body-max-line-length': [0]}};\n",
        encoding="utf-8",
    )
    assert _codes(check_ci_parity(tmp_path), "CIP-7") == []


def test_cip7_clears_on_a_package_json_commitlint_block(tmp_path: Path) -> None:
    """package.json's `commitlint` key is a documented config location."""
    _write_wf(tmp_path, "ci.yml", _COMMITLINT_WF)
    (tmp_path / "package.json").write_text(
        json.dumps({"name": "x", "commitlint": {"rules": {"body-max-line-length": [0]}}}),
        encoding="utf-8",
    )
    assert _codes(check_ci_parity(tmp_path), "CIP-7") == []


def test_cip7_positive_control_package_json_without_the_override(tmp_path: Path) -> None:
    """POSITIVE CONTROL: a package.json with no commitlint block does NOT clear it."""
    _write_wf(tmp_path, "ci.yml", _COMMITLINT_WF)
    (tmp_path / "package.json").write_text(json.dumps({"name": "x"}), encoding="utf-8")
    assert len(_codes(check_ci_parity(tmp_path), "CIP-7")) == 1


def test_cip7_clears_on_the_generated_scaffold(tmp_path: Path) -> None:
    """END-TO-END: a freshly-scaffolded plugin is CIP-7 clean (the template is fixed)."""
    for rel, content, _x in generate_all_files(_params()):
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    assert _codes(check_ci_parity(tmp_path), "CIP-7") == []


# ═════════════════════════════════════════════════════════════════════════
# RC-8 — the validate handler tells findings apart from an infra failure
# ═════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("gen", [gen_ci_yml, gen_release_yml])
def test_rc8_infra_failure_is_labelled_as_such(gen) -> None:
    """A non-verdict exit prints FAILED TO RUN, not "CRITICAL/MAJOR/MINOR/NIT found"."""
    text = gen(_params())
    assert "CPV validator FAILED TO RUN" in text
    assert "infra/network/install failure" in text


@pytest.mark.parametrize("gen", [gen_ci_yml, gen_release_yml])
def test_rc8_findings_label_is_gated_on_exit_1_to_4_and_the_summary_line(gen) -> None:
    """POSITIVE CONTROL: a REAL verdict (exit 1-4 + a SUMMARY line) still says "findings"."""
    text = gen(_params())
    # Quoted since #180 (PIPESTATUS defeats shellcheck's numeric inference, so an
    # unquoted expansion trips SC2086 in the generated Lint job). The GATE is
    # unchanged — still exit 1-4 AND a SUMMARY line.
    assert '[ "$exit_code" -ge 1 ] && [ "$exit_code" -le 4 ]' in text
    assert 'grep -q "SUMMARY: CRITICAL="' in text
    assert "Validation failed (exit $exit_code: CRITICAL/MAJOR/MINOR/NIT found)" in text


@pytest.mark.parametrize("gen", [gen_ci_yml, gen_release_yml])
def test_rc8_exit_zero_still_passes(gen) -> None:
    """POSITIVE CONTROL: a clean run is never blocked by the new handler."""
    text = gen(_params())
    assert 'if [ "$exit_code" -eq 0 ]; then' in text
    assert "Validation passed" in text


def test_rc8_ci_no_longer_greens_an_exit_ge_5() -> None:
    """The fail-OPEN branch is gone: exit >= 5 (e.g. 127) no longer exits 0 as "advisory"."""
    ci = gen_ci_yml(_params())
    assert "-ge 5" not in ci
    assert "advisory, not blocking" not in ci


def test_rc8_release_no_longer_falls_through_on_a_non_verdict_exit() -> None:
    """release.yml must not publish a tag whose validation never ran (fail-closed)."""
    rel = gen_release_yml(_params())
    block_start = rel.index("Run full plugin validation")
    # End anchor is the step that FOLLOWS validation in the same job. It used to
    # be "- name: Run tests", but the suite moved into a separate sharded job
    # whose step is named "Run tests (shard N of M)" — so that anchor still
    # matched, just far later in the file, silently widening this slice across
    # two jobs and breaking the endswith("exit 1") check for the wrong reason.
    block_end = rel.index("- name: Lint Python scripts", block_start)
    block = rel[block_start:block_end]
    # The unconditional `exit 1` tail is what fails the job on a non-verdict exit;
    # the old handler simply fell through to the release steps.
    assert "CPV validator FAILED TO RUN" in block
    assert block.rstrip().endswith("exit 1")


@pytest.mark.parametrize("gen", [gen_ci_yml, gen_release_yml])
def test_rc8_handler_is_a_valid_shell_script(gen) -> None:
    """The emitted handler parses as bash (a broken gate is worse than a mislabelled one)."""
    import shutil
    import subprocess

    bash = shutil.which("bash")
    if bash is None:  # pragma: no cover - bash is present on every dev/CI runner
        pytest.skip("bash not available")
    text = gen(_params())
    start = text.index("          set +e\n")
    end = text.index("          exit 1", start) + len("          exit 1")
    script = "\n".join(line[10:] for line in text[start:end].splitlines())
    proc = subprocess.run([bash, "-n"], input=script, text=True, capture_output=True)
    assert proc.returncode == 0, f"emitted handler is not valid bash:\n{proc.stderr}\n{script}"


def test_rc8_ci_report_is_written_outside_the_checkout() -> None:
    """ci.yml captures to $RUNNER_TEMP so the validator cannot scan its own report."""
    ci = gen_ci_yml(_params())
    assert '"$RUNNER_TEMP/cpv-validation-report.txt"' in ci
    # POSITIVE CONTROL: release.yml still writes into the workspace, because the
    # report is uploaded as a release asset (issue #121) — the two differ on
    # purpose, and the asset name must not change.
    rel = gen_release_yml(_params())
    assert '"validation-report.txt"' in rel
    assert "gh release upload" in rel and "validation-report.txt" in rel


# ═════════════════════════════════════════════════════════════════════════
# RC-9 — the sharded matrix and the pytest-split dep cannot desync
# ═════════════════════════════════════════════════════════════════════════


def test_rc9_dev_extra_declares_pytest_split() -> None:
    """The emitted pyproject dev extra declares pytest-split."""
    data = tomllib.loads(gen_pyproject_toml(_params()))
    dev = data["project"]["optional-dependencies"]["dev"]
    assert PYTEST_SPLIT_REQUIREMENT in dev
    assert any(r.startswith("pytest-split") for r in dev)


def test_rc9_matrix_and_dep_are_coupled_in_both_directions() -> None:
    """THE INVARIANT: `--splits` in ci.yml ⇔ pytest-split in the dev extra.

    If a future template ever emits a NON-sharded matrix, the dependency must not
    be required — so this is a biconditional, not a one-way assertion.
    """
    ci = gen_ci_yml(_params())
    dev = tomllib.loads(gen_pyproject_toml(_params()))["project"]["optional-dependencies"]["dev"]
    sharded = "--splits" in ci
    declared = any(r.startswith("pytest-split") for r in dev)
    assert sharded == declared, "the sharded matrix and the pytest-split dep must move together"


def test_rc9_shard_count_drives_every_emitted_reference() -> None:
    """The matrix dimension, the --splits flag and the step name all come from ONE constant."""
    ci = gen_ci_yml(_params())
    groups = ", ".join(str(i) for i in range(1, TEST_SHARD_COUNT + 1))
    assert f"group: [{groups}]" in ci
    assert f"--splits {TEST_SHARD_COUNT} --group ${{{{ matrix.group }}}}" in ci
    assert f"of {TEST_SHARD_COUNT})" in ci


# ─── CIP-8: detect the RC-9 defect in an already-deployed repo ────────────

_SHARDED_WF = """name: CI
on:
  push:
jobs:
  test:
    strategy:
      matrix:
        group: [1, 2]
    runs-on: ubuntu-latest
    steps:
      - run: uv sync --extra dev
      - run: uv run pytest tests/ --splits 2 --group ${{ matrix.group }} -v
"""

_PYPROJECT = """[project]
name = "demo"
version = "0.1.0"

[project.optional-dependencies]
dev = [
    "pytest>=8.0.0",
{extra}]
"""


def _write_pyproject(root: Path, *extra: str) -> None:
    body = "".join(f'    "{e}",\n' for e in extra)
    (root / "pyproject.toml").write_text(_PYPROJECT.format(extra=body), encoding="utf-8")


def test_cip8_fires_on_a_sharded_matrix_with_no_pytest_split(tmp_path: Path) -> None:
    """THE DEFECT: `pytest --splits` + a dev extra without pytest-split → WARNING."""
    _write_wf(tmp_path, "ci.yml", _SHARDED_WF)
    _write_pyproject(tmp_path, "ruff>=0.14")
    found = _codes(check_ci_parity(tmp_path), "CIP-8")
    assert len(found) == 1
    assert found[0].severity == "WARNING"
    assert "unrecognized arguments" in found[0].message


def test_cip8_clears_when_the_dev_extra_declares_pytest_split(tmp_path: Path) -> None:
    """THE FIX: declaring the dependency clears CIP-8."""
    _write_wf(tmp_path, "ci.yml", _SHARDED_WF)
    _write_pyproject(tmp_path, PYTEST_SPLIT_REQUIREMENT)
    assert _codes(check_ci_parity(tmp_path), "CIP-8") == []


def test_cip8_accepts_an_underscore_spelling(tmp_path: Path) -> None:
    """PEP-503 name normalization: `pytest_split` is the same distribution."""
    _write_wf(tmp_path, "ci.yml", _SHARDED_WF)
    _write_pyproject(tmp_path, "pytest_split>=0.9")
    assert _codes(check_ci_parity(tmp_path), "CIP-8") == []


def test_cip8_accepts_a_dependency_group(tmp_path: Path) -> None:
    """A PEP-735 [dependency-groups] declaration is equally valid — do not nag."""
    _write_wf(tmp_path, "ci.yml", _SHARDED_WF)
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "0.1.0"\n\n'
        '[dependency-groups]\ntest = ["pytest-split>=0.9"]\n',
        encoding="utf-8",
    )
    assert _codes(check_ci_parity(tmp_path), "CIP-8") == []


def test_cip8_never_fires_on_a_non_sharded_matrix(tmp_path: Path) -> None:
    """THE CONVERSE: no `--splits` → the dependency is NOT required → never fires."""
    _write_wf(
        tmp_path,
        "ci.yml",
        _SHARDED_WF.replace("--splits 2 --group ${{ matrix.group }} ", ""),
    )
    _write_pyproject(tmp_path, "ruff>=0.14")
    assert _codes(check_ci_parity(tmp_path), "CIP-8") == []


def test_cip8_is_silent_without_a_pyproject(tmp_path: Path) -> None:
    """No pyproject → undeterminable → no signal (degrade-not-false-block)."""
    _write_wf(tmp_path, "ci.yml", _SHARDED_WF)
    assert _codes(check_ci_parity(tmp_path), "CIP-8") == []


def test_cip8_is_silent_on_an_unparseable_pyproject(tmp_path: Path) -> None:
    """A broken pyproject is not evidence of the defect — refuse to guess."""
    _write_wf(tmp_path, "ci.yml", _SHARDED_WF)
    (tmp_path / "pyproject.toml").write_text("this is not = valid toml [[[", encoding="utf-8")
    assert _codes(check_ci_parity(tmp_path), "CIP-8") == []


def test_cip8_positive_control_a_lookalike_requirement_does_not_clear_it(tmp_path: Path) -> None:
    """POSITIVE CONTROL: `pytest-splitter` is a DIFFERENT distribution — still fires."""
    _write_wf(tmp_path, "ci.yml", _SHARDED_WF)
    _write_pyproject(tmp_path, "pytest-splitter>=1.0")
    assert len(_codes(check_ci_parity(tmp_path), "CIP-8")) == 1


def test_cip8_clears_on_the_generated_scaffold(tmp_path: Path) -> None:
    """END-TO-END: a freshly-scaffolded plugin is CIP-8 clean (the template is fixed)."""
    for rel, content, _x in generate_all_files(_params()):
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    assert _codes(check_ci_parity(tmp_path), "CIP-8") == []


def test_generated_scaffold_is_clean_for_every_cip(tmp_path: Path) -> None:
    """The whole CIP-1..8 suite is quiet on a fresh scaffold — no self-inflicted findings."""
    for rel, content, _x in generate_all_files(_params()):
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    assert check_ci_parity(tmp_path) == []
