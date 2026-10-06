#!/usr/bin/env python3
"""go adapter — plan §3.2 row 5.

lint=`go vet`, typecheck=`staticcheck`, test=`go test ./...`,
build=`GOOS/GOARCH go build -mod=readonly` per build target, install=`go mod verify`.
GOOS/GOARCH are exported by CI (the argv below carries the env via the build
target's os/arch); a unit cannot swap the vet/staticcheck targets.
"""

from __future__ import annotations

import sys

ALLOWED_OPTIONS: tuple[str, ...] = ()

SAMPLE_UNIT = {
    "name": "tool",
    "path": "cmd/tool",
    "toolchain": {"version": "1.23"},
    "build": {
        "kind": "compiled",
        "outputs": [{"name": "tool", "kind": "bin"}],
        "targets": [{"os": "linux", "arch": "arm64", "runner": "ubuntu-24.04-arm", "method": "native"}],
    },
}

# GOOS/GOARCH spellings per plan targets os/arch vocabulary.
_GOOS = {"darwin": "darwin", "linux": "linux", "windows": "windows"}
_GOARCH = {"arm64": "arm64", "x86_64": "amd64"}


def _pkg(path: str) -> str:
    p = f"./{path}/...".replace("//", "/")
    return p if p != "./..." else "..."


def build_commands(unit: dict, command: str, opts: dict | None = None) -> list[list[str]]:
    del opts  # go has no adapter_options
    path = unit.get("path", ".")
    if command == "lint":
        return [["go", "vet", _pkg(path)]]
    if command == "typecheck":
        return [["staticcheck", _pkg(path)]]
    if command == "test":
        return [["go", "test", _pkg(path)]]
    if command == "build":
        targets = unit.get("build", {}).get("targets", [])
        out = []
        for t in targets:
            goos = _GOOS.get(t.get("os"), "")
            goarch = _GOARCH.get(t.get("arch"), "")
            # The env prefix is how CI exports GOOS/GOARCH for this argv.
            out.append(["env", f"GOOS={goos}", f"GOARCH={goarch}", "go", "build", "-mod=readonly", _pkg(path)])
        return out
    if command == "install":
        return [["go", "mod", "verify"]]
    raise ValueError(f"go: unknown command {command!r}")


if __name__ == "__main__":
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2]))  # scripts/ — bare-CLI import
    from cpvppc.adapters import sample_main

    sample_main("go")
    sys.exit(0)
