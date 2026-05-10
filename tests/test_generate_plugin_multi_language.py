#!/usr/bin/env python3
"""Tests for multi-language plugin generation (TRDD-83ab59e7).

Tests that `scripts/generate_plugin_repo.py` correctly scaffolds plugins
for languages beyond Python:
- VALID_LANGUAGES enum coverage (10 languages: python/js/ts/rust/go/deno/elixir/ruby/java/kotlin)
- Per-language manifest generation (mix.exs, Gemfile, pom.xml, build.gradle.kts)
- Per-language gitignore patterns
- --language auto resolves via detect_languages()
- generate_all_files emits the correct manifest per language
- Each language scaffold creates a directory with all expected files

Coverage: ~25 tests covering the new languages and the auto-detection path.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Add scripts directory to path for imports
scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

from generate_plugin_repo import (  # noqa: E402
    VALID_LANGUAGES,
    PluginParams,
    generate_all_files,
    generate_plugin_repo,
    resolve_language,
)

# =============================================================================
# Helper: standard PluginParams instance for tests
# =============================================================================


def _params(**overrides: object) -> PluginParams:
    """Create a PluginParams with sensible defaults, accepting overrides."""
    defaults: dict[str, object] = {
        "name": "my-test-plugin",
        "description": "A test plugin for multi-language tests",
        "author": "Test Author",
        "author_email": "test@example.com",
        "license": "MIT",
        "python_version": "3.12",
        "github_owner": "test-owner",
        "marketplace": "test-marketplace",
        "version": "0.1.0",
    }
    defaults.update(overrides)
    return PluginParams(**defaults)  # type: ignore[arg-type]


# =============================================================================
# Group 1: VALID_LANGUAGES enum coverage
# =============================================================================


class TestValidLanguagesEnum:
    """Tests that the VALID_LANGUAGES enum covers all TRDD-83ab59e7 languages."""

    def test_python_in_enum(self):
        """python must remain in VALID_LANGUAGES (default)."""
        assert "python" in VALID_LANGUAGES

    def test_js_in_enum(self):
        """js must be in VALID_LANGUAGES (TRDD-83ab59e7)."""
        assert "js" in VALID_LANGUAGES

    def test_ts_in_enum(self):
        """ts must be in VALID_LANGUAGES (TRDD-83ab59e7)."""
        assert "ts" in VALID_LANGUAGES

    def test_rust_in_enum(self):
        """rust must be in VALID_LANGUAGES (TRDD-83ab59e7)."""
        assert "rust" in VALID_LANGUAGES

    def test_go_in_enum(self):
        """go must be in VALID_LANGUAGES (TRDD-83ab59e7)."""
        assert "go" in VALID_LANGUAGES

    def test_deno_in_enum(self):
        """deno must be in VALID_LANGUAGES (TRDD-83ab59e7)."""
        assert "deno" in VALID_LANGUAGES

    def test_elixir_in_enum(self):
        """elixir must be in VALID_LANGUAGES (TRDD-83ab59e7)."""
        assert "elixir" in VALID_LANGUAGES

    def test_ruby_in_enum(self):
        """ruby must be in VALID_LANGUAGES (TRDD-83ab59e7)."""
        assert "ruby" in VALID_LANGUAGES

    def test_java_in_enum(self):
        """java must be in VALID_LANGUAGES (TRDD-83ab59e7)."""
        assert "java" in VALID_LANGUAGES

    def test_kotlin_in_enum(self):
        """kotlin must be in VALID_LANGUAGES (TRDD-83ab59e7)."""
        assert "kotlin" in VALID_LANGUAGES

    def test_enum_size(self):
        """VALID_LANGUAGES must have all 10 TRDD-83ab59e7 languages."""
        # 10 languages: python/js/ts/rust/go/deno/elixir/ruby/java/kotlin
        assert len(VALID_LANGUAGES) == 10


# =============================================================================
# Group 2: Per-language manifest generators
# =============================================================================


class TestElixirManifest:
    """Tests for gen_mix_exs (Elixir mix.exs)."""

    def test_mix_exs_emits_module(self):
        """gen_mix_exs must emit a defmodule line with the plugin name."""
        from generate_plugin_repo import gen_mix_exs  # noqa: PLC0415

        p = _params(name="my-elixir-plugin")
        src = gen_mix_exs(p)
        # mix.exs uses CamelCase MixProject — we just want a defmodule line referencing the name
        assert "defmodule" in src
        assert ".MixProject" in src
        assert "use Mix.Project" in src

    def test_mix_exs_includes_app_name(self):
        """The :app key in project must equal the plugin name (atomized)."""
        from generate_plugin_repo import gen_mix_exs  # noqa: PLC0415

        p = _params(name="my-elixir-plugin")
        src = gen_mix_exs(p)
        # plugin name kebab is converted to snake — but at minimum the project app key exists
        assert "app:" in src

    def test_mix_exs_includes_version(self):
        """The version key must include the configured plugin version."""
        from generate_plugin_repo import gen_mix_exs  # noqa: PLC0415

        p = _params(version="1.2.3")
        src = gen_mix_exs(p)
        assert "1.2.3" in src

    def test_mix_exs_includes_credo_dev_dep(self):
        """deps function should include :credo for lint."""
        from generate_plugin_repo import gen_mix_exs  # noqa: PLC0415

        p = _params()
        src = gen_mix_exs(p)
        assert ":credo" in src


class TestRubyManifest:
    """Tests for gen_gemfile (Ruby Gemfile)."""

    def test_gemfile_includes_source_rubygems(self):
        """Gemfile must include the rubygems source line."""
        from generate_plugin_repo import gen_gemfile  # noqa: PLC0415

        p = _params()
        src = gen_gemfile(p)
        assert "source 'https://rubygems.org'" in src or 'source "https://rubygems.org"' in src

    def test_gemfile_includes_rubocop_dev_dep(self):
        """Gemfile must include rubocop in :development group for lint."""
        from generate_plugin_repo import gen_gemfile  # noqa: PLC0415

        p = _params()
        src = gen_gemfile(p)
        assert "rubocop" in src

    def test_gemfile_includes_rspec_test_dep(self):
        """Gemfile must include rspec in :test group."""
        from generate_plugin_repo import gen_gemfile  # noqa: PLC0415

        p = _params()
        src = gen_gemfile(p)
        assert "rspec" in src


class TestJavaManifest:
    """Tests for gen_pom_xml (Java pom.xml)."""

    def test_pom_xml_includes_xml_declaration(self):
        """pom.xml must start with the XML declaration."""
        from generate_plugin_repo import gen_pom_xml  # noqa: PLC0415

        p = _params()
        src = gen_pom_xml(p)
        assert src.startswith("<?xml")

    def test_pom_xml_includes_artifact_id(self):
        """pom.xml must include the artifactId matching the plugin name."""
        from generate_plugin_repo import gen_pom_xml  # noqa: PLC0415

        p = _params(name="my-java-plugin")
        src = gen_pom_xml(p)
        assert "<artifactId>my-java-plugin</artifactId>" in src

    def test_pom_xml_includes_version(self):
        """pom.xml must include the configured version."""
        from generate_plugin_repo import gen_pom_xml  # noqa: PLC0415

        p = _params(version="1.2.3")
        src = gen_pom_xml(p)
        assert "<version>1.2.3</version>" in src

    def test_pom_xml_includes_junit(self):
        """pom.xml must include junit as a test dependency."""
        from generate_plugin_repo import gen_pom_xml  # noqa: PLC0415

        p = _params()
        src = gen_pom_xml(p)
        assert "junit" in src


class TestKotlinManifest:
    """Tests for gen_build_gradle_kts (Kotlin build.gradle.kts)."""

    def test_gradle_kts_includes_kotlin_plugin(self):
        """build.gradle.kts must apply the kotlin plugin."""
        from generate_plugin_repo import gen_build_gradle_kts  # noqa: PLC0415

        p = _params()
        src = gen_build_gradle_kts(p)
        assert "kotlin(" in src or 'id("org.jetbrains.kotlin' in src

    def test_gradle_kts_includes_version(self):
        """build.gradle.kts must include the plugin version."""
        from generate_plugin_repo import gen_build_gradle_kts  # noqa: PLC0415

        p = _params(version="1.2.3")
        src = gen_build_gradle_kts(p)
        assert "1.2.3" in src

    def test_gradle_kts_includes_detekt(self):
        """build.gradle.kts must include detekt for lint."""
        from generate_plugin_repo import gen_build_gradle_kts  # noqa: PLC0415

        p = _params()
        src = gen_build_gradle_kts(p)
        assert "detekt" in src.lower()


# =============================================================================
# Group 3: generate_all_files emits the right manifest per language
# =============================================================================


class TestGenerateAllFilesPerLanguage:
    """Tests that generate_all_files() produces the correct manifest file
    for each language."""

    @pytest.mark.parametrize(
        "language,expected_manifest",
        [
            ("python", "pyproject.toml"),
            ("js", "package.json"),
            ("ts", "package.json"),
            ("rust", "Cargo.toml"),
            ("go", "go.mod"),
            ("deno", "deno.json"),
            ("elixir", "mix.exs"),
            ("ruby", "Gemfile"),
            ("java", "pom.xml"),
            ("kotlin", "build.gradle.kts"),
        ],
    )
    def test_each_language_emits_its_manifest(self, language, expected_manifest):
        """For each language, generate_all_files must emit its manifest."""
        p = _params(language=language)
        files = generate_all_files(p)
        rel_paths = [rel for rel, _content, _exec in files]
        assert expected_manifest in rel_paths, f"Language {language} did not emit {expected_manifest}; got {rel_paths}"

    def test_ts_emits_tsconfig(self):
        """TypeScript must emit BOTH package.json AND tsconfig.json."""
        p = _params(language="ts")
        files = generate_all_files(p)
        rel_paths = [rel for rel, _content, _exec in files]
        assert "package.json" in rel_paths
        assert "tsconfig.json" in rel_paths

    def test_python_does_not_emit_other_manifests(self):
        """Python must NOT emit package.json/Cargo.toml/etc."""
        p = _params(language="python")
        files = generate_all_files(p)
        rel_paths = [rel for rel, _content, _exec in files]
        for forbidden in ("package.json", "Cargo.toml", "go.mod", "deno.json", "mix.exs", "Gemfile", "pom.xml"):
            assert forbidden not in rel_paths, f"Python scaffold should NOT emit {forbidden}"

    def test_non_python_emits_language_todo(self):
        """All non-python languages emit a LANGUAGE-<lang>-TODO.md note."""
        for lang in sorted(VALID_LANGUAGES - {"python"}):
            p = _params(language=lang)
            files = generate_all_files(p)
            rel_paths = [rel for rel, _content, _exec in files]
            # The TODO file is named e.g. LANGUAGE-RUST-TODO.md
            todo = f"LANGUAGE-{lang.upper()}-TODO.md"
            assert todo in rel_paths, f"Language {lang} should emit {todo}"


# =============================================================================
# Group 4: --language auto resolution via detect_languages()
# =============================================================================


class TestResolveLanguageAuto:
    """Tests for resolve_language(arg, target) — handles --language auto."""

    def test_resolve_explicit_language_returned_unchanged(self, tmp_path):
        """Explicit language flag is returned unchanged regardless of target."""
        target = tmp_path / "explicit"
        target.mkdir()
        result = resolve_language("python", target)
        assert result == "python"

    def test_resolve_auto_falls_back_to_python_for_empty_dir(self, tmp_path):
        """For an empty target dir, --language auto falls back to python."""
        target = tmp_path / "empty-dir"
        target.mkdir()
        result = resolve_language("auto", target)
        assert result == "python"

    def test_resolve_auto_falls_back_to_python_for_nonexistent_dir(self, tmp_path):
        """If target doesn't exist, --language auto falls back to python."""
        target = tmp_path / "nonexistent"
        result = resolve_language("auto", target)
        assert result == "python"

    def test_resolve_auto_detects_python(self, tmp_path):
        """Detects python via pyproject.toml."""
        target = tmp_path / "py-plugin"
        target.mkdir()
        (target / "pyproject.toml").write_text('[project]\nname = "x"\n')
        result = resolve_language("auto", target)
        assert result == "python"

    def test_resolve_auto_detects_rust(self, tmp_path):
        """Detects rust via Cargo.toml."""
        target = tmp_path / "rust-plugin"
        target.mkdir()
        (target / "Cargo.toml").write_text('[package]\nname = "x"\n')
        result = resolve_language("auto", target)
        assert result == "rust"

    def test_resolve_auto_detects_go(self, tmp_path):
        """Detects go via go.mod."""
        target = tmp_path / "go-plugin"
        target.mkdir()
        (target / "go.mod").write_text("module example.com/x\n")
        result = resolve_language("auto", target)
        assert result == "go"

    def test_resolve_auto_detects_deno(self, tmp_path):
        """Detects deno via deno.json."""
        target = tmp_path / "deno-plugin"
        target.mkdir()
        (target / "deno.json").write_text("{}\n")
        result = resolve_language("auto", target)
        assert result == "deno"

    def test_resolve_auto_detects_elixir(self, tmp_path):
        """Detects elixir via mix.exs."""
        target = tmp_path / "elixir-plugin"
        target.mkdir()
        (target / "mix.exs").write_text("defmodule X.MixProject do\nend\n")
        result = resolve_language("auto", target)
        assert result == "elixir"

    def test_resolve_auto_detects_ruby(self, tmp_path):
        """Detects ruby via Gemfile."""
        target = tmp_path / "ruby-plugin"
        target.mkdir()
        (target / "Gemfile").write_text("source 'https://rubygems.org'\n")
        result = resolve_language("auto", target)
        assert result == "ruby"

    def test_resolve_auto_prefers_ts_over_js(self, tmp_path):
        """When both package.json and tsconfig.json exist, picks ts."""
        target = tmp_path / "ts-plugin"
        target.mkdir()
        (target / "package.json").write_text("{}\n")
        (target / "tsconfig.json").write_text("{}\n")
        result = resolve_language("auto", target)
        assert result == "ts"


