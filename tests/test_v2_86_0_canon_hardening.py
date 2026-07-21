#!/usr/bin/env python3
"""Tests for the v2.86.0 cpv-canonical-pipeline hardening (issue #22).

Adopts the security hardening from ai-maestro-visual-communicator-plugin's
TRDD-5f41ad36 into CPV's canonical templates so every plugin migrating via
``standardize --force-templates`` lands on a strong baseline:

* SHA-pinned third-party actions (no major-tag drift)
* actionlint workflow-syntax gate
* commitlint conventional-commit gate on PRs
* macOS matrix on the test job
* Atomic ``git push --atomic origin HEAD <tag>``
* Bypass-guard prefix-pattern (CPV_SKIP_*, SKIP_*, NO_VERIFY)
* env: sanitization for every ${{...}} consumed by run: blocks
* CHANGELOG-section extraction in release.yml
* cliff.toml em-dash separator (scope + short-hash commit display restored by #144)
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

from generate_plugin_repo import (  # noqa: E402
    PluginParams,
    gen_ci_yml,
    gen_cliff_toml,
    gen_notify_marketplace_yml,
    gen_publish_py,
    gen_release_yml,
)


def _params(**overrides) -> PluginParams:
    kwargs = {
        "name": "test-plugin",
        "description": "test",
        "author": "X",
        "author_email": "x@x",
        "python_version": "3.12",
        "github_owner": "Emasoft",
        "marketplace": "test-marketplace",
    }
    kwargs.update(overrides)
    return PluginParams(**kwargs)


# ---------------------------------------------------------------------------
# SHA-pinned actions
# ---------------------------------------------------------------------------


def test_ci_yml_third_party_actions_are_SHA_pinned():
    """All third-party (non-actions/, non-github/) uses must be SHA-pinned."""
    yml = gen_ci_yml(_params())
    parsed = yaml.safe_load(yml)
    assert parsed is not None, "ci.yml must be parseable"

    third_party_uses: list[str] = []
    for job in parsed["jobs"].values():
        for step in job.get("steps", []):
            uses = step.get("uses", "")
            if not uses:
                continue
            owner = uses.split("/", 1)[0]
            # First-party orgs (gh-actions.md exemption).
            if owner in {"actions", "github"}:
                continue
            third_party_uses.append(uses)

    assert third_party_uses, "should have at least one third-party action"
    for uses in third_party_uses:
        sha_part = uses.rsplit("@", 1)[1]
        assert re.fullmatch(r"[0-9a-f]{40}", sha_part), f"third-party action not SHA-pinned: {uses}"


def test_release_yml_third_party_actions_are_SHA_pinned():
    """release.yml's third-party uses must be SHA-pinned."""
    yml = gen_release_yml(_params())
    parsed = yaml.safe_load(yml)
    for job in parsed["jobs"].values():
        for step in job.get("steps", []):
            uses = step.get("uses", "")
            if not uses or uses.split("/", 1)[0] in {"actions", "github"}:
                continue
            sha_part = uses.rsplit("@", 1)[1]
            assert re.fullmatch(r"[0-9a-f]{40}", sha_part), f"third-party action not SHA-pinned: {uses}"


def test_notify_marketplace_yml_third_party_actions_are_SHA_pinned():
    """notify-marketplace.yml's peter-evans/repository-dispatch must be SHA-pinned."""
    yml = gen_notify_marketplace_yml(_params())
    parsed = yaml.safe_load(yml)
    for job in parsed["jobs"].values():
        for step in job.get("steps", []):
            uses = step.get("uses", "")
            if not uses or uses.split("/", 1)[0] in {"actions", "github"}:
                continue
            sha_part = uses.rsplit("@", 1)[1]
            assert re.fullmatch(r"[0-9a-f]{40}", sha_part), f"third-party action not SHA-pinned: {uses}"


# ---------------------------------------------------------------------------
# actionlint + commitlint
# ---------------------------------------------------------------------------


