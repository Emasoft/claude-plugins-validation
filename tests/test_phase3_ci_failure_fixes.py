#!/usr/bin/env python3
"""Tests for the phase-3 CI-failure root-cause fixes in the plugin generator.

These pin the template/generator changes that make a freshly-GENERATED
pipeline PASS GitHub CI instead of hanging, stalling at "pending", or
red-lighting downstream plugins on every CPV release. Each test maps to a
numbered root cause from
``reports/fp-fixes/20260619_014329+0200-phase3-ci-failure-rootcause.md``:

* #2  — the CPV git ref is PINNED (not HEAD) in all five callsites + README.
* #4  — the CI + release validate steps carry the integrity-skip env.
* #3  — the matrix-vs-required-``Test``-context contradiction is resolved by an
        aggregate gate job named exactly ``Test`` (``needs: [test]``); the
        required branch contexts still match the emitted job names.
* #1  — every validate/test job has a real ``timeout-minutes`` ceiling.
* #9  — notify-marketplace.yml is a no-op when the secret is absent, and is not
        even emitted when the marketplace is the placeholder (unless forced).
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

import setup_branch_rules  # noqa: E402
from generate_plugin_repo import (  # noqa: E402
    PluginParams,
    _default_cpv_ref,
    gen_ci_yml,
    gen_language_todo,
    gen_notify_marketplace_yml,
    gen_publish_py,
    gen_release_yml,
    generate_all_files,
)

CPV_URL = "git+https://github.com/Emasoft/claude-plugins-validation"


def _params(**overrides) -> PluginParams:
    kwargs: dict[str, object] = {
        "name": "test-plugin",
        "description": "test",
        "author": "X",
        "author_email": "x@x",
        "python_version": "3.12",
        "github_owner": "Emasoft",
        "marketplace": "test-marketplace",
    }
    kwargs.update(overrides)
    return PluginParams(**kwargs)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# #2 — CPV ref is PINNED, default resolves to the scaffolding CPV's version
# ---------------------------------------------------------------------------


def test_default_cpv_ref_matches_own_plugin_version() -> None:
    """_default_cpv_ref() reads CPV's own plugin.json version, 'v'-prefixed."""
    manifest = Path(__file__).parent.parent / ".claude-plugin" / "plugin.json"
    import json

    version = json.loads(manifest.read_text(encoding="utf-8"))["version"]
    assert _default_cpv_ref() == f"v{version}"
    assert _default_cpv_ref().startswith("v")


def test_cpv_ref_resolved_prefers_explicit_then_default() -> None:
    """cpv_ref_resolved uses the explicit ref when set, else the default."""
    assert _params(cpv_ref="v9.9.9").cpv_ref_resolved == "v9.9.9"
    assert _params(cpv_ref="  v1.2.3  ").cpv_ref_resolved == "v1.2.3"
    assert _params().cpv_ref_resolved == _default_cpv_ref()


def test_publish_py_pins_all_three_cpv_callsites() -> None:
    """publish.py's G3, Stage-4, and branch-rule CPV calls are all pinned.

    The bare URL appears 3× in code (plus once in a docstring); none may remain
    UN-pinned. We assert there is no occurrence of the bare URL that is NOT
    immediately followed by '@<ref>'.
    """
    py = gen_publish_py(_params())
    ref = _default_cpv_ref()
    pinned = f"{CPV_URL}@{ref}"
    assert pinned in py
    # No bare URL survives: every occurrence of CPV_URL must be the start of a
    # pinned URL (i.e. immediately followed by '@').
    idx = 0
    while True:
        idx = py.find(CPV_URL, idx)
        if idx == -1:
            break
        after = py[idx + len(CPV_URL)]
        assert after == "@", f"unpinned CPV URL at offset {idx}: ...{py[idx:idx + 70]!r}"
        idx += len(CPV_URL)


def test_publish_py_pin_follows_explicit_cpv_ref() -> None:
    """A custom --cpv-ref propagates into the generated publish.py."""
    py = gen_publish_py(_params(cpv_ref="v7.7.7"))
    assert f"{CPV_URL}@v7.7.7" in py
    assert f"{CPV_URL}@{_default_cpv_ref()}" not in py


def test_ci_yml_validate_step_pins_cpv_ref() -> None:
    """ci.yml's validate step fetches CPV at the pinned ref, not HEAD."""
    yml = gen_ci_yml(_params())
    assert f"{CPV_URL}@{_default_cpv_ref()}" in yml
    # The bare (HEAD-tracking) form must not appear as a fetch target.
    assert f"--from {CPV_URL} " not in yml
    assert f"--from {CPV_URL}\n" not in yml


def test_release_yml_validate_step_pins_cpv_ref() -> None:
    """release.yml's validate step fetches CPV at the pinned ref, not HEAD."""
    yml = gen_release_yml(_params())
    assert f"{CPV_URL}@{_default_cpv_ref()}" in yml
    assert f"--from {CPV_URL} " not in yml
    assert f"--from {CPV_URL}\n" not in yml


