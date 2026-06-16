#!/usr/bin/env python3
"""Regression tests for issue #129 — a package-less native tool (xmllint) must
NOT resolve through a package-fetching executor (npx/npm/bunx/pnpm/yarn/deno-npm).

Root cause: `build_argv_for_executor` did `pkg = spec.package or spec.name`, so
a ToolSpec with `package=None` (xmllint) fell back to its NAME as the npm
package — `build_argv_for_executor("npx", TOOL_DB["xmllint"], …)` returned
`['npx','--yes','xmllint', …]`. On a bare CI runner (no native xmllint), the XML
linter then RAN that command; npx errored "could not determine executable to
run", and the error lines were reported as a false MAJOR on the user's valid
XML, turning downstream CI red.

Fix: every package-fetching executor branch returns None when `spec.package is
None`. xmllint then resolves only via the native PATH (`direct`) or `docker`; on
a bare runner with neither, the resolver returns None and `_lint_xml` degrades
gracefully through `_tool_missing` (WARNING / skip) instead of emitting a MAJOR.

Two-sided coverage:
- xmllint (package=None) -> None for every package-based executor.
- shellcheck / hadolint (native, but real npm wrapper package set) -> STILL
  resolve via npx (FN-safety — the fix must not break package-backed natives).
- eslint (node, package set) -> unaffected via npx.
- deno-lint (deno_builtin, package=None) -> the deno-builtin path still works
  (it needs no package; only the node/native npm path is gated).
- _lint_xml with `_resolve` -> None reports a WARNING (skip), NOT a MAJOR.

REOPEN (v2.126.35 round): after the misroute fix the XML lint correctly routes
to the docker fallback (`docker run … alpine … xmllint --noout`). But a
non-zero returncode's stderr now MIXES three line kinds, and `_lint_xml`
reported EVERY stderr line as a MAJOR — so two new false MAJORs appeared:
docker image-pull PROGRESS lines, and a non-fatal `warning: failed to load
external entity` (offline DTD fetch). The reopened-issue tests
(`TestLintXmlDockerFallbackStderrTriage`) assert `_lint_xml` now triages:
container/registry noise -> dropped; a non-fatal xmllint warning -> WARNING;
only a genuine `parser error` line -> MAJOR; and a failure that is
infra/warning-only -> one explanatory WARNING that does NOT block.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

# tests/conftest.py adds scripts/ to sys.path; defensive duplicate so the file
# works when collected in isolation.
scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

from cpv_lint_engine import lint_xml  # noqa: E402
from cpv_validation_common import ValidationReport  # noqa: E402
from smart_exec import TOOL_DB, build_argv_for_executor  # noqa: E402

# The five package-fetching node/native executors that the fix gates on
# `spec.package is None`. (deno is exercised separately because its
# deno_builtin path is intentionally NOT gated.)
_PACKAGE_EXECUTORS = ("npx", "npm", "bunx", "pnpm", "yarn")


class FakeResult:
    """subprocess.CompletedProcess stand-in (mirrors test_cpv_lint_engine)."""

    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


# ---------------------------------------------------------------------------
# build_argv_for_executor — the actual fix site
# ---------------------------------------------------------------------------


class TestPackagelessToolNoPackageExecutor:
    """xmllint (package=None) must resolve to None for every package executor."""

    def test_xmllint_npx_is_none(self) -> None:
        """build_argv_for_executor('npx', xmllint, …) is None (the issue #129 case)."""
        # `have('npx')` forced True so a non-None result would mean the package
        # guard failed (not merely that npx is absent on this machine).
        with patch("smart_exec.have", return_value=True):
            argv = build_argv_for_executor("npx", TOOL_DB["xmllint"], ["--noout", "f.xml"])
        assert argv is None

    def test_xmllint_all_package_executors_are_none(self) -> None:
        """xmllint yields None for npx/npm/bunx/pnpm/yarn (no package to fetch)."""
        with patch("smart_exec.have", return_value=True):
            for ex in _PACKAGE_EXECUTORS:
                argv = build_argv_for_executor(ex, TOOL_DB["xmllint"], ["--noout", "f.xml"])
                assert argv is None, f"{ex} resolved a package command for package-less xmllint: {argv}"

    def test_xmllint_deno_npm_path_is_none(self) -> None:
        """The deno node/native (npm) path is gated too -> None for xmllint."""
        with patch("smart_exec.have", return_value=True):
            argv = build_argv_for_executor("deno", TOOL_DB["xmllint"], ["--noout", "f.xml"])
        assert argv is None

    def test_xmllint_docker_still_resolves(self) -> None:
        """xmllint MUST still resolve via docker (its only non-PATH executor)."""
        with patch("smart_exec.have", return_value=True):
            argv = build_argv_for_executor("docker", TOOL_DB["xmllint"], ["--noout", "f.xml"])
        assert argv is not None
        assert argv[0] == "docker"

    def test_xmllint_direct_still_resolves_when_on_path(self) -> None:
        """The native PATH (`direct`) executor is unchanged for xmllint."""
        with patch("smart_exec.have", return_value=True):
            argv = build_argv_for_executor("direct", TOOL_DB["xmllint"], ["--noout", "f.xml"])
        assert argv == ["xmllint", "--noout", "f.xml"]


class TestPackageBackedNativesStillResolve:
    """FN-safety: native tools WITH a real npm wrapper must still use npx."""

    def test_shellcheck_npx_still_resolves(self) -> None:
        """shellcheck (native, package='shellcheck') must STILL resolve via npx."""
        with patch("smart_exec.have", return_value=True):
            argv = build_argv_for_executor("npx", TOOL_DB["shellcheck"], ["f.sh"])
        assert argv is not None
        assert "shellcheck" in argv

    def test_hadolint_npx_still_resolves(self) -> None:
        """hadolint (native, package='hadolint') must STILL resolve via npx."""
        with patch("smart_exec.have", return_value=True):
            argv = build_argv_for_executor("npx", TOOL_DB["hadolint"], ["Dockerfile"])
        assert argv is not None
        assert "hadolint" in argv

    def test_shellcheck_all_package_executors_resolve(self) -> None:
        """A package-backed native resolves through every package executor."""
        with patch("smart_exec.have", return_value=True):
            for ex in _PACKAGE_EXECUTORS:
                argv = build_argv_for_executor(ex, TOOL_DB["shellcheck"], ["f.sh"])
                assert argv is not None, f"{ex} failed to resolve package-backed shellcheck"

    def test_eslint_node_npx_unaffected(self) -> None:
        """eslint (node, package='eslint') is unaffected by the native-tool fix."""
        with patch("smart_exec.have", return_value=True):
            argv = build_argv_for_executor("npx", TOOL_DB["eslint"], ["f.js"])
        assert argv is not None
        assert "eslint" in argv

    def test_deno_builtin_needs_no_package(self) -> None:
        """deno-lint (deno_builtin, package=None) still works — only npm path gated."""
        with patch("smart_exec.have", return_value=True):
            argv = build_argv_for_executor("deno", TOOL_DB["deno-lint"], ["f.ts"])
        assert argv is not None
        assert argv[:2] == ["deno", "lint"]


# ---------------------------------------------------------------------------
# _lint_xml — graceful degradation when the resolver returns None
# ---------------------------------------------------------------------------


class TestLintXmlGracefulWhenUnavailable:
    """With xmllint unresolvable, the XML linter must skip, not falsely MAJOR."""

    def test_resolve_none_soft_warns_not_major(self, tmp_path: Path) -> None:
        """_resolve -> None + soft mode -> WARNING (skip), NEVER a MAJOR (issue #129)."""
        f = tmp_path / "valid.xml"
        f.write_text("<root/>\n")
        report = ValidationReport()
        with patch("cpv_lint_engine._resolve", return_value=None):
            ok = lint_xml(tmp_path, [f], report, strict_missing_tools=False)
        assert ok is True
        assert any(r.level == "WARNING" for r in report.results)
        assert not any(r.level == "MAJOR" for r in report.results)

    def test_resolve_none_strict_no_major(self, tmp_path: Path) -> None:
        """_resolve -> None + strict mode -> missing-tool finding, never a MAJOR on valid XML."""
        f = tmp_path / "valid.xml"
        f.write_text("<root/>\n")
        report = ValidationReport()
        with patch("cpv_lint_engine._resolve", return_value=None):
            ok = lint_xml(tmp_path, [f], report, strict_missing_tools=True)
        # Strict missing-tool fails the run, but the failure is a missing-tool
        # finding — NOT a false MAJOR claiming the user's XML is malformed.
        assert ok is False
        assert not any(
            r.level == "MAJOR" and "xmllint:" in r.message for r in report.results
        )

    def test_real_invalid_xml_still_major_when_tool_present(self, tmp_path: Path) -> None:
        """FN-safety: when xmllint IS resolved, genuinely invalid XML still fires MAJOR."""
        f = tmp_path / "broken.xml"
        f.write_text("<root>\n")  # unclosed tag
        report = ValidationReport()
        bad = FakeResult(returncode=1, stderr="broken.xml:2: parser error : Premature end of data")
        with (
            patch("cpv_lint_engine._resolve", return_value=["/usr/bin/xmllint"]),
            patch("cpv_lint_engine._run_linter", return_value=bad),
        ):
            ok = lint_xml(tmp_path, [f], report, strict_missing_tools=True)
        assert ok is False
        assert any(r.level == "MAJOR" and "xmllint:" in r.message for r in report.results)

    def test_valid_xml_passes_when_tool_present(self, tmp_path: Path) -> None:
        """FN-safety: a resolved xmllint reporting rc=0 -> PASS, no MAJOR."""
        f = tmp_path / "ok.xml"
        f.write_text("<root/>\n")
        report = ValidationReport()
        good = FakeResult(returncode=0)
        with (
            patch("cpv_lint_engine._resolve", return_value=["/usr/bin/xmllint"]),
            patch("cpv_lint_engine._run_linter", return_value=good),
        ):
            ok = lint_xml(tmp_path, [f], report, strict_missing_tools=True)
        assert ok is True
        assert not any(r.level == "MAJOR" for r in report.results)


# ---------------------------------------------------------------------------
# _lint_xml stderr triage (issue #129 REOPENED) — the docker fallback now runs
# `docker run … alpine … xmllint`, so a non-zero returncode's stderr mixes
# container/registry PULL PROGRESS, a non-fatal xmllint WARNING, and (only
# sometimes) a genuine validation ERROR. `_lint_xml` must classify each line:
# infra noise + warning -> no MAJOR; only a real parser error -> MAJOR.
# ---------------------------------------------------------------------------

# A realistic `docker run` image-pull transcript on stderr (the alpine pull the
# xmllint docker ToolSpec triggers on a bare runner), with a NON-ZERO returncode.
_DOCKER_PULL_STDERR = (
    "Unable to find image 'alpine:latest' locally\n"
    "latest: Pulling from library/alpine\n"
    "9cda6c963c7b: Pulling fs layer\n"
    "9cda6c963c7b: Verifying Checksum\n"
    "9cda6c963c7b: Download complete\n"
    "9cda6c963c7b: Pull complete\n"
    "Digest: sha256:0a4eaa0eecf5f8c050e5bba433f58c052be7587ee8af3e8b3910ef9ab5fbe9f5\n"
    "Status: Downloaded newer image for alpine:latest\n"
)


class TestLintXmlDockerFallbackStderrTriage:
    """Issue #129 reopened — classify docker/registry noise, warnings, errors."""

    def _run(self, tmp_path: Path, fake: FakeResult) -> tuple[ValidationReport, bool]:
        f = tmp_path / "pom.xml"
        f.write_text("<project/>\n")
        report = ValidationReport()
        with (
            patch("cpv_lint_engine._resolve", return_value=["docker", "run", "alpine"]),
            patch("cpv_lint_engine._run_linter", return_value=fake),
        ):
            ok = lint_xml(tmp_path, [f], report, strict_missing_tools=True)
        return report, ok

    def test_docker_pull_progress_only_is_single_warning_zero_major(
        self, tmp_path: Path
    ) -> None:
        """(1) Pure docker-pull progress + rc!=0 -> ONE WARNING, ZERO MAJOR, passes."""
        fake = FakeResult(returncode=1, stderr=_DOCKER_PULL_STDERR)
        report, ok = self._run(tmp_path, fake)
        assert ok is True
        majors = [r for r in report.results if r.level == "MAJOR"]
        warnings = [r for r in report.results if r.level == "WARNING"]
        assert majors == [], f"docker pull noise leaked as MAJOR: {[m.message for m in majors]}"
        # Exactly one explanatory "could not run cleanly" WARNING — no per-noise-line spam.
        assert len(warnings) == 1, [w.message for w in warnings]
        assert "not validated" in warnings[0].message

    def test_external_entity_warning_is_warning_zero_major(self, tmp_path: Path) -> None:
        """(2) `warning: failed to load external entity …` -> WARNING, ZERO MAJOR."""
        stderr = (
            _DOCKER_PULL_STDERR
            + 'warning: failed to load external entity "/w/parent/pom.xml"\n'
        )
        fake = FakeResult(returncode=1, stderr=stderr)
        report, ok = self._run(tmp_path, fake)
        assert ok is True
        majors = [r for r in report.results if r.level == "MAJOR"]
        warnings = [r for r in report.results if r.level == "WARNING"]
        assert majors == [], f"non-fatal warning surfaced as MAJOR: {[m.message for m in majors]}"
        # The external-entity warning must be visible as a WARNING.
        assert any("external entity" in w.message for w in warnings), [
            w.message for w in warnings
        ]

    def test_real_parser_error_still_major(self, tmp_path: Path) -> None:
        """(3) FN-safety: a genuine `parser error` line -> MAJOR (malformed XML)."""
        stderr = (
            _DOCKER_PULL_STDERR
            + "/w/pom.xml:12: parser error : Opening and ending tag mismatch: a line 1 and b\n"
        )
        fake = FakeResult(returncode=1, stderr=stderr)
        report, ok = self._run(tmp_path, fake)
        assert ok is False
        majors = [r for r in report.results if r.level == "MAJOR" and "xmllint:" in r.message]
        assert majors, "real parser error did not fire a MAJOR"
        assert any("parser error" in m.message for m in majors)
        # The pull-progress lines must NOT each have become their own MAJOR.
        assert len(majors) == 1, [m.message for m in majors]

    def test_clean_valid_xml_rc0_passes(self, tmp_path: Path) -> None:
        """(4) FN-safety: a clean rc=0 run -> PASS, no WARNING, no MAJOR."""
        fake = FakeResult(returncode=0, stderr="")
        report, ok = self._run(tmp_path, fake)
        assert ok is True
        assert not any(r.level == "MAJOR" for r in report.results)
        assert not any(r.level == "WARNING" for r in report.results)
        assert any(r.level == "PASSED" for r in report.results)

    def test_native_xmllint_real_error_still_major(self, tmp_path: Path) -> None:
        """FN-safety: native (non-docker) xmllint with a real error still MAJORs.

        When xmllint runs natively there is no docker pull noise — just the
        parser error on stderr. The triage must still fire a MAJOR (the v1
        behavior is preserved for the native path).
        """
        f = tmp_path / "broken.xml"
        f.write_text("<root>\n")
        report = ValidationReport()
        bad = FakeResult(
            returncode=1,
            stderr="broken.xml:2: parser error : Premature end of data in tag root line 1\n",
        )
        with (
            patch("cpv_lint_engine._resolve", return_value=["/usr/bin/xmllint"]),
            patch("cpv_lint_engine._run_linter", return_value=bad),
        ):
            ok = lint_xml(tmp_path, [f], report, strict_missing_tools=True)
        assert ok is False
        assert any(r.level == "MAJOR" and "parser error" in r.message for r in report.results)
