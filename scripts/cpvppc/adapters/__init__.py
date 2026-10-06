"""CPVPPC P3 adapter catalog — plan §3.2 (CPV code; CI runs CPV-controlled argv).

Registry: config adapter name (schema enum, closed to these 6) → module.
Each module exposes build_commands(unit, command, opts) -> list[list[str]] for
command in {lint, typecheck, test, build, install}. A unit's adapter_options
can never retarget lint/typecheck or swap the test/build toolchain: the argv
shape is fixed here and opts only feed the closed, named knobs (python-uv:
import_mode, plugins[]; node-ts: bundler/entries/externals/test).
"""

from __future__ import annotations

from importlib import import_module

# Must equal the schema's adapter enum (pinned two-sided by the P3 tests).
NAMES = ("python-uv", "node-ts", "rust-cargo", "c-cpp", "go", "shell")

_MODULES = {
    "python-uv": "python_uv",
    "node-ts": "node_ts",
    "rust-cargo": "rust_cargo",
    "c-cpp": "c_cpp",
    "go": "go",
    "shell": "shell",
}

COMMANDS = ("lint", "typecheck", "test", "build", "install")


def module_for(name: str):
    """Resolve a config adapter name to its module. KeyError = not in the catalog."""
    return import_module(f".{_MODULES[name]}", __package__)


def build_commands(name: str, unit: dict, command: str, opts: dict | None = None) -> list[list[str]]:
    """One-command convenience: dispatch through the registry."""
    out = module_for(name).build_commands(unit, command, dict(opts or {}))
    return [list(argv) for argv in out]


def sample_main(name: str) -> None:
    """Bare-CLI smoke: print this adapter's argv for one sample unit.

    P2 central verification caught a module that imported fine under pytest
    but died as a bare script — every adapter keeps a runnable __main__.
    """
    mod = module_for(name)
    unit = getattr(mod, "SAMPLE_UNIT")
    opts = unit.get("adapter_options", {})
    print(f"{name} adapter argv (sample unit path={unit.get('path', '.')!r}):")
    for cmd in COMMANDS:
        for argv in mod.build_commands(unit, cmd, dict(opts)):
            print(f"  {cmd:<9} {' '.join(argv) if argv else '(no command)'}")