def test_readme_snippet_pins_cpv_ref() -> None:
    """The non-python README validate snippet pins the CPV ref."""
    todo = gen_language_todo(_params(language="rust"))
    assert f"{CPV_URL}@{_default_cpv_ref()}" in todo
    assert f"uvx --from {CPV_URL} --with pyyaml" not in todo


# ---------------------------------------------------------------------------
# #4 — integrity-skip env on the CI + release validate steps
# ---------------------------------------------------------------------------


def _validate_step(yml_text: str, job: str, name_prefix: str) -> dict:
    parsed = yaml.safe_load(yml_text)
    steps = parsed["jobs"][job]["steps"]
    return next(s for s in steps if s.get("name", "").startswith(name_prefix))


def _hosting_job(yml_text: str, name_prefix: str) -> tuple[str, dict, dict]:
    """Find the ONE job whose steps include the named step.

    Keyed on the STEP rather than a hardcoded job name: release.yml used to be a
    single `release` job, and when the suite was sharded across
    validate/test-shard/release the validation step moved, which broke three
    assertions that were really about the step, not about where it lived.
    Asserting uniqueness matters as much as finding it — two copies of the
    validation step would mean one of them is ungoverned by these checks.
    """
    parsed = yaml.safe_load(yml_text)
    hits = [
        (job_name, job, step)
        for job_name, job in parsed["jobs"].items()
        for step in job["steps"]
        if step.get("name", "").startswith(name_prefix)
    ]
    assert len(hits) == 1, f"expected exactly one {name_prefix!r} step, found {len(hits)}"
    return hits[0]


def test_ci_yml_validate_step_has_integrity_skip_env() -> None:
    """ci.yml validate step keeps PLUGIN_SKIP_GITHUB_INTEGRITY and (per #140) drops private-usernames.

    #140: the step used to seed CLAUDE_PRIVATE_USERNAMES with the PUBLIC repo owner
    — a semantic inversion (that env lists PRIVATE usernames), so CPV flagged every
    github.com/<owner>/ URL + the owner no-reply email as a CRITICAL leak and the
    downstream Validate job failed under --strict. The integrity-skip env STAYS (a CI
    runner has no developer local-username to protect); the private-usernames env is
    asserted ABSENT here so a re-introduction at the step level is caught.
    """
    step = _validate_step(gen_ci_yml(_params()), "validate", "Run plugin validation")
    assert step["env"]["PLUGIN_SKIP_GITHUB_INTEGRITY"] == "1"
    assert "CLAUDE_PRIVATE_USERNAMES" not in step["env"]


def test_release_yml_validate_step_has_integrity_skip_env() -> None:
    """release.yml validate step keeps PLUGIN_SKIP_GITHUB_INTEGRITY and (per #140) drops private-usernames.

    Located by STEP, not by job name — the step moved from the single `release`
    job into the `validate` job when the release workflow was split so
    validation and the test shards run concurrently.
    """
    _job_name, _job, step = _hosting_job(gen_release_yml(_params()), "Run full plugin validation")
    assert step["env"]["PLUGIN_SKIP_GITHUB_INTEGRITY"] == "1"
    assert "CLAUDE_PRIVATE_USERNAMES" not in step["env"]


# ---------------------------------------------------------------------------
# #3 — matrix vs required-context: aggregate Test gate; contexts stay consistent
# ---------------------------------------------------------------------------


def test_ci_yml_has_aggregate_test_gate() -> None:
    """An aggregate gate job named exactly 'Test' exists and needs: [test]."""
    parsed = yaml.safe_load(gen_ci_yml(_params()))
    jobs = parsed["jobs"]
    assert "test-gate" in jobs, "missing aggregate Test gate job"
    gate = jobs["test-gate"]
    assert gate["name"] == "Test", "aggregate gate must be named exactly 'Test'"
    assert gate["needs"] == ["test"], "gate must depend on the matrix job"
    # if: always() so a failed/cancelled matrix produces a FAILING (not skipped)
    # required check.
    assert "always()" in str(gate.get("if", "")), "gate must run with if: always()"


def test_ci_yml_test_matrix_job_renamed_and_intact() -> None:
    """The matrix job keeps id 'test', is renamed 'Test matrix', matrix intact."""
    parsed = yaml.safe_load(gen_ci_yml(_params()))
    test_job = parsed["jobs"]["test"]
    assert test_job["name"] == "Test matrix"
    assert test_job["strategy"]["fail-fast"] is False
    assert set(test_job["strategy"]["matrix"]["os"]) == {"ubuntu-latest", "macos-latest"}


def test_ci_yml_required_contexts_match_emitted_job_names() -> None:
    """KEYSTONE: every required branch context is produced by a job display name.

    The branch ruleset requires DEFAULT_PLUGIN_CHECK_CONTEXTS = Lint/Validate/Test.
    Each must equal the ``name:`` of some job in the generated ci.yml so the
    required status check actually reports (root-cause #3 — otherwise the PR is
    stuck pending forever).
    """
    parsed = yaml.safe_load(gen_ci_yml(_params()))
    emitted_names = {job.get("name") for job in parsed["jobs"].values()}
    required = setup_branch_rules.default_check_contexts_for("plugin")
    assert required == ["Lint", "Validate", "Test"]
    for ctx in required:
        assert ctx in emitted_names, (
            f"required context {ctx!r} is not the display name of any ci.yml job "
            f"(emitted: {sorted(n for n in emitted_names if n)}) — the required "
            f"status check would never report"
        )


