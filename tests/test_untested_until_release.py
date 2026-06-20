"""Tests for the "untested-until-release" advisory heuristic (#115 part-5).

A NON-BLOCKING WARNING surface: it flags a `.github/workflows/*.yml` that
BUILDS/STAGES a compiled BINARY artifact in a step reachable ONLY from
tag/release triggers (no push/PR path), with NO sibling CI push-triggered smoke
job that exercises the build/stage. Born from the janitor v0.7.0 incident — a
tag-only staging step copied from the wrong ``target/`` dir, passed
actionlint+zizmor+CPV statically, and failed on all four platforms at release
because no push/PR job ever exercised it.

THE MAKE-OR-BREAK PRECISION REQUIREMENT (asserted against the REAL
``gen_release_yml`` output): the standard canonical ``release.yml`` is ALSO
tag-triggered and ALSO runs ``gh release upload … SHA256SUMS`` — it literally
contains the strings ``gh release upload`` AND ``SHA256SUMS``. It MUST produce
ZERO findings (else CPV fires on EVERY canon plugin — catastrophic FP). The
discriminator is a compiled-artifact BUILD/STAGE step, which the standard
release.yml does NOT have (it stages plain text reports).

These are FN-safe TWO-SIDED tests: the FIRES side proves the heuristic catches
the real janitor shape; the CLEARS side proves it never fires on the standard
canon, on a smoke-mitigated repo, on a plain CI workflow, or on a text-only
release.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = REPO_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from cpv_pipeline_profile import (  # noqa: E402
    classify_workflow_triggers,
    repo_has_ci_build_smoke,
    untested_until_release_workflows,
    workflow_has_compiled_artifact_build,
)
from cpv_validation_common import ValidationReport  # noqa: E402
from generate_plugin_repo import PluginParams, gen_ci_yml, gen_release_yml  # noqa: E402
from validate_plugin import check_untested_until_release  # noqa: E402

# ── Fixtures ────────────────────────────────────────────────────────────────

# The REAL janitor memgrep-release.yml shape (#115). Two-phase: a `build`
# matrix produces one cargo binary + checksum per target; a `release` job
# collects + uploads them. Triggers: push tags + workflow_dispatch (both
# release-class). NO sibling CI smoke job that builds.
MEMGREP_RELEASE_YML = """# Build the memgrep binary per target and attach to the release.
name: memgrep release binaries

on:
  push:
    tags:
      - 'v*.*.*'
  workflow_dispatch:
    inputs:
      tag:
        description: "Existing release tag to attach binaries to."
        required: false
        default: ""

permissions: {}

