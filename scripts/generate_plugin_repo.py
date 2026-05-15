#!/usr/bin/env python3
"""Generate a complete Claude Code plugin repository scaffold.

Creates all standard files for a plugin repo: manifest, pyproject.toml,
.gitignore, README with badge markers, LICENSE, cliff.toml, CI/CD workflows,
git hooks, publish script, and empty component directories.

Usage:
    uv run scripts/generate_plugin_repo.py <target-dir> \\
      --name <plugin-name> --description <desc> \\
      --author <name> --author-email <email> \\
      --license MIT --python-version 3.12 \\
      --github-owner <owner> --marketplace <mkt-name> \\
      [--dry-run]
"""

import argparse
import json
import os
import re
import shutil
import stat
import sys
from dataclasses import dataclass
from pathlib import Path

# -- ANSI colors (disabled when NO_COLOR is set or stdout is not a tty) ------


def _colors_supported() -> bool:
    """Return True only when the terminal supports ANSI escape sequences.

    Uses sys.platform (rather than os.name) so that pyright's type-narrowing
    can analyze both branches — os.name == "nt" is evaluated as unreachable
    on non-Windows hosts and flagged as "code not analyzed", but sys.platform
    comparisons are understood by static analyzers.
    """
    if os.environ.get("NO_COLOR"):
        return False
    if sys.platform.startswith("win"):
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
        except (AttributeError, OSError):
            pass
        return bool(os.environ.get("WT_SESSION") or os.environ.get("ANSICON"))
    return hasattr(sys.stdout, "isatty") and sys.stdout.isatty()


_USE_COLOR = _colors_supported()

RED = "\033[0;31m" if _USE_COLOR else ""
GREEN = "\033[0;32m" if _USE_COLOR else ""
YELLOW = "\033[1;33m" if _USE_COLOR else ""
BLUE = "\033[0;34m" if _USE_COLOR else ""
BOLD = "\033[1m" if _USE_COLOR else ""
NC = "\033[0m" if _USE_COLOR else ""


# =============================================================================
# DATA CLASS
# =============================================================================


VALID_LANGUAGES = {"python", "js", "ts", "rust", "go", "deno", "elixir", "ruby", "java", "kotlin"}


# TRDD-83ab59e7: per-language manifest files used by `--language auto`
# resolution and per-language scaffolding. Each language MUST appear in
# VALID_LANGUAGES and (for non-python) MUST have a manifest generator
# wired up in `generate_all_files()`. Order matches the TRDD spec table.
LANGUAGE_MANIFESTS: dict[str, str] = {
    "python": "pyproject.toml",
    "js": "package.json",
    "ts": "package.json",
    "rust": "Cargo.toml",
    "go": "go.mod",
    "deno": "deno.json",
    "elixir": "mix.exs",
    "ruby": "Gemfile",
    "java": "pom.xml",
    "kotlin": "build.gradle.kts",
}


def resolve_language(arg: str, target: Path) -> str:
    """Resolve the --language CLI argument to a concrete language string.

    For `--language auto`, calls `detect_languages(target)` and picks the
    first language found in the canonical priority order. If detection
    finds nothing (or `target` doesn't exist), falls back to `python` so
    the original behaviour is preserved.

    Args:
        arg: The raw value of --language (one of VALID_LANGUAGES, or "auto").
        target: The target directory the plugin will be generated into.

    Returns:
        A concrete language string from VALID_LANGUAGES.

    Why a thin wrapper instead of doing this inline in main(): so tests
    can exercise the auto-detection path without mocking argparse, and so
    `standardize_plugin.py` (which audits an EXISTING plugin) can call
    the same resolver to pick the correct language.
    """
    if arg != "auto":
        return arg
    if not target.exists() or not target.is_dir():
        return "python"
    # Local import to avoid a hard cycle: detect_language imports nothing
    # from this module, but keeping the import local also means tools that
    # `import generate_plugin_repo` for a single helper do not pay the
    # detection-module import cost.
    from detect_language import detect_languages  # noqa: PLC0415

    detected = detect_languages(target)
    if not detected:
        return "python"
    # TRDD priority: prefer ts > js when both are present, then walk the
    # rest of LANGUAGE_MANIFESTS in declaration order. This matches the
    # detect_language module's own discriminator (tsconfig.json wins).
    for lang in LANGUAGE_MANIFESTS:
        if lang in detected:
            return lang
    return "python"


# ── Phase 6: single-input slurp helpers ───────────────────────────────────


def _read_md_frontmatter(path: Path) -> dict[str, str]:
    """Return the YAML-ish frontmatter of a .md file as a flat dict.

    Stops at the closing `---` line. Uses a tiny line-by-line parser
    instead of pyyaml so this script stays dependency-free for the
    callers that import it without a venv.
    """
    if not path.is_file():
        return {}
    text = path.read_text(encoding="utf-8", errors="replace")
    if not text.startswith("---\n"):
        return {}
    body = text[4:]
    end = body.find("\n---")
    if end < 0:
        return {}
    fm: dict[str, str] = {}
    for line in body[:end].splitlines():
        if ":" not in line or line.startswith("#"):
            continue
        key, _, value = line.partition(":")
        fm[key.strip()] = value.strip()
    return fm


def _classify_md(path: Path) -> str:
    """Return 'skill' / 'command' / 'agent' for a .md file.

    Heuristic order:
      1. Filename `SKILL.md` → skill (will be placed under skills/<parent>/).
      2. Frontmatter has `allowed-tools:` → command (per CC spec, only
         commands declare allowed-tools).
      3. Default → agent (the catch-all bucket; agents have the richest
         frontmatter surface).
    """
    if path.name == "SKILL.md":
        return "skill"
    fm = _read_md_frontmatter(path)
    if "allowed-tools" in fm:
        return "command"
    return "agent"


_REQUIRED_SKILL_SECTIONS = (
    "## Overview",
    "## When to use",
    "## Instructions",
    "## Prerequisites",
    "## Output",
    "## Error Handling",
    "## Resources",
)


def _audit_slurped_skill(dest_md: Path) -> None:
    """Print actionable WARN lines if a slurped SKILL.md is missing the
    sections CPV's strict validator requires.

    The slurp does NOT modify user content — that would be surprising and
    risky. Instead, it surfaces every missing section so the user can add
    them before publishing. Each missing section becomes one WARN line
    with the exact heading text to paste in.
    """
    try:
        text = dest_md.read_text(encoding="utf-8")
    except OSError:
        return
    missing = [h for h in _REQUIRED_SKILL_SECTIONS if h not in text]
    if not missing:
        return
    print(
        f"  [slurp] {YELLOW}WARN{NC} skill {dest_md.name} is missing "
        f"{len(missing)} required section(s) for CPV strict-mode validation:"
    )
    for heading in missing:
        print(f"           - add a `{heading}` section before publishing")
    fm_match = re.search(r"^---\n(.*?)\n---", text, re.DOTALL | re.MULTILINE)
    fm_block = fm_match.group(1) if fm_match else ""
    if "Trigger with" not in fm_block:
        print(
            f"  [slurp] {YELLOW}WARN{NC} skill {dest_md.name} description "
            f"is missing 'Trigger with ...' phrase (Nixtla strict mode "
            f"requires both 'Use when ...' AND 'Trigger with ...')."
        )


def _slurp_one(target_root: Path, src: Path, kind: str) -> int:
    """Copy `src` into the right component folder of `target_root`.

    `kind` is one of: 'skill', 'agent', 'command', 'mcp', 'scripts'.
    Returns the number of files copied.
    """
    n = 0
    if kind == "skill":
        # If src is a directory containing SKILL.md, copy whole tree.
        # If src is a SKILL.md file, copy under skills/<parent-dir-name>/.
        if src.is_dir() and (src / "SKILL.md").is_file():
            dest = target_root / "skills" / src.name
            dest.mkdir(parents=True, exist_ok=True)
            for f in src.rglob("*"):
                if f.is_file():
                    rel = f.relative_to(src)
                    dest_f = dest / rel
                    dest_f.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(f, dest_f)
                    n += 1
            print(f"  [slurp] skill {src} → {dest.relative_to(target_root)}/ ({n} files)")
            _audit_slurped_skill(dest / "SKILL.md")
        elif src.is_file() and src.name == "SKILL.md":
            # Use parent dir name OR the skill's `name:` frontmatter.
            fm = _read_md_frontmatter(src)
            skill_name = fm.get("name") or src.parent.name or "imported-skill"
            dest_dir = target_root / "skills" / skill_name
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest_md = dest_dir / "SKILL.md"
            shutil.copy2(src, dest_md)
            n = 1
            print(f"  [slurp] skill {src} → skills/{skill_name}/SKILL.md")
            _audit_slurped_skill(dest_md)
        else:
            print(f"  [slurp] {YELLOW}WARN{NC} --skill {src}: not a SKILL.md or skill dir; skipped")
        return n

    if kind in ("agent", "command"):
        if not src.is_file() or src.suffix != ".md":
            print(f"  [slurp] {YELLOW}WARN{NC} --{kind} {src}: not a .md file; skipped")
            return 0
        sub = "agents" if kind == "agent" else "commands"
        dest_dir = target_root / sub
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest_f = dest_dir / src.name
        shutil.copy2(src, dest_f)
        print(f"  [slurp] {kind} {src} → {sub}/{src.name}")
        return 1

    if kind == "mcp":
        # Either a directory containing .mcp.json OR the .mcp.json itself.
        if src.is_dir():
            mcp = src / ".mcp.json"
            if not mcp.is_file():
                print(f"  [slurp] {YELLOW}WARN{NC} --mcp-server {src}: no .mcp.json found; skipped")
                return 0
            shutil.copy2(mcp, target_root / ".mcp.json")
            n += 1
            # Also copy any sibling files referenced by .mcp.json (best-effort).
            for f in src.rglob("*"):
                if f.is_file() and f.name != ".mcp.json":
                    rel = f.relative_to(src)
                    dest_f = target_root / "mcp-server" / rel
                    dest_f.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(f, dest_f)
                    n += 1
            print(f"  [slurp] mcp {src} → .mcp.json + {n - 1} sidecar files")
        elif src.is_file() and src.name == ".mcp.json":
            shutil.copy2(src, target_root / ".mcp.json")
            n = 1
            print(f"  [slurp] mcp {src} → .mcp.json")
        else:
            print(f"  [slurp] {YELLOW}WARN{NC} --mcp-server {src}: not a .mcp.json file or dir; skipped")
        return n

    if kind == "scripts":
        if not src.is_dir():
            print(f"  [slurp] {YELLOW}WARN{NC} --scripts {src}: not a directory; skipped")
            return 0
        dest_dir = target_root / "scripts"
        dest_dir.mkdir(parents=True, exist_ok=True)
        for f in src.iterdir():
            if f.is_file():
                shutil.copy2(f, dest_dir / f.name)
                n += 1
        print(f"  [slurp] scripts {src} → scripts/ ({n} files)")
        return n

    return 0


def _do_slurp(
    target_root: Path,
    *,
    from_paths: list[Path],
    skill_paths: list[Path],
    agent_paths: list[Path],
    command_paths: list[Path],
    mcp_paths: list[Path],
    scripts_paths: list[Path],
) -> int:
    """Apply all --from / --skill / --agent / --command / --mcp-server /
    --scripts flags to `target_root`. Returns total files copied.

    --from PATH is auto-classified via _classify_md (for .md files) or
    inferred from the filename (.mcp.json → mcp; directory of scripts
    → scripts; SKILL.md → skill).
    """
    total = 0
    for p in from_paths:
        if not p.exists():
            print(f"  [slurp] {YELLOW}WARN{NC} --from {p}: does not exist; skipped")
            continue
        if p.is_file() and p.name == ".mcp.json":
            kind = "mcp"
        elif p.is_file() and p.suffix == ".md":
            kind = _classify_md(p)
        elif p.is_dir() and (p / "SKILL.md").is_file():
            kind = "skill"
        elif p.is_dir() and (p / ".mcp.json").is_file():
            kind = "mcp"
        elif p.is_dir():
            kind = "scripts"
        else:
            print(f"  [slurp] {YELLOW}WARN{NC} --from {p}: cannot classify; skipped")
            continue
        total += _slurp_one(target_root, p, kind)

    for p in skill_paths:
        total += _slurp_one(target_root, p, "skill")
    for p in agent_paths:
        total += _slurp_one(target_root, p, "agent")
    for p in command_paths:
        total += _slurp_one(target_root, p, "command")
    for p in mcp_paths:
        total += _slurp_one(target_root, p, "mcp")
    for p in scripts_paths:
        total += _slurp_one(target_root, p, "scripts")
    return total


@dataclass
class PluginParams:
    """All parameters needed to scaffold a plugin repository."""

    name: str
    description: str
    author: str
    author_email: str
    license: str = "MIT"
    python_version: str = "3.12"
    github_owner: str = ""
    marketplace: str = ""
    version: str = "0.1.0"
    language: str = "python"  # One of VALID_LANGUAGES
    self_marketplace: bool = False  # Layout C: emit .claude-plugin/marketplace.json with self-entry
    strip_dev: bool = True  # TRDD-793ac32a: emit cpv.strip block in plugin.json (default ON)
    # Per-plugin marketplace OWNER override — set by the migration path
    # when the existing notify-marketplace.yml targets a different owner
    # than the plugin itself (e.g. plugin at Emasoft/* lives in a different
    # marketplace org). Empty falls back to github_owner.
    #
    # NOTE (v2.86.0): the secret NAME is NOT per-plugin. CPV enforces the
    # canonical name `MARKETPLACE_PAT` everywhere; deviations are flagged
    # via an [ACTION REQUIRED] migration warning, not preserved. See
    # TRDD-canonical-pipeline-hardening for the rationale.
    marketplace_owner: str = ""  # Owner segment for MARKETPLACE_OWNER (when ≠ plugin owner)

    @property
    def repo_name(self) -> str:
        """GitHub repo name — defaults to plugin name."""
        return self.name

    @property
    def github_url(self) -> str:
        """Full GitHub URL for the plugin."""
        return f"https://github.com/{self.github_owner}/{self.repo_name}"


# =============================================================================
# LANGUAGE-SPECIFIC MANIFEST GENERATORS
# =============================================================================


def gen_package_json(p: PluginParams) -> str:
    """Generate package.json for JS/TS plugins."""
    dev_deps: dict[str, str] = {"eslint": "^9.0.0"}
    if p.language == "ts":
        dev_deps["typescript"] = "^5.0.0"
    manifest: dict[str, object] = {
        "name": p.name,
        "version": p.version,
        "description": p.description,
        "author": f"{p.author} <{p.author_email}>",
        "license": p.license,
        "type": "module",
        "scripts": {
            "lint": "eslint scripts/" if p.language == "js" else "eslint scripts/ && tsc --noEmit",
            "test": "vitest run",
        },
        "devDependencies": dev_deps,
    }
    if p.github_owner:
        manifest["homepage"] = p.github_url
        manifest["repository"] = {"type": "git", "url": f"{p.github_url}.git"}
    return json.dumps(manifest, indent=2) + "\n"


def gen_tsconfig_json() -> str:
    """Generate tsconfig.json for TypeScript plugins."""
    return (
        json.dumps(
            {
                "compilerOptions": {
                    "target": "ES2022",
                    "module": "ESNext",
                    "moduleResolution": "bundler",
                    "strict": True,
                    "esModuleInterop": True,
                    "skipLibCheck": True,
                    "noEmit": True,
                },
                "include": ["scripts/**/*.ts"],
            },
            indent=2,
        )
        + "\n"
    )


def gen_cargo_toml(p: PluginParams) -> str:
    """Generate Cargo.toml for Rust plugins."""
    return f"""[package]
name = "{p.name}"
version = "{p.version}"
edition = "2021"
authors = ["{p.author} <{p.author_email}>"]
description = "{p.description}"
license = "{p.license}"

[dependencies]
"""


def gen_go_mod(p: PluginParams) -> str:
    """Generate go.mod for Go plugins."""
    module = f"github.com/{p.github_owner}/{p.repo_name}" if p.github_owner else p.name
    return f"""module {module}

go 1.22
"""


def gen_deno_json(p: PluginParams) -> str:
    """Generate deno.json for Deno plugins."""
    return (
        json.dumps(
            {
                "name": f"@{p.github_owner or 'local'}/{p.name}",
                "version": p.version,
                "exports": "./scripts/mod.ts",
                "tasks": {
                    "lint": "deno lint scripts/",
                    "test": "deno test",
                    "fmt": "deno fmt scripts/",
                },
            },
            indent=2,
        )
        + "\n"
    )


def _module_name_from_plugin(name: str) -> str:
    """Convert a kebab-case plugin name to a CamelCase Elixir/Java module.

    `my-test-plugin` -> `MyTestPlugin`. Used as the namespace for Elixir
    `defmodule` and Java/Kotlin package fragments. Idempotent on names
    that are already CamelCase.
    """
    parts = [p for p in name.replace("_", "-").split("-") if p]
    return "".join(p[:1].upper() + p[1:] for p in parts) or "Plugin"


def _atom_name_from_plugin(name: str) -> str:
    """Convert a kebab-case plugin name to an Elixir atom (snake_case).

    `my-test-plugin` -> `my_test_plugin`. Elixir atoms use snake_case for
    project app names. Idempotent on already-snake names.
    """
    return name.replace("-", "_").lower() or "plugin"