def test_ci_yml_has_actionlint_lint_step():
    """Lint job must include rhysd/actionlint for workflow-syntax checks."""
    yml = gen_ci_yml(_params())
    parsed = yaml.safe_load(yml)
    lint_steps = parsed["jobs"]["lint"]["steps"]
    uses_list = [s.get("uses", "") for s in lint_steps]
    assert any("rhysd/actionlint" in u for u in uses_list), f"actionlint missing from lint steps: {uses_list}"


def test_ci_yml_has_commitlint_job_on_pr_only():
    """Commitlint job exists, gated on pull_request only (not on push to main)."""
    yml = gen_ci_yml(_params())
    parsed = yaml.safe_load(yml)
    assert "commitlint" in parsed["jobs"]
    job = parsed["jobs"]["commitlint"]
    assert "pull_request" in job.get("if", "")
    uses_list = [s.get("uses", "") for s in job["steps"]]
    assert any("wagoid/commitlint-github-action" in u for u in uses_list)


# ---------------------------------------------------------------------------
# macOS matrix
# ---------------------------------------------------------------------------


def test_ci_yml_test_job_runs_matrix_with_macos():
    """Test job must declare a matrix that includes macos-latest."""
    yml = gen_ci_yml(_params())
    parsed = yaml.safe_load(yml)
    test_job = parsed["jobs"]["test"]
    matrix = test_job["strategy"]["matrix"]
    assert "macos-latest" in matrix["os"]
    assert "ubuntu-latest" in matrix["os"]
    # fail-fast: false so each OS reports its own failure.
    assert test_job["strategy"].get("fail-fast") is False


# ---------------------------------------------------------------------------
# Atomic push + bypass-guard in publish.py
# ---------------------------------------------------------------------------


def test_publish_py_uses_atomic_push():
    """Generated publish.py must push HEAD + every release tag in ONE atomic push.

    The refs became DYNAMIC in v2.156.0 (the push now also carries the
    `{name}--v{version}` dependency-resolution tag), so this asserts the INVARIANT
    — an atomic push whose refs are HEAD + the tags — instead of the old literal
    `git push --atomic origin HEAD`, which no longer appears verbatim. The
    atomicity guarantee itself is unchanged.
    """
    py = gen_publish_py(_params())
    assert '"git", "push", "--atomic", "origin", *push_refs' in py
    assert 'push_refs = ["HEAD", tag]' in py
    # The dependency tag must ride in the SAME push — never a separate one, or a
    # release could ship with the plain tag and not the dependency tag.
    assert "dep_tag" in py
    # Old separated form must NOT survive.
    assert 'git", "push", "origin", "HEAD", "--tags"' not in py


def test_publish_py_has_prefix_match_bypass_guard():
    """publish.py's stage_bypass_guard must use prefix matching, not a fixed list."""
    py = gen_publish_py(_params())
    assert "forbidden_prefixes" in py
    assert '"PLUGIN_SKIP_"' in py
    assert '"CPV_SKIP_"' in py
    assert '"SKIP_"' in py
    # Infrastructure exemptions retained.
    assert '"CPV_SKIP_GITHUB_INTEGRITY"' in py
    assert '"CPV_SKIP_GH_AUTH_CHECK"' in py


# ---------------------------------------------------------------------------
# env: sanitization in notify-marketplace.yml
# ---------------------------------------------------------------------------


def test_notify_marketplace_yml_sanitizes_github_expressions():
    """Every github.* expression consumed by a run: block must go through env:."""
    yml = gen_notify_marketplace_yml(_params())
    parsed = yaml.safe_load(yml)
    notify_job = parsed["jobs"]["notify"]
    for step in notify_job["steps"]:
        run_script = step.get("run", "")
        if not run_script:
            continue
        # Whenever a run: block references a github.* expression, that
        # expression should have been bound to an env: var first; the
        # run-script itself should reference $VAR, not raw ${{ ... }}.
        # An exception is allowed only for github.* expressions wrapped
        # in a comment block.
        non_comment_lines = [line for line in run_script.splitlines() if not line.strip().startswith("#")]
        raw_github_refs = sum("${{ github." in line or "${{ steps." in line for line in non_comment_lines)
        # If there IS a github./steps. ref in the run: block, the step
        # must declare env: so the script can use $VAR.
        if raw_github_refs > 0:
            assert step.get("env"), (
                f"step '{step.get('name')}' references github./steps. in run "
                f"block but declares no env: mapping for sanitization"
            )


