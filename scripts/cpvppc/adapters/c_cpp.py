#!/usr/bin/env python3
"""c-cpp adapter — plan §3.2 row 4.

typecheck: compiler warnings as errors (build-time; no standalone typecheck argv),
lint: `clang-tidy` (option — only when the unit opts in),
test: `ctest` | `make test` (option), build: `cmake --build` | `make` |
`meson compile` | `gn gen && ninja` (option), install: no argv — submodules
pinned by SHA is a verify-side fact (config schema pins the ref), not a command.
Options come from the build table, never from the unit's own Makefile/CMakeLists.
"""

from __future__ import annotations

import sys

ALLOWED_OPTIONS = ("build_system", "clang_tidy", "test_runner")

SAMPLE_UNIT = {
    "name": "native",
    "path": "csrc",
    "adapter_options": {"build_system": "cmake", "clang_tidy": True, "test_runner": "ctest"},
}

_BUILD_SYSTEMS = ("cmake", "make", "meson", "gn-ninja")


def _build_argv(system: str, path: str) -> list[list[str]]:
    if system == "cmake":
        return [["cmake", "--build", path]]
    if system == "make":
        return [["make", "-C", path]]
    if system == "meson":
        return [["meson", "compile", "-C", path]]
    if system == "gn-ninja":
        return [["gn", "gen", path], ["ninja", "-C", path]]
    raise ValueError(f"c-cpp: unknown build system {system!r}")


def build_commands(unit: dict, command: str, opts: dict | None = None) -> list[list[str]]:
    opts = dict(opts or {})
    path = unit.get("path", ".")
    system = opts.get("build_system", "cmake")
    if command == "lint":
        return [["clang-tidy", path]] if opts.get("clang_tidy") else []
    if command == "typecheck":
        # Plan: warnings-as-errors is a compile-time property (CFLAGS). No argv;
        # the build commands carry it via CI's exported flags, not here.
        return []
    if command == "test":
        runner = opts.get("test_runner")
        if runner == "ctest":
            return [["ctest", "--test-dir", path, "--output-on-failure"]]
        if runner == "make test":
            return [["make", "-C", path, "test"]]
        return []
    if command == "build":
        return _build_argv(system, path)
    if command == "install":
        # Plan: submodules pinned by SHA — enforced by config_validate/schema,
        # no install command exists.
        return []
    raise ValueError(f"c-cpp: unknown command {command!r}")


if __name__ == "__main__":
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2]))  # scripts/ — bare-CLI import
    from cpvppc.adapters import sample_main

    sample_main("c-cpp")
    sys.exit(0)