def gen_mix_exs(p: PluginParams) -> str:
    """Generate mix.exs for Elixir plugins.

    Produces a minimal valid Mix project with :credo (lint) and :ex_unit
    (built-in test framework, no extra dep) wired up. The defmodule name
    is CamelCase from the plugin name; the :app atom is snake_case.

    Why not embed test_paths or coverage config: keep the scaffold under
    50 lines so plugin authors can read it top-to-bottom and customise
    without un-learning Elixir defaults.
    """
    module = _module_name_from_plugin(p.name)
    atom = _atom_name_from_plugin(p.name)
    return f"""defmodule {module}.MixProject do
  use Mix.Project

  def project do
    [
      app: :{atom},
      version: "{p.version}",
      elixir: "~> 1.16",
      description: "{p.description}",
      deps: deps(),
      package: package(),
      preferred_cli_env: [credo: :dev, test: :test]
    ]
  end

  def application do
    [extra_applications: [:logger]]
  end

  defp deps do
    [
      {{:credo, "~> 1.7", only: [:dev, :test], runtime: false}}
    ]
  end

  defp package do
    [
      maintainers: ["{p.author}"],
      licenses: ["{p.license}"],
      links: %{{}}
    ]
  end
end
"""


def gen_gemfile(p: PluginParams) -> str:
    """Generate Gemfile for Ruby plugins.

    Pins `rubocop` (lint) and `rspec` (test) under the right Bundler
    groups so `bundle install --without development` works in CI without
    pulling in dev tools by accident.
    """
    return f"""# frozen_string_literal: true
# Gemfile for {p.name} ({p.version}) — {p.description}
source 'https://rubygems.org'

group :development do
  gem 'rubocop', '~> 1.60'
end

group :test do
  gem 'rspec', '~> 3.13'
end
"""


def gen_pom_xml(p: PluginParams) -> str:
    """Generate pom.xml for Java plugins (Maven layout).

    Targets Java 17 (Temurin LTS, GitHub Actions default) and wires
    junit-jupiter as the test framework. checkstyle is the lint pick
    declared in the TRDD; we expose it as a Maven plugin entry rather
    than a dep so `mvn checkstyle:check` works without extra config.
    """
    group_id = f"io.github.{p.github_owner}".replace("-", "_") if p.github_owner else "com.example"
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0"
         xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
         xsi:schemaLocation="http://maven.apache.org/POM/4.0.0
                             http://maven.apache.org/xsd/maven-4.0.0.xsd">
  <modelVersion>4.0.0</modelVersion>

  <groupId>{group_id}</groupId>
  <artifactId>{p.name}</artifactId>
  <version>{p.version}</version>
  <packaging>jar</packaging>

  <name>{p.name}</name>
  <description>{p.description}</description>

  <properties>
    <maven.compiler.source>17</maven.compiler.source>
    <maven.compiler.target>17</maven.compiler.target>
    <project.build.sourceEncoding>UTF-8</project.build.sourceEncoding>
  </properties>

  <dependencies>
    <dependency>
      <groupId>org.junit.jupiter</groupId>
      <artifactId>junit-jupiter</artifactId>
      <version>5.10.2</version>
      <scope>test</scope>
    </dependency>
  </dependencies>

  <build>
    <plugins>
      <plugin>
        <groupId>org.apache.maven.plugins</groupId>
        <artifactId>maven-checkstyle-plugin</artifactId>
        <version>3.3.1</version>
      </plugin>
      <plugin>
        <groupId>org.apache.maven.plugins</groupId>
        <artifactId>maven-surefire-plugin</artifactId>
        <version>3.2.5</version>
      </plugin>
    </plugins>
  </build>
</project>
"""


def gen_build_gradle_kts(p: PluginParams) -> str:
    """Generate build.gradle.kts for Kotlin plugins (Gradle Kotlin DSL).

    Pulls in the JVM Kotlin plugin and wires detekt for lint + JUnit5
    for tests. The Kotlin version is pinned to a recent stable so the
    initial scaffold builds on a fresh JDK 17 without surprise breakage.
    """
    group = f"io.github.{p.github_owner}".replace("-", "_") if p.github_owner else "com.example"
    return f"""// build.gradle.kts for {p.name} ({p.version})
// {p.description}

plugins {{
    kotlin("jvm") version "1.9.23"
    id("io.gitlab.arturbosch.detekt") version "1.23.6"
}}

group = "{group}"
version = "{p.version}"

repositories {{
    mavenCentral()
}}

dependencies {{
    testImplementation(kotlin("test"))
    testImplementation("org.junit.jupiter:junit-jupiter:5.10.2")
}}

tasks.test {{
    useJUnitPlatform()
}}