def test_notify_marketplace_yml_always_uses_canonical_secret():
    """v2.86.0: secret name is unconditionally MARKETPLACE_PAT."""
    yml = gen_notify_marketplace_yml(_params())
    assert "secrets.MARKETPLACE_PAT" in yml
    # No deviant names ever appear in the canonical template.
    assert "MARKETPLACE_DISPATCH_TOKEN" not in yml
    assert "MARKETPLACE_TOKEN" not in yml


# ---------------------------------------------------------------------------
# release.yml CHANGELOG-section extraction
# ---------------------------------------------------------------------------


def test_release_yml_extracts_changelog_section_not_full_file():
    """release.yml must extract the matching ## [X.Y.Z] section, not the whole file."""
    yml = gen_release_yml(_params())
    assert "awk -v ver=" in yml
    # Em-dash separator (canonical) is the primary match form.
    assert "[—-]" in yml  # accepts both em-dash and legacy hyphen
    # Generates a section file, not the whole CHANGELOG.
    assert "Release body extracted from CHANGELOG.md section" in yml


# ---------------------------------------------------------------------------
# cliff.toml em-dash + scope-strip
# ---------------------------------------------------------------------------


def test_cliff_toml_uses_em_dash_in_section_header():
    """cliff.toml header template must use ` — ` (em-dash), not ` - ` (hyphen)."""
    toml = gen_cliff_toml(_params())
    # The canonical form is `## [{{ version | trim_start_matches(pat="v") }}] — {{ ... }}`
    assert "}] — {{ timestamp" in toml
    # Legacy hyphen separator MUST be gone.
    assert "}] - {{ timestamp" not in toml


def test_cliff_toml_renders_scope_and_short_hash_in_commits():
    """cliff.toml RESTORES the commit scope prefix + short hash in commit lines.

    Issue #144 SUPERSEDES the v2.86.0 "drop scope as redundant noise" decision:
    dropping the scope + hash lost changelog traceability (a reader could no
    longer tell which component a change touched or which commit it was). The
    scope is rendered CONDITIONALLY (``{% if commit.scope %}``) so unscoped
    commits are unaffected, and the 7-char short hash is appended in parens.
    This stays compatible with release.yml's em-dash awk section-extractor,
    which keys on the SECTION header, not the per-commit line format.
    """
    toml = gen_cliff_toml(_params())
    assert "{% if commit.scope %}**{{ commit.scope }}:** {% endif %}" in toml
    assert "commit.id | truncate(length=7" in toml


def test_cliff_toml_drops_striptags():
    """cliff.toml's group renderer must NOT pipe through striptags."""
    toml = gen_cliff_toml(_params())
    assert "striptags" not in toml


# ---------------------------------------------------------------------------
# v2.86.0+ follow-on hardening: per-job timeouts (issues #90 / #114)
# ---------------------------------------------------------------------------


def test_ci_yml_every_job_has_timeout_minutes():
    """Issue #90: every ci.yml job must declare timeout-minutes."""
    yml = gen_ci_yml(_params())
    parsed = yaml.safe_load(yml)
    for name, job in parsed["jobs"].items():
        assert "timeout-minutes" in job, f"ci.yml job '{name}' has no timeout-minutes"
        assert isinstance(job["timeout-minutes"], int)


def test_ci_yml_validate_job_timeout_is_cold_install_ceiling():
    """Issue #114: the validate job's timeout must allow a cold uvx build (>= 25)."""
    yml = gen_ci_yml(_params())
    parsed = yaml.safe_load(yml)
    assert parsed["jobs"]["validate"]["timeout-minutes"] >= 25


def test_ci_yml_validate_job_enables_uv_cache():
    """Issue #114: setup-uv in the validate job must enable the UV cache so only
    the first cold run pays the git-build cost."""
    yml = gen_ci_yml(_params())
    parsed = yaml.safe_load(yml)
    setup_steps = [
        s for s in parsed["jobs"]["validate"]["steps"] if "astral-sh/setup-uv" in s.get("uses", "")
    ]
    assert setup_steps, "validate job must use astral-sh/setup-uv"
    assert any(s.get("with", {}).get("enable-cache") is True for s in setup_steps)


