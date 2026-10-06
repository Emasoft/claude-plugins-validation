#!/usr/bin/env python3
"""CPVPPC P3 tests — adapter catalog (plan §3.2), each acceptance two-sided.

Covers: exact argv snapshots per adapter x command vs plan §3.2; the
config-override rule (a unit's adapter_options cannot retarget
lint/typecheck or swap the test runner); the pytest collected>=discovered
check on a tmp fixture with addopts=-k nothing; registry resolution for all
6 names + unknown-name rejection; the schema adapter enum closed to exactly
the 6 names (valid passes config_validate, bogus rejected).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = REPO_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from cpvppc import adapters as reg  # type: ignore[import-not-found]  # noqa: E402
from cpvppc.adapters import (  # type: ignore[import-not-found]  # noqa: E402
    c_cpp,
    go,
    node_ts,
    python_uv,
    rust_cargo,
    shell,
)
from cpvppc.config_validate import load_schema, validate_config  # type: ignore[import-not-found]  # noqa: E402

SCHEMA = load_schema()

# pytest collects it when the P2 suite runs alongside; import lazily-checked below.
yaml = pytest.importorskip("yaml")


def _base_unit(**over):
    u = {"name": "u", "path": "src", "adapter": "x", "toolchain": {"version": "1"}}
    u.update(over)
    return u


# --- argv snapshots: plan §3.2, literal lists (not substrings) --------------------


class TestPythonUvArgv:
    def test_lint(self):
        assert python_uv.build_commands(_base_unit(), "lint") == [["ruff", "check", "src"]]

    def test_typecheck(self):
        assert python_uv.build_commands(_base_unit(), "typecheck") == [["mypy", "src"]]

    def test_test_bare(self):
        argv = python_uv.build_commands(_base_unit(), "test")[0]
        assert argv == [
            "uv",
            "run",
            "pytest",
            "-o",
            'addopts=""',
            "-p",
            "no:cacheprovider",
            "--junitxml",
            "src/.pytest-junit.xml",
            "src",
        ]

    def test_test_with_allowed_options(self):
        opts = {"import_mode": "importlib", "plugins": ["pytest-cov"]}
        argv = python_uv.build_commands(_base_unit(), "test", opts)[0]
        assert argv == [
            "uv",
            "run",
            "pytest",
            "-o",
            'addopts=""',
            "-p",
            "no:cacheprovider",
            "--junitxml",
            "src/.pytest-junit.xml",
            "--import-mode=importlib",
            "-p",
            "pytest-cov",
            "src",
        ]

    def test_build_wheel_declared(self):
        u = _base_unit(build={"kind": "none", "outputs": [{"name": "w", "kind": "wheel"}]})
        assert python_uv.build_commands(u, "build") == [["uv", "build"]]

    def test_build_no_wheel_no_command(self):
        u = _base_unit(build={"kind": "none", "outputs": []})
        assert python_uv.build_commands(u, "build") == []

    def test_install(self):
        assert python_uv.build_commands(_base_unit(), "install") == [["uv", "sync", "--locked"]]

    def test_unknown_command_rejected(self):
        with pytest.raises(ValueError, match="unknown command"):
            python_uv.build_commands(_base_unit(), "deploy")


class TestNodeTsArgv:
    def test_lint(self):
        assert node_ts.build_commands(_base_unit(), "lint") == [["eslint", "--max-warnings", "0", "src"]]

    def test_typecheck(self):
        assert node_ts.build_commands(_base_unit(), "typecheck") == [["tsc", "--noEmit"]]

    @pytest.mark.parametrize(
        "runner,argv",
        [
            ("vitest", ["vitest", "run"]),
            ("node --test", ["node", "--test"]),
            ("bun test", ["bun", "test"]),
        ],
    )
    def test_test_runners(self, runner, argv):
        assert node_ts.build_commands(_base_unit(), "test", {"test": runner}) == [argv]

    def test_test_bad_runner_rejected(self):
        with pytest.raises(ValueError, match="unknown test runner"):
            node_ts.build_commands(_base_unit(), "test", {"test": "mocha"})

    def test_build_esbuild_entries_externals(self):
        opts = {
            "bundler": "esbuild",
            "entries": {"src/cli/main.ts": "dist/llm-ext.js"},
            "externals": ["better-sqlite3"],
        }
        argv = node_ts.build_commands(_base_unit(), "build", opts)[0]
        assert argv[:6] == [
            "esbuild",
            "src/cli/main.ts",
            "--outfile",
            "dist/llm-ext.js",
            "--external",
            "better-sqlite3",
        ]

    def test_build_bun(self):
        argv = node_ts.build_commands(_base_unit(), "build", {"bundler": "bun", "entries": {"a.ts": "a.js"}})[0]
        assert argv[:3] == ["bun", "build", "a.ts"]

    def test_build_bad_bundler_rejected(self):
        with pytest.raises(ValueError, match="unknown bundler"):
            node_ts.build_commands(_base_unit(), "build", {"bundler": "webpack", "entries": {}})

    @pytest.mark.parametrize(
        "lockfile,argv",
        [
            ("package-lock.json", ["npm", "ci", "--ignore-scripts"]),
            ("pnpm-lock.yaml", ["pnpm", "install", "--frozen-lockfile", "--ignore-scripts"]),
            ("bun.lockb", ["bun", "install", "--frozen-lockfile", "--ignore-scripts"]),
        ],
    )
    def test_install_per_lockfile(self, lockfile, argv):
        assert node_ts.build_commands(_base_unit(), "install", {"lockfile": lockfile}) == [argv]

    def test_install_default_is_npm(self):
        assert node_ts.build_commands(_base_unit(), "install") == [["npm", "ci", "--ignore-scripts"]]


class TestRustCargoArgv:
    def test_lint(self):
        assert rust_cargo.build_commands(_base_unit(), "lint") == [
            ["cargo", "--manifest-path", "src/Cargo.toml", "fmt", "--check"]
        ]

    def test_typecheck_locked_werror_clippy_only(self):
        argv = rust_cargo.build_commands(_base_unit(), "typecheck")[0]
        assert argv == [
            "cargo",
            "--manifest-path",
            "src/Cargo.toml",
            "clippy",
            "--all-targets",
            "--locked",
            "--",
            "-D",
            "warnings",
        ]
        # -Werror never forced on C built by cc: no CFLAGS here.
        assert not any("Werror" in a or "CFLAGS" in a for a in argv)

    def test_test_locked(self):
        assert rust_cargo.build_commands(_base_unit(), "test") == [
            ["cargo", "--manifest-path", "src/Cargo.toml", "test", "--locked"]
        ]

    def test_build_native_locked_per_target(self):
        u = _base_unit(
            build={
                "kind": "compiled",
                "outputs": [{"name": "pss", "kind": "bin"}],
                "targets": [{"os": "darwin", "arch": "arm64", "runner": "macos-14", "method": "native"}],
            }
        )
        argv = rust_cargo.build_commands(u, "build")[0]
        assert argv == [
            "cargo",
            "--manifest-path",
            "src/Cargo.toml",
            "build",
            "--release",
            "--locked",
            "--target",
            "aarch64-apple-darwin",
        ]

    def test_build_zigbuild_and_cross(self):
        u = _base_unit(
            build={
                "kind": "compiled",
                "outputs": [],
                "targets": [
                    {
                        "os": "linux",
                        "arch": "x86_64",
                        "runner": "ubuntu-24.04",
                        "method": "zigbuild",
                        "triple": "x86_64-unknown-linux-musl",
                    },
                    {"os": "windows", "arch": "x86_64", "runner": "windows-2022", "method": "cross"},
                ],
            }
        )
        argvs = rust_cargo.build_commands(u, "build")
        assert argvs[0][:2] == ["cargo", "zigbuild"] and "--locked" in argvs[0]
        assert argvs[0][-2:] == ["--target", "x86_64-unknown-linux-musl"]
        assert argvs[1][0] == "cross" and "--locked" in argvs[1]

    def test_install_locked(self):
        argv = rust_cargo.build_commands(_base_unit(), "install")[0]
        assert argv[-2:] == ["fetch", "--locked"]


class TestCCppArgv:
    def test_lint_option_gated(self):
        assert c_cpp.build_commands(_base_unit(), "lint") == []
        assert c_cpp.build_commands(_base_unit(), "lint", {"clang_tidy": True}) == [["clang-tidy", "src"]]

    def test_typecheck_no_argv_by_design(self):
        assert c_cpp.build_commands(_base_unit(), "typecheck") == []

    @pytest.mark.parametrize(
        "runner,argv",
        [
            ("ctest", ["ctest", "--test-dir", "src", "--output-on-failure"]),
            ("make test", ["make", "-C", "src", "test"]),
        ],
    )
    def test_test_runners(self, runner, argv):
        assert c_cpp.build_commands(_base_unit(), "test", {"test_runner": runner}) == [argv]

    def test_test_no_runner_no_command(self):
        assert c_cpp.build_commands(_base_unit(), "test") == []

    @pytest.mark.parametrize(
        "system,argv",
        [
            ("cmake", [["cmake", "--build", "src"]]),
            ("make", [["make", "-C", "src"]]),
            ("meson", [["meson", "compile", "-C", "src"]]),
        ],
    )
    def test_build_systems(self, system, argv):
        assert c_cpp.build_commands(_base_unit(), "build", {"build_system": system}) == argv

    def test_build_gn_ninja_is_two_commands(self):
        assert c_cpp.build_commands(_base_unit(), "build", {"build_system": "gn-ninja"}) == [
            ["gn", "gen", "src"],
            ["ninja", "-C", "src"],
        ]

    def test_install_no_argv_submodules_are_a_config_fact(self):
        assert c_cpp.build_commands(_base_unit(), "install") == []


class TestGoArgv:
    def test_lint_vet(self):
        assert go.build_commands(_base_unit(), "lint") == [["go", "vet", "./src/..."]]

    def test_typecheck_staticcheck(self):
        assert go.build_commands(_base_unit(), "typecheck") == [["staticcheck", "./src/..."]]

    def test_test(self):
        assert go.build_commands(_base_unit(), "test") == [["go", "test", "./src/..."]]

    def test_build_goos_goarch_readonly(self):
        u = _base_unit(
            build={
                "kind": "compiled",
                "outputs": [{"name": "t", "kind": "bin"}],
                "targets": [{"os": "linux", "arch": "arm64", "runner": "ubuntu-24.04-arm", "method": "native"}],
            }
        )
        assert go.build_commands(u, "build") == [
            ["env", "GOOS=linux", "GOARCH=arm64", "go", "build", "-mod=readonly", "./src/..."]
        ]

    def test_install_mod_verify(self):
        assert go.build_commands(_base_unit(), "install") == [["go", "mod", "verify"]]


class TestShellArgv:
    def test_lint_shellcheck(self):
        assert shell.build_commands(_base_unit(), "lint") == [["shellcheck", "src"]]

    def test_typecheck_shfmt_dash_d(self):
        assert shell.build_commands(_base_unit(), "typecheck") == [["shfmt", "-d", "src"]]

    def test_test_bats_option_gated(self):
        assert shell.build_commands(_base_unit(), "test") == []
        assert shell.build_commands(_base_unit(), "test", {"bats": True}) == [["bats", "src"]]

    def test_build_and_install_are_dash_dash(self):
        assert shell.build_commands(_base_unit(), "build") == []
        assert shell.build_commands(_base_unit(), "install") == []


# --- config-override rule ----------------------------------------------------------


class TestConfigArgsOverridden:
    """The plan: CPV passes CPV-controlled arguments; a unit's own config
    (pytest addopts, eslint config, tsc tsconfig targets) must not retarget CI."""

    def test_addopts_cannot_disable_the_type_check_or_swap_the_target(self):
        # CPV passes `-o addopts=""` — a project's addopts (-k nothing,
        # --co, a different testpaths) is overridden, not merged.
        argv = python_uv.build_commands(_base_unit(), "test")[0]
        i = argv.index("-o")
        assert argv[i + 1] == 'addopts=""'
        # The lint/typecheck argv has no project-configurable target: it is
        # always the unit path, full stop.
        assert python_uv.build_commands(_base_unit(), "lint") == [["ruff", "check", "src"]]
        assert python_uv.build_commands(_base_unit(), "typecheck") == [["mypy", "src"]]

    def test_adapter_options_cannot_reach_lint_or_typecheck_argv(self):
        # Whatever opts carry, lint/typecheck argv is identical.
        wild = {"plugins": ["evil"], "import_mode": "x", "test": "mocha", "bundler": "webpack"}
        for mod in (python_uv, node_ts, rust_cargo, c_cpp, go, shell):
            assert mod.build_commands(_base_unit(), "lint", wild) == mod.build_commands(_base_unit(), "lint")
            assert mod.build_commands(_base_unit(), "typecheck", wild) == mod.build_commands(_base_unit(), "typecheck")

    def test_node_ts_test_runner_is_cpvs_choice_not_the_units(self):
        # test key is a closed enum; the unit cannot pass an arbitrary argv.
        with pytest.raises(ValueError):
            node_ts.build_commands(_base_unit(), "test", {"test": "jest --coverage"})

    def test_shell_test_only_via_the_closed_bats_flag(self):
        # bats is option-gated; an argv-shaped value never becomes the command —
        # only a truthy flag enables the fixed ["bats", path] argv.
        assert shell.build_commands(_base_unit(), "test", {"bats": ["bats", "--tap", "/etc"]}) == [["bats", "src"]]
        # The schema closes adapter_options keys outright — bats is not one,
        # so a config declaring it is rejected by config_validate.
        doc = _cfg("shell")
        doc["units"][0]["adapter_options"] = {"bats": True}
        errs = validate_config(doc)
        assert any("unknown key" in e and "bats" in e for e in errs)


# --- pytest collected >= discovered test files --------------------------------------


class TestCollectedVsDiscovered:
    def _mk_fixture(self, root: Path) -> Path:
        pkg = root / "fixture"
        pkg.mkdir()
        (pkg / "test_one.py").write_text(
            "def test_a():\n    assert True\n\ndef test_b():\n    assert True\n", encoding="utf-8"
        )
        (pkg / "test_two.py").write_text("def test_c():\n    assert True\n", encoding="utf-8")
        (pkg / "pytest.ini").write_text("[pytest]\naddopts = -k nothing\n", encoding="utf-8")
        return pkg

    def test_discovered_counts_both_test_files(self, tmp_path):
        pkg = self._mk_fixture(tmp_path)
        found = python_uv.discovered_test_files(str(pkg))
        assert found == [str(pkg / "test_one.py"), str(pkg / "test_two.py")]

    def test_discovered_empty_on_no_tests(self, tmp_path):
        empty = tmp_path / "empty"
        empty.mkdir()
        assert python_uv.discovered_test_files(str(empty)) == []

    def _probe(self, tmp_path: Path, pkg: Path, use_cpv_override: bool) -> Path:
        """Run the fixture's pytest HERMETICALLY: cwd = tmp (no repo conftest
        reachable), -c pins the fixture's own ini, PYTEST_ADDOPTS cleared. A
        non-hermetic probe inherits the repo's pytest.ini and re-collects this
        very test file — the -k 'nothing' expression then matches this test's
        own name and the probe re-spawns itself (the runaway that ate 250
        processes on the first run). Bounded by timeout regardless."""
        import os  # noqa: PLC0415
        import subprocess  # noqa: PLC0415

        junit = tmp_path / "junit.xml"
        argv = [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "no:cacheprovider",
            "--rootdir",
            str(pkg),
            "-c",
            str(pkg / "pytest.ini"),
            "--junitxml",
            str(junit),
            str(pkg),
        ]
        if use_cpv_override:
            # CPV-controlled argument: kills the fixture ini's addopts=-k nothing.
            argv += ["-o", "addopts="]
        env = dict(os.environ, PYTEST_ADDOPTS="")
        subprocess.run(argv, cwd=str(tmp_path), env=env, capture_output=True, timeout=120, check=False)  # noqa: S603
        return junit

    def test_k_nothing_ini_deselects_all_so_the_gate_fails(self, tmp_path):
        """Baseline (no CPV override): the fixture's addopts=-k nothing
        deselects everything; junit reports 0 collected < 2 discovered — the
        trap the collected>=discovered check exists to catch."""
        pkg = self._mk_fixture(tmp_path)
        junit = self._probe(tmp_path, pkg, use_cpv_override=False)
        assert junit.is_file()
        assert python_uv.collected_test_count(str(junit)) == 0
        assert python_uv.collected_test_count(str(junit)) < len(python_uv.discovered_test_files(str(pkg)))

    def test_cpv_addopts_override_collects_all_and_gate_passes(self, tmp_path):
        """With CPV's -o addopts="" override: the adversarial ini addopts is
        dead, all 3 tests run, collected (3) >= discovered files (2)."""
        pkg = self._mk_fixture(tmp_path)
        junit = self._probe(tmp_path, pkg, use_cpv_override=True)
        collected = python_uv.collected_test_count(str(junit))
        assert collected == 3
        assert collected >= len(python_uv.discovered_test_files(str(pkg))) == 2

    def test_missing_or_corrupt_junit_reads_zero_never_passes(self, tmp_path):
        assert python_uv.collected_test_count(str(tmp_path / "absent.xml")) == 0
        bad = tmp_path / "bad.xml"
        bad.write_text("<not-junit/>", encoding="utf-8")
        assert python_uv.collected_test_count(str(bad)) == 0


# --- registry ------------------------------------------------------------------------


class TestRegistry:
    def test_all_six_names_resolve(self):
        for name in reg.NAMES:
            assert hasattr(reg.module_for(name), "build_commands")

    @pytest.mark.parametrize("name", ["python-uv", "node-ts", "rust-cargo", "c-cpp", "go", "shell"])
    def test_build_commands_dispatch(self, name):
        argv = reg.build_commands(name, _base_unit(), "lint")
        assert isinstance(argv, list) and all(isinstance(a, list) for a in argv)

    @pytest.mark.parametrize("bad", ["notinlist", "dotnet", "swift", "zig", "java-gradle", "", "PYTHON-UV"])
    def test_unknown_name_rejected(self, bad):
        with pytest.raises(KeyError):
            reg.build_commands(bad, _base_unit(), "lint")


# --- schema: adapter enum closed to exactly the 6 (two-sided) ------------------------


def _cfg(adapter):
    return {
        "schema_version": "0.1.0",
        "canon": {"version": "0.1.0"},
        "repo": {"kind": "plugin"},
        "units": [{"name": "scripts", "path": "scripts", "adapter": adapter, "toolchain": {"version": "3.12"}}],
        "targets": {"marketplaces": []},
    }


class TestSchemaAdapterEnum:
    def test_enum_is_exactly_the_six_names(self):
        assert SCHEMA["properties"]["units"]["items"]["properties"]["adapter"]["enum"] == list(reg.NAMES)

    @pytest.mark.parametrize("name", list(reg.NAMES))
    def test_valid_name_passes_config_validate(self, name):
        assert validate_config(_cfg(name)) == []

    @pytest.mark.parametrize("bad", ["notinlist", "dotnet", "swift", "zig", "java-gradle", "Python-Uv"])
    def test_bogus_name_rejected(self, bad):
        errs = validate_config(_cfg(bad))
        assert any("adapter" in e for e in errs)

    def test_python_uv_options_still_closed_to_plan_named_keys(self):
        # python_uv.ALLOWED_OPTIONS ⊆ schema adapter_options properties, and
        # nothing outside the plan's closed list is accepted.
        props = SCHEMA["properties"]["units"]["items"]["properties"]["adapter_options"]["properties"]
        assert set(props) == {"bundler", "entries", "externals", "test", "import_mode", "plugins"}
        assert set(python_uv.ALLOWED_OPTIONS) <= set(props)
        doc = _cfg("python-uv")
        doc["units"][0]["adapter_options"] = {"color": "yes"}
        assert any("unknown key" in e for e in validate_config(doc))


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
