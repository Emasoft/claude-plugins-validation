#!/usr/bin/env python3
"""python-uv adapter — plan §3.2 row 1.

lint=`ruff check`, typecheck=`mypy`, install=`uv sync --locked`,
build=`uv build` only when a wheel output is declared,
test=`pytest -o addopts="" -p no:cacheprovider --junitxml=…` plus the CLOSED
allowed adapter_options (import_mode, plugins[]). Config args are overridden:
a unit cannot swap the lint target or disable the type check — the argv here
is what CI runs, whatever the unit's own pyproject.toml says.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Closed list: anything else in adapter_options must be rejected by
# config_validate (schema properties are exactly these two python-uv keys).
ALLOWED_OPTIONS = ("import_mode", "plugins")

SAMPLE_UNIT = {"name": "scripts", "path": "scripts", "toolchain": {"version": "3.12"}}


def _import_mode_argv(opts: dict) -> list[str]:
    mode = opts.get("import_mode")
    return [f"--import-mode={mode}"] if isinstance(mode, str) and mode else []


def _plugins_argv(opts: dict) -> list[str]:
    out: list[str] = []
    for p in opts.get("plugins", []):
        if isinstance(p, str) and p:
            out += ["-p", p]
    return out


def discovered_test_files(unit_path: str = ".") -> list[str]:
    """Files pytest WOULD discover (test_*.py / *_test.py under the unit path)."""
    root = Path(unit_path)
    if not root.is_dir():
        return []
    return sorted(str(p) for p in root.rglob("*.py") if p.name.startswith("test_") or p.name.endswith("_test.py"))


def collected_test_count(junit_xml: str) -> int:
    """Collected tests from the run's JUnit XML (tests + skipped + errors).

    `collected >= discovered test files` per plan §3.2: a pytest run whose
    addopts silently deselect everything (e.g. addopts=-k nothing) must fail
    the gate — the tests block reads the count, not pytest's exit code.
    """
    import xml.etree.ElementTree as ET  # noqa: PLC0415 — stdlib, lazy like the neighbours

    try:
        root = ET.parse(junit_xml).getroot()
    except (OSError, ET.ParseError):
        return 0
    suite = root if root.tag == "testsuite" else (root.find("testsuite") if root.tag == "testsuites" else None)
    if suite is None:
        return 0
    try:
        total = int(suite.get("tests", "0"))
    except ValueError:
        return 0
    return total + sum(1 for _ in suite.iter("skipped"))


def build_commands(unit: dict, command: str, opts: dict | None = None) -> list[list[str]]:
    opts = dict(opts or {})
    path = unit.get("path", ".")
    if command == "lint":
        return [["ruff", "check", path]]
    if command == "typecheck":
        return [["mypy", path]]
    if command == "test":
        junit = f"{path}/.pytest-junit.xml".replace("./", "")
        argv = [
            "uv",
            "run",
            "pytest",
            "-o",
            'addopts=""',
            "-p",
            "no:cacheprovider",
            "--junitxml",
            junit,
        ]
        argv += _import_mode_argv(opts)
        argv += _plugins_argv(opts)
        argv.append(path)  # CPV-controlled target, always last
        return [argv]
    if command == "build":
        kinds = {o.get("kind") for o in unit.get("build", {}).get("outputs", [])}
        return [["uv", "build"]] if "wheel" in kinds else []
    if command == "install":
        return [["uv", "sync", "--locked"]]
    raise ValueError(f"python-uv: unknown command {command!r}")


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # scripts/ — bare-CLI import
    from cpvppc.adapters import sample_main

    sample_main("python-uv")
    sys.exit(0)
