# Plugin Binary Builds

## Table of Contents
- [When to Add a Build Phase](#when-to-add-a-build-phase)
- [build-binaries.yml — Cross-Platform Compilation Workflow](#build-binariesyml--cross-platform-compilation-workflow)
- [Binary Distribution Pattern](#binary-distribution-pattern)
- [Platform Detection Wrapper](#platform-detection-wrapper)
- [Extending the Python Pre-Push Hook](#extending-the-python-pre-push-hook)
- [Extending publish.py for Binary Builds](#extending-publishpy-for-binary-builds)
- [Extending ci.yml for Binary Builds](#extending-ciyml-for-binary-builds)
- [Cargo Release Profile (Rust Optimization)](#cargo-release-profile-rust-optimization)

---

## When to Add a Build Phase

The plugin repo pipeline is always Python (pre-push hook, publish.py, CI workflows). However, a plugin's internal scripts or hooks may use compiled languages. Scan the plugin for:

| File Found | Language | Build Command |
|---|---|---|
| `Cargo.toml` | Rust | `cargo build --release` |
| `package.json` with `"build"` script | JS/TS | `bun run build` or `npm run build` |
| `go.mod` | Go | `go build -o <output> .` |
| `Makefile` | C/C++ | `make` |
| `CMakeLists.txt` | C/C++ | `cmake --build .` |

Also check `hooks.json` and hook scripts for references to binaries that must be compiled.

If any compiled component is found, add a build step **before** the validate/test steps in:
- `git-hooks/pre-push` (local builds — current platform only)
- `scripts/publish.py` (release builds — current platform only)
- `.github/workflows/ci.yml` (CI builds — current platform only)
- `.github/workflows/build-binaries.yml` (cross-compilation — all 5 platforms)

---

## build-binaries.yml — Cross-Platform Compilation Workflow

Cross-compiles the binary for 5 targets: darwin-arm64, darwin-x86_64, linux-arm64, linux-x86_64, windows-x86_64. Uses native compilation on macOS and `cross` on Linux for cross-targets.

```yaml
name: Build Binaries

on:
  push:
    branches: [<placeholder-for-default-branch>]
    paths:
      - '<placeholder-for-source-dir>/**'
  workflow_dispatch:

jobs:
  build-macos:
    runs-on: macos-15
    strategy:
      fail-fast: false
      matrix:
        include:
          - target: aarch64-apple-darwin
            binary_suffix: darwin-arm64
          - target: x86_64-apple-darwin
            binary_suffix: darwin-x86_64
    steps:
      - uses: actions/checkout@v4

      - name: Install Rust toolchain
        uses: dtolnay/rust-toolchain@stable
        with:
          targets: ${{ matrix.target }}

      - name: Build
        run: |
          cargo build --release \
            --target ${{ matrix.target }} \
            --manifest-path <placeholder-for-source-dir>/Cargo.toml

      - name: Rename binary
        run: |
          cp <placeholder-for-cargo-target-dir>/${{ matrix.target }}/release/<placeholder-for-binary-name> \
             <placeholder-for-binary-name>-${{ matrix.binary_suffix }}

      - name: Upload artifact
        uses: actions/upload-artifact@v4
        with:
          name: <placeholder-for-binary-name>-${{ matrix.binary_suffix }}
          path: <placeholder-for-binary-name>-${{ matrix.binary_suffix }}

  build-cross:
    runs-on: ubuntu-latest
    strategy:
      fail-fast: false
      matrix:
        include:
          - target: aarch64-unknown-linux-gnu
            binary_suffix: linux-arm64
          - target: x86_64-unknown-linux-gnu
            binary_suffix: linux-x86_64
          - target: x86_64-pc-windows-gnu
            binary_suffix: windows-x86_64.exe
    steps:
      - uses: actions/checkout@v4

      - name: Install Rust toolchain
        uses: dtolnay/rust-toolchain@stable

      - name: Install cross
        run: cargo install cross --git https://github.com/cross-rs/cross

      - name: Build with cross
        run: |
          cross build --release \
            --target ${{ matrix.target }} \
            --manifest-path <placeholder-for-source-dir>/Cargo.toml

      - name: Rename binary
        run: |
          SRC="<placeholder-for-cargo-target-dir>/${{ matrix.target }}/release/<placeholder-for-binary-name>"
          # Windows target produces .exe
          if [ -f "${SRC}.exe" ]; then SRC="${SRC}.exe"; fi
          cp "$SRC" "<placeholder-for-binary-name>-${{ matrix.binary_suffix }}"

      - name: Upload artifact
        uses: actions/upload-artifact@v4
        with:
          name: <placeholder-for-binary-name>-${{ matrix.binary_suffix }}
          path: <placeholder-for-binary-name>-${{ matrix.binary_suffix }}

  commit-binaries:
    needs: [build-macos, build-cross]
    runs-on: ubuntu-latest
    permissions:
      contents: write
    steps:
      - uses: actions/checkout@v4

      - name: Download all artifacts
        uses: actions/download-artifact@v4
        with:
          path: artifacts/

      - name: Copy binaries to output directory
        run: |
          mkdir -p <placeholder-for-binary-output-dir>
          for dir in artifacts/*/; do
            cp "$dir"* <placeholder-for-binary-output-dir>/
          done
          chmod +x <placeholder-for-binary-output-dir>/<placeholder-for-binary-name>-*

      - name: List binaries
        run: ls -la <placeholder-for-binary-output-dir>/

      - name: Commit and push binaries
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "github-actions[bot]@users.noreply.github.com"
          git add <placeholder-for-binary-output-dir>/
          git diff --cached --quiet && echo "No changes" && exit 0
          git commit -m "chore: update compiled binaries for $(git rev-parse --short HEAD)"
          git push
```

### Adapting for Non-Rust Languages

- **Go**: Replace `cargo build` with `GOOS=<os> GOARCH=<arch> go build -o <output>`. No need for `cross`.
- **C/C++**: Use cross-compilation toolchains (e.g., `gcc-aarch64-linux-gnu`). macOS builds need Xcode.
- **JS/TS bundled binaries** (e.g., `pkg`, `bun build --compile`): Replace cargo steps with the appropriate bundler command per target.

---

## Binary Distribution Pattern

### Naming Convention

```
<binary-name>-<os>-<arch>[.exe]
```

Examples:
- `pss-darwin-arm64`
- `pss-darwin-x86_64`
- `pss-linux-arm64`
- `pss-linux-x86_64`
- `pss-windows-x86_64.exe`

### Storage

Binaries are committed to git in `<placeholder-for-binary-output-dir>/`. This provides:
- **Zero install friction** — `git clone` gives you everything
- **Works offline** — no downloads needed at runtime
- **No build tools needed** — users don't need cargo/go/make installed
- **Version-locked** — binaries match the exact source code version

### Build Scope

| Context | Builds For |
|---|---|
| Pre-push hook (local) | Current platform only |
| publish.py (local) | Current platform only |
| CI (ci.yml) | Current platform only (validation) |
| build-binaries.yml | All 5 platforms (cross-compilation) |

---

## Platform Detection Wrapper

Python template for hooks/scripts that need to call a compiled binary at runtime:

```python
"""Platform detection for compiled binary invocation."""

import platform
import subprocess
import sys
from pathlib import Path


def detect_binary_path(binary_name: str, plugin_root: Path) -> Path | None:
    """Return the platform-specific binary path, or None if not found."""
    system = platform.system().lower()
    machine = platform.machine().lower()

    # Normalize OS name
    os_map = {"darwin": "darwin", "linux": "linux", "windows": "windows"}
    os_name = os_map.get(system)
    if not os_name:
        print(f"WARNING: Unsupported OS '{system}'", file=sys.stderr)
        return None

    # Normalize architecture
    arch_map = {
        "arm64": "arm64", "aarch64": "arm64",
        "x86_64": "x86_64", "amd64": "x86_64",
    }
    arch = arch_map.get(machine)
    if not arch:
        print(f"WARNING: Unsupported architecture '{machine}'", file=sys.stderr)
        return None

    # Compose filename
    suffix = ".exe" if os_name == "windows" else ""
    filename = f"{binary_name}-{os_name}-{arch}{suffix}"
    binary_path = plugin_root / "<placeholder-for-binary-output-dir>" / filename

    if not binary_path.is_file():
        print(f"WARNING: Binary not found: {binary_path}", file=sys.stderr)
        return None

    return binary_path


def run_binary(binary_name: str, plugin_root: Path, args: list[str]) -> int:
    """Run the platform-specific binary. Returns exit code, or 1 if not found."""
    binary_path = detect_binary_path(binary_name, plugin_root)
    if not binary_path:
        return 1
    result = subprocess.run([str(binary_path)] + args)
    return result.returncode
```

---

## Extending the Python Pre-Push Hook

Insert a **build phase** before the version bump check in the pre-push hook template (from `plugin-hooks-and-scripts.md`). Add this block after the `main()` function header, before Gate 1:

```python
    # Gate 0: Build compiled components (local platform only)
    cprint(f"{BLUE}Building compiled components...{NC}")

    # -- Rust --
    cargo_toml = repo_root / "<placeholder-for-source-dir>" / "Cargo.toml"
    if cargo_toml.is_file():
        rc = subprocess.run(
            ["cargo", "build", "--release",
             "--manifest-path", str(cargo_toml)],
            cwd=str(repo_root),
        ).returncode
        if rc != 0:
            cprint(f"{RED}BLOCKED: Rust build failed{NC}")
            return 1
        cprint(f"{GREEN}Rust build OK{NC}")

    # -- Node/Bun --
    pkg_json = repo_root / "<placeholder-for-source-dir>" / "package.json"
    if pkg_json.is_file():
        pkg = json.loads(pkg_json.read_text())
        if "build" in pkg.get("scripts", {}):
            rc = subprocess.run(
                ["bun", "run", "build"],
                cwd=str(pkg_json.parent),
            ).returncode
            if rc != 0:
                cprint(f"{RED}BLOCKED: JS/TS build failed{NC}")
                return 1
            cprint(f"{GREEN}JS/TS build OK{NC}")

    # -- Go --
    go_mod = repo_root / "<placeholder-for-source-dir>" / "go.mod"
    if go_mod.is_file():
        rc = subprocess.run(
            ["go", "build", "-o",
             str(repo_root / "<placeholder-for-binary-output-dir>" / "<placeholder-for-binary-name>"),
             "."],
            cwd=str(go_mod.parent),
        ).returncode
        if rc != 0:
            cprint(f"{RED}BLOCKED: Go build failed{NC}")
            return 1
        cprint(f"{GREEN}Go build OK{NC}")
```

**Key points:**
- The build phase is Gate 0 — it runs before version bump, lint, and validate
- Only builds for the current platform (no cross-compilation locally)
- Each language block is optional — include only the ones your plugin needs
- The agent decides which blocks to include based on what it finds in the plugin
- The Node/Bun block uses `json.loads()` — this is already imported in the base pre-push template

---

## Extending publish.py for Binary Builds

Insert a build step in the publish pipeline **after lint, before validate** (between Step 3 and Step 4 in the pipeline template from `plugin-hooks-and-scripts.md`):

```
Step 3: Lint files
Step 3.5: Build compiled components    ← NEW
Step 4: Validate plugin
```

Add this to the pipeline:

```python
    # Step 3.5: Build compiled components
    print("Step 3.5: Building compiled components...")

    # For Rust: also update Cargo.toml version during bump
    cargo_toml = root / "<placeholder-for-source-dir>" / "Cargo.toml"
    if cargo_toml.is_file():
        run(["cargo", "build", "--release",
             "--manifest-path", str(cargo_toml)], cwd=root)
        # Verify binary exists after build
        binary = root / "<placeholder-for-cargo-target-dir>" / "release" / "<placeholder-for-binary-name>"
        if not binary.is_file():
            print(f"ERROR: Expected binary not found: {binary}", file=sys.stderr)
            sys.exit(1)
```

**Version bump for Rust**: If the plugin includes a `Cargo.toml`, the `do_bump()` function should also update `version = "X.Y.Z"` in `Cargo.toml`. Add this function alongside `update_pyproject_toml`:

```python
def update_cargo_toml(root: Path, new_ver: str) -> tuple[bool, str]:
    """Update version in Cargo.toml."""
    cargo = root / "<placeholder-for-source-dir>" / "Cargo.toml"
    if not cargo.is_file():
        return True, "No Cargo.toml found (skipped)"
    text = cargo.read_text(encoding="utf-8")
    import re
    updated = re.sub(
        r'^version\s*=\s*"[^"]*"',
        f'version = "{new_ver}"',
        text,
        count=1,
        flags=re.MULTILINE,
    )
    if updated == text:
        return False, "Could not find version field in Cargo.toml"
    cargo.write_text(updated, encoding="utf-8")
    return True, f"Cargo.toml -> {new_ver}"
```

Then call it from `do_bump()` alongside the other version update functions.

**Cargo.lock**: For binary plugins, `Cargo.lock` should be committed to git (not gitignored). If the `.gitignore` template includes `Cargo.lock`, remove that line for binary plugins. Libraries should ignore `Cargo.lock`; binaries should commit it for reproducible builds.

---

## Extending ci.yml for Binary Builds

Add toolchain setup and build steps **before** the validate step in `ci.yml` (from `plugin-workflows.md`):

```yaml
      # -- Add these steps BEFORE "Run plugin validation" --

      - name: Install Rust toolchain
        if: hashFiles('<placeholder-for-source-dir>/Cargo.toml') != ''
        uses: dtolnay/rust-toolchain@stable

      - name: Build compiled components
        if: hashFiles('<placeholder-for-source-dir>/Cargo.toml') != ''
        run: |
          cargo build --release \
            --manifest-path <placeholder-for-source-dir>/Cargo.toml
```

For other languages, replace with the appropriate toolchain setup:
- **Go**: `actions/setup-go@v5` + `go build`
- **Node/Bun**: `oven-sh/setup-bun@v2` + `bun run build`
- **C/C++**: gcc is pre-installed on `ubuntu-latest` + `make`

---

## Cargo Release Profile (Rust Optimization)

For Rust binaries, add this to `Cargo.toml` for optimized release builds:

```toml
[profile.release]
opt-level = 3
lto = true
codegen-units = 1
strip = true
panic = "abort"
```

| Setting | Effect |
|---|---|
| `opt-level = 3` | Maximum optimization |
| `lto = true` | Link-Time Optimization — smaller, faster binary |
| `codegen-units = 1` | Single codegen unit — enables more optimizations |
| `strip = true` | Strip debug symbols — smaller binary |
| `panic = "abort"` | No unwinding — smaller binary, faster panics |

**Trade-off**: Longer compile times (~2-3x) for smaller and faster binaries. This is appropriate for release builds in CI and publish pipelines; use default profile for local development.

---

## Placeholder Reference

All binary-build-specific values use `<placeholder-for-...>` tokens. Replace before use.

| Placeholder | Description | Example Value |
|---|---|---|
| `<placeholder-for-default-branch>` | Default branch name (shared with other templates) | `main` |
| `<placeholder-for-source-dir>` | Directory containing the compiled source code (Cargo.toml, go.mod, etc.) | `src` or `rust-src` |
| `<placeholder-for-binary-name>` | Name of the compiled binary (without platform suffix) | `pss` or `my-tool` |
| `<placeholder-for-cargo-target-dir>` | Cargo target directory (Rust only) | `src/target` or `rust-src/target` |
| `<placeholder-for-binary-output-dir>` | Directory where compiled binaries are stored in the repo | `bin` or `binaries` |
