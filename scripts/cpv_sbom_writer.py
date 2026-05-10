#!/usr/bin/env python3
"""CycloneDX 1.6 SBOM emitter for CPV plugins (RC-106).

Reads declared dependencies from a plugin's manifest files and emits a
CycloneDX 1.6 JSON Software Bill of Materials. CycloneDX is the OWASP
SBOM standard and is consumed by GitHub dependency graph, Snyk,
Dependency-Track, and most SBOM-aware supply-chain tools.

Manifest sources:
    package.json       — npm (dependencies, devDependencies,
                                peerDependencies, optionalDependencies)
    requirements*.txt  — pypi (one dep per line, version specifiers stripped)
    pyproject.toml     — pypi (project.dependencies, project.optional-dependencies,
                                tool.poetry.dependencies, tool.poetry.dev-dependencies)
    Cargo.toml         — cargo (dependencies, dev-dependencies, build-dependencies)
    go.mod             — golang (require directives)

Schema reference:
    https://cyclonedx.org/docs/1.6/json/
    https://github.com/package-url/purl-spec
"""

from __future__ import annotations

import json
import re
import tomllib
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import quote

CYCLONEDX_SPEC_VERSION = "1.6"
CYCLONEDX_FORMAT = "CycloneDX"

# ecosystem → purl type
_PURL_TYPE = {
    "npm": "npm",
    "pypi": "pypi",
    "cargo": "cargo",
    "golang": "golang",
}


@dataclass(frozen=True, slots=True)
class Dependency:
    """One declared dependency lifted from a manifest file."""

    ecosystem: str  # npm | pypi | cargo | golang
    name: str
    version: str | None  # raw version-spec string (may be None)
    scope: str  # required | optional | dev
    manifest: str  # relative manifest path (for evidence)


# -----------------------------------------------------------------------------
# Per-manifest parsers
# -----------------------------------------------------------------------------


def _parse_package_json(text: str, manifest_rel: str) -> Iterable[Dependency]:
    try:
        pkg = json.loads(text)
    except json.JSONDecodeError:
        return
    if not isinstance(pkg, dict):
        return
    section_scope = {
        "dependencies": "required",
        "peerDependencies": "required",
        "optionalDependencies": "optional",
        "devDependencies": "dev",
    }
    for section, scope in section_scope.items():
        deps = pkg.get(section)
        if not isinstance(deps, dict):
            continue
        for name, version in deps.items():
            if not isinstance(name, str) or not name:
                continue
            ver = version if isinstance(version, str) else None
            yield Dependency("npm", name, ver, scope, manifest_rel)


def _parse_requirements_txt(text: str, manifest_rel: str) -> Iterable[Dependency]:
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith(("-r ", "--", "-")):
            continue
        # Strip URL-installs and editable installs
        if line.startswith(("http://", "https://", "git+", "file:", "-e ")):
            continue
        # Parse name and version
        m = re.match(r"^([A-Za-z0-9._-]+)(\[[^\]]*\])?\s*([<>=!~]=?)?\s*([^;\s]+)?", line)
        if not m:
            continue
        name = m.group(1)
        op = m.group(3) or ""
        ver = m.group(4) if op == "==" else None
        yield Dependency("pypi", name, ver, "required", manifest_rel)


