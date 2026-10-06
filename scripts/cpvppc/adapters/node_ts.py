#!/usr/bin/env python3
"""node-ts adapter — plan §3.2 row 2.

typecheck=`tsc --noEmit`, lint=`eslint --max-warnings 0`.
test/build/install key off the closed adapter_options knobs
(bundler, entries, externals, test) that the schema and init.py already carry.
Config args are overridden: a unit cannot retarget tsc/eslint.
"""

from __future__ import annotations

import sys

ALLOWED_OPTIONS = ("bundler", "entries", "externals", "test")

SAMPLE_UNIT = {
    "name": "cli",
    "path": "scripts/llm-ext",
    "adapter_options": {
        "bundler": "esbuild",
        "entries": {"src/cli/main.ts": "dist/llm-ext.js"},
        "externals": ["better-sqlite3"],
        "test": "vitest",
    },
}

_TEST_RUNNERS = {"vitest": ["vitest", "run"], "node --test": ["node", "--test"], "bun test": ["bun", "test"]}
_BUNDLERS = ("esbuild", "bun", "vite", "tsdown")


def build_commands(unit: dict, command: str, opts: dict | None = None) -> list[list[str]]:
    opts = dict(opts or {})
    path = unit.get("path", ".")
    if command == "lint":
        return [["eslint", "--max-warnings", "0", path]]
    if command == "typecheck":
        return [["tsc", "--noEmit"]]
    if command == "test":
        runner = opts.get("test", "vitest")
        if runner not in _TEST_RUNNERS:
            raise ValueError(f"node-ts: unknown test runner {runner!r}")
        return [_TEST_RUNNERS[runner]]
    if command == "build":
        bundler = opts.get("bundler")
        if bundler not in _BUNDLERS:
            raise ValueError(f"node-ts: unknown bundler {bundler!r}")
        entries: dict = opts.get("entries", {})
        if bundler == "bun":
            argv = ["bun", "build"]
        else:
            argv = ["esbuild"]
        for src in entries:
            argv += [src, "--outfile", entries[src]]
        for ext in opts.get("externals", []):
            argv += ["--external", ext]
        if bundler in ("vite", "tsdown"):
            argv = [bundler, "build"] + argv[1:]
        # No entry map → nothing to bundle; vite/tsdown still get their bare
        # build (they read their own config for entries).
        return [argv] if entries or bundler in ("vite", "tsdown") else []
    if command == "install":
        if opts.get("lockfile", "").endswith("pnpm-lock.yaml"):
            return [["pnpm", "install", "--frozen-lockfile", "--ignore-scripts"]]
        if opts.get("lockfile", "").endswith("bun.lockb"):
            return [["bun", "install", "--frozen-lockfile", "--ignore-scripts"]]
        return [["npm", "ci", "--ignore-scripts"]]
    raise ValueError(f"node-ts: unknown command {command!r}")


if __name__ == "__main__":
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2]))  # scripts/ — bare-CLI import
    from cpvppc.adapters import sample_main

    sample_main("node-ts")
    sys.exit(0)
