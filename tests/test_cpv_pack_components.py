"""Tests for scripts/cpv_pack_components.py — the standalone-components packer.

Coverage:
- discover() finds every supported component type in a synthetic fixture
- Selection.parse_filter rejects malformed args
- validate_selection catches empty/duplicate/over-singleton selections
- add_to_marketplace + create_marketplace produce the right JSON shape
- main() end-to-end: --list-only, --all, --include, --json mode
- exit codes: 0 ok, 1 invalid args, 2 empty source, 3 selection conflict

The fixture is built in-memory (tmp_path) so tests are hermetic and run
without filesystem state from the user's machine.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import cpv_pack_components as pkg  # noqa: E402

# ── Fixture helpers ──────────────────────────────────────────────────────────


def _write(p: Path, content: str = "") -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


def _make_full_source(root: Path) -> Path:
    """Build a source dir with one of every supported component type.

    Layout:
        root/
            skills/my-skill/SKILL.md
            agents/my-agent.md
            commands/my-cmd.md
            hooks/hooks.json
            .mcp.json
            .lsp.json
            monitors/monitors.json
            output-styles/casual.md
    """
    root.mkdir(parents=True, exist_ok=True)
    _write(
        root / "skills" / "my-skill" / "SKILL.md",
        "---\nname: my-skill\ndescription: Use when ... Trigger with ...\n---\n## Overview\nx\n",
    )
    _write(
        root / "agents" / "my-agent.md",
        "---\nname: my-agent\ndescription: An agent\nmodel: sonnet\ntools: Read\n---\n# my-agent\n",
    )
    _write(
        root / "commands" / "my-cmd.md",
        "---\nname: my-cmd\nallowed-tools: Bash\ndescription: A command\n---\n# my-cmd\n",
    )
    _write(root / "hooks" / "hooks.json", json.dumps({"hooks": {}}))
    _write(root / ".mcp.json", json.dumps({"mcpServers": {}}))
    _write(root / ".lsp.json", json.dumps({"lspServers": {}}))
    _write(root / "monitors" / "monitors.json", json.dumps({"monitors": []}))
    _write(
        root / "output-styles" / "casual.md",
        "---\nname: casual\ndescription: Casual style\n---\n# casual\n",
    )
    return root


# ── discover() ───────────────────────────────────────────────────────────────


class TestDiscover:
    """Verify discovery finds every supported component type and ignores noise."""

    def test_finds_every_component_type(self, tmp_path):
        """A source with all 8 component types yields 8 Component entries."""
        src = _make_full_source(tmp_path / "src")
        components = pkg.discover(src)
        types = sorted(c.type for c in components)
        assert types == ["agent", "command", "hook", "lsp", "mcp", "monitor", "output-style", "skill"]

    def test_ignores_random_files(self, tmp_path):
        """Random .txt / .json files outside known paths must not show up."""
        src = tmp_path / "src"
        src.mkdir()
        _write(src / "README.md", "")  # bare README — frontmatter-less, classified as agent? See below.
        _write(src / "random.txt", "x")
        components = pkg.discover(src)
        # `README.md` will be classified as `agent` by the heuristic — that's
        # the existing _classify_md behaviour. Just confirm we don't trip on
        # the random.txt file.
        assert all(c.src != src / "random.txt" for c in components)

    def test_finds_root_skill(self, tmp_path):
        """SKILL.md at root with frontmatter `name:` becomes a skill."""
        src = tmp_path / "src"
        _write(src / "SKILL.md", "---\nname: solo-skill\ndescription: x\n---\n## Overview\n")
        components = pkg.discover(src)
        skills = [c for c in components if c.type == "skill"]
        assert len(skills) == 1
        assert skills[0].name == "solo-skill"


# ── Selection.parse_filter ───────────────────────────────────────────────────


class TestSelection:
    """Verify the include/exclude DSL parsing and matching logic."""

    def test_parse_filter_valid(self):
        """`agent=foo,bar` → ('agent', ['foo', 'bar'])."""
        kind, names = pkg.Selection.parse_filter("agent=foo,bar")
        assert kind == "agent"
        assert names == ["foo", "bar"]

    def test_parse_filter_empty_names(self):
        """`agent=` → ('agent', []) — caller treats as 'all of agent'."""
        kind, names = pkg.Selection.parse_filter("agent=")
        assert kind == "agent" and names == []

    def test_parse_filter_rejects_unknown_type(self):
        """Unknown type → ValueError."""
        with pytest.raises(ValueError, match="unknown component type"):
            pkg.Selection.parse_filter("schmagent=x")

    def test_parse_filter_rejects_no_equals(self):
        """No `=` → ValueError."""
        with pytest.raises(ValueError, match="expects type=name"):
            pkg.Selection.parse_filter("agent")

    def test_include_all_matches_everything(self):
        """include_all=True selects every component regardless of type."""
        sel = pkg.Selection(include_all=True)
        c = pkg.Component(type="agent", name="foo", src=Path("/x"))
        assert sel.matches(c)

    def test_exclude_overrides_include(self):
        """An excluded name is rejected even when include matches."""
        sel = pkg.Selection(include_all=True)
        sel.add_exclude("agent=foo")
        c = pkg.Component(type="agent", name="foo", src=Path("/x"))
        assert not sel.matches(c)


# ── validate_selection ───────────────────────────────────────────────────────


class TestValidateSelection:
    """Catch problematic selections before scaffolding."""

    def test_empty_selection_problematic(self):
        """No components selected → flagged."""
        problems = pkg.validate_selection([])
        assert problems and "empty" in problems[0]

    def test_duplicate_names_within_type(self):
        """Two agents named 'foo' → flagged."""
        components = [
            pkg.Component(type="agent", name="foo", src=Path("/a")),
            pkg.Component(type="agent", name="foo", src=Path("/b")),
        ]
        problems = pkg.validate_selection(components)
        assert any("duplicate agent" in p for p in problems)

    def test_multiple_hooks_disallowed(self):
        """Two hook configs → flagged (only one hooks.json at root)."""
        components = [
            pkg.Component(type="hook", name="a", src=Path("/a")),
            pkg.Component(type="hook", name="b", src=Path("/b")),
        ]
        problems = pkg.validate_selection(components)
        assert any("more than one hook" in p for p in problems)

    def test_clean_selection_no_problems(self):
        """One of each type → no problems."""
        components = [
            pkg.Component(type="agent", name="a", src=Path("/a")),
            pkg.Component(type="skill", name="s", src=Path("/s")),
        ]
        assert pkg.validate_selection(components) == []


# ── Marketplace ops ──────────────────────────────────────────────────────────


class TestMarketplaceOps:
    """Verify add_to_marketplace + create_marketplace produce valid JSON."""

    def _params(self, name: str = "p1", owner: str = "Alice") -> "pkg.gpr.PluginParams":
        return pkg.gpr.PluginParams(
            name=name,
            description="x",
            author=owner,
            author_email="alice@example.com",
            github_owner=owner,
        )

    def test_create_marketplace_writes_valid_json(self, tmp_path):
        """create_marketplace at empty path → valid marketplace.json with self-entry."""
        target = tmp_path / "mkt"
        pkg.create_marketplace(target, self._params())
        data = json.loads((target / ".claude-plugin" / "marketplace.json").read_text())
        assert data["name"] == "mkt"
        assert len(data["plugins"]) == 1
        assert data["plugins"][0]["name"] == "p1"

    def test_add_to_marketplace_is_idempotent(self, tmp_path):
        """Calling add_to_marketplace twice → still ONE entry per plugin name."""
        target = tmp_path / "mkt"
        pkg.create_marketplace(target, self._params(name="p1"))
        pkg.add_to_marketplace(target, self._params(name="p1"))  # second call
        data = json.loads((target / ".claude-plugin" / "marketplace.json").read_text())
        names = [p["name"] for p in data["plugins"]]
        assert names == ["p1"]  # not ["p1", "p1"]

    def test_add_to_marketplace_appends_distinct(self, tmp_path):
        """Adding p2 alongside p1 → both present."""
        target = tmp_path / "mkt"
        pkg.create_marketplace(target, self._params(name="p1"))
        pkg.add_to_marketplace(target, self._params(name="p2"))
        data = json.loads((target / ".claude-plugin" / "marketplace.json").read_text())
        names = sorted(p["name"] for p in data["plugins"])
        assert names == ["p1", "p2"]

    def test_add_to_marketplace_rejects_missing_file(self, tmp_path):
        """No marketplace.json at target → FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            pkg.add_to_marketplace(tmp_path, self._params())


