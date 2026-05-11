#!/usr/bin/env python3
"""Fixture-grid generator for the CPV vs Claude CLI coverage-surface audit.

Why this exists:
    The audit (TRDD-b4c6cbe7) needs a deterministic set of ~30 plugin
    fixtures that cover every spec field, source type, layout (A/B/C),
    hook event × type, agent variation, skill variation, mcpServers
    shape, and lspServers shape. Hand-rolling 30 directory trees is
    bug-prone and tedious to keep in sync with the spec — so we
    generate them.

How it works:
    Each fixture spec is a dataclass describing the plugin.json (and
    optionally marketplace.json, agents/, skills/, hooks/) content.
    `materialize_all()` writes one directory per fixture under the
    grid root.

Invariants:
    - Every fixture lives at <grid_root>/<NN>-<descriptor>/
    - Every fixture has at least .claude-plugin/plugin.json
    - Fixture NNs are zero-padded so `ls` sorts correctly
    - Re-running the generator is idempotent — it wipes and regenerates

Public API:
    GRID_FIXTURES: tuple[FixtureSpec, ...] — the 30 specs
    materialize_all(grid_root: Path) -> list[Path] — write all fixtures
    materialize_one(spec: FixtureSpec, grid_root: Path) -> Path — write one
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FixtureFile:
    """A single file inside a fixture plugin tree.

    `path` is plugin-relative (e.g. ".claude-plugin/plugin.json").
    `content` is the literal file body (text, will be utf-8 encoded).
    """

    path: str
    content: str


@dataclass(frozen=True)
class FixtureSpec:
    """One fixture-grid entry.

    Attributes:
        nn: Zero-padded sequence number used as filename prefix.
        descriptor: Kebab-case short label of what this fixture exercises.
        plugin_json: The plugin.json body as a Python dict. None means
            "do not emit plugin.json" (used to test missing-manifest paths).
        marketplace_json: Optional marketplace.json body (Layout C plugins).
        extra_files: Additional fixture files (agents, skills, hooks, etc.).
        notes: Free-form description of what the fixture is exercising.
    """

    nn: str
    descriptor: str
    plugin_json: dict[str, object] | None
    marketplace_json: dict[str, object] | None = None
    extra_files: tuple[FixtureFile, ...] = field(default_factory=tuple)
    notes: str = ""

    @property
    def dir_name(self) -> str:
        """Stable directory name like `01-bare-minimum`."""
        return f"{self.nn}-{self.descriptor}"


# ---------------------------------------------------------------------------
# Reusable fragments
# ---------------------------------------------------------------------------

_VALID_AGENT_BODY = """---
name: example-agent
description: Tests an example workflow against the local environment.
---

# Example agent

This agent body exists only to give the plugin a valid agent target.
"""

_VALID_SKILL_BODY = """---
name: example-skill
description: Demonstrates a minimal skill body for fixture-grid purposes.
user-invocable: true
---

# Example skill