def test_release_yml_job_has_cold_install_timeout():
    """Issue #114: the release job's timeout must allow a cold uvx build (>= 25)."""
    yml = gen_release_yml(_params())
    parsed = yaml.safe_load(yml)
    assert parsed["jobs"]["release"]["timeout-minutes"] >= 25


def test_notify_marketplace_yml_job_has_timeout():
    """Issue #90: the notify job must declare timeout-minutes."""
    yml = gen_notify_marketplace_yml(_params())
    parsed = yaml.safe_load(yml)
    assert "timeout-minutes" in parsed["jobs"]["notify"]


# ---------------------------------------------------------------------------
# v2.86.0+ follow-on hardening: first-party actions SHA-pinned (issue #118 d1)
# ---------------------------------------------------------------------------


def _all_uses(parsed: dict) -> list[str]:
    out: list[str] = []
    for job in parsed["jobs"].values():
        for step in job.get("steps", []):
            uses = step.get("uses", "")
            if uses:
                out.append(uses)
    return out


def test_ci_yml_all_actions_sha_pinned_including_first_party():
    """Issue #118 d1: EVERY action (incl. actions/*) in ci.yml is SHA-pinned."""
    parsed = yaml.safe_load(gen_ci_yml(_params()))
    for uses in _all_uses(parsed):
        sha_part = uses.rsplit("@", 1)[1]
        assert re.fullmatch(r"[0-9a-f]{40}", sha_part), f"action not SHA-pinned: {uses}"


def test_release_yml_all_actions_sha_pinned_including_first_party():
    """Issue #118 d1: EVERY action (incl. actions/*) in release.yml is SHA-pinned."""
    parsed = yaml.safe_load(gen_release_yml(_params()))
    for uses in _all_uses(parsed):
        sha_part = uses.rsplit("@", 1)[1]
        assert re.fullmatch(r"[0-9a-f]{40}", sha_part), f"action not SHA-pinned: {uses}"


def test_notify_marketplace_yml_all_actions_sha_pinned_including_first_party():
    """Issue #118 d1: EVERY action in notify-marketplace.yml is SHA-pinned."""
    parsed = yaml.safe_load(gen_notify_marketplace_yml(_params()))
    for uses in _all_uses(parsed):
        sha_part = uses.rsplit("@", 1)[1]
        assert re.fullmatch(r"[0-9a-f]{40}", sha_part), f"action not SHA-pinned: {uses}"


# ---------------------------------------------------------------------------
# v2.86.0+ follow-on hardening: SLSA / SBOM / provenance in release.yml (#121)
# ---------------------------------------------------------------------------


def test_release_yml_generates_sbom():
    """Issue #121: release.yml must run an SBOM tool."""
    yml = gen_release_yml(_params())
    assert "anchore/sbom-action" in yml or "actions/attest-sbom" in yml


def test_release_yml_attests_build_provenance():
    """Issue #121: release.yml must produce a build-provenance attestation."""
    yml = gen_release_yml(_params())
    assert "actions/attest-build-provenance" in yml


def test_release_yml_uploads_per_asset_checksums():
    """Issue #121: release.yml must compute SHA256SUMS and upload them as an asset."""
    yml = gen_release_yml(_params())
    assert "SHA256SUMS" in yml
    assert "sha256sum" in yml
    # SHA256SUMS must be among the assets uploaded with the release.
    parsed = yaml.safe_load(yml)
    upload_steps = [
        s.get("run", "")
        for s in parsed["jobs"]["release"]["steps"]
        if "gh release" in s.get("run", "")
    ]
    assert any("SHA256SUMS" in r for r in upload_steps)


def test_release_yml_declares_attestation_permissions():
    """Issue #121: the release job needs id-token + attestations write for the
    provenance attestation, while keeping contents: write for the release."""
    parsed = yaml.safe_load(gen_release_yml(_params()))
    perms = parsed["jobs"]["release"]["permissions"]
    assert perms.get("contents") == "write"
    assert perms.get("id-token") == "write"
    assert perms.get("attestations") == "write"