# ── main() end-to-end ───────────────────────────────────────────────────────


class TestMainEndToEnd:
    """Black-box CLI tests: invoke main() with argv and assert exit codes
    + JSON output."""

    def test_list_only_finds_components(self, tmp_path, capsys):
        """`--list-only` exits 0 and prints discovered components, no scaffolding."""
        src = _make_full_source(tmp_path / "src")
        rc = pkg.main([str(src), "--list-only"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "skill" in out and "my-skill" in out
        # No target dir scaffolded
        assert not (tmp_path / "target").exists()

    def test_empty_source_exits_2(self, tmp_path, capsys):
        """Empty source → exit 2 + structured error."""
        empty = tmp_path / "empty"
        empty.mkdir()
        rc = pkg.main([str(empty), str(tmp_path / "tgt"), "--name", "x", "--all", "--json"])
        assert rc == 2
        payload = json.loads(capsys.readouterr().out.strip())
        assert payload["ok"] is False
        assert payload["exit_code"] == 2

    def test_missing_source_exits_1(self, tmp_path, capsys):
        """Non-existent source → exit 1 + structured error."""
        rc = pkg.main([str(tmp_path / "nope"), str(tmp_path / "tgt"), "--name", "x", "--all", "--json"])
        assert rc == 1
        payload = json.loads(capsys.readouterr().out.strip())
        assert payload["ok"] is False

    def test_include_and_all_mutex(self, tmp_path, capsys):
        """--include + --all together → exit 3 (selection conflict)."""
        src = _make_full_source(tmp_path / "src")
        rc = pkg.main(
            [
                str(src),
                str(tmp_path / "tgt"),
                "--name",
                "p1",
                "--all",
                "--include",
                "agent=my-agent",
                "--json",
            ]
        )
        assert rc == 3
        payload = json.loads(capsys.readouterr().out.strip())
        assert "mutually exclusive" in payload["error"]

    def test_invalid_name_exits_1(self, tmp_path):
        """Plugin name with invalid chars → SystemExit (argparse-level)."""
        src = _make_full_source(tmp_path / "src")
        with pytest.raises(SystemExit):
            pkg.main([str(src), str(tmp_path / "tgt"), "--name", "INVALID NAME", "--all"])

    def test_pack_with_all_creates_plugin(self, tmp_path):
        """`--all` produces a plugin with .claude-plugin/plugin.json
        and slurps the components into the right folders."""
        src = _make_full_source(tmp_path / "src")
        target = tmp_path / "plugin"
        rc = pkg.main(
            [
                str(src),
                str(target),
                "--name",
                "p1",
                "--description",
                "test",
                "--author",
                "Alice",
                "--author-email",
                "a@e.com",
                "--all",
            ]
        )
        assert rc == 0
        # Plugin manifest exists.
        assert (target / ".claude-plugin" / "plugin.json").is_file()
        # Components landed in their standard locations.
        assert (target / "skills" / "my-skill" / "SKILL.md").is_file()
        assert (target / "agents" / "my-agent.md").is_file()
        assert (target / "commands" / "my-cmd.md").is_file()
        assert (target / "hooks" / "hooks.json").is_file()
        assert (target / ".mcp.json").is_file()
        assert (target / ".lsp.json").is_file()
        assert (target / "monitors" / "monitors.json").is_file()
        assert (target / "output-styles" / "casual.md").is_file()

    def test_pack_with_include_only_subset(self, tmp_path):
        """`--include agent=my-agent` packs only that one component."""
        src = _make_full_source(tmp_path / "src")
        target = tmp_path / "plugin"
        rc = pkg.main(
            [
                str(src),
                str(target),
                "--name",
                "p1",
                "--author",
                "Alice",
                "--author-email",
                "a@e.com",
                "--include",
                "agent=my-agent",
            ]
        )
        assert rc == 0
        assert (target / "agents" / "my-agent.md").is_file()
        # Skill not packed because not included.
        assert not (target / "skills" / "my-skill" / "SKILL.md").is_file()

    def test_dry_run_does_not_write(self, tmp_path):
        """`--dry-run` exits 0 but does NOT create the target dir's plugin shape."""
        src = _make_full_source(tmp_path / "src")
        target = tmp_path / "plugin"
        rc = pkg.main(
            [
                str(src),
                str(target),
                "--name",
                "p1",
                "--author",
                "Alice",
                "--author-email",
                "a@e.com",
                "--all",
                "--dry-run",
            ]
        )
        assert rc == 0
        assert not target.exists() or not (target / ".claude-plugin").exists()

    def test_json_mode_emits_machine_readable(self, tmp_path, capsys):
        """--json: stdout has exactly one JSON line; stderr has the human prose."""
        src = _make_full_source(tmp_path / "src")
        target = tmp_path / "plugin"
        rc = pkg.main(
            [
                str(src),
                str(target),
                "--name",
                "p1",
                "--author",
                "Alice",
                "--author-email",
                "a@e.com",
                "--all",
                "--json",
            ]
        )
        assert rc == 0
        captured = capsys.readouterr()
        # Last line of stdout is a JSON object.
        last_line = captured.out.strip().splitlines()[-1]
        payload = json.loads(last_line)
        assert payload["ok"] is True
        assert payload["files_copied"] >= 8