concurrency:
  group: ${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: true

jobs:
  build:
    name: Build memgrep (${{ matrix.asset }})
    runs-on: ${{ matrix.os }}
    timeout-minutes: 30
    permissions:
      contents: read
    strategy:
      fail-fast: false
      matrix:
        include:
          - os: macos-14
            target: aarch64-apple-darwin
            asset: memgrep-darwin-arm64
          - os: ubuntu-latest
            target: x86_64-unknown-linux-gnu
            asset: memgrep-linux-x64
    steps:
      - uses: actions/checkout@9f698171ed81b15d1823a05fc7211befd50c8ae0  # v6.0.3
        with:
          persist-credentials: false

      - name: Ensure the Rust target is installed
        run: rustup target add "${{ matrix.target }}"

      - name: Build memgrep (release)
        run: cargo build --release --locked --target "${{ matrix.target }}" --manifest-path scripts/memgrep/Cargo.toml

      - name: Stage binary + checksum
        env:
          ASSET: ${{ matrix.asset }}
          TARGET: ${{ matrix.target }}
        run: bash scripts/memgrep/stage.sh "$TARGET" "$ASSET"

      - name: Upload build artifact
        uses: actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a  # v7.0.1
        with:
          name: ${{ matrix.asset }}
          path: |
            dist/${{ matrix.asset }}
            dist/${{ matrix.asset }}.sha256

  release:
    name: Attach binaries to the release
    needs: build
    runs-on: ubuntu-latest
    permissions:
      contents: write
    steps:
      - name: Download all build artifacts
        uses: actions/download-artifact@3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c  # v8.0.1
        with:
          path: artifacts

      - name: Collect binaries + combined checksums
        run: |
          set -euo pipefail
          mkdir -p out
          find artifacts -type f -name 'memgrep-*' ! -name '*.sha256' -exec cp {} out/ \\;
          ( cd out && cat ../artifacts/*/*.sha256 > SHA256SUMS )

      - name: Upload binaries + checksums to the release
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
          TAG: ${{ inputs.tag != '' && inputs.tag || github.ref_name }}
        run: |
          gh release upload "$TAG" out/memgrep-* out/SHA256SUMS --clobber
"""

# A CI workflow (push branches) that builds ONE target — the smoke job that
# mitigates the untested-until-release finding (it exercises the same cargo
# build on every push, so a broken build fails CI before a tag is cut).
CI_BUILD_SMOKE_YML = """name: CI
on:
  push:
    branches: [master, main]
  pull_request:
    branches: [master, main]
jobs:
  build-smoke:
    name: Build smoke
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@9f698171ed81b15d1823a05fc7211befd50c8ae0  # v6.0.3
      - name: Build one target
        run: cargo build --release --target x86_64-unknown-linux-gnu --manifest-path scripts/memgrep/Cargo.toml
      - name: Stage + execute the staged binary
        run: bash scripts/memgrep/stage.sh x86_64-unknown-linux-gnu memgrep-linux-x64
"""

# A release-only workflow that uploads ONLY text files (no matrix, no compile,
# no compiled-artifact stage) — the standard-canon SHAPE. Must NOT fire.
TEXT_ONLY_RELEASE_YML = """name: Release
on:
  push:
    tags:
      - 'v*.*.*'
permissions:
  contents: read
jobs:
  release:
    runs-on: ubuntu-latest
    permissions:
      contents: write
    steps:
      - uses: actions/checkout@9f698171ed81b15d1823a05fc7211befd50c8ae0  # v6.0.3
      - name: Compute checksums
        run: |
          : > SHA256SUMS
          for f in validation-report.txt sbom.spdx.json; do
            sha256sum "$f" >> SHA256SUMS
          done
      - name: Upload to the release
        run: gh release upload "$TAG" validation-report.txt sbom.spdx.json SHA256SUMS --clobber
"""

# A plain push/PR CI workflow with NO release trigger and NO build. Must NOT fire.
PLAIN_CI_YML = """name: CI
on:
  push:
    branches: [master, main]
  pull_request:
    branches: [master, main]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@9f698171ed81b15d1823a05fc7211befd50c8ae0  # v6.0.3
      - run: pytest tests/ -v
      - run: ruff check scripts/
"""


# ── Helpers ─────────────────────────────────────────────────────────────────


def _warnings_for(files: dict[str, str]) -> list[str]:
    """Write the given ``{filename: content}`` workflow files into a temp
    plugin tree and return the RC-UNTESTED-UNTIL-RELEASE WARNING messages."""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        wf_dir = root / ".github" / "workflows"
        wf_dir.mkdir(parents=True)
        for name, content in files.items():
            (wf_dir / name).write_text(content, encoding="utf-8")
        report = ValidationReport()
        check_untested_until_release(root, report)
        return [
            r.message
            for r in report.results
            if r.level == "WARNING" and "RC-UNTESTED-UNTIL-RELEASE" in r.message
        ]


def _std_release_yml() -> str:
    p = PluginParams(
        name="t", description="d", author="A", author_email="a@e.com", github_owner="Emasoft"
    )
    return gen_release_yml(p)


def _std_ci_yml() -> str:
    p = PluginParams(
        name="t", description="d", author="A", author_email="a@e.com", github_owner="Emasoft"
    )
    return gen_ci_yml(p)


# ── FIRES — the heuristic catches the real janitor shape ─────────────────────


def test_fires_on_memgrep_release_shape_no_smoke() -> None:
    """janitor memgrep-release.yml shape (push tags + dispatch; matrix cargo
    build; no sibling CI build) → exactly one WARNING."""
    warnings = _warnings_for({"memgrep-release.yml": MEMGREP_RELEASE_YML})
    assert len(warnings) == 1, f"expected exactly 1 WARNING, got {len(warnings)}: {warnings}"
    assert "memgrep-release.yml" in warnings[0]
    assert "builds/stages compiled" in warnings[0]


def test_fires_warning_names_the_offending_workflow_file() -> None:
    """The WARNING carries the offending workflow's path in its file= column."""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        wf_dir = root / ".github" / "workflows"
        wf_dir.mkdir(parents=True)
        (wf_dir / "memgrep-release.yml").write_text(MEMGREP_RELEASE_YML, encoding="utf-8")
        report = ValidationReport()
        check_untested_until_release(root, report)
        warns = [r for r in report.results if r.level == "WARNING"]
        assert len(warns) == 1
        assert warns[0].file == ".github/workflows/memgrep-release.yml"


# ── CLEARS — the critical catastrophic-FP guard (REAL gen_release_yml) ────────


def test_clears_standard_canonical_release_yml_real_output() -> None:
    """The REAL gen_release_yml output (tag-triggered, gh release upload of
    validation-report/sbom/SHA256SUMS, NO build matrix) → ZERO findings.

    THIS is the make-or-break guard: it MUST be asserted against the real
    generator output, not a hand-written stub, because the standard release.yml
    literally contains ``gh release upload`` AND ``SHA256SUMS``.
    """
    std = _std_release_yml()
    # Sanity-pin the FP trap: the real text DOES contain the upload + checksum
    # tokens — so a heuristic keyed on those alone WOULD fire. Our discriminator
    # is the binary build/stage, which is absent.
    assert "gh release upload" in std
    assert "SHA256SUMS" in std
    warnings = _warnings_for({"release.yml": std})
    assert warnings == [], f"standard release.yml must produce ZERO findings, got: {warnings}"


def test_clears_standard_release_plus_standard_ci_real_output() -> None:
    """The full standard canon (real gen_release_yml + gen_ci_yml together) →
    ZERO findings — the exact two-file shape every CPV-canon plugin ships."""
    warnings = _warnings_for({"release.yml": _std_release_yml(), "ci.yml": _std_ci_yml()})
    assert warnings == [], f"standard canon must produce ZERO findings, got: {warnings}"


# ── CLEARS — a CI build smoke job mitigates ──────────────────────────────────


def test_clears_when_ci_build_smoke_present() -> None:
    """memgrep-release.yml shape PLUS a ci.yml that builds one target on push →
    NO finding (the build is exercised by CI, not untested-until-release)."""
    warnings = _warnings_for(
        {"memgrep-release.yml": MEMGREP_RELEASE_YML, "ci.yml": CI_BUILD_SMOKE_YML}
    )
    assert warnings == [], f"a CI build smoke job must mitigate, got: {warnings}"


def test_clears_when_pr_only_build_smoke_present() -> None:
    """A pull_request-triggered build job (no push) also counts as ci-class
    smoke and mitigates."""
    pr_only_smoke = """name: CI
on:
  pull_request:
    branches: [main]
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - run: cargo build --target x86_64-unknown-linux-gnu
"""
    warnings = _warnings_for(
        {"memgrep-release.yml": MEMGREP_RELEASE_YML, "ci.yml": pr_only_smoke}
    )
    assert warnings == [], f"a PR-triggered build smoke must mitigate, got: {warnings}"


# ── CLEARS — plain CI / text-only release / no workflows ─────────────────────


def test_clears_plain_push_pr_ci_only() -> None:
    """A plain push/PR ci.yml (no release trigger, no build) → NO finding."""
    warnings = _warnings_for({"ci.yml": PLAIN_CI_YML})
    assert warnings == [], f"a plain CI workflow must produce no findings, got: {warnings}"


def test_clears_release_only_text_upload_no_build() -> None:
    """A release-only workflow that uploads ONLY text files (no matrix, no
    compile, no compiled-artifact stage) → NO finding."""
    warnings = _warnings_for({"release.yml": TEXT_ONLY_RELEASE_YML})
    assert warnings == [], f"a text-only release upload must not fire, got: {warnings}"


def test_clears_no_workflows_dir() -> None:
    """A plugin with no .github/workflows directory → no findings, no crash."""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        report = ValidationReport()
        check_untested_until_release(root, report)
        assert [r for r in report.results if r.level == "WARNING"] == []


def test_clears_empty_workflow_dir() -> None:
    """An empty .github/workflows directory → no findings, no crash."""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        (root / ".github" / "workflows").mkdir(parents=True)
        report = ValidationReport()
        check_untested_until_release(root, report)
        assert [r for r in report.results if r.level == "WARNING"] == []


# ── WARNING is NON-BLOCKING (advisory only) ──────────────────────────────────


def test_warning_does_not_block_strict_exit_code() -> None:
    """A plugin whose ONLY finding is this WARNING still exits 0 under --strict
    (WARNING never changes the verdict / blocks --strict — advisory only)."""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        wf_dir = root / ".github" / "workflows"
        wf_dir.mkdir(parents=True)
        (wf_dir / "memgrep-release.yml").write_text(MEMGREP_RELEASE_YML, encoding="utf-8")
        report = ValidationReport()
        check_untested_until_release(root, report)
        # Exactly one WARNING fired, and the strict exit code is still OK (0).
        assert report.has_warning is True
        assert report.exit_code == 0
        assert report.exit_code_strict() == 0


def test_finding_is_warning_severity_only() -> None:
    """The finding is WARNING severity — never CRITICAL/MAJOR/MINOR/NIT."""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        wf_dir = root / ".github" / "workflows"
        wf_dir.mkdir(parents=True)
        (wf_dir / "memgrep-release.yml").write_text(MEMGREP_RELEASE_YML, encoding="utf-8")
        report = ValidationReport()
        check_untested_until_release(root, report)
        rc_findings = [r for r in report.results if "RC-UNTESTED-UNTIL-RELEASE" in r.message]
        assert rc_findings, "expected the finding to fire"
        assert all(r.level == "WARNING" for r in rc_findings)
        assert report.has_critical is False
        assert report.has_major is False
        assert report.has_minor is False
        assert report.has_nit is False


# ── Trigger classifier — unit precision ──────────────────────────────────────


def test_classify_push_tags_only_is_release_class() -> None:
    """`push: tags:` with no `branches:` is release-class, not ci-class."""
    has_release, has_ci = classify_workflow_triggers(MEMGREP_RELEASE_YML)
    assert has_release is True
    assert has_ci is False


def test_classify_real_standard_release_is_release_only() -> None:
    """The real standard release.yml is release-class with no ci-class."""
    has_release, has_ci = classify_workflow_triggers(_std_release_yml())
    assert has_release is True
    assert has_ci is False


def test_classify_real_standard_ci_is_ci_class() -> None:
    """The real standard ci.yml (push branches + PR + merge_group) is ci-class."""
    _has_release, has_ci = classify_workflow_triggers(_std_ci_yml())
    assert has_ci is True


def test_classify_push_with_branches_is_ci_class() -> None:
    """A `push:` with `branches:` is ci-class (a normal-commit trigger)."""
    text = "on:\n  push:\n    branches: [main]\n    tags: ['v*']\njobs: {}\n"
    has_release, has_ci = classify_workflow_triggers(text)
    # push has BOTH tags and branches → ci-class (a branch push fires it).
    assert has_ci is True
    assert has_release is False


def test_classify_release_event_is_release_class() -> None:
    """A `release:` event (e.g. on published) is release-class."""
    text = "on:\n  release:\n    types: [published]\njobs: {}\n"
    has_release, has_ci = classify_workflow_triggers(text)
    assert has_release is True
    assert has_ci is False


def test_classify_schedule_is_ci_class() -> None:
    """A `schedule:` cron trigger is ci-class (recurs without a tag)."""
    text = "on:\n  schedule:\n    - cron: '0 0 * * *'\njobs: {}\n"
    _has_release, has_ci = classify_workflow_triggers(text)
    assert has_ci is True


def test_classify_bare_push_is_ci_class() -> None:
    """A bare `push` (no tags/branches sub-keys) fires on every branch push →
    ci-class (conservative: counts as smoke coverage)."""
    text = "on: [push]\njobs: {}\n"
    _has_release, has_ci = classify_workflow_triggers(text)
    assert has_ci is True


# ── Build/stage discriminator — unit precision ───────────────────────────────


def test_build_signal_fires_on_cargo_build() -> None:
    """`cargo build` is a compile step → build signal fires."""
    assert workflow_has_compiled_artifact_build("run: cargo build --release") is True


def test_build_signal_fires_on_go_build() -> None:
    """`go build` is a compile step → build signal fires."""
    assert workflow_has_compiled_artifact_build("run: go build -o bin/app ./cmd") is True


def test_build_signal_fires_on_stage_sh() -> None:
    """A `stage.sh`-style staging script → build/stage signal fires."""
    assert workflow_has_compiled_artifact_build("run: bash scripts/stage.sh x86_64 asset") is True


def test_build_signal_fires_on_matrix_target() -> None:
    """A build matrix over targets → build signal fires."""
    text = "strategy:\n  matrix:\n    target: [aarch64-apple-darwin, x86_64-unknown-linux-gnu]\n"
    assert workflow_has_compiled_artifact_build(text) is True


def test_build_signal_clears_on_real_standard_release() -> None:
    """The REAL standard release.yml has NO compiled-artifact build/stage."""
    assert workflow_has_compiled_artifact_build(_std_release_yml()) is False


def test_build_signal_clears_on_real_standard_ci() -> None:
    """The REAL standard ci.yml has NO compiled-artifact build/stage."""
    assert workflow_has_compiled_artifact_build(_std_ci_yml()) is False


def test_build_signal_clears_on_bare_build_step_name() -> None:
    """A step NAMED 'Build' with no compile run: is NOT a build signal — only a
    real compile/stage run: counts (a text-report upload is not a build)."""
    text = "steps:\n  - name: Build the report\n    run: gh release upload x report.txt SHA256SUMS\n"
    assert workflow_has_compiled_artifact_build(text) is False


def test_build_signal_clears_on_make_in_english_comment() -> None:
    """`make` inside an English comment ("an attacker can make X …") is NOT a
    build signal — the bare-tool tokens fire only at a shell command position.

    Regression: CPV's own bot-auto-merge.yml has the comment "an attacker can
    make Dependabot the last actor" — the old loose `make <word>` branch matched
    it. Command-anchoring fixes it.
    """
    text = "# trust boundary (an attacker can make Dependabot the last actor via\n"
    assert workflow_has_compiled_artifact_build(text) is False


def test_build_signal_clears_on_make_in_step_name() -> None:
    """`make` in a step NAME (not a `run:` command position) is NOT a build."""
    assert workflow_has_compiled_artifact_build("  - name: make a sandwich\n") is False


def test_build_signal_clears_on_compiler_name_in_prose() -> None:
    """A bare compiler NAME in prose ("gcc is a great compiler") is NOT a build."""
    assert workflow_has_compiled_artifact_build("# gcc is a great compiler\n") is False


def test_build_signal_fires_on_make_at_command_position() -> None:
    """`make <target>` at a real `run:` command position IS a build signal."""
    assert workflow_has_compiled_artifact_build("      - run: make release\n") is True


def test_build_signal_fires_on_raw_compiler_at_command_position() -> None:
    """A raw compiler invocation (`gcc …`) at a command position IS a build."""
    assert workflow_has_compiled_artifact_build("      - run: gcc -O2 a.c -o a\n") is True


def test_build_signal_fires_on_make_after_shell_separator() -> None:
    """`make` after a `&&` shell separator (a chained command) IS a build."""
    assert workflow_has_compiled_artifact_build("    run: cmake -S . -B build && make\n") is True


# ── Smoke-detection helper — unit precision ──────────────────────────────────


def test_repo_smoke_detects_ci_build() -> None:
    """A ci-class workflow that builds → repo_has_ci_build_smoke True."""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        wf_dir = root / ".github" / "workflows"
        wf_dir.mkdir(parents=True)
        (wf_dir / "ci.yml").write_text(CI_BUILD_SMOKE_YML, encoding="utf-8")
        assert repo_has_ci_build_smoke(root) is True


def test_repo_smoke_ignores_release_only_build() -> None:
    """A release-only build workflow is NOT smoke (it never runs on push)."""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        wf_dir = root / ".github" / "workflows"
        wf_dir.mkdir(parents=True)
        (wf_dir / "memgrep-release.yml").write_text(MEMGREP_RELEASE_YML, encoding="utf-8")
        assert repo_has_ci_build_smoke(root) is False


def test_repo_smoke_ignores_ci_without_build() -> None:
    """A ci-class workflow with no build step is NOT a build smoke."""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        wf_dir = root / ".github" / "workflows"
        wf_dir.mkdir(parents=True)
        (wf_dir / "ci.yml").write_text(PLAIN_CI_YML, encoding="utf-8")
        assert repo_has_ci_build_smoke(root) is False


# ── Orchestrating helper — direct unit precision ─────────────────────────────


def test_untested_until_release_workflows_returns_offender_path() -> None:
    """The public orchestrating helper returns the offending workflow Path."""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        wf_dir = root / ".github" / "workflows"
        wf_dir.mkdir(parents=True)
        (wf_dir / "memgrep-release.yml").write_text(MEMGREP_RELEASE_YML, encoding="utf-8")
        offenders = untested_until_release_workflows(root)
        assert [p.name for p in offenders] == ["memgrep-release.yml"]


def test_untested_until_release_workflows_empty_when_smoke_present() -> None:
    """The public orchestrating helper returns [] when a CI build smoke exists."""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        wf_dir = root / ".github" / "workflows"
        wf_dir.mkdir(parents=True)
        (wf_dir / "memgrep-release.yml").write_text(MEMGREP_RELEASE_YML, encoding="utf-8")
        (wf_dir / "ci.yml").write_text(CI_BUILD_SMOKE_YML, encoding="utf-8")
        assert untested_until_release_workflows(root) == []


def test_untested_until_release_workflows_empty_for_standard_canon() -> None:
    """The public orchestrating helper returns [] for the real standard canon."""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        wf_dir = root / ".github" / "workflows"
        wf_dir.mkdir(parents=True)
        (wf_dir / "release.yml").write_text(_std_release_yml(), encoding="utf-8")
        (wf_dir / "ci.yml").write_text(_std_ci_yml(), encoding="utf-8")
        assert untested_until_release_workflows(root) == []


# ── Robustness — never crash on malformed input ──────────────────────────────


def test_no_crash_on_unparseable_workflow() -> None:
    """A workflow file with garbage content → skipped, no crash, no finding."""
    warnings = _warnings_for({"broken.yml": ":::not yaml at all\x00\n  - [\n"})
    assert warnings == []


def test_no_crash_on_binary_garbage_workflow() -> None:
    """A workflow file with binary garbage → skipped, no crash."""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        wf_dir = root / ".github" / "workflows"
        wf_dir.mkdir(parents=True)
        (wf_dir / "weird.yml").write_bytes(b"\xff\xfe\x00\x01garbage\x00")
        report = ValidationReport()
        # Must not raise.
        check_untested_until_release(root, report)
        assert [r for r in report.results if "RC-UNTESTED-UNTIL-RELEASE" in r.message] == []