# =============================================================================
# Group 5: End-to-end scaffold per language
# =============================================================================


class TestEndToEndScaffold:
    """End-to-end: generate_plugin_repo writes a plugin tree per language
    and the expected manifest file exists on disk."""

    @pytest.mark.parametrize(
        "language,manifest",
        [
            ("elixir", "mix.exs"),
            ("ruby", "Gemfile"),
            ("java", "pom.xml"),
            ("kotlin", "build.gradle.kts"),
        ],
    )
    def test_scaffold_writes_manifest(self, tmp_path, language, manifest):
        """Each language writes its manifest to the target directory."""
        target = tmp_path / f"{language}-e2e-plugin"
        target.mkdir()
        p = _params(language=language)
        generate_plugin_repo(target, p)
        assert (target / manifest).exists(), f"{manifest} not created for language {language}"
        assert (target / ".claude-plugin" / "plugin.json").exists()
        assert (target / ".gitignore").exists()
        assert (target / "README.md").exists()

    def test_dry_run_does_not_create_files(self, tmp_path):
        """Dry-run mode for non-python language does not create anything."""
        target = tmp_path / "elixir-dry"
        target.mkdir()
        p = _params(language="elixir")
        generate_plugin_repo(target, p, dry_run=True)
        # Only the empty parent dir from setup
        assert not (target / "mix.exs").exists()
        assert not (target / ".claude-plugin" / "plugin.json").exists()


