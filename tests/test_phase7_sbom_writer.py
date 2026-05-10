"""Tests for Phase 7 (RC-106) CycloneDX 1.6 SBOM writer."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from cpv_sbom_writer import (  # noqa: E402
    CYCLONEDX_FORMAT,
    CYCLONEDX_SPEC_VERSION,
    Dependency,
    generate_sbom,
    iter_dependencies,
    to_purl,
    write_sbom,
)


def _make_plugin(tmp_path: Path, files: dict[str, str] | None = None) -> Path:
    plugin = tmp_path / "demo"
    (plugin / ".claude-plugin").mkdir(parents=True)
    (plugin / ".claude-plugin" / "plugin.json").write_text(
        '{"name": "demo", "version": "1.2.3", "description": "test"}\n'
    )
    for rel, content in (files or {}).items():
        target = plugin / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return plugin


# -----------------------------------------------------------------------------
# Manifest parsers
# -----------------------------------------------------------------------------


class TestPackageJson:
    def test_all_dep_sections(self, tmp_path: Path) -> None:
        plugin = _make_plugin(
            tmp_path,
            {
                "package.json": json.dumps(
                    {
                        "name": "x",
                        "dependencies": {"react": "18.0.0"},
                        "devDependencies": {"jest": "^29.0.0"},
                        "peerDependencies": {"vue": ">=3.0.0"},
                        "optionalDependencies": {"fsevents": "2.3.0"},
                    }
                ),
            },
        )
        deps = list(iter_dependencies(plugin))
        names = {d.name for d in deps}
        assert names == {"react", "jest", "vue", "fsevents"}
        scopes = {d.name: d.scope for d in deps}
        assert scopes["react"] == "required"
        assert scopes["jest"] == "dev"
        assert scopes["vue"] == "required"
        assert scopes["fsevents"] == "optional"

    def test_malformed_json_skipped(self, tmp_path: Path) -> None:
        plugin = _make_plugin(tmp_path, {"package.json": "{not json"})
        deps = list(iter_dependencies(plugin))
        assert deps == []


class TestRequirementsTxt:
    def test_pinned_dep(self, tmp_path: Path) -> None:
        plugin = _make_plugin(tmp_path, {"requirements.txt": "requests==2.31.0\n"})
        deps = list(iter_dependencies(plugin))
        assert len(deps) == 1
        assert deps[0].name == "requests"
        assert deps[0].version == "2.31.0"
        assert deps[0].ecosystem == "pypi"

    def test_unpinned_dep(self, tmp_path: Path) -> None:
        plugin = _make_plugin(tmp_path, {"requirements.txt": "flask>=2.0\n"})
        deps = list(iter_dependencies(plugin))
        assert len(deps) == 1
        assert deps[0].name == "flask"
        assert deps[0].version is None  # only `==` produces a version

    def test_comments_and_blank_lines(self, tmp_path: Path) -> None:
        plugin = _make_plugin(
            tmp_path,
            {
                "requirements.txt": "# header\nflask==2.0  # inline comment\n\n-r other.txt\n",
            },
        )
        deps = list(iter_dependencies(plugin))
        names = {d.name for d in deps}
        assert names == {"flask"}

    def test_extras_and_markers(self, tmp_path: Path) -> None:
        plugin = _make_plugin(
            tmp_path,
            {
                "requirements.txt": "uvicorn[standard]==0.27.0\nrequests==2.31.0; python_version >= '3.7'\n",
            },
        )
        deps = list(iter_dependencies(plugin))
        names = {d.name for d in deps}
        assert "uvicorn" in names
        assert "requests" in names

    def test_url_installs_skipped(self, tmp_path: Path) -> None:
        plugin = _make_plugin(
            tmp_path,
            {
                "requirements.txt": "https://example.com/pkg.tar.gz\ngit+https://github.com/x/y.git\n-e .\n",
            },
        )
        assert list(iter_dependencies(plugin)) == []

    def test_multiple_requirements_files(self, tmp_path: Path) -> None:
        plugin = _make_plugin(
            tmp_path,
            {
                "requirements.txt": "flask==2.0\n",
                "requirements-dev.txt": "pytest==7.0\n",
            },
        )
        deps = list(iter_dependencies(plugin))
        names = {d.name for d in deps}
        assert names == {"flask", "pytest"}


class TestPyprojectToml:
    def test_pep621_deps(self, tmp_path: Path) -> None:
        plugin = _make_plugin(
            tmp_path,
            {
                "pyproject.toml": (
                    '[project]\nname = "demo"\nversion = "1.0"\n'
                    'dependencies = ["requests==2.31", "flask>=2.0"]\n'
                    '[project.optional-dependencies]\ndev = ["pytest==7.0"]\n'
                ),
            },
        )
        deps = list(iter_dependencies(plugin))
        names = {d.name for d in deps}
        assert names == {"requests", "flask", "pytest"}
        scopes = {d.name: d.scope for d in deps}
        assert scopes["pytest"] == "optional"

    def test_poetry_style(self, tmp_path: Path) -> None:
        plugin = _make_plugin(
            tmp_path,
            {
                "pyproject.toml": (
                    '[tool.poetry.dependencies]\npython = "^3.10"\nrequests = "2.31.0"\n'
                    '[tool.poetry.dev-dependencies]\npytest = "7.0"\n'
                ),
            },
        )
        deps = list(iter_dependencies(plugin))
        names = {d.name for d in deps}
        assert names == {"requests", "pytest"}
        # `python = "^3.10"` is excluded
        assert "python" not in names

    def test_malformed_toml_skipped(self, tmp_path: Path) -> None:
        plugin = _make_plugin(tmp_path, {"pyproject.toml": "[invalid toml"})
        assert list(iter_dependencies(plugin)) == []


class TestCargoToml:
    def test_dep_extraction(self, tmp_path: Path) -> None:
        plugin = _make_plugin(
            tmp_path,
            {
                "Cargo.toml": (
                    '[package]\nname = "x"\nversion = "0.1.0"\n'
                    '[dependencies]\nserde = "1.0"\ntokio = { version = "1", features = ["full"] }\n'
                    '[dev-dependencies]\ncriterion = "0.5"\n'
                ),
            },
        )
        deps = list(iter_dependencies(plugin))
        names = {d.name for d in deps}
        assert names == {"serde", "tokio", "criterion"}
        ecosystems = {d.ecosystem for d in deps}
        assert ecosystems == {"cargo"}


class TestGoMod:
    def test_block_require(self, tmp_path: Path) -> None:
        plugin = _make_plugin(
            tmp_path,
            {
                "go.mod": (
                    "module demo\n\ngo 1.21\n\nrequire (\n"
                    "    github.com/gin-gonic/gin v1.9.0\n"
                    "    github.com/spf13/cobra v1.7.0 // direct\n"
                    ")\n"
                ),
            },
        )
        deps = list(iter_dependencies(plugin))
        names = {d.name for d in deps}
        assert names == {"github.com/gin-gonic/gin", "github.com/spf13/cobra"}


# -----------------------------------------------------------------------------
# Skip directories
# -----------------------------------------------------------------------------


class TestSkipDirs:
    def test_node_modules_ignored(self, tmp_path: Path) -> None:
        plugin = _make_plugin(
            tmp_path,
            {
                "package.json": json.dumps({"name": "x", "dependencies": {"a": "1.0"}}),
                "node_modules/foo/package.json": json.dumps({"name": "foo", "dependencies": {"b": "2.0"}}),
            },
        )
        deps = list(iter_dependencies(plugin))
        names = {d.name for d in deps}
        assert names == {"a"}

    def test_dev_folder_ignored(self, tmp_path: Path) -> None:
        plugin = _make_plugin(
            tmp_path,
            {
                "package.json": json.dumps({"name": "x", "dependencies": {"a": "1.0"}}),
                "scripts_dev/package.json": json.dumps({"dependencies": {"b": "2.0"}}),
            },
        )
        deps = list(iter_dependencies(plugin))
        names = {d.name for d in deps}
        assert names == {"a"}


# -----------------------------------------------------------------------------
# purl encoding
# -----------------------------------------------------------------------------


class TestPurl:
    @pytest.mark.parametrize(
        "eco,name,version,expected",
        [
            ("npm", "react", "18.0.0", "pkg:npm/react@18.0.0"),
            ("pypi", "requests", "2.31.0", "pkg:pypi/requests@2.31.0"),
            ("npm", "@scope/pkg", "1.0", "pkg:npm/%40scope%2Fpkg@1.0"),
            ("npm", "react", "^18.0.0", "pkg:npm/react@18.0.0"),  # ^ stripped
            ("pypi", "flask", ">=2.0", "pkg:pypi/flask@2.0"),
            ("cargo", "serde", "1.0.0", "pkg:cargo/serde@1.0.0"),
            ("npm", "react", None, "pkg:npm/react"),
        ],
    )
    def test_purl_format(self, eco: str, name: str, version: str | None, expected: str) -> None:
        dep = Dependency(eco, name, version, "required", "package.json")
        assert to_purl(dep) == expected


# -----------------------------------------------------------------------------
# Full SBOM generation
# -----------------------------------------------------------------------------


class TestSbomGeneration:
    def test_top_level_shape(self, tmp_path: Path) -> None:
        plugin = _make_plugin(
            tmp_path,
            {
                "package.json": json.dumps({"name": "x", "dependencies": {"react": "18.0.0"}}),
            },
        )
        sbom = generate_sbom(plugin, tool_version="2.27.0")
        assert sbom["bomFormat"] == CYCLONEDX_FORMAT
        assert sbom["specVersion"] == CYCLONEDX_SPEC_VERSION
        assert sbom["version"] == 1
        assert sbom["serialNumber"].startswith("urn:uuid:")

    def test_metadata_uses_plugin_manifest(self, tmp_path: Path) -> None:
        plugin = _make_plugin(tmp_path, {})
        sbom = generate_sbom(plugin)
        assert sbom["metadata"]["component"]["name"] == "demo"
        assert sbom["metadata"]["component"]["version"] == "1.2.3"

    def test_tool_block(self, tmp_path: Path) -> None:
        plugin = _make_plugin(tmp_path, {})
        sbom = generate_sbom(plugin, tool_version="9.9.9")
        tools = sbom["metadata"]["tools"]["components"]
        assert tools[0]["name"] == "claude-plugins-validation"
        assert tools[0]["version"] == "9.9.9"

    def test_component_shape(self, tmp_path: Path) -> None:
        plugin = _make_plugin(
            tmp_path,
            {
                "package.json": json.dumps({"dependencies": {"react": "18.0.0"}}),
            },
        )
        sbom = generate_sbom(plugin)
        comp = sbom["components"][0]
        assert comp["type"] == "library"
        assert comp["name"] == "react"
        assert comp["version"] == "18.0.0"
        assert comp["purl"] == "pkg:npm/react@18.0.0"
        assert comp["scope"] == "required"
        assert any(p["name"] == "cpv:ecosystem" and p["value"] == "npm" for p in comp["properties"])
        assert comp["evidence"]["occurrences"][0]["location"] == "package.json"

    def test_dev_scope_marked_optional(self, tmp_path: Path) -> None:
        plugin = _make_plugin(
            tmp_path,
            {
                "package.json": json.dumps({"devDependencies": {"jest": "29.0.0"}}),
            },
        )
        sbom = generate_sbom(plugin)
        comp = sbom["components"][0]
        assert comp["scope"] == "optional"

    def test_empty_plugin_no_components(self, tmp_path: Path) -> None:
        plugin = _make_plugin(tmp_path, {})
        sbom = generate_sbom(plugin)
        assert sbom["components"] == []


# -----------------------------------------------------------------------------
# Round-trip via write_sbom
# -----------------------------------------------------------------------------


class TestWriteSbom:
    def test_writes_valid_json(self, tmp_path: Path) -> None:
        plugin = _make_plugin(
            tmp_path,
            {
                "package.json": json.dumps({"dependencies": {"react": "18"}}),
                "requirements.txt": "flask==2.0\n",
            },
        )
        out = tmp_path / "sbom.json"
        result_path = write_sbom(plugin, out, tool_version="2.27.0")
        assert result_path == out.resolve()
        assert out.exists()
        loaded = json.loads(out.read_text())
        assert loaded["bomFormat"] == "CycloneDX"
        assert loaded["specVersion"] == "1.6"
        names = {c["name"] for c in loaded["components"]}
        assert names == {"react", "flask"}

    def test_creates_parent_dir(self, tmp_path: Path) -> None:
        plugin = _make_plugin(tmp_path, {})
        nested = tmp_path / "a" / "b" / "sbom.json"
        write_sbom(plugin, nested)
        assert nested.exists()


# -----------------------------------------------------------------------------
# Cross-ecosystem combination
# -----------------------------------------------------------------------------


class TestMultiEcosystem:
    def test_all_three_ecosystems(self, tmp_path: Path) -> None:
        plugin = _make_plugin(
            tmp_path,
            {
                "package.json": json.dumps({"dependencies": {"react": "18"}}),
                "requirements.txt": "flask==2.0\n",
                "Cargo.toml": '[package]\nname = "x"\nversion = "0.1"\n[dependencies]\nserde = "1.0"\n',
            },
        )
        sbom = generate_sbom(plugin)
        ecosystems = {p["value"] for c in sbom["components"] for p in c["properties"] if p["name"] == "cpv:ecosystem"}
        assert ecosystems == {"npm", "pypi", "cargo"}