def test_ci_yml_matrix_lanes_do_not_satisfy_bare_test_context() -> None:
    """Sanity: the matrix job name is NOT a bare 'Test' (it would never report bare)."""
    parsed = yaml.safe_load(gen_ci_yml(_params()))
    # The matrix job reports 'Test matrix (<os>)', never bare 'Test'. The ONLY
    # job whose name is exactly 'Test' is the aggregate gate.
    bare_test_jobs = [jid for jid, j in parsed["jobs"].items() if j.get("name") == "Test"]
    assert bare_test_jobs == ["test-gate"], (
        f"exactly one job must produce the bare 'Test' context (the gate); got {bare_test_jobs}"
    )


# ---------------------------------------------------------------------------
# #1 — every job has a real timeout-minutes ceiling (incl. the new gate)
# ---------------------------------------------------------------------------


def test_ci_yml_every_job_including_gate_has_timeout() -> None:
    """Every ci.yml job — including the new aggregate gate — declares timeout-minutes."""
    parsed = yaml.safe_load(gen_ci_yml(_params()))
    for name, job in parsed["jobs"].items():
        assert "timeout-minutes" in job, f"ci.yml job '{name}' has no timeout-minutes"
        assert isinstance(job["timeout-minutes"], int)
        assert job["timeout-minutes"] >= 1


def test_ci_yml_validate_timeout_is_at_least_25() -> None:
    """The validate job's cold-install ceiling stays >= 25 (root-cause #1)."""
    parsed = yaml.safe_load(gen_ci_yml(_params()))
    assert parsed["jobs"]["validate"]["timeout-minutes"] >= 25


def test_release_yml_validate_timeout_is_at_least_25() -> None:
    """The cold-install ceiling stays >= 25 on the job that actually validates (root-cause #1).

    The budget belongs to whichever job runs CPV validation, which is where a
    cold `uvx --from git+...` install is paid. After the release workflow was
    split that is the `validate` job; the `release` job only downloads an
    artifact and calls `gh`, so pinning this assertion to the job NAMED
    "release" would have measured a coordination step's budget and let the real
    validation ceiling drop unnoticed.
    """
    job_name, job, _step = _hosting_job(gen_release_yml(_params()), "Run full plugin validation")
    assert job["timeout-minutes"] >= 25, f"job {job_name!r} validates but is capped below 25 min"


# ---------------------------------------------------------------------------
# #9 — notify is a no-op without the secret, and not emitted for the placeholder
# ---------------------------------------------------------------------------


def test_notify_job_guarded_on_secret_presence() -> None:
    """The notify job exposes HAS_MARKETPLACE_PAT and gates its real steps on it."""
    parsed = yaml.safe_load(gen_notify_marketplace_yml(_params()))
    job = parsed["jobs"]["notify"]
    assert job["env"]["HAS_MARKETPLACE_PAT"].strip() == "${{ secrets.MARKETPLACE_PAT != '' }}"
    # The first step always runs (no if:) and reports the no-op when missing.
    first = job["steps"][0]
    assert first["name"] == "Check marketplace secret"
    assert "if" not in first
    # The dispatch + summary + plugin-info steps are gated on the secret.
    gated = [s for s in job["steps"] if "env.HAS_MARKETPLACE_PAT" in str(s.get("if", ""))]
    assert len(gated) >= 3, f"expected the dispatch/info/summary steps gated; got {len(gated)}"
    # The repository-dispatch step (the one that would 404 without the secret)
    # must be gated.
    dispatch = next(
        s for s in job["steps"] if "repository-dispatch" in str(s.get("uses", ""))
    )
    assert dispatch.get("if") and "HAS_MARKETPLACE_PAT" in dispatch["if"]


def test_notify_not_emitted_for_placeholder_marketplace() -> None:
    """With no marketplace, notify-marketplace.yml is NOT in the file list."""
    files = generate_all_files(_params(marketplace=""))
    paths = {p for p, _, _ in files}
    assert ".github/workflows/notify-marketplace.yml" not in paths
    # CI + release ARE still emitted.
    assert ".github/workflows/ci.yml" in paths
    assert ".github/workflows/release.yml" in paths


def test_notify_emitted_for_real_marketplace() -> None:
    """With a real marketplace, notify-marketplace.yml IS emitted."""
    files = generate_all_files(_params(marketplace="emasoft-plugins"))
    paths = {p for p, _, _ in files}
    assert ".github/workflows/notify-marketplace.yml" in paths


def test_notify_emitted_for_placeholder_when_forced() -> None:
    """--force-notify emits notify-marketplace.yml even for the placeholder."""
    files = generate_all_files(_params(marketplace="", force_notify=True))
    paths = {p for p, _, _ in files}
    assert ".github/workflows/notify-marketplace.yml" in paths
