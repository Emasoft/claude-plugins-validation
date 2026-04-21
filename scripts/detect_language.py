#!/usr/bin/env python3
"""Language detection for plugin projects.

Detects which programming languages a plugin uses based on the presence of
canonical config files (manifests, build files) or source file extensions.

Used by:
- validate_plugin.py — to pick which linters/tools to run
- standardize_plugin.py — CI audit and linter selection
- detect_lockfiles.py — cross-check lockfiles vs detected languages

A plugin may use multiple languages (e.g. a Python plugin with a Node.js
MCP server shim). This module returns every detected language with the
marker file that triggered detection.

Usage:
    from detect_language import detect_languages
    langs = detect_languages(Path("/path/to/plugin"))
    # {'python': PosixPath('.../pyproject.toml'), 'js': PosixPath('.../package.json')}
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

# Canonical markers: filename -> language.
# Order does NOT imply priority — every match is returned.
PYTHON_MARKERS: tuple[str, ...] = ("pyproject.toml", "setup.py", "requirements.txt")
JS_MARKERS: tuple[str, ...] = ("package.json",)
DENO_MARKERS: tuple[str, ...] = ("deno.json", "deno.jsonc")
RUST_MARKERS: tuple[str, ...] = ("Cargo.toml",)
GO_MARKERS: tuple[str, ...] = ("go.mod",)
ELIXIR_MARKERS: tuple[str, ...] = ("mix.exs",)
RUBY_MARKERS: tuple[str, ...] = ("Gemfile",)
JAVA_MARKERS: tuple[str, ...] = ("pom.xml",)
DART_MARKERS: tuple[str, ...] = ("pubspec.yaml",)

# Gradle markers — Java or Kotlin depending on plugin contents
KOTLIN_GRADLE_MARKERS: tuple[str, ...] = ("build.gradle.kts",)
GRADLE_MARKERS: tuple[str, ...] = ("build.gradle",)


def _first_existing(plugin_root: Path, names: Iterable[str]) -> Path | None:
    """Return the first existing path from an iterable of filenames at plugin_root.

    Only checks the plugin root (not subdirectories) because config files for
    the plugin itself live at the root. Skips hidden files and returns None
    if no match.
    """
    for name in names:
        candidate = plugin_root / name
        if candidate.is_file():
            return candidate
    return None


def _has_any_source_file(plugin_root: Path, suffix: str, limit: int = 500) -> Path | None:
    """Return the first source file with the given suffix anywhere under plugin_root.

    Walks the plugin tree (excluding common ignore dirs) and returns the first
    file matching the suffix. Used as a fallback when no canonical manifest
    exists but the language is still present (e.g. TypeScript with no
    tsconfig.json).

    Args:
        plugin_root: Directory to search.
        suffix: File suffix including the dot (e.g. ".ts").
        limit: Max files to scan before giving up (avoids runaway walks).

    Returns:
        Path to the first matching file, or None.
    """
    # Skip common non-source dirs that can bloat scans and would not contain
    # project-owned source files (vendored deps, build artifacts, etc.).
    skip_dirs = {
        ".git",
        "node_modules",
        ".venv",
        "venv",
        "__pycache__",
        "dist",
        "build",
        "target",
        ".pytest_cache",
        ".ruff_cache",
        ".mypy_cache",
        ".tldr",
        ".claude",
        "reports",
        "reports_dev",
        "docs_dev",
        "scripts_dev",
        "samples_dev",
        "examples_dev",
        "tests_dev",
        "downloads_dev",
        "libs_dev",
        "builds_dev",
        "vendor",
        ".idea",
        ".vscode",
    }
    count = 0
    for path in plugin_root.rglob(f"*{suffix}"):
        count += 1
        if count > limit:
            return None
        # Skip anything under a skipped directory
        try:
            rel_parts = path.relative_to(plugin_root).parts
        except ValueError:
            continue
        if any(part in skip_dirs for part in rel_parts):
            continue
        if path.is_file():
            return path
    return None


def _detect_gradle_language(plugin_root: Path, gradle_path: Path) -> str:
    """Inspect a build.gradle file to distinguish Kotlin from plain Java.

    Returns 'kotlin' if the build file applies a Kotlin plugin, otherwise
    'java'. build.gradle.kts always means kotlin and is not passed here.
    """
    try:
        content = gradle_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "java"
    # Kotlin plugin markers in a Groovy build.gradle
    kotlin_markers = (
        "id 'org.jetbrains.kotlin",
        'id "org.jetbrains.kotlin',
        "apply plugin: 'kotlin'",
        'apply plugin: "kotlin"',
        "kotlin-android",
    )
    if any(marker in content for marker in kotlin_markers):
        return "kotlin"
    return "java"


def detect_languages(plugin_root: Path) -> dict[str, Path]:
    """Return a mapping of detected language -> marker file path.

    Detection rules:
        - python : pyproject.toml OR setup.py OR requirements.txt
        - js     : package.json (without tsconfig.json AND without any .ts source)
        - ts     : package.json + tsconfig.json, OR any .ts file in the tree
        - deno   : deno.json OR deno.jsonc
        - rust   : Cargo.toml
        - go     : go.mod
        - elixir : mix.exs
        - ruby   : Gemfile
        - java   : pom.xml, OR build.gradle without Kotlin plugin
        - kotlin : build.gradle.kts, OR build.gradle with Kotlin plugin
        - dart   : pubspec.yaml

    The return value is a dict[language, Path] where Path points to the file
    that triggered detection. Multiple languages may coexist.

    Args:
        plugin_root: Path to the plugin root directory.

    Returns:
        Dict of {language: marker_file_path}. Empty if no language detected.
    """
    if not plugin_root.is_dir():
        return {}

    detected: dict[str, Path] = {}

    # Python
    py_marker = _first_existing(plugin_root, PYTHON_MARKERS)
    if py_marker is not None:
        detected["python"] = py_marker

    # JS / TS — package.json discriminator
    pkg_json = _first_existing(plugin_root, JS_MARKERS)
    tsconfig = plugin_root / "tsconfig.json"
    # Check for TS: either tsconfig.json present OR any .ts file in tree.
    # Scanning only happens when package.json is absent, to keep the fast path cheap.
    ts_source: Path | None = None
    if tsconfig.is_file():
        ts_source = tsconfig
    else:
        found_ts = _has_any_source_file(plugin_root, ".ts")
        if found_ts is not None:
            ts_source = found_ts

    if pkg_json is not None and tsconfig.is_file():
        # package.json + tsconfig.json -> TypeScript project
        detected["ts"] = tsconfig
    elif pkg_json is not None:
        # package.json alone -> JS (unless stray .ts files exist, then both)
        detected["js"] = pkg_json
        if ts_source is not None:
            detected["ts"] = ts_source
    elif ts_source is not None:
        # No package.json but TypeScript source files -> TS only
        detected["ts"] = ts_source

    # Deno
    deno_marker = _first_existing(plugin_root, DENO_MARKERS)
    if deno_marker is not None:
        detected["deno"] = deno_marker

    # Rust
    rust_marker = _first_existing(plugin_root, RUST_MARKERS)
    if rust_marker is not None:
        detected["rust"] = rust_marker

    # Go
    go_marker = _first_existing(plugin_root, GO_MARKERS)
    if go_marker is not None:
        detected["go"] = go_marker

    # Elixir
    elixir_marker = _first_existing(plugin_root, ELIXIR_MARKERS)
    if elixir_marker is not None:
        detected["elixir"] = elixir_marker

    # Ruby
    ruby_marker = _first_existing(plugin_root, RUBY_MARKERS)
    if ruby_marker is not None:
        detected["ruby"] = ruby_marker

    # Java (pom.xml always = java)
    java_marker = _first_existing(plugin_root, JAVA_MARKERS)
    if java_marker is not None:
        detected["java"] = java_marker

    # Kotlin (build.gradle.kts always = kotlin)
    kotlin_kts = _first_existing(plugin_root, KOTLIN_GRADLE_MARKERS)
    if kotlin_kts is not None:
        detected["kotlin"] = kotlin_kts

    # Plain build.gradle — Groovy-based; detect Kotlin plugin to disambiguate
    gradle_marker = _first_existing(plugin_root, GRADLE_MARKERS)
    if gradle_marker is not None:
        gradle_lang = _detect_gradle_language(plugin_root, gradle_marker)
        # Only add if not already detected via other markers — avoid stomping
        # pom.xml-driven java or build.gradle.kts-driven kotlin.
        if gradle_lang not in detected:
            detected[gradle_lang] = gradle_marker

    # Dart
    dart_marker = _first_existing(plugin_root, DART_MARKERS)
    if dart_marker is not None:
        detected["dart"] = dart_marker

    return detected


def main() -> int:
    """CLI entry point — prints detected languages for a given plugin path."""
    import argparse

    parser = argparse.ArgumentParser(description="Detect languages used by a plugin.")
    parser.add_argument("path", nargs="?", default=".", help="Plugin root path")
    args = parser.parse_args()

    plugin_root = Path(args.path).resolve()
    if not plugin_root.is_dir():
        print(f"Error: {plugin_root} is not a directory")
        return 1

    langs = detect_languages(plugin_root)
    if not langs:
        print(f"No languages detected in {plugin_root}")
        return 0

    print(f"Detected languages in {plugin_root}:")
    for lang in sorted(langs):
        marker = langs[lang]
        try:
            rel = marker.relative_to(plugin_root)
        except ValueError:
            rel = marker
        print(f"  {lang:8s}  {rel}")
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