def _parse_pyproject_toml(text: str, manifest_rel: str) -> Iterable[Dependency]:
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return
    # PEP 621 project.dependencies
    project = data.get("project", {})
    if isinstance(project, dict):
        deps = project.get("dependencies", [])
        if isinstance(deps, list):
            for dep_str in deps:
                if not isinstance(dep_str, str):
                    continue
                yield from _parse_pep508(dep_str, "required", manifest_rel)
        opt_deps = project.get("optional-dependencies", {})
        if isinstance(opt_deps, dict):
            for dep_list in opt_deps.values():
                if not isinstance(dep_list, list):
                    continue
                for dep_str in dep_list:
                    if isinstance(dep_str, str):
                        yield from _parse_pep508(dep_str, "optional", manifest_rel)
    # Poetry-style
    tool = data.get("tool", {})
    poetry = tool.get("poetry", {}) if isinstance(tool, dict) else {}
    if isinstance(poetry, dict):
        for section, scope in (("dependencies", "required"), ("dev-dependencies", "dev")):
            sec = poetry.get(section, {})
            if not isinstance(sec, dict):
                continue
            for name, spec in sec.items():
                if not isinstance(name, str) or name == "python":
                    continue
                ver = spec if isinstance(spec, str) else (spec.get("version") if isinstance(spec, dict) else None)
                yield Dependency("pypi", name, ver, scope, manifest_rel)


def _parse_pep508(spec: str, scope: str, manifest_rel: str) -> Iterable[Dependency]:
    """Parse a PEP-508 dependency string like 'requests>=2.0; python_version >= "3.7"'."""
    # Strip environment markers
    spec_no_marker = spec.split(";", 1)[0].strip()
    if not spec_no_marker:
        return
    m = re.match(r"^([A-Za-z0-9._-]+)(\[[^\]]*\])?\s*([<>=!~]=?)?\s*([^,\s]+)?", spec_no_marker)
    if not m:
        return
    name = m.group(1)
    op = m.group(3) or ""
    ver = m.group(4) if op == "==" else None
    yield Dependency("pypi", name, ver, scope, manifest_rel)


def _parse_cargo_toml(text: str, manifest_rel: str) -> Iterable[Dependency]:
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return
    section_scope = {
        "dependencies": "required",
        "dev-dependencies": "dev",
        "build-dependencies": "required",
    }
    for section, scope in section_scope.items():
        deps = data.get(section, {})
        if not isinstance(deps, dict):
            continue
        for name, spec in deps.items():
            if not isinstance(name, str):
                continue
            ver = spec if isinstance(spec, str) else (spec.get("version") if isinstance(spec, dict) else None)
            yield Dependency("cargo", name, ver, scope, manifest_rel)


def _parse_go_mod(text: str, manifest_rel: str) -> Iterable[Dependency]:
    in_block = False
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("//"):
            continue
        if line.startswith("require ("):
            in_block = True
            continue
        if in_block and line == ")":
            in_block = False
            continue
        if line.startswith("require ") or in_block:
            content = line[len("require ") :] if line.startswith("require ") else line
            content = content.split("//", 1)[0].strip()
            parts = content.split()
            if len(parts) >= 2:
                yield Dependency("golang", parts[0], parts[1], "required", manifest_rel)


# -----------------------------------------------------------------------------
# Aggregator
# -----------------------------------------------------------------------------


_PARSERS = {
    "package.json": _parse_package_json,
    "pyproject.toml": _parse_pyproject_toml,
    "Cargo.toml": _parse_cargo_toml,
    "go.mod": _parse_go_mod,
}


def iter_dependencies(plugin_path: Path) -> Iterable[Dependency]:
    """Walk a plugin tree and yield every declared dependency.

    Skips standard ignore directories (node_modules, .venv, .git, dist, build,
    __pycache__, _dev folders) so vendored copies don't double-count.
    """
    plugin_path = plugin_path.resolve()
    skip_dirs = {
        "node_modules",
        ".venv",
        ".git",
        "dist",
        "build",
        "__pycache__",
        ".tox",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        "vendor",
        "target",
    }
    skip_suffixes = ("_dev",)

    seen: set[tuple[str, str, str | None, str]] = set()  # (eco, name, ver, manifest)

    for entry in plugin_path.rglob("*"):
        if not entry.is_file():
            continue
        # Skip if any path component is in the ignore set
        parts = entry.relative_to(plugin_path).parts
        if any(p in skip_dirs or p.endswith(skip_suffixes) for p in parts[:-1]):
            continue

        name = entry.name
        parser = _PARSERS.get(name)
        if parser is None and (name.startswith("requirements") and name.endswith(".txt")):
            parser = _parse_requirements_txt
        if parser is None:
            continue

        try:
            text = entry.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        rel = str(entry.relative_to(plugin_path))
        for dep in parser(text, rel):
            key = (dep.ecosystem, dep.name.lower(), dep.version, dep.manifest)
            if key in seen:
                continue
            seen.add(key)
            yield dep