Body content for the example skill — exists to satisfy fixture parsing only.
"""

_VALID_HOOK_SCRIPT = """#!/usr/bin/env bash
# Minimal hook body — exits 0 with no side effects.
exit 0
"""


# ---------------------------------------------------------------------------
# Fixture grid (30 entries) — see TRDD §4.1 for coverage rationale
# ---------------------------------------------------------------------------


def _build_grid() -> tuple[FixtureSpec, ...]:
    """Construct the 30 fixture specs.

    Coverage targets (one fixture per row at minimum):
        01-04: Bare minima + extreme edges (missing manifest, just version, etc.)
        05-09: Source types (relative-path, github, url, git-subdir, npm)
        10-12: Layouts (A=plugin-only, B=plugin-in-monorepo, C=marketplace-in-plugin)
        13-17: Hook events × types (5 representative combos)
        18-20: Agent frontmatter variations (valid, missing-name, hex-color)
        21-23: Skill variations (valid, $<name>-undeclared, paths-shape)
        24-26: mcpServers shapes (stdio, http, mcp-no-command)
        27-28: lspServers shapes (basic, malformed)
        29: monitors field (v2.1.105)
        30: outputStyle field
    """
    grid: list[FixtureSpec] = []

    # 01: bare minimum valid
    grid.append(
        FixtureSpec(
            nn="01",
            descriptor="bare-minimum-valid",
            plugin_json={"name": "fixture-01", "version": "1.0.0"},
            notes="Smallest possible passing plugin.json — only name+version.",
        )
    )

    # 02: missing name (CRITICAL)
    grid.append(
        FixtureSpec(
            nn="02",
            descriptor="missing-name",
            plugin_json={"version": "1.0.0"},
            notes="Missing required `name` field — both CLI and CPV must flag.",
        )
    )

    # 03: invalid version (not semver)
    grid.append(
        FixtureSpec(
            nn="03",
            descriptor="invalid-semver",
            plugin_json={"name": "fixture-03", "version": "not-semver"},
            notes="Non-semver version — strict semver enforcement check.",
        )
    )

    # 04: extra unknown root key (the `cpv` case we saw on master)
    grid.append(
        FixtureSpec(
            nn="04",
            descriptor="unknown-root-key",
            plugin_json={
                "name": "fixture-04",
                "version": "1.0.0",
                "cpv": {"score": 100},
            },
            notes="Unknown root key `cpv` — CLI flags; CPV currently silent.",
        )
    )

    # 05: marketplace plugin entry — relative-path source
    grid.append(
        FixtureSpec(
            nn="05",
            descriptor="marketplace-source-relative-path",
            plugin_json={"name": "fixture-05", "version": "1.0.0"},
            marketplace_json={
                "name": "fixture-05-marketplace",
                "owner": {"name": "Test"},
                "plugins": [
                    {"name": "fixture-05", "source": {"source": "./", "type": "relative-path"}},
                ],
            },
            notes="Layout C marketplace with relative-path source pointing to self.",
        )
    )

    # 06: github source
    grid.append(
        FixtureSpec(
            nn="06",
            descriptor="marketplace-source-github",
            plugin_json={"name": "fixture-06", "version": "1.0.0"},
            marketplace_json={
                "name": "fixture-06-marketplace",
                "owner": {"name": "Test"},
                "plugins": [
                    {"name": "fixture-06", "source": {"source": "github", "repo": "Emasoft/fixture-06"}},
                ],
            },
            notes="GitHub source — most common multi-plugin marketplace shape.",
        )
    )

    # 07: url source
    grid.append(
        FixtureSpec(
            nn="07",
            descriptor="marketplace-source-url",
            plugin_json={"name": "fixture-07", "version": "1.0.0"},
            marketplace_json={
                "name": "fixture-07-marketplace",
                "owner": {"name": "Test"},
                "plugins": [
                    {
                        "name": "fixture-07",
                        "source": {"source": "url", "url": "https://example.com/fixture.tgz"},
                    },
                ],
            },
            notes="URL source — tarball-style remote plugin.",
        )
    )

    # 08: git-subdir source
    grid.append(
        FixtureSpec(
            nn="08",
            descriptor="marketplace-source-git-subdir",
            plugin_json={"name": "fixture-08", "version": "1.0.0"},
            marketplace_json={
                "name": "fixture-08-marketplace",
                "owner": {"name": "Test"},
                "plugins": [
                    {
                        "name": "fixture-08",
                        "source": {
                            "source": "git-subdir",
                            "url": "https://github.com/Emasoft/monorepo.git",
                            "subdir": "plugins/fixture-08",
                        },
                    },
                ],
            },
            notes="git-subdir source — plugin nested in a larger repo.",
        )
    )

    # 09: npm source
    grid.append(
        FixtureSpec(
            nn="09",
            descriptor="marketplace-source-npm",
            plugin_json={"name": "fixture-09", "version": "1.0.0"},
            marketplace_json={
                "name": "fixture-09-marketplace",
                "owner": {"name": "Test"},
                "plugins": [
                    {"name": "fixture-09", "source": {"source": "npm", "package": "@scope/fixture-09"}},
                ],
            },
            notes="NPM source — plugin distributed via npm registry.",
        )
    )

    # 10: Layout A (separate plugin repo, no marketplace.json)
    grid.append(
        FixtureSpec(
            nn="10",
            descriptor="layout-a-plugin-only",
            plugin_json={"name": "fixture-10", "version": "1.0.0"},
            notes="Layout A — single plugin repo with no marketplace manifest.",
        )
    )

    # 11: Layout B (plugin in monorepo, parent marketplace.json) — emitted as
    # a sentinel marker. The actual marketplace.json lives at the grid root
    # so this fixture stays scoped to plugin.json only.
    grid.append(
        FixtureSpec(
            nn="11",
            descriptor="layout-b-monorepo-plugin",
            plugin_json={"name": "fixture-11", "version": "1.0.0"},
            notes="Layout B — plugin nested in a marketplace monorepo (parent has marketplace.json, child does not).",
        )
    )

    # 12: Layout C (marketplace-in-plugin)
    grid.append(
        FixtureSpec(
            nn="12",
            descriptor="layout-c-marketplace-in-plugin",
            plugin_json={"name": "fixture-12", "version": "1.0.0"},
            marketplace_json={
                "name": "fixture-12",
                "owner": {"name": "Test"},
                "plugins": [
                    {"name": "fixture-12", "source": {"source": "./", "type": "relative-path"}},
                ],
            },
            notes="Layout C — single repo with both plugin.json and marketplace.json at root.",
        )
    )

    # 13-17: hook events × types
    grid.append(
        FixtureSpec(
            nn="13",
            descriptor="hook-pretooluse-command",
            plugin_json={
                "name": "fixture-13",
                "version": "1.0.0",
                "hooks": {
                    "PreToolUse": [
                        {"type": "command", "command": "./hooks/pre.sh", "matcher": "Bash"},
                    ],
                },
            },
            extra_files=(FixtureFile(path="hooks/pre.sh", content=_VALID_HOOK_SCRIPT),),
            notes="PreToolUse with command type — most common hook shape.",
        )
    )

    grid.append(
        FixtureSpec(
            nn="14",
            descriptor="hook-stop-prompt",
            plugin_json={
                "name": "fixture-14",
                "version": "1.0.0",
                "hooks": {
                    "Stop": [
                        {"type": "prompt", "prompt": "Wrap up the session and summarize."},
                    ],
                },
            },
            notes="Stop hook with prompt type — text-based hook injection.",
        )
    )

    grid.append(
        FixtureSpec(
            nn="15",
            descriptor="hook-sessionstart-mcp-tool",
            plugin_json={
                "name": "fixture-15",
                "version": "1.0.0",
                "hooks": {
                    "SessionStart": [
                        {
                            "type": "mcp_tool",
                            "server": "demo-server",
                            "tool": "noop",
                            "input": {"arg": "value"},
                        },
                    ],
                },
                "mcpServers": {
                    "demo-server": {
                        "command": "/bin/true",
                    },
                },
            },
            notes="SessionStart with mcp_tool type (v2.1.118+) — exercises new hook surface.",
        )
    )

    grid.append(
        FixtureSpec(
            nn="16",
            descriptor="hook-userpromptsubmit-http",
            plugin_json={
                "name": "fixture-16",
                "version": "1.0.0",
                "hooks": {
                    "UserPromptSubmit": [
                        {"type": "http", "url": "https://example.com/hook", "method": "POST"},
                    ],
                },
            },
            notes="UserPromptSubmit with http hook type — webhook integration.",
        )
    )

    grid.append(
        FixtureSpec(
            nn="17",
            descriptor="hook-precompact-agent",
            plugin_json={
                "name": "fixture-17",
                "version": "1.0.0",
                "hooks": {
                    "PreCompact": [
                        {"type": "agent", "agent": "summary-agent"},
                    ],
                },
                "agents": ["./agents/summary-agent.md"],
            },
            extra_files=(
                FixtureFile(
                    path="agents/summary-agent.md",
                    content=_VALID_AGENT_BODY.replace("example-agent", "summary-agent"),
                ),
            ),
            notes="PreCompact with agent hook type — cross-references an agent target.",
        )
    )

    # 18-20: agent frontmatter
    grid.append(
        FixtureSpec(
            nn="18",
            descriptor="agent-frontmatter-valid",
            plugin_json={"name": "fixture-18", "version": "1.0.0", "agents": ["./agents/a.md"]},
            extra_files=(FixtureFile(path="agents/a.md", content=_VALID_AGENT_BODY),),
            notes="Valid agent — name+description present.",
        )
    )

    grid.append(
        FixtureSpec(
            nn="19",
            descriptor="agent-frontmatter-missing-name",
            plugin_json={"name": "fixture-19", "version": "1.0.0", "agents": ["./agents/b.md"]},
            extra_files=(
                FixtureFile(
                    path="agents/b.md",
                    content="---\ndescription: Missing name field on purpose.\n---\n\n# body\n",
                ),
            ),
            notes="Agent frontmatter without `name` — CLI may emit a warning.",
        )
    )

    grid.append(
        FixtureSpec(
            nn="20",
            descriptor="agent-frontmatter-hex-color",
            plugin_json={"name": "fixture-20", "version": "1.0.0", "agents": ["./agents/c.md"]},
            extra_files=(
                FixtureFile(
                    path="agents/c.md",
                    content='---\nname: hex-color-agent\ndescription: Tests hex color frontmatter.\ncolor: "#FF5733"\n---\n\n# body\n',
                ),
            ),
            notes="Agent color as hex code — CPV nudges to named color (NIT).",
        )
    )

    # 21-23: skill variations
    grid.append(
        FixtureSpec(
            nn="21",
            descriptor="skill-valid",
            plugin_json={"name": "fixture-21", "version": "1.0.0", "skills": "./skills/"},
            extra_files=(FixtureFile(path="skills/example-skill/SKILL.md", content=_VALID_SKILL_BODY),),
            notes="Valid skill — minimal frontmatter.",
        )
    )

    grid.append(
        FixtureSpec(
            nn="22",
            descriptor="skill-undeclared-named-arg",
            plugin_json={"name": "fixture-22", "version": "1.0.0", "skills": "./skills/"},
            extra_files=(
                FixtureFile(
                    path="skills/bad-skill/SKILL.md",
                    content=(
                        "---\n"
                        "name: bad-skill\n"
                        "description: Uses $<undeclared> without declaring arguments.\n"
                        "user-invocable: true\n"
                        "---\n\n"
                        "# bad skill\n\n"
                        "Use $<undeclared> here — should fail validation.\n"
                    ),
                ),
            ),
            notes="Skill uses $<undeclared> but declares no `arguments:` — CPV must flag.",
        )
    )

    grid.append(
        FixtureSpec(
            nn="23",
            descriptor="skill-paths-shape",
            plugin_json={"name": "fixture-23", "version": "1.0.0", "skills": "./skills/"},
            extra_files=(
                FixtureFile(
                    path="skills/paths-skill/SKILL.md",
                    content=(
                        "---\n"
                        "name: paths-skill\n"
                        "description: Tests the paths frontmatter field.\n"
                        "user-invocable: true\n"
                        "paths:\n"
                        "  - ./resources/data.json\n"
                        "  - ./resources/template.md\n"
                        "---\n\n"
                        "# paths skill\n"
                    ),
                ),
                FixtureFile(path="skills/paths-skill/resources/data.json", content="{}\n"),
                FixtureFile(path="skills/paths-skill/resources/template.md", content="# template\n"),
            ),
            notes="Skill with `paths:` array — exercises path-resolution rules.",
        )
    )

    # 24-26: mcpServers shapes
    grid.append(
        FixtureSpec(
            nn="24",
            descriptor="mcpserver-stdio",
            plugin_json={
                "name": "fixture-24",
                "version": "1.0.0",
                "mcpServers": {"stdio-srv": {"command": "/usr/bin/true", "args": ["--quiet"]}},
            },
            notes="MCP server stdio shape — command+args.",
        )
    )

    grid.append(
        FixtureSpec(
            nn="25",
            descriptor="mcpserver-http",
            plugin_json={
                "name": "fixture-25",
                "version": "1.0.0",
                "mcpServers": {"http-srv": {"url": "https://api.example.com/mcp", "type": "http"}},
            },
            notes="MCP server http shape — url-based remote MCP.",
        )
    )

    grid.append(
        FixtureSpec(
            nn="26",
            descriptor="mcpserver-no-command",
            plugin_json={
                "name": "fixture-26",
                "version": "1.0.0",
                "mcpServers": {"broken-srv": {"args": ["--no-command"]}},
            },
            notes="MCP server missing required command/url — both CLI and CPV must flag.",
        )
    )

    # 27-28: lspServers shapes
    grid.append(
        FixtureSpec(
            nn="27",
            descriptor="lspserver-basic",
            plugin_json={
                "name": "fixture-27",
                "version": "1.0.0",
                "lspServers": {
                    "py-lsp": {
                        "command": "pylsp",
                        "languages": ["python"],
                    },
                },
            },
            notes="LSP server basic shape — command+languages.",
        )
    )

    grid.append(
        FixtureSpec(
            nn="28",
            descriptor="lspserver-malformed",
            plugin_json={
                "name": "fixture-28",
                "version": "1.0.0",
                "lspServers": {"broken-lsp": "this-should-be-an-object"},
            },
            notes="LSP server with wrong type (string instead of object) — schema violation.",
        )
    )

    # 29: monitors field
    grid.append(
        FixtureSpec(
            nn="29",
            descriptor="monitors-field",
            plugin_json={
                "name": "fixture-29",
                "version": "1.0.0",
                "monitors": ["./monitors/example.json"],
            },
            extra_files=(
                FixtureFile(
                    path="monitors/example.json",
                    content=json.dumps({"name": "demo", "interval": 60}) + "\n",
                ),
            ),
            notes="`monitors` field (v2.1.105) — background monitor declarations.",
        )
    )

    # 30: outputStyle field
    grid.append(
        FixtureSpec(
            nn="30",
            descriptor="outputstyle-field",
            plugin_json={
                "name": "fixture-30",
                "version": "1.0.0",
                "outputStyle": "verbose",
            },
            notes="`outputStyle` field — declares default output style.",
        )
    )

    return tuple(grid)


GRID_FIXTURES: tuple[FixtureSpec, ...] = _build_grid()


# ---------------------------------------------------------------------------
# Materialization
# ---------------------------------------------------------------------------


def materialize_one(spec: FixtureSpec, grid_root: Path) -> Path:
    """Write a single fixture to disk under <grid_root>/<dir_name>/.

    Wipes any pre-existing directory of the same name first so the
    generator stays idempotent (re-runs always produce identical output).

    Returns the absolute path of the fixture directory.
    """
    target = grid_root / spec.dir_name
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=True)

    # README.md describing the fixture intent — both CPV and CLI ignore it,
    # but it makes the fixture-grid self-documenting.
    #
    # NOTE: we deliberately reference the generator path in plain prose
    # (no backticks) because CPV's "broken backtick path" check flags
    # references to repo-relative paths that do not exist *inside the
    # fixture*. Backticks around a script name that lives outside the
    # fixture tree would trigger 30 CPV false positives in the audit.
    readme = (
        f"# Fixture {spec.nn} — {spec.descriptor}\n\n"
        f"{spec.notes}\n\n"
        "Generated by the fixture_grid_generator script under "
        "scripts/audit/ in the CPV repo. Do not edit by hand — "
        "regenerate from the source if you need to change behavior.\n"
    )
    (target / "README.md").write_text(readme, encoding="utf-8")

    # plugin.json (or skip if intentionally omitted)
    if spec.plugin_json is not None:
        plugin_dir = target / ".claude-plugin"
        plugin_dir.mkdir(parents=True, exist_ok=True)
        (plugin_dir / "plugin.json").write_text(
            json.dumps(spec.plugin_json, indent=2) + "\n",
            encoding="utf-8",
        )

    # marketplace.json (Layout C only)
    if spec.marketplace_json is not None:
        plugin_dir = target / ".claude-plugin"
        plugin_dir.mkdir(parents=True, exist_ok=True)
        (plugin_dir / "marketplace.json").write_text(
            json.dumps(spec.marketplace_json, indent=2) + "\n",
            encoding="utf-8",
        )

    # Extra files (agents, skills, hooks, resources)
    for extra in spec.extra_files:
        dest = target / extra.path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(extra.content, encoding="utf-8")
        # Make hook scripts executable so the CLI does not flag them
        if extra.path.startswith("hooks/") and extra.path.endswith((".sh", ".py")):
            dest.chmod(0o755)

    return target


def materialize_all(grid_root: Path) -> list[Path]:
    """Materialize every fixture in `GRID_FIXTURES`.

    Returns the list of fixture-root paths in NN order.
    """
    grid_root.mkdir(parents=True, exist_ok=True)
    return [materialize_one(spec, grid_root) for spec in GRID_FIXTURES]


# ---------------------------------------------------------------------------
# CLI entry
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """Generate the fixture grid at the requested path.

    Default grid root is `<repo>/tests/audit/fixtures/grid/` relative to
    the script — same path the audit harness consumes.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    default_root = Path(__file__).resolve().parents[2] / "tests" / "audit" / "fixtures" / "grid"
    parser.add_argument(
        "--root",
        type=Path,
        default=default_root,
        help=f"Grid root directory (default: {default_root}).",
    )
    args = parser.parse_args(argv)
    paths = materialize_all(args.root)
    print(f"Materialized {len(paths)} fixtures under {args.root}")
    for p in paths:
        print(f"  {p.relative_to(args.root.parent)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
