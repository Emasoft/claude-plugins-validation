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
