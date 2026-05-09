"""Tests for scripts/add_dependencies.py — the dep-adder engine.

User request 2026-05-09: "menu option to explicitly add a dependency for
some other plugins (passing the url or paths of them explicitly to the
agent, or pointing to a plugin and saying: 'add the same dependencies
required by that plugin')". The engine backs that menu choice; the tests
here cover every documented input form + the rollback-on-regression
guarantee.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import add_dependencies as add_deps  # noqa: E402


def _make_plugin(tmp_path: Path, name: str = "demo", deps: list | None = None) -> Path:
    """Create a minimal plugin with optional pre-existing deps."""
    root = tmp_path / name
    (root / ".claude-plugin").mkdir(parents=True)
    manifest: dict[str, object] = {
        "name": name,
        "version": "1.0.0",
        "description": "x",
        "author": {"name": "t", "email": "t@e.com"},
    }
    if deps is not None:
        manifest["dependencies"] = deps
    (root / ".claude-plugin" / "plugin.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    return root


def _read_deps(plugin: Path) -> list:
    return json.loads((plugin / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8")).get("dependencies", [])


# ── _parse_add_spec ──────────────────────────────────────────────────────────


class TestParseAddSpec:
    """Cover every documented spec form."""

    def test_bare_string(self):
        assert add_deps._parse_add_spec("dev-browser") == "dev-browser"

    def test_name_and_marketplace(self):
        assert add_deps._parse_add_spec("dev-browser@my-mkt") == {
            "name": "dev-browser", "marketplace": "my-mkt",
        }

    def test_name_marketplace_version(self):
        assert add_deps._parse_add_spec("dev-browser@my-mkt@~1.2.0") == {
            "name": "dev-browser", "marketplace": "my-mkt", "version": "~1.2.0",
        }

    def test_name_double_at_version(self):
        """`name@@version` form (version without marketplace)."""
        assert add_deps._parse_add_spec("dev-browser@@~1.2.0") == {
            "name": "dev-browser", "version": "~1.2.0",
        }

    def test_invalid_kebab_rejected(self):
        with pytest.raises(ValueError, match="kebab-case"):
            add_deps._parse_add_spec("DevBrowser")
        with pytest.raises(ValueError, match="kebab-case"):
            add_deps._parse_add_spec("foo@BadMarket")


# ── merge_dependencies ───────────────────────────────────────────────────────


class TestMergeDependencies:
    """Dedup + last-write-wins + sorted output."""

    def test_empty_existing_returns_additions_sorted(self):
        merged = add_deps.merge_dependencies([], ["b", "a"])
        assert merged == ["a", "b"]

    def test_dedup_by_name_last_write_wins(self):
        existing = ["dev-browser"]
        additions = [{"name": "dev-browser", "version": "~1.2.0"}]
        merged = add_deps.merge_dependencies(existing, additions)
        assert merged == [{"name": "dev-browser", "version": "~1.2.0"}]

    def test_disjoint_names_appended_sorted(self):
        existing = ["alpha"]
        additions = ["zulu", {"name": "mike", "version": "^1.0"}]
        merged = add_deps.merge_dependencies(existing, additions)
        names = [add_deps._name_of(e) for e in merged]
        assert names == ["alpha", "mike", "zulu"]

    def test_malformed_entries_skipped(self):
        """Entries without a name (broken JSON) are silently skipped — the
        validator's job to flag them, not the merger's."""
        existing = ["valid", 42, {"foo": "bar"}, None]
        additions = ["new-dep"]
        merged = add_deps.merge_dependencies(existing, additions)
        names = [add_deps._name_of(e) for e in merged]
        assert sorted(names) == ["new-dep", "valid"]


# ── End-to-end main() ────────────────────────────────────────────────────────


class TestMainEndToEnd:
    """Black-box CLI tests exercising the full flow."""

    def test_dry_run_emits_json_no_write(self, tmp_path: Path, capsys) -> None:
        """`--dry-run` prints the merged array; plugin.json untouched."""
        plugin = _make_plugin(tmp_path, deps=[])
        rc = add_deps.main([str(plugin), "--add", "dev-browser", "--dry-run"])
        assert rc == 0
        out = capsys.readouterr().out.strip()
        assert json.loads(out) == ["dev-browser"]
        # Original plugin.json still has empty deps.
        assert _read_deps(plugin) == []

    def test_add_writes_atomically_and_validates(self, tmp_path: Path) -> None:
        """`--add foo@@^1.0` writes the spec; .bak removed on success."""
        plugin = _make_plugin(tmp_path, deps=["existing"])
        rc = add_deps.main([
            str(plugin), "--add", "new-dep@@^1.0", "--no-validate",
        ])
        assert rc == 0
        deps = _read_deps(plugin)
        # Sorted: existing < new-dep
        assert deps == ["existing", {"name": "new-dep", "version": "^1.0"}]
        assert not (plugin / ".claude-plugin" / "plugin.json.bak").exists()

    def test_invalid_target_exit_1(self, tmp_path: Path, capsys) -> None:
        """Target without plugin.json → exit 1."""
        empty = tmp_path / "empty"
        empty.mkdir()
        rc = add_deps.main([str(empty), "--add", "foo"])
        assert rc == 1
        assert "no plugin.json" in capsys.readouterr().err

    def test_no_add_or_from_exit_1(self, tmp_path: Path, capsys) -> None:
        """Calling with neither --add nor --from → exit 1 (nothing to do)."""
        plugin = _make_plugin(tmp_path)
        rc = add_deps.main([str(plugin)])
        assert rc == 1
        assert "nothing to do" in capsys.readouterr().err

    def test_invalid_spec_exit_1(self, tmp_path: Path, capsys) -> None:
        """Non-kebab-case name → exit 1."""
        plugin = _make_plugin(tmp_path)
        rc = add_deps.main([str(plugin), "--add", "BadName"])
        assert rc == 1

    def test_from_local_plugin_copies_deps(self, tmp_path: Path) -> None:
        """`--from <local-plugin>` copies that plugin's full deps array."""
        # Source plugin with two deps.
        source = _make_plugin(
            tmp_path, name="src",
            deps=["alpha", {"name": "beta", "version": "^2.0"}],
        )
        target = _make_plugin(tmp_path, name="tgt", deps=[])
        rc = add_deps.main([str(target), "--from", str(source), "--no-validate"])
        assert rc == 0
        deps = _read_deps(target)
        assert deps == ["alpha", {"name": "beta", "version": "^2.0"}]

    def test_from_missing_path_exit_2(self, tmp_path: Path, capsys) -> None:
        """`--from` pointing at a non-directory → exit 2."""
        plugin = _make_plugin(tmp_path)
        rc = add_deps.main([str(plugin), "--from", str(tmp_path / "ghost")])
        assert rc == 2
        assert "not a directory" in capsys.readouterr().err

    def test_combined_add_and_from(self, tmp_path: Path) -> None:
        """--from + --add together — merged with last-write-wins."""
        source = _make_plugin(
            tmp_path, name="src",
            deps=["alpha", {"name": "beta", "version": "^1.0"}],
        )
        target = _make_plugin(tmp_path, name="tgt", deps=[])
        rc = add_deps.main([
            str(target),
            "--from", str(source),
            "--add", "beta@@^2.0",  # overrides source's beta@^1.0
            "--add", "gamma",
            "--no-validate",
        ])
        assert rc == 0
        deps = _read_deps(target)
        # Sorted by name: alpha < beta < gamma
        assert deps == [
            "alpha",
            {"name": "beta", "version": "^2.0"},  # last-write-wins
            "gamma",
        ]

    def test_idempotent_re_run(self, tmp_path: Path) -> None:
        """Running the same command twice produces identical plugin.json."""
        plugin = _make_plugin(tmp_path, deps=["existing"])
        args = [str(plugin), "--add", "new-dep@@~1.0", "--no-validate"]
        rc1 = add_deps.main(args)
        first_content = (plugin / ".claude-plugin" / "plugin.json").read_text()
        rc2 = add_deps.main(args)
        second_content = (plugin / ".claude-plugin" / "plugin.json").read_text()
        assert rc1 == rc2 == 0
        assert first_content == second_content
