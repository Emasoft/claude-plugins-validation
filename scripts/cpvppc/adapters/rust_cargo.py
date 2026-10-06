#!/usr/bin/env python3
"""rust-cargo adapter — plan §3.2 row 3.

lint=`cargo fmt --check`, typecheck=`cargo clippy --all-targets --locked -- -D warnings`,
test=`cargo test --locked`, install side runs `--locked`,
build=`cargo build --release --locked --target T` (native | cross | cargo zigbuild).
`-Werror` is never forced on C built by `cc` — the crate's own CFLAGS are left alone;
only the clippy lints above carry `-D warnings`.
"""

from __future__ import annotations

import sys

ALLOWED_OPTIONS: tuple[str, ...] = ()

SAMPLE_UNIT = {
    "name": "engine",
    "path": "rust",
    "toolchain": {"version": "1.82.0"},
    "build": {
        "kind": "compiled",
        "outputs": [{"name": "pss", "kind": "bin"}],
        "targets": [{"os": "darwin", "arch": "arm64", "runner": "macos-14", "method": "native", "c_compiler": "clang"}],
    },
}

# build.kind target → the wrapper prefix that provides the cross toolchain.
_METHOD_TOOLS = {
    "native": [],
    "cross": ["cross"],
    "zigbuild": ["cargo", "zigbuild"],
    "docker": ["cross"],
}


def build_commands(unit: dict, command: str, opts: dict | None = None) -> list[list[str]]:
    del opts  # rust-cargo has no adapter_options
    path = unit.get("path", ".")
    cargo = ["cargo", "--manifest-path", f"{path}/Cargo.toml"]
    if command == "lint":
        return [cargo + ["fmt", "--check"]]
    if command == "typecheck":
        return [cargo + ["clippy", "--all-targets", "--locked", "--", "-D", "warnings"]]
    if command == "test":
        return [cargo + ["test", "--locked"]]
    if command == "build":
        targets = unit.get("build", {}).get("targets", [])
        out = []
        for t in targets:
            triple = t.get("triple") or {
                ("darwin", "arm64"): "aarch64-apple-darwin",
                ("darwin", "x86_64"): "x86_64-apple-darwin",
                ("linux", "x86_64"): "x86_64-unknown-linux-gnu",
                ("linux", "arm64"): "aarch64-unknown-linux-gnu",
                ("windows", "x86_64"): "x86_64-pc-windows-msvc",
                ("windows", "arm64"): "aarch64-pc-windows-msvc",
            }.get((t.get("os"), t.get("arch")), "")
            prefix = _METHOD_TOOLS.get(t.get("method", "native"), [])
            if prefix[:1] == ["cargo"] and "zigbuild" in prefix:
                out.append(
                    [
                        "cargo",
                        "zigbuild",
                        "--manifest-path",
                        f"{path}/Cargo.toml",
                        "--release",
                        "--locked",
                        "--target",
                        triple,
                    ]
                )
            elif prefix == ["cross"]:
                out.append(
                    [
                        "cross",
                        "build",
                        "--manifest-path",
                        f"{path}/Cargo.toml",
                        "--release",
                        "--locked",
                        "--target",
                        triple,
                    ]
                )
            else:
                out.append(cargo + ["build", "--release", "--locked", "--target", triple])
        return out
    if command == "install":
        return [cargo + ["fetch", "--locked"]]
    raise ValueError(f"rust-cargo: unknown command {command!r}")


if __name__ == "__main__":
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2]))  # scripts/ — bare-CLI import
    from cpvppc.adapters import sample_main

    sample_main("rust-cargo")
    sys.exit(0)
