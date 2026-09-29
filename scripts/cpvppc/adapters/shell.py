#!/usr/bin/env python3
"""shell adapter — plan §3.2 row 6.

lint=`shellcheck`, typecheck=`shfmt -d`, test=`bats` (option — only when the
unit opts in), build/install: no command (— in the plan row).
Both linters run with CPV-controlled targets; the unit cannot retarget them.
"""

from __future__ import annotations

import sys

ALLOWED_OPTIONS = ("bats",)

SAMPLE_UNIT = {
    "name": "scripts-sh",
    "path": "scripts",
    "adapter_options": {"bats": True},
}


def build_commands(unit: dict, command: str, opts: dict | None = None) -> list[list[str]]:
    opts = dict(opts or {})
    path = unit.get("path", ".")
    if command == "lint":
        return [["shellcheck", path]]
    if command == "typecheck":
        return [["shfmt", "-d", path]]
    if command == "test":
        return [["bats", path]] if opts.get("bats") else []
    if command in ("build", "install"):
        return []
    raise ValueError(f"shell: unknown command {command!r}")


if __name__ == "__main__":
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2]))  # scripts/ — bare-CLI import
    from cpvppc.adapters import sample_main

    sample_main("shell")
    sys.exit(0)