# -----------------------------------------------------------------------------
# CycloneDX assembly
# -----------------------------------------------------------------------------


def to_purl(dep: Dependency) -> str:
    """Build a Package URL for a Dependency.

    https://github.com/package-url/purl-spec/blob/master/PURL-SPEC.rst
    """
    purl_type = _PURL_TYPE.get(dep.ecosystem, dep.ecosystem)
    name_enc = quote(dep.name, safe="")
    if dep.version:
        # Strip leading version-spec operators like ^, ~, >=, ==
        clean = re.sub(r"^[<>=!~^]+\s*", "", dep.version).strip()
        if clean:
            return f"pkg:{purl_type}/{name_enc}@{quote(clean, safe='.')}"
    return f"pkg:{purl_type}/{name_enc}"


def _component_for(dep: Dependency) -> dict[str, Any]:
    component: dict[str, Any] = {
        "type": "library",
        "name": dep.name,
        "purl": to_purl(dep),
        "scope": "optional" if dep.scope in ("dev", "optional") else "required",
        "evidence": {"occurrences": [{"location": dep.manifest}]},
        "properties": [
            {"name": "cpv:ecosystem", "value": dep.ecosystem},
            {"name": "cpv:scope", "value": dep.scope},
        ],
    }
    if dep.version:
        component["version"] = dep.version
    return component


def generate_sbom(
    plugin_path: Path,
    plugin_name: str | None = None,
    plugin_version: str | None = None,
    tool_name: str = "claude-plugins-validation",
    tool_version: str = "0.0.0",
) -> dict[str, Any]:
    """Build a complete CycloneDX 1.6 SBOM dict for the plugin."""
    components = [_component_for(d) for d in iter_dependencies(plugin_path)]

    # Try to read plugin name + version from manifest if not given
    if plugin_name is None or plugin_version is None:
        manifest = plugin_path / ".claude-plugin" / "plugin.json"
        if manifest.is_file():
            try:
                data = json.loads(manifest.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    plugin_name = plugin_name or str(data.get("name", plugin_path.name))
                    plugin_version = plugin_version or str(data.get("version", "0.0.0"))
            except (json.JSONDecodeError, OSError, UnicodeDecodeError):
                pass
    plugin_name = plugin_name or plugin_path.name
    plugin_version = plugin_version or "0.0.0"

    return {
        "bomFormat": CYCLONEDX_FORMAT,
        "specVersion": CYCLONEDX_SPEC_VERSION,
        "serialNumber": f"urn:uuid:{uuid.uuid4()}",
        "version": 1,
        "metadata": {
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "tools": {
                "components": [
                    {
                        "type": "application",
                        "name": tool_name,
                        "version": tool_version,
                    }
                ]
            },
            "component": {
                "type": "application",
                "name": plugin_name,
                "version": plugin_version,
                "bom-ref": f"plugin:{plugin_name}@{plugin_version}",
            },
        },
        "components": components,
    }


def write_sbom(
    plugin_path: Path,
    output_path: Path,
    plugin_name: str | None = None,
    plugin_version: str | None = None,
    tool_name: str = "claude-plugins-validation",
    tool_version: str = "0.0.0",
) -> Path:
    """Generate and serialize a CycloneDX SBOM. Returns the resolved output path."""
    sbom = generate_sbom(
        plugin_path,
        plugin_name=plugin_name,
        plugin_version=plugin_version,
        tool_name=tool_name,
        tool_version=tool_version,
    )
    output_path = output_path.expanduser()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(sbom, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return output_path.resolve()
