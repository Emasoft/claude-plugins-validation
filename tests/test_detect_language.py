#!/usr/bin/env python3
"""Tests for detect_language.py (TRDD-79638eb6 — Part 1).

Covers detect_languages() against fixture-style synthetic plugin trees.
Each detection rule listed in the TRDD has at least one positive and one
negative test:

- python : pyproject.toml / setup.py / requirements.txt
- js     : package.json (without tsconfig + without .ts files)
- ts     : package.json + tsconfig.json, OR any .ts file in the tree
- deno   : deno.json or deno.jsonc
- rust   : Cargo.toml
- go     : go.mod
- elixir : mix.exs
- ruby   : Gemfile
- java   : pom.xml, OR build.gradle without Kotlin plugin
- kotlin : build.gradle.kts, OR build.gradle with Kotlin plugin
- dart   : pubspec.yaml

Plus mixed-language plugins (Python + JS shim), empty trees, and
non-existent paths.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Make scripts/ importable
SCRIPTS_DIR = Path(__file__).parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from detect_language import detect_languages  # noqa: E402


class TestDetectLanguagesPython:
    """Python detection via the three canonical markers."""

    def test_pyproject_toml_detects_python(self, tmp_path):
        """pyproject.toml at root marks the project as Python."""
        (tmp_path / "pyproject.toml").write_text("[project]\nname = 'x'\n")
        result = detect_languages(tmp_path)
        assert "python" in result
        assert result["python"].name == "pyproject.toml"

    def test_setup_py_detects_python(self, tmp_path):
        """setup.py at root marks the project as Python (legacy layout)."""
        (tmp_path / "setup.py").write_text("from setuptools import setup\nsetup()\n")
        result = detect_languages(tmp_path)
        assert "python" in result
        assert result["python"].name == "setup.py"

    def test_requirements_txt_detects_python(self, tmp_path):
        """requirements.txt at root marks the project as Python."""
        (tmp_path / "requirements.txt").write_text("requests>=2.30\n")
        result = detect_languages(tmp_path)
        assert "python" in result
        assert result["python"].name == "requirements.txt"

    def test_no_python_marker_skips_python(self, tmp_path):
        """A tree with no Python marker omits 'python' from the result."""
        (tmp_path / "Cargo.toml").write_text("[package]\nname = 'x'\n")
        result = detect_languages(tmp_path)
        assert "python" not in result


class TestDetectLanguagesJsTs:
    """package.json / tsconfig.json discriminator for JS vs TS."""

    def test_package_json_alone_detects_js(self, tmp_path):
        """package.json without tsconfig and without .ts files = pure JS."""
        (tmp_path / "package.json").write_text('{"name": "x"}')
        result = detect_languages(tmp_path)
        assert "js" in result
        assert "ts" not in result

    def test_package_json_plus_tsconfig_detects_ts_only(self, tmp_path):
        """package.json + tsconfig.json registers as TypeScript only."""
        (tmp_path / "package.json").write_text('{"name": "x"}')
        (tmp_path / "tsconfig.json").write_text("{}")
        result = detect_languages(tmp_path)
        assert "ts" in result
        assert "js" not in result

    def test_package_json_with_stray_ts_file_detects_both(self, tmp_path):
        """package.json + any .ts file (no tsconfig) = both js AND ts."""
        (tmp_path / "package.json").write_text('{"name": "x"}')
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "index.ts").write_text("export const x = 1;\n")
        result = detect_languages(tmp_path)
        assert "js" in result
        assert "ts" in result

    def test_only_ts_file_no_package_json_detects_ts(self, tmp_path):
        """A standalone .ts file with no package.json still registers as TS."""
        (tmp_path / "snippet.ts").write_text("const x: number = 1;\n")
        result = detect_languages(tmp_path)
        assert "ts" in result
        assert "js" not in result

    def test_skip_dirs_are_not_scanned_for_ts(self, tmp_path):
        """node_modules / .venv etc. should NOT be scanned for stray .ts files."""
        node_modules = tmp_path / "node_modules" / "some-pkg"
        node_modules.mkdir(parents=True)
        (node_modules / "lib.ts").write_text("export {};\n")
        # No package.json at root — and the stray .ts is inside node_modules
        result = detect_languages(tmp_path)
        assert "ts" not in result
        assert "js" not in result


class TestDetectLanguagesDeno:
    """Deno detection."""

    def test_deno_json_detects_deno(self, tmp_path):
        """deno.json at root = Deno project."""
        (tmp_path / "deno.json").write_text("{}")
        result = detect_languages(tmp_path)
        assert "deno" in result

    def test_deno_jsonc_detects_deno(self, tmp_path):
        """deno.jsonc (with comments allowed) is also a Deno marker."""
        (tmp_path / "deno.jsonc").write_text("// comment\n{}")
        result = detect_languages(tmp_path)
        assert "deno" in result

    def test_no_deno_marker_skips_deno(self, tmp_path):
        """Without deno.json* the result has no 'deno' key."""
        (tmp_path / "package.json").write_text("{}")
        result = detect_languages(tmp_path)
        assert "deno" not in result


class TestDetectLanguagesRust:
    """Rust detection via Cargo.toml."""

    def test_cargo_toml_detects_rust(self, tmp_path):
        """Cargo.toml at root marks the project as Rust."""
        (tmp_path / "Cargo.toml").write_text("[package]\nname = 'x'\n")
        result = detect_languages(tmp_path)
        assert "rust" in result

    def test_no_cargo_skips_rust(self, tmp_path):
        """Without Cargo.toml the result has no 'rust' key."""
        (tmp_path / "go.mod").write_text("module example.com/x\n")
        result = detect_languages(tmp_path)
        assert "rust" not in result


class TestDetectLanguagesGo:
    """Go detection via go.mod."""

    def test_go_mod_detects_go(self, tmp_path):
        """go.mod at root marks the project as Go."""
        (tmp_path / "go.mod").write_text("module example.com/x\n")
        result = detect_languages(tmp_path)
        assert "go" in result

    def test_no_go_mod_skips_go(self, tmp_path):
        """Without go.mod the result has no 'go' key."""
        (tmp_path / "package.json").write_text("{}")
        result = detect_languages(tmp_path)
        assert "go" not in result


class TestDetectLanguagesElixir:
    """Elixir detection via mix.exs."""

    def test_mix_exs_detects_elixir(self, tmp_path):
        """mix.exs at root marks the project as Elixir."""
        (tmp_path / "mix.exs").write_text("defmodule MyApp.MixProject do\nend\n")
        result = detect_languages(tmp_path)
        assert "elixir" in result

    def test_no_mix_exs_skips_elixir(self, tmp_path):
        """Without mix.exs the result has no 'elixir' key."""
        (tmp_path / "go.mod").write_text("module x\n")
        result = detect_languages(tmp_path)
        assert "elixir" not in result


class TestDetectLanguagesRuby:
    """Ruby detection via Gemfile."""

    def test_gemfile_detects_ruby(self, tmp_path):
        """Gemfile at root marks the project as Ruby."""
        (tmp_path / "Gemfile").write_text("source 'https://rubygems.org'\n")
        result = detect_languages(tmp_path)
        assert "ruby" in result

    def test_no_gemfile_skips_ruby(self, tmp_path):
        """Without Gemfile the result has no 'ruby' key."""
        (tmp_path / "Cargo.toml").write_text("[package]\nname = 'x'\n")
        result = detect_languages(tmp_path)
        assert "ruby" not in result


class TestDetectLanguagesJavaKotlin:
    """Java/Kotlin discrimination via pom.xml and build.gradle*."""

    def test_pom_xml_detects_java(self, tmp_path):
        """pom.xml at root = Java project."""
        (tmp_path / "pom.xml").write_text("<project></project>\n")
        result = detect_languages(tmp_path)
        assert "java" in result
        assert "kotlin" not in result

    def test_build_gradle_kts_detects_kotlin(self, tmp_path):
        """build.gradle.kts always = Kotlin project."""
        (tmp_path / "build.gradle.kts").write_text('plugins { kotlin("jvm") }\n')
        result = detect_languages(tmp_path)
        assert "kotlin" in result
        assert "java" not in result

    def test_build_gradle_without_kotlin_plugin_detects_java(self, tmp_path):
        """build.gradle (Groovy) without Kotlin plugin defaults to Java."""
        (tmp_path / "build.gradle").write_text("plugins { id 'java' }\n")
        result = detect_languages(tmp_path)
        assert "java" in result
        assert "kotlin" not in result

    def test_build_gradle_with_kotlin_plugin_detects_kotlin(self, tmp_path):
        """build.gradle with `id 'org.jetbrains.kotlin.jvm'` = Kotlin project."""
        (tmp_path / "build.gradle").write_text("plugins { id 'org.jetbrains.kotlin.jvm' version '1.9.0' }\n")
        result = detect_languages(tmp_path)
        assert "kotlin" in result
        assert "java" not in result

    def test_pom_xml_plus_build_gradle_keeps_java(self, tmp_path):
        """pom.xml takes precedence over a stray build.gradle for the 'java' slot."""
        (tmp_path / "pom.xml").write_text("<project></project>\n")
        (tmp_path / "build.gradle").write_text("plugins { id 'java' }\n")
        result = detect_languages(tmp_path)
        assert result.get("java", Path()).name == "pom.xml"


class TestDetectLanguagesDart:
    """Dart detection via pubspec.yaml."""

    def test_pubspec_yaml_detects_dart(self, tmp_path):
        """pubspec.yaml at root marks the project as Dart/Flutter."""
        (tmp_path / "pubspec.yaml").write_text("name: x\n")
        result = detect_languages(tmp_path)
        assert "dart" in result

    def test_no_pubspec_skips_dart(self, tmp_path):
        """Without pubspec.yaml the result has no 'dart' key."""
        (tmp_path / "go.mod").write_text("module x\n")
        result = detect_languages(tmp_path)
        assert "dart" not in result


class TestDetectLanguagesMixed:
    """Multi-language plugins (e.g. Python plugin with a Node.js MCP shim)."""

    def test_python_plus_js_shim_detects_both(self, tmp_path):
        """A Python plugin with a Node.js MCP server shim returns both python and js."""
        (tmp_path / "pyproject.toml").write_text("[project]\nname = 'x'\n")
        (tmp_path / "package.json").write_text('{"name": "x"}')
        result = detect_languages(tmp_path)
        assert "python" in result
        assert "js" in result

    def test_python_plus_rust_detects_both(self, tmp_path):
        """A Python plugin with a Rust extension returns both python and rust."""
        (tmp_path / "pyproject.toml").write_text("[project]\nname = 'x'\n")
        (tmp_path / "Cargo.toml").write_text("[package]\nname = 'x'\n")
        result = detect_languages(tmp_path)
        assert "python" in result
        assert "rust" in result


class TestDetectLanguagesEdgeCases:
    """Empty trees, missing dirs, non-directory inputs, type-stability."""

    def test_empty_directory_returns_empty_dict(self, tmp_path):
        """An empty directory returns {} with no detected languages."""
        result = detect_languages(tmp_path)
        assert result == {}

    def test_nonexistent_path_returns_empty_dict(self, tmp_path):
        """A path that does not exist returns {} (no exception)."""
        missing = tmp_path / "no-such-dir"
        result = detect_languages(missing)
        assert result == {}

    def test_path_to_file_returns_empty_dict(self, tmp_path):
        """A path that points to a regular file (not a directory) returns {}."""
        f = tmp_path / "afile.txt"
        f.write_text("hello")
        result = detect_languages(f)
        assert result == {}

    def test_returns_dict_of_str_to_path(self, tmp_path):
        """The return value's values are Path instances (typed contract)."""
        (tmp_path / "Cargo.toml").write_text("[package]\nname = 'x'\n")
        result = detect_languages(tmp_path)
        assert all(isinstance(v, Path) for v in result.values())

    def test_only_directory_no_marker_returns_empty(self, tmp_path):
        """A directory that contains only subdirs (no markers) returns {}."""
        (tmp_path / "src").mkdir()
        (tmp_path / "docs").mkdir()
        result = detect_languages(tmp_path)
        assert result == {}


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