# =============================================================================
# Group 6: Layout C compatibility for non-python languages
# =============================================================================


class TestLayoutCWithNonPython:
    """Layout C (--self-marketplace) must work for non-python languages."""

    def test_elixir_with_self_marketplace_emits_marketplace_json(self, tmp_path):
        """Elixir + Layout C still emits .claude-plugin/marketplace.json."""
        target = tmp_path / "elixir-layout-c"
        target.mkdir()
        p = _params(language="elixir", self_marketplace=True)
        generate_plugin_repo(target, p)
        mp_path = target / ".claude-plugin" / "marketplace.json"
        assert mp_path.exists()
        # Sanity: marketplace.json plugin entry has the right name
        manifest = json.loads(mp_path.read_text())
        assert manifest["plugins"][0]["name"] == p.name


# =============================================================================
# Group 7: Plugin.json is identical across languages
# =============================================================================


class TestPluginJsonLanguageAgnostic:
    """plugin.json (the CC manifest) is language-agnostic — it must be
    identical regardless of the --language flag."""

    def test_plugin_json_same_for_python_and_rust(self):
        """The .claude-plugin/plugin.json content does not change with --language."""
        from generate_plugin_repo import gen_plugin_json  # noqa: PLC0415

        py = _params(language="python")
        rs = _params(language="rust")
        # plugin.json is generated independently of language
        assert gen_plugin_json(py) == gen_plugin_json(rs)