detekt {{
    buildUponDefaultConfig = true
}}
"""


# =============================================================================
# TEMPLATE GENERATORS
# =============================================================================


def gen_plugin_json(p: PluginParams) -> str:
    """Generate .claude-plugin/plugin.json manifest content."""
    manifest = {
        "name": p.name,
        "version": p.version,
        "description": p.description,
        "author": {
            "name": p.author,
            "email": p.author_email,
        },
        "license": p.license,
        "keywords": [],
    }
    # Only include homepage/repository when github_owner is set (avoids double-slash URLs)
    if p.github_owner:
        manifest["homepage"] = p.github_url
        manifest["repository"] = p.github_url
    # TRDD-793ac32a: emit cpv.strip block when --strip-dev (default).
    # Lets the user run `cpv strip-dev-parts` later without editing
    # plugin.json — the block is the configuration the engine reads.
    if p.strip_dev:
        # PSS-style: ONE submodule per plugin (not three). Default extracts
        # tests/ only — typically the heaviest dev folder. design/ and
        # git-hooks/ are tiny (<300 KB combined) and stay in the main repo.
        # Plugin authors can add more extract entries by hand if their
        # plugin has additional heavy dev folders worth stripping.
        owner = p.github_owner or "<owner>"
        manifest["cpv"] = {
            "strip": {
                "extract": [
                    {
                        "src": "tests/",
                        "submodule": f"{owner}/{p.repo_name}-tests",
                        "submodule_path": "tests/",
                        # PSS pattern: submodule mounts at the same path
                        # as the original folder, so all references in
                        # CI / scripts / README continue to work unchanged.
                    },
                ],
                "require_url_allowlist": True,
            }
        }
    return json.dumps(manifest, indent=2) + "\n"


def gen_self_marketplace_json(p: PluginParams) -> str:
    """Generate .claude-plugin/marketplace.json with a single self-entry (Layout C)."""
    self_entry: dict[str, object] = {
        "name": p.name,
        "source": "./",
        "version": p.version,
        "description": p.description,
        "author": {
            "name": p.author,
            "email": p.author_email,
        },
        "license": p.license,
    }
    if p.github_owner:
        self_entry["homepage"] = p.github_url
        self_entry["repository"] = p.github_url
    manifest: dict[str, object] = {
        "name": p.name,
        "owner": {
            "name": p.author,
            "email": p.author_email,
        },
        "metadata": {
            "version": p.version,
            "description": p.description,
        },
        "plugins": [self_entry],
    }
    return json.dumps(manifest, indent=2) + "\n"


def gen_pyproject_toml(p: PluginParams) -> str:
    """Generate pyproject.toml with hatchling build system and ruff config."""
    return f"""[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["scripts"]

[project]
name = "{p.name}"
version = "{p.version}"
description = "{p.description}"
readme = "README.md"
requires-python = ">={p.python_version}"
dependencies = []

[project.optional-dependencies]
dev = [
    "mypy>=1.19.1",
    "pyyaml>=6.0",
    "pytest>=8.0.0",
    "pytest-cov>=4.1.0",
    "ruff>=0.14.14",
]

[tool.ruff]
line-length = 120

[tool.ruff.lint]
select = ["E", "F", "W", "I"]
ignore = ["E501"]

[tool.ruff.lint.per-file-ignores]
"tests/*.py" = ["E402"]

[tool.mypy]
python_version = "{p.python_version}"
warn_return_any = true
warn_unused_configs = true

[tool.pyright]
pythonVersion = "{p.python_version}"
extraPaths = ["scripts", "tests"]
reportMissingImports = "warning"
typeCheckingMode = "basic"
"""


def gen_python_version(p: PluginParams) -> str:
    """Generate .python-version file."""
    return f"{p.python_version}\n"


def gen_gitignore(p: PluginParams) -> str:
    """Generate comprehensive .gitignore for a Claude Code plugin repo."""
    _ = p  # unused but kept for consistent signature
    return """# Python
__pycache__/
*.py[cod]
*$py.class
*.so
.Python
*.egg-info/
.eggs/
dist/
build/
.coverage
.venv/
venv/
.pytest_cache/

# Type checking
.mypy_cache/
.dmypy.json
dmypy.json

# Linting
.ruff_cache/

# IDE
.idea/
.vscode/
*.swp
*.swo

# OS
.DS_Store
Thumbs.db

# Environment
.env
.env.*

# Dev folders (NEVER PUBLISH - development artifacts only)
# Wildcard pattern catches all: docs_dev, scripts_dev, tests_dev, samples_dev,
# examples_dev, downloads_dev, libs_dev, builds_dev, etc.
*_dev/

# Agent/script reports — ALWAYS gitignored since they often contain private data
# (full paths, source snippets, API output, validation results, env metadata).
# Canonical rule: every agent/skill/script that saves a report MUST write
# under the main-repo `./reports/<component>/<YYYYMMDD_HHMMSS±HHMM>-<slug>.md`.
# Neither folder may ever be tracked. `reports_dev/` is also covered by the
# `*_dev/` rule above, listed explicitly because both entries must be present.
reports/
reports_dev/

# Node
node_modules/

# Claude Code
.claude/
llm_externalizer_output/
.tldr/

# Mega-Linter
megalinter-reports/
mega-linter.log

# Rust (remove Cargo.lock line for binary plugins)
target/
Cargo.lock
"""


def gen_readme(p: PluginParams) -> str:
    """Generate README.md with badges, installation, usage, and development sections."""
    owner = p.github_owner
    repo = p.repo_name
    # Skip badge URLs if github_owner is empty (avoids broken // in URLs)
    if owner:
        badges = (
            f"[![CI](https://github.com/{owner}/{repo}/actions/workflows/ci.yml/badge.svg)]"
            f"(https://github.com/{owner}/{repo}/actions/workflows/ci.yml)\n"
            f"[![Version](https://img.shields.io/badge/version-{p.version}-blue)]"
            f"(https://github.com/{owner}/{repo})\n"
            f"[![License](https://img.shields.io/badge/license-{p.license}-green)](LICENSE)"
        )
    else:
        badges = "<!-- Badges will appear here once github_owner is set -->"
    # Build GitHub-specific sections only when github_owner is set (avoids broken URLs)
    if owner:
        from_github = f"""### From GitHub

```bash
gh repo clone {owner}/{repo}
cd {repo}
uv venv --python {p.python_version}
source .venv/bin/activate
uv pip install -e .
```

### As a Claude Code Plugin

Add to your Claude Code configuration:

```json
{{
  "plugins": [
    "https://github.com/{owner}/{repo}"
  ]
}}
```"""
        marketplace_section = f"""## Marketplace

This plugin is available on the [{p.marketplace} marketplace](https://github.com/{owner}/{p.marketplace})."""
        author_section = f"""## Author

**{p.author}** - [GitHub](https://github.com/{owner})"""
    else:
        from_github = ""
        marketplace_section = (
            f"""## Marketplace

This plugin is available on the {p.marketplace} marketplace."""
            if p.marketplace
            else ""
        )
        author_section = f"""## Author

**{p.author}**"""

    return f"""# {p.name}

<!--BADGES-START-->
{badges}
<!--BADGES-END-->

{p.description}

## Installation

### From Marketplace

```bash
# 1. Add the marketplace (first time only)
claude plugin marketplace add {owner}/{p.marketplace if p.marketplace else repo}

# 2. Install the plugin
claude plugin install {p.name}@{p.marketplace if p.marketplace else repo}

# 3. Restart Claude Code (or run /reload-plugins) to activate
```

{from_github}

## Uninstall

```bash
claude plugin uninstall {p.name}
```

## Update

```bash
claude plugin update {p.name}@{p.marketplace if p.marketplace else repo}
```

## Troubleshooting

| Issue | Resolution |
|-------|------------|
| Plugin not appearing after install | Restart Claude Code or run `/reload-plugins` |
| Old version still showing after update | Restart Claude Code; if still stale, run `claude plugin update {p.name}` again |
| Hook path not found after update | Re-run `uv run python scripts/publish.py --install-hook` |
| `marketplace not found` error | Run `claude plugin marketplace update {p.marketplace if p.marketplace else repo}` to refresh |
| Permission denied on script | Ensure scripts are executable: `chmod +x scripts/*.py` |
| Import errors after install | Re-run `uv pip install -e .` to refresh the venv |
| Session won't pick up new hooks | Restart required — `/reload-plugins` does NOT re-read project-scoped settings.json hooks |

## Usage

```bash
# Run the plugin
uv run python scripts/main.py --help
```

## Development

### Prerequisites

- Python >= {p.python_version}
- [uv](https://docs.astral.sh/uv/) package manager

### Setup

```bash
uv venv --python {p.python_version}
source .venv/bin/activate
uv pip install -e ".[dev]"
```

### Testing

```bash
uv run pytest tests/ -v
```

### Linting & Formatting

```bash
uv run ruff check scripts/ tests/
uv run ruff format scripts/ tests/
uv run mypy scripts/
```

## Project Structure

```
{repo}/
├── .claude-plugin/
│   └── plugin.json          # Plugin manifest
├── .github/
│   └── workflows/           # CI/CD workflows
├── git-hooks/               # Git hooks (pre-push)
├── scripts/                 # Plugin source code
├── tests/                   # Test suite
├── pyproject.toml           # Project configuration
├── cliff.toml               # Changelog generation config
├── README.md                # This file
├── LICENSE                  # License file
└── .gitignore               # Git ignore rules
```

{marketplace_section}

## License

This project is licensed under the {p.license} License. See [LICENSE](LICENSE) for details.

{author_section}
"""


def gen_license_mit(p: PluginParams) -> str:
    """Generate MIT license text."""
    return f"""MIT License

Copyright (c) 2025 {p.author}

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""


def gen_cliff_toml(p: PluginParams) -> str:
    """Generate cliff.toml for git-cliff changelog generation."""
    # TOML uses triple-double-quotes (""") for multi-line strings, which collides
    # with Python triple-quoted strings. We inject them via a variable.
    tq = '"""\n'
    # v2.86.0 hardening (issue #22):
    # * Em-dash separator ``— `` instead of `` - `` between version and date.
    #   Matches the typographic style of the rest of CPV's docs and is the
    #   form release.yml's section-extraction awk script looks for.
    # * Drop the scope prefix from rendered commits — the per-group header
    #   already announces the kind (Features, Bug Fixes, …), so repeating
    #   the scope on every commit line is redundant noise.
    # * Drop ``striptags`` from the group renderer — conventional-commit
    #   group names never contain HTML and the filter just adds template
    #   surface for nothing.
    body_template = (
        "{% if version %}\\\n"
        '    ## [{{ version | trim_start_matches(pat="v") }}]'
        ' — {{ timestamp | date(format="%Y-%m-%d") }}\n'
        "{% else %}\\\n"
        "    ## [Unreleased]\n"
        "{% endif %}\\\n"
        "{% for group, commits in commits | group_by(attribute="
        '"group") %}\n'
        "    ### {{ group | upper_first }}\n"
        "    {% for commit in commits %}\n"
        "        - {{ commit.message | upper_first }}\\\n"
        "    {% endfor %}\n"
        "{% endfor %}\n"
    )
    lines = [
        "# git-cliff configuration for changelog generation",
        "# https://git-cliff.org",
        "",
        "[changelog]",
    ]
    # Build the TOML content as a list of lines, then join
    # We handle the triple-quoted TOML strings by direct string building
    result = "\n".join(lines) + "\n"
    result += "header = " + tq
    result += "# Changelog\n\nAll notable changes to this project will be documented in this file.\n\n"
    result += tq
    result += "body = " + tq
    result += body_template
    result += tq
    result += "footer = " + tq
    result += "---\n*Generated by [git-cliff](https://git-cliff.org)*\n"
    result += tq
    result += "trim = true\n"
    result += "postprocessors = []\n"
    result += "\n"
    result += "[git]\n"
    result += "conventional_commits = true\n"
    result += "filter_unconventional = true\n"
    result += "split_commits = false\n"
    result += "commit_preprocessors = [\n"
    result += r"  { pattern = '\((\w+\s)?#([0-9]+)\)',"
    result += f' replace = "([#${{2}}](https://github.com/{p.github_owner}/{p.name}/issues/${{2}}))" }},\n'
    result += r"  { pattern = '\s+$', replace = " + '"" },\n'
    result += "]\n"
    result += "commit_parsers = [\n"
    result += '  { message = "^feat", group = "Features" },\n'
    result += '  { message = "^fix", group = "Bug Fixes" },\n'
    result += '  { message = "^doc", group = "Documentation" },\n'
    result += '  { message = "^perf", group = "Performance" },\n'
    result += '  { message = "^refactor", group = "Refactor" },\n'
    result += '  { message = "^style", group = "Styling" },\n'
    result += '  { message = "^test", group = "Testing" },\n'
    result += '  { message = "^chore\\\\(release\\\\)", skip = true },\n'
    result += '  { message = "^chore\\\\(deps\\\\)", skip = true },\n'
    result += '  { message = "^chore\\\\(pr\\\\)", skip = true },\n'
    result += '  { message = "^chore\\\\(pull\\\\)", skip = true },\n'
    result += '  { message = "^chore|^ci", group = "Miscellaneous Tasks" },\n'
    result += '  { body = ".*security", group = "Security" },\n'
    result += '  { message = "^revert", group = "Revert" },\n'
    result += "]\n"
    result += "protect_breaking_commits = false\n"
    result += "filter_commits = false\n"
    result += 'tag_pattern = "v[0-9].*"\n'
    result += 'skip_tags = ""\n'
    result += 'ignore_tags = ""\n'
    result += "topo_order = false\n"
    result += 'sort_commits = "oldest"\n'
    return result


def gen_publish_py(p: PluginParams) -> str:
    """Generate scripts/publish.py — unified publish pipeline with --gate mode."""
    _ = p  # unused but kept for consistent signature
    return r'''#!/usr/bin/env python3
"""Unified publish pipeline: bypass-guard -> lint -> validate (remote CPV) -> test -> bump -> badge -> changelog -> commit -> push -> release.

Modes:
  --gate                  Pre-push gate: orchestrator check + lint + validate + tests
                          only (no bump/push). Called by git-hooks/pre-push automatically.
  --install-hook          Install git-hooks/pre-push into .git/hooks/ and set core.hooksPath.
  --install-branch-rules  Apply the cpv-branch-rules GitHub ruleset to the origin
                          (server-side CI enforcement — run once after first push).
  (no flag)               Full release pipeline (11 stages, fail-fast). The bump type
                          is AUTO-DETECTED via `git-cliff --bumped-version` from the
                          conventional commits on HEAD.
  --patch/--minor/--major Force a specific bump type (overrides auto-detection).

Pipeline stages (all fail-fast — any non-zero exit aborts):
   0. Bypass guard — reject CPV_SKIP_*, SKIP_*, NO_VERIFY env vars
   1. Check working tree is clean
   2. Lint files (ruff)
   3. Validate plugin (uvx cpv-remote-validate plugin . --strict — fetches
      the canonical CPV validator from GitHub so this plugin never vendors
      a local copy and never drifts from upstream rules)
   4. Run tests (pytest)
   5. Marketplace-registration check (Layout A: notify workflow + PAT secret +
      remote marketplace.json registration + remote receiver workflow;
      Layout B: must run from marketplace root + nested plugin must be listed)
   6. Check version consistency across all sources
   7. Bump version in plugin.json, pyproject.toml, and __version__ vars
   8. Update README version badge
   9. Generate changelog (git-cliff)
  10. Commit, tag, push
  11. Create GitHub release (gh CLI)

Gate stages (--gate mode, called by pre-push hook):
   G0. Orchestrator check — direct `git push` is blocked; only publish.py
       may initiate a push (verified via process ancestry, NOT env vars).
   G1. Version bump check (local vs remote, auto-detects origin/HEAD)
   G2. Lint (ruff)
   G3. Validate (uvx cpv-remote-validate plugin . --strict)
   G4. Tests (pytest)

Usage:
    uv run python scripts/publish.py                      # auto-bump from git-cliff
    uv run python scripts/publish.py --gate
    uv run python scripts/publish.py --install-hook
    uv run python scripts/publish.py --install-branch-rules
    uv run python scripts/publish.py --patch              # force patch
    uv run python scripts/publish.py --minor              # force minor
    uv run python scripts/publish.py --major              # force major
    uv run python scripts/publish.py --dry-run            # preview (auto-bump)

Cornerstone rule: a plugin CANNOT be pushed unless validation passes with
0 issues (WARNING allowed). There are no exceptions and no bypass flags.
Every push is blocked unless scripts/publish.py orchestrates it end-to-end
AND stage_validate / stage_tests / stage_lint all succeed.
"""

import argparse
import json
import os
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path

# Load gh / git retry wrappers from the sibling module so every push +
# `gh release create` survives transient github.com hiccups (the retry
# pattern from ~/.claude/rules/github-timeouts.md). Shipped verbatim
# from the canonical CPV install via gen_cpv_network_resilience_py().
sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from cpv_network_resilience import gh_with_retry, git_with_retry
except ImportError:
    # Fallback: scripts/cpv_network_resilience.py was not shipped with this
    # plugin (older scaffold). Define no-op shims so publish.py still works,
    # but warn so the user knows to refresh via `cpv standardize --force-templates`.
    print(
        "[publish.py] WARNING: scripts/cpv_network_resilience.py missing — "
        "network calls will not auto-retry on transient errors. "
        "Run `cpv standardize --force-templates` to refresh.",
        file=sys.stderr,
    )
    def gh_with_retry(cmd, **kwargs):  # type: ignore[no-redef]
        kwargs.pop("max_attempts", None)
        kwargs.pop("backoff", None)
        kwargs.setdefault("check", True)
        kwargs.setdefault("capture_output", False)
        return subprocess.run(cmd, **kwargs)
    def git_with_retry(cmd, **kwargs):  # type: ignore[no-redef]
        kwargs.pop("max_attempts", None)
        kwargs.pop("backoff", None)
        kwargs.setdefault("check", True)
        kwargs.setdefault("capture_output", False)
        return subprocess.run(cmd, **kwargs)

# -- ANSI colors ---------------------------------------------------------------


def _colors_ok() -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    return hasattr(sys.stdout, "isatty") and sys.stdout.isatty()


_C = _colors_ok()
RED    = "\033[0;31m" if _C else ""
GREEN  = "\033[0;32m" if _C else ""
YELLOW = "\033[1;33m" if _C else ""
BLUE   = "\033[0;34m" if _C else ""
BOLD   = "\033[1m" if _C else ""
NC     = "\033[0m" if _C else ""


# -- Helpers -------------------------------------------------------------------


def cprint(msg: str) -> None:
    print(msg, flush=True)

def run(
    cmd: list[str], cwd: Path | None = None, *, check: bool = True, capture: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Run a command, stream output, fail-fast on error."""
    cprint(f"  {BLUE}$ {' '.join(cmd)}{NC}")
    result = subprocess.run(cmd, cwd=str(cwd) if cwd else None, text=True,
                            capture_output=capture, timeout=300)
    if check and result.returncode != 0:
        cprint(f"  {RED}Command failed (exit {result.returncode}){NC}")
        sys.exit(result.returncode)
    return result

def get_repo_root() -> Path:
    r = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                       capture_output=True, text=True, check=True)
    return Path(r.stdout.strip())


# -- gh-auth precheck (TRDD-bbff5bc5) ---------------------------------------


def _parse_owner_repo_from_remote(remote_url: str) -> tuple[str, str] | None:
    """Extract (owner, repo) from `git@host:owner/repo.git` or
    `https://host/owner/repo[.git]`. Returns None on unparseable input.
    """
    if not remote_url:
        return None
    url = remote_url.strip().rstrip("/")
    if url.endswith(".git"):
        url = url[:-4]
    match = re.search(r"[:/]([^:/\s]+)/([^/\s]+)$", url)
    if not match:
        return None
    return match.group(1), match.group(2)


def _resolve_owner_repo(plugin_root: Path) -> tuple[str, str]:
    """Read remote.origin.url, parse (owner, repo). Exit 1 on failure."""
    result = subprocess.run(
        ["git", "config", "--get", "remote.origin.url"],
        cwd=str(plugin_root), capture_output=True, text=True, timeout=10, check=False,
    )
    if result.returncode != 0 or not result.stdout.strip():
        cprint(f"  {RED}Could not read remote.origin.url. Run: git remote add origin <url>{NC}")
        sys.exit(1)
    parsed = _parse_owner_repo_from_remote(result.stdout.strip())
    if parsed is None:
        cprint(f"  {RED}Could not parse owner/repo from remote URL: {result.stdout.strip()!r}{NC}")
        sys.exit(1)
    return parsed


def _ensure_gh_auth(owner: str, repo: str) -> None:
    """Verify gh CLI installed + authenticated + push perm on owner/repo.

    Called BEFORE every push gate. Exits 1 on any of: gh missing, not
    authed, no push permission. Per TRDD-bbff5bc5 §4.1: never invokes
    `gh auth token`; uses only `gh auth status` and `gh api` so PAT-shaped
    strings cannot leak to stdout/stderr.
    """
    if os.environ.get("CPV_SKIP_GH_AUTH_CHECK") == "1":
        return
    gh_bin = shutil.which("gh")
    if gh_bin is None:
        cprint(f"  {RED}gh CLI not installed. Install: brew install gh{NC}")
        sys.exit(1)
    # 60s timeout (was 15s) — slow-link tolerance; downstream push gates
    # still enforce real auth on failure.
    try:
        status = subprocess.run(
            [gh_bin, "auth", "status"],
            capture_output=True, text=True, timeout=60, check=False,
        )
    except subprocess.TimeoutExpired:
        cprint(f"  {RED}gh auth status timed out after 60 s — flaky network. Retry, or set CPV_SKIP_GH_AUTH_CHECK=1.{NC}")
        sys.exit(1)
    if status.returncode != 0:
        cprint(f"  {RED}gh CLI not authenticated.{NC}")
        cprint(f"  {YELLOW}Run: gh auth login --hostname github.com --git-protocol https{NC}")
        sys.exit(1)
    try:
        perms = subprocess.run(
            [gh_bin, "api", f"repos/{owner}/{repo}", "--jq", ".permissions.push"],
            capture_output=True, text=True, timeout=60, check=False,
        )
    except subprocess.TimeoutExpired:
        cprint(f"  {RED}gh permission check timed out after 60 s — set CPV_SKIP_GH_AUTH_CHECK=1 to bypass this gate.{NC}")
        sys.exit(1)
    if perms.returncode != 0 or perms.stdout.strip() != "true":
        active_login = ""
        for line in (status.stdout + status.stderr).splitlines():
            line = line.strip()
            if "account " in line and ("Logged in" in line or "Active" in line):
                m = re.search(r"account\s+(\S+)", line)
                if m:
                    active_login = m.group(1)
                    break
        login_str = f" '{active_login}'" if active_login else ""
        cprint(f"  {RED}gh user{login_str} has no push permission on {owner}/{repo}.{NC}")
        cprint(f"  {YELLOW}Diagnose:{NC}")
        cprint(f"  {YELLOW}  1. Ask the repo owner to add you as a collaborator with write access.{NC}")
        cprint(f"  {YELLOW}  2. If you have multiple gh accounts: gh auth status; gh auth switch{NC}")
        cprint(f"  {YELLOW}  3. If using a fine-grained token: ensure 'Contents: write' on this repo.{NC}")
        sys.exit(1)


# -- Semver --------------------------------------------------------------------

def parse_semver(version: str) -> tuple[int, int, int] | None:
    """Parse 'X.Y.Z' into (major, minor, patch)."""
    m = re.match(r"^(\d+)\.(\d+)\.(\d+)$", version.strip())
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)), int(m.group(3))

def bump_semver(current: str, bump_type: str) -> str | None:
    """Bump version by major/minor/patch. Returns new version string or None."""
    parsed = parse_semver(current)
    if not parsed:
        return None
    major, minor, patch = parsed
    if bump_type == "major":
        return f"{major + 1}.0.0"
    elif bump_type == "minor":
        return f"{major}.{minor + 1}.0"
    elif bump_type == "patch":
        return f"{major}.{minor}.{patch + 1}"
    return None


# -- Version readers/writers ---------------------------------------------------

def get_current_version(plugin_root: Path) -> str | None:
    """Read version from .claude-plugin/plugin.json."""
    pj = plugin_root / ".claude-plugin" / "plugin.json"
    if not pj.is_file():
        return None
    try:
        data = json.loads(pj.read_text(encoding="utf-8"))
        ver = data.get("version")
        return str(ver) if ver is not None else None
    except (json.JSONDecodeError, OSError):
        return None

def update_plugin_json(root: Path, new_ver: str) -> tuple[bool, str]:
    """Write version to .claude-plugin/plugin.json."""
    pj = root / ".claude-plugin" / "plugin.json"
    if not pj.is_file():
        return False, "plugin.json not found"
    try:
        data = json.loads(pj.read_text(encoding="utf-8"))
        data["version"] = new_ver
        pj.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        return True, f"plugin.json -> {new_ver}"
    except (json.JSONDecodeError, OSError) as e:
        return False, f"plugin.json update failed: {e}"

def update_self_marketplace_json(root: Path, new_ver: str) -> tuple[bool, str]:
    """Write version to .claude-plugin/marketplace.json (Layout C — both metadata and self-entry)."""
    mp = root / ".claude-plugin" / "marketplace.json"
    if not mp.is_file():
        return False, "no marketplace.json (not Layout C)"
    try:
        data = json.loads(mp.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        return False, f"marketplace.json read failed: {e}"
    # Bump metadata.version if present
    metadata = data.get("metadata")
    if isinstance(metadata, dict):
        metadata["version"] = new_ver
    # Bump the self-entry's version (the entry whose name matches plugin.json's name AND source is "./")
    plugin_json_path = root / ".claude-plugin" / "plugin.json"
    plugin_name: str | None = None
    if plugin_json_path.is_file():
        try:
            pdata = json.loads(plugin_json_path.read_text(encoding="utf-8"))
            plugin_name = pdata.get("name")
        except (json.JSONDecodeError, OSError):
            plugin_name = None
    plugins = data.get("plugins")
    bumped_entry = False
    if isinstance(plugins, list):
        for entry in plugins:
            if not isinstance(entry, dict):
                continue
            entry_name = entry.get("name")
            entry_source = entry.get("source")
            is_self = (
                (entry_name == plugin_name or plugin_name is None)
                and entry_source in ("./", {"source": "directory", "path": "./"})
            )
            if is_self:
                entry["version"] = new_ver
                bumped_entry = True
                break
    try:
        mp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    except OSError as e:
        return False, f"marketplace.json write failed: {e}"
    if bumped_entry:
        return True, f"marketplace.json (metadata + self-entry) -> {new_ver}"
    return True, f"marketplace.json (metadata only — no self-entry matched) -> {new_ver}"

def update_pyproject_toml(root: Path, new_ver: str) -> tuple[bool, str]:
    """Write version to pyproject.toml."""
    pp = root / "pyproject.toml"
    if not pp.is_file():
        return False, "pyproject.toml not found"
    try:
        content = pp.read_text(encoding="utf-8")
        updated = re.sub(
            r'^(version\s*=\s*")[^"]*(")',
            rf'\g<1>{new_ver}\2',
            content,
            count=1,
            flags=re.MULTILINE,
        )
        if updated == content:
            return False, "pyproject.toml: version field not found"
        pp.write_text(updated, encoding="utf-8")
        return True, f"pyproject.toml -> {new_ver}"
    except OSError as e:
        return False, f"pyproject.toml update failed: {e}"

def update_python_versions(root: Path, new_ver: str) -> list[tuple[bool, str]]:
    """Update __version__ = '...' in all .py files under scripts/."""
    results: list[tuple[bool, str]] = []
    scripts_dir = root / "scripts"
    if not scripts_dir.is_dir():
        return results
    pattern = re.compile(r'^(__version__\s*=\s*["\'])([^"\']*)(["\']\s*)$', re.MULTILINE)
    for py_file in scripts_dir.rglob("*.py"):
        try:
            content = py_file.read_text(encoding="utf-8")
        except OSError:
            continue
        if not pattern.search(content):
            continue
        updated = pattern.sub(rf"\g<1>{new_ver}\3", content)
        if updated != content:
            py_file.write_text(updated, encoding="utf-8")
            results.append((True, f"{py_file.relative_to(root)} -> {new_ver}"))
    return results

def check_version_consistency(root: Path) -> tuple[bool, str]:
    """Verify all version sources match. Includes marketplace.json metadata
    and self-entry (Layout C) when present."""
    versions: dict[str, str | None] = {}

    # plugin.json
    pj = root / ".claude-plugin" / "plugin.json"
    if pj.is_file():
        try:
            versions["plugin.json"] = json.loads(pj.read_text(encoding="utf-8")).get("version")
        except (json.JSONDecodeError, OSError):
            versions["plugin.json"] = None

    # marketplace.json (Layout C) — both metadata.version and the self-entry's version
    mp = root / ".claude-plugin" / "marketplace.json"
    if mp.is_file():
        try:
            mp_data = json.loads(mp.read_text(encoding="utf-8"))
            md = mp_data.get("metadata")
            if isinstance(md, dict):
                versions["marketplace.json:metadata"] = md.get("version")
            plugins_arr = mp_data.get("plugins")
            if isinstance(plugins_arr, list):
                for entry in plugins_arr:
                    if not isinstance(entry, dict):
                        continue
                    src = entry.get("source")
                    if src == "./" or (
                        isinstance(src, dict) and src.get("source") == "directory" and src.get("path") == "./"
                    ):
                        versions["marketplace.json:self-entry"] = entry.get("version")
                        break
        except (json.JSONDecodeError, OSError):
            versions["marketplace.json"] = None

    # pyproject.toml
    pp = root / "pyproject.toml"
    if pp.is_file():
        m = re.search(r'^version\s*=\s*"([^"]*)"', pp.read_text(encoding="utf-8"), re.MULTILINE)
        versions["pyproject.toml"] = m.group(1) if m else None

    found = {k: v for k, v in versions.items() if v is not None}
    if not found:
        return False, "No version sources found"
    unique = set(found.values())
    if len(unique) == 1:
        return True, f"All versions match: {unique.pop()}"
    details = ", ".join(f"{k}={v}" for k, v in found.items())
    return False, f"Version mismatch: {details}"

def do_bump(root: Path, new_ver: str, dry_run: bool = False) -> bool:
    """Orchestrate all version updates. Detects Layout C (marketplace.json at repo root)
    and bumps both manifests atomically when present."""
    cprint(f"\n{BOLD}Bumping to {new_ver}{' (dry-run)' if dry_run else ''}{NC}")

    is_layout_c = (root / ".claude-plugin" / "marketplace.json").is_file()

    if dry_run:
        cprint(f"  Would update plugin.json -> {new_ver}")
        if is_layout_c:
            cprint(f"  Would update marketplace.json (metadata + self-entry, Layout C) -> {new_ver}")
        cprint(f"  Would update pyproject.toml -> {new_ver}")
        cprint(f"  Would update __version__ vars -> {new_ver}")
        return True

    ok1, msg1 = update_plugin_json(root, new_ver)
    cprint(f"  {'OK' if ok1 else 'FAIL'}: {msg1}")

    ok_mp = True
    if is_layout_c:
        ok_mp, msg_mp = update_self_marketplace_json(root, new_ver)
        cprint(f"  {'OK' if ok_mp else 'FAIL'}: {msg_mp}")

    ok2, msg2 = update_pyproject_toml(root, new_ver)
    cprint(f"  {'OK' if ok2 else 'FAIL'}: {msg2}")

    py_results = update_python_versions(root, new_ver)
    for ok, msg in py_results:
        cprint(f"  {'OK' if ok else 'FAIL'}: {msg}")

    return ok1 and ok2 and ok_mp


# -- Hook installer ------------------------------------------------------------

def install_hook(root: Path) -> int:
    """Copy git-hooks/pre-push to .git/hooks/pre-push and set core.hooksPath."""
    cprint(f"\\n{BOLD}Installing git hooks...{NC}")
    source = root / "git-hooks" / "pre-push"
    if not source.is_file():
        cprint(f"  {RED}git-hooks/pre-push not found{NC}")
        return 1
    git_dir = root / ".git"
    if not git_dir.is_dir():
        cprint(f"  {RED}.git/ not found — is this a git repository?{NC}")
        return 1
    hooks_dir = git_dir / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    dest = hooks_dir / "pre-push"
    shutil.copy2(source, dest)
    dest.chmod(dest.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    cprint(f"  {GREEN}Installed: git-hooks/pre-push -> .git/hooks/pre-push{NC}")
    # Also set core.hooksPath so git finds hooks in git-hooks/ directly
    subprocess.run(["git", "config", "core.hooksPath", "git-hooks"],
                   cwd=str(root), check=False)
    cprint(f"  {GREEN}Set git config core.hooksPath = git-hooks{NC}")
    return 0


def _get_origin_slug(root: Path) -> str | None:
    """Return OWNER/REPO parsed from the current repo's origin remote, or None."""
    try:
        r = subprocess.run(
            ["git", "config", "--get", "remote.origin.url"],
            capture_output=True, text=True, cwd=str(root), check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0 or not r.stdout.strip():
        return None
    url = r.stdout.strip()
    # Handle git@github.com:OWNER/REPO.git and https://github.com/OWNER/REPO.git
    if url.startswith("git@"):
        _, _, path = url.partition(":")
    elif "//" in url:
        _, _, path = url.partition("//")
        # path is now "github.com/OWNER/REPO.git"
        path = path.split("/", 1)[1] if "/" in path else ""
    else:
        return None
    if path.endswith(".git"):
        path = path[:-4]
    parts = path.strip("/").split("/")
    if len(parts) != 2 or not parts[0] or not parts[1]:
        return None
    return f"{parts[0]}/{parts[1]}"


def install_branch_rules(root: Path) -> int:
    """Apply the cpv-branch-rules ruleset to the repo's GitHub origin.

    Auto-detects the OWNER/REPO slug from `git config remote.origin.url` and
    shells out to `uvx cpv-setup-branch-rules` so downstream plugins do not
    need to vendor setup_branch_rules.py locally. This is the server-side
    gate that enforces CI as a required status check — the local pre-push
    hook alone is bypassable with `git push --no-verify`, but a ruleset is
    enforced by GitHub itself.
    """
    cprint(f"\\n{BOLD}Installing branch-protection ruleset...{NC}")
    slug = _get_origin_slug(root)
    if slug is None:
        cprint(f"  {RED}Could not read origin remote URL — skipping.{NC}")
        cprint(f"  {YELLOW}Set `git remote add origin <url>` first, then retry.{NC}")
        return 1
    cprint(f"  Target repo: {slug}")
    try:
        r = subprocess.run(
            [
                "uvx",
                "--from",
                "git+https://github.com/Emasoft/claude-plugins-validation",
                "--with",
                "pyyaml",
                "cpv-setup-branch-rules",
                slug,
            ],
            cwd=str(root),
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        cprint(f"  {RED}uvx call failed: {exc}{NC}")
        return 1
    if r.returncode != 0:
        cprint(f"  {RED}cpv-setup-branch-rules exited with code {r.returncode}{NC}")
        return r.returncode
    cprint(f"  {GREEN}Branch rules applied to {slug}.{NC}")
    return 0


# -- Gate mode (pre-push quality checks) --------------------------------------

def _get_process_ancestry(max_depth: int = 30) -> list[tuple[int, str]]:
    """Walk parent processes via ps(1). Returns [(pid, cmdline), ...] closest-first.

    Used by the orchestrator check to verify that scripts/publish.py is an
    ancestor of the current pre-push gate invocation. Process ancestry is
    non-spoofable (unlike env vars, which a user could set with
    `CPV_PIPELINE=1 git push`).
    """
    ancestry: list[tuple[int, str]] = []
    pid = os.getpid()
    seen: set[int] = set()
    for _ in range(max_depth):
        if pid in seen or pid <= 0:
            break
        seen.add(pid)
        try:
            r = subprocess.run(
                ["ps", "-p", str(pid), "-o", "ppid=,args="],
                capture_output=True, text=True, timeout=5,
            )
        except (OSError, subprocess.SubprocessError):
            return []
        if r.returncode != 0:
            break
        line = r.stdout.strip()
        if not line:
            break
        parts = line.split(None, 1)
        if not parts:
            break
        try:
            ppid = int(parts[0])
        except ValueError:
            break
        cmdline = parts[1] if len(parts) > 1 else ""
        ancestry.append((pid, cmdline))
        if ppid <= 1:
            break
        pid = ppid
    return ancestry


def _called_by_publish_orchestrator(root: Path) -> bool:
    """Verify that scripts/publish.py (in publish mode, NOT --gate) is an ancestor.

    Expected chain for an orchestrated push:
        publish.py --patch|--minor|--major   (orchestrator)
          └─ git push
              └─ git (runs pre-push hook)
                  └─ sh (hook script)
                      └─ publish.py --gate   (this process)

    Walk the parent chain. At least one ancestor must be scripts/publish.py
    WITHOUT the --gate flag (that is, a publish orchestrator — not our own
    gate-mode re-entry).
    """
    expected_abs = str((root / "scripts" / "publish.py").resolve())
    expected_rel = "scripts/publish.py"
    for _pid, cmdline in _get_process_ancestry():
        if "publish.py" not in cmdline:
            continue
        if "--gate" in cmdline:
            continue
        if expected_abs in cmdline or expected_rel in cmdline:
            return True
    return False


def run_gate(root: Path) -> int:
    """Pre-push gate: blocks on any quality issue. Returns 0 if clean."""
    cprint(f"\n{BOLD}Pre-push gate checks{NC}\n")

    # Gate 0: Orchestrator check — only publish.py may trigger a push.
    # Prevents a user from running `git push` directly and bypassing the
    # version-bump / changelog / tag / release pipeline. Uses process
    # ancestry (non-spoofable), NOT env vars.
    cprint(f"{BLUE}[G0] Checking push orchestrator...{NC}")
    if not _called_by_publish_orchestrator(root):
        cprint("")
        cprint(f"  {RED}========================================{NC}")
        cprint(f"  {RED}  BLOCKED: Direct push not allowed{NC}")
        cprint(f"  {RED}  This pre-push hook only accepts pushes{NC}")
        cprint(f"  {RED}  initiated by scripts/publish.py.{NC}")
        cprint(f"  {RED}  Run one of:{NC}")
        cprint(f"  {RED}    uv run python scripts/publish.py --patch{NC}")
        cprint(f"  {RED}    uv run python scripts/publish.py --minor{NC}")
        cprint(f"  {RED}    uv run python scripts/publish.py --major{NC}")
        cprint(f"  {RED}========================================{NC}")
        return 1
    cprint(f"  {GREEN}Orchestrated by publish.py.{NC}")

    # Gate 1: Version bump check — local vs remote
    # Resolves origin/HEAD dynamically so the gate works on both `main` and
    # `master` default branches (and any other name). If none of the
    # candidates return a remote plugin.json, it's a first push and we allow.
    cprint(f"\n{BLUE}[G1] Checking version bump...{NC}")
    local_ver = get_current_version(root)
    if local_ver:
        # Try origin/HEAD first (most reliable), then explicit main/master
        candidates: list[str] = []
        try:
            sym = subprocess.run(
                ["git", "symbolic-ref", "refs/remotes/origin/HEAD"],
                capture_output=True, text=True, cwd=str(root), timeout=10,
            )
            if sym.returncode == 0 and sym.stdout.strip():
                # Output looks like "refs/remotes/origin/main"
                branch = sym.stdout.strip().split("/")[-1]
                candidates.append(f"origin/{branch}")
        except (OSError, subprocess.SubprocessError):
            pass
        for fallback in ("origin/main", "origin/master"):
            if fallback not in candidates:
                candidates.append(fallback)
        remote_ver: str | None = None
        matched_ref: str | None = None
        for ref in candidates:
            try:
                r = subprocess.run(
                    ["git", "show", f"{ref}:.claude-plugin/plugin.json"],
                    capture_output=True, text=True, cwd=str(root), timeout=10,
                )
            except (OSError, subprocess.SubprocessError):
                continue
            if r.returncode == 0 and r.stdout:
                try:
                    data = json.loads(r.stdout)
                    rv = data.get("version")
                    if isinstance(rv, str):
                        remote_ver = rv
                        matched_ref = ref
                        break
                except json.JSONDecodeError:
                    continue
        if remote_ver is None:
            cprint(f"  {YELLOW}No remote plugin.json found (first push?) — skipping version-bump check.{NC}")
        elif local_ver == remote_ver:
            cprint(f"  {RED}BLOCKED: Version not bumped — local {local_ver} == {matched_ref} {remote_ver}{NC}")
            return 1
        else:
            cprint(f"  {GREEN}Version bump OK: {remote_ver} → {local_ver} (via {matched_ref}){NC}")

    # Gate 2: Lint with ruff. MANDATORY — missing scripts/ dir is a BLOCK.
    cprint(f"\n{BLUE}[G2] Linting...{NC}")
    scripts_dir = root / "scripts"
    if not scripts_dir.is_dir():
        cprint(f"  {RED}BLOCKED: scripts/ directory missing — cannot lint.{NC}")
        return 1
    lint_result = subprocess.run(
        ["uv", "run", "ruff", "check", "scripts/"],
        cwd=str(root), timeout=120)
    if lint_result.returncode != 0:
        cprint(f"  {RED}BLOCKED: Lint issues found{NC}")
        return 1
    cprint(f"  {GREEN}Lint passed.{NC}")

    # Gate 3: Validate via REMOTE CPV validator. MANDATORY — no skip, no exceptions.
    # CORNERSTONE: a plugin cannot be pushed unless validation passes with 0
    # blocking issues (WARNING allowed). The validator is ALWAYS fetched from
    # GitHub so a tampered local copy cannot weaken the rules.
    cprint(f"\n{BLUE}[G3] Validating plugin (remote CPV)...{NC}")
    if not shutil.which("uvx"):
        cprint(f"  {RED}BLOCKED: uvx not found on PATH.{NC}")
        return 1
    ve = subprocess.run(
        ["uvx", "--from",
         "git+https://github.com/Emasoft/claude-plugins-validation",
         "--with", "pyyaml",
         "cpv-remote-validate", "plugin", ".", "--strict"],
        cwd=str(root), timeout=600).returncode
    # Exit codes: 0=pass, 1=CRITICAL, 2=MAJOR, 3=MINOR, 4=NIT, 5+=WARNING
    if ve != 0 and ve < 5:
        labels = {1: "CRITICAL", 2: "MAJOR", 3: "MINOR", 4: "NIT"}
        cprint(f"  {RED}BLOCKED: {labels.get(ve, f'exit {ve}')} issues found{NC}")
        return 1
    cprint(f"  {GREEN}Validation passed (0 blocking issues).{NC}")

    # Gate 4: Tests. MANDATORY — missing tests/ dir or zero tests is a BLOCK.
    cprint(f"\n{BLUE}[G4] Running tests...{NC}")
    test_dir = root / "tests"
    if not (test_dir.is_dir() and any(test_dir.glob("test_*.py"))):
        cprint(f"  {RED}BLOCKED: tests/ directory missing or empty.{NC}")
        cprint(f"  {RED}Every CPV plugin MUST ship tests.{NC}")
        return 1
    try:
        te = subprocess.run(
            ["uv", "run", "pytest", "tests/", "-x", "-q", "--tb=short"],
            cwd=str(root), timeout=300).returncode
    except subprocess.TimeoutExpired:
        cprint(f"  {RED}BLOCKED: Tests timed out after 300s.{NC}")
        return 1
    if te == 5:
        cprint(f"  {RED}BLOCKED: pytest collected 0 tests.{NC}")
        return 1
    if te != 0:
        cprint(f"  {RED}BLOCKED: Tests failed{NC}")
        return 1
    cprint(f"  {GREEN}Tests passed.{NC}")

    cprint(f"\n{GREEN}{BOLD}All gates passed.{NC}")
    return 0


# -- Pipeline stages -----------------------------------------------------------

def stage_bypass_guard() -> None:
    """Step 0: Reject any env var that could bypass a check. No exceptions.

    Issue #22 hardening (v2.86.0): broadened from a fixed allowlist to
    prefix-pattern matching. Any env var matching ``PLUGIN_SKIP_*``,
    ``CPV_SKIP_*``, ``SKIP_*``, or named ``NO_VERIFY`` aborts the publish.
    Closes the loophole where a fresh skip name (e.g. ``CPV_SKIP_GATE7``)
    that was not in the original explicit list would silently slip past.

    Two explicit infrastructure exemptions remain — both are read-only
    overrides used by CPV's own integrity / auth subsystems and never
    skip a gate:
        * ``CPV_SKIP_GITHUB_INTEGRITY=1`` — used to bypass GitHub-anchored
          integrity check (see cpv_integrity.py). The integrity check is
          a defence against tampering, NOT a publish gate.
        * ``CPV_SKIP_GH_AUTH_CHECK=1`` — used by `_ensure_gh_auth` to bypass
          the `gh auth status` round-trip on flaky networks. Auth still
          has to work for the actual `git push` / `gh release create`;
          this only skips the precheck.

    Both are documented exemptions, listed below and excluded from the
    pattern match.
    """
    cprint(f"\n{BOLD}[0/11] Checking for bypass attempts...{NC}")
    # Explicit infrastructure exemptions — see docstring above.
    exemptions = {"CPV_SKIP_GITHUB_INTEGRITY", "CPV_SKIP_GH_AUTH_CHECK"}
    forbidden_prefixes = ("PLUGIN_SKIP_", "CPV_SKIP_", "SKIP_")
    forbidden_exact = {"NO_VERIFY"}
    attempted = [
        v
        for v in sorted(os.environ)
        if (v.startswith(forbidden_prefixes) or v in forbidden_exact) and v not in exemptions
        if os.environ.get(v)
    ]
    if attempted:
        cprint(f"  {RED}BLOCKED: forbidden env vars set: {', '.join(attempted)}{NC}")
        cprint(f"  {RED}The publish pipeline enforces every check. "
               f"Fix failures, do not skip them.{NC}")
        cprint(f"  {DIM}(infrastructure exemptions: {', '.join(sorted(exemptions))}){NC}")
        sys.exit(1)
    cprint(f"  {GREEN}No bypass vars set.{NC}")

def stage_check_clean(root: Path) -> None:
    """Step 1: Working tree must be clean."""
    cprint(f"\n{BOLD}[1/11] Checking working tree...{NC}")
    r = run(["git", "status", "--porcelain"], cwd=root, capture=True)
    if r.stdout.strip():
        cprint(f"  {RED}Working tree is dirty. Commit or stash changes first.{NC}")
        cprint(r.stdout)
        sys.exit(1)
    cprint(f"  {GREEN}Clean.{NC}")

def stage_lint(root: Path) -> None:
    """Step 2: Lint + typecheck (ruff + mypy). MANDATORY — no skip.

    Runs ruff for style/syntax and mypy for static types in the same stage.
    Both must succeed — the cornerstone rule forbids any push with lint or
    type errors. Type-checking runs BEFORE the test suite so the cheap fails
    come before the expensive ones.
    """
    cprint(f"\n{BOLD}[2/11] Linting + type-checking...{NC}")
    scripts_dir = root / "scripts"
    if not scripts_dir.is_dir():
        cprint(f"  {RED}BLOCKED: scripts/ directory missing — cannot lint.{NC}")
        sys.exit(1)
    cprint(f"  {BLUE}ruff check scripts/{NC}")
    run(["uv", "run", "ruff", "check", "scripts/"], cwd=root)
    cprint(f"  {BLUE}mypy scripts/ --ignore-missing-imports{NC}")
    run(["uv", "run", "mypy", "scripts/", "--ignore-missing-imports"], cwd=root)
    cprint(f"  {GREEN}Lint + typecheck passed.{NC}")

def stage_tests(root: Path) -> None:
    """Step 3: Run pytest. MANDATORY — no skip, no exceptions.

    Cornerstone rule: failing tests block the push. Missing tests/ directory
    is a scaffolding bug and must be fixed, not bypassed.

    Order: tests run BEFORE the CPV validator so behavioral regressions fail
    fast on unit tests before the structural validator inspects the manifest.
    """
    cprint(f"\n{BOLD}[3/11] Running tests...{NC}")
    test_dir = root / "tests"
    if not test_dir.is_dir():
        cprint(f"  {RED}BLOCKED: tests/ directory missing.{NC}")
        cprint(f"  {RED}Every CPV plugin MUST ship a tests/ directory.{NC}")
        sys.exit(1)
    r = run(["uv", "run", "pytest", "tests/", "-x", "-q", "--tb=short"], cwd=root, check=False)
    if r.returncode == 5:
        # pytest exit 5 = no tests collected. This is ALSO a block — no exceptions.
        cprint(f"  {RED}BLOCKED: pytest collected 0 tests.{NC}")
        cprint(f"  {RED}Every CPV plugin MUST ship at least one test.{NC}")
        sys.exit(1)
    if r.returncode != 0:
        cprint(f"  {RED}BLOCKED: tests failed (exit {r.returncode}).{NC}")
        sys.exit(r.returncode)
    cprint(f"  {GREEN}Tests passed.{NC}")


def stage_validate(root: Path) -> None:
    """Step 4: Validate plugin via REMOTE CPV validator. MANDATORY — no skip.

    Cornerstone rule: a plugin cannot be pushed unless validation passes
    with 0 issues (WARNING allowed). The validator is ALWAYS fetched from
    GitHub (git+https://github.com/Emasoft/claude-plugins-validation) via
    uvx so a local tampered copy cannot weaken the rules. No exceptions.

    Order: runs AFTER lint + tests so behavioral regressions fail fast
    before the structural validator even looks at the manifest.
    """
    cprint(f"\n{BOLD}[4/11] Validating plugin (remote CPV)...{NC}")
    if not shutil.which("uvx"):
        cprint(f"  {RED}BLOCKED: uvx not found on PATH.{NC}")
        cprint(f"  {RED}Install via: brew install uv  or  pip install uv{NC}")
        sys.exit(1)
    # Fetch CPV from GitHub and run validate_plugin remotely. --strict blocks
    # on CRITICAL(1), MAJOR(2), MINOR(3), NIT(4); WARNING(5+) passes.
    run([
        "uvx", "--from",
        "git+https://github.com/Emasoft/claude-plugins-validation",
        "--with", "pyyaml",
        "cpv-remote-validate", "plugin", ".", "--strict",
    ], cwd=root)
    cprint(f"  {GREEN}Validation passed (0 blocking issues).{NC}")


# ── Marketplace-registration helpers (mirror of CPV's own publish.py Gate 6) ─

def _find_parent_marketplace(plugin_root: Path) -> Path | None:
    """Walk up looking for a parent marketplace.json (Layout B signature)."""
    current = plugin_root.resolve().parent
    while current != current.parent:
        mp = current / ".claude-plugin" / "marketplace.json"
        if mp.is_file():
            try:
                rel = plugin_root.resolve().relative_to(current)
                parts = rel.parts
                if len(parts) >= 2 and parts[0] == "plugins":
                    return current
            except ValueError:
                pass
            return None
        current = current.parent
    return None


def _detect_layout(plugin_root: Path) -> tuple[str, dict]:
    """Detect Layout A (standalone+notify), Layout B (nested), or 'none'."""
    parent = _find_parent_marketplace(plugin_root)
    if parent is not None:
        return "B", {"marketplace_root": parent, "plugin_name": plugin_root.name}
    notify_wf = plugin_root / ".github" / "workflows" / "notify-marketplace.yml"
    if notify_wf.is_file():
        try:
            content = notify_wf.read_text(encoding="utf-8")
        except OSError:
            content = ""
        m_owner = re.search(r"^\s*MARKETPLACE_OWNER:\s*[\"']?([^\"'\s]+)[\"']?\s*$", content, re.MULTILINE)
        m_repo = re.search(r"^\s*MARKETPLACE_REPO:\s*[\"']?([^\"'\s]+)[\"']?\s*$", content, re.MULTILINE)
        return "A", {
            "notify_workflow": notify_wf,
            "mkt_owner": m_owner.group(1) if m_owner else None,
            "mkt_repo": m_repo.group(1) if m_repo else None,
        }
    return "none", {}


def _gh_secret_exists(plugin_root: Path, secret_name: str) -> bool:
    """Check whether a GitHub secret with the given name exists on this repo."""
    gh = shutil.which("gh")
    if gh is None:
        return False
    r = subprocess.run([gh, "secret", "list"], cwd=str(plugin_root),
                       capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        return False
    for line in r.stdout.splitlines():
        if line.split("\t", 1)[0].strip() == secret_name:
            return True
    return False


def _current_repo_slug(plugin_root: Path) -> str | None:
    """Return owner/repo slug for current git origin, or None."""
    r = subprocess.run(["git", "remote", "get-url", "origin"], cwd=str(plugin_root),
                       capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        return None
    m = re.search(r"[:/]([^/:]+)/([^/]+?)(?:\\.git)?$", r.stdout.strip())
    return f"{m.group(1)}/{m.group(2)}" if m else None


def _read_plugin_name(plugin_root: Path) -> str:
    pj = plugin_root / ".claude-plugin" / "plugin.json"
    if pj.is_file():
        try:
            data = json.loads(pj.read_text(encoding="utf-8"))
            name = data.get("name")
            if isinstance(name, str) and name:
                return name
        except (OSError, json.JSONDecodeError):
            pass
    return plugin_root.name


def _fetch_remote_marketplace_json(owner: str, repo: str) -> dict | None:
    gh = shutil.which("gh")
    if gh is None:
        return None
    r = subprocess.run(
        [gh, "api", f"repos/{owner}/{repo}/contents/.claude-plugin/marketplace.json",
         "-H", "Accept: application/vnd.github.raw+json"],
        capture_output=True, text=True, timeout=60,
    )
    if r.returncode != 0:
        return None
    try:
        data = json.loads(r.stdout)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _remote_has_receiver_workflow(owner: str, repo: str) -> bool:
    gh = shutil.which("gh")
    if gh is None:
        return False
    r = subprocess.run(
        [gh, "api", f"repos/{owner}/{repo}/contents/.github/workflows"],
        capture_output=True, text=True, timeout=60,
    )
    if r.returncode != 0:
        return False
    try:
        entries = json.loads(r.stdout)
    except json.JSONDecodeError:
        return False
    if not isinstance(entries, list):
        return False
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name", "")
        if not isinstance(name, str) or not name.endswith((".yml", ".yaml")):
            continue
        f = subprocess.run(
            [gh, "api", f"repos/{owner}/{repo}/contents/.github/workflows/{name}",
             "-H", "Accept: application/vnd.github.raw+json"],
            capture_output=True, text=True, timeout=60,
        )
        if f.returncode == 0 and "repository_dispatch" in f.stdout:
            return True
    return False


def _plugin_in_remote_marketplace(mkt_json: dict, plugin_name: str, expected_repo: str | None) -> bool:
    """Accept github/url/git source forms; match URL slug for url|git (issue #25 Defect A)."""
    plugins = mkt_json.get("plugins")
    if not isinstance(plugins, list):
        return False
    for entry in plugins:
        if not isinstance(entry, dict):
            continue
        if entry.get("name") != plugin_name:
            continue
        source = entry.get("source")
        if not isinstance(source, dict):
            continue
        stype = source.get("source") or source.get("type")
        if stype == "github":
            if expected_repo is None or source.get("repo") == expected_repo:
                return True
        elif stype in ("url", "git"):
            url = source.get("url")
            if expected_repo is None:
                return True
            if isinstance(url, str):
                norm = url.removesuffix(".git").rstrip("/")
                if norm.endswith("/" + expected_repo) or norm.endswith(":" + expected_repo):
                    return True
    return False


def stage_marketplace_registration(root: Path) -> None:
    """Step 5: Verify the plugin is wired to its marketplace for auto-updates.

    Mirror of CPV's own publish.py Gate 6. Three modes:
      - Layout A (standalone + notify-marketplace.yml): verifies workflow,
        MARKETPLACE_PAT secret, remote marketplace.json registration,
        remote receiver workflow with repository_dispatch trigger
      - Layout B (nested under <marketplace>/plugins/<name>/): refuses to
        publish from the nested folder, requires running at marketplace root
      - 'none' (no marketplace wiring): emits a WARNING and proceeds — valid
        for first releases or experimental standalone plugins
    """
    cprint(f"\n{BOLD}[5/11] Marketplace-registration check...{NC}")
    layout, details = _detect_layout(root)

    if layout == "none":
        cprint(f"  {YELLOW}WARNING: no marketplace registration found for this plugin.{NC}")
        cprint(f"  {YELLOW}If you intend to publish to a marketplace, run the{NC}")
        cprint(f"  {YELLOW}setup-marketplace-auto-notification skill to wire up auto-updates.{NC}")
        cprint(f"  {YELLOW}Allowing release to proceed (standalone/experimental mode).{NC}")
        return

    if layout == "A":
        cprint("  Layout A detected (standalone plugin repo)")
        notify_wf = details.get("notify_workflow")
        mkt_owner = details.get("mkt_owner")
        mkt_repo = details.get("mkt_repo")
        if not notify_wf or not Path(notify_wf).is_file():
            cprint(f"  {RED}BLOCKED: .github/workflows/notify-marketplace.yml missing.{NC}")
            sys.exit(1)
        if not mkt_owner or not mkt_repo:
            cprint(f"  {RED}BLOCKED: notify-marketplace.yml has no MARKETPLACE_OWNER/MARKETPLACE_REPO.{NC}")
            sys.exit(1)
        cprint(f"  target marketplace: {mkt_owner}/{mkt_repo}")
        if shutil.which("gh") is None:
            cprint(f"  {RED}BLOCKED: gh CLI not installed — cannot verify secret/marketplace.{NC}")
            sys.exit(1)
        if not _gh_secret_exists(root, "MARKETPLACE_PAT"):
            cprint(f"  {RED}BLOCKED: MARKETPLACE_PAT secret not configured on this plugin repo.{NC}")
            cprint(f"  {RED}  Fix: uv run python scripts/set_marketplace_pat.py {_current_repo_slug(root) or 'OWNER/REPO'}{NC}")
            sys.exit(1)
        cprint(f"  {GREEN}MARKETPLACE_PAT secret configured{NC}")
        mkt_json = _fetch_remote_marketplace_json(mkt_owner, mkt_repo)
        if mkt_json is None:
            cprint(f"  {RED}BLOCKED: cannot fetch marketplace.json from {mkt_owner}/{mkt_repo}.{NC}")
            sys.exit(1)
        plugin_name = _read_plugin_name(root)
        slug = _current_repo_slug(root)
        if not _plugin_in_remote_marketplace(mkt_json, plugin_name, slug):
            cprint(f"  {RED}BLOCKED: plugin '{plugin_name}' not registered in {mkt_owner}/{mkt_repo} marketplace.json.{NC}")
            cprint(f"  {RED}  Add an entry: {{\"name\": \"{plugin_name}\", \"source\": {{\"source\": \"github\", \"repo\": \"{slug}\"}}}}{NC}")
            sys.exit(1)
        cprint(f"  {GREEN}Plugin registered in remote marketplace.json{NC}")
        if not _remote_has_receiver_workflow(mkt_owner, mkt_repo):
            cprint(f"  {RED}BLOCKED: remote marketplace {mkt_owner}/{mkt_repo} has no workflow with repository_dispatch trigger.{NC}")
            cprint(f"  {RED}  See setup-marketplace-auto-notification skill.{NC}")
            sys.exit(1)
        cprint(f"  {GREEN}Remote marketplace has receiver workflow{NC}")
        cprint(f"  {GREEN}Layout A marketplace registration verified.{NC}")
        return

    if layout == "B":
        cprint("  Layout B detected (nested plugin under marketplace repo)")
        marketplace_root_raw = details.get("marketplace_root")
        marketplace_root: Path | None = marketplace_root_raw if isinstance(marketplace_root_raw, Path) else None
        plugin_name_raw = details.get("plugin_name")
        # Note: no type annotation here — mypy's no-redef rule complains even
        # though the Layout A branch above returns before reaching this
        # point. Plain assignment avoids the false positive in the generated
        # template output (which downstream CI runs with mypy --strict).
        plugin_name = plugin_name_raw if isinstance(plugin_name_raw, str) else root.name
        if marketplace_root is None:
            cprint(f"  {RED}BLOCKED: Layout B detected but marketplace_root unresolved.{NC}")
            sys.exit(1)
        if root.resolve() != marketplace_root.resolve():
            cprint(f"  {RED}BLOCKED: This is a Layout B nested plugin.{NC}")
            cprint(f"  {RED}  publish.py must run at the MARKETPLACE root, not the nested folder.{NC}")
            cprint(f"  {RED}  Bumping a nested plugin alone breaks the atomic marketplace tag.{NC}")
            cprint(f"  {RED}  Fix: cd {marketplace_root} && uv run python scripts/publish.py --patch{NC}")
            sys.exit(1)
        mp_path = marketplace_root / ".claude-plugin" / "marketplace.json"
        try:
            mp_data = json.loads(mp_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            cprint(f"  {RED}BLOCKED: cannot read {mp_path}: {e}{NC}")
            sys.exit(1)
        entries = mp_data.get("plugins") if isinstance(mp_data, dict) else None
        if not isinstance(entries, list):
            cprint(f"  {RED}BLOCKED: marketplace.json has no 'plugins' array.{NC}")
            sys.exit(1)
        if not any(isinstance(e, dict) and e.get("name") == plugin_name for e in entries):
            cprint(f"  {RED}BLOCKED: plugin '{plugin_name}' not registered in {mp_path}.{NC}")
            cprint(f"  {RED}  Add: {{\"name\": \"{plugin_name}\", \"source\": \"./plugins/{plugin_name}\"}}{NC}")
            sys.exit(1)
        cprint(f"  {GREEN}Plugin '{plugin_name}' registered in parent marketplace.json{NC}")
        cprint(f"  {GREEN}Layout B marketplace registration verified.{NC}")


def stage_consistency(root: Path) -> None:
    """Step 6: Check version consistency."""
    cprint(f"\n{BOLD}[6/11] Checking version consistency...{NC}")
    ok, msg = check_version_consistency(root)
    cprint(f"  {msg}")
    if not ok:
        cprint(f"  {RED}Fix version mismatch before publishing.{NC}")
        sys.exit(1)
    cprint(f"  {GREEN}Consistent.{NC}")

def _read_remote_version(plugin_root: Path) -> str | None:
    """Read .claude-plugin/plugin.json's `version` from origin/master (or main).

    Idempotency baseline: the publish pipeline reads the REMOTE version, not
    the local one, so an interrupted publish that already bumped + committed
    locally cannot double-bump on re-run. Returns None when offline / no
    remote ref / file missing — caller must fall back to local baseline.
    """
    for ref in ("origin/master", "origin/main", "origin/HEAD"):
        try:
            r = subprocess.run(
                ["git", "show", f"{ref}:.claude-plugin/plugin.json"],
                capture_output=True, text=True, cwd=str(plugin_root),
                check=False, timeout=15,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if r.returncode != 0:
            continue
        try:
            v = json.loads(r.stdout).get("version")
        except json.JSONDecodeError:
            continue
        if isinstance(v, str):
            return v
    return None


def _infer_bump_type(old: str, new: str) -> str | None:
    """Classify a semver delta as 'major', 'minor', 'patch', or None."""
    o = parse_semver(old)
    n = parse_semver(new)
    if o is None or n is None or n <= o:
        return None
    if n[0] != o[0]:
        return "major"
    if n[1] != o[1]:
        return "minor"
    return "patch"


def _git_porcelain_clean(root: Path) -> bool:
    """True iff `git status --porcelain` is empty (working tree clean)."""
    try:
        r = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True, text=True, cwd=str(root),
            check=False, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode == 0 and not r.stdout.strip()


def _head_commit_message(root: Path) -> str:
    """Return the subject line of HEAD, or '' on failure."""
    try:
        r = subprocess.run(
            ["git", "log", "-1", "--pretty=%s"],
            capture_output=True, text=True, cwd=str(root),
            check=False, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return r.stdout.strip() if r.returncode == 0 else ""


def _local_tag_exists(root: Path, tag: str) -> bool:
    """True iff `tag` already exists in the local git repo."""
    try:
        r = subprocess.run(
            ["git", "rev-parse", "--verify", f"refs/tags/{tag}"],
            capture_output=True, text=True, cwd=str(root),
            check=False, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode == 0


def stage_bump(root: Path, new_ver: str, dry_run: bool) -> None:
    """Step 7: Bump version. Idempotent — skips when local already matches target.

    Recovery semantics: when a previous publish was interrupted between the
    local commit+tag and the push (transient network failure during git push,
    pre-push hook reject, etc.), the local repo is at the bumped version while
    origin is one minor behind. Re-running publish.py would DOUBLE-BUMP
    (read-local-then-add-1 → next minor on top of the already-bumped local).
    The fix: read REMOTE plugin.json as baseline, infer bump type from
    local-vs-remote delta, and skip the bump entirely when local already
    matches the target.
    """
    cprint(f"\n{BOLD}[7/11] Bumping version...{NC}")
    current = get_current_version(root)
    remote = _read_remote_version(root)
    if remote and current and current == new_ver:
        cprint(f"  {YELLOW}Local plugin.json is already at {new_ver} (remote at {remote}) — "
               f"skipping bump (interrupted-publish recovery).{NC}")
        return
    if remote and current and current != remote and current != new_ver:
        cprint(f"  {RED}REFUSED: local plugin.json is at {current} but remote is at "
               f"{remote} and target is {new_ver}. Refuse to guess what state this is.{NC}")
        cprint(f"  {RED}Manual intervention required: align local with remote, then re-run.{NC}")
        sys.exit(1)
    if not do_bump(root, new_ver, dry_run=dry_run):
        cprint(f"  {RED}Version bump failed.{NC}")
        sys.exit(1)
    cprint(f"  {GREEN}Version bumped to {new_ver}.{NC}")

def stage_update_badges(root: Path, old_ver: str, new_ver: str, dry_run: bool) -> None:
    """Step 7: Replace version badge in README.md.

    Strategy:
      1. Try exact-string substitution `version-<old>-blue` → `version-<new>-blue`
      2. If the exact old version is not present, fall back to a regex that
         matches ANY `version-X.Y.Z-blue` pattern (handles drift from a hand-edit
         or a missed release). Prevents the "stale forever" trap that bit CPV
         itself when its README badge fell 20 releases behind.
      3. Emit a WARNING (not silent skip) when no badge is found at all so the
         author notices the README has no shields.io version badge to update.
    """
    cprint(f"\n{BOLD}[8/11] Updating README badge...{NC}")
    readme = root / "README.md"
    if not readme.exists():
        cprint(f"  {YELLOW}WARNING: no README.md — skipping badge update.{NC}")
        return
    content = readme.read_text(encoding="utf-8")
    old_badge = f"version-{old_ver}-blue"
    new_badge = f"version-{new_ver}-blue"

    if old_badge in content:
        if dry_run:
            cprint(f"  Would update badge (exact match): {old_badge} -> {new_badge}")
            return
        readme.write_text(content.replace(old_badge, new_badge, 1), encoding="utf-8")
        cprint(f"  {GREEN}Updated README badge: {old_ver} -> {new_ver}{NC}")
        return

    # Fallback: regex match on any version-X.Y.Z-blue pattern
    badge_re = re.compile(r"version-\d+\.\d+\.\d+-blue")
    match = badge_re.search(content)
    if match is None:
        cprint(f"  {YELLOW}WARNING: no version-X.Y.Z-blue badge found in README.md.{NC}")
        cprint(f"  {YELLOW}Add a shields.io badge so future releases can update it automatically.{NC}")
        return
    found = match.group(0)
    if dry_run:
        cprint(f"  Would update badge (regex match): {found} -> {new_badge}")
        return
    readme.write_text(badge_re.sub(new_badge, content, count=1), encoding="utf-8")
    cprint(f"  {GREEN}Updated README badge (was {found}, now {new_badge}){NC}")

def detect_bump_type(root: Path) -> str:
    """Auto-detect the next bump type from conventional commits via git-cliff.

    Runs `git-cliff --bumped-version` and compares the predicted version to
    the REMOTE one (origin/master) to determine major/minor/patch. Falls back
    to 'patch' on any failure (git-cliff missing, repo empty, parse error) so
    the cornerstone rule — every push is a bump — is never violated.

    Idempotency: when the local repo already has a release commit (interrupted
    publish), reading local plugin.json would over-shoot the bump (current is
    already the bumped version, git-cliff would compute current+1). Reading
    remote/origin gives the true baseline.

    Conventional commit mapping (git-cliff defaults):
      feat:                 -> minor
      fix:/perf:/refactor:  -> patch
      BREAKING CHANGE / !   -> major
    """
    cliff_bin = shutil.which("git-cliff")
    if cliff_bin is None:
        cprint(f"{YELLOW}git-cliff not installed — auto-bump falls back to 'patch'.{NC}")
        return "patch"
    current = _read_remote_version(root) or get_current_version(root)
    if not current:
        cprint(f"{YELLOW}Cannot read current version for auto-bump — falling back to 'patch'.{NC}")
        return "patch"
    try:
        r = subprocess.run(
            [cliff_bin, "--bumped-version"],
            capture_output=True,
            text=True,
            cwd=str(root),
            check=False,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return "patch"
    if r.returncode != 0:
        return "patch"
    out = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else ""
    bumped = out.lstrip("v").strip()
    if not bumped or bumped == current:
        return "patch"
    try:
        cur = [int(p) for p in current.split(".")[:3]]
        nxt = [int(p) for p in bumped.split(".")[:3]]
        while len(cur) < 3:
            cur.append(0)
        while len(nxt) < 3:
            nxt.append(0)
    except ValueError:
        return "patch"
    if nxt[0] > cur[0]:
        return "major"
    if nxt[1] > cur[1]:
        return "minor"
    return "patch"


def stage_changelog(root: Path, new_ver: str, dry_run: bool) -> None:
    """Step 9: Generate CHANGELOG.md with git-cliff using the bumped tag.

    Uses the git-cliff pattern recommended for release pipelines:
        git cliff --bump --unreleased --tag v<NEXT> -o CHANGELOG.md

    --bump          promote the unreleased section into a dated tag entry
    --unreleased    process only commits since the last tag
    --tag v<NEXT>   label the new entry with the computed version (prefixed v)
    -o CHANGELOG.md write the regenerated changelog back to disk
    """
    cprint(f"\n{BOLD}[9/11] Generating changelog (git-cliff)...{NC}")
    if not shutil.which("git-cliff"):
        cprint(f"  {YELLOW}git-cliff not installed — skipping changelog.{NC}")
        return
    cliff_toml = root / "cliff.toml"
    if not cliff_toml.is_file():
        cprint(f"  {YELLOW}No cliff.toml — skipping changelog.{NC}")
        return
    tag = f"v{new_ver}"
    if dry_run:
        cprint(f"  Would run: git-cliff --bump --unreleased --tag {tag} -o CHANGELOG.md")
        return
    run(
        ["git-cliff", "--bump", "--unreleased", "--tag", tag, "-o", "CHANGELOG.md"],
        cwd=root,
    )
    cprint(f"  {GREEN}CHANGELOG.md updated with {tag}.{NC}")

def stage_commit_and_push(root: Path, new_ver: str, dry_run: bool) -> None:
    """Step 10: Commit, tag, push. Idempotent on commit + tag.

    Idempotency: if HEAD's subject is already `chore: bump version to <new_ver>`
    AND the working tree is clean, skip the commit step (interrupted-publish
    recovery). If the tag already exists locally, skip the tag step. The push
    always runs — that is what brings the remote into sync.

    TRDD-bbff5bc5 §5: gh-auth precheck runs BEFORE the first push so the
    user gets an actionable error if their gh CLI is unauthed/lacks push
    perm — instead of an opaque git push failure mid-pipeline.
    """
    cprint(f"\n{BOLD}[10/11] Committing and pushing...{NC}")
    tag = f"v{new_ver}"
    expected_subject = f"chore: bump version to {new_ver}"
    head_subject = _head_commit_message(root)
    tree_clean = _git_porcelain_clean(root)
    tag_exists = _local_tag_exists(root, tag)

    if dry_run:
        if head_subject == expected_subject and tree_clean:
            cprint(f"  Would skip commit (HEAD already '{expected_subject}', tree clean)")
        else:
            cprint(f"  Would commit: {expected_subject}")
        if tag_exists:
            cprint(f"  Would skip tag (already exists locally): {tag}")
        else:
            cprint(f"  Would tag: {tag}")
        cprint(f"  Would push (atomic): origin HEAD {tag}")
        return

    if head_subject == expected_subject and tree_clean:
        cprint(f"  {YELLOW}HEAD is already '{expected_subject}' and tree is clean — "
               f"skipping commit (interrupted-publish recovery).{NC}")
    else:
        run(["git", "add", "-A"], cwd=root)
        run(["git", "commit", "-m", expected_subject], cwd=root)

    if tag_exists:
        cprint(f"  {YELLOW}Tag {tag} already exists locally — skipping tag step.{NC}")
    else:
        run(["git", "tag", "-a", tag, "-m", f"Release {tag}"], cwd=root)

    # gh-auth precheck — fail fast with actionable error if gh missing/unauthed.
    owner, repo = _resolve_owner_repo(root)
    _ensure_gh_auth(owner, repo)
    # Atomic push: commit + tag land together or not at all. Eliminates the
    # half-published-state failure mode where `git push origin HEAD --tags`
    # could push the commit, fail on the tag (rejected/network), and leave
    # the remote with an unreleased commit + no tag. `--atomic` is a single
    # transaction in the wire protocol; the server rolls back if any ref
    # update fails. git_with_retry still wraps the call so transient
    # network hiccups (4xx-class permanent errors fall through immediately).
    cprint(f"  {BLUE}$ git push --atomic origin HEAD {tag}{NC}")
    git_with_retry(
        ["git", "push", "--atomic", "origin", "HEAD", tag],
        cwd=str(root), capture_output=False,
    )
    cprint(f"  {GREEN}Pushed {tag} atomically.{NC}")

def stage_gh_release(root: Path, new_ver: str, dry_run: bool) -> None:
    """Step 10: Create GitHub release via gh CLI.

    TRDD-bbff5bc5 §5: re-runs the gh-auth precheck before `gh release
    create` so an auth state change between gates 10 and 11 (token
    revoked, account switched) surfaces as an actionable error.
    """
    cprint(f"\n{BOLD}[11/11] Creating GitHub release...{NC}")
    tag = f"v{new_ver}"
    if not shutil.which("gh"):
        cprint(f"  {YELLOW}gh CLI not installed — skipping release.{NC}")
        return
    if dry_run:
        cprint(f"  Would create release: {tag}")
        return
    owner, repo = _resolve_owner_repo(root)
    _ensure_gh_auth(owner, repo)
    changelog_file = root / "CHANGELOG.md"
    # Use --notes-file when CHANGELOG exists (the git-cliff structured
    # release notes are the right thing to ship). Fall back to
    # --generate-notes only when no CHANGELOG is present. Passing both
    # flags simultaneously produces undefined behavior across gh versions
    # (some concatenate, some override) — never both.
    args = ["gh", "release", "create", tag, "--title", tag]
    if changelog_file.is_file():
        args.extend(["--notes-file", str(changelog_file)])
    else:
        args.append("--generate-notes")
    cprint(f"  {BLUE}$ {' '.join(args)}{NC}")
    result = gh_with_retry(args, cwd=str(root), check=False, capture_output=True)
    if result.stdout and result.stdout.strip():
        cprint(result.stdout.strip())
    if result.stderr and result.stderr.strip():
        print(result.stderr.strip(), file=sys.stderr)
    if result.returncode != 0:
        cprint(f"  {RED}Failed to create release (exit code {result.returncode}).{NC}")
    else:
        cprint(f"  {GREEN}Release created.{NC}")


# -- Main ----------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Unified publish pipeline for Claude Code plugins.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    # Mutually exclusive modes: side-modes (--gate / --install-hook /
    # --install-branch-rules) are distinct entry points; --patch/--minor/--major
    # are OPTIONAL overrides for the auto-bump default. Calling publish.py with
    # no flags runs the full publish pipeline with an auto-detected bump type.
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument("--gate", action="store_true",
                            help="Pre-push gate mode: lint + validate + tests only (no bump/push)")
    mode_group.add_argument("--install-hook", action="store_true",
                            help="Install pre-push hook into .git/hooks/ and set core.hooksPath")
    mode_group.add_argument("--install-branch-rules", action="store_true",
                            dest="install_branch_rules",
                            help="Apply the cpv-branch-rules ruleset to the GitHub origin "
                                 "(enforces CI as a required status check — the server-side gate)")
    mode_group.add_argument("--patch", action="store_const", dest="bump", const="patch",
                            help="Force a patch bump (override auto-detection)")
    mode_group.add_argument("--minor", action="store_const", dest="bump", const="minor",
                            help="Force a minor bump (override auto-detection)")
    mode_group.add_argument("--major", action="store_const", dest="bump", const="major",
                            help="Force a major bump (override auto-detection)")
    parser.add_argument("--dry-run", action="store_true", help="Preview only, no changes")
    # NOTE: --skip-tests was intentionally removed. The cornerstone rule is that
    # every CPV plugin MUST pass validation with 0 issues (WARNING allowed) before
    # any push. Skipping tests would bypass that guarantee — there are no exceptions.
    args = parser.parse_args()

    root = get_repo_root()

    # --install-hook mode: just set up the hook and exit
    if args.install_hook:
        return install_hook(root)

    # --install-branch-rules mode: apply the server-side GitHub ruleset
    if args.install_branch_rules:
        return install_branch_rules(root)

    # --gate mode: run quality checks only (called by pre-push hook)
    if args.gate:
        return run_gate(root)

    # Full publish pipeline — auto-detect bump type unless user forced one.
    # Idempotency: read REMOTE plugin.json (origin/master) as the bump
    # baseline. When local is ahead (interrupted publish: bumped + committed
    # but not pushed), bumping from local would double-bump. From remote,
    # bumping recomputes the SAME target as the original interrupted run,
    # and stage_bump's "already-at-target" guard then skips the bump.
    local = get_current_version(root)
    if not local:
        cprint(f"{RED}Cannot read version from .claude-plugin/plugin.json{NC}")
        return 1
    remote = _read_remote_version(root)
    baseline = remote or local

    if args.bump is None:
        bump_type = detect_bump_type(root)
        cprint(f"{BLUE}Bump type: {bump_type} (auto-detected from git-cliff){NC}")
    else:
        bump_type = args.bump
        cprint(f"{BLUE}Bump type: {bump_type} (forced via --{bump_type}){NC}")

    new_ver = bump_semver(baseline, bump_type)
    if not new_ver:
        cprint(f"{RED}Cannot parse baseline version: {baseline}{NC}")
        return 1

    if remote and local != remote:
        cprint(f"{YELLOW}Local plugin.json is at {local} but origin is at {remote} — "
               f"using remote as bump baseline (interrupted-publish recovery).{NC}")
    current = baseline

    cprint(f"\n{BOLD}Publish pipeline: {current} -> {new_ver}{NC}")
    if args.dry_run:
        cprint(f"{YELLOW}(dry-run mode — no changes will be made){NC}")

    # Gate 0: reject bypass attempts BEFORE running any other stage.
    # Pipeline order (per the cornerstone rule "every push is a bump"):
    #   lint+typecheck → tests → validate → marketplace-reg → consistency →
    #   bump → badge → changelog → commit → push → github release
    # Lint runs before tests (cheap fails first). Tests run before validate
    # so behavioral regressions fail the test suite before the structural
    # validator inspects the manifest.
    stage_bypass_guard()
    stage_check_clean(root)
    stage_lint(root)
    stage_tests(root)  # MANDATORY — no skip flag, no exceptions
    stage_validate(root)
    stage_marketplace_registration(root)  # Gate 6 parity with CPV's own publish.py
    stage_consistency(root)
    stage_bump(root, new_ver, args.dry_run)
    stage_update_badges(root, current, new_ver, args.dry_run)
    stage_changelog(root, new_ver, args.dry_run)
    stage_commit_and_push(root, new_ver, args.dry_run)
    stage_gh_release(root, new_ver, args.dry_run)

    cprint(f"\n{GREEN}{BOLD}Published {new_ver} successfully!{NC}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''


def gen_cpv_network_resilience_py() -> str:
    """Emit scripts/cpv_network_resilience.py for the new plugin.

    The new plugin's publish.py imports gh_with_retry / git_with_retry from
    this module to apply the documented retry pattern (~/.claude/rules/
    github-timeouts.md) to its own pushes and gh release calls. Shipping
    a verbatim copy keeps every plugin byte-identical on the resilience
    surface, so a fix landed in CPV propagates via `cpv standardize
    --force-templates` to every plugin in one go.
    """
    src = Path(__file__).resolve().parent / "cpv_network_resilience.py"
    return src.read_text(encoding="utf-8")


def gen_setup_hooks_py() -> str:
    """Generate scripts/setup-hooks.py — installs git hooks from git-hooks/ into .git/hooks/."""
    return '''#!/usr/bin/env python3
"""Install git hooks from git-hooks/ into .git/hooks/.

Usage: uv run python scripts/setup-hooks.py
"""

from __future__ import annotations

import shutil
import stat
import sys
from pathlib import Path


def get_repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def main() -> int:
    repo_root = get_repo_root()
    source_dir = repo_root / "git-hooks"
    target_dir = repo_root / ".git" / "hooks"

    if not source_dir.is_dir():
        print(f"ERROR: {source_dir} does not exist.", file=sys.stderr)
        return 1
    if not target_dir.is_dir():
        print(f"ERROR: {target_dir} does not exist. Is this a git repo?",
              file=sys.stderr)
        return 1

    hooks = [h for h in source_dir.iterdir() if not h.name.startswith(".")]
    if not hooks:
        print("No hooks found in git-hooks/.")
        return 0

    for hook_src in hooks:
        hook_dst = target_dir / hook_src.name
        shutil.copy2(hook_src, hook_dst)
        hook_dst.chmod(hook_dst.stat().st_mode
                       | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        print(f"  Installed: {hook_src.name} -> .git/hooks/{hook_src.name}")

    print(f"\\nDone. {len(hooks)} hook(s) installed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''


def gen_hooks_json(p: PluginParams) -> str:
    """Generate hooks/hooks.json — SessionStart hook to install deps into ${CLAUDE_PLUGIN_DATA}.

    Per official Anthropic docs, runtime dependencies should be installed into
    ${CLAUDE_PLUGIN_DATA} (persists across plugin updates) rather than
    ${CLAUDE_PLUGIN_ROOT} (wiped on every update).
    """
    _ = p  # unused but kept for consistent signature
    return """{
  "hooks": {
    "SessionStart": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "diff -q \\"${CLAUDE_PLUGIN_ROOT}/pyproject.toml\\" \\"${CLAUDE_PLUGIN_DATA}/pyproject.toml\\" >/dev/null 2>&1 || (cp \\"${CLAUDE_PLUGIN_ROOT}/pyproject.toml\\" \\"${CLAUDE_PLUGIN_DATA}/\\" && cd \\"${CLAUDE_PLUGIN_DATA}\\" && uv venv --python 3.12 -q && uv pip install -q -r \\"${CLAUDE_PLUGIN_ROOT}/pyproject.toml\\") || rm -f \\"${CLAUDE_PLUGIN_DATA}/pyproject.toml\\"",
            "statusMessage": "Installing plugin dependencies...",
            "timeout": 120
          }
        ]
      }
    ]
  }
}
"""


def gen_pre_push_hook(p: PluginParams) -> str:
    """Generate git-hooks/pre-push — thin bash delegator to publish.py --gate."""
    _ = p  # unused but kept for consistent signature
    return """#!/usr/bin/env bash
# Pre-push hook — delegates to publish.py --gate for all quality checks.
# Follows the PSS (perfect-skill-suggester) pattern: one script, two modes.
set -euo pipefail
REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"
if command -v uv &> /dev/null; then
    uv run python scripts/publish.py --gate
else
    python3 scripts/publish.py --gate
fi
exit $?
"""


def gen_ci_yml(p: PluginParams) -> str:
    """Generate .github/workflows/ci.yml — single consolidated CI workflow.

    Jobs (display names must stay exactly "Lint" / "Validate" / "Test" —
    cpv-setup-branch-rules reads them verbatim to wire the branch ruleset):
      - lint           : actionlint (workflow syntax) + Mega-Linter (multi-language)
      - validate       : uvx cpv-remote-validate plugin . --strict (issue #11)
      - test           : pytest (matrix: ubuntu-latest + macOS-latest)
      - commitlint     : conventional-commit gate (pull_request only)

    Triggers on both master and main branches (handles repos renamed either way).
    Includes merge_group for GitHub merge-queue / auto-merge support.

    v2.86.0 hardening (issue #22):
    * SHA-pin every third-party action (gh-actions.md §"Pin third-party
      actions to a full commit SHA"). Major-tag aliases re-point silently
      on the upstream side, so a hostile tag rewrite would otherwise let
      an attacker swap action code. The trailing `# vX.Y.Z` comment is
      pinact-compatible — pinact run will keep it in sync on uv lock.
    * actionlint step in the lint job catches workflow YAML syntax
      regressions BEFORE they hit production (e.g. a stray `SCANDIR
      env:` block, a malformed matrix dimension, an undefined ${{ secrets.X
      }} reference). Cheap-fail-first ordering: workflow syntax errors
      should not waste a full mega-linter run.
    * commitlint on PRs rejects non-conventional commits at the door so
      git-cliff's --bump and the CHANGELOG generator never see a junk
      subject line. Falls back to @commitlint/config-conventional when
      the repo has no .commitlintrc.json.
    * macOS matrix on the test job catches darwin-specific regressions
      (pathlib casing, mtime resolution, BSD `ps` vs procps-ng output).
      `fail-fast: false` so each OS reports its own failure even when
      one fails.
    """
    return f"""name: CI

# Job display names below MUST stay exactly Lint / Validate / Test —
# cpv-setup-branch-rules reads them verbatim when wiring the branch
# ruleset (TRDD-bbff5bc5).

on:
  push:
    branches: [master, main]
  pull_request:
    branches: [master, main]
  merge_group:

permissions:
  contents: read

concurrency:
  group: ${{{{ github.workflow }}}}-${{{{ github.ref }}}}
  cancel-in-progress: true

jobs:
  lint:
    name: Lint
    runs-on: ubuntu-latest
    permissions:
      contents: read
      issues: write
      pull-requests: write
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0

      # Cheap-fail-first: workflow-syntax errors before mega-linter.
      - name: Lint workflow YAML (actionlint)
        uses: rhysd/actionlint@914e7df21a07ef503a81201c76d2b11c789d3fca # v1.7.12

      - name: Mega-Linter
        uses: oxsecurity/megalinter@e08c2b05e3dbc40af4c23f41172ef1e068a7d651 # v8
        env:
          GITHUB_TOKEN: ${{{{ secrets.GITHUB_TOKEN }}}}
          VALIDATE_ALL_CODEBASE: false

      - name: Upload Mega-Linter reports
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: mega-linter-reports
          path: |
            megalinter-reports/
            mega-linter.log

  commitlint:
    name: Commitlint
    runs-on: ubuntu-latest
    # PRs only — push events on main don't need conventional-commit
    # enforcement (the commits are already merged).
    if: github.event_name == 'pull_request'
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - name: Conventional-commits gate
        uses: wagoid/commitlint-github-action@6cf16efdf4da5277c791d335142c03a0bdf1766e # v6.2.1

  validate:
    name: Validate
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          submodules: recursive

      - name: Install uv
        uses: astral-sh/setup-uv@e4db8464a088ece1b920f60402e813ea4de65b8f # v4

      - name: Set up Python
        run: uv python install {p.python_version}

      - name: Install dependencies
        run: uv sync --extra dev

      - name: Run plugin validation (remote CPV, --strict)
        # Fetches CPV from GitHub via uvx so downstream plugins do not need to
        # vendor scripts/validate_plugin.py. Matches publish.py's local gate
        # so CI and local gate agree. Issue #11: do NOT call local
        # scripts/validate_plugin.py — it does not exist in scaffolded plugins.
        run: |
          set +e
          uvx --from git+https://github.com/Emasoft/claude-plugins-validation \\
              --with pyyaml \\
              cpv-remote-validate plugin . --strict
          exit_code=$?
          set -e
          if [ $exit_code -eq 0 ]; then
            echo "Validation passed"
            exit 0
          elif [ $exit_code -ge 5 ]; then
            echo "Only WARNING-level findings (exit $exit_code) — advisory, not blocking"
            exit 0
          else
            echo "::error::Validation failed (exit $exit_code: CRITICAL/MAJOR/MINOR/NIT)"
            exit $exit_code
          fi

  test:
    name: Test
    # macOS matrix added v2.86.0 (issue #22) — catches darwin-specific
    # regressions that ubuntu-only runs miss (pathlib casing, mtime
    # resolution, BSD `ps` vs procps-ng output). fail-fast: false so
    # each OS reports its own failure.
    strategy:
      fail-fast: false
      matrix:
        os: [ubuntu-latest, macos-latest]
    runs-on: ${{{{ matrix.os }}}}
    steps:
      - uses: actions/checkout@v4

      - name: Install uv
        uses: astral-sh/setup-uv@e4db8464a088ece1b920f60402e813ea4de65b8f # v4

      - name: Set up Python
        run: uv python install {p.python_version}

      - name: Install dependencies
        run: uv sync --extra dev

      - name: Run tests
        run: |
          if [ -d "tests" ] && ls tests/test_*.py 1>/dev/null 2>&1; then
            uv run pytest tests/ -v
          else
            echo "No test files found, skipping"
          fi
"""


def gen_release_yml(p: PluginParams) -> str:
    """Generate .github/workflows/release.yml — GitHub Release on semver tag."""
    # Use p.python_version instead of hardcoded 3.12
    return f"""name: Release

on:
  push:
    tags:
      - 'v*.*.*'

jobs:
  release:
    runs-on: ubuntu-latest
    permissions:
      contents: write
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - name: Install uv
        uses: astral-sh/setup-uv@e4db8464a088ece1b920f60402e813ea4de65b8f # v4

      - name: Set up Python
        run: uv python install {p.python_version}

      - name: Install dependencies
        run: uv sync --extra dev

      - name: Run full plugin validation (remote CPV, --strict)
        # Fetches CPV from GitHub so downstream plugins do not need to vendor
        # scripts/validate_plugin.py. Matches publish.py's local gate and the
        # CI validate job. --strict blocks on CRITICAL/MAJOR/MINOR/NIT (exit
        # codes 1-4); WARNING (exit 5+) is advisory only.
        # Issue #11: removed local scripts/validate_plugin.py invocation.
        run: |
          set +e
          uvx --from git+https://github.com/Emasoft/claude-plugins-validation \
              --with pyyaml \
              cpv-remote-validate plugin . --strict \
              > validation-report.txt 2>&1
          exit_code=$?
          set -e
          cat validation-report.txt
          if [ $exit_code -ge 1 ] && [ $exit_code -le 4 ]; then
            echo "::error::Validation failed with exit code $exit_code (CRITICAL/MAJOR/MINOR/NIT found)"
            exit $exit_code
          fi

      - name: Run tests
        run: |
          if [ -d "tests" ]; then
            uv run pytest tests/ -v
          fi

      - name: Lint Python scripts
        run: uv run ruff check scripts/

      - name: Type check
        run: uv run mypy scripts/ --ignore-missing-imports

      - name: Generate changelog
        id: changelog
        # v2.86.0 hardening (issue #22): extract ONLY the matching
        # ``## [X.Y.Z] — YYYY-MM-DD`` block from CHANGELOG.md instead of
        # uploading the entire file. The release body should be the
        # release-specific notes, not the whole project history.
        # Fallback chain:
        #   1. CHANGELOG.md ## [version] section (curated, em-dash separator)
        #   2. CHANGELOG.md ## [version] section (legacy hyphen separator)
        #   3. Full CHANGELOG.md (when no section header matches)
        #   4. Auto-generated git log (when no CHANGELOG.md at all)
        env:
          TAG: ${{{{ github.ref_name }}}}
        run: |
          set -e
          # Strip leading 'v' so v1.2.3 matches ## [1.2.3] — ...
          VERSION="${{TAG#v}}"
          PREV_TAG=$(git describe --tags --abbrev=0 HEAD^ 2>/dev/null || echo "")
          if [ -z "$PREV_TAG" ]; then
            GITLOG=$(git log --pretty=format:"- %s (%h)" HEAD)
          else
            GITLOG=$(git log --pretty=format:"- %s (%h)" "$PREV_TAG..HEAD")
          fi
          if [ -f "CHANGELOG.md" ]; then
            # Extract the section header through the next ## or EOF. Try
            # em-dash (canonical) first, then legacy hyphen. awk prints
            # the block bounded by the matching header and the NEXT
            # `## ` at column 0 (or EOF).
            SECTION=$(awk -v ver="$VERSION" '
              $0 ~ "^## \\\\[" ver "\\\\] [—-] " {{found=1; print; next}}
              found && /^## / {{exit}}
              found {{print}}
            ' CHANGELOG.md)
            if [ -n "$SECTION" ]; then
              printf '%s\\n' "$SECTION" > changelog.txt
              echo "::notice::Release body extracted from CHANGELOG.md section for $VERSION"
            else
              echo "::warning::No CHANGELOG.md section matched ## [$VERSION] — falling back to full CHANGELOG.md"
              cp CHANGELOG.md changelog.txt
            fi
          else
            echo "$GITLOG" > changelog.txt
          fi
          echo "changelog_file=changelog.txt" >> "$GITHUB_OUTPUT"

      - name: Create or update GitHub Release (idempotent)
        # Idempotent shell flow — replaces the `softprops/action-gh-release`
        # action which 422s when publish.py's Gate-13 already created the
        # release locally. Pattern: `gh release view` to detect existence,
        # then `gh release edit` (with --notes-file fallback to existing body)
        # OR `gh release create` (when not pre-existing). Both branches
        # upload the validation-report artifact.
        env:
          GH_TOKEN: ${{{{ secrets.GITHUB_TOKEN }}}}
          TAG: ${{{{ github.ref_name }}}}
        run: |
          set -e
          if gh release view "$TAG" >/dev/null 2>&1; then
            echo "Release $TAG already exists — editing in place"
            gh release edit "$TAG" \
              --notes-file changelog.txt \
              --verify-tag
            gh release upload "$TAG" validation-report.txt --clobber
          else
            echo "Release $TAG does not exist yet — creating"
            gh release create "$TAG" validation-report.txt \
              --title "$TAG" \
              --notes-file changelog.txt \
              --verify-tag
          fi
"""


def gen_mega_linter_yml(p: PluginParams) -> str:
    """Generate .mega-linter.yml — Mega-Linter configuration."""
    _ = p  # unused but kept for consistent signature
    return """# Mega-Linter configuration
# https://megalinter.io/latest/configuration/

# Only lint changed files (faster, less noise)
APPLY_FIXES: none
VALIDATE_ALL_CODEBASE: false

# Enable these linter groups
ENABLE_LINTERS:
  - PYTHON_RUFF
  - PYTHON_MYPY
  - PYTHON_BANDIT
  - BASH_SHELLCHECK
  - BASH_SHFMT
  - JSON_JSONLINT
  - YAML_YAMLLINT
  - MARKDOWN_MARKDOWNLINT
  - SPELL_CSPELL
  - COPYPASTE_JSCPD
  - REPOSITORY_CHECKOV
  - REPOSITORY_GITLEAKS
  - REPOSITORY_TRIVY

# Exclude paths
FILTER_REGEX_EXCLUDE: "(tests_dev/|docs_dev/|scripts_dev/|samples_dev/|examples_dev/|builds_dev/|downloads_dev/|libs_dev/|llm_externalizer_output/|\\.claude/|\\.tldr/)"

# Python settings
PYTHON_RUFF_ARGUMENTS: "--select=E,F,W,I --ignore=E501"
PYTHON_MYPY_ARGUMENTS: "--ignore-missing-imports"

# Copy-paste detection — allow up to 5% duplication (0% is too strict for plugins)
COPYPASTE_JSCPD_ARGUMENTS: "--threshold 5"

# Checkov — skip workflow-level permission checks (we set permissions per-job)
REPOSITORY_CHECKOV_ARGUMENTS: "--skip-check CKV2_GHA_1"

# Markdown settings — allow long lines in README (badges)
MARKDOWN_MARKDOWNLINT_FILTER_REGEX_EXCLUDE: "CHANGELOG\\.md"

# Spell check — add project-specific words
SPELL_CSPELL_FILTER_REGEX_EXCLUDE: "(uv\\.lock|\\.json)"

# Disable reporters that create PR comments (we handle that ourselves)
DISABLE_REPORTERS:
  - GITHUB_COMMENT_REPORTER
"""


def gen_markdownlint_json(p: PluginParams) -> str:
    """Generate .markdownlint.json — disables MD013 (line-length).

    Plugin .md files (skill descriptions, agent prompts, slash commands,
    hook recipes) commonly contain extremely long lines that cannot be
    safely wrapped:

      - Inline `!command` shell snippets that would break across newlines
      - Long URLs in references / badges
      - Pipe-delimited tables describing config keys
      - Frontmatter with comma-joined tool lists

    Wrapping any of these would change the rendered markdown / break the
    Claude Code parser. CPV-published plugins therefore disable MD013
    everywhere; the rest of the markdownlint defaults stay in effect.
    """
    _ = p  # unused but kept for consistent signature
    return """{
  \"default\": true,
  \"MD007\": false,
  \"MD013\": false,
  \"MD022\": false,
  \"MD024\": { \"siblings_only\": true },
  \"MD026\": false,
  \"MD029\": false,
  \"MD031\": false,
  \"MD032\": false,
  \"MD033\": false,
  \"MD034\": false,
  \"MD036\": false,
  \"MD037\": false,
  \"MD038\": false,
  \"MD040\": false,
  \"MD041\": false,
  \"MD046\": false,
  \"MD048\": false,
  \"MD049\": false,
  \"MD050\": false,
  \"MD051\": false,
  \"MD052\": false,
  \"MD053\": false,
  \"MD055\": false,
  \"MD057\": false,
  \"MD058\": false,
  \"MD059\": false,
  \"MD060\": false
}
"""


def gen_notify_marketplace_yml(p: PluginParams) -> str:
    """Generate .github/workflows/notify-marketplace.yml — marketplace notification.

    Per-plugin VALUES that vary: marketplace owner and marketplace repo.
    When ``p.marketplace_owner`` is set (e.g. detected from an existing
    notify-marketplace.yml during a ``--force-templates`` migration) it
    overrides ``p.github_owner`` so a plugin whose marketplace lives under
    a different owner doesn't have its OWNER overwritten.

    Per-plugin NAMES are NOT supported (v2.86.0+). The dispatch secret is
    ALWAYS named ``MARKETPLACE_PAT``. Plugins with deviant secret names
    get a loud ``[ACTION REQUIRED]`` warning at migration time directing
    the maintainer to ``gh secret set MARKETPLACE_PAT --body
    "$MARKETPLACE_PAT"`` rather than CPV preserving their off-canon name.
    Single canon-name policy keeps `cpv_setup_auth`, GH webhook receivers,
    and docs strictly aligned across every plugin.

    Env-var sanitization (gh-actions.md §"Avoid expression injection"):
    every ``${{{{ github.* }}}}`` consumed by a ``run:`` block is first
    bound to an ``env:`` mapping; the shell sees ``$VAR`` rather than the
    raw expression. Prevents shell-metacharacter exfil if the upstream
    repository metadata is ever crafted hostile.
    """
    marketplace_owner = p.marketplace_owner if p.marketplace_owner else p.github_owner
    marketplace_repo = p.marketplace if p.marketplace else "my-plugins-marketplace"
    return f"""# Notify marketplace repo when this plugin is updated
# Requires MARKETPLACE_PAT secret (Personal Access Token with repo scope).
# Create via: gh secret set MARKETPLACE_PAT --repo {marketplace_owner}/{p.repo_name} \\
#               --body "$MARKETPLACE_PAT"
# (assumes $MARKETPLACE_PAT is exported in your shell or .env file)

name: Notify Marketplace

on:
  push:
    branches: [main]
    paths:
      - '.claude-plugin/plugin.json'
      - 'hooks/**'
      - 'commands/**'
      - 'agents/**'
      - 'skills/**'
      - 'scripts/**'

env:
  MARKETPLACE_OWNER: '{marketplace_owner}'
  MARKETPLACE_REPO: '{marketplace_repo}'

jobs:
  notify:
    runs-on: ubuntu-latest
    steps:
      # Sanitization: every `${{{{ github.* }}}}` value crossing into a shell `run:`
      # block is first bound to an `env:` mapping; the run-script sees `$VAR`
      # rather than raw expression interpolation. Prevents shell-injection if
      # upstream metadata is ever crafted hostile (gh-actions.md L66-77).
      - name: Get plugin info
        id: plugin
        env:
          REPO_NAME: ${{{{ github.event.repository.name }}}}
          REF_SHA: ${{{{ github.sha }}}}
        run: |
          printf 'name=%s\\n' "$REPO_NAME" >> "$GITHUB_OUTPUT"
          printf 'ref=%s\\n'  "$REF_SHA"   >> "$GITHUB_OUTPUT"

      - name: Trigger marketplace update
        uses: peter-evans/repository-dispatch@28959ce8df70de7be546dd1250a005dd32156697 # v4.0.1
        with:
          token: ${{{{ secrets.MARKETPLACE_PAT }}}}
          repository: ${{{{ env.MARKETPLACE_OWNER }}}}/${{{{ env.MARKETPLACE_REPO }}}}
          event-type: plugin-updated
          client-payload: |
            {{
              "plugin": "${{{{ steps.plugin.outputs.name }}}}",
              "ref": "${{{{ steps.plugin.outputs.ref }}}}",
              "source_repo": "${{{{ github.repository }}}}",
              "triggered_by": "${{{{ github.actor }}}}"
            }}

      - name: Summary
        env:
          PLUGIN_NAME: ${{{{ steps.plugin.outputs.name }}}}
          PLUGIN_REF: ${{{{ steps.plugin.outputs.ref }}}}
        run: |
          {{
            printf '## Marketplace Notification\\n\\n'
            printf 'Triggered update in %s/%s\\n\\n' "$MARKETPLACE_OWNER" "$MARKETPLACE_REPO"
            printf -- '- Plugin: %s\\n' "$PLUGIN_NAME"
            printf -- '- Commit: %s\\n' "$PLUGIN_REF"
          }} >> "$GITHUB_STEP_SUMMARY"
"""


def gen_tests_init() -> str:
    """Generate tests/__init__.py placeholder."""
    return '"""Test suite for the plugin."""\n'


def gen_scripts_init(p: PluginParams) -> str:
    """Generate scripts/__init__.py with version."""
    return f'"""Plugin scripts for {p.name}."""\n\n__version__ = "{p.version}"\n'


# =============================================================================
# FILE ASSEMBLY
# =============================================================================


def generate_all_files(p: PluginParams) -> list[tuple[str, str, bool]]:
    """Return list of (relative_path, content, is_executable) for all scaffold files."""
    files: list[tuple[str, str, bool]] = [
        # Manifest
        (".claude-plugin/plugin.json", gen_plugin_json(p), False),
        (".gitignore", gen_gitignore(p), False),
    ]
    # Layout C — also emit a self-referential marketplace manifest at repo root
    if p.self_marketplace:
        files.append((".claude-plugin/marketplace.json", gen_self_marketplace_json(p), False))
    # Language-specific project config
    if p.language == "python":
        files.extend(
            [
                ("pyproject.toml", gen_pyproject_toml(p), False),
                (".python-version", gen_python_version(p), False),
            ]
        )
    elif p.language in ("js", "ts"):
        files.append(("package.json", gen_package_json(p), False))
        if p.language == "ts":
            files.append(("tsconfig.json", gen_tsconfig_json(), False))
    elif p.language == "rust":
        files.append(("Cargo.toml", gen_cargo_toml(p), False))
    elif p.language == "go":
        files.append(("go.mod", gen_go_mod(p), False))
    elif p.language == "deno":
        files.append(("deno.json", gen_deno_json(p), False))
    elif p.language == "elixir":
        files.append(("mix.exs", gen_mix_exs(p), False))
    elif p.language == "ruby":
        files.append(("Gemfile", gen_gemfile(p), False))
    elif p.language == "java":
        files.append(("pom.xml", gen_pom_xml(p), False))
    elif p.language == "kotlin":
        files.append(("build.gradle.kts", gen_build_gradle_kts(p), False))
    files.extend(
        [
            # Documentation
            ("README.md", gen_readme(p), False),
            ("LICENSE", gen_license_mit(p), False),
            # Changelog config
            ("cliff.toml", gen_cliff_toml(p), False),
        ]
    )
    # Python-specific scripts + CI/CD — only emitted for python language for now.
    # Non-python plugins get a minimal scaffold and must provide their own CI.
    if p.language == "python":
        files.extend(
            [
                ("scripts/__init__.py", gen_scripts_init(p), False),
                ("scripts/publish.py", gen_publish_py(p), True),
                ("scripts/cpv_network_resilience.py", gen_cpv_network_resilience_py(), True),
                ("scripts/setup-hooks.py", gen_setup_hooks_py(), True),
                ("hooks/hooks.json", gen_hooks_json(p), False),
                ("git-hooks/pre-push", gen_pre_push_hook(p), True),
                (".mega-linter.yml", gen_mega_linter_yml(p), False),
                (".markdownlint.json", gen_markdownlint_json(p), False),
                (".github/workflows/ci.yml", gen_ci_yml(p), False),
                (".github/workflows/release.yml", gen_release_yml(p), False),
                (".github/workflows/notify-marketplace.yml", gen_notify_marketplace_yml(p), False),
                ("tests/__init__.py", gen_tests_init(), False),
            ]
        )
    else:
        # Minimal non-python scaffold — leaves CI/publish to the plugin author,
        # but ships a README section explaining the expected commands.
        files.append(
            (
                f"LANGUAGE-{p.language.upper()}-TODO.md",
                gen_language_todo(p),
                False,
            )
        )
    return files


def gen_language_todo(p: PluginParams) -> str:
    """Generate a TODO note for non-python plugins explaining what to add."""
    return f"""# TODO: Wire up CI/CD for `{p.language}` plugin

This plugin was scaffolded with `--language {p.language}`. CPV's Python
scaffold (pyproject.toml, pytest, ruff, publish.py, pre-push hook) was
skipped because it does not apply to your language.

## What you still need to add

1. A lint command (e.g. `eslint`, `cargo clippy`, `golangci-lint`, `deno lint`)
2. A test runner (e.g. `vitest`, `cargo test`, `go test`, `deno test`)
3. A publish/release script that bumps the version in both `plugin.json` AND
   your language manifest (`package.json`, `Cargo.toml`, `go.mod`, `deno.json`)
4. A pre-push git hook that runs lint + tests + CPV validation before pushing
5. GitHub Actions workflows for CI + release

## CPV validates all plugins regardless of language

You can validate this plugin against the current CPV ruleset from anywhere
using `uvx` — no need to clone or install CPV:

```bash
uvx --from git+https://github.com/Emasoft/claude-plugins-validation --with pyyaml \\
    cpv-remote-validate plugin . --strict
```

CPV checks:
- plugin.json manifest
- commands/, agents/, skills/, hooks/ structure
- No hardcoded secrets or personal paths
- Cross-references in all .md files

## Monitor, userConfig, channels, CLAUDE_PLUGIN_OPTION_*

All v2.1.80+ plugin features work regardless of language.
See `skills/canonical-pipeline/references/v2-1-80-features.md` in the CPV
plugin for schemas and examples.
"""


# =============================================================================
# DIRECTORY CREATION
# =============================================================================

# Standard component directories that every plugin repo should have
COMPONENT_DIRS = [
    ".claude-plugin",
    ".github/workflows",
    "agents",
    "commands",
    "git-hooks",
    "hooks",
    "scripts",
    "skills",
    "tests",
]


def generate_plugin_repo(target: Path, p: PluginParams, dry_run: bool = False) -> list[str]:
    """Write all scaffold files to target directory. Returns list of created file paths."""
    created: list[str] = []

    # Create component directories (including empty ones for plugin structure)
    for dir_name in COMPONENT_DIRS:
        dir_path = target / dir_name
        if dry_run:
            print(f"  {BLUE}[dry-run]{NC} mkdir -p {dir_path}")
        else:
            dir_path.mkdir(parents=True, exist_ok=True)
        created.append(str(dir_path) + "/")

    # Write all generated files
    all_files = generate_all_files(p)
    for rel_path, content, is_executable in all_files:
        file_path = target / rel_path

        if dry_run:
            print(f"  {BLUE}[dry-run]{NC} write {file_path} ({len(content)} bytes){' [exec]' if is_executable else ''}")
            created.append(str(file_path))
            continue

        # Ensure parent directory exists
        file_path.parent.mkdir(parents=True, exist_ok=True)

        # Write the file
        file_path.write_text(content, encoding="utf-8")

        # Set executable bit if needed
        if is_executable:
            file_path.chmod(file_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

        created.append(str(file_path))

    return created


# =============================================================================
# MAIN
# =============================================================================


def main() -> int:
    """Parse CLI arguments and generate the plugin repository scaffold."""
    parser = argparse.ArgumentParser(
        description="Generate a complete Claude Code plugin repository scaffold.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  uv run scripts/generate_plugin_repo.py /tmp/my-plugin \\
    --name my-plugin --description "A cool plugin" \\
    --author "John Doe" --author-email "john@example.com" \\
    --github-owner johndoe --marketplace my-marketplace

  uv run scripts/generate_plugin_repo.py ./new-plugin \\
    --name new-plugin --description "Plugin desc" \\
    --author Emasoft --author-email "713559+Emasoft@users.noreply.github.com" \\
    --github-owner Emasoft --marketplace claude-plugins-marketplace \\
    --dry-run
""",
    )
    parser.add_argument("target_dir", type=Path, help="Target directory for the new plugin repo")
    parser.add_argument("--name", required=True, help="Plugin name (lowercase, hyphens allowed)")
    parser.add_argument("--description", required=True, help="One-line plugin description")
    parser.add_argument("--author", required=True, help="Author display name")
    parser.add_argument("--author-email", required=True, help="Author email")
    parser.add_argument("--license", default="MIT", help="SPDX license identifier (default: MIT)")
    parser.add_argument("--python-version", default="3.12", help="Minimum Python version (default: 3.12)")
    parser.add_argument("--github-owner", default="", help="GitHub account or organization name")
    parser.add_argument("--marketplace", default="", help="Marketplace name for install commands")
    parser.add_argument("--version", default="0.1.0", help="Initial version (default: 0.1.0)")
    parser.add_argument(
        "--language",
        choices=sorted(VALID_LANGUAGES) + ["auto"],
        default="python",
        help="Plugin language (default: python). Use 'auto' to detect from "
        "an existing manifest in target_dir (uses detect_language). "
        "Non-python emits a minimal scaffold.",
    )
    parser.add_argument(
        "--self-marketplace",
        action="store_true",
        help="Layout C: also emit .claude-plugin/marketplace.json with a self-entry "
        '(source: "./"). Use when the repo should be both plugin and marketplace.',
    )
    # TRDD-793ac32a: dev-stripping. Default ON — emits cpv.strip block in
    # plugin.json so the user can run `cpv strip-dev-parts` later. The actual
    # extraction is NOT done at scaffold time; only the configuration.
    parser.add_argument(
        "--strip-dev",
        dest="strip_dev",
        action="store_true",
        default=True,
        help="(default) Emit cpv.strip block in plugin.json so dev-only "
        "folders can later be moved to per-plugin git submodules via "
        "`cpv strip-dev-parts` (TRDD-793ac32a). Saves ~12 MB per cache install.",
    )
    parser.add_argument(
        "--no-strip-dev",
        dest="strip_dev",
        action="store_false",
        help="Disable dev-stripping config (legacy mode — keep all dev parts in MAIN repo).",
    )
    parser.add_argument("--dry-run", action="store_true", help="Preview files without writing")

    # ── Phase 6: single-input slurp flags ────────────────────────────────
    # Each --skill/--agent/--command/--mcp-server/--scripts copies its
    # input into the right component folder of the new plugin. --from PATH
    # auto-classifies (best-effort): SKILL.md → skill; .md with
    # `allowed-tools:` frontmatter → command; .md with `tools:`/no
    # allowed-tools → agent; .mcp.json → MCP server; directory of scripts
    # → scripts/. Multiple flags can be combined.
    slurp_grp = parser.add_argument_group("slurp inputs (Phase 6)")
    slurp_grp.add_argument(
        "--from",
        dest="from_path",
        type=Path,
        action="append",
        default=[],
        metavar="PATH",
        help="Auto-classify and copy a file or folder into the new plugin "
        "(may repeat). SKILL.md → skill; .md with allowed-tools → command; "
        ".md without → agent; .mcp.json → MCP config; directory → scripts/.",
    )
    slurp_grp.add_argument(
        "--skill",
        type=Path,
        action="append",
        default=[],
        metavar="PATH",
        help="Copy SKILL.md (or folder containing SKILL.md) into skills/<name>/. May repeat.",
    )
    slurp_grp.add_argument(
        "--agent",
        type=Path,
        action="append",
        default=[],
        metavar="PATH",
        help="Copy a .md agent file into agents/. May repeat.",
    )
    slurp_grp.add_argument(
        "--command",
        type=Path,
        action="append",
        default=[],
        metavar="PATH",
        help="Copy a .md command file into commands/. May repeat.",
    )
    slurp_grp.add_argument(
        "--mcp-server",
        type=Path,
        action="append",
        default=[],
        metavar="PATH",
        help="Copy .mcp.json (or a directory containing one) into the plugin root. May repeat.",
    )
    slurp_grp.add_argument(
        "--scripts",
        type=Path,
        action="append",
        default=[],
        metavar="DIR",
        help="Copy all files from DIR into scripts/. May repeat.",
    )

    args = parser.parse_args()

    target = args.target_dir.resolve()

    # TRDD-83ab59e7: --language auto resolves against any pre-existing
    # manifest in target_dir. We resolve BEFORE constructing PluginParams
    # so the rest of the pipeline sees a concrete language and never has
    # to special-case "auto" again.
    resolved_language = resolve_language(args.language, target)
    if args.language == "auto":
        print(f"{BLUE}--language auto detected:{NC} {resolved_language}")

    # Build params
    params = PluginParams(
        name=args.name,
        description=args.description,
        author=args.author,
        author_email=args.author_email,
        license=args.license,
        python_version=args.python_version,
        github_owner=args.github_owner,
        marketplace=args.marketplace,
        version=args.version,
        language=resolved_language,
        self_marketplace=args.self_marketplace,
        strip_dev=args.strip_dev,
    )

    # Check target directory
    if target.exists() and any(target.iterdir()):
        print(f"{YELLOW}WARNING: Target directory is not empty: {target}{NC}")
        print(f"{YELLOW}Files will be added/overwritten.{NC}")

    print(f"\n{BOLD}Generating plugin scaffold: {params.name}{NC}")
    print(f"  Target: {target}")
    print(f"  Version: {params.version}")
    print(f"  Author: {params.author} <{params.author_email}>")
    print(f"  License: {params.license}")
    if params.github_owner:
        print(f"  GitHub: {params.github_url}")
    if params.marketplace:
        print(f"  Marketplace: {params.marketplace}")
    if params.self_marketplace:
        print("  Layout: C (marketplace-in-plugin, self-referential)")
    if args.dry_run:
        print(f"  {YELLOW}(dry-run mode){NC}")
    print()

    created = generate_plugin_repo(target, params, dry_run=args.dry_run)

    # ── Phase 6: post-scaffold slurp of user-provided inputs ─────────
    slurp_count = 0
    if not args.dry_run:
        slurp_count = _do_slurp(
            target,
            from_paths=args.from_path,
            skill_paths=args.skill,
            agent_paths=args.agent,
            command_paths=args.command,
            mcp_paths=args.mcp_server,
            scripts_paths=args.scripts,
        )
    elif any([args.from_path, args.skill, args.agent, args.command, args.mcp_server, args.scripts]):
        print(
            f"  {YELLOW}(dry-run: --from / --skill / --agent / --command / "
            f"--mcp-server / --scripts inputs are NOT slurped){NC}"
        )

    # Summary
    file_count = sum(1 for f in created if not f.endswith("/"))
    dir_count = sum(1 for f in created if f.endswith("/"))
    extra = f" + {slurp_count} slurped from --from/--skill/--agent/etc" if slurp_count else ""
    print(f"\n{GREEN}{BOLD}Done!{NC} Created {file_count} files in {dir_count} directories{extra}.")

    if not args.dry_run:
        print(f"\n{BOLD}Next steps:{NC}")
        print(f"  cd {target}")
        print("  git init && git add -A && git commit -m 'Initial scaffold'")
        print(f"  uv venv --python {params.python_version} && source .venv/bin/activate")
        print("  uv pip install -e .")
        print("  uv run python scripts/setup-hooks.py")
        print(f"\n{BOLD}After first push to GitHub:{NC}")
        print("  # Apply the server-side ruleset that enforces CI as a required check")
        print("  uv run python scripts/publish.py --install-branch-rules")

    return 0


if __name__ == "__main__":
    sys.exit(main())