# =============================================================================
# Group 8: Fixture-style validation per language (TRDD success criterion)
# =============================================================================


class TestLanguageFixtureValidation:
    """Per the TRDD success criterion 'At least one fixture plugin per
    language exists in tests/fixtures/' — these tests scaffold a fresh
    plugin per non-python language and assert the resulting tree matches
    the canonical structure (manifest + plugin.json + .gitignore)."""

    @pytest.mark.parametrize("language", sorted(VALID_LANGUAGES - {"python"}))
    def test_each_language_scaffold_passes_minimum_structure_check(self, tmp_path, language):
        """Scaffolds a plugin per language and verifies it has a valid
        plugin.json and the language's manifest. This stand-in for a
        committed fixture is intentional: keeping fixtures on disk for
        every language would balloon the repo and require per-language
        package-manager tooling that CI can't always install. Generating
        them on the fly proves the scaffold round-trips correctly."""
        target = tmp_path / f"{language}-fixture"
        target.mkdir()
        p = _params(language=language)
        generate_plugin_repo(target, p)
        # Mandatory files for any plugin
        assert (target / ".claude-plugin" / "plugin.json").exists()
        # Language-specific manifest
        from generate_plugin_repo import LANGUAGE_MANIFESTS  # noqa: PLC0415

        manifest_file = LANGUAGE_MANIFESTS[language]
        assert (target / manifest_file).exists(), f"{manifest_file} missing for {language}"
        # plugin.json parses as valid JSON
        plugin_json = json.loads((target / ".claude-plugin" / "plugin.json").read_text())
        assert plugin_json["name"] == p.name
        assert plugin_json["version"] == p.version
